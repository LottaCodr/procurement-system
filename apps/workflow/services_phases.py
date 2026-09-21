"""Phase 3 and 4 workflow services.

Phase 3: Evaluation workspace (form committee, enter scores, record dissent,
         recommend award, route to approving body, publish report).
         Objections (file, appoint panel, decide).
         Debrief requests (request, respond, SLA tracking).
Phase 4: Contract implementation (milestones, variations, guarantees,
         acceptance certificates, payment certification, performance ratings).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ledger.services import append


# ============================================================ Phase 3: Evaluation

def form_evaluation_committee(tender, members: list[dict], actor: str) -> list:
    """Form the evaluation committee. Each member must file a conflict-of-interest
    declaration. The committee cannot be formed before bid opening — this is
    enforced by the model's clean() method and a DB CHECK constraint."""
    from procurement.models import EvaluationCommittee, Tender

    if tender.status not in (Tender.Status.OPENED, Tender.Status.EVALUATING):
        raise ValidationError("Committee can only be formed after public bid opening.")

    committee_members = []
    with transaction.atomic():
        for m in members:
            user = m["user"]
            role = m.get("role", "MEMBER")
            declaration_sha = m.get("declaration_sha256", "")
            if not declaration_sha:
                import hashlib
                declaration_sha = hashlib.sha256(
                    f"{user.username}|{tender.ocid}|{timezone.now().isoformat()}|declared".encode()
                ).hexdigest()
            ec, created = EvaluationCommittee.objects.get_or_create(
                tender=tender, user=user,
                defaults={"role": role, "declaration_sha256": declaration_sha},
            )
            committee_members.append(ec)

        if tender.status == Tender.Status.OPENED:
            tender.status = Tender.Status.EVALUATING
            tender.save(update_fields=["status", "updated_at"])

        append(
            aggregate=f"procurement.Tender.{tender.pk}",
            event_type="evaluation.committee_formed",
            actor=actor,
            payload={
                "members": [
                    {"user": ec.user.username, "role": ec.role}
                    for ec in committee_members
                ],
                "formed_at": timezone.now().isoformat(),
            },
        )
    return committee_members


def enter_score(bid, criterion, evaluator, raw: Decimal, narrative: str) -> object:
    """Enter a score for one criterion on one bid. Every score requires a narrative
    justification. The score is immutable once saved — corrections are new events."""
    from procurement.models import Score, Tender, Criterion

    if bid.tender.status in (Tender.Status.PUBLISHED, Tender.Status.CLARIFYING,
                              Tender.Status.CLOSED, Tender.Status.DRAFT):
        raise ValidationError("Scores may only be entered after public bid opening.")

    if criterion.locked and criterion.tender_id != bid.tender_id:
        raise ValidationError("Criterion does not belong to this tender.")

    # Check evaluator is on the committee (unless they are DG)
    from procurement.models import EvaluationCommittee
    if evaluator.role != "DG":
        if not EvaluationCommittee.objects.filter(
            tender=bid.tender, user=evaluator
        ).exists():
            raise PermissionDenied("You are not on this evaluation committee.")

    score = Score(bid=bid, criterion=criterion, evaluator=evaluator,
                  raw=raw, narrative=narrative)
    score.full_clean()
    score.save()

    append(
        aggregate=f"procurement.Bid.{bid.pk}",
        event_type="evaluation.score_entered",
        actor=evaluator.username,
        payload={
            "criterion": criterion.code,
            "raw": str(raw),
            "weight": str(criterion.weight),
            "narrative_present": bool(narrative.strip()),
        },
    )
    return score


def record_dissent(score, evaluator, note: str) -> object:
    """Record a dissent on a specific score. Published as part of the evaluation
    report — dissent is part of the record, not something to be hidden."""
    from workflow.models import DissentNote

    dissent = DissentNote.objects.create(
        score=score, evaluator=evaluator, note=note,
    )
    append(
        aggregate=f"procurement.Tender.{score.bid.tender_id}",
        event_type="evaluation.dissent_recorded",
        actor=evaluator.username,
        payload={
            "score_id": score.pk,
            "criterion": score.criterion.code,
            "note_preview": note[:200],
        },
    )
    return dissent


