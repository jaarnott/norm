"""READ-ONLY: what in an environment still points at an API endpoint?

Sep 2026 decision (docs/tool-architecture-strategy.md): only tools reach an
LLM; endpoints are building blocks. Before the LLM-facing readers are
tightened to tools only, this lists every stored reference to a
``(connector, action)`` and says whether it names a tool, an endpoint, or
something that doesn't exist:

- main DB (per environment): saved report charts (``report_charts.script``),
  user-built app versions (``app_versions.spec.actions``), automated tasks'
  ``tool_filter``, and tool calls still waiting for approval;
- config DB (shared): App Map claims, enabled MCP capabilities, enabled
  binding capabilities.

Usage (from apps/api, .env loaded):

    uv run python scripts/audit_endpoint_references.py                  # local main DB
    uv run python scripts/audit_endpoint_references.py --main-url URL   # another environment

Nothing is written. The main-DB session is opened READ ONLY.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--main-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args()

    from sqlalchemy import create_engine, text

    from app.connectors import spec_rows
    from app.db.config_models import (
        AgentConnectionBinding,
        ConnectionSpec,
        McpCapability,
    )
    from app.db.engine import _ConfigSessionLocal
    from app.services.entitlements import tool_owners

    import app.agents.internal_tools  # noqa: F401 — registers built-ins

    cdb = _ConfigSessionLocal()
    kinds: dict[tuple[str, str], str] = {}
    by_action: dict[str, set[str]] = {}
    for spec in cdb.query(ConnectionSpec).all():
        for r in spec_rows.rows(spec):
            kinds[(spec.connector_name, r.get("action"))] = spec_rows.kind_of(spec, r)
            by_action.setdefault(r.get("action"), set()).add(spec.connector_name)

    def kind(connector: str | None, action: str | None) -> str:
        if not action:
            return "no action"
        if connector:
            return kinds.get((connector, action), "MISSING")
        owners = by_action.get(action) or set()
        if not owners:
            return "MISSING"
        return "/".join(sorted({kinds[(c, action)] for c in owners}))

    findings: list[tuple[str, str, str]] = []  # (where, ref, kind)

    # ── config DB (shared) ───────────────────────────────────────────────
    for key, app_slug in sorted(tool_owners(cdb).items()):
        c, _, a = key.partition(".")
        findings.append((f"App {app_slug}", key, kind(c, a)))
    for cap in cdb.query(McpCapability).filter(McpCapability.kind == "connector").all():
        if cap.enabled:
            findings.append(
                (
                    "MCP capability",
                    f"{cap.target}.{cap.action}",
                    kind(cap.target, cap.action),
                )
            )
    for b in cdb.query(AgentConnectionBinding).all():
        if not b.enabled:
            continue
        for cap in b.capabilities or []:
            if isinstance(cap, dict) and cap.get("enabled", True) and cap.get("action"):
                findings.append(
                    (
                        f"binding {b.agent_slug}",
                        f"{b.connector_name}.{cap['action']}",
                        kind(b.connector_name, cap["action"]),
                    )
                )
    cdb.close()

    # ── main DB (this environment) ───────────────────────────────────────
    engine = create_engine(args.main_url)
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        for title, script in conn.execute(
            text("SELECT title, script FROM report_charts")
        ).fetchall():
            s = script or {}
            if isinstance(s, dict) and s.get("action"):
                findings.append(
                    (
                        f"chart '{title}'",
                        f"{s.get('connector')}.{s.get('action')}",
                        kind(s.get("connector"), s.get("action")),
                    )
                )
        for app_id, version, spec in conn.execute(
            text("SELECT app_id, version, spec FROM app_versions")
        ).fetchall():
            for e in (spec or {}).get("actions") or []:
                if isinstance(e, dict):
                    findings.append(
                        (
                            f"app {app_id[:8]} v{version}",
                            f"{e.get('connector')}.{e.get('action')}",
                            kind(e.get("connector"), e.get("action")),
                        )
                    )
                elif isinstance(e, str):
                    c, _, a = e.rpartition(".")
                    findings.append(
                        (f"app {app_id[:8]} v{version}", e, kind(c or None, a))
                    )
        for task_id, title, tf in conn.execute(
            text(
                "SELECT id, title, tool_filter FROM automated_tasks WHERE tool_filter IS NOT NULL"
            )
        ).fetchall():
            for a in tf or []:
                findings.append((f"task '{title}'", str(a), kind(None, str(a))))
        for c, a, n in conn.execute(
            text(
                "SELECT connector_name, action, count(*) FROM tool_calls WHERE status = 'pending_approval' GROUP BY 1, 2"
            )
        ).fetchall():
            findings.append((f"{n} pending tool call(s)", f"{c}.{a}", kind(c, a)))

    counts = Counter(k for _, _, k in findings)
    print(
        f"references: {len(findings)} — "
        + ", ".join(f"{k} {n}" for k, n in counts.most_common())
    )
    for where, ref, k in findings:
        if k != "tool":
            print(f"  {k:9s} {ref:52s} {where}")


if __name__ == "__main__":
    main()
