"""Crawl every public page and report what a visitor would actually get.

The failure this command exists to catch is the one that looks like success:
a page that returns HTTP 200 with blank figures, or a route that 500s only for
rows that exist in production and not in the developer's sample. So the crawl
resolves each route against **real objects in the current database** and prints
a table of status codes, plus the underlying exception for anything that is not
200/302/404.

Two passes:

1. **Status** — is the route reachable and does it render without raising?
2. **Content** — page by page, is what came back actually readable? Empty table
   cells, leaked Python values, `None` where a number belongs, tables with no
   caption, duplicate ids, dangling anchors and unlabelled inputs are reported
   per URL. See `core.html_audit` for the checks and why they exist.

Usage:
    python manage.py audit_pages                  # status + content
    python manage.py audit_pages --verbose        # every URL
    python manage.py audit_pages --show-exceptions
    python manage.py audit_pages --warn-only      # content findings do not fail
"""
from __future__ import annotations

import traceback as tb_module

from django.core.management.base import BaseCommand
from django.test import Client
from django.urls import reverse, NoReverseMatch


class Command(BaseCommand):
    help = "Fetch every public URL against real data and report the status codes."

    def add_arguments(self, parser):
        # NOTE: `--traceback` is already taken by Django's BaseCommand.
        parser.add_argument(
            "--show-exceptions", action="store_true",
            help="Print the underlying exception for each failure",
        )
        parser.add_argument("--verbose", action="store_true", help="List every URL, not just failures")
        parser.add_argument(
            "--warn-only", action="store_true",
            help="Report content findings but do not fail the command",
        )

    def handle(self, *args, **options):
        from procurement.models import (
            Award, Contract, Objection, Tender,
        )
        from procurement.models_additional import DefectReport, ReverseAuction
        from procurement.models_party import Agency, Party
        from workflow.models import CatalogueItem, PurchaseOrder, WhistleblowerReport
        from workflow.models_phases import DebriefRequest

        def first(model, **filters):
            return model.objects.filter(**filters).order_by("pk").first()

        tender = first(Tender, status="AWARDED") or first(Tender)
        award = first(Award) 
        contract = first(Contract) or (getattr(award, "contract", None) if award else None)
        party = first(Party)
        agency = first(Agency)
        objection = first(Objection)
        catalogue = first(CatalogueItem)
        po = first(PurchaseOrder)
        auction = first(ReverseAuction)
        defect_contract = first(DefectReport)
        whistle = first(WhistleblowerReport)

        # (url, note). Anything whose object does not exist in this database is
        # skipped with a printed note rather than reported as a failure.
        routes: list[tuple[str | None, str]] = [
            ("/", "landing page"),
            ("/tenders/", "tender register"),
            ("/tenders/?q=road&status=PUBLISHED&sort=value", "register with filters"),
            ("/tenders/?page=2", "register, page 2"),
            ("/tenders/export.csv", "register as CSV"),
            ("/tenders/awards/", "award register"),
            ("/tenders/awards/?year=2026", "award register filtered"),
            ("/tenders/contracts/", "contracts"),
            ("/tenders/contracts/?status=SIGNED", "contracts filtered"),
            ("/tenders/suppliers/", "suppliers"),
            ("/tenders/suppliers/?q=a", "suppliers filtered"),
            ("/tenders/rules/", "rules and thresholds"),
            ("/tenders/help/", "help"),
            ("/tenders/indicators/", "red-flag indicators"),
            ("/tenders/open-data/", "open data"),
            ("/tenders/status/", "service status"),
            ("/tenders/mdas/", "MDA utilisation"),
            ("/tenders/ratings/", "supplier ratings"),
            ("/tenders/payments/", "payment certifications"),
            ("/tenders/agent-desk/", "assisted bidding desks"),
            ("/tenders/register/", "supplier registration start"),
            ("/tenders/register/form/", "supplier registration form"),
            ("/tenders/register/review/", "registration review queue"),
            ("/tenders/catalogue/", "catalogue"),
            ("/tenders/purchase-orders/", "purchase orders"),
            ("/tenders/auctions/", "reverse auctions"),
            ("/tenders/defects/", "defects liability"),
            ("/tenders/whistleblower/", "whistleblower intake"),
            ("/tenders/stylesheet", "stylesheet alias (redirects)"),
            ("/stylesheet", "stylesheet"),
            ("/robots.txt", "robots"),
            ("/sitemap.xml", "sitemap"),
            ("/redirects", "retired-URL map"),
            ("/accounts/login/", "staff sign-in"),
            ("/api/v1/stats", "API stats"),
            ("/api/v1/tenders", "API tenders"),
            ("/api/v1/awards", "API awards"),
            ("/api/v1/contracts", "API contracts"),
            ("/api/v1/suppliers", "API suppliers"),
            ("/api/v1/releases", "API releases"),
            ("/api/v1/ledger/head", "API ledger head"),
            ("/api/v1/indicators", "API indicators"),
            ("/api/v1/policy/access", "API access policy"),
            ("/api/v1/schema", "OpenAPI schema"),
            ("/tenders/feed/tenders/", "tender RSS"),
            ("/tenders/feed/awards/", "award RSS"),
        ]

        def add(name: str, note: str, **kwargs):
            if not all(v is not None for v in kwargs.values()):
                if options["verbose"]:
                    self.stdout.write(f"  · skipped {note}: no {note.split()[0]} in this database")
                return
            try:
                routes.append((reverse(name, kwargs=kwargs), note))
            except NoReverseMatch as exc:
                routes.append((None, f"{note} — NoReverseMatch: {exc}"))

        if tender:
            add("tender-detail", "tender page", ocid=tender.ocid)
            add("tender-ocds", "OCDS release", ocid=tender.ocid)
            add("tender-document", "document metadata", ocid=tender.ocid,
                doc_id=getattr(tender.documents.first(), "pk", None))
            add("bid-submit", "bid submission", ocid=tender.ocid)
            add("evaluation", "evaluation workspace", ocid=tender.ocid)
            add("evaluation-report", "evaluation report", ocid=tender.ocid)
            add("objections", "objections for a tender", ocid=tender.ocid)
            add("debriefs", "debrief log", ocid=tender.ocid)
            if award:
                add("objection-file", "file an objection", ocid=tender.ocid, award_id=award.pk)
        if contract:
            add("contract-detail", "contract page", reference=contract.reference)
            add("contract-workspace", "delivery workspace", reference=contract.reference)
            add("contract-milestones", "milestones", reference=contract.reference)
            add("contract-variations", "variations", reference=contract.reference)
        if party:
            add("supplier-detail", "supplier page", pk=party.pk)
        if agency and agency.code:
            add("mda-detail", "MDA page", mda_code=agency.code)
        if objection:
            add("objection-detail", "objection file", pk=objection.pk)
        if catalogue:
            add("catalogue-detail", "catalogue item", catalogue_id=catalogue.pk)
        if po:
            add("po-detail", "purchase order", po_id=po.pk)
        if auction:
            add("auction-detail", "reverse auction", auction_id=auction.pk)
        if defect_contract:
            add("defects-detail", "defects for a contract",
                contract_id=defect_contract.contract_id)
        if whistle:
            add("whistleblower-status", "whistleblower status", reference=whistle.ref)

        # Every row, not one sample row. A payload leak, a blank cell or a missing
        # relation shows up on the record that happens to have that data, so the
        # sample row is exactly the wrong thing to check.
        # Public processes only: a draft must not be reachable, so asking for it
        # would report a correct 404 as a finding.
        for extra in Tender.objects.public().order_by("pk")[:20]:
            add("tender-detail", f"tender {extra.ocid}", ocid=extra.ocid)
        for extra in Contract.objects.order_by("pk")[:12]:
            add("contract-detail", f"contract {extra.reference}", reference=extra.reference)
            add("contract-milestones", f"milestones {extra.reference}", reference=extra.reference)
            add("contract-variations", f"variations {extra.reference}", reference=extra.reference)
        for extra in Party.objects.order_by("pk")[:12]:
            add("supplier-detail", f"supplier {extra.legal_name}", pk=extra.pk)
        for extra in Agency.objects.order_by("pk")[:12]:
            add("mda-detail", f"MDA {extra.code}", mda_code=extra.code)
        for extra in Objection.objects.order_by("pk")[:6]:
            add("objection-detail", f"objection {extra.pk}", pk=extra.pk)

        from core.html_audit import check as content_check

        client = Client()
        failures = []
        content_findings: list[tuple[str, str]] = []
        counts = {"ok": 0, "redirect": 0, "missing": 0}
        self.stdout.write(f"Auditing {len(routes)} URLs against the current database\n")
        for url, note in routes:
            if url is None:
                failures.append((note, "no URL"))
                self.stderr.write(self.style.ERROR(f"  ✗ {note}"))
                continue
            try:
                response = client.get(url)
                status = response.status_code
            except Exception as exc:  # noqa: BLE001 - the point is to report it
                failures.append((url, f"{type(exc).__name__}: {exc}"))
                self.stderr.write(self.style.ERROR(f"  ✗ {url} — {type(exc).__name__}: {exc}"))
                if options["show_exceptions"]:
                    self.stderr.write(tb_module.format_exc())
                continue

            if status == 200:
                counts["ok"] += 1
                if options["verbose"]:
                    self.stdout.write(f"  ✓ {url} — {note}")
                if "html" in response.headers.get("Content-Type", ""):
                    for finding in content_check(response.content.decode("utf-8", "replace")):
                        content_findings.append((url, str(finding)))
            elif status in (301, 302):
                counts["redirect"] += 1
                if options["verbose"]:
                    self.stdout.write(f"  → {url} — {note} ({response.url})")
            elif status == 404:
                counts["missing"] += 1
                if options["verbose"]:
                    self.stdout.write(f"  ∅ {url} — {note} (404)")
            else:
                failures.append((url, str(status)))
                self.stderr.write(self.style.ERROR(f"  ✗ {url} — {note}: HTTP {status}"))

        self.stdout.write("")
        self.stdout.write(
            f"200: {counts['ok']}   3xx: {counts['redirect']}   404: {counts['missing']}   "
            f"failures: {len(failures)}"
        )
        self.stdout.write("")
        if content_findings:
            style = self.style.WARNING if options["warn_only"] else self.style.ERROR
            self.stdout.write(style(f"Content findings: {len(content_findings)}"))
            for url, why in content_findings:
                self.stdout.write(f"  · {url} — {why}")
        else:
            self.stdout.write(self.style.SUCCESS("Content: no findings"))

        if failures:
            self.stderr.write(self.style.ERROR("\nFailures:"))
            for url, why in failures:
                self.stderr.write(self.style.ERROR(f"  {url} — {why}"))
            raise SystemExit(1)
        if content_findings and not options["warn_only"]:
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("✓ every crawled page rendered, and reads cleanly"))
