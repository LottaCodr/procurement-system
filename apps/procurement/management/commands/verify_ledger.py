"""Recompute the whole hash chain. Exit 1 on any break, so CI can gate on it.

This is the command that makes "we cannot secretly edit a tender" a claim a
third party can test themselves, using only the public bulk dump.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from ledger.services import head_hash, verify_chain
from ledger.models import Event


class Command(BaseCommand):
    help = "Verify the append-only event ledger's hash chain."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="machine-readable output")

    def handle(self, *a, **kw):
        total = Event.objects.count()
        checked, broken_at = verify_chain()
        ok = broken_at is None
        payload = {
            "ok": ok,
            "events_in_table": total,
            "events_checked": checked,
            "first_broken_seq": broken_at,
            "head_hash": head_hash(),
            "algorithm": "sha256",
        }
        if kw["json"]:
            import json

            self.stdout.write(json.dumps(payload, indent=2))
        else:
            if ok:
                self.stdout.write(self.style.SUCCESS(f"LEDGER OK  {checked} events, chain intact"))
                self.stdout.write(f"     head hash: {payload['head_hash']}")
                if total != checked:
                    self.stdout.write(self.style.WARNING(f"     note: {total - checked} rows were not reachable from the chain"))
            else:
                self.stdout.write(self.style.ERROR(f"LEDGER BROKEN at seq {broken_at}"))
                self.stdout.write("     Somebody altered history. Escalate; do not edit forward.")
        raise SystemExit(0 if ok else 1)
