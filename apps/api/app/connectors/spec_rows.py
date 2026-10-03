"""Endpoints and tools — the one place that reads a connector's rows.

The vocabulary (``docs/tool-architecture-strategy.md``, "Decision (September
2026)"):

- An **API endpoint** is one call to an outside system: an HTTP template, or
  one of Orbit's MCP functions. It is a building block — consolidators, page
  components and the engine call it — and it never reaches an LLM.
- A **tool** is what an LLM may see: a *consolidator* (config Python over
  endpoints, ``consolidator_config.function_code``) or a *built-in* (Norm code
  registered with ``@register`` in ``app/agents/internal_tools.py``, allowed
  only for work on Norm itself).

Storage is two lists on ``ConnectionSpec``: ``tools`` and ``endpoints``. A
connector whose ``endpoints`` is NULL has not been split yet ("legacy"): every
row is still in ``tools``. ``scripts/sync_split_connector_endpoints.py`` moves a
connector's endpoint rows across (and back, with ``--reverse``).

How to read them:

- :func:`rows` / :func:`find` — every row, whichever list it is in. Anything
  that just needs "the row for this action" uses these, so splitting a
  connector changes nothing for it.
- :func:`tools` / :func:`find_tool` — only tools. Everything LLM-facing (the
  agent menu, MCP, the App Builder's catalogue, charts) reads these.
- :func:`endpoints` / :func:`find_endpoint` — only endpoints: the building
  blocks consolidators and pages call.

Never loop over ``spec.tools`` directly: on a split connector it no longer
holds the endpoints, and before the split it still does.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, Literal

Kind = Literal["tool", "endpoint"]
Build = Literal["consolidator", "built-in", "endpoint"]


def _action(row: Any) -> str:
    return str(row.get("action") or "") if isinstance(row, dict) else ""


def _dicts(value: Any) -> list[dict]:
    return [r for r in (value or []) if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Classification — what a row IS, independent of which list it sits in
# ---------------------------------------------------------------------------


def is_consolidator(row: Any) -> bool:
    """A consolidator carries its own code; config without code is not one."""
    cfg = row.get("consolidator_config") if isinstance(row, dict) else None
    return isinstance(cfg, dict) and bool(cfg.get("function_code"))


def is_builtin(connector: str | None, row: Any) -> bool:
    """A registered handler runs INSTEAD of the row, so the row is a built-in."""
    if not connector:
        return False
    from app.agents.internal_tools import get_handler

    return get_handler(connector, _action(row)) is not None


def build_of(
    connector: str | None, row: Any, *, execution_mode: str | None = None
) -> Build:
    """How a row is built: consolidator, built-in (Norm code) or endpoint.

    A handler shadows a consolidator at dispatch (tool_loop, tool_executor), so
    a row with both is a built-in. A row on an ``internal`` connector with no
    handler is a broken built-in (the validator reports it) — never an
    endpoint, because there is nothing outside Norm for it to call.
    """
    if is_builtin(connector, row):
        return "built-in"
    if is_consolidator(row):
        return "consolidator"
    if execution_mode == "internal":
        return "built-in"
    return "endpoint"


def classify(
    connector: str | None, row: Any, *, execution_mode: str | None = None
) -> Kind:
    """``"tool"`` for a consolidator or built-in, else ``"endpoint"``."""
    return (
        "endpoint"
        if build_of(connector, row, execution_mode=execution_mode) == "endpoint"
        else "tool"
    )


def partition(
    connector: str | None, rows_: list, *, execution_mode: str | None = None
) -> tuple[list[dict], list[dict]]:
    """Split rows into (tools, endpoints), keeping their order."""
    tools_: list[dict] = []
    endpoints_: list[dict] = []
    for r in _dicts(rows_):
        if classify(connector, r, execution_mode=execution_mode) == "tool":
            tools_.append(r)
        else:
            endpoints_.append(r)
    return tools_, endpoints_


# ---------------------------------------------------------------------------
# What a tool does — its declared effect (approvals, Oct 2026)
# ---------------------------------------------------------------------------

#: ``read`` changes nothing; ``draft`` prepares something a person finishes in
#: an editor (the editor's button is the approval); ``write`` changes something,
#: and its ``approval`` says whether a person is asked. Every tool an App claims
#: declares one (config_validator). Rows that don't yet fall back to the old
#: rule — a GET runs, anything else asks (services/approvals.gate).
EFFECTS = ("read", "draft", "write")


def effect(row: Any) -> str | None:
    """The tool's declared effect, or None when the row doesn't say."""
    value = row.get("effect") if isinstance(row, dict) else None
    return value if value in EFFECTS else None


def approval(row: Any) -> dict:
    """A write tool's approval policy (``{}`` when none is declared).

    Keys: ``default`` ("ask" | "auto"), ``allow_auto`` (may a person switch it
    to "always allow"), ``label`` (what it does, for the card and Settings),
    and for a tiered tool ``levels`` ([{id, label, description, writes}], the
    first writing nothing) and ``options`` (switches within a level).
    """
    value = row.get("approval") if isinstance(row, dict) else None
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Reading a spec
# ---------------------------------------------------------------------------


def is_split(spec: Any) -> bool:
    """Has this connector's ``endpoints`` list been populated (even if empty)?"""
    return isinstance(getattr(spec, "endpoints", None), list)


