# Finance apps — Payroll, Entertainers and Budget

The group's weekly pay run on Loaded's time (pay run and Ace exports, leave payouts,
gratuities, intercompany, the wages reconciliation and Xero journals, the entertainers'
roster, onboarding, portal and payments) and its finances (the Weekly Summary, the locked
sales budget, budget against Loaded and actual, a scored daily sales forecast) — rebuilt
from Orbit's Finance app as native Norm modules (Oct 2026), first shipped as two Apps (Loaded
Payroll, Norm Finance, 23 Oct) and split on 24 Oct into **three job apps**. Design and the owner's
decisions: `docs/plans/finance-migration.md` (v5, 19 Oct 2026) and the seven research reports
under `docs/plans/finance-migration/`.

To users they are three Apps under the existing **Finance** AI agent, beside Bills and Profit &
Loss (Xero): catalog Apps `payroll` ("Payroll"), `entertainers` ("Entertainers") and `budget`
("Budget"), pages gated by `app: 'payroll' | 'entertainers' | 'budget'`, tools claimed through
the App Map. All three ship `bundled=False`, unpriced and unlisted on the public marketplace
(until the January cut-over) — hiring Finance does not switch them on; an organisation switches
each on from the Team page, and its own module mode (below) has the last word. The Finance menu
reads Dashboard · Weekly Summary · Profit & Loss · Budgets · Bills · Payroll · Entertainers ·
Tasks. "Native" only means
the code lives in Norm's main codebase rather than the app-platform sandbox; Loaded and Xero
are reached only through their connectors (consolidators with preview cards), never directly.

## Layout

| Layer | Where |
|---|---|
| Models | `apps/api/app/db/payroll_models.py` (16 `pay_*`), `entertainers_models.py` (11 `ent_*`), `finance_models.py` (16 `fin_*` incl. `fin_job_runs`, `fin_parity_*`) — 43 tables; shared helpers `app/db/_native_rows.py`; migrations `kb6e8g0i2k4m` (scopes, modes, `venues.is_trading`) → `lc7f9h1j3l5n` → `md8g0i2k4m6o` → `ne9h1j3l5n7p` |
| Shared core | `app/services/finance_core/` — `errors`, `modes`, `ctx` (`FinanceCtx`), `jobs` (the one claim table), `tick`, `parity`, `catalog` (the three Apps, declared once, and `LEGACY_SLUGS` for the switch from the two old ones), `importer`, `importer_jobs` |
| Payroll | `app/services/payroll/` — `entities`, `weeks`, `snapshot`, `hygiene`, `leave`, `gratuities`, `exports`, `rules`, `inputs`, `parity`, `retention`, `jobs`; wages rec + Xero: `reconciliation`, `intercompany`, `journal`, `xero_mapping`, `xero_pushes`, `xero_status` |
| Entertainers | `app/services/entertainers/` — `performers`, `onboarding`, `band`, `series`, `performances`, `status`, `portal`, `tokens`, `emails`, `email_links`, `invoice_check`, `invoice_reader`, `closing_manager`, `bills`, `payments`, `jobs` |
| Budget | `app/services/finance/` — `summary`, `budgets`, `budget_import`, `holidays`, `actuals`, `forecast`, `scoring`, `drivers`, `pushes`, `jobs` |
| Platform pieces | `app/services/page_writes.py` (the page-side approval helper), `public_links.py`, `files/pdf_check.py`, `files/store.py`, `caller_scope.py` (`requires` on tool rows), `app/auth/permissions.py` (the four scopes) |
| HTTP | `routers/payroll.py` + `payroll_xero.py` (`/api/payroll/*`), `entertainers.py` (`/api/entertainers/*`), `finance.py` (`/api/finance/*`), `entertainers_public.py` (`/api/public/entertainers/{token}/*`, `/api/public/join/{token}/*`, no login), `email_events.py` (`POST /api/public/email-events/resend`), `connectors.py` (`POST /api/connectors/{name}/execute/{action}/preview|confirm`) |
| Agent | `app/agents/payroll_tools.py`, `payroll_xero_tools.py`, `entertainers_tools.py` (connector `norm_payroll`), `finance_tools.py` (`norm_finance`) — registered from `internal_tools.py`; `norm.caller_holds` |
| Config layer | `config/consolidators/{get_payrun,get_weekly_financials,get_closing_manager,set_budgets,set_budgets_typed}.py` (+ `set_budgets_common.py`), `post_wage_journal.py`, `post_intercompany_documents.py`, `void_payroll_document.py`, `create_bill.py` (attachment); shapes in `config/consolidators/shapes.json` |
| Web (staff) | `apps/web/app/components/finance/{payroll,entertainers,finance,shared}/` — one entry file per page: `payroll/PayrollPage.tsx` (Payroll, its tabs and the settings gear), `entertainers/EntertainersPage.tsx`, `finance/FinanceBudgets.tsx`, `finance/FinanceSummary.tsx`; shared tab state in `shared/useWorkspaceTab.ts`; old page ids (`payroll-leave`, `ent-roster`, `finance-forecasting`, …) are translated by `components/pages/pageAliases.ts`; `components/display/FinanceWeekly.tsx` (the chat block) |
| Web (performers) | `apps/web/app/p/[token]` (portal), `apps/web/app/join/[token]` (onboarding, band member, declaration) |
| Config scripts | `scripts/sync_norm_finance.py`, `sync_payroll_config.py`, `sync_xero_connector.py`, `sync_marketplace_catalog.py` (the three App rows, Bills and the `finance-agent` row), `switch_finance_apps.py` (the 24 Oct switch, below), `sync_approval_labels.py`, `sync_caller_holds_tool.py`, `sync_member_dashboards_config.py` (`finance-weekly`) |
| Import | `scripts/migrate_orbit_finance.py`, `app/services/finance_core/importer.py` |

