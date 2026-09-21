from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from procurement.models import (
    AcceptanceCertificate,
    Award,
    Bid,
    Contract,
    ContractEvent,
    Criterion,
    EvaluationCommittee,
    Lot,
    Objection,
    PaymentCertification,
    Score,
    Tender,
    TenderDocument,
    TenderQuestion,
)
from procurement.models_party import (
    Agency,
    BudgetLine,
    Party,
    PartyOwnership,
    PartyVerification,
    RelatedPartyDeclaration,
    ThresholdRule,
    User,
)


@admin.register(User)
class PlatformUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Platform role", {"fields": ("role", "agency", "mfa_secret", "mfa_enrolled_at")}),)
    list_display = ("username", "role", "agency", "mfa_enrolled_at", "is_active")
    list_filter = ("role", "agency")


@admin.register(ThresholdRule)
class ThresholdRuleAdmin(admin.ModelAdmin):
    """Versioned data: a new row set per fiscal year, never an edit in place, so a
    tender can always be re-checked against the rules that applied on its date."""

    list_display = ("fy", "method", "min_amount", "max_amount", "approval_body", "min_advert_days", "bid_security_pct", "is_active")
    list_filter = ("fy", "method", "approval_body")
    ordering = ("-fy", "method", "min_amount")


@admin.register(Agency)
class AgencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "kind", "lga", "is_active")
    search_fields = ("code", "name")


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "rc_number", "state", "lga", "scope", "category", "bo_declared", "debarred_from")
    search_fields = ("legal_name", "rc_number", "tin")
    list_filter = ("scope", "category", "is_active")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PartyVerification)
class VerificationAdmin(admin.ModelAdmin):
    list_display = ("party", "kind", "status", "verified_at", "expires_at", "verified_by")
    list_filter = ("kind", "status")
    autocomplete_fields = ("party",)


class VerificationInline(admin.TabularInline):
    model = PartyVerification
    extra = 0


class OwnershipInline(admin.TabularInline):
    model = PartyOwnership
    extra = 0
    fields = ("owner_name", "owner_rc_number", "pct", "is_pep")


# PartyAdmin is defined above without inlines; attach them here so beneficial
# ownership is edited alongside the party it belongs to.
PartyAdmin.inlines = [VerificationInline, OwnershipInline]


@admin.register(PartyOwnership)
class OwnershipAdmin(admin.ModelAdmin):
    list_display = ("party", "owner_name", "pct", "is_pep")
    search_fields = ("owner_name", "owner_rc_number")


@admin.register(RelatedPartyDeclaration)
class DeclarationAdmin(admin.ModelAdmin):
    list_display = ("party", "relationship", "named_official", "declared_at")


@admin.register(BudgetLine)
class BudgetLineAdmin(admin.ModelAdmin):
    list_display = ("fy", "agency", "project_code", "amount", "released", "committed")
    list_filter = ("fy", "agency")


class DocumentInline(admin.TabularInline):
    model = TenderDocument
    extra = 0
    readonly_fields = ("sha256", "published_at")


class LotInline(admin.TabularInline):
    model = Lot
    extra = 0


class BidInline(admin.TabularInline):
    model = Bid
    extra = 0
    readonly_fields = ("commitment_hash", "receipt_ref", "submitted_at", "late")


@admin.register(Tender)
class TenderAdmin(admin.ModelAdmin):
    """Read-mostly. Business state changes go through the service layer so they are
    ledgered; the admin panel is for lookup and correction of typos pre-publication
    only, and `save_model` blocks status edits outright."""

    list_display = ("ocid", "title", "agency", "method", "status", "est_value", "published_at", "submission_close_at")
    list_filter = ("status", "method", "agency")
    search_fields = ("ocid", "title", "reference")
    readonly_fields = ("ocid", "immutable_from", "commitment_root", "published_at")
    inlines = [DocumentInline, LotInline, BidInline]
    actions = ["verify_chain"]

    @admin.action(description="Check these processes against the append-only ledger")
    def verify_chain(self, request, queryset):
        from ledger.models import Event
        from ledger.services import verify_chain as global_verify

        checked, broken_at = global_verify()
        missing = [t.ocid for t in queryset if not Event.objects.filter(aggregate=f"procurement.Tender.{t.pk}").exists()]
        msg = f"Ledger: {checked} events, chain {'VALID' if broken_at is None else f'BROKEN at seq {broken_at}'}. "
        msg += f"{len(missing)} of the selected {queryset.count()} processes have no ledger events at all"
        msg += f" ({', '.join(missing[:5])})" if missing else "."
        self.message_user(request, msg, level="WARNING" if (broken_at or missing) else "INFO")

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            raise PermissionError(
                "Tender status changes are business events: use the workspace action (or "
                "`manage.py` service call) so the transition is written to the ledger."
            )
        super().save_model(request, obj, form, change)


@admin.register(Award)
class AwardAdmin(admin.ModelAdmin):
    list_display = ("tender", "bid", "amount", "status", "published_at", "objection_until")
    list_filter = ("status",)
    search_fields = ("tender__ocid", "bid__supplier__legal_name")
    readonly_fields = ("published_at", "objection_until")


@admin.register(Objection)
class ObjectionAdmin(admin.ModelAdmin):
    list_display = ("tender", "award", "outcome", "filed_at", "decided_at", "frozen_until")
    list_filter = ("outcome",)


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ("reference", "award", "value", "status", "signed_at", "duration_days")
    list_filter = ("status",)


@admin.register(ContractEvent)
class ContractEventAdmin(admin.ModelAdmin):
    list_display = ("contract", "kind", "amount", "occurred_at", "published")
    list_filter = ("kind", "published")


@admin.register(AcceptanceCertificate)
class AcceptanceAdmin(admin.ModelAdmin):
    list_display = ("ref", "contract", "value", "inspected_at", "certified_by")


@admin.register(PaymentCertification)
class PaymentCertAdmin(admin.ModelAdmin):
    list_display = ("reference", "contract", "amount", "certified_at", "treasury_ref")
    readonly_fields = ("certified_at",)


@admin.register(Criterion)
class CriterionAdmin(admin.ModelAdmin):
    list_display = ("tender", "code", "name", "kind", "weight", "min_score", "locked")


@admin.register(Score)
class ScoreAdmin(admin.ModelAdmin):
    list_display = ("bid", "criterion", "raw", "evaluator", "scored_at")
    readonly_fields = ("scored_at",)


@admin.register(EvaluationCommittee)
class CommitteeAdmin(admin.ModelAdmin):
    list_display = ("tender", "user", "role", "formed_at")


@admin.register(TenderQuestion)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("tender", "asked_at", "answered_at", "extends_deadline")
