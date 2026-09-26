"""Stocktake ids come from Orbit; the agent pairs them itself.

Sep 2026. Loaded's completed-stocktakes endpoint is dead (retired 23 Sep,
sync_retire_loaded_stocktakes.py), so the variance playbook had to ask the
user for two stocktake ids. Orbit (the cook_brothers_app connector) lists
stocktakes with Loaded's OWN ids — verified 24 Sep: the two ids August's real
variance report used appear in Orbit's list — and its `status=completed`
filter works since Orbit's 26 Sep fix (verified live: 23 completed counts in
La Zeppa's May–June window, sorted newest first).

Why the agent pairs, not Orbit: a variance report is opening count + received
− closing count against POS sales, so it needs two counts OF THE SAME
TEMPLATE. Orbit's `variance_pair` option picks "the two most recent completed
in the window" across templates; for La Zeppa's 27 May – 24 Jun window that is
a Beverage count and a Food count, and Loaded happily reports on the pair:
548 items, 0 of them counted at both ends (August's Food→Food report: 209 of
261). So the playbook tells the agent to take the list and pick the two most
recent completed counts of the chosen template.

What this does:
- binds `stock_find_stocktakes` (read) to the procurement agent's
  cook_brothers_app binding (sync_tender_actions.py appends, so a replay of
  it keeps this);
- rewrites step 2 of the stocktake_variance playbook (the "ask for ids" text
  sync_retire_loaded_stocktakes.py wrote) and the step-3 sentence, so the
  older count is the opening and the newer the closing.

The two stocktake rows also get a ``field_schema``: discovery used to keep
only descriptions, so the agent was shown every field as a string and Orbit's
strict schema rejected "true" / "100" (three failed calls in the first live
run, 26 Sep 2026). convert_mcp_tools_to_spec now records types, so the next
re-discovery (POST /connector-specs/cook_brothers_app/sync-mcp-tools) writes
the same values; until then this sets them from Orbit's published schema. The
agent is told to omit venue_id — the venue's own Orbit connection is used.

Usage:
    uv run python scripts/sync_orbit_stocktake_lookup.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

AGENT = "procurement"
CONNECTOR = "cook_brothers_app"
ACTION = "stock_find_stocktakes"
LABEL = (
    "List completed stocktakes (id, template, completed date) — the two inputs "
    "of a variance report. Omit venue_id: the venue's own connection is used."
)

#: Orbit's own inputSchema for the two rows, as published on 26 Sep 2026.
FIELD_SCHEMAS = {
    "stock_find_stocktakes": {
        "status": {"type": "string", "enum": ["pending", "completed", "all"]},
        "include_templates": {"type": "boolean"},
        "limit": {"type": "number"},
    },
    "stock_get_stocktake": {
        "include": {
            "type": "array",
            "items": {"type": "string", "enum": ["areas", "recipes"]},
        },
    },
}

PLAYBOOK = "stocktake_variance"
OLD_STEP2 = (
    "### Step 2: Get the two stocktake ids\n"
    "- The report needs an opening and a closing stocktake id. If the user "
    "gave them, use them as given.\n"
    "- Otherwise ask the user for both ids. Norm can't list completed "
    "stocktakes yet — Loaded offers no endpoint for them, and the lookup is "
    "moving to Orbit. Say so plainly; never guess an id."
)
NEW_STEP2 = (
    "### Step 2: Find the two stocktakes\n"
    "- Call `stock_find_stocktakes` with status 'completed' (add from/to if "
    "the user named a period; leave venue_id empty — the venue's own Orbit "
    "connection is used). Each row carries templateId, title, "
    "datestampCompleted and pending.\n"
    "- Pick the two most recent completed stocktakes whose templateId equals "
    "the template chosen in step 1: the older is the opening count, the newer "
    "the closing. Never pair different templates, and never use ad-hoc counts "
    "(templateId all zeros) unless the user asks for them.\n"
    "- If the user gave two ids, use them as given. If fewer than two "
    "completed counts exist for that template, say so and list the ones that "
    "do — never guess an id."
)
OLD_STEP3 = "- Call `generate_stocktake_report` with the two stocktake IDs."
NEW_STEP3 = (
    "- Call `generate_stocktake_report` with the older stocktake as "
    "start_stocktake_id and the newer as end_stocktake_id."
)


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectionBinding, ConnectionSpec, Playbook
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec or not any(t.get("action") == ACTION for t in spec.tools or []):
            raise SystemExit(
                f"{CONNECTOR}.{ACTION} is not in the spec — discover tools first"
            )

        tools = [dict(t) for t in (spec.tools or [])]
        typed = False
        for t in tools:
            want = FIELD_SCHEMAS.get(t.get("action"))
            if want and (t.get("field_schema") or {}) != want:
                t["field_schema"] = want
                typed = True
                changes.append(f"spec {CONNECTOR}.{t['action']}: field_schema set")
        if typed and not dry_run:
            spec.tools = tools
            flag_modified(spec, "tools")
            spec.version = (spec.version or 0) + 1

        b = (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.agent_slug == AGENT,
                AgentConnectionBinding.connector_name == CONNECTOR,
            )
            .first()
        )
        if not b:
            raise SystemExit(
                f"no {AGENT}/{CONNECTOR} binding — run sync_tender_actions.py first"
            )
        caps = [dict(c) for c in (b.capabilities or [])]
        existing = [c for c in caps if c.get("action") == ACTION]
        if not existing:
            caps.append({"action": ACTION, "label": LABEL, "enabled": True})
            changes.append(f"binding {AGENT}/{CONNECTOR}: {ACTION} enabled")
        elif not existing[0].get("enabled", True):
            existing[0]["enabled"] = True
            changes.append(f"binding {AGENT}/{CONNECTOR}: {ACTION} re-enabled")
        if changes and not dry_run:
            b.capabilities = caps
            flag_modified(b, "capabilities")

        pb = db.query(Playbook).filter(Playbook.slug == PLAYBOOK).first()
        if not pb:
            raise SystemExit(f"playbook {PLAYBOOK} not found")
        text = pb.instructions or ""
        for old, new, what in (
            (OLD_STEP2, NEW_STEP2, "step 2"),
            (OLD_STEP3, NEW_STEP3, "step 3"),
        ):
            if old in text:
                text = text.replace(old, new)
                changes.append(f"playbook {PLAYBOOK}: {what} rewritten")
            elif new not in text:
                raise SystemExit(
                    f"playbook {PLAYBOOK} {what} has drifted from the expected text — "
                    "update the needle before running"
                )
        if text != (pb.instructions or "") and not dry_run:
            pb.instructions = text

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
                print("\nVALIDATION ERRORS:")
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
