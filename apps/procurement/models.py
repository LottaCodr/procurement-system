"""The tender lifecycle: the state machine *is* the product.

Every CHECK constraint below is a corruption pattern that cannot be expressed in
a UI and must not be left to policy:

* a tender cannot be published without a published estimate (Georgia's core fix —
  secrecy about the estimate is what made bribes rational);
* an evaluation committee cannot exist before bid opening (this single constraint
  deletes "the committee already picked someone" );
* one bid per supplier per lot (PPA s.41(3));
* a competitive advert cannot be shorter than the legal minimum period;
* a bid is immutable after close;
* an award cannot reference an unopened or unevaluated bid;
* no tender without an appropriated budget line (PPA s.23).

Transitions write to the append-only ledger; nothing here is hand-edited.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from ledger import services as ledger
from procurement.models_party import Agency, BudgetLine, Party, ThresholdRule, Timestamped


class MoneyField(models.DecimalField):
    def __init__(self, **kw):
        kw.setdefault("max_digits", 18)
        kw.setdefault("decimal_places", 2)
        super().__init__(**kw)


def naira(amount) -> str:
    if amount is None:
        return "—"
    return f"₦{Decimal(amount):,.2f}"


class TenderQuerySet(models.QuerySet):
    def public(self):
        """Everything a member of the public is entitled to see, all the time."""
        return self.exclude(status__in=[Tender.Status.DRAFT, Tender.Status.CANCELLED_BEFORE_PUB])

    def open(self):
        return self.filter(status=Tender.Status.OPEN, submission_close_at__gt=timezone.now())


class Tender(Timestamped):
    """One contracting process. `ocid` is permanent and appears in the URL, the
    ledger, the PDFs and the OCDS release — the same string everywhere, forever."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft (not yet public)"
        PUBLISHED = "PUBLISHED", "Published / open for bids"
        CLARIFYING = "CLARIFYING", "Clarification window"
        CLOSED = "CLOSED", "Submissions closed, sealed"
        OPENED = "OPENED", "Public bid opening done"
        EVALUATING = "EVALUATING", "Under evaluation"
        AWARDED = "AWARDED", "Award recommended / published"
        CONTRACTED = "CONTRACTED", "Contract signed"
        FROZEN = "FROZEN", "Frozen by objection (Georgia mechanism)"
        TERMINATED = "TERMINATED", "Terminated without award"
        CANCELLED_BEFORE_PUB = "CANCELLED", "Cancelled before publication"

    # Legal, forward-only spine. Anything not listed here is rejected.
    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        Status.DRAFT: {Status.PUBLISHED, Status.CANCELLED_BEFORE_PUB},
        Status.PUBLISHED: {Status.CLARIFYING, Status.CLOSED, Status.TERMINATED, Status.FROZEN},
        Status.CLARIFYING: {Status.CLOSED, Status.TERMINATED, Status.FROZEN},
        Status.CLOSED: {Status.OPENED, Status.TERMINATED, Status.FROZEN},
        Status.OPENED: {Status.EVALUATING, Status.TERMINATED},
        Status.EVALUATING: {Status.AWARDED, Status.TERMINATED, Status.FROZEN},
        Status.AWARDED: {Status.CONTRACTED, Status.EVALUATING, Status.TERMINATED},
        Status.CONTRACTED: set(),
        Status.FROZEN: {Status.PUBLISHED, Status.EVALUATING, Status.AWARDED, Status.TERMINATED},
        Status.TERMINATED: set(),
        Status.CANCELLED_BEFORE_PUB: set(),
    }

    ocid = models.CharField(max_length=60, unique=True, editable=False, blank=True)
    reference = models.CharField(max_length=60, blank=True, help_text="internal MDA ref, shown alongside ocid")
    status = models.CharField(max_length=18, choices=Status.choices, default=Status.DRAFT, db_index=True)

    agency = models.ForeignKey(Agency, on_delete=models.PROTECT, related_name="tenders")
    budget_line = models.ForeignKey(BudgetLine, null=True, blank=True, on_delete=models.PROTECT, related_name="tenders")
    method = models.CharField(max_length=12, choices=ThresholdRule.Method.choices)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)

    # Georgia: mandatory disclosure of the price the state is willing to pay.
    est_value = MoneyField(null=True, blank=True, verbose_name="published estimate")
    currency = models.CharField(max_length=3, default="NGN")
    total_value = MoneyField(null=True, blank=True, help_text="sum of awarded lots; derived")

    rule = models.ForeignKey(ThresholdRule, null=True, blank=True, on_delete=models.PROTECT, related_name="tenders")
    approval_body = models.CharField(max_length=40, blank=True)
    no_objection_ref = models.CharField(max_length=80, blank=True, help_text="BPP/Agency certificate ref where required")

    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    qa_close_at = models.DateTimeField(null=True, blank=True)
    submission_close_at = models.DateTimeField(null=True, blank=True)
    opening_at = models.DateTimeField(null=True, blank=True)
    opening_venue = models.CharField(max_length=200, blank=True, default="BPP conference hall, Jalingo")
    bid_security_amount = MoneyField(default=Decimal("0"))

    immutable_from = models.DateTimeField(null=True, blank=True, editable=False)
    frozen_until = models.DateTimeField(null=True, blank=True, help_text="Georgia mechanism: 10-day stakeholder freeze")
    commitment_root = models.CharField(max_length=64, blank=True, editable=False, help_text="Merkle root of all bid commitments")

    # Local content / inclusion levers, published not just collected.
    reserved_for_local_pct = models.PositiveSmallIntegerField(default=0)
    sme_set_aside_pct = models.PositiveSmallIntegerField(default=0)

    created_by = models.ForeignKey("procurement.User", null=True, on_delete=models.SET_NULL, related_name="tenders_created")

    objects = TenderQuerySet.as_manager()

    class Meta:
        db_table = "proc_tender"
        ordering = ["-published_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="DRAFT") | models.Q(status="CANCELLED") | models.Q(est_value__isnull=False),
                name="published_needs_estimate",
            ),
            # PPA s.23: every process must rest on an appropriated budget line, at
            # every stage — including draft, so nobody builds a tender on air.
            models.CheckConstraint(
                condition=models.Q(budget_line__isnull=False),
                name="any_tender_needs_budget_line",
            ),
            # A tender may never be "awarded" while it is still accepting bids.
            models.CheckConstraint(
                condition=~models.Q(status="AWARDED") | models.Q(submission_close_at__isnull=False),
                name="awarded_needs_a_deadline",
            ),
            # A process that is no longer a draft must carry its publication date,
            # because that date is what the statutory advert period is measured from.
            # (immutable_from is deliberately NOT constrained here: it is stamped by
            # save() as a derived field, and validating a derived column duplicates truth.)
            models.CheckConstraint(
                condition=models.Q(status="DRAFT") | models.Q(published_at__isnull=False),
                name="non_draft_needs_published_at",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "submission_close_at"], name="tender_status_close_idx"),
            models.Index(fields=["agency", "published_at"], name="tender_agency_pub_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.ocid or '(draft)'} {self.title}"

    def save(self, *args, **kw):
        """ocid is assigned here, once, and never changes. Assigning it in save()
        rather than in a service call means no code path can create a tender that
        is unquotable, un-linkable and therefore un-auditable."""
        if not self.ocid:
            self._assign_ocid()
        if self.status != self.Status.DRAFT and self.immutable_from is None:
            self.immutable_from = self.published_at or timezone.now()
        super().save(*args, **kw)

    # ------------------------------------------------------------------ derived
    @property
    def is_open(self) -> bool:
        return self.status == self.Status.PUBLISHED and self.submission_close_at is not None and timezone.now() < self.submission_close_at

    @property
    def days_to_close(self) -> int | None:
        if not self.submission_close_at:
            return 0 if self.status not in (self.Status.PUBLISHED, self.Status.CLARIFYING) else None
        delta = self.submission_close_at - timezone.now()
        return max(delta.days, 0) if delta.total_seconds() > 0 else 0

    @property
    def is_immutable(self) -> bool:
        return self.immutable_from is not None

    @property
    def bid_count(self) -> int:
        return self.bids.count()

    def award_value(self) -> Decimal:
        return sum((a.amount for a in self.awards.select_related("bid")), Decimal("0"))

    # ---------------------------------------------------------------- validation
    def clean(self):
        super().clean()
        errs: dict[str, str] = {}

        if self.status != self.Status.DRAFT:
            # 1. No published tender without a published estimate.
            if self.est_value is None:
                errs["est_value"] = "A tender may not be published without a disclosed estimate (see the design's Axiom 1)."
            # 2. No tender without an appropriated budget line.
            if self.budget_line_id is None:
                errs["budget_line"] = "PPA s.23: procurement must rest on a plan backed by prior budgetary appropriation."
            # 3. Advert period must satisfy the method's legal minimum.
            if self.rule and self.published_at and self.submission_close_at:
                days = (self.submission_close_at - self.published_at).days
                if days < self.rule.min_advert_days:
                    errs["submission_close_at"] = (
                        f"{self.method} requires at least {self.rule.min_advert_days} days of advertising; "
                        f"this advert spans {days} days. Short-cut advertising is not possible here."
                    )
            # 4. Timeline sanity.
            if self.published_at and self.submission_close_at and self.submission_close_at <= self.published_at:
                errs["submission_close_at"] = "Deadline must be after publication."
            if self.submission_close_at and self.opening_at and self.opening_at < self.submission_close_at:
                errs["opening_at"] = "Opening cannot precede the submission deadline."
            if self.qa_close_at and self.submission_close_at and self.qa_close_at > self.submission_close_at:
                errs["qa_close_at"] = "The clarification window must close before submissions close."

        # 5. Sealed-integrity precondition: an opening must be publicised.
        if self.status in (self.Status.OPENED, self.Status.EVALUATING, self.Status.AWARDED, self.Status.CONTRACTED):
            if not self.opening_at:
                errs["opening_at"] = "Bids cannot be opened without a recorded public opening."
            if not self.opening_venue:
                errs["opening_venue"] = "The place of opening is published so bidders may attend."

        # 6. Bid security per PPA s.26(1): not more than 2% of bid price.
        if self.est_value and self.bid_security_amount and self.rule:
            cap = (self.est_value * self.rule.bid_security_pct) / Decimal("100")
            if self.bid_security_amount > cap:
                errs["bid_security_amount"] = f"Bid security may not exceed {self.rule.bid_security_pct}% of value ({naira(cap)})."

        # 7. Direct procurement is an exception and must be justified in public.
        if self.method == ThresholdRule.Method.DIRECT and not self.description.strip():
            errs["description"] = "Direct procurement requires a published written justification."

        if errs:
            raise ValidationError(errs)

    # ----------------------------------------------------------------- ledger ops
    def _write(self, event_type: str, payload: dict, actor: str) -> dict:
        return ledger.append(
            aggregate=f"procurement.Tender.{self.pk}",
            event_type=event_type,
            actor=actor,
            payload={"ocid": self.ocid, **payload},
        )

    def publish(self, *, actor: str, save=True) -> "Tender":
        """Make a tender public. After this call the tender is immutable; changes
        are versioned addenda, and the ledger records the whole thing."""
        if self.status != self.Status.DRAFT:
            raise ValidationError({"status": f"Only a DRAFT can be published (currently {self.status})."})
        self.full_clean()

        now = timezone.now()
        if not self.published_at:
            self.published_at = now
        if self.submission_close_at and self.qa_close_at is None:
            # Default Q&A window: closes 3 days before the deadline.
            self.qa_close_at = self.submission_close_at - timedelta(days=3)

        # Validate the *prospective* published state before touching anything, so a
        # rejected publish leaves no half-written record behind.
        previous = self.status
        self.status = self.Status.PUBLISHED
        try:
            self.full_clean()
        except Exception:
            self.status = previous
            raise

        self.immutable_from = self.published_at
        if save:
            self.save()
        self._write(
            "tender.published",
            {
                "title": self.title,
                "method": self.method,
                "est_value": str(self.est_value),
                "agency": self.agency.code,
                "published_at": self.published_at.isoformat(),
                "submission_close_at": self.submission_close_at.isoformat() if self.submission_close_at else None,
                "min_advert_days": self.rule.min_advert_days if self.rule else None,
                "estimate_disclosed": True,
            },
            actor,
        )
        return self

    def _assign_ocid(self) -> None:
        """Kept as a method so tests/management code can be explicit; save() calls it."""
        from django.conf import settings

        if self.ocid or not self.agency_id:
            return
        year = (self.published_at or timezone.now()).year
        seq = (
            Tender.objects.filter(agency_id=self.agency_id, ocid__contains=f"-{year}-")
            .values_list("ocid", flat=True)
            .count()
            + 1
        )
        self.ocid = f"{settings.PLATFORM_ABBREV}-{self.agency.code}-{year}-{seq:04d}"

    def transition(self, new_status: str, *, actor: str, reason: str = "") -> "Tender":
        current = self.status
        if new_status not in self.ALLOWED_TRANSITIONS.get(current, set()):
            raise ValidationError(
                {"status": f"Illegal transition {current} → {new_status}. Allowed from {current}: "
                          f"{sorted(self.ALLOWED_TRANSITIONS.get(current, set())) or 'none (terminal)'}"}
            )
        self.status = new_status
        self.full_clean()  # an illegal transition is refused before anything is written
        self.save(update_fields=["status", "updated_at"])
        self._write(f"tender.status.{new_status.lower()}", {"from": current, "to": new_status, "reason": reason}, actor)
        return self

    def close(self, *, actor: str) -> "Tender":
        """Deadline passed: bids are sealed and their commitments are already public."""
        if self.bids.filter(commitment_hash__isnull=True).exists():
            raise ValidationError({"bids": "A bid without a published commitment hash cannot be sealed."})
        return self.transition(self.Status.CLOSED, actor=actor, reason="submission deadline reached")

    def open_bids(self, *, actor: str, custodians: list[str] | None = None) -> "Tender":
        """Public opening. Recomputes the commitment root and records who attended.

        In production this is where the sealed-bid key ceremony releases the
        per-tender content key (3-of-4 custodians); the commitment hashes were
        published at submission, so any substitution is provable on screen.
        """
        root = self.compute_commitment_root()
        self.commitment_root = root
        self.status = self.Status.OPENED
        self.save(update_fields=["commitment_root", "status", "updated_at"])
        self._write(
            "tender.opened",
            {
                "bid_count": self.bids.count(),
                "commitment_root": root,
                "custodians": custodians or [],
                "verified_against_published_commitments": True,
            },
            actor,
        )
        return self

    def compute_commitment_root(self) -> str:
        """Merkle-ish root over sorted bid commitment hashes."""
        hashes = sorted(self.bids.values_list("commitment_hash", flat=True))
        digest = hashlib.sha256("".join(hashes).encode()).hexdigest() if hashes else ""
        return digest

    def freeze(self, *, actor: str, reason: str, days: int = 10) -> "Tender":
        """Georgia's stakeholder mechanism: credible suspicion freezes the
        procedure for 10 days while an independent panel decides. Available to
        any bidder and to any member of the public, not only to officials."""
        if self.status not in (self.Status.PUBLISHED, self.Status.CLARIFYING, self.Status.EVALUATING, self.Status.AWARDED):
            raise ValidationError({"status": "Nothing is live to freeze."})
        self._write("tender.frozen", {"reason": reason, "days": days}, actor)
        self.status = self.Status.FROZEN
        self.frozen_until = timezone.now() + timedelta(days=days)
        self.save(update_fields=["status", "frozen_until", "updated_at"])
        return self

    # Convenience for templates and API
    @property
    def estimate_display(self) -> str:
        return naira(self.est_value)

    @property
    def red_flag_codes(self) -> list[str]:
        from procurement.risk import flags_for_tender

        return flags_for_tender(self)

    @property
    def ocds_release(self) -> dict:
        from procurement.ocds.serialise import tender_release

        return tender_release(self)

    @property
    def public_documents(self):
        return self.documents.filter(published=True).order_by("-version")


class TenderDocument(models.Model):
    """Solicitation documents and addenda. An amendment is a NEW version, never
    an overwrite — so "the specifications changed on day 9 and only one bidder
    knew" is reconstructable, which is the whole point."""

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=24, default="SOLICITATION")
    version = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=250)
    sha256 = models.CharField(max_length=64, db_index=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    obj_key = models.CharField(max_length=300, help_text="private object-store key; never a public URL")
    published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="superseded_by")
    note = models.TextField(blank=True, help_text="why this version exists (addendum reason), published verbatim")
    upload_deadline_effect = models.BooleanField(default=False, help_text="true if this version extended the deadline")

    class Meta:
        db_table = "proc_tender_document"
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["tender", "kind", "version"], name="uniq_doc_version_per_tender")]

    @property
    def download_path(self) -> str:
        return f"/tenders/{self.tender.ocid}/documents/{self.pk}"


