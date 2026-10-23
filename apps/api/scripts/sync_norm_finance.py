"""The Finance Apps' config rows: the ``norm_payroll`` and ``norm_finance``
connector specs, the four skills, and the MCP read capabilities.

Upserts, in the SHARED config DB (dry-run first — committing reaches
production immediately):

- the ``norm_payroll`` spec: one TOOL row per built-in in
  app/agents/payroll_tools.py, payroll_xero_tools.py and
  entertainers_tools.py, and the ``norm_finance`` spec: one per built-in in
  app/agents/finance_tools.py. Each row carries a ``field_schema`` for every
  parameter (without one the model sends strings, and a list sent as a
  string is silently dropped), its ``effect`` + approval policy from
  scripts/sync_approval_labels.py (one source for the labels, so the rows
  are labelled the moment they exist), ``requires`` (the org scopes a
  caller's person must hold — DESIGN-CONTRACT §1/§4) and, on the internal
  rows, ``engine_only`` (never shown to a model; called by consolidators
  and pages). The two Apps claim the rows
  (app/services/finance_core/catalog.py ``PAYROLL_TOOLS`` / ``FINANCE_TOOLS``).
- the four skills (playbooks) the Apps own — ``weekly_payroll_close`` and
  ``leave_payout_rules`` on Loaded Payroll, ``budget_review`` and
  ``forecast_review`` on Norm Finance — written the way
  scripts/sync_norm_functions.py writes ``PLAYBOOKS``: created switched OFF,
  and switched on only once the App claims them (an unclaimed enabled
  playbook would be offered to every organisation's agent).
- the three ``mcp_capabilities`` rows that put the reads on Norm's MCP
  (plan §12): ``loadedhub.get_payrun`` behind ``mcp:payroll:read``,
  ``loadedhub.get_weekly_financials`` and ``norm_finance.get_forecast``
  behind ``mcp:finance:read`` (app/mcp/scopes.py). Writes stay behind
  Norm's cards; the other reads duplicate these or carry no scope.

No team member here: the ``finance`` member exists (scripts/sync_xero_connector.py
writes its agent_configs row) and owns the Xero App; the two Apps join it.

**The registry contract.** Each tools module registers its handlers with
``app.agents.internal_tools.register(CONNECTOR, action)`` as every built-in
does, and beside them declares the rows this script installs::

    CONNECTOR = "norm_payroll"            # or "norm_finance"
    TOOL_ROWS: list[dict] = [
        {
            "action": "get_week",
            "method": "GET",
            "description": "...",
            "required_fields": [...],
            "optional_fields": [...],
            "field_schema": {...},        # every parameter
            "requires": ["payroll:read"], # [] for venue-access-only tools
            "engine_only": False,         # True: internal; no label, no App claim
            "max_result_chars": 40_000,   # optional
        },
        ...
    ]

The script imports the three modules lazily (a module that does not exist
yet is skipped), checks that every row has a registered handler and every
handler a row, that a claimed row is in its App's tool list, that every
``requires`` scope is known, and that every non-engine-only row has an
approval label — and refuses to install otherwise. It works with zero
handlers: then both specs are installed with no rows, which is what the
validator expects before the modules ship.

Ship-day order — code first: the config DB is shared, and production must
know the handlers before their rows appear (docs/norm-finance.md has the
whole runbook).

  1. deploy (the tools modules registered from internal_tools).
  2. this script:                  --dry-run, then for real — the two specs
     and their rows (labelled), the skills (off), and the MCP capability
     for norm_finance.get_forecast; the two loadedhub capabilities are
     skipped with a note until step 3 has written their rows.
  3. sync_payroll_config.py, then sync_xero_connector.py: the loadedhub and
     xero rows (the validator refuses set_budgets / get_weekly_financials
     before step 2 — they call norm_finance rows).
  4. sync_marketplace_catalog.py --only loaded-payroll,norm-finance,xero:
     --dry-run, then for real — creates the two App rows (claiming the tools
     and skills) and the Xero App's three payroll tools.
  5. this script again — switches the claimed skills on and adds the two
     loadedhub MCP capabilities.
  6. sync_approval_labels.py:      --dry-run — every finance row reports
     "up to date"; apply only if it doesn't.

Idempotent. Dry-run first.

Usage:
    uv run python scripts/sync_norm_finance.py --dry-run
    uv run python scripts/sync_norm_finance.py
"""

from __future__ import annotations

