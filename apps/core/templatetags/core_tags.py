"""Template filters and tags.

Kept in one place so that money, dates, statuses and hashes are rendered
identically everywhere. A procurement register that prints "₦1.2m" on one page
and "1200000" on the next is a register whose numbers people stop trusting.

Three rules are encoded here rather than left to whoever writes the next
template:

1.  **Money is never shown approximately without the exact figure attached.**
    `naira` renders a compact form for a table cell *and* carries the full
    amount in a `title`/`<data>` pair, so the visual shorthand cannot become a
    substitute for the number the state actually spent.
2.  **Timestamps print the clock of the state that published them**, labelled
    (Africa/Lagos, WAT). A bid deadline quoted in the wrong timezone is not a
    formatting bug — it is a missed bid.
3.  **Status is never colour alone.** Every pill, bar and flag prints the word
    as well, so the meaning survives a monochrome print-out, a screen reader
    and a reader who cannot distinguish red from green (WCAG 1.4.1).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

#: Timezone label printed next to every timestamp. Derived from settings at
#: import time, so a deployment in another state cannot silently mislabel its
#: own deadlines.
try:  # pragma: no cover - trivial import guard
    from django.conf import settings as _settings

    TZ_LABEL = "WAT" if "Lagos" in _settings.TIME_ZONE else _settings.TIME_ZONE
except Exception:  # pragma: no cover
    TZ_LABEL = "WAT"

_NGN = "₦"


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _grouped(d: Decimal) -> str:
    """Naira with thousands separators, no decimals when there are none."""
    if d == d.to_integral_value():
        return f"{_NGN}{d:,.0f}"
    return f"{_NGN}{d:,.2f}"


# ---------------------------------------------------------------------- money
@register.filter
def naira(value) -> str:
    """Compact Naira for tables, with the exact figure attached for hover/AT."""
    d = _to_decimal(value)
    if d is None:
        return "—"
    exact = _grouped(d)
    if d == 0:
        return format_html('<data class="money" value="0">{}</data>', exact)
    sign = "−" if d < 0 else ""
    a = abs(d)
    if a >= Decimal("1000000000000"):
        short = f"{sign}{_NGN}{a / Decimal('1000000000000'):.1f}tn"
    elif a >= Decimal("1000000000"):
        short = f"{sign}{_NGN}{a / Decimal('1000000000'):.2f}bn"
    elif a >= Decimal("1000000"):
        short = f"{sign}{_NGN}{a / Decimal('1000000'):.1f}m"
    elif a >= Decimal("10000"):
        short = f"{sign}{_NGN}{a / Decimal('1000'):.0f}k"
    else:
        short = _grouped(d)
    return format_html(
        '<data class="money" value="{}" title="{}">{}</data>', str(d), exact, short
    )


@register.filter
def naira_exact(value) -> str:
    d = _to_decimal(value)
    if d is None:
        return "—"
    return format_html('<data class="money money--exact" value="{}">{}</data>', str(d), _grouped(d))


@register.filter
def pct(value) -> str:
    """One decimal place only where it says something (12% not 12.0%)."""
    d = _to_decimal(value)
    if d is None:
        return "—"
    if d == d.to_integral_value():
        return f"{d:.0f}%"
    return f"{d:.1f}%"


@register.filter
def width_class(value) -> str:
    """Quantise a percentage into one of the `.w-*` bar classes.

    Bars are drawn with a fixed class palette rather than an inline style: the
    Content-Security-Policy forbids inline styles, and a data-derived inline
    style is exactly the injection surface that policy exists to close.
    """
    d = _to_decimal(value)
    if d is None:
        return "w-0"
    p = max(0.0, min(100.0, float(d)))
    return f"w-{int(round(p / 5.0) * 5)}"


# ----------------------------------------------------------------------- time
def _as_dt(value):
    """Normalise datetimes, dates and ISO strings to an aware datetime.

    `PartyVerification.expires_at` and `ThresholdRule`-driven expiry dates are
    plain `date` objects; `timezone.is_naive()` raises on those, which used to
    turn any page showing an expiry date into a 500.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.get_current_timezone())
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


