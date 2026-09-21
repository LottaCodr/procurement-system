"""The procurement domain: parties, rules, budget.

Kept deliberately plain: the interesting behaviour lives in `models.py` (the
tender state machine) and `services.py`. Thresholds are *data* (`ThresholdRule`)
because the state's own law governs them and they change between fiscal years.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Platform administrator"
    DG = "DG", "Bureau Director-General"
    PDE = "PDE", "Procurement unit (MDA)"
    HEAD = "HEAD", "Head of MDA"
    EVALUATOR = "EVALUATOR", "Evaluation committee member"
    APPROVER = "APPROVER", "Approving authority"
    TREASURY = "TREASURY", "Finance / payment certification"
    AUDITOR = "AUDITOR", "Audit"
    APPEALS = "APPEALS", "Independent appeals panel"
    SUPPLIER = "SUPPLIER", "Registered supplier"
    PUBLIC = "PUBLIC", "Anonymous oversight (no login)"


class User(AbstractUser):
    """One user, one named human. Shared ministry logins are a fraud vector.

    MFA is enforced in `clean()` for every internal role: suppliers may use SMS
    OTP because smartphone ownership is not universal in the state, but no
    official gets to evaluate a bid with a shared password.
    """

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.PUBLIC)
    mfa_secret = models.CharField(max_length=64, blank=True)
    mfa_enrolled_at = models.DateTimeField(null=True, blank=True)
    agency = models.ForeignKey("procurement.Agency", null=True, blank=True, on_delete=models.SET_NULL, related_name="users")

    class Meta:
        db_table = "party_user"

    def clean(self):
        from django.conf import settings
        from django.core.exceptions import ValidationError

        if self.role in settings.MFA_REQUIRED_ROLES and not self.mfa_secret:
            raise ValidationError({"mfa_secret": f"MFA enrolment is mandatory for role {self.role}."})


class Agency(Timestamped):
    """A procuring entity: ministry, department, agency or local government.

    121 bodies are "regulated" on the current site as a marketing number. Here the
    record is real and each one gets a published utilisation score, so the laggards
    are visible (design Part 9, risk 5).
    """

    code = models.CharField(max_length=12, unique=True, help_text="e.g. MOH, BPP, LGA-WUK")
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=24, default="MINISTRY")
    lga = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)
    contact_email = models.EmailField(blank=True)

    class Meta:
        db_table = "party_agency"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class ThresholdRule(models.Model):
    """One row of the state's procurement threshold matrix.

    Versioned by fiscal year and *never* edited in place: a change in thresholds
    is a new row set, so a tender can always be re-checked against the rules that
    applied on its publish date. Kano-style "the rules moved under the bidder"
    becomes impossible to hide.
    """

    class Method(models.TextChoices):
        ICB = "ICB", "International competitive bidding"
        NCB = "NCB", "National competitive bidding"
        RFQ = "RFQ", "Request for quotations"
        SHOP = "SHOP", "Shopping / market survey"
        DIRECT = "DIRECT", "Direct procurement"
        CATALOGUE = "CATALOGUE", "Catalogue fast-lane"
        TWO_STAGE = "TWO_STAGE", "Two-stage bidding"
        RESTRICTED = "RESTRICTED", "Restricted bidding"

    fy = models.CharField(max_length=9, db_index=True, help_text="e.g. 2026 or 2026/2027")
    method = models.CharField(max_length=12, choices=Method.choices)
    goods = models.CharField(max_length=16, default="GENERIC")
    min_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    max_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, help_text="null = open ended")
    approval_body = models.CharField(max_length=40, help_text="e.g. ACCOUNTING_OFFICER, MTB, BPP_NO_OBJECTION, FEC")
    min_advert_days = models.PositiveSmallIntegerField(default=14, help_text="Lagos/Nigeria rule: >= 2 weeks for competitive bidding")
    bid_security_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("2.00"), help_text="PPA s.26(1): not more than 2%")
    min_quotations = models.PositiveSmallIntegerField(default=1, help_text="RFQ: at least 3 unrelated suppliers")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "rule_threshold"
        ordering = ["-fy", "method", "min_amount"]
        constraints = [
            models.UniqueConstraint(fields=["fy", "method", "goods", "min_amount"], name="uniq_threshold_band"),
            models.CheckConstraint(condition=models.Q(min_amount__gte=Decimal("0")), name="threshold_non_negative"),
        ]

    def covers(self, amount: Decimal) -> bool:
        if amount < self.min_amount:
            return False
        return self.max_amount is None or amount < self.max_amount

    def __str__(self) -> str:
        top = f"{self.max_amount:,.0f}" if self.max_amount is not None else "∞"
        return f"{self.fy} {self.method} ₦{self.min_amount:,.0f}–{top} → {self.approval_body}"


class BudgetLine(Timestamped):
    """PPA s.23: no procurement may be formalised without budgetary appropriation.

    Enforced by Tender requiring a budget line at publish time, not by a policy
    document nobody reads.
    """

    fy = models.CharField(max_length=9, db_index=True)
    agency = models.ForeignKey(Agency, on_delete=models.PROTECT, related_name="budget_lines")
    project_code = models.CharField(max_length=60)
    description = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    source = models.CharField(max_length=40, default="STATE")
    released = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    committed = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "fin_budget_line"
        constraints = [models.UniqueConstraint(fields=["fy", "agency", "project_code"], name="uniq_budget_line")]
        indexes = [models.Index(fields=["fy", "agency"], name="budget_fy_agency_idx")]

    @property
    def available(self) -> Decimal:
        return max(self.released - self.committed, Decimal("0"))


class Party(Timestamped):
    """Any legal person: supplier, consultant, JV partner.

    Identity is a *verifiable credential bundle*, not a form. Kano captured no CAC
    and no TIN at all; Taraba captures them but stores them as strings. The
    difference that matters is the dated, attributable verification assertion, so
    the public profile can honestly print "verified against CAC on 3 Sep 2026".
    """

    class Kind(models.TextChoices):
        COMPANY = "COMPANY", "Limited liability company"
        BUSINESS_NAME = "BUSINESS_NAME", "Business name / sole proprietor"
        PARTNERSHIP = "PARTNERSHIP", "Partnership"
        COOPERATIVE = "COOPERATIVE", "Cooperative"
        JV = "JV", "Joint venture (lead partner)"

    class Scope(models.TextChoices):
        LOCAL = "LOCAL", "Taraba-domiciled"
        NATIONAL = "NATIONAL", "Nigerian, outside Taraba"

    class Category(models.TextChoices):
        A = "A", "Category A — Works (major)"
        B = "B", "Category B — Goods"
        C = "C", "Category C — Services / consultancy"
        D = "D", "Category D — ICT"

    kind = models.CharField(max_length=14, choices=Kind.choices, default=Kind.COMPANY)
    legal_name = models.CharField(max_length=250, db_index=True)
    rc_number = models.CharField(max_length=20, blank=True, help_text="CAC RC/GT number, e.g. RC1234567")
    tin = models.CharField(max_length=20, blank=True, help_text="FIRS TIN, 8 digits + dash + 4")
    ubn = models.CharField(max_length=20, blank=True, help_text="Taxation Reform Act UBN, when issued")
    pencom = models.CharField(max_length=40, blank=True, help_text="PenCom pension certificate ref")
    website = models.URLField(blank=True)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=24)
    address = models.TextField(blank=True)
    state = models.CharField(max_length=40, default="Taraba")
    lga = models.CharField(max_length=60, blank=True)
    scope = models.CharField(max_length=8, choices=Scope.choices, default=Scope.LOCAL)
    category = models.CharField(max_length=1, choices=Category.choices, default=Category.B)
    year_incorporated = models.PositiveSmallIntegerField(null=True, blank=True)
    # Beneficial ownership: the single most useful anti-capture field, and absent
    # from both Kano and Taraba. >=5% owners; drives the shared-owner red flag.
    bo_declared = models.BooleanField(default=False, help_text="beneficial ownership disclosed")
    pep_flag = models.BooleanField(default=False, help_text="politically exposed person among owners")
    is_active = models.BooleanField(default=True)
    debarred_from = models.DateField(null=True, blank=True)
    debarred_to = models.DateField(null=True, blank=True)
    debarment_reason = models.TextField(blank=True)
    # Performance tracking
    performance_score = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("100.00"),
        help_text="Performance score out of 100, updated on contract close-out"
    )
    total_contracts_completed = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "party_supplier"
        ordering = ["legal_name"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(rc_number="") | models.Q(is_active=False), name="supplier_needs_rc"),
            models.CheckConstraint(condition=~models.Q(tin="") | models.Q(is_active=False), name="supplier_needs_tin"),
        ]

    def __str__(self) -> str:
        return self.legal_name

    def update_performance_score(self):
        """Update performance score based on contract close-out ratings."""
        from procurement.models_additional import ContractCloseOut

        close_outs = ContractCloseOut.objects.filter(
            contract__award__supplier=self
        )

        if not close_outs.exists():
            return

        # Calculate weighted average
        weights = {
            'EXCELLENT': 100,
            'SATISFACTORY': 80,
            'POOR': 40,
            'UNSATISFACTORY': 20,
        }

        total_score = sum(weights.get(co.performance_rating, 50) for co in close_outs)
        avg_score = total_score / close_outs.count()

        self.performance_score = Decimal(str(avg_score))
        self.total_contracts_completed = close_outs.count()
        self.save(update_fields=['performance_score', 'total_contracts_completed'])

    @property
    def is_debarred(self) -> bool:
        from django.utils import timezone

        if not self.debarred_from:
            return False
        today = timezone.localdate()
        return self.debarred_from <= today and (self.debarred_to is None or today <= self.debarred_to)


class PartyVerification(models.Model):
    """A dated assertion that someone checked one credential against a source."""

    class Kind(models.TextChoices):
        CAC = "CAC", "Corporate affairs (CAC registry)"
        TIN = "TIN", "Tax identification (FIRS)"
        PENSION = "PENSION", "PenCom certificate"
        ITF = "ITF", "Industrial Training Fund compliance"
        NSITF = "NSITF", "NSITF employee-compensation compliance"
        BANK = "BANK", "NUBAN account-name match"
        BO = "BO", "Beneficial ownership"
        DEBARMENT = "DEBARMENT", "Debarment/exclusion screening"
        CAPABILITY = "CAPABILITY", "Technical and financial capability"

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="verifications")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    PASSED, FAILED, PENDING = "PASSED", "FAILED", "PENDING"
    status = models.CharField(max_length=7, default=PENDING)
    reference = models.CharField(max_length=120, blank=True, help_text="registry receipt / search ref")
    evidence_sha256 = models.CharField(max_length=64, blank=True)
    document = models.ForeignKey("procurement.TenderDocument", null=True, blank=True, on_delete=models.SET_NULL)
    verified_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="verifications_made")
    verified_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True, help_text="e.g. tax clearance year-end")

    class Meta:
        db_table = "party_verification"
        ordering = ["-verified_at"]
        constraints = [
            models.UniqueConstraint(fields=["party", "kind"], name="uniq_verification_per_kind"),
            models.CheckConstraint(
                condition=(models.Q(status__in=["PASSED", "FAILED"], verified_at__isnull=False) | models.Q(status="PENDING")),
                name="passed_verification_needs_date",
            ),
        ]

    @property
    def is_current(self) -> bool:
        from django.utils import timezone

        return self.status == self.PASSED and (self.expires_at is None or self.expires_at >= timezone.localdate())


class PartyOwnership(models.Model):
    """One beneficial owner (>=5%). Common owners across bidders is a red flag."""

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="owners")
    owner_name = models.CharField(max_length=250)
    owner_rc_number = models.CharField(max_length=20, blank=True, help_text="if the owner is itself a company")
    owner_nin_hash = models.CharField(max_length=64, blank=True, help_text="hash only; never store NIN in clear")
    pct = models.DecimalField(max_digits=5, decimal_places=2)
    is_pep = models.BooleanField(default=False)

    class Meta:
        db_table = "party_ownership"
        constraints = [
            models.CheckConstraint(condition=models.Q(pct__gt=Decimal("0")) & models.Q(pct__lte=Decimal("100")), name="owner_pct_range"),
            models.UniqueConstraint(fields=["party", "owner_name"], name="uniq_owner_per_party"),
        ]


class RelatedPartyDeclaration(models.Model):
    """PPA ss.18–19 conflicts: bidders must declare relationships to officials."""

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="declarations")
    relationship = models.CharField(max_length=250, help_text="free text, published verbatim")
    named_official = models.CharField(max_length=250, blank=True)
    declared_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = "party_declaration"