import argparse
import copy
import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.services.finance_core.catalog import (  # noqa: E402 — path set above
    FINANCE_APP_SLUG,
    FINANCE_ENGINE_ONLY,
    FINANCE_SKILLS,
    FINANCE_TOOLS,
    MEMBER,
    PAYROLL_APP_SLUG,
    PAYROLL_ENGINE_ONLY,
    PAYROLL_SKILLS,
    PAYROLL_TOOLS,
)

PAYROLL_CONNECTOR = "norm_payroll"
FINANCE_CONNECTOR = "norm_finance"
CONNECTORS = (PAYROLL_CONNECTOR, FINANCE_CONNECTOR)

#: The tools modules that register handlers and declare rows, by connector.
TOOL_MODULES = {
    PAYROLL_CONNECTOR: (
        "app.agents.payroll_tools",
        "app.agents.payroll_xero_tools",
        "app.agents.entertainers_tools",
    ),
    FINANCE_CONNECTOR: ("app.agents.finance_tools",),
}

#: The org scopes a row may require (DESIGN-CONTRACT §1).
KNOWN_SCOPES = frozenset(
    {"payroll:read", "payroll:write", "finance:read", "budgets:write"}
)

#: Each connector's claimed and engine-only tools, as the catalog declares them.
CLAIMED = {
    PAYROLL_CONNECTOR: frozenset(PAYROLL_TOOLS),
    FINANCE_CONNECTOR: frozenset(FINANCE_TOOLS),
}
ENGINE_ONLY = {
    PAYROLL_CONNECTOR: frozenset(PAYROLL_ENGINE_ONLY),
    FINANCE_CONNECTOR: frozenset(FINANCE_ENGINE_ONLY),
}

#: The reads on Norm's MCP (plan §12): exactly these three, not every read
#: tool. ``norm_payroll.get_week`` duplicates what ``get_payrun`` gives an
#: MCP client; ``get_entertainers`` carries no ``requires`` (venue access
#: only), so no MCP scope maps onto it; ``get_budget_plan`` rides inside
#: ``get_weekly_financials``. Writes never go out as direct tools.
MCP_CAPABILITIES = (
    ("loadedhub", "get_payrun", "mcp:payroll:read"),
    ("loadedhub", "get_weekly_financials", "mcp:finance:read"),
    (FINANCE_CONNECTOR, "get_forecast", "mcp:finance:read"),
)

SPECS = {
    PAYROLL_CONNECTOR: {
        "display_name": "Loaded Payroll",
        "category": "internal",
        "execution_mode": "internal",
        "auth_type": "none",
        "auth_config": {},
        "base_url_template": None,
        "credential_fields": [],
        "api_documentation": (
            "Norm's own payroll module on Loaded's time: pay weeks and their "
            "snapshots, leave payouts, gratuities, the wages reconciliation and "
            "Xero journal drafts, and the entertainers' roster and payments. "
            "Every tool is a built-in (app/agents/payroll_tools.py, "
            "entertainers_tools.py) acting as the person in the conversation; "
            "Loaded and Xero are reached only through their own connectors."
        ),
        "enabled": True,
    },
    FINANCE_CONNECTOR: {
        "display_name": "Norm Finance",
        "category": "internal",
        "execution_mode": "internal",
        "auth_type": "none",
        "auth_config": {},
        "base_url_template": None,
        "credential_fields": [],
        "api_documentation": (
            "Norm's own finance module: the weekly summary, the locked sales "
            "budget with its event notes, and the daily sales forecast. Every "
            "tool is a built-in (app/agents/finance_tools.py) acting as the "
            "person in the conversation; Loaded's budget is written only "
            "through the loadedhub connector's set_budgets tools."
        ),
        "enabled": True,
    },
}


# ---------------------------------------------------------------------------
# Skills — the four playbooks the Apps own (plan §12). Each says what the
# Finance member does step by step with its tools and pages, what stays a
# person's, and the shadow rule. Written in the words a payroll or finance
# person would use; the tool names are the only jargon.
# ---------------------------------------------------------------------------

