from django.http import HttpResponse
from django.template.loader import get_template
from django.urls import include, path
from django.views.generic import RedirectView


def stylesheet(request):
    resp = HttpResponse(get_template("app.css").render({}), content_type="text/css; charset=utf-8")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


urlpatterns = [
    path("", include("core.urls")),
    path("tenders/", include("procurement.urls")),
    path("api/v1/", include("procurement.api.urls")),
    path("api/wf/", include("workflow.urls")),
    path("api/schema", RedirectView.as_view(url="/api/v1/schema", permanent=False)),
]
