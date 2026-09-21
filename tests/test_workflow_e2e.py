"""End-to-end workflow test across Phases 2-5."""
import json
from datetime import datetime
from decimal import Decimal
from django.utils import timezone

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from procurement.crypto import encrypt_bid_payload, decrypt_bid_payload
from procurement.models import Award, Contract, Tender
from procurement.models_party import Agency, BudgetLine, Party
from workflow.models import (
    CatalogueItem,
    PurchaseOrder,
    SupplierRegistrationDraft,
    TenderKey,
    WhistleblowerReport,
)
from workflow.services import (
    add_draft_owner,
    approve_draft,
    award_contract,
    certify_acceptance,
    certify_payment,
    client_encrypt_helper,
    create_po,
    enrol_totp,
    file_objection,
    file_whistleblower,
    save_draft_step,
    score_bid,
    start_registration,
    submit_draft,
    unseal_bids,
    upload_draft_document,
)


User = get_user_model()


@pytest.mark.django_db
def test_supplier_registration_approval_flow():
    dg = User.objects.create_user("supervisor", "a@b.c", "pw", role="DG", mfa_secret="A"*32, mfa_enrolled_at=timezone.make_aware(datetime(2026,1,1)))
    d = start_registration()
    save_draft_step(d, 1, {"company_name":"Acme Ltd","rc_number":"RC9000001","tin":"99999999-0001","year_incorporated":2018,"address":"1 Bali road","category":"B","scope":"LOCAL"})
    save_draft_step(d, 2, {"full_name":"Ada Bello","email":"ada@acme.ng","phone":"+2348091112222"})
    upload_draft_document(d, "CAC", "a"*64, 10000, "obj:cac.pdf", "cac.pdf", "application/pdf", expiry="2027-12-31")
    upload_draft_document(d, "TIN", "b"*64, 10000, "obj:tin.pdf", "tin.pdf", "application/pdf", expiry="2027-06-30")
    upload_draft_document(d, "PENCOM", "c"*64, 10000, "obj:pen.pdf", "pen.pdf", "application/pdf")
    add_draft_owner(d, "Ada Bello", "RC9000001", Decimal("100.0"), is_pep=False)
    d.accept_terms = True; d.save()
    assert not d.ready_to_submit()  # should now be empty
    submit_draft(d)
    p = approve_draft(d, dg)
    assert p.is_active
    assert p.verifications.filter(kind="CAC", status="PASSED").exists()
    assert p.owners.filter(owner_name="Ada Bello", pct=100).exists()


@pytest.mark.django_db
def test_sealed_bid_submit_and_unseal():
    from procurement.models_party import ThresholdRule
    from datetime import timedelta
    from django.utils import timezone
    dg = User.objects.create_user("b_dg", "a@b.c", "pw", role="DG", mfa_secret="A"*32, mfa_enrolled_at=timezone.make_aware(datetime(2026,1,1)))
    moh = Agency.objects.create(code="TST", name="Test", kind="MINISTRY", lga="Jalingo")
    bl = BudgetLine.objects.create(fy="2026", agency=moh, project_code="CAP/01", description="x", amount=Decimal("200000000"), released=Decimal("200000000"))
    r = ThresholdRule.objects.create(fy="2026", method="NCB", min_amount=Decimal("50000000"), max_amount=Decimal("5000000000"),
                                    approval_body="MTB", min_advert_days=14, bid_security_pct=Decimal("2"), min_quotations=3, goods="GENERIC")
    sup = Party.objects.create(legal_name="Sup1", rc_number="RC1", tin="11111111-0001", email="x@y", phone="+234", state="Taraba", lga="Jalingo", category="B", scope="LOCAL", year_incorporated=2015)
    t = Tender(agency=moh, budget_line=bl, method="NCB", title="Test", est_value=Decimal("90000000"), rule=r, approval_body="MTB", created_by=dg,
               published_at=timezone.now()-timedelta(days=20), submission_close_at=timezone.now()+timedelta(days=1),
               opening_at=timezone.now()+timedelta(days=1,hours=4), qa_close_at=timezone.now()-timedelta(days=3), opening_venue="hall")
    t.publish(actor=dg.username)
    from procurement.models import Lot
    Lot.objects.create(tender=t, seq=1, title="lot1", quantity=1, unit="each", est_value=Decimal("90000000"))
    # signal should have created the key
    assert TenderKey.objects.filter(tender=t).exists()
    fkey = bytes(t.sealing.tender_key_b64)
    ct = client_encrypt_helper(fkey, {"amount":"80000000","duration_days":120,"items":[],"declaration":""})
    bid, env = __import__("workflow.services", fromlist=["submit_bid"]).submit_bid(t, sup, {"amount":"80000000","duration_days":120,"items":[]}, ct)
    assert bid.commitment_hash
    assert bid.status == "RECEIVED"
    assert env.ciphertext_sha256
    # fast-forward to opening
    t.submission_close_at = timezone.now()-timedelta(minutes=1); t.save()
    t.close(actor=dg.username); t.open_bids(actor=dg.username)
    failures = unseal_bids(t, dg.username)
    assert failures == 0
    bid.refresh_from_db()
    assert bid.amount == Decimal("80000000")
    assert bid.status == "UNSEALED"