WEEKLY_PAYROLL_CLOSE = """\
Goal: walk one payroll entity's pay week from Monday's snapshot to a closed week with the payroll person — reading Norm's figures, pointing at what still needs fixing in Loaded, recording the typed inputs they give you, and leaving the steps that belong to a person to them.

**The week.** A pay week runs from Monday at the venue's day start to the next Monday at day start. `week_start` is the Monday, `week_ending` the Sunday; "last week" is the most recent completed week, which is what `norm_payroll.get_week` reads when no week is named. Every entity (a venue that runs its own pay run) closes separately — name the entity before you read anything, and ask if the person has more than one.

**Figures.** The tools return integer cents; speak in dollars. Never quote a wage, hours, a payout or a total you have not read from `norm_payroll.get_week`, `loadedhub.get_payrun` or the person. `get_week` is the week as Norm holds it; `get_payrun` is a live read of Loaded for the same week (views: staff, security, leave, salaried, hygiene, intercompany, all — `rows: false` keeps just the sections for chat).

**Shadow.** While Loaded Payroll is in shadow, Orbit is still the system of record: Norm computes beside it, and `record_week` refuses a write unless `fields.rehearsal` is true (a rehearsal row is stored flagged and never feeds an export, a draft or a journal). When a write is refused with "not live", say that is the shadow rule, not an error. In shadow the hygiene and parity emails go to the organisation's payroll contact.

The steps, in order, and who does each:

1. **Monday — the snapshot (Norm).** Thirty minutes after the venue's day start, Norm takes the week's snapshot: `get_payrun view=all` with the entity's own Loaded login, stored as the week's shift facts. `get_week` shows it under `snapshot` (when it was taken, how many shifts, how many deleted and unfinished clock-ins were dropped). No snapshot yet → the person takes one on the Payrun page; you cannot take one from chat.

2. **Monday — hygiene (the person, in Loaded).** `get_week.hygiene_from_snapshot` lists unapproved clock-ins and staff without a pay code; `get_payrun view=hygiene` adds unfinished clock-ins, staff without a timeline, the week's public holidays and a parity line against Loaded's own pay summary. Every fix happens in Loaded — approving a clock-in, finishing one, giving someone a pay code, changing a rate or a timeline. Norm approves nothing in Loaded. The Monday 20:00 hygiene email sends the same lists to the payroll contact.

3. **Tuesday — the close snapshot (Norm, or the person).** At 10:00 venue time Norm re-snapshots the week, or earlier when the person presses "Start close" on the Payrun page. The exports, the reconciliation and the journal build from this close snapshot; anything Loaded changes after it shows in `get_week.drift` ("drift since close"). If the drift is not empty, say what moved and let the person decide whether to re-snapshot.

4. **Leave (the person decides; you record).** `get_payrun view=leave` is what Loaded says the week's leave is worth; `get_week.payouts` is what payroll decided to pay. A decision is recorded with `record_week op=leave_payout` — the rules for annual, sick and generic payouts are in the `leave_payout_rules` skill; read it before recording one.

5. **Gratuities (monthly).** A venue's pool for a month is split by Norm over the hours physically worked there across the overlapping pay weeks (leave and security excluded, inactive staff keep a share that is not payable, excluded staff redistributed, cents by largest remainder). Record a batch with `record_week op=gratuity_batch` (`fields`: `period` "YYYY-MM", `total_cents`, `gl_code`, optional `excluded_staff_ids`); the shares appear under `get_week.gratuities`. A reminder goes out on the 1st of each month.

6. **Entertainers.** Approved, unpaid payroll-lane performances carry forward into the week's IMPORT_ENTERTAINERS.TXT; marking them paid is the Payments page (`payroll:write`). A performer with no pay code, or one finance has not accepted yet, blocks the file and is named — nothing is paid silently.

7. **Exports (the person).** The person generates the four Ace files on the Payrun page — IMPORT.TXT, IMPORT_LEAVE.TXT, GRATUITIES.TXT, IMPORT_ENTERTAINERS.TXT — from the close snapshot, and imports them into Ace. Norm refuses the files when there is no close snapshot, a staff member has no pay code, a leave type has no Ace style, or the entity has no gratuity allowance code; you can see most of those coming from `get_week` and warn before they press the button. You never generate or import the files.

8. **The Ace run (outside Norm).** When Ace has run, the person gives you gross, net, ESCT and KiwiSaver; record them with `record_week op=ace_inputs` (`fields`: `gross_cents`, `net_cents`, `esct_cents`, `kiwisaver_cents` — $12,345.67 is 1234567).

9. **Reconciliation.** `get_week.inputs` holds the Ace totals, the adjustments and the accrual; `get_week.parity` the last parity run. Record an adjustment with `record_week op=adjustment` (`fields`: `label`, `amount_cents` signed, `xero_account_code`, `report_target` wages | leave | null, `notes`) and the accrual with `op=accrual` (`opening_cents` or null, `accrued_cents`, `paid_cents`). The Reconciliation page explains Loaded's cost against Ace gross — timeclock, plus intercompany, plus leave paid, less security, less accrued additions, plus accrued deductions, plus adjustments, entertainers and gratuities — in bands: ok under $1, near under $50, off above. Blockers (no close snapshot, Ace inputs missing, a leave request with no payout, an adjustment with no account, a missing Xero mapping) are listed on the page; read them back rather than guessing the cause.

10. **The journal and the intercompany documents (Xero App tools, a card every time).** The person builds the journal draft on the Reconciliation page. `xero.post_wage_journal` posts it to Xero as a DRAFT manual journal; `xero.post_intercompany_documents` posts the DRAFT invoice and bill per counterparty; `xero.void_payroll_document` takes one back. Each needs `payroll:write`, previews every line on a card, refuses while payroll is not live, and is done only when the person asks. The accountant posts the drafts inside Xero.

11. **Close (the person).** "Close week" on the Payrun page records who closed it and when. There is no tool for it.

**Always a person, never you:** generating and importing the Ace files; closing a week; the entity's settings (pay type and allowance codes, the security pay code, the uniform bond, the leave uplift), the leave styles and their sign-off, and the Xero account mappings; approving clock-ins and changing pay rates or timelines (Loaded's job); signing off a parity difference on the Shadow tab. In a scheduled task's run nothing is recorded: `record_week` refuses, and says why.
"""

