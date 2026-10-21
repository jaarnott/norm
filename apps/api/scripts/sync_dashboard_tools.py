"""Install Norm's dashboard tools in config: the rows, Norm Core's claim, and
what Norm is told about dashboards.

Norm had no way to make a dashboard, so asked for one it built an App (Rich,
20 Oct 2026). docs/plans/norm-builds-dashboards.md, Stage 2. The handlers are
in app/agents/dashboard_tools.py.

Config edits, in one transaction, backed up first; each only when missing:
- ``norm_reports.get_dashboards`` (read) and ``norm_reports.manage_dashboard``
  (write: asks only for a dashboard shared with the organisation);
- Norm Core claims both (exposure is the App Map). The catalog seed lists them
  too, so a full catalog sync keeps them;
- a "## Dashboards" section in Norm's prompt (the base row);
- a note at the top of the build_an_app skill: a dashboard is not an App.

**Run AFTER the code with the handlers is deployed** — a claimed tool with no
handler fails when called. The config DB is shared by every environment.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_dashboard_tools.py --dry-run
    uv run python scripts/sync_dashboard_tools.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.sync_approval_labels import WRITES  # noqa: E402
from scripts.tool_row_updates import BACKUP_DIR, apply_row_updates  # noqa: E402

from app.agents.dashboard_tools import AGENTS, HEIGHTS, KINDS, OPS, WIDTHS  # noqa: E402

CONNECTOR = "norm_reports"
APP = "norm-core"
PERIODS = [
    "today",
    "yesterday",
    "last_7_days",
    "this_week",
    "last_week",
    "last_13_weeks",
    "this_month",
    "last_month",
    "last_12_months",
]

TILE_SCHEMA = {
    "type": "object",
    "description": (
        "A tile, in Norm's terms. Every tile: title, kind. A tool tile: tool "
        "(connector.action of a reading tool), inputs (its parameters; a tool "
        "that takes `period` needs one, in plain English: the dashboard's "
        "period control then replaces it with the period picked on the page), "
        "list (where the rows are in the tool's answer, "
        "e.g. rows, summary, by_venue). number: value (+ format, compare, "
        "change, note). bar/line/stacked_bar/pie: x and y (+ format, "
        "horizontal, by_venue). table: columns (+ labels, formats). text: "
        "text. app: app (the App's slug). Place it with width and height."
    ),
    "properties": {
        "title": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "tool": {"type": "string", "description": "connector.action"},
        "inputs": {"type": "object"},
        "list": {"type": "string"},
        "x": {"type": "string"},
        "y": {"type": "array", "items": {"type": "string"}},
        "value": {"type": "string"},
        "compare": {"type": "string"},
        "change": {"type": "string"},
        "note": {"type": "string"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "object"},
        "formats": {"type": "object"},
        "format": {"type": "string", "enum": ["currency", "percent", "number"]},
        "horizontal": {"type": "boolean"},
        "by_venue": {"type": "boolean"},
        "text": {"type": "string"},
        "app": {"type": "string"},
        "width": {"type": "string", "enum": list(WIDTHS)},
        "height": {"type": "string", "enum": list(HEIGHTS)},
        "refresh_minutes": {"type": "number"},
    },
}

ROWS = [
    {
        "action": "get_dashboards",
        "method": "GET",
        "read_only": True,
        "description": (
            "Norm's dashboards: the page of tiles (numbers, charts, tables) "
            "each AI agent has. Without dashboard_id: the dashboards this "
            "person can see here, which they use, and the ready-made "
            "templates. With dashboard_id: that dashboard's tiles (tool, "
            "inputs, fields, where each sits) and the period it opens on, and "
            "it shows the dashboard to the person. Read a dashboard before "
            "changing it, and again after, to check it and show it."
        ),
        "required_fields": [],
        "optional_fields": ["agent", "dashboard_id", "history"],
        "field_descriptions": {
            "agent": "Only this AI agent's dashboards.",
            "dashboard_id": "Describe (and show) this dashboard.",
            "history": "With dashboard_id: also list its versions.",
        },
        "field_schema": {
            "agent": {"type": "string", "enum": list(AGENTS)},
            "dashboard_id": {"type": "string"},
            "history": {"type": "boolean"},
        },
        "display_component": "dashboard_view",
        "suppress_display_early_exit": True,
        "effect": "read",
    },
    {
        "action": "manage_dashboard",
        "method": "POST",
        "read_only": False,
        "description": (
            "Make and change Norm dashboards. op: create (a new dashboard for "
            "an AI agent, blank or from a template; it becomes the one they "
            "see for that agent), add_tile, change_tile, move_tile, "
            "remove_tile, rename, set_period (the period it opens on), share / "
            "unshare (with the organisation), copy (their own copy of a "
            "dashboard they can see), use (make it the one they see for its "
            "agent), restore (bring back an earlier version). A change to a "
            "dashboard only its author sees happens at once; a change to a "
            "shared one shows the person a card first. Every tile is tried "
            "for the person's venues before it is saved: call its tool "
            "yourself first so you know the field names, and if a tile is "
            "refused, fix what the refusal names. Place tiles with width and "
            "after (a tile_id); 'three columns' is three tiles a third wide, "
            "one after another."
        ),
        "required_fields": ["op"],
        "optional_fields": [
            "dashboard_id",
            "agent",
            "template",
            "title",
            "description",
            "make_it_mine",
            "tile",
            "tile_id",
            "after",
            "top",
            "width",
            "height",
            "period",
            "version",
            "allow_empty",
        ],
        "field_descriptions": {
            "op": "What to do.",
            "dashboard_id": "The dashboard (every op but create).",
            "agent": "create: the AI agent whose Dashboard page it is for.",
            "template": "create: start from this template (get_dashboards lists them).",
            "title": "create / rename: the dashboard's title.",
            "description": "create / rename: one line on what it is for.",
            "make_it_mine": "create / copy: make it the one they see for its agent (default yes).",
            "tile": "add_tile: the tile; change_tile: only what changes.",
            "tile_id": "change_tile / move_tile / remove_tile: which tile.",
            "after": "add_tile / move_tile: place it after this tile (beside it if there's room, else under its row). Default: after the last tile.",
            "top": "add_tile / move_tile: put it in a new row at the top.",
            "width": "move_tile: new width.",
            "height": "move_tile: new height.",
            "period": "set_period: the period the dashboard opens on.",
            "version": "restore: the version to bring back.",
            "allow_empty": "add_tile / change_tile: save it even though the tool has no rows right now (no sales yet today).",
        },
        "field_schema": {
            "op": {"type": "string", "enum": list(OPS)},
            "dashboard_id": {"type": "string"},
            "agent": {"type": "string", "enum": list(AGENTS)},
            "template": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "make_it_mine": {"type": "boolean"},
            "tile": TILE_SCHEMA,
            "tile_id": {"type": "string"},
            "after": {"type": "string"},
            "top": {"type": "boolean"},
            "width": {"type": "string", "enum": list(WIDTHS)},
            "height": {"type": "string", "enum": list(HEIGHTS)},
            "period": {"type": "string", "enum": PERIODS},
            "version": {"type": "integer"},
            "allow_empty": {"type": "boolean"},
        },
        "effect": "write",
        "approval": WRITES[f"{CONNECTOR}.manage_dashboard"],
    },
]

PROMPT_MARKER = "## Dashboards"
PROMPT_SECTION = """

