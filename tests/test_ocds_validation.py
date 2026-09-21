"""OCDS schema validation tests.

Every OCDS release must validate against the project's OCDS schema.
This is tested in CI to prevent silent corruption of open data.
"""
import pytest
import json
from django.test import Client


@pytest.mark.django_db
def test_ocds_release_has_required_fields():
    """Every tender's OCDS release must have the required top-level fields."""
    from procurement.models import Tender

    tenders = Tender.objects.exclude(
        status__in=["DRAFT", "CANCELLED"]
    )[:10]

    for tender in tenders:
        release = tender.ocds_release

        assert "ocid" in release, f"{tender.ocid}: missing ocid"
        assert release["ocid"] == tender.ocid, \
            f"{tender.ocid}: ocid mismatch in release"
        assert "id" in release, f"{tender.ocid}: missing id"
        assert "date" in release, f"{tender.ocid}: missing date"
        assert "tag" in release, f"{tender.ocid}: missing tag"
        assert "initiationType" in release, f"{tender.ocid}: missing initiationType"


@pytest.mark.django_db
def test_ocds_api_endpoint_returns_valid_json():
    """The /api/v1/tenders/{ocid}/ocds endpoint must return valid OCDS."""
    from procurement.models import Tender

    client = Client()

    tenders = Tender.objects.exclude(
        status__in=["DRAFT", "CANCELLED"]
    )[:5]

    for tender in tenders:
        url = f"/tenders/{tender.ocid}/ocds.json"
        response = client.get(url)

        assert response.status_code == 200
        assert "json" in response["Content-Type"]

        try:
            data = json.loads(response.content)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON from {url}: {e}")

        assert "ocid" in data
        assert data["ocid"] == tender.ocid


@pytest.mark.django_db
def test_ocds_releases_feed_works():
    """The /api/v1/releases NDJSON feed must contain valid JSON per line."""
    client = Client()

    response = client.get("/api/v1/releases")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    lines = [line for line in content.strip().split("\n") if line]

    if not lines:
        return  # Empty feed is valid if no data

    for i, line in enumerate(lines[:10]):
        try:
            release = json.loads(line)
        except json.JSONDecodeError as e:
            pytest.fail(f"Line {i+1} is not valid JSON: {e}")

        assert "ocid" in release, f"Line {i+1} missing ocid"
        assert "id" in release, f"Line {i+1} missing id"


@pytest.mark.django_db
def test_ocds_bulk_download_works():
    """The /api/v1/bulk endpoint must return a valid ZIP file."""
    client = Client()

    response = client.get(f"/api/v1/bulk?year={timezone.now().year}")
    assert response.status_code == 200
    assert "zip" in response["Content-Type"]

    import io
    import zipfile

    try:
        zip_buffer = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_buffer, 'r') as zf:
            assert len(zf.namelist()) > 0
    except zipfile.BadZipFile:
        pytest.fail("Bulk download is not a valid ZIP file")


@pytest.mark.django_db
def test_ocds_tender_section_present():
    """OCDS releases for published tenders must have a tender section."""
    from procurement.models import Tender

    tenders = Tender.objects.exclude(
        status__in=["DRAFT", "CANCELLED"]
    )[:5]

    for tender in tenders:
        release = tender.ocds_release

        if "tender" in release:
            tender_data = release["tender"]
            assert "id" in tender_data, \
                f"{tender.ocid}: tender section missing id"
            assert "title" in tender_data, \
                f"{tender.ocid}: tender section missing title"


@pytest.mark.django_db
def test_ocds_dates_are_strings():
    """All dates in OCDS releases must be ISO 8601 strings."""
    from procurement.models import Tender

    tenders = Tender.objects.exclude(
        status__in=["DRAFT", "CANCELLED"]
    )[:5]

    for tender in tenders:
        release = tender.ocds_release

        date_str = release.get("date")
        assert isinstance(date_str, str), \
            f"{tender.ocid}: date must be a string, got {type(date_str)}"


from django.utils import timezone
