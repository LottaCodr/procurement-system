"""Ledger writes that must not be forgettable.

Publishing a document, signing a contract or certifying a payment is written to
the append-only log from a signal, so a developer cannot ship a code path that
forgets to record it. Corrections to published data are new events, never edits.
"""
from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from ledger import services as ledger
from procurement.models import (
    AcceptanceCertificate,
    Award,
    Bid,
    Contract,
    Objection,
    PaymentCertification,
    TenderDocument,
)


@receiver(post_save, sender=TenderDocument)
def document_published(sender, instance: TenderDocument, created: bool, **kw):
    if created and instance.published:
        ledger.append(
            aggregate=f"procurement.Tender.{instance.tender_id}",
            event_type="document.published",
            actor="system",
            payload={"version": instance.version, "kind": instance.kind, "sha256": instance.sha256, "title": instance.title},
        )


@receiver(post_save, sender=Bid)
def bid_unsealed(sender, instance: Bid, created: bool, **kw):
    if not created and instance.status == Bid.Status.UNSEALED:
        ledger.append(
            aggregate=f"procurement.Tender.{instance.tender_id}",
            event_type="bid.unsealed",
            actor="system",
            payload={"receipt": instance.receipt_ref, "amount": str(instance.amount), "commitment_hash": instance.commitment_hash},
        )


@receiver(post_save, sender=Contract)
def contract_signed(sender, instance: Contract, created: bool, **kw):
    if created:
        ledger.append(
            aggregate=f"procurement.Tender.{instance.award.tender_id}",
            event_type="contract.signed",
            actor="system",
            payload={"reference": instance.reference, "value": str(instance.value), "duration_days": instance.duration_days},
        )


@receiver(post_save, sender=AcceptanceCertificate)
def delivery_accepted(sender, instance: AcceptanceCertificate, created: bool, **kw):
    if created:
        ledger.append(
            aggregate=f"procurement.Contract.{instance.contract_id}",
            event_type="contract.accepted",
            actor=instance.certified_by.username,
            payload={"ref": instance.ref, "value": str(instance.value), "inspected_at": instance.inspected_at.isoformat()},
        )


@receiver(post_save, sender=PaymentCertification)
def payment_certified(sender, instance: PaymentCertification, created: bool, **kw):
    if created:
        ledger.append(
            aggregate=f"procurement.Contract.{instance.contract_id}",
            event_type="payment.certified",
            actor=instance.certified_by.username,
            payload={"reference": instance.reference, "amount": str(instance.amount), "acceptance": instance.acceptance.ref},
        )


@receiver(post_save, sender=Objection)
def objection_logged(sender, instance: Objection, created: bool, **kw):
    if created:
        ledger.append(
            aggregate=f"procurement.Tender.{instance.tender_id}",
            event_type="objection.filed",
            actor=instance.filed_by_label or "anonymous",
            payload={"ground": instance.ground[:500], "award_id": instance.award_id, "filed_at": instance.filed_at.isoformat()},
        )
