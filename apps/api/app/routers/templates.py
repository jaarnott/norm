"""Dashboard templates CRUD — manage reusable dashboard layouts in the config DB."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.config_models import DashboardTemplate
from app.db.engine import get_config_db, get_config_db_rw, get_db
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard-templates", tags=["dashboard-templates"])


class TemplateCreate(BaseModel):
    slug: str
    agent_slug: str
    title: str
    description: str | None = None
    charts: list[dict] = []
    enabled: bool = True


class TemplateUpdate(BaseModel):
    agent_slug: str | None = None
    title: str | None = None
    description: str | None = None
    charts: list[dict] | None = None
    enabled: bool | None = None


def _to_dict(t: DashboardTemplate) -> dict:
    return {
        "id": t.id,
        "slug": t.slug,
        "agent_slug": t.agent_slug,
        "title": t.title,
        "description": t.description,
        "charts": t.charts or [],
        "chart_count": len(t.charts or []),
        "enabled": t.enabled,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("")
async def list_templates(
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    templates = (
        config_db.query(DashboardTemplate)
        .order_by(DashboardTemplate.agent_slug, DashboardTemplate.title)
        .all()
    )
    return {"templates": [_to_dict(t) for t in templates]}


@router.get("/{slug}")
async def get_template(
    slug: str,
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    t = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == slug)
        .first()
    )
    if not t:
        raise HTTPException(404, "Template not found")
    return _to_dict(t)


@router.post("", status_code=201)
async def create_template(
    body: TemplateCreate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    existing = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == body.slug)
        .first()
    )
    if existing:
        raise HTTPException(409, f"Template already exists: {body.slug}")
    t = DashboardTemplate(
        slug=body.slug,
        agent_slug=body.agent_slug,
        title=body.title,
        description=body.description,
        charts=body.charts,
        enabled=body.enabled,
    )
    config_db.add(t)
    config_db.commit()
    config_db.refresh(t)
    return _to_dict(t)


@router.put("/{slug}")
async def update_template(
    slug: str,
    body: TemplateUpdate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    t = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == slug)
        .first()
    )
    if not t:
        raise HTTPException(404, "Template not found")
    if body.agent_slug is not None:
        t.agent_slug = body.agent_slug
    if body.title is not None:
        t.title = body.title
    if body.description is not None:
        t.description = body.description
    if body.charts is not None:
        t.charts = body.charts
    if body.enabled is not None:
        t.enabled = body.enabled
    config_db.commit()
    config_db.refresh(t)
    return _to_dict(t)


@router.delete("/{slug}")
async def delete_template(
    slug: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    t = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == slug)
        .first()
    )
    if not t:
        raise HTTPException(404, "Template not found")
    config_db.delete(t)
    config_db.commit()
    return {"deleted": True}


@router.post("/seed")
async def seed_templates_endpoint(
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Seed default templates from the Python definitions."""
    from app.db.dashboard_templates import seed_templates

    count = seed_templates(config_db)
    return {"seeded": count}


@router.post("/{slug}/edit")
async def create_temp_report_for_editing(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Create a temporary Report from a template for live editing."""
    from app.db.models import Report, ReportChart
    from sqlalchemy.orm.attributes import flag_modified

    tmpl = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == slug)
        .first()
    )
    if not tmpl:
        raise HTTPException(404, "Template not found")

    # Create a temporary report flagged for cleanup
    report = Report(
        user_id=user.id,
        title=f"[Template] {tmpl.title}",
        description=tmpl.description,
        is_dashboard=True,
        agent_slug=tmpl.agent_slug,
        refresh_interval_seconds=0,  # no auto-refresh for editing
    )
    db.add(report)
    db.flush()

    layout = []
    for chart_def in tmpl.charts or []:
        chart = ReportChart(
            report_id=report.id,
            title=chart_def.get("title", ""),
            chart_type=chart_def.get("chart_type", "bar"),
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
    flag_modified(report, "layout")
    db.commit()
    db.refresh(report)

    return {"report_id": report.id, "template_slug": slug}


@router.post("/{slug}/save-from-report/{report_id}")
async def save_report_to_template(
    slug: str,
    report_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Snapshot a report's current charts/layout back into a template."""
    from app.db.models import Report, ReportChart

    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")

    tmpl = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == slug)
        .first()
    )
    if not tmpl:
        raise HTTPException(404, "Template not found")

    charts = (
        db.query(ReportChart)
        .filter(ReportChart.report_id == report_id)
        .order_by(ReportChart.position)
        .all()
    )
    layout = report.layout or []
    layout_map = {
        item.get("chart_id"): item for item in layout if isinstance(item, dict)
    }

    chart_defs = []
    for c in charts:
        lo = layout_map.get(c.id, {})
        chart_defs.append(
            {
                "title": c.title,
                "chart_type": c.chart_type,
                "chart_spec": c.chart_spec or {},
                "script": c.script or {},
                "layout": {k: v for k, v in lo.items() if k != "chart_id"},
            }
        )

    tmpl.title = report.title.replace("[Template] ", "")
    tmpl.description = report.description
    tmpl.agent_slug = report.agent_slug or tmpl.agent_slug
    tmpl.charts = chart_defs
    config_db.commit()

    # Clean up the temporary report
    for c in charts:
        db.delete(c)
    db.delete(report)
    db.commit()

    return _to_dict(tmpl)
