#!/usr/bin/env python3
"""Taraba State e-Procurement Platform — management entry point."""
import sys


def main() -> None:
    # This file's directory (the project root) is sys.path[0], so ``config``
    # imports. prepare() adds ``apps/`` and replaces a blank
    # DJANGO_SETTINGS_MODULE. setdefault() cannot: "" is already "set", and
    # Django treats a blank value as settings-not-configured.
    from config.bootstrap import prepare

    prepare()
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django is not installed in this interpreter. Run: pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