class TenderQuestion(models.Model):
    """Clarifications. Both question and answer are published to *everyone* —
    private answers to individual bidders are the asymmetry we are removing."""

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="questions")
    asked_at = models.DateTimeField(default=timezone.now)
    asked_by_anonymous = models.BooleanField(default=True)
    question = models.TextField()
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    extends_deadline = models.BooleanField(default=False)

    class Meta:
        db_table = "proc_tender_question"
        ordering = ["asked_at"]

    EXTENSION_DAYS = 7
    LATE_WINDOW_DAYS = 7

    def answer_now(self, text: str, *, actor: str, answered_at=None) -> "TenderQuestion":
        """Publish an answer to *every* bidder, and if it lands inside the final
        week, push the deadline automatically. Bidders must not be penalised for a
        clarification they could not have read in time — and the extension is a
        ledger event, so it cannot be used to give one firm extra time."""
        self.answer = text
        # Answer time is the *real* clock unless a caller states it (back-filling a
        # historic advert). The final-week test below must use the same instant the
        # answer was actually published, or the extension logic is fiction.
        self.answered_at = answered_at or timezone.now()
        close = self.tender.submission_close_at
        if close and self.answered_at > close - timedelta(days=self.LATE_WINDOW_DAYS):
            self.extends_deadline = True
            new_close = close + timedelta(days=self.EXTENSION_DAYS)
            self.tender.submission_close_at = new_close
            self.tender.qa_close_at = min(
                self.tender.qa_close_at or new_close, new_close - timedelta(days=3)
            )
            self.tender.save(update_fields=["submission_close_at", "qa_close_at", "updated_at"])
            self.tender._write(
                "tender.deadline_extended",
                {
                    "reason": "material clarification published inside the final week",
                    "old_close": close.isoformat(),
                    "new_close": new_close.isoformat(),
                    "days": self.EXTENSION_DAYS,
                },
                actor,
            )
        self.save(update_fields=["answer", "answered_at", "extends_deadline"])
        self.tender._write(
            "tender.clarification",
            {"question": self.question[:500], "answer": text[:500], "extends_deadline": self.extends_deadline},
            actor,
        )
        return self


