"""Norm builds and changes dashboards (Oct 2026).

Until now Norm had no way to make a dashboard, so when Rich asked for one it
built an App (20 Oct 2026), while the dashboard page told people to "ask Norm
to build a dashboard". Plan: docs/plans/norm-builds-dashboards.md.

Two tools on ``norm_reports``, both thin layers over the dashboard rules in
``routers/reports_crud.py`` (who sees what, organisations, copies, history):

* ``get_dashboards`` (read) lists the dashboards the person can see for an AI
  agent, or describes one: its tiles in plain terms, where each sits, the
  period it opens on. Describing one also shows it, live, in the conversation.
  Norm reads a dashboard before changing it, so it never works from a stale
  copy (Rich's App lost a chart that way).
* ``manage_dashboard`` (write) makes and changes them. A change to a dashboard
  only its author sees runs at once; a change to one shared with the
  organisation shows a before-and-after card first (its preview answers
  "run" or "card": services/previews.py).

Every tile Norm adds or changes is tried once before it is saved, for the
person's venues on the dashboard's period: the tool must answer, the list it
plots must exist, and every field it shows must really be in the rows. A wrong
field name used to give a tile that was silently empty. Only reading tools can
be tiles (a tile refreshes on its own every few minutes).

Norm places tiles in words: a width (full, half, a third, a quarter…) and
where (after another tile, or at the top). The grid's numbers are worked out
here, so tiles never overlap and "three columns" is three thirds side by side.
"""

from __future__ import annotations

import copy
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.internal_tools import _thread_user, previews, register

#: The AI agents with a dashboard page (web pageRegistry: dashboard_view).
AGENTS = (
    "reports",
    "procurement",
    "hr",
    "time_attendance",
    "executive_chef",
    "finance",
    "marketing",
)

#: What Norm calls a kind of tile -> the chart type the page draws.
KINDS = {
    "number": "kpi",
    "bar": "bar",
    "stacked_bar": "stacked_bar",
    "line": "line",
    "pie": "pie",
    "table": "table",
    "text": "text",
    "app": "component",
}
KIND_OF = {v: k for k, v in KINDS.items()}

#: Widths in the page's 24-column grid, and heights in its 40px rows.
WIDTHS = {
    "full": 24,
    "three_quarters": 18,
    "two_thirds": 16,
    "half": 12,
    "third": 8,
    "quarter": 6,
}
WIDTH_WORDS = {
    24: "full width",
    18: "three quarters wide",
    16: "two thirds wide",
    12: "half wide",
    8: "a third wide",
    6: "a quarter wide",
}
HEIGHTS = {"short": 4, "medium": 8, "tall": 12, "extra_tall": 16}
HEIGHT_WORDS = {v: k.replace("_", " ") for k, v in HEIGHTS.items()}
DEFAULT_HEIGHT = {"kpi": 4, "text": 4, "table": 10, "component": 14}

OPS = (
    "create",
    "add_tile",
    "change_tile",
    "move_tile",
    "remove_tile",
    "rename",
    "set_period",
    "share",
    "unshare",
    "copy",
    "use",
    "restore",
)
#: Ops that never change a dashboard someone else sees.
_PRIVATE_OPS = {"create", "copy", "use", "share"}


class _Refused(Exception):
    """A change that can't be made as asked; the message goes to Norm."""


def _rc():
    from app.routers import reports_crud

    return reports_crud


def _config_session():
    from app.db.engine import _ConfigSessionLocal

    return _ConfigSessionLocal()


def _obj(value):
    """The model sometimes sends an object as a JSON string."""
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# ------------------------------------------------------------- the grid ----


def _items(report) -> list[dict]:
    return [dict(i) for i in (report.layout or []) if isinstance(i, dict)]


def _overlaps(a: dict, row: int, col: int, w: int, h: int) -> bool:
    return not (
        a["col"] + a["colSpan"] <= col
        or col + w <= a["col"]
        or a["row"] + a["rowSpan"] <= row
        or row + h <= a["row"]
    )


def _fits(layout: list[dict], row: int, col: int, w: int, h: int) -> bool:
    return (
        col >= 1
        and col + w - 1 <= 24
        and not any(_overlaps(i, row, col, w, h) for i in layout)
    )


