"""Stop three playbooks telling agents to resolve dates by hand.

Sep 2026. Each of these lines predates the domain tools and now instructs a
tool call for work the tool it precedes already does:

- stock_requirements  -> calculate_template_stock_requirements takes
  `order_until` in plain English ("next Friday", "end of next week").
- stocktake_variance  -> get_completed_stocktakes_for_period takes `period`
  in plain English.
- candidate_review    -> get_applications has no date parameter at all, and
  today's date is already stated in every system prompt.

Production has not called resolve_dates since 27 Aug 2026 — the day after
get_sales shipped — so these lines are the last place still asking for it
where it buys nothing. The resolver itself stays: time_attendance's shift
writes and procurement's tender review still take explicit ISO datetimes.

These playbooks have no seed script; their text lives only in the config DB,
so this is the canonical record of the change.

Usage:
    uv run python scripts/sync_obsolete_resolve_dates_prose.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PATCHES: dict[str, list[tuple[str, str]]] = {
    "stock_requirements": [
        (
            "Use resolve_dates to convert this to a date.",
            'Pass their phrase straight through as `order_until` ("next '
            'Friday", "end of next week") — the tool resolves it against '
            "the venue's calendar.",
        )
    ],
    "stocktake_variance": [
        (
            "- Call `resolve_dates` for the date range.\n"
            "- Call `get_completed_stocktakes_for_period` to find stocktakes "
            "within that period.",
            "- Call `get_completed_stocktakes_for_period` with the period in "
            'plain English ("last month", "the last 8 weeks") to find the '
            "stocktakes within it.",
        )
    ],
    "candidate_review": [
        (
            "- If asking about recent applications, call `resolve_dates` "
            "first for the cutoff date.",
            "- Today's date is already in your context — use it directly for "
            'a "recent" cutoff.',
        )
    ],
}


def main(dry_run: bool = False) -> None:
    from app.db.config_models import Playbook
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    missing: list[str] = []
    try:
        for slug, patches in PATCHES.items():
            p = db.query(Playbook).filter(Playbook.slug == slug).first()
            if not p:
                missing.append(slug)
                continue
            text = p.instructions or ""
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"{slug}: prose swapped")
                else:
                    missing.append(f"{slug}: needle not found")
            if text != (p.instructions or ""):
                p.instructions = text

        if missing:
            print("NOT APPLIED — needles did not match:")
            for m in missing:
                print(f"  {m}")
            db.rollback()
            sys.exit(1)

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


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
