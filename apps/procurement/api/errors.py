from __future__ import annotations

from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """Errors are machine-readable and never leak internals.

    Note what is *not* here: no stack, no SQL, no settings. Kano's health page was
    the stock Laravel `up` view exposing framework internals on a public URL.
    """
    response = exception_handler(exc, context)
    if response is None:
        return None
    data = getattr(response, "data", None)
    if isinstance(data, dict) and "detail" in data:
        detail = str(data["detail"])
        if "Not found" in detail or detail.startswith("No ") and " matches" in detail:
            response.data = {"error": "not_found", "detail": "No such record. If this URL used to exist, see /redirects for the permanent map."}
            response.status_code = 404
    return response
