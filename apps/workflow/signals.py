"""Wire Phase-2 actions to events. We attach here so procurement/models.py stays
clean and focused on the state machine."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from procurement.models import Tender
from workflow.models import TenderKey
from workflow.services import publish_tender_key, dispatch_category_watches, unseal_bids
import logging

log = logging.getLogger(__name__)


@receiver(post_save, sender=Tender)
def _on_tender_publish_key(sender, instance: Tender, created: bool = False, **kw):
    """When a tender is first published, generate its per-tender sealing key."""
    if instance.status == Tender.Status.PUBLISHED and not TenderKey.objects.filter(tender=instance).exists():
        try:
            publish_tender_key(instance)
        except Exception:
            log.exception("sealed-bid key generation failed for %s", instance.ocid)


@receiver(post_save, sender=Tender)
def _on_tender_open_dispatch(sender, instance: Tender, created: bool = False, **kw):
    """When moving to OPENED and sealed bids still exist, unseal them (demo mode).
    Production unsealing happens via the custodian-shares ceremony API endpoint."""
    if instance.status == Tender.Status.OPENED:
        # only auto-unseal in demo mode when bids have envelopes but haven't been opened
        if not getattr(instance, "_opening_already_called", False) and instance.bids.filter(envelope__isnull=False).exists() and instance.bids.filter(envelope__unsealed_at__isnull=True).exists():
            try:
                unseal_bids(instance, actor="system:demo")
            except Exception:
                log.exception("auto-unseal failed for %s", instance.ocid)


@receiver(post_save, sender=Tender)
def _on_tender_published_notify_watchers(sender, instance: Tender, created: bool = False, **kw):
    if instance.status == Tender.Status.PUBLISHED and instance.published_at:
        try:
            dispatch_category_watches(instance)
        except Exception:
            log.exception("category-watch dispatch failed for %s", instance.ocid)
