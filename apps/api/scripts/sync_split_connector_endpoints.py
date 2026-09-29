"""Split a connector's rows into two lists: tools and API endpoints.

Sep 2026 decision (docs/tool-architecture-strategy.md): an LLM only ever sees
tools (consolidators and built-ins); API endpoints are building blocks and live
in ``ConnectionSpec.endpoints``. This script moves a connector's endpoint rows
out of ``tools`` into ``endpoints``, using the one classifier in
``app/connectors/spec_rows.py``.

The move is behaviour-neutral: every reader goes through ``spec_rows.rows()``
(tools + endpoints), so nothing that runs a row can tell where it sits. What
changes is what the admin screen shows, and what the tightened, LLM-facing
readers (``spec_rows.tools()``) are allowed to see later.

The config DB is SHARED by every environment, so:

- a JSON backup of both lists is written first (``~/norm-split-backups/``);
- ``--reverse`` puts the rows back in one list in their original order — run it
  before rolling any image back to one that predates ``spec_rows`` (such an
  image reads only ``tools`` and would not see the endpoints);
- rows are moved under ``spec_rows.moving()``, so nothing is re-stamped.

Usage (from apps/api, with the .env loaded):

    uv run python scripts/sync_split_connector_endpoints.py --connector bamboohr --dry-run
    uv run python scripts/sync_split_connector_endpoints.py --connector bamboohr
    uv run python scripts/sync_split_connector_endpoints.py --all
    uv run python scripts/sync_split_connector_endpoints.py --connector bamboohr --reverse

Also reports, per connector, endpoints something still exposes to an LLM (an
App claim, an enabled MCP capability, an enabled binding capability) — the
to-do list for tightening; a move doesn't change them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"


def _exposure(config_db) -> dict[str, list[str]]:
    """``connector.action`` -> what exposes it to an LLM."""
    from app.db.config_models import AgentConnectionBinding, McpCapability
    from app.services.entitlements import tool_owners

    out: dict[str, list[str]] = {}
    for key, app_slug in tool_owners(config_db).items():
        out.setdefault(key, []).append(f"App {app_slug}")
    for cap in (
        config_db.query(McpCapability).filter(McpCapability.kind == "connector").all()
    ):
        if cap.enabled:
            out.setdefault(f"{cap.target}.{cap.action}", []).append("MCP capability")
    for b in config_db.query(AgentConnectionBinding).all():
        if not b.enabled:
            continue
        for c in b.capabilities or []:
            if isinstance(c, dict) and c.get("enabled", True) and c.get("action"):
                out.setdefault(f"{b.connector_name}.{c['action']}", []).append(
                    f"binding {b.agent_slug}"
                )
    return out


def _backup(spec) -> pathlib.Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"{spec.connector_name}-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "connector_name": spec.connector_name,
                "version": spec.version,
                "tools": spec.tools,
                "endpoints": spec.endpoints,
            },
            indent=1,
            default=str,
        )
    )
    return path


def _latest_backup(connector: str) -> dict | None:
    files = sorted(BACKUP_DIR.glob(f"{connector}-*.json"))
    for f in reversed(files):
        data = json.loads(f.read_text())
        if data.get("endpoints") is None:  # a pre-split snapshot
            return data
    return None


def split(config_db, spec, exposure: dict, dry_run: bool) -> str:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows

    name = spec.connector_name
    current = spec_rows.rows(spec)
    tools_, endpoints_ = spec_rows.partition(
        name, current, execution_mode=spec.execution_mode
    )
    already = spec_rows.is_split(spec)
    exposed = [
        f"{r['action']} ({', '.join(exposure[f'{name}.{r["action"]}'])})"
        for r in endpoints_
        if f"{name}.{r['action']}" in exposure
    ]
    header = f"{name}: {len(tools_)} tools / {len(endpoints_)} endpoints" + (
        " (already split)" if already else ""
    )
    lines = [header]
    if tools_:
        lines.append("  tools: " + ", ".join(r["action"] for r in tools_))
    if exposed:
        lines.append("  endpoints still exposed to an LLM: " + "; ".join(exposed))
    if dry_run:
        return "\n".join(lines)

    same = (
        already
        and [r.get("action") for r in spec.tools or []] == [r["action"] for r in tools_]
        and [r.get("action") for r in spec.endpoints or []]
        == [r["action"] for r in endpoints_]
    )
    if same:
        lines.append("  no change")
        return "\n".join(lines)

    path = _backup(spec)
    with spec_rows.moving():
        spec.endpoints = endpoints_
        spec.tools = tools_
    flag_modified(spec, "endpoints")
    flag_modified(spec, "tools")
    spec.version = (spec.version or 0) + 1
    config_db.commit()
    lines.append(f"  split (backup {path})")
    return "\n".join(lines)


def reverse(config_db, spec) -> str:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows

    name = spec.connector_name
    if not spec_rows.is_split(spec):
        return f"{name}: not split — nothing to reverse"
    path = _backup(spec)
    snapshot = _latest_backup(name)
    combined = spec_rows.rows(spec)
    if snapshot:
        order = {r.get("action"): i for i, r in enumerate(snapshot.get("tools") or [])}
        combined.sort(key=lambda r: order.get(r.get("action"), len(order)))
    with spec_rows.moving():
        spec.tools = combined
        spec.endpoints = None
    flag_modified(spec, "tools")
    flag_modified(spec, "endpoints")
    spec.version = (spec.version or 0) + 1
    config_db.commit()
    return f"{name}: reversed to one list of {len(combined)} rows (backup {path})"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--connector", action="append", help="connector name (repeatable)"
    )
    target.add_argument("--all", action="store_true", help="every connector spec")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reverse", action="store_true")
    args = ap.parse_args()

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    import app.agents.internal_tools  # noqa: F401 — registers the built-in handlers

    config_db = _ConfigSessionLocal()
    try:
        q = config_db.query(ConnectionSpec)
        if args.connector:
            q = q.filter(ConnectionSpec.connector_name.in_(args.connector))
        specs = q.order_by(ConnectionSpec.connector_name).with_for_update().all()
        missing = set(args.connector or []) - {s.connector_name for s in specs}
        if missing:
            raise SystemExit(f"unknown connector(s): {', '.join(sorted(missing))}")
        exposure = {} if args.reverse else _exposure(config_db)
        for spec in specs:
            if args.reverse:
                print(
                    reverse(config_db, spec)
                    if not args.dry_run
                    else f"{spec.connector_name}: would reverse"
                )
            else:
                print(split(config_db, spec, exposure, args.dry_run))
        if args.dry_run:
            config_db.rollback()
    finally:
        config_db.close()


if __name__ == "__main__":
    main()
