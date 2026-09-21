"""Catalogue (common-use goods) — public read views.

The transparency question a catalogue answers is *"what does the state pay for a
box of gloves, and who does it buy them from?"*. So this module publishes the
price list and the comparison that decides the winner, and nothing else: raising
a purchase order is an MDA action performed inside the authenticated workspace,
not from a public page. (An earlier revision exposed `create_purchase_order` on
the public URL conf, which would have let any anonymous visitor mint a purchase
order — a hole no amount of styling can compensate for.)
"""
from __future__ import annotations

from django.db.models import Count, Min
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, render

from workflow.models import CatalogueItem, CatalogueQuote, PurchaseOrder

#: The platform will not raise a purchase order without this many competing
#: quotes. Published because the rule that constrains spending must be visible.
MIN_QUOTES_FOR_ORDER = 3


def _items():
    return CatalogueItem.objects.filter(is_active=True).select_related("supplier")


def catalogue_list(request):
    category = (request.GET.get("category") or "").strip()
    q = (request.GET.get("q") or "").strip()

    items = _items()
    if category:
        items = items.filter(category__iexact=category)
    if q:
        items = items.filter(name__icontains=q)

    items = items.order_by("category", "unit_price")

    # Cheapest quote per SKU, plus how many suppliers compete for it: this is
    # the "≥3 quotes → L1" rule made legible on one screen.
    comparison = (
        items.values("sku", "name", "unit", "category")
        .annotate(
            best_price=Min("unit_price"),
            supplier_count=Count("supplier", distinct=True),
            best_lead=Min("lead_time_days"),
        )
        .order_by("category", "name")
    )

    # Cheapest supplier per SKU, resolved in ONE pass over an ordered queryset
    # rather than a query per row. Prices are shown per unit with the supplier
    # attached, because "the state pays ₦450 for a box of gloves" is only useful
    # if you can also see who it pays and what the alternative costs.
    cheapest: dict[str, CatalogueItem] = {}
    for item in items.order_by("category", "sku", "unit_price"):
        cheapest.setdefault(item.sku, item)

    rows = []
    for row in comparison:
        best = cheapest.get(row["sku"])
        row["supplier"] = best.supplier if best else None
        row["item"] = best
        row["competitive"] = row["supplier_count"] >= MIN_QUOTES_FOR_ORDER
        rows.append(row)

    categories = (
        _items().values_list("category", flat=True).distinct().order_by(Lower("category"))
    )

    return render(
        request,
        "catalogue_list.html",
        {
            "rows": rows,
            "categories": categories,
            "selected_category": category,
            "q": q,
            "min_quotes": MIN_QUOTES_FOR_ORDER,
            "item_count": len(rows),
            "po_count": PurchaseOrder.objects.count(),
        },
    )


def catalogue_detail(request, catalogue_id: int):
    """One SKU across every supplier that quotes it, cheapest first."""
    item = get_object_or_404(_items().select_related("supplier"), pk=catalogue_id)

    quotes = (
        CatalogueItem.objects.filter(sku=item.sku, is_active=True)
        .select_related("supplier")
        .order_by("unit_price", "lead_time_days")
    )
    cheapest = quotes.first()

    # Purchase orders raised against this SKU, so the published price can be
    # reconciled with what was actually paid.
    orders = (
        PurchaseOrder.objects.filter(title__icontains=item.name)
        .select_related("agency", "awarded_supplier")
        .order_by("-created_at")[:10]
    )

    return render(
        request,
        "catalogue_detail.html",
        {
            "item": item,
            "quotes": quotes,
            "cheapest": cheapest,
            "quote_count": quotes.count(),
            "min_quotes": MIN_QUOTES_FOR_ORDER,
            "orders": orders,
        },
    )


def purchase_order_list(request):
    """Every purchase order raised through the fast lane, newest first."""
    status = (request.GET.get("status") or "").strip().upper()
    orders = PurchaseOrder.objects.select_related(
        "agency", "awarded_supplier", "created_by"
    ).order_by("-created_at")
    if status:
        orders = orders.filter(status=status)

    return render(
        request,
        "purchase_order_list.html",
        {
            "orders": orders[:200],
            "total_orders": orders.count(),
            "status": status,
            "statuses": PurchaseOrder._meta.get_field("status").choices,
        },
    )


def purchase_order_detail(request, po_id: int):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related(
            "agency", "awarded_supplier", "budget_line", "created_by"
        ),
        pk=po_id,
    )
    quotes = CatalogueQuote.objects.filter(po=po).select_related("supplier").order_by("unit_price")
    return render(
        request,
        "purchase_order_detail.html",
        {"po": po, "quotes": quotes, "min_quotes": MIN_QUOTES_FOR_ORDER},
    )
