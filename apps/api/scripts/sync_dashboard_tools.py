"""Install what Norm is told and given for dashboards as pages (Oct 2026).

A dashboard is now a page of code, an app of kind ``dashboard`` (docs/plans/
apps-and-dashboards-one-system.md). Norm builds and changes it with
``save_app``; ``get_dashboards`` / ``manage_dashboard`` (app/agents/
dashboard_tools.py) cover which dashboard each Dashboard page shows — use,
copy, share, bring back a version, remove. The tile ops are gone.

Config edits, in one transaction, backed up first; each only when it differs:
- the ``norm_reports.get_dashboards`` / ``manage_dashboard`` rows, and the
  ``norm.save_app`` / ``norm.get_app`` rows (kind, agent, edits, change_kind);
- Norm Core keeps claiming the two dashboard tools;
- the "## Dashboards and apps" section of Norm's prompt (the base row),
  replacing the tile-era "## Dashboards";
- the ``build_a_dashboard`` skill (new) on the App Builder App, beside
  ``build_an_app``;
- in ``build_an_app``: "Dashboard or app" replaces "A dashboard is not an
  App", plus "Small changes: send edits" and "Each person's own settings".

**Run AFTER the code is deployed** — the rows describe fields only the new
handlers understand. The config DB is shared by every environment.

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

from app.agents.dashboard_tools import AGENTS, OPS  # noqa: E402

CONNECTOR = "norm_reports"
APP = "norm-core"
BUILDER_APP = "app-builder"

ROWS = [
    {
        "action": "get_dashboards",
        "method": "GET",
        "read_only": True,
        "description": (
            "Each AI agent's Dashboard page: which dashboard it shows this "
            "person, and what else they can pick — their own, those shared "
            "with their organisation, and Norm's templates — with who owns "
            "each and whether they can change it (can_change). Given a ref "
            "(app:<slug> or template:<slug>), shows that dashboard in the "
            "conversation. A dashboard is a page built with save_app (kind "
            "'dashboard'); read its code with get_app."
        ),
        "required_fields": [],
        "optional_fields": ["agent", "ref"],
        "field_descriptions": {
            "agent": "Only this AI agent's Dashboard page.",
            "ref": "Show this dashboard: app:<slug> or template:<slug>.",
        },
        "field_schema": {
            "agent": {"type": "string", "enum": list(AGENTS)},
            "ref": {"type": "string"},
        },
        "display_component": "app_runner",
        "suppress_display_early_exit": True,
        "effect": "read",
    },
    {
        "action": "manage_dashboard",
        "method": "POST",
        "read_only": False,
        "description": (
            "What a person does with dashboards (building or changing one is "
            "save_app, kind 'dashboard'). op: use (show this dashboard on its "
            "AI agent's Dashboard page, as it is — no copy, so they get its "
            "updates), copy (their own copy, to change; it's then what that "
            "page shows), share / unshare (their own dashboard, with the whole "
            "organisation, to use as it is), restore (bring back an earlier "
            "version of their own), remove (their own). Only a dashboard's "
            "owner changes it: to change a template or someone else's shared "
            "dashboard, ask the person whether they'd like a copy first. A "
            "change to a shared dashboard shows the person a card first."
        ),
        "required_fields": ["op"],
        "optional_fields": ["ref", "agent", "name", "version", "approve_writes"],
        "field_descriptions": {
            "op": "What to do.",
            "ref": "The dashboard: app:<slug> (the organisation's) or template:<slug> (Norm's).",
            "agent": "use: the AI agent whose Dashboard page shows it.",
            "name": "copy: a name for their copy (default: the same name).",
            "version": "restore: the version to bring back.",
            "approve_writes": "share: let everyone using it record things through it.",
        },
        "field_schema": {
            "op": {"type": "string", "enum": list(OPS)},
            "ref": {"type": "string"},
            "agent": {"type": "string", "enum": list(AGENTS)},
            "name": {"type": "string"},
            "version": {"type": "integer"},
            "approve_writes": {"type": "boolean"},
        },
        "effect": "write",
        "approval": WRITES[f"{CONNECTOR}.manage_dashboard"],
    },
]

EDITS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "find": {
                "type": "string",
                "description": "exact text, unique in the current page",
            },
            "replace": {"type": "string"},
        },
        "required": ["find", "replace"],
    },
}

#: Merged into the live norm.* rows (keys other installers set stay).
NORM_ROWS = {
    "save_app": {
        "description": (
            "Save a dashboard or an app. A new one: name, slug, kind "
            "('dashboard' or 'app'), agent, spec, ui_source (+ icon, "
            "description, purpose, logic_source?, changelog). A change to "
            "an existing one: its slug plus only what changes — for part of "
            "the page send edits [{find, replace}] (and logic_edits), not the "
            "whole ui_source; name, spec and page carry over. Every save is a "
            "new version. Kind changes only with change_kind: true, when the "
            "person asks. A dashboard can be changed only by its owner."
        ),
        "required_fields": ["slug"],
        "field_descriptions": {
            "kind": (
                "'dashboard' (sits on the AI agent's Dashboard page) or 'app' "
                "(its own page in the AI agent's menu) — required for a new one"
            ),
            "agent": (
                "the AI agent whose job it does, never app_builder — required "
                "for a new one"
            ),
            "change_kind": "true only when the person asked to turn it into the other kind",
            "edits": "small changes to ui_source: [{find, replace}], each find exactly once in the current page",
            "logic_edits": "small changes to logic_source, the same way",
        },
        "field_schema": {
            "kind": {"type": "string", "enum": ["dashboard", "app"]},
            "change_kind": {"type": "boolean"},
            "edits": EDITS_SCHEMA,
            "logic_edits": EDITS_SCHEMA,
        },
    },
    "get_app": {
        "description": (
            "Read a dashboard or app (spec, ui_source, logic_source) before "
            "changing it: its kind, its AI agent, who owns it, and whether "
            "this person can change it (can_change, and why_not when they "
            "can't). Takes: slug, or template:<slug> for one of Norm's "
            "templates (read-only)."
        ),
    },
}

PROMPT_MARKER = "## Dashboards"
PROMPT_SECTION = """