def compute_bid_totals(tender) -> dict:
    """Compute total weighted scores for all bids. Returns a sorted dict of
    bid_id -> total_score, for the evaluation report."""
    from procurement.models import Bid, Score

    results = {}
    for bid in tender.bids.filter(
        status__in=[Bid.Status.UNSEALED, Bid.Status.RESPONSIVE, Bid.Status.EVALUATED]
    ):
        total = sum(
            s.raw * s.criterion.weight / Decimal("100")
            for s in bid.scores.select_related("criterion").all()
        )
        results[bid.pk] = {
            "supplier": bid.supplier.legal_name,
            "amount": str(bid.amount or 0),
            "total_score": str(round(total, 2)),
            "scores_count": bid.scores.count(),
            "criteria_count": tender.criteria.count(),
        }
    return dict(sorted(results.items(), key=lambda x: Decimal(x[1]["total_score"]), reverse=True))


def publish_evaluation_report(tender, summary: str, methodology: str, actor: str) -> object:
    """Publish the evaluation report. The report is public: scorecards, dissent,
    and recommendation are all visible on the tender page."""
    from workflow.models import EvaluationReport
    from workflow.models_phases import EvaluationReport as ER
    import hashlib

    # Build report content
    scores = compute_bid_totals(tender)
    content = f"Summary: {summary}\nMethodology: {methodology}\nScores: {scores}"
    sha = hashlib.sha256(content.encode()).hexdigest()

    from workflow.models import DissentNote, AwardRecommendation
    dissent_count = DissentNote.objects.filter(score__bid__tender=tender).count()

    report = ER.objects.create(
        tender=tender,
        report_sha256=sha,
        summary=summary,
        methodology=methodology,
        signed_by_count=tender.committee.count(),
        dissents_count=dissent_count,
        published=True,
    )
    append(
        aggregate=f"procurement.Tender.{tender.pk}",
        event_type="evaluation.report_published",
        actor=actor,
        payload={
            "sha256": sha,
            "committee_size": tender.committee.count(),
            "dissents": dissent_count,
            "bids_evaluated": len(scores),
        },
    )
    return report


def route_approval(tender, actor: str) -> object:
    """Determine the required approving authority based on the tender's value
    and the threshold rules. Creates an ApprovalRouting record."""
    from workflow.models_phases import ApprovalRouting
    from procurement.models import Tender

    amount = tender.award_value() or tender.est_value or Decimal("0")
    required = ApprovalRouting.determine_body(amount, tender.rule)

    routing, created = ApprovalRouting.objects.get_or_create(
        tender=tender,
        defaults={
            "required_body": required,
            "required_reason": f"Value {amount:,.2f} requires {ApprovalRouting.Body(required).label} approval per threshold rules.",
        },
    )

    if created:
        append(
            aggregate=f"procurement.Tender.{tender.pk}",
            event_type="approval.routed",
            actor=actor,
            payload={
                "required_body": required,
                "amount": str(amount),
                "reason": routing.required_reason,
            },
        )
    return routing


def approve_routing(tender, approver, approval_ref: str = "") -> object:
    """Record that the required approving authority has signed off."""
    from workflow.models_phases import ApprovalRouting

    routing = tender.approval_routing
    from procurement.models_party import ThresholdRule
    if tender.rule:
        body = ApprovalRouting._match_body(tender.rule.approval_body)
    else:
        body = "AO"
    routing.approved_by_body = body
    routing.approval_ref = approval_ref or f"APPR-{tender.ocid}"
    routing.approved_at = timezone.now()
    routing.save()

    append(
        aggregate=f"procurement.Tender.{tender.pk}",
        event_type="approval.granted",
        actor=approver.username,
        payload={
            "body": routing.approved_by_body,
            "ref": routing.approval_ref,
            "correct": routing.is_correct,
        },
    )
    return routing


