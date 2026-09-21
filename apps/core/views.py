"""Project-level views: home redirect surface, robots, sitemap, URL map."""
from __future__ import annotations

from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from django.conf import settings


@require_GET
def home(request):
    """The root URL redirects to the procurement home so there is exactly one
    implementation of the landing page. (There used to be two views named
    `home`: the real one in `procurement` with the metrics, and a second in
    `core` that rendered the same template with *no context*. Because Django
    templates render missing variables as empty strings, the second one returned
    a perfectly healthy HTTP 200 showing a dashboard of blank figures — the
    exact "transparency theatre" failure this project exists to prevent.)"""
    return HttpResponseRedirect("/tenders/")


@require_GET
def redirect_awards(request):
    return HttpResponseRedirect("/tenders/awards/")


@require_GET
def redirect_map(request):
    """Published, machine-readable: what maps to what, forever."""
    return JsonResponse(
        {
            "policy": "Retired URLs redirect permanently; they never 404.",
            "permanent": settings.LEGACY_REDIRECT_MAP,
            "contact": settings.CONTACT_EMAIL,
        }
    )


@require_GET
def robots(request):
    """Crawl rules for a register that *wants* to be indexed.

    Everything public is crawlable; review queues, receipts and the style guide
    are not, because indexing a supplier's draft registration or a bid receipt
    reference would be a privacy failure, not a discovery win.
    """
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /tenders/register/review/",
        "Disallow: /tenders/receipts/",
        "Disallow: /tenders/styleguide/",
        "Disallow: /tenders/contracts/*/workspace/",
        "Disallow: /api/",
        "",
        "Sitemap: /sitemap.xml",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


@require_GET
def sitemap(request):
    """XML sitemap of the public register.

    Absolute URLs, because a sitemap of relative paths is invalid XML as far as
    every crawler is concerned, and published records should be findable by the
    search engines citizens actually use.
    """
    from procurement.models import Contract, Tender

    entries: list[tuple[str, str, str]] = [
        ("/tenders/", "1.0", "hourly"),
        ("/tenders/awards/", "0.9", "daily"),
        ("/tenders/contracts/", "0.8", "daily"),
        ("/tenders/suppliers/", "0.7", "weekly"),
        ("/tenders/rules/", "0.8", "monthly"),
        ("/tenders/indicators/", "0.7", "monthly"),
        ("/tenders/open-data/", "0.7", "weekly"),
        ("/tenders/status/", "0.5", "daily"),
        ("/tenders/help/", "0.6", "monthly"),
        ("/tenders/whistleblower/", "0.6", "monthly"),
    ]
    entries += [(f"/tenders/{t.ocid}/", "0.8", "daily") for t in Tender.objects.public().only("ocid")]
    entries += [
        (f"/tenders/contracts/{c.reference}/", "0.6", "weekly")
        for c in Contract.objects.only("reference")[:5000]
    ]

    today = timezone.localdate().isoformat()
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, priority, freq in entries:
        body.append(
            f"<url><loc>{request.build_absolute_uri(loc)}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        )
    body.append("</urlset>")
    return HttpResponse("\n".join(body), content_type="application/xml; charset=utf-8")
