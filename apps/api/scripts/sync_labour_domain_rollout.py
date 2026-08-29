"""Roll every config-DB reference over to `get_labour`, then retire the old rows.

Phase 2 of the domain-tools arc (25 Aug 2026). The sequence, each step
idempotent:

    1. deploy the API (the view-gated roster-card mapping, the show_roster
       acceptance filter and the rebuilt artifact bundle ride the push),
    2. uv run python scripts/sync_labour_config.py      (install get_labour)
    3. uv run python scripts/sync_labour_domain_rollout.py  (THIS: swap
       bindings, playbook filter + prose, prompts, MCP rows; demote
       get_staff_members to an engine backend)
    4. uv run python scripts/sync_for_period_config.py  (prunes the three
       labour wrapper rows — they carry `wraps` and left WRAPPED)
    5. uv run python scripts/sync_staff_attendance_config.py  (deletes the
       get_staff_attendance row — its engine lives in get_labour)

Then republish the claude.ai roster artifact (dist-artifact/roster.html)
— it now calls loadedhub__get_labour with view='roster', which exists
only after step 3's MCP row swap.

Needles were verified against the LIVE rows on 25 Aug 2026 (prompts: hr,
time_attendance — including the dead get_roster_summary sentence, a tool
that no longer exists; playbook: roster_viewer; bindings:
hr/time_attendance/procurement/reports/app_builder loadedhub; MCP: 5
loadedhub rows, scope mcp:roster:read).

The final validation FAILS only on issues that name this rollout's
entities — the config DB carries pre-existing drift (social playbooks,
invoice write-action names) that is not this script's to fix.

Usage:
    uv run python scripts/sync_labour_domain_rollout.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

RETIRED = [
    "get_roster_for_period",
    "get_roster_vs_actual_for_period",
    "get_timeclock_entries_for_period",
    "get_staff_attendance",
    "get_staff_members",
]

DEMOTED = "get_staff_members"  # survives engine-only: the staff view's backend

PLAYBOOK_PATCHES: dict[str, list[tuple[str, str]]] = {
    "roster_viewer": [
        (
            "Use `get_staff_attendance` for all roster analysis.\n\n"
            "### Parameters\n"
            "- `venue` — venue name (required)\n"
            "- `start_datetime` / `end_datetime` — date range\n"
            "- `staff_name` — optional filter (fuzzy match on first/last "
            "name)\n"
            '- `group_by` — `"staff"` (default), `"day"`, or `"detail"`',
            "Use `get_labour` for all roster analysis — view 'attendance' "
            "is the default.\n\n"
            "### Parameters\n"
            "- `venue` — venue name (required)\n"
            "- `period` — plain English ('last week', 'yesterday'); "
            "resolved against the venue's trading day. Never work out "
            "dates yourself.\n"
            "- `staff_name` — optional filter (fuzzy match on first/last "
            "name)\n"
            '- `group_by` — `"staff"` (default), `"day"`, or `"detail"`\n'
            "- `venues` — a list or 'all' for per-venue totals in one call",
        ),
        (
            "For raw roster viewing only: use `get_roster_for_period`",
            "For raw roster viewing only: use `get_labour` with view='roster'",
        ),
        (
            "For clockin times only: use `get_timeclock_entries_for_period`.",
            "For clockin times only: use `get_labour` with view='timeclock'.",
        ),
    ],
}

_COMPARE_OLD = (
    "- When comparing actual vs rostered hours, call both "
    "get_roster_for_period and get_timeclock_entries_for_period with the "
    "same period phrase."
)
_COMPARE_NEW = (
    "- When comparing actual vs rostered hours, call get_labour (view "
    "'attendance' is the default) — it fetches roster and timeclock "
    "together, splits booked leave out, and computes the variance."
)

PROMPT_PATCHES: dict[str, list[tuple[str, str]]] = {
    "hr": [(_COMPARE_OLD, _COMPARE_NEW)],
    "time_attendance": [
        (_COMPARE_OLD, _COMPARE_NEW),
        (
            # get_roster_summary no longer exists — this sentence pointed at
            # a dead tool before this rollout; get_labour's day grouping is
            # the living answer.
            "- Use get_roster_summary for daily summaries — it combines "
            "roster and clockin data into rostered_shifts, actual_shifts, "
            "rostered_hours, actual_hours, rostered_cost, actual_cost, "
            "no_shows, and unrostered_clockins per day.",
            "- For daily summaries use get_labour with group_by='day' — "
            "per-day rostered/actual hours and cost, plus the unrostered "
            "count.",
        ),
        (
            "- For staff queries, use get_staff_members to look up names, "
            "roles, and employment details.",
            "- For staff queries, use get_labour with view='staff' to look "
            "up names, roles, and rates.",
        ),
    ],
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConfig,
        AgentConnectionBinding,
        ConnectionSpec,
        McpCapability,
        Playbook,
    )
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        # ── 1. Bindings: retired names → get_labour ──────────────────────
        for b in (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.enabled == True,  # noqa: E712
                AgentConnectionBinding.connector_name == "loadedhub",
            )
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            if not caps:
                continue
            new_caps: list[dict] = []
            swapped = False
            for c in caps:
                if c.get("action") in RETIRED:
                    if not any(x.get("action") == "get_labour" for x in new_caps):
                        new_caps.append({**c, "action": "get_labour"})
                    swapped = True
                else:
                    new_caps.append(c)
            if swapped:
                b.capabilities = new_caps
                flag_modified(b, "capabilities")
                changes.append(
                    f"binding {b.agent_slug}/loadedhub: "
                    f"{len(caps)} -> {len(new_caps)} caps"
                )

        # ── 2. Playbooks: tool_filter swaps + prose needles ──────────────
        for p in db.query(Playbook).all():
            filt = list(p.tool_filter or [])
            new_filt: list[str] = []
            touched = False
            for name in filt:
                if name in RETIRED:
                    if "get_labour" not in new_filt:
                        new_filt.append("get_labour")
                    touched = True
                else:
                    new_filt.append(name)
            if touched:
                p.tool_filter = new_filt
                flag_modified(p, "tool_filter")
                changes.append(f"playbook {p.slug}: filter -> {new_filt}")
            text = p.instructions or ""
            for old, new in PLAYBOOK_PATCHES.get(p.slug, []):
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"playbook {p.slug}: prose swapped")
            if text != (p.instructions or ""):
                p.instructions = text

        # ── 3. Prompts ───────────────────────────────────────────────────
        for slug, patches in PROMPT_PATCHES.items():
            a = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
            if not a or not a.system_prompt:
                continue
            text = a.system_prompt
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"prompt {slug}: needle swapped")
            if text != a.system_prompt:
                a.system_prompt = text

        # ── 4. MCP rows: retire old, ensure get_labour ───────────────────
        for m in db.query(McpCapability).all():
            if m.target == "loadedhub" and m.action in RETIRED:
                db.delete(m)
                changes.append(f"mcp row deleted: loadedhub/{m.action}")
        has = (
            db.query(McpCapability)
            .filter(
                McpCapability.target == "loadedhub",
                McpCapability.action == "get_labour",
            )
            .first()
        )
        if not has:
            db.add(
                McpCapability(
                    kind="connector",
                    target="loadedhub",
                    action="get_labour",
                    scopes=["mcp:roster:read"],
                    enabled=True,
                )
            )
            changes.append("mcp row added: loadedhub/get_labour")

        # ── 5. Demote get_staff_members to the engine backend ────────────
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if spec:
            tools = [dict(t) for t in (spec.tools or [])]
            for t in tools:
                if t.get("action") == DEMOTED and not t.get("engine_only"):
                    t["engine_only"] = True
                    desc = str(t.get("description") or "")
                    if not desc.startswith("[consolidator-only]"):
                        t["description"] = (
                            "[consolidator-only] Superseded by get_labour "
                            "(view='staff'). " + desc
                        )
                    spec.tools = tools
                    flag_modified(spec, "tools")
                    spec.version = (spec.version or 0) + 1
                    changes.append(f"demoted {DEMOTED} (engine_only)")
                    break

        if dry_run:
            db.rollback()
            print("DRY RUN — would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")

        # ── 6. Validate — fail ONLY on issues naming this rollout's names ─
        if not dry_run:
            from app.services.config_validator import validate_config

            watch = set(RETIRED) | {"get_labour", "roster_viewer"}
            summary = validate_config(config_db=db)
            mine = [
                i
                for i in summary["issues"]
                if i.get("severity") == "error"
                and any(
                    n in (str(i.get("where")) + str(i.get("problem"))) for n in watch
                )
            ]
            if mine:
                print("\nVALIDATION ERRORS in this rollout's entities:")
                for i in mine:
                    print(f"  {i['where']}: {i['problem']}")
                sys.exit(1)
            others = sum(1 for i in summary["issues"] if i.get("severity") == "error")
            print(
                "\nvalidation: clean for this rollout "
                f"({others} pre-existing error(s) elsewhere, untouched)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
