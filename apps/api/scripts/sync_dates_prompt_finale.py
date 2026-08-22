"""Retire resolve_dates from the agent prompts — the date rule's finale.

Run LAST, after every date-taking consolidator accepts `period` in plain
English (sync_budget_dates, sync_staff_attendance_config,
sync_invoice_receiving_config, sync_stock_requirements_config,
sync_periodic_reports_config). At that point the conditional rule the
for_period arc installed ("…for a tool that still wants explicit dates,
get them from resolve_dates first…") is obsolete everywhere except one
place: the shift WRITES (create_rostered_shift / update_shift), whose
clockin/clockout datetimes are the shift's own values — so
time_attendance keeps a one-line exception and everyone else drops the
clause entirely.

resolve_dates itself stays exposed (prompt_builder._ALWAYS_INCLUDE, MCP
ALWAYS_EXPOSE) — it remains the oracle for questions ABOUT dates; it just
stops being prompt-mandated plumbing.

Usage:
    uv run python scripts/sync_dates_prompt_finale.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

#: The sentence the for_period arc installed (identical in all six prompts).
OLD_RULE = (
    "Dates: tools that take `period` accept plain English ('last week', "
    "'yesterday') and resolve it against the venue's trading calendar "
    "themselves — pass the phrase, never work out dates. For a tool that "
    "still wants explicit dates, get them from resolve_dates first; "
    "never calculate dates yourself."
)

NEW_RULE = (
    "Dates: pass periods in plain English ('last week', 'every Friday for "
    "12 weeks') — tools resolve them against the venue's trading calendar. "
    "Never calculate dates yourself."
)

SHIFT_EXCEPTION = (
    " Exception: shift writes (create_rostered_shift / update_shift) need "
    "explicit clockin/clockout datetimes — resolve the day with "
    "resolve_dates, then build the times."
)


def main(dry_run: bool = False) -> None:
    from app.db.config_models import AgentConfig
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changed: list[str] = []
    try:
        for ag in db.query(AgentConfig).all():
            sp = ag.system_prompt or ""
            if OLD_RULE not in sp:
                continue
            new_rule = NEW_RULE + (
                SHIFT_EXCEPTION if ag.agent_slug == "time_attendance" else ""
            )
            new_sp = sp.replace(OLD_RULE, new_rule)
            changed.append(
                f"prompt {ag.agent_slug}: resolve_dates clause "
                + (
                    "narrowed to shift writes"
                    if ag.agent_slug == "time_attendance"
                    else "removed"
                )
            )
            if not dry_run:
                ag.system_prompt = new_sp
        if not dry_run:
            db.commit()
        print("DRY RUN — would apply:" if dry_run else "Applied:")
        for line in changed or ["  (nothing to do)"]:
            print(f"  {line}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