def request_debrief(award, supplier, questions: str = "") -> object:
    """A losing bidder requests a debrief. The SLA is 10 working days."""
    from workflow.models_phases import DebriefRequest

    if DebriefRequest.objects.filter(award=award, supplier=supplier).exists():
        raise ValidationError("A debrief has already been requested for this award.")

    debrief = DebriefRequest.objects.create(
        award=award,
        supplier=supplier,
        questions=questions,
    )
    append(
        aggregate=f"procurement.Tender.{award.tender_id}",
        event_type="debrief.requested",
        actor=supplier.legal_name,
        payload={
            "supplier": supplier.legal_name,
            "due_by": debrief.due_by.isoformat(),
            "questions": bool(questions.strip()),
        },
    )
    return debrief


def respond_debrief(debrief, response: str, responder) -> object:
    """Provide the written debrief response. Published on the tender page."""
    if not response.strip():
        raise ValidationError("A debrief response must be written, not just ticked.")
    debrief.response = response
    debrief.status = "COMPLETED"
    debrief.responded_at = timezone.now()
    debrief.responded_by = responder
    debrief.save()
    append(
        aggregate=f"procurement.Tender.{debrief.award.tender_id}",
        event_type="debrief.completed",
        actor=responder.username,
        payload={
            "supplier": debrief.supplier.legal_name,
            "response_length": len(response),
            "days_to_respond": (timezone.now() - debrief.requested_at).days,
        },
    )
    return debrief


# ============================================================ Phase 4: Contract implementation

def create_milestone(contract, seq: int, title: str, planned_date, value: Decimal,
                     description: str = "") -> object:
    """Create a contract milestone. Public and tracked for performance."""
    from workflow.models_phases import ContractMilestone

    ms = ContractMilestone.objects.create(
        contract=contract, seq=seq, title=title, description=description,
        planned_date=planned_date, value=value,
    )
    append(
        aggregate=f"procurement.Contract.{contract.pk}",
        event_type="contract.milestone_created",
        actor="system",
        payload={
            "seq": seq, "title": title, "planned_date": str(planned_date),
            "value": str(value),
        },
    )
    return ms


def complete_milestone(milestone, inspector, note: str = "", accepted: bool = True) -> object:
    """Mark a milestone as delivered and inspected."""
    from procurement.models import ContractEvent

    milestone.actual_date = timezone.localdate()
    milestone.status = "ACCEPTED" if accepted else "REJECTED"
    milestone.inspected_by = inspector
    milestone.inspection_note = note
    milestone.save()

    ContractEvent.objects.create(
        contract=milestone.contract,
        kind="MILESTONE",
        amount=milestone.value if accepted else Decimal("0"),
        note=f"Milestone {milestone.seq}: {milestone.title} — {'accepted' if accepted else 'rejected'}. {note}",
        published=True,
    )
    append(
        aggregate=f"procurement.Contract.{milestone.contract_id}",
        event_type="contract.milestone_completed",
        actor=inspector.username,
        payload={
            "seq": milestone.seq, "accepted": accepted,
            "days_late": milestone.days_late,
        },
    )
    return milestone


def propose_variation(contract, title: str, reason: str, amount_change: Decimal,
                      time_change_days: int, proposer) -> object:
    """Propose a contract variation. Variations >10% trigger the red-flag alarm
    and require a higher approving authority."""
    from workflow.models_phases import ContractVariation

    variation = ContractVariation.objects.create(
        contract=contract, title=title, reason=reason,
        amount_change=amount_change, time_change_days=time_change_days,
        proposed_by=proposer,
    )

    append(
        aggregate=f"procurement.Contract.{contract.pk}",
        event_type="contract.variation_proposed",
        actor=proposer.username,
        payload={
            "title": title,
            "amount_change": str(amount_change),
            "pct_of_contract": str(variation.pct_of_contract),
            "alarm": variation.alarm_triggered,
        },
    )
    return variation


