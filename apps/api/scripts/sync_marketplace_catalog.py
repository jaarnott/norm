"""Seed the marketplace catalog — the reviewed source of `marketplace_apps`.

One row per **App**, the unified user-facing unit (docs/apps-marketplace-plan.md):
integration apps (connector-spec-backed: Loaded, BambooHR, …), platform apps
(App/AppVersion-backed: Hiring, Training, …) and the three per-agent bundles
that generalize today's agent billing. Each row's ``composition`` declares what
enabling the app lights up — spec, agents, components with their pages,
playbooks — replacing the implicit page/component/connector binding seams.

Invariants this script enforces (it exits non-zero rather than seed a bad
catalog):
  * every component is claimed by exactly ONE app;
  * every integration app's ``spec`` names an existing ConnectionSpec;
  * every agent slug referenced exists in ``agent_configs``.

Day-one neutrality: every seeded row is ``bundled: true`` (and absence of an
org entitlement row means "bundled default applies"), so seeding this catalog
changes NOTHING for any org until an owner flips a toggle. The per-agent price
metadata is carried for the marketplace UI; billing's booleans keep working
until the billing generalization lands.

Composition follows the connections/apps split (29 Aug): apps declare
``connections`` (the pure pipes they consume, one or many) and optionally
``tool_actions`` ("connector.*" wildcards or exact keys) claiming agent tools;
a connection stays available while ANY entitled app declares it. ``agents`` on
a connection-backed app is informational (whose menus its pages join).
``owns_agents`` appears only on the agent bundles — the entitlement gate reads
ONLY that key, so disabling e.g. Loaded can never switch an agent off.

Idempotent — safe to re-run; ``user``-tier rows (org-published apps) are never
touched. The config DB is shared across every environment, so committing
reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_marketplace_catalog.py --dry-run
    .venv/bin/python scripts/sync_marketplace_catalog.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _c(key, agent, page=None, full_width=False, description=""):
    return {
        "key": key,
        "agent": agent,
        "page": page,
        "full_width": full_width,
        "description": description,
    }


def _p(id, label, icon):
    return {"id": id, "label": label, "icon": icon}


APPS = [
    # ── Integration apps (connector-spec-backed) ─────────────────────────
    {
        "slug": "loaded",
        "name": "Loaded",
        "icon": "📦",
        "tier": "integration",
        "description": (
            "Stock, purchasing, invoices, rosters, recipes, menus and sales "
            "from LoadedHub — the venues' system of record."
        ),
        "composition": {
            # Loaded functionality; recipe/menu saves ride the CB pipe (the
            # connections/apps split: apps declare EVERY connection they use).
            "connections": ["loadedhub", "cook_brothers_app"],
            "tool_actions": ["loadedhub.*"],
            # informational: whose menus this app's tools/pages serve
            "agents": [],  # filled live from bindings at seed time
            "components": [
                _c(
                    "roster_editor",
                    "time_attendance",
                    _p("roster", "Roster", "Calendar"),
                    True,
                    "Week/day roster grid with drag editing and Loaded publish.",
                ),
                _c(
                    "roster_table",
                    "time_attendance",
                    None,
                    False,
                    "Compact roster table for chat answers.",
                ),
                _c(
                    "orders_dashboard",
                    "procurement",
                    _p("orders", "Orders", "ShoppingCart"),
                    True,
                    "Purchase orders: outstanding, recent, and detail.",
                ),
                _c(
                    "invoices_dashboard",
                    "procurement",
                    _p("invoices", "Invoices", "Receipt"),
                    True,
                    "Outstanding supplier invoices with review state.",
                ),
                _c(
                    "purchase_order_editor",
                    "procurement",
                    None,
                    False,
                    "Editable purchase-order draft card; Place Order submits to Loaded.",
                ),
                _c(
                    "receive_invoice_editor",
                    "procurement",
                    None,
                    False,
                    "Receive-invoice card: units, costs, PO link, Accept & Receive.",
                ),
                _c(
                    "stock_picker",
                    "procurement",
                    None,
                    False,
                    "Stock item picker used by order flows.",
                ),
                _c(
                    "menu_editor",
                    "executive_chef",
                    _p("menus", "Menus", "BookOpen"),
                    True,
                    "Menus with sections, dishes and sell prices; saves to Loaded.",
                ),
                _c(
                    "recipe_editor",
                    "executive_chef",
                    _p("recipes", "Recipes", "ChefHat"),
                    True,
                    "Recipe editor with live Loaded costs; Save writes to Loaded.",
                ),
                _c(
                    "menu_engineering",
                    "executive_chef",
                    _p("menu-engineering", "Menu Engineering", "Grid2x2"),
                    True,
                    "Popularity × profitability quadrants from the COGS report.",
                ),
            ],
            "playbooks": [],  # informational; playbooks stay agent-owned
            "mcp_domain": None,
        },
    },
    {
        "slug": "bamboohr",
        "name": "BambooHR",
        "icon": "🎋",
        "tier": "integration",
        "description": "Jobs, applications and candidate files from BambooHR.",
        "composition": {
            "connections": ["bamboohr"],
            "tool_actions": ["bamboohr.*"],
            "agents": [],
            "components": [
                _c(
                    "hiring_board",
                    "hr",
                    _p("hiring", "Hiring (BambooHR)", "Users"),
                    True,
                    "Hiring pipeline board over BambooHR jobs and applications.",
                ),
            ],
        },
    },
    {
        "slug": "cook-brothers-app",
        "name": "Cook Brothers App",
        "icon": "🍳",
        "tier": "integration",
        "description": (
            "Kitchen, food safety, functions, training and marketing tools "
            "from the Cook Brothers App (MCP), plus the Loaded surfaces Norm's "
            "own OAuth can't reach (recipe writes, supplier tenders). Migrating "
            "into Norm domain by domain — see docs/lite-apps-architecture.md."
        ),
        "composition": {
            "connections": ["cook_brothers_app"],
            "tool_actions": ["cook_brothers_app.*"],
            "agents": [],
            "components": [
                # Loaded's tenders API is unreachable with Norm's OAuth client,
                # so this page bridges through the CB App connection — the
                # component belongs to THIS app: no CB connection, no tenders.
                _c(
                    "supplier_tenders",
                    "procurement",
                    _p("supplier-tenders", "Supplier Tenders", "Gavel"),
                    True,
                    "Agreed supplier price lists, with tendered-vs-paid price review.",
                ),
            ],
        },
    },
    {
        "slug": "gmail",
        "name": "Gmail",
        "icon": "✉️",
        "tier": "integration",
        "description": "Send email on behalf of a connected Gmail account.",
        "composition": {
            "connections": ["gmail"],
            "tool_actions": ["gmail.*"],
            "agents": [],
            "components": [],
        },
    },
    {
        "slug": "deputy",
        "name": "Deputy",
        "icon": "🕐",
        "tier": "integration",
        "description": "Deputy workforce data (currently disabled at spec level).",
        "composition": {
            "connections": ["deputy"],
            "tool_actions": ["deputy.*"],
            "agents": [],
            "components": [],
        },
    },
    {
        "slug": "bidfood",
        "name": "Bidfood",
        "icon": "🚚",
        "tier": "integration",
        "description": "Bidfood supplier ordering.",
        "composition": {
            "connections": ["bidfood"],
            "tool_actions": ["bidfood.*"],
            "agents": [],
            "components": [],
        },
    },
    {
        "slug": "brevo",
        "name": "Brevo",
        "icon": "📣",
        "tier": "integration",
        "description": "Email campaigns, contact lists and senders via Brevo.",
        "composition": {
            "connections": ["brevo"],
            "tool_actions": ["brevo.*"],
            "agents": [],
            "components": [],
        },
    },
    {
        "slug": "metricool",
        "name": "Metricool",
        "icon": "📊",
        "tier": "integration",
        "description": "Social analytics, posts and scheduling via Metricool.",
        "composition": {
            "connections": ["metricool"],
            "tool_actions": ["metricool.*"],
            "agents": [],
            "components": [],
        },
    },
    # ── Platform apps (App/AppVersion-backed) ────────────────────────────
    {
        "slug": "hiring",
        "name": "Hiring",
        "icon": "🧑‍🍳",
        "tier": "platform",
        "description": "Norm's hiring pipeline: jobs, candidates, stages, hires.",
        "composition": {"app_slug": "hiring", "agents": ["hr"], "components": []},
    },
    {
        "slug": "training",
        "name": "Training",
        "icon": "🎓",
        "tier": "platform",
        "description": "Training programs, enrolments, sign-offs and records.",
        "composition": {"app_slug": "training", "agents": ["hr"], "components": []},
    },
    {
        "slug": "weekly-venue-performance",
        "name": "Weekly venue performance",
        "icon": "📈",
        "tier": "platform",
        "description": "Weekly sales snapshot per venue.",
        "composition": {
            "app_slug": "weekly-venue-performance",
            "connections": ["loadedhub"],
            "agents": ["app_builder"],
            "components": [],
        },
    },
    # ── Agent bundles (generalize the per-agent billing add-ons) ─────────
    # bundled:true for day-one neutrality — flipping one to paid is a
    # deliberate later act, not a side effect of seeding. Price metadata
    # mirrors billing_service.AGENT_PRICES_CENTS.
    {
        "slug": "hr-agent",
        "name": "Norm HR",
        "icon": "🧑‍💼",
        "tier": "platform",
        "price_cents": 1000,
        "stripe_price_key": "hr",
        "description": "The HR agent: hiring, onboarding, staff questions.",
        "composition": {"owns_agents": ["hr"], "agents": ["hr"], "components": []},
    },
    {
        "slug": "procurement-agent",
        "name": "Norm Procurement",
        "icon": "🛒",
        "tier": "platform",
        "price_cents": 500,
        "stripe_price_key": "procurement",
        "description": "The procurement agent: ordering, invoices, stock.",
        "composition": {
            "owns_agents": ["procurement"],
            "agents": ["procurement"],
            "components": [],
        },
    },
    {
        "slug": "reports-agent",
        "name": "Norm Reports",
        "icon": "📑",
        "tier": "platform",
        "price_cents": 0,
        "stripe_price_key": "reports",
        "description": "The reports agent: sales, labour and custom reporting.",
        "composition": {
            "owns_agents": ["reports"],
            "agents": ["reports"],
            "components": [],
        },
    },
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
    # First run predates the deployed create_all: add the table if missing
    # (create_all only ADDS tables — same call main.py makes at startup).
    ConfigBase.metadata.create_all(db.get_bind())
    try:
        # ── validation inputs ────────────────────────────────────────────
        spec_names = {s.connector_name for s in db.query(ConnectionSpec).all()}
        agent_slugs = {a.agent_slug for a in db.query(AgentConfig).all()}
        live_agents: dict[str, list[str]] = {}
        for b in (
            db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.enabled == True)  # noqa: E712
            .all()
        ):
            live_agents.setdefault(b.connector_name, [])
            if b.agent_slug not in live_agents[b.connector_name]:
                live_agents[b.connector_name].append(b.agent_slug)

        # ── invariants ───────────────────────────────────────────────────
        errors: list[str] = []
        claimed: dict[str, str] = {}
        for app in APPS:
            comp = app["composition"]
            for conn in comp.get("connections") or []:
                if conn not in spec_names:
                    errors.append(f"{app['slug']}: connection '{conn}' does not exist")
            for c in comp.get("components", []):
                if c["key"] in claimed:
                    errors.append(
                        f"component '{c['key']}' claimed by both "
                        f"'{claimed[c['key']]}' and '{app['slug']}'"
                    )
                claimed[c["key"]] = app["slug"]
                if c["agent"] not in agent_slugs:
                    errors.append(
                        f"{app['slug']}/{c['key']}: unknown agent '{c['agent']}'"
                    )
            for a in (comp.get("agents") or []) + (comp.get("owns_agents") or []):
                if a and a not in agent_slugs:
                    errors.append(f"{app['slug']}: unknown agent '{a}'")
        if errors:
            for e in errors:
                print(f"INVALID: {e}")
            sys.exit(1)

        # ── upsert ───────────────────────────────────────────────────────
        changes = []
        for app in APPS:
            comp = dict(app["composition"])
            conns = comp.get("connections") or []
            if conns and not comp.get("app_slug"):
                # informational agent list derives from the LIVE bindings of
                # every declared connection, so the catalog never drifts from
                # what actually serves whom.
                agents: set[str] = set()
                for conn in conns:
                    agents.update(live_agents.get(conn, []))
                comp["agents"] = sorted(agents)
            desired = {
                "name": app["name"],
                "description": app["description"],
                "icon": app.get("icon"),
                "tier": app["tier"],
                "bundled": app.get("bundled", True),
                "price_cents": app.get("price_cents", 0),
                "stripe_price_key": app.get("stripe_price_key"),
                "status": "active",
                "composition": comp,
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
