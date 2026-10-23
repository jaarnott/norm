"""Publish the norm.caller_holds spec row — the engine's permission question.

A consolidator asks ``norm.caller_holds`` whether the person a run is for
holds an org scope, so one tool can answer differently by person: get_labour
keeps rates and per-shift costs for ``payroll:read`` holders and strips them
for everyone else (owner decision D8, Oct 2026). Engine-only: the model never
sees it.

Every engine-called norm.* action needs a publishing script (the
match_supplier lesson — a handler with no spec row dies with "Tool not
found" at runtime). Here that failure is silent and wide: without this row
``get_labour`` fails closed and strips pay detail for EVERY caller, owners
included — run this in the same deploy as the code.

Usage:
    uv run python scripts/sync_caller_holds_tool.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

TOOL = {
    "action": "caller_holds",
    "method": "GET",
    "description": (
        "[engine-only] Does the person this run is for hold an org permission "
        'scope ({"holds": true|false}; false when nobody is known)? Called '
        "by consolidators that answer differently by person (get_labour's "
        "pay detail); never bind this to an agent."
    ),
    "engine_only": True,
    "required_fields": ["scope"],
    "optional_fields": [],
    "field_descriptions": {
        "scope": "An org permission scope, e.g. payroll:read or finance:read"
    },
    "field_schema": {"scope": {"type": "string"}},
    "effect": "read",
    "read_only": True,
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "norm")
            .first()
        )
        if not spec:
            raise SystemExit("norm ConnectionSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        idx = next(
            (i for i, t in enumerate(tools) if t.get("action") == TOOL["action"]), None
        )
        if idx is not None and tools[idx] == TOOL:
            print("caller_holds: already up to date")
            return
        what = "updated" if idx is not None else "added"
        if idx is None:
            tools.append(dict(TOOL))
        else:
            tools[idx] = dict(TOOL)
        if dry_run:
            print(f"DRY RUN — would have {what} caller_holds on the norm spec")
            return
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"caller_holds {what}, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
