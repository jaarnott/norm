"""Sync the norm_reports periodic consolidators — their FIRST sync script.

Until 22 Aug 2026 the three get_periodic_* tools' function_code lived ONLY
in the config DB (the one consolidator family with no reviewed repo file —
the coverage dashboard flagged exactly this), and they required the model
to supply exact period_start/period_end dates it computed itself: the
documented incident class (test_mcp_execution.py:279 — a Saturday total
reported midnight-to-midnight because Claude routed around the date-safe
tools to these).

This script installs the canonical files from config/consolidators/ and
re-shapes the date surface: `period` in plain English leads (resolved
through Norm's venue calendar inside the tool; a recurring phrase like
"every Friday for the last 12 weeks" resolves to the matching days), with
explicit period_start/period_end kept as the exact-dates fallback. Every
other row property (descriptions of the analysis params, max_result_chars,
bindings, MCP rows) is left as it stands.

Usage:
    uv run python scripts/sync_periodic_reports_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"

CONNECTOR = "norm_reports"

PERIOD_FD = (
    "The period in plain English — 'last 12 weeks', 'every Friday for the "
    "last 12 weeks'. Norm resolves it against this venue's calendar; prefer "
    "this over period_start/period_end and never work out dates yourself."
)
START_FD = (
    "Exact start date YYYY-MM-DD — only when the user gave exact dates. "
    "Explicit dates are CIVIL calendar days (midnight boundaries); pass "
    "period instead for the venue's trading days."
)
END_FD = "Exact end date YYYY-MM-DD, same rule as period_start."

#: action → (required fields kept, extra note for recurring phrases)
TOOLS = {
    "get_periodic_sales": ["venue", "time_windows"],
    "get_periodic_product_sales": ["venue"],
    "get_periodic_staff_sales": ["venue"],
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changed: list[str] = []
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"{CONNECTOR} ConnectorSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        for t in tools:
            action = t.get("action")
            if action not in TOOLS:
                continue
            code = (_DIR / f"{action}.py").read_text(encoding="utf-8")
            cfg = dict(t.get("consolidator_config") or {})
            if cfg.get("function_code") != code:
                cfg["function_code"] = code
                # +1 call for resolve_dates on top of the per-month batch.
                cfg["max_api_calls"] = max(int(cfg.get("max_api_calls") or 0), 16)
                t["consolidator_config"] = cfg
                changed.append(f"{action}: function_code updated")
            required = list(TOOLS[action])
            optional = ["period", "period_start", "period_end"] + [
                f
                for f in (t.get("optional_fields") or [])
                + [
                    x
                    for x in (t.get("required_fields") or [])
                    if x not in required and x not in ("period_start", "period_end")
                ]
                if f not in ("period", "period_start", "period_end")
            ]
            # dedupe, order-preserving
            seen: list[str] = []
            for f in optional:
                if f not in seen:
                    seen.append(f)
            if t.get("required_fields") != required or t.get("optional_fields") != seen:
                t["required_fields"] = required
                t["optional_fields"] = seen
                changed.append(f"{action}: field surface reshaped")
            fd = dict(t.get("field_descriptions") or {})
            if fd.get("period") != PERIOD_FD:
                fd["period"] = PERIOD_FD
                fd["period_start"] = START_FD
                fd["period_end"] = END_FD
                t["field_descriptions"] = fd
                changed.append(f"{action}: date field descriptions updated")

        if dry_run:
            print("DRY RUN — would apply:")
        else:
            spec.tools = tools
            flag_modified(spec, "tools")
            spec.version = (spec.version or 0) + 1
            db.commit()
            print("Applied:")
        for line in changed or ["  (nothing to do)"]:
            print(f"  {line}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
