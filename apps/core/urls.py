"""Project-level URLs: the homepage, the stylesheet, the permanent-URL map,
robots/sitemap, and the staff sign-in used by the internal workspace.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import path

from core import css_build, views


def stylesheet(request):
    """Serve the single built stylesheet.

    Built at deploy time by `manage.py buildcss`; built on the fly here if the
    artifact is missing, so a fresh checkout and a test run both render correct
    CSS without a build step in the loop.

    Cached hard *because the URL is content-addressed* (`/stylesheet?v=<hash>`):
    a new build produces a new URL, so there is no stale-style window and no
    "please hard-refresh" support burden.
    """
    css = css_build.built_css()
    resp = HttpResponse(css, content_type="text/css; charset=utf-8")
    resp["ETag"] = f'"{css_build.read_manifest().get("source_hash") or "dev"}"'
    resp["Cache-Control"] = "public, max-age=31536000, immutable"
    resp["X-Content-Type-Options"] = "nosniff"
    if request.headers.get("If-None-Match") == resp["ETag"]:
        return HttpResponse(status=304, headers={"ETag": resp["ETag"]})
    return resp


urlpatterns = [
    path("awards/", views.redirect_awards, name="awards-root"),
    path("redirects", views.redirect_map, name="redirect-map"),
    path("stylesheet", stylesheet, name="stylesheet"),
    path("robots.txt", views.robots, name="robots"),
    path("sitemap.xml", views.sitemap, name="sitemap"),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
]

if settings.DEBUG:
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += staticfiles_urlpatterns()
