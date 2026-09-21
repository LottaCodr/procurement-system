"""Defects liability and contract close-out — public read views.

The defects liability period is where retention money sits and where quality
failures surface after the ribbon-cutting. It is published for the same reason
the rest of the register is: the state's leverage over a contractor is highest
while the retention is still held, and that is exactly when citizens should be
able to see what is being ignored.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from procurement.models import Contract
from procurement.models_additional import ContractCloseOut, DefectReport


def defects_list(request):
    now = timezone.now()
    contracts = (
        Contract.objects.filter(defects_liability_end__gt=now)
        .select_related("award__tender", "award__bid__supplier")
        .order_by("defects_liability_end")
    )

    # Two queries for the whole page. The earlier revision issued two queries
    # per contract — on a 300-contract register that is 600 round trips to
    # render a list, which is how a "lightweight" page becomes a database
    # incident during a press conference.
    open_statuses = ["OPEN", "IN_PROGRESS"]
    defect_agg = {
        row["contract"]: row
        for row in DefectReport.objects.values("contract")
        .annotate(
            total=Count("id"),
            open_count=Count("id", filter=Q(status__in=open_statuses)),
            critical=Count("id", filter=Q(severity="CRITICAL")),
        )
        .order_by()
    }

    rows = []
    for contract in contracts:
        agg = defect_agg.get(contract.pk, {})
        rows.append(
            {
                "contract": contract,
                "remaining_days": max((contract.defects_liability_end - now).days, 0),
                "defect_count": agg.get("total", 0),
                "open_defects": agg.get("open_count", 0),
                "critical_defects": agg.get("critical", 0),
                "retention_held": contract.value
                * (contract.retention_pct or Decimal("0"))
                / Decimal("100")
                - (contract.retention_released or Decimal("0")),
            }
        )

    return render(
        request,
        "defects_list.html",
        {
            "rows": rows,
            "contract_count": len(rows),
            "open_total": sum(r["open_defects"] for r in rows),
            "critical_total": sum(r["critical_defects"] for r in rows),
            "retention_total": sum((r["retention_held"] for r in rows), Decimal("0")),
        },
    )


def defects_detail(request, contract_id: int):
    contract = get_object_or_404(
        Contract.objects.select_related("award__tender__agency", "award__bid__supplier"),
        pk=contract_id,
    )
    defects = DefectReport.objects.filter(contract=contract).order_by("-reported_at")

    retention_amount = contract.value * (contract.retention_pct or Decimal("0")) / Decimal("100")
    released = contract.retention_released or Decimal("0")

    close_out = None
    try:
        close_out = contract.close_out
    except ContractCloseOut.DoesNotExist:
        close_out = None

    return render(
        request,
        "defects_detail.html",
        {
            "c": contract,
            "defects": defects,
            "open_defects": defects.exclude(status__in=["VERIFIED", "CLOSED"]),
            "retention_amount": retention_amount,
            "retention_released": released,
            "retention_held": retention_amount - released,
            "close_out": close_out,
        },
    )