def _reading_order(layout: list[dict]) -> list[dict]:
    return sorted(layout, key=lambda i: (i["row"], i["col"]))


def _place(
    layout: list[dict], w: int, h: int, after: str | None, top: bool
) -> tuple[int, int]:
    """Where a w-by-h tile goes, shifting tiles down to make room. With
    ``after``, beside that tile if it fits there, else in a new row under its
    row; otherwise after the last tile."""
    if top:
        for i in layout:
            i["row"] += h
        return 1, 1
    anchor = next((i for i in layout if i["chart_id"] == after), None)
    if anchor is None and layout:
        anchor = _reading_order(layout)[-1]
    if anchor is None:
        return 1, 1
    col = anchor["col"] + anchor["colSpan"]
    if _fits(layout, anchor["row"], col, w, h):
        return anchor["row"], col
    # a new row under everything that shares the anchor's row
    band_end = anchor["row"] + anchor["rowSpan"]
    for i in layout:
        if i["row"] < band_end and i["row"] + i["rowSpan"] > anchor["row"]:
            band_end = max(band_end, i["row"] + i["rowSpan"])
    for i in layout:
        if i["row"] >= band_end:
            i["row"] += h
    return band_end, 1


def _close_gaps(layout: list[dict]) -> None:
    """Pull tiles up over rows nothing occupies."""
    if not layout:
        return
    row = 1
    while row < max(i["row"] + i["rowSpan"] for i in layout):
        if any(i["row"] <= row < i["row"] + i["rowSpan"] for i in layout):
            row += 1
            continue
        for i in layout:
            if i["row"] > row:
                i["row"] -= 1


def _place_words(layout: list[dict], item: dict) -> str:
    rows = sorted({i["row"] for i in layout})
    n = rows.index(item["row"]) + 1 if item["row"] in rows else "?"
    side = "left" if item["col"] == 1 else f"from column {item['col']} of 24"
    width = WIDTH_WORDS.get(item["colSpan"], f"{item['colSpan']}/24 wide")
    height = HEIGHT_WORDS.get(item["rowSpan"], f"{item['rowSpan']} rows tall")
    return f"row {n}, {side}, {width}, {height}"


# ------------------------------------------------------------ the tiles ----


def _describe_tile(chart, layout: list[dict]) -> dict:
    spec = chart.chart_spec or {}
    script = chart.script or {}
    kind = KIND_OF.get(chart.chart_type, chart.chart_type)
    out = {"tile_id": chart.id, "title": chart.title, "kind": kind}
    if spec.get("component_key") == "app_runner":
        out["kind"] = "app"
        out["app"] = (spec.get("component_props") or {}).get("slug")
    elif spec.get("component_key"):
        out["kind"] = "board"
        out["board"] = spec["component_key"]
    if script.get("action"):
        out["tool"] = f"{script.get('connector')}.{script['action']}"
        out["inputs"] = script.get("params") or {}
        # Only a tile with a period input follows the page's period control
        # (reports_crud._apply_global_dates); Norm told a person the opposite.
        out["follows_period_control"] = "period" in out["inputs"]
        if script.get("rows"):
            out["list"] = script["rows"]
    if kind == "text":
        out["text"] = spec.get("text_content")
    if spec.get("x_axis"):
        out["x"] = (spec["x_axis"] or {}).get("key")
    if spec.get("series") and chart.chart_type not in ("kpi", "table"):
        out["y"] = [s.get("key") for s in spec["series"] if isinstance(s, dict)]
    if spec.get("value_key"):
        out["value"] = spec["value_key"]
    if spec.get("comparison_key"):
        out["compare"] = spec["comparison_key"]
    if spec.get("delta_key"):
        out["change"] = spec["delta_key"]
    if spec.get("field_order"):
        out["columns"] = spec["field_order"]
    if spec.get("format"):
        out["format"] = spec["format"]
    if spec.get("refresh_interval_seconds"):
        out["refresh_minutes"] = round(spec["refresh_interval_seconds"] / 60)
    item = next((i for i in layout if i.get("chart_id") == chart.id), None)
    if item:
        out["place"] = _place_words(layout, item)
    return out


