"""Install the member dashboards (Oct 2026): Operations, Kitchen costs,
Finance, HR — plan: docs/plans/member-dashboards.md.

What this puts in the shared config DB, idempotently:

  tool rows        loadedhub.get_daily_performance  (config/consolidators/daily_performance.py)
                   — more tools join this list as the boards land
  App claims       the App that exposes each tool (a direct edit of the App's
                   composition; the catalog script would also apply every other
                   pending catalog diff, pricing included)
  templates        every slug in TEMPLATE_SLUGS, from app.db.dashboard_templates
                   (an existing row is brought up to date, a missing one added)

validate_config runs on the pending state before anything is committed, so a
claim of a tool the spec doesn't carry, or a tool without an effect, stops the
install.

Usage:
    .venv/bin/python scripts/sync_member_dashboards_config.py --dry-run
    .venv/bin/python scripts/sync_member_dashboards_config.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONSOLIDATORS = (
    pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
)

TEMPLATE_SLUGS = (
    "reports-dashboard",
    "hr-dashboard",
    "kitchen-costs",
    "finance-overview",
)

#: App slug -> tools it claims (added to the composition's tools list).
APP_CLAIMS = {
    "loaded-reports": ["loadedhub.get_daily_performance"],
    "loaded-kitchen": ["loadedhub.get_menu_item_costs"],
    # A tool belongs to exactly one App. The records read lives on Norm's core
    # App (always on) rather than on Hiring, Training or Performance, so the HR
    # member has it whichever of the three an organisation runs; the tool
    # itself refuses an app the organisation has off.
    "norm-core": ["norm.app_records"],
}

TOOLS = {
    "loadedhub": [
        {
            "action": "get_daily_performance",
            "method": "GET",  # consolidator dispatch; reads only
            "effect": "read",
            "read_only": True,
            "description": (
                "The operations picture for a period: one row per trading day (or "
                "week / month) per venue with sales incl. GST, sales ex GST, budget, "
                "last year (same trading days 52 weeks back), orders and average "
                "order, rostered and worked hours and cost, wage % (labour cost ÷ "
                "sales ex GST) and budget achievement — joined from get_sales and "
                "get_labour so a 'how are we tracking' question is ONE call. Summary "
                "carries the totals and the same ratios; by_venue one row per venue. "
                "For the group, venues='all'. Use get_sales / get_labour themselves "
                "for anything finer (items, staff, half-hours)."
            ),
            "required_fields": [],
            "optional_fields": [
                "period",
                "start",
                "end",
                "confirmed_by_user",
                "interval",
                "venues",
            ],
            "field_descriptions": {
                "period": (
                    "Plain English — 'today', 'this week', 'last month'. Prefer it "
                    "to start/end; never work out dates yourself."
                ),
                "start": "Only for exact clock times the user asked for: ISO 8601 with offset.",
                "end": "Window end; same rule as start.",
                "interval": "'day' (default), 'week' or 'month' — the bucket of each row.",
                "venues": "'all', or a list of venue names — one answer, a row per venue and bucket.",
            },
            "field_schema": {
                "interval": {
                    "type": "string",
                    "enum": ["day", "week", "month"],
                    "description": "The bucket of each row (default day)",
                },
                "venues": {
                    "description": "'all', or a list of venue names — one merged answer"
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
                # dates + venues + 3 sales reads + 1 labour read; each read is a
                # consolidator that fans out over venues itself.
                "max_api_calls": 20,
                "allowed_write_actions": [],
                "_code": "daily_performance.py",
            },
        },
        {
            "action": "get_menu_item_costs",
            "method": "GET",
            "effect": "read",
            "read_only": True,
            "description": (
                "The chef's cost of goods for a period: per product (POS item) the "
                "revenue ex GST, quantity, recipe cost (Loaded's own report: recipe × "
                "sold), margin, cost of goods % and whether it HAS a recipe — a product "
                "that sold with no cost has no recipe or an uncosted one; `missing` "
                "lists the top ones and the summary says what share of revenue they "
                "are. Theoretical cost of goods % (recipe cost ÷ revenue) sits beside "
                "the ACTUAL (received invoices ÷ revenue) and the gap in points — "
                "waste, portioning, uncosted recipes. by_category splits both Food / "
                "Beverage; `dear` is the highest-cost dishes; interval 'week' or "
                "'month' adds a `trend` (one report per bucket, up to 13). Ask ONCE "
                "for the period."
            ),
            "required_fields": [],
            "optional_fields": [
                "period",
                "start",
                "end",
                "confirmed_by_user",
                "interval",
                "top",
                "include_freight",
            ],
            "field_descriptions": {
                "period": (
                    "Plain English — 'this month', 'last week'. Prefer it to "
                    "start/end; never work out dates yourself."
                ),
                "start": "Only for exact clock times the user asked for: ISO 8601 with offset.",
                "end": "Window end; same rule as start.",
                "interval": "'week' or 'month' — adds a trend with one bucket per period.",
                "top": "Rows per list (default 25); the rest roll into '(others)'.",
                "include_freight": "Default true: freight counts as purchases.",
            },
            "field_schema": {
                "interval": {
                    "type": "string",
                    "enum": ["week", "month"],
                    "description": "Trend bucket (none = no trend)",
                },
                "top": {"type": "integer", "description": "Rows per list (default 25)"},
                "include_freight": {
                    "type": "boolean",
                    "description": "Default true: freight counts as purchases",
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
                # dates + up to 13 product reports (one per trend bucket) + the
                # purchases read.
                "max_api_calls": 20,
                "allowed_write_actions": [],
                "_code": "menu_item_costs.py",
            },
        },
    ],
    "norm": [
        {
            "action": "app_records",
            "method": "GET",
            "effect": "read",
            "read_only": True,
            "description": (
                "Rows of one of Norm's own apps — Hiring, Training, Performance — for "
                "this organisation: app + collection (job_openings, applications, "
                "interviews, assignments, performance_reviews …), an optional `where` "
                'on top-level fields ({"status": "open"}, or {"status": {"ne": '
                '"completed"}}), and a `date_field` with `period` (or start/end) to '
                "keep rows whose date falls in the window, or before_today for overdue "
                "ones. count_only for a number. The app's own screens remain the place "
                "to change anything."
            ),
            "required_fields": ["app", "collection"],
            "optional_fields": [
                "where",
                "date_field",
                "period",
                "start",
                "end",
                "before_today",
                "fields",
                "limit",
                "count_only",
            ],
            "field_descriptions": {
                "app": "hiring, training or performance",
                "collection": "The app's collection, e.g. job_openings, applications, assignments",
                "where": "Equality or {op: value} filters on top-level fields",
                "date_field": "A date field to window on (scheduled_at, due_date)",
                "period": "Plain English window for date_field ('this week')",
                "before_today": "true: rows whose date_field is before today (overdue)",
                "fields": "Only these fields in the rows",
                "limit": "Max rows (default 500)",
                "count_only": "true: the count, no rows",
            },
            "field_schema": {
                "app": {
                    "type": "string",
                    "enum": ["hiring", "training", "performance"],
                },
                "where": {"type": "object", "description": "Field filters"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
                "count_only": {"type": "boolean"},
                "before_today": {"type": "boolean"},
            },
            "max_result_chars": 40_000,
        },
    ],
}


#: Rows that must say they are read-only: a consolidator may call another
#: only when the callee is marked read_only, and get_cogs_detail_for_period
#: (a pure read wrapping Loaded's report) never was — the chef's tool got
#: "only read-only consolidators can be called from another consolidator".
READ_ONLY_FIXES = {"loadedhub": ["get_cogs_detail_for_period"]}


def _tool_row(tool: dict) -> dict:
    row = dict(tool)
    cc = tool.get("consolidator_config")
    if cc:  # a built-in (norm.*) row has none: its code is in internal_tools
        cc = dict(cc)
        code = cc.pop("_code")
        cc["function_code"] = (CONSOLIDATORS / code).read_text(encoding="utf-8")
        row["consolidator_config"] = cc
    return row


def _sync_tools(db, changes):
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec

    for connector, tools in TOOLS.items():
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == connector)
            .first()
        )
        if not spec:
            raise SystemExit(f"{connector} ConnectionSpec not found in config DB")
        rows = list(spec.tools or [])
        by_action = {t.get("action"): i for i, t in enumerate(rows)}
        touched = False
        for tool in tools:
            want = _tool_row(tool)
            at = by_action.get(want["action"])
            if at is None:
                rows.append(want)
                changes.append(f"added tool {connector}.{want['action']}")
                touched = True
            elif rows[at] != want:
                # Keep anything the live row carries that the script doesn't know
                # (shapes moved into a row in Sep 2026, for one).
                merged = {**rows[at], **want}
                if want.get("consolidator_config"):
                    merged["consolidator_config"] = {
                        **(rows[at].get("consolidator_config") or {}),
                        **want["consolidator_config"],
                    }
                if merged != rows[at]:
                    rows[at] = merged
                    changes.append(f"updated tool {connector}.{want['action']}")
                    touched = True
        for action in READ_ONLY_FIXES.get(connector, []):
            at = by_action.get(action)
            if at is not None and not rows[at].get("read_only"):
                rows[at] = {**rows[at], "read_only": True}
                changes.append(
                    f"marked {connector}.{action} read-only (nested reads need it)"
                )
                touched = True
        if touched:
            spec.tools = rows
            flag_modified(spec, "tools")


def _sync_claims(db, changes):
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import MarketplaceApp

    for slug, tools in APP_CLAIMS.items():
        app = db.query(MarketplaceApp).filter(MarketplaceApp.slug == slug).first()
        if app is None:
            raise SystemExit(f"App {slug} not found in the catalog")
        comp = dict(app.composition or {})
        have = list(comp.get("tools") or [])
        missing = [t for t in tools if t not in have]
        if missing:
            comp["tools"] = have + missing
            app.composition = comp
            flag_modified(app, "composition")
            changes.append(f"App {slug} claims {', '.join(missing)}")


def _sync_templates(db, changes):
    from app.db.config_models import DashboardTemplate
    from app.db.dashboard_templates import get_template

    for slug in TEMPLATE_SLUGS:
        wanted = get_template(slug)
        if not wanted:
            raise SystemExit(f"{slug} is not in app.db.dashboard_templates")
        row = db.query(DashboardTemplate).filter(DashboardTemplate.slug == slug).first()
        fields = {
            "agent_slug": wanted["agent_slug"],
            "title": wanted["title"],
            "description": wanted.get("description", ""),
            "charts": wanted["charts"],
            "enabled": True,
        }
        if row is None:
            db.add(DashboardTemplate(slug=slug, **fields))
            changes.append(f"added dashboard template {slug}")
            continue
        differs = {k for k, v in fields.items() if getattr(row, k) != v}
        if differs:
            for k in differs:
                setattr(row, k, fields[k])
            changes.append(
                f"updated dashboard template {slug} ({', '.join(sorted(differs))})"
            )


def main(dry_run: bool = False) -> None:
    from app.db.engine import _ConfigSessionLocal
    from app.services import config_validator

    db = _ConfigSessionLocal()
    try:
        changes: list[str] = []
        _sync_tools(db, changes)
        _sync_claims(db, changes)
        _sync_templates(db, changes)
        if not changes:
            print("Already in sync.")
            db.rollback()
            return
        db.flush()
        report = config_validator.validate_config(db=None, config_db=db)
        if not report.get("ok", False):
            db.rollback()
            print(
                "validate_config found issues on the pending state — nothing applied:"
            )
            for issue in report.get("issues") or []:
                print("  -", issue)
            raise SystemExit(1)
        verb = "Would apply" if dry_run else "Applied"
        if dry_run:
            db.rollback()
        else:
            db.commit()
        for line in changes:
            print(f"{verb}: {line}")
        print("validate_config: ok")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(dry_run=parser.parse_args().dry_run)
