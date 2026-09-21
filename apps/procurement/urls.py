from django.urls import path

from procurement import views

urlpatterns = [
    path("", views.tender_list, name="tenders"),
    path("<str:ocid>/", views.tender_detail, name="tender-detail"),
    path("<str:ocid>/documents/<int:doc_id>", views.document_meta, name="tender-document"),
    path("<str:ocid>/ocds.json", views.tender_ocds, name="tender-ocds"),
    path("awards/", views.awards, name="awards"),
    path("contracts/<str:reference>/", views.contract_detail, name="contract-detail"),
    path("suppliers/", views.supplier_list, name="suppliers"),
    path("open-data/", views.open_data, name="open-data"),
    path("indicators/", views.indicators, name="indicators"),
    path("status/", views.status, name="status"),
    path("metrics.js", views.metrics, name="metrics"),
]
