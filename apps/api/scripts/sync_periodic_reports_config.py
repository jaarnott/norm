"""Retire the norm_reports periodic consolidators — replaced by get_sales.

History: the three get_periodic_* tools lived config-only until 22 Aug
2026, were given canonical repo files and a plain-English period surface,
and on 24 Aug 2026 were absorbed into `loadedhub.get_sales`
(scripts/sync_sales_config.py):

- get_periodic_sales          -> get_sales with time_windows (the day-start
                                 attribution ported verbatim — prod thread
                                 b9bda2c1)
- get_periodic_product_sales  -> get_sales breakdown='items'
                                 (+ time_windows for clock cuts)
- get_periodic_staff_sales    -> get_sales breakdown='staff'
                                 (+ staff_name drill-down, interval winners)

This script now DELETES those rows so a re-run enforces the end state (the
chef-seed lesson: a sync script that re-installs yesterday's doctrine
regresses production on every replay). Bindings/playbooks/prompts/MCP rows
are swapped by scripts/sync_sales_domain_rollout.py — run that FIRST so
nothing still points at the rows this removes.

Usage:
    uv run python scripts/sync_periodic_reports_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "norm_reports"

RETIRED = (
    "get_periodic_sales",
    "get_periodic_product_sales",
    "get_periodic_staff_sales",
)


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"{CONNECTOR} ConnectorSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        kept = [t for t in tools if t.get("action") not in RETIRED]
        removed = [t.get("action") for t in tools if t.get("action") in RETIRED]
        if not removed:
            print("periodic tools already retired — nothing to do")
            return
        if dry_run:
            print("DRY RUN — would remove: " + ", ".join(removed))
            return
        spec.tools = kept
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(
            "removed "
            + ", ".join(removed)
            + f" (replaced by loadedhub.get_sales), spec version -> {spec.version}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