def _tile_parts(tile: dict) -> tuple[str, str, dict, dict, int, int]:
    """A tile as Norm describes it -> (title, chart type, chart spec, script,
    width, height). Raises _Refused for anything that can't be drawn."""
    kind = str(tile.get("kind") or "").strip()
    if kind not in KINDS:
        raise _Refused(f"kind must be one of {', '.join(KINDS)}; got '{kind}'.")
    chart_type = KINDS[kind]
    title = str(tile.get("title") or "").strip()
    if not title:
        raise _Refused("Every tile needs a title.")
    spec: dict = {}
    script: dict = {}
    if kind == "text":
        text = str(tile.get("text") or "").strip()
        if not text:
            raise _Refused("A text tile needs `text`.")
        spec["text_content"] = text
    elif kind == "app":
        slug = str(tile.get("app") or "").strip()
        if not slug:
            raise _Refused("An app tile needs `app`: the App's slug.")
        spec = {
            "component_key": "app_runner",
            "title": title,
            "component_props": {"slug": slug},
        }
    else:
        tool = str(tile.get("tool") or "")
        if "." not in tool:
            raise _Refused(
                "`tool` names the reading tool as connector.action, "
                "e.g. loadedhub.get_sales."
            )
        connector, action = tool.split(".", 1)
        script = {
            "connector": connector,
            "action": action,
            "params": _obj(tile.get("inputs")) or {},
        }
        if not isinstance(script["params"], dict):
            raise _Refused("`inputs` must be an object of the tool's inputs.")
        if tile.get("list"):
            script["rows"] = str(tile["list"])
        fmt = tile.get("format")
        if kind == "number":
            if not tile.get("value"):
                raise _Refused("A number tile needs `value`: the field to show.")
            spec["value_key"] = tile["value"]
            if fmt:
                spec["format"] = fmt
            if tile.get("compare"):
                spec["comparison_key"] = tile["compare"]
            if tile.get("change"):
                spec["delta_key"] = tile["change"]
            if tile.get("note"):
                spec["note"] = tile["note"]
        elif kind == "table":
            columns = _obj(tile.get("columns")) or []
            if columns:
                spec["field_order"] = list(columns)
            labels = _obj(tile.get("labels")) or {}
            if labels:
                spec["field_labels"] = dict(labels)
            formats = _obj(tile.get("formats")) or {}
            if formats:
                spec["field_formats"] = {k: {"type": v} for k, v in formats.items()}
        else:
            x = tile.get("x")
            ys = _obj(tile.get("y")) or []
            if isinstance(ys, str):
                ys = [ys]
            if not x or not ys:
                raise _Refused(
                    f"A {kind} tile needs `x` (the field along the bottom) and "
                    "`y` (the field or fields it plots)."
                )
            labels = _obj(tile.get("labels")) or {}
            spec["x_axis"] = {"key": x, "label": labels.get(x, x)}
            spec["series"] = [{"key": y, "label": labels.get(y, y)} for y in ys]
            if fmt:
                spec["y_axis"] = {"key": ys[0], "format": fmt}
            if tile.get("horizontal"):
                spec["orientation"] = "horizontal"
            if tile.get("by_venue"):
                spec["group_by"] = "venue"
        if tile.get("refresh_minutes"):
            spec["refresh_interval_seconds"] = int(float(tile["refresh_minutes"]) * 60)
    width = WIDTHS.get(str(tile.get("width") or ""), None)
    if width is None:
        width = 6 if chart_type == "kpi" else 12 if chart_type != "component" else 24
    height = HEIGHTS.get(str(tile.get("height") or ""), None)
    if height is None:
        height = DEFAULT_HEIGHT.get(chart_type, 8)
    return title, chart_type, spec, script, width, height


#: Changes that alter what a tile reads or plots: only these re-try it.
_DATA_KEYS = {
    "kind",
    "tool",
    "inputs",
    "list",
    "x",
    "y",
    "value",
    "columns",
    "compare",
    "change",
}


