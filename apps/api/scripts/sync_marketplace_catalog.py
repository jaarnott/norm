"""Seed the marketplace catalog — the reviewed source of `marketplace_apps`.

Hierarchy v2 (plan of 27 Sep 2026):

    Organisation -> AI Team Members -> Apps -> Components -> Connections

Three row kinds:
  * ``tier='agent'`` — the hireable team members. ``owns_agents`` joins the
    row to ``agent_configs``; ``unlocks`` names the Apps the member uses.
    Hiring one is the entitlement that turns everything downstream on.
  * ``tier='app'`` — the Apps, the user-facing unit, named after the product
    they are ("Norm HR", "Loaded", "BambooHR" — never feature words). A
    composition carries either ``components`` (registry components, each with
    its page and, for self-loading ones, an explicit ``connections`` list) or
    ``app_slug`` (an App-platform pointer — Norm-native storage apps). An App
    inherits the combined Connection requirements of its components; a
    Norm-native App legitimately requires NOTHING. ``tool_actions`` claims
    chat capabilities so switching an App off also removes them
    (kept-while-any-entitled-claimer semantics).
  * ``tier='user'`` — published community apps; never touched here.

Every App has an enabled state: hiring a member enables its bundled Apps by
default, an org can switch one off, and a priced App ships bundled=false
until enabled (same OrgAppEntitlement row = access and the charge).

Day-one neutrality: every member row is bundled, so seeding this catalog
changes NOTHING for any org — the tool union before and after must be
byte-identical (verify with the snapshot diff in the plan). Prices: HR $10,
Procurement $5, the rest $0 until the owner reprices (data-only).

This script also performs the one-time rollout moves, all idempotent:
  * upsert the ``base`` agent_configs row (the always-included assistant's
    home for shared bindings);
  * re-home the empty-capabilities (wildcard) ``executive_chef -> norm``
    binding to ``base -> norm`` — a wildcard on a hireable member would let
    hire/retire toggle a whole connector surface for everyone;
  * DELETE the retired rows: the 8 integration rows (connections are plumbing
    now, not products) and ``reports-agent`` (Reports is always included).

Idempotent; the config DB is shared across every environment, so committing
reaches production. Dry-run first.

Norm Functions' two rows are declared in app/services/functions/catalog.py
(the team payload stands them in until they exist here). Their member needs
its agent_configs row first: run scripts/sync_norm_functions.py before this,
or the validation below refuses with "unknown agent 'functions'".

The two Finance Apps (Loaded Payroll, Norm Finance — Oct 2026) are declared
in app/services/finance_core/catalog.py the same way, under the existing
Finance member, bundled=False. Their tools and skills must exist first
(scripts/sync_norm_finance.py, sync_payroll_config.py, sync_xero_connector.py
— docs/norm-finance.md has the order), or the validation refuses with "tool
… does not exist" / "skill … does not exist". Land them with
``--only loaded-payroll,norm-finance,xero`` (comma-separated, no spaces) so
nothing else pending in this seed applies with them.

Usage:
    .venv/bin/python scripts/sync_marketplace_catalog.py --dry-run
    .venv/bin/python scripts/sync_marketplace_catalog.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.services.finance_core.catalog import (  # noqa: E402 — path set above
    APPS as FINANCE_APPS,
    FINANCE_APP_SLUG,
    PAYROLL_APP_SLUG,
)
from app.services.functions.catalog import (  # noqa: E402 — path set above
    APP as FUNCTIONS_APP,
    MEMBER as FUNCTIONS_MEMBER,
)
from app.services.screens import SYSTEMS  # noqa: E402


def _c(
    key,
    page=None,
    full_width=False,
    description="",
    connections=None,
    connection=None,
):
    entry = {
        "key": key,
        "page": page,
        "full_width": full_width,
        "description": description,
    }
    if connections:
        entry["connections"] = connections
    if connection:
        # the system this screen is for (services/screens.py): it shows only
        # while the organisation has that system
        entry["connection"] = connection
    return entry


def _p(id, label, icon):
    return {"id": id, "label": label, "icon": icon}


# Rows that stop existing. Integration rows + reports-agent went in v2; the
# v3 split retires the per-member Loaded rows it replaces.
RETIRED_SLUGS = [
    "loaded",
    "cook-brothers-app",
    "bamboohr",
    "gmail",
    "deputy",
    "bidfood",
    "brevo",
    "metricool",
    "reports-agent",
    "loaded-procurement",
    "loaded-rostering",
    "norm-procurement",
    # an example App-platform app that was never installed in any org, so
    # its row fronted nothing; the fixture was deleted with it (28 Sep 2026)
    "weekly-venue-performance",
    # Apps named after the system, replaced by the job apps (owner, 23 Oct
    # 2026 — docs/plans/agents-and-apps-restructure.md). Bidfood, Brevo and
    # Metricool had no tools: Brevo and Metricool are "coming soon"
    # connections now, and Bidfood is gone (scripts/switch_to_job_apps.py).
    "loaded-stock",
    "loaded-kitchen",
    "loaded-time",
    "loaded-reports",
    "xero",
    "bamboohr-app",
    "bidfood-app",
    "brevo-app",
    "metricool-app",
]

# Connectors with no spec whose bindings are pure dead weight.
RETIRED_BINDING_CONNECTORS = ["microsoft_outlook"]

# Apps v3 tool audit (28 Sep 2026): tools that stay callable by
# consolidators, pages and cards but leave the agent's menu — backends of a
# consolidator, duplicates, and raw endpoints nobody uses. engine_only never
# blocks execution; it only hides the tool from agents.
ENGINE_ONLY = {
    "norm": [
        "review_invoices",
        "invoice_copy_evidence",
        "record_split_order",
        "show_orders",
    ],
    "brevo": "*",
    "metricool": "*",
}


# Every hired AI team member is $10/month on one shared Stripe price
# (STRIPE_PRICE_AGENT, quantity = members hired) — Oct 2026 pricing; keep in
# step with billing_service.AGENT_PRICE_CENTS.
AGENT_PRICE_CENTS = 1000
AGENT_PRICE_KEY = "agent"


def _member(
    slug,
    *,
    owns,
    name,
    description,
    icon,
    price=AGENT_PRICE_CENTS,
    key=AGENT_PRICE_KEY,
    tagline="",
    bundled=None,
):
    row = {
        "slug": slug,
        "name": name,
        "description": description,
        "icon": icon,
        "tier": "agent",
        "price_cents": price,
        "stripe_price_key": key,
        "composition": {"owns_agents": [owns], "tagline": tagline},
    }
    if bundled is not None:
        # bundled=False: hired per organisation from the Team page (and billed
        # as an agent) instead of on for everyone the moment it is seeded.
        row["bundled"] = bundled
    return row


#: Who makes Norm's own apps, on the marketplace (marketplace plan, 20 Oct)
MAKER = "Hospo Apps"


def _listing(blurb, about, asks):
    """What the App Marketplace says about an App (the public page reads it,
    GET /api/public/marketplace)."""
    return {"blurb": blurb, "about": about, "asks": list(asks), "maker": MAKER}


def _app(
    slug,
    *,
    name,
    member,
    description,
    icon,
    tools=(),
    uses_tools=(),
    components=(),
    skills=(),
    switchable=True,
    bundled=True,
    works_with=None,
    listing=None,
    extra=None,
):
    """One App. ``tools`` it owns — each exactly once across the catalog;
    ``uses_tools`` it shares from the App that owns them (a hired agent gets
    both, entitlements.app_tools). Its screens are its components with a
    page; each names the system it's for (``connection``)."""
    comp = {
        "member": member,
        "tools": list(tools),
        "components": list(components),
        "skills": list(skills),
    }
    if uses_tools:
        comp["uses_tools"] = list(uses_tools)
    if not switchable:
        comp["switchable"] = False
    if works_with:
        comp["works_with"] = list(works_with)
    if listing:
        comp["listing"] = listing
    comp.update(extra or {})
    return {
        "slug": slug,
        "name": name,
        "description": description,
        "icon": icon,
        "tier": "app",
        "bundled": bundled,
        "composition": comp,
    }


