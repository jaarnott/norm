"""The two Finance Apps in the marketplace catalog — Loaded Payroll and Norm
Finance — declared once (plan §4).

No new member: the ``finance`` member exists (catalog ``finance-agent``,
hired per organisation) and owns the Xero App; these two join it, so one
Finance tab holds Bills, Payroll and Reports/Budgets/Forecasting.
``scripts/sync_marketplace_catalog.py`` seeds both rows from ``APPS``
(``_app(**APPS[slug])``) — F11 finalises them. Until it has, the entitlement
checks stand in unsaved rows built from this same declaration
(``stand_in_rows``), so nothing is written to the database production reads
while an organisation is switched on locally.

**Both ship ``bundled=False``** (``_app()`` defaults to bundled, which would
switch them on for every organisation whose Finance member is hired the
moment the rows sync) and carry no price (a priced row needs a Stripe key).
Neither carries an App note: the catalog's notes already fill the validator's
budget (1,458 of 1,500 characters across every App, ``check_app_notes``), and
the rules a note would hold — never quote a pay figure or budget you haven't
read; nothing here changes Loaded or Xero from chat alone — are on the tool
descriptions, which travel with the tools, and in the four skills.

**The mode rule.** ``entitled_after_modes`` is what ``entitlements`` calls:
``loaded-payroll`` counts only while ``payroll_mode`` or ``entertainers_mode``
is on, ``norm-finance`` only while ``finance_mode`` is — ``off`` beats any
entitlement, so an organisation in ``off`` is neither shown nor billed for
an App it has not switched on. The Functions rule is applied beside it, not
replaced.

**Before the seed.** ``catalog_view`` (the team page) and ``adjust_apps_on``
(the ``apps_on`` hook) stand the rows in while the config DB has none, the
way Functions' are: an App whose modes are on counts as on, one whose modes
are off is not listed at all. Once the row exists the organisation switches
the App on from the Team page (``bundled=False``) and the mode can still veto.
"""

from __future__ import annotations

from app.services.finance_core import modes as modes_svc

#: The team member both Apps belong to: its agent_configs row,
#: app/agents/registry.MEMBERS, and the ``agent`` of the pages in the web's
#: pageRegistry. Exists already (catalog slug ``finance-agent``).
MEMBER = "finance"

PAYROLL_APP_SLUG = "loaded-payroll"
FINANCE_APP_SLUG = "norm-finance"

#: Tools the Loaded Payroll App claims (DESIGN-CONTRACT §4): rows the model
#: sees. ``loadedhub.get_payrun`` and the week tools carry
#: ``requires: ["payroll:read"]`` (writes ``payroll:write``); the entertainer
#: tools carry none — venue access only (D15; mark-paid refuses inside).
#: ``loadedhub.get_pay_adjustments`` (Loaded's pay adjustments and public
#: holidays, owner 23 Oct) carries none either: it holds no dollar rates.
PAYROLL_TOOLS = (
    "loadedhub.get_payrun",
    "loadedhub.get_pay_adjustments",
    "norm_payroll.get_week",
    "norm_payroll.record_week",
    "norm_payroll.get_entertainers",
    "norm_payroll.record_performance",
)
#: Engine-only rows beside them — called by consolidators and pages, never
#: shown to the model, so no App claims them (the validator exempts
#: ``engine_only`` rows from the App Map).
PAYROLL_ENGINE_ONLY = (
    "norm_payroll.get_mode",
    "norm_payroll.get_journal_draft",
    "norm_payroll.record_push",
    "loadedhub.get_closing_manager",
)

#: Tools the Norm Finance App claims.
FINANCE_TOOLS = (
    "loadedhub.get_weekly_financials",
    "norm_finance.get_budget_plan",
    "norm_finance.get_forecast",
    "norm_finance.record_budget",
    "loadedhub.set_budgets",
)
FINANCE_ENGINE_ONLY = (
    "norm_finance.get_mode",
    "norm_finance.record_budget_push",
    "loadedhub.set_budgets_typed",
)

PAYROLL_SKILLS = ("weekly_payroll_close", "leave_payout_rules")
FINANCE_SKILLS = ("budget_review", "forecast_review")


def _page(key: str, page_id: str, label: str, icon: str, description: str) -> dict:
    """A component with its menu page, in the shape the seed's ``_c`` writes.
    Every Finance page loads its own data from /api/payroll/*,
    /api/entertainers/* or /api/finance/*."""
    return {
        "key": key,
        "page": {"id": page_id, "label": label, "icon": icon},
        "full_width": True,
        "description": description,
    }


def _display(key: str, description: str) -> dict:
    """A display component with no menu page: rendered inside chat by a tool
    whose row names it as ``display_component`` (the ``xero_bills`` shape
    without a page). The validator refuses such a tool row unless an App's
    composition declares the component."""
    return {"key": key, "page": None, "full_width": True, "description": description}


