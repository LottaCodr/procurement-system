"""Authenticated and public endpoints for Phases 2-5. Read API continues to
live in procurement.api.views; these endpoints are the ones that write."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.contrib.auth import authenticate, login
from django.http import JsonResponse, HttpResponseForbidden
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
    client_encrypt_helper,
    file_whistleblower,
    enrol_totp,
    save_draft_step,
    send_mfa_challenge,
    start_registration,
    submit_bid,
    submit_draft,
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


@require_POST
@csrf_exempt
def api_register_start(request):
    d = start_registration()
    return JsonResponse({"draft_token": d.draft_token, "next_step": d.current_step})


@require_POST
@csrf_exempt
def api_register_step(request, token: str):
    body = _json(request)
    d = SupplierRegistrationDraft.objects.get(draft_token=token)
    step = int(body.get("step", d.current_step))
    save_draft_step(d, step, body.get("data", {}))
    return JsonResponse({"draft_token": d.draft_token, "next_step": d.current_step, "ready": not d.ready_to_submit()})


@require_POST
@csrf_exempt
def api_register_document(request, token: str):
    body = _json(request)
    d = SupplierRegistrationDraft.objects.get(draft_token=token)
    dd = upload_draft_document(d, body["kind"], body["sha256"], body["size_bytes"],
                               body["obj_key"], body["filename"], body.get("content_type","application/pdf"),
                               body.get("issued"), body.get("expiry"))
    return JsonResponse({"ok": True, "kind": dd.kind})


@require_POST
@csrf_exempt
def api_register_owner(request, token: str):
    body = _json(request)
    d = SupplierRegistrationDraft.objects.get(draft_token=token)
    add_draft_owner(d, body["name"], body.get("rc",""), Decimal(str(body.get("pct","0"))),
                    body.get("nin_hash",""), bool(body.get("is_pep",False)))
    return JsonResponse({"ok": True})


@require_POST
@csrf_exempt
def api_register_submit(request, token: str):
    d = SupplierRegistrationDraft.objects.get(draft_token=token)
    submit_draft(d)
    return JsonResponse({"ok": True, "status": d.status})


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