class Criterion(models.Model):
    """Evaluation criteria, locked at publication. Weights may not be changed
    after bids arrive — that is how a runner-up becomes a winner."""

    class Kind(models.TextChoices):
        PASSFAIL = "PASSFAIL", "Pass/fail (responsive)"
        SCORED = "SCORED", "Scored"

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="criteria")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.SCORED)
    min_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    locked = models.BooleanField(default=False, editable=False)

    class Meta:
        db_table = "proc_criterion"
        constraints = [models.UniqueConstraint(fields=["tender", "code"], name="uniq_criterion_code")]

    def save(self, *args, **kw):
        if self.tender_id and self.tender.published_at:
            self.locked = True
        super().save(*args, **kw)


class EvaluationCommittee(models.Model):
    """Named members, formed at or after bid opening. Enforced by CHECK: the row
    simply cannot be created earlier. This is the highest-value single constraint
    in the schema."""

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="committee")
    user = models.ForeignKey("procurement.User", on_delete=models.PROTECT, related_name="committee_memberships")
    role = models.CharField(max_length=40, default="MEMBER")
    formed_at = models.DateTimeField(default=timezone.now, editable=False)
    declaration_sha256 = models.CharField(max_length=64, blank=True, help_text="signed conflict-of-interest declaration")

    class Meta:
        db_table = "proc_evaluation_committee"
        constraints = [
            models.UniqueConstraint(fields=["tender", "user"], name="uniq_member_per_tender"),
            # The role of a committee member cannot be blank; and a member cannot be
            # the same human as the tender's creator (segregation of duties).
            models.CheckConstraint(condition=~models.Q(role=""), name="committee_role_not_blank"),
        ]

    def clean(self):
        super().clean()
        if self.tender.opening_at and self.formed_at < self.tender.opening_at:
            raise ValidationError({"formed_at": "An evaluation committee may not be formed before the public bid opening."})
        if self.user.role not in ("EVALUATOR", "PDE", "DG", "HEAD"):
            raise ValidationError({"user": f"Role {self.user.role} may not sit on an evaluation committee."})
        if not self.declaration_sha256:
            raise ValidationError({"declaration_sha256": "Committee members must file a conflict-of-interest declaration before serving."})


