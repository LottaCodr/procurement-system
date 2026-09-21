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
"""
from __future__ import annotations

import os
import subprocess
import sys


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def main() -> int:
    python = sys.executable

    # 1. CSS artifact (idempotent; seconds).
    run(python, "manage.py", "buildcss")

    # 2. Migrations, production deploys only.
    vercel_env = os.environ.get("VERCEL_ENV", "").strip().lower()
    allow = os.environ.get("RUN_DB_MIGRATIONS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    if vercel_env == "production" and allow:
        run(python, "manage.py", "migrate", "--noinput")
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
