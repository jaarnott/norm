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


# Rows that stop existing under hierarchy v2. Deleting an integration row
# un-blocks its connection for orgs that had disabled the old app — intended:
# connections are plumbing now, gated by hires and App switches instead.
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
]

APPS = [
    # ── AI Team Members (tier='agent') ───────────────────────────────────
    {
        "slug": "procurement-agent",
        "name": "Procurement",
        "description": "Handles purchasing, suppliers and ordering.",
        "icon": "🛒",
        "tier": "agent",
        "price_cents": 500,
        "stripe_price_key": "procurement",
        "composition": {
            "owns_agents": ["procurement"],
            "tagline": "Purchase orders, invoice receiving and reconciliation, stock and tenders.",
            "unlocks": ["norm-procurement", "loaded-procurement", "bidfood-app"],
        },
    },
    {
        "slug": "hr-agent",
        "name": "HR",
        "description": "Runs hiring, training and employee records.",
        "icon": "🧑‍💼",
        "tier": "agent",
        "price_cents": 1000,
        "stripe_price_key": "hr",
        "composition": {
            "owns_agents": ["hr"],
            "tagline": "Job posts, candidates, onboarding and employee records.",
            "unlocks": ["hiring", "training", "bamboohr-app"],
        },
    },
    {
        "slug": "executive-chef-agent",
        "name": "Executive Chef",
        "description": "Keeps recipes, menus and menu engineering profitable and up to date.",
        "icon": "👨‍🍳",
        "tier": "agent",
        "price_cents": 0,
        "composition": {
            "owns_agents": ["executive_chef"],
            "tagline": "Recipes, menus, costs and menu engineering.",
            "unlocks": ["loaded-kitchen"],
        },
    },
    {
        "slug": "time-attendance-agent",
        "name": "Time & Attendance",
        "description": "Builds and publishes rosters, watches labour against sales.",
        "icon": "⏱️",
        "tier": "agent",
        "price_cents": 0,
        "composition": {
            "owns_agents": ["time_attendance"],
            "tagline": "Rosters, timeclock and labour cost.",
            "unlocks": ["loaded-rostering"],
        },
    },
    {
        "slug": "marketing-agent",
        "name": "Marketing",
        "description": "Writes and schedules campaigns and social posts, reports on reach.",
        "icon": "📣",
        "tier": "agent",
        "price_cents": 0,
        "composition": {
            "owns_agents": ["marketing"],
            "tagline": "Campaigns, social posts and reach.",
            "unlocks": [],
        },
    },
    # ── Apps (tier='app') — named after the product they are ─────────────
    {
        "slug": "norm-procurement",
        "name": "Norm Procurement",
        "description": "Ordering workflows, drafts and tasks — runs on Norm.",
        "icon": "🧾",
        "tier": "app",
        "composition": {"components": [], "connections": []},
    },
    {
        "slug": "loaded-procurement",
        "name": "Loaded",
        "description": "Orders, invoices and receiving inside Loaded — the venues' system of record.",
        "icon": "📦",
        "tier": "app",
        "composition": {
            "components": [
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
                    "purchase_order_editor",
                    full_width=False,
                    description="Editable purchase-order draft card; Place Order submits to Loaded.",
                ),
                _c(
                    "receive_invoice_editor",
                    full_width=False,
                    description="Receive-invoice card: units, costs, PO link, Accept & Receive.",
                    connections=["loadedhub"],
                ),
                _c(
                    "stock_picker",
                    full_width=False,
                    description="Stock item picker used by order flows.",
                ),
                _c(
                    "supplier_tenders",
                    page=_p("supplier-tenders", "Supplier Tenders", "Gavel"),
                    full_width=True,
                    description="Agreed supplier price lists, with tendered-vs-paid price review.",
                    connections=["cook_brothers_app"],
                ),
            ],
        },
    },
    {
        "slug": "bidfood-app",
        "name": "Bidfood",
        "description": "Ordering through the Bidfood catalogue.",
        "icon": "🚚",
        "tier": "app",
        "bundled": False,  # ships off — its capabilities are disabled today
        "composition": {
            "components": [],
            # NOTE: no top-level `connections` and no `tool_actions` anywhere
            # in this catalog, deliberately: those keys feed the
            # unentitled_connectors / unentitled_tool_actions BLOCKING filters,
            # and with the integration rows gone a single-claimer row would let
            # one App switch black-hole a whole connector for the org (the
            # weekly-venue-performance/loadedhub footgun the pre-push review
            # caught). Until per-App claims are scoped to exact action lists,
            # an App switch gates its PAGES AND COMPONENTS only; chat tools
            # gate by team member. Component-level `connections` entries are
            # safe — they only feed required_connections derivation.
            "component_connections": ["bidfood"],
        },
    },
    {
        "slug": "loaded-kitchen",
        "name": "Loaded",
        "description": "Recipes, menus and menu engineering inside Loaded.",
        "icon": "📗",
        "tier": "app",
        "composition": {
            "components": [
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
        },
    },
    {
        "slug": "loaded-rostering",
        "name": "Loaded",
        "description": "Rosters built and published in Loaded.",
        "icon": "🗓️",
        "tier": "app",
        "composition": {
            "components": [
                _c(
                    "roster_editor",
                    page=_p("roster", "Roster", "Calendar"),
                    full_width=True,
                    description="Week/day roster grid with drag editing and Loaded publish.",
                ),
                _c(
                    "roster_table",
                    full_width=False,
                    description="Compact roster table for chat answers.",
                ),
            ],
        },
    },
    {
        "slug": "bamboohr-app",
        "name": "BambooHR",
        "description": "Hiring pipeline over BambooHR jobs and applications.",
        "icon": "🎋",
        "tier": "app",
        "composition": {
            "components": [
                _c(
                    "hiring_board",
                    page=_p("hiring", "Hiring", "Users"),
                    full_width=True,
                    description="Hiring pipeline board over BambooHR jobs and applications.",
                    connections=["bamboohr"],
                ),
            ],
        },
    },
    # ── Norm-native storage Apps (App-platform pointers, zero connections) ─
    {
        "slug": "hiring",
        "name": "Norm HR",
        "description": "Hiring and onboarding records — runs on Norm.",
        "icon": "🧑‍💻",
        "tier": "app",
        "composition": {"app_slug": "hiring", "agents": ["hr"]},
    },
    {
        "slug": "training",
        "name": "Training",
        "description": "Training programs and completion records — runs on Norm.",
        "icon": "🎓",
        "tier": "app",
        "bundled": False,  # optional under HR — enable when wanted
        "composition": {"app_slug": "training", "agents": ["hr"]},
    },
    {
        "slug": "weekly-venue-performance",
        "name": "Weekly venue performance",
        "description": "A weekly sales/performance snapshot per venue.",
        "icon": "📈",
        "tier": "app",
        "composition": {
            "app_slug": "weekly-venue-performance",
            "agents": ["reports"],
            "component_connections": ["loadedhub"],
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
    ConfigBase.metadata.create_all(db.get_bind())
    try:
        # ── validation inputs ────────────────────────────────────────────
        spec_names = {s.connector_name for s in db.query(ConnectionSpec).all()}
        agent_slugs = {a.agent_slug for a in db.query(AgentConfig).all()}

        # ── invariants ───────────────────────────────────────────────────
        errors: list[str] = []
        claimed: dict[str, str] = {}
        app_slugs_here = {a["slug"] for a in APPS}
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
            for u in comp.get("unlocks") or []:
                if u not in app_slugs_here:
                    errors.append(f"{app['slug']}: unlocks unknown app '{u}'")
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
