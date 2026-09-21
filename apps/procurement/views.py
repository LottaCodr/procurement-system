"""Public read views: the transparency spine, rendered server-side.

Principles applied in this module:

* **Every number is computed.** `live_metrics()` is the only source of headline
  figures, and each figure is labelled with the moment it was computed.
* **Filters are additive and shareable.** Every list view encodes its state in
  the query string only, so a filtered register can be pasted into WhatsApp and
  arrive identically for the next person — and be downloaded as CSV.
* **No query inside a loop.** List pages issue a fixed number of queries
  regardless of row count; risk indicators are computed on the detail page, not
  per row on a register of thousands.
* **Hostile input is clamped, not trusted.** Page numbers, year filters and
  free-text search are bounded before they reach the ORM.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from ledger import services as ledger
from ledger.models import Event
from procurement.models import Award, Contract, Tender
from procurement.models_party import Agency, Party, PartyVerification, ThresholdRule
from procurement.ocds.schema import OCDSValidationError, validate_release
from procurement.risk import INDICATORS, INDICATOR_VERSION, evaluate_tender

PER_PAGE = 25
MAX_CSV_ROWS = 20_000


# --------------------------------------------------------------------- metrics
def live_metrics() -> dict:
    """The single source of every headline figure on the site.

    Nothing may print a number this function did not compute. That rule exists
    because the incumbent Taraba site advertises "₦48.7bn contracts published /
    100% awards disclosed" while its own register returns ₦0.00.
    """
    year = timezone.localdate().year
    tenders = Tender.objects.public()
    awards = Award.objects.filter(status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED])
    published_this_year = tenders.filter(published_at__year=year).count()
    return {
        # Same definition the register's "open for bids" filter uses, so the
        # headline count and the filtered list can never disagree.
        "open_tenders": tenders.open().count(),
        "published_this_year": published_this_year,
        "published_all_time": tenders.count(),
        "awards_published": awards.count(),
        "total_award_value": awards.aggregate(v=Sum("amount"))["v"] or 0,
        "contracts_signed": Contract.objects.count(),
        "suppliers_verified": Party.objects.filter(
            verifications__kind=PartyVerification.Kind.CAC, verifications__status="PASSED"
        )
        .distinct()
        .count(),
        "suppliers_total": Party.objects.filter(is_active=True).count(),
        "mdas_onboarding": tenders.values("agency").distinct().count(),
        "mdas_total": Agency.objects.filter(is_active=True).count(),
        "ledger_events": Event.objects.count(),
        "ledger_head_hash": ledger.head_hash(),
        "generated_at": timezone.now(),
        "year": year,
    }


def _chain_state(m: dict) -> dict:
    checked, broken = ledger.verify_chain()
    m["chain_valid"] = broken is None
    m["chain_events"] = checked
    m["chain_detail"] = f"{checked} events verified" if broken is None else f"BROKEN at seq {broken}"
    return m


# ------------------------------------------------------------------- the pages
@require_GET
def home(request):
    m = _chain_state(live_metrics())
    upcoming = (
        Tender.objects.open().select_related("agency").order_by("submission_close_at")[:6]
    )
    recent_awards = (
        Award.objects.filter(status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED])
        .select_related("bid__supplier", "tender__agency")
        .order_by("-published_at", "-id")[:6]
    )
    return render(
        request,
        "home.html",
        {
            "m": m,
            "upcoming": upcoming,
            "recent_awards": recent_awards,
            "agency_count": m["mdas_total"],
        },
    )


def _tender_queryset(request):
    """Apply the register's filters. Shared by the HTML page and the CSV export so
    the two can never disagree about what 'the filtered set' means."""
    q = (request.GET.get("q") or "").strip()[:120]
    status = (request.GET.get("status") or "").strip().upper()
    agency = (request.GET.get("agency") or "").strip()[:40]
    method = (request.GET.get("method") or "").strip().upper()
    show_open = request.GET.get("open") == "1"
    sort = (request.GET.get("sort") or "closing").strip()

    qs = Tender.objects.public().select_related("agency")
    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(ocid__icontains=q)
            | Q(reference__icontains=q)
            | Q(agency__name__icontains=q)
            | Q(agency__code__icontains=q)
            | Q(awards__bid__supplier__legal_name__icontains=q)
        ).distinct()
    if status in Tender.Status.values:
        qs = qs.filter(status=status)
    if agency:
        qs = qs.filter(agency__code__iexact=agency)
    if method in {code for code, _ in Tender._meta.get_field("method").choices}:
        qs = qs.filter(method=method)
    if show_open:
        qs = qs.filter(status=Tender.Status.PUBLISHED, submission_close_at__gt=timezone.now())

    orderings = {
        "closing": ("submission_close_at", "-published_at"),
        "published": ("-published_at", "-id"),
        "value": ("-est_value", "-published_at"),
        "title": ("title",),
    }
    return qs.order_by(*orderings.get(sort, orderings["closing"])), {
        "q": q,
        "status": status,
        "agency": agency,
        "method": method,
        "show_open": show_open,
        "sort": sort if sort in orderings else "closing",
    }


@require_GET
def tender_list(request):
    qs, filters = _tender_queryset(request)
    paginator = Paginator(qs, PER_PAGE)
    try:
        page = paginator.page(max(1, int(request.GET.get("page", 1))))
    except (EmptyPage, ValueError):
        page = paginator.page(paginator.num_pages)

    active = {k: v for k, v in filters.items() if v and k != "sort"}
    return render(
        request,
        "tenders.html",
        {
            "page_obj": page,
            "rows": page.object_list,
            "total": paginator.count,
            "filters": filters,
            "active_filters": active,
            "query_string": request.GET.urlencode(),
            "agency_list": Agency.objects.filter(is_active=True).order_by("code"),
            "method_choices": Tender._meta.get_field("method").choices,
            "status_choices": [
                (s, l) for s, l in Tender.Status.choices if s not in ("DRAFT", "CANCELLED")
            ],
        },
    )


@require_GET
def tender_export_csv(request):
    """Download exactly the rows the register is showing.

    A journalist's second question after 'who won this one?' is 'send me all of
    them'. The API answers that for programmers; this answers it for the
    spreadsheet, which is what most desks actually use.
    """
    qs, filters = _tender_queryset(request)
    stamp = timezone.localdate().isoformat()
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="taraba-tenders-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "ocid", "reference", "title", "agency", "method", "status",
            "estimated_value_ngn", "awarded_value_ngn", "published_at", "closes_at",
            "bid_count", "source_url",
        ]
    )
    rows = (
        qs.select_related("agency")
        .annotate(n_bids=Count("bids", distinct=True))
        .prefetch_related("awards")[:MAX_CSV_ROWS]
    )
    for t in rows:
        awarded = sum((a.amount for a in t.awards.all() if a.amount), Decimal("0"))
        writer.writerow(
            [
                t.ocid, t.reference, t.title, t.agency.code, t.method, t.status,
                t.est_value or "", awarded or "",
                timezone.localtime(t.published_at).isoformat() if t.published_at else "",
                timezone.localtime(t.submission_close_at).isoformat() if t.submission_close_at else "",
                t.n_bids, request.build_absolute_uri(f"/tenders/{t.ocid}/"),
            ]
        )
    return response


def _stages(tender: Tender) -> list[dict]:
    """The state machine as a list of stages with their dates.

    Publishes *where the process is* and *what has already happened*, which is
    the first question a bidder asks and the thing a screenshot of a status pill
    cannot answer.
    """
    order = [
        ("PUBLISHED", "Published", tender.published_at, "Advertisement opens; the pack and the estimate are public."),
        ("CLOSED", "Submissions closed", tender.submission_close_at, "Bids are sealed; commitment hashes are already public."),
        ("OPENED", "Bids opened", tender.opening_at, f"Public opening at {tender.opening_venue or 'the Bureau'}."),
        ("EVALUATING", "Under evaluation", None, "Committee scores against the published criteria."),
        ("AWARDED", "Awarded", None, "Award notice published with the reason for the decision."),
        ("CONTRACTED", "Contract signed", None, "Implementation, variations and payments become public."),
    ]
    rank = {
        "PUBLISHED": 0, "CLARIFYING": 0, "CLOSED": 1, "OPENED": 2, "EVALUATING": 3,
        "AWARDED": 4, "CONTRACTED": 5, "FROZEN": 3, "TERMINATED": 1, "CANCELLED": 0, "DRAFT": -1,
    }
    here = rank.get(tender.status, 0)
    award = tender.awards.first()
    contract = getattr(award, "contract", None) if award else None
    dates = {
        "AWARDED": award.published_at if award else None,
        "CONTRACTED": contract.signed_at if contract else None,
    }
    stages = []
    for i, (code, label, when, blurb) in enumerate(order):
        when = dates.get(code) or when
        stages.append(
            {
                "code": code,
                "label": label,
                "when": when,
                "blurb": blurb,
                "state": "done" if i < here else ("current" if i == here else "todo"),
            }
        )
    return stages


@require_GET
def tender_detail(request, ocid: str):
    tender = get_object_or_404(
        Tender.objects.public()
        .select_related("agency", "budget_line", "rule", "created_by")
        .prefetch_related(
            "documents", "questions", "committee__user", "lots", "criteria",
            "objections__panelists", "bids__supplier__verifications", "awards__bid__supplier",
        ),
        ocid=ocid,
    )
    flags = evaluate_tender(tender)
    try:
        release = tender.ocds_release
        validate_release(release)
        ocds_ok, ocds_error = True, ""
    except OCDSValidationError as exc:
        release, ocds_ok, ocds_error = tender.ocds_release, False, str(exc)

    reveal = tender.status in (
        Tender.Status.OPENED, Tender.Status.EVALUATING, Tender.Status.AWARDED,
        Tender.Status.CONTRACTED, Tender.Status.FROZEN,
    )
    award = tender.awards.first()
    return render(
        request,
        "tender.html",
        {
            "t": tender,
            "flags": flags,
            "public_flags": [f for f in flags if f.get("public")],
            "bids": tender.bids.select_related("supplier").order_by("amount"),
            "reveal": reveal,
            "stages": _stages(tender),
            "award": award,
            "contract": getattr(award, "contract", None) if award else None,
            "ocds": release,
            "ocds_ok": ocds_ok,
            "ocds_error": ocds_error,
            "events": ledger.history(f"procurement.Tender.{tender.pk}"),
            "indicators": INDICATORS,
            "INDICATOR_VERSION": INDICATOR_VERSION,
            "internal_flags": len([f for f in flags if not f.get("public")]),
        },
    )


@require_GET
def document_meta(request, ocid: str, doc_id: int):
    """Document metadata + hash. No public bucket, no directory listing."""
    tender = get_object_or_404(Tender.objects.public(), ocid=ocid)
    doc = get_object_or_404(tender.documents, pk=doc_id, published=True)
    return JsonResponse(
        {
            "ocid": tender.ocid,
            "title": doc.title,
            "kind": doc.kind,
            "version": doc.version,
            "sha256": doc.sha256,
            "size_bytes": doc.size_bytes,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "note": doc.note,
            "integrity": "verify with: sha256sum <file> — the digest must equal sha256 above",
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
    year = request.GET.get("year") or ""
    agency = (request.GET.get("agency") or "").strip()[:40]
    method = (request.GET.get("method") or "").strip().upper()

    qs = Award.objects.filter(
        status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED]
    ).select_related("bid__supplier", "tender__agency", "approved_by")

    if year.isdigit():
        qs = qs.filter(tender__published_at__year=int(year))
    else:
        year = ""
    if agency:
        qs = qs.filter(tender__agency__code__iexact=agency)
    if method:
        qs = qs.filter(tender__method=method)

    agg = qs.aggregate(total=Sum("amount"), n=Count("id"))
    total = agg["total"] or Decimal("0")
    count = agg["n"] or 0

    # Method mix, computed once for the whole filtered set: a bar chart that is
    # a table underneath, so it is readable by a screen reader and by anyone
    # printing the page.
    mix = list(
        qs.values("tender__method")
        .annotate(n=Count("id"), value=Sum("amount"))
        .order_by("-value")
    )
    for row in mix:
        row["pct"] = (row["value"] / total * 100) if total else Decimal("0")

    years = sorted(
        {y for y in Award.objects.values_list("tender__published_at__year", flat=True) if y},
        reverse=True,
    )
    return render(
        request,
        "awards.html",
        {
            "rows": qs.order_by("-published_at", "-id")[:200],
            "count": count,
            "total": total,
            "mix": mix,
            "year": year,
            "agency": agency,
            "method": method,
            "years": years,
            "agency_list": Agency.objects.filter(is_active=True).order_by("code"),
            "method_choices": Tender._meta.get_field("method").choices,
            "active_filters": {k: v for k, v in
                               {"year": year, "agency": agency, "method": method}.items() if v},
        },
    )


@require_GET
def contract_detail(request, reference: str):
    contract = get_object_or_404(
        Contract.objects.select_related(
            "award__bid__supplier", "award__tender__agency", "award__approved_by"
        ).prefetch_related("events", "acceptances__certified_by", "certifications__certified_by"),
        reference=reference,
    )
    return render(
        request,
        "contract.html",
        {
            "c": contract,
            "events": contract.events.filter(published=True).order_by("-occurred_at"),
            "acceptances": contract.acceptances.select_related("certified_by"),
            "certifications": contract.certifications.select_related("certified_by").order_by("-certified_at"),
            "variation_high": contract.variation_pct > 10,
        },
    )


@require_GET
def supplier_list(request):
    q = (request.GET.get("q") or "").strip()[:120]
    category = (request.GET.get("category") or "").strip().upper()
    scope = (request.GET.get("scope") or "").strip().upper()

    qs = Party.objects.filter(is_active=True).prefetch_related("verifications")
    if q:
        qs = qs.filter(Q(legal_name__icontains=q) | Q(rc_number__icontains=q) | Q(tin__icontains=q))
    if category in Party.Category.values:
        qs = qs.filter(category=category)
    if scope in Party.Scope.values:
        qs = qs.filter(scope=scope)

    qs = qs.annotate(n_awards=Count("bids__tender__awards", distinct=True)).order_by("-n_awards", "legal_name")
    return render(
        request,
        "suppliers.html",
        {
            "rows": list(qs[:200]),
            "q": q,
            "category": category,
            "scope": scope,
            "count": qs.count(),
            "debarred": Party.objects.filter(is_active=True).exclude(debarred_from__isnull=True).count(),
            "category_choices": Party.Category.choices,
            "scope_choices": Party.Scope.choices,
            "active_filters": {k: v for k, v in {"q": q, "category": category, "scope": scope}.items() if v},
        },
    )


@require_GET
def open_data(request):
    m = _chain_state(live_metrics())
    return render(
        request,
        "open_data.html",
        {
            "m": m,
            "endpoints": [
                ("GET /api/v1/tenders", "Open and closed processes. Filters: status, agency, method, min, max, q, updated_since, open=1."),
                ("GET /api/v1/tenders/{ocid}", "Full detail: bids after opening, scores, committee, Q&A, objections."),
                ("GET /api/v1/tenders/{ocid}/ocds", "OCDS 1.1 compiled release, schema-validated before it is sent."),
                ("GET /api/v1/tenders/{ocid}/events", "Append-only ledger for that process, in order."),
                ("GET /api/v1/tenders/{ocid}/flags", "Red-flag indicators computed for that process, with the evidence."),
                ("GET /api/v1/awards", "The award register. ?year=&agency="),
                ("GET /api/v1/contracts", "Signed contracts with variations and payment certifications."),
                ("GET /api/v1/releases", "Continuous NDJSON release feed (ProZorro pattern). ?since="),
                ("GET /api/v1/bulk?year=", "Zip: NDJSON releases + award CSV + licence, for offline analysis."),
                ("GET /api/v1/suppliers", "Verified supplier register. ?q=&rc=&debarred=1"),
                ("GET /api/v1/stats", "Every figure this site displays, as JSON."),
                ("GET /api/v1/ledger/head", "Head hash plus the result of re-verifying the whole chain."),
                ("GET /api/v1/indicators", "Indicator definitions, versioned."),
                ("GET /api/v1/schema", "OpenAPI 3.1, generated from the serialisers that also validate writes."),
                ("GET /api/v1/policy/access", "Written statement that reads need no key, quota or permission."),
            ],
        },
    )


@require_GET
def indicators(request):
    """Definitions *and* live counts.

    Publishing a rule and publishing how often it fires are different acts. The
    second is what makes the first auditable: a rule that never fires, on data
    where it should, is a rule that has been disabled.
    """
    counts = {code: 0 for code in INDICATORS}
    examples = {code: [] for code in INDICATORS}
    scanned = 0
    for tender in (
        Tender.objects.public()
        .filter(status__in=[Tender.Status.OPENED, Tender.Status.EVALUATING,
                            Tender.Status.AWARDED, Tender.Status.CONTRACTED, Tender.Status.FROZEN])
        .prefetch_related("criteria", "bids__supplier__owners", "awards", "lots")
        .order_by("-published_at")[:200]
    ):
        scanned += 1
        for flag in evaluate_tender(tender):
            code = flag["code"]
            counts[code] = counts.get(code, 0) + 1
            if len(examples[code]) < 3:
                examples[code].append({"tender": tender, "detail": flag.get("detail", "")})

    rows = [
        {
            "code": code,
            "name": meta["name"],
            "definition": meta["definition"],
            "public": meta["public"],
            "count": counts.get(code, 0),
            "examples": examples.get(code, []),
        }
        for code, meta in INDICATORS.items()
    ]
    return render(
        request,
        "indicators.html",
        {
            "rows": rows,
            "VERSION": INDICATOR_VERSION,
            "scanned": scanned,
            "total_flags": sum(counts.values()),
        },
    )


@require_GET
def rules(request):
    """The threshold matrix, published as a table anyone can check a value against.

    This is the page that answers the question a contractor actually has —
    *"for ₦40m of furniture, which method must the MDA use, how long must it be
    advertised, and who approves it?"* — without them having to find a copy of
    the law. Rules are read from `ThresholdRule` rows, never hard-coded, because
    the state's own law can differ from the federal revision and the figures
    change between fiscal years.
    """
    rules_qs = ThresholdRule.objects.filter(is_active=True).order_by("-fy", "method", "min_amount")
    years = sorted({r.fy for r in rules_qs}, reverse=True)
    fy = (request.GET.get("fy") or (years[0] if years else "")).strip()
    if fy:
        rules_qs = rules_qs.filter(fy=fy)

    grouped: dict[str, list] = {}
    for rule in rules_qs:
        grouped.setdefault(rule.method, []).append(rule)

    method_help = {
        "ICB": "Open to international bidders. The longest advertisement and the most disclosure.",
        "NCB": "Open to national bidders. Two weeks or more of advertisement.",
        "RFQ": "Quotations from at least three unrelated suppliers, compared and recorded.",
        "SHOP": "Shopping or market survey for small, routine purchases.",
        "DIRECT": "No competition. Only lawful with a published written justification.",
        "CATALOGUE": "Common-use goods bought from a published price list at the lowest quoted price.",
        "TWO_STAGE": "Technical proposals first, then priced bids from those who qualify.",
        "RESTRICTED": "Only pre-qualified firms invited; the pre-qualification list is public.",
    }
    return render(
        request,
        "rules.html",
        {
            "grouped": grouped,
            "method_help": method_help,
            "years": years,
            "fy": fy,
            "total_rules": sum(len(v) for v in grouped.values()),
        },
    )


@require_GET
def help_page(request):
    """One help page, linked from the same place on every page (SC 3.2.6).

    Answers the five questions the Bureau's front desk is actually asked, in the
    order they are asked, with the phone number printed rather than hidden
    behind a contact form.
    """
    return render(
        request,
        "help.html",
        {
            "top_tenders": Tender.objects.open().select_related("agency").order_by("submission_close_at")[:5],
            "contact_phone": settings.CONTACT_PHONE,
            "contact_email": settings.CONTACT_EMAIL,
            "support_hours": settings.SUPPORT_HOURS,
        },
    )


@require_GET
def status(request):
    """Continuity, published.

    The failure mode that kills these platforms is not a hack — it is a
    certificate that quietly expired and a service nobody noticed was down.
    """
    m = _chain_state(live_metrics())
    return render(
        request,
        "status.html",
        {
            "m": m,
            "head_hash": m["ledger_head_hash"],
            "append_only": ledger.enforce_append_only(),
            "hsts": getattr(settings, "SECURE_HSTS_SECONDS", 0),
            "csp": dict(getattr(settings, "CSP_POLICY", {}) or {}),
            "placeholder_risk": bool(
                re.search(
                    r"XXX|TBD|TODO|000 0000",
                    f"{settings.CONTACT_PHONE} {settings.CONTACT_EMAIL}",
                    re.I,
                )
            ),
            "generated_at": timezone.now(),
            "published_tenders": m["published_all_time"],
            "awards": m["awards_published"],
        },
    )


@require_GET
def styleguide(request):
    """The component gallery. Developer tool, never public content."""
    if not settings.DEBUG:
        raise Http404("The style guide is only served in DEBUG.")
    return render(
        request,
        "styleguide.html",
        {
            "sample_tender": Tender.objects.public().select_related("agency").first(),
            "now": timezone.now(),
            "in5": timezone.now() + timedelta(days=5),
            "past": timezone.now() - timedelta(days=1),
            "statuses": [s for s, _ in Tender.Status.choices],
            "flags": list(INDICATORS.items())[:4],
        },
    )


@require_GET
def metrics(request):
    """JS embed so an MDA's own site can publish the same honest numbers."""
    m = live_metrics()
    payload = {**m, "total_award_value": float(m["total_award_value"] or 0)}
    body = "window.TARABA_PROCUREMENT_METRICS = " + json.dumps(payload, default=str) + ";"
    resp = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    resp["Cache-Control"] = "public, max-age=120"
    return resp
