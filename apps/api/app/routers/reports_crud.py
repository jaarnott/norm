"""Reports CRUD — create, list, update, delete reports and charts."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, get_config_db
from app.db.models import Report, ReportChart, User
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CreateReportBody(BaseModel):
    title: str = "Untitled Report"
    venue_id: str | None = None


class UpdateReportBody(BaseModel):
    title: str | None = None
    description: str | None = None
    layout: list[dict] | None = None
    status: str | None = None
    is_dashboard: bool | None = None
    agent_slug: str | None = None
    is_published: bool | None = None
    refresh_interval_seconds: int | None = None
    global_filters: dict | None = None


class AddChartBody(BaseModel):
    title: str
    chart_type: str = "bar"
    chart_spec: dict = {}
    data: list[dict] = []
    script: dict = {}
    source_thread_id: str | None = None


class UpdateChartBody(BaseModel):
    title: str | None = None
    chart_type: str | None = None
    chart_spec: dict | None = None
    script: dict | None = None
    position: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _report_to_dict(report: Report) -> dict:
    return {
        "id": report.id,
        "title": report.title,
        "description": report.description,
        "layout": report.layout,
        "status": report.status,
        "venue_id": report.venue_id,
        "is_dashboard": report.is_dashboard,
        "agent_slug": report.agent_slug,
        "is_published": report.is_published,
        "is_template": report.is_template,
        "refresh_interval_seconds": report.refresh_interval_seconds,
        "global_filters": report.global_filters,
        "organization_id": report.organization_id,
        "charts": [_chart_to_dict(c) for c in report.charts],
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }


def _chart_to_dict(chart: ReportChart) -> dict:
    return {
        "id": chart.id,
        "title": chart.title,
        "chart_type": chart.chart_type,
        "chart_spec": chart.chart_spec,
        "data": chart.data,
        "script": chart.script,
        "position": chart.position,
        "source_thread_id": chart.source_thread_id,
        "created_at": chart.created_at.isoformat() if chart.created_at else None,
    }


def _sample_rows(rows: list, n: int) -> list:
    """Pick n rows evenly spaced across the list so the preview covers all groups."""
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def _resolve_date_placeholders(params: dict, day_start_time: str | None = None) -> dict:
    """Replace placeholder strings with actual timestamps.

    ``day_start_time`` is the business day start in HH:MM format (e.g. "05:00").
    When set, "today_start" means 05:00 today instead of 00:00.
    """
    import datetime as _dt
    import re as _re
    from zoneinfo import ZoneInfo as _ZoneInfo

    tz = _ZoneInfo("Pacific/Auckland")
    now = _dt.datetime.now(tz)

    # Parse business-day start hour/minute
    ds_hour, ds_min = 0, 0
    if day_start_time:
        parts = day_start_time.split(":")
        ds_hour = int(parts[0]) if parts else 0
        ds_min = int(parts[1]) if len(parts) > 1 else 0

    today_start = now.replace(hour=ds_hour, minute=ds_min, second=0, microsecond=0)
    # If it's before the business-day start, "today" is actually yesterday's start
    if now < today_start:
        today_start -= _dt.timedelta(days=1)
    today_end = today_start + _dt.timedelta(days=1) - _dt.timedelta(seconds=1)
    yesterday_start = today_start - _dt.timedelta(days=1)
    yesterday_end = today_start - _dt.timedelta(seconds=1)
    tomorrow_start = today_start + _dt.timedelta(days=1)
    tomorrow_end = tomorrow_start + _dt.timedelta(days=1) - _dt.timedelta(seconds=1)
    # Week start = most recent Monday at business-day start
    days_since_monday = today_start.weekday()
    week_start = today_start - _dt.timedelta(days=days_since_monday)
    month_start = today_start.replace(day=1)
    twelve_h_ago = now - _dt.timedelta(hours=12)

    placeholders = {
        "today_start": today_start.isoformat(),
        "today_end": today_end.isoformat(),
        "yesterday_start": yesterday_start.isoformat(),
        "yesterday_end": yesterday_end.isoformat(),
        "tomorrow_start": tomorrow_start.isoformat(),
        "tomorrow_end": tomorrow_end.isoformat(),
        "week_start": week_start.isoformat(),
        "month_start": month_start.isoformat(),
        "12h_ago": twelve_h_ago.isoformat(),
        "now": now.isoformat(),
    }

    round_30 = bool(params.get("_round_30"))

    resolved = {}
    for k, v in params.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str):
            for placeholder, value in placeholders.items():
                v = v.replace(placeholder, value)
            # Round ISO datetimes to nearest 30 minutes if flag is set
            if round_30 and _re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", v):
                try:
                    dt = _dt.datetime.fromisoformat(v)
                    m = dt.minute
                    if m == 0 or m == 30:
                        dt = dt.replace(second=0, microsecond=0)
                    elif m < 30:
                        dt = dt.replace(minute=30, second=0, microsecond=0)
                    else:
                        dt = (dt + _dt.timedelta(hours=1)).replace(
                            minute=0, second=0, microsecond=0
                        )
                    v = dt.isoformat()
                except ValueError:
                    pass
        resolved[k] = v
    return resolved


# ---------------------------------------------------------------------------
# Report CRUD
# ---------------------------------------------------------------------------


@router.post("")
async def create_report(
    body: CreateReportBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = Report(
        user_id=user.id,
        venue_id=body.venue_id,
        title=body.title,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _report_to_dict(report)


@router.get("")
async def list_reports(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reports = (
        db.query(Report)
        .filter(Report.user_id == user.id)
        .order_by(Report.updated_at.desc())
        .all()
    )
    return {"reports": [_report_to_dict(r) for r in reports]}


# ---------------------------------------------------------------------------
# Dashboard templates (must be before /{report_id} catch-all)
# ---------------------------------------------------------------------------


@router.get("/templates")
async def list_templates():
    """List available dashboard templates."""
    from app.db.dashboard_templates import DASHBOARD_TEMPLATES

    return {
        "templates": [
            {
                "slug": t["slug"],
                "agent_slug": t["agent_slug"],
                "title": t["title"],
                "description": t["description"],
                "chart_count": len(t["charts"]),
            }
            for t in DASHBOARD_TEMPLATES
        ]
    }


@router.post("/templates/{slug}/instantiate")
async def instantiate_template(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a real dashboard from a template."""
    from app.db.dashboard_templates import get_template

    template = get_template(slug)
    if not template:
        raise HTTPException(404, "Template not found")

    # Create the report
    report = Report(
        user_id=user.id,
        title=template["title"],
        description=template["description"],
        is_dashboard=True,
        agent_slug=template["agent_slug"],
        refresh_interval_seconds=300,  # 5 min default
    )
    db.add(report)
    db.flush()

    layout = []
    for chart_def in template["charts"]:
        chart = ReportChart(
            report_id=report.id,
            title=chart_def["title"],
            chart_type=chart_def["chart_type"],
            chart_spec=chart_def.get("chart_spec", {}),
            data=chart_def.get("data", []),
            script=chart_def.get("script", {}),
            position=len(layout),
        )
        db.add(chart)
        db.flush()
        item = dict(chart_def.get("layout", {}))
        item["chart_id"] = chart.id
        layout.append(item)

    report.layout = layout
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(report, "layout")
    db.commit()
    db.refresh(report)
    return _report_to_dict(report)


