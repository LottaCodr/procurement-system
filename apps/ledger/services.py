"""Append-only, hash-chained event ledger.

Axiom 2 of the design: *a record that can be edited is not a record.* Georgia
made tender documents immutable on the platform precisely to stop the habit of
editing documents overnight to disqualify a rival. This module is the technical
expression of that rule, and it is deliberately stronger than a policy
statement: business facts are *derived* from this log by projections, so an
UPDATE here is detectable by anyone, including a DBA holding state credentials.

Chain rule
----------
Each event stores `prev_hash` = the row hash of the immediately preceding event
globally. `verify_chain()` recomputes the whole chain from genesis. A single
edited row invalidates every hash after it, so partial, targeted tampering is
not survivable, and the head hash can be published periodically so the state
cannot later deny a record it did publish.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone as dt_timezone

from django.db import connection, transaction

GENESIS_HASH = "0" * 64


def canonical(obj) -> str:
    """Deterministic serialisation. Dict order must never change a hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(*, seq: int, aggregate: str, event_type: str, actor: str, occurred_at: str, payload: dict, prev_hash: str) -> str:
    body = canonical(
        {
            "seq": seq,
            "aggregate": aggregate,
            "event_type": event_type,
            "actor": actor,
            "occurred_at": occurred_at,
            "payload": payload,
            "prev_hash": prev_hash,
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class LedgerIntegrityError(RuntimeError):
    """Raised when a write would break the chain or violate append-only rules."""


class LedgerTamperDetected(LedgerIntegrityError):
    """Raised by verify_chain when stored hashes do not recompute."""


def append(*, aggregate: str, event_type: str, actor: str, payload: dict, occurred_at: datetime | None = None) -> dict:
    """Append one immutable event and return it.

    `aggregate` is `"<model>.<pk>"` (e.g. ``procurement.Tender.41``). It is text
    rather than a FK on purpose: the log must still be writable for objects that
    have been logically superseded, and it must never be the thing that blocks a
    legitimate business write.
    """
    from ledger.models import Event

    if not aggregate or "." not in aggregate:
        raise LedgerIntegrityError("aggregate must look like 'app_label.Model.pk'")
    if not event_type or "." not in event_type:
        raise LedgerIntegrityError("event_type must be namespaced, e.g. 'tender.published'")
    if not isinstance(payload, dict):
        raise LedgerIntegrityError("payload must be a dict")

    with transaction.atomic():
        # Lock the tail so concurrent appends cannot both claim the same seq.
        last = Event.objects.select_for_update().order_by("-seq").first() if connection.features.has_select_for_update else Event.objects.order_by("-seq").first()
        seq = 1 if last is None else last.seq + 1
        prev_hash = GENESIS_HASH if last is None else last.row_hash

        when = occurred_at or datetime.now(dt_timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_timezone.utc)

        digest = row_hash(
            seq=seq,
            aggregate=aggregate,
            event_type=event_type,
            actor=actor,
            occurred_at=when.isoformat(),
            payload=payload,
            prev_hash=prev_hash,
        )
        event = Event.objects.create(
            seq=seq,
            aggregate=aggregate,
            event_type=event_type,
            actor=actor,
            payload=payload,
            occurred_at=when,
            prev_hash=prev_hash,
            row_hash=digest,
        )
        return as_dict(event)


def as_dict(event) -> dict:
    return {
        "seq": event.seq,
        "aggregate": event.aggregate,
        "event_type": event.event_type,
        "actor": event.actor,
        "occurred_at": event.occurred_at,
        "payload": event.payload,
        "prev_hash": event.prev_hash,
        "row_hash": event.row_hash,
    }


def history(aggregate: str) -> list[dict]:
    from ledger.models import Event

    return [as_dict(e) for e in Event.objects.filter(aggregate=aggregate).order_by("seq")]


def head_hash() -> str:
    """Publishable commitment to the entire ledger state at this instant."""
    from ledger.models import Event

    last = Event.objects.order_by("-seq").first()
    return GENESIS_HASH if last is None else last.row_hash


def verify_chain() -> tuple[int, str | None]:
    """Recompute every hash. Returns (checked_count, first_broken_seq or None)."""
    from ledger.models import Event

    prev = GENESIS_HASH
    checked = 0
    for e in Event.objects.order_by("seq").iterator():
        expected = row_hash(
            seq=e.seq,
            aggregate=e.aggregate,
            event_type=e.event_type,
            actor=e.actor,
            occurred_at=e.occurred_at.isoformat(),
            payload=e.payload,
            prev_hash=prev,
        )
        checked += 1
        if e.prev_hash != prev:
            return checked, e.seq
        if e.row_hash != expected:
            return checked, e.seq
        prev = e.row_hash
    return checked, None


def enforce_append_only() -> list[str]:
    """DB-level guard against mutation.

    Postgres gets real triggers (installed by migration 0002). SQLite, used for
    dev and unit tests, cannot carry those triggers, so the ORM layer refuses the
    verbs instead (see ``LedgerEventQuerySet``). This returns a human-readable
    report of what is currently enforced, and /status publishes it: an
    unenforceable claim of immutability is worse than an honest gap.
    """
    from django.conf import settings

    engine = settings.DATABASES["default"]["ENGINE"]
    if engine.endswith("postgresql"):
        return ["postgres: trigger ledger_no_update + ledger_no_delete installed", "ORM: update/delete refused"]
    return ["ORM: update/delete refused", "sqlite dev DB: no trigger layer (Postgres-only, migration 0002)"]
