"""Roll the agent surface over to `get_stock`; demote the eight tools it absorbs.

Phase 3 of the stock consolidation arc (Sep 2026). The sequence, each step
idempotent:

    1. uv run python scripts/sync_stock_config.py                  (install)
    2. uv run python scripts/sync_stock_domain_rollout.py --expose-only
       — bind get_stock and add its MCP row; retire NOTHING yet, so the
       old tools can be called side by side with the new views (stable
       facts against tests/goldens/stock_2026-09.json, stock on hand by
       the same-moment protocol).
    3. uv run python scripts/sync_stock_domain_rollout.py          (retire)

No API deploy is needed: get_stock is config only, and every backend it
calls already exists as a spec row.

What this does, and why each part is DEMOTE rather than delete:

- The eight retiring rows become ``engine_only`` with a "[consolidator-only]
  Superseded by get_stock" description. engine_only is the one dependable
  hide: it is filtered in prompt_builder._collect_tools regardless of binding
  shape (a binding with an EMPTY capability list exposes every action, so
  unbinding alone hides nothing), and it also removes the row from MCP and
  the task tool-picker. Execution never checks it — call_api and
  _execute_tool_call read the spec row directly — so consolidators that
  compose these raws (calculate_template_stock_requirements,
  received_items_for_period, update_stock_item, …), old threads, saved
  report charts and the saved "Tender Builder" app (3 versions, calling
  get_stock_items by name) all keep working. Only deleting or renaming a row
  breaks them; that waits for Phase 4, after a soak.
- Binding entries naming the eight are dropped and get_stock is bound where
  one of them was enabled (procurement, executive_chef, app_builder). The
  three bindings that carried them all DISABLED (hr, time_attendance,
  reports) lose the dead entries and gain nothing.
- MCP rows for the eight are disabled (not deleted — reversible for the
  soak) and a get_stock row is added under the orders read scope.
  check_mcp_capability flags a MISSING spec tool but not an engine-only one,
  so without this step the rows would simply vanish from the projection
  while the admin UI showed them on.
- Prose that named a retiring tool is rewritten — two prompts, four
  playbooks, five spec descriptions — with sentence-level needles verified
  against the live rows on 24 Sep 2026. An instruction naming a tool the
  agent no longer holds is worse than none.
- Saved AutomatedTask.tool_filter lists are the one place a demotion bites
  silently (names are intersected with the union, so a demoted name just
  drops out); they are patched in the app DB this runs against. Production
  was scanned on 24 Sep 2026: no saved task names a stock tool.

Replay hazards were neutralised in the same commit: the seed scripts that
would re-bind the raws or reinstall get_stock_items un-demoted now write
current doctrine (sync_stock_item_consolidators.py,
sync_stock_item_minimums_action.py, sync_stock_item_write_actions.py,
sync_executive_chef_agent.py, sync_app_builder_agent.py,
sync_cb_recipe_write_binding.py, consolidator_coverage._SUPERSEDES).

Usage:
    uv run python scripts/sync_stock_domain_rollout.py [--dry-run] [--expose-only]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

NEW = "get_stock"

#: Retiring action -> how to ask get_stock for the same thing.
RETIRING: dict[str, str] = {
    "get_stock_items": "view 'items' (the default)",
    "get_stock_on_hand_for_item": "view 'on_hand' with item_id",
    "get_stock_on_hand": "view 'on_hand' with template",
    "get_stock_units": "view 'reference', kind 'units'",
    "get_suppliers": "view 'reference', kind 'suppliers'",
    "get_stock_item_groups": "view 'reference', kind 'groups'",
    "get_stocktake_templates": "view 'reference', kind 'templates'",
    "get_stock_item_minimums": "view 'minimums'",
}


def demoted_prefix(action: str) -> str:
    return f"[consolidator-only] Superseded by {NEW} — {RETIRING[action]}. "


#: Spec descriptions that named a retiring tool (sentence-level needles).
DESCRIPTION_PATCHES: dict[str, list[tuple[str, str]]] = {
    "update_stock_item": [
        (
            "Look the item up first with get_stock_items (item_id or a name "
            "query), never the full list.",
            "Look the item up first with get_stock (item_id or a name query), "
            "never the full list.",
        )
    ],
    "create_stock_item": [
        (
            "groupId (from get_stock_item_groups)",
            "groupId (from get_stock: view 'reference', kind 'groups')",
        ),
        (
            "its ratio from get_stock_units)",
            "its ratio from get_stock: view 'reference', kind 'units')",
        ),
    ],
    "update_variant_unit": [
        (
            "(from get_stock_items — the summary lists each variant_id)",
            "(from get_stock — the summary lists each variant_id)",
        ),
        (
            "(from get_stock_units)",
            "(from get_stock: view 'reference', kind 'units')",
        ),
    ],
    "get_stock_item_full": [
        (
            "Superseded by get_stock_items (detail 'full')",
            "Superseded by get_stock (detail 'full')",
        )
    ],
    "get_stock_items_raw": [("Call get_stock_items instead", "Call get_stock instead")],
}

FIELD_DESCRIPTION_PATCHES: dict[str, dict[str, tuple[str, str]]] = {
    "update_stock_item": {
        "item_id": (
            "Loaded stock item id (from get_stock_items)",
            "Loaded stock item id (from get_stock)",
        )
    },
    "get_stock_item_full": {
        "item_id": ("(GUID, from get_stock_items)", "(GUID, from get_stock)")
    },
    "get_stock_on_hand": {
        "template_id": (
            "use get_stocktake_templates to get this id",
            "use get_stock (view 'reference', kind 'templates') to get this id",
        )
    },
}

PROMPT_PATCHES: dict[str, list[tuple[str, str]]] = {
    "procurement": [
        (
            "call get_stock_items with the item_id (detail 'summary' includes "
            "each variant's stock code and default flag).",
            "call get_stock with the item_id (detail 'summary' includes each "
            "variant's stock code and default flag).",
        ),
        (
            "When looking up a specific stock item, call get_stock_items with "
            "query (name substring) first, then again with the item_id for "
            "detail — never scan the full list.",
            "When looking up a specific stock item, call get_stock with query "
            "(name substring) first, then again with the item_id for detail — "
            "never scan the full list. get_stock also answers stock on hand "
            "(view 'on_hand'), units / suppliers / groups / templates (view "
            "'reference') and par levels (view 'minimums').",
        ),
    ],
    "executive_chef": [
        (
            "Look an item up with\nget_stock_items (query or item_id;",
            "Look an item up with\nget_stock (query or item_id;",
        ),
        (
            "also set its paired ratio (from get_stock_units).",
            "also set its paired ratio (from get_stock, view 'reference', kind "
            "'units').",
        ),
    ],
}

PLAYBOOK_PATCHES: dict[str, list[tuple[str, str]]] = {
    "stock_requirements": [
        (
            "1. **Get templates**: Call `get_stocktake_templates` for the "
            "user's venue to see available stock areas.",
            "1. **Get templates**: Call `get_stock` with view 'reference' and "
            "kind 'templates' for the user's venue to see available stock "
            "areas.",
        )
    ],
    "stocktake_variance": [
        (
            "- Call `get_stocktake_templates` to see available templates.",
            "- Call `get_stock` with view 'reference', kind 'templates' to see "
            "available templates.",
        ),
        (
            "- If relevant, call `get_stock_on_hand` for current positions.",
            "- If relevant, call `get_stock` with view 'on_hand' and the "
            "template for current positions.",
        ),
    ],
    "create_stock_order": [
        (
            "also call `get_suppliers` and tell the user",
            "also call `get_stock` (view 'reference', kind 'suppliers') and "
            "tell the user",
        )
    ],
    "create_recipe_from_ingredients": [
        (
            "check get_stock_items / get_recipes for anything ambiguous",
            "check get_stock / get_recipes for anything ambiguous",
        )
    ],
}


def main(dry_run: bool = False, expose_only: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConfig,
        AgentConnectionBinding,
        ConnectionSpec,
        McpCapability,
        Playbook,
    )
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import AutomatedTask

    db = _ConfigSessionLocal()
    app_db = SessionLocal()
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
        if NEW not in by_action:
            if not dry_run:
                raise SystemExit(
                    f"{NEW} is not installed — run scripts/sync_stock_config.py first"
                )
            print(
                f"NOTE: {NEW} is not installed yet (sync_stock_config.py); dry run continues.\n"
            )
        missing = [a for a in RETIRING if a not in by_action]
        if missing:
            # Phase 4 (scripts/sync_stock_phase4_delete.py) deletes the two
            # retiring consolidators once the soak is clean; a replay after
            # that must not refuse — the prose needles below still apply.
            print(f"NOTE: no longer in the spec (deleted): {missing}")

        # ── 1. Spec rows: demote the eight; rewrite prose naming them ─────
        tools_changed = False
        for t in [] if expose_only else tools:
            action = t.get("action")
            if action in RETIRING:
                if not t.get("engine_only"):
                    t["engine_only"] = True
                    tools_changed = True
                    changes.append(f"spec {action}: engine_only")
                desc = str(t.get("description") or "")
                if not desc.startswith("[consolidator-only]"):
                    t["description"] = demoted_prefix(action) + desc
                    tools_changed = True
                    changes.append(f"spec {action}: description marked superseded")
            for old, new in DESCRIPTION_PATCHES.get(action, []):
                desc = str(t.get("description") or "")
                if old in desc:
                    t["description"] = desc.replace(old, new)
                    tools_changed = True
                    changes.append(f"spec {action}: description needle swapped")
            for field, (old, new) in FIELD_DESCRIPTION_PATCHES.get(action, {}).items():
                fd = dict(t.get("field_descriptions") or {})
                if old in str(fd.get(field) or ""):
                    fd[field] = str(fd[field]).replace(old, new)
                    t["field_descriptions"] = fd
                    tools_changed = True
                    changes.append(f"spec {action}.{field}: needle swapped")
        if tools_changed:
            spec.tools = tools
            flag_modified(spec, "tools")
            spec.version = (spec.version or 0) + 1

        # ── 2. Bindings: drop the eight; bind get_stock where one was on ─
        for b in (
            db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.connector_name == "loadedhub")
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            if not caps:
                continue  # an empty list is a wildcard; leave it be
            dropped = [c for c in caps if c.get("action") in RETIRING]
            if not dropped:
                continue
            # expose-only keeps the old entries so both surfaces coexist.
            kept = caps if expose_only else [c for c in caps if c not in dropped]
            needs_new = any(c.get("enabled", True) for c in dropped)
            existing = [c for c in kept if c.get("action") == NEW]
            added = False
            if needs_new and not existing:
                kept.append({"action": NEW, "enabled": True})
                added = True
            elif needs_new and not existing[0].get("enabled", True):
                existing[0]["enabled"] = True
                added = True
            if kept == caps and not added:
                continue
            b.capabilities = kept
            flag_modified(b, "capabilities")
            changes.append(
                f"binding {b.agent_slug}: "
                + (
                    f"{NEW} enabled"
                    if expose_only
                    else f"dropped {sorted(c.get('action') for c in dropped)}"
                    + (f"; {NEW} enabled" if added else "")
                )
            )

        # ── 3. MCP rows: disable the eight, add get_stock ─────────────────
        for m in (
            []
            if expose_only
            else db.query(McpCapability)
            .filter(
                McpCapability.kind == "connector",
                McpCapability.target == "loadedhub",
                McpCapability.action.in_(list(RETIRING)),
            )
            .all()
        ):
            if m.enabled:
                m.enabled = False
                changes.append(f"mcp {m.action}: disabled")
        if not (
            db.query(McpCapability)
            .filter(McpCapability.target == "loadedhub", McpCapability.action == NEW)
            .first()
        ):
            db.add(
                McpCapability(
                    kind="connector",
                    target="loadedhub",
                    action=NEW,
                    scopes=["mcp:orders:read"],
                    enabled=True,
                )
            )
            changes.append(f"mcp {NEW}: added")

        # ── 4. Prompts and playbooks ──────────────────────────────────────
        for slug, patches in ({} if expose_only else PROMPT_PATCHES).items():
            a = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
            if not a or not a.system_prompt:
                continue
            text = a.system_prompt
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"prompt {slug}: needle swapped")
            if text != a.system_prompt:
                a.system_prompt = text
        for slug, patches in ({} if expose_only else PLAYBOOK_PATCHES).items():
            p = db.query(Playbook).filter(Playbook.slug == slug).first()
            if not p or not p.instructions:
                continue
            text = p.instructions
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"playbook {slug}: needle swapped")
            if text != p.instructions:
                p.instructions = text

        # ── 5. Saved task filters (app DB) ────────────────────────────────
        for task in [] if expose_only else app_db.query(AutomatedTask).all():
            names = list(task.tool_filter or [])
            if not any(n in RETIRING for n in names):
                continue
            new_names = [n for n in names if n not in RETIRING]
            if NEW not in new_names:
                new_names.append(NEW)
            task.tool_filter = new_names
            flag_modified(task, "tool_filter")
            changes.append(f"task {task.id} ({task.title}): filter repointed to {NEW}")

        # ── 6. Leftovers: anything still naming a retiring tool ───────────
        leftovers: list[str] = []
        for a in [] if expose_only else db.query(AgentConfig).all():
            hits = [n for n in RETIRING if n in (a.system_prompt or "")]
            if hits:
                leftovers.append(f"prompt {a.agent_slug}: {hits}")
        for p in [] if expose_only else db.query(Playbook).all():
            hits = [n for n in RETIRING if n in (p.instructions or "")]
            if hits:
                leftovers.append(f"playbook {p.slug}: {hits}")
        for t in [] if expose_only else tools:
            if t.get("action") in RETIRING or t.get("engine_only"):
                continue
            blob = str(t.get("description") or "") + str(
                t.get("field_descriptions") or ""
            )
            hits = [n for n in RETIRING if n in blob]
            if hits:
                leftovers.append(f"spec {t.get('action')}: {hits}")

        if dry_run:
            db.rollback()
            app_db.rollback()
            print(
                f"DRY RUN ({'expose only' if expose_only else 'retire'}) — would apply:"
            )
        else:
            db.commit()
            app_db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")
        if leftovers:
            print("\nSTILL NAMING A RETIRED TOOL (agent-visible prose):")
            for line in leftovers:
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
        app_db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv, expose_only="--expose-only" in sys.argv)
