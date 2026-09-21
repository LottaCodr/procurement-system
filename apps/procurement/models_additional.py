"""Additional procurement models: reverse auctions and defects liability.

Deliberately NOT in here: catalogue items, catalogue quotes and purchase orders.
Those are defined once, in :mod:`workflow.models` (tables `cat_item`,
`cat_quote`, `cat_po`). An earlier revision of this file re-declared them with
different fields under different tables, which produced two competing
definitions of "catalogue" and a set of tables nothing could read. One concept,
one model, one table.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from procurement.models import Contract, Tender
from procurement.models_party import Party as Supplier


class ReverseAuction(models.Model):
    """Reverse auction for electronic price discovery."""

    tender = models.OneToOneField(Tender, on_delete=models.CASCADE, related_name='auction')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reserve_price = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Minimum acceptable price"
    )
    is_active = models.BooleanField(default=True)
    winning_bid = models.ForeignKey(
        'AuctionBid', on_delete=models.SET_NULL, null=True, blank=True
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-starts_at']

    def __str__(self):
        return f"Auction for {self.tender.ocid}"


class AuctionBid(models.Model):
    """Bid placed in a reverse auction."""

    auction = models.ForeignKey(ReverseAuction, on_delete=models.CASCADE, related_name='bids')
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='auction_bids')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    bid_at = models.DateTimeField(auto_now_add=True)
    is_valid = models.BooleanField(default=True)

    class Meta:
        ordering = ['amount', 'bid_at']

    def __str__(self):
        return f"{self.supplier.legal_name}: ₦{self.amount}"


class DefectReport(models.Model):
    """Defect reported during defects liability period."""

    SEVERITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ]

    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('IN_PROGRESS', 'In Progress'),
        ('RESOLVED', 'Resolved'),
        ('VERIFIED', 'Verified'),
    ]

    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='defects')
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    reported_at = models.DateTimeField(auto_now_add=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='reported_defects'
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='verified_defects'
    )

    class Meta:
        ordering = ['-reported_at']

    def __str__(self):
        return f"Defect #{self.id} — contract {self.contract.reference}"


class ContractCloseOut(models.Model):
    """Formal contract close-out with performance evaluation."""

    PERFORMANCE_CHOICES = [
        ('EXCELLENT', 'Excellent'),
        ('SATISFACTORY', 'Satisfactory'),
        ('POOR', 'Poor'),
        ('UNSATISFACTORY', 'Unsatisfactory'),
    ]

    contract = models.OneToOneField(Contract, on_delete=models.CASCADE, related_name='close_out')
    performance_rating = models.CharField(max_length=20, choices=PERFORMANCE_CHOICES)
    comments = models.TextField(blank=True)
    closed_out_at = models.DateTimeField(auto_now_add=True)
    closed_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='close_outs'
    )

    class Meta:
        ordering = ['-closed_out_at']

    def __str__(self):
        return f"Close-out: {self.contract.reference} ({self.performance_rating})"
