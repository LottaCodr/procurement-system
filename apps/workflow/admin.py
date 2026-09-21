from django.contrib import admin

from workflow.models import (
    CatalogueItem,
    CatalogueQuote,
    CategoryWatch,
    ClarificationPost,
    DissentNote,
    DraftDocument,
    DraftOwner,
    MFASession,
    Notification,
    ObjectionEvidence,
    ObjectionPanelist,
    PurchaseOrder,
    AwardRecommendation,
    Submission,
    SupplierRegistrationDraft,
    TenderKey,
    TenderWatch,
    TranslationString,
    UserLanguagePreference,
    WhistleblowerReport,
)
from workflow.models_phases import (
    ApprovalRouting,
    ContractGuarantee,
    ContractMilestone,
    ContractVariation,
    DebriefRequest,
    EvaluationReport,
    PaymentSchedule,
    SupplierPerformanceRating,
)


@admin.register(SupplierRegistrationDraft)
class DraftAdmin(admin.ModelAdmin):
    list_display = ("draft_token", "company_name", "rc_number", "current_step", "status", "updated_at")
    list_filter = ("status", "current_step", "state")
    search_fields = ("company_name", "rc_number", "tin", "email", "draft_token")
    readonly_fields = ("draft_token", "created_at", "updated_at", "submitted_at")


@admin.register(DraftDocument, DraftOwner)
class InlineAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "verification_status", "uploaded_at") if False else ("__str__",)


@admin.register(TenderKey)
class TenderKeyAdmin(admin.ModelAdmin):
    list_display = ("tender", "released_at", "released_by")
    readonly_fields = ("tender", "tender_key_b64", "wrapped_shares", "public_keys", "released_at")

    def has_change_permission(self, request, obj=None):
        return False  # the sealing key is operational; admin must not mutate it


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("bid", "ciphertext_sha256", "received_at", "unsealed_at")
    readonly_fields = [f.name for f in Submission._meta.fields]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("channel", "kind", "recipient_phone", "recipient_email", "scheduled_for", "sent_at", "error")
    list_filter = ("channel", "kind", "sent_at")


@admin.register(WhistleblowerReport)
class WhistleblowerAdmin(admin.ModelAdmin):
    list_display = ("ref", "tender_ocid", "status", "submitted_at")
    readonly_fields = ("ref", "submitted_at", "body_ciphertext", "contact_hash")
    list_filter = ("status",)


@admin.register(CatalogueItem)
class CatalogueAdmin(admin.ModelAdmin):
    list_display = ("supplier", "category", "sku", "name", "unit_price", "is_active", "updated_at")
    list_filter = ("category", "is_active")


@admin.register(SupplierPerformanceRating)
class PerformanceRatingAdmin(admin.ModelAdmin):
    list_display = ("supplier", "contract", "grade", "composite", "rated_at", "published")
    list_filter = ("grade", "published")
    search_fields = ("supplier__legal_name", "contract__reference")


@admin.register(DebriefRequest)
class DebriefAdmin(admin.ModelAdmin):
    list_display = ("award", "supplier", "status", "requested_at", "due_by", "responded_at")
    list_filter = ("status",)


@admin.register(ApprovalRouting)
class ApprovalRoutingAdmin(admin.ModelAdmin):
    list_display = ("tender", "required_body", "approved_by_body", "is_correct", "approved_at")
    list_filter = ("required_body", "is_correct")


@admin.register(EvaluationReport)
class EvalReportAdmin(admin.ModelAdmin):
    list_display = ("tender", "published_at", "signed_by_count", "dissents_count")


@admin.register(ContractMilestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ("contract", "seq", "title", "planned_date", "actual_date", "status")
    list_filter = ("status",)


@admin.register(ContractVariation)
class VariationAdmin(admin.ModelAdmin):
    list_display = ("contract", "title", "amount_change", "status", "alarm_triggered")
    list_filter = ("status", "alarm_triggered")


@admin.register(ContractGuarantee)
class GuaranteeAdmin(admin.ModelAdmin):
    list_display = ("contract", "kind", "issuer", "amount", "expires_at", "is_valid")
    list_filter = ("kind", "is_valid")


@admin.register(PaymentSchedule)
class PaymentScheduleAdmin(admin.ModelAdmin):
    list_display = ("contract", "seq", "description", "amount", "paid")
    list_filter = ("paid",)


@admin.register(PurchaseOrder, CatalogueQuote, AwardRecommendation, DissentNote,
                ClarificationPost, ObjectionPanelist, ObjectionEvidence,
                CategoryWatch, TenderWatch, TranslationString, UserLanguagePreference, MFASession)
class SimpleAdmin(admin.ModelAdmin):
    pass
