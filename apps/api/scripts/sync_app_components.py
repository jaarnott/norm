"""App components: Norm can open an app somewhere specific (28 Sep 2026).

See app/services/app_components.py. Two config changes, idempotent; the config
DB is shared by every environment:

  1. the `norm.open_app` tool on the norm spec — opens one of the org's
     App-platform apps in the conversation, at the place its component's
     inputs name (display_component `app_runner`). Owned by Norm Core in the
     catalog seed (scripts/sync_marketplace_catalog.py).
  2. App Builder's `build_an_app` skill learns the rule: every app declares
     its components — one by default — and honours its inputs on first load.

Deploy the code first: an older API has no `open_app` handler, so the tool
would fail when called (it is only offered once Norm Core owns it, which the
catalog seed does after this).

Usage:
    .venv/bin/python scripts/sync_app_components.py --dry-run
    .venv/bin/python scripts/sync_app_components.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

OPEN_APP = {
    "action": "open_app",
    "method": "GET",
    "read_only": True,
    "description": (
        "Open one of the organization's apps (Norm Hiring, Norm Training, or an "
        "app your team built) in the conversation, optionally at a specific "
        "place. Use it when the user wants to SEE or work in an app — 'show me "
        "the pipeline for the head chef role', 'open Sam's training'. Pass the "
        "app's name in `app`, and where to start in `inputs`: Norm Hiring takes "
        "view ('openings' or 'talent_pool'), job (id or title) and candidate "
        "(id or name); Norm Training takes view ('programs', 'plans', "
        "'tracker', 'signoffs', 'frameworks'), program (id or name) and person "
        "(id or name). Names are fine — the app resolves them. If the app or an "
        "input isn't valid, the error lists what is."
    ),
    "required_fields": ["app"],
    "optional_fields": ["component", "inputs"],
    "field_schema": {
        "app": {
            "type": "string",
            "description": "The app's name or slug, e.g. 'Norm Hiring' or 'hiring'.",
        },
        "component": {
            "type": "string",
            "description": "Which of the app's components to open. Leave out "
            "for an app with one component (most apps, Hiring and Training "
            "included).",
        },
        "inputs": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Where to start: the component's inputs, e.g. "
            '{"job": "Head Chef"} or {"person": "Sam"}. Leave out to open the '
            "app at its start.",
        },
    },
    "display_component": "app_runner",
}

SKILL_SPEC_ANCHOR = (
    "- `params`: {name: description} for inputs the app asks its viewer for.\n"
)
SKILL_SPEC_ADD = (
    "- `components`: the screens Norm may open directly — by default exactly ONE, "
    'named after the app: [{"key": "<snake_case>", "label": ..., "description": ..., '
    '"page": true, "inputs": {name: description}}]. `inputs` are all optional and say '
    "WHERE to start (a job, a person, a supplier — accept an id OR a name and resolve "
    "it in the app, since Norm opens apps by what people call things). Navigation "
    "inside the app (tabs, drill-downs) stays the app's own business and is never "
    "declared. Split into several components only when a piece is useful on its own "
    "in chat, is reused elsewhere, or needs different permissions.\n"
)
SKILL_CTX_OLD = (
    "- `ctx = {venueId, venues: [{id, name}], app: {slug, name, version}}`. The venue "
    "picker lives in Norm's chrome — do not build your own.\n"
)
SKILL_CTX_NEW = (
    "- `ctx = {venueId, venues: [{id, name}], app: {slug, name, version}, component, "
    "inputs}`. The venue picker lives in Norm's chrome — do not build your own. "
    "`component`/`inputs` are where Norm asked the app to open: honour them on the "
    "FIRST onReady only (they ride every re-init, and a venue switch must not yank "
    "the viewer back).\n"
)


def plan(db, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec, Playbook

    changes: list[str] = []

    spec = (
        db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == "norm").first()
    )
    if spec is None:
        raise SystemExit("no norm spec in the config DB")
    tools = [dict(t) for t in spec.tools or []]
    current = next((t for t in tools if t.get("action") == "open_app"), None)
    if current != OPEN_APP:
        changes.append(("update" if current else "add") + " tool norm.open_app")
        if not dry_run:
            tools = [t for t in tools if t.get("action") != "open_app"] + [OPEN_APP]
            spec.tools = tools
            flag_modified(spec, "tools")
            spec.version = (spec.version or 0) + 1

    pb = db.query(Playbook).filter(Playbook.slug == "build_an_app").first()
    if pb is None:
        raise SystemExit("no build_an_app skill — run sync_unified_prompt.py first")
    text = pb.instructions or ""
    new = text
    if SKILL_SPEC_ADD not in new:
        if SKILL_SPEC_ANCHOR not in new:
            raise SystemExit("build_an_app: spec anchor not found — edit by hand")
        new = new.replace(SKILL_SPEC_ANCHOR, SKILL_SPEC_ANCHOR + SKILL_SPEC_ADD, 1)
    if SKILL_CTX_NEW not in new:
        if SKILL_CTX_OLD not in new:
            raise SystemExit("build_an_app: ctx line not found — edit by hand")
        new = new.replace(SKILL_CTX_OLD, SKILL_CTX_NEW, 1)
    if new != text:
        changes.append("skill build_an_app: declare components, honour inputs")
        if not dry_run:
            pb.instructions = new
    return changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changes = plan(db, args.dry_run)
        if not changes:
            print("app components config up to date")
            return
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        db.commit()
        print(f"committed {len(changes)} change(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
