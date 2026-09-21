"""Phase 2-5 models. These extend the Phase 1 spine with:
  * MFA sessions (TOTP + SMS fallback)
  * Supplier registration drafts (multi-step, save-and-resume, file uploads)
  * Sealed-bid envelope (ciphertext + sha256 + per-tender key)
  * Live clarifications, dissents, signed recommendations
  * Objection panelists (CSO/GOV nominations) and evidence uploads
  * Confidential whistleblower channel
  * Catalogue fast-lane (GeM-style L1 direct purchase)
  * Notification outbox (email/SMS/WhatsApp/USSD)
  * i18n strings + user language pref
  * Tender watch / category watch alerts
"""
from __future__ import annotations

import secrets
import string
from decimal import Decimal

from django.db import models
from django.utils import timezone


# ------------------------------------------------------------- auth + MFA
class MFASession(models.Model):
    """Time-bound second-factor challenge after a successful password. Internal
    roles must use TOTP; suppliers may use SMS OTP fallback (smartphone ownership
    is not universal in Taraba)."""

    class Method(models.TextChoices):
        TOTP = "TOTP", "Authenticator app (TOTP)"
        SMS = "SMS", "SMS OTP"

    user = models.ForeignKey("procurement.User", on_delete=models.CASCADE, related_name="mfa_challenges")
    method = models.CharField(max_length=4, choices=Method.choices)
    otp_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "auth_mfa_session"
        ordering = ["-created_at"]

    def mark_consumed(self):
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])

    def is_valid(self, window_minutes: int = 10) -> bool:
        return not self.consumed_at and timezone.now() < self.created_at + __import__("datetime").timedelta(minutes=window_minutes)