## Dashboards
- A dashboard is a Norm page of tiles (numbers, charts, tables) for one AI agent, built from your reading tools. When someone asks for a dashboard, or to change "this dashboard", build or change a dashboard with `manage_dashboard` — never an App instead.
- Read it first with `get_dashboards`, then change it a tile at a time. Each change returns the dashboard as it now stands. To show it to the person, read it again once your changes have returned: a read sent in the same turn as a change sees it as it was before.
- Before adding a tile, call its tool yourself once so you know the field names. Every tile is tried before it is saved; if one is refused, fix what the refusal names.
- Lay tiles out in words: a width (full, half, third, quarter…) and the tile it goes after. "Three columns" is three tiles a third wide, one after another.
- A tile whose tool takes a period follows the dashboard's period control: its `period` input is only where it starts, and `set_period` sets the period the dashboard opens on.
- When part of a dashboard needs what tiles can't do — a figure worked out from several tools (gross profit = sales − wages − purchases), notes people type, buttons — build the tiles, make only that part as a small App, and add it as an app tile; say you did. Fill a whole dashboard with one App only when the person asks for exactly that.
- A shared dashboard can be changed only by the person who shared it; anyone else copies it (op copy) and changes the copy."""

SKILL_MARKER = "## A dashboard is not an App"
SKILL_NOTE = """## A dashboard is not an App
When someone asks for a dashboard, build it with the dashboard tools (`get_dashboards`, `manage_dashboard`), not as an App. An App is for what tiles can't do: figures worked out across several tools, custom layouts, notes or buttons. Built for a dashboard, such an App goes on it as one tile.

