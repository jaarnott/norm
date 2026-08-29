"""One stock-item read surface, and updates that carry only the deltas.

Before: agents juggled four stock-item tools — the all-items list (already
transformed down to {id, name}), get_stock_item, get_stock_item_full, and an
update_stock_item PUT that made the LLM fetch the COMPLETE item and echo
every field back around one edit (expensive, and one hallucinated field from
corrupting the item).

After:
- ``get_stock_items`` (consolidator, read-only): name query → slim matches;
  item_id → one item at detail "summary" or "full". The agent chooses what
  data it wants; the description steers it to fetch ONE item, never the list,
  before an update.
- ``update_stock_item`` (consolidator, method PUT so the human-approval gate
  holds): the model sends item_id + deltas; the read-merge-write happens
  server-side via get_stock_item_full + update_stock_item_raw.
- Demoted to [consolidator-only]/engine_only: ``get_stock_items_raw`` (the
  old list, transform intact — internal callers depend on the {id, name}
  shape), ``get_stock_item``, ``get_stock_item_full``,
  ``update_stock_item_raw``.
- The procurement and executive_chef prompts are updated in place (they
  referenced get_stock_item / get_stock_item_full by name).

ORDER: run AFTER deploying the API — the internal callers
(received_items_for_period, the PO-resolution helper) are repointed to
``get_stock_items_raw`` in the same push, and nested consolidator dispatch
(function_executor) ships with it. Until the deploy, the currently-synced
raw tools keep working untouched.

Usage:
    uv run python scripts/sync_stock_item_consolidators.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, ".")

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"

READ_TOOL = {
    "action": "get_stock_items",
    "method": "GET",  # read-only consolidator: auto-executes, nestable
    "description": (
        "THE stock item lookup. Pass query (name substring) for slim "
        "{id, name} matches, or item_id for ONE item — detail 'summary' "
        "(units, minimum, variants with codes and costs) or 'full' (the "
        "complete Loaded object). Always look up the ONE item you need "
        "(query or item_id) rather than reading the whole list; before an "
        "update, fetch just that item, then call update_stock_item with "
        "only the fields to change."
    ),
    "required_fields": [],
    "optional_fields": ["item_id", "query", "detail", "limit"],
    "field_descriptions": {
        "item_id": "Loaded stock item id — returns exactly this item",
        "query": "Case-insensitive name substring to search for",
        "detail": "'summary' (default) or 'full' (complete Loaded object)",
        "limit": (
            "Max rows returned (default 25). Raise it (e.g. 2000) only when "
            "a task genuinely needs the whole catalogue"
        ),
    },
    # A full-catalogue listing (~1,000 slim {id, name} rows) must survive the
    # tool-result slimmer when explicitly requested via limit.
    "max_result_chars": 80_000,
    "read_only": True,
    "consolidator_config": {
        "function_code": (_DIR / "get_stock_items.py").read_text(),
        "max_api_calls": 3,
    },
}

UPDATE_TOOL = {
    "action": "update_stock_item",
    # PUT keeps the agent loop's human-approval gate; the consolidator's
    # deterministic merge decides what is actually written.
    "method": "PUT",
    "description": (
        "Update a stock item by sending item_id and ONLY the changes — the "
        "server fetches the item, merges, and writes the whole object back. "
        "Never fetch or resend the full item yourself. `changes` uses "
        "Loaded's own field names (e.g. {'minimumStockOnHand': 6}); "
        "`variant_changes` edits suppliers[] entries matched by variant_id "
        "or supplier_id + stock_code; `add_suppliers` appends new variant "
        "entries. Look the item up first with get_stock_items (item_id or a "
        "name query), never the full list. This is a write — human-approved."
    ),
    "required_fields": ["item_id"],
    "optional_fields": ["changes", "variant_changes", "add_suppliers"],
    "field_descriptions": {
        "item_id": "Loaded stock item id (from get_stock_items)",
        "changes": "Top-level fields to set, Loaded's field names, deltas only",
        "variant_changes": (
            "List of edits to existing suppliers[] entries: each needs "
            "variant_id OR supplier_id + stock_code, plus the fields to set"
        ),
        "add_suppliers": "New suppliers[] entries to append (full entry dicts)",
    },
    "consolidator_config": {
        "function_code": (_DIR / "update_stock_item.py").read_text(),
        "max_api_calls": 3,
        "allowed_write_actions": ["update_stock_item_raw"],
    },
}

#: Raw plumbing: demoted, engine-only. get_stock_items_raw keeps the old
#: list transform verbatim — internal callers depend on the {id, name} shape
#: and on its deleted-items filter.
DEMOTE_EXISTING = {
    "get_stock_item_full": "get_stock_items (detail 'full') / update_stock_item",
}

#: Deleted outright: a duplicate tool for the same endpoint as
#: get_stock_item_full (its one internal caller, get_stock_on_hand_for_item,
#: is repointed below). Binding capability entries are switched off too.
DELETE_ACTIONS = ("get_stock_item",)


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConfig, AgentConnectionBinding, ConnectionSpec
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
        by_action = {t.get("action"): t for t in tools}

        # 1. get_stock_items_raw — clone of the current raw list tool.
        if "get_stock_items_raw" not in by_action:
            old = by_action.get("get_stock_items")
            if not old or old.get("consolidator_config"):
                raise SystemExit(
                    "expected the raw get_stock_items HTTP tool to clone; "
                    "found a consolidator — already migrated?"
                )
            raw = dict(old)
            raw["action"] = "get_stock_items_raw"
            raw["engine_only"] = True
            raw["description"] = (
                "[consolidator-only] The raw all-items list, transformed to "
                "{id, name} with deleted items filtered. Call get_stock_items "
                "instead; never bind this to an agent."
            )
            raw.pop("added_at", None)
            tools.append(raw)
            changed.append("added get_stock_items_raw")

        # 2. update_stock_item_raw — clone of the current raw PUT.
        if "update_stock_item_raw" not in by_action:
            old = by_action.get("update_stock_item")
            if not old or old.get("consolidator_config"):
                raise SystemExit(
                    "expected the raw update_stock_item PUT to clone; "
                    "found a consolidator — already migrated?"
                )
            raw = dict(old)
            raw["action"] = "update_stock_item_raw"
            raw["engine_only"] = True
            raw["description"] = (
                "[consolidator-only] PUT the WHOLE item object back to Loaded. "
                "Called by the update_stock_item consolidator's server-side "
                "merge; never bind this to an agent."
            )
            raw.pop("added_at", None)
            tools.append(raw)
            changed.append("added update_stock_item_raw")

        # 3. Install/replace the two consolidators under the public names.
        for tool in (READ_TOOL, UPDATE_TOOL):
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

        # 3a2. get_received_items_for_period calls the all-items list for
        # name resolution; once get_stock_items is a consolidator that call
        # must target the raw twin. Patched on the CONFIG row directly — the
        # canonical file lives with another session's in-flight work, and
        # this string is exact enough to be safe and idempotent.
        for t in tools:
            if t.get("action") == "get_received_items_for_period":
                cc = dict(t.get("consolidator_config") or {})
                code = cc.get("function_code") or ""
                needle = 'call_api("loadedhub", "get_stock_items", {})'
                if needle in code:
                    cc["function_code"] = code.replace(
                        needle,
                        'call_api("loadedhub", "get_stock_items_raw", {})',
                        1,
                    )
                    t["consolidator_config"] = cc
                    changed.append(
                        "repointed received_items_for_period to get_stock_items_raw"
                    )

        # 3b. get_stock_on_hand_for_item: install the canonical file (it
        # previously lived ONLY in the config DB) with its item read
        # repointed to get_stock_item_full.
        soh_code = (_DIR / "get_stock_on_hand_for_item.py").read_text()
        for t in tools:
            if t.get("action") == "get_stock_on_hand_for_item":
                cc = dict(t.get("consolidator_config") or {})
                if cc.get("function_code") != soh_code:
                    cc["function_code"] = soh_code
                    t["consolidator_config"] = cc
                    changed.append("updated get_stock_on_hand_for_item code")

        # 3c. Delete the duplicate single-item tool outright.
        before_n = len(tools)
        tools = [t for t in tools if t.get("action") not in DELETE_ACTIONS]
        if len(tools) != before_n:
            changed.append(f"deleted {', '.join(DELETE_ACTIONS)}")

        # 4. Demote the superseded reads.
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

        if not dry_run:
            spec.tools = tools
            flag_modified(spec, "tools")

        # 5. Binding capabilities: switch the demoted reads off.
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
            for cap in caps:
                if (
                    cap.get("action") in DEMOTE_EXISTING
                    or cap.get("action") in DELETE_ACTIONS
                ) and cap.get("enabled", True):
                    cap["enabled"] = False
                    touched = True
                    changed.append(
                        f"binding {b.agent_slug}: {cap.get('action')} disabled"
                    )
            if touched and not dry_run:
                b.capabilities = caps
                flag_modified(b, "capabilities")

        # 6. Agent prompts that named the old tools.
        rewrites = {
            "procurement": [
                (
                    "When creating orders, always get the default variant from "
                    "get_stock_item before submitting.",
                    "When creating orders, always get the default variant first: "
                    "call get_stock_items with the item_id (detail 'summary' "
                    "includes each variant's stock code and default flag).",
                ),
                (
                    "When looking up a specific stock item, search "
                    "get_stock_items first then use the item ID for detailed "
                    "queries.",
                    "When looking up a specific stock item, call "
                    "get_stock_items with query (name substring) first, then "
                    "again with the item_id for detail — never scan the full "
                    "list.",
                ),
            ],
        }
        for slug, pairs in rewrites.items():
            ag = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
            if not ag or not ag.system_prompt:
                continue
            sp = ag.system_prompt
            for old_text, new_text in pairs:
                if old_text in sp:
                    sp = sp.replace(old_text, new_text)
                    changed.append(f"prompt {slug}: rewrote '{old_text[:40]}…'")
            if sp != ag.system_prompt and not dry_run:
                ag.system_prompt = sp

        # executive_chef references get_stock_item_full in a longer sentence —
        # rewrite by marker rather than exact match.
        chef = (
            db.query(AgentConfig)
            .filter(AgentConfig.agent_slug == "executive_chef")
            .first()
        )
        if chef and chef.system_prompt and "get_stock_item_full" in chef.system_prompt:
            new_sp = chef.system_prompt.replace(
                "call get_stock_item_full first and resend the WHOLE item "
                "object changing only the",
                "call update_stock_item with the item_id and ONLY the fields "
                "to change — the server fetches, merges and writes the whole "
                "item (never fetch or resend the full object). Look the item "
                "up first with get_stock_items; do not change the",
            )
            if new_sp == chef.system_prompt:
                print(
                    "WARNING: executive_chef prompt references "
                    "get_stock_item_full but the expected sentence was not "
                    "found — update it by hand."
                )
            else:
                changed.append("prompt executive_chef: rewrote the full-item flow")
                if not dry_run:
                    chef.system_prompt = new_sp

        if not changed:
            print("Already in sync.")
            return
        if dry_run:
            print("Would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changed:
            print("  " + line)
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
