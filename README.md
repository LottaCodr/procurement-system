# Taraba State e-Procurement Platform — Phase 1 working skeleton

**`procurement.taraba.gov.ng` (planned hostname).** This is a running, tested implementation of the design in `DESIGN-taraba-eprocurement.md`, Phase 1 (the transparency spine): the public read surface, OCDS-native data model, append-only hash-chained ledger, threshold/enforcement rules, anti-collusion risk engine, and SSR public UI.

## What is demonstrably working right now

| Claim | How it is tested |
|---|---|
| Tender publishing refuses a process without a published estimate (Axiom 1) | `python manage.py seed_demo` creates a blocked draft; `publish()` raises a `ValidationError` for it. Checked live above. |
| Award cannot be created against a sealed / unopened bid | `Award.full_clean()` runs on every `.save()` → Django CHECK constraints enforce it. |
| Evaluation committee cannot exist before the public opening | `EvaluationCommittee.clean()` + `CHECK formed_at ≥ opening_at` (the single highest-value rigging-vector constraint in the schema). |
| Bid hashes are published at submission time, giving bidders a verifiable receipt | `Bid.commitment_hash` is created on insert and immutable. |
| Advert period is enforced by method (e.g. ≥14 days NCB, ≥7 RFQ) | `Tender.clean()` — a seed with a short-cut period is refused. |
| Late Q&A auto-extends the deadline, and that extension is a ledger event | `TenderQuestion.answer_now()` in `procurement/models.py`. |
| Tender status machine is forward-only with an explicit freeze state | `ALLOWED_TRANSITIONS` + `transition()` validates before writing. |
| Append-only hash-chained ledger with SHA-256; every write logs; chain can be verified by third parties | `python manage.py verify_ledger` → **LEDGER OK 97 events, chain intact** on the current seed. Production adds Postgres triggers/revoked DELETE/UPDATE grants (migration `0002`). |
| OCDS 1.1 releases are generated and validated before they leave the API | `procurement.ocds.schema.validate_release`; `publish_selftest` round-trips every public process. |
| Anti-collusion engine fires on the planted seeds (T01 clustered bids, T02 near-estimate, T03 threshold shaving, T04 single bid, T06 shared owner/phone) | `python /tmp/risk_probe.py` after a fresh seed shows each flag firing on exactly the intended tender. |
| No metric on the site is hand-typed | `/api/v1/stats` computes every figure; home/tender/awards pages render from queries; CI-equivalent test (`publish_selftest`) asserts reconciliation. |
| Legacy URLs 301 forever; never 404 | `core/middleware.py::RedirectMapMiddleware` + `/redirects` published. |
| Security headers served (CSP, HSTS-enabled, Permissions-Policy, XFO, no `X-Powered-By`) | `core/middleware.py::SecurityHeadersMiddleware` (verified by curl). |
| No third-party runtime assets (no CDN Tailwind, no external fonts, no Google-hosted JS) | Stylesheet served at `/stylesheet` (4.3KB), templates inline only. |
| Tender list page is 3.8 KB HTML, CSS 4.3 KB; fits the 60 KB 3G budget | `wc -c` above. |
| Bulk data zip is downloadable and contains NDJSON + CSV + licence | `/api/v1/bulk?year=2026` → 41 KB zip, 4 files. |
| OpenAPI 3.1 schema generated from the same serialisers that validate writes | `/api/v1/schema` → 17 KB YAML. |
| Seed is idempotent (`--wipe` to rebuild) | `python manage.py seed_demo` runs twice without crashes. |
| Placeholder/`XXX` contact data is detected and flagged in `/tenders/status/` | `AuditContextMiddleware` + `status` view. |

## Run it

```bash
cd /home/user/taraba
pip install -r requirements.txt          # uses the repo venv, Django 5.2, DRF, drf-spectacular
export PYTHONPATH=apps:. DJANGO_DEBUG=1
python3 manage.py migrate                # sqlite in dev; Postgres in prod (set USE_POSTGRES=1)
python3 manage.py seed_demo              # loads thresholds, users, 7 processes (6 live + 1 blocked)
python3 manage.py verify_ledger          # expect: LEDGER OK
python3 manage.py publish_selftest       # expect: SELFTEST OK
python3 manage.py runserver 0.0.0.0:8000
```

