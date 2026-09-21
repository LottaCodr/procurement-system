"""Security headers, the permanent-URL promise, and audit attribution.

Three Kano failures are closed here mechanically: absent HSTS/CSP, framework
fingerprinting (`x-powered-by`), and a wholesale URL-space change that orphaned
every published document.
"""
from __future__ import annotations

import re
import time

from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.utils.deprecation import MiddlewareMixin

_PLACEHOLDER_PATTERNS = re.compile(r"(XXX|xxxx|TBD|TODO|FIXME|000 0000|placeholder|lorem)", re.I)
_BODY_SIZE_BUDGET_KB = 60  # design target: a tender list page under 60KB


class SecurityHeadersMiddleware(MiddlewareMixin):
    """CSP + HSTS + fingerprint suppression, report-only first if asked."""

    def process_response(self, request, response):
        # No framework disclosure: `x-powered-by: PHP/8.2.33` is a free
        # version-enumeration gift to an attacker.
        for hdr in ("X-Powered-By", "Server", "X-AspNet-Version"):
            response.headers.pop(hdr, None)

        policy = getattr(settings, "CSP_POLICY", {}) or {}
        directive = "; ".join(f"{k} {' '.join(v)}" for k, v in policy.items())
        if directive:
            header = "Content-Security-Policy-Report-Only" if getattr(settings, "REPORT_ONLY", False) else "Content-Security-Policy"
            if getattr(settings, "COLLECTORS_ENDPOINT", ""):
                directive += f"; report-uri {settings.COLLECTORS_ENDPOINT}"
            response.headers[header] = directive
        response.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=(), payment=()")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Public read-only data should be cacheable by CDNs; anything else no-store.
        if request.path.startswith(("/tenders", "/api/v1", "/status", "/metrics.js")):
            response.headers.setdefault("Cache-Control", "public, max-age=60, stale-while-revalidate=300")
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class RedirectMapMiddleware(MiddlewareMixin):
    """The permanent-URL promise.

    When the platform changes, old URLs 301 to their successors; they do not
    404. The Kano platform replaced a WordPress ministry site that published
    quarterly OCDS reports, award lists and the state procurement law, and every
    one of those URLs became a 404 — a documented transparency record was
    destroyed by a re-platforming. `tests/test_links.py` asserts every mapped
    target resolves, so the promise cannot rot silently.
    """

    def process_response(self, request, response):
        if response.status_code == 404:
            target = settings.LEGACY_REDIRECT_MAP.get(request.path) or settings.LEGACY_REDIRECT_MAP.get(request.path.rstrip("/") + "/")
            if target:
                return HttpResponsePermanentRedirect(target)
        return response


class AuditContextMiddleware(MiddlewareMixin):
    """Attach request timing + actor so ledger events carry provenance, and warn
    (loudly, in a header) when a page ships placeholder content or busts size."""

    def process_request(self, request):
        request._t0 = time.monotonic()
        request.audit_actor = getattr(getattr(request, "user", None), "username", None) or "anonymous"

    def process_response(self, request, response):
        ms = round((time.monotonic() - getattr(request, "_t0", time.monotonic())) * 1000, 1)
        response.headers["X-Response-Ms"] = str(ms)

        if getattr(settings, "DEBUG", False) and request.path.startswith(("/tenders", "/awards", "/")):
            body = getattr(response, "content", b"") or b""
            if len(body) / 1024 > _BODY_SIZE_BUDGET_KB:
                response.headers["X-Perf-Warning"] = f"body {len(body) // 1024}KB exceeds {_BODY_SIZE_BUDGET_KB}KB budget"
            if _PLACEHOLDER_PATTERNS.search(body.decode("utf-8", "ignore")[:20000]):
                response.headers["X-Content-Warning"] = "placeholder text detected (XXX/TBD/TODO) — release checklist must fail"
        return response
