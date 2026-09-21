from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from procurement.api import views

# trailing_slash=False: an API that answers /api/v1/tenders/<ocid> with a 301 to a
# slash is a real trap — curl does not follow redirects by default, and a naive
# consumer reads the 301 body as the payload. URLs are canonical, not redirected.
router = DefaultRouter(trailing_slash=False)
router.register("tenders", views.TenderViewSet, basename="tender")
router.register("awards", views.AwardViewSet, basename="award")
router.register("contracts", views.ContractViewSet, basename="contract")

urlpatterns = [
    path("", include(router.urls)),
    path("releases", views.releases, name="releases"),
    path("bulk", views.bulk, name="bulk"),
    path("suppliers", views.suppliers, name="suppliers"),
    path("stats", views.stats, name="stats"),
    path("ledger/head", views.ledger_head, name="ledger-head"),
    path("indicators", views.indicators, name="indicators"),
    path("policy/access", views.access_policy, name="access-policy"),
    path("schema", SpectacularAPIView.as_view(), name="schema"),
    path("schema.json", views.release_schema, name="release-schema"),
    path("docs", SpectacularSwaggerView.as_view(url_name="procurement.api:schema"), name="docs"),
]