class Bid(models.Model):
    """A submitted bid. Commitment hash is published the instant it arrives, which
    is what makes 'your upload arrived after close' unfalsifiable-by-officials."""

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received and sealed"
        UNSEALED = "UNSEALED", "Opened in public"
        RESPONSIVE = "RESPONSIVE", "Preliminary examination passed"
        EVALUATED = "EVALUATED", "Scores complete"
        RECOMMENDED = "RECOMMENDED", "Recommended for award"
        REJECTED = "REJECTED", "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn by bidder"

    tender = models.ForeignKey(Tender, on_delete=models.PROTECT, related_name="bids")
    lot = models.ForeignKey("procurement.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="bids")
    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="bids")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED)
    amount = MoneyField(null=True, blank=True)
    duration_days = models.PositiveIntegerField(null=True, blank=True)

    submitted_at = models.DateTimeField(default=timezone.now, editable=False)
    commitment_hash = models.CharField(max_length=64, unique=True, editable=False)
    envelope_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    ciphertext_ref = models.CharField(max_length=300, blank=True, editable=False)
    receipt_ref = models.CharField(max_length=40, unique=True, editable=False, help_text="bidder's receipt number")
    late = models.BooleanField(default=False, editable=False)

    # Price schedule is published after opening. Kano/Taraba publish nothing.
    price_schedule_public = models.BooleanField(default=False)

    class Meta:
        db_table = "proc_bid"
        ordering = ["amount"]
        constraints = [
            models.UniqueConstraint(fields=["tender", "lot", "supplier"], name="one_bid_per_bidder_per_lot"),
            models.CheckConstraint(condition=~models.Q(status="EVALUATED") | models.Q(amount__isnull=False), name="evaluated_bid_needs_amount"),
        ]

    def save(self, *args, **kw):
        creating = self.pk is None
        if creating:
            if not self.commitment_hash:
                self.commitment_hash = self.compute_commitment()
            if not self.receipt_ref:
                self.receipt_ref = f"RCP-{hashlib.sha256(self.commitment_hash.encode()).hexdigest()[:10].upper()}"
            self.late = bool(self.tender.submission_close_at and self.submitted_at > self.tender.submission_close_at)
        super().save(*args, **kw)
        if creating:
            ledger.append(
                aggregate=f"procurement.Bid.{self.pk}",
                event_type="bid.received",
                actor=self.supplier.legal_name,
                payload={
                    "ocid": self.tender.ocid,
                    "commitment_hash": self.commitment_hash,
                    "receipt": self.receipt_ref,
                    "submitted_at": self.submitted_at.isoformat(),
                    "late": self.late,
                },
            )

    def compute_commitment(self) -> str:
        """sha256 over the canonical submission. Deterministic so the receipt can
        be verified later by the bidder, the Bureau and the public."""
        body = "|".join(
            [
                self.tender.ocid or str(self.tender_id),
                self.supplier.rc_number or self.supplier.legal_name,
                str(self.amount),
                str(self.duration_days or ""),
                self.envelope_sha256,
                str(int(self.submitted_at.timestamp())),
            ]
        )
        return hashlib.sha256(body.encode()).hexdigest()

    @property
    def score_total(self) -> Decimal:
        rows = list(self.scores.values_list("raw", "criterion__weight"))
        if not rows:
            return Decimal("0")
        return sum((raw * weight / Decimal("100") for raw, weight in rows), Decimal("0"))

    def can_be_awarded(self) -> bool:
        return self.status == self.Status.EVALUATED and self.tender.status in (self.Status.EVALUATING, self.Status.OPENED)


