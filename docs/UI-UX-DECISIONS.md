# Interface decisions: research, rules, and what was changed

This document records the interface work on the public procurement register: what
was researched, what was decided, what was rejected and why. It is written so a
reviewer can disagree with a specific decision and see the evidence behind it,
rather than being handed a list of preferences.

The engineering brief for this project is in `DESIGN-taraba-eprocurement.md`
(Volume 1–2). This file covers the layer that document left open: **the public
interface itself** — the landing page, the register, every record page, and the
rules/help surfaces.

---

## 1. Who the interface is for

Ranked by how much the interface decides for them:

| User | Device and connection | What they come to do | Design consequence |
|---|---|---|---|
| Small contractor in Wukari, Bali or Takum | Android phone, 3G or worse, data bought by the gigabyte | Find a tender, read the pack, submit before the deadline | Every page must work at 320px and on a slow link; weight is a monetary cost to this user, not a metric |
| Losing bidder | Any | Find out why they lost, decide whether to challenge | The reason, the scores and the objection route must be one page away from the award |
| Journalist or researcher | Laptop, spreadsheet open | "Who won what, from whom, and how does it compare with last year" | Filters in the URL, CSV of exactly the filtered set, permanent links |
| MDA officer | Desktop, authenticated workspace | Do the governed action | The public pages must never be a second, weaker system |
| Citizen with a concern | Phone, possibly a shared device | Report or check something without being identified | Report needs no account, no tracker, no third-party script |

This ranking is the source of most of the rules below. When a decision was
between "richer interface" and "works on the cheapest phone in the state", the
phone won.

---

## 2. Research that changed the design

Each entry: source → what it said → what changed here.

### 2.1 WCAG 2.2, not 2.1

