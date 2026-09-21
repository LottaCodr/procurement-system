from __future__ import annotations

import json
import re

from django.db.models import Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from ledger import services as ledger
from ledger.models import Event
from procurement.models import Award, Contract, Tender
from procurement.models_party import Agency, Party, PartyVerification
from procurement.ocds.schema import OCDSValidationError, validate_release
from procurement.risk import INDICATORS, INDICATOR_VERSION, evaluate_tender


def live_metrics() -> dict:
    """The single source of homepage and open-data figures.

    Nothing on the site may print a number this function did not compute. That
    rule exists because the incumbent Taraba site advertises "₦48.7bn contracts
    published / 100% awards disclosed" while its register returns ₦0.00.
    """
    year = timezone.localdate().year
    tenders = Tender.objects.public()
    awards = Award.objects.filter(status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED])
    total = awards.aggregate(v=Sum("amount"))["v"] or 0
    return {
        "open_tenders": tenders.filter(status=Tender.Status.PUBLISHED, submission_close_at__gt=timezone.now()).count(),
        "published_this_year": tenders.filter(published_at__year=year).count(),
        "awards_published": awards.count(),
        "total_award_value": total,
        "suppliers_verified": Party.objects.filter(
            verifications__kind=PartyVerification.Kind.CAC, verifications__status="PASSED"
        ).distinct().count(),
        "suppliers_total": Party.objects.filter(is_active=True).count(),
        "mdas_onboarding": tenders.values("agency").distinct().count(),
        "mdas_total": Agency.objects.filter(is_active=True).count(),
        "ledger_events": Event.objects.count(),
        "ledger_head_hash": ledger.head_hash(),
        "generated_at": timezone.now(),
    }


@require_GET
def home(request):
    m = live_metrics()
    checked, broken = ledger.verify_chain()
    m["chain_valid"] = broken is None
    m["chain_detail"] = f"{checked} events verified" if broken is None else f"BROKEN at seq {broken}"
    upcoming = Tender.objects.open().select_related("agency").order_by("submission_close_at")[:8]
    recent_awards = Award.objects.filter(status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED]).select_related(
        "bid__supplier", "tender__agency"
    )[:8]
    return render(
        request,
        "home.html",
        {"m": m, "upcoming": upcoming, "recent_awards": recent_awards, "flags": m["awards_published"]},
    )


