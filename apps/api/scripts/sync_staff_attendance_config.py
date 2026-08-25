"""Retire the get_staff_attendance consolidator — absorbed into get_labour.

History: built 20 Aug 2026 to fix the leave-as-worked-hours ghost-shift
incident (Bessie, Sat 15 Aug 2026, prod thread 51a90809 — Loaded returns
booked leave as pseudo clock-ins and the original code counted them as
worked time). On 25 Aug 2026 its engine — leave-split doctrine intact —
became the DEFAULT view of `loadedhub.get_labour`
(config/consolidators/get_labour.py, scripts/sync_labour_config.py), and
this row retired.

This script now DELETES the row so a re-run enforces the end state (the
chef-seed lesson: a sync script that re-installs yesterday's doctrine
regresses production on every replay). Bindings/playbooks/prompts/MCP
references are swapped by scripts/sync_labour_domain_rollout.py — run
that FIRST so nothing still points at the row this removes.

Usage:
    uv run python scripts/sync_staff_attendance_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ACTION = "get_staff_attendance"


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectorSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        kept = [t for t in tools if t.get("action") != ACTION]
        if len(kept) == len(tools):
            print(f"{ACTION} already retired — nothing to do")
            return
        if dry_run:
            print(f"DRY RUN — would remove {ACTION}")
            return
        spec.tools = kept
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(
            f"removed {ACTION} (its engine lives on as get_labour's default "
            f"view), spec version -> {spec.version}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
