"""Template filters. Kept here (not in view code) so every page renders money and
dates identically — the last thing a procurement register can afford is two
screens disagreeing about a figure."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone
from django.utils.html import format_html

register = template.Library()


@register.filter
def naira(value) -> str:
    if value in (None, ""):
        return "—"
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if d == 0:
        return "₦0"
    if abs(d) >= Decimal("1000000000"):
        return f"₦{d / Decimal('1000000000'):.2f}bn"
    if abs(d) >= Decimal("1000000"):
        return f"₦{d / Decimal('1000000'):.1f}m"
    return f"₦{d:,.0f}"


@register.filter
def pct(value) -> str:
    try:
        return f"{Decimal(str(value)) * 100:.1f}%"
    except (InvalidOperation, ValueError, TypeError):
        return "—"


@register.filter
def shortdate(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return timezone.localtime(value).strftime("%d %b %Y") if getattr(value, "hour", None) is not None else value.strftime("%d %b %Y")


@register.filter
def deadline(value):
    """Honest countdown. Never a fake 'closing soon' — a deadline is a fact."""
    if not value:
        return format_html('<span class="muted">no deadline</span>')
    now = timezone.now()
    dt = value if value.tzinfo else timezone.make_aware(value)
    delta = dt - now
    days = delta.days + (delta.seconds // 86400)
    if days < 0:
        return format_html('<span class="tag closed">closed {}</span>', shortdate(dt))
    if days == 0:
        return format_html('<span class="tag frozen">CLOSES TODAY</span>')
    if days <= 3:
        return format_html('<span class="tag frozen">{} days left</span>', days)
    return format_html('<span class="tag open">{} days left</span>', days)


@register.filter
def status_tag(value) -> str:
    cls = {
        "PUBLISHED": "open",
        "CLARIFYING": "open",
        "CLOSED": "closed",
        "OPENED": "closed",
        "EVALUATING": "closed",
        "AWARDED": "ok",
        "CONTRACTED": "ok",
        "FROZEN": "frozen",
        "TERMINATED": "flag",
        "DRAFT": "closed",
    }.get(value, "closed")
    return format_html('<span class="tag {}">{}</span>', cls, value)


@register.filter
def hash_short(value) -> str:
    return f"{value[:12]}…" if value and len(value) > 12 else (value or "—")


@register.filter
def get_item(dictionary, key):
    """Dict lookup in templates: {{ mydict|get_item:key }}."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@register.filter
def total_weight(criteria_list):
    """Sum of criterion weights for display."""
    try:
        return sum(c.weight for c in criteria_list)
    except (TypeError, AttributeError):
        return 0


@register.filter
def filesizeformat(value):
    """Format bytes as human-readable file size."""
    try:
        b = int(value)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}" if unit != "B" else f"{b} {unit}"
        b /= 1024
    return f"{b:.1f} TB"
