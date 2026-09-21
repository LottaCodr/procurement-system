"""Verify that every figure the public site prints is produced by the database.

The rule this enforces is the project's first axiom about honesty: **no number
appears on the site that a query did not produce.** A transparency portal with
unbacked headline figures is worse than no portal at all, because it trains
people to distrust the numbers that *are* real.

Three comparisons, all of them against the live database:

1. `/api/v1/stats` — the machine-readable copy of the figures — must agree with
   an independent aggregate computed here, using the **same definitions** the
   site uses. The definitions live in one place (`live_metrics`, `TenderQuerySet
   .open()`), and this command deliberately re-derives them from raw queries so
   a definition that drifts is caught rather than agreed with.
2. The landing page must print exactly those figures. A matching API that the
   page ignores would be a different bug, and this catches it.
3. The counts must be self-consistent (no verified suppliers out of a smaller
   total, no awards worth more than nothing).

Run in CI, and by hand after a change to a headline figure:

    python manage.py check_metrics_match
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.test import Client
from django.utils import timezone

KPI_VALUE = re.compile(r'<span class="kpi__value">\s*([\d,]+)')


class Command(BaseCommand):
    help = "Verify that every headline figure on the site matches the database."

    def handle(self, *args, **options):
        from procurement.models import Award, Contract, Tender
        from procurement.models_party import Agency, Party, PartyVerification

        self.stdout.write("Checking homepage metrics integrity...")

        now = timezone.now()
        expected = {
            # Definitions copied from the site, deliberately written out rather
            # than imported: if someone changes the definition, this command
            # should disagree with the page and say so.
            "open_tenders": Tender.objects.filter(
                status__in=["PUBLISHED", "CLARIFYING"], submission_close_at__gt=now
            ).count(),
            "awards_published": Award.objects.filter(
                status__in=["PUBLISHED", "CONTRACTED"]
            ).count(),
            "contracts_signed": Contract.objects.count(),
            "suppliers_verified": Party.objects.filter(
                verifications__kind=PartyVerification.Kind.CAC,
                verifications__status="PASSED",
            )
            .distinct()
            .count(),
            "suppliers_total": Party.objects.filter(is_active=True).count(),
            "mdas_total": Agency.objects.filter(is_active=True).count(),
        }
        total_award_value = Award.objects.filter(
            status__in=["PUBLISHED", "CONTRACTED"]
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

        errors: list[str] = []

        client = Client()

        # ---- 1. the API -------------------------------------------------
        response = client.get("/api/v1/stats")
        if response.status_code != 200:
            raise CommandError(f"Stats API returned {response.status_code}")
        stats = json.loads(response.content)

        for key, value in expected.items():
            if stats.get(key) != value:
                errors.append(f"/api/v1/stats {key}: API={stats.get(key)!r}, DB={value}")

        api_value = Decimal(str(stats.get("total_award_value", 0)))
        if abs(api_value - total_award_value) > Decimal("0.01"):
            errors.append(
                f"/api/v1/stats total_award_value: API={api_value}, DB={total_award_value}"
            )

        # ---- 2. the page ------------------------------------------------
        home = client.get("/")
        if home.status_code != 200:
            raise CommandError(f"Homepage returned {home.status_code}")
        printed = [int(raw.replace(",", "")) for raw in KPI_VALUE.findall(home.content.decode())]
        wanted = [
            expected["open_tenders"],
            expected["awards_published"],
            expected["contracts_signed"],
            expected["suppliers_verified"],
        ]
        if printed[:4] != wanted:
            errors.append(
                "homepage KPI figures do not match the database: "
                f"page={printed[:4]}, DB={wanted}"
            )

        # ---- 3. internal consistency ------------------------------------
        if expected["suppliers_verified"] > expected["suppliers_total"]:
            errors.append(
                f"{expected['suppliers_verified']} verified suppliers out of "
                f"{expected['suppliers_total']} total"
            )
        awards_total = Award.objects.filter(status__in=["PUBLISHED", "CONTRACTED"]).count()
        if awards_total == 0 and total_award_value != Decimal("0"):
            errors.append("awards total ₦0 but the sum of awards is not zero")

        if errors:
            for error in errors:
                self.stderr.write(self.style.ERROR(error))
            raise CommandError(f"{len(errors)} metric mismatch(es) found")

        self.stdout.write(
            self.style.SUCCESS(
                "✓ Every headline figure matches the database\n"
                f"  open tenders:       {expected['open_tenders']}\n"
                f"  awards published:   {expected['awards_published']}\n"
                f"  total awarded:      ₦{total_award_value:,.2f}\n"
                f"  contracts signed:   {expected['contracts_signed']}\n"
                f"  suppliers verified: {expected['suppliers_verified']}"
                f"/{expected['suppliers_total']}"
            )
        )