LEAVE_PAYOUT_RULES = """\
Goal: record a leave payout decision for a pay week correctly — the three workflows, the gates each must pass, and what you may never decide for the person. Read `weekly_payroll_close` for where leave sits in the week.

**What you read.** `loadedhub.get_payrun view=leave` lists the week's leave requests as Loaded prices them: the request id, the staff member, the leave type, the dates, hours and the requested cost. `norm_payroll.get_week.payouts` lists the decisions already recorded. The Leave page shows both side by side.

**What you record.** `norm_payroll.record_week op=leave_payout` with `fields` = the Leave page's body: `loaded_request_id`, `staff_member_id`, `staff_name`, `leave_type`, `requested_cents`, `workflow_kind` (annual | sick | generic), `inputs` (below), optional `paid_cents`, `notes`, `rehearsal`, and `version` on an edit. Norm works the paid amount out from the inputs; a `paid_cents` override may only lower it. Cents in, dollars when you speak. Use `preview: true` when the figures are large or the person is unsure — the card shows exactly what would be recorded.

**Annual leave.** Paid = the smaller of what was requested and what is available after the bond. `inputs`: `holiday_pay_available_cents` (the balance lives in Ace — the person types it; never estimate it), `uniform_bond_cents` (the entity's default, $150), and either `bond_consent_file_id` or `bond_held_in_ace: true`. The uniform bond may be deducted from holiday pay only with the employee's written consent (Wages Protection Act 1983 s 5): without a consent file on record or "held in Ace", Norm refuses the deduction and says so. Do not set either flag yourself — the person uploads the consent on the Leave page or states that Ace holds it.

**Sick leave.** Four gates, and the payout is $0 unless every one passes:
1. Qualification (Holidays Act 2003 ss 63–65): six months' current continuous employment, an average of at least 10 hours a week over the last 26 weeks, and at least 1 hour a week or 40 hours a month. The Leave page's eligibility check reads the facts: the start date from Loaded (the approver enters it when Loaded has none) and that person's own 26 weeks of clock-ins.
2. Balance: `available_days × daily_hours` must cover the hours claimed. A sick day is the hours that person would otherwise have worked that day (s 63), not a flat eight — the rule row carries 8 only as the default.
3. Certificate: received, or the approver overrides.
4. Payout: the sum of each day's hours × the hourly rate.
`inputs`: `qualification` {employment_start_date, six_months, avg_hours, continuous, override, override_notes}, `balance` {available_days, daily_hours, override, override_notes}, `certificate` {received, override, override_notes}, `payout` {day_hours: [{date, hours}], hourly_rate_cents}. An override is the approver's call with a note; never set one yourself. Three emails exist for the person to send from the Leave page: to the approver when someone does not qualify, to the staff member that nothing can be processed without a certificate, and a request for a certificate. Sick-leave inputs and notes are health information: Norm shows them only to people with `payroll:write`, and you do not repeat them in a thread others can read.

**Anything else (generic).** Paid may not exceed requested; `inputs` is empty.

**Rules that are dated.** The sick-leave thresholds and the default day length are rows in `pay_rules` with an effective date (the platform defaults, plus an organisation's overrides from the Settings page). When the Holidays Act replacement arrives, it is a new row, not a code change — if someone asks why a figure changed, the rule's effective date is the answer.

**Leave styles.** Each Loaded leave type maps to an Ace style on the Settings page (annual → #70, sick → #60, bereavement → #63 by default; family violence, alternative holiday, public holiday not worked, unpaid and parental leave must not be booked as annual leave). A type with no style, or one not signed off, refuses IMPORT_LEAVE.TXT by name. Mapping and signing off are the payroll person's and the accountant's, not yours.

**Imported history.** A payout whose source is `orbit_legacy` shows "week inferred from Orbit": Orbit's row carried no pay week and the importer took the week before it was keyed. Say so if one is questioned.

**Shadow.** In shadow a payout is recorded only as a rehearsal (`fields.rehearsal: true`); rehearsal rows never feed the export or the journal.
"""

