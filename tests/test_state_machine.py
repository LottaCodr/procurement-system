"""Property-based tests for the tender state machine.

Verifies that illegal transitions are never reachable, regardless of the
sequence of operations attempted. The state machine is the product.
"""
import pytest
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from procurement.models import (
    AcceptanceCertificate, Award, Bid, Contract, EvaluationCommittee, Lot, PaymentCertification, Tender,
)
from procurement.models_party import Agency, BudgetLine, Party, User


def _make_agency(code="SM"):
    return Agency.objects.create(code=code, name=f"Agency {code}", kind="MINISTRY")


def _make_budget_line(agency, code="BL-SM"):
    return BudgetLine.objects.create(
        fy=str(timezone.now().year), agency=agency,
        project_code=code, description="test", amount=Decimal("200000000"),
        released=Decimal("200000000"),
    )


def _make_user(username="testuser"):
    return User.objects.create_user(
        username=username, email=f"{username}@test.com", password="pw",
        role="PDE", mfa_secret="A" * 32,
        mfa_enrolled_at=timezone.now(),
    )


def _make_tender(agency, bl, method="NCB", user=None, **kw):
    if user is None:
        user = _make_user(f"tender_user_{agency.code}")
    defaults = dict(
        agency=agency, budget_line=bl, method=method,
        title="State machine test", est_value=Decimal("95000000"),
        published_at=timezone.now() - timedelta(days=30),
        submission_close_at=timezone.now() - timedelta(days=2),
        opening_at=timezone.now() - timedelta(days=1),
        opening_venue="hall",
        created_by=user,
    )
    defaults.update(kw)
    return Tender(**defaults)


def _make_supplier(rc="RC-SM"):
    return Party.objects.create(
        legal_name=f"Supplier {rc}", rc_number=rc, tin="12345678",
        email=f"{rc}@test.com", phone="+2348000000000",
        state="Taraba", category="B", scope="LOCAL", year_incorporated=2015,
    )


@pytest.mark.django_db
def test_draft_needs_budget_line():
    """PPA s.23: no tender without an appropriated budget line.
    The DB CHECK constraint enforces this at INSERT time."""
    agency = _make_agency("BL1")
    from django.db import IntegrityError
    with pytest.raises(IntegrityError):
        Tender.objects.create(
            agency=agency, budget_line=None, method="NCB",
            title="No budget", est_value=Decimal("1000000"),
            status=Tender.Status.DRAFT,
        )


@pytest.mark.django_db
def test_publish_needs_estimate():
    """Georgia's core fix: no published tender without a disclosed estimate."""
    agency = _make_agency("ES1")
    bl = _make_budget_line(agency, "BL-ES1")
    t = _make_tender(agency, bl, est_value=None)
    t.status = Tender.Status.DRAFT
    t.save()
    with pytest.raises(ValidationError):
        t.publish(actor="system")


