"""UI and UX contract tests.

These are not rendering smoke tests. Each one encodes a decision that a person
made about how this register should behave, and each will fail if a later change
quietly reverses it:

* Accessibility and semantics are asserted on the **rendered HTML**, not on the
  template source, because a template can contain every good practice and still
  be broken by the time a browser sees it.
* Security-relevant markup (no inline script, no inline style) is asserted
  because the Content-Security-Policy deliberately has no `unsafe-inline`
  escape hatch — a page that needs one is a page that would break in production.
* The page-weight budget is asserted because for a bidder paying for data by the
  gigabyte in Taraba, a heavy page is a cost, not a metric.
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from core import css_build
from procurement.models import Award, Bid, Contract, Criterion, Tender, TenderDocument
from procurement.models_party import Agency, BudgetLine, Party

# Pages that must satisfy every rule below. Kept short on purpose: these are the
# surfaces a bidder, a journalist or a supplier actually lands on.
PUBLIC_PAGES = [
    "/",
    "/tenders/",
    "/tenders/?open=1&sort=closing",
    "/tenders/?q=nothing-matches-this&status=PUBLISHED",
    "/tenders/awards/",
    "/tenders/contracts/",
    "/tenders/suppliers/",
    "/tenders/rules/",
    "/tenders/help/",
    "/tenders/indicators/",
    "/tenders/open-data/",
    "/tenders/status/",
    "/tenders/mdas/",
    "/tenders/ratings/",
    "/tenders/payments/",
    "/tenders/agent-desk/",
    "/tenders/register/",
    "/tenders/catalogue/",
    "/tenders/purchase-orders/",
    "/tenders/auctions/",
    "/tenders/defects/",
    "/tenders/whistleblower/",
    "/accounts/login/",
    "/this-page-does-not-exist/",
]


@pytest.fixture
def dataset(db):
    """The smallest register that exercises every template branch."""
    agency = Agency.objects.create(code="MOH", name="Ministry of Health", kind="MINISTRY")
    # A tender without a budget line is refused by a CHECK constraint: public
    # money must be traceable to an appropriation before it can be advertised.
    budget = BudgetLine.objects.create(
        fy="2026", agency=agency, project_code="MOH-2026-BH",
        description="Primary health care capital", amount=Decimal("250000000.00"),
    )
    supplier = Party.objects.create(
        legal_name="Bali Medical Supplies Ltd",
        rc_number="RC1234567",
        tin="12345678-0001",
        email="sales@balimed.ng",
        phone="+2348091112222",
        lga="Bali",
        category="B",
    )
    other = Party.objects.create(
        legal_name="Wukari General Merchants Ltd",
        rc_number="RC7654321",
        tin="87654321-0002",
        email="info@wukari-gm.ng",
        phone="+2348091113333",
        lga="Wukari",
        category="B",
    )
    now = timezone.now()
    tender = Tender.objects.create(
        agency=agency,
        budget_line=budget,
        method="NCB",
        title="Rehabilitation of Bali boreholes",
        description="Rehabilitation of twelve boreholes in Bali LGA.",
        est_value=Decimal("12400000.00"),
        status=Tender.Status.PUBLISHED,
        published_at=now - timedelta(days=3),
        qa_close_at=now + timedelta(days=5),
        submission_close_at=now + timedelta(days=12),
        opening_at=now + timedelta(days=13),
    )
    TenderDocument.objects.create(
        tender=tender, kind="SOLICITATION", title="Solicitation documents",
        sha256="a" * 64, size_bytes=204800, obj_key="obj:solicitation.pdf",
    )
    Criterion.objects.create(tender=tender, code="C1", name="Technical approach", weight=70, kind="SCORED", min_score=50)
    Criterion.objects.create(tender=tender, code="C2", name="Experience", weight=30, kind="SCORED", min_score=40)

    awarded = Tender.objects.create(
        agency=agency, budget_line=budget, method="NCB", title="Supply of hospital beds",
        est_value=Decimal("48000000.00"), status=Tender.Status.CONTRACTED,
        published_at=now - timedelta(days=90),
        submission_close_at=now - timedelta(days=60),
        opening_at=now - timedelta(days=58),
    )
    bid = Bid.objects.create(
        tender=awarded, supplier=supplier, amount=Decimal("48500000.00"),
        status=Bid.Status.EVALUATED,
    )
    award = Award.objects.create(
        tender=awarded, bid=bid, amount=Decimal("48500000.00"),
        status=Award.Status.CONTRACTED, reason="Highest weighted score.",
        published_at=now - timedelta(days=30),
    )
    # A second bid so the awards page has more than one row of shape to render.
    bid2 = Bid.objects.create(
        tender=awarded, supplier=other, amount=Decimal("49900000.00"),
        status=Bid.Status.EVALUATED,
    )
    Award.objects.create(
        tender=awarded, bid=bid2, amount=Decimal("49900000.00"),
        status=Award.Status.PUBLISHED, reason="Second lot.",
        published_at=now - timedelta(days=29),
    )
    Contract.objects.create(
        award=award, reference="CTR/TAR-MOH-2026-0001", value=Decimal("48500000.00"),
        duration_days=120, location="Bali LGA", deliverables="Delivery to six facilities.",
    )
    return {"agency": agency, "supplier": supplier, "tender": tender, "awarded": awarded}


def fetch(client, url):
    return client.get(url)


# --------------------------------------------------------------------- semantics
@pytest.mark.django_db
@pytest.mark.parametrize("url", PUBLIC_PAGES)
def test_page_has_one_h1_and_a_descriptive_title(url, dataset):
    response = Client().get(url)
    assert response.status_code in (200, 404), f"{url} returned {response.status_code}"
    html = response.content.decode()
    assert html.count("<h1") == 1, f"{url} has {html.count('<h1')} h1 elements"
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    assert title and len(title.group(1).strip()) > 8, f"{url} has no useful <title>"


@pytest.mark.django_db
@pytest.mark.parametrize("url", PUBLIC_PAGES)
def test_page_has_language_skip_link_and_landmarks(url, dataset):
    html = Client().get(url).content.decode()
    assert re.search(r'<html lang="[a-zA-Z-]+"', html), f"{url} does not declare a language"
    assert 'class="skip-link"' in html, f"{url} has no skip link"
    assert 'id="main"' in html, f"{url} has no main landmark"
    assert "<header" in html and "<footer" in html, f"{url} is missing a header or footer landmark"


@pytest.mark.django_db
@pytest.mark.parametrize("url", PUBLIC_PAGES)
def test_no_inline_script_or_style(url, dataset):
    """The CSP has no `unsafe-inline`, so inline script or style would be dropped
    by the browser in production — the page would work in DEBUG and fail live."""
    html = Client().get(url).content.decode()
    assert "<script>" not in html, f"{url} contains an inline <script> block"
    assert not re.search(r'\sstyle="', html), f"{url} contains an inline style attribute"
    assert "javascript:" not in html.lower(), f"{url} contains a javascript: URL"


@pytest.mark.django_db
def test_every_table_has_a_caption_and_scoped_headers(dataset):
    client = Client()
    for url in ["/tenders/", "/tenders/awards/", "/", "/tenders/contracts/"]:
        html = client.get(url).content.decode()
        for table in re.findall(r"<table.*?</table>", html, re.S):
            assert "<caption>" in table, f"a table on {url} has no caption"
            for th in re.findall(r"<th(?:\s[^>]*)?>", table):
                assert "scope=" in th, f"a header cell on {url} has no scope attribute"


@pytest.mark.django_db
def test_every_form_control_is_labelled(dataset):
    client = Client()
    for url in ["/tenders/", "/tenders/mdas/", "/tenders/whistleblower/", "/accounts/login/"]:
        html = client.get(url).content.decode()
        ids = set(re.findall(r'<(?:input|select|textarea)[^>]*id="([^"]+)"', html))
        labelled = set(re.findall(r'<label[^>]*for="([^"]+)"', html))
        aria_labelled = set(re.findall(r'aria-labelledby="([^"]+)"', html)) | set(
            re.findall(r'aria-label="[^"]+"', html)
        )
        hidden_ok = set(re.findall(r'<input[^>]*type="hidden"[^>]*id="([^"]+)"', html))
        for field_id in ids - labelled - hidden_ok:
            # A control may be labelled by wrapping (field--inline) or by ref.
            wrapped = re.search(
                r'<label[^>]*>\s*<input[^>]*id="%s"' % re.escape(field_id), html
            )
            assert wrapped or aria_labelled, f"{url}: control #{field_id} has no label"


@pytest.mark.django_db
def test_help_is_in_the_same_place_on_every_page(dataset):
    """WCAG 2.2 SC 3.2.6 Consistent Help: the contact route must not move."""
    from core.contacts import contact_state

    state = contact_state()
    client = Client()
    for url in ["/", "/tenders/", "/tenders/awards/", "/tenders/status/", "/tenders/help/"]:
        html = client.get(url).content.decode()
        assert "/tenders/help/" in html, f"{url} does not link to help"
        if state["CONTACT_PHONE_REAL"]:
            assert "tel:" in html, f"{url} does not expose the telephone contact"


@pytest.mark.django_db
def test_a_placeholder_contact_is_never_presented_as_real(dataset):
    """A number that rings nowhere is worse than no number: the page must say
    the line is unpublished and offer a route that works."""
    from core.contacts import is_real_phone, is_placeholder

    assert is_placeholder("+234 800 000 0000")
    assert not is_real_phone("+234 800 000 0000")
    assert is_real_phone("+234 809 111 2222")

    if is_real_phone(contact_phone()):
        pytest.skip("a real telephone line is configured")

    client = Client()
    for url in ["/", "/tenders/", "/tenders/help/", "/tenders/status/", "/tenders/agent-desk/",
                "/tenders/whistleblower/"]:
        html = client.get(url).content.decode()
        assert "tel:" not in html, f"{url} links a telephone number that is not published"
        assert "000 0000" not in html, f"{url} prints the unconfigured number"
    status = client.get("/tenders/status/").content.decode()
    assert "telephone line is not yet published" in status.lower(), (
        "the status page must record that the telephone line is unpublished"
    )


def contact_phone() -> str:
    from django.conf import settings

    return settings.CONTACT_PHONE


# ------------------------------------------------------------------- formatting
def test_deadline_never_overstates_the_time_left():
    from core.templatetags.core_tags import deadline

    now = timezone.now()
    assert "closed" in deadline(now - timedelta(minutes=1))
    assert "min" in deadline(now + timedelta(minutes=40))
    assert "hours" in deadline(now + timedelta(hours=4))
    assert "days left" in deadline(now + timedelta(days=9))
    assert "no deadline" in deadline(None)


def test_status_tag_always_prints_the_status_word():
    """Colour is never the only carrier of meaning (WCAG 1.4.1)."""
    from core.templatetags.core_tags import status_tag

    for code, word in [
        ("PUBLISHED", "Published"),
        ("AWARDED", "Awarded"),
        ("FROZEN", "Frozen"),
        ("IN_PROGRESS", "In progress"),
    ]:
        rendered = status_tag(code)
        assert word.lower() in rendered.lower(), f"{code} rendered without its word"


def test_compact_money_carries_the_exact_figure():
    """A table may show ₦12.4m; the exact amount must remain in the markup."""
    from core.templatetags.core_tags import naira, naira_exact

    compact = naira(Decimal("12400000"))
    assert "12.4m" in compact
    assert 'value="12400000"' in compact
    assert 'title="₦12,400,000"' in compact
    assert "₦12,400,000" in naira_exact(Decimal("12400000"))


def test_dates_render_for_dates_as_well_as_datetimes():
    """PartyVerification.expires_at is a DateField; formatting it must not 500."""
    from datetime import date

    from core.templatetags.core_tags import datetime_wat, shortdate

    assert shortdate(date(2027, 12, 31)) == "31 Dec 2027"
    assert "31 Dec 2027" in datetime_wat(date(2027, 12, 31))


def test_tax_identifiers_are_masked():
    from core.templatetags.core_tags import mask_tax

    masked = mask_tax("12345678-0001")
    assert masked.endswith("0001")
    assert "12345678" not in masked


# --------------------------------------------------------------------- behaviour
@pytest.mark.django_db
def test_register_search_returns_only_matching_rows(dataset):
    client = Client()
    html = client.get("/tenders/?q=boreholes").content.decode()
    assert "Rehabilitation of Bali boreholes" in html
    assert "Supply of hospital beds" not in html

    empty = client.get("/tenders/?q=zzz-nothing-matches").content.decode()
    assert "No process matches those filters" in empty
    # An empty result must offer a way out rather than a dead end.
    assert 'href="/tenders/"' in empty


@pytest.mark.django_db
def test_csv_export_matches_the_filtered_page(dataset):
    client = Client()
    csv_response = client.get("/tenders/export.csv?status=CONTRACTED")
    rows = csv_response.content.decode().strip().splitlines()
    # header + one matching contracted tender
    assert len(rows) == 2, rows
    assert "Supply of hospital beds" in rows[1]
    assert "Rehabilitation of Bali boreholes" not in rows[1]


@pytest.mark.django_db
def test_whistleblower_errors_preserve_input_and_focus_the_summary(dataset):
    client = Client()
    response = client.post(
        "/tenders/whistleblower/",
        {"body": "too short", "tender_ocid": "TAR-MOH-2026-0001", "contact": ""},
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert 'class="error-summary"' in html, "no error summary"
    assert 'role="alert"' in html, "the error summary is not announced"
    assert "too short" in html, "the reporter's text was thrown away"
    assert "TAR-MOH-2026-0001" in html, "the reporter's reference was thrown away"
    assert 'aria-invalid="true"' in html, "the failing field is not marked invalid"
    # The summary repeats the field's own wording verbatim (GOV.UK error
    # summary pattern): once as the link, once beside the field.
    assert html.count("Add a little more detail") == 2


@pytest.mark.django_db
def test_whistleblower_report_is_stored_encrypted_and_returns_a_reference(dataset):
    from workflow.models import WhistleblowerReport

    body = "A tender for boreholes in Bali was advertised for three days only, then awarded."
    response = Client().post("/tenders/whistleblower/", {"body": body, "tender_ocid": "", "contact": ""})
    assert response.status_code == 200
    report = WhistleblowerReport.objects.latest("submitted_at")
    assert report.ref.startswith("WB-")
    assert body not in report.body_ciphertext, "the report was stored in clear text"
    assert report.ref in response.content.decode()


@pytest.mark.django_db
def test_404_page_offers_recovery_links(dataset):
    response = Client().get("/tenders/definitely-not-a-page/")
    assert response.status_code == 404
    html = response.content.decode()
    assert "/tenders/" in html and "/tenders/open-data/" in html


@pytest.mark.django_db
def test_ledger_status_page_states_the_chain_verdict(dataset):
    html = Client().get("/tenders/status/").content.decode()
    assert "ledger chain" in html.lower()
    assert ("Valid" in html) or ("Broken" in html)


# ------------------------------------------------------------------ performance
@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/", "/tenders/", "/tenders/?q=hospital"])
def test_html_stays_inside_the_page_budget(url, dataset):
    """Budget: < 100 KB of HTML for a list page. On a 3G phone this is the
    difference between a page a bidder reads and a page they abandon."""
    body = Client().get(url).content
    assert len(body) < 100_000, f"{url} served {len(body)} bytes of HTML"


def test_stylesheet_is_small_and_built_from_source():
    css = css_build.built_css()
    assert len(css.encode()) < 40_000, "the stylesheet grew past its budget"
    # The committed artifact must match the sources, or production serves a
    # stale design. `manage.py buildcss` regenerates it.
    assert css_build.built_css() == css_build.build_css(), (
        "static/css/app.css is stale — run `python manage.py buildcss`"
    )


def test_stylesheet_is_served_content_addressed():
    response = Client().get("/stylesheet")
    assert response.status_code == 200
    assert response["Cache-Control"].startswith("public")
    assert response["ETag"]
    assert response["X-Content-Type-Options"] == "nosniff"


# ------------------------------------------------- the content checker itself
def test_content_checker_catches_what_status_codes_cannot():
    """`audit_pages` proves a page renders. These are the failures that render
    successfully and still waste a reader's time."""
    from core.html_audit import check

    page = """
    <html><body>
      <h1>Register</h1><h1>Second heading</h1>
      <a href="#nowhere">Jump</a><a href="#">Nowhere</a>
      <table><tr><th>Value</th><td>{'version': 1, 'kind': 'BOQ'}</td><td></td></tr></table>
      <div id="dup"></div><div id="dup"></div>
      <input name="amount">
      <p aria-labelledby="ghost">Label</p>
      <p>None</p>
    </body></html>
    """
    found = {f.check for f in check(page)}
    assert {
        "h1-count", "dangling-anchor", "dead-link", "table-caption", "table-scope",
        "empty-cell", "duplicate-id", "unlabelled-control", "dangling-reference",
        "raw-value", "missing-value",
    } <= found


