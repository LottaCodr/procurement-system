from django.conf import settings
from django.utils import timezone


def platform(request):
    """Site identity. Note that no figure here is hand-typed: the template pulls
    live metrics from /api/v1/stats, so a homepage claim cannot drift from data.
    """
    return {
        "PLATFORM_NAME": settings.PLATFORM_NAME,
        "STATE_NAME": settings.STATE_NAME,
        "CONTACT_PHONE": settings.CONTACT_PHONE,
        "CONTACT_EMAIL": settings.CONTACT_EMAIL,
        "SUPPORT_HOURS": settings.SUPPORT_HOURS,
        "YEAR": timezone.now().year,
    }
