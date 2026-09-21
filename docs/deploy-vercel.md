# Deploying to Vercel

This project is Django, and Vercel now has first-class Django support: it finds
`manage.py`, reads `WSGI_APPLICATION` / `ASGI_APPLICATION` from settings (it
uses our ASGI entrypoint, `config/asgi.py`), runs `python build.py` at build
time, and serves the app as one serverless function behind their CDN.

**Read this first — two serverless facts that shape everything below:**

1. **The disk is ephemeral.** The dev `db.sqlite3` will not survive a deploy
   (every cold start would reset the database). Production **must** use
   Postgres — Neon, Supabase or Vercel Postgres. Our settings accept a plain
   `DATABASE_URL`, which is exactly what those providers give you.
2. **Vercel is borrowed infrastructure.** DESIGN Part 9, risk 3: the state must
   own the account, the domain and the database, and keep the repo exportable.
   Vercel is fine for staging, demos and an interim production — as long as the
   login, the domain and the DB live in **state-owned** accounts and you can
   walk away. The Kano failure mode is a vendor holding the keys.

---

## 1. Push the repo to GitHub

The branch `arena/01a0c441-procurement-system` (or `main`) on GitHub. Vercel
deploys from Git; every pull request then gets its own preview URL.

## 2. Create the database (state-owned account!)

Neon (via the Vercel Marketplace "Storage" tab) or Supabase or Vercel
Postgres. From the provider, take the **pooled** connection string, e.g.

```
postgres://user:pass@pooler.host.neon.tech/dbname?sslmode=require
```

Keep the **direct/non-pooled** URL too — use it for one-off management
commands (`migrate`, `seed_demo`, `verify_ledger`) from your machine.

## 3. Create the Vercel project

Vercel dashboard → **Add New… → Project** → import the GitHub repo.
The framework preset auto-detects **Django** from `manage.py`. The existing
`vercel.json` already sets the build command (`python build.py`) and the
function shape (`config/asgi.py`, 60s max, Frankfurt region — edit `regions`
if your plan has a closer one, e.g. `cpt1`).

CLI alternative:

```bash
npm i -g vercel        # needs CLI ≥ 50.38 for the Django preset
vercel link            # from the repo root
vercel env add DJANGO_SECRET_KEY production
# … add each variable below …
vercel --prod
```

## 4. Environment variables

Set these for **Production** (and Preview, minus the production hostname):

| Variable | Value |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings` |
| `DJANGO_SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `0` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,127.0.0.1:8000,testserver,.vercel.app,procurement.taraba.gov.ng` |
| `CSRF_TRUSTED_ORIGINS` | `https://procurement.taraba.gov.ng,https://<project>.vercel.app` |
| `DATABASE_URL` | the pooled Postgres URL with `?sslmode=require` |
| `TARABA_HTTPS` | `1` |
| `CONTACT_PHONE` | a **real, tested** number (release blocker — `/tenders/status/` will shame you otherwise) |
| `CONTACT_EMAIL` | a real mailbox with a working MX |
| `RUN_DB_MIGRATIONS` | `1` (default) — set `0` only if a DBA applies migrations by hand |

`VERCEL_ENV` is injected by Vercel itself (`production` / `preview`).
Migrations run **only** on production deploys (see `build.py`) so a preview of
a half-finished pull request can never migrate the shared database.

## 5. Deploy

Dashboard "Deploy", or `vercel --prod`. The build will:

1. `pip install -r requirements.txt`
2. `python build.py` → builds the stylesheet artifact, then `migrate`
   (production only)
3. `collectstatic` (automatic — `STATIC_ROOT` is set)
4. Freeze the app into one function at `config/asgi.py`

## 6. First-deploy smoke test (the acceptance rows)

```bash
BASE=https://<project>.vercel.app
curl -s $BASE/api/v1/stats          # metrics computed, not typed
curl -s $BASE/api/v1/schema | head  # OpenAPI 3.1
curl -sI $BASE/ | grep -i strict    # HSTS present
python reference_consumer.py $BASE  # publish/fetch/validate round-trip
```

Optional demo data (from your machine, against the cloud DB):

```bash
vercel pull                          # writes .env.local — never commit it
export $(grep -v '^#' .env.local | xargs)
python manage.py seed_demo
python manage.py verify_ledger       # expect: LEDGER OK
```

## 7. Custom domain (this is where the Kano lesson bites)

Project → **Domains** → add `procurement.taraba.gov.ng`. Vercel prints the
CNAME/A record. **The record must be created by whoever controls `tr.gov.ng` —
and that must be the state, in a state-owned registrar account.** Kano's
parent zone (`kn.gov.ng`) has no delegation at all; do not repeat that. Bonus:
Vercel issues and auto-renews the TLS certificate (with the correct CN), which
is exactly the continuity Kano's portal lacks.

## 8. What does NOT fit on Vercel (plan around it)

| Thing | Why | What to do |
|---|---|---|
| Cron (`verify_ledger` nightly, `anchor_ledger`) | no always-on scheduler in this setup | GitHub Actions cron with the direct DB URL |
| Future file uploads (Phase 2 document packs) | disk is ephemeral | S3-compatible object storage (state-owned bucket); today everything is in Postgres |
| Live reverse-auction websockets at scale | needs an external channel layer | Redis channel layer, or host Phase 5 on state infra |
| `seed_demo` / ad-hoc commands | need a shell | run locally via `vercel pull` env, or `python manage.py …` from a workstation |

Bundle limits: 500MB standard (this app is a few MB). Free/Hobby plans have
function-duration and bandwidth caps — fine for a transparency portal, but
check the numbers before promising 121 MDAs.
