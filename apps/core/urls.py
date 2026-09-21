from django.http import HttpResponse
from django.template.loader import get_template
from django.urls import path

from core import views


def stylesheet(request):
    """CSS served from the app: no CDN, no webfonts, no runtime Tailwind.
    Kano shipped @tailwindcss/browser@4 from a public CDN on a government
    transaction, which is an availability and supply-chain bug. Cached a day."""
    resp = HttpResponse(get_template("app.css").render({}), content_type="text/css; charset=utf-8")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


urlpatterns = [
    path("", views.home, name="home"),
    path("awards/", views.redirect_awards, name="awards-root"),
    path("redirects", views.redirect_map, name="redirect-map"),
    path("stylesheet", stylesheet, name="stylesheet"),
]