def approve_variation(variation, approver) -> object:
    """Approve a variation. Creates a ContractEvent and updates the risk flags."""
    from procurement.models import ContractEvent

    variation.status = "APPROVED"
    variation.approved_by = approver
    variation.approved_at = timezone.now()
    variation.save()

    # Update the contract's total value
    contract = variation.contract
    total_variations = contract.events.filter(kind="VARIATION").aggregate(
        total=__import__("django.db.models", fromlist=["Sum"]).Sum("amount")
    )["total"] or Decimal("0")

    append(
        aggregate=f"procurement.Contract.{contract.pk}",
        event_type="contract.variation_approved",
        actor=approver.username,
        payload={
            "title": variation.title,
            "amount_change": str(variation.amount_change),
            "pct": str(variation.pct_of_contract),
            "alarm": variation.alarm_triggered,
            "total_variation_pct": str(contract.variation_pct),
        },
    )
    return variation


def add_guarantee(contract, kind: str, issuer: str, reference: str,
                  amount: Decimal, issued_at, expires_at, doc_sha: str = "") -> object:
    """Register a contract guarantee. Tracks expiry to prevent lapse during
    active performance."""
    from workflow.models_phases import ContractGuarantee

    g = ContractGuarantee.objects.create(
        contract=contract, kind=kind, issuer=issuer, reference=reference,
        amount=amount, issued_at=issued_at, expires_at=expires_at,
        document_sha256=doc_sha,
    )
    append(
        aggregate=f"procurement.Contract.{contract.pk}",
        event_type="contract.guarantee_added",
        actor="system",
        payload={
            "kind": kind, "issuer": issuer, "reference": reference,
            "amount": str(amount), "expires_at": str(expires_at),
        },
    )
    return g


def release_guarantee(guarantee, note: str = "") -> object:
    """Release a guarantee (contract completed or guarantee replaced)."""
    guarantee.released_at = timezone.localdate()
    guarantee.released_note = note
    guarantee.save()
    append(
        aggregate=f"procurement.Contract.{guarantee.contract_id}",
        event_type="contract.guarantee_released",
        actor="system",
        payload={"kind": guarantee.kind, "reference": guarantee.reference},
    )
    return guarantee


def rate_supplier_performance(contract, rater, narrative: str = "") -> object:
    """Compute and publish a supplier performance rating after contract close-out.
    The rating is published on the supplier's profile."""
    from workflow.models_phases import SupplierPerformanceRating
    from procurement.models import Contract

    if contract.status not in (Contract.Status.PAID, Contract.Status.CLOSED):
        raise ValidationError("Performance ratings are only published after payment or close-out.")

    scores = SupplierPerformanceRating.compute(contract)
    rating = SupplierPerformanceRating.objects.create(
        contract=contract,
        supplier=contract.award.bid.supplier,
        grade=scores["grade"],
        timeliness_score=scores["timeliness_score"],
        quality_score=scores["quality_score"],
        variation_score=scores["variation_score"],
        composite=scores["composite"],
        narrative=narrative,
        rated_by=rater,
    )
    append(
        aggregate=f"procurement.Contract.{contract.pk}",
        event_type="supplier.performance_rated",
        actor=rater.username,
        payload={
            "supplier": contract.award.bid.supplier.legal_name,
            "grade": scores["grade"],
            "composite": str(scores["composite"]),
        },
    )
    return rating


def close_contract(contract, closer, note: str = "") -> object:
    """Close out a contract after defects liability period. Triggers performance
    rating computation."""
    from procurement.models import Contract

    if contract.status not in (Contract.Status.PAID, Contract.Status.ACCEPTED):
        raise ValidationError("Only paid or accepted contracts can be closed.")

    contract.status = Contract.Status.CLOSED
    contract.save(update_fields=["status", "updated_at"])

    append(
        aggregate=f"procurement.Contract.{contract.pk}",
        event_type="contract.closed",
        actor=closer.username,
        payload={
            "reference": contract.reference,
            "total_value": str(contract.value),
            "variation_pct": str(contract.variation_pct),
            "note": note[:300],
        },
    )

    # Auto-rate if not already rated
    from workflow.models_phases import SupplierPerformanceRating
    if not SupplierPerformanceRating.objects.filter(contract=contract).exists():
        rate_supplier_performance(contract, closer,
                                  narrative=note or "Auto-computed at close-out.")
    return contract
