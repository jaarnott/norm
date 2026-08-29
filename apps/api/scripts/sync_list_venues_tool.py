"""Publish the norm.list_venues spec row — venue fan-out's backbone.

The engine resolves connector credentials one venue per call, so any
consolidator offering venues='all' needs the venue list as a tool call
(get_sales is the first). Engine-only: agents already know
their venues from context; this exists for sandboxed code.

Every engine-called norm.* action needs a publishing script (the
match_supplier lesson — a handler with no spec row dies with "Tool not
found" at runtime).

Usage:
    uv run python scripts/sync_list_venues_tool.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

TOOL = {
    "action": "list_venues",
    "method": "GET",
    "description": (
        "[engine-only] The venues a connector fan-out can cover — name, id "
        "and whether the connector is connected. Called by consolidators "
        "resolving venues='all'; never bind this to an agent."
    ),
    "engine_only": True,
    "required_fields": [],
    "optional_fields": ["connector"],
    "field_descriptions": {
        "connector": "Connector whose connections to report (default loadedhub)"
    },
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
            print("list_venues: already up to date")
            return
        what = "updated" if idx is not None else "added"
        if idx is None:
            tools.append(dict(TOOL))
        else:
            tools[idx] = dict(TOOL)
        if dry_run:
            print(f"DRY RUN — would have {what} list_venues")
            return
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"list_venues {what}, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
