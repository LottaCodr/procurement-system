"""Public, unauthenticated read API + OCDS endpoints.

Axiom 3: trust must not depend on trusting the operator. Every read here is
available to a journalist, a competitor, an auditor or a vendor's own tools
without a key, a login or a quota. That is a deliberate, defended decision —
not an oversight — and `/api/v1/policy` states it.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from ledger import services as ledger
from procurement.api.serializers import (
    AwardSerializer,
    ContractSerializer,
    StatsSerializer,
    TenderDetailSerializer,
    TenderListSerializer,
)
from procurement.models import Award, Contract, Tender
from procurement.models_party import Party
from procurement.ocds.schema import OCDS_RELEASE_SCHEMA, validate_release
from procurement.risk import INDICATORS, INDICATOR_VERSION, evaluate_tender
from procurement.views import live_metrics


class TenderCursorPagination(CursorPagination):
    page_size = 50
    max_page_size = 500
    page_size_query_param = "page_size"
    ordering = "-published_at"


class ContractCursorPagination(TenderCursorPagination):
    """Contracts have no `published_at`; a cursor ordered by a field the model
    does not have raises FieldError on the first request, which is how
    /api/v1/contracts came to return 500 instead of the contract register."""

    ordering = "-signed_at"


@extend_schema(tags=["tenders"])
class TenderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only. There is no write path on the public API by design; bids are
    created inside the authenticated supplier workspace, which Phase 2 ships."""

    serializer_class = TenderListSerializer
    pagination_class = TenderCursorPagination
    lookup_field = "ocid"
    lookup_value_regex = "[A-Za-z0-9_.-]+"

    def get_queryset(self):
        qs = Tender.objects.public().select_related("agency", "rule").prefetch_related(
            "bids__supplier", "awards__bid__supplier", "documents", "questions", "committee__user",
            "lots", "criteria", "objections",
        )
        p = self.request.query_params

        if p.get("status"):
            qs = qs.filter(status=p["status"].upper())
        if p.get("agency"):
            qs = qs.filter(agency__code__iexact=p["agency"])
        if p.get("method"):
            qs = qs.filter(method=p["method"].upper())
        if p.get("min"):
            qs = qs.filter(est_value__gte=p["min"])
        if p.get("max"):
            qs = qs.filter(est_value__lte=p["max"])
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(description__icontains=p["q"]) | Q(ocid__icontains=p["q"]))
        if p.get("updated_since"):
            qs = qs.filter(updated_at__gte=p["updated_since"])
        if p.get("open") == "1":
            qs = qs.filter(status=Tender.Status.PUBLISHED, submission_close_at__gt=timezone.now())
        return qs.order_by("-published_at")

    def get_serializer_class(self):
        return TenderDetailSerializer if self.kwargs.get("ocid") else TenderListSerializer

    @extend_schema(
        parameters=[OpenApiParameter("year", int, description="Fiscal/calendar year of publication")],
        examples=[OpenApiExample("Awarded tender", value={"ocid": settings.PLATFORM_ABBREV + "-MOH-2026-0007"})],
    )
    @action(detail=True, methods=["get"], url_path="ocds")
    def ocds(self, request, ocid=None):
        """Full OCDS 1.1 release for one contracting process. Validated before send."""
        tender = self.get_object()
        release = tender.ocds_release
        validate_release(release)
        return Response(release)

    @extend_schema(methods=["get"], summary="Event ledger for this process (chronological)")
    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request, ocid=None):
        tender = self.get_object()
        rows = ledger.history(f"procurement.Tender.{tender.pk}")
        return Response(
            {
                "ocid": tender.ocid,
                "count": len(rows),
                "events": [
                    {"seq": r["seq"], "type": r["event_type"], "actor": r["actor"], "at": r["occurred_at"].isoformat(), "payload": r["payload"]}
                    for r in rows
                ],
            }
        )

    @extend_schema(methods=["get"], summary="Red-flag indicators evaluated for this process")
    @action(detail=True, methods=["get"], url_path="flags")
    def flags(self, request, ocid=None):
        tender = self.get_object()
        return Response(
            {
                "ocid": tender.ocid,
                "indicator_version": INDICATOR_VERSION,
                "flags": evaluate_tender(tender),
                "note": "Public definitions at /api/v1/indicators. Computed only from published data.",
            }
        )