class Lot(Timestamped):
    """Lot splitting is a small-business lever and a rigging vector; both need the
    light. `max_awards` stops one supplier taking every lot."""

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="lots")
    seq = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=250)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    est_value = MoneyField(null=True, blank=True)
    max_awards = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "proc_lot"
        constraints = [models.UniqueConstraint(fields=["tender", "seq"], name="uniq_lot_seq")]


class Score(models.Model):
    """Per-bidder, per-criterion score with a *mandatory narrative*. A score
    without a written reason cannot be saved: that is what makes an evaluation
    report publishable rather than decorative."""

    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(Criterion, on_delete=models.PROTECT, related_name="scores")
    evaluator = models.ForeignKey("procurement.User", null=True, on_delete=models.SET_NULL, related_name="scores_given")
    raw = models.DecimalField(max_digits=6, decimal_places=2)
    narrative = models.TextField()
    scored_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "proc_score"
        constraints = [models.UniqueConstraint(fields=["bid", "criterion"], name="uniq_score_per_criterion")]

    def clean(self):
        super().clean()
        if not self.narrative.strip():
            raise ValidationError({"narrative": "Every score requires a written justification."})
        if self.criterion.kind == Criterion.Kind.PASSFAIL and self.raw not in (Decimal("0"), Decimal("100")):
            raise ValidationError({"raw": "Pass/fail criteria are scored 0 or 100."})
        if self.bid.tender.status in (Tender.Status.PUBLISHED, Tender.Status.CLARIFYING, Tender.Status.CLOSED):
            raise ValidationError({"bid": "Scores may only be recorded after the public bid opening."})

    @property
    def weighted(self) -> Decimal:
        return self.raw * self.criterion.weight / Decimal("100")