def test_content_checker_accepts_a_clean_page():
    from core.html_audit import check

    page = """
    <html lang="en"><body><h1>Register</h1>
      <table><caption>Bids</caption>
        <tr><th scope="col">Supplier</th><th scope="col">Amount</th></tr>
        <tr><th scope="row">Acme Ltd</th><td>₦12,400,000</td></tr>
      </table>
      <form><label for="q">Search</label><input id="q" name="q"></form>
      <p>None of the tenders in this list are open.</p>
    </body></html>
    """
    assert check(page) == []


@pytest.mark.django_db
def test_no_public_page_leaks_a_python_value(dataset):
    """The rendered pages themselves, checked with the same parser the crawl uses."""
    from core.html_audit import check

    client = Client()
    for url in PUBLIC_PAGES:
        findings = [f for f in check(client.get(url).content.decode()) if f.check == "raw-value"]
        assert not findings, f"{url}: {findings}"


def test_ledger_payload_reads_as_facts_not_as_a_dict():

    from core.templatetags.core_tags import ledger_payload

    rendered = ledger_payload(
        {
            "ocid": "TAR-MOH-2026-0001",
            "estimate": "120000000",
            "published_at": "2026-08-07T12:21:58.641953+00:00",
            "sha256": "a" * 64,
            "extends_deadline": False,
            "members": [{"role": "pde"}, {"role": "dg"}],
        }
    )
    assert "OCID" in rendered and "TAR-MOH-2026-0001" in rendered
    assert "₦120,000,000" in rendered, "an estimate should be money, not a bare number"
    assert "07 Aug 2026" in rendered, "a timestamp should be a date, not an ISO string"
    assert "aaaa…" in rendered or "a" * 8 in rendered, "a hash should be shortened, not dumped"
    assert "No" in rendered, "a boolean should read as a word"
    assert "pde, dg" in rendered, "a member list should name the members"
    assert "{'" not in rendered and "True" not in rendered


