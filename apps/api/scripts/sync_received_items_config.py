"""Register the `get_received_items_for_period` consolidator on the loadedhub spec.

`get_received_invoices_for_period` answers "which invoices landed"; this answers
the question people actually ask — how much of an ITEM did we take in, what did
it cost, and did the price move. The data was always there, one level down
inside each invoice's `lines`, which meant handing the model 117 invoices / 552
lines for a venue-fortnight and asking it to flatten and add up.

Reads only. It composes three existing actions rather than adding any:

  loadedhub.get_received_invoices  the feed (property=Received, ISO datetimes,
                                   lines carry unitRatio — so quantities can be
                                   made comparable without a catalogue lookup)
  loadedhub.get_stock_items        id -> name, ONE call; the feed carries no
                                   item name at all, only ids and the supplier's
                                   own line text
  loadedhub.get_stock_units        (name, ratio) -> stock dimension, so a base
                                   quantity can be labelled kg / L / each

Deliberately no new connector action: `get_received_invoices` is already what
`get_received_invoices_for_period` wraps, so both tools read the same source and
their totals can be cross-checked against each other.

It is also switched ON here, in the two places that gate reachability, so the
whole configuration is reproducible from this file rather than from a one-off
poke at the shared config DB:

  procurement agent   AgentConnectionBinding.capabilities (alongside the
                      invoice-level get_received_invoices_for_period)
  Claude / MCP        McpCapability row, scope mcp:orders:read — the same
                      scope its invoice-level sibling already uses

Idempotent — safe to re-run; reports only what changed.

Usage:
    .venv/bin/python scripts/sync_received_items_config.py --dry-run
    .venv/bin/python scripts/sync_received_items_config.py
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
ACTION = "get_received_items_for_period"
AGENT_SLUG = "procurement"
# The item-level view of exactly the data get_received_invoices_for_period
# already exposes under this scope, so it belongs behind the same one.
MCP_SCOPES = ["mcp:orders:read"]
CAPABILITY_LABEL = (
    "Stock items received over a period given in plain English, aggregated per "
    "item (or per item+supplier, or per line — see group_by). Quantities are "
    "converted to each item's base unit before summing, so mixed pack sizes add "
    "up correctly, and unit costs are normalised per base unit so a pack-size "
    "change is not reported as a price change. Item names come from the stock "
    "catalogue, not the supplier's line text. Norm resolves the period using "
    "this venue's trading day — do not calculate timestamps yourself; the result "
    "always states which window was used, so report that window alongside the "
    "numbers."
)
FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "received_items_for_period.py"
)
SHAPES_PATH = FUNCTION_CODE_PATH.parent / "shapes.json"

TOOL = {
    "action": ACTION,
    "method": "GET",  # consolidator dispatch; reads only, writes nothing ever
    # Tightened 7 Oct 2026: each rule stated once (the tool list is ~65% of
    # every prompt).
    "description": (
        "Stock received over a period, from invoices — spend and quantity by item, or "
        "one item's purchase history. THE tool for 'how much of X did we buy' — never "
        "page invoices and sum lines yourself. group_by: 'item' (default; per item: "
        "base-unit quantity, spend, invoice and supplier counts, top rows by spend) | "
        "'history' (per invoice line — date, supplier, invoice, quantity, unit, unit "
        "cost — for ONE item or supplier; needs item_id, query or suppliers) | "
        "'item_supplier' (each supplier's unit cost avg/min/max/last — 'are we paying "
        "different prices') | 'group' / 'super_group' (spend by stock group / Loaded "
        "category). Quantities use each line's unit ratio, so 6x1L and 1L add up. For "
        "the group, venues='all' (or a list) gives ONE merged ranking with per-venue "
        "splits — never fetch venue by venue. Ask ONCE for the whole period."
    ),
    "required_fields": [],
    "optional_fields": [
        "period",
        "start",
        "end",
        "confirmed_by_user",
        "group_by",
        "suppliers",
        "item_id",
        "query",
        "group",
        "limit",
        "venues",
    ],
    "field_descriptions": {
        "period": (
            "Plain English — 'last week', 'this month'. Prefer it to start/end; never "
            "work out dates yourself."
        ),
        "start": "Only for exact clock times the user asked for: ISO 8601 with offset.",
        "end": "Window end; same rule as start.",
        "group_by": "The mode; it sets the row shape (default item).",
        "item_id": "One stock item id.",
        "query": "Items whose catalogue name contains this.",
        "group": "Lines whose stock group name contains this.",
        "limit": "Max rows (default 25); the rest roll into '(others)'.",
        "venues": "'all', or a list of venue names — one merged answer.",
    },
    "field_schema": {
        "group_by": {
            "type": "string",
            "enum": ["item", "history", "item_supplier", "group", "super_group"],
            "description": "The mode; it sets the row shape (default item)",
        },
        "suppliers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Restrict to these supplier names",
        },
        # Same shape as get_sales' `venues`: "all" or a list of names.
        "venues": {
            "description": "'all', or a list of venue names — one merged answer"
        },
        "limit": {
            "type": "integer",
            "description": "Max rows (default 25); the rest roll into '(others)'",
        },
        "confirmed_by_user": {
            "type": "boolean",
            "description": (
                "Only for an explicit start/end that is not a trading day, and "
                "only when the user really did ask for those clock times."
            ),
        },
    },
    # 40k (was 100k). An item row is ~110 chars now and a history row ~200, so
    # 25 rows is ~5 kB and the 86-row report that produced the half-finished
    # table in Sep 2026 is ~18 kB with venue splits. 100k existed to let 130
    # full-fat rows through, which was never a sensible thing to hand a model.
    "max_result_chars": 40_000,
    "consolidator_config": {
        # function_code injected from FUNCTION_CODE_PATH at sync time
        # 6 venues x (invoices, catalogue, units[, groups]) + dates + venue list.
        "max_api_calls": 50,  # 2 + 5 per venue (incl. one catalogue retry)
        # Reads only — the sandbox refuses any non-GET action with this empty.
        "allowed_write_actions": [],
    },
    "read_only": True,
}


def _switch_on(db, dry_run: bool):
    """Make the tool reachable: the procurement agent, and Claude via MCP.

    Both are fail-closed by design — a consolidator that exists but is bound to
    nobody is invisible, which is the correct default but a silent one. Doing it
    here keeps the whole configuration in one reviewed file.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectionBinding, McpCapability
    from app.mcp.scopes import MCP_SCOPES as _VOCAB

    unknown = [s for s in MCP_SCOPES if s not in _VOCAB]
    if unknown:
        raise SystemExit(f"unknown MCP scope(s): {', '.join(unknown)}")

    changes = []

    binding = (
        db.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.agent_slug == AGENT_SLUG,
            AgentConnectionBinding.connector_name == CONNECTOR,
        )
        .first()
    )
    if not binding:
        raise SystemExit(
            f"no {CONNECTOR} binding for agent '{AGENT_SLUG}' — create it first"
        )
    caps = list(binding.capabilities or [])
    entry = {"action": ACTION, "label": CAPABILITY_LABEL, "enabled": True}
    at = next(
        (
            i
            for i, c in enumerate(caps)
            if isinstance(c, dict) and c.get("action") == ACTION
        ),
        None,
    )
    if at is None:
        caps.append(entry)
        changes.append(f"bound {ACTION} to the {AGENT_SLUG} agent")
    elif caps[at] != entry:
        caps[at] = entry
        changes.append(f"updated the {AGENT_SLUG} agent's {ACTION} capability")
    if changes and not dry_run:
        binding.capabilities = caps
        flag_modified(binding, "capabilities")

    row = (
        db.query(McpCapability)
        .filter(
            McpCapability.kind == "connector",
            McpCapability.target == CONNECTOR,
            McpCapability.action == ACTION,
        )
        .first()
    )
    if not row:
        changes.append(f"exposed {ACTION} over MCP ({', '.join(MCP_SCOPES)})")
        if not dry_run:
            db.add(
                McpCapability(
                    kind="connector",
                    target=CONNECTOR,
                    action=ACTION,
                    scopes=list(MCP_SCOPES),
                    enabled=True,
                )
            )
    elif not row.enabled or list(row.scopes or []) != list(MCP_SCOPES):
        changes.append(f"updated the MCP capability for {ACTION}")
        if not dry_run:
            row.enabled = True
            row.scopes = list(MCP_SCOPES)
            flag_modified(row, "scopes")

    return changes


