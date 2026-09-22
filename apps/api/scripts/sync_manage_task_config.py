"""Install `norm.manage_task` and retire the five tools it replaces.

Sep 2026. Two findings from 60 days of production tool_calls drove this:

1. The four task verbs — create_automated_task, update_automated_task,
   update_task_config, set_override — are one object ("this scheduled
   task") in four schemas. They cost ~780 tokens across four agents and
   were called 13 times in 60 days, while being the most confusable set
   on the norm surface: "change how this task behaves" legitimately
   described three of them. They merge into `manage_task(op=…)`; every
   op reaches the handler that always did the work.

2. `get_workflow_mode` was called 75 times across 75 threads — exactly
   once per conversation — because both invoice playbooks opened with
   "Step 0: call get_workflow_mode". It reads `User.workflow_modes`,
   which the engine already has, and for receiving it returned a
   per-user value that is no longer the effective setting (that moved to
   the venue). The mode is now STATED in the prompt
   (prompt_builder.workflow_modes_guidance), so the tool goes. Its
   writer, `set_workflow_mode`, stays — that is how a user's choice is
   saved.

ORDER: deploy the API first. The prompt guidance, the manage_task
handler and the MCP/read-only denylist entries all ship with the code;
this script only moves config. Then run
scripts/sync_invoice_receiving_config.py to reseed the two invoice
playbooks (their step 0 and tool_filters changed in the same commit).

Usage:
    uv run python scripts/sync_manage_task_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "norm"

#: Retired → what replaces them on the agent menu. get_workflow_mode has no
#: replacement tool by design: its answer is context now.
RETIRED = {
    "create_automated_task": "manage_task",
    "update_automated_task": "manage_task",
    "update_task_config": "manage_task",
    "set_override": "manage_task",
    "get_workflow_mode": None,
}

#: Sentence-level prose swaps, verified against the LIVE rows 22 Sep 2026.
#: The same edits are in scripts/sync_invoice_receiving_config.py (the seed
#: that owns these playbooks) so a replay keeps them; they are applied here
#: too because that seed also rewrites two loadedhub tool rows, and running
#: it just for prose risks reverting another session's work on those.
PLAYBOOK_PATCHES: dict[str, list[tuple[str, str]]] = {
    "receive_loadedhub_invoices": [
        (
            "This workflow honours a per-user run mode, and you must NOT run "
            "the review until it is set:\n0. Call get_workflow_mode with "
            'workflow="review_and_receive_invoices".\n   - If it returns mode '
            '"unset": DO NOT run the review.',
            "This workflow honours a run mode, and you must NOT run the "
            "review until it is set:\n0. The current mode is already in your "
            'context, under "Run modes" — read it there. There is no tool to '
            "fetch it and you do not need one.\n   - If the mode is "
            '"unset": DO NOT run the review.',
        ),
        (
            "   - If it returns a set mode: go straight to step 1 (the review "
            "runs in that mode automatically).",
            "   - If a mode is set: go straight to step 1 (the review runs in "
            "that mode automatically).",
        ),
        (
            'call update_task_config with key "require_valid_po" and value false',
            'call manage_task with op="set_config", key "require_valid_po" and '
            "value false",
        ),
    ],
    "reconcile_received_invoices": [
        (
            "0. Call get_workflow_mode with workflow="
            '"reconcile_received_invoices".\n   - If it returns mode "unset": '
            "DO NOT run the tool.",
            "0. The current mode is already in your context, under "
            '"Run modes" — read it there. There is no tool to fetch it and '
            'you do not need one.\n   - If the mode is "unset": DO NOT run '
            "the tool.",
        ),
        (
            "   - If it returns a set mode: go straight to step 1.",
            "   - If a mode is set: go straight to step 1.",
        ),
    ],
}

TOOL = {
    "action": "manage_task",
    # GET like the four it replaces — but every op mutates, so it is on the
    # DENY list in scripts/sync_read_only_flags.py and read_only stays false:
    # a delegated sub-agent must not be able to rewrite a schedule.
    "method": "GET",
    "read_only": False,
    "description": (
        "Create or change an automated (scheduled) task. `op` picks the "
        "action: 'create' schedules new work from an `intent`; 'update' "
        "changes an existing task's instruction, schedule or status; "
        "'set_config' sets a persistent setting on it (`key`/`value`); "
        "'set_override' gives a one-off instruction that applies to the NEXT "
        "RUN ONLY and then clears itself. Inside a task's own conversation "
        "the task is resolved automatically — omit task_id."
    ),
    "required_fields": ["op"],
    "optional_fields": [
        "intent",
        "agent_slug",
        "schedule",
        "task_id",
        "title",
        "description",
        "prompt",
        "schedule_type",
        "schedule_config",
        "status",
        "key",
        "value",
        "instruction",
    ],
    # Kept deliberately tight. A merged tool only pays for itself if its one
    # schema is smaller than the four it replaces: the first draft of this
    # block was longer than all four together and made marketing (which had
    # only two of them enabled) 94 tokens WORSE. Every line that survived
    # here is one that prevents a known failure.
    "field_descriptions": {
        "op": (
            "create | update | set_config | set_override. No default: "
            "guessing 'create' for a change to an existing task leaves a "
            "duplicate draft."
        ),
        "intent": (
            "create: what the task should do, with venue/staff/item names and "
            "whether to email results."
        ),
        "agent_slug": "create: hr, procurement or reports. Auto-detected if omitted.",
        "schedule": (
            'create: natural language — "daily at 9am", "every monday at 8am". '
            "Omit for manual-only."
        ),
        "task_id": "update: omit inside the task's own conversation.",
        "prompt": (
            "update: the COMPLETE replacement instruction — a fragment "
            "replaces the rest."
        ),
        "title": "update: new title",
        "description": "update: new short description",
        "schedule_type": "update: daily | weekly | monthly | manual",
        "schedule_config": (
            'update: e.g. {"hour": 9, "minute": 0} plus day_of_week or day_of_month'
        ),
        "status": "update: active | paused | draft",
        "key": "set_config: setting name, e.g. 'require_valid_po'",
        "value": "set_config: value, or null to remove the key",
        "instruction": "set_override: applies to the next run only, then clears",
    },
    "enabled": True,
}


def merge_caps(caps: list[dict]) -> tuple[list[dict], bool]:
    """Rewrite one binding's capability list. Returns (new_caps, changed).

    The merged entry is enabled if ANY entry it replaces was enabled.
    Marketing lists update_task_config (disabled) BEFORE
    create_automated_task (enabled); inheriting the first entry's flag
    would hand it a disabled manage_task and silently take away task
    creation. get_workflow_mode has no replacement — it just goes.
    """
    merged_enabled = any(
        c.get("enabled", True)
        for c in caps
        if RETIRED.get(c.get("action")) == "manage_task"
    )
    new_caps: list[dict] = []
    changed = False
    for c in caps:
        action = c.get("action")
        if action not in RETIRED:
            new_caps.append(c)
            continue
        changed = True
        replacement = RETIRED[action]
        if replacement and not any(x.get("action") == replacement for x in new_caps):
            new_caps.append(
                {
                    **c,
                    "action": replacement,
                    "label": "Manage Task",
                    "enabled": merged_enabled,
                }
            )
    return new_caps, changed


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConnectionBinding,
        ConnectionSpec,
        McpCapability,
    )
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"{CONNECTOR} ConnectionSpec not found")

        # ── 1. Install manage_task ───────────────────────────────────────
        tools = [dict(t) for t in (spec.tools or [])]
        spec_changed = False
        idx = next(
            (i for i, t in enumerate(tools) if t.get("action") == TOOL["action"]), None
        )
        if idx is None:
            tools.append(dict(TOOL))
            changes.append("added manage_task")
            spec_changed = True
        elif tools[idx] != TOOL:
            keep = tools[idx].get("added_at")
            row = dict(TOOL)
            if keep:
                row["added_at"] = keep
            tools[idx] = row
            changes.append("updated manage_task")
            spec_changed = True

        # ── 2. Retire the replaced rows ──────────────────────────────────
        before = len(tools)
        tools = [t for t in tools if t.get("action") not in RETIRED]
        if len(tools) != before:
            changes.append(
                f"removed {before - len(tools)} retired row(s): "
                + ", ".join(sorted(RETIRED))
            )
            spec_changed = True
        # Explicitly, not `if changes:` — that would also fire for a binding or
        # playbook edit recorded later and bump the spec version for nothing.
        if spec_changed:
            spec.tools = tools
            flag_modified(spec, "tools")
            spec.version = (spec.version or 0) + 1

        # ── 3. Swap bindings ─────────────────────────────────────────────
        for b in (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.enabled == True,  # noqa: E712
                AgentConnectionBinding.connector_name == CONNECTOR,
            )
            .all()
        ):
            caps = [dict(c) for c in (b.capabilities or [])]
            if not caps:
                continue
            new_caps, touched = merge_caps(caps)
            if touched:
                b.capabilities = new_caps
                flag_modified(b, "capabilities")
                changes.append(
                    f"binding {b.agent_slug}/{CONNECTOR}: "
                    f"{len(caps)} -> {len(new_caps)} caps"
                )

        # ── 4. Playbook tool_filters ─────────────────────────────────────
        # Eight playbooks list create_automated_task. A filter naming a tool
        # that no longer exists does not error at runtime — the entry is
        # silently dropped and the playbook's instructions reference a
        # capability the agent no longer has. That is the b9bda2c1 incident
        # class, and it is a hard validator error now.
        from app.db.config_models import Playbook

        for p in db.query(Playbook).all():
            filt = list(p.tool_filter or [])
            if not any(n in RETIRED for n in filt):
                continue
            new_filt: list[str] = []
            for name in filt:
                if name not in RETIRED:
                    new_filt.append(name)
                    continue
                replacement = RETIRED[name]
                if replacement and replacement not in new_filt:
                    new_filt.append(replacement)
            p.tool_filter = new_filt
            flag_modified(p, "tool_filter")
            changes.append(f"playbook {p.slug}: filter -> {new_filt}")

        for p in db.query(Playbook).all():
            patches = PLAYBOOK_PATCHES.get(p.slug)
            if not patches:
                continue
            text = p.instructions or ""
            for old, new in patches:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"playbook {p.slug}: prose swapped")
            if text != (p.instructions or ""):
                p.instructions = text
            # A playbook that NAMES a tool must also admit it: tool_filter
            # narrows the menu, so instructions referencing a tool outside the
            # filter describe a capability the agent cannot reach.
            if "manage_task" in text:
                filt = list(p.tool_filter or [])
                if filt and "manage_task" not in filt:
                    p.tool_filter = filt + ["manage_task"]
                    flag_modified(p, "tool_filter")
                    changes.append(f"playbook {p.slug}: filter += manage_task")

        # ── 5. MCP rows: manage_task is denylisted, the old ones go ──────
        for m in db.query(McpCapability).all():
            if m.target == CONNECTOR and m.action in RETIRED:
                db.delete(m)
                changes.append(f"mcp row deleted: {CONNECTOR}/{m.action}")

        if dry_run:
            db.rollback()
            print("DRY RUN — would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")

        # ── 6. Validate this rollout's entities ──────────────────────────
        if not dry_run:
            from app.services.config_validator import validate_config

            watch = set(RETIRED) | {"manage_task"}
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