def _new_tile(chart, changes: dict) -> tuple[str, str, dict, dict, int, int]:
    """The tile after ``changes``: a new one from them alone, or an existing
    one with them on top. An existing tile keeps every setting Norm didn't
    touch — a template's comparison or ratio settings are not in Norm's terms,
    and rebuilding the spec from those terms dropped them."""
    if chart is None:
        return _tile_parts(changes)
    title, chart_type, spec, script, width, height = _tile_parts(
        _merged_tile(chart, changes)
    )
    kept = copy.deepcopy(chart.chart_spec or {})
    if chart_type != chart.chart_type:
        kept = {}
    return title, chart_type, {**kept, **spec}, script, width, height


def _fields_used(chart_type: str, spec: dict) -> list[str]:
    fields = []
    if spec.get("x_axis"):
        fields.append(spec["x_axis"]["key"])
    if chart_type not in ("kpi", "table"):
        fields += [s["key"] for s in spec.get("series") or []]
    for key in ("value_key", "comparison_key", "delta_key"):
        if spec.get(key):
            fields.append(spec[key])
    fields += list(spec.get("field_order") or [])
    return [f for f in fields if f and f != "venue"]


def _try_tile(db, user, report, chart_type, spec, script, allow_empty) -> dict:
    """Run the tile once, as the page would, and check it can draw. Returns a
    short account for Norm; raises _Refused with exactly what was wrong."""
    from app.connectors import spec_rows
    from app.db.models import ConnectionSpec

    rc = _rc()
    cdb = _config_session()
    try:
        try:
            rc._require_tool(script, cdb)
        except HTTPException as exc:
            raise _Refused(str(exc.detail)) from None
        row = spec_rows.find_tool(
            cdb.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == script["connector"])
            .first(),
            script["action"],
        )
        effect = spec_rows.effect(row)
        method = str((row or {}).get("method") or "GET").upper()
        if effect not in ("read", None) or (effect is None and method != "GET"):
            raise _Refused(
                f"{script['connector']}.{script['action']} changes data, and a "
                "tile refreshes on its own every few minutes. A tile can only "
                "use a tool that reads."
            )
        period = (report.global_filters or {}).get("default_period") or "this_month"
        gf = {"venue_id": "__all__", "period": period}
        rows, errors, _ = rc.run_tile(db, cdb, user, report, script, gf)
    finally:
        cdb.close()
    when = period.replace("_", " ")
    if errors and not rows:
        raise _Refused(
            f"The tool answered with an error for {when}: "
            + "; ".join(dict.fromkeys(errors))
        )
    if not rows:
        if allow_empty:
            return {
                "tried": f"no rows for {when} at the person's venues; fields not checked"
            }
        raise _Refused(
            f"The tool returned nothing for {when} at the person's venues, so "
            "the tile's fields can't be checked. Try the tool yourself first; "
            "if an empty tile is expected (no sales yet today), send "
            "allow_empty: true."
        )
    have = set()
    for r in rows[:50]:
        if isinstance(r, dict):
            have.update(r.keys())
    missing = [f for f in _fields_used(chart_type, spec) if f not in have]
    if missing:
        raise _Refused(
            f"These fields aren't in the rows the tool returns: {', '.join(missing)}. "
            f"The rows have: {', '.join(sorted(have)[:40])}."
            + (
                ""
                if script.get("rows")
                else " If the data is under another list, name it with `list`."
            )
        )
    sample = [
        {k: v for k, v in list(r.items())[:12]} for r in rows[:3] if isinstance(r, dict)
    ]
    follows = "period" in (script.get("params") or {})
    return {
        "tried": f"{len(rows)} rows for {when}",
        "first_rows": sample,
        "follows_period_control": follows,
    }


