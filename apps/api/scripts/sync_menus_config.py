"""Install get_menus + manage_menu; the last raw families leave the agent menu.

Sep 2026, the tail of the "no raw endpoints as tools" work. After this the
LoadedHub agent surface is consolidators, one working report
(generate_stocktake_report) and internal handlers.

What goes behind a consolidator:

- the five menu rows — list_menus (every line of every menu: ~10,600 tokens
  for La Zeppa's eight menus, the model then paged it with search_tool_result),
  get_menu, create_menu, update_menu, delete_menu (the writes asked the model to
  echo the whole MenuModel around one price change). get_menus reads;
  manage_menu(op=create|update|delete) writes with deltas and re-reads to
  prove added lines landed.

What is simply demoted (engine_only; nothing on the agent surface replaces it):

- the five roster shift writes — create_rostered_shift, update_shift,
  delete_rostered_shift, add_shift, delete_shift. No agent has called one in
  90 days; the roster editor makes every shift change through its working
  document, which dispatches add_shift / update_shift / delete_shift BY NAME
  on this spec (document_sync -> _execute_tool_call), so the rows stay and
  keep working — demotion never touches execution.
- get_user_session — two calls ever, both in March.

Bindings: the chef swaps the five menu rows for the two new tools; every
other binding loses its dead entries (time_attendance's roster writes were
its only enabled raws). Prompts: the chef's Menus paragraph names the tools;
time_attendance's "exception for shift writes" sentence goes — the agent no
longer holds them.

Replay hazards fixed in the same commit: sync_menu_actions.py and
sync_roster_write_fix.py preserve engine_only; sync_executive_chef_agent.py
lists the new tools; consolidator_coverage._SUPERSEDES maps the eleven.

Usage:
    uv run python scripts/sync_menus_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CHEF = "executive_chef"

READ_TOOL = {
    "action": "get_menus",
    "method": "GET",  # read-only consolidator: auto-executes
    "read_only": True,
    "description": (
        "THE menu lookup. No arguments: every live menu as {id, name, sections, "
        "lines}. query: find a dish (or menu) by name across all menus — returns "
        "each hit with its menu, section and price. menu_id: one menu — detail "
        "'summary' (sections with dish, price and whether it points at a recipe "
        "or a stock item) or 'full' (Loaded's raw MenuModel). Never read every "
        "menu in full to answer a question about one dish."
    ),
    "required_fields": [],
    "optional_fields": ["menu_id", "query", "detail", "limit"],
    "field_descriptions": {
        "menu_id": "The menu's id (from the list) — returns exactly this menu.",
        "query": "Case-insensitive substring of a dish or menu name.",
        "detail": "menu_id: 'summary' (default) or 'full' (the raw MenuModel).",
        "limit": "Max rows (default 50).",
    },
    "max_result_chars": 40_000,
    "consolidator_config": {"max_api_calls": 2, "allowed_write_actions": []},
}

WRITE_TOOL = {
    "action": "manage_menu",
    # PUT keeps the agent loop's human-approval gate; the consolidator's merge
    # decides what is actually written.
    "method": "PUT",
    "read_only": False,
    # Tightened 7 Oct 2026: each rule stated once (the tool list is ~65% of
    # every prompt).
    "description": (
        "Create or change a Loaded menu — a write, human-approved. op 'create': `menu` "
        "{name, sections [{name, lines [{name, price, recipe_id | stock_item_id}]}]}. "
        "op 'update': menu_id plus ONLY the deltas — `changes` {name}, `line_changes`, "
        "`add_lines` (a missing section is created), `remove_lines`, "
        "`remove_sections`; Norm merges, writes the whole menu and re-reads to confirm "
        "added lines — never resend it. A dish on more than one section needs "
        "`section`. Prices are numbers (0 if free; '$12.50' is read) — anything else "
        "is refused, never saved as $0. op 'delete': menu_id AND the menu's name, "
        "checked first. Look menus up with get_menus."
    ),
    "required_fields": ["op"],
    "optional_fields": [
        "menu",
        "menu_id",
        "name",
        "changes",
        "line_changes",
        "add_lines",
        "remove_lines",
        "remove_sections",
    ],
    "field_descriptions": {
        "op": "create | update | delete. No default.",
        "menu": "create: the menu to create.",
        "menu_id": "update / delete: the menu's id (from get_menus).",
        "name": "delete: the menu's name (from get_menus), checked against the id.",
        "changes": "update: {name}.",
        "line_changes": (
            "update: [{line_id | name (+ section if the name is on several sections), "
            "price, new_name, move_to}]."
        ),
        "add_lines": (
            "update: [{section, name, price, recipe_id | stock_item_id}] — ids from "
            "get_recipes / get_stock."
        ),
        "remove_lines": "update: line ids or names.",
        "remove_sections": "update: section names, removed with their lines.",
    },
    "field_schema": {
        "op": {"type": "string", "enum": ["create", "update", "delete"]},
        "menu": {"type": "object"},
        "changes": {"type": "object"},
        "line_changes": {"type": "array", "items": {"type": "object"}},
        "add_lines": {"type": "array", "items": {"type": "object"}},
        "remove_lines": {"type": "array", "items": {"type": "string"}},
        "remove_sections": {"type": "array", "items": {"type": "string"}},
    },
    "consolidator_config": {
        # create 1; update = read + PUT + re-read; delete 1.
        "max_api_calls": 3,
        "allowed_write_actions": ["create_menu", "update_menu", "delete_menu"],
    },
}

#: Retiring row -> (its replacement on the agent surface, or None if the row
#: is engine plumbing with no agent-facing successor).
DEMOTE: dict[str, str | None] = {
    "list_menus": "get_menus",
    "get_menu": "get_menus (menu_id)",
    "create_menu": "manage_menu (op 'create')",
    "update_menu": "manage_menu (op 'update')",
    "delete_menu": "manage_menu (op 'delete')",
    "create_rostered_shift": None,
    "update_shift": None,
    "delete_rostered_shift": None,
    "add_shift": None,
    "delete_shift": None,
    "get_user_session": None,
}
_ROSTER_NOTE = (
    "[engine-only] Shift changes are made in the roster editor (show_roster), "
    "whose working document dispatches this row by name; no agent calls it. "
)


def demoted_prefix(action: str) -> str:
    to = DEMOTE[action]
    if to:
        return f"[consolidator-only] Superseded by {to}. "
    if action == "get_user_session":
        return "[engine-only] Not on any agent surface (two calls ever, Mar 2026). "
    return _ROSTER_NOTE


PROMPT_PATCHES = {
    CHEF: [
        (
            "**Menus** — read every menu with its sections and dishes. Create and update\n"
            "menus (sections, dishes, sell prices) back to Loaded. Each menu line references\n"
            "either a recipe or a stock item.",
            "**Menus** — get_menus lists the menus, finds a dish across them (query) or\n"
            "reads one (menu_id). manage_menu writes: op 'create' with the full menu, or\n"
            "op 'update' with the menu_id and ONLY the deltas (a price, a new line, a\n"
            "removed section) — the server merges and writes the whole menu back. Each\n"
            "line references either a recipe or a stock item.",
        )
    ],
    "time_attendance": [
        (
            " Exception: shift writes (create_rostered_shift / update_shift) need "
            "explicit clockin/clockout datetimes — build them from today's date and "
            "weekday, stated above, in the venue's timezone. These are literal clock "
            "times, not trading-day windows.",
            " Shift changes are made in the roster editor: call show_roster and let the "
            "user edit there — you do not write shifts directly.",
        )
    ],
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConfig, AgentConnectionBinding, ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectionSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        by_action = {t.get("action"): t for t in tools}
        missing = [a for a in DEMOTE if a not in by_action]
        if missing:
            raise SystemExit(f"rows missing from the spec: {missing}")

        # ── 1. The two consolidators ──────────────────────────────────────
        tools_changed = False
        for tool_def, fname in (
            (READ_TOOL, "get_menus.py"),
            (WRITE_TOOL, "manage_menu.py"),
        ):
            tool = dict(tool_def)
            tool["consolidator_config"] = {
                **tool_def["consolidator_config"],
                "function_code": (_DIR / fname).read_text(encoding="utf-8"),
            }
            action = tool["action"]
            if action in by_action:
                keep = by_action[action].get("added_at")
                if keep:
                    tool["added_at"] = keep
                if by_action[action] != tool:
                    tools[[t.get("action") for t in tools].index(action)] = tool
                    by_action[action] = tool
                    tools_changed = True
                    changes.append(f"spec: updated {action}")
            else:
                tools.append(tool)
                by_action[action] = tool
                tools_changed = True
                changes.append(f"spec: added {action}")

        # ── 2. Demotions ──────────────────────────────────────────────────
        for t in tools:
            action = t.get("action")
            if action not in DEMOTE:
                continue
            if not t.get("engine_only"):
                t["engine_only"] = True
                tools_changed = True
                changes.append(f"spec {action}: engine_only")
            desc = str(t.get("description") or "")
            if not desc.startswith(("[consolidator-only]", "[engine-only]")):
                t["description"] = demoted_prefix(action) + desc
                tools_changed = True
                changes.append(f"spec {action}: description marked")
        if tools_changed and not dry_run:
            spec.tools = tools
            flag_modified(spec, "tools")
            spec.version = (spec.version or 0) + 1

        # ── 3. Bindings ───────────────────────────────────────────────────
        for b in (
            db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.connector_name == "loadedhub")
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            if not caps:
                continue
            dropped = [c for c in caps if c.get("action") in DEMOTE]
            if not dropped:
                continue
            kept = [c for c in caps if c.get("action") not in DEMOTE]
            note = f"dropped {sorted(c.get('action') for c in dropped)}"
            if b.agent_slug == CHEF:
                for new in ("get_menus", "manage_menu"):
                    if not any(c.get("action") == new for c in kept):
                        kept.append({"action": new, "enabled": True})
                note += "; get_menus + manage_menu enabled"
            changes.append(f"binding {b.agent_slug}: {note}")
            if not dry_run:
                b.capabilities = kept
                flag_modified(b, "capabilities")

        # ── 4. Prompts ────────────────────────────────────────────────────
        for slug, patches in PROMPT_PATCHES.items():
            a = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
            if not a or not a.system_prompt:
                continue
            text = a.system_prompt
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"prompt {slug}: needle swapped")
                elif new not in text:
                    print(
                        f"WARNING: prompt {slug} has neither the old nor the new sentence — patch by hand"
                    )
            if text != a.system_prompt and not dry_run:
                a.system_prompt = text

        if dry_run:
            db.rollback()
            print("DRY RUN — would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")

        if not dry_run:
            from app.services.config_validator import validate_config

            summary = validate_config(config_db=db)
            errors = [i for i in summary["issues"] if i.get("severity") == "error"]
            if errors:
                print("\nVALIDATION ERRORS (fix before walking away):")
                for i in errors:
                    print(f"  {i['where']}: {i['problem']}")
                sys.exit(1)
            print(
                f"\nconfig validation: clean ({summary['issue_count']} non-error notes)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
