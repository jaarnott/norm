"""Install the CV reader as a consolidator (Sep 2026, built-in audit wave 1).

`bamboohr.get_applicant_resume` was a Norm built-in that called BambooHR's
/files/{id} itself. Under the rule in docs/tool-architecture-strategy.md —
anything reaching an outside system is a consolidator over endpoints — it
becomes config/consolidators/get_applicant_resume.py over two endpoints:
`get_application_details` (existing) and `download_file` (added here, binary).

Order: run this BEFORE deploying the code that drops the built-in handler.
While the handler is registered it still runs instead of the row (dispatch
prefers a handler), so installing the consolidator first changes nothing
until the deploy; the deploy then hands over.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_cv_reader_consolidator.py --dry-run
    uv run python scripts/sync_cv_reader_consolidator.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CODE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "config/consolidators/get_applicant_resume.py"
)

DOWNLOAD_FILE = {
    "action": "download_file",
    "method": "GET",
    "description": "Download one BambooHR file (a CV, an employee document) by id — the raw bytes, base64-encoded.",
    "path_template": "/files/{{ file_id }}",
    "headers": {},
    "required_fields": ["file_id"],
    "field_descriptions": {"file_id": "The file id (an application's resumeFileId)."},
    "field_mapping": {},
    "success_status_codes": [200],
    "timeout_seconds": 30,
    "response_format": "binary",
    "read_only": True,
}


def cv_tool() -> dict:
    return {
        "action": "get_applicant_resume",
        "method": "GET",  # read-only consolidator: auto-executes
        "read_only": True,
        "description": (
            "Read an applicant's CV. Pass application_id (from get_hr's "
            "applications view) — or the CV's file_id if you already have it. "
            "Returns the file itself for you to read and assess."
        ),
        "required_fields": [],
        "optional_fields": ["application_id", "file_id"],
        "field_descriptions": {
            "application_id": "The BambooHR application id — the CV is found from it.",
            "file_id": "The CV's file id (resumeFileId), when you already hold it.",
        },
        "consolidator_config": {
            "function_code": CODE.read_text(),
            "max_api_calls": 2,
            "allowed_write_actions": [],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services.config_validator import validate_config

    import app.agents.internal_tools  # noqa: F401 — registers built-ins

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter_by(connector_name="bamboohr")
            .with_for_update()
            .one()
        )
        if not spec_rows.is_split(spec):
            raise SystemExit(
                "bamboohr is not split yet — run sync_split_connector_endpoints.py first"
            )
        changes = []
        existing_ep = spec_rows.find_endpoint(spec, "download_file")
        if (
            existing_ep is None
            or {k: v for k, v in existing_ep.items() if k != "added_at"}
            != DOWNLOAD_FILE
        ):
            changes.append("endpoint bamboohr.download_file")
        tool = cv_tool()
        existing_tool = spec_rows.find_tool(spec, "get_applicant_resume")
        if (
            existing_tool is None
            or {k: v for k, v in existing_tool.items() if k != "added_at"} != tool
        ):
            changes.append("tool bamboohr.get_applicant_resume -> consolidator")
        for c in changes or ["nothing to do"]:
            print(("[dry-run] " if args.dry_run else "") + c)
        if args.dry_run or not changes:
            db.rollback()
            return
        spec.endpoints = spec_rows.upsert(spec.endpoints, [DOWNLOAD_FILE])
        # replace the tool row wholesale (drop its old HTTP template fields)
        spec.tools = [
            tool if r.get("action") == "get_applicant_resume" else r for r in spec.tools
        ]
        flag_modified(spec, "endpoints")
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        summary = validate_config(config_db=db)
        print(
            f"validate_config: {'ok' if summary.get('ok') else summary.get('issue_count')}"
        )
        for i in (summary.get("issues") or [])[:10]:
            print(f"   {i.get('severity')}: {i.get('where')} — {i.get('problem')}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