@require_GET
def tender_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip().upper()
    agency = request.GET.get("agency", "").strip()
    show_open = request.GET.get("open") == "1"

    qs = Tender.objects.public().select_related("agency", "rule")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(ocid__icontains=q) | Q(awards__bid__supplier__legal_name__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if agency:
        qs = qs.filter(agency__code__iexact=agency)
    if show_open:
        qs = qs.filter(status=Tender.Status.PUBLISHED, submission_close_at__gt=timezone.now())
    qs = qs.order_by("-published_at").distinct()

    page, per = int(request.GET.get("page", 1)), 25
    start = (page - 1) * per
    rows = list(qs[start : start + per])
    total_pages = max((qs.count() + per - 1) // per, 1)
    return render(
        request,
        "tenders.html",
        {
            "rows": rows,
            "total": qs.count(),
            "page": page,
            "per": per,
            "pages": total_pages,
            "prev_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if page < total_pages else None,
            "q": q,
            "status": status,
            "agency": agency,
            "agency_list": Agency.objects.filter(is_active=True).order_by("code"),
            "show_open": show_open,
        },
    )


@require_GET
def tender_detail(request, ocid: str):
    tender = get_object_or_404(
        Tender.objects.public().select_related("agency", "budget_line", "rule", "created_by").prefetch_related(
            "documents", "questions", "committee__user", "lots", "criteria", "objections", "bids__supplier__verifications", "awards__bid__supplier"
        ),
        ocid=ocid,
    )
    flags = evaluate_tender(tender)
    try:
        release = tender.ocds_release
        validate_release(release)
        ocds_ok = True
    except OCDSValidationError as exc:
        release, ocds_ok = tender.ocds_release, False
        ocds_error = str(exc)
    else:
        ocds_error = ""
    return render(
        request,
        "tender.html",
        {
            "t": tender,
            "flags": flags,
            "public_flags": [f for f in flags if f["public"]],
            "bids": tender.bids.select_related("supplier").order_by("amount"),
            "reveal": tender.status in (Tender.Status.OPENED, Tender.Status.EVALUATING, Tender.Status.AWARDED, Tender.Status.CONTRACTED, Tender.Status.FROZEN),
            "ocds": release,
            "ocds_ok": ocds_ok,
            "ocds_error": ocds_error,
            "events": ledger.history(f"procurement.Tender.{tender.pk}"),
            "indicators": INDICATORS,
        },
    )


@require_GET
def document_meta(request, ocid: str, doc_id: int):
    """Document metadata, then a short-lived signed URL to the private object store.

    Note the deliberate shape: no public bucket, no listing. Kano/Taraba patterns
    that expose /storage or accept any file type are closed here. Phase 2 wires
    `obj_key` to a real signing endpoint; for now this returns the hash so a
    bidder can verify what they downloaded.
    """
    tender = get_object_or_404(Tender, ocid=ocid)
    doc = get_object_or_404(tender.documents, pk=doc_id, published=True)
    return JsonResponse(
        {
            "ocid": tender.ocid,
            "title": doc.title,
            "kind": doc.kind,
            "version": doc.version,
            "sha256": doc.sha256,
            "size_bytes": doc.size_bytes,
            "published_at": doc.published_at.isoformat(),
            "note": doc.note,
            "integrity": "verify with: sha256sum <file> — must equal sha256 above",
            "download": "/storage-redirect-not-yet-wired",
        }
    )


@require_GET
def tender_ocds(request, ocid: str):
    tender = get_object_or_404(Tender.objects.public().select_related("agency", "rule"), ocid=ocid)
    release = tender.ocds_release
    try:
        validate_release(release)
    except OCDSValidationError as exc:
        return JsonResponse({"error": "ocds_invalid", "detail": str(exc)}, status=500)
    resp = JsonResponse(release, json_dumps_params={"indent": 2, "default": str})
    resp["Cache-Control"] = "public, max-age=300"
    resp["Content-Disposition"] = f'inline; filename="{tender.ocid}.release.json"'
    return resp


@require_GET
def awards(request):
    year = request.GET.get("year")
    agency = request.GET.get("agency")
    qs = Award.objects.filter(status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED]).select_related(
        "bid__supplier", "tender__agency", "approved_by"
    )
    if year:
        qs = qs.filter(tender__published_at__year=int(year))
    if agency:
        qs = qs.filter(tender__agency__code__iexact=agency)
    qs = qs.order_by("-published_at", "-id")
    total = qs.aggregate(v=Sum("amount"))["v"] or 0
    years = (
        Award.objects.filter(status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED])
        .values_list("tender__published_at__year", flat=True)
        .distinct()
    )
    return render(
        request,
        "awards.html",
        {
            "rows": qs[:200],
            "count": qs.count(),
            "total": total,
            "year": year,
            "agency": agency,
            "years": sorted((y for y in years if y), reverse=True),
            "agency_list": Agency.objects.filter(is_active=True).order_by("code"),
        },
    )


@require_GET
def contract_detail(request, reference: str):
    c = get_object_or_404(
        Contract.objects.select_related("award__bid__supplier", "award__tender__agency", "award__approved_by").prefetch_related(
            "events", "acceptances__certified_by", "certifications__certified_by"
        ),
        reference=reference,
    )
    return render(
        request,
        "contract.html",
        {
            "c": c,
            "events": c.events.filter(published=True),
            "acceptances": c.acceptances.select_related("certified_by"),
            "certifications": c.certifications.select_related("certified_by"),
            "variation_high": c.variation_pct > 10,
        },
    )