def rows(spec: Any) -> list[dict]:
    """Every row — tools first, then endpoints. The stored dicts themselves, so
    a caller that mutates one must ``flag_modified`` the list it came from."""
    if spec is None:
        return []
    return _dicts(getattr(spec, "tools", None)) + _dicts(
        getattr(spec, "endpoints", None)
    )


def actions(spec: Any) -> list[str]:
    return [_action(r) for r in rows(spec)]


def find(spec: Any, action: str) -> dict | None:
    """The row for ``action``, whichever list it is in."""
    for r in rows(spec):
        if r.get("action") == action:
            return r
    return None


def tools(spec: Any) -> list[dict]:
    """Only tools. Before the split, the rows of ``tools`` that classify as one."""
    if spec is None:
        return []
    if is_split(spec):
        return _dicts(spec.tools)
    name = getattr(spec, "connector_name", None)
    mode = getattr(spec, "execution_mode", None)
    return [
        r
        for r in _dicts(getattr(spec, "tools", None))
        if classify(name, r, execution_mode=mode) == "tool"
    ]


def endpoints(spec: Any) -> list[dict]:
    """Only endpoints. Before the split, the rows of ``tools`` that classify as one."""
    if spec is None:
        return []
    if is_split(spec):
        return _dicts(spec.endpoints)
    name = getattr(spec, "connector_name", None)
    mode = getattr(spec, "execution_mode", None)
    return [
        r
        for r in _dicts(getattr(spec, "tools", None))
        if classify(name, r, execution_mode=mode) == "endpoint"
    ]


def find_tool(spec: Any, action: str) -> dict | None:
    for r in tools(spec):
        if r.get("action") == action:
            return r
    return None


def find_endpoint(spec: Any, action: str) -> dict | None:
    for r in endpoints(spec):
        if r.get("action") == action:
            return r
    return None


def kind_of(spec: Any, row: dict) -> Kind:
    """Where a row sits: its list once split, its classification before."""
    if is_split(spec):
        return "endpoint" if any(r is row for r in _dicts(spec.endpoints)) else "tool"
    return classify(
        getattr(spec, "connector_name", None),
        row,
        execution_mode=getattr(spec, "execution_mode", None),
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def upsert(existing: list | None, new_rows: list) -> list[dict]:
    """``existing`` with each of ``new_rows`` replacing the row of the same
    action in place, or appended when new."""
    out = list(_dicts(existing))
    index = {_action(r): i for i, r in enumerate(out)}
    for r in _dicts(new_rows):
        i = index.get(_action(r))
        if i is None:
            index[_action(r)] = len(out)
            out.append(r)
        else:
            out[i] = r
    return out


_local = threading.local()


@contextmanager
def moving() -> Iterator[None]:
    """Suspend the listeners' routing and ``added_at`` stamping while a script
    MOVES rows between the two lists — a move is not an edit, so nothing may
    be re-stamped or re-routed on the way."""
    prev = getattr(_local, "moving", False)
    _local.moving = True
    try:
        yield
    finally:
        _local.moving = prev


def is_moving() -> bool:
    return bool(getattr(_local, "moving", False))
