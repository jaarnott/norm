"""The three Finance Apps in the marketplace catalog — Payroll, Entertainers
and Budget — declared once (plan §4; split into job apps 24 Oct 2026,
docs/plans/finance-menu-consolidation.md).

No new AI agent: the ``finance`` agent exists (catalog ``finance-agent``,
hired per organisation) and owns Bills and Profit & Loss; these three join
it, so one Finance menu holds Bills, Profit & Loss, Payroll, Entertainers,
the Weekly Summary and Budgets. ``scripts/sync_marketplace_catalog.py``
seeds the rows from ``APPS`` (``_app(**APPS[slug])``). Until it has, the
entitlement checks stand in unsaved rows built from this same declaration
(``stand_in_rows``), so nothing is written to the database production reads
while an organisation is switched on locally.

**All three ship ``bundled=False``** (``_app()`` defaults to bundled, which
would switch them on for every organisation whose Finance agent is hired the
moment the rows sync) and carry no price (a priced row needs a Stripe key).
None carries an App note: the rules a note would hold — never quote a pay
figure or budget you haven't read; nothing here changes Loaded or Xero from
chat alone — are on the tool descriptions, which travel with the tools, and
in the skills.

**One mode per App.** ``entitled_after_modes`` is what ``entitlements``
calls: Payroll counts only while ``payroll_mode`` is on, Entertainers only
while ``entertainers_mode`` is, Budget only while ``finance_mode`` is
(``MODE_OF``) — ``off`` beats any entitlement, so an organisation in ``off``
is neither shown nor billed for an App it has not switched on. The routers
gate each module by the same mode. The Functions rule is applied beside it,
not replaced.

**The two Apps they replace** (``LEGACY_SLUGS``): Loaded Payroll
(``loaded-payroll``) became Payroll and Entertainers, Norm Finance
(``norm-finance``) became Budget. Between the deploy of this code and the
catalog step (``scripts/switch_finance_apps.py``) the shared config DB still
holds the old rows, so:

- the old rows keep their old mode rule (Loaded Payroll off only while
  payroll AND entertainers are off) and still give their tools — tools reach
  Norm only through real catalog rows;
- the team page never lists them; it lists the new Apps' stand-ins instead
  (``catalog_view``);
- a new App with no row of its own counts as on exactly when the App it
  comes from is on for the organisation, and only while its own mode is on
  (``adjust_apps_on``). This is NOT ``entitlements.SUCCESSORS``: that map
  ignores modes, and would show Entertainers to an organisation whose
  entertainers mode is off.

Because the Apps are opt-in, each organisation's switch rows for the old
slugs must be copied to the new ones in every environment's app database
before the catalog step writes the new rows — ``switch_finance_apps.py``
does both, in that order. ``LEGACY_SLUGS`` and the legacy rule are removed
at the restructure's stage-5 tidy-up (docs/plans/agents-and-apps-
restructure.md §11).

**Before the seed.** ``catalog_view`` (the team page) and ``adjust_apps_on``
(the ``apps_on`` hook) stand the rows in while the config DB has none, the
way Functions' are: an App whose mode is on counts as on, one whose mode is
off is not listed at all. Once the row exists the organisation switches the
App on from the Team page (``bundled=False``) and the mode can still veto.
"""

from __future__ import annotations

from app.services.finance_core import modes as modes_svc

#: The AI agent the three Apps belong to: its agent_configs row,
#: app/agents/registry.MEMBERS, and the ``agent`` of the pages in the web's
#: pageRegistry. Exists already (catalog slug ``finance-agent``).
MEMBER = "finance"

PAYROLL_APP_SLUG = "payroll"
ENTERTAINERS_APP_SLUG = "entertainers"
BUDGET_APP_SLUG = "budget"

