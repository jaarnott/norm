# Norm Finance — Loaded Payroll and Norm Finance

The group's weekly pay run on Loaded's time (payrun and Ace exports, leave payouts,
gratuities, intercompany, the wages reconciliation and Xero journals, the entertainers'
roster, onboarding, portal and payments) and its finances (the Weekly Summary, the locked
sales budget, budget against Loaded and actual, a scored daily sales forecast) — rebuilt
from Orbit's Finance app as **two native Norm modules** (Oct 2026). Design and the owner's
decisions: `docs/plans/finance-migration.md` (v5, 19 Oct 2026) and the seven research reports
under `docs/plans/finance-migration/`.

To users they are two Apps under the existing **Finance** team member, beside the Xero App:
catalog Apps `loaded-payroll` ("Loaded Payroll") and `norm-finance` ("Norm Finance"), pages
gated by `app: 'loaded-payroll' | 'norm-finance'`, tools claimed through the App Map. Both
ship `bundled=False` — hiring Finance does not switch them on; an organisation switches each
on from the Team page, and its module modes (below) have the last word. "Native" only means
the code lives in Norm's main codebase rather than the app-platform sandbox; Loaded and Xero
are reached only through their connectors (consolidators with preview cards), never directly.

## Layout

| Layer | Where |
|---|---|
| Models | `apps/api/app/db/payroll_models.py` (16 `pay_*`), `entertainers_models.py` (11 `ent_*`), `finance_models.py` (16 `fin_*` incl. `fin_job_runs`, `fin_parity_*`) — 43 tables; shared helpers `app/db/_native_rows.py`; migrations `kb6e8g0i2k4m` (scopes, modes, `venues.is_trading`) → `lc7f9h1j3l5n` → `md8g0i2k4m6o` → `ne9h1j3l5n7p` |
| Shared core | `app/services/finance_core/` — `errors`, `modes`, `ctx` (`FinanceCtx`), `jobs` (the one claim table), `tick`, `parity`, `catalog` (the two Apps, declared once), `importer`, `importer_jobs` |
| Payroll | `app/services/payroll/` — `entities`, `weeks`, `snapshot`, `hygiene`, `leave`, `gratuities`, `exports`, `rules`, `inputs`, `parity`, `retention`, `jobs`; wages rec + Xero: `reconciliation`, `intercompany`, `journal`, `xero_mapping`, `xero_pushes`, `xero_status` |
| Entertainers | `app/services/entertainers/` — `performers`, `onboarding`, `band`, `series`, `performances`, `status`, `portal`, `tokens`, `emails`, `email_links`, `invoice_check`, `invoice_reader`, `closing_manager`, `bills`, `payments`, `jobs` |
| Finance | `app/services/finance/` — `summary`, `budgets`, `budget_import`, `holidays`, `actuals`, `forecast`, `scoring`, `drivers`, `pushes`, `jobs` |
| Platform pieces | `app/services/page_writes.py` (the page-side approval helper), `public_links.py`, `files/pdf_check.py`, `files/store.py`, `caller_scope.py` (`requires` on tool rows), `app/auth/permissions.py` (the four scopes) |
| HTTP | `routers/payroll.py` + `payroll_xero.py` (`/api/payroll/*`), `entertainers.py` (`/api/entertainers/*`), `finance.py` (`/api/finance/*`), `entertainers_public.py` (`/api/public/entertainers/{token}/*`, `/api/public/join/{token}/*`, no login), `email_events.py` (`POST /api/public/email-events/resend`), `connectors.py` (`POST /api/connectors/{name}/execute/{action}/preview|confirm`) |
| Agent | `app/agents/payroll_tools.py`, `payroll_xero_tools.py`, `entertainers_tools.py` (connector `norm_payroll`), `finance_tools.py` (`norm_finance`) — registered from `internal_tools.py`; `norm.caller_holds` |
| Config layer | `config/consolidators/{get_payrun,get_weekly_financials,get_closing_manager,set_budgets,set_budgets_typed}.py` (+ `set_budgets_common.py`), `post_wage_journal.py`, `post_intercompany_documents.py`, `void_payroll_document.py`, `create_bill.py` (attachment); shapes in `config/consolidators/shapes.json` |
| Web (staff) | `apps/web/app/components/finance/{payroll,entertainers,finance,shared}/` — one entry file per page (`PayrollPayrun.tsx`, …); `components/display/FinanceWeekly.tsx` (the chat block) |
| Web (performers) | `apps/web/app/p/[token]` (portal), `apps/web/app/join/[token]` (onboarding, band member, declaration) |
| Config scripts | `scripts/sync_norm_finance.py`, `sync_payroll_config.py`, `sync_xero_connector.py`, `sync_marketplace_catalog.py` (the two App rows + the Xero App's three tools), `sync_approval_labels.py`, `sync_caller_holds_tool.py`, `sync_member_dashboards_config.py` (`finance-weekly`) |
| Import | `scripts/migrate_orbit_finance.py`, `app/services/finance_core/importer.py` |

## The two Apps

| App | Pages (`pageRegistry.ts`, `agent: 'finance'`) | Tools the model sees | Skills |
|---|---|---|---|
| **Loaded Payroll** `loaded-payroll` | Payrun · Leave · Intercompany · Reconciliation · Payroll settings · Shadow — `permission: payroll:read`; Entertainers (register) · Roster · Emails — **no permission**, venue access decided server-side; Payments — `payroll:write` | `loadedhub.get_payrun` (`requires: payroll:read`), `norm_payroll.get_week` (`payroll:read`), `norm_payroll.record_week` (`payroll:write`, asks), `norm_payroll.get_entertainers` and `norm_payroll.record_performance` (no `requires` — venue access; `op=mark_paid` refuses without `payroll:write` inside) | `weekly_payroll_close`, `leave_payout_rules` |
| **Norm Finance** `norm-finance` | Weekly Summary · Budgets · Budget vs Loaded · Forecasting — `finance:read` (`budgets:write` to edit); also the page-less display component `finance_weekly` | `loadedhub.get_weekly_financials` (`finance:read`, renders `finance_weekly`), `norm_finance.get_budget_plan`, `norm_finance.get_forecast` (`finance:read`), `norm_finance.record_budget` (`budgets:write`, asks), `loadedhub.set_budgets` (`budgets:write`, asks every time, never always-allow) | `budget_review`, `forecast_review` |
| **Xero** (exists) | Bills (+ the payroll/intercompany mapping section) | the five existing (now with `requires`: reads `finance:read`, `get_profit_and_loss` `payroll:read`, `create_bill` `orders:write`) + `xero.post_wage_journal`, `xero.post_intercompany_documents` (ask, always-allow permitted), `xero.void_payroll_document` (ask only) — each `requires: payroll:write` | — |

Engine-only rows no App claims and the model never sees: `norm_payroll.get_mode`,
`get_journal_draft`, `record_push`; `norm_finance.get_mode`, `record_budget_push`;
`loadedhub.get_closing_manager`, `loadedhub.set_budgets_typed` (the Budgets day grid's
always-allowable typed push); `norm.caller_holds`. MCP exposes three reads only —
`get_payrun` (`mcp:payroll:read`), `get_weekly_financials` and `get_forecast`
(`mcp:finance:read`); writes stay behind Norm's cards.

## Rules the code keeps

- **Tenancy in the schema.** Every `pay_*`, `ent_*`, `fin_*` table has `organization_id NOT NULL`,
  `UNIQUE (organization_id, id)` and composite foreign keys to its parents (venues included);
  a metadata test enforces it. Services take a `FinanceCtx` built from membership — the
  person's `UserVenueAccess` venues (owners and the system: every venue, non-trading ones
  included), their role's scopes, the three modes. **Fail closed:** no venue rows → no venues;
  a venue the person cannot see is a 404, never a 403.