def _shared(key, label, description):
    return {
        "key": key,
        "page": None,
        "full_width": False,
        "description": description,
        "shared": True,
        "shared_label": label,
    }


APPS = [
    # ── AI Team Members (tier='agent') ───────────────────────────────────
    _member(
        "procurement-agent",
        owns="procurement",
        name="Procurement",
        icon="🛒",
        description="Handles purchasing, suppliers and ordering.",
        tagline="Purchase orders, invoice receiving and reconciliation, stock and tenders.",
    ),
    _member(
        "hr-agent",
        owns="hr",
        name="HR",
        icon="🧑‍💼",
        description="Runs hiring, training and employee records.",
        tagline="Job posts, candidates, onboarding and employee records.",
    ),
    _member(
        "executive-chef-agent",
        owns="executive_chef",
        name="Executive Chef",
        icon="👨‍🍳",
        description="Keeps recipes, menus and menu engineering profitable and up to date.",
        tagline="Recipes, menus, costs and menu engineering.",
    ),
    _member(
        "time-attendance-agent",
        owns="time_attendance",
        name="Time & Attendance",
        icon="⏱️",
        description="Builds and publishes rosters, watches labour against sales.",
        tagline="Rosters, timeclock and labour cost.",
    ),
    _member(
        "marketing-agent",
        owns="marketing",
        name="Marketing",
        icon="📣",
        description="Writes and schedules campaigns and social posts, reports on reach.",
        tagline="Campaigns, social posts and reach.",
    ),
    # Shown only to organisations whose functions_mode is on (shadow or live).
    _member(**FUNCTIONS_MEMBER),
    # Finance (Oct 2026): hired per organisation, not bundled — the Xero App
    # is its only App, and a Xero connection is a per-organisation decision.
    _member(
        "finance-agent",
        owns="finance",
        name="Finance",
        icon="🧾",
        description=(
            "Watches the books when you ask: bills, suppliers, chart of accounts "
            "and profit and loss from Xero."
        ),
        tagline="Bills, suppliers, accounts and P&L from Xero.",
        bundled=False,
    ),
    # ── Apps (tier='app'): one job each, one AI agent each (Norm Core: all) ─
    # The job apps (owner, 23 Oct 2026 — docs/plans/agents-and-apps-
    # restructure.md §3): every App has at least one screen; a tool is owned
    # by one App and shared by others through uses_tools.
    _app(
        "norm-core",
        name="Norm Core",
        member="*",
        switchable=False,
        icon="🧭",
        description="Norm's own foundations: memory, search, tasks, charts, dashboards, email and connection setup.",
        tools=[
            "norm.search_tool_result",
            "norm.remember",
            "norm.recall_memory",
            "norm.manage_task",
            "norm.show_connect",
            # opens any App-platform app in chat at the place its inputs name
            # (Hiring at a job, Training at a person) — scripts/sync_app_components.py
            "norm.open_app",
            "norm_email.send_report_email",
            "norm_reports.render_chart",
            "gmail.send_email",
            # was set_workflow_mode under loaded-stock (scripts/sync_approval_preferences.py)
            "norm.set_approval_preference",
            # what a person says about an insight they clicked on Home
            # (scripts/sync_note_insight_tool.py)
            "norm.note_insight",
            # reads Norm's own apps' records for dashboards
            # (scripts/sync_member_dashboards_config.py)
            "norm.app_records",
            # which dashboard each Dashboard page shows: use, copy, share…
            # (scripts/sync_dashboard_tools.py); building one is save_app
            "norm_reports.get_dashboards",
            "norm_reports.manage_dashboard",
        ],
        components=[
            _shared(
                "dashboard_view", "Dashboard", "Each AI agent's dashboard page."
            ),
            _shared(
                "automated_task_board",
                "Tasks",
                "Each AI agent's scheduled tasks page.",
            ),
            _c("generic_table", description="Any tabular answer in chat."),
            _c("chart", description="Charts drawn from data in chat."),
            _c(
                "tool_approval",
                description="Approve-or-reject card for actions that change data.",
            ),
            _c(
                "venue_picker",
                description="Pick a venue when a conversation needs one.",
            ),
            _c(
                "connector_connect",
                description="Connect or reconnect a system from chat.",
            ),
            _c(
                "background_job",
                description="Live progress of a long job Norm runs in the background.",
            ),
            _c(
                "automated_task_preview",
                description="Preview of a scheduled task in chat.",
            ),
            _c("mcp_embed", description="Embeds an external app's screen."),
        ],
    ),
    _app(
        "saved-reports",
        name="Saved Reports",
        member="reports",
        switchable=False,
        icon="🗂️",
        description="Build report layouts and keep them — runs on Norm.",
        components=[
            _c("report_builder", description="Drag-and-drop report layout builder."),
            _c(
                "saved_reports_board",
                page=_p("saved-reports", "Saved Reports", "BarChart3"),
                full_width=True,
                description="Your saved report layouts.",
            ),
        ],
    ),
    # ── Procurement ──────────────────────────────────────────────────────
    _app(
        "ordering",
        name="Ordering",
        member="procurement",
        icon="🛒",
        description="Purchase orders drafted from what you sell and hold, and the stock catalogue.",
        tools=[
            "loadedhub.get_purchase_orders",
            "norm.create_purchase_order",
            "loadedhub.calculate_template_stock_requirements",
        ],
        uses_tools=[
            "loadedhub.get_stock",
            "loadedhub.manage_stock_item",
            "loadedhub.manage_supplier",
        ],
        components=[
            _c(
                "orders_dashboard",
                page=_p("orders", "Orders", "ShoppingCart"),
                full_width=True,
                description="Purchase orders: outstanding, recent, and detail.",
                connection="loaded",
            ),
            _c(
                "stock_catalogue",
                page=_p("stock-catalogue", "Catalogue", "Package"),
                full_width=True,
                description="The venue's stock items, units and suppliers: edit, "
                "retire, restore and merge them.",
                # Unit changes and item merges go through Loaded's older web API
                # (docs/plans/stock-catalogue-page.md).
                connections=["loadedhub", "loaded_legacy"],
                connection="loaded",
            ),
            _c(
                "purchase_order_editor",
                description="Editable purchase-order draft; Place Order submits to Loaded.",
            ),
            _c("stock_picker", description="Stock item picker used by order flows."),
        ],
        skills=["create_stock_order", "stock_requirements"],
        works_with=["loaded"],
        listing=_listing(
            "Orders drafted from what you sell and hold, for you to approve.",
            "Procurement looks at what you sell and what you hold, works out what you need, and drafts the purchase order. You check it, change it if you like, and approve it.",
            [
                "What do we need to order for the weekend?",
                "Order 20 cartons of Peroni from Coastal Drinks for Friday.",
                "Show me the orders still waiting on a delivery.",
            ],
        ),
    ),
    _app(
        "invoice-receiving",
        name="Invoice Receiving",
        member="procurement",
        icon="🧾",
        description="Every supplier invoice read, checked against its order and received.",
        tools=[
            "loadedhub.get_invoices",
            "loadedhub.review_and_receive_invoices",
            "loadedhub.reconcile_received_invoices",
            # an organisation's own receiving rules (sync_receiving_rules_tools.py)
            "norm.get_receiving_rules",
            "norm.set_receiving_rule",
            # stock items and supplier aliases a received invoice fixes
            "loadedhub.manage_stock_item",
            "loadedhub.manage_supplier",
        ],
        uses_tools=["loadedhub.get_stock", "loadedhub.get_received_items_for_period"],
        components=[
            _c(
                "invoices_dashboard",
                page=_p("invoices", "Invoices", "Receipt"),
                full_width=True,
                description="Outstanding supplier invoices with review state.",
                connections=["loadedhub"],
                connection="loaded",
            ),
            _c(
                "receive_invoice_editor",
                description="Receive-invoice card: units, costs, PO link, Accept & Receive.",
                connections=["loadedhub"],
            ),
        ],
        skills=[
            "receive_loadedhub_invoice",
            "receive_loadedhub_invoices",
            "reconcile_received_invoices",
        ],
        works_with=["loaded"],
        listing=_listing(
            "Every supplier invoice read, checked against its order and received.",
            "Procurement reads each supplier invoice line by line, checks it against the order and receives it. You only look at the lines that don’t add up, and at month end it reconciles the supplier’s statement.",
            [
                "Receive this morning’s invoices and show me anything that doesn’t match.",
                "Why is the Coastal Drinks statement $84 out?",
                "Which suppliers put their prices up this month?",
            ],
        ),
    ),
    _app(
        "stocktakes",
        name="Stocktakes",
        member="procurement",
        icon="📋",
        description="Stock on hand, stocktakes and their variances, and cost of goods.",
        tools=[
            "loadedhub.get_stock",
            # get_stocktakes replaced generate_stocktake_report + Orbit's
            # stock_find_stocktakes (Sep 2026, sync_last_raw_tools.py).
            "loadedhub.get_stocktakes",
            "loadedhub.get_received_items_for_period",
            # Purchases ÷ POS sales per month / week, food vs beverage — the
            # cost-of-goods figure for a venue without stocktakes (Oct 2026).
            "loadedhub.get_purchase_cogs",
        ],
        components=[
            _c(
                "stocktakes_board",
                page=_p("stocktakes", "Stocktakes", "ClipboardCheck"),
                full_width=True,
                description="Recent stocktakes, their variances, and cost of goods.",
                connection="loaded",
            ),
        ],
        skills=["stocktake_variance", "cogs_analysis"],
        works_with=["loaded"],
        listing=_listing(
            "Stock on hand, counts by area, and the variance explained.",
            "See what you hold and what it’s worth, and find out where a stocktake went wrong, by area and by item.",
            [
                "What was the variance on last week’s bar stocktake?",
                "What’s our stock on hand worth today?",
                "Which items went missing in the kitchen count?",
            ],
        ),
    ),
    _app(
        "supplier-tenders",
        name="Supplier Tenders",
        member="procurement",
        icon="⚖️",
        description="Agreed supplier price lists, with tendered-vs-paid review.",
        uses_tools=["loadedhub.manage_supplier"],
        components=[
            _c(
                "supplier_tenders",
                page=_p("supplier-tenders", "Supplier Tenders", "Gavel"),
                full_width=True,
                description="Agreed supplier price lists, with tendered-vs-paid review.",
                # Loaded's tenders API refuses Norm's OAuth token (TEMPORARY:
                # app/connectors/loaded_legacy.py).
                connections=["loaded_legacy"],
                connection="loaded",
            ),
        ],
        works_with=["loaded"],
        listing=_listing(
            "Compare suppliers’ prices for the same items before you switch.",
            "Line up what each supplier charges for the same products, so you can see what you’d save before you move an order.",
            [
                "Which supplier is cheapest for limes?",
                "Compare our dry goods prices across suppliers.",
                "What would we save moving dairy to another supplier?",
            ],
        ),
    ),
    # ── Executive Chef ───────────────────────────────────────────────────
    _app(
        "recipes-and-costing",
        name="Recipes & Costing",
        member="executive_chef",
        icon="📗",
        description="Recipes written up and costed at today's prices.",
        tools=[
            "loadedhub.get_recipes",
            "loadedhub.draft_recipe",
            "loadedhub.edit_recipe",
            # the recipe write (config/consolidators/record_recipe.py) — on
            # Loaded Legacy since 19 Oct 2026
            "loaded_legacy.record_recipe",
            "loadedhub.get_menu_item_costs",
        ],
        uses_tools=[
            "loadedhub.get_stock",
            "loadedhub.manage_stock_item",
            "loadedhub.get_menus",
        ],
        components=[
            _c(
                "recipe_editor",
                page=_p("recipes", "Recipes", "BookOpen"),
                full_width=False,  # in chat: one inline card per recipe (Oct 2026)
                description="Recipe editor with live Loaded costs; Save writes to Loaded.",
                # Saves go through Loaded Legacy (TEMPORARY:
                # app/connectors/loaded_legacy.py).
                connections=["loaded_legacy"],
                connection="loaded",
            ),
        ],
        skills=["create_recipes"],
        works_with=["loaded"],
        listing=_listing(
            "Write recipes and see what every plate costs.",
            "Describe a dish or upload a recipe and Norm writes it up, costed at today’s prices. When a supplier’s price moves, the plate cost moves with it.",
            [
                "What does our margherita cost to make?",
                "Write up a recipe for the new brunch special.",
                "Which dishes got dearer this month?",
            ],
        ),
    ),
    _app(
        "menu-manager",
        name="Menu Manager",
        member="executive_chef",
        icon="📖",
        description="Every menu, item and price across the venues.",
        tools=["loadedhub.get_menus", "loadedhub.manage_menu"],
        components=[
            _c(
                "menu_editor",
                page=_p("menus", "Menus", "LayoutGrid"),
                full_width=True,
                description="Menus with sections, dishes and sell prices; saves to Loaded.",
                connection="loaded",
            ),
        ],
        works_with=["loaded"],
        listing=_listing(
            "Every menu, item and price, across your venues.",
            "See every menu across your venues, find the items that are missing a recipe, and keep prices in step.",
            [
                "List the items on the lunch menu.",
                "Which menu items have no recipe?",
                "Show me the dinner menu at Harbourside Kitchen.",
            ],
        ),
    ),
    _app(
        "menu-engineering",
        name="Menu Engineering",
        member="executive_chef",
        icon="🧮",
        description="Which dishes to push, rework or drop.",
        uses_tools=["loadedhub.get_menu_item_costs", "loadedhub.get_sales"],
        components=[
            _c(
                "menu_engineering",
                page=_p("menu-engineering", "Menu Engineering", "Grid2x2"),
                full_width=True,
                description="Popularity × profitability quadrants from the COGS report.",
                connection="loaded",
            ),
        ],
        works_with=["loaded"],
        listing=_listing(
            "Which dishes to push, rework or drop.",
            "Norm puts each dish’s sales beside its margin, so you can see your stars, the dishes that need a rethink and the ones to take off.",
            [
                "Which dishes should we push, rework or drop?",
                "What’s the margin on each main?",
                "How did the new menu do in its first month?",
            ],
        ),
    ),
    # ── Time & Attendance ────────────────────────────────────────────────
    _app(
        "rosters",
        name="Rosters",
        member="time_attendance",
        icon="🗓️",
        description="The week's roster, built, costed and published.",
        tools=["norm.show_roster"],
        # labour against sales while the roster is built
        uses_tools=["loadedhub.get_labour", "loadedhub.get_sales"],
        components=[
            _c(
                "roster_editor",
                page=_p("roster", "Roster", "Calendar"),
                full_width=True,
                description="Week/day roster grid with drag editing and Loaded publish.",
                connections=["loadedhub"],
                connection="loaded",
            ),
            _c("roster_table", description="Compact roster table for chat answers."),
        ],
        skills=["roster_viewer"],
        works_with=["loaded"],
        listing=_listing(
            "Build, cost and publish the week’s roster.",
            "Drag shifts around or ask Norm to make the change, watch labour against sales as you go, and publish when you’re happy.",
            [
                "Show me next week’s roster.",
                "Move Mia to the Friday close.",
                "How does labour compare to sales this week?",
            ],
        ),
    ),
    _app(
        "attendance",
        name="Attendance",
        member="time_attendance",
        icon="⏱️",
        description="Who turned up against the roster, and what it cost.",
        tools=["loadedhub.get_labour"],
        uses_tools=["loadedhub.get_daily_performance"],
        components=[
            _c(
                "attendance_board",
                page=_p("attendance", "Attendance", "UserCheck"),
                full_width=True,
                description="Rostered against clocked hours, flags and labour cost.",
                connection="loaded",
            ),
        ],
        works_with=["loaded"],
        listing=_listing(
            "Who turned up against the roster, and what it cost.",
            "Norm checks clock-ins against the roster, so you can see who was late, who didn’t show and what the week really cost.",
            [
                "Who was late this week?",
                "Did anyone not turn up for a rostered shift?",
                "What did labour cost yesterday against the roster?",
            ],
        ),
    ),
    # ── Finance ──────────────────────────────────────────────────────────
    _app(
        "bills",
        name="Bills",
        member="finance",
        icon="📒",
        description="Supplier bills in Xero, created from checked invoices.",
        tools=[
            "xero.get_bills",
            "xero.get_contacts",
            "xero.create_bill",
            # Loaded Payroll's Xero writes (Oct 2026, docs/plans/finance-
            # migration.md §4, D5): the payroll journal, the intercompany
            # invoices and bills, and the void — each `requires:
            # ["payroll:write"]` on its row (scripts/sync_xero_connector.py),
            # invoked by the Payroll pages through their approval cards.
            "xero.post_wage_journal",
            "xero.post_intercompany_documents",
            "xero.void_payroll_document",
        ],
        uses_tools=["xero.get_accounts"],
        components=[
            _c(
                "xero_bills",
                page=_p("xero-bills", "Bills", "Receipt"),
                full_width=True,
                description=(
                    "Bills in Xero for a venue: outstanding, overdue, by supplier, "
                    "with links into Xero."
                ),
                connections=["xero"],
                connection="xero",
            ),
        ],
        works_with=["xero"],
        listing=_listing(
            "Checked supplier invoices become bills, coded to your accounts.",
            "Once an invoice has been checked, Norm creates the bill in Xero, coded to your accounts, so nobody types it in twice.",
            [
                "Turn yesterday’s invoices into bills in Xero.",
                "What bills are unpaid?",
                "Which bills are due this week?",
            ],
        ),
    ),
    # The two Finance Apps (Oct 2026): native modules under the Finance member
    # — Loaded Payroll (the pay week, leave, intercompany, the reconciliation
    # and the entertainers) and Norm Finance (the weekly summary, budgets and
    # the forecast). Declared once in app/services/finance_core/catalog.py
    # (tests pin pages, tools and skills); bundled=False so hiring Finance
    # does not switch them on, and each organisation's module modes gate them.
    _app(**FINANCE_APPS[PAYROLL_APP_SLUG]),
    _app(**FINANCE_APPS[FINANCE_APP_SLUG]),
    _app(
        "profit-and-loss",
        name="Profit & Loss",
        member="finance",
        icon="📈",
        description="Profit and loss by venue and month from Xero.",
        tools=["xero.get_profit_and_loss", "xero.get_accounts"],
        components=[
            _c(
                "profit_and_loss",
                page=_p("profit-and-loss", "Profit & Loss", "TrendingUp"),
                full_width=True,
                description="A venue's month in Xero: income, costs and profit, compared.",
                connections=["xero"],
                connection="xero",
            ),
        ],
        works_with=["xero"],
        listing=_listing(
            "Profit and loss by venue and month, explained in plain words.",
            "Ask how a venue or a month went and Norm reads your profit and loss and explains what moved.",
            [
                "How was last month’s profit?",
                "Why was September down on August?",
                "Which venue made the most last quarter?",
            ],
        ),
    ),
    # ── HR ───────────────────────────────────────────────────────────────
    _app(
        "hiring",
        name="Hiring",
        member="hr",
        icon="🧑‍💻",
        description="Roles, candidate pipeline, candidates and talent pool — with Hospo HR or BambooHR.",
        uses_tools=["bamboohr.get_hr"],
        components=[
            _c(
                "hiring_board",
                page=_p("hiring", "Hiring (BambooHR)", "Users"),
                full_width=True,
                description="Hiring pipeline over BambooHR jobs and applications.",
                connections=["bamboohr"],
                connection="bamboohr",
            ),
            _c(
                "criteria_editor",
                description="Screening criteria for job applications.",
                connection="bamboohr",
            ),
        ],
        skills=["candidate_review"],
        works_with=["hospo-hr", "bamboohr"],
        listing=_listing(
            "Job posts, a candidate pipeline and a talent pool.",
            "Post roles, move candidates from applied to hired, keep a pool of the people you’d hire again, and get new starters onboarded.",
            [
                "Open the bar manager role.",
                "Show me this week’s applicants.",
                "Who’s in our talent pool for the kitchen?",
            ],
        ),
        # Norm Hiring's own pages (services/builtin_apps.py), on Hospo HR
        extra={"app_slug": "hiring", "app_connection": "hospo-hr"},
    ),
    _app(
        "training",
        name="Training",
        member="hr",
        icon="🎓",
        description="Training programs, plans, tracker and sign-offs — on Hospo HR.",
        works_with=["hospo-hr"],
        listing=_listing(
            "Programs for each role, a plan for each starter, sign-offs on shift.",
            "Set up training programs for each role, give every new starter a plan, and have their trainer sign them off on shift.",
            [
                "Open Mia’s training plan.",
                "Who hasn’t finished their RSA training?",
                "Set up a program for new bartenders.",
            ],
        ),
        extra={"app_slug": "training", "app_connection": "hospo-hr"},
    ),
    _app(
        "performance",
        name="Reviews",
        member="hr",
        icon="📈",
        description="Performance reviews, review cycles and capability frameworks — on Hospo HR.",
        works_with=["hospo-hr"],
        listing=_listing(
            "Review cycles that run on time.",
            "Run reviews against a capability framework for each role, with reminders when someone is due.",
            [
                "Which reviews are due this month?",
                "Open Tom’s last review.",
                "Who hasn’t had a review this year?",
            ],
        ),
        extra={"app_slug": "performance", "app_connection": "hospo-hr"},
    ),
    _app(
        "employee-records",
        name="Employee Records",
        member="hr",
        icon="🪪",
        description="The BambooHR directory: who works where, since when.",
        tools=[
            # get_hr replaced six raw reads (Sep 2026, sync_last_raw_tools.py)
            # and took in the CV reader as `cv` (2 Oct 2026, sync_hr_fixes.py).
            "bamboohr.get_hr",
        ],
        components=[
            _c(
                "employee_records",
                page=_p("employee-records", "Employees", "IdCard"),
                full_width=True,
                description="The BambooHR directory, searchable, with each person's details.",
                connections=["bamboohr"],
                connection="bamboohr",
            ),
        ],
        works_with=["bamboohr"],
        listing=_listing(
            "Your people’s records, kept in step with your HR system.",
            "Look people up, see who started when, and keep an eye on open roles in the HR system you already use.",
            [
                "Find Tom’s employee record.",
                "Who started this month?",
                "Show me the open jobs in BambooHR.",
            ],
        ),
    ),
    # ── Functions: a native Norm module (services/functions/catalog.py) ──
    _app(**FUNCTIONS_APP),
    # ── Reports ──────────────────────────────────────────────────────────
    _app(
        "sales-analysis",
        name="Sales Analysis",
        member="reports",
        icon="📊",
        description="Product, staff and period sales from Loaded, and revenue groups.",
        tools=[
            "loadedhub.get_sales",
            "loadedhub.get_budgets",
            "loadedhub.get_daily_performance",
            "loadedhub.get_cogs_detail_for_period",
            # POS items into Food / Beverage / Other — what makes food vs
            # beverage sales and cost of goods right (sync_revenue_groups_config.py)
            "loadedhub.set_revenue_group",
        ],
        components=[
            _c(
                "sales_analysis",
                page=_p("sales-analysis", "Sales Analysis", "ChartColumnIncreasing"),
                full_width=True,
                description="Top and bottom sellers, staff sales and period comparisons.",
                connection="loaded",
            ),
        ],
        skills=["product_sales_analysis", "sales_comparison", "staff_sales_performance"],
        works_with=["loaded"],
        listing=_listing(
            "Your best and worst sellers, staff sales and how this week compares.",
            "See what sold and what didn’t, how each person on the floor sold, and how any week compares with the one before, last year or budget.",
            [
                "What were our top sellers last week?",
                "How did sales compare with the same week last year?",
                "Who sold the most on Saturday?",
            ],
        ),
    ),
    _app(
        "weekly-report",
        name="Weekly Report",
        member="reports",
        icon="📬",
        description="Last week's numbers in the team's inbox on Monday morning.",
        uses_tools=[
            "loadedhub.get_daily_performance",
            "loadedhub.get_sales",
            "loadedhub.get_labour",
            "loadedhub.get_budgets",
            "loadedhub.get_cogs_detail_for_period",
        ],
        components=[
            _c(
                "weekly_report",
                page=_p("weekly-report", "Weekly Report", "CalendarClock"),
                full_width=True,
                description="Last week's report as emailed, who gets it and when.",
                connection="loaded",
            ),
        ],
        skills=["weekly_sales_report"],
        works_with=["loaded"],
        listing=_listing(
            "Last week’s numbers in the team’s inbox on Monday morning.",
            "Pick the numbers that matter and Norm emails them to the team every Monday, so nobody has to pull them together.",
            [
                "Email me last week’s numbers every Monday.",
                "Send the managers a weekly sales summary.",
                "Add labour to the Monday report.",
            ],
        ),
    ),
    # ── Norm itself: App Builder ─────────────────────────────────────────
    _app(
        "app-builder",
        name="App Builder",
        member="app_builder",
        switchable=False,
        icon="🧩",
        description="Build custom Apps and dashboards for your team by chatting.",
        tools=["norm.list_app_capabilities", "norm.save_app", "norm.get_app"],
        # build_a_dashboard: scripts/sync_dashboard_tools.py
        skills=["build_an_app", "build_a_dashboard"],
        components=[
            _c(
                "apps_dashboard",
                page=_p("apps-hub", "Apps", "Blocks"),
                full_width=True,
                description="Your team's Apps.",
            ),
            _c("app_runner", description="Runs an App's own screen."),
        ],
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--only",
        help=(
            "comma-separated catalog slugs: validate and upsert ONLY these rows "
            "and skip everything else this seed does (the rollout moves, the "
            "engine-only audit, binding cleanup, retirements). For landing one "
            "App while another's rows are still invalid — e.g. a member whose "
            "config hasn't been synced yet — without touching anything pending."
        ),
    )
    args = parser.parse_args()
    selected = (
        {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None
    )
    apps = [a for a in APPS if selected is None or a["slug"] in selected]
    if selected is not None:
        unknown = selected - {a["slug"] for a in apps}
        if unknown:
            sys.exit(f"unknown catalog slug(s): {', '.join(sorted(unknown))}")
        print(f"--only {', '.join(sorted(selected))}: other rows and moves untouched")
    engine_only = ENGINE_ONLY if selected is None else {}
    retired_slugs = RETIRED_SLUGS if selected is None else []
    retired_binding_connectors = RETIRED_BINDING_CONNECTORS if selected is None else []

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows

    from app.db.config_models import (
        AgentConfig,
        AgentConnectionBinding,
        ConfigBase,
        ConnectionSpec,
        MarketplaceApp,
    )
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    ConfigBase.metadata.create_all(db.get_bind())
    try:
        # ── validation inputs ────────────────────────────────────────────
        from app.db.config_models import Playbook

        all_specs = db.query(ConnectionSpec).all()
        spec_names = {s.connector_name for s in all_specs}
        spec_actions = {
            s.connector_name: {
                t.get("action") for t in spec_rows.rows(s) if isinstance(t, dict)
            }
            for s in all_specs
        }
        # Only TOOLS may be claimed (Sep 2026): an App claim puts a row in
        # front of an LLM, and endpoints never reach one.
        spec_tools = {
            s.connector_name: {
                t.get("action") for t in spec_rows.tools(s) if isinstance(t, dict)
            }
            for s in all_specs
        }
        agent_slugs = {a.agent_slug for a in db.query(AgentConfig).all()}
        playbook_slugs = {pb.slug for pb in db.query(Playbook).all()}
        tool_claimed: dict[str, str] = {}
        skill_claimed: dict[str, str] = {}

        # ── invariants ───────────────────────────────────────────────────
        errors: list[str] = []
        claimed: dict[str, str] = {}
        for app in apps:
            comp = app["composition"]
            declared = (comp.get("connections") or []) + (
                comp.get("component_connections") or []
            )
            for conn in declared:
                if conn not in spec_names:
                    errors.append(f"{app['slug']}: connection '{conn}' does not exist")
            for c in comp.get("components", []):
                if c["key"] in claimed:
                    errors.append(
                        f"component '{c['key']}' claimed by both "
                        f"'{claimed[c['key']]}' and '{app['slug']}'"
                    )
                claimed[c["key"]] = app["slug"]
                for conn in c.get("connections") or []:
                    if conn not in spec_names:
                        errors.append(
                            f"{app['slug']}/{c['key']}: connection '{conn}' missing"
                        )
            for a in comp.get("owns_agents") or []:
                if a not in agent_slugs and a != "base":
                    errors.append(f"{app['slug']}: unknown agent '{a}'")
            if app["tier"] == "app":
                m = comp.get("member")
                if m == "*" and app["slug"] != "norm-core":
                    errors.append(
                        f"{app['slug']}: only norm-core may be bound to every member"
                    )
                elif m != "*" and m not in agent_slugs | {"reports", "app_builder"}:
                    errors.append(f"{app['slug']}: unknown member '{m}'")
            for key in comp.get("tools") or []:
                conn, _, action = key.partition(".")
                if action not in spec_actions.get(conn, set()):
                    errors.append(f"{app['slug']}: tool '{key}' does not exist")
                elif action not in spec_tools.get(conn, set()):
                    errors.append(
                        f"{app['slug']}: '{key}' is an API endpoint, not a tool — "
                        "claim a consolidator that wraps it"
                    )
                if key in tool_claimed:
                    errors.append(
                        f"tool '{key}' claimed by both '{tool_claimed[key]}' and '{app['slug']}'"
                    )
                tool_claimed[key] = app["slug"]
            for c in comp.get("components", []):
                sys_ = c.get("connection")
                if sys_ and sys_ not in SYSTEMS:
                    errors.append(
                        f"{app['slug']}/{c['key']}: unknown system '{sys_}'"
                    )
            for sk in comp.get("skills") or []:
                if sk not in playbook_slugs:
                    errors.append(f"{app['slug']}: skill '{sk}' does not exist")
                if sk in skill_claimed:
                    errors.append(
                        f"skill '{sk}' claimed by both '{skill_claimed[sk]}' and '{app['slug']}'"
                    )
                skill_claimed[sk] = app["slug"]
        # A shared tool is owned by another App (uses_tools never claims).
        for app in apps:
            for key in app["composition"].get("uses_tools") or []:
                if key not in tool_claimed and selected is None:
                    errors.append(f"{app['slug']}: uses '{key}', which no App owns")
        if errors:
            for e in errors:
                print(f"INVALID: {e}")
            sys.exit(1)

        changes: list[str] = []

        # ── rollout move 1: the base assistant's agent_configs row ───────
        if selected is None and "base" not in agent_slugs:
            changes.append("create agent_configs row 'base' (Norm Assistant)")
            if not args.dry_run:
                db.add(
                    AgentConfig(
                        agent_slug="base",
                        display_name="Norm Assistant",
                        description=(
                            "The always-included assistant — home for the "
                            "shared internal tool bindings."
                        ),
                    )
                )

        # ── rollout move 2: the wildcard binding moves to base ───────────
        wild = (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.agent_slug == "executive_chef",
                AgentConnectionBinding.connector_name == "norm",
            )
            .first()
        )
        if selected is None and wild is not None and not (wild.capabilities or []):
            already = (
                db.query(AgentConnectionBinding)
                .filter(
                    AgentConnectionBinding.agent_slug == "base",
                    AgentConnectionBinding.connector_name == "norm",
                )
                .first()
            )
            changes.append(
                "re-home wildcard binding executive_chef->norm to base->norm"
            )
            if not args.dry_run:
                if already is None:
                    wild.agent_slug = "base"
                else:
                    db.delete(wild)

        # ── upsert the target catalog ────────────────────────────────────
        for app in apps:
            desired = {
                "name": app["name"],
                "description": app["description"],
                "icon": app.get("icon"),
                "tier": app["tier"],
                "bundled": app.get("bundled", True),
                "price_cents": app.get("price_cents", 0),
                "stripe_price_key": app.get("stripe_price_key"),
                "status": "active",
                "composition": dict(app["composition"]),
            }
            row = (
                db.query(MarketplaceApp)
                .filter(MarketplaceApp.slug == app["slug"])
                .first()
            )
            if row is None:
                changes.append(f"create {app['slug']}")
                if not args.dry_run:
                    db.add(MarketplaceApp(slug=app["slug"], **desired))
            else:
                diff = [k for k, v in desired.items() if getattr(row, k) != v]
                if diff:
                    changes.append(f"update {app['slug']} ({', '.join(diff)})")
                    if not args.dry_run:
                        for k, v in desired.items():
                            setattr(row, k, v)
                        flag_modified(row, "composition")

        # ── tool audit: move backends/duplicates/unused raws off the menu ─
        for spec in all_specs:
            wanted = engine_only.get(spec.connector_name)
            if not wanted:
                continue
            changed = []
            for t in spec_rows.rows(spec):
                if not isinstance(t, dict) or t.get("engine_only"):
                    continue
                if wanted == "*" or t.get("action") in wanted:
                    changed.append(t.get("action"))
                    if not args.dry_run:
                        t["engine_only"] = True
            if changed:
                changes.append(
                    f"engine_only {spec.connector_name}: {', '.join(changed)}"
                )
                if not args.dry_run:
                    flag_modified(spec, "tools")
                    if spec_rows.is_split(spec):
                        flag_modified(spec, "endpoints")
                    spec.version = (spec.version or 0) + 1

        # ── …and out of every binding: a capability naming an engine-only
        #    tool is dead config the validator flags. Disabled, not deleted
        #    (reversible, and the binding UI keeps its history).
        hidden = {
            (s.connector_name, t.get("action"))
            for s in all_specs
            for t in spec_rows.rows(s)
            if isinstance(t, dict) and t.get("engine_only")
        }
        for spec in all_specs:
            wanted = engine_only.get(spec.connector_name)
            if wanted:
                for t in spec_rows.rows(spec):
                    if isinstance(t, dict) and (
                        wanted == "*" or t.get("action") in wanted
                    ):
                        hidden.add((spec.connector_name, t.get("action")))
        for b in db.query(AgentConnectionBinding).all() if selected is None else []:
            caps = [dict(c) for c in b.capabilities or []]
            off = [
                c["action"]
                for c in caps
                if isinstance(c, dict)
                and c.get("enabled", True)
                and (b.connector_name, c.get("action")) in hidden
            ]
            if not off:
                continue
            changes.append(
                f"binding {b.agent_slug}/{b.connector_name}: disable {', '.join(off)}"
            )
            if not args.dry_run:
                for c in caps:
                    if c.get("action") in off:
                        c["enabled"] = False
                b.capabilities = caps
                flag_modified(b, "capabilities")

        # ── bindings to connectors that no longer exist (Mar-2026 Outlook
        #    experiment): no spec, no tools — deleted outright.
        for b in (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.connector_name.in_(retired_binding_connectors)
            )
            .all()
        ):
            changes.append(f"delete binding {b.agent_slug}/{b.connector_name}")
            if not args.dry_run:
                db.delete(b)

        # ── delete the retired rows LAST (components already re-homed) ───
        for slug in retired_slugs:
            row = db.query(MarketplaceApp).filter(MarketplaceApp.slug == slug).first()
            if row is not None:
                changes.append(f"delete {slug} ({row.tier})")
                if not args.dry_run:
                    db.delete(row)

        if not changes:
            print("catalog up to date")
            return
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        db.commit()
        print(f"committed {len(changes)} change(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