#: Tools the Payroll App claims (DESIGN-CONTRACT §4): rows the model sees.
#: ``loadedhub.get_payrun`` and the week tools carry
#: ``requires: ["payroll:read"]`` (writes ``payroll:write``).
#: ``loadedhub.get_pay_adjustments`` (Loaded's pay adjustments and public
#: holidays, owner 23 Oct) carries none: it holds no dollar rates.
PAYROLL_TOOLS = (
    "loadedhub.get_payrun",
    "loadedhub.get_pay_adjustments",
    "norm_payroll.get_week",
    "norm_payroll.record_week",
)
#: Payroll's Xero writes (Oct 2026, docs/plans/finance-migration.md §4, D5;
#: moved from Bills to Payroll 24 Oct): the payroll journal, the intercompany
#: invoices and bills, and the void — each ``requires: ["payroll:write"]`` on
#: its row (scripts/sync_xero_connector.py), invoked by the Payroll page's
#: Reconciliation and Intercompany tabs through their approval cards.
PAYROLL_XERO_TOOLS = (
    "xero.post_wage_journal",
    "xero.post_intercompany_documents",
    "xero.void_payroll_document",
)
#: Tools the Entertainers App claims. They carry no ``requires`` — venue
#: access only (D15; marking a performance paid refuses inside without
#: ``payroll:write``).
ENTERTAINERS_TOOLS = (
    "norm_payroll.get_entertainers",
    "norm_payroll.record_performance",
)
#: Tools the Budget App claims.
BUDGET_TOOLS = (
    "loadedhub.get_weekly_financials",
    "norm_finance.get_budget_plan",
    "norm_finance.get_forecast",
    "norm_finance.record_budget",
    "loadedhub.set_budgets",
)

#: Engine-only rows beside them — called by consolidators and pages, never
#: shown to the model, so no App claims them (the validator exempts
#: ``engine_only`` rows from the App Map).
PAYROLL_ENGINE_ONLY = (
    "norm_payroll.get_mode",
    "norm_payroll.get_journal_draft",
    "norm_payroll.record_push",
)
ENTERTAINERS_ENGINE_ONLY = ("loadedhub.get_closing_manager",)
BUDGET_ENGINE_ONLY = (
    "norm_finance.get_mode",
    "norm_finance.record_budget_push",
    "loadedhub.set_budgets_typed",
)

PAYROLL_SKILLS = ("weekly_payroll_close", "leave_payout_rules")
#: None for now: the tool descriptions say how booking, approval and paying
#: work (owner decision, 24 Oct 2026).
ENTERTAINERS_SKILLS: tuple[str, ...] = ()
BUDGET_SKILLS = ("budget_review", "forecast_review")

#: App slug -> the organisation mode (``finance_core.modes``) that switches it.
MODE_OF = {
    PAYROLL_APP_SLUG: "payroll",
    ENTERTAINERS_APP_SLUG: "entertainers",
    BUDGET_APP_SLUG: "finance",
}

#: The two Apps the three replaced (24 Oct 2026) -> the Apps that replaced
#: them. Their catalog rows stay live until switch_finance_apps.py deletes
#: them; their organisation switch rows are kept for a rollback. Remove this
#: map, and the legacy rule it drives, at the restructure's stage-5 tidy-up.
LEGACY_SLUGS: dict[str, tuple[str, ...]] = {
    "loaded-payroll": (PAYROLL_APP_SLUG, ENTERTAINERS_APP_SLUG),
    "norm-finance": (BUDGET_APP_SLUG,),
}
#: New App slug -> the old App it comes from.
PREDECESSOR = {new: old for old, news in LEGACY_SLUGS.items() for new in news}


def _page(
    key: str,
    page_id: str,
    label: str,
    icon: str,
    description: str,
    *,
    connection: str | None = None,
) -> dict:
    """A component with its menu page, in the shape the seed's ``_c`` writes.
    Every Finance page loads its own data from /api/payroll/*,
    /api/entertainers/* or /api/finance/*; ``connection`` is the system the
    screen is for (services/screens.py) — it shows only while the
    organisation has that system."""
    entry = {
        "key": key,
        "page": {"id": page_id, "label": label, "icon": icon},
        "full_width": True,
        "description": description,
    }
    if connection:
        entry["connection"] = connection
    return entry


def _display(key: str, description: str) -> dict:
    """A display component with no menu page: rendered inside chat by a tool
    whose row names it as ``display_component`` (the ``xero_bills`` shape
    without a page). The validator refuses such a tool row unless an App's
    composition declares the component."""
    return {"key": key, "page": None, "full_width": True, "description": description}


#: Page id -> the org scope the web gate needs (``FunctionalPageConfig.permission``,
#: DESIGN-CONTRACT §7). A page not listed shows to every user of an
#: organisation whose App is on; the server-side ctx then limits them to
#: their venues. Entertainers relies on this: its Payments tab is gated in
#: the page (``meta.can_pay``) and on the server (``payroll:write``).
PAGE_PERMISSIONS = {
    "payroll": "payroll:read",
    "finance-summary": "finance:read",
    "finance-budgets": "finance:read",
}

