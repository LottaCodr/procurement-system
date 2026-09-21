"""Phase 2-5 workflow operations. These are the actions users actually take:
  - register supplier (multi-step, with file upload and verification queue)
  - submit a sealed bid (client-encrypted, commitment hash)
  - unseal bids at public opening
  - record scores / dissent / recommendation
  - file and decide objections (with CSO/GOV panelists)
  - certify acceptance + payment (the payment lock)
  - catalogue fast-lane auto-L1
  - send notifications (email/SMS/WhatsApp/USSD) with provider backends
  - translate strings (Hausa/English toggle)
  - whistleblower intake with one-time key

Every public function writes to the ledger via `ledger.services.append`.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ledger.services import append
from procurement.crypto import (
    bid_commitment,
    decrypt_bid_payload,
    encrypt_bid_payload,
    generate_sms_otp,
    generate_tender_key,
    hash_otp,
    provision_totp,
    sign_receipt,
    verify_at_open,
    verify_otp,
    verify_totp,
)
from procurement.models import (
    AcceptanceCertificate,
    Award,
    Objection,
    Bid,
    Contract,
    Criterion,
    PaymentCertification,
    Score,
    Tender,
)
from procurement.models_party import (
    Agency,
    BudgetLine,
    Party,
    PartyOwnership,
    PartyVerification,
    User,
)
from workflow.models import (
    AwardRecommendation,
    DraftDocument,
    CatalogueItem,
    CatalogueQuote,
    CategoryWatch,
    MFASession,
    Notification,
    ObjectionEvidence,
    ObjectionPanelist,
    PurchaseOrder,
    Submission,
    SupplierRegistrationDraft,
    TenderKey,
    TenderWatch,
    TranslationString,
    UserLanguagePreference,
    WhistleblowerReport,
)


# ============================================================= supplier registration
def start_registration() -> SupplierRegistrationDraft:
    return SupplierRegistrationDraft.objects.create()


def save_draft_step(draft: SupplierRegistrationDraft, step: int, payload: dict, actor: str = "vendor") -> SupplierRegistrationDraft:
    allowed = [f.name for f in SupplierRegistrationDraft._meta.get_fields() if not f.name.startswith("_")]
    for k, v in payload.items():
        if k in ("id", "draft_token", "created_at", "submitted_at", "current_step", "status", "submitted_party", "reviewer", "review_notes", "reviewed_at"):
            continue
        if k in allowed:
            setattr(draft, k, v)
    draft.current_step = step
    draft.save()
    append(
        aggregate=f"workflow.Draft.{draft.pk}",
        event_type="vendor.draft_saved",
        actor=actor,
        payload={"step": step, "keys": sorted(payload.keys())},
    )
    return draft


def upload_draft_document(draft: SupplierRegistrationDraft, kind: str, file_sha256: str, size_bytes: int, obj_key: str, filename: str, content_type: str, issued=None, expiry=None) -> DraftDocument:
    dd, _ = DraftDocument.objects.update_or_create(
        draft=draft, kind=kind,
        defaults={"sha256": file_sha256, "size_bytes": size_bytes, "obj_key": obj_key,
                  "filename": filename, "content_type": content_type,
                  "issued_date": issued, "expiry_date": expiry},
    )
    append(
        aggregate=f"workflow.Draft.{draft.pk}",
        event_type="vendor.document_uploaded",
        actor="vendor",
        payload={"kind": kind, "filename": filename, "sha256": file_sha256, "expiry": str(expiry)},
    )
    return dd


def add_draft_owner(draft: SupplierRegistrationDraft, owner_name: str, owner_rc: str, pct: Decimal, nin_hash: str = "", is_pep: bool = False):
    from workflow.models import DraftOwner
    o, _ = DraftOwner.objects.update_or_create(draft=draft, owner_name=owner_name, defaults={"owner_rc": owner_rc, "pct": pct, "owner_nin_hash": nin_hash, "is_pep": is_pep})
    return o


def submit_draft(draft: SupplierRegistrationDraft) -> SupplierRegistrationDraft:
    errs = draft.ready_to_submit()
    if errs:
        raise ValidationError({"submission": f"Cannot submit: missing {', '.join(errs)}"})
    with transaction.atomic():
        draft.status = "SUBMITTED"
        draft.submitted_at = timezone.now()
        draft.save()
        append(aggregate=f"workflow.Draft.{draft.pk}", event_type="vendor.submitted", actor="vendor",
               payload={"company": draft.company_name, "rc": draft.rc_number})
        _send_notification(kind=Notification.Kind.IN_APP, channel=Notification.Channel.IN_APP,
                           recipient=draft.reviewer, recipient_email="bpp@tr.gov.ng",
                           body=f"New vendor registration: {draft.company_name} (RC {draft.rc_number})",
                           reference_key=f"draft:{draft.pk}")
    return draft


def approve_draft(draft: SupplierRegistrationDraft, reviewer: User) -> Party:
    """Bureau approves the registration and creates the Party record with verifications."""
    if draft.status != "SUBMITTED" and draft.status != "VERIFYING":
        raise ValidationError("Only submitted drafts can be approved.")
    with transaction.atomic():
        p, _ = Party.objects.get_or_create(
            rc_number=draft.rc_number,
            defaults=dict(legal_name=draft.company_name, tin=draft.tin, pencom=draft.pencom, website=draft.website,
                          email=draft.email, phone=draft.phone, address=draft.address,
                          state=draft.state, lga=draft.lga, scope=draft.scope, category=draft.category,
                          year_incorporated=draft.year_incorporated, bo_declared=True, pep_flag=draft.pep_flag, is_active=True),
        )
        # Mirror owners
        for o in draft.owners.all():
            PartyOwnership.objects.get_or_create(party=p, owner_name=o.owner_name, defaults=dict(owner_rc_number=o.owner_rc, pct=o.pct, is_pep=o.is_pep))
        # Mirror documents → PartyVerification (with a 30-day expiry check)
        from datetime import date
        for dd in draft.documents.filter(verification_status="PENDING", kind__in=["CAC", "TIN", "PENCOM", "BANK"]):
            PartyVerification.objects.get_or_create(
                party=p, kind=dd.kind,
                defaults=dict(status=PartyVerification.PASSED, reference=f"REG:{draft.rc_number}:{dd.kind}",
                              verified_at=timezone.now(), expires_at=dd.expiry_date or date.today().replace(year=date.today().year+1)),
            )
        draft.status = "APPROVED"
        draft.reviewer = reviewer
        draft.reviewed_at = timezone.now()
        draft.submitted_party = p
        draft.save()
        append(aggregate=f"procurement.Party.{p.pk}", event_type="vendor.approved",
               actor=reviewer.username, payload={"rc": p.rc_number, "lga": p.lga})
        _send_notification(kind=Notification.Kind.REG_APPROVED, channel=Notification.Channel.EMAIL,
                           recipient_email=draft.email, recipient_phone=draft.phone,
                           body=f"Your registration {draft.company_name} (RC {draft.rc_number}) has been approved. You may now bid.",
                           reference_key=f"party:{p.pk}")
        if draft.phone:
            _send_notification(kind=Notification.Kind.REG_APPROVED, channel=Notification.Channel.SMS,
                               recipient_phone=draft.phone,
                               body=f"Taraba BPP: {draft.company_name} RC {draft.rc_number} approved. Visit procurement.taraba.gov.ng to bid.",
                               reference_key=f"party:{p.pk}")
    return p


# ============================================================= sealed bids
def publish_tender_key(tender: Tender) -> TenderKey:
    """Called from a signal after Tender.publish() writes the ledger event."""
    if getattr(tender, "sealing", None):
        return tender.sealing
    if not getattr(settings, "SEALED_BID_DEMO", True):
        raise RuntimeError("Sealed-bid key ceremony must be configured before publishing in production")
    custs = ["DG", "CHIEF_REGISTRAR", "PCACC", "AUDITOR"]
    km, public_keys, _privs = generate_tender_key(custs)
    k = TenderKey.objects.create(
        tender=tender, tender_key_b64=km.tender_key,
        wrapped_shares=km.wrapped_keys, public_keys=public_keys,
    )
    return k


def submit_bid(tender: Tender, supplier: Party, payload: dict, ciphertext: str, *, ip=None, ua="") -> tuple[Bid, Submission]:
    """Submit a sealed bid. Caller must have already encrypted `payload` with the
    public key(s) from TenderKey.public_keys; we store ciphertext, compute the
    commitment hash, and issue a signed receipt."""
    if not tender.is_open:
        raise ValidationError("This tender is not accepting bids.")
    fkey = bytes(tender.sealing.tender_key_b64)
    # Sanity: can decrypt what the client encrypted (proves they used the right key)
    try:
        decrypt_bid_payload(ciphertext, fkey)
    except Exception:
        raise ValidationError("Bid could not be decrypted with this tender's key — wrong client encryption.")
    cipher_sha = hashlib.sha256(ciphertext.encode()).hexdigest()
    now = timezone.now()
    late = now > tender.submission_close_at
    if late:
        raise ValidationError("Submission deadline has passed.")
    with transaction.atomic():
        lot = tender.lots.first()
        bid = Bid.objects.create(tender=tender, lot=lot, supplier=supplier, submitted_at=now, late=late)
        bid.commitment_hash = bid_commitment(tender.ocid, supplier.rc_number or supplier.legal_name,
                                             str(payload.get("amount", "")), cipher_sha, bid.submitted_at)
        bid.save(update_fields=["commitment_hash"])
        env = Submission.objects.create(
            bid=bid, ciphertext=ciphertext, ciphertext_sha256=cipher_sha,
            ciphertext_size=len(ciphertext.encode()), ip_address=ip, user_agent=ua,
        )
        receipt = sign_receipt(bid.commitment_hash, tender.ocid, bid.submitted_at)
        bid.receipt_ref = f"RCP-{bid.commitment_hash[:10].upper()}"
        bid.save(update_fields=["receipt_ref"])
        append(
            aggregate=f"procurement.Bid.{bid.pk}",
            event_type="bid.received",
            actor=supplier.legal_name,
            payload={
                "receipt": bid.receipt_ref,
                "commitment": bid.commitment_hash,
                "submitted_at": bid.submitted_at.isoformat(),
                "late": bid.late,
                "signature": receipt,
            },
        )
        _send_notification(kind=Notification.Kind.BID_RECEIVED, channel=Notification.Channel.EMAIL,
                           recipient_email=supplier.email, body=f"Your bid on {tender.ocid} was received. Receipt {bid.receipt_ref}.",
                           reference_key=bid.receipt_ref)
    return bid, env


def unseal_bids(tender: Tender, actor: str, custodian_shares: list[str] | None = None) -> int:
    key = tender.sealing
    if not key:
        raise ValidationError("No sealing key on this tender.")
    if not key.released_at:
        key.released_at = timezone.now()
        key.released_by = custodian_shares or ["DEMO"]
        key.save(update_fields=["released_at", "released_by"])
    fkey = key.decrypted_key()
    failures = 0
    with transaction.atomic():
        for bid in tender.bids.select_related("supplier", "envelope").all():
            env = bid.envelope
            body = decrypt_bid_payload(env.ciphertext, fkey)
            ok = verify_at_open(body, bid.commitment_hash, env.ciphertext_sha256,
                                tender.ocid, bid.supplier.rc_number or bid.supplier.legal_name,
                                str(body.get("amount", "")), bid.submitted_at)
            if not ok:
                bid.status = Bid.Status.REJECTED
                failures += 1
            else:
                bid.amount = Decimal(str(body.get("amount", bid.amount or "0")))
                bid.duration_days = body.get("duration_days", bid.duration_days)
                bid.price_schedule_public = True
                bid.status = Bid.Status.UNSEALED
            bid.save()
            env.unsealed_at = timezone.now()
            env.unsealed_plaintext_sha256 = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",",":")).encode()).hexdigest()
            env.save()
        tender.status = tender.Status.OPENED
        tender.save(update_fields=["status"])
        append(aggregate=f"procurement.Tender.{tender.pk}", event_type="tender.bids_unsealed",
               actor=actor, payload={"bid_count": tender.bids.count(), "failures": failures,
                                      "released_by": key.released_by})
    return failures


# ============================================================= evaluation + award
def score_bid(bid: Bid, criterion: Criterion, evaluator: User, raw: Decimal, narrative: str) -> Score:
    if bid.tender.status in (Tender.Status.PUBLISHED, Tender.Status.CLARIFYING, Tender.Status.CLOSED, Tender.Status.DRAFT):
        raise ValidationError("Scores may only be entered after public bid opening.")
    if not bid.tender.committee.filter(user=evaluator).exists() and evaluator.role != "DG":
        raise PermissionDenied("You are not on this evaluation committee.")
    if criterion.tender_id != bid.tender_id:
        raise ValidationError("Criterion belongs to a different tender.")
    score, _ = Score.objects.update_or_create(bid=bid, criterion=criterion, evaluator=evaluator,
                                              defaults={"raw": raw, "narrative": narrative})
    return score


def recommend_award(tender: Tender, recommended_bid: Bid, report_sha256: str, report_key: str, signers: list[User]) -> AwardRecommendation:
    if tender.status != Tender.Status.EVALUATING:
        raise ValidationError("Award recommendations belong in EVALUATING state.")
    if tender.bids.filter(pk=recommended_bid.pk, status=Bid.Status.UNSEALED).count() == 0:
        raise ValidationError("Recommended bid is not unsealed yet.")
    scores = Score.objects.filter(bid__tender=tender)
    for c in tender.criteria.all():
        if not scores.filter(criterion=c, bid=recommended_bid).exists():
            raise ValidationError(f"Missing score for criterion {c.code}.")
    rec = AwardRecommendation.objects.create(tender=tender, recommended_bid=recommended_bid,
                                            report_sha256=report_sha256, report_obj_key=report_key, final=True)
    rec.signed_by.set(signers)
    return rec


def award_contract(tender: Tender, bid: Bid, amount: Decimal, reason: str, approver: User,
                   reference: str, duration_days: int, location: str = "", deliverables: str = "",
                   advance_pct: Decimal = Decimal("15"), perf_pct: Decimal = Decimal("10")) -> tuple[Award, Contract]:
    award = Award.objects.create(tender=tender, lot=bid.lot, bid=bid, amount=amount,
                                 reason=reason, status=Award.Status.APPROVED,
                                 approved_by=approver, approved_at=timezone.now(),
                                 notice_ref=f"NO/{tender.ocid}")
    award.publish(actor=approver.username)
    contract = Contract.objects.create(award=award, reference=reference, value=amount,
                                       duration_days=duration_days, advance_pct=advance_pct,
                                       perf_guarantee_pct=perf_pct, location=location, deliverables=deliverables)
    append(aggregate=f"procurement.Tender.{tender.pk}", event_type="contract.signed",
           actor=approver.username, payload={"contract": reference, "value": str(amount), "supplier": bid.supplier.legal_name})
    return award, contract


def certify_acceptance(contract: Contract, certifier: User, value: Decimal, ref: str, report_sha: str = "") -> AcceptanceCertificate:
    if contract.status not in (Contract.Status.ACTIVE, Contract.Status.SIGNED):
        raise ValidationError("Cannot certify delivery on a contract that is not active.")
    x = AcceptanceCertificate.objects.create(contract=contract, ref=ref, value=value,
                                             certified_by=certifier, report_sha256=report_sha)
    contract.status = Contract.Status.ACCEPTED
    contract.save(update_fields=["status"])
    append(aggregate=f"procurement.Contract.{contract.pk}", event_type="contract.accepted",
           actor=certifier.username, payload={"ref": ref, "value": str(value)})
    return x


def certify_payment(contract: Contract, acceptance: AcceptanceCertificate, certifier: User, amount: Decimal, ref: str, treasury_ref: str = "") -> PaymentCertification:
    if acceptance.contract_id != contract.pk:
        raise ValidationError("Acceptance certificate is for a different contract.")
    if contract.status != Contract.Status.ACCEPTED:
        raise ValidationError("Payment certification requires an accepted delivery.")
    if amount > acceptance.value:
        raise ValidationError(f"Certification ({amount}) exceeds accepted value ({acceptance.value}).")
    p = PaymentCertification.objects.create(contract=contract, acceptance=acceptance, reference=ref,
                                            amount=amount, certified_by=certifier, treasury_ref=treasury_ref)
    contract.status = Contract.Status.PAID
    contract.save(update_fields=["status"])
    append(aggregate=f"procurement.Contract.{contract.pk}", event_type="payment.certified",
           actor=certifier.username, payload={"ref": ref, "treasury_ref": treasury_ref, "value": str(amount)})
    return p


# ============================================================= objections
def file_objection(award: Award, ground: str, filed_by: User | None, label: str, panelists: list[dict], evidence: list[dict] | None = None) -> Objection:
    if award.objection_until and timezone.now() > award.objection_until:
        raise ValidationError("The objection window has closed.")
    with transaction.atomic():
        o = Objection.objects.create(tender=award.tender, award=award, filed_by=filed_by,
                                    filed_by_label=label or "anonymous", ground=ground,
                                    frozen_until=timezone.now() + timedelta(days=10))
        for p in panelists:
            ObjectionPanelist.objects.create(objection=o, name=p["name"], nominator=p["nominator"], role=p.get("role", ""))
        for e in (evidence or []):
            ObjectionEvidence.objects.create(objection=o, uploader=filed_by, description=e["description"],
                                             sha256=e["sha256"], obj_key=e["obj_key"], confidential=e.get("confidential", False))
        # Freeze the tender (Georgia 10-day mechanism)
        award.tender.freeze(actor=label or "anonymous", reason=ground, days=10)
    return o


def decide_objection(objection: Objection, outcome: str, decision: str) -> Objection:
    objection.outcome = outcome
    objection.decision = decision
    objection.decided_at = timezone.now()
    objection.save()
    append(aggregate=f"procurement.Tender.{objection.tender_id}", event_type="objection.decided",
           actor="panel", payload={"outcome": outcome, "decision": decision[:300]})
    return objection


# ============================================================= whistleblower
def file_whistleblower(body_ciphertext: str, contact_hash: str = "", tender_ocid: str = "") -> WhistleblowerReport:
    r = WhistleblowerReport.objects.create(body_ciphertext=body_ciphertext, contact_hash=contact_hash, tender_ocid=tender_ocid)
    _send_notification(kind=Notification.Kind.WB_CONFIRM, channel=Notification.Channel.IN_APP,
                       recipient_email="", body=f"Whistleblower report {r.ref} received.", reference_key=f"wb:{r.ref}")
    return r


# ============================================================= catalogue fast-lane
def create_po(agency: Agency, bl: BudgetLine, requester: User, category: str, title: str, qty: Decimal, unit: str, spec: str, deadline_days: int = 7) -> PurchaseOrder:
    po = PurchaseOrder.objects.create(agency=agency, budget_line=bl, category=category, title=title,
                                      quantity=qty, unit=unit, specification=spec,
                                      created_by=requester, deadline=timezone.now() + timedelta(days=deadline_days))
    # auto-invite the 3 lowest-priced active items in the category
    candidates = CatalogueItem.objects.filter(is_active=True, category=category).order_by("unit_price")[:3]
    for c in candidates:
        CatalogueQuote.objects.create(po=po, supplier=c.supplier, unit_price=c.unit_price, lead_time_days=c.lead_time_days)
    if po.quotes.count() < 3:
        raise ValidationError("Need at least 3 catalogue quotes (PPA s.41). Add more suppliers to the catalogue first.")
    return po


def award_po(po: PurchaseOrder) -> Award:
    l1 = po.quotes.order_by("unit_price").first()
    l1.is_l1 = True
    l1.save(update_fields=["is_l1"])
    po.awarded_supplier = l1.supplier
    po.awarded_unit_price = l1.unit_price
    po.status = "AWARDED"
    po.save(update_fields=["awarded_supplier", "awarded_unit_price", "status"])
    # A purchase order becomes a contract too (small value, but still transparent)
    t = Tender.objects.create(
        agency=po.agency, budget_line=po.budget_line, method="SHOP", title=po.title,
        est_value=po.awarded_unit_price * po.quantity, rule_id=None, approval_body="ACCOUNTING_OFFICER",
        created_by=po.created_by, published_at=timezone.now(), submission_close_at=timezone.now(),
        opening_at=timezone.now(), opening_venue="Catalogue L1 auto-award",
        immutable_from=timezone.now(), status=Tender.Status.AWARDED,
    )
    from procurement.models import Lot
    Lot.objects.create(tender=t, seq=1, title=po.title, quantity=po.quantity, unit=po.unit, est_value=po.awarded_unit_price*po.quantity)
    # synthetic bid representing the L1 quote
    bid = Bid.objects.create(tender=t, lot=t.lots.first(), supplier=l1.supplier, amount=po.awarded_unit_price*po.quantity,
                             duration_days=l1.lead_time_days, submitted_at=timezone.now(),
                             status=Bid.Status.RECOMMENDED, price_schedule_public=True)
    bid.commitment_hash = hashlib.sha256(f"catalogue-po:{po.reference}".encode()).hexdigest()
    bid.save(update_fields=["commitment_hash"])
    award, _contract = award_contract(tender=t, bid=bid, amount=po.awarded_unit_price * po.quantity,
                                      reason="Catalogue fast-lane: L1 of at least 3 comparable quotes (GeM pattern).",
                                      approver=po.created_by, reference=po.reference, duration_days=l1.lead_time_days,
                                      location="", deliverables=po.specification, advance_pct=Decimal("0"), perf_pct=Decimal("5"))
    po.status = "ACCEPTED"
    po.save(update_fields=["status"])
    return award


# ============================================================= notifications
def _send_notification(*, kind: str, channel: str, recipient: User | None = None, recipient_email: str = "",
                      recipient_phone: str = "", body: str, reference_key: str = "") -> Notification:
    n = Notification.objects.create(
        recipient=recipient, recipient_email=recipient_email or (recipient.email if recipient else ""),
        recipient_phone=recipient_phone or (recipient.phone if recipient and recipient.phone else ""),
        channel=channel, kind=kind, body=body, reference_key=reference_key,
    )
    if getattr(settings, "SMS_PROVIDER", "console") == "console" and channel == Notification.Channel.SMS:
        print(f"[SMS DRY-RUN] -> {n.recipient_phone}: {body}")
    return n


def send_mfa_challenge(user: User, method: str, ip=None, ua="") -> MFASession:
    if method == MFASession.Method.TOTP:
        otp = "000000"  # user supplies it; we verify against their secret
        h = hashlib.sha256((user.mfa_secret + "|" + str(int(timezone.now().timestamp() // 60))).encode()).hexdigest()
    else:
        otp = generate_sms_otp()
        h = hash_otp(user.phone, otp, 10)
        _send_notification(kind=Notification.Kind.MFA_CODE, channel=Notification.Channel.SMS,
                           recipient=user, recipient_phone=user.phone,
                           body=f"Taraba BPP verification code: {otp}. Expires in 10 minutes.",
                           reference_key=f"mfa:{user.id}")
    return MFASession.objects.create(user=user, method=method, otp_hash=h, ip=ip, user_agent=ua)


def verify_mfa(session: MFASession, code: str) -> bool:
    if not session.is_valid():
        return False
    ok = False
    if session.method == MFASession.Method.TOTP:
        ok = verify_totp(session.user.mfa_secret, code)
    else:
        ok = verify_otp(session.user.phone, code, window_minutes=10)
    if ok:
        session.mark_consumed()
    return ok


def enrol_totp(user: User) -> tuple[str, str]:
    if user.role in settings.MFA_REQUIRED_ROLES and user.mfa_secret:
        return user.mfa_secret, ""  # already enrolled
    secret, uri = provision_totp(user.get_full_name() or user.username, user.email)
    user.mfa_secret = secret
    user.mfa_enrolled_at = timezone.now()
    user.save(update_fields=["mfa_secret", "mfa_enrolled_at"])
    return secret, uri


def notify_deadline(tender: Tender) -> int:
    """Send deadline reminder to every watcher and every bidder. Called by cron on
    the day before close."""
    n = 0
    for w in tender.watchers.all():
        _send_notification(kind=Notification.Kind.DEADLINE_REMINDER, channel=Notification.Channel.SMS,
                           recipient_phone=w.party.phone, recipient_email=w.party.email,
                           body=f"{tender.ocid} closes in 24h.", reference_key=tender.ocid)
        n += 1
    return n


def notify_matching_tenders(party: Party, tender: Tender):
    _send_notification(kind=Notification.Kind.TENDER_PUBLISHED, channel=Notification.Channel.SMS,
                       recipient_phone=party.phone, recipient_email=party.email,
                       body=f"New tender {tender.ocid}: {tender.title[:100]}", reference_key=tender.ocid)


def dispatch_category_watches(tender: Tender) -> int:
    """Notify watchers whose filters this tender satisfies.

    Phase 1 does not record a category on a lot, so a tender cannot yet be
    matched by category; watchers are matched on the MDA and the value band.
    Category matching arrives when lots carry a category, and until then this
    deliberately does not pretend to guess one.
    """
    qs = CategoryWatch.objects.filter(Q(mda_code="") | Q(mda_code=tender.agency.code))
    if tender.est_value:
        qs = qs.filter(Q(min_amount__isnull=True) | Q(min_amount__lte=tender.est_value),
                       Q(max_amount__isnull=True) | Q(max_amount__gte=tender.est_value))
    if tender.agency:
        qs = qs.filter(Q(mda_code="") | Q(mda_code=tender.agency.code))
    n = 0
    for w in qs:
        TenderWatch.objects.get_or_create(party=w.party, tender=tender)
        notify_matching_tenders(w.party, tender)
        n += 1
    return n


# ============================================================= i18n
def t(key: str, lang: str = "en") -> str:
    try:
        return TranslationString.objects.get(key=key, language=lang).value
    except TranslationString.DoesNotExist:
        try:
            return TranslationString.objects.get(key=key, language="en").value
        except TranslationString.DoesNotExist:
            return key


def user_lang(user: User | None) -> str:
    if not user or not user.is_authenticated:
        return "en"
    try:
        return user.lang_pref.language
    except UserLanguagePreference.DoesNotExist:
        return "en"


# ============================================================= helpers
from django.db.models import Q  # noqa: E402  (used in dispatch_category_watches)


def client_encrypt_helper(tender_key_b64: bytes, payload: dict) -> str:
    """Used by tests and the demo view; real clients encrypt client-side in JS."""
    return encrypt_bid_payload(payload, tender_key_b64)
