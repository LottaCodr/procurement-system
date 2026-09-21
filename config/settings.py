"""Django settings for the Taraba State e-Procurement Platform.

Design notes that matter to reviewers
-------------------------------------
* Postgres is the production target; SQLite is permitted only so that the
  invariants and the ledger can be exercised in CI/dev without a database
  server. Backend-specific hardening (INSERT-only grants, CHECK constraints
  that SQLite cannot express) lives in explicit SQL migrations and is
  re-tested against Postgres in CI (see tests/test_invariants.py).
* Nothing in this file may reference a third-party hosted runtime asset.
  Kano's platform shipped with `@tailwindcss/browser@4` from a public CDN and
  fonts fetched at request time; that is a supply-chain and availability bug.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APPS_DIR = BASE_DIR / "apps"


def env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-only-do-not-ship")
DEBUG = env_flag("DJANGO_DEBUG")
# "testserver" is Django's test client host; omitting it turns every request test
# into a 400 that looks like an application bug. Real deployments override the env var.
ALLOWED_HOSTS = [
    h
    for h in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
    ).split(",")
    if h
]
# In DEBUG the host header is not a security boundary worth breaking the demo
# over: previews, LAN demos and container hostnames all arrive as unknown
# hosts, and the failure mode (a bare 400 "Bad Request" with no explanation) is
# indistinguishable from an application bug. Production is unaffected: DEBUG is
# 0 there and DJANGO_ALLOWED_HOSTS is mandatory.
if DEBUG and not os.environ.get("DJANGO_ALLOWED_HOSTS"):
    ALLOWED_HOSTS = ["*"]

# ---------------------------------------------------------------- applications
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "ledger",
    "procurement",
    "core",
]

MIDDLEWARE = [
    # GZip first: text compresses ~5:1 and the audience is on metered mobile
    # data. It costs one CPU pass per response to save real naira per visit.
    "django.middleware.gzip.GZipMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "core.middleware.RedirectMapMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # CSRF protection is on for every template view. The JSON API keeps its own
    # DRF authentication story, and DRF's APIView is csrf_exempt by design.
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.AuditContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project-level templates first, then the core app's. The whistleblower
        # intake template lived in `templates/` while DIRS pointed only at the app
        # directory, so the page 500'd on every visit.
        "DIRS": [BASE_DIR / "templates", APPS_DIR / "core" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "core.context_processors.platform",
            ],
        },
    }
]

# ---------------------------------------------------------------------- data
DATABASES = {
    "default": (
        {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("PGDATABASE", "taraba_procure"),
            "USER": os.environ.get("PGUSER", "taraba_app"),
            "PASSWORD": os.environ.get("PGPASSWORD", ""),
            "HOST": os.environ.get("PGHOST", "127.0.0.1"),
            "PORT": os.environ.get("PGPORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
        if env_flag("USE_POSTGRES")
        else {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/tenders/"
LOGOUT_REDIRECT_URL = "/"
AUTH_USER_MODEL = "procurement.User"

# ------------------------------------------------------------------- locale
# Deadlines are the core of this domain, and an hour of confusion at a bid
# deadline is not a cosmetic bug: Nigerian bidders read WAT (UTC+1). Store UTC,
# render Africa/Lagos, and label the timezone in the UI.
LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------------- security
# The Kano audit found: no HSTS, CSP reduced to `upgrade-insecure-requests`,
# `x-powered-by` disclosure, MFA absent. Each has a concrete countermeasure here.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"

if env_flag("TARABA_HTTPS", "1" if not DEBUG else "0"):
    SECURE_HSTS_SECONDS = 63072000  # 2 years
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Content-Security-Policy. Enforced, not report-only, from day one: a stored
# XSS in a tender notice is a bid-tampering vulnerability, not a nuisance.
CSP_POLICY = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'"],
    "connect-src": ["'self'"],
    "frame-ancestors": ["'none'"],
    "form-action": ["'self'"],
    "base-uri": ["'self'"],
    "object-src": ["'none'"],
}
REPORT_ONLY = env_flag("CSP_REPORT_ONLY")
COLLECTORS_ENDPOINT = os.environ.get("COLLECTORS_ENDPOINT", "")  # report-only sink

# MFA is mandatory for every internal (buyer/oversight) role. Suppliers may use
# SMS OTP because smartphone ownership is not universal in the state.
MFA_REQUIRED_ROLES = {"ADMIN", "DG", "PDE", "EVALUATOR", "APPROVER", "TREASURY", "AUDITOR", "APPEALS"}

# --------------------------------------------------------------------- REST API
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "procurement.api.errors.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Taraba State Public Procurement API",
    "DESCRIPTION": (
        "Open, unauthenticated read access to tender, award and contract data, "
        "published as Open Contracting Data Standard (OCDS) releases. No key, no "
        "quota, no permission required."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ALLOWED_SERVERS": [{"": "/"}],
}

# ------------------------------------------------------------------ proc domain
# Thresholds are DATA, not code: the state's own law may differ from the federal
# BPP revision, and figures change between fiscal years. `seed_demo` loads these
# defaults so a reviewer can see the shape; Phase 0 must replace them with the
# Taraba State Public Procurement Law text.
PLATFORM_NAME = os.environ.get("PLATFORM_NAME", "Taraba State Procurement Transparency Portal")
PLATFORM_ABBREV = os.environ.get("PLATFORM_ABBREV", "TAR")
STATE_NAME = "Taraba State"
CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "+234 800 000 0000")  # CI asserts: no placeholders
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "bpp@tr.gov.ng")
SUPPORT_HOURS = "Mon-Fri 08:00-16:00 WAT"

# Every retired URL must have a permanent successor. This map is asserted in CI
# (tests/test_links.py) — it is the direct countermeasure to Kano deleting its
# entire award/OCDS corpus when the platform changed.
LEGACY_REDIRECT_MAP = {
    "/disclosure": "/awards/",
    "/register": "/vendor/register/",
    "/login": "/accounts/login/",
    "/forgot-password": "/accounts/password-reset/",
    "/tenders": "/tenders/",
    "/disclosure/ocds.json": "/api/v1/releases",
    "/ocds.json": "/api/v1/releases",
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [APPS_DIR / "core" / "static"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"key": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "key"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

# ---------------------------------------------------------------- Phases 2-5: workflow
INSTALLED_APPS += ["workflow"]
SEALED_BID_DEMO = True   # set False in production once the key ceremony is rehearsed
OTP_SIGNING_KEY = os.environ.get("OTP_SIGNING_KEY", SECRET_KEY)
RECEIPT_SIGNING_KEY = os.environ.get("RECEIPT_SIGNING_KEY", SECRET_KEY)
SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "console")   # console | africastalking | termii
SMS_SENDER_ID = os.environ.get("SMS_SENDER_ID", "TAR-BPP")
WHATSAPP_PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "none")
