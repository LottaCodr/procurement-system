"""Site identity and asset versioning.

Nothing here is hand-typed to look impressive: every figure rendered in a
template comes from a query, and the only constants are the state's own name,
its contact points and the retired-URL policy.
"""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from core import contacts, css_build


def platform(request):
    return {
        "PLATFORM_NAME": settings.PLATFORM_NAME,
        "PLATFORM_ABBREV": settings.PLATFORM_ABBREV,
        "STATE_NAME": settings.STATE_NAME,
        # Whether the telephone line and email are real, not just configured:
        # a page must never send a citizen to a number nobody answers.
        **contacts.contact_state(),
        "SUPPORT_HOURS": settings.SUPPORT_HOURS,
        "YEAR": timezone.now().year,
        # The single stylesheet is content-addressed, so a deploy invalidates
        # caches without anyone being asked to press "hard refresh".
        "ASSET_VERSION": css_build.read_manifest().get("source_hash") or "dev",
        "STYLESHEET": css_build.stylesheet_href(),
        # Public contact + help live in one place so SC 3.2.6 (consistent help)
        # cannot drift between pages.
        "HELP_ANCHOR": "/tenders/help/",
    }
