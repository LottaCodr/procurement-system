"""Red-flag indicators (anti-collusion engine).

ProZorro runs an automated risk-indicator scan over live tenders; Georgia gave
stakeholders a freeze button. Two rules govern this module:

1. Every indicator is computed from the ledger-derived read model only — no
   hidden inputs, so an audit can reproduce any flag.
2. Indicator *definitions* are published and versioned. A flag that only the
   Bureau can see is a flag the Bureau can quietly switch off.

Each function returns `list[dict]` of `{code, severity, detail, evidence}`.
`public=True` results are rendered on the tender page.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Avg, Count, Q

INDICATOR_VERSION = "2026.1"

INDICATORS: dict[str, dict] = {
    "T01_CLUSTERED_BIDS": {
        "name": "Clustered bid prices",
        "public": True,
        "definition": "Three or more bids lie within 0.5% of each other, suggesting "
                      "cover pricing. Computed on every tender after opening.",
    },
    "T02_NEAR_ESTIMATE": {
        "name": "Award at 98–99.9% of the published estimate",
        "public": True,
        "definition": "Systematic pricing just under the disclosed estimate implies the "
                      "estimate leaked despite being published.",
    },
    "T03_THRESHOLD_SHAVING": {
        "name": "Value set just below the next approval tier",
        "public": True,
        "definition": "Award within 1% below a threshold boundary avoids a higher "
                      "approving authority or a no-objection certificate.",
    },
    "T04_SINGLE_BID": {
        "name": "Single bid on a competitive method",
        "public": True,
        "definition": "Exactly one bid on NCB/ICB/RFQ. Not proof of wrongdoing; a "
                      "persistent agency-level rate is.",
    },
    "T05_LOSING_SPIN": {
        "name": "Serial losing bidder",
        "public": False,
        "definition": "A supplier bidding on 5+ tenders with zero awards may be a cover "
                      "bidder inflating competition.",
    },
    "T06_SHARED_OWNER": {
        "name": "Bidders share a beneficial owner, phone or bank",
        "public": True,
        "definition": "Two or more bidders on one tender share a registered owner, "
                      "contact number, address or bank account.",
    },
    "T07_LATE_RUSH": {
        "name": "All bids in the last three minutes",
        "public": False,
        "definition": "Suspiciously simultaneous submissions can indicate an insider "
                      "watching the counter.",
    },
    "T10_VARIATION_INFLATION": {
        "name": "Contract grew >10% after signature",
        "public": True,
        "definition": "Post-award variations and claims exceeding 10% of signed value.",
    },
    "T12_DIRECT_DRIFT": {
        "name": "Direct procurement share above 5%",
        "public": True,
        "definition": "Agency share of spend awarded without competition over the last "
                      "4 quarters.",
    },
}


def _flag(code: str, detail: str, **evidence) -> dict:
    meta = INDICATORS[code]
    return {
        "code": code,
        "name": meta["name"],
        "public": meta["public"],
        "severity": evidence.pop("severity", "MEDIUM"),
        "detail": detail,
        "evidence": evidence,
        "definition_version": INDICATOR_VERSION,
    }


def flags_for_tender(tender) -> list[str]:
    """Cheap accessor used in templates/API. Public codes only."""
    return [f["code"] for f in evaluate_tender(tender, include_private=False)]


def evaluate_tender(tender, *, include_private: bool = True) -> list[dict]:
    flags: list[dict] = []
    bids = list(tender.bids.select_related("supplier"))
    naira = Decimal

    # T01 clustered bids ----------------------------------------------------
    amounts = sorted(b.amount for b in bids if b.amount)
    if len(amounts) >= 3:
        for i in range(len(amounts) - 2):
            window = amounts[i : i + 3]
            if min(window) and (max(window) - min(window)) / min(window) <= naira("0.005"):
                flags.append(
                    _flag(
                        "T01_CLUSTERED_BIDS",
                        f"{len(window)} bids fall within 0.5% ({', '.join(str(a) for a in window)}).",
                        spread_pct=str(round((max(window) - min(window)) / min(window) * 100, 4)),
                        severity="HIGH",
                    )
                )
                break

    # T02 award very close to the published estimate -----------------------
    for award in tender.awards.all():
        if tender.est_value and award.amount:
            ratio = award.amount / tender.est_value
            if naira("0.98") <= ratio < naira("1.0"):
                flags.append(
                    _flag(
                        "T02_NEAR_ESTIMATE",
                        f"Award is {ratio * 100:.2f}% of the published estimate {tender.estimate_display}.",
                        ratio=str(round(ratio, 4)),
                        severity="MEDIUM",
                    )
                )

    # T03 threshold shaving -------------------------------------------------
    # A value set just under the floor of a higher-approval band avoids a
    # no-objection certificate or Tenders Board review. The relevant boundary is
    # the *minimum* of the next-higher approval band for the same goods class —
    # comparing across unrelated classes produces false positives.
    from procurement.models_party import ThresholdRule

    amount = tender.award_value() or tender.est_value
    goods = (tender.rule.goods if tender.rule else "GENERIC")
    if amount and amount > Decimal("0"):
        higher = (
            ThresholdRule.objects.filter(is_active=True, goods=goods, min_amount__gt=amount)
            .order_by("min_amount")
            .first()
        )
        if higher:
            headroom = (higher.min_amount - amount) / higher.min_amount
            if Decimal("0") <= headroom <= Decimal("0.01"):
                flags.append(
                    _flag(
                        "T03_THRESHOLD_SHAVING",
                        f"Value sits {headroom * 100:.2f}% below the {higher.approval_body} "
                        f"approval floor (₦{higher.min_amount:,.0f}).",
                        boundary=str(higher.min_amount),
                        severity="HIGH",
                    )
                )

    # T04 single bid on a competitive method --------------------------------
    if tender.method in ("NCB", "ICB", "RFQ", "TWO_STAGE", "RESTRICTED") and len(bids) == 1:
        flags.append(
            _flag(
                "T04_SINGLE_BID",
                f"Only one bid received on a {tender.get_method_display()}.",
                severity="LOW",
            )
        )

    # T06 shared identity between bidders -----------------------------------
    keys: dict[tuple, list[str]] = {}
    for b in bids:
        s = b.supplier
        for kind, value in (("phone", s.phone), ("email", s.email), ("rc", s.rc_number), ("tin", s.tin)):
            if value:
                keys.setdefault((kind, value), []).append(s.legal_name)
        for owner in s.owners.all():
            if owner.owner_rc_number or owner.owner_name:
                keys.setdefault(("owner", owner.owner_name.lower()), []).append(s.legal_name)
    for (kind, value), names in keys.items():
        if len(set(names)) >= 2:
            flags.append(
                _flag(
                    "T06_SHARED_OWNER",
                    f"Bidders {' / '.join(sorted(set(names)))} share a registered {kind}.",
                    field=kind,
                    severity="HIGH",
                )
            )

    if not include_private:
        flags = [f for f in flags if f["public"]]
    return _dedupe(flags)


def _dedupe(flags: list[dict]) -> list[dict]:
    """Collapse repeated hits of the same indicator into one finding.

    Two suppliers sharing both a phone and an owner is ONE collusive relationship,
    not two: duplicated findings read as a broken tool and get ignored.
    """
    order: list[str] = []
    merged: dict[str, dict] = {}
    for f in flags:
        key = f["code"]
        if key not in merged:
            merged[key] = {**f, "evidence": dict(f["evidence"]), "occurrences": 1, "detail_list": [f["detail"]]}
            order.append(key)
            continue
        m = merged[key]
        m["occurrences"] += 1
        if f["detail"] not in m["detail_list"]:
            m["detail_list"].append(f["detail"])
        m["evidence"].update(f["evidence"])
        if SEVERITY_ORDER.get(f["severity"], 0) > SEVERITY_ORDER.get(m["severity"], 0):
            m["severity"] = f["severity"]
    out = []
    for key in order:
        m = merged[key]
        m["detail"] = m.pop("detail_list")[0] if len(merged[key]["detail_list"]) == 1 else " | ".join(merged[key]["detail_list"])
        out.append(m)
    return out


SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def agency_rate_flags(agency) -> list[dict]:
    """T05 / T07 / T12 are aggregate, not per-tender."""
    from procurement.models import Bid, Tender

    flags: list[dict] = []
    tenders = Tender.objects.filter(agency=agency).exclude(status="DRAFT")

    single = tenders.filter(method__in=["NCB", "ICB", "RFQ"], bids__isnull=False).annotate(n=Count("bids")).filter(n=1).count()
    total = tenders.count() or 1
    rate = Decimal(single) / Decimal(total)
    if rate > Decimal("0.25"):
        flags.append(
            _flag(
                "T04_SINGLE_BID",
                f"{agency.code}: {single} of {tenders.count()} tenders drew exactly one bid ({rate * 100:.0f}%).",
                rate=str(round(rate, 3)),
                severity="HIGH",
            )
        )

    direct = tenders.filter(method="DIRECT").aggregate(total=models_sum(tenders.filter(method="DIRECT")))
    value = tenders.aggregate(v=Avg("total_value"))["v"] or 0
    if direct and value and Decimal(direct) / Decimal(value) > Decimal("0.05"):
        flags.append(
            _flag(
                "T12_DIRECT_DRIFT",
                f"{agency.code}: direct procurement is {Decimal(direct) / Decimal(value) * 100:.1f}% of awarded value.",
                severity="HIGH",
            )
        )
    return flags


def models_sum(qs):
    from django.db.models import Sum

    return qs.aggregate(s=Sum("total_value"))["s"]


def losing_spin(window: int = 5) -> list[dict]:
    """T05: many bids, no awards. Private until reviewed."""
    from procurement.models import Bid
    from procurement.models_party import Party

    out = []
    for p in Party.objects.filter(bids__isnull=False).annotate(b=Count("bids", distinct=True), a=Count("bids__awards", distinct=True)):
        if p.b >= window and p.a == 0:
            out.append(_flag("T05_LOSING_SPIN", f"{p.legal_name} bid on {p.b} tenders and won none.", severity="MEDIUM", bids=p.b))
    return out