@router.get("/connector-list")
async def list_available_connectors(
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """List all connector names that have specs configured."""
    from app.db.config_models import ConnectorSpec

    specs = config_db.query(ConnectorSpec.connector_name).all()
    return {"connectors": sorted(set(s.connector_name for s in specs))}


@router.get("/connector-tools/{connector_name}")
async def get_connector_tools(
    connector_name: str,
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """List available tools/endpoints for a connector."""
    from app.connectors.tool_executor import list_connector_tools

    return list_connector_tools(connector_name, config_db)


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    return _report_to_dict(report)


@router.patch("/{report_id}")
async def update_report(
    report_id: str,
    body: UpdateReportBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    if body.title is not None:
        report.title = body.title
    if body.description is not None:
        report.description = body.description
    if body.layout is not None:
        report.layout = body.layout
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(report, "layout")
    if body.status is not None:
        report.status = body.status
    if body.is_dashboard is not None:
        report.is_dashboard = body.is_dashboard
    if body.agent_slug is not None:
        report.agent_slug = body.agent_slug
    if body.is_published is not None:
        report.is_published = body.is_published
    if body.refresh_interval_seconds is not None:
        report.refresh_interval_seconds = body.refresh_interval_seconds
    if body.global_filters is not None:
        report.global_filters = body.global_filters
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(report, "global_filters")
    db.commit()
    db.refresh(report)
    return _report_to_dict(report)


@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    db.delete(report)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chart CRUD
# ---------------------------------------------------------------------------


@router.post("/{report_id}/charts")
async def add_chart(
    report_id: str,
    body: AddChartBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")

    position = len(report.charts)
    chart = ReportChart(
        report_id=report_id,
        title=body.title,
        chart_type=body.chart_type,
        chart_spec=body.chart_spec,
        data=body.data,
        script=body.script,
        source_thread_id=body.source_thread_id,
        position=position,
    )
    db.add(chart)
    db.flush()  # Generate chart.id before using it in layout

    # Auto-add to grid layout — place below existing items
    layout = list(report.layout or [])
    max_row = 1
    for item in layout:
        if isinstance(item, dict) and "row" in item and "rowSpan" in item:
            max_row = max(max_row, item["row"] + item["rowSpan"])
    layout.append(
        {
            "chart_id": chart.id,
            "col": 1,
            "row": max_row,
            "colSpan": 24,
            "rowSpan": 8,  # 8 × 40px = 320px
        }
    )
    report.layout = layout
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(report, "layout")

    db.commit()
    db.refresh(report)
    return _report_to_dict(report)


@router.patch("/{report_id}/charts/{chart_id}")
async def update_chart(
    report_id: str,
    chart_id: str,
    body: UpdateChartBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    chart = (
        db.query(ReportChart)
        .filter(
            ReportChart.id == chart_id,
            ReportChart.report_id == report_id,
        )
        .first()
    )
    if not chart:
        raise HTTPException(404, "Chart not found")
    if body.title is not None:
        chart.title = body.title
    if body.chart_type is not None:
        chart.chart_type = body.chart_type
    if body.chart_spec is not None:
        chart.chart_spec = body.chart_spec
    if body.script is not None:
        chart.script = body.script
    if body.chart_spec is not None or body.script is not None:
        from sqlalchemy.orm.attributes import flag_modified

        if body.chart_spec is not None:
            flag_modified(chart, "chart_spec")
        if body.script is not None:
            flag_modified(chart, "script")
    if body.position is not None:
        chart.position = body.position
    db.commit()
    db.refresh(chart)
    return _chart_to_dict(chart)


@router.delete("/{report_id}/charts/{chart_id}")
async def remove_chart(
    report_id: str,
    chart_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    chart = (
        db.query(ReportChart)
        .filter(
            ReportChart.id == chart_id,
            ReportChart.report_id == report_id,
        )
        .first()
    )
    if not chart:
        raise HTTPException(404, "Chart not found")

    # Remove from grid layout
    report = db.query(Report).filter(Report.id == report_id).first()
    if report:
        report.layout = [
            item
            for item in (report.layout or [])
            if isinstance(item, dict) and item.get("chart_id") != chart_id
        ]
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(report, "layout")

    db.delete(chart)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Refresh — re-run chart scripts for fresh data
# ---------------------------------------------------------------------------


class RefreshBody(BaseModel):
    global_filters: dict | None = None


@router.post("/{report_id}/refresh")
async def refresh_report(
    report_id: str,
    body: RefreshBody | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Re-run all chart scripts and update with fresh data."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")

    from app.connectors.tool_executor import execute_connector_tool

    errors = []
    gf = (body.global_filters if body else None) or {}

    # Look up business-day start time from venue or first available venue
    day_start = None
    gf_venue = gf.get("venue_id")
    venue_id_for_day = (
        gf_venue if gf_venue and gf_venue != "__all__" else report.venue_id
    )
    if venue_id_for_day:
        from app.db.models import Venue

        v = db.query(Venue).filter(Venue.id == venue_id_for_day).first()
        if v and v.day_start_time:
            day_start = v.day_start_time
    if not day_start:
        # Fall back to first venue with a day_start_time
        from app.db.models import Venue

        v = db.query(Venue).filter(Venue.day_start_time.isnot(None)).first()
        if v:
            day_start = v.day_start_time

    def _resolve_params(params: dict) -> dict:
        """Resolve date placeholders and merge global date filters."""
        resolved = _resolve_date_placeholders(params, day_start_time=day_start)
        if gf.get("start"):
            for k in ("start_datetime", "start", "start_time", "from_date", "from"):
                if k in resolved:
                    resolved[k] = gf["start"]
        if gf.get("end"):
            for k in ("end_datetime", "end", "end_time", "to_date", "to"):
                if k in resolved:
                    resolved[k] = gf["end"]
        return resolved

    debug_info: list[dict] = []

    for chart in report.charts:
        script = chart.script
        if not script or not script.get("connector") or not script.get("action"):
            continue
        try:
            raw_params = script.get("params", {})
            resolved = _resolve_params(raw_params)

            # Resolve venue_id: "__all__" = explicit all-venues, else use filter/script/report
            gf_venue = gf.get("venue_id")
            if gf_venue == "__all__":
                venue_id = None
            elif gf_venue:
                venue_id = gf_venue
            else:
                venue_id = script.get("venue_id") or report.venue_id
            # Determine which venues to query
            if venue_id:
                venue_ids = [venue_id]
            else:
                from app.db.models import ConnectorConfig

                venue_configs = (
                    db.query(ConnectorConfig.venue_id)
                    .filter(
                        ConnectorConfig.connector_name == script["connector"],
                        ConnectorConfig.enabled == "true",
                    )
                    .all()
                )
                venue_ids = [vc.venue_id for vc in venue_configs] or [None]

            aggregated_rows: list[dict] = []
            chart_debug: dict = {
                "chart_id": chart.id,
                "title": chart.title,
                "action": script["action"],
                "params_resolved": resolved,
                "venues_queried": len(venue_ids),
            }
            any_success = False

            for vid in venue_ids:
                tool_result = execute_connector_tool(
                    connector_name=script["connector"],
                    action=script["action"],
                    params=resolved,
                    db=db,
                    config_db=config_db,
                    venue_id=vid,
                )

                if tool_result.rendered_request:
                    chart_debug["url"] = tool_result.rendered_request.get("url")
                    chart_debug["method"] = tool_result.rendered_request.get("method")

                if tool_result.success and tool_result.payload:
                    any_success = True
                    rows = (
                        tool_result.payload
                        if isinstance(tool_result.payload, list)
                        else [tool_result.payload]
                    )
                    # Always tag rows with venue name for multi-venue queries
                    if not venue_id and len(venue_ids) > 1:
                        from app.db.models import Venue

                        venue_obj = (
                            db.query(Venue).filter(Venue.id == vid).first()
                            if vid
                            else None
                        )
                        venue_name = venue_obj.name if venue_obj else (vid or "unknown")
                        for row in rows:
                            if isinstance(row, dict):
                                row["venue"] = venue_name
                    aggregated_rows.extend(rows)
                elif tool_result.error:
                    chart_debug.setdefault("venue_errors", []).append(
                        {"venue_id": vid, "error": tool_result.error}
                    )

            chart_debug["success"] = any_success
            chart_debug["rows"] = len(aggregated_rows)
            debug_info.append(chart_debug)

            if any_success and aggregated_rows:
                chart.data = aggregated_rows
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(chart, "data")
        except Exception as exc:
            errors.append(
                {"chart_id": chart.id, "title": chart.title, "error": str(exc)}
            )

    db.commit()
    db.refresh(report)
    result = _report_to_dict(report)
    if errors:
        result["refresh_errors"] = errors
    if debug_info:
        result["refresh_debug"] = debug_info
    return result


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------


@router.get("/dashboards")
async def list_dashboards(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all dashboards visible to the current user."""
    dashboards = (
        db.query(Report)
        .filter(
            Report.is_dashboard.is_(True),
            (Report.user_id == user.id) | (Report.is_published.is_(True)),
        )
        .order_by(Report.agent_slug, Report.updated_at.desc())
        .all()
    )
    return {"dashboards": [_report_to_dict(d) for d in dashboards]}


@router.get("/dashboards/{agent_slug}")
async def get_dashboard_for_agent(
    agent_slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the active dashboard for a specific agent."""
    # First try user's own dashboard, then published, then template
    dashboard = (
        db.query(Report)
        .filter(
            Report.is_dashboard.is_(True),
            Report.agent_slug == agent_slug,
            Report.user_id == user.id,
        )
        .order_by(Report.updated_at.desc())
        .first()
    )
    if not dashboard:
        dashboard = (
            db.query(Report)
            .filter(
                Report.is_dashboard.is_(True),
                Report.agent_slug == agent_slug,
                Report.is_published.is_(True),
            )
            .order_by(Report.updated_at.desc())
            .first()
        )
    if not dashboard:
        return {"dashboard": None}
    return {"dashboard": _report_to_dict(dashboard)}


class TestChartBody(BaseModel):
    venue_id: str | None = None
    param_overrides: dict | None = None


@router.post("/{report_id}/charts/{chart_id}/test")
async def test_chart_script(
    report_id: str,
    chart_id: str,
    body: TestChartBody | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Test a single chart's script — returns the rendered request, response, and accepted params."""
    from app.connectors.tool_executor import execute_connector_tool, get_tool_info

    chart = (
        db.query(ReportChart)
        .filter(ReportChart.id == chart_id, ReportChart.report_id == report_id)
        .first()
    )
    if not chart:
        raise HTTPException(404, "Chart not found")

    script = chart.script
    if not script or not script.get("connector") or not script.get("action"):
        return {
            "error": "Chart has no script configured",
            "script": script,
            "accepted_params": [],
        }

    # Get tool metadata (accepted params, field descriptions)
    tool_info = get_tool_info(script["connector"], script["action"], config_db)
    if tool_info.get("error"):
        return {"error": tool_info["error"], "script": script, **tool_info}

    # Resolve params (placeholder substitution) — use business-day start if available
    raw_params = dict(script.get("params", {}))
    if body and body.param_overrides:
        raw_params.update(body.param_overrides)

    venue_id = (body.venue_id if body else None) or script.get("venue_id")
    test_day_start = None
    if venue_id:
        from app.db.models import Venue

        v = db.query(Venue).filter(Venue.id == venue_id).first()
        if v and v.day_start_time:
            test_day_start = v.day_start_time
    if not test_day_start:
        from app.db.models import Venue

        v = db.query(Venue).filter(Venue.day_start_time.isnot(None)).first()
        if v:
            test_day_start = v.day_start_time

    resolved = _resolve_date_placeholders(raw_params, day_start_time=test_day_start)

    # Determine venues to test against
    if venue_id:
        venue_ids = [venue_id]
    else:
        from app.db.models import ConnectorConfig

        venue_configs = (
            db.query(ConnectorConfig.venue_id)
            .filter(
                ConnectorConfig.connector_name == script["connector"],
                ConnectorConfig.enabled == "true",
            )
            .all()
        )
        venue_ids = [vc.venue_id for vc in venue_configs] or [None]

    # Execute per venue and aggregate
    venue_results: list[dict] = []
    all_rows: list = []
    any_success = False
    last_rendered = None

    for vid in venue_ids:
        tool_result = execute_connector_tool(
            connector_name=script["connector"],
            action=script["action"],
            params=resolved,
            db=db,
            config_db=config_db,
            venue_id=vid,
        )
        # Resolve venue name
        venue_name = vid
        if vid:
            from app.db.models import Venue

            venue_obj = db.query(Venue).filter(Venue.id == vid).first()
            if venue_obj:
                venue_name = venue_obj.name

        vr: dict = {
            "venue_id": vid,
            "venue_name": venue_name,
            "success": tool_result.success,
            "error": tool_result.error,
            "row_count": tool_result.row_count,
        }
        if tool_result.rendered_request:
            vr["rendered_request"] = tool_result.rendered_request
            last_rendered = tool_result.rendered_request
        venue_results.append(vr)

        if tool_result.success and tool_result.payload:
            any_success = True
            rows = (
                tool_result.payload
                if isinstance(tool_result.payload, list)
                else [tool_result.payload]
            )
            # Tag rows with venue name when querying multiple venues
            if len(venue_ids) > 1 and venue_name:
                for row in rows:
                    if isinstance(row, dict):
                        row["venue"] = venue_name
            all_rows.extend(rows)

    return {
        "script": script,
        "accepted_params": tool_info.get("accepted_params", []),
        "resolved_params": resolved,
        "venue_id": venue_id,
        "venues_queried": len(venue_ids),
        "venue_results": venue_results,
        "has_credentials": last_rendered is not None,
        "success": any_success,
        "error": (venue_results[0].get("error") if len(venue_results) == 1 else None),
        "row_count": len(all_rows),
        "response_preview": all_rows if all_rows else None,
        "rendered_request": last_rendered,
    }


class PromoteToDashboardBody(BaseModel):
    agent_slug: str | None = None


@router.post("/{report_id}/promote-to-dashboard")
async def promote_to_dashboard(
    report_id: str,
    body: PromoteToDashboardBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Promote a report to a dashboard."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    report.is_dashboard = True
    if body and body.agent_slug:
        report.agent_slug = body.agent_slug
    db.commit()
    db.refresh(report)
    return _report_to_dict(report)
