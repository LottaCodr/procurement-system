#!/usr/bin/env python3
"""Vercel build step (wired up as `buildCommand` in vercel.json).

Runs after dependencies are installed, before the serverless bundle is frozen.
Two jobs:

1. **Build the content-addressed stylesheet** so ``/stylesheet?v=<hash>`` has a
   real hash and collectstatic (run by Vercel automatically) picks up the
   artifact. The view can build in memory at request time, but a deploy should
   not rely on that.

2. **Apply database migrations — but only for production deploys.** Preview
   deployments (one per pull request) share the production database on a
   one-database setup; a feature branch must never be able to migrate the
   shared schema. ``VERCEL_ENV`` is set by Vercel itself (``production`` /
   ``preview`` / ``development``) and cannot be spoofed from project settings.

   Escape hatches:
   * ``RUN_DB_MIGRATIONS=0`` — skip migrate even in production, e.g. when a
     DBA applies migrations by hand from a state-owned workstation.
   * Run the same commands locally against the cloud database with
     ``vercel pull`` + ``python manage.py migrate`` (see docs/deploy-vercel.md).

The stylesheet step is belt-and-braces. Normally it is ``manage.py buildcss``.
Django only discovers management commands from *packages* —
``<app>/management/`` and ``<app>/management/commands/`` must each contain an
``__init__.py`` (ours do; this repo once shipped ``core`` without them and the
command silently vanished on some builders, which discover commands through
``pkgutil``). If the builder's Python still fails to discover the command, we
print a diagnosis (so the build log shows exactly what the builder sees) and
then build the identical artifact in-process via ``core.css_build``. A deploy
must not hinge on command *discovery* on borrowed infrastructure; ``migrate``
is unaffected — it is a Django core command.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APPS_DIR = os.path.join(ROOT, "apps")
COMMANDS_DIR = os.path.join(APPS_DIR, "core", "management", "commands")


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def _setup_import_path() -> None:
    """Mirror what manage.py does, so in-process work sees the same apps."""
    if APPS_DIR not in sys.path:
        sys.path.insert(0, APPS_DIR)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def diagnose_buildcss() -> None:
    """Print what the builder's Python actually sees, for the build log."""
    import pkgutil

    print("--- buildcss diagnostics " + "-" * 40, flush=True)
    print("python:", sys.version.replace("\n", " "), flush=True)
    print("executable:", sys.executable, flush=True)
    print("cwd:", os.getcwd(), flush=True)
    print("commands dir:", COMMANDS_DIR, flush=True)
    print("commands dir exists:", os.path.isdir(COMMANDS_DIR), flush=True)
    if os.path.isdir(COMMANDS_DIR):
        print("commands dir contents:", sorted(os.listdir(COMMANDS_DIR)), flush=True)
    print(
        "pkgutil.iter_modules(commands dir):",
        sorted(m.name for m in pkgutil.iter_modules([COMMANDS_DIR])),
        flush=True,
    )
    try:
        import django

        print("django:", django.get_version(), flush=True)
        _setup_import_path()
        django.setup()
        from django.apps import apps

        for cfg in apps.get_app_configs():
            has_commands = os.path.isdir(os.path.join(cfg.path, "management", "commands"))
            print(f"app {cfg.name!r} path={cfg.path} has_commands={has_commands}", flush=True)
        from django.core.management import find_commands

        print(
            "find_commands(core):",
            find_commands(os.path.join(APPS_DIR, "core", "management")),
            flush=True,
        )
    except Exception as exc:  # pragma: no cover — diagnostics must never kill the build
        print("django diagnostics failed:", repr(exc), flush=True)
    print("sys.path:", sys.path, flush=True)
    print("-" * 66, flush=True)


def build_css() -> None:
    """Build the stylesheet via the management command, with an in-process fallback."""
    try:
        run(sys.executable, "manage.py", "buildcss")
        return
    except subprocess.CalledProcessError:
        print(
            "!! `manage.py buildcss` failed on this builder — printing a "
            "diagnosis, then building the stylesheet in-process "
            "(byte-identical artifact).",
            flush=True,
        )
        diagnose_buildcss()

    # Fallback: run exactly what the command would run, in this process.
    _setup_import_path()
    import django

    django.setup()
    from core import css_build

    meta = css_build.write_build()
    print(
        f"built apps/core/static/css/app.css in-process — {meta['bytes']} bytes, "
        f"hash {meta['hash']}, from {len(meta['files'])} source files",
        flush=True,
    )


def main() -> int:
    # 1. CSS artifact (idempotent; seconds).
    build_css()

    # 2. Migrations, production deploys only.
    vercel_env = os.environ.get("VERCEL_ENV", "").strip().lower()
    allow = os.environ.get("RUN_DB_MIGRATIONS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    if vercel_env == "production" and allow:
        run(sys.executable, "manage.py", "migrate", "--noinput")
    elif vercel_env == "production":
        print("RUN_DB_MIGRATIONS=0 — skipping migrate; apply it out-of-band.")
    elif vercel_env:
        print(
            f"VERCEL_ENV={vercel_env!r} — skipping migrate so a preview "
            "deployment cannot change the shared schema."
        )
    else:
        print("No VERCEL_ENV (local run) — skipping migrate.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
