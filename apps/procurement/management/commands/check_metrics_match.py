"""Management command to verify homepage metrics match the database.

This is called in CI to ensure no unbacked claims are displayed.
"""
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.db.models import Sum
from decimal import Decimal
import json


class Command(BaseCommand):
    help = "Verify that homepage metrics match the database"

    def handle(self, *args, **options):
        self.stdout.write("Checking homepage metrics integrity...")
        
        from procurement.models import Tender, Award
        from procurement.models_party import Party, PartyVerification
        from django.utils import timezone
        
        # Query database
        open_tenders_db = Tender.objects.filter(
            status="PUBLISHED",
            submission_close_at__gt=timezone.now()
        ).count()
        
        awards_db = Award.objects.filter(
            status__in=["PUBLISHED", "CONTRACTED"]
        ).count()
        
        total_value_db = Award.objects.filter(
            status__in=["PUBLISHED", "CONTRACTED"]
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        verified_suppliers_db = PartyVerification.objects.filter(
            kind="CAC",
            status="PASSED"
        ).values("party").distinct().count()
        
        # Get stats API
        client = Client()
        response = client.get("/api/v1/stats")
        
        if response.status_code != 200:
            raise CommandError(f"Stats API returned {response.status_code}")
        
        stats = json.loads(response.content)
        
        # Verify matches
        errors = []
        
        if stats.get("open_tenders") != open_tenders_db:
            errors.append(
                f"open_tenders mismatch: API={stats.get('open_tenders')}, "
                f"DB={open_tenders_db}"
            )
        
        if stats.get("awards_count") != awards_db:
            errors.append(
                f"awards_count mismatch: API={stats.get('awards_count')}, "
                f"DB={awards_db}"
            )
        
        # Total value comparison (allow small float differences)
        api_value = Decimal(str(stats.get("total_award_value", 0)))
        if abs(api_value - total_value_db) > Decimal("0.01"):
            errors.append(
                f"total_award_value mismatch: API={api_value}, "
                f"DB={total_value_db}"
            )
        
        if stats.get("verified_suppliers") != verified_suppliers_db:
            errors.append(
                f"verified_suppliers mismatch: API={stats.get('verified_suppliers')}, "
                f"DB={verified_suppliers_db}"
            )
        
        if errors:
            for error in errors:
                self.stderr.write(self.style.ERROR(error))
            raise CommandError(f"{len(errors)} metric mismatches found")
        
        self.stdout.write(self.style.SUCCESS(
            f"✓ All metrics match\n"
            f"  Open tenders: {open_tenders_db}\n"
            f"  Awards: {awards_db}\n"
            f"  Total value: ₦{total_value_db:,.2f}\n"
            f"  Verified suppliers: {verified_suppliers_db}"
        ))