def test_ledger_payload_handles_an_empty_or_odd_payload():
    from core.templatetags.core_tags import ledger_payload

    assert "no recorded detail" in ledger_payload({})
    assert "no recorded detail" in ledger_payload(None)
    assert "unexpected" in ledger_payload("unexpected")


# --------------------------------------------------------- template hygiene
@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/", "/tenders/", "/tenders/whistleblower/", "/tenders/awards/"])
def test_no_template_syntax_leaks_into_the_page(url, dataset):
    """Django's `{# #}` comment is single-line only. A multi-line one is rendered
    as visible text, which is how a "comment" ends up on a public page."""
    html = Client().get(url).content.decode()
    for leaked in ("{#", "#}", "{%", "%}", "{{", "}}"):
        assert leaked not in html, f"{url} renders the literal template syntax {leaked!r}"


@pytest.mark.django_db
def test_no_python_value_reaches_the_page(dataset):
    """A raw dict or repr in a page means a template printed a data structure
    instead of formatting it — the failure mode that still returns HTTP 200."""
    from core.html_audit import check

    client = Client()
    for url in PUBLIC_PAGES:
        checks = {f.check for f in check(client.get(url).content.decode())}
        assert "raw-value" not in checks, f"{url} renders a Python value"
        assert "missing-value" not in checks, f"{url} prints a missing value as a literal"