- **Four org scopes** — `payroll:read`, `payroll:write`, `budgets:write`, `finance:read`.
  `owner` and `payroll_admin` hold all four; `manager` holds `finance:read` + `budgets:write`
  and is excluded from `payroll:*`; `team_member` none. A tool row's `requires` is enforced at
  every door (chat, page execute, charts, self-built apps, MCP, page writes); menus omit rows
  the person lacks; nobody known (a job, a script) is refused. `get_labour` strips rates and
  per-shift costs for callers without `payroll:read` (`norm.caller_holds`).
- **Money is integer cents** in the tables and the Norm APIs; dollar floats only where a
  Loaded consolidator's own result passes through. Plans (dates, times) are venue-local;
  facts are UTC instants. Pay weeks run Monday at the venue's `day_start_time` to the next
  Monday (`business_calendar`).
- **The snapshot is the record.** `loadedhub.get_payrun` is the one shaping of a pay week; the
  Monday job stores its rows as `pay_shift_facts` (hashed), Tuesday's close re-snapshot is
  what exports, the reconciliation and the journal build from, and anything Loaded changes
  after it shows as drift by `source_shift_id`. Exports are stored byte for byte with their
  hash; every Xero push lands as DRAFT with its id in the push ledger (`pay_xero_pushes`,
  one live row per week/kind/counterparty) and can be voided.