"""

#: An App open on the person's screen shows a saved version by itself
#: (web lib/liveChanges); Norm kept telling Rich to refresh (21 Oct 2026).
SAVE_MARKER = "## After you save"
SAVE_NOTE = """## After you save
An App the person has open, on its page or in another tab, shows the new version by itself as soon as you save. Don't tell them to refresh or reopen it.

"""


def also(db, dry_run: bool) -> list[str]:
    import datetime as dt
    import json

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import (
        AgentConfig,
        ConnectionSpec,
        MarketplaceApp,
        Playbook,
    )

    lines = []
    spec = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .with_for_update()
        .one()
    )
    app = (
        db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug == APP)
        .with_for_update()
        .one()
    )
    base = db.query(AgentConfig).filter(AgentConfig.agent_slug == "base").one()
    skill = db.query(Playbook).filter(Playbook.slug == "build_an_app").first()
    backup = {
        "norm_reports_tools": spec.tools,
        "app": app.composition,
        "base_prompt": base.system_prompt,
        "build_an_app": skill.instructions if skill else None,
    }

    tools = [dict(t) for t in spec.tools or []]
    changed = False
    for row in ROWS:
        live = spec_rows.find_tool(spec, row["action"])
        if live is None:
            lines.append(f"{CONNECTOR}.{row['action']}: add tool row")
            tools.append(row)
            changed = True
        elif {**live, **row} != live:
            # Keys this script doesn't define (set by another installer) stay.
            lines.append(f"{CONNECTOR}.{row['action']}: update tool row")
            tools = [
                {**t, **row} if t.get("action") == row["action"] else t for t in tools
            ]
            changed = True
    if changed and not dry_run:
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1

    comp = dict(app.composition or {})
    claimed = list(comp.get("tools") or [])
    for row in ROWS:
        key = f"{CONNECTOR}.{row['action']}"
        if key not in claimed:
            lines.append(f"app {APP}: claim {key}")
            claimed.append(key)
    if claimed != list(comp.get("tools") or []) and not dry_run:
        comp["tools"] = claimed
        app.composition = comp
        flag_modified(app, "composition")

    prompt = base.system_prompt or ""
    if PROMPT_SECTION.strip() not in prompt:
        if PROMPT_MARKER in prompt:
            # Replace the section as installed (it runs to the next heading).
            head, _, rest = prompt.partition(PROMPT_MARKER)
            _, nxt, tail = rest.partition("\n## ")
            prompt = head.rstrip() + (("\n\n## " + tail) if nxt else "")
            lines.append("Norm's prompt: update the Dashboards section")
        else:
            lines.append("Norm's prompt: add the Dashboards section")
        if not dry_run:
            base.system_prompt = prompt.rstrip() + PROMPT_SECTION
    if skill is not None and SKILL_MARKER not in (skill.instructions or ""):
        lines.append("build_an_app skill: a dashboard is not an App")
        if not dry_run:
            skill.instructions = SKILL_NOTE + (skill.instructions or "")
    if skill is not None and SAVE_MARKER not in (skill.instructions or ""):
        lines.append("build_an_app skill: an open App updates itself")
        if not dry_run:
            skill.instructions = SAVE_NOTE + (skill.instructions or "")

    if lines and not dry_run:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"dashboard-tools-config-{stamp}.json"
        path.write_text(json.dumps(backup, indent=1, default=str))
        print(f"backup {path}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates([], dry_run=args.dry_run, label="dashboard-tools", also=also)


if __name__ == "__main__":
    main()
