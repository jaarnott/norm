"""Advertise the memory tools (`remember`, `recall_memory`) on the `norm` spec.

The handlers already exist (`internal_tools._remember` / `_recall_memory`) and
recall is already injected into every turn, but the tools were never advertised
to the model, so nothing was ever saved — production had zero memories. This
wires the write path: it adds the two tool definitions to the `norm` connector
spec AND enables them in each agent's binding capabilities.

Both steps are required. The agent's toolset is built only from advertised
`spec.tools` (`prompt_builder._collect_tools`), and the `norm` binding uses a
per-agent capability allow-list — so a tool the binding does not list is filtered
out even when it is on the spec. Mirrors `sync_show_connect_tool.py`.

The config DB is shared across every environment, so committing this reaches
production immediately. Apply it as part of the coordinated deploy, once the
`memory_guidance` prompt block and the `needs_confirmation` change are also live.
Dry-run first.

Usage:
    .venv/bin/python scripts/sync_memory_tools.py --dry-run
    .venv/bin/python scripts/sync_memory_tools.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

REMEMBER = {
    "action": "remember",
    "method": "POST",
    "description": (
        "Save a durable fact about this user or organisation so future "
        "conversations already know it — this is how Norm builds lasting "
        "memory, like ChatGPT and Claude. Use it proactively when the user "
        "states something that will still be true next week, or corrects you in "
        "a way that should stick: standing preferences, the group's vocabulary, "
        "lasting operational context (e.g. a venue closed for repairs), or a "
        "correction about how things really are. Set `type` to one of "
        "vocabulary | preference | context | correction. Set `scope` to 'user' "
        "for one person's preference or 'org' for a fact about the business (an "
        "org fact about a single venue should also pass `venue_id`). Do NOT "
        "save one-off or right-now details, anything that defines how a figure "
        "is calculated, or anything that gates money or approval — those are "
        "refused. A refusal is authoritative: do not rephrase and retry it."
    ),
    "required_fields": ["type", "title", "body"],
    "optional_fields": ["why", "how_to_apply", "scope", "venue_id", "trigger"],
    "field_schema": {
        "type": {
            "type": "string",
            "description": ("One of: vocabulary, preference, context, correction."),
        },
        "title": {
            "type": "string",
            "description": "A short label for the fact (e.g. 'Mr Murdochs closed').",
        },
        "body": {
            "type": "string",
            "description": "The fact itself, in one or two sentences.",
        },
        "why": {
            "type": "string",
            "description": "Optional: why this matters, if not obvious.",
        },
        "how_to_apply": {
            "type": "string",
            "description": "Optional: how a future answer should use this fact.",
        },
        "scope": {
            "type": "string",
            "description": (
                "'user' for one person's preference; 'org' for a fact about the "
                "business. Defaults are inferred from the type; you may narrow "
                "to 'user' but not widen to 'org'."
            ),
        },
        "venue_id": {
            "type": "string",
            "description": "Optional: the venue id when the fact is about one venue.",
        },
        "trigger": {
            "type": "string",
            "description": (
                "How this came up — usually leave unset (defaults to 'explicit')."
            ),
        },
    },
}

RECALL_MEMORY = {
    "action": "recall_memory",
    "method": "GET",
    "read_only": True,
    "description": (
        "Fetch the full detail of one memory listed in the "
        "'[What Norm has learned]' background context. Pass the short id shown "
        "in that list as `memory_id`. Use this when you need the full body of a "
        "remembered fact, not just its title."
    ),
    "required_fields": ["memory_id"],
    "field_schema": {
        "memory_id": {
            "type": "string",
            "description": "The short id shown in the learned-memory list.",
        }
    },
}

MEMORY_TOOLS = [REMEMBER, RECALL_MEMORY]
MEMORY_ACTIONS = {t["action"] for t in MEMORY_TOOLS}

# Human-readable labels for the binding capability entries.
CAPABILITY_LABELS = {
    "remember": "Remember a durable fact about the user or organisation.",
    "recall_memory": "Recall the full detail of a remembered fact.",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectionBinding, ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        # 1. Advertise the tools on the `norm` spec.
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "norm")
            .first()
        )
        if not spec:
            print("no 'norm' connector spec found — aborting")
            return

        tools = [t for t in (spec.tools or []) if t.get("action") not in MEMORY_ACTIONS]
        before = len(spec.tools or [])
        tools.extend(MEMORY_TOOLS)
        print(
            f"norm spec: {before} tools -> {len(tools)} "
            f"(ensuring {sorted(MEMORY_ACTIONS)})"
        )

        # 2. Enable the capabilities on every `norm` agent binding.
        bindings = (
            db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.connector_name == "norm")
            .all()
        )
        binding_changes = []
        for binding in bindings:
            caps = list(binding.capabilities or [])
            have = {c.get("action") for c in caps}
            added = []
            for action in ("remember", "recall_memory"):
                if action not in have:
                    caps.append(
                        {
                            "action": action,
                            "label": CAPABILITY_LABELS[action],
                            "enabled": True,
                        }
                    )
                    added.append(action)
            if added:
                binding_changes.append((binding, caps, added))
                print(f"  binding {binding.agent_slug}: + {added}")
            else:
                print(f"  binding {binding.agent_slug}: already has both")

        if args.dry_run:
            print("(dry run — nothing written)")
            return

        spec.tools = tools
        flag_modified(spec, "tools")
        for binding, caps, _added in binding_changes:
            binding.capabilities = caps
            flag_modified(binding, "capabilities")
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
