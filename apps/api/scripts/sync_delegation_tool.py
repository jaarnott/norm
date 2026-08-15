"""Register norm__delegate_to_agent and grant it to the agents that need it.

A tool only reaches a model when three things line up (prompt_builder._collect_tools):
the @register decorator in code, a row in the connector spec's `tools` JSON, and
an enabled capability on the calling agent's AgentConnectorBinding. This script
does the second and third.

Grants are deliberately narrow. Delegation is decentralised — there is no
orchestrator, so "who may consult whom" *is* the binding list, and every grant
is a standing invitation for one agent to spend tokens on another's behalf.
Start with the pairs there is a real question for, and widen on evidence.

Usage:
    .venv/bin/python scripts/sync_delegation_tool.py --dry-run
    .venv/bin/python scripts/sync_delegation_tool.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "norm"
ACTION = "delegate_to_agent"

TOOL = {
    "action": ACTION,
    "method": "GET",
    # Consulting is itself a read: the sub-agent is handed read-only tools.
    # This also keeps the delegation from needing its own approval — the model
    # asking a question is not something a human should have to authorise.
    "read_only": True,
    "description": (
        "Ask another Norm agent a question and get its answer. Use this when you "
        "need information that belongs to another domain — rostered hours and "
        "attendance (time_attendance), sales and reporting (reports), stock, "
        "suppliers and invoices (procurement), staff, jobs and candidates (hr), "
        "campaigns and social (marketing). The agent you ask has its own tools "
        "and knows its own domain's rules, so ask for what you want to know, not "
        "how to get it. It answers from data only and cannot change anything — "
        "if something needs doing, do it yourself afterwards. It cannot see this "
        "conversation, so put everything it needs in the question."
    ),
    "required_fields": ["target", "question"],
    "optional_fields": ["context"],
    "field_descriptions": {
        "target": (
            "Agent to ask: procurement, hr, reports, time_attendance or "
            "marketing. Optionally narrow it to a playbook with "
            "'agent/playbook_slug'."
        ),
        "question": (
            "The question, written so it stands alone — the other agent sees "
            "nothing of this conversation. Name the venue and the period."
        ),
        "context": (
            "Optional facts you already know, so it need not look them up again."
        ),
    },
    "field_schema": {
        "target": {"type": "string"},
        "question": {"type": "string"},
        "context": {"type": "string"},
    },
    # The answer is prose; there is nothing to slim.
    "max_result_chars": 8000,
}

# agent_slug -> why it needs to ask
#
# Widened to every agent on 15 Aug 2026 (thread 46c17508). The original three
# were the pairs there was a known question for, and the principle above still
# holds — but the evidence that arrived is that ANY agent can be handed a
# conversation mid-flight by the router, so any agent without this is a place a
# conversation can go and not come back from. A wage-cost question landed on
# hr, which has no hours or pay data and no way to ask anyone who does; the
# user had to route it by hand. A grant costs tokens only when used; not having
# one costs the user the answer.
GRANTS = {
    "procurement": "ordering depends on rostered hours and on sales trends",
    "reports": "commentary needs stock and labour context, not just numbers",
    "time_attendance": "rostering against sales and labour cost",
    "hr": "staffing questions arrive here with wage and roster data elsewhere",
    "executive_chef": "menu and recipe costing depends on stock and sales",
    "marketing": "campaign planning needs sales and what is actually in stock",
    "app_builder": "an app's data comes from every other domain",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectorBinding, ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changes = []

        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        existing = next((t for t in tools if t.get("action") == ACTION), None)
        if existing:
            if existing != TOOL:
                changes.append(f"update spec tool {ACTION}")
                if not args.dry_run:
                    tools[tools.index(existing)] = TOOL
                    spec.tools = tools
                    flag_modified(spec, "tools")
        else:
            changes.append(f"add spec tool {ACTION}")
            if not args.dry_run:
                tools.append(TOOL)
                spec.tools = tools
                flag_modified(spec, "tools")

        for slug, why in GRANTS.items():
            binding = (
                db.query(AgentConnectorBinding)
                .filter(
                    AgentConnectorBinding.agent_slug == slug,
                    AgentConnectorBinding.connector_name == CONNECTOR,
                )
                .first()
            )
            if not binding:
                # executive_chef had no `norm` binding at all, so it could not
                # be granted anything — and it is one of the agents the router
                # actually stranded a conversation on. Create the binding with
                # just this action rather than skipping.
                changes.append(f"create '{CONNECTOR}' binding for {slug} ({why})")
                if not args.dry_run:
                    db.add(
                        AgentConnectorBinding(
                            agent_slug=slug,
                            connector_name=CONNECTOR,
                            capabilities=[{"action": ACTION, "enabled": True}],
                            enabled=True,
                        )
                    )
                continue
            caps = list(binding.capabilities or [])
            if any(c.get("action") == ACTION for c in caps):
                continue
            changes.append(f"grant {ACTION} to {slug} ({why})")
            if not args.dry_run:
                caps.append({"action": ACTION, "enabled": True})
                binding.capabilities = caps
                flag_modified(binding, "capabilities")

        if not changes:
            print("nothing to do")
            return
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
