"""Link and redirect integrity tests.

Every URL that has ever been published must either:
1. Return 200 OK, or
2. Redirect (301/302) to a URL that returns 200 OK.

This prevents the Kano failure mode: all legacy URLs returning 404.
"""
import pytest
from django.test import Client


@pytest.mark.django_db
def test_all_public_pages_return_200():
    """Every public page must be accessible without authentication."""
    client = Client()

    urls = [
        "/",
        "/tenders/",
        "/tenders/awards/",
        "/tenders/contracts/",
        "/tenders/suppliers/",
        "/tenders/ratings/",
        "/tenders/payments/",
        "/tenders/register/",
        "/tenders/agent-desk/",
        "/tenders/status/",
        "/tenders/open-data/",
        "/tenders/indicators/",
    ]

    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, f"{url} returned {response.status_code}"


@pytest.mark.django_db
def test_api_endpoints_accessible():
    """All public API endpoints must be accessible without authentication."""
    client = Client()

    endpoints = [
        "/api/v1/stats",
        "/api/v1/suppliers",
        "/api/v1/releases",
        "/api/v1/ledger/head",
        "/api/v1/indicators",
        "/api/v1/policy/access",
        "/api/v1/schema",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200, \
            f"{endpoint} returned {response.status_code}"


@pytest.mark.django_db
def test_ocid_urls_are_permanent():
    """OCID-based URLs must work for any existing tender."""
    from procurement.models import Tender

    client = Client()

    tenders = Tender.objects.exclude(
        status__in=["DRAFT", "CANCELLED"]
    )[:5]

    for tender in tenders:
        url = f"/tenders/{tender.ocid}/"
        response = client.get(url)
        assert response.status_code == 200, \
            f"Tender {tender.ocid} at {url} returned {response.status_code}"


@pytest.mark.django_db
def test_legacy_redirect_map_exists():
    """The redirect map must be defined and non-empty."""
    from django.conf import settings
    assert hasattr(settings, "LEGACY_REDIRECT_MAP")
    assert len(settings.LEGACY_REDIRECT_MAP) > 0


@pytest.mark.django_db
def test_static_files_accessible():
    """CSS and other static files must be accessible."""
    client = Client()
    response = client.get("/stylesheet")
    assert response.status_code == 200
    assert "text/css" in response["Content-Type"]


@pytest.mark.django_db
def test_404_returns_proper_page():
    """404 errors must return a proper page, not a crash."""
    client = Client()
    response = client.get("/this-url-does-not-exist-12345/")
    assert response.status_code == 404
    assert response.status_code != 500


@pytest.mark.django_db
def test_contract_detail_works():
    """Contract detail pages must work for existing contracts."""
    from procurement.models import Contract

    client = Client()
    contracts = Contract.objects.all()[:3]

    for contract in contracts:
        url = f"/tenders/contracts/{contract.reference}/"
        response = client.get(url)
        assert response.status_code == 200, \
            f"Contract {contract.reference} at {url} returned {response.status_code}"


@pytest.mark.django_db
def test_supplier_detail_works():
    """Supplier detail pages must work for existing suppliers."""
    from procurement.models_party import Party

    client = Client()
    suppliers = Party.objects.filter(is_active=True)[:3]

    for supplier in suppliers:
        url = f"/tenders/suppliers/{supplier.pk}/"
        response = client.get(url)
        assert response.status_code == 200, \
            f"Supplier {supplier.legal_name} at {url} returned {response.status_code}"