Sources: [WCAG 2.2 additions](https://testparty.ai/blog/wcag-focus-appearance-minimum),
[2.2 vs 2.1](https://www.wcagsafe.com/blog/wcag-2-2-vs-wcag-2-1),
[audioeye](https://www.audioeye.com/post/wcag-22/).

* **2.5.8 Target Size (Minimum) is AA at 24×24 CSS px**; the familiar 44×44 is AAA
  (2.5.5). → Controls are built at **44px**, so the AAA bar is met on the
  audience's actual touch devices rather than the AA floor on a reviewer's
  laptop. Verified in `01-tokens.css` (`min-height: 44px` on inputs; 40px pill
  navigation is the one deliberate exception, compensated by spacing).
* **2.4.11 Focus Not Obscured (Minimum)** — a sticky header must not fully hide
  the focused element. → The masthead is not sticky, and the focus ring is 3px
  with 2px offset so it is never clipped by a container's `overflow: hidden`.
* **3.2.6 Consistent Help** — help must be in the same relative place on every
  page. → One help page (`/tenders/help/`), linked from the primary navigation
  *and* the footer on every page, with the telephone number printed in the
  footer. Asserted by `tests/test_ui_contract.py::test_help_is_in_the_same_place_on_every_page`.
* **3.3.7 Redundant Entry** — do not ask twice for what the user already gave. →
  The whistleblower form is the only public write form, and it re-renders with
  everything the reporter typed.
* Focus indicator recipe adopted: `outline: 3px solid; outline-offset: 2px`,
  checked in `forced-colors: active` too.

### 2.2 Error handling: the GOV.UK pattern

Sources: [GOV.UK validation](https://design-system.service.gov.uk/patterns/validation/),
[error summary](https://design-system.service.gov.uk/components/error-summary/),
[error message](https://design-system.service.gov.uk/components/error-message/).

* On failure: **re-render with the user's values intact**, prefix the page title
  with `Error:`, put an **error summary at the top** and move focus to it, list
  each error as a link to its field, and repeat the identical wording inline.
* Use `novalidate`, and do **not** rely on HTML5 `required` — the server decides.
* Never clear a field; never signal an error with colour alone.

→ Implemented in `apps/core/templates/whistleblower_intake.html`, with focus
moved to the summary by `autofocus` + `tabindex="-1"` so it works **without
JavaScript**. Asserted by
`test_whistleblower_errors_preserve_input_and_focus_the_summary`, which fails if
the reporter's text, the reference, the summary or the inline message is lost.

* Rejected: client-side validation as the primary mechanism. The users who most
  need this form are the least likely to have a browser that runs it, and a
  report that fails silently on a 2G connection is a report that never arrives.

### 2.3 Data tables on a phone

Sources: [data table UI](https://www.setproduct.com/blog/data-table-ui-design),
[UX patterns](https://uxpatterns.dev/patterns/data-display/table),
[responsive accessible tables](http://adrianroselli.com/2017/11/a-responsive-accessible-table.html).

* **Tables are an explicit exception to WCAG 1.4.10 Reflow**, so a horizontally
  scrollable table inside a focusable container is compliant *and* preserves the
  header/value relationships a screen reader needs.
* The popular "stack the rows and use `::before` content" trick is **not
  announced** by screen readers. → Not used anywhere.
* Decisions: keep native table semantics; wrap in an accessible scroll region
  (`.table-wrap` with `role="region"`, `aria-label`, `tabindex="0"`); freeze the
  first column so a row keeps its identity when scrolled; hide genuinely
  secondary columns below 640px (`.hide-xs`); every table gets a `<caption>`
  that says what the numbers mean and a `scope` on every header.
* Sort/filter state is in the URL, so it can be shared and re-fetched; the result
  count is printed in text, not announced by a live region that most users of
  this site will never hear.

Asserted by `test_every_table_has_a_caption_and_scoped_headers`.

### 2.4 Performance budgets for a data-priced audience

Sources: [mobile-first for African markets](https://lioncapventures.com/blog/building-mobile-first-web-apps-african-markets),
[low-bandwidth design](https://launchpad.ng/resources/africa-low-bandwidth-design),
[performance budgets](https://web.dev/articles/performance-budgets-101).

* Budget adopted: **HTML < 100 KB, stylesheet < 40 KB, no webfonts, no CDN, no
  analytics, no third-party script.** Baseline is a 3G connection on a
  Moto G4-class phone, and mobile data costs the user ₦1,000–3,000 per GB, so
  page weight is a cost the citizen pays.
* What this rules out, deliberately: webfonts (a single family is ~100 KB),
  icon fonts (SVG or nothing), charting libraries (bars are CSS), and any
  third-party script — a bundled analytics tag is both a data cost and a
  surveillance risk on a page people visit to report corruption.
* Enforcement: `test_html_stays_inside_the_page_budget` and
  `test_stylesheet_is_small_and_built_from_source` (which also fails if the
  built artifact is stale).

### 2.5 What an OCDS portal is actually asked

Sources: [Open Contracting publication](https://data.open-contracting.org/en/publication/92),
[OGP commitment](https://www.opengovpartnership.org/members/united-kingdom/commitments/uk0093/).

The recurring questions — *how many contracts does government hold with firm X,
how do terms vary across authorities, how close to completion is this, what is
the buy→pay chain for this money* — were used as the landing page's four entry
points and as the award/contract/payment page structures. Concretely:

* "Start here" on the landing page is organised by **question**, not by
  database table.
* Payment certification is a first-class public page, because the buy→pay link
  is the part most registers silently drop.
* The award page prints *why this bidder* and, where the winner was not the
  lowest bid, *why not the lowest* — the two questions a losing bidder asks.

### 2.6 Plain language for government copy

Sources: [plain language in government websites](https://digital.georgia.gov/blog-post/2023-02-09/theres-no-fine-print-in-government-websites),
plus this repository's own Axiom 3.

Every page states, in this order: **what this is → what the numbers mean → what
you can do → what the limits are.** Sentences are short and active. No page says
"invalid", "forbidden" or "sorry" (the GOV.UK wording rule). Empty states state
the fact rather than apologising or implying a fault.

---

## 3. The rules this interface now follows

These are enforced by tests or by the build, not by good intentions.

1. **No hand-typed figure.** Every number comes from `live_metrics()` or a
   queryset on the same page. A figure that disagrees with `/api/v1/stats` is a
   bug in the page.
2. **Colour is never the only carrier of meaning.** Every status, severity and
   deadline prints its word (`status_tag`, `severity_tag`, `deadline`).
3. **Money keeps its exact value.** Compact forms (`₦12.4m`) are `<data>`
   elements carrying the full figure in `value` and `title`.
4. **Every timestamp is labelled WAT**, and the deadline filter never claims more
   time than exists ("closes in 40 min", "closed 12 Sep 2026 WAT").
5. **One `<h1>` per page, one main landmark, a skip link, a declared language.**
6. **No inline script, no inline style, no `javascript:` URL** — the CSP has no
   `unsafe-inline` escape hatch, so anything inline would break in production.
7. **Filters live in the query string** and are shared by the HTML page and the
   CSV export, so the two can never disagree about what "the filtered set" is.
8. **Empty states are answers.** They state the fact and offer at most two ways
   forward; they never pretend a filter matched nothing because of an error.
9. **The help route is in the same place on every page** (SC 3.2.6).
10. **JavaScript is optional.** The one script (2 KB) adds copy-to-clipboard for
    hashes and a swipe hint on wide tables. Nothing else depends on it.

### Design tokens

`apps/core/static/css/src/01-tokens.css` holds colour, type, space and focus
tokens with the contrast rationale beside each one. Notable choices:

* **Taraba green + Sahel ochre**, on the reasoning that the palette should read
  as this state's civic identity rather than as a generic dashboard.
* **System font stack.** No webfont: on the target network a font is 100 KB of
  the user's data.
* **Dark mode via `prefers-color-scheme`**, with every semantic colour re-tuned
  rather than inverted, and focus colours that survive both surfaces.

### Composition

Sources: `02-layout.css` (page shell, grid, masthead, footer, breadcrumbs),
`03-components.css` (buttons, tags, callouts, tables, forms, steps, timeline,
chips, KPIs, snippets), `04-print.css` (print takes navigation and controls out
and expands captions), `05-utilities.css` (the small text/table vocabulary).

The build (`manage.py buildcss`) concatenates and minifies these into one
content-addressed stylesheet served at `/stylesheet?v=<hash>`, so a deploy
invalidates caches without asking anyone to hard-refresh.

---

## 4. What was changed in this pass

### Ledger history (`/tenders/<ocid>/`)
* The payload of each ledger event used to be rendered as a Python dictionary
  (`{'version': 1, 'kind': 'BOQ', ...}`). It is now printed as labelled facts:
  amounts as money, timestamps in West Africa Time, hashes shortened but kept
  complete in the DOM, booleans as words. The record is unchanged; only its
  readability is.
* The crawler now walks **every** tender, contract, supplier and MDA in the
  database, not one sample row per route — the leak above existed on the tenders
  that had documents and clarifications, which the single sample row did not.

### Landing page (`/`)
* Rebuilt around four entry points phrased as **questions a person has**, not as
  product sections.
* Live figures are links to the records behind them, each labelled with the time
  it was computed.
* The ledger-integrity claim is stated with the command to verify it
  (`manage.py verify_ledger`) and a link to the public head hash.

### Register (`/tenders/`)
* Filters (search, status, agency, method, sort, open-only) are a plain GET form;
  the URL *is* the state, so a filtered register can be sent on WhatsApp and
  arrive identically.
* Active filters appear as removable chips.
* The row of the register links to a permanent OCID URL, and the same filtered
  set is downloadable as CSV and JSON from the same page.

### Tender record (`/tenders/<ocid>/`)
* Ordered by the questions a bidder actually asks: what you need to know before
  you bid → where the process has got to (a dated state machine) → documents
  (with SHA-256 digests) → clarifications → bids (sealed vs revealed, with
  commitment hashes) → scoring (criteria, weights, committee declarations) →
  award (with the reason and the non-lowest justification) → objections →
  red flags with their arithmetic → the raw ledger history → machine-readable
  links.
* "At a glance" sidebar, a jump list, and verification links to the JSON and
  OCDS releases.

### Awards, contracts, payments, suppliers, MDAs
* Award register now prints the **method mix** (the share of value awarded
  without competition is the number worth watching) and the difference between
  the award and the published estimate.
* Contracts distinguish "signed", "varied" and "paid": variations above 10% are
  called out, and payment certifications name the acceptance certificate that
  authorised them.
* Supplier pages answer "how much public money has this firm been given, for
  what, and did it deliver", with verification shown as dated assertions rather
  than a tick.
* Per-MDA utilisation is published monthly, with direct-procurement share and a
  published attention line (20%) — so the threshold can be argued about in
  public rather than applied in private.

### Rules and help
* `/tenders/rules/` is the threshold matrix read from `ThresholdRule` rows,
  versioned by fiscal year: *for ₦40m of furniture, which method, how long must
  it be advertised, who approves it, what bid security is allowed.*
* `/tenders/help/` answers the five questions the Bureau's front desk is asked,
  in that order, with the telephone number printed rather than a contact form.

### Error pages
* 404 and 500 are useful pages: they name the fault, do not blame the user, and
  offer the register, status page and telephone number. Both render without a
  request context (a missing request must not turn an error into a second error).

---

## 5. Engineering method

The work was done as a sequence of verifiable steps, each with a check that can
be re-run:

| Step | Command | Expected |
|---|---|---|
| Schema is consistent with the models | `python manage.py makemigrations --check --dry-run` | no changes |
| Every public URL renders against real data | `python manage.py audit_pages --verbose` | 0 failures |
| Public pages return 200, redirects resolve | `python manage.py check_links` | all pass |
| Stylesheet artifact is current | `python manage.py buildcss` | hash printed |
| Every page reads cleanly, not just renders | `python manage.py audit_pages` | 0 failures, 0 content findings |
| Interface rules hold | `pytest tests/test_ui_contract.py` | all pass |
| The rest of the system still works | `pytest` | all pass |

`audit_pages` exists because the dangerous failure is the one that *looks* fine:
a route that returns HTTP 200 with blank figures, or a page that only breaks for
rows that exist in production. It resolves each route against real objects in
the current database and prints what a visitor would actually get.

### Defects found and fixed while doing this

| Defect | Symptom | Fix |
|---|---|---|
| `Tender.Status.OPEN` did not exist | The landing page raised `AttributeError` on every visit | `TenderQuerySet.open()` now means PUBLISHED or CLARIFYING and a future deadline; one definition shared by the register, the counter and the agent desk |
| Contract references contain `/` | Every contract page was unreachable (`NoReverseMatch`) | `<path:reference>` with the sub-pages ordered first |
| `format_html` on `date` objects | Any page showing a certificate expiry 500'd | `_as_dt()` normalises `date`, `datetime` and ISO strings |
| Duplicate catalogue models | Two competing definitions of "catalogue"; the shadow tables were unreadable | Duplicates dropped in a migration that deletes models rather than rebuilding tables (SQLite cannot rebuild a table for a model that no longer exists) |
| `bids__supplier__scores__criterion` | Evaluation pages raised `AttributeError` | Correct relation path (`bids__scores__criterion`) |
| Cursor pagination ordered by `published_at` on Contract | `/api/v1/contracts` returned 500 | `ContractCursorPagination` ordered by `signed_at` |
| `Party.name` | The award RSS feed raised `AttributeError` | `legal_name` |
| N+1 in list views | Contract dashboard issued ~100 extra queries; defects list 2 per row; catalogue 1 per row | Grouped annotations and a single pass over an ordered queryset |
| Ledger payloads rendered as Python dicts | `/tenders/TAR-MOH-2026-0001/` printed `{'version': 1, 'kind': 'BOQ', …}` into a public cell — a 200 OK that no status-code audit could see | A `ledger_payload` filter formats the same payload as labelled facts (`ledger_payload` tests) |
| The audit checked one row per route | A defect that needed a document or a clarification to appear was invisible to it | The crawl now walks every tender, contract, supplier, MDA and objection (89 URLs) |
| CSS classes used but never defined | `.tiny`, `.muted`, `.money`, table footers unstyled | `05-utilities.css`, with the rule that anything there is a documented word in the vocabulary |
| No build artifact for the stylesheet | First request of a fresh checkout built CSS in the request thread | `buildcss` run as part of the standard verification sequence, and a test asserting the artifact is not stale |

### Verification that found nothing (recorded so it is not re-checked)

* OCDS releases validate for every public process (`publish_selftest`).
* Ledger chain verifies (`verify_ledger`).
* The redirect map resolves for every retired URL (`check_links`).

---

## 6. Deliberately not done

Stated so the gap is visible rather than assumed:

* **Supplier-side authenticated actions** (bid submission, registration
  submission, objection filing) still point at the sign-in surface rather than a
  working form. The public pages say who may bid and what happens next; they do
  not pretend the transaction is available when it is not.
* **Language coverage.** The interface is English; Hausa labels exist in the
  i18n table but the public templates are not yet translated. The help page says
  what the Bureau can do by telephone in the meantime.
* **No images, no icons, no illustrations.** Nothing on a register page needs
  one, and on this network each one is data the user pays for. The one exception
  is the state's own mark, which is text.
* **No client-side search or filtering.** The server is the source of truth and
  the URL is the state; a client-side filter would create a second answer that
  can disagree with the CSV export.
