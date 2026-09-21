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

from django.db.models import Avg, Count

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
    "T08_WINNER_ROTATION": {
        "name": "Suspicious winner rotation among fixed set",
        "public": True,
        "definition": "A fixed group of suppliers taking turns winning contracts in "
                      "the same LGA or category, suggesting bid-rigging cartel. "
                      "Computed across all tenders in the last 12 months.",
    },
    "T09_SPEC_CAPTURE": {
        "name": "Specification written for one supplier",
        "public": True,
        "definition": "Evaluation criteria or technical specifications that reference "
                      "a specific brand, model, or proprietary certification that only "
                      "one bidder holds. Computed by scanning criteria text for brand "
                      "names and proprietary terms.",
    },
    "T10_VARIATION_INFLATION": {
        "name": "Contract grew >10% after signature",
        "public": True,
        "definition": "Post-award variations and claims exceeding 10% of signed value.",
    },
    "T11_CYCLE_TIME": {
        "name": "Abnormally short procurement cycle",
        "public": True,
        "definition": "Tender published to award in fewer days than the legal minimum "
                      "advertising period for the method. Indicates the outcome was "
                      "decided before the process started.",
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

    # T09 specification capture ----------------------------------------------
    # Scan criteria text for brand names, model numbers, or proprietary terms
    # that would only match one supplier. This is a heuristic check.
    if tender.status in ("OPENED", "EVALUATING", "AWARDED", "CONTRACTED"):
        criteria = list(tender.criteria.all())
        brand_indicators = ["brand", "model", "make", "manufacturer", "proprietary",
                           "certified by", "authorized by", "exclusive", "patented"]
        # The scanned text is exactly the text published to bidders: the
        # criterion name and its description, plus the tender description where
        # technical specifications are carried. Scanning a field that exists
        # only in the risk engine's imagination is how this indicator used to
        # take the whole tender page down with an AttributeError.
        tender_text = (tender.description or "").lower()
        for criterion in criteria:
            text = f"{criterion.name} {criterion.description} {tender_text}".lower()

            # Check for brand-specific language
            matches = [term for term in brand_indicators if term in text]
            if matches:
                # If only one bidder meets this criterion, it's suspicious
                bidders_matching = sum(
                    1 for b in bids
                    if b.scores.filter(criterion=criterion, score__gte=criterion.min_score).exists()
                )
                if bidders_matching == 1 and len(bids) > 1:
                    flags.append(
                        _flag(
                            "T09_SPEC_CAPTURE",
                            f"Criterion '{criterion.name}' contains brand-specific terms "
                            f"({', '.join(matches[:3])}) and only 1 of {len(bids)} bidders meets it.",
                            criterion=criterion.name,
                            terms=matches[:3],
                            severity="HIGH",
                        )
                    )

    # T11 cycle-time anomaly -------------------------------------------------
    # Check if tender went from published to awarded faster than legal minimum
    if tender.published_at and tender.awards.exists():

        # Get legal minimum advertising days for this method
        min_days = {
            "NCB": 21,
            "ICB": 30,
            "RFQ": 7,
            "SHOPPING": 3,
            "DIRECT": 0,
        }.get(tender.method, 14)

        # Find earliest award date
        earliest_award = tender.awards.order_by("created_at").first()
        if earliest_award and earliest_award.created_at:
            cycle_days = (earliest_award.created_at - tender.published_at).days
            if cycle_days < min_days and min_days > 0:
                flags.append(
                    _flag(
                        "T11_CYCLE_TIME",
                        f"Tender awarded in {cycle_days} days, below legal minimum of "
                        f"{min_days} days for {tender.method}. Suggests pre-determined outcome.",
                        cycle_days=cycle_days,
                        min_days=min_days,
                        method=tender.method,
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
    from procurement.models import Tender

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
    from procurement.models_party import Party

    out = []
    for p in Party.objects.filter(bids__isnull=False).annotate(b=Count("bids", distinct=True), a=Count("bids__awards", distinct=True)):
        if p.b >= window and p.a == 0:
            out.append(_flag("T05_LOSING_SPIN", f"{p.legal_name} bid on {p.b} tenders and won none.", severity="MEDIUM", bids=p.b))
    return out


def winner_rotation(agency=None, months: int = 12, min_tenders: int = 5) -> list[dict]:
    """T08: suspicious winner rotation among a fixed set of suppliers.

    Detects when the same small group of suppliers takes turns winning
    contracts from the same agency, suggesting a bid-rigging cartel.
    """
    from procurement.models import Tender
    from procurement.models_party import Party
    from django.utils import timezone
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(days=months * 30)

    # Get tenders with awards in the period
    tenders_qs = Tender.objects.filter(
        published_at__gte=cutoff,
        awards__isnull=False,
    ).exclude(status="DRAFT")

    if agency:
        tenders_qs = tenders_qs.filter(agency=agency)

    # Group by agency and category
    from collections import defaultdict
    groups = defaultdict(list)

    for tender in tenders_qs.select_related("agency"):
        for award in tender.awards.select_related("bid__supplier"):
            key = (tender.agency_id, tender.method)
            groups[key].append({
                "tender": tender.ocid,
                "supplier": award.bid.supplier.legal_name,
                "supplier_id": award.bid.supplier_id,
                "date": tender.published_at,
            })

    flags = []

    for (agency_id, method), awards_list in groups.items():
        if len(awards_list) < min_tenders:
            continue

        # Count wins per supplier
        from collections import Counter
        supplier_wins = Counter(a["supplier_id"] for a in awards_list)

        # If a small group (2-4 suppliers) wins most contracts, suspicious
        top_suppliers = supplier_wins.most_common(4)
        if len(top_suppliers) >= 2:
            top_wins = sum(s[1] for s in top_suppliers)
            total_awards = len(awards_list)

            # If top 2-4 suppliers win >80% of contracts
            if top_wins / total_awards > 0.8 and len(top_suppliers) <= 4:
                # Check if they're taking turns (no one dominates)
                win_counts = [s[1] for s in top_suppliers]
                max_wins = max(win_counts)
                min_wins = min(win_counts)

                # If the ratio is close (no one has 3x more wins than another)
                if min_wins > 0 and max_wins / min_wins < 3:
                    supplier_names = [
                        Party.objects.get(id=s[0]).legal_name
                        for s in top_suppliers
                    ]
                    flags.append(
                        _flag(
                            "T08_WINNER_ROTATION",
                            f"{len(top_suppliers)} suppliers ({', '.join(supplier_names)}) "
                            f"won {top_wins} of {total_awards} {method} contracts "
                            f"({top_wins/total_awards*100:.0f}%), taking turns.",
                            suppliers=supplier_names,
                            win_counts=dict(zip(supplier_names, win_counts)),
                            severity="HIGH",
                        )
                    )

    return flags
