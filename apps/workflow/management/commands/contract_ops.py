"""Management command for Phase 4 contract lifecycle operations.

Usage:
  python manage.py contract_ops --list-contracts
  python manage.py contract_ops --check-guarantees
  python manage.py contract_ops --check-milestones
  python manage.py contract_ops --rate-all
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Phase 4 contract lifecycle operations"

    def add_arguments(self, parser):
        parser.add_argument("--list-contracts", action="store_true", help="List all contracts with status")
        parser.add_argument("--check-guarantees", action="store_true", help="Check for expiring guarantees")
        parser.add_argument("--check-milestones", action="store_true", help="Check for overdue milestones")
        parser.add_argument("--rate-all", action="store_true", help="Rate all closed contracts without ratings")

    def handle(self, *args, **options):
        from procurement.models import Contract
        from workflow.models_phases import (
            ContractGuarantee, ContractMilestone, SupplierPerformanceRating,
        )

        if options["list_contracts"]:
            contracts = Contract.objects.select_related(
                "award__bid__supplier", "award__tender__agency"
            ).order_by("-signed_at")
            self.stdout.write(f"\n{'Reference':<20} {'Supplier':<30} {'Value':>15} {'Status':<12} {'Var%':>6}")
            self.stdout.write("-" * 90)
            for c in contracts:
                self.stdout.write(
                    f"{c.reference:<20} {c.award.bid.supplier.legal_name[:28]:<30} "
                    f"₦{c.value:>12,.0f} {c.status:<12} {c.variation_pct:>5.1f}%"
                )
            self.stdout.write(f"\nTotal: {contracts.count()} contracts")

        if options["check_guarantees"]:
            expiring = ContractGuarantee.objects.filter(
                released_at__isnull=True,
                expires_at__lte=timezone.localdate() + timedelta(days=30),
            ).select_related("contract__award__bid__supplier")
            self.stdout.write(f"\nGuarantees expiring within 30 days: {expiring.count()}")
            for g in expiring:
                status = "EXPIRED" if g.is_expired else f"{g.days_to_expiry}d left"
                self.stdout.write(
                    f"  {g.get_kind_display()} — {g.reference} — {g.issuer} — "
                    f"₦{g.amount:,.0f} — {status} — "
                    f"Contract: {g.contract.reference}"
                )

        if options["check_milestones"]:
            overdue = ContractMilestone.objects.filter(
                status__in=["PLANNED", "IN_PROGRESS"],
                planned_date__lt=timezone.localdate(),
            ).select_related("contract__award__bid__supplier")
            self.stdout.write(f"\nOverdue milestones: {overdue.count()}")
            for m in overdue:
                self.stdout.write(
                    f"  {m.contract.reference} — {m.title} — "
                    f"planned {m.planned_date} — {m.days_late} days late"
                )

        if options["rate_all"]:
            from workflow.services_phases import rate_supplier_performance
            closed = Contract.objects.filter(
                status__in=[Contract.Status.PAID, Contract.Status.CLOSED]
            ).select_related("award__bid__supplier")
            rated = 0
            for c in closed:
                if not SupplierPerformanceRating.objects.filter(contract=c).exists():
                    try:
                        # Use "system" as rater for auto-rating
                        from procurement.models_party import User
                        sys_user = User.objects.filter(role="ADMIN").first()
                        if sys_user:
                            rate_supplier_performance(
                                c, sys_user, "Auto-computed by contract_ops management command."
                            )
                            rated += 1
                    except Exception as e:
                        self.stderr.write(f"  Failed to rate {c.reference}: {e}")
            self.stdout.write(f"\nRated {rated} contracts.")
