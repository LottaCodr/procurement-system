"""Whistleblower intake — anonymous reports of procurement corruption.

Three deliberate properties, and one honest limitation that the page states in
plain words:

* **No account, no login, no CAPTCHA.** Requiring an account to report the
  misuse of public money is a filter that only keeps honest people out.
* **Encrypted before it is written down.** The body is encrypted with a
  Fernet key derived from the deployment secret and stored as ciphertext with a
  SHA-256 of the ciphertext for integrity. Investigators decrypt with a key that
  lives outside the web role.
* **A case reference the reporter keeps.** `WB-XXXXXXXX`, with a status lookup
  so a reporter can see movement without identifying themselves.
* **The limitation, stated plainly:** this is not a Tor-grade anonymous
  channel. The submission carries ordinary server logs. Anyone whose safety
  depends on untraceability should use the physical drop box at the Bureau, and
  the page says so rather than implying a guarantee it cannot make.
"""
from __future__ import annotations

import logging

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from procurement.crypto import encrypt_report_body
from workflow.models import WhistleblowerReport

logger = logging.getLogger(__name__)

MIN_BODY = 40
MAX_BODY = 8000


@require_http_methods(["GET", "POST"])
def whistleblower_intake(request):
    """The intake form. Renders its own errors; never loses a typed report."""
    context = {"body": "", "tender_ocid": "", "contact": "", "errors": {}, "case": None}

    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()
        tender_ocid = (request.POST.get("tender_ocid") or "").strip()
        contact = (request.POST.get("contact") or "").strip()
        context.update({"body": body, "tender_ocid": tender_ocid, "contact": contact})

        errors = {}
        if not body:
            errors["body"] = "Enter what you want to report."
        elif len(body) < MIN_BODY:
            errors["body"] = f"Add a little more detail — at least {MIN_BODY} characters."
        elif len(body) > MAX_BODY:
            errors["body"] = f"Shorten the report to {MAX_BODY} characters or fewer."

        if not errors:
            report = WhistleblowerReport.objects.create(
                body_ciphertext=encrypt_report_body(body),
                contact_hash=_contact_hash(contact) if contact else "",
                tender_ocid=tender_ocid[:64],
            )
            # Log the reference only: the body is never written to a log line.
            logger.info("whistleblower report received: %s", report.ref)
            context.update({"case": report, "body": "", "tender_ocid": "", "contact": ""})
        else:
            context["errors"] = errors

    return render(request, "whistleblower_intake.html", context)


def _contact_hash(contact: str) -> str:
    import hashlib

    return hashlib.sha256(contact.strip().lower().encode("utf-8")).hexdigest()[:64]


@require_GET
def whistleblower_status(request, reference: str):
    """Status lookup by case reference.

    A browser gets a page that states the status; `?format=json` gets the same
    facts as JSON for the API surface. Either way the response carries the
    status and nothing else — never the report body, never the contact hash.
    """
    report = (
        WhistleblowerReport.objects.filter(ref=reference.upper())
        .only("ref", "status", "submitted_at")
        .first()
    )
    payload = {
        "reference": reference.upper(),
        "found": report is not None,
        "status": report.get_status_display() if report else None,
        "received_at": report.submitted_at.isoformat() if report else None,
        "checked_at": timezone.now().isoformat(),
    }
    if request.GET.get("format") == "json" or request.headers.get("Accept", "").startswith("application/json"):
        if report is None:
            return JsonResponse({"error": "not_found", "detail": "No case with that reference."}, status=404)
        return JsonResponse(payload)

    return render(
        request,
        "whistleblower_status.html",
        {
            "report": report,
            "reference": reference.upper(),
            "checked_at": timezone.now(),
        },
        status=200 if report else 404,
    )


@require_GET
def whistleblower_status_lookup(request):
    """Turn `?ref=WB-…` into the canonical, permanent status URL.

    The form needs somewhere to post to that does not already contain a
    reference, and a redirect keeps one canonical URL per case — so a reference
    forwarded in a chat resolves for whoever opens it.
    """
    ref = (request.GET.get("ref") or "").strip().upper()
    if not ref:
        return render(request, "whistleblower_status.html", {"reference": "", "checked_at": timezone.now()})
    return redirect("whistleblower-status", reference=ref)
