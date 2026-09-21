"""Whether a published contact point is real, in one place.

The most common lie on a government website is a contact that nobody ever
tested: a telephone number that rings nowhere, an address that bounces. The
platform therefore refuses to *present* a placeholder as a working channel — if
the Bureau's line is not yet published, the page says so and routes the reader
to a route that does exist (the help page, the assisted desks, email).

Detection lives here rather than in each template so that the interface, the
service-status page and the release checklist all agree on what "not yet
published" means.
"""
from __future__ import annotations

from django.conf import settings

#: Patterns that mark a value as *not a real contact point*. Written as
#: substrings, matched case-insensitively and with spaces normalised.
PLACEHOLDER_MARKERS = (
    "xxx",
    "tbd",
    "todo",
    "placeholder",
    "example.com",
    "example.org",
    "n/a",
    "000 0000",
    "0000000",
    "1234567",
    "555 01",
)

#: A contact must look like a contact: a phone number with enough digits, or an
#: address with a dot in the domain.
MIN_PHONE_DIGITS = 7


def is_placeholder(value: str | None) -> bool:
    """True when a configured value is a stand-in rather than a real channel."""
    if not value:
        return True
    text = " ".join(str(value).split()).lower()
    return any(marker in text for marker in PLACEHOLDER_MARKERS)


def is_real_phone(value: str | None) -> bool:
    if is_placeholder(value):
        return False
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return len(digits) >= MIN_PHONE_DIGITS


def is_real_email(value: str | None) -> bool:
    if is_placeholder(value):
        return False
    text = str(value).strip()
    return "@" in text and "." in text.split("@")[-1]


def contact_state() -> dict:
    """The contact points, plus whether each one can honestly be published.

    Templates use the booleans to choose between a link and a plain statement;
    nothing in the interface has to re-implement this judgement.
    """
    phone = getattr(settings, "CONTACT_PHONE", "")
    email = getattr(settings, "CONTACT_EMAIL", "")
    phone_real, email_real = is_real_phone(phone), is_real_email(email)
    return {
        "CONTACT_PHONE": phone,
        "CONTACT_PHONE_REAL": phone_real,
        "CONTACT_EMAIL": email,
        "CONTACT_EMAIL_REAL": email_real,
        # A single flag for "no channel has been confirmed yet", which the status
        # page publishes as a known gap rather than hiding it.
        "CONTACT_ANY_REAL": phone_real or email_real,
    }