@pytest.mark.django_db
def test_objection_panel_and_payment_lock():
    import os,json
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"]="1"
    """Build a fresh tender and run it through EVALUATING -> award -> objection -> accept -> pay."""
    from datetime import timedelta
    from django.utils import timezone
    dg = User.objects.create_user("o_dg", "o@b.c", "pw", role="DG", mfa_secret="A"*32, mfa_enrolled_at=timezone.make_aware(datetime(2026,1,1)))
    tre = User.objects.create_user("o_tre", "t@b.c", "pw", role="TREASURY", mfa_secret="A"*32, mfa_enrolled_at=timezone.make_aware(datetime(2026,1,1)))
    moh = Agency.objects.create(code="MOHX", name="MOH test", kind="MINISTRY", lga="Jalingo")
    bl = BudgetLine.objects.create(fy=str(timezone.now().year), agency=moh, project_code="CAP/OB", description="o", amount=Decimal("200000000"), released=Decimal("200000000"))
    from procurement.models_party import ThresholdRule
    rule, _ = ThresholdRule.objects.get_or_create(fy=str(timezone.now().year), method="NCB", goods="GENERIC", min_amount=Decimal("50000000"), defaults={"max_amount": Decimal("5000000000"),"approval_body":"MTB","min_advert_days":14,"bid_security_pct":Decimal("2"),"min_quotations":3})
    sup = Party.objects.create(legal_name="Bidder", rc_number="RC-OBJ", tin="1-0002", email="x@y", phone="+9", state="Taraba", lga="Jalingo", category="B", scope="LOCAL", year_incorporated=2015)
    t = Tender(agency=moh, budget_line=bl, method="NCB", title="Objection test",
               est_value=Decimal("95000000"), rule=rule, approval_body="MTB", created_by=dg,
               published_at=timezone.now()-timedelta(days=20),
               submission_close_at=timezone.now()-timedelta(days=2),
               opening_at=timezone.now()-timedelta(days=1),
               qa_close_at=timezone.now()-timedelta(days=4),
               opening_venue="hall", immutable_from=timezone.now()-timedelta(days=20))
    t.status = Tender.Status.PUBLISHED  # skip publish's state machine for this test (sealing already exists from signal in real runs)
    t.save()
    from procurement.models import Lot
    Lot.objects.create(tender=t, seq=1, title="lot", quantity=1, unit="each", est_value=Decimal("95000000"))
    from procurement.models import Bid
    bid = Bid.objects.create(tender=t, lot=t.lots.first(), supplier=sup,
                             amount=Decimal("85000000"), duration_days=90, submitted_at=t.submission_close_at-timedelta(days=1),
                             status=Bid.Status.EVALUATED, price_schedule_public=True, receipt_ref="RCP-RRR")
    bid.commitment_hash = "h"*64; bid.save(update_fields=["commitment_hash"])
    from procurement.models import EvaluationCommittee, Criterion, Score
    EvaluationCommittee.objects.create(tender=t, user=dg, role="CHAIR", formed_at=t.opening_at+timedelta(hours=2), declaration_sha256="f"*64)
    t.transition("CLOSED", actor=dg.username, reason="deadline")
    t.open_bids(actor=dg.username)
    t.transition("EVALUATING", actor=dg.username, reason="committee")
    c = Criterion.objects.create(tender=t, code="P1", name="price", weight=Decimal("100"), kind="SCORED", min_score=Decimal("0"))
    score_bid(bid, c, dg, Decimal("100"), "lowest")
    award, contract = award_contract(t, bid, Decimal("85000000"), "Lowest", dg, "CTR/OBJ/1", 90)
    obj = file_objection(award, "grounds", None, "anonymous", [{"name":"CSO","nominator":"CSO"}], [])
    assert t.status == "FROZEN"
    from workflow.models import ObjectionPanelist
    assert ObjectionPanelist.objects.filter(objection=obj, nominator="CSO").exists()
    acc = certify_acceptance(contract, dg, Decimal("85000000"), "CRAC/OBJ")
    cert = certify_payment(contract, acc, tre, Decimal("85000000")/2, "PAY/OBJ")
    assert cert.reference == "PAY/OBJ"
    with pytest.raises(Exception):
        certify_payment(contract, acc, tre, Decimal("85000000")+Decimal("1"), "PAY/2")

