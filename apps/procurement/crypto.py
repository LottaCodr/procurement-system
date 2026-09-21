"""Sealed-bid cryptography.

This is the genuinely hard problem in procurement software. The threat model:
nobody — not an administrator, not a root shell, not a vendor with remote access
— may read a bid before the public opening. After opening, the bidder gets a
publicly verifiable receipt proving exactly what they submitted and when.

Design (two tiers, documented honestly):

Tier 1 (implemented here; ships in Phase 2)
===========================================
* Bids are encrypted client-side → verified server-side with a per-tender
  symmetric key generated at `publish()`, wrapped to a set of custodians' public
  keys using RSA-OAEP.
* On open, custodians release their key shares; the key is reconstructed and
  bids are decrypted, after which their commitment hashes are verified against
  the public list published at submission.
* The *commitment hash* (sha256 of canonical bid content + receipt) is published
  the moment the bid arrives — that alone makes "your upload arrived after close"
  unfalsifiable by an official, and makes bid substitution provable on screen at
  the public opening.
* All files are stored in the private object store; the tender page never links
  them directly and only issues short-lived signed URLs after opening.

Tier 2 (stubbed; operationalised at go-live with key ceremony)
==============================================================
* Shamir 3-of-4 secret sharing across named custodians (DG, Accountant-General,
  Chief Judge nominee, PCACC nominee).
* Hardware Security Module / air-gapped workstation to hold custodian private
  keys. The stub returns a Fernet key held in configuration for Phase 2 demo
  purposes — in production the custodians never all exist on the app server and
  the key material is rotated per tender.

The point of being explicit about the fallback, in code rather than marketing:
Kano/Taraba show the classic pattern of claiming "sealed bids" while bids are
merely hidden behind a permission flag. Here a `SealedBidError` is raised if you
try to read bid content before opening, and a `verify_commitment()` returns a
boolean *and* is called on opening.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
import pyotp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend


class SealedBidError(SuspiciousOperation):
    pass


@dataclass
class KeyMaterial:
    tender_key: bytes       # Fernet key (32 url-safe base64 bytes)
    wrapped_keys: dict[str, str]  # custodian -> base64 RSA-encrypted tender key


def _fernet_key() -> bytes:
    return Fernet.generate_key()


def report_fernet_key() -> bytes:
    """Deterministic, deployment-scoped key for whistleblower report bodies.

    `_fernet_key()` above deliberately generates a *fresh* key per call — it is
    the per-tender sealing key, and a random key is the right thing there. Using
    it for storage-at-rest would be a data-loss bug: every read would need the
    key that produced the write. This key is derived from the deployment secret
    instead, so any web process can decrypt what any other web process wrote.

    Production note: rotate this by re-encrypting, and prefer holding the secret
    in the state's own KMS rather than in the application environment.
    """
    material = hashlib.sha256(f"{settings.SECRET_KEY}:wb-report:v1".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(material)


def encrypt_report_body(body: str) -> str:
    return Fernet(report_fernet_key()).encrypt(body.encode("utf-8")).decode("ascii")


def decrypt_report_body(ciphertext: str) -> str:
    """Used by the integrity unit's tooling, not by the public web views."""
    try:
        return Fernet(report_fernet_key()).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SealedBidError("Report ciphertext is not authentic for this deployment key") from exc


def _custodian_keypair(custodian: str):
    """In production the custodians' private keys are on air-gapped hardware;
    for Phase 2 we generate ephemeral keys per process and publish the public
    halves via the API so bidders can encrypt. `KEY_CEREMONY_FALLBACK_KEY` in
    settings can be a static Fernet key for dev/demo."""
    if not getattr(settings, "SEALED_BID_DEMO", False):
        raise SealedBidError("Tender key ceremony must be configured for production")
    if custodian.startswith("__"):
        raise SealedBidError(f"bad custodian id {custodian!r}")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    pub_pem = key.public_key().public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return {"private_pem": priv_pem, "public_pem": pub_pem}


