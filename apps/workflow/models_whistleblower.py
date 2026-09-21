"""Whistleblower case tracking model."""
from django.db import models
import secrets


class WhistleblowerCase(models.Model):
    """Encrypted whistleblower report for corruption allegations."""
    
    STATUS_CHOICES = [
        ('RECEIVED', 'Received'),
        ('UNDER_REVIEW', 'Under Review'),
        ('INVESTIGATING', 'Investigating'),
        ('RESOLVED', 'Resolved'),
        ('DISMISSED', 'Dismissed'),
    ]
    
    case_id = models.CharField(max_length=20, unique=True)
    encrypted_content = models.TextField(
        help_text="AES-256 encrypted report content"
    )
    content_hash = models.CharField(
        max_length=64,
        help_text="SHA256 hash of encrypted content for integrity verification"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='RECEIVED')
    assigned_to = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='whistleblower_cases'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Whistleblower Case'
        verbose_name_plural = 'Whistleblower Cases'
    
    def save(self, *args, **kwargs):
        if not self.case_id:
            self.case_id = f"WB-{secrets.token_hex(4).upper()}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.case_id} ({self.status})"