def _check_app(db, user, report, slug: str) -> None:
    """An app tile's App must be one the person can open here; on a shared
    dashboard everyone must be able to open it, or they see an empty tile."""
    from app.db.models import AppShare
    from app.routers.apps import _load
    from app.services.app_runtime import resolve_access
    from app.services.builtin_apps import is_builtin

    cdb = _config_session()
    try:
        try:
            app, _ = _load(db, slug, user, cdb)
        except HTTPException:
            raise _Refused(f"There's no App '{slug}' here that the person can open.")
    finally:
        cdb.close()
    if not resolve_access(db, app, user).can_run:
        raise _Refused(f"The person can't open the App '{slug}'.")
    if report.is_published and not is_builtin(app):
        shared = (
            db.query(AppShare)
            .filter(
                AppShare.app_id == app.id,
                AppShare.principal_type == "organization",
                AppShare.principal_id == app.organization_id,
            )
            .first()
        )
        if shared is None:
            raise _Refused(
                f"The App '{slug}' is private, so everyone else who sees this "
                "shared dashboard would get an empty tile. The person can share "
                "the App with the organisation from its page first."
            )


# ------------------------------------------------------- reading them ----


def _describe(db, report, user) -> dict:
    rc = _rc()
    layout = _items(report)
    last = rc._last_version(db, report.id)
    return {
        "dashboard_id": report.id,
        "title": report.title,
        "agent": report.agent_slug,
        "yours": report.user_id == user.id,
        "shared": bool(report.is_published),
        "in_use": rc._choices(db, user).get(report.agent_slug) == report.id,
        "opens_on": (report.global_filters or {}).get("default_period") or "this_month",
        "version": last.version if last else None,
        "tiles": [
            _describe_tile(c, layout)
            for c in sorted(
                report.charts,
                key=lambda c: next(
                    ((i["row"], i["col"]) for i in layout if i.get("chart_id") == c.id),
                    (10_000, c.position),
                ),
            )
        ],
    }


@register("norm_reports", "get_dashboards")
def _get_dashboards(params: dict, db: Session, thread_id: str | None) -> dict:
    from app.db.config_models import DashboardTemplate
    from app.db.models import Report

    user = _thread_user(db, thread_id)
    if user is None:
        return {"success": False, "data": None, "error": "no user on this thread"}
    rc = _rc()
    params = params or {}
    dashboard_id = str(params.get("dashboard_id") or "").strip()
    if dashboard_id:
        try:
            report = rc._report_for_viewer(db, dashboard_id, user)
        except HTTPException:
            return {
                "success": False,
                "data": None,
                "error": "No dashboard with that id that this person can see here.",
            }
        out = _describe(db, report, user)
        if params.get("history"):
            from app.db.models import ReportVersion

            out["versions"] = [
                {"version": v.version, "note": v.note, "at": str(v.created_at)[:16]}
                for v in db.query(ReportVersion)
                .filter(ReportVersion.report_id == report.id)
                .order_by(ReportVersion.version.desc())
                .limit(20)
            ]
        # The page shows it, live, in the conversation.
        out["report_id"] = report.id
        return {"success": True, "data": out}

    agent = str(params.get("agent") or "").strip() or None
    q = db.query(Report).filter(Report.is_dashboard.is_(True), rc._visible_to(db, user))
    if agent:
        q = q.filter(Report.agent_slug == agent)
    chosen = rc._choices(db, user)
    dashboards = [
        {
            "dashboard_id": r.id,
            "title": r.title,
            "agent": r.agent_slug,
            "yours": r.user_id == user.id,
            "shared": bool(r.is_published),
            "in_use": chosen.get(r.agent_slug) == r.id,
            "tiles": len(r.charts),
        }
        for r in q.order_by(Report.agent_slug, Report.updated_at.desc()).all()
    ]
    cdb = _config_session()
    try:
        tq = cdb.query(DashboardTemplate).filter(DashboardTemplate.enabled.is_(True))
        if agent:
            tq = tq.filter(DashboardTemplate.agent_slug == agent)
        templates = [
            {"template": t.slug, "title": t.title, "agent": t.agent_slug}
            for t in tq.order_by(DashboardTemplate.agent_slug, DashboardTemplate.title)
        ]
    finally:
        cdb.close()
    return {
        "success": True,
        "data": {"dashboards": dashboards, "templates": templates, "_no_display": True},
    }


# ------------------------------------------------------ changing them ----


def _owned(db, user, dashboard_id: str):
    rc = _rc()
    try:
        report = rc._report_for_viewer(db, str(dashboard_id or ""), user)
    except HTTPException:
        raise _Refused("No dashboard with that id that this person can see here.")
    if report.user_id != user.id:
        raise _Refused(
            "Only the person who shared this dashboard can change it. Copy it "
            "(op copy) and change the copy."
        )
    return report