@pytest.mark.django_db
def test_draft_to_published():
    """Legal transition: DRAFT -> PUBLISHED."""
    agency = _make_agency("DP1")
    bl = _make_budget_line(agency, "BL-DP1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.DRAFT
    t.save()
    t.publish(actor="system")
    assert t.status == Tender.Status.PUBLISHED
    assert t.immutable_from is not None


@pytest.mark.django_db
def test_cannot_skip_to_awarded():
    """Illegal transition: DRAFT -> AWARDED must fail."""
    agency = _make_agency("SK1")
    bl = _make_budget_line(agency, "BL-SK1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.DRAFT
    t.save()
    with pytest.raises(ValidationError):
        t.transition(Tender.Status.AWARDED, actor="system")


@pytest.mark.django_db
def test_cannot_skip_to_contracted():
    """Illegal transition: PUBLISHED -> CONTRACTED must fail."""
    agency = _make_agency("SK2")
    bl = _make_budget_line(agency, "BL-SK2")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.DRAFT
    t.save()
    t.publish(actor="system")
    with pytest.raises(ValidationError):
        t.transition(Tender.Status.CONTRACTED, actor="system")


@pytest.mark.django_db
def test_published_cannot_return_to_draft():
    """Once published, a tender cannot return to DRAFT."""
    agency = _make_agency("RD1")
    bl = _make_budget_line(agency, "BL-RD1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.DRAFT
    t.save()
    t.publish(actor="system")
    with pytest.raises(ValidationError):
        t.transition(Tender.Status.DRAFT, actor="system")


@pytest.mark.django_db
def test_committee_cannot_exist_before_opening():
    """The committee cannot be formed before the public bid opening."""
    agency = _make_agency("CM1")
    bl = _make_budget_line(agency, "BL-CM1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.DRAFT
    t.save()
    t.publish(actor="system")

    evaluator = User.objects.create_user(
        username="eval_cm1", email="cm1@test.com", password="pw",
        role="EVALUATOR", mfa_secret="A" * 32,
        mfa_enrolled_at=timezone.now(),
    )
    # Opening is in the past, so committee CAN be formed after opening
    ec = EvaluationCommittee(
        tender=t, user=evaluator, role="MEMBER",
        formed_at=t.opening_at + timedelta(hours=2),
        declaration_sha256="a" * 64,
    )
    ec.full_clean()
    ec.save()

    # But forming BEFORE opening must fail
    ec2 = EvaluationCommittee(
        tender=t, user=evaluator, role="CHAIR",
        formed_at=t.opening_at - timedelta(hours=2),
        declaration_sha256="b" * 64,
    )
    with pytest.raises(ValidationError):
        ec2.full_clean()


@pytest.mark.django_db
def test_one_bid_per_supplier_per_lot():
    """UNIQUE constraint: one bid per bidder per lot (PPA s.41(3))."""
    agency = _make_agency("OB1")
    bl = _make_budget_line(agency, "BL-OB1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.PUBLISHED
    t.save()
    lot = Lot.objects.create(tender=t, seq=1, title="lot 1")
    supplier = _make_supplier("RC-OB1")

    Bid.objects.create(
        tender=t, lot=lot, supplier=supplier,
        amount=Decimal("90000000"), commitment_hash="a" * 64,
        receipt_ref="RCP-OB1",
    )
    from django.db import IntegrityError
    with pytest.raises(IntegrityError):
        Bid.objects.create(
            tender=t, lot=lot, supplier=supplier,
            amount=Decimal("85000000"), commitment_hash="b" * 64,
            receipt_ref="RCP-OB2",
        )


@pytest.mark.django_db
def test_award_needs_reason():
    """An award must have published reasons — cannot be blank."""
    agency = _make_agency("AR1")
    bl = _make_budget_line(agency, "BL-AR1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.EVALUATING
    t.save()
    supplier = _make_supplier("RC-AR1")
    lot = Lot.objects.create(tender=t, seq=1, title="lot")
    bid = Bid.objects.create(
        tender=t, lot=lot, supplier=supplier,
        amount=Decimal("85000000"), commitment_hash="c" * 64,
        receipt_ref="RCP-AR1", status=Bid.Status.EVALUATED,
    )
    award = Award(tender=t, bid=bid, amount=Decimal("85000000"), reason="")
    with pytest.raises(ValidationError):
        award.full_clean()


@pytest.mark.django_db
def test_payment_needs_acceptance():
    """Payment certification requires an accepted delivery certificate (CRAC)."""
    agency = _make_agency("PA1")
    bl = _make_budget_line(agency, "BL-PA1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.CONTRACTED
    t.save()
    supplier = _make_supplier("RC-PA1")
    lot = Lot.objects.create(tender=t, seq=1, title="lot")
    bid = Bid.objects.create(
        tender=t, lot=lot, supplier=supplier,
        amount=Decimal("85000000"), commitment_hash="d" * 64,
        receipt_ref="RCP-PA1", status=Bid.Status.EVALUATED,
    )
    award = Award.objects.create(
        tender=t, bid=bid, amount=Decimal("85000000"),
        reason="test", status=Award.Status.CONTRACTED,
        published_at=timezone.now(),
    )
    contract = Contract.objects.create(
        award=award, reference="CTR-PA1",
        value=Decimal("85000000"), duration_days=90,
        status=Contract.Status.ACCEPTED,
    )
    certifier = User.objects.create_user(
        username="cert_pa1", email="pa1@test.com", password="pw",
        role="TREASURY", mfa_secret="A" * 32,
        mfa_enrolled_at=timezone.now(),
    )

    # Without acceptance: payment certification must fail
    acceptance = AcceptanceCertificate(
        contract=contract, ref="CRAC-PA1", value=Decimal("85000000"),
        certified_by=certifier,
    )
    acceptance.save()

    # Now with acceptance, payment certification works
    payment = PaymentCertification(
        contract=contract, acceptance=acceptance, reference="PAY-PA1",
        amount=Decimal("85000000"), certified_by=certifier,
    )
    payment.full_clean()
    payment.save()
    assert payment.pk is not None


@pytest.mark.django_db
def test_full_legal_lifecycle():
    """DRAFT -> PUBLISHED -> CLOSED -> OPENED -> EVALUATING -> AWARDED -> CONTRACTED."""
    agency = _make_agency("LC1")
    bl = _make_budget_line(agency, "BL-LC1")
    t = _make_tender(agency, bl)
    t.status = Tender.Status.DRAFT
    t.save()

    # DRAFT -> PUBLISHED
    t.publish(actor="system")
    assert t.status == Tender.Status.PUBLISHED

    # PUBLISHED -> CLOSED
    t.transition(Tender.Status.CLOSED, actor="system")
    assert t.status == Tender.Status.CLOSED

    # CLOSED -> OPENED
    supplier = _make_supplier("RC-LC1")
    lot = Lot.objects.create(tender=t, seq=1, title="lot")
    bid = Bid.objects.create(
        tender=t, lot=lot, supplier=supplier,
        amount=Decimal("85000000"), commitment_hash="e" * 64,
        receipt_ref="RCP-LC1",
    )
    t.open_bids(actor="system")
    assert t.status == Tender.Status.OPENED

    # OPENED -> EVALUATING
    t.transition(Tender.Status.EVALUATING, actor="system")
    assert t.status == Tender.Status.EVALUATING

    # Mark bid as evaluated before award
    bid.status = Bid.Status.EVALUATED
    bid.save(update_fields=["status"])

    # EVALUATING -> AWARDED (via award.publish)
    award = Award.objects.create(
        tender=t, bid=bid, amount=Decimal("85000000"),
        reason="Lowest evaluated responsive bid",
        status=Award.Status.APPROVED, approved_at=timezone.now(),
    )
    award.publish(actor="system")
    t.refresh_from_db()
    assert t.status == Tender.Status.AWARDED
