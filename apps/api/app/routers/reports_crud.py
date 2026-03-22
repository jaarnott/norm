"""Reports CRUD — create, list, update, delete reports and charts."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
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


class AddChartBody(BaseModel):
    title: str
    chart_type: str = "bar"
    chart_spec: dict = {}
    data: list[dict] = []
    script: dict = {}
    source_task_id: str | None = None


class UpdateChartBody(BaseModel):
    title: str | None = None
    chart_type: str | None = None
    chart_spec: dict | None = None
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
        "source_task_id": chart.source_task_id,
        "created_at": chart.created_at.isoformat() if chart.created_at else None,
    }


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
    reports = db.query(Report).filter(Report.user_id == user.id).order_by(Report.updated_at.desc()).all()
    return {"reports": [_report_to_dict(r) for r in reports]}


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
        source_task_id=body.source_task_id,
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
    layout.append({
        "chart_id": chart.id,
        "col": 1,
        "row": max_row,
        "colSpan": 24,
        "rowSpan": 8,  # 8 × 40px = 320px
    })
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
    chart = db.query(ReportChart).filter(
        ReportChart.id == chart_id,
        ReportChart.report_id == report_id,
    ).first()
    if not chart:
        raise HTTPException(404, "Chart not found")
    if body.title is not None:
        chart.title = body.title
    if body.chart_type is not None:
        chart.chart_type = body.chart_type
    if body.chart_spec is not None:
        chart.chart_spec = body.chart_spec
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(chart, "chart_spec")
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
    chart = db.query(ReportChart).filter(
        ReportChart.id == chart_id,
        ReportChart.report_id == report_id,
    ).first()
    if not chart:
        raise HTTPException(404, "Chart not found")

    # Remove from grid layout
    report = db.query(Report).filter(Report.id == report_id).first()
    if report:
        report.layout = [item for item in (report.layout or []) if isinstance(item, dict) and item.get("chart_id") != chart_id]
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(report, "layout")

    db.delete(chart)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Refresh — re-run chart scripts for fresh data
# ---------------------------------------------------------------------------

@router.post("/{report_id}/refresh")
async def refresh_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-run all chart scripts and update with fresh data."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")

    from app.connectors.spec_executor import execute_spec
    from app.db.models import ConnectorSpec, ConnectorConfig

    errors = []
    for chart in report.charts:
        script = chart.script
        if not script or not script.get("connector") or not script.get("action"):
            continue
        try:
            spec = db.query(ConnectorSpec).filter(
                ConnectorSpec.connector_name == script["connector"],
            ).first()
            if not spec:
                errors.append({"chart_id": chart.id, "error": f"Connector not found: {script['connector']}"})
                continue

            tool_def = None
            for t in spec.tools or []:
                if t.get("action") == script["action"]:
                    tool_def = t
                    break
            if not tool_def:
                errors.append({"chart_id": chart.id, "error": f"Tool not found: {script['action']}"})
                continue

            config_row = db.query(ConnectorConfig).filter(
                ConnectorConfig.connector_name == script["connector"],
                ConnectorConfig.enabled == "true",
            ).first()
            credentials = config_row.config if config_row else {}

            result, _ = execute_spec(spec, tool_def, script.get("params", {}), credentials, db)
            if result.success and result.response_payload:
                chart.data = result.response_payload if isinstance(result.response_payload, list) else result.response_payload
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(chart, "data")
        except Exception as exc:
            errors.append({"chart_id": chart.id, "error": str(exc)})

    db.commit()
    db.refresh(report)
    result = _report_to_dict(report)
    if errors:
        result["refresh_errors"] = errors
    return result
