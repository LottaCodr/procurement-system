"""Phase 2-4 additional models that fill the gaps between the core domain model
and the operational workflows users actually perform.

Phase 2: Supplier performance, bid receipt generation, registration review queue.
Phase 3: Evaluation workspace, debrief requests, approval routing, evaluation
         report publication.
Phase 4: Contract milestones, guarantee tracking, performance ratings, variation
         alarm thresholds.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone


# ============================================================ Phase 2 additions

class SupplierPerformanceRating(models.Model):
    """Published after contract close-out. The rating follows the supplier, not
    the contract — and is visible on their public profile. This is how a good
    contractor in Wukari gets known without meeting an official.

    Computed from delivery timeliness, quality acceptance, and variation ratio.
    The methodology is public, so a supplier can dispute a rating."""

    class Grade(models.TextChoices):
        EXCELLENT = "A", "Excellent"
        GOOD = "B", "Good"
        SATISFACTORY = "C", "Satisfactory"
        POOR = "D", "Poor"
        DEFAULTED = "F", "Defaulted"

    contract = models.OneToOneField(
        "procurement.Contract", on_delete=models.CASCADE, related_name="performance"
    )
    supplier = models.ForeignKey(
        "procurement.Party", on_delete=models.CASCADE, related_name="ratings"
    )
    grade = models.CharField(max_length=1, choices=Grade.choices)
    timeliness_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    variation_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    composite = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    narrative = models.TextField(blank=True, help_text="Published rationale for the grade")
    rated_by = models.ForeignKey(
        "procurement.User", null=True, on_delete=models.SET_NULL, related_name="ratings_given"
    )
    rated_at = models.DateTimeField(default=timezone.now)
    published = models.BooleanField(default=True)

    class Meta:
        db_table = "proc_performance_rating"

    def __str__(self):
        return f"{self.supplier.legal_name} — {self.get_grade_display()} ({self.composite:.0f})"

    @staticmethod
    def compute(contract) -> dict:
        """Compute performance scores from contract events.
        Timeliness: on-time delivery = 100, late by X% of duration = proportional deduction.
        Quality: number of snags / rework events; 0 = 100, each snag = -10.
        Variation: 0% growth = 100, each 1% = -5, floor at 0."""
        from procurement.models import ContractEvent

        events = list(contract.events.all())
        duration = contract.duration_days or 1

        # Timeliness
        milestones = [e for e in events if e.kind == "MILESTONE"]
        snags = [e for e in events if e.kind == "SNAG"]
        timeliness = max(Decimal("100") - Decimal(len([m for m in milestones if m.amount < 0]) * 10), Decimal("0"))

        # Quality
        quality = max(Decimal("100") - Decimal(len(snags) * 10), Decimal("0"))

        # Variation
        var_pct = contract.variation_pct
        variation = max(Decimal("100") - (var_pct * 5), Decimal("0"))

        # Composite: weighted average
        composite = (timeliness * Decimal("0.35") + quality * Decimal("0.40") + variation * Decimal("0.25"))

        if composite >= 85:
            grade = "A"
        elif composite >= 70:
            grade = "B"
        elif composite >= 50:
            grade = "C"
        elif composite >= 30:
            grade = "D"
        else:
            grade = "F"

        return {
            "grade": grade,
            "timeliness_score": timeliness,
            "quality_score": quality,
            "variation_score": variation,
            "composite": composite,
        }


class BidReceipt(models.Model):
    """The signed, downloadable receipt a bidder gets after submission.
    Contains the commitment hash, receipt reference, and a verification URL."""

    bid = models.OneToOneField(
        "procurement.Bid", on_delete=models.CASCADE, related_name="receipt_doc"
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    pdf_obj_key = models.CharField(max_length=300, blank=True)
    signature = models.CharField(max_length=120, help_text="HMAC signature for verification")
    verification_url = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "proc_bid_receipt"


# ============================================================ Phase 3 additions

class DebriefRequest(models.Model):
    """Losing bidders may request a debrief (statutory right, SLA 10 working days).
    The request is logged, the debrief must be provided in writing, and the
    response is published (redacted for commercial sensitivity)."""

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        OVERDUE = "OVERDUE", "Overdue (>10 working days)"

    award = models.ForeignKey(
        "procurement.Award", on_delete=models.CASCADE, related_name="debriefs"
    )
    supplier = models.ForeignKey(
        "procurement.Party", on_delete=models.CASCADE, related_name="debrief_requests"
    )
    requested_at = models.DateTimeField(default=timezone.now)
    due_by = models.DateTimeField(help_text="10 working days from request")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    questions = models.TextField(blank=True, help_text="Specific questions from the losing bidder")
    response = models.TextField(blank=True, help_text="Written debrief response, published")
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(
        "procurement.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="debriefs_given"
    )
    published = models.BooleanField(default=True, help_text="Debrief response is public")

    class Meta:
        db_table = "proc_debrief"
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["award", "supplier"], name="uniq_debrief_per_supplier_per_award"
            ),
            models.CheckConstraint(
                condition=~models.Q(status="COMPLETED") | models.Q(responded_at__isnull=False),
                name="completed_debrief_needs_response_date",
            ),
        ]

    def save(self, *args, **kw):
        if not self.due_by and self.requested_at:
            from datetime import timedelta
            # 10 working days ≈ 14 calendar days
            self.due_by = self.requested_at + timedelta(days=14)
        if self.status == self.Status.COMPLETED and self.responded_at is None:
            self.responded_at = timezone.now()
        # Auto-detect overdue
        if self.status == self.Status.REQUESTED and self.due_by and timezone.now() > self.due_by:
            self.status = self.Status.OVERDUE
        super().save(*args, **kw)


class ApprovalRouting(models.Model):
    """Tracks which approval body was required for a tender based on its value
    and the threshold rules, and whether that approval was actually obtained.
    Published: the public can see if the right authority signed off."""

    class Body(models.TextChoices):
        ACCOUNTING_OFFICER = "AO", "Accounting Officer / Perm Sec"
        MINISTERIAL_TENDER_BOARD = "MTB", "Ministerial Tenders Board"
        BPP_NO_OBJECTION = "BPP", "BPP No-Objection Certificate"
        STATE_EXECUTIVE = "SEC", "State Executive Council"
        FEC = "FEC", "Federal Executive Council"

    tender = models.OneToOneField(
        "procurement.Tender", on_delete=models.CASCADE, related_name="approval_routing"
    )
    required_body = models.CharField(max_length=4, choices=Body.choices)
    required_reason = models.CharField(max_length=300, blank=True)
    approved_by_body = models.CharField(max_length=4, choices=Body.choices, blank=True)
    approval_ref = models.CharField(max_length=80, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    is_correct = models.BooleanField(default=True, help_text="Approval matches threshold rules")

    class Meta:
        db_table = "proc_approval_routing"

    def save(self, *args, **kw):
        self.is_correct = (self.required_body == self.approved_by_body) if self.approved_by_body else False
        super().save(*args, **kw)

    @staticmethod
    def determine_body(amount: Decimal, rule=None) -> str:
        """Determine which approval body is required based on value."""
        from procurement.models_party import ThresholdRule
        if rule:
            return ApprovalRouting._match_body(rule.approval_body)
        # Fallback: use the threshold matrix
        rules = ThresholdRule.objects.filter(is_active=True).order_by("-min_amount")
        for r in rules:
            if r.covers(amount):
                return ApprovalRouting._match_body(r.approval_body)
        return "AO"

    @staticmethod
    def _match_body(approval_body: str) -> str:
        mapping = {
            "ACCOUNTING_OFFICER": "AO",
            "MTB": "MTB",
            "BPP_NO_OBJECTION": "BPP",
            "SEC": "SEC",
            "FEC": "FEC",
        }
        return mapping.get(approval_body, "AO")


class EvaluationReport(models.Model):
    """The published evaluation report. Contains the full scorecards, committee
    membership, dissenting opinions, and the recommendation. This is what makes
    an evaluation report publishable rather than decorative."""

    tender = models.OneToOneField(
        "procurement.Tender", on_delete=models.CASCADE, related_name="eval_report"
    )
    report_sha256 = models.CharField(max_length=64)
    report_obj_key = models.CharField(max_length=300, blank=True)
    summary = models.TextField(help_text="Executive summary, published verbatim")
    methodology = models.TextField(blank=True, help_text="How scores were computed")
    published_at = models.DateTimeField(default=timezone.now)
    published = models.BooleanField(default=True)
    signed_by_count = models.PositiveSmallIntegerField(default=0)
    dissents_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "proc_evaluation_report"


class CommitteeDissent(models.Model):
    """A committee member's dissent on the overall recommendation. Separate from
    per-score DissentNote — this is a dissent on the final recommendation."""

    committee_member = models.ForeignKey(
        "procurement.EvaluationCommittee", on_delete=models.CASCADE, related_name="dissents_overall"
    )
    recommendation = models.ForeignKey(
        "workflow.AwardRecommendation", on_delete=models.CASCADE, related_name="dissents"
    )
    reason = models.TextField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    published = models.BooleanField(default=True)

    class Meta:
        db_table = "proc_committee_dissent"


# ============================================================ Phase 4 additions

class ContractMilestone(models.Model):
    """A contractual deliverable with a planned and actual date. Public. The
    gap between planned and actual is a performance signal."""

    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        DELIVERED = "DELIVERED", "Delivered, pending inspection"
        ACCEPTED = "ACCEPTED", "Inspected and accepted"
        REJECTED = "REJECTED", "Inspected and rejected"
        OVERDUE = "OVERDUE", "Past due date"

    contract = models.ForeignKey(
        "procurement.Contract", on_delete=models.CASCADE, related_name="milestones"
    )
    seq = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=250)
    description = models.TextField(blank=True)
    planned_date = models.DateField()
    actual_date = models.DateField(null=True, blank=True)
    value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED)
    inspected_by = models.ForeignKey(
        "procurement.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="milestones_inspected"
    )
    inspection_note = models.TextField(blank=True)
    published = models.BooleanField(default=True)

    class Meta:
        db_table = "proc_milestone"
        ordering = ["seq"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "seq"], name="uniq_milestone_seq"),
        ]

    @property
    def days_late(self) -> int:
        if self.actual_date and self.planned_date:
            return max((self.actual_date - self.planned_date).days, 0)
        if self.status == self.Status.PLANNED and timezone.localdate() > self.planned_date:
            return (timezone.localdate() - self.planned_date).days
        return 0


class ContractGuarantee(models.Model):
    """Advance payment guarantees and performance guarantees. The Bureau tracks
    expiry dates so a guarantee cannot lapse while the contract is active."""

    class Kind(models.TextChoices):
        ADVANCE = "ADVANCE", "Advance payment guarantee"
        PERFORMANCE = "PERFORMANCE", "Performance guarantee"
        RETENTION = "RETENTION", "Retention money"
        WARRANTY = "WARRANTY", "Warranty bond"

    contract = models.ForeignKey(
        "procurement.Contract", on_delete=models.CASCADE, related_name="guarantees"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    issuer = models.CharField(max_length=200, help_text="Bank or insurance company")
    reference = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    issued_at = models.DateField()
    expires_at = models.DateField()
    document_sha256 = models.CharField(max_length=64, blank=True)
    document_obj_key = models.CharField(max_length=300, blank=True)
    released_at = models.DateField(null=True, blank=True)
    released_note = models.TextField(blank=True)
    is_valid = models.BooleanField(default=True)

    class Meta:
        db_table = "proc_guarantee"
        ordering = ["-expires_at"]

    @property
    def days_to_expiry(self) -> int:
        return max((self.expires_at - timezone.localdate()).days, 0)

    @property
    def is_expired(self) -> bool:
        return timezone.localdate() > self.expires_at and self.released_at is None

    def save(self, *args, **kw):
        self.is_valid = not self.is_expired and self.released_at is None
        super().save(*args, **kw)


class PaymentSchedule(models.Model):
    """The payment plan tied to milestones. Treasury sees this when certifying
    payment — they can check the payment is for a real deliverable at the
    contractually agreed amount."""

    contract = models.ForeignKey(
        "procurement.Contract", on_delete=models.CASCADE, related_name="payment_schedule"
    )
    milestone = models.ForeignKey(
        ContractMilestone, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="payments"
    )
    seq = models.PositiveSmallIntegerField(default=1)
    description = models.CharField(max_length=250)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_cert = models.ForeignKey(
        "procurement.PaymentCertification", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="schedule_items"
    )

    class Meta:
        db_table = "proc_payment_schedule"
        ordering = ["seq"]


class ContractVariation(models.Model):
    """A formal variation to the contract. Variations >10% of signed value
    trigger the red-flag indicator T10 and require a higher approving authority.
    Every variation is published: the public sees the contract's growth."""

    class Status(models.TextChoices):
        PROPOSED = "PROPOSED", "Proposed"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    contract = models.ForeignKey(
        "procurement.Contract", on_delete=models.CASCADE, related_name="variations"
    )
    title = models.CharField(max_length=250)
    reason = models.TextField()
    amount_change = models.DecimalField(max_digits=18, decimal_places=2)
    time_change_days = models.IntegerField(default=0)
    proposed_by = models.ForeignKey(
        "procurement.User", null=True, on_delete=models.SET_NULL,
        related_name="variations_proposed"
    )
    approved_by = models.ForeignKey(
        "procurement.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="variations_approved"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PROPOSED)
    proposed_at = models.DateTimeField(default=timezone.now)
    approved_at = models.DateTimeField(null=True, blank=True)
    alarm_triggered = models.BooleanField(default=False, help_text="Variation >10% of signed value")

    class Meta:
        db_table = "proc_variation"
        ordering = ["-proposed_at"]

    @property
    def pct_of_contract(self) -> Decimal:
        if self.contract.value:
            return round(abs(self.amount_change) / self.contract.value * 100, 2)
        return Decimal("0")

    def save(self, *args, **kw):
        self.alarm_triggered = self.pct_of_contract > Decimal("10")
        super().save(*args, **kw)
        # Also create a ContractEvent for the risk engine and public timeline
        if self.status == self.Status.APPROVED and self.pk:
            from procurement.models import ContractEvent
            ContractEvent.objects.get_or_create(
                contract=self.contract,
                kind="VARIATION",
                amount=self.amount_change,
                defaults={
                    "note": f"Variation: {self.title}. Reason: {self.reason[:200]}",
                    "occurred_at": timezone.localdate(),
                    "published": True,
                },
            )