BUDGET_REVIEW = """\
Goal: review a venue's trading week and its budget with the person — the Weekly Summary, Norm's locked budget and its monthly lines, the budget against Loaded's live figure and the actual — and change Norm's budget when asked, without ever setting Loaded's budget except through a card.

**The Weekly Summary.** `loadedhub.get_weekly_financials` (one week — `week_ending` any date in it; `venues` "all" for the group, or a name) renders the week and the month-to-date as tiles; reply with what the tiles do not say. Reading the figures: sales include GST; wage % is wages ÷ sales ex GST; food and beverage wages follow Loaded's role cost groups, and beverage wage % divides by beverage sales (Loaded's own report divides by total sales — the note says so); orders are placed orders, not paid; the cash-up variance and voids come from Loaded's cash-up sets and POS voids. A venue with an error block had its sales read fail — say so, never fill it with zeros. `budget_plan` inside each venue's week is `norm_finance.get_budget_plan`: the locked budget for the week and the month to date, the monthly lines prorated by day, and the week's adjusted wages and leave from the payroll reconciliation.

**Budgets in Norm.** A venue-year has budget versions; the latest locked version is the current budget, and `norm_finance.get_budget_plan` reads it. `norm_finance.record_budget` changes it, always asking first:
- `op=lines` sets a month's line items (`fiscal_year`, `month`, `lines`: wage_pct or wage_cents — never both — cogs_pct, food_cogs_pct, bev_cogs_pct, leave_cents, opex_cents, depreciation_cents, rebates_pct; a null removes a line). Ratios as fractions (0.29), money as cents.
- `op=days` sets daily amounts on an unlocked version (`version_id`, or a `label` for a new one; `days` [{date, amount_cents, note}]). A locked version refuses — the person unlocks it on the Budgets page or starts a new version.
Locking and unlocking a version, importing a spreadsheet and the fiscal-year settings are page actions.

**Budget vs Loaded.** The Budget vs Loaded page shows, by day and by month, the locked budget beside Loaded's live budget and the actual; variance is Loaded minus locked. Loaded's live figure is `loadedhub.get_budgets`; the actual is the nightly actuals cache.

**Setting Loaded's budget — three doors, never a fourth.**
1. The Budgets day grid: a person types a number and it syncs (the engine-only `set_budgets_typed`, which a person may let through) — "Sync month to Loaded" pushes a locked month the same way. Page actions, not yours.
2. The Forecasting page's "Set Loaded budget" — a card every time (see `forecast_review`).
3. Chat: `loadedhub.set_budgets` (`venue`, `days` [{date, amount}] in GST-inclusive dollars, `allow_zero`). It asks every time and can never be let through: read `loadedhub.get_budgets` first, keep to 92 days, say which days move by more than 25 %, and remember Loaded has no delete — a day created in Loaded stays. $0 days are skipped unless `allow_zero` is true.
Never set a budget from a scheduled task, and never from a number you worked out yourself without the person seeing it on the card.

**Shadow.** While Norm Finance is in shadow, reads and Norm's own budget edits work; every Loaded push is refused ("not live") — the shadow rule, not an error. In `off` the pages and tools are not there at all.

**Figures.** Cents from the norm_finance tools, dollars from the Loaded reads; say which you are quoting. Never quote a budget, a variance or a wage % you have not read.
"""