#: Page id -> the org scope the web gate needs (``FunctionalPageConfig.permission``,
#: DESIGN-CONTRACT §7). A page not listed shows to every user of an
#: organisation whose App is on; the server-side ctx then limits them to
#: their venues (the entertainer Register, Roster and Emails rely on this).
PAGE_PERMISSIONS = {
    "payroll-payrun": "payroll:read",
    "payroll-leave": "payroll:read",
    "payroll-intercompany": "payroll:read",
    "payroll-reconciliation": "payroll:read",
    "payroll-settings": "payroll:read",
    "payroll-shadow": "payroll:read",
    "ent-payments": "payroll:write",
    "finance-summary": "finance:read",
    "finance-budgets": "finance:read",
    "finance-budget-vs-loaded": "finance:read",
    "finance-forecasting": "finance:read",
}

#: Keyword arguments for the seed's ``_app``, per slug. The pages mirror the
#: web's pageRegistry entries (DESIGN-CONTRACT §7 — the coordinator registers
#: them; tests pin ids, labels and icons against this declaration).
APPS: dict[str, dict] = {
    PAYROLL_APP_SLUG: {
        "slug": PAYROLL_APP_SLUG,
        "name": "Loaded Payroll",
        "member": MEMBER,
        "icon": "💵",
        "description": (
            "The weekly pay run on Loaded's time: payrun and Ace exports, leave "
            "payouts, intercompany, the wages reconciliation and Xero journals, "
            "and the entertainers' roster and payments."
        ),
        "tools": list(PAYROLL_TOOLS),
        "skills": list(PAYROLL_SKILLS),
        "bundled": False,
        "components": [
            _page(
                "payroll_payrun",
                "payroll-payrun",
                "Payrun",
                "Banknote",
                "One entity's pay week: every shift, leave and gratuity to pay, "
                "and the Ace import files.",
            ),
            _page(
                "payroll_leave",
                "payroll-leave",
                "Leave",
                "CalendarOff",
                "Leave requests touching the week, what Loaded says they are "
                "worth and what payroll decided to pay.",
            ),
            _page(
                "payroll_intercompany",
                "payroll-intercompany",
                "Intercompany",
                "ArrowLeftRight",
                "Shifts worked at one entity and paid by another, and the "
                "recharges between them.",
            ),
            _page(
                "payroll_reconciliation",
                "payroll-reconciliation",
                "Reconciliation",
                "Scale",
                "Loaded's cost against the Ace pay run, adjustments and accruals, "
                "and the Xero journal and intercompany documents.",
            ),
            _page(
                "payroll_settings",
                "payroll-settings",
                "Payroll settings",
                "Settings",
                "Each entity's payroll settings, leave styles and the dated "
                "statutory rules.",
            ),
            _page(
                "payroll_shadow",
                "payroll-shadow",
                "Shadow",
                "Eye",
                "Parity runs against Orbit during the shadow period: every "
                "difference, explained or not, and the job log.",
            ),
            _page(
                "entertainers_register",
                "ent-register",
                "Entertainers",
                "Music",
                "Performers: who they are, how they are paid, their onboarding "
                "and band members.",
            ),
            _page(
                "entertainers_roster",
                "ent-roster",
                "Roster",
                "CalendarDays",
                "Performances by venue and week: booking, approval, the "
                "performer's confirmation and the closing manager.",
            ),
            _page(
                "entertainers_payments",
                "ent-payments",
                "Payments",
                "Wallet",
                "Approved performances to pay: through payroll or as a Xero "
                "bill, with the invoice check.",
            ),
            _page(
                "entertainers_emails",
                "ent-emails",
                "Emails",
                "Mail",
                "Every email the module sent a performer, band member or manager, "
                "and whether it was delivered.",
            ),
        ],
    },
    FINANCE_APP_SLUG: {
        "slug": FINANCE_APP_SLUG,
        "name": "Norm Finance",
        "member": MEMBER,
        "icon": "📈",
        "description": (
            "The weekly financial summary, the locked sales budget with its "
            "event notes, budget against Loaded and actual, and a daily sales "
            "forecast that can set Loaded's budget."
        ),
        "tools": list(FINANCE_TOOLS),
        "skills": list(FINANCE_SKILLS),
        "bundled": False,
        "components": [
            _page(
                "finance_summary",
                "finance-summary",
                "Weekly Summary",
                "BarChart3",
                "Last week and month to date, per venue and for the group: "
                "sales, wages, COGS and profit against budget.",
            ),
            _page(
                "finance_budgets",
                "finance-budgets",
                "Budgets",
                "Table",
                "Each venue's daily sales budget by month with event notes, and "
                "the monthly line items.",
            ),
            _page(
                "finance_budget_vs_loaded",
                "finance-budget-vs-loaded",
                "Budget vs Loaded",
                "GitCompare",
                "The locked budget beside Loaded's live budget and actual sales, "
                "by day.",
            ),
            _page(
                "finance_forecasting",
                "finance-forecasting",
                "Forecasting",
                "TrendingUp",
                "Norm's daily sales forecast, its drivers and overrides, how it "
                "has scored, and setting Loaded's budget from it.",
            ),
            # loadedhub.get_weekly_financials renders this in chat (F2's
            # apps/web/app/components/display/FinanceWeekly.tsx).
            _display(
                "finance_weekly",
                "The week's financials for one or more venues, as the chat shows them.",
            ),
        ],
    },
}

