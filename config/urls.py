from django.urls import include, path
from django.views.generic import RedirectView

from procurement import views as procurement_views

urlpatterns = [
    # The landing page is the procurement surface: one implementation, mounted
    # at the root, so "/" and "/tenders/" can never drift apart.
    path("", procurement_views.home, name="home"),
    path("", include("core.urls")),
    path("tenders/", include("procurement.urls")),
    path("api/v1/", include("procurement.api.urls")),
    path("api/wf/", include("workflow.urls")),
    path("api/schema", RedirectView.as_view(url="/api/v1/schema", permanent=False)),
]