# ------------------------------------------------------------- vendor onboarding
class SupplierRegistrationDraft(models.Model):
    class Step(models.IntegerChoices):
        COMPANY = 1, "Company identity"
        CONTACT = 2, "Contact person"
        DOCUMENTS = 3, "Certificates"
        CAPABILITY = 4, "Capability & classification"
        BENEFICIAL = 5, "Beneficial ownership & declarations"
        REVIEW = 6, "Review & submit"

    draft_token = models.CharField(max_length=64, unique=True, editable=False)
    reference = models.CharField(max_length=16, blank=True, editable=False, help_text="Public reference, e.g. VND-000042")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    current_step = models.PositiveSmallIntegerField(default=1, choices=Step.choices)
    status = models.CharField(
        max_length=12,
        default="DRAFT",
        choices=[("DRAFT","Draft"),("SUBMITTED","Submitted"),("VERIFYING","Verifying"),("APPROVED","Approved"),("REJECTED","Rejected")],
    )
    company_name = models.CharField(max_length=250, blank=True)
    rc_number = models.CharField(max_length=20, blank=True)
    tin = models.CharField(max_length=20, blank=True)
    pencom = models.CharField(max_length=40, blank=True)
    year_incorporated = models.PositiveSmallIntegerField(null=True, blank=True)
    website = models.URLField(blank=True)
    state = models.CharField(max_length=40, default="Taraba")
    lga = models.CharField(max_length=60, blank=True)
    address = models.TextField(blank=True)
    category = models.CharField(max_length=1, blank=True, choices=__import__("procurement.models_party", fromlist=["Party"]).Party.Category.choices)
    scope = models.CharField(max_length=8, default="LOCAL", choices=__import__("procurement.models_party", fromlist=["Party"]).Party.Scope.choices)
    employees_count = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Staff on the payroll. Drives the statutory exemptions: PenCom does not apply below 3 employees (Pension Reform Act 2014), ITF below 5.",
    )
    full_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=24, blank=True)
    similar_contracts_n = models.PositiveIntegerField(default=0)
    annual_turnover = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    related_party_declaration = models.TextField(blank=True)
    accept_terms = models.BooleanField(default=False)
    pep_flag = models.BooleanField(default=False)
    submitted_party = models.ForeignKey("procurement.Party", null=True, blank=True, on_delete=models.SET_NULL, related_name="drafts")
    reviewer = models.ForeignKey("procurement.User", null=True, blank=True, on_delete=models.SET_NULL)
    review_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vendor_draft"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reference"],
                condition=~models.Q(reference=""),
                name="uniq_vendor_draft_reference",
            )
        ]

    def save(self, *a, **kw):
        if not self.draft_token:
            alphabet = string.ascii_letters + string.digits
            self.draft_token = "".join(secrets.choice(alphabet) for _ in range(40))
        super().save(*a, **kw)
        if not self.reference:
            # Needs a pk first; the reference is what the vendor quotes on the
            # phone to the Bureau, so it is short and sequential, unlike the
            # draft token which is an unguessable private link.
            self.reference = f"VND-{self.pk:06d}"
            super().save(update_fields=["reference"])

    def required_document_kinds(self) -> set[str]:
        """Which certificates this particular company must attach.

        The requirements are statutory, not uniform: PenCom applies only to
        employers of three or more (Pension Reform Act 2014), so a two-person
        shop in Wukari must not be blocked by a certificate it legally cannot
        obtain. CAC and TIN are unconditional.
        """
        kinds = {"CAC", "TIN"}
        from django.conf import settings as _s
        if self.employees_count is None or self.employees_count >= _s.PENCOM_MIN_EMPLOYEES:
            kinds.add("PENCOM")
        return kinds

    def ready_to_submit(self) -> list[str]:
        from workflow.validators import rc_number_errors, tin_errors

        errs = []
        for f, label in [
            ("company_name", "Company name"), ("rc_number", "RC number"), ("tin", "TIN"),
            ("full_name", "Contact name"), ("email", "Email"), ("phone", "Phone"),
            ("address", "Address"), ("category", "Category"),
        ]:
            if not getattr(self, f):
                errs.append(label)
        errs += rc_number_errors(self.rc_number) if self.rc_number else []
        errs += tin_errors(self.tin) if self.tin else []
        kinds_present = set(self.documents.values_list("kind", flat=True))
        missing_kinds = self.required_document_kinds() - kinds_present
        if missing_kinds:
            errs.append(
                "supporting documents: " + ", ".join(sorted(missing_kinds))
                + ("" if "PENCOM" not in missing_kinds else " (PenCom is not needed if you employ fewer than 3 people — enter your staff count in step 4)")
            )
        if not self.owners.exists():
            errs.append("beneficial ownership disclosure (≥5%)")
        if not self.accept_terms:
            errs.append("terms acceptance")
        return errs