def _chart(report, tile_id: str):
    chart = next((c for c in report.charts if c.id == tile_id), None)
    if chart is None:
        raise _Refused(f"No tile {tile_id} on this dashboard. Read it again.")
    return chart


def _merged_tile(chart, changes: dict) -> dict:
    """The tile as it stands, in Norm's terms, with the changes on top."""
    current = _describe_tile(chart, [])
    current.pop("tile_id", None)
    current.pop("place", None)
    spec = chart.chart_spec or {}
    if spec.get("field_labels"):
        current["labels"] = spec["field_labels"]
    if spec.get("field_formats"):
        current["formats"] = {
            k: (v or {}).get("type") for k, v in spec["field_formats"].items()
        }
    if (spec.get("y_axis") or {}).get("format"):
        current.setdefault("format", spec["y_axis"]["format"])
    if spec.get("orientation") == "horizontal":
        current["horizontal"] = True
    if spec.get("group_by") == "venue":
        current["by_venue"] = True
    return {**current, **changes}


def _plan(db, user, params: dict) -> dict:
    """Check a change and work out what it does, without writing. Returns
    {"report", "lines" (before/after rows for a card), "tried"}."""
    rc = _rc()
    op = str(params.get("op") or "")
    if op not in OPS:
        raise _Refused(f"op must be one of {', '.join(OPS)}.")
    if op == "create":
        agent = str(params.get("agent") or "").strip()
        if agent not in AGENTS:
            raise _Refused(
                f"`agent` is the AI agent whose Dashboard page it is for: one of "
                f"{', '.join(AGENTS)}."
            )
        return {"report": None, "lines": []}
    if op in ("copy", "use"):
        try:
            report = rc._report_for_viewer(
                db, str(params.get("dashboard_id") or ""), user
            )
        except HTTPException:
            raise _Refused("No dashboard with that id that this person can see here.")
        if op == "use" and not report.is_dashboard:
            raise _Refused("That is a report, not a dashboard.")
        return {"report": report, "lines": []}

    report = _owned(db, user, params.get("dashboard_id"))
    lines: list[dict] = []
    tried = None
    if op in ("add_tile", "change_tile"):
        tile = _obj(params.get("tile")) or {}
        if not isinstance(tile, dict):
            raise _Refused("`tile` must be an object describing the tile.")
        before = chart = None
        if op == "change_tile":
            chart = _chart(report, str(params.get("tile_id") or ""))
            before = chart.title
        title, chart_type, spec, script, _, _ = _new_tile(chart, tile)
        retry = chart is None or bool(_DATA_KEYS & set(tile))
        if chart_type == "component":
            if retry:
                _check_app(db, user, report, spec["component_props"]["slug"])
        elif script and retry:
            tried = _try_tile(
                db,
                user,
                report,
                chart_type,
                spec,
                script,
                bool(params.get("allow_empty")),
            )
        what = f"{title} ({KIND_OF.get(chart_type, chart_type)}"
        what += f" of {script['connector']}.{script['action']})" if script else ")"
        lines.append(
            {
                "field": "New tile" if op == "add_tile" else "Tile",
                "before": before,
                "after": what,
            }
        )
    elif op == "move_tile":
        chart = _chart(report, str(params.get("tile_id") or ""))
        lines.append({"field": "Moved", "after": chart.title})
    elif op == "remove_tile":
        chart = _chart(report, str(params.get("tile_id") or ""))
        lines.append({"field": "Tile removed", "before": chart.title})
    elif op == "rename":
        title = str(params.get("title") or "").strip()
        if not title:
            raise _Refused("rename needs `title`.")
        lines.append({"field": "Title", "before": report.title, "after": title})
    elif op == "set_period":
        period = str(params.get("period") or "")
        if period not in rc._PERIOD_CODES:
            raise _Refused(f"period must be one of {', '.join(rc._PERIOD_CODES)}.")
        lines.append(
            {
                "field": "Opens on",
                "before": (report.global_filters or {}).get("default_period"),
                "after": period,
            }
        )
    elif op == "unshare":
        lines.append(
            {"field": "Shared with the organisation", "before": "Yes", "after": "No"}
        )
    elif op == "restore":
        from app.db.models import ReportVersion

        n = int(params.get("version") or 0)
        if (
            db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id, ReportVersion.version == n)
            .first()
            is None
        ):
            raise _Refused(f"This dashboard has no version {n}.")
        lines.append({"field": "Version", "after": f"bring back version {n}"})
    return {"report": report, "lines": lines, "tried": tried}


