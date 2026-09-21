"""Authenticated and public endpoints for Phases 2-5. Read API continues to
live in procurement.api.views; these endpoints are the ones that write."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.contrib.auth import authenticate, login
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from procurement.models import Tender
from procurement.models_party import Party, User
from workflow.models import (
    CatalogueItem,
    MFASession,
    SupplierRegistrationDraft,
    TenderWatch,
)
from workflow.services import (
    add_draft_owner,
    approve_draft,
    client_encrypt_helper,
    decide_document,
    file_whistleblower,
    enrol_totp,
    reject_draft,
    save_draft_step,
    send_mfa_challenge,
    start_registration,
    start_verification,
    submit_bid,
    submit_draft,
    suspend_expired_suppliers,
    unseal_bids,
    upload_draft_document,
    verify_mfa,
)


def _json(request) -> dict:
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception:
        return {}


def _u(request) -> User | None:
    return request.user if request.user.is_authenticated else None


# ---------------------------------------------------------- vendor registration
#
# The write API for supplier onboarding. Vendor-side calls need no login (a
# draft is held by an unguessable 40-character token); reviewer-side calls need
# a session with an ADMIN/DG role. Errors come back as JSON with a 4xx code and
# the specific field named — never a bare 500 the vendor cannot act on.


def _draft_or_404(token: str) -> SupplierRegistrationDraft:
    try:
        return SupplierRegistrationDraft.objects.get(draft_token=token)
    except SupplierRegistrationDraft.DoesNotExist:
        raise Http404("No registration draft matches that link.")


def _validation_error(e: Exception) -> JsonResponse:
    messages = getattr(e, "message_dict", None) or {"detail": getattr(e, "messages", [str(e)])}
    return JsonResponse({"ok": False, "errors": messages}, status=400)


@require_POST
@csrf_exempt
def api_register_start(request):
    d = start_registration()
    return JsonResponse({"draft_token": d.draft_token, "reference": d.reference, "next_step": d.current_step})


@require_POST
@csrf_exempt
def api_register_step(request, token: str):
    body = _json(request)
    d = _draft_or_404(token)
    try:
        step = int(body.get("step", d.current_step))
        save_draft_step(d, step, body.get("data", {}))
    except ValidationError as e:
        return _validation_error(e)
    return JsonResponse({"draft_token": d.draft_token, "next_step": d.current_step, "ready": not d.ready_to_submit()})


@require_POST
@csrf_exempt
def api_register_document(request, token: str):
    """Attach a certificate. Two shapes: multipart with a real `file`, or JSON
    metadata (sha256/size/obj_key) for clients that uploaded the file straight
    to the object store."""
    d = _draft_or_404(token)
    try:
        if request.FILES.get("file"):
            f = request.FILES["file"]
            dd = upload_draft_document(
                d, request.POST.get("kind", ""), filename=f.name,
                content_type=f.content_type or "application/pdf",
                issued=request.POST.get("issued") or None, expiry=request.POST.get("expiry") or None,
                blob=f.read(),
            )
        else:
            body = _json(request)
            dd = upload_draft_document(d, body["kind"], body["sha256"], body["size_bytes"],
                                       body["obj_key"], body["filename"], body.get("content_type", "application/pdf"),
                                       body.get("issued"), body.get("expiry"))
    except ValidationError as e:
        return _validation_error(e)
    return JsonResponse({"ok": True, "kind": dd.kind, "sha256": dd.sha256, "size_bytes": dd.size_bytes})


@require_POST
@csrf_exempt
def api_register_owner(request, token: str):
    body = _json(request)
    d = _draft_or_404(token)
    try:
        add_draft_owner(d, body.get("name", ""), body.get("rc", ""), Decimal(str(body.get("pct", "0"))),
                        body.get("nin_hash", ""), bool(body.get("is_pep", False)))
    except ValidationError as e:
        return _validation_error(e)
    return JsonResponse({"ok": True})


@require_POST
@csrf_exempt
def api_register_submit(request, token: str):
    d = _draft_or_404(token)
    try:
        submit_draft(d)
    except ValidationError as e:
        return _validation_error(e)
    return JsonResponse({"ok": True, "status": d.status, "reference": d.reference})


@require_GET
def api_register_status(request, token: str):
    """The vendor's own view of their application, keyed by the private token."""
    d = _draft_or_404(token)
    required = sorted(d.required_document_kinds())
    return JsonResponse({
        "reference": d.reference,
        "status": d.status,
        "submitted_at": d.submitted_at,
        "reviewed_at": d.reviewed_at,
        "company_name": d.company_name,
        "outstanding": d.ready_to_submit() if d.status == "DRAFT" else [],
        "documents": {
            dd.kind: {"status": dd.verification_status, "note": dd.verification_note,
                      "expiry": str(dd.expiry_date) if dd.expiry_date else None}
            for dd in d.documents.all()
        },
        "required_documents": required,
        "review_notes": d.review_notes if d.status in ("REJECTED", "APPROVED") else "",
    })


