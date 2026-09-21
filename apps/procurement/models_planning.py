"""Procurement planning models: Annual Plan and Procurement Request.

These models implement the buyer workspace foundation from the design document:
- AnnualProcurementPlan: MDA's yearly procurement plan (required before any tender)
- ProcurementRequest: Individual procurement request within a plan
- Method auto-determination based on threshold rules
- Funds availability check (hard gate: no appropriation, no tender)
"""
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal


class AnnualProcurementPlan(models.Model):
    """MDA's annual procurement plan.
    
    Per PPA 2007 s.7, no procurement may commence unless it is in the approved
    annual plan. This is the foundation of the buyer workspace.
    """
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("SUBMITTED", "Submitted for approval"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
    ]
    
    fiscal_year = models.CharField(max_length=4, db_index=True)
    agency = models.ForeignKey(
        "procurement.Agency",
        on_delete=models.CASCADE,
        related_name="procurement_plans",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    total_budget = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "procurement.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_plans",
    )
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "proc_annual_plan"
        unique_together = ["fiscal_year", "agency"]
        ordering = ["-fiscal_year", "agency__name"]
    
    def __str__(self):
        return f"{self.agency.code} {self.fiscal_year} Plan ({self.status})"
    
    def clean(self):
        if self.status == "APPROVED" and not self.approved_at:
            raise ValidationError("Approved plans must have approved_at timestamp")
    
    @property
    def total_requested(self):
        """Sum of all procurement requests in this plan."""
        return self.requests.aggregate(
            total=models.Sum("estimated_value")
        )["total"] or Decimal("0.00")
    
    @property
    def budget_available(self):
        """Remaining budget after approved requests."""
        approved_total = self.requests.filter(
            status__in=["APPROVED", "IN_PROGRESS", "COMPLETED"]
        ).aggregate(total=models.Sum("estimated_value"))["total"] or Decimal("0.00")
        return self.total_budget - approved_total


class ProcurementRequest(models.Model):
    """Individual procurement request within an annual plan.
    
    This is the starting point for any tender. The request must:
    1. Be part of an approved annual plan
    2. Have funds available (estimated_value <= plan.budget_available)
    3. Have method auto-determined based on threshold rules
    """
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("SUBMITTED", "Submitted"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("IN_PROGRESS", "In Progress (tender created)"),
        ("COMPLETED", "Completed (contract signed)"),
        ("CANCELLED", "Cancelled"),
    ]
    
    plan = models.ForeignKey(
        AnnualProcurementPlan,
        on_delete=models.CASCADE,
        related_name="requests",
    )
    reference = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(
        max_length=50,
        choices=[
            ("GOODS", "Goods"),
            ("WORKS", "Works"),
            ("SERVICES", "Services"),
            ("CONSULTING", "Consulting Services"),
        ],
    )
    estimated_value = models.DecimalField(max_digits=18, decimal_places=2)
    method = models.CharField(
        max_length=20,
        blank=True,  # Auto-determined on save
        help_text="Auto-determined based on threshold rules",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    requested_by = models.ForeignKey(
        "procurement.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="procurement_requests",
    )
    approved_by = models.ForeignKey(
        "procurement.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_requests",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    
    # Link to tender once created
    tender = models.OneToOneField(
        "procurement.Tender",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="procurement_request",
    )
    
    # Budget line reference
    budget_line = models.ForeignKey(
        "procurement.BudgetLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="procurement_requests",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "proc_request"
        ordering = ["-created_at"]
    
    def __str__(self):
        return f"{self.reference}: {self.title}"
    
    def clean(self):
        # Validate plan is approved before request can be submitted
        if self.status != "DRAFT" and self.plan.status != "APPROVED":
            raise ValidationError(
                "Procurement request can only be submitted if annual plan is approved"
            )
        
        # Validate funds availability
        if self.status == "SUBMITTED":
            available = self.plan.budget_available
            # Add back this request's value if it was already counted
            if self.pk:
                existing = ProcurementRequest.objects.filter(pk=self.pk).first()
                if existing and existing.status in ["APPROVED", "IN_PROGRESS", "COMPLETED"]:
                    available += existing.estimated_value
            
            if self.estimated_value > available:
                raise ValidationError(
                    f"Insufficient funds. Requested: {self.estimated_value}, "
                    f"Available: {available}"
                )
    
    def save(self, *args, **kwargs):
        # Auto-determine method based on threshold rules
        if not self.method:
            self.method = self._determine_method()
        
        # Auto-generate reference if not set
        if not self.reference:
            self.reference = self._generate_reference()
        
        super().save(*args, **kwargs)
    
    def _determine_method(self):
        """Auto-determine procurement method based on threshold rules.
        
        Uses the ThresholdRule model to find the appropriate method for the
        estimated value and category.
        """
        from procurement.models import ThresholdRule
        
        rules = ThresholdRule.objects.filter(
            fiscal_year=self.plan.fiscal_year,
            is_active=True,
        ).order_by("-min_value")
        
        for rule in rules:
            if self.estimated_value >= rule.min_value:
                if rule.max_value is None or self.estimated_value <= rule.max_value:
                    return rule.method
        
        # Default to open competitive bidding if no rule matches
        return "NCB"  # National Competitive Bidding
    
    def _generate_reference(self):
        """Generate unique reference number."""
        year = timezone.now().year
        count = ProcurementRequest.objects.filter(
            plan__fiscal_year=self.plan.fiscal_year,
            plan__agency=self.plan.agency,
            created_at__year=year,
        ).count() + 1
        return f"PR-{self.plan.agency.code}-{year}-{count:04d}"
    
    @property
    def funds_available(self):
        """Check if funds are available for this request."""
        if self.budget_line:
            return self.estimated_value <= self.budget_line.available
        return self.estimated_value <= self.plan.budget_available


class MethodDeterminationLog(models.Model):
    """Log of method auto-determination for audit trail.
    
    Records why a particular method was chosen for transparency and debugging.
    """
    request = models.ForeignKey(
        ProcurementRequest,
        on_delete=models.CASCADE,
        related_name="method_logs",
    )
    determined_method = models.CharField(max_length=20)
    rule_applied = models.ForeignKey(
        "procurement.ThresholdRule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    estimated_value = models.DecimalField(max_digits=18, decimal_places=2)
    category = models.CharField(max_length=50)
    fiscal_year = models.CharField(max_length=4)
    reason = models.TextField()
    determined_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = "proc_method_log"
        ordering = ["-determined_at"]
    
    def __str__(self):
        return f"{self.request.reference}: {self.determined_method}"