#: Keyword arguments for the seed's ``_app``, per slug. The pages mirror the
#: web's pageRegistry entries (tests pin ids, labels, icons and apps against
#: this declaration).
APPS: dict[str, dict] = {
    PAYROLL_APP_SLUG: {
        "slug": PAYROLL_APP_SLUG,
        "name": "Payroll",
        "member": MEMBER,
        "icon": "💵",
        "description": (
            "The weekly pay run on Loaded's time: hours, checks, leave, "
            "gratuities, entertainers' pay lines, intercompany, the wages "
            "reconciliation and Xero journal, and the Ace files."
        ),
        "tools": list(PAYROLL_TOOLS + PAYROLL_XERO_TOOLS),
        "skills": list(PAYROLL_SKILLS),
        "bundled": False,
        "works_with": ["loaded", "xero"],
        "components": [
            _page(
                "payroll",
                "payroll",
                "Payroll",
                "Banknote",
                "One entity's pay week in the order of the close (Hours, Checks, "
                "Leave, Gratuities, Entertainers, Intercompany, Reconciliation, "
                "Ace files) and the Trial run until the cut-over.",
                connection="loaded",
            ),
        ],
    },
    ENTERTAINERS_APP_SLUG: {
        "slug": ENTERTAINERS_APP_SLUG,
        "name": "Entertainers",
        "member": MEMBER,
        "icon": "🎤",
        "description": "Book, approve, pay and email the venues' performers.",
        "tools": list(ENTERTAINERS_TOOLS),
        "skills": list(ENTERTAINERS_SKILLS),
        "bundled": False,
        "works_with": ["loaded", "xero"],
        "components": [
            _page(
                "entertainers",
                "entertainers",
                "Entertainers",
                "Music",
                "Performances by venue and week, the performers and their setup, "
                "payments (payroll permission) and every email sent.",
                connection="loaded",
            ),
        ],
    },
    BUDGET_APP_SLUG: {
        "slug": BUDGET_APP_SLUG,
        "name": "Budget",
        "member": MEMBER,
        "icon": "🎯",
        "description": (
            "The Weekly Summary, the locked sales budget against Loaded and "
            "actual, and a daily sales forecast that can set Loaded's budget."
        ),
        "tools": list(BUDGET_TOOLS),
        # Loaded's live budget, owned by Sales Analysis: the Budgets page
        # compares against it and the budget_review skill names it.
        "uses_tools": ["loadedhub.get_budgets"],
        "skills": list(BUDGET_SKILLS),
        "bundled": False,
        "works_with": ["loaded"],
        "components": [
            _page(
                "finance_summary",
                "finance-summary",
                "Weekly Summary",
                "BarChart3",
                "Last week and month to date, per venue and for the group: "
                "sales, wages, COGS and profit against budget.",
                connection="loaded",
            ),
            _page(
                "finance_budgets",
                "finance-budgets",
                "Budgets",
                "Table",
                "Each venue's daily budget against Loaded and actual, the "
                "forecast, the monthly lines and the history of pushes to Loaded.",
                connection="loaded",
            ),
            # loadedhub.get_weekly_financials renders this in chat
            # (apps/web/app/components/display/FinanceWeekly.tsx). It must
            # stay declared: the validator refuses the tool's display without
            # it, and the finance-weekly dashboard template uses it.
            _display(
                "finance_weekly",
                "The week's financials for one or more venues, as the chat shows them.",
            ),
        ],
    },
}

#: The three App rows. A module's ``off`` takes its App out of an
#: organisation's entitlements whatever the catalog says.
CATALOG_SLUGS = frozenset(APPS)
#: Every slug the mode rule governs: the three, and the two they replaced
#: while those rows still exist.
GOVERNED_SLUGS = CATALOG_SLUGS | frozenset(LEGACY_SLUGS)


def off_slugs(modes: dict) -> frozenset[str]:
    """The App slugs the modes switch off: each App while its own mode is off
    (``MODE_OF``), and an old App while every App that replaced it is off —
    Loaded Payroll while payroll AND entertainers are off, Norm Finance while
    finance is (the rule those rows always had)."""
    out = {s for s, module in MODE_OF.items() if modes.get(module, "off") == "off"}
    for old, news in LEGACY_SLUGS.items():
        if all(n in out for n in news):
            out.add(old)
    return frozenset(out)


