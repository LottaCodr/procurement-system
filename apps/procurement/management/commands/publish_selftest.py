"""Publish → fetch back through the public API → validate. Run this in CI.

Purpose: make a broken transparency promise a build failure. Both Kano and the
current Taraba portal advertise open data that does not resolve; a test like this
is the difference between "we have an API" and "the API returns valid OCDS".
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.test import Client

from procurement.models import Tender
from procurement.ocds.schema import OCDSValidationError, validate_release


class Command(BaseCommand):
    help = "Round-trip every published process through the public read API and validate its OCDS."

    def handle(self, *a, **kw):
        c = Client(follow=False)
        tenders = list(Tender.objects.public())
        if not tenders:
            self.stdout.write(self.style.WARNING("no published tenders exist; nothing to verify — seed data first"))
            raise SystemExit(0)

        failures: list[str] = []

        r = c.get("/api/v1/stats")
        if r.status_code != 200:
            failures.append(f"stats returned {r.status_code}")
        else:
            stats = r.json()
            # The homepage figures and the database may not disagree.
            if stats["awards_published"] != sum(t.awards.filter(status__in=["PUBLISHED", "CONTRACTED"]).count() for t in tenders):
                failures.append("stats.awards_published disagrees with the register")

        for t in tenders:
            for path in (f"/api/v1/tenders/{t.ocid}", f"/tenders/{t.ocid}/", f"/tenders/{t.ocid}/ocds.json"):
                resp = c.get(path)
                if resp.status_code != 200:
                    failures.append(f"{path} -> {resp.status_code}")
            try:
                validate_release(t.ocds_release)
            except (OCDSValidationError, Exception) as exc:  # noqa: BLE001
                failures.append(f"{t.ocid}: {exc}")

            # Anonymous detail must show the estimate — that is the whole reform.
            resp = c.get(f"/api/v1/tenders/{t.ocid}")
            if resp.status_code == 200:
                body = resp.json()
                if not body.get("est_value"):
                    failures.append(f"{t.ocid}: published tender has no disclosed estimate")

        feed = c.get("/api/v1/releases?validate=1")
        if feed.status_code != 200:
            failures.append(f"releases feed -> {feed.status_code}")
        else:
            lines = [l for l in feed.content.decode().splitlines() if l.strip()]
            if len(lines) != len(tenders):
                failures.append(f"releases feed has {len(lines)} lines, expected {len(tenders)}")
            for l in lines:
                json.loads(l)

        bulk = c.get("/api/v1/bulk")
        if bulk.status_code != 200 or not bulk["Content-Type"].startswith("application/zip"):
            failures.append("bulk dump is not a downloadable zip")

        schema = c.get("/api/v1/schema")
        if schema.status_code != 200:
            failures.append("OpenAPI schema missing — the API cannot be self-describing")

        if failures:
            self.stdout.write(self.style.ERROR(f"SELFTEST FAILED ({len(failures)} problems)"))
            for f in failures[:25]:
                self.stdout.write(f"  - {f}")
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(
            f"SELFTEST OK  {len(tenders)} processes round-tripped through the public API, "
            f"all OCDS releases schema-valid, stats reconciled, bulk + OpenAPI served"
        ))