- **Writes that reach Loaded or Xero are consolidators with a preview card.** Pages go
  through `page_writes.preview/confirm` (`POST /api/connectors/{name}/execute/{action}/preview`
  and `/confirm` with a fingerprint); chat goes through the approval card; `source` on a
  budget push is set by the door, never by the model (`set_budgets` chat → `chat`, page
  forecast → `forecast`, the day grid's `set_budgets_typed` → `manual`).
- **Nothing from an unattended run.** `norm_payroll.get_mode` / `norm_finance.get_mode`
  answer `error: unattended run` for a task-run thread; every write built-in and consolidator
  checks them first.
- **PII encrypted at rest** (`EncryptedText`: IRD numbers, bank accounts, dates of birth —
  `TOKEN_ENCRYPTION_KEYS`); no public response ever carries a stored bank account, IRD number
  or date of birth; the one staff route that returns them (`/performers/{id}/sensitive`)
  needs `payroll:write` and logs every read. Sick-leave inputs are health information: shown
  only to `payroll:write`.
- **Public routes** (portal, onboarding, the webhook) carry `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow`; one 404 for any unknown,
  revoked, expired or wrong-purpose link; rate limits per IP and per link; PDF magic + parse
  (no JavaScript, forms or open actions), 10 MB cap, five uploads per link per day; a six-digit
  code to the invited address before the bank/tax steps (10-minute code, 5 attempts, 3 sends
  per 15 minutes, the sixth wrong code of the day locks the link); a spent onboarding link
  refuses a second submission; finance accepts every submission before anything is payable.
  These routes are covered by blocking CI tests.
- **Statutory rules are dated rows** (`pay_rules`: sick-leave eligibility, the sick day's
  hours; the Holidays Act replacement is a new row, not a code change); the $150 uniform bond
  is deducted from holiday pay only with a consent file or "held in Ace"; an unmapped leave
  type refuses `IMPORT_LEAVE.TXT`; a `payroll_contractor` with no IR330C declaration gets no
  Payrun line; an exemption certificate past its expiry is not billable as exempt.
- **Entertainer bookings and approvals are venue-access work** (D15): anyone with access to
  the venue books and approves (as Orbit allowed); only `payroll:write` opens Payments, marks
  paid and sees bank/IRD details. The closing Sales Manager is read from Loaded's clock-ins
  (else the roster) each morning and emailed a link that opens the approval; one with no Norm
  login goes on the venue digest instead.

## Modes (`organizations.payroll_mode`, `entertainers_mode`, `finance_mode`)

Each module has its own switch, `off | shadow | live` (`finance_core.modes`).

| Mode | Loaded Payroll | Entertainers | Norm Finance |
|---|---|---|---|
| `off` | every route 404; the App is not listed | 404 | 404; the App is not listed |
| `shadow` | reads, snapshots, hygiene emails, exports (kept inside Norm) and parity work; a write is refused with `409 not_live` unless the body carries `rehearsal: true` (stored flagged, never feeds an export, draft or journal); every email goes to the organisation's `payroll_contact_email` (none → logged `suppressed`) | staff routes work (register, bookings, approvals); the portal is off (404 `not_yet`); mark-paid and Xero bills refuse; emails to the payroll contact | reads and Norm's own budget edits work; every Loaded push 409 `not_live`; nightly vintages and scores run |
| `live` | everything | everything | everything |

`loaded-payroll` counts as on while `payroll_mode` or `entertainers_mode` is on;
`norm-finance` while `finance_mode` is. Until the catalog rows exist (local testing, and the
hours between a deploy and the seed) an App whose modes are on is stood in from the
declaration in `finance_core/catalog.py` and counts as on, as Functions' are; once the rows
exist the organisation's own switch on the Team page decides, and `off` still vetoes.

## Background jobs (the finance tick)

All ride the existing five-minute Cloud Scheduler job through `/internal/run-due-tasks`
(thread `finance-tick`, `finance_core.tick.run_due_jobs`) — no new scheduler job per
environment. Each claims a `fin_job_runs` row (org, job, scope, slot) before working, so
overlapping ticks and several API instances never repeat a run; a run started more than 30
minutes ago with no finish is stale and reclaimed; every failure is on the row and in the
weekly parity email.

| Job | When | What |
|---|---|---|
| `payroll.snapshot` | Monday, venue day start + 30 min, per entity-week | `get_payrun view=all` with the entity's own token → `pay_shift_facts` |
| `payroll.hygiene_email` | Monday 20:00 venue-local | unapproved clock-ins, missing pay codes, unfinished clock-ins, parity line → the payroll contact; opens with "snapshot taken for N of M entities" |
| `payroll.close_snapshot` | Tuesday 10:00 venue-local (skipped when "Start close" already took one) | the close re-snapshot exports and the journal build from |
| `payroll.gratuity_reminder` | the 1st, per entity-month | the monthly pool reminder |
| `payroll.retention` | daily, per org | 7-year retention floor; token and IP clean-up |
| `finance.parity_weekly` | Wednesday 04:00 venue-local, per org-week, after the nightly import | the parity run and the parity email (every failed job of the week listed) |
| `xero.status_refresh` | Wednesday 03:00 NZ, per org-week | refreshes the Xero status of every live push; a document voided in Xero marks the ledger row voided |
| `ent.morning` | 08:00 org-local | closing-manager lookup → approval emails → performer, band and onboarding reminders → the venue digest |
| `ent.invoice_checks` | every tick, ≤ 100 per org per day | the uploaded-invoice check (one LLM read per file, Norm's cost) |
| `finance.nightly` | trading-day close + 30 min, per venue-day, `shadow` or `live` | refresh 14 days of actuals (`get_sales`), log Loaded's budget for the next 10 weeks when it changed, build the 70-day vintage, score the vintages made 1/7/28 days earlier and the Loaded budget as it stood 28 days earlier. Never a Loaded write |
| `importer.nightly` | 02:30 NZ per org-day, until a module is live | shadow import from Orbit (`ORBIT_SUPABASE_CREDS`) |
| `importer.post_freeze_watch` | daily inside the freeze window (19 Jan – 3 Feb) | emails the payroll contact any Orbit payroll-input row created after the freeze |

## The parity engine (the shadow period)

Norm computes beside Orbit, which stays the system of record until each step is proven.
`fin_parity_runs` / `fin_parity_items` hold every comparison, weekly (the job) and on demand
from the Shadow tab (`POST /api/payroll/shadow/runs`, `.../exports-check` with the owner's
Ace files); each item is `expected` (a named class from `finance_core.parity.EXPECTED_CLASSES`
— Loaded-side attribution, dropped deleted/unfinished rows, the "Other team" line, the
one-day budget shift, summed percentages, gratuity cent residuals, "Orbit defaulted to #70",
"Loaded changed between snapshot and export", the importer's inferred legacy week) or
`unexplained`, signed off by the payroll person. The gates before each cut-over are in the
plan §14: three consecutive clean pay weeks from WE 15 Nov (exports), three clean journal
weeks including Christmas, three clean entertainer weeks, both rehearsal weeks explained,
four weeks of forecast scores before "Set Loaded budget" shows.

## Ship-day runbook

The config DB is shared with production: `--dry-run` each script first. The scripts are
idempotent. The full order was rehearsed end to end on a scratch copy of the config DB on
20 Oct 2026 (nothing applied to the shared DB).

1. **Rebase and migrate.** Rebase onto `origin/main`; the chain `ja5d7f9h1j3l` →
   `kb6e8g0i2k4m` → `lc7f9h1j3l5n` → `md8g0i2k4m6o` → `ne9h1j3l5n7p` must be one head
   (`alembic heads`); verify upgrade/downgrade on a throwaway database. Full gates (API suite
   on its own database, web lint/tsc/vitest, `test_mcp_ui.py`).
2. **Deploy** (push to main → production). The pipeline runs the migrations before switching
   traffic. The intended split is Phase 0 first — `kb6e8g0i2k4m` (the four scopes, the role
   backfill, the mode columns, `venues.is_trading`) with `scripts/sync_caller_holds_tool.py`
   applied **in the same deploy** (without the `norm.caller_holds` row, `get_labour` fails closed
   and strips pay detail for everyone, owners included) — then the three model migrations when
   the venues are quiet (they add foreign keys to hot tables; migrations give up on a busy lock
   after 5 s rather than block, so a failed migrate step is re-run, not waited out). In this
   tree the four migrations chain under one head, so a single push runs all four: pick a quiet
   hour, or push F0's migration on its own first.
   ```bash
   cd apps/api
   uv run python scripts/sync_caller_holds_tool.py --dry-run && uv run python scripts/sync_caller_holds_tool.py
   ```
   The code is inert until an organisation's modes change; every App row ships `bundled=False`.
3. **Config scripts, in this order** (each `--dry-run`, then for real):
   ```bash
   cd apps/api
   # a. the two internal specs, their 12 rows (labelled), the four skills (off) and the
   #    MCP row for norm_finance.get_forecast; the two loadedhub MCP rows are skipped until (b)
   uv run python scripts/sync_norm_finance.py --dry-run && uv run python scripts/sync_norm_finance.py
   # b. loadedhub: nine engine-only endpoint rows + get_payrun, get_weekly_financials,
   #    set_budgets, set_budgets_typed, get_closing_manager (refuses before (a): the budget
   #    rows call norm_finance.get_mode / record_budget_push)
   uv run python scripts/sync_payroll_config.py --dry-run && uv run python scripts/sync_payroll_config.py
   # c. xero: `requires` on the five existing tools, four endpoint rows, the three payroll
   #    write tools, attachment_file_id on create_bill, the Finance agent row
   uv run python scripts/sync_xero_connector.py --dry-run && uv run python scripts/sync_xero_connector.py
   # d. the catalog: ONLY the three rows — without --only every pending diff in the seed applies
   uv run python scripts/sync_marketplace_catalog.py --dry-run --only loaded-payroll,norm-finance,xero
   uv run python scripts/sync_marketplace_catalog.py --only loaded-payroll,norm-finance,xero
   # e. again: switches the four claimed skills on, adds the two loadedhub MCP rows
   uv run python scripts/sync_norm_finance.py --dry-run && uv run python scripts/sync_norm_finance.py
   # f. the finance-weekly dashboard template — after the catalog: it validates the pending
   #    state and refuses while the 15 finance components are still unclaimed
   uv run python scripts/sync_member_dashboards_config.py --dry-run && uv run python scripts/sync_member_dashboards_config.py
   # g. every finance row reports "up to date" (apply only if one doesn't)
   uv run python scripts/sync_approval_labels.py --dry-run
   ```
   **Two fixes must land before this order runs** (found in the 20 Oct rehearsal, requested in
   the build notes): (b) refuses `get_weekly_financials` because no catalog row declares
   `finance_weekly` yet, while (d) refuses the Norm Finance App because `get_weekly_financials`
   does not exist yet — `sync_payroll_config.py` must count the components
   `finance_core/catalog.py` declares as known; and `norm_payroll.record_performance` asks for
   approval with no registered preview, the one `validate_config` error left once everything
   is synced. Between the deploy and (d), `validate_config` in every environment also reports
   the 15 finance components as "belongs to no App" (the code registers them first).
   `validate_config` reports ok after (e) once both fixes are in. Expected dry-run output: (a) "create spec
   'norm_payroll' with 7 tool rows", "create spec 'norm_finance' with 5 tool rows", four
   playbooks off, one MCP row; (b) 14 rows; (d) "create loaded-payroll", "create norm-finance",
   "update xero (composition)"; (e) four playbooks on, two MCP rows; (g) up to date.
4. **Production steps the owner does** (each a production write — go-ahead on the day, D17):
   - `TOKEN_ENCRYPTION_KEYS` — already live in production; nothing to do.
   - **Modes:** `finance_mode = shadow` on the `default` organisation the day the Weekly Summary
     ships (nightly vintages and scores need it); `payroll_mode` and `entertainers_mode` to
     `shadow` before the first Monday snapshot. The AU organisation stays `off` (D12). Per
     organisation: `UPDATE organizations SET payroll_mode='shadow', entertainers_mode='shadow',
     finance_mode='shadow' WHERE slug='default';` (the `norm` login from `DATABASE_URL_DIRECT`).
   - **Switch the Apps on** for the `default` organisation from the Team page (Finance →
     Loaded Payroll, Norm Finance); the Xero App is on already.
   - `organizations.payroll_contact_email` (D25) — without it every payroll and entertainer email in
     shadow is suppressed and logged, nothing is sent.
   - Per NZ entity on Payroll settings: `payroll_on`, the Ace pay type code (`50001`), the
     gratuity allowance code, the security pay code, the $150 bond and 12 % uplift (D19,
     confirmed with the accountant); seed the leave styles
     (`POST /api/payroll/leave-styles/seed {"venue_id"}` — the Settings button) and sign each
     off (D28) before the first leave export. The AU venues keep `payroll_on = false`.
   - Every NZ venue's `day_start_time` before the first Monday snapshot (D11: DSC and The
     Glass Goose 06:00 if Loaded agrees, 07:00 elsewhere) — it also moves those venues'
     trading day in every other Norm read.
   - Holidays: `POST /api/finance/holidays/seed {"year": 2027, "country": "NZ"}` (each January;
     `AU` for the AU org), then each venue's `holiday_region` and closed weekdays
     (`PUT /api/finance/settings`).
   - Load history once per venue: `POST /api/finance/forecast/load-history {"venue_id", "months": 24}`
     (reads only) — the first thing to press at a new venue; `POST /api/finance/forecast/backtest`
     populates the accuracy strip before anyone trusts a number.
   - **CBI** as a non-trading venue (`venues.is_trading = false`, D1) with its Loaded and Xero
     connections — the week of 9 Nov; the nightly importer refuses on the "every Orbit venue
     mapped" gate until it exists (`--skip-venue CBI` by hand until then).
   - **Xero consents** for all seven entities, from production, by each entity's Xero admin;
     after each one: `uv run python scripts/migrate_orbit_finance.py --org default --module payroll
     --write-xero-mapping` once (fills the `xero_mapping.payroll` / `.intercompany` keys nobody
     set; never overwrites a key a person set), then the mapping panel validated per entity
     (`rounding` is new and comes from the panel). Two journal decisions for the accountant:
     the "Reclass Gratuities" debit and its account; the leave-line tax rule on ICO documents.
   - `ORBIT_SUPABASE_CREDS` (D31): a dedicated SELECT-only Orbit Postgres role for the finance,
     forecasting and entertainer tables, in Secret Manager, mounted as a file whose path the
     env var names (rotated, deleted 31 Mar); else the import runs by hand from the dev box.
   - **The import** (`migrate_orbit_finance.py`): `--org default --dry-run` against production,
     the report read (56 performers with no setup type and 25 set up one way and paid another
     to decide, 18 tax-code spellings, 16 bills with GST for a non-registered performer, 3
     legacy payouts set aside, 3 gratuity batches short of their pool, 94 La Zeppa event drivers
     to prune; the people and approvers matched to Norm users, D30), then the real run with
     `--skip-venue CBI` until CBI exists. Nightly re-import while in shadow.
   - `RESEND_WEBHOOK_SECRET` (D32) — a new secret per environment; Resend's webhook pointed at
     `https://<env>/api/public/email-events/resend` (delivered / bounced / complained land on
     the Emails page). Without the secret the webhook answers 503 and nothing else changes.
   - The performer sender address and display name (D26) on the entertainer emails.
   - **The Xero Demo Company test** (F4's list, owner go-ahead): connect a local venue to the
     Demo Company (NZ), map accounts, build a week's draft, journal preview → confirm, confirm
     again ("already in Xero"), void preview → confirm on the DRAFT (confirm Xero accepts
     `DELETED` on a draft and `VOIDED` on a posted one), then the ICO pair, then one entertainer
     bill with its PDF attached.
   - **The ten-invoice extraction trial** (D35, ≈ 40 cents on the shared Anthropic key, stated
     first): the invoice check on ten real Orbit invoices with every finding read by a person.
   - **Declaration links** (D36): Mon 7 Dec, each active WT performer with no declaration on
     record (`GET /api/entertainers/performers/missing-declarations`) gets the declaration-only
     link (`POST /api/entertainers/performers/{id}/declaration-link`) — the one Norm email to real
     performers before the cut-over.
   - **PITR on `norm-prod-db`** (Mon 11 Jan) before Norm becomes the payroll record.
   - **The freeze** (Mon 18 Jan 18:00): `finance_core.importer_jobs.set_freeze_window(db, org_id,
     freeze_at=<tz-aware instant>, until=date(2027, 2, 3))` — the daily post-freeze watch then
     emails any Orbit payroll-input row created after it.
   - The Loaded scope request (D13) and the Orbit-side actions (D27: RLS SELECT-only, the push
     functions undeployed on each date, the "this link has moved" banners via Lovable) — owner
     actions with named people, verified the day after each date.
5. **Golden files** (D18): head office keeps every week's four Ace files from now; parity
   byte-diffs Norm's exports against them on the Shadow tab.

## Cut-over calendar (from plan §15) and rollback

Shadow through Christmas; **Tue 19 Jan 2027** payroll and entertainers cut over together for
WE 17 Jan (Orbit bookings frozen Mon 18 Jan 18:00 → final import → `payroll_mode` and
`entertainers_mode` → `live` → exports, payouts, entertainer lines, reconciliation, journal
and ICO documents from Norm; Norm's emails on; Orbit's entertainer screens, sweep, portal and
onboarding off); **Tue 2 Feb** budgets live from Norm (`finance_mode` → `live` for the push;
Orbit's budget pushes undeployed); **Wed 3 Feb** Orbit's payroll screens read-only after two
clean Norm weeks; **Mon 15 Feb** the Forecasting page's "Set Loaded budget"; **Mon 1 Mar** the
Weekly Summary retires Orbit's three report screens; **Mon 22 Mar** FY2028 budget imported and
pushed from Norm; **Wed 31 Mar** Orbit Finance off, read-only to 30 Apr, archived. Slip
clause: a slip is a missed shadow week — without three clean journal weeks by Wed 13 Jan both
cut-overs move to Tue 2 Feb.

**Rollback, per module.** *Xero:* every write is DRAFT with its id in the ledger;
`void_payroll_document` takes a journal, an ICO document or an entertainer bill back. *Budgets:*
`fin_budget_pushes` holds before values — "restore Loaded budgets" is one approved `set_budgets`
(`POST /api/finance/pushes/restore/plan`, then `/push/preview` + `/confirm` with `kind: "restore"`;
a day Norm created in Loaded cannot be removed — Loaded has no delete). *Payroll and
entertainers from 19 Jan:* the fallback pack per week (`GET /api/payroll/weeks/{id}/fallback-pack`:
the four Ace files plus `REKEY.csv` of every Norm-authored payout, gratuity share, ACE input,
adjustment, accrual and approval) re-keys a bad week into Orbit, which stays writable by head
office until 3 Feb. *Always:* modes flip back to `shadow`, which refuses writes; until a
module is live the importer never overwrites a Norm-authored row; code rolls back by image
pin (with `encrypt_connection_tokens.py --decrypt` before an image that predates encryption).

## What the owner decided (the defaults this build took — plan §18)

Taken as given: **D2** two Apps under Finance, no new member; **D3** both Apps $0, the
member $10; **D4** the Financial Report lives inside the Weekly Summary; **D5** the journal and
intercompany tools on the Xero App; **D6** always-allow only on the day grid's typed push,
never in chat, never for the forecast push (a card every time, hidden until four weeks of
scores, 92-day cap); **D7** Ace Payroll (if Xero Payroll comes, only the export layer dies);
**D8** `payroll:*` for owner and payroll_admin only, `finance:read` + `budgets:write` for owner,
manager and payroll_admin, never team_member; `get_labour` strips rates without `payroll:read`;
the Xero App's five tools gated the same way; **D9** IRD, bank and DOB encrypted; **D10** Xero
DRAFT only; **D12** NZ payroll only, AU `payroll_mode = off`; **D14 a–g** personal onboarding
links only, the email code before bank/tax, finance accepts before payable, the IR330C signed on
the form (upload kept), band shares set by finance, amounts by email never on the link, the
unknown-lane performers decided from the importer's list; **D15** register, roster and emails
on venue access, payments and bank/IRD on `payroll:write`; **D16 / D29** cut-over in January,
entertainers with payroll (the modes allow earlier, with the D29 bridge); **D21** text Ace
files and CSV of any grid; XLSX and PDF deferred; **D22** closing-manager roles default
`["Sales Manager"]` (an `ent_settings` list); **D23** purchases-based COGS, cash variance and
voids kept; **D24** the close runs on a snapshot; **D32** the Emails page with Resend delivery
status and Orbit's send dates imported; **D33** the four monthly lines (intercompany fees,
finance costs, income tax, leave %) not carried; **D34** total-only forecast; **D35** the
invoice check on the extraction model, Norm's cost, paid Orbit invoices not re-read; **D36** the
declaration-only link exists.

Owner actions, not build (dated in §15 and the runbook above): **D1** CBI venue, the seven
Xero consents, the eighth Xero organisation named; **D11** every venue's day start and the AU
org's currency; **D13** the Loaded scope request; **D17** go-ahead on each production write;
**D18** Ace golden files kept from now; **D19** the bond and uplift confirmed; **D20** the Orbit
forecast crons stay until Norm Functions is live; **D25** the payroll contact; **D26** the
performer sender; **D27** the three Orbit-side actions with names; **D28** the statutory
sign-off; **D30** people and roles by 4 Jan; **D31** the Orbit credential.

Design notes the owner should know: the Monday snapshot job has no person, and `requires`
refuses callers with no person, so `snapshot.loaded()` runs the `get_payrun` consolidator
directly under the organisation's scope for the job (a platform "system caller" scope would
let that bypass go — backlog); `pay_ace_inputs` and `pay_accruals` are one row per week, so a
Norm-keyed and an Orbit-keyed row for the same rehearsal week cannot both exist (parity records
the Norm row as "check against Orbit by hand"); `level_correction_on` defaults to false;
`email_logs.status` gains `suppressed`; the two Finance App rows carry no App note (the
catalog's note budget is full — the rules are on the tool descriptions and in the skills).

## Testing

`apps/api/tests/test_finance_*.py`, `test_payroll_*.py`, `test_entertainers_*.py`,
`test_consolidator_finance.py`, `test_payroll_xero*.py`, `test_app_map.py`; worlds in
`tests/payroll_world.py`, `entertainers_world.py`, `finance_f6_world.py`. The public routes
(portal, onboarding, the code, the webhook) and the D15 gate (a `team_member` with venue
access approves at their venue, is refused at another, cannot open the Payrun, never sees
bank or IRD) are blocking CI tests. The three consolidators were run live on La Zeppa (reads
only) and their shapes recorded in `shapes.json`; the forecast backtest reproduced the
research reader's figures on real data (Bessie inside both targets, La Zeppa not — the human
budget still wins there, which is the frame the plan sets). Web logic tests sit next to the
components (`*.test.ts`).
