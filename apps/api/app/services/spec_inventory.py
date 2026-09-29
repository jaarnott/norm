"""What each of a connector's rows is, who exposes it, and what it's built from.

The admin screen's view of ``app/connectors/spec_rows.py`` (Sep 2026: tools and
API endpoints). For every row of one connector:

- ``kind`` — tool or endpoint (its list once split, its classification before);
- ``build`` — consolidator, built-in (Norm code) or endpoint;
- ``app`` — the App whose claim puts a tool in front of an LLM (App Map), if any;
- ``uses`` — for a consolidator, the rows it calls (read statically from its
  code: ``call_api("connector", "action", …)`` literals, tuple/dict literals
  naming a known connector, and ``consolidator_config.wraps``);
- ``used_by`` — for any row, the consolidators that call it, across every
  connector (so ``norm.resolve_dates`` lists the Loaded tools that use it);
- ``calls_30d`` — direct tool calls in the last 30 days (a consolidator's own
  ``call_api`` calls are not recorded as tool calls, so endpoints read 0 here —
  ``used_by`` is their usage);
- ``code`` — where a built-in's handler lives in the repo.
"""

from __future__ import annotations

import ast
import inspect
import logging
import pathlib

from sqlalchemy.orm import Session

from app.connectors import spec_rows

logger = logging.getLogger(__name__)

_CALLERS = {"call_api", "extract_document"}


def calls_in_code(code: str, connectors: set[str]) -> set[tuple[str, str]]:
    """``(connector, action)`` pairs a consolidator's code names literally."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return set()
    found: set[tuple[str, str]] = set()

    def _str(n: ast.AST) -> str | None:
        return (
            n.value
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            else None
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else fn.attr
                if isinstance(fn, ast.Attribute)
                else None
            )
            if name in _CALLERS and len(node.args) >= 2:
                c, a = _str(node.args[0]), _str(node.args[1])
                if c and a:
                    found.add((c, a))
        elif isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) >= 2:
            c, a = _str(node.elts[0]), _str(node.elts[1])
            if c in connectors and a:
                found.add((c, a))
        elif isinstance(node, ast.Dict):
            kv = {
                _str(k): _str(v)
                for k, v in zip(node.keys, node.values)
                if k is not None
            }
            if kv.get("connector") in connectors and kv.get("action"):
                found.add((kv["connector"], kv["action"]))
    return found


def _handler_location(connector: str, action: str) -> str | None:
    from app.agents.internal_tools import get_handler

    fn = get_handler(connector, action)
    if fn is None:
        return None
    try:
        path = pathlib.Path(inspect.getsourcefile(fn) or "")
        line = inspect.getsourcelines(fn)[1]
        parts = path.parts
        rel = "/".join(parts[parts.index("app") :]) if "app" in parts else path.name
        return f"{rel}:{line} ({fn.__name__})"
    except (OSError, TypeError):
        return getattr(fn, "__name__", None)


def dependency_map(specs: list) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(uses, used_by)`` across every connector, keyed ``connector.action``.

    ``uses`` — for each consolidator, the rows its code names; ``used_by`` —
    the inverse, for any row a consolidator calls.
    """
    connectors = {s.connector_name for s in specs}
    uses: dict[str, list[str]] = {}
    for s in specs:
        for r in spec_rows.rows(s):
            if not spec_rows.is_consolidator(r):
                continue
            cfg = r["consolidator_config"]
            refs = {
                f"{c}.{a}"
                for c, a in calls_in_code(cfg.get("function_code") or "", connectors)
            }
            if cfg.get("wraps"):
                refs.add(f"{s.connector_name}.{cfg['wraps']}")
            refs.discard(f"{s.connector_name}.{r.get('action')}")
            uses[f"{s.connector_name}.{r.get('action')}"] = sorted(refs)
    used_by: dict[str, list[str]] = {}
    for tool_key, refs in uses.items():
        for ref in refs:
            used_by.setdefault(ref, []).append(tool_key)
    return uses, {k: sorted(v) for k, v in used_by.items()}


def inventory(name: str, db: Session, config_db: Session) -> dict | None:
    from app.db.config_models import ConnectionSpec, MarketplaceApp
    from app.services.consolidator_coverage import _usage
    from app.services.entitlements import tool_owners

    specs = config_db.query(ConnectionSpec).all()
    target = next((s for s in specs if s.connector_name == name), None)
    if target is None:
        return None
    uses, used_by = dependency_map(specs)

    try:
        owners = tool_owners(config_db)
    except Exception as exc:  # noqa: BLE001 — enrichment only
        logger.info("inventory: App Map read failed: %s", exc)
        owners = {}
    app_names = {
        a.slug: getattr(a, "name", None) or a.slug
        for a in config_db.query(MarketplaceApp).all()
    }
    usage = _usage(db, 30)

    out = []
    for r in spec_rows.rows(target):
        action = r.get("action") or ""
        key = f"{name}.{action}"
        build = spec_rows.build_of(name, r, execution_mode=target.execution_mode)
        owner = owners.get(key)
        out.append(
            {
                "action": action,
                "kind": spec_rows.kind_of(target, r),
                "build": build,
                "app": {"slug": owner, "name": app_names.get(owner, owner)}
                if owner
                else None,
                "uses": uses.get(key, []),
                "used_by": used_by.get(key, []),
                "calls_30d": usage.get(f"{name}__{action}", 0),
                "code": _handler_location(name, action)
                if build == "built-in"
                else None,
            }
        )
    return {"connector_name": name, "split": spec_rows.is_split(target), "rows": out}
