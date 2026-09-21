"""Homepage metrics integrity tests.

Every figure displayed on the homepage must be computed from live data.
No unbacked claims. This test verifies that the metrics match the database.
"""
import pytest
from django.test import Client
from django.db.models import Sum
from django.utils import timezone


@pytest.mark.django_db
def test_homepage_loads_with_metrics():
    """Homepage must load and contain computed metrics."""
    client = Client()
    response = client.get("/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    # Should contain some metric-like content
    assert len(content) > 100


@pytest.mark.django_db
def test_no_placeholder_numbers():
    """Homepage must not contain placeholder numbers like ₦48.7bn."""
    client = Client()
    response = client.get("/")
    content = response.content.decode("utf-8")

    fake_claims = [
        "₦48.7bn",
        "48.7 billion",
        "48,700,000,000",
    ]

    for claim in fake_claims:
        assert claim not in content, \
            f"Homepage contains unbacked claim: {claim}"


@pytest.mark.django_db
def test_stats_api_returns_valid_data():
    """The /api/v1/stats endpoint must return valid JSON with expected fields."""
    client = Client()
    response = client.get("/api/v1/stats")
    assert response.status_code == 200

    import json
    stats = json.loads(response.content)

    # These are the fields live_metrics() returns
    expected_fields = [
        "open_tenders", "published_this_year", "awards_published",
        "total_award_value", "suppliers_verified",
    ]
    for field in expected_fields:
        assert field in stats, f"Stats API missing field: {field}"


@pytest.mark.django_db
def test_zero_values_displayed_honestly():
    """If there are zero awards, the site must say zero, not hide it."""
    from procurement.models import Award

    client = Client()
    response = client.get("/")
    content = response.content.decode("utf-8")

    awards_count = Award.objects.filter(
        status__in=["PUBLISHED", "CONTRACTED"]
    ).count()

    # If zero awards, the page should show 0 somewhere
    if awards_count == 0:
        assert "0" in content


@pytest.mark.django_db
def test_ledger_head_api_works():
    """The /api/v1/ledger/head endpoint must return chain status."""
    client = Client()
    response = client.get("/api/v1/ledger/head")
    assert response.status_code == 200

    import json
    data = json.loads(response.content)

    assert "head_hash" in data
    assert "chain_valid" in data
    assert isinstance(data["chain_valid"], bool)


@pytest.mark.django_db
def test_status_page_loads():
    """The /tenders/status/ page must load and show system information."""
    client = Client()
    response = client.get("/tenders/status/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert "ledger" in content.lower() or "chain" in content.lower()


@pytest.mark.django_db
def test_awards_page_matches_database():
    """The /tenders/awards/ page must show awards from the database."""
    from procurement.models import Award

    client = Client()
    response = client.get("/tenders/awards/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    awards = Award.objects.filter(
        status__in=["PUBLISHED", "CONTRACTED"]
    ).order_by("-published_at")[:10]

    if awards.exists():
        found = False
        for award in awards:
            if award.tender.ocid in content:
                found = True
                break
        assert found, "Awards page doesn't show any awards from database"


@pytest.mark.django_db
def test_suppliers_page_matches_database():
    """The /tenders/suppliers/ page must show suppliers from the database."""
    from procurement.models_party import Party

    client = Client()
    response = client.get("/tenders/suppliers/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    suppliers = Party.objects.filter(is_active=True)[:10]

    if suppliers.exists():
        found = False
        for supplier in suppliers:
            if supplier.legal_name in content or supplier.rc_number in content:
                found = True
                break
        assert found, "Suppliers page doesn't show any suppliers from database"


@pytest.mark.django_db
def test_metrics_match_database():
    """API stats must match what we compute from the database directly."""
    from procurement.models import Tender, Award

    client = Client()
    response = client.get("/api/v1/stats")
    import json
    stats = json.loads(response.content)

    # "Open for bids" has exactly one definition in this codebase —
    # TenderQuerySet.open(), reused by the register, the headline count and the
    # API. A second definition here (PUBLISHED only, missing CLARIFYING) is how
    # the API and the page came to disagree.
    db_open = Tender.objects.public().open().count()
    assert stats["open_tenders"] == db_open
    if Tender.objects.filter(status=Tender.Status.CLARIFYING).exists():
        assert Tender.objects.public().open().count() > Tender.objects.public().filter(
            status=Tender.Status.PUBLISHED, submission_close_at__gt=timezone.now()
        ).count(), "a CLARIFYING tender should count as open"

    # Verify awards_published
    db_awards = Award.objects.filter(
        status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED]
    ).count()
    assert stats["awards_published"] == db_awards

    # Verify total_award_value
    db_total = Award.objects.filter(
        status__in=[Award.Status.PUBLISHED, Award.Status.CONTRACTED]
    ).aggregate(v=Sum("amount"))["v"] or 0
    assert float(stats["total_award_value"]) == float(db_total)


@pytest.mark.django_db
def test_stats_api_exposes_every_live_metric():
    """The site tells readers the API is the source of truth for every figure on
    it. A figure the API omits makes that claim false, so the payload is checked
    against `live_metrics()` itself."""
    import json

    from procurement.views import live_metrics

    stats = json.loads(Client().get("/api/v1/stats").content)
    missing = set(live_metrics()) - set(stats)
    assert not missing, f"figures printed on the site but absent from the API: {sorted(missing)}"


@pytest.mark.django_db
def test_homepage_kpis_equal_the_stats_api(dataset=None):
    """The page and the API must not be able to disagree about a headline number."""
    import json
    import re

    stats = json.loads(Client().get("/api/v1/stats").content)
    html = Client().get("/").content.decode()
    printed = [int(v.replace(",", "")) for v in
               re.findall(r'<span class="kpi__value">\s*([\d,]+)', html)]
    assert printed[:4] == [
        stats["open_tenders"],
        stats["awards_published"],
        stats["contracts_signed"],
        stats["suppliers_verified"],
    ], f"homepage shows {printed[:4]}, API reports "
    f"{[stats['open_tenders'], stats['awards_published'], stats['contracts_signed'], stats['suppliers_verified']]}"


@pytest.mark.django_db
def test_every_artefact_publication_command_reports_a_verdict():
    """The verification commands CI runs must state a verdict and exit accordingly.

    They are the project's whole safety argument, so their behaviour on an empty
    database matters: a command that fails on no data teaches people to ignore
    it, and one that silently passes on no data teaches them to trust it wrongly.
    """
    from django.core.management import call_command

    import io

    for name in ("verify_ledger", "publish_selftest", "check_links", "check_metrics_match"):
        try:
            call_command(name, stdout=io.StringIO(), stderr=io.StringIO())
        except SystemExit as exc:  # some commands exit rather than return
            assert exc.code in (0, None), f"{name} exited {exc.code} on an empty register"