def _reviewer(request) -> User:
    u = _u(request)
    if not u or u.role not in ("ADMIN", "DG"):
        raise PermissionDenied("Only Bureau administrators (ADMIN/DG) may review registrations.")
    return u


@require_POST
def api_register_verify_start(request, token: str):
    d = _draft_or_404(token)
    try:
        start_verification(d, _reviewer(request))
    except (ValidationError, PermissionDenied) as e:
        if isinstance(e, PermissionDenied):
            raise
        return _validation_error(e)
    return JsonResponse({"ok": True, "status": d.status})


@require_POST
def api_register_decide_document(request, token: str):
    body = _json(request)
    d = _draft_or_404(token)
    try:
        dd = decide_document(d, body.get("kind", ""), bool(body.get("accepted")), _reviewer(request), body.get("note", ""))
    except (ValidationError, PermissionDenied) as e:
        if isinstance(e, PermissionDenied):
            raise
        return _validation_error(e)
    return JsonResponse({"ok": True, "kind": dd.kind, "status": dd.verification_status})


@require_POST
def api_register_approve(request, token: str):
    d = _draft_or_404(token)
    try:
        p = approve_draft(d, _reviewer(request))
    except (ValidationError, PermissionDenied) as e:
        if isinstance(e, PermissionDenied):
            raise
        return _validation_error(e)
    return JsonResponse({"ok": True, "status": d.status, "party_id": p.pk, "rc_number": p.rc_number})


@require_POST
def api_register_reject(request, token: str):
    body = _json(request)
    d = _draft_or_404(token)
    try:
        reject_draft(d, _reviewer(request), body.get("reason", ""))
    except (ValidationError, PermissionDenied) as e:
        if isinstance(e, PermissionDenied):
            raise
        return _validation_error(e)
    return JsonResponse({"ok": True, "status": d.status})


@require_POST
def api_suspend_expired_suppliers(request):
    """Ops endpoint for the nightly job; equivalent to the management command."""
    if not (_u(request) and _u(request).role == "ADMIN"):
        raise PermissionDenied("ADMIN only.")
    suspended = suspend_expired_suppliers()
    return JsonResponse({"ok": True, "suspended": [p.rc_number for p in suspended]})


@require_GET
def api_tender_sealing_key(request, ocid: str):
    t = Tender.objects.get(ocid=ocid)
    if t.status != Tender.Status.PUBLISHED:
        return JsonResponse({"error":"not_open"}, status=400)
    k = t.sealing
    return JsonResponse({
        "ocid": ocid,
        "key": {
            "algorithm": "Fernet-AES-128-CBC-HMAC-SHA256",
            "public_keys": k.public_keys,
            "custodians": list(k.wrapped_shares.keys()),
        },
        "how": "encrypt(payload_json, tender Fernet key; base64; POST to /api/wf/bids/submit)",
    })