@pytest.mark.django_db
def test_catalogue_l1_auto_award():
    dg = User.objects.create_user("d_dg", "a@b.c", "pw", role="DG", mfa_secret="A"*32, mfa_enrolled_at=timezone.make_aware(datetime(2026,1,1)))
    agency = Agency.objects.create(code="ED2", name="Ministry Test", kind="MINISTRY")
    bl = BudgetLine.objects.create(fy="2026", agency=agency, project_code="X", description="x", amount=Decimal("5000000"), released=Decimal("5000000"))
    sup = Party.objects.create(legal_name="VendorA", rc_number="RC-A", tin="1-0001", email="a@b", phone="+1", state="Taraba", lga="Jalingo", category="B", scope="LOCAL", year_incorporated=2020)
    sup2 = Party.objects.create(legal_name="VendorB", rc_number="RC-B", tin="2-0001", email="b@b", phone="+2", state="Taraba", lga="Jalingo", category="B", scope="NATIONAL", year_incorporated=2020)
    sup3 = Party.objects.create(legal_name="VendorC", rc_number="RC-C", tin="3-0001", email="c@b", phone="+3", state="Nasarawa", lga="Keffi", category="B", scope="NATIONAL", year_incorporated=2020)
    CatalogueItem.objects.create(supplier=sup, category="STATIONERY", sku="PEN-B", name="Blue pen box", unit="box", unit_price=Decimal("1500"), lead_time_days=5)
    CatalogueItem.objects.create(supplier=sup2, category="STATIONERY", sku="PEN-B2", name="Blue pen box", unit="box", unit_price=Decimal("1400"), lead_time_days=7)
    CatalogueItem.objects.create(supplier=sup3, category="STATIONERY", sku="PEN-B3", name="Blue pen box", unit="box", unit_price=Decimal("1600"), lead_time_days=3)
    po = create_po(agency, bl, dg, "STATIONERY", "Pens", Decimal("1000"), "box", "standard blue pens", deadline_days=5)
    assert po.quotes.count() == 3
    award = __import__("workflow.services", fromlist=["award_po"]).award_po(po)
    po.refresh_from_db()
    assert po.status == "ACCEPTED"
    assert po.awarded_unit_price == Decimal("1400")  # lowest L1


@pytest.mark.django_db
def test_whistleblower_returns_trackable_reference():
    r = file_whistleblower("encrypted-body", contact_hash="h", tender_ocid="TAR-X")
    assert r.ref.startswith("WB-")
    assert WhistleblowerReport.objects.filter(ref=r.ref).exists()


@pytest.mark.django_db
def test_mfa_challenge_totp_enrol():
    u = User.objects.create_user("mfa_user", "u@b.c", "pw", role="PDE")
    sec, uri = enrol_totp(u)
    assert uri.startswith("otpauth://totp/")
    import pyotp
    code = pyotp.TOTP(sec).now()
    from django.test import RequestFactory
    from workflow.services import send_mfa_challenge, verify_mfa
    rf = RequestFactory(); req = rf.post("/x"); req.META["REMOTE_ADDR"]="127.0.0.1"
    ch = send_mfa_challenge(u, "TOTP")
    assert verify_mfa(ch, code)
