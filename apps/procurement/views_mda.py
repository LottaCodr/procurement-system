"""Per-MDA utilisation dashboard.

Design requirement: *"publish per-MDA utilisation and per-MDA direct-procurement
share, monthly, unredacted. Sunlight on the laggards is the cheapest management
tool you have."*

Implementation note: this page previously issued roughly eight queries per
agency inside a Python loop (about 60 queries for six agencies, and it grew
linearly with every MDA onboarded). It is now four aggregate queries that group
by agency and are folded together in memory — a page a journalist refreshes on a
phone should not cost the state a database incident.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from procurement.models import Award, Contract, Tender
from procurement.models_party import Agency

#: Direct procurement above this share of value is treated as a governance
#: concern rather than a legitimate expedient. Published so the threshold is
#: arguable in public instead of applied in private.
DIRECT_SHARE_ALARM = Decimal("20")


def _money(value) -> Decimal:
    return value or Decimal("0")


def _share(part, whole) -> Decimal:
    return (part / whole * 100) if whole else Decimal("0")


def _collect(year: int) -> list[dict]:
    tenders = (
        Tender.objects.filter(published_at__year=year)
        .values("agency")
        .annotate(
            tenders=Count("id"),
            value=Sum("est_value"),
            direct=Count("id", filter=Q(method="DIRECT")),
            direct_value=Sum("est_value", filter=Q(method="DIRECT")),
        )
    )
    awards = (
        Award.objects.filter(tender__published_at__year=year, status__in=["PUBLISHED", "CONTRACTED"])
        .values("tender__agency")
        .annotate(
            awards=Count("id"),
            awarded=Sum("amount"),
            local_awards=Count("id", filter=Q(bid__supplier__scope="LOCAL")),
            local_value=Sum("amount", filter=Q(bid__supplier__scope="LOCAL")),
        )
    )
    contracts = (
        Contract.objects.filter(award__tender__published_at__year=year)
        .values("award__tender__agency")
        .annotate(
            contracts=Count("id"),
            contracted=Sum("value"),
            completed=Count("id", filter=Q(status="COMPLETED")),
        )
    )

    by_agency: dict[int, dict] = {}

    def bucket(agency_id):
        return by_agency.setdefault(
            agency_id,
            {
                "tenders": 0, "value": Decimal("0"), "direct": 0, "direct_value": Decimal("0"),
                "awards": 0, "awarded": Decimal("0"), "local_awards": 0, "local_value": Decimal("0"),
                "contracts": 0, "contracted": Decimal("0"), "completed": 0,
            },
        )

    for row in tenders:
        b = bucket(row["agency"])
        b.update(
            tenders=row["tenders"], value=_money(row["value"]),
            direct=row["direct"], direct_value=_money(row["direct_value"]),
        )
    for row in awards:
        b = bucket(row["tender__agency"])
        b.update(
            awards=row["awards"], awarded=_money(row["awarded"]),
            local_awards=row["local_awards"], local_value=_money(row["local_value"]),
        )
    for row in contracts:
        b = bucket(row["award__tender__agency"])
        b.update(
            contracts=row["contracts"], contracted=_money(row["contracted"]), completed=row["completed"],
        )

    agencies = {a.pk: a for a in Agency.objects.filter(is_active=True)}
    out = []
    for agency_id, b in by_agency.items():
        agency = agencies.get(agency_id)
        if agency is None:
            continue
        b["agency"] = agency
        b["direct_pct"] = _share(b["direct_value"], b["value"])
        b["local_pct"] = _share(b["local_value"], b["awarded"])
        b["completion_pct"] = _share(Decimal(b["completed"]), Decimal(b["contracts"]))
        b["direct_alarm"] = b["direct_pct"] > DIRECT_SHARE_ALARM
        out.append(b)
    out.sort(key=lambda r: r["value"], reverse=True)
    return out


def _totals(rows: list[dict]) -> dict:
    keys = (
        "tenders", "value", "direct", "direct_value", "awards", "awarded",
        "local_awards", "local_value", "contracts", "contracted", "completed",
    )
    totals = {k: sum((r[k] for r in rows), Decimal("0") if k not in
                     ("tenders", "direct", "awards", "local_awards", "contracts", "completed")
                     else 0) for k in keys}
    totals["direct_pct"] = _share(totals["direct_value"], totals["value"])
    totals["local_pct"] = _share(totals["local_value"], totals["awarded"])
    totals["mdas"] = len(rows)
    return totals


def mda_dashboard(request):
    year = int(request.GET.get("year") or timezone.now().year)
    rows = _collect(year)
    return render(
        request,
        "mda_dashboard.html",
        {
            "rows": rows,
            "totals": _totals(rows),
            "year": year,
            "direct_alarm": DIRECT_SHARE_ALARM,
        },
    )


def mda_detail(request, mda_code: str):
    agency = get_object_or_404(Agency, code__iexact=mda_code)
    year = int(request.GET.get("year") or timezone.now().year)

    row = next((r for r in _collect(year) if r["agency"].pk == agency.pk), None)

    tenders = (
        Tender.objects.filter(agency=agency, published_at__year=year)
        .select_related("agency")
        .order_by("-published_at")
    )
    awards = (
        Award.objects.filter(tender__agency=agency, tender__published_at__year=year)
        .select_related("tender", "bid__supplier")
        .order_by("-published_at")
    )
    contracts = (
        Contract.objects.filter(award__tender__agency=agency, award__tender__published_at__year=year)
        .select_related("award__bid__supplier", "award__tender")
        .order_by("-signed_at")
    )

    monthly = [
        {
            "month": m,
            "tenders": tenders.filter(published_at__month=m).count(),
            "value": _money(tenders.filter(published_at__month=m).aggregate(v=Sum("est_value"))["v"]),
            "awards": awards.filter(tender__published_at__month=m).count(),
            "awarded": _money(awards.filter(tender__published_at__month=m).aggregate(v=Sum("amount"))["v"]),
        }
        for m in range(1, 13)
    ]
    peak = max((r["value"] for r in monthly), default=Decimal("0")) or Decimal("1")

    return render(
        request,
        "mda_detail.html",
        {
            "agency": agency,
            "row": row,
            "tenders": tenders,
            "awards": awards,
            "contracts": contracts,
            "monthly": monthly,
            "peak": peak,
            "year": year,
        },
    )
