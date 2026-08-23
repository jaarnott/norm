"""Publish the norm.resolve_dates spec row — the missing publisher.

resolve_dates is the platform's most-called internal tool (every
``*_for_period`` consolidator and every period-taking consolidator resolves
through it), yet its `norm` ConnectorSpec row predated the sync-script era:
it existed only as a hand-made config row no repo file could recreate.
TestEngineNormToolsPublished (test_invoice_review_consolidator.py) rightly
demands every engine-called norm.* action have a publishing script — the
match_supplier lesson (a handler with no spec row dies with "Tool not
found" at runtime).

This mirrors the live row verbatim, with one wording change: the
description no longer commands "Always call this FIRST" — since the period
convergence (22 Aug 2026), date-taking tools resolve periods themselves,
and resolve_dates is the oracle for questions ABOUT dates.

Usage:
    uv run python scripts/sync_resolve_dates_tool.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

TOOL = {
    "action": "resolve_dates",
    "method": "GET",
    "description": (
        "Resolve a natural language time reference into exact ISO 8601 "
        "timestamps against the venue's trading calendar. Most data tools "
        "take `period` in plain English and resolve it themselves — call "
        "this directly when the DATES are the answer (e.g. to state which "
        "window a figure covers) or for a tool that still wants explicit "
        "timestamps (e.g. shift writes). Returns one or more periods with "
        "start/end timestamps."
    ),
    "required_fields": ["query"],
    "optional_fields": ["timezone"],
    "field_descriptions": {
        "query": (
            "The natural language time reference to resolve (e.g., 'last "
            "week', 'every Friday 5pm-9pm for last 12 weeks', 'March 2026')"
        ),
        "timezone": "IANA timezone (default: Pacific/Auckland)",
    },
    "read_only": True,
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "norm")
            .first()
        )
        if not spec:
            raise SystemExit("norm ConnectorSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        idx = next(
            (i for i, t in enumerate(tools) if t.get("action") == TOOL["action"]), None
        )
        if idx is not None and tools[idx] == TOOL:
            print("resolve_dates: already up to date")
            return
        if idx is None:
            tools.append(dict(TOOL))
            what = "added"
        else:
            tools[idx] = dict(TOOL)
            what = "updated"
        if dry_run:
            print(f"DRY RUN — would have {what} resolve_dates")
            return
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"resolve_dates {what}, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