## Dashboards and apps
- You build pages of two kinds. A **dashboard** sits on an AI agent's Dashboard page: figures to look at. An **app** is its own page in an AI agent's menu: something people work through. Both are built with `save_app`; open the `build_a_dashboard` skill for a dashboard and `build_an_app` for an app.
- Which kind: the person's word, "dashboard" or "app". If they said neither, ask one question: where should it sit ("on your Reports dashboard, or as its own app in the Reports menu?"). If they don't mind, figures to look at are a dashboard and a form, checklist or report people fill in is an app. Say which you're building before you start, and where to find it when you're done.
- Each Dashboard page shows the person's choice: their own dashboard, one shared with their organisation, or one of Norm's templates. Using one never copies it, so they get its updates. `get_dashboards` says what each page shows and what else they can pick; `manage_dashboard` uses, copies, shares, brings back a version, or removes one.
- Only a dashboard's owner changes it. Asked to change a template or someone else's shared dashboard, say the one they're using can't be changed and ask whether they'd like their own copy (`manage_dashboard` op `copy`). Change nothing until they say yes; then change the copy.
- To change a page they can change, read it with `get_app`, then save with `edits` (find → replace) for small changes rather than the whole page."""

SKILL_SLUG = "build_a_dashboard"
SKILL_NAME = "Build a dashboard"
SKILL_DESCRIPTION = (
    "What makes a great dashboard: layout, tables, numbers, controls, states; "
    "and the use-or-copy rules for dashboards people share."
)
SKILL = """## A dashboard
A dashboard is a page of figures on an AI agent's Dashboard page: `save_app` with kind `dashboard`. It's built exactly like an app: the same spec, page rules, styles, storage and hand-editing rules as the `build_an_app` skill, so open that one too for the technical rules. This skill is what makes a great one.

## Before you build
- Ask at most one or two questions: who looks at it, how often, and which decisions it helps with. Otherwise build a first version and refine.
- Call every tool you'll use once, with real inputs, and read the real fields. Never write against field names from memory.
- Sanity-check every figure against the tool's own answer before saving. A wage percentage over 100%, or food plus beverage not adding up to the total, means the sums are wrong: fix them or say why.

## Layout
- Top row: the 3–5 numbers that matter most, as big figures, each with its comparison (vs budget, last week or last year) in a small line below, coloured for good or bad.
- Then the detail, in columns: two across on a computer (three only when every table in the row is narrow), one on a phone. The table, and the chart of the same figures directly under it.
- Most important top-left. Group related things. Leave white space.
- Lay it out in rows, columns and sections (build_an_app, "Pages people can edit by hand"), so people can move and resize sections by hand. A row is one line: four figures in two pairs are two rows.
- It must work on a phone.

## Tables
- Short headers: one or two words.
- No explanation text on the page: anything that needs explaining goes in a small info icon that shows it on hover.
- Columns in the same order everywhere (Food, Beverage, Total, for example).
- Same units and the same pattern of rows in every table: money, then hours, then percentages.
- Right-align numbers. Show variance rows only when asked.

