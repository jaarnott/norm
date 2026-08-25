"""Close the last three for_period leaks and retire the old date doctrine.

The ``*_for_period`` consolidators resolve plain-English periods through
Norm's venue-aware calendar INSIDE the tool, so the raw twins are plumbing
and the old prompt rule ("always call resolve_dates first") is obsolete
noise for them. sync_demote_superseded_tools.py demoted 8 of the 11 raw
twins; the last three were deferred because live agent prompts named them.
This script finishes the job:

- Demote ``get_pos_item_sales``, ``get_roster``, ``get_timeclock_entries``
  (engine_only + [consolidator-only] description — they stay callable as
  the wrappers' call_api backends and by the staff_attendance consolidator
  and dashboard widgets, which resolve outside the agent surface).
- Binding capabilities: the three raws off wherever enabled; the
  ``_for_period`` twin on in each binding that lost one.
- MCP capability rows: disable rows for the three, and tidy stale enabled
  rows of the 8 previously demoted raws (engine_only already hides them
  from the projection; the rows should agree). get_roster_for_period's
  row stays — it feeds the claude.ai roster card.
- Prompt patches on the LIVE agent_configs rows (no repo seeds exist since
  system_config.py was deleted): the reports/hr/time_attendance sentences
  that name the raws move to the _for_period twins, and all six agents'
  "Always call resolve_dates before making API calls that need dates."
  becomes conditional — period-taking tools resolve dates themselves;
  resolve_dates remains the source for tools that still want explicit
  dates (budgets, staff attendance, the invoice review engines — next
  arc's conversions).

ORDER: run AFTER deploying the API — show_roster's replay widens to match
either roster action name in the same push.

Usage:
    uv run python scripts/sync_finish_for_period_demotions.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

#: The three remaining raw twins (action → its date-safe front). Kept at
#: CURRENT doctrine: the sales family's front is get_sales since 24 Aug
#: 2026 (sync_sales_config.py), so a replay demotes toward it, never
#: toward a retired wrapper name.
DEMOTIONS = {
    "get_pos_item_sales": "get_sales",
    # The labour front is get_labour since 25 Aug 2026.
    "get_roster": "get_labour",
    "get_timeclock_entries": "get_labour",
}

#: Previously demoted raw twins whose McpCapability rows may still say
#: enabled (harmless — engine_only hides them — but the rows should agree).
PREVIOUSLY_DEMOTED = [
    "get_sales_data",
    "get_pos_orders",
    "get_staff_orders",
    "get_staff_item_orders",
    "get_pos_discounts",
    "get_roster_vs_actual",
    "get_cogs_detail",
    "get_completed_stocktakes",
]

#: Sentence-level prompt needles (never token swaps: "get_roster" is a
#: substring of get_roster_summary and get_roster_vs_actual). Applied in
#: order; each is skipped when absent. Verified against the live rows on
#: 22 Aug 2026.
PROMPT_PATCHES = [
    (
        "call both get_roster and get_timeclock_entries for the same period",
        "call get_labour (view 'attendance' is the default) — it fetches "
        "roster and timeclock together and splits booked leave out",
    ),
    (
        "call both get_roster and get_timeclock_entries",
        "call get_labour (view 'attendance' is the default) — it fetches "
        "roster and timeclock together and splits booked leave out",
    ),
    # Replay guard: a prompt already patched to the (now retired) wrappers
    # moves on to current doctrine.
    (
        "call both get_roster_for_period and get_timeclock_entries_for_period "
        "with the same period phrase",
        "call get_labour (view 'attendance' is the default) — it fetches "
        "roster and timeclock together and splits booked leave out",
    ),
    (
        "use get_pos_item_sales and rank by revenue",
        "use get_sales with breakdown 'items' and rank by revenue",
    ),
    # Replay guard: a prompt already patched to the (now retired) wrapper
    # moves on to current doctrine.
    (
        "use get_pos_item_sales_for_period and rank by revenue",
        "use get_sales with breakdown 'items' and rank by revenue",
    ),
    (
        "Always call resolve_dates before making API calls that need dates.",
        "Dates: tools that take `period` accept plain English ('last week', "
        "'yesterday') and resolve it against the venue's trading calendar "
        "themselves — pass the phrase, never work out dates. For a tool that "
        "still wants explicit dates, get them from resolve_dates first; "
        "never calculate dates yourself.",
    ),
]


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConfig,
        AgentConnectorBinding,
        ConnectorSpec,
        McpCapability,
    )
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changed: list[str] = []
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectorSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]

        # 1. Demote the three raws.
        for action, twin in DEMOTIONS.items():
            for t in tools:
                if t.get("action") == action and not t.get("engine_only"):
                    t["engine_only"] = True
                    desc = str(t.get("description") or "")
                    if not desc.startswith("[consolidator-only]"):
                        t["description"] = (
                            f"[consolidator-only] Superseded by {twin}. " + desc
                        )
                    changed.append(f"demoted {action}")
        if not dry_run:
            spec.tools = tools
            flag_modified(spec, "tools")

        # 2. Bindings: raws off, twins on where a raw was lost.
        for b in (
            db.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.connector_name == "loadedhub",
                AgentConnectorBinding.enabled == True,  # noqa: E712
            )
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            touched = False
            needed: set[str] = set()
            for cap in caps:
                if cap.get("action") in DEMOTIONS and cap.get("enabled", True):
                    cap["enabled"] = False
                    touched = True
                    needed.add(DEMOTIONS[cap.get("action")])
                    changed.append(
                        f"binding {b.agent_slug}: {cap.get('action')} disabled"
                    )
            for twin in sorted(needed):
                existing = next(
                    (c for c in caps if c.get("action") == twin), None
                )
                if existing is None:
                    caps.append({"action": twin, "enabled": True})
                    touched = True
                    changed.append(f"binding {b.agent_slug}: {twin} enabled")
                elif not existing.get("enabled", True):
                    existing["enabled"] = True
                    touched = True
                    changed.append(f"binding {b.agent_slug}: {twin} re-enabled")
            if touched and not dry_run:
                b.capabilities = caps
                flag_modified(b, "capabilities")

        # 3. MCP capability rows.
        for action in list(DEMOTIONS) + PREVIOUSLY_DEMOTED:
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

        # 4. Prompt patches, every agent that carries a needle.
        for ag in db.query(AgentConfig).all():
            sp = ag.system_prompt or ""
            new_sp = sp
            for old, new in PROMPT_PATCHES:
                if old in new_sp:
                    new_sp = new_sp.replace(old, new)
                    changed.append(f"prompt {ag.agent_slug}: rewrote '{old[:48]}…'")
            if new_sp != sp and not dry_run:
                ag.system_prompt = new_sp

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
