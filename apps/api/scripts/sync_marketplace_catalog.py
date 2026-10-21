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

Usage:
    .venv/bin/python scripts/sync_marketplace_catalog.py --dry-run
    .venv/bin/python scripts/sync_marketplace_catalog.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.services.functions.catalog import (  # noqa: E402 — path set above
    APP as FUNCTIONS_APP,
    MEMBER as FUNCTIONS_MEMBER,
)


def _c(key, page=None, full_width=False, description="", connections=None):
    entry = {
        "key": key,
        "page": page,
        "full_width": full_width,
        "description": description,
    }
    if connections:
        entry["connections"] = connections
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
    "bidfood": "*",
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


def _app(
    slug,
    *,
    note=None,
    name,
    member,
    description,
    icon,
    tools=(),
    components=(),
    skills=(),
    switchable=True,
    bundled=True,
    extra=None,
):
    comp = {
        "member": member,
        "tools": list(tools),
        "components": list(components),
        "skills": list(skills),
    }
    if not switchable:
        comp["switchable"] = False
    if note:
        # Standing rule for this App, added to conversations while it's on
        # (validator caps: 300 chars each, 1,500 total).
        comp["note"] = note
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
    # ── Apps (tier='app'): one member each (Norm Core: all) ──────────────
    _app(
        "norm-core",
        name="Norm Core",
        member="*",
        switchable=False,
        icon="🧭",
        description="Norm's own foundations: memory, search, tasks, charts, email and connection setup.",
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
            # Norm builds and changes dashboards (scripts/sync_dashboard_tools.py)
            "norm_reports.get_dashboards",
            "norm_reports.manage_dashboard",
        ],
        components=[
            _shared(
                "dashboard_view", "Dashboard", "Each team member's dashboard page."
            ),
            _shared(
                "automated_task_board",
                "Tasks",
                "Each team member's scheduled tasks page.",
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
        "loaded-reports",
        name="Loaded Reports",
        member="reports",
        icon="📊",
        description="Sales, labour, budgets and cost of goods from Loaded — the numbers everyone needs.",
        tools=[
            "loadedhub.get_sales",
            "loadedhub.get_labour",
            "loadedhub.get_budgets",
            "loadedhub.get_cogs_detail_for_period",
            # POS items into Food / Beverage / Other — what makes food vs
            # beverage sales and cost of goods right. get_sales' items breakdown
            # is the read (sync_revenue_groups_config.py)
            "loadedhub.set_revenue_group",
        ],
        skills=[
            "product_sales_analysis",
            "sales_comparison",
            "staff_sales_performance",
            "weekly_sales_report",
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
    _app(
        "loaded-stock",
        note=(
            "Stock-on-hand, received-stock and stocktake questions need item ids from "
            "get_stock — never guess them. Cost of goods for a venue that doesn't "
            "stocktake is purchases against sales: get_purchase_cogs."
        ),
        name="Loaded Stock",
        member="procurement",
        icon="📦",
        description="Stock, ordering, invoices and supplier tenders in Loaded.",
        tools=[
            "loadedhub.get_stock",
            "loadedhub.calculate_template_stock_requirements",
            # get_stocktakes replaced generate_stocktake_report + Orbit's
            # stock_find_stocktakes (Sep 2026, sync_last_raw_tools.py).
            "loadedhub.get_stocktakes",
            "loadedhub.get_received_items_for_period",
            # Purchases ÷ POS sales per month / week, food vs beverage — the
            # cost-of-goods figure for a venue without stocktakes (Oct 2026).
            "loadedhub.get_purchase_cogs",
            "loadedhub.get_purchase_orders",
            "norm.create_purchase_order",
            "loadedhub.get_invoices",
            "loadedhub.review_and_receive_invoices",
            "loadedhub.reconcile_received_invoices",
            # Supplier aliases: review them, remove wrong ones (Oct 2026,
            # sync_manage_supplier_config.py).
            "loadedhub.manage_supplier",
        ],
        components=[
            _c(
                "orders_dashboard",
                page=_p("orders", "Orders", "ShoppingCart"),
                full_width=True,
                description="Purchase orders: outstanding, recent, and detail.",
            ),
            _c(
                "invoices_dashboard",
                page=_p("invoices", "Invoices", "Receipt"),
                full_width=True,
                description="Outstanding supplier invoices with review state.",
                connections=["loadedhub"],
            ),
            _c(
                "supplier_tenders",
                page=_p("supplier-tenders", "Supplier Tenders", "Gavel"),
                full_width=True,
                description="Agreed supplier price lists, with tendered-vs-paid review.",
                # Loaded's tenders API refuses Norm's OAuth token (TEMPORARY:
                # app/connectors/loaded_legacy.py; the Cook Brothers App until
                # 19 Oct 2026).
                connections=["loaded_legacy"],
            ),
            _c(
                "purchase_order_editor",
                description="Editable purchase-order draft; Place Order submits to Loaded.",
            ),
            _c(
                "receive_invoice_editor",
                description="Receive-invoice card: units, costs, PO link, Accept & Receive.",
                connections=["loadedhub"],
            ),
            _c("stock_picker", description="Stock item picker used by order flows."),
        ],
        skills=[
            "cogs_analysis",
            "create_stock_order",
            "receive_loadedhub_invoice",
            "receive_loadedhub_invoices",
            "reconcile_received_invoices",
            "stock_requirements",
            "stocktake_variance",
        ],
    ),
    _app(
        "bidfood-app",
        name="Bidfood",
        member="procurement",
        icon="🚚",
        description="Ordering through the Bidfood catalogue (tools to come).",
        extra={"component_connections": ["bidfood"]},
    ),
    _app(
        "loaded-kitchen",
        note=(
            "Recipes, menus and stock items live in Loaded. New recipes: draft_recipe opens each one as a card the chef completes and saves (no approval card); record_recipe writes one directly when asked to skip the card. An edit needs the recipe id from get_recipes."
        ),
        name="Loaded Kitchen",
        member="executive_chef",
        icon="📗",
        description="Recipes, menus and menu engineering in Loaded.",
        tools=[
            "loadedhub.get_recipes",
            "loadedhub.draft_recipe",
            "loadedhub.edit_recipe",
            "loadedhub.get_menus",
            "loadedhub.manage_menu",
            "loadedhub.manage_stock_item",
            # the recipe write (config/consolidators/record_recipe.py) — on
            # Loaded Legacy since 19 Oct 2026, the Cook Brothers App before
            "loaded_legacy.record_recipe",
        ],
        components=[
            _c(
                "recipe_editor",
                page=_p("recipes", "Recipes", "BookOpen"),
                full_width=False,  # in chat: one inline card per recipe (Oct 2026)
                description="Recipe editor with live Loaded costs; Save writes to Loaded.",
                # Saves go through Loaded Legacy (TEMPORARY:
                # app/connectors/loaded_legacy.py; the Cook Brothers App until
                # 19 Oct 2026).
                connections=["loaded_legacy"],
            ),
            _c(
                "menu_editor",
                page=_p("menus", "Menus", "LayoutGrid"),
                full_width=True,
                description="Menus with sections, dishes and sell prices; saves to Loaded.",
            ),
            _c(
                "menu_engineering",
                page=_p("menu-engineering", "Menu Engineering", "Grid2x2"),
                full_width=True,
                description="Popularity × profitability quadrants from the COGS report.",
            ),
        ],
        skills=["create_recipes"],
    ),
    _app(
        "loaded-time",
        note="Answer roster questions grouped by day and sorted by start time: staff, role, start–end, hours.",
        name="Loaded Time",
        member="time_attendance",
        icon="🗓️",
        description="Rosters built and published in Loaded.",
        tools=["norm.show_roster"],
        components=[
            _c(
                "roster_editor",
                page=_p("roster", "Roster", "Calendar"),
                full_width=True,
                description="Week/day roster grid with drag editing and Loaded publish.",
                connections=["loadedhub"],
            ),
            _c("roster_table", description="Compact roster table for chat answers."),
        ],
        skills=["roster_viewer"],
    ),
    _app(
        "hiring",
        name="Norm Hiring",
        member="hr",
        icon="🧑‍💻",
        description="Roles, candidate pipeline, candidates and talent pool — runs on Norm.",
        note="To show it, open_app 'Norm Hiring' — at a job or candidate by name (inputs job, candidate) or the talent pool (view).",
        extra={"app_slug": "hiring"},
    ),
    _app(
        "training",
        name="Norm Training",
        member="hr",
        icon="🎓",
        description="Training programs, plans, tracker and sign-offs — runs on Norm.",
        note="To show it, open_app 'Norm Training' — at a program or a person by name (inputs program, person) or a tab (view).",
        extra={"app_slug": "training"},
    ),
    _app(
        "performance",
        name="Norm Performance",
        member="hr",
        icon="📈",
        description="Performance reviews, review cycles and capability frameworks — runs on Norm.",
        note="To show it, open_app 'Norm Performance' — at a review (input review) or a tab (view).",
        extra={"app_slug": "performance"},
    ),
    _app(
        "bamboohr-app",
        name="BambooHR",
        member="hr",
        icon="🎋",
        description="Jobs, applications, CVs and employees — BambooHR is the group's hiring system.",
        tools=[
            # get_hr replaced six raw reads (Sep 2026, sync_last_raw_tools.py)
            # and took in the CV reader, get_applicant_resume, as `cv` (2 Oct
            # 2026, sync_hr_fixes.py).
            "bamboohr.get_hr",
        ],
        components=[
            _c(
                "hiring_board",
                page=_p("hiring", "Hiring pipeline", "Users"),
                full_width=True,
                description="Hiring pipeline over BambooHR jobs and applications.",
                connections=["bamboohr"],
            ),
            _c(
                "criteria_editor",
                description="Screening criteria for job applications.",
            ),
        ],
        skills=["candidate_review"],
    ),
    _app(
        "brevo-app",
        name="Brevo",
        member="marketing",
        icon="✉️",
        description="Email campaigns and contacts via Brevo (tools to come).",
        skills=["email_campaign_builder"],
        extra={"component_connections": ["brevo"]},
    ),
    _app(
        "metricool-app",
        name="Metricool",
        member="marketing",
        icon="📱",
        description="Social posts and analytics via Metricool (tools to come).",
        extra={"component_connections": ["metricool"]},
    ),
    # A native Norm module, like Loaded Stock's pages: React components gated by
    # `app: 'functions'`, data in Norm's own fn_* tables.
    _app(**FUNCTIONS_APP),
    _app(
        "xero",
        note=(
            "Amounts, due dates and accounts come from the xero tools — never "
            "quote one you haven't read. A bill lands in Xero as a draft via an "
            "approval card. If a tool says a venue is not connected to Xero, "
            "show the connect card (norm.show_connect, connector_name 'xero')."
        ),
        name="Xero",
        member="finance",
        icon="📒",
        description=(
            "Bills, contacts, chart of accounts and profit and loss from Xero; "
            "creates supplier bills from reviewed invoices."
        ),
        tools=[
            "xero.get_bills",
            "xero.get_contacts",
            "xero.get_accounts",
            "xero.get_profit_and_loss",
            "xero.create_bill",
        ],
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
            ),
        ],
    ),
    _app(
        "app-builder",
        note="To build or change an App, open the build_an_app skill first.",
        name="App Builder",
        member="app_builder",
        switchable=False,
        icon="🧩",
        description="Build custom Apps for your team by chatting.",
        tools=["norm.list_app_capabilities", "norm.save_app", "norm.get_app"],
        skills=["build_an_app"],
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
            for sk in comp.get("skills") or []:
                if sk not in playbook_slugs:
                    errors.append(f"{app['slug']}: skill '{sk}' does not exist")
                if sk in skill_claimed:
                    errors.append(
                        f"skill '{sk}' claimed by both '{skill_claimed[sk]}' and '{app['slug']}'"
                    )
                skill_claimed[sk] = app["slug"]
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