def _apply(db, user, params: dict, plan: dict) -> dict:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.models import Report, ReportChart

    rc = _rc()
    op = params["op"]
    report = plan["report"]
    note = f"Norm: {op.replace('_', ' ')}"

    if op == "create":
        cdb = _config_session()
        try:
            from app.db.config_models import DashboardTemplate

            tslug = str(params.get("template") or "").strip()
            if tslug:
                tmpl = (
                    cdb.query(DashboardTemplate)
                    .filter(
                        DashboardTemplate.slug == tslug,
                        DashboardTemplate.enabled.is_(True),
                    )
                    .first()
                )
                if tmpl is None:
                    raise _Refused(f"No template '{tslug}'.")
                report = rc._copy_template(db, cdb, tmpl, user)
                if params.get("title"):
                    report.title = str(params["title"])
                    db.commit()
                    rc._record(db, report, user.id, note)
            else:
                report = Report(
                    user_id=user.id,
                    organization_id=rc._org(db, user),
                    title=str(params.get("title") or "New dashboard"),
                    description=params.get("description"),
                    status="saved",
                    is_dashboard=True,
                    agent_slug=str(params["agent"]),
                    refresh_interval_seconds=300,
                    layout=[],
                )
                db.add(report)
                db.commit()
                rc._record(db, report, user.id, "Made by Norm")
        finally:
            cdb.close()
        if params.get("make_it_mine", True):
            rc._choose(db, user, report.agent_slug, report.id)
            db.commit()
        return report
    if op == "copy":
        return rc._copy_report(db, report, user, bool(params.get("make_it_mine", True)))
    if op == "use":
        rc._choose(db, user, report.agent_slug, report.id)
        db.commit()
        return report
    if op == "restore":
        return rc._restore(db, report, int(params["version"]), user)

    rc._baseline(db, report)
    layout = _items(report)
    if op in ("add_tile", "change_tile"):
        tile = _obj(params.get("tile")) or {}
        chart = _chart(report, str(params["tile_id"])) if op == "change_tile" else None
        title, chart_type, spec, script, width, height = _new_tile(chart, tile)
        if op == "add_tile":
            chart = ReportChart(
                report_id=report.id, data=[], position=len(report.charts)
            )
            db.add(chart)
        old_script = chart.script
        chart.title, chart.chart_type = title, chart_type
        chart.chart_spec, chart.script = spec, script
        if op == "change_tile" and old_script != script:
            chart.data, chart.data_meta = [], None
        db.flush()
        for field in ("chart_spec", "script", "data"):
            flag_modified(chart, field)
        item = next((i for i in layout if i["chart_id"] == chart.id), None)
        given = _obj(params.get("tile")) or {}
        resized = op == "add_tile" or "width" in given or "height" in given
        if item is None or resized:
            if item is not None:
                layout.remove(item)
            w = width if "width" in given or item is None else item["colSpan"]
            h = height if "height" in given or item is None else item["rowSpan"]
            if item is not None and _fits(layout, item["row"], item["col"], w, h):
                row, col = item["row"], item["col"]
            else:
                row, col = _place(
                    layout, w, h, params.get("after"), bool(params.get("top"))
                )
            layout.append(
                {
                    "chart_id": chart.id,
                    "col": col,
                    "row": row,
                    "colSpan": w,
                    "rowSpan": h,
                }
            )
        note = f"Norm: {'added' if op == 'add_tile' else 'changed'} the tile {title}"
    elif op == "move_tile":
        chart = _chart(report, str(params["tile_id"]))
        item = next((i for i in layout if i["chart_id"] == chart.id), None)
        w = WIDTHS.get(str(params.get("width") or ""), item["colSpan"] if item else 12)
        h = HEIGHTS.get(str(params.get("height") or ""), item["rowSpan"] if item else 8)
        if item is not None:
            layout.remove(item)
        if (
            item is not None
            and not params.get("after")
            and not params.get("top")
            and _fits(layout, item["row"], item["col"], w, h)
        ):
            row, col = item["row"], item["col"]
        else:
            _close_gaps(layout)
            row, col = _place(
                layout, w, h, params.get("after"), bool(params.get("top"))
            )
        layout.append(
            {"chart_id": chart.id, "col": col, "row": row, "colSpan": w, "rowSpan": h}
        )
        _close_gaps(layout)
        note = f"Norm: moved the tile {chart.title}"
    elif op == "remove_tile":
        chart = _chart(report, str(params["tile_id"]))
        layout = [i for i in layout if i["chart_id"] != chart.id]
        _close_gaps(layout)
        report.charts.remove(chart)
        note = f"Norm: removed the tile {chart.title}"
    elif op == "rename":
        report.title = str(params["title"]).strip()
        if params.get("description") is not None:
            report.description = params.get("description")
    elif op == "set_period":
        filters = copy.deepcopy(report.global_filters or {})
        filters["default_period"] = params["period"]
        report.global_filters = filters
        flag_modified(report, "global_filters")
    elif op in ("share", "unshare"):
        report.is_published = op == "share"

    report.layout = layout
    flag_modified(report, "layout")
    db.commit()
    rc._record(db, report, user.id, note)
    return report


