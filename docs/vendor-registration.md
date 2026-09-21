# Vendor registration — research, rules, and operations

## What registration means in Nigerian public procurement (the research)

A company that wants government work in Nigeria must prove, with documents,
that it legally exists, pays its taxes, and treats its workers lawfully. The
standard evidence set demanded by the Public Procurement Act 2007 and by BPP
practice (and repeated in essentially every state/federal tender notice) is:

| Evidence | Issuer | Notes |
|---|---|---|
| Certificate of Incorporation / Business Name (RC/BN number) + MEMART | CAC | Proof of legal existence; annual returns keep it "active" |
| TIN + Tax Clearance Certificate (last 3 years) | FIRS | Valid to 31 Dec of the year; renews annually |
| PenCom compliance certificate | National Pension Commission | **Exempt below 3 employees** (Pension Reform Act 2014) |
| ITF compliance certificate | Industrial Training Fund | Exempt below 5 employees or under ₦50m turnover |
| NSITF compliance certificate | NSITF | Employee-compensation scheme |
| Bank reference / audited accounts | Bank / auditor | Financial capacity, scaled to contract size |
| Evidence of similar contracts (last 5 years) | any client | Private-sector jobs count |

Two facts matter as much as the list:

1. **Registration is free.** BPP charges nothing; anyone demanding a fee from a
   vendor is committing an offence. The intake page says so and links to the
   whistleblower channel.
2. **State contracts need the same core papers.** Federal tenders additionally
   want the BPP National Database IRR; a state bureau registers against the
   same CAC/TIN identities and adds its own record on top.

The design doc (section 3.2) turns this into a principle: **supplier identity
is a verifiable credential, not a form.** Every check is a *typed, dated,
attributable assertion* — the register prints "verified against CAC on
3 Sep 2026 by user X", never "documents on file". And nobody is approved
once and trusted forever: credentials carry expiry dates, and expiry
auto-suspends the supplier.

## What is implemented

### The vendor side (no account, no fee, no JavaScript required)

* `/tenders/register/` — what you need before you start, how long it takes,
  what happens after.
* `/tenders/register/form/<token>/` — six steps (company identity → contact →
  certificates → capability → beneficial ownership → review & submit). The
  draft lives behind an unguessable 40-character link, so there is no password
  and nothing is published before submission.
* Real file uploads: certificates are hashed server-side (SHA-256), capped at
  5 MB (`MAX_UPLOAD_BYTES`), and the bytes are stored in the database row —
  serverless-safe, and re-hashable to prove the reviewer approved exactly what
  the vendor submitted.
* Identity fields are format-checked **at the form** (`workflow/validators.py`):
  RC/BN/GT + 4–8 digits; TIN as either the FIRS `12345678-0001` form or the
  10-digit JTB TIN. Sloppy-but-valid input is normalised, not refused.
* **Statutory exemptions are honoured**: a firm with fewer than 3 employees
  (`PENCOM_MIN_EMPLOYEES`) is not asked for a PenCom certificate — the form
  explains the exemption instead of blocking submission.
* Beneficial ownership (≥5% owners, PEP flags) and related-party declarations
  (PPA ss.18–19) are captured and published on approval.
* On submit the vendor gets reference `VND-000042` + private tracking link;
  `/tenders/register/status/` also looks applications up by reference + email
  for vendors who lost the link.

### The Bureau side

* `/tenders/register/review/` — the public queue: what is waiting, and for how
  long. Public read; decisions require an ADMIN/DG session.
* `/tenders/register/review/<pk>/` — one application: every field as submitted,
  each certificate with accept/refuse buttons (refusing requires a note the
  vendor sees), approve/reject forms.
* Approval rules: every *required* certificate must have been explicitly
  ACCEPTED first. Only accepted documents become PASSED verifications on the
  supplier profile, each carrying reviewer + date + document hash. Refusals
  block approval with a named list of what is undecided — never a silent
  wave-through.
* Rejection requires reasons; they are sent to the vendor by email and SMS
  (REG_NO notification) and shown on the status page.
* Duplicate RC numbers are named at submission ("Existing Co already holds
  RC…") and again at approval, instead of silently doubling records.

### The lifecycle after approval

* Every state change is a ledger event (`vendor.submitted`,
  `vendor.verification_started`, `vendor.document_decided`, `vendor.approved`,
  `vendor.rejected`, `vendor.suspended_credential_lapsed`).
* `python manage.py annual_reverification` (nightly cron): suspends suppliers
  whose CAC/TIN/pension verifications lapsed, and warns those expiring within
  `--days-before-expiry` (default 30). Suspension removes the firm from the
  public supplier list and the API until it re-registers.

### API surface

| Endpoint | Purpose |
|---|---|
| `POST /api/wf/vendor/register/start` | create a draft → `draft_token` |
| `POST /api/wf/vendor/register/<token>/step` | save step data (validates) |
| `POST /api/wf/vendor/register/<token>/document` | attach cert: multipart `file` or pre-hashed JSON |
| `POST /api/wf/vendor/register/<token>/owner` | add a ≥5% owner |
| `POST /api/wf/vendor/register/<token>/submit` | submit |
| `GET  /api/wf/vendor/register/<token>/status` | vendor's own view of progress |
| `POST /api/wf/vendor/register/<token>/decide-document` | reviewer: accept/refuse one cert |
| `POST /api/wf/vendor/register/<token>/approve` / `reject` | reviewer decision |
| `POST /api/wf/vendor/suspend-expired` | ADMIN-only ops trigger |

## What deliberately still needs doing

1. **Live registry lookups.** Today a named officer checks the RC against the
   CAC portal and the TIN against FIRS by hand; the verification record is
   dated and attributable either way. When CAC/FIRS expose verification APIs
   (or the JTB TIN endpoint), plug them into `decide_document` — the data
   model already stores `reference`/`evidence_sha256` for it.
2. **Real SMS/email delivery.** Notifications land in the `notif_outbox`
   table; the console SMS backend prints dry-runs until a provider
   (Africa's Talking / Termii, both already implemented in
   `workflow/notifications.py`) is configured.
3. **Supplier logins for bidding.** Registration needs no account by design;
   bidding needs one (SMS-OTP for suppliers per the design doc).
4. **Migration of existing contractors.** The design doc says to import
   Taraba's existing registered contractors rather than make them re-register
   — a CSV import command is the next natural step.