FORECAST_REVIEW = """\
Goal: explain and review Norm's daily sales forecast for a venue with the person — where each number comes from, how the forecast has scored against the human budget, and how a driver or an override changes it — and never push a forecast to Loaded except through the page's card.

**Reading it.** `norm_finance.get_forecast` (`venue`; `start` / `end`, default the next four weeks; `explain` a date for one day's workings) returns per day: the actual (when past), the forecast, the final figure (the override if one exists, else the forecast), Loaded's budget as logged last night, the locked Norm budget, the drivers and the holiday name. Cents, GST included, like Loaded's budget.

**How a day's number is made** (say it this way when asked "why"):
- the baseline is the median of the last eight same-weekday trading days (at least four), after taking each sample day's confirmed functions spend out, and skipping closed days, public holidays and days a driver flagged;
- uplifts are added on top and labelled: confirmed Norm Functions events on the day, a public holiday (last year's same holiday if the venue traded, else the baseline — always flagged), and manual drivers in dollars or percent;
- an override replaces the number, keeps who set it and why, and survives the nightly regeneration;
- last year is shown for reference and never blended in;
- the level correction (the recent four weeks against the previous four, damped and capped at ±15 %) is a per-venue switch, off by default.
`explain` gives the eight samples with each one's reason (ok, closed, holiday, driver, no data, deflated), the uplifts, the override and a one-line "why". The flag `thin_history` means fewer than eight usable samples.

**How it has scored.** Every night Norm scores the vintages made 1, 7 and 28 days earlier, and Loaded's budget as it stood 28 days earlier, against the day's actual: MAPE, median APE, bias, the share within ±10 % and ±20 %, and the monthly-sum error — the Forecasting page's accuracy strip shows the previous month. The targets are a monthly-sum error of 10 % or less and a daily median APE of 25 % or less at the steadier venues. Be straight about the standing: on real data the human budget still beats the forecast on daily accuracy at the busier venues, so present the forecast as a defensible starting point with reasons, not a prediction, and quote the strip rather than a feeling.

**Changing the inputs** — `norm_finance.record_budget`, which asks first:
- `op=driver`: a labelled uplift on a date (`date`, `kind` holiday | event | function | manual, `label`, `uplift_cents` or `uplift_pct`). Removing one is a page action.
- `op=override`: replace a day (`date`, `amount_cents`, `reason`), or `clear: true` to remove it. An override is the person's number — record their reason, not yours.
A driver or an override changes the next vintage; the page shows the new number after the nightly run (or when the person presses "Run now").

**Pushing to Loaded.** "Set Loaded budget" on the Forecasting page pushes the final figure (override, else forecast) for a day, a week or a month through `loadedhub.set_budgets` with a card every time; the button appears only once four weeks of nightly scores exist for the venue, and each push records which vintage it came from so the pushed series is scored on its own. From chat, `set_budgets` also asks every time and is never let through; a scheduled task never sets a budget. Never type a forecast into a budget push yourself unless the person asked for exactly that and sees it on the card.

**Always a person:** the venue's settings (holiday region, closed weekdays, the level-correction switch), loading history at a new venue, running a backtest, seeding the year's holidays, locking a budget version, and every Loaded push.

**Shadow.** In shadow the nightly vintages and scores run and the grid works; every Loaded push is refused ("not live"). Say so rather than retrying.
"""

#: slug -> playbook row. Description = what the agent's playbook menu shows.
PLAYBOOKS = {
    "weekly_payroll_close": {
        "display_name": "Weekly payroll close",
        "description": (
            "Walk one payroll entity's pay week with the payroll person: the "
            "Monday snapshot, what Loaded still needs fixed, the Tuesday close, "
            "leave, gratuities, entertainers, the Ace files, the Ace totals, the "
            "reconciliation and the Xero journal — which steps are Norm's, which "
            "are theirs. Use for any pay-week, payrun, hygiene, reconciliation "
            "or journal question."
        ),
        "instructions": WEEKLY_PAYROLL_CLOSE,
    },
    "leave_payout_rules": {
        "display_name": "Leave payout rules",
        "description": (
            "How a leave payout is decided and recorded: annual leave and the "
            "uniform bond's consent, sick leave's four statutory gates, generic "
            "leave, the dated rules, Ace leave styles. Use before recording or "
            "explaining any leave payout."
        ),
        "instructions": LEAVE_PAYOUT_RULES,
    },
    "budget_review": {
        "display_name": "Budget review",
        "description": (
            "Review a venue's week and its budget: reading the Weekly Summary, "
            "Norm's locked budget and monthly lines, budget against Loaded and "
            "actual, changing Norm's budget, and the three doors to Loaded's "
            "budget. Use for budget, weekly summary, wage % or variance questions."
        ),
        "instructions": BUDGET_REVIEW,
    },
    "forecast_review": {
        "display_name": "Forecast review",
        "description": (
            "Explain and review Norm's daily sales forecast: how a day's number "
            "is made, how it has scored against the human budget, drivers and "
            "overrides, and setting Loaded's budget from it. Use for any "
            "forecast or 'why this number' question."
        ),
        "instructions": FORECAST_REVIEW,
    },
}


