"""Process bootstrap shared by manage.py, WSGI, ASGI, and the Vercel build.

Must not import Django. It runs *before* Django reads ``DJANGO_SETTINGS_MODULE``.

Why this exists
---------------
``os.environ.setdefault`` does not replace a variable that is already present,
and an empty string counts as present. Django then treats that blank value as
"settings are not configured" and raises ``ImproperlyConfigured`` on the first
setting it touches (``LOGGING_CONFIG``, inside ``django.setup()``).

That is exactly the Vercel build failure: the project environment (or a
copied dashboard variable) had ``DJANGO_SETTINGS_MODULE`` set but blank, the
build script called ``setdefault(..., "config.settings")``, and ``django.setup()``
still crashed. ``.env`` cannot save the build either — ``.vercelignore`` strips
it, so the builder never sees it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"
SETTINGS_MODULE = "config.settings"


def prepare() -> str:
    """Put ``apps/`` on the import path and refuse a blank settings module.

    A non-blank ``DJANGO_SETTINGS_MODULE`` is left alone. Vercel's collectstatic
    step sets it to a temporary shim (``_vercel_collectstatic_settings``);
    overwriting that would make ``collectstatic`` load the wrong module.
    """
    apps = str(APPS_DIR)
    if apps not in sys.path:
        sys.path.insert(0, apps)
    current = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if not current.strip():
        os.environ["DJANGO_SETTINGS_MODULE"] = SETTINGS_MODULE
    return os.environ["DJANGO_SETTINGS_MODULE"]
