"""`manage.py buildcss` — build the stylesheet, or verify it is current."""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core import css_build


class Command(BaseCommand):
    help = "Build apps/core/static/css/app.css from the layered sources in css/src/."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero if the built stylesheet is stale (used by CI).",
        )

    def handle(self, *args, **options):
        if options["check"]:
            if css_build.is_stale():
                raise CommandError(
                    "stylesheet is stale — run `python manage.py buildcss` and commit the result"
                )
            self.stdout.write(self.style.SUCCESS("CSS OK (artifact matches sources)"))
            return

        meta = css_build.write_build()
        self.stdout.write(
            self.style.SUCCESS(
                f"built apps/core/static/css/app.css — {meta['bytes']} bytes, "
                f"hash {meta['hash']}, from {len(meta['files'])} source files"
            )
        )