## Numbers
- Money without GST unless asked; say so once, in an info icon.
- Percentages to one decimal place, money to whole dollars on summaries.
- Where a figure can be read as % or $, show the main one big and the other small underneath. Offer one quiet % / $ switch for the whole page if asked.

## Controls
- Period tabs at the top (today, this week, last week, this month, last month), passed to tools in plain English.
- The venue comes from Norm's venue picker; use it, don't build another. To compare venues side by side (sales by venue), call the tool once for each of the person's venues (`ctx.venues`, passing `venue_id`); the picker still decides the venue the rest of the page is about.
- Keep controls quiet: small, grey, out of the way.

## Look
- Use Norm's own styles (cards, tables, tabs).
- Default colours from Norm's palette. If people want to choose, offer a short palette (about 20 colours) and save each person's choice with `norm.mine`, never in storage: one person's colours must never change anyone else's screen.

## States
- Show a loading state while figures load.
- When there's no data, say why ("no sales yet today") rather than showing a blank or a zero.
- When a tool fails, say which figure is missing, and keep the rest of the page working.

## Using, sharing and copying
- A person uses any dashboard they can see as it is: their own, one shared with their organisation, or one of Norm's templates. Using one never copies it, so they get its updates.
- Only its owner changes a dashboard. Asked to change a template or someone else's shared dashboard, say the one they're using can't be changed and ask whether they'd like their own copy (`manage_dashboard` op `copy`). Change nothing until they say yes; then change the copy.
- Sharing (`manage_dashboard` op `share`) lets everyone in the organisation use it as it is; only the person who shared it changes it.

## Changing it
- Read it with `get_app` first. Change only what was asked, and keep everything else exactly as it is, including what people changed by hand.
- Send `edits` for small changes, not the whole page.
- Say what changed, in a sentence or a short list.
- The page shows the new version by itself as soon as you save. Don't tell them to refresh.
"""

#: build_an_app: replaced, then added (each section runs to the next "## ").
OLD_SKILL_NOTE = "## A dashboard is not an App"
KIND_MARKER = "## Dashboard or app"
KIND_NOTE = """## Dashboard or app
Every new page names its `kind` and its `agent` in `save_app`: the AI agent whose job it does, never app_builder (a page that fits no agent goes under the nearest).
- kind `dashboard` sits on that AI agent's Dashboard page: follow the `build_a_dashboard` skill too. Kind `app` is its own page in that AI agent's menu: this skill.
- The person's word decides: "dashboard" or "app". If they said neither, ask one question: where should it sit? If they don't mind, figures to look at are a dashboard and something people work through (a form, a checklist, a report they fill in) is an app. Say which you're building before you start.
- Changing a page's kind is a step of its own, only when the person asks ("make this a dashboard"): save with `change_kind: true`. Turning an app into a dashboard takes it off the marketplace.

"""
EDITS_MARKER = "## Small changes: send edits"
EDITS_NOTE = """## Small changes: send edits
To change part of a page, read it with `get_app`, then save with `edits`: [{"find": "<exact text from the current page>", "replace": "<the new text>"}] instead of the whole `ui_source` (`logic_edits` for `logic_source`). Each `find` must appear exactly once, so include enough around it. Leave out what you don't change: the name, spec and page carry over. Send the whole `ui_source` only for a redesign.

"""
MINE_MARKER = "## Each person's own settings"
MINE_NOTE = """## Each person's own settings
`window.norm.mine.get(name)` and `window.norm.mine.set(name, value)` (Promises) keep one person's own choices on a page: colours, % or $, the last period. Nobody else's screen changes. Use them for preferences; use storage for what people record for everyone.

