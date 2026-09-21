"""Format validation for the identity numbers a vendor submits.

These are *format* checks, done immediately so a vendor learns at the form and
not three weeks into review. They are deliberately tolerant of the ways numbers
get mistyped (spaces, dashes, lower case) and strict about shape:

* **RC / BN / GT numbers** — CAC issues ``RC`` (companies), ``BN`` (business
  names) and ``GT`` (incorporated trustees) prefixes followed by digits.
* **TIN** — either the FIRS 8-digit taxpayer id with a 4-digit check suffix
  (``12345678-0001``) or the 10-digit TIN issued instantly by the JTB
  self-service portal. Both are accepted because vendors hold both in the wild.

Registry *existence* checks (is this RC real, does the name match) are the
reviewer's job against the CAC portal and are recorded as dated verifications
on the supplier profile — design doc section 3.2.
"""
from __future__ import annotations

import re

_RC_RE = re.compile(r"^(RC|BN|GT)\s?-?(\d{4,8})$", re.IGNORECASE)
_TIN_FIRS_RE = re.compile(r"^(\d{8})-?(\d{4})$")
_TIN_JTB_RE = re.compile(r"^\d{10}$")


def normalise_rc_number(raw: str) -> str:
    """``rc 1234567`` → ``RC1234567``. Returns the raw string if it fails."""
    m = _RC_RE.match(raw.strip())
    if not m:
        return raw.strip()
    return f"{m.group(1).upper()}{m.group(2)}"


def normalise_tin(raw: str) -> str:
    """``12345678 0001`` / ``123456780001`` → ``12345678-0001``; JTB 10-digit
    TINs pass through unchanged."""
    tin = raw.strip().replace(" ", "")
    m = _TIN_FIRS_RE.match(tin)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return tin


def rc_number_errors(rc: str) -> list[str]:
    if not rc or not rc.strip():
        return []  # absence is reported by the field-presence check
    if not _RC_RE.match(rc.strip()):
        return [
            "RC number must look like RC1234567 (or BN… / GT…) as printed on "
            "your CAC certificate"
        ]
    return []


def tin_errors(tin: str) -> list[str]:
    if not tin or not tin.strip():
        return []
    compact = tin.strip().replace(" ", "")
    if _TIN_JTB_RE.match(compact) or _TIN_FIRS_RE.match(compact):
        return []
    return [
        "TIN must be the 10-digit JTB TIN or the FIRS form 12345678-0001 "
        "(8 digits, dash, 4 digits) as printed on your tax documents"
    ]


def normalise_phone(raw: str) -> str:
    """Keep the digits, keep a leading +. ``0809 111 2222`` → ``08091112222``."""
    digits = re.sub(r"[^\d+]", "", raw.strip())
    return digits
