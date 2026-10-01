"""Update live tool rows from their canonical definitions — and nothing else.

The consolidator-review stages (1 Oct 2026) each change a few tools' code,
text or shapes. Their full installers do far more (clone endpoints, demote
rows, rebind agents; some can no longer run on the split spec), so re-running
them for a code change is a risk. A stage script instead lists RowUpdates:
the exact top-level keys to set, plus optionally the consolidator's code and
shapes. Everything else on the live row — method, approval gate, budgets,
added_at, other config — is kept.

    from scripts.tool_row_updates import RowUpdate, apply_row_updates, load_script
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
from dataclasses import dataclass, field

API = pathlib.Path(__file__).resolve().parents[1]
CONSOLIDATORS = API / "config" / "consolidators"
BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"


@dataclass
class RowUpdate:
    connector: str
    action: str
    fields: dict = field(default_factory=dict)  # top-level keys to set
    code: str | None = None  # consolidator_config.function_code
    shapes: dict | None = None  # consolidator_config.shapes


def load_script(name: str):
    """Import scripts/<name>.py for its definitions (never its main)."""
    spec = importlib.util.spec_from_file_location(name, API / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def code_of(filename: str) -> str:
    return (CONSOLIDATORS / filename).read_text(encoding="utf-8")


def shapes_of(tool_key: str) -> dict:
    return json.loads((CONSOLIDATORS / "shapes.json").read_text(encoding="utf-8"))[
        tool_key
    ]


def merged(live: dict, update: RowUpdate) -> dict:
    row = {**live, **update.fields}
    cc = dict(live.get("consolidator_config") or {})
    if update.code is not None:
        cc["function_code"] = update.code
    if update.shapes is not None:
        cc["shapes"] = update.shapes
    row["consolidator_config"] = cc
    return row


def apply_row_updates(updates, *, dry_run: bool, label: str, db=None) -> list[str]:
    """Plan, back up, write and validate. Returns the change lines printed.

    ``updates`` is a list of RowUpdate, or a callable taking {connector: spec}
    and returning one (for definitions that need a live endpoint row)."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services.config_validator import validate_config

    import app.agents.internal_tools  # noqa: F401 — registers built-ins

    own = db is None
    db = db or _ConfigSessionLocal()
    lines: list[str] = []
    try:
        names = sorted(
            {u.connector for u in updates} if not callable(updates) else set()
        )
        if callable(updates):
            specs = {
                s.connector_name: s
                for s in db.query(ConnectionSpec).with_for_update().all()
            }
            updates = updates(specs)
        else:
            specs = {
                s.connector_name: s
                for s in db.query(ConnectionSpec)
                .filter(ConnectionSpec.connector_name.in_(names))
                .with_for_update()
                .all()
            }
        plan, backup = [], {}
        for u in updates:
            spec = specs.get(u.connector)
            live = spec_rows.find_tool(spec, u.action) if spec else None
            if live is None:
                raise SystemExit(f"{u.connector}.{u.action} is not a tool row")
            row = merged(live, u)
            diff = [k for k in row if row.get(k) != live.get(k)]
            if "consolidator_config" in diff:
                cc_old = live.get("consolidator_config") or {}
                cc_new = row["consolidator_config"]
                diff.remove("consolidator_config")
                diff += [
                    f"consolidator_config.{k}"
                    for k in sorted(cc_new)
                    if cc_new.get(k) != cc_old.get(k)
                ]
            line = f"{u.connector}.{u.action}: " + (
                ("update " + ", ".join(diff)) if diff else "up to date"
            )
            lines.append(line)
            print(("[dry-run] " if dry_run else "") + line)
            if diff:
                plan.append((spec, u.action, row))
                backup[f"{u.connector}.{u.action}"] = live
        if dry_run or not plan:
            db.rollback()
            return lines

        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"{label}-{stamp}.json"
        path.write_text(json.dumps(backup, indent=1, default=str))
        print(f"backup {path}")
        touched = {}
        for spec, action, row in plan:
            spec.tools = [row if t.get("action") == action else t for t in spec.tools]
            flag_modified(spec, "tools")
            touched[spec.connector_name] = spec
        for spec in touched.values():
            spec.version = (spec.version or 0) + 1
        db.commit()
        summary = validate_config(config_db=db)
        print(
            f"validate_config: {'ok' if summary.get('ok') else summary.get('issue_count')}"
        )
        for i in (summary.get("issues") or [])[:10]:
            print(f"   {i.get('severity')}: {i.get('where')} — {i.get('problem')}")
        return lines
    finally:
        if own:
            db.close()
