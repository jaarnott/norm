"""One recipe read surface: get_recipes, with both raw reads fully demoted.

Before: agents (and claude.ai via MCP) saw two raw recipe reads —
``get_all_recipes`` (434 recipes, ~2.5MB raw, notes carrying entire pasted
web pages of HTML) and ``get_recipe_details`` (~2k tokens, the most-called
raw tool). The write side is untouched: ``edit_recipe`` (interactive card
draft) and the CB App's ``kitchen_loadedhub_update_recipe`` stay as-is.

After:
- ``get_recipes`` (consolidator, read-only): name query → slim {id, name}
  matches (optionally decorated with Live costs); recipe_id → ONE recipe at
  detail "summary" (display units, names over ids, current version_id, cost
  per yield unit, notes stripped to text) or "full" (raw Loaded payload).
- ``get_recipe_costs_raw`` (NEW, engine-only): Loaded's costs endpoint with
  a pre-built ``{{ q }}`` query. priceType=Live carries real invoice-derived
  costs; Forecast returns zeros (verified live 21 Aug 2026).
- Demoted to [consolidator-only]/engine_only: ``get_all_recipes``,
  ``get_recipe_details``. Their MCP capability rows are disabled and a
  ``get_recipes`` row (same mcp:orders:read scope) is enabled, so claude.ai
  swaps surfaces too.
- Live config rows that named the raws are needle-patched in place — the
  executive_chef prompt, the create_recipe_from_ingredients playbook
  (tool_filter + instructions), the edit_recipe description, and the CB
  kitchen_loadedhub_update_recipe description/field_descriptions. Those
  rows' canonical sync scripts belong to another session's in-flight work;
  this script patches ROWS only and their files must be reconciled once
  both arcs land (a re-run of their sync would restore the old tool names
  in prose — harmless, the engine_only gate holds, but worth tidying).

ORDER: run AFTER deploying the API — the claude.ai recipe card mapping
moves from get_recipe_details to get_recipes in app/mcp/ui_apps.py in the
same push. Until the deploy, the currently-synced raw tools keep working.

Usage:
    uv run python scripts/sync_recipe_consolidator.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, ".")

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"

_LOADED_HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}

READ_TOOL = {
    "action": "get_recipes",
    "method": "GET",  # read-only consolidator: auto-executes, nestable
    "description": (
        "THE recipe lookup. Pass query (name substring) for slim {id, name} "
        "matches, or recipe_id for ONE recipe — detail 'summary' (ingredient "
        "lines in display units with names, yield, current version_id, cost "
        "per yield unit at Live prices, notes as plain text) or 'full' (the "
        "raw Loaded object). include_cost adds Live cost to search matches. "
        "Always look up the ONE recipe you need (query or recipe_id) rather "
        "than scanning the whole list."
    ),
    "required_fields": [],
    "optional_fields": ["recipe_id", "query", "detail", "limit", "include_cost"],
    "field_descriptions": {
        "recipe_id": "Loaded recipe id — returns exactly this recipe",
        "query": "Case-insensitive name substring to search for",
        "detail": "'summary' (default) or 'full' (raw Loaded payload)",
        "limit": "Max matches returned (default 25 with a query; a bare call lists all)",
        "include_cost": "true → decorate search matches with Live cost per yield unit",
    },
    # The list-them-all path (~434 slim {id, name} rows) must survive the
    # tool-result slimmer.
    "max_result_chars": 80_000,
    "read_only": True,
    "consolidator_config": {
        "function_code": (_DIR / "get_recipes.py").read_text(),
        "max_api_calls": 6,
    },
}

COSTS_RAW_TOOL = {
    "action": "get_recipe_costs_raw",
    "method": "GET",
    "path_template": "//api.loadedhub.com/1.0/stock/internal/costs?{{ q }}",
    "headers": dict(_LOADED_HEADERS),
    "description": (
        "[consolidator-only] Loaded costs for recipe/item ids. q is a "
        "pre-built query string (repeated recipeIdTimeStrings=<id>,<iso "
        "timestamp> params plus priceType=Live). Called by get_recipes; "
        "never bind this to an agent."
    ),
    "required_fields": ["q"],
    "read_only": True,
    "engine_only": True,
}

#: action → what supersedes it (goes into the demoted description).
DEMOTE_EXISTING = {
    "get_all_recipes": "get_recipes (query, or a bare call to list all)",
    "get_recipe_details": "get_recipes (recipe_id)",
}

#: Rows whose prose named the raw tools: (row locator, [(old, new), ...]).
#: Straight token swaps — every live occurrence reads correctly with
#: get_recipes substituted (verified against the rows on 21 Aug 2026).
_TOKEN_SWAPS = [
    ("get_recipe_details", "get_recipes"),
    ("get_all_recipes", "get_recipes"),
]


def _swap_tokens(text: str) -> str:
    for old, new in _TOKEN_SWAPS:
        text = text.replace(old, new)
    return text


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConfig,
        AgentConnectionBinding,
        ConnectionSpec,
        McpCapability,
        Playbook,
    )
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changed: list[str] = []
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectionSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]

        # 1. Install/replace the consolidator and the costs backend.
        for tool in (READ_TOOL, COSTS_RAW_TOOL):
            idx = next(
                (i for i, t in enumerate(tools) if t.get("action") == tool["action"]),
                None,
            )
            if idx is None:
                tools.append(dict(tool))
                changed.append(f"added {tool['action']}")
            elif tools[idx] != tool:
                keep = tools[idx].get("added_at")
                entry = dict(tool)
                if keep:
                    entry["added_at"] = keep
                tools[idx] = entry
                changed.append(f"updated {tool['action']}")

        # 2. Demote the superseded reads (they stay as call_api backends).
        for action, use in DEMOTE_EXISTING.items():
            for t in tools:
                if t.get("action") == action and not t.get("engine_only"):
                    t["engine_only"] = True
                    desc = str(t.get("description") or "")
                    if not desc.startswith("[consolidator-only]"):
                        t["description"] = (
                            f"[consolidator-only] Superseded by {use}. " + desc
                        )
                    changed.append(f"demoted {action}")

        # 3. edit_recipe's description pointed at get_all_recipes for ids.
        for t in tools:
            if t.get("action") == "edit_recipe":
                desc = str(t.get("description") or "")
                if "get_all_recipes" in desc:
                    t["description"] = _swap_tokens(desc)
                    changed.append("patched edit_recipe description")

        if not dry_run:
            spec.tools = tools
            flag_modified(spec, "tools")

        # 4. Binding capabilities: raws off, get_recipes on wherever a raw
        #    was enabled. Bindings with empty capabilities (= ALL actions)
        #    inherit get_recipes automatically and lose the raws via
        #    engine_only, so they need no touch.
        for b in (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.connector_name == "loadedhub",
                AgentConnectionBinding.enabled == True,  # noqa: E712
            )
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            touched = False
            had_raw = False
            for cap in caps:
                if cap.get("action") in DEMOTE_EXISTING and cap.get("enabled", True):
                    cap["enabled"] = False
                    touched = True
                    had_raw = True
                    changed.append(
                        f"binding {b.agent_slug}: {cap.get('action')} disabled"
                    )
            if had_raw and not any(c.get("action") == "get_recipes" for c in caps):
                caps.append({"action": "get_recipes", "enabled": True})
                touched = True
                changed.append(f"binding {b.agent_slug}: get_recipes enabled")
            if touched and not dry_run:
                b.capabilities = caps
                flag_modified(b, "capabilities")

        # 5. MCP capability rows: swap the claude.ai surface too.
        for action in DEMOTE_EXISTING:
            cap = (
                db.query(McpCapability)
                .filter(
                    McpCapability.kind == "connector",
                    McpCapability.target == "loadedhub",
                    McpCapability.action == action,
                )
                .first()
            )
            if cap and cap.enabled:
                if not dry_run:
                    cap.enabled = False
                changed.append(f"mcp capability {action} disabled")
        new_cap = (
            db.query(McpCapability)
            .filter(
                McpCapability.kind == "connector",
                McpCapability.target == "loadedhub",
                McpCapability.action == "get_recipes",
            )
            .first()
        )
        if not new_cap:
            if not dry_run:
                db.add(
                    McpCapability(
                        kind="connector",
                        target="loadedhub",
                        action="get_recipes",
                        scopes=["mcp:orders:read"],
                        enabled=True,
                    )
                )
            changed.append("mcp capability get_recipes enabled")
        elif not new_cap.enabled:
            if not dry_run:
                new_cap.enabled = True
            changed.append("mcp capability get_recipes re-enabled")

        # 6. executive_chef prompt named get_recipe_details for version_id;
        #    get_recipes' summary carries version_id, so the swap reads true.
        chef = (
            db.query(AgentConfig)
            .filter(AgentConfig.agent_slug == "executive_chef")
            .first()
        )
        if chef and chef.system_prompt:
            new_sp = _swap_tokens(chef.system_prompt)
            if new_sp != chef.system_prompt:
                if not dry_run:
                    chef.system_prompt = new_sp
                changed.append("prompt executive_chef: raw recipe reads → get_recipes")

        # 7. The recipe-creation playbook filtered in both raws.
        pb = (
            db.query(Playbook)
            .filter(Playbook.slug == "create_recipe_from_ingredients")
            .first()
        )
        if pb:
            tf = list(pb.tool_filter or [])
            new_tf = []
            for name in tf:
                swapped = _swap_tokens(name)
                if swapped not in new_tf:
                    new_tf.append(swapped)
            if new_tf != tf:
                if not dry_run:
                    pb.tool_filter = new_tf
                    flag_modified(pb, "tool_filter")
                changed.append(f"playbook tool_filter: {new_tf}")
            if pb.instructions and (
                "get_all_recipes" in pb.instructions
                or "get_recipe_details" in pb.instructions
            ):
                if not dry_run:
                    pb.instructions = _swap_tokens(pb.instructions)
                changed.append("playbook instructions: raw recipe reads → get_recipes")

        # 8. The CB write tool's prose pointed at the raws for ids.
        cb = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "cook_brothers_app")
            .first()
        )
        if cb:
            cb_tools = [dict(t) for t in (cb.tools or [])]
            cb_touched = False
            for t in cb_tools:
                if (t.get("tool_action") or t.get("action")) != (
                    "kitchen_loadedhub_update_recipe"
                ):
                    continue
                desc = str(t.get("description") or "")
                if "get_recipe_details" in desc or "get_all_recipes" in desc:
                    t["description"] = _swap_tokens(desc)
                    cb_touched = True
                    changed.append("patched kitchen_loadedhub_update_recipe description")
                fd = dict(t.get("field_descriptions") or {})
                for k, v in fd.items():
                    if "get_all_recipes" in str(v) or "get_recipe_details" in str(v):
                        fd[k] = _swap_tokens(str(v))
                        cb_touched = True
                        changed.append(
                            f"patched kitchen_loadedhub_update_recipe fd[{k}]"
                        )
                if cb_touched:
                    t["field_descriptions"] = fd
            if cb_touched and not dry_run:
                cb.tools = cb_tools
                flag_modified(cb, "tools")

        if dry_run:
            print("DRY RUN — would apply:")
        else:
            spec.version = (spec.version or 0) + 1
            db.commit()
            print("Applied:")
        for line in changed or ["  (nothing to do)"]:
            print(f"  {line}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