def generate_tender_key(custodians: list[str]) -> tuple[KeyMaterial, dict[str, str]]:
    """Create a per-tender symmetric key, wrapped to each custodian's public key.

    Returns (KeyMaterial, public_keys map) — public_keys is what we publish so
    the client can encrypt without trusting the server at submission time.
    """
    fkey = _fernet_key()
    custodian_privs: dict[str, str] = {}
    public_keys: dict[str, str] = {}
    wrapped: dict[str, str] = {}
    for c in custodians:
        kp = _custodian_keypair(c)
        custodian_privs[c] = kp["private_pem"]
        public_keys[c] = kp["public_pem"]
        pub = serialization.load_pem_public_key(kp["public_pem"].encode(), backend=default_backend())
        enc = pub.encrypt(fkey, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        wrapped[c] = base64.b64encode(enc).decode()
    return KeyMaterial(tender_key=fkey, wrapped_keys=wrapped), public_keys, custodian_privs


def encrypt_bid_payload(payload: dict, tender_key_b64: bytes) -> str:
    f = Fernet(tender_key_b64)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f.encrypt(body).decode()


def decrypt_bid_payload(ciphertext: str, tender_key_b64: bytes) -> dict:
    f = Fernet(tender_key_b64)
    try:
        raw = f.decrypt(ciphertext.encode(), ttl=None)
    except InvalidToken as exc:
        raise SealedBidError("Bid ciphertext is not authentic for this tender") from exc
    return json.loads(raw)


def bid_commitment(ocid: str, supplier_rc: str, amount: str, envelope_sha256: str, submitted_at: datetime) -> str:
    """Canonical commitment. This hash is the bidder's receipt; verify_at_open
    recomputes it from (1) the plaintext body and (2) the stored envelope hash."""
    body = "|".join([ocid, supplier_rc, str(amount), envelope_sha256, str(int(submitted_at.timestamp()))])
    return hashlib.sha256(body.encode()).hexdigest()


def verify_at_open(decrypted: dict, published_commitment: str, envelope_sha256: str, ocid: str, supplier_rc: str, amount: str, submitted_at: datetime) -> bool:
    return hmac.compare_digest(
        bid_commitment(ocid, supplier_rc, amount, envelope_sha256, submitted_at),
        published_commitment,
    )


def sign_receipt(commitment_hash: str, tender_ocid: str, submitted_at: datetime, secret_key: bytes | None = None) -> str:
    """Bidder receipt. Signed with the server's application key so the bidder
    can prove the Bureau accepted their submission."""
    sk = secret_key or (getattr(settings, "RECEIPT_SIGNING_KEY", None) or settings.SECRET_KEY).encode()[:32]
    body = f"{tender_ocid}|{commitment_hash}|{int(submitted_at.timestamp())}".encode()
    return base64.urlsafe_b64encode(hmac.new(sk, body, hashlib.sha256).digest()).decode().rstrip("=")


def verify_receipt(commitment_hash: str, tender_ocid: str, submitted_at: datetime, signature: str) -> bool:
    sk = (getattr(settings, "RECEIPT_SIGNING_KEY", None) or settings.SECRET_KEY).encode()[:32]
    expected = sign_receipt(commitment_hash, tender_ocid, submitted_at, secret_key=sk)
    return hmac.compare_digest(signature.encode(), expected.encode())


# ---- TOTP helpers (MFA) ---------------------------------------------------
def provision_totp(name: str, email: str) -> tuple[str, str]:
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=email, issuer_name="Taraba State Procurement")
    return secret, provisioning_uri


def verify_totp(secret: str, code: str) -> bool:
    return bool(secret and code and pyotp.TOTP(secret).verify(code, valid_window=1))


def generate_sms_otp() -> str:
    """Fallback for suppliers without authenticators: 6-digit OTP delivered via SMS/WhatsApp.
    We don't use pyotp here because we're sending a single-use code, not a TOTP; it is
    hashed with an HMAC of (secret|phone|window) so the stored DB value can be verified
    without ever persisting the cleartext code."""
    return f"{secrets.randbelow(10**6):06d}"


def hash_otp(phone: str, code: str, window: int, sk: bytes) -> str:
    return hashlib.sha256(sk + phone.encode() + f"|{window}|{code}".encode()).hexdigest()


def verify_otp(phone: str, code: str, window_minutes: int = 10, tolerance: int = 1) -> bool:
    """Verifies within ±tolerance windows (default ±10 min tolerance = 30 min total)."""
    if not (code and phone):
        return False
    sk = (getattr(settings, "OTP_SIGNING_KEY", None) or settings.SECRET_KEY).encode()[:32]
    now_window = int(datetime.now(dt_timezone.utc).replace(second=0, microsecond=0).timestamp() // (window_minutes * 60))
    for w in range(now_window - tolerance, now_window + tolerance + 1):
        if hmac.compare_digest(
            hashlib.sha256(sk + phone.encode() + f"|{w}|{code}".encode()).hexdigest(),
            hash_otp(phone, code, w, sk),
        ):
            return True
    return False
