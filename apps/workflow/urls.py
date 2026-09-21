from django.urls import path

from workflow import api_views as wf

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
]