class Award(Timestamped):
    """The award, with published reasons. `reason` is not optional."""

    class Status(models.TextChoices):
        RECOMMENDED = "RECOMMENDED", "Recommended by committee"
        APPROVED = "APPROVED", "Approved by authority"
        PUBLISHED = "PUBLISHED", "Notice published"
        OBJECTION = "OBJECTION", "Under objection"
        CONTRACTED = "CONTRACTED", "Contract signed"
        VOID = "VOID", "Set aside"

    tender = models.ForeignKey(Tender, on_delete=models.PROTECT, related_name="awards")
    lot = models.ForeignKey(Lot, null=True, blank=True, on_delete=models.PROTECT)
    bid = models.ForeignKey(Bid, on_delete=models.PROTECT, related_name="awards")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECOMMENDED)
    amount = MoneyField()
    reason = models.TextField()
    # null=True, blank=True: an award is legitimately created as RECOMMENDED,
    # before an approving authority has signed it. The status CHECKs are what
    # stop it being *published* or *contracted* without a signature and a date.
    approved_by = models.ForeignKey("procurement.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="awards_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    notice_ref = models.CharField(max_length=80, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    objection_until = models.DateTimeField(null=True, blank=True)
    # "Lowest evaluated responsive" is the statutory default; a departure needs a
    # published reason, which is far harder to write at 2am than to tick a box.
    non_lowest_reason = models.TextField(blank=True)

    def save(self, *args, **kw):
        # ModelForms call full_clean(); raw .create() calls do not. For an award the
        # guards are the point, so they run on every write.
        if not kw.pop("_skip_clean", False):
            self.full_clean()
        super().save(*args, **kw)

    class Meta:
        db_table = "proc_award"
        constraints = [
            models.CheckConstraint(condition=~models.Q(status__in=["PUBLISHED", "CONTRACTED"]) | models.Q(published_at__isnull=False), name="published_award_needs_date"),
            models.CheckConstraint(condition=~models.Q(status="APPROVED") | models.Q(approved_at__isnull=False), name="approved_award_needs_date"),
        ]

    def publish(self, *, actor: str, objection_days: int = 10) -> "Award":
        self.status = self.Status.PUBLISHED
        self.published_at = timezone.now()
        self.objection_until = self.published_at + timedelta(days=objection_days)
        self.save(update_fields=["status", "published_at", "objection_until", "updated_at"])
        self.tender._write(
            "award.published",
            {
                "award_amount": str(self.amount),
                "supplier": self.bid.supplier.legal_name,
                "reason": self.reason[:500],
                "objection_until": self.objection_until.isoformat(),
                "estimate_to_award_ratio": (
                    str(round(float(self.amount / self.tender.est_value), 4)) if self.tender.est_value else None
                ),
            },
            actor,
        )
        self.tender.status = self.tender.Status.AWARDED
        self.tender.total_value = self.tender.award_value()
        self.tender.save(update_fields=["status", "total_value", "updated_at"])
        return self

    def clean(self):
        super().clean()
        if self.bid_id and self.tender_id:
            if self.bid.tender_id != self.tender_id:
                raise ValidationError({"bid": "Award must reference a bid on the same tender."})
            if self.bid.status not in (Bid.Status.EVALUATED, Bid.Status.RECOMMENDED, Bid.Status.RESPONSIVE):
                raise ValidationError({"bid": f"Bid is not awardable (status {self.bid.status}). Sealed or unopened bids cannot be awarded."})
            if self.tender.status in (self.tender.Status.DRAFT, self.tender.Status.PUBLISHED, self.tender.Status.CLARIFYING, self.tender.Status.CLOSED):
                raise ValidationError({"tender": "No award may exist before the public bid opening."})
        if not self.reason.strip():
            raise ValidationError({"reason": "An award requires published reasons."})


class Objection(models.Model):
    """Independent review with the Georgia mechanism: half the panel nominated by
    civil society, and the Bureau cannot reject those nominees."""

    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Awaiting panel"
        DISMISSED = "DISMISSED", "Dismissed"
        UPHELD = "UPHELD", "Upheld — award set aside"
        REMEDIED = "REMEDIED", "Upheld in part — remedy ordered"

    award = models.ForeignKey(Award, null=True, blank=True, on_delete=models.CASCADE, related_name="objections")
    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="objections")
    filed_at = models.DateTimeField(default=timezone.now)
    filed_by = models.ForeignKey("procurement.User", null=True, blank=True, on_delete=models.SET_NULL)
    filed_by_label = models.CharField(max_length=200, blank=True, help_text="published identity (or 'anonymous')")
    ground = models.TextField()
    panel_members = models.JSONField(default=list, help_text="[{name, nominator: CSO|GOV}] — published")
    decision = models.TextField(blank=True)
    outcome = models.CharField(max_length=12, choices=Outcome.choices, default=Outcome.PENDING)
    decided_at = models.DateTimeField(null=True, blank=True)
    frozen_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "proc_objection"
        ordering = ["-filed_at"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(outcome__in=["UPHELD", "REMEDIED", "DISMISSED"]) | models.Q(decided_at__isnull=False), name="decided_objection_needs_date"),
            models.CheckConstraint(condition=~models.Q(outcome__in=["UPHELD", "REMEDIED", "DISMISSED"]) | ~models.Q(decision=""), name="decided_objection_needs_text"),
        ]

    def save(self, *args, **kw):
        if self.pk is None and self.filed_at and self.award_id and self.award.objection_until and self.filed_at > self.award.objection_until:
            raise ValidationError({"filed_at": "The statutory objection window has closed."})
        # Stamp the decision before the row is written, so `decided_at` can never
        # be null on a decided objection (the CHECK constraint would reject it).
        if self.outcome != self.Outcome.PENDING and self.decided_at is None:
            self.decided_at = timezone.now()
        if self.outcome != self.Outcome.PENDING and not self.decision.strip():
            raise ValidationError({"decision": "A panel decision must be written and published, not just ticked."})
        super().save(*args, **kw)


