#!/usr/bin/env python3
"""Vercel build step (wired up as ``buildCommand`` in vercel.json).

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

The stylesheet does not need Django. A previous deploy died because
``manage.py buildcss`` failed, the in-process fallback called
``django.setup()``, and ``DJANGO_SETTINGS_MODULE`` was blank.
``os.environ.setdefault`` does not replace ``""``, so Django raised
``ImproperlyConfigured: Requested setting LOGGING_CONFIG, but settings are
not configured``. The CSS step now builds the file directly and never calls
``django.setup()``. Migrations still go through ``manage.py``, which refuses
a blank settings module (see ``config.bootstrap``).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=ROOT)


def build_css() -> None:
    """Write ``apps/core/static/css/app.css`` without starting Django.

    Prefer ``manage.py buildcss`` so a deploy exercises the same command CI
    does. If that command is missing or Django itself cannot start, build the
    identical artifact in-process. A borrowed builder must not be able to
    block a deploy on command discovery or on settings configuration — the
    stylesheet is four files concatenated next to the ``core`` package.
    """
    try:
        run([sys.executable, "manage.py", "buildcss"])
        return
    except subprocess.CalledProcessError as exc:
        print(
            f"!! `manage.py buildcss` exited {exc.returncode} — building the "
            "stylesheet in-process (byte-identical artifact, no Django setup).",
            flush=True,
        )

    apps = str(ROOT / "apps")
    if apps not in sys.path:
        sys.path.insert(0, apps)
    from core import css_build

    meta = css_build.write_build()
    print(
        f"built apps/core/static/css/app.css in-process — {meta['bytes']} bytes, "
        f"hash {meta['hash']}, from {len(meta['files'])} source files",
        flush=True,
    )


def _database_configured() -> bool:
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    return os.environ.get("USE_POSTGRES", "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    # Fix a blank DJANGO_SETTINGS_MODULE *before* any child manage.py runs.
    # Child processes inherit this environment. .env is not on the builder:
    # .vercelignore removes it, and Django does not load it on its own.
    from config.bootstrap import prepare

    incoming = os.environ.get("DJANGO_SETTINGS_MODULE")
    resolved = prepare()
    print(
        f"DJANGO_SETTINGS_MODULE incoming={incoming!r} resolved={resolved!r}",
        flush=True,
    )

    build_css()

    vercel_env = os.environ.get("VERCEL_ENV", "").strip().lower()
    allow = os.environ.get("RUN_DB_MIGRATIONS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    has_db = _database_configured()

    if vercel_env == "production" and allow:
        if not has_db:
            print(
                "ERROR: production deploy has no database. Set DATABASE_URL "
                "(Neon, Supabase, or Vercel Postgres) in the Vercel project "
                "environment. SQLite is ephemeral on Vercel and cannot hold "
                "the register. .env is not uploaded (.vercelignore).",
                file=sys.stderr,
            )
            return 1
        run([sys.executable, "manage.py", "migrate", "--noinput"])
    elif vercel_env == "production":
        print("RUN_DB_MIGRATIONS=0 — skipping migrate; apply it out-of-band.")
    elif vercel_env:
        print(
            f"VERCEL_ENV={vercel_env!r} — skipping migrate so a preview "
            "deployment cannot change the shared schema."
        )
        if not has_db:
            print(
                "warning: no DATABASE_URL on this preview. The function will "
                "boot, but pages that read the register will fail until a "
                "database is attached. SQLite does not survive on Vercel.",
                flush=True,
            )
    else:
        print("No VERCEL_ENV (local run) — skipping migrate.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
