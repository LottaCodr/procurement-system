from django.db import models


class LedgerEventQuerySet(models.QuerySet):
    """Refuses UPDATE and DELETE. The ledger is append-only at the ORM layer.

    This is belt-and-braces: on Postgres the real guarantee is triggers plus
    table grants (the application role gets INSERT/SELECT only). Both layers are
    tested, because the design principle is that the guarantee must not depend on
    trusting any one operator.
    """

    def update(self, *args, **kwargs):
        raise PermissionError(
            "The event ledger is append-only. Corrections are new events, never edits. "
            "If a published fact was wrong, append '<aggregate>.corrected' with the reason."
        )

    def delete(self):
        raise PermissionError(
            "Ledger rows may not be deleted. Redaction happens in the read model, "
            "with the redaction itself logged and its rule published."
        )


class Event(models.Model):
    """One immutable, hash-chained procurement event."""

    seq = models.PositiveBigIntegerField(unique=True, editable=False)
    aggregate = models.CharField(max_length=160, db_index=True, editable=False)
    event_type = models.CharField(max_length=80, db_index=True, editable=False)
    actor = models.CharField(max_length=120, editable=False, help_text="username, or 'anonymous' / 'system'")
    payload = models.JSONField(editable=False)
    occurred_at = models.DateTimeField(editable=False, db_index=True)
    prev_hash = models.CharField(max_length=64, editable=False)
    row_hash = models.CharField(max_length=64, editable=False, unique=True)

    objects = LedgerEventQuerySet.as_manager()

    class Meta:
        db_table = "ledger_event"
        ordering = ["seq"]
        verbose_name = "ledger event"
        verbose_name_plural = "ledger events"
        constraints = [
            # An event can never be its own predecessor, and a payload can never
            # be empty: an empty event is how "we edited it later" starts.
            models.CheckConstraint(condition=~models.Q(payload={}), name="event_payload_not_empty"),
            models.CheckConstraint(condition=~models.Q(seq=0), name="event_seq_nonzero"),
        ]
        indexes = [
            models.Index(fields=["aggregate", "seq"], name="ledger_aggregate_seq_idx"),
            models.Index(fields=["event_type", "occurred_at"], name="ledger_type_time_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"#{self.seq} {self.event_type} @ {self.occurred_at:%Y-%m-%d %H:%M}"

    def natural_repr(self) -> dict:
        from ledger.services import as_dict

        return as_dict(self)