@register.filter
def shortdate(value) -> str:
    dt = _as_dt(value)
    return timezone.localtime(dt).strftime("%d %b %Y") if dt else "—"


@register.filter
def datetime_wat(value) -> str:
    """Full timestamp with the timezone named — used for deadlines and openings."""
    dt = _as_dt(value)
    if not dt:
        return "—"
    return format_html(
        '{} <span class="visually-hidden">West Africa Time</span>'
        '<abbr title="West Africa Time (UTC+1)" class="tz">{}</abbr>',
        timezone.localtime(dt).strftime("%d %b %Y, %H:%M"),
        TZ_LABEL,
    )


@register.filter
def deadline(value):
    """An honest countdown. Never a manufactured "closing soon".

    The countdown stops being useful at the hour scale a bidder actually cares
    about, so under an hour it says so instead of printing "0 days left".
    """
    dt = _as_dt(value)
    if not dt:
        # No interpolated data: mark_safe on a literal, so this does not raise
        # Django 6's deprecation for argument-free format_html.
        return mark_safe('<span class="tag tag--closed">no deadline set</span>')
    delta = dt - timezone.now()
    secs = delta.total_seconds()
    if secs < 0:
        return format_html(
            '<span class="tag tag--closed">closed {} {}</span>', shortdate(dt), TZ_LABEL
        )
    if secs < 3600:
        mins = max(int(secs // 60), 1)
        return format_html(
            '<span class="tag tag--warn">closes in {} min</span>', mins
        )
    if secs < 86400:
        hours = int(secs // 3600)
        return format_html(
            '<span class="tag tag--warn">closes in {} hour{}</span>',
            hours,
            "" if hours == 1 else "s",
        )
    days = int(secs // 86400)
    if days <= 3:
        return format_html('<span class="tag tag--warn">{} days left</span>', days)
    return format_html('<span class="tag tag--open">{} days left</span>', days)


@register.filter
def relative(value) -> str:
    dt = _as_dt(value)
    if not dt:
        return "—"
    delta = timezone.now() - dt
    secs = delta.total_seconds()
    if secs < 0:
        return shortdate(dt)
    if secs < 3600:
        return f"{max(int(secs // 60), 1)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    if delta.days == 1:
        return "yesterday"
    if delta.days < 30:
        return f"{delta.days} days ago"
    return shortdate(dt)


# ------------------------------------------------------------------- statuses
_STATUS_CLASS = {
    "PUBLISHED": "tag--open",
    "CLARIFYING": "tag--open",
    "CLOSED": "tag--closed",
    "OPENED": "tag--closed",
    "EVALUATING": "tag--closed",
    "AWARDED": "tag--awarded",
    "CONTRACTED": "tag--awarded",
    "FROZEN": "tag--warn",
    "TERMINATED": "tag--flag",
    "CANCELLED": "tag--flag",
    "DRAFT": "tag--closed",
    "UNSEALED": "tag--awarded",
    "RESPONSIVE": "tag--open",
    "EVALUATED": "tag--awarded",
    "RECOMMENDED": "tag--awarded",
    "REJECTED": "tag--flag",
    "WITHDRAWN": "tag--closed",
    "SUBMITTED": "tag--closed",
    "RECEIVED": "tag--closed",
    "VERIFYING": "tag--warn",
    "PASSED": "tag--open",
    "FAILED": "tag--flag",
    "EXPIRED": "tag--flag",
    "APPROVED": "tag--open",
    "PROPOSED": "tag--warn",
    "RESOLVED": "tag--open",
    "VERIFIED": "tag--open",
    "IN_PROGRESS": "tag--warn",
    "DISMISSED": "tag--closed",
    "UPHELD": "tag--flag",
    "PENDING": "tag--warn",
    "SIGNED": "tag--awarded",
    "ACTIVE": "tag--open",
    "SUSPENDED": "tag--warn",
    "PAID": "tag--open",
    "PLANNED": "tag--closed",
    "DELIVERED": "tag--closed",
    "ACCEPTED": "tag--open",
    "OVERDUE": "tag--flag",
    "OPEN": "tag--warn",
    "RECEIVED_PENDING": "tag--closed",
}


@register.filter
def status_tag(value) -> str:
    """A status pill: the colour *and* the status word itself."""
    if value is None or value == "":
        return "—"
    code = str(value).upper()
    label = code.replace("_", " ").capitalize()
    cls = _STATUS_CLASS.get(code, "tag--closed")
    return format_html('<span class="tag {}">{}</span>', cls, label)


@register.filter
def severity_tag(value) -> str:
    """Risk severity. The word carries the meaning; the colour reinforces it."""
    code = str(value or "").upper()
    cls = {"HIGH": "tag--flag", "MEDIUM": "tag--warn", "LOW": "tag--closed"}.get(code, "tag--closed")
    return format_html('<span class="tag {}">{} severity</span>', cls, code.capitalize() or "Unknown")


# --------------------------------------------------------------- identifiers
@register.filter
def hash_short(value) -> str:
    """Short hash for the eye. The full value stays in the DOM for copying, so
    truncation is a display choice and never a loss of the verifiable value."""
    if not value:
        return "—"
    s = str(value)
    if len(s) <= 24:
        return format_html('<code class="hash" title="{}">{}</code>', s, s)
    return format_html('<code class="hash" title="{}">{}…{}</code>', s, s[:12], s[-6:])


@register.filter
def mask_tax(value) -> str:
    """Mask a tax identifier: the register exists to audit spending, not to
    publish identifiers that can be used to impersonate a business."""
    if not value:
        return "—"
    s = str(value)
    if len(s) <= 4:
        return "•" * len(s)
    return f"{'•' * (len(s) - 4)}{s[-4:]}"


# ------------------------------------------------------------------ utilities
@register.filter
def get_item(dictionary, key):
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@register.filter
def total_weight(criteria_list):
    try:
        return sum(c.weight for c in criteria_list)
    except (TypeError, AttributeError):
        return 0


@register.filter
def bytesize(value) -> str:
    """File size for people. Named `bytesize` so it does not shadow Django's own
    `filesizeformat` and quietly change another app's output."""
    try:
        b = float(value)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(b) < 1024:
            return f"{int(b)} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


@register.filter
def yesno_plain(value, words="Yes,No"):
    yes, no = (words.split(",") + ["Yes", "No"])[:2]
    return yes if value else no


@register.filter
def percent_of(part, whole):
    """Percentage of a whole, as a number (no sign) — for `widthratio`-free maths."""
    p, w = _to_decimal(part), _to_decimal(whole)
    if not p or not w:
        return None
    return (p / w) * Decimal("100")


@register.simple_tag
def bar(value, modifier="") -> str:
    """A share bar. The numeric value is always printed next to it in the caller,
    so the bar is decoration over a datum, never the datum itself."""
    return format_html(
        '<span class="bar {}" aria-hidden="true"><span class="bar__fill {}"></span></span>',
        modifier,
        width_class(value),
    )


@register.simple_tag(takes_context=True)
def nav_current(context, prefix: str, exact: bool = False) -> str:
    """Mark the current section in the primary navigation.

    Exact matching for short paths ("/tenders/") and prefix matching for
    sections, so "/tenders/awards/" highlights Awards rather than Tenders.
    """
    # The 404/500 handlers render without a RequestContext, and a missing
    # request must not turn an error page into a second error.
    request = context.get("request")
    if request is None:
        return ""
    path = request.path
    hit = path == prefix if exact else (path == prefix or path.startswith(prefix))
    return mark_safe('aria-current="page"') if hit else ""


@register.simple_tag(takes_context=True)
def with_filters(context, **kwargs) -> str:
    """Build a query string from the current request, overriding the given keys.

    Same contract as Django's ``{% querystring %}`` but tolerant of a missing
    request (error pages) and of `None` meaning *remove this key*.
    """
    from urllib.parse import urlencode

    request = context.get("request")
    params = {}
    if request is not None:
        params = {k: v for k, v in request.GET.items() if k != "page"}
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return urlencode(params)


# --------------------------------------------------------------------------
# Ledger payloads
# --------------------------------------------------------------------------
# The ledger stores a JSON payload per event. Rendering it straight into the
# page produces `{'version': 1, 'kind': 'BOQ', ...}` — readable by a programmer
# and by nobody else. These labels and formatters print the same facts in the
# language the rest of the site uses.

_PAYLOAD_LABELS = {
    "ocid": "OCID",
    "sha256": "SHA-256",
    "pdf_sha256": "Document SHA-256",
    "min_advert_days": "Minimum advert period",
    "estimate_disclosed": "Estimate published",
    "extends_deadline": "Extends the deadline",
    "verified_against_published_commitments": "Checked against published commitments",
    "receipt": "Receipt",
    "members": "Committee",
    "roles": "Roles",
    "reason": "Reason",
    "question": "Question",
    "answer": "Answer",
    "title": "Title",
    "amount": "Amount",
    "value": "Value",
    "est_value": "Estimate",
    "amount_change": "Variation",
    "treasury_ref": "Treasury reference",
    "notice_ref": "Notice reference",
    "non_lowest_reason": "Why not the lowest bid",
    "treasury": "Treasury reference",
}

_MONEY_KEYS = {
    "amount", "value", "est_value", "amount_change", "awarded", "estimate",
    "total", "price", "unit_price", "value_change",
}

_QUIET_KEYS = {"id", "pk", "seq", "index"}


def _payload_label(key: str) -> str:
    if key in _PAYLOAD_LABELS:
        return _PAYLOAD_LABELS[key]
    words = key.replace("_id", "").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _payload_value(key: str, value):
    import re as _re
    from decimal import Decimal, InvalidOperation

    if isinstance(value, bool):
        return format_html(
            '<span class="{cls}">{word}</span>',
            cls="tag tag--open" if value else "tag tag--closed",
            word="Yes" if value else "No",
        )
    if isinstance(value, (int, float, Decimal)) and key in _MONEY_KEYS:
        return naira(Decimal(str(value)))
    if isinstance(value, (int, float, Decimal)):
        return format_html("{}", f"{value:,}" if isinstance(value, int) else value)
    if isinstance(value, (list, tuple)):
        if not value:
            return "none"
        if all(isinstance(item, dict) for item in value):
            parts = [
                str(item.get("role") or item.get("name") or item.get("label") or "member")
                for item in value
            ]
            return format_html("{} <span class=\"muted\">({} member{})</span>", ", ".join(parts),
                               len(parts), "" if len(parts) == 1 else "s")
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{_payload_label(k).lower()}: {v}" for k, v in value.items())
    text = str(value)
    # Values that arrived from JSON as strings still deserve the site's own
    # formatting: an estimate is money, a timestamp is a West Africa Time
    # instant, and neither should reach the reader as a raw literal.
    if key in _MONEY_KEYS:
        try:
            return naira(Decimal(text.replace(",", "")))
        except (InvalidOperation, ValueError):
            pass
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:\+00:00|Z)?", text):
        return datetime_wat(text)
    # A hash is verifiable, not readable: shorten it but keep the full value in
    # the DOM so it can be copied and compared.
    if key.endswith("sha256") or (len(text) == 64 and all(c in "0123456789abcdef" for c in text)):
        return hash_short(text)
    if len(text) > 320:
        return text[:317] + "…"
    return text


@register.filter
def ledger_payload(value) -> str:
    """Render a ledger event payload as labelled facts, not as a Python repr."""
    if not value:
        return mark_safe('<span class="muted">no recorded detail</span>')
    if not isinstance(value, dict):
        return format_html("{}", str(value)[:320])
    rows = []
    for key, item in value.items():
        if key in _QUIET_KEYS or item in ("", None):
            continue
        rows.append(
            format_html(
                '<span class="payload__row"><span class="payload__key">{}</span> '
                '<span class="payload__value">{}</span></span>',
                _payload_label(key), _payload_value(key, item),
            )
        )
    if not rows:
        return mark_safe('<span class="muted">no recorded detail</span>')
    return mark_safe('<span class="payload">' + "".join(rows) + "</span>")
