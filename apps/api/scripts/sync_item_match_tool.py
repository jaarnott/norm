"""Sync the norm.match_stock_items LLM function into the `norm` ConnectorSpec.

An "LLM function": an internal handler (app/agents/internal_tools.py) whose body
is one schema-bound call_llm — the resolve_dates pattern. The review engine
(review_and_receive_invoices) calls it via call_api("norm", "match_stock_items")
to attach item-match suggestions to its artifact; the spec row must exist because
the sandbox's call_api resolves the tool def before routing to the handler.

Deliberately bound to NO agent — a building-block tool per
docs/tool-architecture-strategy.md (callable only by engine code). Publishing it
to agents/MCP later is a binding flip, not new machinery.

Idempotent — upserts by action. Run against the shared config DB.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOL = {
    "action": "match_stock_items",
    "method": "GET",
    "description": (
        "[engine-only LLM function] Match NEW supplier-invoice lines to the "
        "venue's stock catalogue: an existing item to LINK, else a normalized "
        "name + group to CREATE. Called by review_and_receive_invoices via "
        "call_api; not bound to any agent."
    ),
    "required_fields": ["lines"],
    "optional_fields": ["venue", "venue_id"],
    "field_descriptions": {
        "lines": "List of {id, description, code, brand, unit} for unlinked lines.",
        "venue": "Venue name (resolved to an id); or pass venue_id directly.",
    },
    "read_only": True,
}


def main(dry_run: bool = False) -> None:
    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal
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
    if TOOL["action"] in by_action:
        if tools[by_action[TOOL["action"]]] != TOOL:
            tools[by_action[TOOL["action"]]] = TOOL
            changed.append(f"updated tool {TOOL['action']}")
    else:
        tools.append(TOOL)
        changed.append(f"added tool {TOOL['action']}")

    if not changed:
        print("norm.match_stock_items already up to date")
        return
    if dry_run:
        print("DRY RUN — would apply:", *changed, sep="\n  ")
        return
    spec.tools = tools
    flag_modified(spec, "tools")
    db.commit()
    print("Applied:", *changed, sep="\n  ")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
