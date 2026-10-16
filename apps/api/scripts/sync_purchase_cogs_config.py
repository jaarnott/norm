"""Install the purchases-based cost-of-goods dashboard (Oct 2026).

A venue that receives its invoices into Loaded but runs no stocktakes reads
cost of goods as purchases ÷ POS sales. Plan and the figures it was checked
against: docs/plans/procurement-cogs-dashboard.md.

What this puts in the shared config DB, idempotently:

  loadedhub.get_received_items_for_period   code + description + schema from
                                            sync_received_items_config.TOOL
                                            (month / week / supplier modes,
                                            freight, the reconciliation); the
                                            row's shapes and effect are kept
  loadedhub.get_purchase_cogs               NEW tool: the join of purchases and
                                            sales per month / week, food vs
                                            beverage (config/consolidators/
                                            purchase_cogs.py)
  dashboard template procurement-cogs       from app.db.dashboard_templates;
                                            the stale procurement-dashboard
                                            (Xero, raw endpoints) is removed
  skill cogs_analysis                       one section on the no-stocktake
                                            case

The App claim (Loaded Stock → get_purchase_cogs) lives in
sync_marketplace_catalog.py — run it AFTER this, since its validator wants the
tool row to exist.

Usage:
    .venv/bin/python scripts/sync_purchase_cogs_config.py --dry-run
    .venv/bin/python scripts/sync_purchase_cogs_config.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sync_received_items_config import (  # noqa: E402
    ACTION as RECEIVED_ACTION,
)
from sync_received_items_config import (  # noqa: E402
    FUNCTION_CODE_PATH as RECEIVED_CODE_PATH,
)
from sync_received_items_config import (  # noqa: E402
    TOOL as RECEIVED_TOOL,
)

CONNECTOR = "loadedhub"
ACTION = "get_purchase_cogs"
TEMPLATE_SLUG = "procurement-cogs"
STALE_TEMPLATE_SLUG = "procurement-dashboard"
SKILL_SLUG = "cogs_analysis"
CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "purchase_cogs.py"
)

#: What the received-items row takes from its script on every run. The live
#: row's `shapes` (the response shaping that moved into the tool in Sep 2026)
#: and anything else the script doesn't know are left as they are.
RECEIVED_FIELDS = (
    "description",
    "required_fields",
    "optional_fields",
    "field_descriptions",
    "field_schema",
    "max_result_chars",
    "effect",
    "read_only",
)

TOOL = {
    "action": ACTION,
    "method": "GET",  # consolidator dispatch; reads only
    "effect": "read",
    "read_only": True,
    "description": (
        "Cost of goods for a venue WITHOUT stocktakes: purchases (received "
        "invoice lines ex-GST, by invoice date, credits negative, freight "
        "included unless include_freight=false) against POS sales ex-GST, per "
        "month or trading week — cogs_pct = purchases / sales, plus food and "
        "beverage cost of goods from Loaded's categories on both sides. The "
        "previous period of the same length rides along (summary.previous, "
        "cogs_pct_change in points) unless compare_previous=false. Say it is "
        "purchases-based: what was bought, not what was used. When the summary "
        "carries uncategorised_sales_pct, that share of sales has no POS "
        "category and the food / beverage split misses it — say so. For the "
        "group, venues='all' gives one answer with per-venue rows. Ask ONCE for "
        "the whole period."
    ),
    "required_fields": [],
    "optional_fields": [
        "period",
        "start",
        "end",
        "confirmed_by_user",
        "interval",
        "venues",
        "include_freight",
        "compare_previous",
    ],
    "field_descriptions": {
        "period": (
            "Plain English — 'this month', 'last 12 months'. Prefer it to "
            "start/end; never work out dates yourself."
        ),
        "start": "Only for exact clock times the user asked for: ISO 8601 with offset.",
        "end": "Window end; same rule as start.",
        "interval": "'month' (default) or 'week' — the bucket of each row.",
        "venues": "'all', or a list of venue names — one answer, a row per venue and bucket.",
        "include_freight": "Default true: freight is part of purchases.",
        "compare_previous": "Default true: the previous period is read too, for the change.",
    },
    "field_schema": {
        "interval": {
            "type": "string",
            "enum": ["month", "week"],
            "description": "The bucket of each row (default month)",
        },
        "venues": {
            "description": "'all', or a list of venue names — one merged answer"
        },
        "include_freight": {
            "type": "boolean",
            "description": "Default true: freight counts as purchases",
        },
        "compare_previous": {
            "type": "boolean",
            "description": "Default true: read the previous period too",
        },
        "confirmed_by_user": {
            "type": "boolean",
            "description": (
                "Only for an explicit start/end that is not a trading day, and "
                "only when the user really did ask for those clock times."
            ),
        },
    },
    "max_result_chars": 40_000,
    "consolidator_config": {
        # function_code injected from CODE_PATH at sync time.
        # dates + venues + purchases + daily sales + one items read per venue:
        # 5 for one venue, 7 for three. Each read is itself a consolidator.
        "max_api_calls": 12,
        "allowed_write_actions": [],
    },
}

SKILL_SECTION = """
### Venues without stocktakes
If the venue receives invoices into Loaded but doesn't stocktake (or
`get_cogs_detail_for_period` has nothing useful), use `get_purchase_cogs`:
purchases ex-GST ÷ POS sales ex-GST per month or week, food and beverage from
Loaded's categories on both sides, with the previous period for the change.
Say it is purchases-based — what was bought, not what was used — and when the
summary reports `uncategorised_sales_pct`, say that share of sales has no POS
category in Loaded so the food / beverage split misses it. The Cost of Goods
dashboard on the Procurement page shows the same figures.
"""


def _sync_tools(spec, changes):
    tools = list(spec.tools or [])
    by_action = {t.get("action"): i for i, t in enumerate(tools)}

    # Received items: the script's row, over the live one.
    if RECEIVED_ACTION not in by_action:
        raise SystemExit(
            f"{RECEIVED_ACTION} is not installed — run "
            "scripts/sync_received_items_config.py first"
        )
    live = dict(tools[by_action[RECEIVED_ACTION]])
    updated = dict(live)
    for key in RECEIVED_FIELDS:
        if key in RECEIVED_TOOL:
            updated[key] = RECEIVED_TOOL[key]
    cc = dict(updated.get("consolidator_config") or {})
    cc["function_code"] = RECEIVED_CODE_PATH.read_text(encoding="utf-8")
    cc["max_api_calls"] = RECEIVED_TOOL["consolidator_config"]["max_api_calls"]
    cc.setdefault("allowed_write_actions", [])
    updated["consolidator_config"] = cc
    if updated != live:
        tools[by_action[RECEIVED_ACTION]] = updated
        changes.append(f"updated tool {RECEIVED_ACTION}")

    # The join tool.
    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": CODE_PATH.read_text(encoding="utf-8"),
    }
    if ACTION in by_action:
        if tools[by_action[ACTION]] != tool:
            tools[by_action[ACTION]] = tool
            changes.append(f"updated tool {ACTION}")
    else:
        tools.append(tool)
        changes.append(f"added tool {ACTION}")
    return tools


def _sync_template(db, changes):
    from app.db.config_models import DashboardTemplate
    from app.db.dashboard_templates import get_template

    wanted = get_template(TEMPLATE_SLUG)
    if not wanted:
        raise SystemExit(f"{TEMPLATE_SLUG} is not in app.db.dashboard_templates")
    row = (
        db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == TEMPLATE_SLUG)
        .first()
    )
    fields = {
        "agent_slug": wanted["agent_slug"],
        "title": wanted["title"],
        "description": wanted.get("description", ""),
        "charts": wanted["charts"],
        "enabled": True,
    }
    if row is None:
        db.add(DashboardTemplate(slug=TEMPLATE_SLUG, **fields))
        changes.append(f"added dashboard template {TEMPLATE_SLUG}")
    else:
        differs = {k for k, v in fields.items() if getattr(row, k) != v}
        if differs:
            for k in differs:
                setattr(row, k, fields[k])
            changes.append(
                f"updated dashboard template {TEMPLATE_SLUG} ({', '.join(sorted(differs))})"
            )
    stale = (
        db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == STALE_TEMPLATE_SLUG)
        .first()
    )
    if stale is not None:
        db.delete(stale)
        changes.append(f"removed stale dashboard template {STALE_TEMPLATE_SLUG}")


def _sync_skill(db, changes):
    from app.db.config_models import Playbook

    skill = db.query(Playbook).filter(Playbook.slug == SKILL_SLUG).first()
    if skill is None:
        changes.append(f"skill {SKILL_SLUG} not found — section NOT added")
        return
    if "get_purchase_cogs" in (skill.instructions or ""):
        return
    skill.instructions = (skill.instructions or "").rstrip() + "\n" + SKILL_SECTION
    changes.append(f"added the no-stocktake section to skill {SKILL_SLUG}")


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"{CONNECTOR} ConnectionSpec not found in config DB")
        by_action = {t.get("action") for t in (spec.tools or [])}
        missing = [a for a in ("get_sales",) if a not in by_action]
        if missing:
            raise SystemExit(
                f"{CONNECTOR} spec is missing the tools this consolidator "
                f"composes: {', '.join(missing)}"
            )

        changes: list[str] = []
        tools = _sync_tools(spec, changes)
        tool_changes = [
            c for c in changes if c.endswith(("get_purchase_cogs", RECEIVED_ACTION))
        ]
        _sync_template(db, changes)
        _sync_skill(db, changes)

        if not changes:
            print("Already in sync.")
            db.rollback()
            return
        verb = "Would apply" if dry_run else "Applied"
        if dry_run:
            db.rollback()
        else:
            if tool_changes:
                spec.tools = tools
                flag_modified(spec, "tools")
            db.commit()
        for line in changes:
            print(f"{verb}: {line}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(dry_run=parser.parse_args().dry_run)
