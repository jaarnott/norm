"""Take `resolve_dates` off the in-app agent surface. It stays everywhere else.

Sep 2026. resolve_dates existed because every read tool took raw ISO
timestamps and the model computed windows itself — a documented incident
class (a Saturday read midnight-to-midnight, $0 for a late-night venue). The
domain tools ended that: get_sales, get_labour, get_invoices, get_budgets and
the rest take `period` in plain English and resolve it against the venue's
trading day themselves. Production last called it on 27 Aug 2026 — the day
after get_sales shipped.

What this script removes: the capability from all five agent bindings, so it
leaves the entitled union that the (now single) agent's menu is built from.

What it deliberately does NOT touch:

- **The spec row.** Nine consolidators still call it through
  `call_api("norm", "resolve_dates", …)`, which reads ConnectionSpec and never
  the bindings. Deleting the row would break every period-taking tool at once.
- **The MCP surface.** `projection.ALWAYS_EXPOSE` keeps it for claude.ai
  clients, which have no Norm prompt telling them today's date — and the MCP
  server instructions tell every client to call it. That needed a code fix in
  the same commit: ALWAYS_EXPOSE was silently gated on an agent binding, so
  unbinding here would have removed it from MCP too.

Prose that pointed at it is rewritten, because an instruction naming a tool
the agent no longer holds is worse than no instruction:

- time_attendance's prompt sent SHIFT WRITES through it
  (create_rostered_shift / update_shift take literal clockin/clockout
  datetimes). Those are clock times, not trading-day windows, so today's date
  and weekday — already stated in every system prompt — are enough.
- two marketing playbooks opened with it. metricool/brevo have no credentials
  in production and have never been called, but a live playbook should still
  name only tools that exist.

ORDER: deploy the API first — the ALWAYS_EXPOSE fix and the _ALWAYS_INCLUDE
change ship with the code.

Usage:
    uv run python scripts/sync_retire_resolve_dates_from_agents.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ACTION = "resolve_dates"

PROMPT_PATCHES: dict[str, list[tuple[str, str]]] = {
    "time_attendance": [
        (
            "Exception: shift writes (create_rostered_shift / update_shift) "
            "need explicit clockin/clockout datetimes — resolve the day with "
            "resolve_dates, then build the times.",
            "Exception: shift writes (create_rostered_shift / update_shift) "
            "need explicit clockin/clockout datetimes — build them from "
            "today's date and weekday, stated above, in the venue's timezone. "
            "These are literal clock times, not trading-day windows.",
        )
    ],
}

PLAYBOOK_PATCHES: dict[str, list[tuple[str, str]]] = {
    "competitor_analysis": [
        (
            "`resolve_dates` + `get_analytics` for own brand.",
            "`get_analytics` for own brand — build the YYYY-MM-DD range from "
            "today's date, stated above.",
        )
    ],
    "social_performance_report": [
        (
            "### Step 1: Resolve dates\nCall `resolve_dates`.",
            "### Step 1: Date range\nBuild the YYYY-MM-DD range from today's "
            "date, stated above.",
        )
    ],
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConfig, AgentConnectionBinding, Playbook
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    missed: list[str] = []
    try:
        # ── 1. Unbind from every agent ───────────────────────────────────
        for b in (
            db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.connector_name == "norm")
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            kept = [c for c in caps if c.get("action") != ACTION]
            if len(kept) != len(caps):
                b.capabilities = kept
                flag_modified(b, "capabilities")
                changes.append(
                    f"binding {b.agent_slug}/norm: {len(caps)} -> {len(kept)} caps"
                )

        # ── 2. Prompts ───────────────────────────────────────────────────
        for slug, patches in PROMPT_PATCHES.items():
            a = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
            if not a or not a.system_prompt:
                missed.append(f"prompt {slug}: agent not found")
                continue
            text = a.system_prompt
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"prompt {slug}: needle swapped")
                else:
                    missed.append(f"prompt {slug}: needle not found")
            if text != a.system_prompt:
                a.system_prompt = text

        # ── 3. Playbook prose + any filter still naming it ───────────────
        for p in db.query(Playbook).all():
            text = p.instructions or ""
            for old, new in PLAYBOOK_PATCHES.get(p.slug, []):
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"playbook {p.slug}: prose swapped")
                else:
                    missed.append(f"playbook {p.slug}: needle not found")
            if text != (p.instructions or ""):
                p.instructions = text
            # Only ENABLED playbooks matter for the live surface; a disabled
            # one is never projected, so leave its filter as found.
            if p.enabled and ACTION in (p.tool_filter or []):
                p.tool_filter = [n for n in p.tool_filter if n != ACTION]
                flag_modified(p, "tool_filter")
                changes.append(f"playbook {p.slug}: filter -= {ACTION}")

        if missed:
            print("NOT APPLIED — these did not match:")
            for m in missed:
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