@register("norm_reports", "manage_dashboard")
def _manage_dashboard(params: dict, db: Session, thread_id: str | None) -> dict:
    user = _thread_user(db, thread_id)
    if user is None:
        return {"success": False, "data": None, "error": "no user on this thread"}
    params = dict(params or {})
    try:
        plan = _plan(db, user, params)
        report = _apply(db, user, params, plan)
    except _Refused as exc:
        # Refused before anything is written (_plan checks everything first);
        # never roll back here, the tool loop's own rows share this session.
        return {"success": False, "data": None, "error": str(exc)}
    db.refresh(report)
    rc = _rc()
    layout = _items(report)
    last = rc._last_version(db, report.id)
    out = {
        "done": params.get("op"),
        "dashboard_id": report.id,
        "title": report.title,
        "shared": bool(report.is_published),
        # How it stands NOW. A read sent in the same turn as a change runs
        # before it and sees the old dashboard: Norm once told a person an
        # undo hadn't worked when it had (21 Oct 2026).
        "now": {
            "version": last.version if last else None,
            "tiles": [
                {"tile_id": c.id, "title": c.title, "place": d.get("place")}
                for c in report.charts
                for d in [_describe_tile(c, layout)]
            ],
        },
        "next": "`now` is how it stands after this change. To show it to the "
        "person, read it with get_dashboards in a later turn, never alongside "
        "a change: a read sent with a change sees it as it was before.",
    }
    if plan.get("tried"):
        out["tile_tried"] = plan["tried"]
    return {"success": True, "data": out}


@previews("norm_reports", "manage_dashboard")
def _preview_manage_dashboard(params: dict, db: Session, thread_id: str | None) -> dict:
    """A change to a dashboard only its author sees needs nobody's yes; one
    shared with the organisation shows what it changes first."""
    user = _thread_user(db, thread_id)
    if user is None:
        return {"error": "no user on this thread"}
    params = dict(params or {})
    try:
        plan = _plan(db, user, params)
    except _Refused as exc:
        return {"error": str(exc)}
    report = plan["report"]
    if params.get("op") in _PRIVATE_OPS or report is None or not report.is_published:
        return {"run": True}
    rc = _rc()
    last = rc._last_version(db, report.id)
    card = {
        "target": report.title,
        "changes": plan["lines"],
        "note": "This dashboard is shared: everyone in the organisation sees the change.",
    }
    basis = {
        "dashboard": report.id,
        "version": last.version if last else None,
        "updated": str(report.updated_at),
        "params": params,
    }
    return {"preview": card, "basis": basis}
