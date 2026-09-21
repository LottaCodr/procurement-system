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


@admin.register(PurchaseOrder, CatalogueQuote, AwardRecommendation, DissentNote,
                ClarificationPost, ObjectionPanelist, ObjectionEvidence,
                CategoryWatch, TenderWatch, TranslationString, UserLanguagePreference, MFASession)
class SimpleAdmin(admin.ModelAdmin):
    pass