def build_tool() -> dict:
    """The row as installed: TOOL plus its code and its shapes (the response
    shaping that moved into the tool in Sep 2026 — replacing the row without
    them would quietly undo that)."""
    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
        "shapes": json.loads(SHAPES_PATH.read_text(encoding="utf-8"))[
            "loadedhub.get_received_items_for_period"
        ],
    }
    return tool


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    tool = build_tool()

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"{CONNECTOR} ConnectionSpec not found in config DB")

        tools = list(spec.tools or [])
        by_action = {t.get("action"): i for i, t in enumerate(tools)}

        # The consolidator composes these; without them every call dies at
        # runtime with "Tool not found" and the tool degrades silently.
        missing = [
            a
            for a in ("get_received_invoices", "get_stock_items_raw", "get_stock_units")
            if a not in by_action
        ]
        if missing:
            raise SystemExit(
                f"{CONNECTOR} spec is missing the actions this consolidator "
                f"composes: {', '.join(missing)}"
            )

        if ACTION in by_action:
            if tools[by_action[ACTION]] == tool:
                change = "tool unchanged"
            else:
                tools[by_action[ACTION]] = tool
                change = f"updated tool {ACTION}"
        else:
            tools.append(tool)
            change = f"added tool {ACTION}"

        if dry_run:
            switched = _switch_on(db, dry_run=True)
            db.rollback()
            if change == "tool unchanged" and not switched:
                print("Already in sync.")
                return
            if change != "tool unchanged":
                print("Would apply:", change)
            for line in switched:
                print("Would apply:", line)
            return

        switched = _switch_on(db, dry_run=False)
        if change == "tool unchanged" and not switched:
            print("Already in sync.")
            return

        spec.tools = tools
        flag_modified(spec, "tools")
        db.commit()
        for line in switched:
            print("Applied:", line)
        if change != "tool unchanged":
            print("Applied:", change)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(dry_run=parser.parse_args().dry_run)