@extend_schema(tags=["awards"])
class AwardViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The award register. This endpoint alone is the difference between a
    procurement portal and a poster."""

    serializer_class = AwardSerializer
    pagination_class = TenderCursorPagination
    queryset = (
        Award.objects.select_related("bid__supplier", "tender__agency", "approved_by")
        .filter(status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED, Award.Status.APPROVED])
        .order_by("-published_at", "-id")
    )

    def get_queryset(self):
        qs = super().get_queryset()
        year = self.request.query_params.get("year")
        if year:
            qs = qs.filter(tender__published_at__year=int(year))
        if agency := self.request.query_params.get("agency"):
            qs = qs.filter(tender__agency__code__iexact=agency)
        return qs


@extend_schema(tags=["contracts"])
class ContractViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ContractSerializer
    pagination_class = ContractCursorPagination
    queryset = Contract.objects.select_related("award__bid__supplier", "award__tender__agency").order_by("-signed_at")


# --------------------------------------------------------------------------- OCDS
@extend_schema(
    tags=["open data"],
    summary="Continuous OCDS release feed (NDJSON)",
    description="One release per line, newest changes first. Publish a release whenever "
                "the process changes — ProZorro's pattern — rather than a nightly dump. "
                "Unauthenticated, cacheable, cursor-paginated.",
    parameters=[
        OpenApiParameter("cursor", str, description="opaque, from Link header"),
        OpenApiParameter("since", str, description="ISO-8601 timestamp filter"),
        OpenApiParameter("validate", bool, description="if 1, each release is schema-validated before emit (used by CI)"),
    ],
)
@api_view(["GET"])
def releases(request):
    qs = Tender.objects.public().order_by("-published_at", "-id").select_related("agency", "rule")
    if since := request.query_params.get("since"):
        qs = qs.filter(updated_at__gte=since)
    want_validate = request.query_params.get("validate") in ("1", "true", "yes")
    out = io.StringIO()
    n = 0
    for tender in qs[:500]:
        release = tender.ocds_release
        if want_validate:
            validate_release(release)
        out.write(json.dumps(release, default=str, separators=(",", ":")) + "\n")
        n += 1
    return HttpResponse(
        out.getvalue(),
        content_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300", "X-Release-Count": str(n)},
    )


@extend_schema(tags=["open data"], summary="Whole-year OCDS bulk dump (zip)")
@api_view(["GET"])
def bulk(request):
    year = int(request.query_params.get("year") or timezone.localdate().year)
    tenders = Tender.objects.public().filter(published_at__year=year).select_related("agency", "rule")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        releases_lines = []
        rows = []
        for tender in tenders:
            release = tender.ocds_release
            releases_lines.append(json.dumps(release, default=str, separators=(",", ":")))
            for a in tender.awards.all():
                rows.append(
                    {
                        "ocid": tender.ocid,
                        "agency": tender.agency.code,
                        "method": tender.method,
                        "title": tender.title,
                        "estimate": str(tender.est_value or ""),
                        "award_amount": str(a.amount),
                        "supplier": a.bid.supplier.legal_name,
                        "supplier_rc": a.bid.supplier.rc_number,
                        "published_at": a.published_at.isoformat() if a.published_at else "",
                        "flags": "|".join(tender.red_flag_codes),
                    }
                )
        zf.writestr(f"releases-{year}.ndjson", "\n".join(releases_lines))
        mem = io.StringIO()
        if rows:
            w = csv.DictWriter(mem, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        zf.writestr(f"awards-{year}.csv", mem.getvalue())
        zf.writestr(
            "LICENSE.md",
            "# Data licence\n\nAll data on this endpoint is published under CC BY 4.0. "
            "Reuse is free, including commercially, with attribution to the Taraba State "
            "Bureau of Public Procurement. No registration, key, quota or permission is required.\n",
        )
        zf.writestr("README.md", f"# Bulk dump {year}\n\nTenders: {len(releases_lines)}\nAwards: {len(rows)}\nGenerated: {timezone.now().isoformat()}\n\nSchema: OCDS 1.1 with the `taraba` extension.\n")
    resp = HttpResponse(buf.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="taraba-procurement-{year}.zip"'
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


@extend_schema(tags=["open data"], summary="Publishable ledger commitment (head hash)", methods=["get"])
@api_view(["GET"])
def ledger_head(request):
    checked, broken = ledger.verify_chain()
    return Response(
        {
            "head_hash": ledger.head_hash(),
            "events_checked": checked,
            "chain_valid": broken is None,
            "first_broken_seq": broken,
            "algorithm": "sha256",
            "checked_at": timezone.now(),
            "how_to_verify": "pip install -r requirements.txt && python manage.py verify_ledger",
        }
    )


@extend_schema(tags=["open data"], summary="Indicator definitions (versioned, public)", methods=["get"])
@api_view(["GET"])
def indicators(request):
    return Response({"version": INDICATOR_VERSION, "indicators": INDICATORS})


@extend_schema(tags=["meta"], summary="Public metrics — every figure computed here, never typed")
@api_view(["GET"])
def stats(request):
    """This endpoint is the reason the homepage can print numbers at all.

    The incumbent Taraba site advertises ₦48.7bn published and 100% disclosure
    while its register returns ₦0.00. Here the two cannot diverge, because both
    render the same call to `live_metrics()` — the page in HTML, this endpoint
    as JSON — and CI asserts that payload against the database.

    The earlier version of this view re-derived the figures, which is how it
    came to define "open" as PUBLISHED-only while the register counted
    CLARIFYING processes too. Two implementations of one metric is one
    implementation too many.
    """
    payload = live_metrics()
    ser = StatsSerializer(data=payload)
    ser.is_valid(raise_exception=True)
    return Response(ser.data, headers={"Cache-Control": "public, max-age=60"})


@extend_schema(tags=["meta"], summary="Access policy: why this API needs no key", responses={200: {"type": "object"}})
@api_view(["GET"])
def access_policy(request):
    return Response(
        {
            "authentication": "none",
            "rate_limit": "none for reads; abuse throttling applies to writes only",
            "licence": "CC BY 4.0",
            "schema": "/api/v1/schema",
            "data_standard": "OCDS 1.1 with 'taraba' extension",
            "redactions": "commercially sensitive annexes only; the redaction rule is published at /policy/redaction",
            "rationale": "An oversight API that requires permission from the body being "
                         "oversight is not an oversight API. This choice is documented, not accidental.",
        }
    )


@extend_schema(tags=["open data"], summary="JSON schema used to validate every release", methods=["get"])
@api_view(["GET"])
def release_schema(request):
    return JsonResponse(OCDS_RELEASE_SCHEMA, json_dumps_params={"indent": 2})


@extend_schema(tags=["suppliers"], summary="Supplier register (verified identities only)", methods=["get"])
@api_view(["GET"])
def suppliers(request):
    qs = Party.objects.filter(is_active=True).prefetch_related("verifications", "owners")
    if rc := request.query_params.get("rc"):
        qs = qs.filter(rc_number__iexact=rc)
    if name := request.query_params.get("q"):
        qs = qs.filter(legal_name__icontains=name)
    if request.query_params.get("debarred") == "1":
        qs = qs.exclude(debarred_from__isnull=True)
    rows = []
    for p in qs[:500]:
        rows.append(
            {
                "id": p.pk,
                "legal_name": p.legal_name,
                "rc_number": p.rc_number,
                "tin": p.tin[:4] + "****" if p.tin else "",  # TIN is not a public lookup key
                "state": p.state,
                "lga": p.lga,
                "scope": p.scope,
                "category": p.category,
                "debarred": p.is_debarred,
                "verifications": {
                    v.kind: {"status": v.status, "verified_at": v.verified_at, "expires_at": v.expires_at}
                    for v in p.verifications.all()
                },
                "owners_disclosed": p.bo_declared,
                "awards_won": Award.objects.filter(bid__supplier=p, status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED]).count(),
                "bids_submitted": p.bids.count(),
            }
        )
    return Response({"count": len(rows), "results": rows})


urlpatterns_api = None  # declared in urls.py