class DraftDocument(models.Model):
    class Kind(models.TextChoices):
        CAC = "CAC", "CAC incorporation certificate"
        TIN = "TIN", "TIN / tax clearance"
        PENCOM = "PENCOM", "PenCom compliance certificate"
        ITF = "ITF", "ITF compliance certificate"
        NSITF = "NSITF", "NSITF compliance certificate"
        BANK = "BANK", "Bank reference / NUBAN verification"
        TECH = "TECH", "Technical capability memo"

    draft = models.ForeignKey(SupplierRegistrationDraft, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    obj_key = models.CharField(max_length=300)
    filename = models.CharField(max_length=240)
    content_type = models.CharField(max_length=80, default="application/pdf")
    # The uploaded bytes string. Kept on the row (not the filesystem) so it
    # survives serverless/ephemeral storage and can be re-hashed to prove the
    # file the reviewer approved is bit-for-bit the file the vendor submitted.
    blob = models.BinaryField(null=True, blank=True)
    issued_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    verification_status = models.CharField(max_length=12, default="PENDING", choices=[("PENDING","Pending"),("ACCEPTED","Accepted"),("REJECTED","Rejected")])
    verification_note = models.TextField(blank=True)

    class Meta:
        db_table = "vendor_draft_document"
        constraints = [models.UniqueConstraint(fields=["draft", "kind"], name="uniq_doc_kind_per_draft")]


class DraftOwner(models.Model):
    draft = models.ForeignKey(SupplierRegistrationDraft, on_delete=models.CASCADE, related_name="owners")
    owner_name = models.CharField(max_length=250)
    owner_rc = models.CharField(max_length=20, blank=True)
    owner_nin_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 of NIN; NIN never stored cleartext")
    pct = models.DecimalField(max_digits=5, decimal_places=2)
    is_pep = models.BooleanField(default=False)

    class Meta:
        db_table = "vendor_draft_owner"
        constraints = [
            models.CheckConstraint(condition=models.Q(pct__gt=Decimal("0")) & models.Q(pct__lte=Decimal("100")), name="owner_pct_range_draft"),
        ]


# ------------------------------------------------------------- sealed bids
class TenderKey(models.Model):
    """Per-tender symmetric Fernet key, wrapped to custodian public keys. The
    public_keys map is published at tender publish so client-side encryptors
    never need to trust the server with plaintext."""

    tender = models.OneToOneField("procurement.Tender", on_delete=models.CASCADE, related_name="sealing")
    tender_key_b64 = models.BinaryField(editable=False)
    wrapped_shares = models.JSONField(default=dict)
    public_keys = models.JSONField(default=dict)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.JSONField(default=list)

    def decrypted_key(self) -> bytes:
        """In production this comes from reconstructing the Fernet key from the
        custodians' released shares; in demo the key is held verbatim and this
        returns it."""
        return bytes(self.tender_key_b64)

    class Meta:
        db_table = "proc_tender_key"


class Submission(models.Model):
    """Ciphertext envelope for a sealed bid, created the moment upload finishes."""

    bid = models.OneToOneField("procurement.Bid", on_delete=models.CASCADE, related_name="envelope")
    ciphertext = models.TextField(help_text="Fernet-encrypted JSON payload, base64")
    ciphertext_sha256 = models.CharField(max_length=64)
    ciphertext_size = models.PositiveBigIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    resume_offsets = models.JSONField(default=list, help_text="resumable upload chunk offsets")
    received_at = models.DateTimeField(auto_now_add=True)
    unsealed_at = models.DateTimeField(null=True, blank=True)
    unsealed_plaintext_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "proc_submission"


# ------------------------------------------------------------- evaluation record
class DissentNote(models.Model):
    """Dissent on an evaluation score. Mandatory to record, not just for the
    record — it is part of the published evaluation report."""

    score = models.ForeignKey("procurement.Score", on_delete=models.CASCADE, related_name="dissents")
    evaluator = models.ForeignKey("procurement.User", on_delete=models.CASCADE, related_name="dissents")
    note = models.TextField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "proc_dissent"


class AwardRecommendation(models.Model):
    """Signed committee recommendation, attached to the tender before approval."""

    tender = models.ForeignKey("procurement.Tender", on_delete=models.CASCADE, related_name="recommendations")
    recommended_bid = models.ForeignKey("procurement.Bid", on_delete=models.CASCADE)
    report_sha256 = models.CharField(max_length=64)
    report_obj_key = models.CharField(max_length=300)
    signed_by = models.ManyToManyField("procurement.EvaluationCommittee")
    created_at = models.DateTimeField(auto_now_add=True)
    final = models.BooleanField(default=False)

    class Meta:
        db_table = "proc_recommendation"


class ClarificationPost(models.Model):
    """Threaded Q&A — an official answer creates a public post."""

    question = models.ForeignKey("procurement.TenderQuestion", on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey("procurement.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="clar_posts")
    is_official = models.BooleanField(default=False)
    body = models.TextField()
    posted_at = models.DateTimeField(auto_now_add=True)
    visible_to_bidders = models.BooleanField(default=False)

    class Meta:
        db_table = "proc_clar_post"
        ordering = ["posted_at"]


# ------------------------------------------------------------- objections/appeals
class ObjectionPanelist(models.Model):
    objection = models.ForeignKey("procurement.Objection", on_delete=models.CASCADE, related_name="panelists")
    name = models.CharField(max_length=200)
    nominator = models.CharField(max_length=8, choices=[("GOV", "Government"), ("CSO", "Civil society")])
    role = models.CharField(max_length=120, blank=True)
    appointed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "proc_panelist"


class ObjectionEvidence(models.Model):
    objection = models.ForeignKey("procurement.Objection", on_delete=models.CASCADE, related_name="evidence")
    uploader = models.ForeignKey("procurement.User", null=True, on_delete=models.SET_NULL)
    description = models.CharField(max_length=250)
    sha256 = models.CharField(max_length=64)
    obj_key = models.CharField(max_length=300)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    confidential = models.BooleanField(default=False, help_text="withheld from public publication per whistleblower protection")

    class Meta:
        db_table = "proc_objection_evidence"


# ------------------------------------------------------------- whistleblower
class WhistleblowerReport(models.Model):
    ref = models.CharField(max_length=16, unique=True, editable=False)
    tender_ocid = models.CharField(max_length=60, blank=True)
    body_ciphertext = models.TextField(help_text="encrypted with the reporter's one-time key")
    contact_hash = models.CharField(max_length=64, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, default="RECEIVED", choices=[
        ("RECEIVED","Received"),("TRIAGED","Triaged"),("INVESTIGATING","Investigating"),
        ("RESOLVED","Resolved"),("DISMISSED","Dismissed"),
    ])
    assigned_to = models.ForeignKey("procurement.User", null=True, blank=True, on_delete=models.SET_NULL)
    triage_notes = models.TextField(blank=True, help_text="internal, not published")
    outcome_published = models.TextField(blank=True, help_text="published outcome summary")
    outcome_published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "wb_report"
        ordering = ["-submitted_at"]

    def save(self, *a, **kw):
        if not self.ref:
            alphabet = string.ascii_uppercase + string.digits
            self.ref = "WB-" + "".join(secrets.choice(alphabet) for _ in range(10))
        super().save(*a, **kw)


# ------------------------------------------------------------- catalogue / fast-lane
class CatalogueItem(models.Model):
    supplier = models.ForeignKey("procurement.Party", on_delete=models.CASCADE, related_name="catalogue_items")
    category = models.CharField(max_length=40, db_index=True)
    sku = models.CharField(max_length=60)
    name = models.CharField(max_length=250)
    unit = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    tax_inclusive = models.BooleanField(default=True)
    lead_time_days = models.PositiveSmallIntegerField(default=14)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    product_image_sha256 = models.CharField(max_length=64, blank=True)
    spec_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "cat_item"
        constraints = [models.UniqueConstraint(fields=["supplier", "sku"], name="uniq_sku_per_supplier")]
        indexes = [models.Index(fields=["category", "is_active", "unit_price"], name="cat_price_idx")]


class PurchaseOrder(models.Model):
    reference = models.CharField(max_length=40, unique=True)
    agency = models.ForeignKey("procurement.Agency", on_delete=models.PROTECT, related_name="purchase_orders")
    budget_line = models.ForeignKey("procurement.BudgetLine", on_delete=models.PROTECT)
    category = models.CharField(max_length=40)
    title = models.CharField(max_length=250)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=20)
    specification = models.TextField()
    created_by = models.ForeignKey("procurement.User", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField()
    awarded_supplier = models.ForeignKey("procurement.Party", null=True, blank=True, on_delete=models.SET_NULL, related_name="po_awards")
    awarded_unit_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=16, default="QUOTING", choices=[
        ("QUOTING","Quoting"),("AWARDED","Awarded"),("ACCEPTED","Accepted"),
        ("PAID","Paid"),("CANCELLED","Cancelled"),
    ])

    class Meta:
        db_table = "cat_po"
        ordering = ["-created_at"]

    def save(self, *a, **kw):
        if not self.reference:
            from datetime import datetime, timezone as dt_tz
            self.reference = f"PO-{datetime.now(dt_tz.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
        super().save(*a, **kw)


class CatalogueQuote(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="quotes")
    supplier = models.ForeignKey("procurement.Party", on_delete=models.CASCADE)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    lead_time_days = models.PositiveSmallIntegerField()
    submitted_at = models.DateTimeField(auto_now_add=True)
    is_l1 = models.BooleanField(default=False)

    class Meta:
        db_table = "cat_quote"
        constraints = [models.UniqueConstraint(fields=["po", "supplier"], name="uniq_quote_per_supplier")]


# ------------------------------------------------------------- notifications
class Notification(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        SMS = "SMS", "SMS"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        USSD = "USSD", "USSD push"
        IN_APP = "IN_APP", "In-app"

    class Kind(models.TextChoices):
        BID_RECEIVED = "BID_RECEIVED", "Bid submission receipt"
        TENDER_PUBLISHED = "TENDER_PUBLISHED", "New tender matching alert"
        ADDENDUM = "ADDENDUM", "Addendum published"
        DEADLINE_REMINDER = "DEADLINE", "Deadline reminder"
        AWARD_NOTICE = "AWARD", "Award notice"
        OBJECTION_UPDATE = "OBJECTION", "Objection status"
        REG_APPROVED = "REG_OK", "Supplier registration approved"
        REG_REJECTED = "REG_NO", "Supplier registration rejected"
        MFA_CODE = "MFA", "MFA code"
        WB_CONFIRM = "WB", "Whistleblower reference"
        IN_APP = "IN_APP", "In-app notification"

    recipient = models.ForeignKey("procurement.User", null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    recipient_email = models.EmailField(blank=True)
    recipient_phone = models.CharField(max_length=24, blank=True)
    channel = models.CharField(max_length=8, choices=Channel.choices)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    body = models.TextField()
    reference_key = models.CharField(max_length=64, blank=True)
    scheduled_for = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        db_table = "notif_outbox"
        indexes = [models.Index(fields=["channel", "scheduled_for", "sent_at"], name="notif_pending_idx")]
        ordering = ["-scheduled_for"]


# ------------------------------------------------------------- i18n
class TranslationString(models.Model):
    key = models.CharField(max_length=120, db_index=True)
    language = models.CharField(max_length=6, db_index=True, choices=[("en","English"),("ha","Hausa"),("tiv","Tiv"),("wuk","Wukari")])
    value = models.TextField()

    class Meta:
        db_table = "i18n_string"
        constraints = [models.UniqueConstraint(fields=["key", "language"], name="uniq_i18n_key")]


class UserLanguagePreference(models.Model):
    user = models.OneToOneField("procurement.User", on_delete=models.CASCADE, related_name="lang_pref")
    language = models.CharField(max_length=6, default="en")

    class Meta:
        db_table = "i18n_user_pref"


# ------------------------------------------------------------- alerts/watchlist
class TenderWatch(models.Model):
    party = models.ForeignKey("procurement.Party", on_delete=models.CASCADE, related_name="tender_watches")
    tender = models.ForeignKey("procurement.Tender", on_delete=models.CASCADE, related_name="watchers")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "proc_watch"
        constraints = [models.UniqueConstraint(fields=["party", "tender"], name="uniq_watch")]


class CategoryWatch(models.Model):
    party = models.ForeignKey("procurement.Party", on_delete=models.CASCADE, related_name="category_watches")
    category = models.CharField(max_length=1, choices=__import__("procurement.models_party", fromlist=["Party"]).Party.Category.choices)
    mda_code = models.CharField(max_length=12, blank=True)
    method = models.CharField(max_length=12, blank=True)
    min_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    max_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "proc_category_watch"

# Phase 2-4 additional models.
#
# Re-exported deliberately: Django only imports an app's `models` module when it
# builds the registry, so importing them here is what registers the tables, and
# callers get one obvious import path (`from workflow.models import
# ContractMilestone`). The noqa is the record of that decision, not a mistake.
from workflow.models_phases import (  # noqa: E402,F401  (re-export: registers the models)
    ApprovalRouting,
    BidReceipt,
    CommitteeDissent,
    ContractGuarantee,
    ContractMilestone,
    ContractVariation,
    DebriefRequest,
    EvaluationReport,
    PaymentSchedule,
    SupplierPerformanceRating,
)