## The three Apps

Every Finance page names its system (`connection: "loaded"`), so it hides for an organisation
with no Loaded connection. Works with: Payroll — Loaded and Xero; Entertainers — Loaded and Xero;
Budget — Loaded.

| App | Pages (`pageRegistry.ts`, `agent: 'finance'`) and their tabs (`tab=` in the address) | Tools the model sees | Skills |
|---|---|---|---|
| **Payroll** `payroll` (follows `payroll_mode`) | **Payroll** (`payroll`, `permission: payroll:read`): Hours · Checks · Leave · Gratuities · Entertainers (the week's entertainer pay lines) · Intercompany · Reconciliation · Ace files · Trial run (only while payroll is in its trial run); **Payroll settings** behind the gear (`tab=settings`, `view=entities\|leave\|rules\|xero`: Entities · Leave types · Rules · Xero mapping). Intercompany and Reconciliation open on "All entities" when no entity is chosen | `loadedhub.get_payrun` (`requires: payroll:read`), `loadedhub.get_pay_adjustments` (none — no dollar rates), `norm_payroll.get_week` (`payroll:read`), `norm_payroll.record_week` (`payroll:write`, asks), `xero.post_wage_journal`, `xero.post_intercompany_documents` (ask, always-allow permitted), `xero.void_payroll_document` (ask only) — the three Xero writes each `requires: payroll:write` (moved from Bills on 24 Oct) | `weekly_payroll_close`, `leave_payout_rules` |
| **Entertainers** `entertainers` (follows `entertainers_mode`) | **Entertainers** (`entertainers`, **no permission** — venue access decided server-side): Roster (default) · Performers (All / Awaiting acceptance / Missing declaration as filter chips) · Payments (only `payroll:write`) · Emails (follows the shared venue picker); Settings behind the gear for those who may edit settings | `norm_payroll.get_entertainers` and `norm_payroll.record_performance` (no `requires` — venue access; `op=mark_paid` refuses without `payroll:write` inside) | none — the tool descriptions say how booking, approval and paying work |
| **Budget** `budget` (follows `finance_mode`) | **Weekly Summary** (`finance-summary`, `finance:read`); **Budgets** (`finance-budgets`, `finance:read`, `budgets:write` to edit): Daily budget (default; `show=grid\|compare\|year` — the grid, Compare by day with Loaded's per-day variances, week subtotals and CSV, and the year table) · Forecast · Monthly lines · History, with Versions and Import at the top of the page (`panel=versions\|import`); also the page-less display component `finance_weekly` | `loadedhub.get_weekly_financials` (`finance:read`, renders `finance_weekly`), `norm_finance.get_budget_plan`, `norm_finance.get_forecast` (`finance:read`), `norm_finance.record_budget` (`budgets:write`, asks), `loadedhub.set_budgets` (`budgets:write`, asks every time, never always-allow) | `budget_review`, `forecast_review` |
| **Bills** and **Profit & Loss** (Xero, existing) | Bills; Profit & Loss (`permission: payroll:read`) | Bills: `xero.get_bills`, `xero.get_contacts`, `xero.create_bill` (uses `get_accounts`); reads `requires: finance:read`, `get_profit_and_loss` `payroll:read`, `create_bill` `orders:write`. The payroll and intercompany account mapping is under Payroll settings → Xero mapping | — |

The connector names stay `norm_payroll` (display name "Norm Payroll") and `norm_finance`
("Norm Finance"), and the scopes, mode columns and module names (`payroll`, `entertainers`,
`finance`) are unchanged by the split.

Engine-only rows no App claims and the model never sees: `norm_payroll.get_mode`,
`get_journal_draft`, `record_push`; `norm_finance.get_mode`, `record_budget_push`;
`loadedhub.get_closing_manager`, `loadedhub.set_budgets_typed` (the Daily budget grid's
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
  the person lacks; nobody known (a job, a script) is refused. `get_labour` is unchanged:
  managers keep seeing rates and per-shift costs in chat (owner, 23 Oct — D8's strip not taken).
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
  forecast → `forecast`, the Daily budget grid's `set_budgets_typed` → `manual`).
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
  is deducted from holiday pay only with a consent file or "held in Ace"; **leave types come
  from Loaded** (owner, 23 Oct): Loaded's own NZ annual, sick and bereavement leave go out as
  #70, #60, #63 (keyed by Loaded's id — Loaded's leave types carry no payroll code), unpaid
  leave is left out, and a company's own type with no Ace style set under Payroll settings →
  Leave types refuses `IMPORT_LEAVE.TXT` by name; **public holidays come from Loaded's pay
  adjustments** (the Public Holiday adjustments in each venue's activated packages — no
  region setting); a `payroll_contractor` with no IR330C declaration gets no
  pay run line; an exemption certificate past its expiry is not billable as exempt.
- **Entertainer bookings and approvals are venue-access work** (D15): anyone with access to
  the venue books and approves (as Orbit allowed); only `payroll:write` opens Payments, marks
  paid and sees bank/IRD details. The closing Sales Manager is read from Loaded's clock-ins
  (else the roster) each morning and emailed a link that opens the approval; one with no Norm
  login goes on the venue digest instead. **Which roles count is a setting**
  (`ent_settings.closing_manager_roles`, default `["Sales Manager"]`, D22): a payroll writer or an
  owner picks them in the Entertainers page's **Settings** (the gear) from the venue's live Loaded roles
  (`GET /api/entertainers/settings/loaded-roles`), or types one.
- **Bank accounts pass Inland Revenue's validation** (Payroll Calculations and Business Rules
  Specification 2026–27 §7: a listed bank, a branch in its Payments NZ ranges, the check digits
  by algorithm A–G or X) on the onboarding form and server side
  (`performers.nz_bank_account_valid`, mirrored in `entertainers/logic.ts`); a wrong digit reads
  "That bank account number doesn't check out — please re-check it." The branch ranges cover
  every branch in Payments NZ's register as downloaded 23 Oct 2026 — a bank that opens a branch
  outside them needs both copies widened.
- **Emails come from Norm, in the organisation's name** (D26 settled 23 Oct): entertainer and
  payroll emails leave from Norm's platform address (`EMAIL_FROM_ADDRESS`) with the
  organisation's name as the display name — as app and Functions emails already do — and
  `Reply-To` the payroll contact, so a reply reaches a person.

## Modes (`organizations.payroll_mode`, `entertainers_mode`, `finance_mode`)

Each module has its own switch, `off | shadow | live` (`finance_core.modes`), and each App
follows exactly one: Payroll ← `payroll_mode`, Entertainers ← `entertainers_mode`, Budget ←
`finance_mode`. People and the model read `shadow` as **Trial run** — the banners and badges,
the Payroll page's Trial run tab, the emails' "Trial run:" line, the refusal messages and the
skills; the stored value stays `shadow`.

| Mode | Payroll | Entertainers | Budget |
|---|---|---|---|
| `off` | every route 404; the App is not listed | 404; the App is not listed | 404; the App is not listed |
| `shadow` | reads, snapshots, hygiene emails, exports (kept inside Norm) and parity work; a write is refused with `409 not_live` unless the body carries `rehearsal: true` (stored flagged, never feeds an export, draft or journal); every email goes to the organisation's `payroll_contact_email` (none → logged `suppressed`) | staff routes work (performers, bookings, approvals); the portal is off (404 `not_yet`); mark-paid and Xero bills refuse; emails to the payroll contact | reads and Norm's own budget edits work; every Loaded push 409 `not_live`; nightly vintages and scores run |
| `live` | everything | everything | everything |

`payroll` counts as on only while `payroll_mode` is on, `entertainers` while
`entertainers_mode` is, `budget` while `finance_mode` is (`catalog.MODE_OF`). Until the catalog
rows exist (local testing, and the hours between a deploy and the catalog step) an App whose
mode is on is stood in from the declaration in `finance_core/catalog.py` and counts as on, as
Functions' are; once the rows exist the organisation's own switch on the Team page decides, and
`off` still vetoes.

**The window after the 24 Oct split** (`catalog.LEGACY_SLUGS`, removed at the restructure's
stage-5 tidy-up): while the old `loaded-payroll` / `norm-finance` rows are still in the catalog
they keep their old rule (Loaded Payroll on while payroll OR entertainers is on) and the Team
page never lists them; a new App with no row of its own counts as on exactly when the App it
replaced is on for the organisation, and only while its own mode is. This is deliberately not
`entitlements.SUCCESSORS`, which ignores modes.

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
| `finance.nightly` | trading-day close + 30 min, per venue-day, `shadow` or `live` | refresh 14 days of actuals (`get_sales`), refresh the next 12 months of public holidays from Loaded's pay adjustments (`get_pay_adjustments`), log Loaded's budget for the next 10 weeks when it changed, build the 70-day vintage, score the vintages made 1/7/28 days earlier and the Loaded budget as it stood 28 days earlier. Never a Loaded write |
| `importer.nightly` | 02:30 NZ per org-day, until a module is live | shadow import from Orbit (`ORBIT_SUPABASE_CREDS`) |
| `importer.post_freeze_watch` | daily inside the freeze window (19 Jan – 3 Feb) | emails the payroll contact any Orbit payroll-input row created after the freeze |

## The parity engine (the trial run)

Norm computes beside Orbit, which stays the system of record until each step is proven.
`fin_parity_runs` / `fin_parity_items` hold every comparison, weekly (the job) and on demand
from the Payroll page's Trial run tab (`POST /api/payroll/shadow/runs`, `.../exports-check` with the owner's
Ace files); each item is `expected` (a named class from `finance_core.parity.EXPECTED_CLASSES`
— Loaded-side attribution, dropped deleted/unfinished rows, the "Other team" line, the
one-day budget shift, summed percentages, gratuity cent residuals, "Orbit defaulted to #70",
"Loaded changed between snapshot and export", the importer's inferred legacy week) or
`unexplained`, signed off by the payroll person. The gates before each cut-over are in the
plan §14: three consecutive clean pay weeks from WE 15 Nov (exports), three clean journal
weeks including Christmas, three clean entertainer weeks, both rehearsal weeks explained,
four weeks of forecast scores before "Set Loaded budget" shows.

## Shipping the 24 Oct split (two Apps → three)

The Apps are opt-in, so each organisation's switch rows must move before the shared catalog
changes — otherwise an organisation with Loaded Payroll and Norm Finance on (Cook Brothers in
production) loses every Finance tool, skill and page the moment the new rows land. The order,
each step `--dry-run` first and every production write with the owner's go-ahead:

1. **Deploy** (push to main → production). The new code reads the old catalog rows through
   `LEGACY_SLUGS`, so nothing changes for anyone yet.
2. **`scripts/switch_finance_apps.py`, the switches phase, in every environment's app
   database** (production, testing, staging, local): for each `org_app_entitlements` row on
   `loaded-payroll` or `norm-finance` it adds one row per new App with the same on/off value
   (skipping rows that exist), keeps the old rows (a rollback still works), and rewrites any
   role's app list that names an old slug. Dry run by default; flags are in the script's
   docstring. Early copies are harmless: a switch row whose App has no catalog row is ignored.
3. **The catalog phase, once** (the shared config DB, after production serves the new code):
   `sync_marketplace_catalog.py --only payroll,entertainers,budget,bills` (expect create
   payroll, create entertainers, create budget, update bills), then the two old rows deleted
   (`--only` never retires a row) and the catalog cache invalidated. Never run the seed
   without `--only`. The script refuses while this environment still has an old switch row
   without its successors.
4. **The word re-syncs** (each writes live; dry-run and read the diff first):
   `sync_norm_finance.py` (skills, tool descriptions, the "Norm Payroll" display name — all
   four playbooks must stay on; never run the pre-split version after step 3),
   `sync_payroll_config.py` (the loadedhub refusals and descriptions), `sync_xero_connector.py`
   (the three Xero refusals, the `post_wage_journal` description and the Finance AI agent's
   description and persona — it rewrites the whole xero spec, so confirm nothing else moves),
   optionally `sync_marketplace_catalog.py --only finance-agent`, and
   `sync_approval_labels.py --dry-run` reporting every finance row up to date.
5. **Tell the payroll and finance people to reload** any open Norm tab: an old bundle gates
   the pages on the old slugs.

Old links keep working: `pageAliases.ts` maps every retired page id (`payroll-leave`,
`payroll-settings&view=xero`, `payroll-shadow&run=…`, `ent-roster&performance=…`,
`finance-forecasting`, `finance-budgets&view=versions`, …) to the new page and tab, and the
root path forwards `?page=` to `/app`.

## Ship-day runbook (23 Oct 2026 — history: the first ship as two Apps)

Kept as the record of how the modules first shipped; for the split, use the section above.
Step 3d's `--only loaded-payroll,norm-finance,xero` no longer works (`xero` stopped being a
catalog slug when Bills and Profit & Loss replaced the Xero App), and the old rows it names are
retired by the switch script.

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
   applied **in the same deploy** (without the `norm.caller_holds` row no Xero bill can carry a
   performer's invoice — the check answers "no") — then the three model migrations when
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
   # b. loadedhub: ten engine-only endpoint rows (incl. get_pay_adjustment_packages_raw) +
   #    get_payrun, get_weekly_financials, set_budgets, set_budgets_typed,
   #    get_pay_adjustments, get_closing_manager (refuses before (a): the budget rows call
   #    norm_finance.get_mode / record_budget_push)
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
     Payroll, Entertainers, Budget since the split; Loaded Payroll and Norm Finance before it);
     Bills and Profit & Loss are on already.
   - `organizations.payroll_contact_email` (D25) — without it every payroll and entertainer email in
     shadow is suppressed and logged, nothing is sent.
   - Per NZ entity in Payroll settings → Entities (the gear on the Payroll page): `payroll_on`, the Ace pay type code (`50001`), the
     gratuity allowance code, the security pay code, the $150 bond and 12 % uplift (D19,
     confirmed with the accountant), the intercompany leave line's tax rule, the sick-leave
     approver. Fill them from Orbit first, on their own, before the full import:
     `uv run python scripts/migrate_orbit_finance.py --org default --settings-only --dry-run`
     (then without `--dry-run`; add `--sick-approver NAME` — Orbit names the approver only in its
     email wording). It writes ONLY `pay_entities` and prints each venue's values and the Xero
     mapping the full import would set; a setting someone already changed in Norm is kept.
     Dry-run against the local copy on 23 Oct: the six mapped venues get pay run on, `50001`,
     `SECURITY`, $150, 12 %, `account_default`; gratuities allowance codes Bessie 13, Freeman &
     Grey 26, Glass Goose 25, La Zeppa 10, Mr Murdochs 25, Dunedin Social Club blank (Orbit
     has none); CBI (14) waits for its venue. Leave types need no seeding or sign-off — they are read
     from Loaded when the leave file is built. Open Payroll settings → Leave types per entity:
     any **company's own** type (live: "Paid Public Holiday" at Bessie & Royals and Mr Murdochs)
     needs its Ace style chosen with the accountant, or that week's leave export refuses it by
     name. The AU venues keep `payroll_on = false`.
   - Every NZ venue's `day_start_time` before the first Monday snapshot (D11: DSC and The
     Glass Goose 06:00 if Loaded agrees, 07:00 elsewhere) — it also moves those venues'
     trading day in every other Norm read.
   - Public holidays come from Loaded's pay adjustments: nothing to seed. The nightly job
     fills the next 12 months per venue once `finance_mode` is on; for history press
     **Sync from Loaded** in each venue's Forecast settings (the Budgets page's Forecast tab)
     (`POST /api/finance/holidays/sync {"venue_id"}`). Check each venue's regional anniversary
     day is activated in Loaded (Compliance › Pay Rules) — live 23 Oct, only Bessie & Royals
     has one (Canterbury, activated for 14 Nov 2025 only, so 13 Nov 2026 is not priced), and
     Loaded's managed dates run to 31 Dec 2026: 2027's appear when Loaded publishes them.
     Each venue's closed weekdays: `PUT /api/finance/settings`.
   - Load history once per venue: `POST /api/finance/forecast/load-history {"venue_id", "months": 24}`
     (reads only) — the first thing to press at a new venue; `POST /api/finance/forecast/backtest`
     populates the accuracy strip before anyone trusts a number.
   - **CBI** as a non-trading venue (`venues.is_trading = false`, D1) with its Loaded and Xero
     connections — the week of 9 Nov; the nightly importer refuses on the "every Orbit venue
     mapped" gate until it exists (`--skip-venue CBI` by hand until then).
   - **Xero consents** for all seven entities, from production, by each entity's Xero admin;
     after each one: `uv run python scripts/migrate_orbit_finance.py --org default --module payroll
     --write-xero-mapping` once (fills the `xero_mapping.payroll` / `.intercompany` keys nobody
     set; never overwrites a key a person set), then Payroll settings → Xero mapping validated
     per entity (`rounding` is new and comes from there). Two journal decisions for the accountant,
     both settings now: the **"Reclass Gratuities" account** (`xero_mapping.payroll.gratuities_reclass`
     under Payroll settings → Xero mapping — the importer reports Orbit's 41100 Bar Sales but never writes it; until
     it is chosen a week that pays gratuities through payroll is refused by name, never journalled
     without the line), and the **leave line's tax rule** on intercompany invoices and bills
     (Payroll settings → Entities, `pay_entities.ico_leave_tax`: `account_default` — Orbit's, the
     default — `no_gst` or `standard_gst`; set it the same on every entity, the push card warns
     when two differ).
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
     the Entertainers page's Emails tab). Without the secret the webhook answers 503 and nothing else changes.
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
   - The Loaded scope request (D13: plan §7's list **plus `core:pay-packages:r`** — the pay
     adjustment packages behind the public holidays, `GET /pay-rules/packages`,
     `RequireOneOf("Time,Payroll", core:pay-packages:r)`; the leave types need `core:leave:r`,
     already listed) and the Orbit-side actions (D27: RLS SELECT-only, the push
     functions undeployed on each date, the "this link has moved" banners via Lovable) — owner
     actions with named people, verified the day after each date.
