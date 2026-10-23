"""Install Norm's receiving-rule tools (Oct 2026).

An organisation's own rules for the invoices it receives — the due date first
(services/receiving_rules, docs/plans/receiving-rules.md). Norm reads them with
``norm.get_receiving_rules`` and an owner or manager changes one with
``norm.set_receiving_rule`` (always shown on a card first). Until this, Norm
offered to "save the rule" as a note, which never changes how an invoice is
received (The Lott, thread 3dd88935, 22 Oct 2026).

Config edits, in one transaction, backed up first; each only when it differs:
- the two tool rows on the ``norm`` connector;
- the Loaded Stock App claims both. (Its note is left alone: App notes are capped at 300
  characters each and 1,500 in all, and the set tool's own description already says a
  remembered note changes nothing about receiving.)

**Run AFTER the code is deployed** — the rows name handlers only the new code
has. The config DB is shared by every environment.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_receiving_rules_tools.py --dry-run
    uv run python scripts/sync_receiving_rules_tools.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import apply_row_updates  # noqa: E402

from app.services.receiving_rules import (  # noqa: E402
    DUE_BASES,
    DUE_WHEN,
    KINDS,
    WEEKDAYS,
)

CONNECTOR = "norm"
APP = "loaded-stock"

ROWS = [
    {
        "action": "get_receiving_rules",
        "method": "GET",
        "read_only": True,
        "description": (
            "The organisation's receiving rules — how Norm fills in what an "
            "invoice leaves to the venue (the due date; the unit a venue books "
            "bunches in) whenever it reviews "
            "one, on the card and under Autopilot alike — each venue's own "
            "overrides and what is in force there, and whether this person "
            "can change them (can_edit: owners and managers)."
        ),
        "required_fields": [],
        "optional_fields": ["venue"],
        "field_descriptions": {"venue": "Only this venue (its name)."},
        "field_schema": {"venue": {"type": "string"}},
        "effect": "read",
    },
    {
        "action": "set_receiving_rule",
        "method": "POST",
        "read_only": False,
        "description": (
            "Save or remove one of the organisation's receiving rules, for the "
            "whole organisation or one venue (a venue's rule overrides the "
            "organisation's). Use when a person says how invoices should be "
            "received, e.g. 'if there's no due date I always choose Sunday'. "
            "Where a rule applies it WINS over what the invoice says. 'when' "
            "says which invoices it covers: no_due_date only those printing no "
            "due date (the person said 'if there's no due date…'), always "
            "every invoice, even one printing its own. Saving a note or memory "
            "instead changes nothing about receiving. Ask anything the rule "
            "leaves open (which Sunday: on or after the invoice date, or the "
            "first after it?); the card shows what it does to invoices dated "
            "across a week. kind bunch_unit: a venue that counts herbs and "
            "greens by the bunch books every line sold by the bunch in its own "
            "unit ('unit', e.g. Bunch) instead of a weight. Owners and managers "
            "only."
        ),
        "required_fields": ["scope"],
        "optional_fields": [
            "kind",
            "when",
            "venue",
            "basis",
            "weekday",
            "days",
            "day",
            "unit",
            "remove",
            "note",
        ],
        "field_descriptions": {
            "kind": "The rule: due_date (default), or bunch_unit.",
            "scope": "organisation (every venue) or venue (one venue's override).",
            "venue": "The venue's name, when scope is venue.",
            "basis": "How the due date is worked out from the invoice's date: "
            + "; ".join(f"{k} = {v}" for k, v in DUE_BASES.items()),
            "weekday": "For weekday_on_or_after / weekday_after: the day.",
            "days": "For days_after: how many days (0–120).",
            "day": "For day_of_next_month: the day of the month (1–31).",
            "when": "Which invoices it covers: "
            + "; ".join(f"{k} = {v}" for k, v in DUE_WHEN.items())
            + ". Default always.",
            "unit": "For bunch_unit: the venue's unit, as Loaded spells it.",
            "remove": "true to remove the rule at this scope instead.",
            "note": "The person's own words for the rule, short.",
        },
        "field_schema": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "scope": {"type": "string", "enum": ["organisation", "venue"]},
            "venue": {"type": "string"},
            "basis": {"type": "string", "enum": list(DUE_BASES)},
            "weekday": {"type": "string", "enum": WEEKDAYS},
            "days": {"type": "integer"},
            "day": {"type": "integer"},
            "unit": {"type": "string"},
            "when": {"type": "string", "enum": list(DUE_WHEN)},
            "remove": {"type": "boolean"},
            "note": {"type": "string"},
        },
        "effect": "write",
        "approval": {
            "default": "ask",
            "allow_auto": False,
            "label": "change a receiving rule",
        },
    },
]


def also(db, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec, MarketplaceApp

    lines = []
    spec = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .with_for_update()
        .one()
    )
    app = (
        db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug == APP)
        .with_for_update()
        .one()
    )
    tools = [dict(t) for t in spec.tools or []]
    changed = False
    for row in ROWS:
        live = spec_rows.find_tool(spec, row["action"])
        if live is None:
            lines.append(f"{CONNECTOR}.{row['action']}: add tool row")
            tools.append(row)
            changed = True
        elif {k: v for k, v in live.items() if k != "added_at"} != row:
            lines.append(f"{CONNECTOR}.{row['action']}: replace tool row")
            new = {
                **row,
                **({"added_at": live["added_at"]} if "added_at" in live else {}),
            }
            tools = [new if t.get("action") == row["action"] else t for t in tools]
            changed = True
    if changed and not dry_run:
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1

    comp = dict(app.composition or {})
    claimed = list(comp.get("tools") or [])
    for row in ROWS:
        key = f"{CONNECTOR}.{row['action']}"
        if key not in claimed:
            lines.append(f"app {APP}: claim {key}")
            claimed.append(key)
    if claimed != list(comp.get("tools") or []) and not dry_run:
        comp["tools"] = claimed
        app.composition = comp
        flag_modified(app, "composition")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates([], dry_run=args.dry_run, label="receiving-rules", also=also)


if __name__ == "__main__":
    main()