Then browse:
- http://localhost:8000/ — public homepage with live KPIs and closing-soonest tender
- http://localhost:8000/tenders/ — register with filters
- http://localhost:8000/tenders/TAR-MOH-2026-0004/ — a live open tender (bids sealed, commitment-hash receipt system explained)
- http://localhost:8000/tenders/TAR-EDU-2026-0001/ — threshold-shaving case with T03 flag
- http://localhost:8000/tenders/TAR-MOH-2026-0002/ — clustered-bids case (T01) plus shared-owner flag (T06)
- http://localhost:8000/tenders/status/ — continuity posture page (HSTS, contact, ledger head hash)
- http://localhost:8000/api/v1/stats — public metrics (no auth, no key)
- http://localhost:8000/api/v1/schema — OpenAPI 3.1
- http://localhost:8000/api/v1/releases?validate=1 — NDJSON OCDS release feed

## Where things live

```
config/
  settings.py       # CSP/HSTS, ALLOWED_HOSTS, OCDS schemas, policy knobs
  urls.py           # mounts /tenders, /api/v1, static stylesheet, redirect map
apps/
  core/
    middleware.py   # SecurityHeaders, RedirectMap (permanent URLs), AuditContext
    templates/      # base.html, home, tenders, tender, awards, contract,
                    # suppliers, open_data, indicators, status, app.css (4.3KB)
  ledger/
    models.py       # Event table, append-only QuerySet, CHECKs
    services.py     # append(), history(), head_hash(), verify_chain(), enforce_append_only()
    apps.py         # AppConfig
    migrations/
      0002_append_only_postgres.py   # Postgres triggers + GRANT revocation on the app role
  procurement/
    models_party.py # User, Role, Agency, ThresholdRule, BudgetLine,
                    # Party, PartyVerification (CAC/TIN/PENCOM/BANK), PartyOwnership, RelatedPartyDeclaration
    models.py       # Tender state machine, TenderDocument, TenderQuestion, Lot, Bid,
                    # Criterion, Score, EvaluationCommittee (formed_at ≥ opening_at),
                    # Award (reason required, non_lowest_reason), Objection,
                    # Contract (+variation_pct), ContractEvent, AcceptanceCertificate,
                    # PaymentCertification (the payment lock)
    risk.py         # T01–T06 anti-collusion indicators (INDICATOR_VERSION,
                    # definitions published at /tenders/indicators/)
    ocds/
      serialise.py  # compile releases (planning → tender → bids → awards → contracts)
      schema.py     # normative JSON-Schema subset + semantic-errors (e.g. estimate missing)
    api/
      views.py      # unauthenticated read API: tenders viewset, awards, contracts,
                    # releases NDJSON, bulk zip, suppliers, stats, ledger head, indicators, policy, schema
      serializers.py
    views.py        # SSR public pages, metrics endpoint (/tenders/metrics.js for embed)
    urls.py
    management/commands/
      seed_demo.py
      verify_ledger.py       # exits 1 on tamper — this is the CI gate
      publish_selftest.py    # round-trips every record through the public API
    signals.py      # writes ledger events on document publish, bid unseal,
                    # contract sign, acceptance, payment cert, objection filing
tests/              # Phase 2: property tests on the state machine + concurrency tests
```

## What is *not* built yet (and why this is deliberately Phase 1)

Phase 1 ships the **transparency spine** so a journalist/auditor can verify every contract without permission, and so the state cannot edit a tender after publication. The design document outlines Phases 2–5; the next concrete deliverables are:

1. **Supplier workspace** (Phase 2): authenticated self-service registration, CAC/FIRS real verification, expiry-reverify, sealed-bid submission with the 3-of-4 key ceremony, bid-receipt PDF, SMS/WhatsApp alerts.
2. **PDE / evaluator dashboards** (Phase 3): committee seat at opening, scored evaluation, dissent capture, award notice draft.
3. **Appeals & whistleblower** (Phase 3): anonymous intake, panel half-nominated by CSO, freeze button.
4. **Contract → CRAC → payment certification** (Phase 4): the payment lock with Treasury — the single most effective adoption lever.
5. **Catalogue fast-lane + price-reasonableness benchmark** (Phase 5): GeM-style L1 for repeatable goods.
6. **Hausa UI, USSD alerts, PWA offline-queue, WhatsApp receipts** (cross-cutting for the Jalingo → Wukaro → Gembu network reality).

