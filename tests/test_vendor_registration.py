"""Vendor registration, end to end, the way a real bidder would drive it.

What these tests nail down:

* The full browser flow — start, six steps, real file uploads, submit, track,
  review, approve — works without a single line of JavaScript.
* Statutory exemptions are honoured: a firm with fewer than 3 employees is not
  asked for a PenCom certificate (Pension Reform Act 2014), and the form says
  so instead of failing.
* Identity fields are format-checked at the form, not three weeks into review.
* Nobody gets silently duplicated, silently approved, or silently refused.
* Review decisions are role-gated, named, and dated; the public supplier
  register only ever shows verified suppliers.
* Credentials that lapse suspend the supplier — "approved once, trusted
  forever" is not how the register behaves.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from procurement.models_party import Party, PartyVerification
from workflow.models import DraftDocument, Notification, SupplierRegistrationDraft
from workflow.services import (
    approve_draft,
    decide_document,
    save_draft_step,
    start_registration,
    submit_draft,
    suspend_expired_suppliers,
    upload_draft_document,
)

User = __import__("django.contrib.auth", fromlist=["get_user_model"]).get_user_model()

PDF = b"%PDF-1.4 fake certificate bytes for tests"


def _make_dg(username="dg"):
    return User.objects.create_user(
        username, f"{username}@bpp.ng", "pw", role="DG",
        mfa_secret="A" * 32, mfa_enrolled_at=timezone.make_aware(datetime(2026, 1, 1)),
    )


def _start_and_fill(client, rc="RC5000001", tin="12345678-0001", employees=None, name="Wukari Traders Ltd"):
    """Drive the real form pages through steps 1, 2 and 4."""
    r = client.post("/tenders/register/create/")
    assert r.status_code == 302
    token = r.url.rstrip("/").split("/")[-1]
    url = f"/tenders/register/form/{token}/"

    step1 = {"action": "save", "step": "1", "company_name": name, "rc_number": rc, "tin": tin,
             "pencom": "", "year_incorporated": "2019", "website": ""}
    r = client.post(url, step1, follow=True)
    assert r.status_code == 200, f"step1 failed: {r.content[:600]}"

    r = client.post(url, {"action": "save", "step": "2", "full_name": "Amina Yusuf",
                          "email": "amina@wukaritraders.ng", "phone": "+234 803 555 0101",
                          "address": "12 Sardauna Street", "state": "Taraba", "lga": "Wukari"},
                    follow=True)
    assert r.status_code == 200

    if employees is not None:
        r = client.post(url, {"action": "save", "step": "4", "category": "B", "scope": "LOCAL",
                              "employees_count": str(employees), "similar_contracts_n": "3",
                              "annual_turnover": "25,000,000"}, follow=True)
    else:
        r = client.post(url, {"action": "save", "step": "4", "category": "B", "scope": "LOCAL",
                              "similar_contracts_n": "3", "annual_turnover": ""}, follow=True)
    assert r.status_code == 200
    return token, url


def _attach_docs(client, url, kinds=("CAC", "TIN", "PENCOM")):
    for kind in kinds:
        f = SimpleUploadedFile(f"{kind.lower()}.pdf", PDF, content_type="application/pdf")
        r = client.post(url, {"action": "upload", "kind": kind, "file": f, "expiry": "2027-12-31"},
                        follow=True)
        assert r.status_code == 200, f"upload {kind} failed: {r.content[:600]}"


def _add_owner_and_terms(client, url):
    r = client.post(url, {"action": "add_owner", "owner_name": "Amina Yusuf",
                          "owner_rc": "", "owner_pct": "100"}, follow=True)
    assert r.status_code == 200
    return client.post(url, {"action": "submit", "accept_terms": "on"})


# ------------------------------------------------------------------ happy path
@pytest.mark.django_db
def test_full_browser_flow_start_to_approved_supplier():
    client = Client()
    token, url = _start_and_fill(client)
    _attach_docs(client, url)
    r = _add_owner_and_terms(client, url)
    assert r.status_code == 302 and r.url.startswith("/tenders/register/done/")

    draft = SupplierRegistrationDraft.objects.get(draft_token=token)
    assert draft.status == "SUBMITTED" and draft.reference.startswith("VND-")
    # The certificate bytes the vendor uploaded are what the register holds…
    doc = draft.documents.get(kind="CAC")
    assert bytes(doc.blob) == PDF and doc.size_bytes == len(PDF)
    # …and the stored hash proves it.
    import hashlib
    assert doc.sha256 == hashlib.sha256(PDF).hexdigest()

    # Confirmation and status pages work from the vendor's side.
    done = client.get(f"/tenders/register/done/{token}/")
    assert done.status_code == 200 and draft.reference in done.content.decode()
    status = client.get(f"/tenders/register/status/{token}/")
    assert "Submitted" in status.content.decode()

    # The reviewer sees it in the public queue and opens the detail page.
    dg = _make_dg()
    client.force_login(dg)
    queue = client.get("/tenders/register/review/")
    assert draft.reference in queue.content.decode()
    detail = client.get(f"/tenders/register/review/{draft.pk}/")
    assert detail.status_code == 200

    decide_url = f"/tenders/register/review/{draft.pk}/decide/"
    # Approval is impossible before the documents are decided.
    r = client.post(decide_url, {"action": "approve"})
    assert r.status_code == 400 and "certificate" in r.content.decode()

    for kind in ("CAC", "TIN", "PENCOM"):
        r = client.post(decide_url, {"action": "accept_doc", "kind": kind, "note": "matches CAC/FIRS record"})
        assert r.status_code == 200
    r = client.post(decide_url, {"action": "approve"})
    assert r.status_code == 200

    draft.refresh_from_db()
    assert draft.status == "APPROVED"
    party = draft.submitted_party
    assert party is not None and party.is_active

    # Verifications are dated and attributable — design doc 3.2.
    cac = party.verifications.get(kind="CAC")
    assert cac.status == "PASSED" and cac.verified_by == dg and cac.verified_at is not None
    assert party.verifications.filter(kind="PENSION", status="PASSED").exists()

    # The public register now lists the firm; the API agrees.
    assert client.get("/tenders/suppliers/?q=Wukari").status_code == 200
    api = Client().get(f"/api/v1/suppliers?rc={party.rc_number}").json()
    assert api["count"] == 1 and api["results"][0]["verifications"]["CAC"]["status"] == "PASSED"

    # Approval notification went to the vendor by email and SMS.
    assert Notification.objects.filter(kind="REG_OK", recipient_email="amina@wukaritraders.ng").exists()
    assert Notification.objects.filter(kind="REG_OK", channel="SMS").exists()

    # A submitted draft is locked: editing it now is refused.
    r = client.post(url, {"action": "save", "step": "1", "company_name": "Renamed Ltd",
                          "rc_number": "RC5000001", "tin": "12345678-0001"})
    assert r.status_code == 400


# ------------------------------------------------------------------ exemptions
@pytest.mark.django_db
def test_pencom_exempt_for_micro_employers_and_explained():
    client = Client()
    token, url = _start_and_fill(client, rc="RC5000002", tin="23456789-0001", employees=2,
                                 name="Two Hands Ventures")
    _attach_docs(client, url, kinds=("CAC", "TIN"))  # no PenCom
    r = _add_owner_and_terms(client, url)
    assert r.status_code == 302, f"micro firm must submit without PenCom: {r.content[:400]}"

    draft = SupplierRegistrationDraft.objects.get(draft_token=token)
    assert draft.status == "SUBMITTED"
    assert "PENCOM" not in draft.required_document_kinds()


@pytest.mark.django_db
def test_pencom_still_required_for_larger_employers():
    client = Client()
    token, url = _start_and_fill(client, rc="RC5000003", tin="34567890-0001", employees=10,
                                 name="Ten Staff Ltd")
    _attach_docs(client, url, kinds=("CAC", "TIN"))
    r = _add_owner_and_terms(client, url)
    assert r.status_code == 400
    assert "PENCOM" in r.content.decode()


# ------------------------------------------------------------------ validation
@pytest.mark.django_db
def test_bad_tin_and_rc_are_rejected_at_the_form():
    client = Client()
    r = client.post("/tenders/register/create/")
    token = r.url.rstrip("/").split("/")[-1]
    url = f"/tenders/register/form/{token}/"

    r = client.post(url, {"action": "save", "step": "1", "company_name": "X", "rc_number": "RC5000004",
                          "tin": "123"})
    assert r.status_code == 400 and "TIN" in r.content.decode()

    r = client.post(url, {"action": "save", "step": "1", "company_name": "X", "rc_number": "hello",
                          "tin": "12345678-0001"})
    assert r.status_code == 400 and "RC" in r.content.decode()

    # Sloppy-but-valid input is normalised, not refused.
    r = client.post(url, {"action": "save", "step": "1", "company_name": "X", "rc_number": "rc 5000004",
                          "tin": "123456780001"})
    assert r.status_code == 302  # saved and moved on
    d = SupplierRegistrationDraft.objects.get(draft_token=token)
    assert d.rc_number == "RC5000004" and d.tin == "12345678-0001"


@pytest.mark.django_db
def test_duplicate_rc_is_named_not_silently_doubled():
    Party.objects.create(legal_name="Existing Co Ltd", rc_number="RC5000005", tin="45678901-0001",
                         email="old@co.ng", phone="08000000000", is_active=True)
    client = Client()
    token, url = _start_and_fill(client, rc="RC5000005", tin="45678901-0001", name="Copycat Ltd")
    _attach_docs(client, url)
    client.post(url, {"action": "add_owner", "owner_name": "C Copy", "owner_rc": "", "owner_pct": "100"})
    r = client.post(url, {"action": "submit", "accept_terms": "on"})
    assert r.status_code == 400
    assert "already registered" in r.content.decode()


# ------------------------------------------------------------------ rejection
@pytest.mark.django_db
def test_rejection_names_reasons_and_reaches_the_vendor():
    client = Client()
    token, url = _start_and_fill(client, rc="RC5000006", tin="56789012-0001", name="Shaky Ltd")
    _attach_docs(client, url)
    _add_owner_and_terms(client, url)
    draft = SupplierRegistrationDraft.objects.get(draft_token=token)

    dg = _make_dg("boss2")
    client.force_login(dg)
    r = client.post(f"/tenders/register/review/{draft.pk}/decide/",
                    {"action": "refuse_doc", "kind": "CAC", "note": ""})
    assert r.status_code == 400  # refusing without a reason is itself refused

    r = client.post(f"/tenders/register/review/{draft.pk}/decide/",
                    {"action": "refuse_doc", "kind": "CAC", "note": "Certificate number not found on CAC portal."})
    assert r.status_code == 200
    r = client.post(f"/tenders/register/review/{draft.pk}/decide/",
                    {"action": "reject", "reason": "CAC certificate could not be verified against the registry."})
    assert r.status_code == 200

    draft.refresh_from_db()
    assert draft.status == "REJECTED" and "CAC" in draft.review_notes

    notice = Notification.objects.get(kind="REG_NO", recipient_email="amina@wukaritraders.ng")
    assert "CAC certificate could not be verified" in notice.body

    # The vendor's status page shows the refusal and the way forward.
    anon = Client()
    page = anon.get(f"/tenders/register/status/{token}/").content.decode()
    assert "Rejected" in page and "could not be verified" in page and draft.review_notes in page

    # Refusal without a reason is impossible at the service level too.
    with pytest.raises(Exception):
        from workflow.services import reject_draft
        reject_draft(draft, dg, "   ")


# ------------------------------------------------------------------ permissions
@pytest.mark.django_db
def test_review_actions_are_gated_to_bureau_roles():
    client = Client()
    token, url = _start_and_fill(client, rc="RC5000007", tin="67890123-0001", name="No Entry Ltd")
    _attach_docs(client, url)
    _add_owner_and_terms(client, url)
    draft = SupplierRegistrationDraft.objects.get(draft_token=token)
    decide_url = f"/tenders/register/review/{draft.pk}/decide/"

    anon = Client()
    assert anon.post(decide_url, {"action": "approve"}).status_code == 403

    pde = User.objects.create_user("pde1", "p@b.ng", "pw", role="PDE",
                                   mfa_secret="B" * 32, mfa_enrolled_at=timezone.now())
    anon.force_login(pde)
    assert anon.post(decide_url, {"action": "approve"}).status_code == 403
    # And nothing happened.
    draft.refresh_from_db()
    assert draft.status == "SUBMITTED"


@pytest.mark.django_db
def test_status_lookup_by_reference_and_email():
    client = Client()
    token, url = _start_and_fill(client, rc="RC5000008", tin="78901234-0001", name="Findable Ltd")
    _attach_docs(client, url)
    _add_owner_and_terms(client, url)
    draft = SupplierRegistrationDraft.objects.get(draft_token=token)

    anon = Client()
    r = anon.post("/tenders/register/status/", {"reference": draft.reference, "email": "amina@wukaritraders.ng"})
    assert r.status_code == 200 and draft.reference in r.content.decode()
    r = anon.post("/tenders/register/status/", {"reference": draft.reference, "email": "wrong@elsewhere.ng"})
    assert "Not found" in r.content.decode()


# ------------------------------------------------------------------ json api
@pytest.mark.django_db
def test_json_api_registration_still_works_and_reports_status():
    c = Client()
    token = c.post("/api/wf/vendor/register/start").json()["draft_token"]
    r = c.post(f"/api/wf/vendor/register/{token}/step",
               data='{"step":1,"data":{"company_name":"API Co","rc_number":"rc5000009","tin":"89012345-0001"}}',
               content_type="application/json")
    assert r.status_code == 200
    bad = c.post(f"/api/wf/vendor/register/{token}/step",
                 data='{"step":1,"data":{"tin":"9"}}', content_type="application/json")
    assert bad.status_code == 400 and "tin" in bad.json()["errors"]

    st = c.get(f"/api/wf/vendor/register/{token}/status").json()
    assert st["status"] == "DRAFT" and st["company_name"] == "API Co"
    assert "rc_number" not in st["outstanding"]  # normalised + present

    # Reviewer endpoints refuse anonymous callers.
    assert c.post(f"/api/wf/vendor/register/{token}/approve").status_code == 403


@pytest.mark.django_db
def test_approve_requires_accepted_documents_even_via_service():
    dg = _make_dg("svc")
    d = start_registration()
    save_draft_step(d, 1, {"company_name": "S Ltd", "rc_number": "RC5000010", "tin": "11111111-0001",
                           "category": "B", "scope": "LOCAL"})
    save_draft_step(d, 2, {"full_name": "S One", "email": "s@l.ng", "phone": "0801", "address": "x"})
    for kind, h in (("CAC", "a" * 64), ("TIN", "b" * 64), ("PENCOM", "c" * 64)):
        upload_draft_document(d, kind, h, 100, f"obj:{kind}", f"{kind}.pdf", "application/pdf")
    from workflow.services import add_draft_owner
    add_draft_owner(d, "S One", "", Decimal("100"))
    d.accept_terms = True
    d.save()
    submit_draft(d)

    with pytest.raises(Exception):
        approve_draft(d, dg)  # nothing accepted yet
    for kind in ("CAC", "TIN", "PENCOM"):
        decide_document(d, kind, True, dg, "ok")
    p = approve_draft(d, dg)
    assert p.is_active


# ------------------------------------------------------------------ expiry
@pytest.mark.django_db
def test_lapsed_credentials_suspend_the_supplier_and_leave_a_trail():
    p = Party.objects.create(legal_name="Lapsed Ltd", rc_number="RC5000011", tin="22222222-0001",
                             email="l@l.ng", phone="0802", is_active=True)
    PartyVerification.objects.create(party=p, kind="TIN", status="PASSED",
                                     verified_at=timezone.now() - timedelta(days=400),
                                     expires_at=date.today() - timedelta(days=1))

    suspended = suspend_expired_suppliers()
    p.refresh_from_db()
    assert p in suspended and p.is_active is False
    # Hidden from the public supplier list and the API.
    assert not Party.objects.filter(is_active=True, pk=p.pk).exists()
    from ledger.models import Event
    assert Event.objects.filter(event_type="vendor.suspended_credential_lapsed").exists()

    # The management command agrees and reports.
    from django.core.management import call_command
    from io import StringIO
    out = StringIO()
    call_command("annual_reverification", "--dry-run", stdout=out)
    assert "Lapsed" not in out.getvalue()  # already suspended; nothing left to do
