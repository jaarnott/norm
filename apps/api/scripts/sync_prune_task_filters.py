"""Drop dead names from saved tasks' tool_filter lists.

A task's tool_filter is intersected with the agent's entitled tools at run
time, so a name that no longer matches anything — renamed, deleted, demoted
to engine_only, or hidden from agents — is dropped silently. validate_config
now flags those (check_task_tool_filters); this script removes them so the
filter says what the task actually gets. It never adds a name: a rollout
that replaces a tool patches its own successor in (see
sync_stock_domain_rollout.py step 5).

Runs against the app DB DATABASE_URL points at — once locally, once with the
production URL through the IAM proxy:

    uv run python scripts/sync_prune_task_filters.py [--dry-run]
    DATABASE_URL=postgresql://github-deploy%40norm-production-491101.iam@127.0.0.1:5434/norm \\
        uv run python scripts/sync_prune_task_filters.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.agents.prompt_builder import _ENGINE_AND_MCP_ONLY, _RETIRED_ACTIONS
    from app.db.config_models import ConnectionSpec
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import AutomatedTask

    cfg = _ConfigSessionLocal()
    db = SessionLocal()
    try:
        known: set[str] = set()
        hidden: set[str] = set(_ENGINE_AND_MCP_ONLY) | set(_RETIRED_ACTIONS)
        for spec in cfg.query(ConnectionSpec).all():
            for t in spec.tools or []:
                if isinstance(t, dict) and t.get("action"):
                    known.add(t["action"])
                    if t.get("engine_only"):
                        hidden.add(t["action"])
        changes: list[str] = []
        for task in db.query(AutomatedTask).all():
            names = task.tool_filter
            if not isinstance(names, list):
                continue
            kept = [n for n in names if n in known and n not in hidden]
            dead = [n for n in names if n not in kept]
            if not dead:
                continue
            changes.append(
                f"task '{task.title}' ({task.agent_slug}, {task.status}): drop {dead}"
            )
            if not dry_run:
                task.tool_filter = kept
                flag_modified(task, "tool_filter")
        if dry_run:
            db.rollback()
            print("DRY RUN — would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")
    finally:
        db.close()
        cfg.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
