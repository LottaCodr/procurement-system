"""URL map for the public procurement surface, under /tenders/.

Two rules govern this file:

1. **The order matters and is deliberate.** The `<str:ocid>` catch-all is last,
   so a new static page can never be shadowed by a tender reference that happens
   to look like a word.
2. **Every published URL is permanent.** When a path changes (as
   `catalogue/<id>/order/` did — it was an unauthenticated write endpoint, which
   was wrong), the old path is added to `settings.LEGACY_REDIRECT_MAP` rather
   than deleted. `tests/test_link_integrity.py` asserts every mapped target
   resolves.
"""
from django.urls import path

from procurement import views
from procurement.feeds import AwardsFeed, TendersFeed
from procurement.views_auction import auction_detail, auction_list
from procurement.views_catalogue import (
    catalogue_detail,
    catalogue_list,
    purchase_order_detail,
    purchase_order_list,
)
from procurement.views_defects import defects_detail, defects_list
from procurement.views_mda import mda_dashboard, mda_detail
from procurement.views_whistleblower import (
    whistleblower_intake,
    whistleblower_status,
    whistleblower_status_lookup,
)
from workflow import views as wf_views

urlpatterns = [
    # --- the register itself ------------------------------------------------
    path("", views.tender_list, name="tenders"),
    path("export.csv", views.tender_export_csv, name="tenders-export"),
    path("awards/", views.awards, name="awards"),
    path("rules/", views.rules, name="rules"),
    path("help/", views.help_page, name="help"),
    path("styleguide/", views.styleguide, name="styleguide"),

    # --- contracts and delivery --------------------------------------------
    path("contracts/", wf_views.contracts_dashboard, name="contracts-dashboard"),
    # `path:` (not `str:`) because real contract references contain slashes —
    # "CTR/TAR-MOH-2026-0001" is what the MDA printed on the contract, and an
    # internal reference must not be silently rewritten to fit the URL scheme.
    # The sub-pages come FIRST so the greedy converter cannot swallow
    # "/workspace/" as part of the reference.
    path("contracts/<path:reference>/workspace/", wf_views.contract_workspace, name="contract-workspace"),
    path("contracts/<path:reference>/milestones/", wf_views.contract_milestones, name="contract-milestones"),
    path("contracts/<path:reference>/variations/", wf_views.contract_variations, name="contract-variations"),
    path("contracts/<path:reference>/", views.contract_detail, name="contract-detail"),

    # --- suppliers ----------------------------------------------------------
    path("suppliers/", views.supplier_list, name="suppliers"),
    path("suppliers/<int:pk>/", wf_views.supplier_detail, name="supplier-detail"),
    path("register/", wf_views.register_start, name="register-start"),
    path("register/form/", wf_views.register_form, name="register-form"),
    path("register/form/<str:token>/", wf_views.register_form, name="register-form-token"),
    path("register/review/", wf_views.register_review_queue, name="register-review"),
    path("ratings/", wf_views.supplier_ratings, name="ratings"),

    # --- open data and accountability --------------------------------------
    path("open-data/", views.open_data, name="open-data"),
    path("indicators/", views.indicators, name="indicators"),
    path("status/", views.status, name="status"),
    path("metrics.js", views.metrics, name="metrics"),

    # --- bidding ------------------------------------------------------------
    path("<str:ocid>/bid/", wf_views.bid_submit_page, name="bid-submit"),
    path("receipts/<str:receipt_ref>/", wf_views.bid_receipt, name="bid-receipt"),

    # --- assisted bidding and language -------------------------------------
    path("agent-desk/", wf_views.agent_desk, name="agent-desk"),
    path("set-language/", wf_views.set_language, name="set-language"),

    # --- evaluation, objections, debriefs ----------------------------------
    path("<str:ocid>/evaluation/", wf_views.evaluation_workspace, name="evaluation"),
    path("<str:ocid>/evaluation/report/", wf_views.evaluation_report_page, name="evaluation-report"),
    path("<str:ocid>/objections/", wf_views.objection_list, name="objections"),
    path("<str:ocid>/objections/file/<int:award_id>/", wf_views.objection_file_page, name="objection-file"),
    path("objections/<int:pk>/", wf_views.objection_detail, name="objection-detail"),
    path("<str:ocid>/debriefs/", wf_views.debrief_list, name="debriefs"),

    # --- money --------------------------------------------------------------
    path("payments/", wf_views.payments_dashboard, name="payments-dashboard"),

    # --- MDA utilisation ----------------------------------------------------
    path("mdas/", mda_dashboard, name="mda-dashboard"),
    path("mdas/<str:mda_code>/", mda_detail, name="mda-detail"),

    # --- catalogue fast lane (read-only in public; ordering is an MDA action) -
    path("catalogue/", catalogue_list, name="catalogue-list"),
    path("catalogue/<int:catalogue_id>/", catalogue_detail, name="catalogue-detail"),
    path("purchase-orders/", purchase_order_list, name="po-list"),
    path("purchase-orders/<int:po_id>/", purchase_order_detail, name="po-detail"),

    # --- reverse auctions ---------------------------------------------------
    path("auctions/", auction_list, name="auction-list"),
    path("auctions/<int:auction_id>/", auction_detail, name="auction-detail"),

    # --- defects liability --------------------------------------------------
    path("defects/", defects_list, name="defects-list"),
    path("defects/<int:contract_id>/", defects_detail, name="defects-detail"),

    # --- whistleblower ------------------------------------------------------
    path("whistleblower/", whistleblower_intake, name="whistleblower-intake"),
    path("whistleblower/status/", whistleblower_status_lookup, name="whistleblower-status-lookup"),
    path("whistleblower/status/<str:reference>/", whistleblower_status, name="whistleblower-status"),

    # --- OCID catch-all: keep last -----------------------------------------
    path("<str:ocid>/", views.tender_detail, name="tender-detail"),
    path("<str:ocid>/documents/<int:doc_id>", views.document_meta, name="tender-document"),
    path("<str:ocid>/ocds.json", views.tender_ocds, name="tender-ocds"),

    # --- feeds --------------------------------------------------------------
    path("feed/tenders/", TendersFeed(), name="tender-feed"),
    path("feed/awards/", AwardsFeed(), name="award-feed"),
]
