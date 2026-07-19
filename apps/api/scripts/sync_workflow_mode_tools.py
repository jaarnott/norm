"""Sync the get/set_workflow_mode internal tools into the `norm` ConnectorSpec.

These agent-callable tools let a conversation read and change the caller's
per-workflow run mode (approve_all / approve_fixes / autopilot). Their handlers
live in app/agents/internal_tools.py; this adds their schema to the internal
`norm` spec (which lives only in the config DB) so the LLM can call them.

Idempotent — upserts by action. Run against the shared config DB.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOLS = [
    {
        "action": "get_workflow_mode",
        "method": "GET",
        "description": (
            "Get the current user's run mode for a workflow "
            "(approve_all / approve_fixes / autopilot, or 'unset')."
        ),
        "required_fields": ["workflow"],
        "field_descriptions": {
            "workflow": "Workflow key, e.g. 'review_and_receive_invoices' or "
            "'reconcile_received_invoices'.",
        },
    },
    {
        "action": "set_workflow_mode",
        "method": "GET",  # internal write; auto-executes like update_task_config
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
    from app.db.config_models import AgentConnectorBinding, ConnectorSpec
    from sqlalchemy.orm.attributes import flag_modified

    db = _ConfigSessionLocal()
    spec = (
        db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == "norm").first()
    )
    if not spec:
        raise SystemExit("norm ConnectorSpec not found in config DB")

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

    # Bind the two tools to the procurement agent's norm connector so the
    # invoice playbooks can call them (tool_filter narrows from bound tools).
    binding = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == "procurement",
            AgentConnectorBinding.connector_name == "norm",
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
