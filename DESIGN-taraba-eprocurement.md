# Taraba State e-Procurement Platform — First-Principles Design
### Volume 1: Ground truth, benchmarks, domain model, architecture, delivery plan
*Prepared 20 September 2026. Every measured number in this document came from a live probe run during this session (DNS/RDAP/TLS/HTTP/crt.sh/Wayback). Benchmark claims cite public sources. Items needing your confirmation are marked **[CONFIRM]**.*

---

# PART 0 — What "100× better than Kano" actually means

Kano failed on 7 measurable axes. Not one of them is code quality. So "100× better" is not a UI brief — it's an **operating-model** brief.

| # | Axis | Kano measured state | Taraba today (measured) | Target for v1 |
|---|---|---|---|---|
| 1 | Data published | 0 tenders, 0 contracts, 0 awards | 2 tenders, **0 awards, ₦0.00** | every award, ≤48h, forever |
| 2 | Identity verification | no CAC, no TIN | **RC + TIN + expiry-dated documents** ✓ | + NIBSS/TIN validation, beneficial ownership |
| 3 | DNS/control | parent zone `kn.gov.ng` **undelegated** (status `inactive`), apex NXDOMAIN | `bpp.tr.gov.ng` resolves; parent `tr.gov.ng` has **no A** (same latent bug) | Taraba-controlled delegation + documented records |
| 4 | TLS continuity | 2 lapsed certs; current cert CN = vendor's **parked** `e-proc.cloud` | correct CNs, Let's Encrypt, **90-day certs issued Aug 9 / Aug 27 2026 → both expire Nov 7 / Nov 25 2026** | auto-renew + expiry alarm at 21 days, public status page |
| 5 | Contact reachability | phone `+234 64 XXX XXXX`; email domain has **no MX at all** | not measured | tested live before go-live, in every page footer |
| 6 | Migration of prior record | all legacy URLs → 404 (OCDS, award lists, the Procurement Law) | n/a (no predecessor) | permanent-URL promise + redirect map, tested in CI |
| 7 | Governance features | none | whistleblower page exists on BPP site | appeals, bid-freeze, published complaint committee, red-flag dashboard |

**The strategic insight:** Kano's and Taraba's shared failure is *not* the software. Taraba's codebase is demonstrably more careful than Kano's (CSRF tokens, draft/resume workflow, expiry-tracked document upload, a real multi-step wizard, `permissions-policy`, no test data in production UI). What's missing in both is **the institution around the software**: no data, no continuity engineering, no independent scrutiny. Build the software *and* the operating contract, or you will end up exactly where Kano is.

### A hard-earned caution about the "100×" target
Taraba's own homepage currently claims **"₦48.7bn Contracts published"**, **"121 MDAs regulated"**, **"100% Awards disclosed online"**, and **"2+ Registered contractors"** — while the live portal shows **₦0.00 awarded, 0 contracts published** across every year I queried (2021–2026), and it advertises an **"Open data (OCDS JSON)"** link for which there is **no endpoint** (7 candidate paths probed → all 404).

If we ship a beautiful portal with unbacked headline numbers, we have built a *worse* version of the thing we set out to fix: a transparency theatre. **Rule 1 for this project: no metric is published on the site unless a query produces it from live data.** I'd make this a build-time test.

---

# PART 1 — First principles: what a procurement system is *for*

Strip away software. A public procurement system exists to convert a budgeted public need into a delivered good/work/service, at the lowest whole-life cost, with **no party able to profit from asymmetry of information or of power.**

Everything else is derived. Four axioms follow:

**Axiom 1 — Corruption is an information problem before it is a morality problem.**
Georgia's reformers diagnosed exactly this: the fatal flaw was that *the state's budget estimate was secret*, so the only way a bidder could learn it was to bribe someone. Their fix was mandatory disclosure of the estimated price, and mandating the platform for every entity. Once every input was public, "the official has no restricted information to sell."
→ **Design rule:** publish the estimate, the criteria, the bidders, the scores, the award, and the variation. Secrecy is only ever justified pre-deadline, for sealed bids.

**Axiom 2 — A record that can be edited is not a record.**
Georgia made uploaded tender data **permanent and unchangeable**, specifically to stop the post-Soviet habit of editing documents overnight to disqualify a rival.
→ **Design rule:** append-only, hash-chained event ledger. No UPDATE on business facts. Corrections are new events.

**Axiom 3 — Trust must not depend on trusting the operator.**
The operator is the Ministry, and the Ministry is a party to the dispute. So the system must let an *outsider* verify without permission: public data, published methodology, independent appeals. Georgia added a stakeholder **freeze button** (auto-suspends a tender 10 days) and a **complaint committee half-nominated by civil society, which the agency cannot reject**. ProZorro's principle was literally *"everyone sees everything."*
→ **Design rule:** every internal control must have a public shadow — an export, a dashboard, or a complaint path.

**Axiom 4 — Adoption is a competition for the official's time.**
Systems die because using them is slower than the phone call. Kenya's e-GP reformer said resistance "will not be tolerated"; Georgia legislated mandatoriness. But mandate alone creates fake compliance (uploading PDFs of paper). The durable answer is **the platform is easier than the alternative for the officer, the contractor, and the auditor simultaneously.**
→ **Design rule:** instrument *time-to-award* and *clicks-to-bid* as first-class KPIs. GeM's own review noted supplier-side onboarding friction as a live failure mode — we measure it per LGA.

**Derived objective (the north star):** *A contractor in Wukaro should be able to learn of a tender, qualify, bid, and be paid without meeting a single official, and a journalist in Jalingo should be able to reconstruct any contract's full history without asking permission.*

---

# PART 2 — Lessons from the systems that actually worked