#: Both App rows. A module's ``off`` takes its App out of an organisation's
#: entitlements whatever the catalog says.
CATALOG_SLUGS = frozenset(APPS)


def off_slugs(modes: dict) -> frozenset[str]:
    """The App slugs the modes switch off: Loaded Payroll while payroll AND
    entertainers are off, Norm Finance while finance is."""
    out = set()
    if (
        modes.get("payroll", "off") == "off"
        and modes.get("entertainers", "off") == "off"
    ):
        out.add(PAYROLL_APP_SLUG)
    if modes.get("finance", "off") == "off":
        out.add(FINANCE_APP_SLUG)
    return frozenset(out)


def entitled_after_modes(entitled: set[str], org_id: str | None, db) -> set[str]:
    """The entitlements hook (``services/entitlements.py``): drop whichever
    of the two Apps the organisation's modes have off. The modes are only
    read when one of the two is entitled."""
    if not entitled & CATALOG_SLUGS:
        return entitled
    return entitled - off_slugs(modes_svc.all_modes(db, org_id))


def _off_and_missing(catalog_rows, organization_id, db) -> tuple[frozenset, set]:
    """(the slugs the organisation's modes switch off, the slugs that are on
    but have no catalog row yet). No organisation reads as every mode off."""
    modes = modes_svc.all_modes(db, organization_id) if organization_id else {}
    off = off_slugs(modes)
    present = {getattr(r, "slug", None) for r in catalog_rows}
    return off, {s for s in CATALOG_SLUGS - off if s not in present}


def catalog_view(catalog_rows, organization_id, db) -> tuple[list, set[str]]:
    """The catalog as this organisation's team page sees the two Apps (the
    ``_native_view`` hook in ``services/agent_catalog.py``): an App whose
    modes are off is not there at all — not "available to switch on", the
    mode switches it (the Functions rule); one whose modes are on but whose
    row the seed hasn't written yet is stood in by ``stand_in_rows``, so the
    team page, the sidebar and the page gate agree with ``apps_on``. Returns
    the rows and the stand-in slugs — rows with nothing behind them to switch."""
    off, missing = _off_and_missing(catalog_rows, organization_id, db)
    kept = [r for r in catalog_rows if getattr(r, "slug", None) not in off]
    return kept + stand_in_rows(missing), missing


def adjust_apps_on(
    on: set[str], organization_id, db, catalog_rows, *, hired=None
) -> set[str]:
    """The ``apps_on`` hook (``services/entitlements.py``), Functions'
    ``adjust_apps_on`` mirrored: until the seed writes an App's row, the App
    counts as on exactly when its module's mode is on — and only while the
    Finance member is hired, because the App is the member's. Once the row
    exists this adds nothing: the organisation switches the App on from the
    Team page (both ship ``bundled=False``), and ``entitled_after_modes``
    still lets ``off`` veto."""
    if hired is not None and MEMBER not in hired:
        return on
    _, missing = _off_and_missing(catalog_rows, organization_id, db)
    return on | missing


def stand_in_rows(missing) -> list:
    """Unsaved ``MarketplaceApp`` rows, shaped as the seed writes them, for
    whichever of the two slugs in ``missing`` the config DB doesn't hold yet.

    Never priced, never bundled: nothing bills for a row that doesn't exist,
    and nothing switches on by itself. Never added to a session — they are
    read like catalog rows and thrown away."""
    from app.db.config_models import MarketplaceApp

    rows = []
    for slug, app in APPS.items():
        if slug not in missing:
            continue
        composition = {
            "member": app["member"],
            "tools": list(app.get("tools", ())),
            "components": [dict(c) for c in app["components"]],
            "skills": list(app.get("skills", ())),
        }
        if app.get("note"):
            composition["note"] = app["note"]
        rows.append(
            MarketplaceApp(
                slug=slug,
                name=app["name"],
                description=app["description"],
                icon=app["icon"],
                tier="app",
                bundled=False,
                price_cents=0,
                stripe_price_key=None,
                status="active",
                composition=composition,
            )
        )
    return rows