"""
#: The note an open page needs, now true of dashboards too.
SAVE_OLD = "An App the person has open, on its page or in another tab, shows"
SAVE_NEW = (
    "A dashboard or app the person has open, on its page or in another tab, shows"
)


def _drop_section(text: str, marker: str) -> str:
    if marker not in text:
        return text
    head, _, rest = text.partition(marker)
    _, nxt, tail = rest.partition("\n## ")
    return head + (("## " + tail) if nxt else "")


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
    norm_spec = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == "norm")
        .with_for_update()
        .one()
    )
    core = (
        db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug == APP)
        .with_for_update()
        .one()
    )
    builder = (
        db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug == BUILDER_APP)
        .with_for_update()
        .one()
    )
    base = db.query(AgentConfig).filter(AgentConfig.agent_slug == "base").one()
    skill = db.query(Playbook).filter(Playbook.slug == "build_an_app").first()
    new_skill = db.query(Playbook).filter(Playbook.slug == SKILL_SLUG).first()
    backup = {
        "norm_reports_tools": spec.tools,
        "norm_tools": [spec_rows.find_tool(norm_spec, a) for a in NORM_ROWS],
        "core": core.composition,
        "builder": builder.composition,
        "base_prompt": base.system_prompt,
        "build_an_app": skill.instructions if skill else None,
        SKILL_SLUG: new_skill.instructions if new_skill else None,
    }

    # The dashboard tools: replaced whole (their tile fields are gone).
    tools = [dict(t) for t in spec.tools or []]
    changed = False
    for row in ROWS:
        live = spec_rows.find_tool(spec, row["action"])
        if live is None:
            lines.append(f"{CONNECTOR}.{row['action']}: add tool row")
            tools.append(row)
            changed = True
        elif live != row:
            lines.append(f"{CONNECTOR}.{row['action']}: replace tool row")
            tools = [row if t.get("action") == row["action"] else t for t in tools]
            changed = True
    if changed and not dry_run:
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1

    # save_app / get_app: merged (other installers' keys stay).
    ntools = [dict(t) for t in norm_spec.tools or []]
    nchanged = False
    for action, want in NORM_ROWS.items():
        live = spec_rows.find_tool(norm_spec, action)
        if live is None:
            raise SystemExit(f"norm.{action} has no tool row")
        merged = dict(live)
        for key, value in want.items():
            if isinstance(value, dict) and isinstance(live.get(key), dict):
                merged[key] = {**live[key], **value}
            else:
                merged[key] = value
        if merged != live:
            lines.append(f"norm.{action}: update tool row")
            ntools = [merged if t.get("action") == action else t for t in ntools]
            nchanged = True
    if nchanged and not dry_run:
        norm_spec.tools = ntools
        flag_modified(norm_spec, "tools")
        norm_spec.version = (norm_spec.version or 0) + 1

    comp = dict(core.composition or {})
    claimed = list(comp.get("tools") or [])
    for row in ROWS:
        key = f"{CONNECTOR}.{row['action']}"
        if key not in claimed:
            lines.append(f"app {APP}: claim {key}")
            claimed.append(key)
    if claimed != list(comp.get("tools") or []) and not dry_run:
        comp["tools"] = claimed
        core.composition = comp
        flag_modified(core, "composition")

    bcomp = dict(builder.composition or {})
    skills = list(bcomp.get("skills") or [])
    note = (
        "To build or change a dashboard, open the build_a_dashboard skill "
        "first; an app, the build_an_app skill."
    )
    if SKILL_SLUG not in skills or bcomp.get("note") != note:
        lines.append(f"app {BUILDER_APP}: offer the {SKILL_SLUG} skill")
        if not dry_run:
            if SKILL_SLUG not in skills:
                skills.append(SKILL_SLUG)
            bcomp["skills"] = skills
            bcomp["note"] = note
            builder.composition = bcomp
            flag_modified(builder, "composition")

    prompt = base.system_prompt or ""
    if PROMPT_SECTION.strip() not in prompt:
        if PROMPT_MARKER in prompt:
            # Replace the section as installed (it runs to the next heading).
            head, _, rest = prompt.partition(PROMPT_MARKER)
            _, nxt, tail = rest.partition("\n## ")
            prompt = head.rstrip() + (("\n\n## " + tail) if nxt else "")
            lines.append("Norm's prompt: replace the Dashboards section")
        else:
            lines.append("Norm's prompt: add the Dashboards and apps section")
        if not dry_run:
            base.system_prompt = prompt.rstrip() + PROMPT_SECTION

    if new_skill is None:
        lines.append(f"skill {SKILL_SLUG}: add")
        if not dry_run:
            db.add(
                Playbook(
                    slug=SKILL_SLUG,
                    agent_slug="app_builder",
                    display_name=SKILL_NAME,
                    description=SKILL_DESCRIPTION,
                    instructions=SKILL,
                    enabled=True,
                )
            )
    elif new_skill.instructions != SKILL:
        lines.append(f"skill {SKILL_SLUG}: update")
        if not dry_run:
            new_skill.instructions = SKILL
            new_skill.description = SKILL_DESCRIPTION

    if skill is not None:
        text = skill.instructions or ""
        before = text
        text = _drop_section(text, OLD_SKILL_NOTE)
        for marker, note_text in (
            (MINE_MARKER, MINE_NOTE),
            (EDITS_MARKER, EDITS_NOTE),
            (KIND_MARKER, KIND_NOTE),
        ):
            if note_text.strip() not in text:
                text = note_text + _drop_section(text, marker).lstrip()
        text = text.replace(SAVE_OLD, SAVE_NEW)
        if text != before:
            lines.append("build_an_app skill: dashboard or app, edits, own settings")
            if not dry_run:
                skill.instructions = text

    if lines and not dry_run:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"dashboard-pages-config-{stamp}.json"
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