| System | What it proved | What we copy |
|---|---|---|
| **Georgia** (state-centred) | Fewest possible procedures (open tender, direct contract, design contest) + disclosed estimates + immutability → **GEL 1bn saved 2011–2016** | Radical method simplification; permanent records; freeze mechanism; CSO seat on complaints |
| **ProZorro / OpenProcurement** (Ukraine) | **Hybrid**: one central database + API, 17+ independent commercial "eMalls" competing on UX. Python, Apache-licensed, OCDS in the core. Continuous OCDS releases on every state change. **~$3bn saved in 4 years**; 35k entities / 165k suppliers | Don't build the UI monopoly — build the **API + central ledger**, let front-ends compete later. Publish a release *per event*, not a nightly dump. Risk-indicator engine scanning live tenders |
| **GeM** (India) | For **common-use goods**, a *catalogue marketplace* beats tendering: direct purchase, L1 bid, reverse auction, **CRAC** receipt before payment, TReDS invoice financing for small vendors, MSE/startup/women tags, GSTIN+PAN verified. **15–20% average savings**; orders 40–60% faster. Caveat from its own audits: brand-spec capture, inflated catalogue prices pre-auction | A **fast lane** for repeatable goods (medicines, furniture, generators, ICT) with catalogue + L1; mandatory CRAC/acceptance gate before payment certification; publish price-reasonableness checks |
| **Moldova MTender** | Built on the same open toolkit → **€25m in year 1**; proof that the ProZorro pattern is portable to a small, low-capacity state | Open-source core, not a bespoke vendor product. Reduces both cost and hostage-risk |
| **Kenya e-GP** | Legal mandate (s.7 PPADA 2015) + integration with iTax/BRS/IFMIS + "only procurements processed through the platform will be sanctioned and paid"; 60% of budget; supplier onboarding/training/help desk/webinars | **The payment lock** — the single most effective adoption lever. Also: a real training programme is part of the deliverable, not marketing |
| **Nigeria PPA 2007 + BPP (2025 thresholds)** | Method and approval ladder already defined federally | Our workflow must *enforce* the ladder, not merely describe it |

**Nigeria's rules the engine must encode** (federal BPP, revised and effective per its May 2025 announcement — **Taraba's state law may set different figures → all thresholds become DB config, never code** [CONFIRM against the Taraba State Public Procurement Law 2012 and any amendment]):

*Method by value:* ICB/NCB — goods ≥₦1bn, works ≥₦5bn. NCB — goods ₦30m–₦1bn, works ₦50m–₦5bn, non-consultant services ₦30m–₦1bn. RFQ — goods/services <₦30m, works <₦50m. Shopping <₦10m. Direct/single-source <₦5m. Least-cost consultancy <₦100m.
*Approval:* Accounting Officer/Perm Sec below ₦50m (goods) / ₦75m (works). MTB ₦50m–₦1bn (goods). BPP "no objection" ₦1bn–₦5bn. FEC ≥₦5bn (≥₦10bn works).
*Hard rules to encode:* award = **lowest evaluated responsive** bid (s.24(3)); bid security **≤2%** of bid price by bank guarantee above threshold (s.26(1)); quotations from **≥3 unrelated** suppliers, one quote each, **no negotiation** on a quotation (s.41); **no bidding by phone or unverifiable means** (Lagos PPA regs); debrief losers on request; **contract only on a plan backed by appropriated funds**.

---

# PART 3 — The domain model (this is the real design)

The screen is trivial. **The state machine is the product.** Get it wrong and no amount of React fixes it.

## 3.1 Procurement lifecycle

```
BUDGET LINE ──▶ ANNUAL PROCUREMENT PLAN ──▶ plan published (T-0, start of FY)
                          │
                          ▼
                    PROCUREMENT REQUEST (MDA) ──▶ funds-availability check ──▶ APPROVED / REJECTED
                          │  (hard gate: no appropriation, no tender)
                          ▼
              METHOD DETERMINATION (auto from threshold table + value + lot size)
                          │
                          ▼
        ┌─────────────────┴──────────────────┐
        ▼                                    ▼
  CATALOGUE FAST-LANE                    COMPETITIVE
  (published specs,                     (open tender / RFQ /
   ≥3 quotes auto, L1)                    2-stage / direct*)
        │                                    │
        └────────────┬───────────────────────┘
                     ▼
       TENDER PUBLISHED  ◀── immutable from here; amendments = new versioned addendum
       { notice, docs, estimate ₦, criteria, Q&A window, deadline }
                     ▼
       CLARIFICATION WINDOW   (any Q&A published to ALL bidders; deadline auto-extends
                               if a material addendum lands <7 days out)
                     ▼
       SUBMISSION CLOSED      (bids sealed: ciphertext at rest, key held by
                               threshold-of-custodians; nobody can peek, incl. admins)
                     ▼
       PUBLIC BID OPENING     (date/time/place published; attenders registered;
                               minutes + price schedule published ≤24h)
                     ▼
       EVALUATION             (committee formed at opening, not before;
                               criteria locked to published weights;
                               per-bidder scorecards; dissent recorded)
                     ▼
       AWARDS RECOMMENDATION ──▶ APPROVING AUTHORITY per threshold (auto-routed)
                     ▼
       AWARD NOTICE + REASONS PUBLISHED     losers may request debrief (SLA 10 working days)
                     ▼
       OBJECTION WINDOW (10 days) ──▶ independent panel if triggered → tender frozen
                     ▼
       CONTRACT SIGNED ──▶ contract summary public (parties, value, duration, deliverables)
                     ▼
       IMPLEMENTATION: milestones, variations, claims, advances/guarantees
                     ▼
       DELIVERY & ACCEPTANCE  (CRAC-equivalent certificate, inspected by named officer)
                     ▼
       PAYMENT CERTIFICATION  ──▶ only here can Treasury release funds
                     ▼
       COMPLETION / DEFECTS-LIABILITY CLOSE-OUT ──▶ performance rating published on supplier
```
`*` Direct procurement is a **system-enforced exception with published justification + DG sign-off + a public running total of direct-spend as % of state spend.** This one number, published monthly, is more useful than a hundred dashboards.