# ---------------------------------------------------------------------------
# The registry: rows the tools modules declare
# ---------------------------------------------------------------------------


def _module(path: str):
    """The tools module, or None while it does not exist yet."""
    try:
        return importlib.import_module(path)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing and (path == missing or path.startswith(missing + ".")):
            return None
        raise


def declared_rows(connector: str) -> list[dict]:
    """The rows the connector's tools modules declare, validated against
    the registered handlers and the catalog. Raises ``SystemExit`` on any
    disagreement — a wrong row must never reach the shared config DB."""
    from app.agents.internal_tools import registered_actions

    rows: list[dict] = []
    for path in TOOL_MODULES[connector]:
        mod = _module(path)
        if mod is None:
            continue
        if getattr(mod, "CONNECTOR", None) != connector:
            raise SystemExit(f"{path}.CONNECTOR is not {connector!r}")
        for row in getattr(mod, "TOOL_ROWS", ()):
            rows.append(copy.deepcopy(row))
    actions = [r.get("action") for r in rows]
    if len(set(actions)) != len(actions):
        raise SystemExit(f"{connector}: duplicate tool rows ({actions})")
    handlers = {a for c, a in registered_actions() if c == connector}
    problems = []
    for a in sorted(handlers - set(actions)):
        problems.append(f"{connector}.{a} has a handler but no TOOL_ROWS entry")
    for row in rows:
        key = f"{connector}.{row.get('action')}"
        if row.get("action") not in handlers:
            problems.append(f"{key} has a row but no registered handler")
        if not isinstance(row.get("field_schema"), dict):
            problems.append(f"{key} has no field_schema")
        requires = row.get("requires", [])
        if not isinstance(requires, list) or any(
            s not in KNOWN_SCOPES for s in requires
        ):
            problems.append(f"{key}: requires must list known scopes, got {requires!r}")
        if row.get("engine_only"):
            if key not in ENGINE_ONLY[connector]:
                problems.append(f"{key} is engine_only but catalog.py does not list it")
        elif key not in CLAIMED[connector]:
            problems.append(f"{key} is not claimed by its App in catalog.py")
    if problems:
        raise SystemExit("\n".join(problems))
    return rows


def tool_rows(connector: str) -> list[dict]:
    """The rows as installed: each labelled from sync_approval_labels
    (engine-only rows take their ``effect`` from the row, default read)."""
    from scripts.sync_approval_labels import labels

    known = labels()
    out = []
    for row in declared_rows(connector):
        key = f"{connector}.{row['action']}"
        row.setdefault("requires", [])
        if row.get("engine_only"):
            row.setdefault("effect", "read")
            out.append(row)
            continue
        if key not in known:
            raise SystemExit(f"{key} has no effect label in sync_approval_labels.py")
        out.append({**row, **copy.deepcopy(known[key])})
    return out


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _locked(query, dry_run: bool):
    """Lock the rows a real run rewrites; a dry run (against the database
    production reads) takes no locks."""
    return query if dry_run else query.with_for_update()


def _spec(db, connector: str, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    rows = tool_rows(connector)
    fields = SPECS[connector]
    spec = _locked(
        db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == connector),
        dry_run,
    ).first()
    if spec is None:
        if not dry_run:
            db.add(
                ConnectionSpec(
                    connector_name=connector, tools=rows, endpoints=[], **fields
                )
            )
        return [f"create spec '{connector}' with {len(rows)} tool rows"]
    lines = []
    for k, v in fields.items():
        if getattr(spec, k) != v:
            lines.append(f"spec '{connector}': update {k}")
            if not dry_run:
                setattr(spec, k, v)
    changed = [r["action"] for r in rows if spec_rows.find_tool(spec, r["action"]) != r]
    if changed:
        lines.append(f"spec '{connector}': add/update tool rows {', '.join(changed)}")
        if not dry_run:
            spec.tools = spec_rows.upsert(spec.tools, rows)
            flag_modified(spec, "tools")
    if spec.endpoints is None:
        lines.append(f"spec '{connector}': split (endpoints = [])")
        if not dry_run:
            spec.endpoints = []
    if lines and not dry_run:
        spec.version = (spec.version or 0) + 1
    return lines


