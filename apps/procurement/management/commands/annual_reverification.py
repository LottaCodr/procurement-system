"""Annual supplier re-verification — the nightly cron entry.

Design requirement (doc 3.2): suppliers are never "approved once, trusted
forever". Re-verify annually, auto-suspend on expiry, and warn before expiry.

Two jobs, in order:

1. **Suspend** firms whose core credentials (CAC, TIN, pension) have lapsed.
   A lapsed tax clearance proves nothing, so the register stops showing the
   firm as verified instead of carrying a stale badge. The suspension is a
   ledger event, so the register can show *when* and *why* a firm disappeared.
2. **Warn** firms whose credentials lapse within the reminder window, by SMS
   and email, so the suspension is never a surprise.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from procurement.models_party import PartyVerification
from workflow.models import Notification
from workflow.services import _send_notification, suspend_expired_suppliers


class Command(BaseCommand):
    help = "Suspend suppliers whose credentials expired; warn those expiring soon"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would happen without changing anything.")
        parser.add_argument("--notify", action="store_true",
                            help="Also send expiry warnings for credentials inside the window.")
        parser.add_argument("--days-before-expiry", type=int, default=30,
                            help="Warning window in days (default: 30).")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        today = timezone.localdate()
        window = today + timedelta(days=options["days_before_expiry"])

        lapsed = PartyVerification.objects.filter(
            kind__in=[PartyVerification.Kind.CAC, PartyVerification.Kind.TIN, PartyVerification.Kind.PENSION],
            status=PartyVerification.PASSED, expires_at__lt=today, party__is_active=True,
        ).select_related("party")
        lapsed_parties = {v.party for v in lapsed}
        self.stdout.write(f"Suppliers with lapsed core credentials: {len(lapsed_parties)}")
        for p in sorted(lapsed_parties, key=lambda x: x.legal_name):
            self.stdout.write(f"  - {p.legal_name} ({p.rc_number})")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing suspended."))
        else:
            suspended = suspend_expired_suppliers()
            for p in suspended:
                self.stdout.write(self.style.WARNING(f"  Suspended: {p.legal_name} ({p.rc_number})"))

        expiring = PartyVerification.objects.filter(
            status=PartyVerification.PASSED, expires_at__gte=today, expires_at__lte=window,
            party__is_active=True,
        ).select_related("party")
        by_party: dict = {}
        for v in expiring:
            by_party.setdefault(v.party, []).append(v)
        self.stdout.write(f"Suppliers with credentials expiring within {options['days_before_expiry']} days: {len(by_party)}")

        notified = 0
        for party, verifications in by_party.items():
            lines = ", ".join(f"{v.get_kind_display()} ({v.expires_at})" for v in verifications)
            body = (f"Taraba BPP: your supplier credentials expire soon: {lines}. "
                    "Renew them and re-register on the portal to stay eligible to bid.")
            self.stdout.write(f"  - {party.legal_name}: {lines}")
            if not dry_run and options["notify"]:
                _send_notification(kind=Notification.Kind.DEADLINE_REMINDER, channel=Notification.Channel.EMAIL,
                                   recipient_email=party.email, recipient_phone=party.phone,
                                   body=body, reference_key=f"party:{party.pk}")
                if party.phone:
                    _send_notification(kind=Notification.Kind.DEADLINE_REMINDER, channel=Notification.Channel.SMS,
                                       recipient_phone=party.phone, body=body, reference_key=f"party:{party.pk}")
                notified += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. Suspended: {0 if dry_run else len(lapsed_parties)}, warned: {notified}."))