@require_GET
def supplier_list(request):
    q = request.GET.get("q", "").strip()
    qs = Party.objects.filter(is_active=True).prefetch_related("verifications")
    if q:
        qs = qs.filter(Q(legal_name__icontains=q) | Q(rc_number__icontains=q))
    return render(
        request,
        "suppliers.html",
        {
            "rows": list(qs[:200]),
            "q": q,
            "count": qs.count(),
            "debarred": Party.objects.exclude(debarred_from__isnull=True).count(),
        },
    )


@require_GET
def open_data(request):
    m = live_metrics()
    return render(
        request,
        "open_data.html",
        {
            "m": m,
            "endpoints": [
                ("GET /api/v1/tenders", "Open/closed tenders. Filters: status, agency, method, min, max, q, updated_since, open=1."),
                ("GET /api/v1/tenders/{ocid}", "Full detail: bids after opening, scores, committee, Q&A, objections."),
                ("GET /api/v1/tenders/{ocid}/ocds", "OCDS 1.1 compile release, validated before send."),
                ("GET /api/v1/tenders/{ocid}/events", "Append-only ledger for that process, chronological."),
                ("GET /api/v1/tenders/{ocid}/flags", "Red-flag indicators computed for that process."),
                ("GET /api/v1/awards", "The award register. ?year=&agency=."),
                ("GET /api/v1/contracts", "Signed contracts with variations and payment certifications."),
                ("GET /api/v1/releases", "Continuous NDJSON release feed (ProZorro pattern). ?since="),
                ("GET /api/v1/bulk?year=", "Zip: NDJSON releases + awards CSV + licence. Offline analysis."),
                ("GET /api/v1/suppliers", "Verified supplier register. ?q=&rc=&debarred=1."),
                ("GET /api/v1/stats", "Every figure the site displays. Nothing is typed."),
                ("GET /api/v1/ledger/head", "Head hash + chain verification result."),
                ("GET /api/v1/indicators", "Indicator definitions, versioned."),
                ("GET /api/v1/schema", "OpenAPI 3.1. Generated from the serializers that write the data."),
                ("GET /api/v1/policy/access", "Written statement that reads need no key, quota or permission."),
            ],
        },
    )


@require_GET
def indicators(request):
    return render(request, "indicators.html", {"INDICATORS": INDICATORS, "VERSION": INDICATOR_VERSION})


@require_GET
def status(request):
    """Continuity, published. Kano's portal ran on expired certificates and was
    returning 503 for months with nobody watching. A status page that the public
    can read is the cheapest possible accountability device."""
    checked, broken = ledger.verify_chain()
    from django.conf import settings

    return render(
        request,
        "status.html",
        {
            "chain_events": checked,
            "chain_valid": broken is None,
            "chain_broken_at": broken,
            "head_hash": ledger.head_hash(),
            "append_only": ledger.enforce_append_only(),
            "hsts": getattr(settings, "SECURE_HSTS_SECONDS", 0),
            "csp": dict(getattr(settings, "CSP_POLICY", {}) or {}),
            "contact_phone": settings.CONTACT_PHONE,
            "contact_email": settings.CONTACT_EMAIL,
            "placeholder_risk": bool(re.search(r"XXX|TBD|TODO|000 0000", f"{settings.CONTACT_PHONE} {settings.CONTACT_EMAIL}", re.I)),
            "generated_at": timezone.now(),
        },
    )


@require_GET
def metrics(request):
    """JS so a static front-end or an MDA's own site can embed honest numbers."""
    m = live_metrics()
    # Decimal and datetime are not JSON-native: `default=str` handles them, and
    # award values are cast explicitly so the JS consumer gets numbers.
    payload = {**m, "total_award_value": float(m["total_award_value"] or 0)}
    body = "window.TARABA_PROCUREMENT_METRICS = " + json.dumps(payload, default=str) + ";"
    return HttpResponse(body, content_type="application/javascript; charset=utf-8")
