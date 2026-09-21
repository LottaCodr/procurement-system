"""Management command for annual supplier re-verification.

Design requirement: "Suppliers are never 'approved once, trusted forever':
re-verify annually, auto-suspend on expiry"
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from procurement.models_party import Party as Supplier, PartyVerification as SupplierVerification
from workflow.notifications import get_sms_backend, get_email_backend


class Command(BaseCommand):
    help = 'Re-verify suppliers annually and suspend those with expired documents'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--notify',
            action='store_true',
            help='Send notifications to suppliers with expiring documents',
        )
        parser.add_argument(
            '--days-before-expiry',
            type=int,
            default=30,
            help='Days before expiry to send reminder (default: 30)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        notify = options['notify']
        days_before = options['days_before_expiry']
        
        today = timezone.now().date()
        reminder_date = today + timedelta(days=days_before)
        
        self.stdout.write(self.style.SUCCESS(
            f'Starting annual supplier re-verification (dry_run={dry_run})'
        ))
        
        # Find suppliers with expired verifications
        expired_verifications = SupplierVerification.objects.filter(
            expires_at__lt=today,
            party__is_active=True,
        ).select_related('party')
        
        expired_suppliers = set(v.party for v in expired_verifications)
        
        self.stdout.write(f'Found {len(expired_suppliers)} suppliers with expired verifications')
        
        # Suspend suppliers with expired documents
        suspended_count = 0
        for supplier in expired_suppliers:
            if dry_run:
                self.stdout.write(f'  Would suspend: {supplier.name} ({supplier.rc_number})')
            else:
                supplier.is_active = False
                supplier.save(update_fields=['is_active'])
                self.stdout.write(self.style.WARNING(
                    f'  Suspended: {supplier.name} ({supplier.rc_number})'
                ))
            suspended_count += 1
        
        # Find suppliers with expiring documents (within reminder window)
        expiring_verifications = SupplierVerification.objects.filter(
            expires_at__gte=today,
            expires_at__lte=reminder_date,
            party__is_active=True,
        ).select_related('party')
        
        expiring_suppliers = {}
        for v in expiring_verifications:
            if v.party not in expiring_suppliers:
                expiring_suppliers[v.party] = []
            expiring_suppliers[v.party].append(v)
        
        self.stdout.write(f'Found {len(expiring_suppliers)} suppliers with expiring documents')
        
        # Send notifications
        notified_count = 0
        if notify:
            sms_backend = get_sms_backend()
            email_backend = get_email_backend()
            
            for supplier, verifications in expiring_suppliers.items():
                expiring_docs = [f"{v.verification_type} (expires {v.expiry_date})" for v in verifications]
                
                message = (
                    f"Taraba Procurement: Your supplier registration documents will expire soon:\n"
                    f"{chr(10).join(expiring_docs)}\n"
                    f"Please renew at https://procurement.taraba.gov.ng/supplier/profile/"
                )
                
                if dry_run:
                    self.stdout.write(f'  Would notify: {supplier.name}')
                else:
                    # Try SMS first
                    if supplier.phone:
                        result = sms_backend.send(supplier.phone, message, channel='SMS')
                        if result.get('success'):
                            self.stdout.write(f'  SMS sent to {supplier.name}')
                    
                    # Also try email
                    if supplier.email:
                        result = email_backend.send(
                            supplier.email,
                            message,
                            subject='Action Required: Supplier Documents Expiring Soon'
                        )
                        if result.get('success'):
                            self.stdout.write(f'  Email sent to {supplier.name}')
                    
                    notified_count += 1
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\nRe-verification complete:'))
        self.stdout.write(f'  Suppliers suspended: {suspended_count}')
        self.stdout.write(f'  Suppliers notified: {notified_count}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nThis was a dry run. No changes were made.'
            ))
