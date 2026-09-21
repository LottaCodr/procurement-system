from django.urls import path

from workflow import api_views as wf
from workflow import api_views_phases as ph

urlpatterns = [
    # MFA / auth
    path("login", wf.api_login),
    path("mfa/verify", wf.api_mfa_verify),
    path("mfa/enrol", wf.api_enrol_totp),

    # Supplier registration (multi-step, no auth needed to start; returns a draft token)
    path("vendor/register/start", wf.api_register_start),
    path("vendor/register/<str:token>/step", wf.api_register_step),
    path("vendor/register/<str:token>/document", wf.api_register_document),
    path("vendor/register/<str:token>/owner", wf.api_register_owner),
    path("vendor/register/<str:token>/submit", wf.api_register_submit),

    # Sealed bidding
    path("tenders/<str:ocid>/sealing-key", wf.api_tender_sealing_key),
    path("tenders/<str:ocid>/bids/submit", wf.api_bid_submit),
    path("tenders/<str:ocid>/unseal", wf.api_unseal),

    # Watchlist (needs auth)
    path("watchlist", wf.api_watchlist),
    path("tenders/<str:ocid>/watch", wf.api_watch),

    # Catalogue fast-lane
    path("catalogue", wf.api_catalogue),

    # Whistleblower (anonymous)
    path("whistleblower", wf.api_whistleblower),

    # Phase 3: Evaluation workflow
    path("tenders/<str:ocid>/committee", ph.api_form_committee),
    path("tenders/<str:ocid>/bids/<int:bid_id>/score", ph.api_score_bid),
    path("tenders/<str:ocid>/scores/<int:score_id>/dissent", ph.api_record_dissent),
    path("tenders/<str:ocid>/evaluation/report", ph.api_publish_report),
    path("tenders/<str:ocid>/approval", ph.api_route_approval),

    # Phase 3: Objections & debriefs
    path("tenders/<str:ocid>/awards/<int:award_id>/objection", ph.api_file_objection),
    path("objections/<int:objection_id>/decide", ph.api_decide_objection),
    path("tenders/<str:ocid>/awards/<int:award_id>/debrief", ph.api_request_debrief),
    path("debriefs/<int:debrief_id>/respond", ph.api_respond_debrief),

    # Phase 4: Contract management
    path("contracts/<str:reference>/milestones", ph.api_create_milestone),
    path("milestones/<int:milestone_id>/complete", ph.api_complete_milestone),
    path("contracts/<str:reference>/variations", ph.api_propose_variation),
    path("variations/<int:variation_id>/approve", ph.api_approve_variation),
    path("contracts/<str:reference>/guarantees", ph.api_add_guarantee),
    path("contracts/<str:reference>/accept", ph.api_certify_acceptance),
    path("contracts/<str:reference>/pay", ph.api_certify_payment),
    path("contracts/<str:reference>/rate", ph.api_rate_performance),
    path("contracts/<str:reference>/close", ph.api_close_contract),
]
