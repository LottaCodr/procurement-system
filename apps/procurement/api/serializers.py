from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from procurement.models import Award, Bid, Contract, Tender
from procurement.models_party import Agency, Party


class AgencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Agency
        fields = ["code", "name", "kind", "lga", "contact_email"]


class MoneySerializer(serializers.Serializer):
    currency = serializers.CharField(default="NGN")
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)


class PartyBriefSerializer(serializers.ModelSerializer):
    verified = serializers.SerializerMethodField()

    class Meta:
        model = Party
        fields = ["id", "legal_name", "rc_number", "tin", "state", "lga", "scope", "category", "verified"]

    def get_verified(self, obj) -> dict:
        # Honest transparency: which assertions exist, dated, per credential kind.
        return {
            v.kind: {"status": v.status, "at": v.verified_at, "expires": v.expires_at}
            for v in obj.verifications.all()
        }


class BidSerializer(serializers.ModelSerializer):
    supplier = PartyBriefSerializer(read_only=True)
    reveal = serializers.SerializerMethodField()

    class Meta:
        model = Bid
        fields = ["receipt_ref", "commitment_hash", "submitted_at", "late", "status", "amount", "duration_days", "supplier", "reveal"]

    def get_reveal(self, obj) -> bool:
        return obj.tender.status in (
            Tender.Status.OPENED, Tender.Status.EVALUATING, Tender.Status.AWARDED,
            Tender.Status.CONTRACTED, Tender.Status.FROZEN,
        )


class AwardSerializer(serializers.ModelSerializer):
    supplier = serializers.CharField(source="bid.supplier.legal_name")
    supplier_rc = serializers.CharField(source="bid.supplier.rc_number")
    tender_ocid = serializers.CharField(source="tender.ocid")
    agency = serializers.CharField(source="tender.agency.code")
    method = serializers.CharField(source="tender.method")

    class Meta:
        model = Award
        fields = [
            "id", "tender_ocid", "agency", "method", "supplier", "supplier_rc",
            "amount", "status", "reason", "published_at", "objection_until",
            "notice_ref", "non_lowest_reason",
        ]


class TenderListSerializer(serializers.ModelSerializer):
    agency_code = serializers.CharField(source="agency.code")
    agency_name = serializers.CharField(source="agency.name")
    is_open = serializers.BooleanField(read_only=True)
    red_flags = serializers.ListField(source="red_flag_codes", child=serializers.CharField(), read_only=True)
    bid_count = serializers.IntegerField(read_only=True)
    days_to_close = serializers.IntegerField(read_only=True)
    documents_url = serializers.SerializerMethodField()

    class Meta:
        model = Tender
        fields = [
            "ocid", "reference", "title", "status", "method", "agency_code", "agency_name",
            "est_value", "total_value", "published_at", "submission_close_at", "qa_close_at",
            "opening_at", "is_open", "days_to_close", "bid_count", "red_flags",
            "reserved_for_local_pct", "sme_set_aside_pct", "documents_url",
        ]

    def get_documents_url(self, obj) -> str:
        return f"/tenders/{obj.ocid}"


class TenderDetailSerializer(TenderListSerializer):
    bids = BidSerializer(many=True, read_only=True)
    awards = AwardSerializer(many=True, read_only=True)
    questions = serializers.SerializerMethodField()
    committee = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    lots = serializers.SerializerMethodField()
    criteria = serializers.SerializerMethodField()
    objections = serializers.SerializerMethodField()
    ocds = serializers.SerializerMethodField()

    class Meta(TenderListSerializer.Meta):
        fields = TenderListSerializer.Meta.fields + [
            "description", "budget_line", "approval_body", "no_objection_ref", "bid_security_amount",
            "immutable_from", "commitment_root", "bids", "awards", "questions", "committee",
            "documents", "lots", "criteria", "objections", "ocds",
        ]

    def get_questions(self, obj) -> list[dict]:
        return [
            {
                "asked_at": q.asked_at,
                "question": q.question,
                "answer": q.answer or None,
                "answered_at": q.answered_at,
                "extended_deadline": q.extends_deadline,
            }
            for q in obj.questions.all()
        ]

    def get_committee(self, obj) -> list[dict]:
        return [
            {"name": c.user.get_full_name() or c.user.username, "role": c.role, "formed_at": c.formed_at}
            for c in obj.committee.select_related("user")
        ]

    def get_documents(self, obj) -> list[dict]:
        return [
            {"version": d.version, "kind": d.kind, "title": d.title, "sha256": d.sha256, "size_bytes": d.size_bytes, "published_at": d.published_at, "url": d.download_path, "note": d.note}
            for d in obj.public_documents
        ]

    def get_lots(self, obj) -> list[dict]:
        return [
            {"seq": l.seq, "title": l.title, "quantity": l.quantity, "unit": l.unit, "est_value": l.est_value, "max_awards": l.max_awards}
            for l in obj.lots.all()
        ]

    def get_criteria(self, obj) -> list[dict]:
        return [
            {"code": c.code, "name": c.name, "weight": c.weight, "kind": c.kind, "min_score": c.min_score, "locked": c.locked}
            for c in obj.criteria.all()
        ]

    def get_objections(self, obj) -> list[dict]:
        return [
            {"filed_at": o.filed_at, "by": o.filed_by_label or "anonymous", "ground": o.ground,
             "outcome": o.outcome, "decided_at": o.decided_at, "decision": o.decision,
             "panel": o.panel_members}
            for o in obj.objections.all()
        ]

    def get_ocds(self, obj) -> dict:
        return obj.ocds_release


class ContractSerializer(serializers.ModelSerializer):
    contract_ref = serializers.CharField(source="reference")
    supplier = serializers.CharField(source="award.bid.supplier.legal_name")
    variation_pct = serializers.DecimalField(max_digits=7, decimal_places=2, read_only=True)

    class Meta:
        model = Contract
        fields = [
            "reference", "contract_ref", "supplier", "status", "value", "signed_at",
            "duration_days", "advance_pct", "perf_guarantee_pct", "location",
            "deliverables", "variation_pct",
        ]


class StatsSerializer(serializers.Serializer):
    open_tenders = serializers.IntegerField()
    published_this_year = serializers.IntegerField()
    awards_published = serializers.IntegerField()
    total_award_value = serializers.DecimalField(max_digits=18, decimal_places=2)
    suppliers_verified = serializers.IntegerField()
    mdas_onboarding = serializers.IntegerField()
    ledger_events = serializers.IntegerField()
    ledger_head_hash = serializers.CharField()
    generated_at = serializers.DateTimeField()

    # Rule 1: no metric is displayed that a query did not produce.
    def validate_total_award_value(self, v) -> Decimal:
        return v
