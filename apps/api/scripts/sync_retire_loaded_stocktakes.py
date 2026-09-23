"""Retire Loaded's completed-stocktakes read. Stocktakes move to Orbit.

Sep 2026. `get_completed_stocktakes` calls Loaded's legacy `/wapi/` host,
which rejects the OAuth token Norm holds: 65 straight 403s in production since
16 Jul 2026 and not one success in 90 days. Loaded has no modern endpoint for
completed stocktakes, so the decision (user, 23 Sep 2026) is to stop reading
them from Loaded at all and build stocktakes through Orbit (the
`cook_brothers_app` connector: stock_find_stocktakes / stock_get_stocktake)
as their own arc.

What this removes, from the shared config DB:

- the spec rows `get_completed_stocktakes` (the dead raw) and
  `get_completed_stocktakes_for_period` (its wrapper — dropped from WRAPPED in
  sync_for_period_config.py in the same commit, so a replay can't re-add it);
- their capability entries on every loadedhub binding (all were already
  disabled) and any McpCapability rows (there were none);
- the `stocktake_variance` playbook's instruction to call the wrapper.

What it deliberately does NOT touch:

- **`generate_stocktake_report`.** It works (18/19 in 90 days) — every run
  came through the MCP "Stocktake & Variance" playbook with the caller
  supplying both stocktake ids. It moves with the Orbit arc.
- **The playbook itself.** The plan's fallback was to disable it, but that
  would break the one stocktake path that works. Step 2 now uses the ids the
  user gives and asks for them otherwise.

Nothing else references either name: no saved task filter, prompt, chart,
app or MCP row (scanned 23 Sep 2026, config DB and prod app DB).

Usage:
    uv run python scripts/sync_retire_loaded_stocktakes.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

RETIRED = ("get_completed_stocktakes", "get_completed_stocktakes_for_period")

PLAYBOOK = "stocktake_variance"
OLD_STEP = (
    "### Step 2: Get completed stocktakes\n"
    "- Call `get_completed_stocktakes_for_period` with the period in plain "
    'English ("last month", "the last 8 weeks") to find the stocktakes within '
    "it.\n"
    "- You need at least 2 stocktakes to generate a variance report."
)
NEW_STEP = (
    "### Step 2: Get the two stocktake ids\n"
    "- The report needs an opening and a closing stocktake id. If the user "
    "gave them, use them as given.\n"
    "- Otherwise ask the user for both ids. Norm can't list completed "
    "stocktakes yet — Loaded offers no endpoint for them, and the lookup is "
    "moving to Orbit. Say so plainly; never guess an id."
)


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
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
        tools = list(spec.tools or [])
        kept = [t for t in tools if t.get("action") not in RETIRED]
        for t in tools:
            if t.get("action") in RETIRED:
                changed.append(f"removed spec row {t.get('action')}")
        if len(kept) != len(tools) and not dry_run:
            spec.tools = kept
            flag_modified(spec, "tools")

        for b in (
            db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.connector_name == "loadedhub")
            .all()
        ):
            caps = list(b.capabilities or [])
            left = [c for c in caps if c.get("action") not in RETIRED]
            if len(left) != len(caps):
                for c in caps:
                    if c.get("action") in RETIRED:
                        changed.append(
                            f"binding {b.agent_slug}: removed {c.get('action')}"
                        )
                if not dry_run:
                    b.capabilities = left
                    flag_modified(b, "capabilities")

        for cap in (
            db.query(McpCapability)
            .filter(
                McpCapability.kind == "connector",
                McpCapability.target == "loadedhub",
                McpCapability.action.in_(RETIRED),
            )
            .all()
        ):
            changed.append(f"removed McpCapability {cap.action}")
            if not dry_run:
                db.delete(cap)

        pb = db.query(Playbook).filter(Playbook.slug == PLAYBOOK).first()
        if pb and OLD_STEP in (pb.instructions or ""):
            changed.append(f"playbook {PLAYBOOK}: step 2 asks for the ids")
            if not dry_run:
                pb.instructions = pb.instructions.replace(OLD_STEP, NEW_STEP)
        elif pb and any(r in (pb.instructions or "") for r in RETIRED):
            # The needle drifted: refuse rather than leave a dead tool named.
            raise SystemExit(
                f"playbook {PLAYBOOK} still names a retired tool but the "
                "expected step text has changed — update OLD_STEP"
            )

        if not changed:
            print("Already in sync.")
            return
        if dry_run:
            db.rollback()
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