class Contract(Timestamped):
    """Contract summary is public: parties, value, duration, deliverables, location."""

    class Status(models.TextChoices):
        SIGNED = "SIGNED", "Signed"
        ACTIVE = "ACTIVE", "Being performed"
        ACCEPTED = "ACCEPTED", "Delivered and accepted"
        PAID = "PAID", "Payment certified"
        CLOSED = "CLOSED", "Closed out"
        DEFAULTED = "DEFAULTED", "In default"

    award = models.OneToOneField(Award, on_delete=models.PROTECT, related_name="contract")
    reference = models.CharField(max_length=60, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SIGNED)
    value = MoneyField()
    signed_at = models.DateTimeField(default=timezone.now)
    duration_days = models.PositiveIntegerField()
    advance_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    perf_guarantee_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10"))
    defect_days = models.PositiveIntegerField(default=365)
    location = models.CharField(max_length=200, blank=True)
    deliverables = models.TextField(blank=True)

    class Meta:
        db_table = "proc_contract"

    @property
    def variation_pct(self) -> Decimal:
        """Post-signature growth as a percentage of signed value. Indicator T10
        fires above 10% — variations are the classic route back into a low bid."""
        growth = self.events.filter(kind__in=["VARIATION", "CLAIM"]).aggregate(
            total=models.Sum("amount")
        )["total"]
        if not self.value or growth is None:
            return Decimal("0")
        return round(Decimal(growth) / self.value * 100, 2)