## 3.2 Supplier identity as a *verifiable credential*, not a form

Taraba already asks RC + TIN + documents with expiry dates. Go further:

```
SUPPLIER PROFILE (versioned, append-only)
 ├─ legal identity ....... RC/CBN number ──▶ verified against CAC registry (API or
 │                                          documented manual check) ──▶ {status, evidence, date}
 ├─ tax .................. TIN ──▶ format + FIRS state-of-origin check ──▶ cert + expiry
 ├─ pension .............. PenCom certificate + expiry
 ├─ beneficial ownership .≥5% owners (names, RC of corporate holders, political-exposure flag)
 │                       ──▶ cross-check vs company registry for common owners across bidders
 ├─ eligibility .......... debarment list, conflict-of-interest declaration,
 │                       next-of-kin/related-party declaration (PPA s.18/19)
 ├─ capability ........... years trading, similar contracts, staff, plant ──▶ tier/category A/B/C
 ├─ scope ................ local (Taraba LGA of origin) / national ──▶ drives local-content rule
 └─ money ................ bank account in the company's own name ──▶ NUBAN name check
                          ──▶ this is how you kill the "consultant who collects for many firms"
```
Every verification is a typed, dated, attributable assertion — so the site can honestly print *"verified against CAC on 3 Sep 2026"* instead of *"documents on file."* Suppliers are never "approved once, trusted forever": **re-verify annually, auto-suspend on expiry** (Taraba's `expiry_date` field shows the author already understood this — build on it).

## 3.3 The two flows the Kano/Taraba class of systems never have

**(a) Evaluation is a workflow with custody, not a spreadsheet.** Committee members are *named and published at bid opening*; each criterion has a locked weight inherited from the published document; scores are entered per-bidder with a mandatory narrative; disagreement above a threshold auto-escalates; the full (redacted) evaluation report is published with the award. **You cannot award without a published reason.**

**(b) Payment certification is inside the system.** The single highest-leverage design choice available to you: `PAYMENT CERTIFICATION` is generated only by the platform, referencing a valid award + accepted CRAC, and the state's Finance/Accountability policy is *"no platform record, no payment."* Kenya did exactly this. It converts a nice-to-have into physics.

## 3.4 Anti-collusion engine (the "100×" feature nobody else in Nigeria has)

Georgia let stakeholders freeze a suspicious tender for 10 days; ProZorro runs an automated risk-indicator scanner over live tenders. Implement as ~12 rules computed in SQL over the ledger, each with a public definition page:

1. **Clustered bids** — bids within 0.5% of each other across ≥3 bidders.
2. **Losing-spin** — a bidder who appears in ≥5 tenders and never wins (cover bidder).
3. **Shared ownership** — two bidders with common beneficial owner/phone/bank/address.
4. **Winner rotation** — suspicious round-robin among a fixed set in one LGA/category.
5. **Specification capture** — criteria naming a brand, model, or a spec only one bidder holds.
6. **Threshold shaving** — value repeatedly set just under the next approval tier (₦49.8m…).
7. **Single-bid rate** by MDA/method (with national comparison).
8. **Late-night submissions** — all bids in the last 3 minutes before close.
9. **Estimate-to-award ratio** — awards systematically at 98–99.9% of published estimate.
10. **Variation inflation** — post-award value growth >10% at signature of contract.
11. **Cycle-time anomaly** — tender to close in <7 days for a competitive method (short-cutting).
12. **Direct-procurement drift** — share of state spend via direct over 5%, or rising.

Output: a **public red-flag dashboard** per tender ("this tender triggers 2 indicators"), plus a private queue for the Bureau. Published indicators mean the tool can't be quietly switched off.

---

# PART 4 — Architecture

## 4.1 The shape of it: OCDS-native, event-sourced, API-first, modular monolith

**Principle: the database *is* the public dataset.** Not "an export module bolted on later" — that's how OCDS feeds rot, and how Kano/Taraba ended up advertising a JSON link that 404s.

```
                     ┌───────────────────────────────────────────────────┐
                     │  PUBLIC LAYER  (no auth, cacheable, CDN-friendly) │
                     │  web: SSR + progressive enhancement               │
                     │  /tenders /awards /suppliers/{id} /contracts/{id} │
                     │  /api/v1/releases  /api/v1/ocds/{ocid}.json       │
                     │  /bulk/{year}.json.zip   /feed/tenders.rss         │
                     │  /dashboards  /status                              │
                     └───────────────────────┬───────────────────────────┘
                                             │  read models (projections)
   ┌─────────────────────────┐   ┌───────────┴────────────┐   ┌──────────────────────────┐
   │ BUYER WORKSPACE (MDAs)  │◀─▶│      CORE SERVICE      │◀─▶│ SUPPLIER WORKSPACE       │
   │ plan → request → tender │   │  modular monolith      │   │ profile → bid → contract │
   │ evaluation → award      │   │  (one deployable)      │   │ → invoice → payment      │
   └─────────────────────────┘   │  modules:              │   └──────────────────────────┘
                                 │  · identity & roles    │
   ┌─────────────────────────┐   │  · supplier registry   │   ┌──────────────────────────┐
   │ APPEALS / OVERSIGHT     │──▶│  · catalogue & quotes  │──▶│ INTEGRATION GATEWAY      │
   │ committee panel, SLA    │   │  · tender & documents  │   │ CAC · FIRS/TIN · NUBAN   │
   │ whistleblower intake    │   │  · sealed submission   │   │ Treasury/IFMIS · bank    │
   └─────────────────────────┘   │  · evaluation engine   │   │ SMS/USSD · eIDAS-style   │
                                 │  · award & contract    │   │   signing service        │
   ┌─────────────────────────┐   │  · payments cert.      │   └──────────────────────────┘
   │ RISK ENGINE             │──▶│  · notifications       │
   │ 12 indicators, cron+live │   │  · audit & ledger    │
   └─────────────────────────┘   └────────────────────────┘
                                 ▲
                    ┌────────────┴─────────────┐
                    │ APPEND-ONLY LEDGER        │  Postgres, hash-chained
                    │ events(tender_id, seq,    │  prev_hash = sha256(row)
                    │  type, actor, payload,    │  nightly anchor → publish
                    │  time, signature)         │  root hash publicly (WotM)
                    └───────────────────────────┘
                    ┌───────────────────────────┐
                    │ OBJECT STORE (S3-compat)   │ documents: AES-256, no public ACL,
                    │ bid vault: envelope crypto │ time-limited signed URLs
                    └───────────────────────────┘
```

**Why a modular monolith and not microservices.** This is the single most consequential architecture decision, so the reasoning is explicit:
- Your team is small, in a low-capacity environment, with 121 MDAs and one deadline. Microservices convert every bug into a distributed-systems investigation and every deploy into a roll-out plan. **A monolith with hard module boundaries** (own schemas, no cross-module table access, an internal event bus, contract-tested seams) gives you 90% of the isolation and none of the operational tax.
- ProZorro/Moldova's OpenProcurement toolkit is a *modular Python monolith*. Georgia's system is state-centred. GeM is one SPV-operated platform. **No successful low-resource e-GP system started microservices.**
- The seam where you *do* split, from day one: **the public read layer and the ledger.** If adoption explodes, or you later want 17 competing front-ends like ProZorro, the API and the projections are already separable. You defer the decision rather than paying for it.

## 4.2 The ledger and immutability (Axiom 2 made physical)

- One table, `event`. Business facts are *derived* by projection, never hand-edited.
- Each row includes `prev_hash`; a background worker re-verifies the chain hourly and publishes the head hash. **Any editor of the database becomes detectable — including a DBA with the state's credentials.** This is how you make "the Ministry can't quietly rewrite a tender" a technical fact rather than a policy.
- Nightly: publish the day's root hash to a public, third-party-anchored location (a timestamping service / OCDS publishing channel). Cheap, and it defeats "we lost that database during the migration."
- Deletion never happens. Redaction (commercially sensitive bid annexes) is applied **at the read model**, with the redaction itself logged and its rule published.
- Practical: Postgres 16, `logical decoding` → outbox → projections; no Kafka. At 121 MDAs you'll see low thousands of tenders/year — the problem is not throughput, it's correctness and continuity.

## 4.3 Sealed bids — the one genuinely hard crypto problem

Deadline integrity is *the* trust event. Design:
1. Bid payload encrypted client-side→server-verified with a **per-tender symmetric key**, itself encrypted to a **key-share set** (Bureau DG + Chief Judge nominee + PCACC nominee + Accountant-General, 3-of-4) using each custodian's public key.
2. Server stores only ciphertext + a **commitment hash** of the bid (published at submission as a receipt). At opening, custodians submit their shares → key released → every bid's hash is verified against its published commitment **on screen, in the minutes**.
3. Consequences: nobody — no admin, no root shell, no vendor — can read a bid early. And any substitution is provable by hash mismatch, publicly.
4. Non-repudiation receipt to the bidder: signed `submission_id`, timestamp, hash — so a bidder can prove they submitted what and when, killing "your upload arrived after close" disputes.

If 3-of-4 ceremony is too heavy for v1, the honest fallback is **server-side encryption with an HSM/KMS key + policy that decryption before opening time requires two named roles**, plus publishing the commitment hashes immediately. Document whichever you choose; don't ship "sealed" that is merely "hidden behind a permission flag." (Taraba/Kano both currently do the latter.)

## 4.4 Public API as a first-class product (ProZorro's real lesson)

```
GET /api/v1/tenders?status=open&method=ncb&mda=health&min=5e6&max=1e9&updated_since=...
GET /api/v1/releases?cursor=...           → NDJSON of OCDS 1.1/1.0 releases, continuous
GET /api/v1/ocds/{ocid}.json              → full contracting process, one document
GET /api/v1/awards?year=2026&format=ocds  → award release per OCDS "award" stage
GET /bulk/{fy}.json.zip                   → whole-year dump for CSOs/analysts
GET /openapi.json                         → the API's own spec, versioned, no auth
```
Rules: no auth for reads; cursor pagination; `ETag`/`Cache-Control: public, max-age=300`; JSON Schema published per release; `ocid` format `TAR-{MDA}-{FY}-{seq}` (stable forever, and **the same string used in URLs, PDFs and the ledger**); **the same schema both validates input and generates output** so a malformed record cannot be created. Include a `taraba` extension block (LGA, local-content flag, PenCom/TIN verification status) via OCDS's extension mechanism — never fork the core.
Ship a **reference consumer in the repo** (a 100-line Python fetcher) so the API can never silently rot: CI publishes a tender, fetches it back through the public API, and asserts OCDS validity against the official schema.

## 4.5 Security posture — measured, not aspirational

What's verifiable today on Taraba: **no HSTS**, CSP = `upgrade-insecure-requests` only (i.e. effectively none), `x-powered-by: PHP/8.2.33` leaked, no MFA (`/login` accepts email+password only), and a `/storage`-class misconfiguration pattern is possible. Baseline for ours:

| Control | Implementation | Why it's load-bearing here |
|---|---|---|
| HSTS | `max-age=63072000; includeSubDomains; preload` + submit to preload list | Kano's 3-month lapses prove certs rot silently |
| TLS automation | ACME with 21-day alarm, renewal on a *separate* credential, public `/status` showing cert expiry | The "cert issued the day before the site went 503" failure mode |
| Auth | password + **TOTP required for all buyer/admin roles**, WebAuthn optional for high-value actions; **SMS OTP fallback for suppliers** (smartphone ownership is not universal in Taraba) | Suppliers will reuse one password across a state; officials will share logins unless MFA forbids it |
| Session | short-lived access (15m) + rotating refresh, device list, IP-anomaly alert, concurrent-session cap | Shared "the ministry's account" is a real pattern |
| RBAC | ABAC-flavoured: role × MDA × stage × value-band; **evaluator sees only assigned tenders, only after opening**; segregation enforced (requester ≠ evaluator ≠ approver ≠ certifier) | The whole corruption surface is role overlap |
| CSP | `default-src 'self'`; no inline script; `frame-ancestors 'none'`; report-only first, then enforce | Kills the stored-XSS-becomes-bid-tampering path |
| Upload | magic-byte verification (not extension), **re-encode images**, `pdfinfo`/`qpdf` structural check, ClamAV, no SVG, 25MB, private bucket, per-object signed URL ≤300s | Both existing sites accept by extension only |
| Files | hash on ingest, store hash in the ledger, publish bid commitment hash | Immutability of evidence |
| Rate/abuse | per-IP+per-account throttle with `Retry-After`, exponential lockout, CAPTCHA only on failure, honeypot on registration | Prevents the enumeration/brute pattern |
| Audit | every read of a sealed object is an event, visible in a transparency log | "Who looked at the bids?" must be answerable |
| DNS | **fix the parent zone**: request proper `tr.gov.ng`/`bpp.tr.gov.ng` delegation from NIRA, TTL 300, two resolvers, no orphaned-glue-only subdomains | This is the Kano catastrophe, latent in Taraba today |
| Contact reality | every phone/email on the site is *called/emailed by the release checklist* before go-live; unsubscribe/monitor the mailbox | `XXX XXXX` and a no-MX domain are shipped-placeholders; a red line in CI |
| Supply chain | lockfile + `pip-audit`/`npm audit` in CI, SBOM published, **no `@tailwindcss/browser`-class CDN-at-runtime**, no fonts from third parties at request time | Kano's stock `/up` page and CDN runtime show an unhardened default build |
| Vendor lock-out | all source in a state-owned repo, docker images in state registry, **encrypted nightly export** to state storage, documented restore runbook rehearsed quarterly, and a contract clause: *state may operate the system with 30 days' notice without the vendor* | The Kano portal runs on a vendor's parked domain, on a Hostinger VPS, in a shared-tenant app. Don't repeat it |

## 4.6 Designing for Taraba's actual network (this is where "100×" is won or lost)

Jalingo, Wukaro, Bali, Takum, Gembu: intermittent 3G, expensive data, many suppliers with a feature phone and one shared email. Assume this and the system gets used; ignore it and you build a portal for the capital's consultants.

1. **SMS/USSD tender alerts** — free opt-in "TDR WATCH <category>" → weekly digest + deadline-day reminder. Cost is trivial; it's the single most-used feature in African e-GP deployments.
2. **Zero-rating negotiation** with the three MNOs for the public domain (Kenya/GeM both lean on this).
3. **Lite endpoints**: `?format=text` and `<60KB` HTML pages (no webfonts, no hero video, lazy everything). Target: tender list in **<2s on 3G**, bid page usable on a 4-inch screen.
4. **Offline-capable PWA** with a local queue: fill the bid form on a phone at home, submit when the signal appears; resumable multipart upload with `Upload-Length`/offsets for a 20MB BOQ.
5. **WhatsApp Business API** for status notifications ("your bid received, ref X, commitment hash Y") — the message the bidder will trust more than email.
6. **Agent-assisted bidding**: physical desk at the Bureau + each Senatorial zone hub, whose actions are the *same* API with a `acted_on_behalf_of` field — never a separate, unaudited back channel.
7. **Hausa + English** toggle from day one (not a translation patch): all labels in a JSON dictionary, one screen tested end-to-end in Hausa before go-live. Taraba is one of the most multilingual states — add Jukun/Wuki and Tiv glossaries for the key legal notices **[CONFIRM]**.
8. **Print/PDF-first bid packs** — many officials still sign paper. Every screen has a clean print stylesheet, and every submission produces a signed PDF receipt.
9. **Data-saver mode** as default for anonymous visitors; media pre-rendered at low bpp.

---

# PART 5 — Tech stack, and why

**Recommendation: Python 3.12 / Django 5 + Celery (or a worker on `asyncio`) + Postgres 16 + DRF + django-pgnoupdate-free immutable constraints, on infrastructure you control.**

Why Django rather than the current PHP/Laravel:
- **Migrations + ORM + admin + auth + password policy + CSRF + template escaping are already correct and audited.** You are not building a startup; you're building a compliance system where the boring parts must be right.
- The **OpenProcurement toolkit (ProZorro, MTender, Rialto) is Python and Apache-licensed**. Dipping into that design (their tender-state machine, their OCDS mapping, their key-management ceremony) is a genuine accelerant — and it's the only proven open-source core for this exact problem class. Consider *studying and reusing patterns/licence-clean components*, not forking: Nigerian legal workflows, TIN/CAC integration, and local-content rules are yours.
- Python for the **risk engine** (pandas/`statsmodels` for bid-clustering tests) beats doing it in either framework's view layer.
- If your team is PHP-strong, **Laravel is a legitimate second choice** (Taraba's current app shows the vendor already writes careful PHP) — but then you must hand-build the ledger, RBAC and OCDS serialiser, and you lose ProZorro code adjacency. State the reason in the ADR and move on.

Concrete choices:

| Concern | Choice | Note |
|---|---|---|
| Framework | Django 5 LTS track | one deployable, module per bounded context |
| API | DRF + `drf-spectacular` → OpenAPI 3.1 published at `/openapi.json` | schema generated from the same serializers that write data |
| DB | Postgres 16, one primary + streaming replica, PITR backups | `citext` for names, `btree_gist` for exclusion constraints, partition `event` by year |
| OCDS | `ocdskit`-style validation against official JSON Schema; extensions in `taraba.json` | validation failure = HTTP 422, never a silent save |
| Search | Postgres FTS v1 (`tsvector` + trigram for RC/TIN); Meilisearch only if volume demands | don't start with Elasticsearch |
| Files | MinIO/S3-compatible, SSE-KMS;ClamAV daemon; re-encode via `libvips`/ghostscript | private by default |
| Cache/queue | Redis + Celery (or `django-tasks`) | outbox pattern for all external effects |
| Notifications | Africa's Talking / Termii for SMS+USSD; SMTP with DSN capture | one gateway, behind a provider interface |
| Crypto ceremony | age/PGP-style recipients; `hashlib` chains; optional HSM later | document the 3-of-4 runbook with the actual names |
| Front-end | Django templates + htmx + Alpine; PWA manifest; no SPA | SSR = indexable by Google, cheap on 3G, works with JS off |
| Deploy | Docker Compose on 2 VMs + object storage + managed Postgres; **no "parked-domain" vendor box**; IaC in git (Terraform/Ansible) | Kano: `srv1780802.hstgr.cloud`. Fix this at the contract level |
| CI/CD | GitHub Actions: lint, tests, **OCDS schema validation**, **link/redirect integrity test**, **security-header test**, **cert-expiry probe**, secret-scan, SBOM | every Kano failure mode becomes a pipeline assertion |
| Observability | Sentry + Prometheus (blackbox probes on `/status`, TLS expiry, RSS of tender feed) + uptime monitoring from *outside* Nigeria | the 503-for-months problem is an alarm problem |
| Data pipeline | nightly `COPY` → Parquet to public bucket; DuckDB/DBT for dashboards | analysts get bulk without hitting prod |

**Testing strategy (what makes "verified" mean something):** property-based tests on the state machine (no illegal transition reachable); golden OCDS files per release type; **concurrency tests on bid submission and opening** (two bidders at T-1s, clock skew, replayed receipts); a "chaos Saturday" rehearsing cert expiry, DB restore, and full data egress to a second cloud; and a **red-team brief before each phase gate**.

---

# PART 6 — Schema sketch (the load-bearing 12 tables)

```sql
-- identity & supplier
party(id, ocid_party_id, kind, legal_name, rc_number, tin, ubn,
      country, state, lga, created_at)                     -- unique: rc_number, tin
party_verification(party_id, kind, status, evidence_ref,
                   verified_by, verified_at, expires_at)   -- kind: cac|tin|pension|bank|bo
party_ownership(party_id, owner_name, owner_rc, pct, pep_flag)
supplier_category(supplier_id, category, class)             -- A works | B goods | C services, tiers
eligibility_status(party_id, debarred_from, debarred_to, reason_ref)

-- money & rules (config-driven, versioned!)
threshold_rule(fy, method, value_band numrange, min_amount, max_amount,
               approval_body, security_pct, min_advert_days)  -- bumpted, never edited
budget_line(id, fy, mda_id, programme, project_code, amount, source)

-- the procurement process
plan(id, fy, mda_id, published_at, doc_ref)
request(id, mda_id, budget_line_id, title, description, est_value, method_code,
        status, created_by, approved_by, approved_at)
tender(id, ocid, request_id, method, status, est_value, lot_split, security_amount,
       published_at, qa_close_at, submission_close_at, opening_at,
       commitment_root, immutable_from, doc_hash)
tender_document(tender_id, version, kind, sha256, obj_key, published_at)  -- addenda = new version
tender_question(tender_id, asked_at, question, answer, answered_at, affects_deadline)
lot(id, tender_id, seq, title, qty, unit, est_value)
bid(id, tender_id, lot_id, supplier_id, amount, commitment_hash, ciphertext_ref,
    submitted_at, received_hash, status)                    -- UNIQUE(tender_id,lot_id,supplier_id)
bid_criterion_score(bid_id, criterion_id, raw, weight, narrative, evaluator_id, scored_at)
criterion(tender_id, code, name, weight, is_pass_fail, min_score)
evaluation_committee(tender_id, member_id, role, formed_at)  -- formed_at MUST be >= opening_at
award(id, tender_id, lot_id, bid_id, amount, reason, approved_by, approved_at,
      notice_ref, published_at)
contract(id, award_id, ref, signed_at, value, duration_days,
         advance_pct, perf_guarantee_pct, status)
contract_event(contract_id, kind, amount, note, occurred_at)   -- variations, claims, milestones
certification(id, contract_id, kind, crac_ref, value, certified_by, certified_at) -- gates payment
objection(id, award_id, party_id, filed_at, ground, panel_ids, frozen_until, outcome, decided_at)
red_flag(tender_id, indicator, detail, computed_at, public)
```
**Invariants the DB must enforce, not the app:**
- `UNIQUE(tender_id, lot_id, supplier_id)` on `bid` → one bid per bidder (PPA s.41(3) for quotations too).
- `CHECK (formed_at >= opening_at)` on `evaluation_committee` → the committee cannot exist before opening. This single constraint deletes an entire class of rigging.
- `est_value IS NOT NULL` on `tender` → no tender without a published estimate (Georgia's core fix).
- `submission_close_at - published_at >= min_advert_days` per method → no short-cut advertsing (Lagos rule: min 2 weeks for NCB).
- `request.budget_line_id NOT NULL` → **no tender without an appropriated budget line** (PPA s.23).
- `award.bid_id` must reference a `bid` with `status='evaluated'` and score completeness.
- `event` table: `INSERT`-only via privileges + triggers, `prev_hash NOT NULL`, and `created_at NOT NULL DEFAULT now()`; app role has no `UPDATE/DELETE` grant on any `*_document` table.
- `certification` requires an accepted `contract_event(kind='crac')` → no payment without inspection.

---

# PART 7 — Delivery: how to actually get this live and keep it alive

## Phase 0 — Ground truth (4 weeks, do not skip)
This phase is why Kano is empty and Taraba shows ₦0.00.
1. **Read the law.** Taraba State Public Procurement Law 2012 + amendments; extract the *actual* thresholds, committee compositions, advert periods, security %, and the Bureau's powers. Put it in a public `RULES.md` and encode as `threshold_rule` rows. **[CONFIRM: I have not been able to read the Taraba state law text; the numbers in Part 2 are the federal BPP 2025 revision.]**
2. **Interview the five stakeholder groups Georgia says must all be satisfied:** Bureau DG + 3 officers, 2 Permanent Secretaries, 6 contractors (2 local/LGA, 2 national, 1 who currently wins a lot), 1 CSO/journalist, 1 Accountant-General/Treasury rep. Ask one question: *"walk me through the last tender you did, and where you had to call someone."*
3. **Fix the boring existential risks immediately, before a line of code:** proper `tr.gov.ng` delegation at NIRA, TLS auto-renewal with alarm, real phone + working mailbox with MX, and remove the unbacked ₦48.7bn/100%-disclosed claims or make them true.
4. Decide the **payment lock** with Finance. If Finance won't say "no platform record, no payment," this project becomes a website. **This is the go/no-go gate.**

## Phase 1 — Transparency spine (6–8 weeks) → *ship publicly*
Publication-first. No login required for anything read-only.
`/tenders`, `/awards`, `/contracts/{id}`, `/suppliers/{id}`, OCDS release model, bulk dump, public API + OpenAPI, permanent URLs, **backfill 3–5 years of historic awards from MDA records** (the highest-credibility act available; the reason SolaceBase could write about Kano's neglect was that data existed *somewhere*).
Deliverable that proves it works: a journalist reconstructs a real 2025 contract from the API alone, in their office, without contacting anyone.

## Phase 2 — Supplier registry + sealed e-bidding (8–12 weeks)
Credential model with CAC/TIN/NUBAN/PenCom verification; the crypto-sealed submission + opening ceremony; the bid-receipt PDF; SMS alerts; Hausa UI; agent-assisted desk. **Migrate Taraba's existing registered contractors** rather than asking them to re-register — their `/register` resume-code flow is already decent; keep the field names.

## Phase 3 — Evaluation, award, objections (6–8 weeks)
Committee workflow with locked criteria weights, scorecards, dissent capture, auto-routing to the approving body by threshold, published reasons, debrief SLA, objection filing + freeze + independent panel with the CSO seat.

## Phase 4 — Contract → CRAC → payment certification (8–10 weeks)
Milestones, variations with the >10% alarm, advance/performance guarantees, acceptance certificate, certification record feeding Treasury, supplier performance ratings published. **This is the phase that changes behaviour**; 1–3 change what people see.

## Phase 5 — Marketplace + risk engine (ongoing)
Catalogue fast-lane for repeatable goods (medicines, furniture, ICT, vehicles) with ≥3-quote auto-comparison and L1; reverse auction for high-volume commodities; **red-flag indicators go live public**; price-reasonableness reference dataset (GeM's lesson: publish the benchmark, or catalogue sellers inflate before the auction).

## Team & cost envelope (realistic for a state)
1 tech lead (you) + 2 Django/backend + 1 front-end/a11y + 1 devops/security (0.5) + 1 procurement-domain analyst (the most important hire, and the one always omitted) + 0.5 trainer/community manager. **~5.5 FTE for 12 months.**
Runway: 2 VMs + managed Postgres + object storage + SMS credits + a real WAF ≈ **₦3–6m/yr** at this scale. That number is the argument: Kano/Taraba's current setup costs a fraction of one contract's savings, and Georgia-type reforms show **5–10% of procurement value** as the realistic prize — on a state budget where capital spend alone runs into hundreds of billions of naira, the platform is rounding error against its own upside.

## The 90-day credibility sprint (if you can only do one thing)
Weeks 1–4: DNS/TLS/contacts fixed + law encoded + historic awards **extracted from MDAs into a CSV** and published as an OCDS bulk file. Weeks 5–8: public tenders/awards pages + API + permanent URLs, on infra *you* control. Weeks 9–12: supplier registry with CAC/TIN verification live, SMS alerts live, `/status` page live, ₦48.7bn claim replaced with measured numbers.
**Outcome: more real transparency in a quarter than the Kano platform has produced in its lifetime — and it is un-cancellable, because the data is the product.**

---

# PART 8 — "100× better": acceptance test against Kano

Every row is a check that can run in CI or a scripted external audit. A phase isn't done until its rows pass.

| # | Assertion | Kano (measured) | Pass condition |
|---|---|---|---|
| 1 | Open tenders published | 0 | ≥1 live tender with downloadable pack |
| 2 | Award register | 0 | ≥50 historic awards queryable by MDA/LGA/year |
| 3 | Total award value | ₦0 | > 0, and equals `SUM(award.amount)` in prod |
| 4 | Published estimate per tender | absent | every tender has `est_value`, enforced NOT NULL |
| 5 | Machine-readable data | advertised, 404s (Taraba) | `/api/v1/releases` returns valid OCDS; CI validates against schema |
| 6 | Bulk access | none | `/bulk/{fy}.json.zip` < 60s to download |
| 7 | Immutability | none | ledger chain verifies; no `UPDATE` grant on docs tables |
| 8 | Tender detail without login | none | `GET /tenders/{ocid}` → 200 anonymous |
| 9 | Supplier verification | none (no RC/TIN) | every profile has dated CAC + TIN assertion |
| 10 | Beneficial ownership | none | ≥5% owners recorded; shared-owner check runs |
| 11 | Anti-collusion | none | 12 indicators computed, published per tender |
| 12 | Appeals | none | file objection online, panel + decision published, freeze enforced |
| 13 | Whistleblower | page exists (untested) | intake with anonymous handle + tracked case ID |
| 14 | Advert-period rule | none | `CHECK` on `tender` rejects under-period adverts |
| 15 | Committee-after-opening | none | DB `CHECK` constraint |
| 16 | Bid security handling | none | guarantee captured, expiry tracked, refund recorded |
| 17 | Contract→payment link | none | certification exists, gated by CRAC |
| 18 | Local content | scope field only | LGA/state-of-origin recorded; ratio published per MDA |
| 19 | Language | English only | full Hausa UI on the bidder's 3 core screens |
| 20 | Low-bandwidth | JS-dependent | tender list <60KB, usable with JS disabled, LCP <2s on throttled 3G |
| 21 | Uptime/alarm | 503s unnoticed months | external monitoring + on-call rota; <15min alert |
| 22 | TLS continuity | 2 lapsed, wrong CN | 90-day auto-renew, CN correct, `/status` shows expiry, alarm at 21d |
| 23 | DNS control | parent undelegated | `tr.gov.ng`/`bpp.tr.gov.ng` properly delegated, 2 resolvers, tested |
| 24 | Contact reachability | `XXX XXXX`, no-MX email | release checklist calls the number and emails the address |
| 25 | Migration integrity | all old URLs 404 | link-rot test suite green; 301 map for every retired path |
| 26 | Security headers | no HSTS/CSP, `x-powered-by` | HSTS preload-ready, real CSP, `x-powered-by` gone, A+ on ssllabs |
| 27 | MFA | none | required for all buyer/admin roles |
| 28 | Openness of source | proprietary vendor SaaS | repo in state's control + published SBOM + restore runbook rehearsed |
| 29 | No unbacked claims | "100% disclosed" vs ₦0 | every homepage figure generated from live data at build time |
| 30 | Adoption | no MDAs | ≥60% of MDA spend value transacted on-platform by month 12 |

**Score yourself on rows 1–5 alone.** Kano = 0/5, Taraba = 0.5/5 (the register flow is real, the data isn't). Twenty green rows is not polish — it is the difference between a procurement system and a poster.

---

# PART 9 — The five risks that will actually kill this

1. **No payment lock.** Without Finance refusing to pay off-platform, MDAs will keep doing procurement by WhatsApp and use the portal as a filing cabinet. *Mitigate: Phase 0 gate; make it a Commissioner-level written instruction.*
2. **The change-of-government wipe.** Kano's portal history is a story of abandoned sites (`dueprocess.kn.gov.ng` is now NXDOMAIN). *Mitigate: the **data** is the asset; publish immutable bulk dumps to at least two external hosts (OCDS data hub, a university/CSO mirror, Internet Archive). If the site dies tomorrow, the record must survive — that's what makes it uncancellable.*
3. **Vendor capture on borrowed infrastructure.** Kano's cert names a parked `e-proc.cloud` on a Hostinger VPS; Taraba's runs on Hostinger shared hosting with `panel: hpanel`. *Mitigate: state-owned accounts, state-owned domains, state-owned repos, vendor as contractor-of-record with a rehearsed exit.*
4. **Rigging adapts.** Specification capture and clustered bids move *around* any static rule set. *Mitigate: publish indicator definitions (so they're socially enforced, not just quietly running), review quarterly, and never let the Bureau be the only party able to change them.*
5. **Fake adoption.** 121 MDAs "regulated" is a claim, not a fact (the current site pairs it with "2+ registered contractors"). *Mitigate: publish per-MDA utilisation and per-MDA direct-procurement share, monthly, unredacted. Sunlight on the laggards is the cheapest management tool you have.*

---

## Sources for Part 2 benchmarks
- *E-Procurement Lessons from Georgia, Ukraine, and Moldova*, Bogdana Depo, German Marshall Fund (Mar 2021) — Georgia's GEL 1bn savings 2011–2016, three-procedure simplification, immutability, 10-day freeze, CSO-half complaint committee; Moldova €25m (MTender, year 1); Ukraine ~$3bn (ProZorro, 4 years).
- Open Contracting Partnership, *ProZorro: how a volunteer project led to nationwide reform* (2016) and *OpenProcurement* — hybrid central-database + API + 17 marketplaces; OCDS-in-the-core; continuous releases; Apache-licensed Python toolkit (github.com/openprocurement).
- OECD-OPSI innovation case 70016, *ProZorro risk indicators* — automated live-tender risk scanning.
- GeM (India): GFR Rule 149 mandate; catalogue/L1/reverse-auction/CRAC/TReDS; GSTIN+PAN+bank verification; 15–20% savings; vendor-assessment via RITES/QCI; documented failure modes (brand-spec capture, pre-auction catalogue inflation, thin disaggregated transparency).
- Kenya Treasury, *e-GP* — PPADA 2015 s.7 mandate, integration with iTax/BRS/IFMIS, "only procurements processed through the platform will be sanctioned and paid for", 60% of budget, AGPO 30% reservation, OCDS-based design.
- Nigeria Public Procurement Act 2007 (s.23–26, 40–41) + BPP threshold revision announced May 2025 (₦5bn/₦10bn FEC lines, ₦1bn/₦5bn no-objection lines, RFQ <₦30m/<₦50m, direct <₦5m, 2% bid security, ≥3 unrelated quotations, no unverifiable bidding); Lagos State PPA Regulations (2-week minimum advertising, board composition, debrief).
- Open Contracting Data Standard 1.1 schema & extension mechanism.
- Measured live (this session, 20 Sep 2026): `bpp.tr.gov.ng`, `e-procure.bpp.tr.gov.ng` (DNS, TLS, headers, `/register`, `/login`, `/disclosure?year=2021..2026`, 7 OCDS path probes), and the Kano baseline (`procurement.kn.gov.ng`).
