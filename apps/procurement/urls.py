from django.urls import path

from procurement import views
from procurement.views_mda import mda_dashboard, mda_detail
from procurement.views_catalogue import catalogue_list, catalogue_detail, create_purchase_order
from procurement.views_auction import auction_list, auction_detail, place_bid
from procurement.views_defects import defects_list, defects_detail, report_defect
from procurement.views_whistleblower import whistleblower_intake, whistleblower_submit
from procurement.feeds import TendersFeed, AwardsFeed
from workflow import views as wf_views

urlpatterns = [
    # Phase 1: Public read views
    path("", views.tender_list, name="tenders"),
    path("awards/", views.awards, name="awards"),
    path("contracts/", wf_views.contracts_dashboard, name="contracts-dashboard"),
    path("contracts/<str:reference>/", views.contract_detail, name="contract-detail"),
    path("contracts/<str:reference>/workspace/", wf_views.contract_workspace, name="contract-workspace"),
    path("contracts/<str:reference>/milestones/", wf_views.contract_milestones, name="contract-milestones"),
    path("contracts/<str:reference>/variations/", wf_views.contract_variations, name="contract-variations"),
    path("suppliers/", views.supplier_list, name="suppliers"),
    path("suppliers/<int:pk>/", wf_views.supplier_detail, name="supplier-detail"),
    path("open-data/", views.open_data, name="open-data"),
    path("indicators/", views.indicators, name="indicators"),
    path("status/", views.status, name="status"),
    path("metrics.js", views.metrics, name="metrics"),

    # Phase 2: Supplier registration
    path("register/", wf_views.register_start, name="register-start"),
    path("register/form/", wf_views.register_form, name="register-form"),
    path("register/form/<str:token>/", wf_views.register_form, name="register-form-token"),
    path("register/review/", wf_views.register_review_queue, name="register-review"),

    # Phase 2: Bidding
    path("<str:ocid>/bid/", wf_views.bid_submit_page, name="bid-submit"),
    path("receipts/<str:receipt_ref>/", wf_views.bid_receipt, name="bid-receipt"),

    # Phase 2: Agent desk & language
    path("agent-desk/", wf_views.agent_desk, name="agent-desk"),
    path("set-language/", wf_views.set_language, name="set-language"),

    # Phase 3: Evaluation
    path("<str:ocid>/evaluation/", wf_views.evaluation_workspace, name="evaluation"),
    path("<str:ocid>/evaluation/report/", wf_views.evaluation_report_page, name="evaluation-report"),

    # Phase 3: Objections & debriefs
    path("<str:ocid>/objections/", wf_views.objection_list, name="objections"),
    path("objections/<int:pk>/", wf_views.objection_detail, name="objection-detail"),
    path("<str:ocid>/objections/file/<int:award_id>/", wf_views.objection_file_page, name="objection-file"),
    path("<str:ocid>/debriefs/", wf_views.debrief_list, name="debriefs"),

    # Phase 4: Performance ratings & payments
    path("ratings/", wf_views.supplier_ratings, name="ratings"),
    path("payments/", wf_views.payments_dashboard, name="payments-dashboard"),

    # RSS feeds
    path("feed/tenders/", TendersFeed(), name="tender-feed"),
    path("feed/awards/", AwardsFeed(), name="award-feed"),

    # MDA Dashboard
    path("mdas/", mda_dashboard, name="mda-dashboard"),
    path("mdas/<str:mda_code>/", mda_detail, name="mda-detail"),

    # Catalogue (common-use goods)
    path("catalogue/", catalogue_list, name="catalogue-list"),
    path("catalogue/<int:catalogue_id>/", catalogue_detail, name="catalogue-detail"),
    path("catalogue/<int:catalogue_id>/order/", create_purchase_order, name="create-purchase-order"),

    # Reverse auctions
    path("auctions/", auction_list, name="auction-list"),
    path("auctions/<int:auction_id>/", auction_detail, name="auction-detail"),
    path("auctions/<int:auction_id>/bid/", place_bid, name="place-bid"),

    # Defects liability
    path("defects/", defects_list, name="defects-list"),
    path("defects/<int:contract_id>/", defects_detail, name="defects-detail"),
    path("defects/<int:contract_id>/report/", report_defect, name="report-defect"),

    # Whistleblower intake
    path("whistleblower/", whistleblower_intake, name="whistleblower-intake"),
    path("whistleblower/submit/", whistleblower_submit, name="whistleblower-submit"),

    # Phase 1: Tender detail and documents (must be last — catches all <ocid> patterns)
    path("<str:ocid>/", views.tender_detail, name="tender-detail"),
    path("<str:ocid>/documents/<int:doc_id>", views.document_meta, name="tender-document"),
    path("<str:ocid>/ocds.json", views.tender_ocds, name="tender-ocds"),
]