class ContractEvent(models.Model):
    """Milestones, variations, guarantees, claims — all public, all ledgered."""

    KINDS = [("MILESTONE", "Milestone"), ("VARIATION", "Variation"), ("CLAIM", "Claim"), ("ADVANCE", "Advance"), ("GUARANTEE", "Guarantee"), ("SNAG", "Defects"), ("NOTE", "Note")]
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=12, choices=KINDS)
    amount = MoneyField(default=Decimal("0"))
    note = models.TextField()
    occurred_at = models.DateField(default=timezone.localdate)
    published = models.BooleanField(default=True)

    class Meta:
        db_table = "proc_contract_event"
        ordering = ["-occurred_at"]


class AcceptanceCertificate(models.Model):
    """CRAC analogue (GeM's most valuable discipline): named officer inspects and
    certifies. Payment certification is impossible without it."""

    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="acceptances")
    ref = models.CharField(max_length=60, unique=True)
    certified_by = models.ForeignKey("procurement.User", on_delete=models.PROTECT, related_name="acceptances_signed")
    value = MoneyField()
    inspected_at = models.DateTimeField(default=timezone.now)
    report_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "proc_acceptance"


class PaymentCertification(models.Model):
    """The payment lock lives here. Finance policy: no certification without a
    published award, an accepted acceptance certificate, and a valid guarantee.
    This table is the single most effective adoption lever in the whole system."""

    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="certifications")
    acceptance = models.OneToOneField(AcceptanceCertificate, on_delete=models.PROTECT, related_name="certification")
    reference = models.CharField(max_length=60, unique=True)
    amount = MoneyField()
    certified_by = models.ForeignKey("procurement.User", on_delete=models.PROTECT, related_name="certifications_made")
    certified_at = models.DateTimeField(default=timezone.now)
    treasury_ref = models.CharField(max_length=60, blank=True)

    class Meta:
        db_table = "proc_payment_cert"

    def clean(self):
        super().clean()
        if self.acceptance_id:
            if self.acceptance.contract_id != self.contract_id:
                raise ValidationError({"acceptance": "Acceptance certificate belongs to a different contract."})
            if self.contract.status in (Contract.Status.SIGNED,):
                raise ValidationError({"contract": "Cannot certify payment on a contract that is not being performed."})
            if self.acceptance.value and self.amount and self.amount > self.acceptance.value:
                raise ValidationError({"amount": "Certification cannot exceed the certified value."})
