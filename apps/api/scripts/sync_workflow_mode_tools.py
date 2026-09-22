"""Sync the set_workflow_mode internal tool into the `norm` ConnectionSpec.

This agent-callable tool lets a conversation change the caller's per-workflow
run mode (approve_all / approve_fixes / autopilot). Its handler lives in
app/agents/internal_tools.py; this adds its schema to the internal `norm` spec
(which lives only in the config DB) so the LLM can call it.

The matching READER, `get_workflow_mode`, was retired in Sep 2026 and is
deliberately not published here: it was called 75 times across 75 threads —
once per conversation — to read a value the engine already had, and for
receiving it returned a per-user setting that is no longer the effective one.
The mode is stated in the prompt instead (prompt_builder.workflow_modes_guidance),
and scripts/sync_manage_task_config.py removes the row. Re-adding it here
would put that per-conversation round-trip straight back.

Idempotent — upserts by action. Run against the shared config DB.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOLS = [
    {
        "action": "set_workflow_mode",
        "method": "GET",  # internal write; auto-executes, and on the read_only DENY list
        "description": (
            "Set the current user's run mode for a workflow. Use when the user "
            "chooses or changes how much Norm should do on its own for that "
            "workflow. Modes: approve_all, approve_fixes, autopilot."
        ),
        "required_fields": ["workflow", "mode"],
        "field_descriptions": {
            "workflow": "Workflow key, e.g. 'review_and_receive_invoices'.",
            "mode": "One of: approve_all, approve_fixes, autopilot.",
        },
    },
]


def main(dry_run: bool = False) -> None:
    from app.db.engine import _ConfigSessionLocal
    from app.db.config_models import AgentConnectionBinding, ConnectionSpec
    from sqlalchemy.orm.attributes import flag_modified

    db = _ConfigSessionLocal()
    spec = (
        db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == "norm").first()
    )
    if not spec:
        raise SystemExit("norm ConnectionSpec not found in config DB")

    tools = list(spec.tools or [])
    by_action = {t.get("action"): i for i, t in enumerate(tools)}
    changed = []
    for tool in TOOLS:
        action = tool["action"]
        if action in by_action:
            if tools[by_action[action]] != tool:
                tools[by_action[action]] = tool
                changed.append(f"updated tool {action}")
        else:
            tools.append(tool)
            changed.append(f"added tool {action}")

    # Bind the tool to the procurement agent's norm connector so the invoice
    # playbooks can call it (tool_filter narrows from bound tools).
    binding = (
        db.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.agent_slug == "procurement",
            AgentConnectionBinding.connector_name == "norm",
        )
        .first()
    )
    if not binding:
        raise SystemExit("procurement/norm binding not found in config DB")
    caps = list(binding.capabilities or [])
    existing = {c.get("action") if isinstance(c, dict) else c for c in caps}
    for tool in TOOLS:
        action = tool["action"]
        if action not in existing:
            caps.append(
                {"action": action, "label": tool["description"], "enabled": True}
            )
            changed.append(f"bound capability {action} to procurement/norm")

    if not changed:
        print("norm workflow-mode tools already up to date")
        return
    if dry_run:
        print("DRY RUN — would apply:", *changed, sep="\n  ")
        return
    spec.tools = tools
    binding.capabilities = caps
    flag_modified(spec, "tools")
    flag_modified(binding, "capabilities")
    db.commit()
    print("Applied:", *changed, sep="\n  ")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