def entitled_after_modes(entitled: set[str], org_id: str | None, db) -> set[str]:
    """The entitlements hook (``services/entitlements.py``): drop whichever
    of the Finance Apps the organisation's modes have off — the three, and
    the two old rows while they exist. The modes are only read when one of
    them is entitled."""
    if not entitled & GOVERNED_SLUGS:
        return entitled
    return entitled - off_slugs(modes_svc.all_modes(db, org_id))


def _old_apps_on(catalog_rows, organization_id, db) -> set[str]:
    """The old Apps switched on for the organisation: its explicit switch row
    wins, else the row's ``bundled`` default (entitlements.entitled_slugs'
    rule). Only the old rows still in the catalog are looked at."""
    rows = {
        getattr(r, "slug", None): r
        for r in catalog_rows
        if getattr(r, "slug", None) in LEGACY_SLUGS
    }
    if not rows or not organization_id:
        return set()
    from app.db.models import OrgAppEntitlement

    switches = {
        e.app_slug: e.enabled
        for e in db.query(OrgAppEntitlement)
        .filter(
            OrgAppEntitlement.organization_id == organization_id,
            OrgAppEntitlement.app_slug.in_(list(rows)),
        )
        .all()
    }
    return {s for s, r in rows.items() if switches.get(s, bool(r.bundled))}


def _off_and_missing(
    catalog_rows, organization_id, db, old_on: set[str]
) -> tuple[frozenset, set]:
    """(the slugs the organisation's modes switch off, the new slugs that
    stand in: mode on, no catalog row yet, and — while the old App they come
    from still has its row — that old App on for the organisation, in
    ``old_on``). No organisation reads as every mode off."""
    modes = modes_svc.all_modes(db, organization_id) if organization_id else {}
    off = off_slugs(modes)
    present = {getattr(r, "slug", None) for r in catalog_rows}
    missing = set()
    for slug in CATALOG_SLUGS - off:
        if slug in present:
            continue
        old = PREDECESSOR.get(slug)
        if old in present and old not in old_on:
            continue
        missing.add(slug)
    return off, missing


def catalog_view(catalog_rows, organization_id, db) -> tuple[list, set[str]]:
    """The catalog as this organisation's team page sees the Finance Apps
    (the ``_native_view`` hook in ``services/agent_catalog.py``): an App
    whose mode is off is not there at all — not "available to switch on",
    the mode switches it (the Functions rule); one whose mode is on but whose
    row the seed hasn't written yet is stood in by ``stand_in_rows`` (while
    the old App's row exists, only when the old App is on — what
    ``adjust_apps_on`` counts), so the team page, the sidebar and the page
    gate agree with ``apps_on``. The old rows are never listed: the team page
    shows three Finance Apps, never five. Returns the rows and the stand-in
    slugs — rows with nothing behind them to switch."""
    old_on = _old_apps_on(catalog_rows, organization_id, db)
    off, missing = _off_and_missing(catalog_rows, organization_id, db, old_on)
    kept = [
        r
        for r in catalog_rows
        if getattr(r, "slug", None) not in off
        and getattr(r, "slug", None) not in LEGACY_SLUGS
    ]
    return kept + stand_in_rows(missing), missing


def adjust_apps_on(
    on: set[str], organization_id, db, catalog_rows, *, hired=None
) -> set[str]:
    """The ``apps_on`` / ``apps_reachable`` hook (``services/entitlements.py``),
    Functions' ``adjust_apps_on`` mirrored: until the seed writes an App's
    row, the App counts as on exactly when its own mode is on — and only
    while the Finance agent is hired, because the App is the agent's — and,
    while the old App it comes from still has its row, only when that old
    App is in ``on``. Once the row exists this adds nothing: the organisation
    switches the App on from the Team page (all three ship
    ``bundled=False``). Either way a mode that is off wins: whatever its own
    mode has off leaves the result."""
    if hired is not None and MEMBER not in hired:
        return on
    off, missing = _off_and_missing(catalog_rows, organization_id, db, set(on))
    return (on | missing) - off


def stand_in_rows(missing) -> list:
    """Unsaved ``MarketplaceApp`` rows, shaped exactly as the seed's ``_app``
    writes them, for whichever of the three slugs in ``missing`` the config
    DB doesn't hold yet.

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
        # the optional keys, under the seed's own conditions
        for key in ("uses_tools", "works_with"):
            if app.get(key):
                composition[key] = list(app[key])
        if app.get("listing"):
            composition["listing"] = app["listing"]
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