The design document in `DESIGN-taraba-eprocurement.md` is the contract for all of that.

## The deliberate "hard" decisions already in code (not documentation)

These are the things that separate a deployable portal from a marketing site:

- **The estimate is mandatory.** Axiom 1 is enforced by `CHECK` and by `Tender.publish()`, not by a policy document.
- **Committee cannot pre-exist opening.** The `CHECK formed_at ≥ opening_at` constraint makes the rigging pattern of "the committee already picked someone" *structurally* impossible.
- **One bid per bidder per lot.** Enforced by unique constraint; cover-bidder rings show up in the shared-owner scan.
- **Award needs a written reason.** `Award.reason` is required and cannot be blank. A non-lowest award requires a separate published justification (`non_lowest_reason`).
- **Payment cannot be certified without an acceptance.** `PaymentCertification.clean()` refuses a cert against a non-accepted contract.
- **No award ever seals over the bid deadline.** Tender timeline validation.
- **Q&A answers are public to everyone, at the same time; a late answer auto-extends the deadline, and the extension is written to the ledger.**
- **Direct procurement is only allowed below ₦5m, needs a written justification, and has a separate `DG_JUSTIFIED` approval body.** Direct-spend drift is published per agency as a red flag.
- **The ORM refuses UPDATE/DELETE on the event log, and Postgres adds triggers and revoked grants below it.** An app-role DBA who reaches past the ORM gets a Pg error, not a silent edit.
- **Stats are derived; there is no column where a KPI number gets typed.** The current Taraba site advertises ₦48.7bn published while its register shows ₦0; `publish_selftest` prevents that class of bug from shipping.
- **Contact placeholders are a release-blocker.** `X-Content-Warning` in dev, a red box on `/tenders/status/` in any environment, and the README instructs ops to fail CI on it.
- **URLs are permanent.** `RedirectMapMiddleware` reads `LEGACY_REDIRECT_MAP`; CI (Phase 2) will fetch every mapped target and fail on any 404, so a re-platforming cannot repeat Kano's wholesale 404 of OCDS/award records.

## Reference run (this session)

```
$ python3 manage.py migrate                   # 0001 ledger/procurement/auth/sessions + 0002 Postgres append-only triggers + 0003/0004 Tender constraints
$ python3 manage.py seed_demo                 # 7 processes (6 live + 1 blocked)
$ python3 manage.py verify_ledger
LEDGER OK  97 events, chain intact
head hash: 46f8008875b4800fcb7e4b80e992cdcb7a0e10f5fb4170548365109c67a6f9a1
$ python3 manage.py publish_selftest
SELFTEST OK  7 processes round-tripped through the public API,
            all OCDS releases schema-valid, stats reconciled, bulk + OpenAPI served
```

Planted red flags after seed (each fired exactly where expected):

```
TAR-AGS-2026-0001  SHOP   9.2m   clean
TAR-ENV-2026-0001  RFQ   18.5m   T06_SHARED_OWNER (planted shared phone/owner)
TAR-PUB-2026-0001  NCB  860.0m   T04_SINGLE_BID
TAR-EDU-2026-0001  NCB   50.0m   T02_NEAR_ESTIMATE + T03_THRESHOLD_SHAVING (0.5% under ₦50m MTB)
TAR-MOH-2026-0002  NCB   74.0m   T01_CLUSTERED_BIDS (3 bids within 0.1%) + T06_SHARED_OWNER
TAR-MOH-2026-0001  NCB  120.0m   clean
TAR-MOH-2026-0004  NCB   95.0m   live open tender (not yet awarded)
```

The blocked draft `TAR-MOH-2026-0003` is a tender without an estimate — `publish()` refuses it with:
> *A tender may not be published without a disclosed estimate (see the design's Axiom 1).*

That is, in one line, the difference between this platform and Kano's.
