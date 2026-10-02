"""Stage 6 of the 1 Oct 2026 consolidator review: HR.

1. bamboohr.get_hr — fixes from the review, each found on the live account:
   venue names match after dropping case, punctuation and a leading "The"
   (Norm's "The Glass Goose" is BambooHR's "Glass Goose": 0 staff before, 15
   after) and no match lists the real names; a failed BambooHR read says so,
   and a refused key (the 28 Sep 401, after which the agent quietly answered
   from Norm Hiring) says an admin must re-enter it; division is searched
   ("kitchen": 1 person before, 27 after); a job's candidates say they are
   the active ones; rows carry job_id; limit is capped at 100.
2. The CV reader folds in: get_hr with application_id and cv true returns the
   application and the CV in one call. bamboohr.get_applicant_resume goes —
   its row, its BambooHR App claim and its hr binding entry — and the
   candidate_review skill's step 3 (which asked for the CV by a file id that
   only a previous call could supply) becomes that one call.

The CV reaches the model through the tool loop, which since this stage runs
it through the attachment converter (a Word CV becomes text; it used to fail
the turn) — deploy the API before relying on Word CVs.

BambooHR is the group's hiring system: Norm Hiring was switched off by the
user on 2 Oct 2026, and the App's description says so.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_hr_fixes.py --dry-run
    uv run python scripts/sync_hr_fixes.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import (  # noqa: E402 — path set above
    RowUpdate,
    apply_row_updates,
    code_of,
    load_script,
)

OLD = "get_applicant_resume"
KEYS = ("description", "optional_fields", "field_descriptions", "field_schema")
APP = "bamboohr-app"


def updates() -> list[RowUpdate]:
    hr = load_script("sync_last_raw_tools").HR_TOOL
    return [
        RowUpdate(
            "bamboohr",
            "get_hr",
            {k: hr[k] for k in KEYS},
            code=code_of("get_hr.py"),
            config={"max_api_calls": hr["consolidator_config"]["max_api_calls"]},
        )
    ]


def also(db, dry_run: bool) -> list[str]:
    """The old tool's App claim, binding entry, MCP row; the skill; the App's
    description."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConnectionBinding,
        MarketplaceApp,
        McpCapability,
        Playbook,
    )

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    spec = db.query(ConnectionSpec).filter_by(connector_name="bamboohr").one()
    if spec_rows.find_endpoint(spec, "download_file") is None:
        raise SystemExit(
            "bamboohr has no download_file endpoint — get_hr's cv reads the CV "
            "through it (installed by the old sync_cv_reader_consolidator.py, "
            "in git history before 2 Oct 2026)"
        )
    lines = []
    key = f"bamboohr.{OLD}"
    catalog = {a["slug"]: a for a in load_script("sync_marketplace_catalog").APPS}
    for app_row in db.query(MarketplaceApp).all():
        comp = dict(app_row.composition or {})
        claimed = list(comp.get("tools") or [])
        if key in claimed:
            lines.append(f"App {app_row.slug}: drop claim {key}")
            if not dry_run:
                comp["tools"] = [k for k in claimed if k != key]
                app_row.composition = comp
                flag_modified(app_row, "composition")
        want = (catalog.get(app_row.slug) or {}).get("description")
        if app_row.slug == APP and want and app_row.description != want:
            lines.append(f"App {app_row.slug}: description → {want!r}")
            if not dry_run:
                app_row.description = want
    for b in db.query(AgentConnectionBinding).filter_by(connector_name="bamboohr"):
        caps = list(b.capabilities or [])
        kept = [
            c for c in caps if (c.get("action") if isinstance(c, dict) else c) != OLD
        ]
        if len(kept) != len(caps):
            lines.append(f"binding {b.agent_slug}: drop capability {OLD}")
            if not dry_run:
                b.capabilities = kept
                flag_modified(b, "capabilities")
    cap = (
        db.query(McpCapability)
        .filter_by(kind="connector", target="bamboohr", action=OLD)
        .first()
    )
    if cap is not None:
        lines.append(f"McpCapability bamboohr.{OLD}: delete")
        if not dry_run:
            db.delete(cap)
    text = load_script("sync_last_raw_tools").SKILLS["candidate_review"]
    skill = db.query(Playbook).filter_by(slug="candidate_review").first()
    if skill is not None and skill.instructions != text:
        lines.append("skill candidate_review: one get_hr call per candidate (cv true)")
        if not dry_run:
            skill.instructions = text
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(
        updates(),
        dry_run=args.dry_run,
        label="hr-fixes",
        delete=(("bamboohr", OLD),),
        also=also,
    )


if __name__ == "__main__":
    main()
