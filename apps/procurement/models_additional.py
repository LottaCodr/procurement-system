"""Additional models for advanced procurement features.

Includes:
- Catalogue fast-lane (common-use goods)
- Reverse auctions
- Defects liability tracking
- Contract close-out
"""
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from procurement.models import Tender, Contract
from procurement.models_party import Party as Supplier
import secrets


class CatalogueItem(models.Model):
    """Common-use goods available for fast-lane procurement."""
    
    CATEGORY_CHOICES = [
        ('MEDICINE', 'Medicines & Medical Supplies'),
        ('FURNITURE', 'Office Furniture'),
        ('ICT', 'ICT Equipment'),
        ('VEHICLES', 'Vehicles'),
        ('STATIONERY', 'Stationery & Office Supplies'),
        ('CLEANING', 'Cleaning Supplies'),
        ('OTHER', 'Other'),
    ]
    
    name = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    unit_of_measure = models.CharField(max_length=50, help_text="e.g., piece, box, kg")
    specifications = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['category', 'name']
        indexes = [
            models.Index(fields=['category', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class CatalogueQuote(models.Model):
    """Supplier quote for a catalogue item."""
    
    item = models.ForeignKey(CatalogueItem, on_delete=models.CASCADE, related_name='quotes')
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='catalogue_quotes')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default='NGN')
    lead_time_days = models.PositiveIntegerField(help_text="Delivery lead time in days")
    valid_until = models.DateField()
    terms = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['item', 'supplier']
        ordering = ['unit_price']
    
    def __str__(self):
        return f"{self.supplier.name} - {self.item.name}: ₦{self.unit_price}"


class PurchaseOrder(models.Model):
    """Purchase order created from catalogue fast-lane."""
    
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('AWARDED', 'Awarded'),
        ('CONFIRMED', 'Confirmed'),
        ('DELIVERED', 'Delivered'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    po_number = models.CharField(max_length=50, unique=True)
    catalogue_item = models.ForeignKey(CatalogueItem, on_delete=models.PROTECT)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    selected_quote = models.ForeignKey(CatalogueQuote, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    delivery_location = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    awarded_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchase_orders')
    
    class Meta:
        ordering = ['-created_at']
    
    def save(self, *args, **kwargs):
        if not self.po_number:
            self.po_number = f"PO-{secrets.token_hex(4).upper()}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.po_number}: {self.catalogue_item.name} x {self.quantity}"


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
        return f"{self.supplier.name}: ₦{self.amount}"


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
        return f"Defect #{self.id} - {self.contract.contract_number}"


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
        return f"Close-out: {self.contract.contract_number} ({self.performance_rating})"