@require_POST
@csrf_exempt
def api_bid_submit(request, ocid: str):
    """Demo endpoint: in production the client encrypts with custodian public keys
    directly; here we accept a plaintext JSON body, encrypt it server-side, and
    immediately verify. This endpoint is wired as a demo so a bidder can see a
    working flow end-to-end in Phase 2."""
    body = _json(request)
    t = Tender.objects.get(ocid=ocid)
    supplier = None
    if request.user.is_authenticated and hasattr(request.user, "party") and request.user.party:
        supplier = request.user.party
    elif body.get("rc"):
        supplier = Party.objects.filter(rc_number=body["rc"]).first()
    if not supplier:
        return JsonResponse({"error":"no_supplier_identity"}, status=401)
    payload = {"amount": str(body["amount"]), "duration_days": int(body.get("duration_days", 90)),
               "items": body.get("items", []), "declaration": body.get("declaration", "")}
    fkey = bytes(t.sealing.tender_key_b64)
    ct = client_encrypt_helper(fkey, payload)
    bid, env = submit_bid(t, supplier, payload, ct, ip=_ip(request), ua=request.META.get("HTTP_USER_AGENT",""))
    return JsonResponse({"receipt": bid.receipt_ref, "commitment": bid.commitment_hash, "submitted_at": bid.submitted_at.isoformat()})


@require_POST
def api_unseal(request, ocid: str):
    u = _u(request)
    if not u or u.role not in ("DG", "AUDITOR"):
        return HttpResponseForbidden("Only DG/Auditor may trigger unsealing (custodian ceremony in production).")
    t = Tender.objects.get(ocid=ocid)
    failures = unseal_bids(t, actor=u.username)
    return JsonResponse({"ok": True, "failures": failures})


@require_POST
@csrf_exempt
def api_login(request):
    body = _json(request)
    u = authenticate(request, username=body.get("username"), password=body.get("password"))
    if not u:
        return JsonResponse({"error": "invalid"}, status=401)
    method = MFASession.Method.TOTP if u.role in { "DG","PDE","EVALUATOR","APPROVER","TREASURY","AUDITOR","ADMIN","APPEALS"} else MFASession.Method.SMS
    ch = send_mfa_challenge(u, method, ip=_ip(request), ua=request.META.get("HTTP_USER_AGENT",""))
    return JsonResponse({"mfa_token": ch.pk, "method": ch.method, "required": True})


@require_POST
@csrf_exempt
def api_mfa_verify(request):
    body = _json(request)
    ch = MFASession.objects.get(pk=body["token"])
    if not verify_mfa(ch, body.get("code","")):
        return JsonResponse({"error": "bad_code"}, status=401)
    login(request, ch.user, backend="django.contrib.auth.backends.ModelBackend")
    return JsonResponse({"ok": True, "role": ch.user.role, "enrol_totp": bool(not ch.user.mfa_secret and ch.user.role in {"DG","PDE","EVALUATOR","APPROVER","TREASURY","AUDITOR","ADMIN"})})


@require_POST
def api_enrol_totp(request):
    u = _u(request)
    if not u:
        return HttpResponseForbidden()
    secret, uri = enrol_totp(u)
    return JsonResponse({"secret": secret, "otpauth": uri})


@require_GET
def api_watchlist(request):
    u = _u(request)
    if not u or not hasattr(u,"party"):
        return HttpResponseForbidden()
    return JsonResponse({"watches": [w.tender.ocid for w in u.party.tender_watches.all()]})


@require_POST
def api_watch(request, ocid: str):
    u = _u(request)
    if not u or not hasattr(u, "party"):
        return HttpResponseForbidden()
    t = Tender.objects.get(ocid=ocid)
    TenderWatch.objects.get_or_create(party=u.party, tender=t)
    return JsonResponse({"ok": True})


@require_POST
@csrf_exempt
def api_whistleblower(request):
    body = _json(request)
    contact = body.get("contact", "")
    contact_hash = hashlib.sha256(contact.encode()).hexdigest() if contact else ""
    # The body is ciphertext generated client-side with a one-time key; we store it opaque.
    r = file_whistleblower(body_ciphertext=body.get("ciphertext",""), contact_hash=contact_hash, tender_ocid=body.get("ocid",""))
    return JsonResponse({"ref": r.ref})


@require_GET
def api_catalogue(request):
    items = CatalogueItem.objects.filter(is_active=True).order_by("category", "unit_price").values("supplier__legal_name", "category", "sku", "name", "unit", "unit_price", "lead_time_days")
    return JsonResponse({"items": list(items[:200])})


def _ip(request):
    return request.META.get("REMOTE_ADDR")