5. **Golden files** (D18): head office keeps every week's four Ace files from now; parity
   byte-diffs Norm's exports against them on the Payroll page's Trial run tab.

## Cut-over calendar (from plan §15) and rollback

The trial run (mode `shadow`) through Christmas; **Tue 19 Jan 2027** payroll and entertainers cut over together for
WE 17 Jan (Orbit bookings frozen Mon 18 Jan 18:00 → final import → `payroll_mode` and
`entertainers_mode` → `live` → exports, payouts, entertainer lines, reconciliation, journal
and ICO documents from Norm; Norm's emails on; Orbit's entertainer screens, sweep, portal and
onboarding off); **Tue 2 Feb** budgets live from Norm (`finance_mode` → `live` for the push;
Orbit's budget pushes undeployed); **Wed 3 Feb** Orbit's payroll screens read-only after two
clean Norm weeks; **Mon 15 Feb** "Set Loaded budget" on the Budgets page's Forecast tab; **Mon 1 Mar** the
Weekly Summary retires Orbit's three report screens; **Mon 22 Mar** FY2028 budget imported and
pushed from Norm; **Wed 31 Mar** Orbit Finance off, read-only to 30 Apr, archived. Slip
clause: a slip is a missed trial-run week — without three clean journal weeks by Wed 13 Jan both
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

Taken as given: **D2** two Apps under Finance, no new AI agent; **D3** both Apps $0, the
AI agent $10 (D2 and D3 superseded on 24 Oct by the three job apps — Payroll, Entertainers,
Budget — still under Finance, still $0); **D4** the Financial Report lives inside the Weekly Summary; **D5** the journal and
intercompany tools on the Xero App (superseded on 24 Oct: they belong to Payroll); **D6** always-allow only on the Daily budget grid's typed push,
never in chat, never for the forecast push (a card every time, hidden until four weeks of
scores, 92-day cap); **D7** Ace Payroll (if Xero Payroll comes, only the export layer dies);
**D8** `payroll:*` for owner and payroll_admin only, `finance:read` + `budgets:write` for owner,
manager and payroll_admin, never team_member; `get_labour` NOT stripped (owner, 23 Oct: managers keep rates);
the Xero tools gated the same way; **D9** IRD, bank and DOB encrypted; **D10** Xero
DRAFT only; **D12** NZ payroll only, AU `payroll_mode = off`; **D14 a–g** personal onboarding
links only, the email code before bank/tax, finance accepts before payable, the IR330C signed on
the form (upload kept), band shares set by finance, amounts by email never on the link, the
unknown-lane performers decided from the importer's list; **D15** performers, roster and emails
on venue access, payments and bank/IRD on `payroll:write`; **D16 / D29** cut-over in January,
entertainers with payroll (the modes allow earlier, with the D29 bridge); **D21** text Ace
files and CSV of any grid; XLSX and PDF deferred; **D22** closing-manager roles default
`["Sales Manager"]` (an `ent_settings` list, editable in the Entertainers page's Settings);
**D26** settled (23 Oct): no performer sender of its own — Norm's address in the
organisation's name, `Reply-To` the payroll contact; **D23** purchases-based COGS, cash variance and
voids kept; **D24** the close runs on a snapshot; **D32** the Emails tab with Resend delivery
status and Orbit's send dates imported; **D33** the four monthly lines (intercompany fees,
finance costs, income tax, leave %) not carried; **D34** total-only forecast; **D35** the
invoice check on the extraction model, Norm's cost, paid Orbit invoices not re-read; **D36** the
declaration-only link exists.

Owner actions, not build (dated in §15 and the runbook above): **D1** CBI venue, the seven
Xero consents, the eighth Xero organisation named; **D11** every venue's day start and the AU
org's currency; **D13** the Loaded scope request; **D17** go-ahead on each production write;
**D18** Ace golden files kept from now; **D19** the bond and uplift confirmed; **D20** the Orbit
forecast crons stay until Norm Functions is live; **D25** the payroll contact; **D27** the
three Orbit-side actions with names; **D28** the statutory
sign-off; **D30** people and roles by 4 Jan; **D31** the Orbit credential.

Design notes the owner should know: the Monday snapshot job has no person, and `requires`
refuses callers with no person, so `snapshot.loaded()` runs the `get_payrun` consolidator
directly under the organisation's scope for the job (a platform "system caller" scope would
let that bypass go — backlog); `pay_ace_inputs` and `pay_accruals` are one row per week, so a
Norm-keyed and an Orbit-keyed row for the same rehearsal week cannot both exist (parity records
the Norm row as "check against Orbit by hand"); `level_correction_on` defaults to false;
`email_logs.status` gains `suppressed`; the Finance App rows carry no App note (the
catalog's note budget is full — the rules are on the tool descriptions and in the skills).

## Testing

`apps/api/tests/test_finance_*.py`, `test_payroll_*.py`, `test_entertainers_*.py`,
`test_consolidator_finance.py`, `test_payroll_xero*.py`, `test_app_map.py`; worlds in
`tests/payroll_world.py`, `entertainers_world.py`, `finance_f6_world.py`. The public routes
(portal, onboarding, the code, the webhook) and the D15 gate (a `team_member` with venue
access approves at their venue, is refused at another, cannot open the Payroll page, never sees
bank or IRD) are blocking CI tests. The three consolidators were run live on La Zeppa (reads
only) and their shapes recorded in `shapes.json`; the forecast backtest reproduced the
research reader's figures on real data (Bessie inside both targets, La Zeppa not — the human
budget still wins there, which is the frame the plan sets). Web logic tests sit next to the
components (`*.test.ts`).