def _skills(db, dry_run: bool) -> list[str]:
    """The four playbooks, each enabled exactly when its App claims it — the
    catalog seed is what claims, so the first run creates them off and the
    run after the seed switches them on."""
    from app.db.config_models import MarketplaceApp, Playbook

    if sorted(PLAYBOOKS) != sorted(PAYROLL_SKILLS + FINANCE_SKILLS):
        raise SystemExit("PLAYBOOKS and catalog.py's skills disagree")
    claimed: set[str] = set()
    for slug in (PAYROLL_APP_SLUG, FINANCE_APP_SLUG):
        app = db.query(MarketplaceApp).filter(MarketplaceApp.slug == slug).first()
        claimed |= set((app.composition or {}).get("skills") or []) if app else set()
    lines = []
    for slug, fields in PLAYBOOKS.items():
        want = {"agent_slug": MEMBER, **fields, "enabled": slug in claimed}
        row = db.query(Playbook).filter(Playbook.slug == slug).first()
        state = "on" if want["enabled"] else "off until its App claims it"
        if row is None:
            lines.append(f"create playbook '{slug}' ({state})")
            if not dry_run:
                db.add(Playbook(slug=slug, **want))
            continue
        diff = [k for k, v in want.items() if getattr(row, k) != v]
        if diff:
            lines.append(f"update playbook '{slug}' ({', '.join(diff)}; {state})")
            if not dry_run:
                for k, v in want.items():
                    setattr(row, k, v)
    return lines


def mcp_rows() -> list[dict]:
    """The three capability rows, in the shape ``mcp_capabilities`` stores."""
    return [
        {"kind": "connector", "target": target, "action": action, "scopes": [scope]}
        for target, action, scope in MCP_CAPABILITIES
    ]


def _tool_row_exists(db, target: str, action: str) -> bool:
    """Is the capability's tool row in the config DB — or about to be, because
    this very run installs it? A capability pointing at a row that does not
    exist is a validator error, so the loadedhub ones wait for
    sync_payroll_config.py and are created by the run after it."""
    if target in CONNECTORS:
        return any(r["action"] == action for r in tool_rows(target))
    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    spec = (
        db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == target).first()
    )
    return spec is not None and spec_rows.find_tool(spec, action) is not None


def _mcp(db, dry_run: bool) -> list[str]:
    from app.db.config_models import McpCapability

    lines = []
    for want in mcp_rows():
        name = f"{want['target']}.{want['action']}"
        if not _tool_row_exists(db, want["target"], want["action"]):
            lines.append(
                f"skip MCP capability {name}: its tool row is not installed yet "
                "(run this script again after the row's sync script)"
            )
            continue
        row = (
            db.query(McpCapability)
            .filter(
                McpCapability.kind == want["kind"],
                McpCapability.target == want["target"],
                McpCapability.action == want["action"],
            )
            .first()
        )
        if row is None:
            lines.append(
                f"create MCP capability {want['kind']} {name} ({want['scopes'][0]})"
            )
            if not dry_run:
                db.add(McpCapability(enabled=True, **want))
        elif row.scopes != want["scopes"] or not row.enabled:
            lines.append(f"update MCP capability {want['kind']} {name}")
            if not dry_run:
                row.scopes, row.enabled = want["scopes"], True
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import app.agents.internal_tools  # noqa: F401 — registers the handlers
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changes: list[str] = []
        for connector in CONNECTORS:
            changes += _spec(db, connector, args.dry_run)
        changes += _skills(db, args.dry_run)
        changes += _mcp(db, args.dry_run)
        if not changes:
            print("Norm Finance config up to date")
            return 0
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            db.rollback()
            print("(dry run — nothing written)")
            return 0
        db.commit()
        print(f"committed {len(changes)} change(s)")
        from app.services.config_validator import validate_config

        summary = validate_config(config_db=db)
        print(
            f"validate_config: {'ok' if summary.get('ok') else summary.get('issue_count')}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
