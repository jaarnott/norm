"""Phase 4 of the stock consolidation arc: delete the two retired consolidators.

get_stock_items and get_stock_on_hand_for_item were demoted to engine_only by
sync_stock_domain_rollout.py on 24 Sep 2026 and replaced by get_stock's
`items` and `on_hand` views. Nothing composes them: the consolidators that
need the item list call get_stock_items_raw, and get_stock inlines the
on-hand chain. The one external reference was a saved app's declared action
list ("Tender Builder", production, 3 versions), which is repointed here to
get_stock — a drop-in, since the items view is get_stock's default and takes
the same query / item_id / detail / limit.

The six RAW rows the rollout demoted (get_stock_units, get_suppliers,
get_stock_item_groups, get_stocktake_templates, get_stock_on_hand,
get_stock_item_minimums) are NOT touched: get_stock calls them by name.

Order — the app DB first, so no version ever declares a missing action:

    1. soak report (last 7 days, both logs) — read it; abort if real traffic
    2. app_versions.spec: get_stock_items -> get_stock, every version
    3. config DB: delete the two spec rows (guarded: they must still carry
       the rollout's "[consolidator-only] Superseded by get_stock" marker),
       their McpCapability rows and any binding entries; bump spec.version
    4. validate_config

Run once against the LOCAL app DB (nothing to repoint there) and once with
DATABASE_URL pointed at production through the IAM proxy — the config DB is
shared, so step 3 is idempotent on the second run.

Usage:
    uv run python scripts/sync_stock_phase4_delete.py [--dry-run]
    DATABASE_URL=postgresql://github-deploy%40norm-production-491101.iam@127.0.0.1:5434/norm \\
        uv run python scripts/sync_stock_phase4_delete.py [--dry-run]
"""

from __future__ import annotations

import copy
import sys

sys.path.insert(0, ".")

NEW = "get_stock"
DELETE = ("get_stock_items", "get_stock_on_hand_for_item")
MARKER = f"[consolidator-only] Superseded by {NEW}"


def main(dry_run: bool = False) -> None:
    from sqlalchemy import text
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConnectionBinding,
        ConnectionSpec,
        McpCapability,
    )
    from app.db.engine import SessionLocal, _ConfigSessionLocal

    db = _ConfigSessionLocal()
    app_db = SessionLocal()
    changes: list[str] = []
    try:
        # ── 1. Soak: who still called the two by name, last 7 days ────────
        print("Soak (this app DB, last 7 days):")
        for r in app_db.execute(
            text(
                "select action, status, count(*), max(created_at) from tool_calls "
                "where action = any(:n) and created_at > now() - interval '7 days' "
                "group by 1, 2 order by 1, 2"
            ),
            {"n": list(DELETE)},
        ).fetchall():
            print(f"  tool_calls {r[0]} {r[1]}: {r[2]} (last {r[3]:%Y-%m-%d %H:%M})")
        for r in app_db.execute(
            text(
                "select capability, success, count(*), max(created_at) from "
                "mcp_audit_log where capability = any(:n) and created_at > "
                "now() - interval '7 days' group by 1, 2 order by 1, 2"
            ),
            {"n": [f"loadedhub__{a}" for a in DELETE]},
        ).fetchall():
            print(
                f"  mcp_audit_log {r[0]} ok={r[1]}: {r[2]} (last {r[3]:%Y-%m-%d %H:%M})"
            )
        print("  (end of soak report)")

        # ── 2. App specs: declared actions ───────────────────────────────
        for vid, app_id, version, spec in app_db.execute(
            text("select id, app_id, version, spec from app_versions")
        ).fetchall():
            if not isinstance(spec, dict):
                continue
            actions = spec.get("actions") or []
            if not any(
                isinstance(e, dict)
                and e.get("connector") == "loadedhub"
                and e.get("action") in DELETE
                for e in actions
            ):
                continue
            new_spec = copy.deepcopy(spec)
            kept = []
            for e in new_spec["actions"]:
                if (
                    isinstance(e, dict)
                    and e.get("connector") == "loadedhub"
                    and e.get("action") in DELETE
                ):
                    e = {**e, "action": NEW}
                if e not in kept:
                    kept.append(e)
            new_spec["actions"] = kept
            changes.append(f"app {app_id} v{version}: declared actions -> {NEW}")
            if not dry_run:
                import json

                app_db.execute(
                    text(
                        "update app_versions set spec = cast(:s as json) where id = :id"
                    ),
                    {"s": json.dumps(new_spec), "id": vid},
                )

        # ── 3. Config DB rows ─────────────────────────────────────────────
        spec_row = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec_row:
            raise SystemExit("loadedhub ConnectionSpec not found")
        tools = [dict(t) for t in (spec_row.tools or [])]
        by_action = {t.get("action"): t for t in tools}
        if NEW not in by_action:
            raise SystemExit(f"{NEW} is not installed — nothing may be deleted")
        for action in DELETE:
            t = by_action.get(action)
            if not t:
                continue
            if not t.get("engine_only") or not str(t.get("description", "")).startswith(
                MARKER
            ):
                raise SystemExit(
                    f"{action} is not in the demoted state the rollout left it in "
                    "(engine_only + superseded marker) — refusing to delete"
                )
            changes.append(f"spec {action}: deleted")
        remaining = [t for t in tools if t.get("action") not in DELETE]
        if len(remaining) != len(tools) and not dry_run:
            spec_row.tools = remaining
            flag_modified(spec_row, "tools")
            spec_row.version = (spec_row.version or 0) + 1

        for m in (
            db.query(McpCapability)
            .filter(
                McpCapability.kind == "connector",
                McpCapability.target == "loadedhub",
                McpCapability.action.in_(list(DELETE)),
            )
            .all()
        ):
            changes.append(f"mcp {m.action}: row deleted (was enabled={m.enabled})")
            if not dry_run:
                db.delete(m)

        for b in (
            db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.connector_name == "loadedhub")
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            kept = [c for c in caps if c.get("action") not in DELETE]
            if len(kept) != len(caps):
                changes.append(f"binding {b.agent_slug}: dead entries removed")
                if not dry_run:
                    b.capabilities = kept
                    flag_modified(b, "capabilities")

        if dry_run:
            db.rollback()
            app_db.rollback()
            print("DRY RUN — would apply:")
        else:
            app_db.commit()
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
        app_db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
