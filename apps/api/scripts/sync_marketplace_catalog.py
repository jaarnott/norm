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

Usage:
    .venv/bin/python scripts/sync_marketplace_catalog.py --dry-run
    .venv/bin/python scripts/sync_marketplace_catalog.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


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
        "match_supplier",
        "match_stock_items",
        "get_supplier_invoice_specs",
        "sensei_train_supplier",
        "show_orders",
    ],
    "cook_brothers_app": ["stock_loadedhub_tender"],
    "brevo": "*",
    "metricool": "*",
    "bidfood": "*",
}


def _member(slug, *, owns, name, description, icon, price=0, key=None, tagline=""):
    return {
        "slug": slug,
        "name": name,
        "description": description,
        "icon": icon,
        "tier": "agent",
        "price_cents": price,
        "stripe_price_key": key,
        "composition": {"owns_agents": [owns], "tagline": tagline},
    }


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
        price=500,
        key="procurement",
        description="Handles purchasing, suppliers and ordering.",
        tagline="Purchase orders, invoice receiving and reconciliation, stock and tenders.",
    ),
    _member(
        "hr-agent",
        owns="hr",
        name="HR",
        icon="🧑‍💼",
        price=1000,
        key="hr",
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
            "norm.update_thread_summary",
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
        note="Stock-on-hand, received-stock and stocktake questions need item ids from get_stock — never guess them.",
        name="Loaded Stock",
        member="procurement",
        icon="📦",
        description="Stock, ordering, invoices and supplier tenders in Loaded.",
        tools=[
            "loadedhub.get_stock",
            "loadedhub.calculate_template_stock_requirements",
            "loadedhub.generate_stocktake_report",
            "loadedhub.get_received_items_for_period",
            "cook_brothers_app.stock_find_stocktakes",
            "loadedhub.get_purchase_orders",
            "norm.create_purchase_order",
            "loadedhub.get_invoices",
            "loadedhub.review_and_receive_invoices",
            "loadedhub.receive_loadedhub_invoice",
            "loadedhub.reconcile_received_invoices",
            "norm.set_workflow_mode",
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
                connections=["cook_brothers_app"],
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
            "Recipes, menus and stock items live in Loaded. A recipe create needs a name, yield unit and at least one line; an update needs the recipe id and version_id from get_recipes. A draft recipe can be extracted from an uploaded PDF, image or Word document."
        ),
        name="Loaded Kitchen",
        member="executive_chef",
        icon="📗",
        description="Recipes, menus and menu engineering in Loaded.",
        tools=[
            "loadedhub.get_recipes",
            "loadedhub.edit_recipe",
            "loadedhub.get_menus",
            "loadedhub.manage_menu",
            "loadedhub.manage_stock_item",
            "cook_brothers_app.kitchen_record_recipe",
        ],
        components=[
            _c(
                "recipe_editor",
                page=_p("recipes", "Recipes", "BookOpen"),
                full_width=True,
                description="Recipe editor with live Loaded costs; Save writes to Loaded.",
                connections=["cook_brothers_app"],
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
        skills=["create_recipe_from_ingredients"],
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
        "bamboohr-app",
        name="BambooHR",
        member="hr",
        icon="🎋",
        description="Jobs, applications and employees from BambooHR.",
        tools=[
            "bamboohr.get_jobs",
            "bamboohr.get_applications",
            "bamboohr.get_application_details",
            "bamboohr.get_applicant_statuses",
            "bamboohr.list_employees",
            "bamboohr.get_employee",
            "bamboohr.get_applicant_resume",
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
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

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
                t.get("action") for t in s.tools or [] if isinstance(t, dict)
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
        for app in APPS:
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
        if "base" not in agent_slugs:
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
        if wild is not None and not (wild.capabilities or []):
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
        for app in APPS:
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
            wanted = ENGINE_ONLY.get(spec.connector_name)
            if not wanted:
                continue
            changed = []
            for t in spec.tools or []:
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
                    spec.version = (spec.version or 0) + 1

        # ── …and out of every binding: a capability naming an engine-only
        #    tool is dead config the validator flags. Disabled, not deleted
        #    (reversible, and the binding UI keeps its history).
        hidden = {
            (s.connector_name, t.get("action"))
            for s in all_specs
            for t in s.tools or []
            if isinstance(t, dict) and t.get("engine_only")
        }
        for spec in all_specs:
            wanted = ENGINE_ONLY.get(spec.connector_name)
            if wanted:
                for t in spec.tools or []:
                    if isinstance(t, dict) and (
                        wanted == "*" or t.get("action") in wanted
                    ):
                        hidden.add((spec.connector_name, t.get("action")))
        for b in db.query(AgentConnectionBinding).all():
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
                AgentConnectionBinding.connector_name.in_(RETIRED_BINDING_CONNECTORS)
            )
            .all()
        ):
            changes.append(f"delete binding {b.agent_slug}/{b.connector_name}")
            if not args.dry_run:
                db.delete(b)

        # ── delete the retired rows LAST (components already re-homed) ───
        for slug in RETIRED_SLUGS:
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
