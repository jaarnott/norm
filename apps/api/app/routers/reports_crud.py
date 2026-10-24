"""Reports CRUD — create, list, update, delete reports and charts."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.services import caller_scope, screen_access

from app.db.engine import get_db, get_config_db
from app.db.models import OrganizationMembership, Report, ReportChart, User
from app.auth.dependencies import get_current_user

# Reports and the old tile dashboards, for a role that reaches them
# (services/screen_access): Saved Reports, or Norm Core's dashboard view —
# never venue staff by default. A chart's tool is checked against the role
# again whenever it runs (_tool_refusal), so a report can't be a way round the
# chat's tool list.
router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[
        Depends(
            screen_access.needs(
                "saved_reports_board", "report_builder", "dashboard_view"
            )
        )
    ],
)


def _tool_refusal(db: Session, config_db: Session, user: User, script: dict):
    """Why this chart's tool may not run for this person, or None."""
    if screen_access.tool_reachable(
        db, config_db, user, script.get("connector"), script.get("action")
    ):
        return None
    return screen_access.REFUSED_TOOL


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


def _report_to_dict(report: Report, viewer_id: str | None = None) -> dict:
    return {
        "id": report.id,
        # Whether the person asking may change it: only its owner may.
        "is_own": viewer_id is not None and report.user_id == viewer_id,
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
        "charts": [_chart_to_dict(c, viewer_id) for c in report.charts],
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }


def _chart_to_dict(chart: ReportChart, viewer_id: str | None = None) -> dict:
    data, meta = chart.data, chart.data_meta
    # A tile's saved figures are the person's who loaded them: they came from
    # THEIR venues. Anyone else gets an empty tile, which the page refreshes
    # for them. Until Oct 2026 a team member opening a shared dashboard saw the
    # manager's figures for venues they cannot see. A tile with no tool keeps
    # its data: that is its content (a text tile), not a cache.
    fetched_by = (meta or {}).get("user_id")
    if viewer_id and (chart.script or {}).get("action") and fetched_by != viewer_id:
        data, meta = [], None
    return {
        "id": chart.id,
        "title": chart.title,
        "chart_type": chart.chart_type,
        "chart_spec": chart.chart_spec,
        "data": data,
        "data_meta": meta,
        "script": chart.script,
        "position": chart.position,
        "source_thread_id": chart.source_thread_id,
        "created_at": chart.created_at.isoformat() if chart.created_at else None,
    }


# Who may see and change a report. Until 21 Oct 2026 every route here checked
# only "signed in": anyone could read, change or delete any report by its id,
# and a published dashboard showed in every organisation's picker. Now
# (docs/plans/norm-builds-dashboards.md):
# - a report belongs to the organisation it was made in, and is seen only
#   there: by its owner, and by everyone in it once shared;
# - only its owner changes it; anyone who can see it can copy it;
# - a report from before organisations were recorded is its owner's, and
#   shared with people in the owner's organisations.


def _org(db: Session, user: User) -> str | None:
    """The organisation the person is working in."""
    from app.services import active_org

    return active_org.org_id_for(db, user.id)


def _visible_to(db: Session, user: User):
    from sqlalchemy import select

    own = Report.user_id == user.id
    shared = Report.is_published.is_(True)
    org = _org(db, user)
    if org is None:
        return own
    colleagues = select(OrganizationMembership.user_id).where(
        OrganizationMembership.organization_id == org
    )
    unassigned = Report.organization_id.is_(None)
    return (
        (own & (unassigned | (Report.organization_id == org)))
        | (shared & (Report.organization_id == org))
        | (shared & unassigned & Report.user_id.in_(colleagues))
    )


def _report_for_viewer(db: Session, report_id: str, user: User) -> Report:
    report = (
        db.query(Report).filter(Report.id == report_id, _visible_to(db, user)).first()
    )
    if not report:
        raise HTTPException(404, "Report not found")
    return report


def _report_for_owner(db: Session, report_id: str, user: User) -> Report:
    report = _report_for_viewer(db, report_id, user)
    if report.user_id != user.id:
        raise HTTPException(
            403,
            "Only the person who made this dashboard can change it. "
            "You can make your own copy.",
        )
    return report


# Which dashboard a person uses for each AI agent, kept per organisation:
# dashboard_preferences = {"by_org": {org: {agent: report_id}}, ...}. The older
# flat {agent: report_id} is read in every organisation until they choose
# there. ("_pinned_apps" lives in the same column; it is left alone.)


def _choices(db: Session, user: User) -> dict:
    prefs = user.dashboard_preferences or {}
    org = _org(db, user)
    by_org = prefs.get("by_org") or {}
    if org and org in by_org:
        return dict(by_org[org])
    return {
        k: v
        for k, v in prefs.items()
        if not k.startswith("_") and k != "by_org" and isinstance(v, str)
    }


def _choose(db: Session, user: User, agent_slug: str, report_id: str | None):
    from sqlalchemy.orm.attributes import flag_modified

    prefs = dict(user.dashboard_preferences or {})
    org = _org(db, user)
    chosen = _choices(db, user)
    if report_id:
        chosen[agent_slug] = report_id
    else:
        chosen.pop(agent_slug, None)
    if org:
        prefs["by_org"] = {**(prefs.get("by_org") or {}), org: chosen}
    else:
        prefs[agent_slug] = report_id
        if not report_id:
            prefs.pop(agent_slug, None)
    user.dashboard_preferences = prefs
    flag_modified(user, "dashboard_preferences")


# History. Every change keeps the dashboard as it then stood, as an App's
# versions are kept, and any version can be brought back. A version is the
# content (layout, period, tiles), never the figures or who it is shared with.


def _snapshot(report: Report) -> dict:
    period = (report.global_filters or {}).get("default_period")
    return {
        "title": report.title,
        "description": report.description,
        "layout": report.layout or [],
        "is_dashboard": report.is_dashboard,
        "agent_slug": report.agent_slug,
        "refresh_interval_seconds": report.refresh_interval_seconds,
        "default_period": period,
        "charts": [
            {
                "id": c.id,
                "title": c.title,
                "chart_type": c.chart_type,
                "chart_spec": c.chart_spec,
                "script": c.script,
                "position": c.position,
            }
            for c in sorted(report.charts, key=lambda c: c.position)
        ],
    }


def _last_version(db: Session, report_id: str):
    from app.db.models import ReportVersion

    return (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc())
        .first()
    )


def _record(db: Session, report: Report, by: str | None, note: str | None = None):
    """Keep the dashboard as it now stands, unless nothing changed. Commits."""
    from app.db.models import ReportVersion

    db.refresh(report)
    snap = _snapshot(report)
    last = _last_version(db, report.id)
    if last is not None and last.snapshot == snap:
        return
    db.add(
        ReportVersion(
            report_id=report.id,
            version=(last.version + 1) if last else 1,
            snapshot=snap,
            note=note,
            created_by=by,
        )
    )
    db.commit()


def _baseline(db: Session, report: Report):
    """A dashboard from before history was kept: record it as it stands, so
    the change about to be made can be undone."""
    if _last_version(db, report.id) is None:
        _record(db, report, report.user_id, "As it was before history was kept")


def _sample_rows(rows: list, n: int) -> list:
    """Pick n rows evenly spaced across the list so the preview covers all groups."""
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def _venue_for_dates(db, venue_id: str | None):
    """The venue whose business calendar applies, or None for the default.

    Returns None rather than borrowing another venue's settings. This used to
    fall back to "the first venue in the table with a day_start_time", which
    silently applied one venue's trading boundary to another — and was
    duplicated at three call sites.
    """
    if not venue_id or venue_id == "__all__":
        return None
    from app.db.models import Venue

    return db.query(Venue).filter(Venue.id == venue_id).first()


def _shared_calendar_venue(db, venue_ids):
    """The one venue whose calendar a fan-out can share, or None.

    Every venue in the fan-out must keep the same timezone and day start;
    otherwise there is no single right calendar and the default applies.
    """
    ids = [v for v in venue_ids or [] if v]
    if not ids:
        return None
    from app.db.models import Venue

    venues = db.query(Venue).filter(Venue.id.in_(ids)).all()
    if not venues:
        return None
    calendars = {
        (getattr(v, "timezone", None), getattr(v, "day_start_time", None))
        for v in venues
    }
    return venues[0] if len(calendars) == 1 else None


def _data_meta(gf: dict, venue_id, user_id: str | None = None) -> dict:
    """What a refresh fetched the rows for, and for whom, kept beside them.

    The page compares this with the tile's venue, period and its own
    refresh interval to decide whether the cache is still good on open.
    ``user_id`` keeps the figures to the person they were fetched for
    (``_chart_to_dict``).
    """
    import datetime as _dt

    return {
        "refreshed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "venue_id": venue_id or "__all__",
        "period": (gf or {}).get("period") or None,
        "user_id": user_id,
    }


def _rows_from_payload(payload, script: dict) -> list:
    """The rows a chart plots, out of one tool result.

    A tool (consolidator) returns a document — ``{"rows": [...], "totals": …,
    "window": …}`` — so a chart names the list it plots in ``script["rows"]``
    (a dotted path: "rows", "jobs", "days"). Without one, the old behaviour
    for a raw endpoint's payload: a list as-is, anything else as one row.
    """
    path = (script or {}).get("rows")
    if path:
        cur = payload
        for part in str(path).split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if isinstance(cur, list):
            return [r for r in cur if isinstance(r, dict)]
        # A KPI reads a document's totals ("summary"): one row, so a fan-out
        # over venues gives one row per venue for the card to add up.
        return [cur] if isinstance(cur, dict) else []
    return payload if isinstance(payload, list) else [payload]


#: The dashboard's period control (DashboardView) → a window on the venue's
#: calendar, and the bucket the charts should use. Resolved here in Python so
#: a refresh never spends a model call on "last 12 months".
_PERIOD_CODES = {
    "today": ("day", 0, 0),
    "yesterday": ("day", -1, -1),
    "last_7_days": ("day", -6, 0),
    "this_week": ("week", 0, 0),
    "last_week": ("week", -1, -1),
    "last_13_weeks": ("week", -12, 0),
    "this_month": ("month", 0, 0),
    "last_month": ("month", -1, -1),
    "last_12_months": ("month", -11, 0),
}

#: The date keys a tool may take instead of start/end (Xero and the budgets
#: take from_date/to_date; the older raw shapes took *_datetime).
_START_KEYS = ("start", "start_datetime", "start_time", "from_date", "from")
_END_KEYS = ("end", "end_datetime", "end_time", "to_date", "to")


def _period_window(code: str, venue, span: int = 0):
    """(start ISO, end ISO, interval) for a period code, or None.

    ``span`` is a trend chart's reach: the window is widened backwards to
    that many buckets ending at the chosen period, so "this month" shows the
    month on the cards and the last twelve months on the trend.
    """
    from app.services import business_calendar as _bc

    spec = _PERIOD_CODES.get(str(code or ""))
    if not spec:
        return None
    kind, first, last = spec
    if span and span > 1:
        first = min(first, last - (span - 1))
    fn = {"day": _bc.trading_day, "week": _bc.trading_week, "month": _bc.trading_month}[
        kind
    ]
    start = fn(venue, None, first).start
    end = fn(venue, None, last).end
    return start.isoformat(), end.isoformat(), kind


def _apply_global_dates(resolved: dict, gf: dict, venue=None) -> dict:
    """A dashboard's date filter onto one chart's params.

    Tools take a plain-English ``period``; a window the viewer picked replaces
    it with start/end, confirmed — the viewer chose those times. Charts still
    carrying explicit date keys get them overwritten, as before. A period
    CODE from the dashboard's period control becomes that window on the
    venue's calendar, and sets the chart's bucket (``interval`` / a month or
    week ``group_by``) to match.
    """
    span = resolved.pop("period_span", None)
    # A chart whose bucket is part of its meaning ("rostered vs worked BY
    # DAY") keeps it whatever period is chosen; the others follow the control.
    fixed_interval = bool(resolved.pop("fixed_interval", False))
    if gf.get("period") and "period" in resolved:
        try:
            span = int(span or 0)
        except (TypeError, ValueError):
            span = 0
        window = _period_window(gf["period"], venue, span)
        if window:
            start, end, kind = window
            resolved.pop("period")
            # The window lands on the tool's OWN date keys when the chart
            # names them (Xero's from_date/to_date); start/end otherwise.
            start_key = next((k for k in _START_KEYS if k in resolved), "start")
            end_key = next((k for k in _END_KEYS if k in resolved), "end")
            resolved[start_key] = start
            resolved[end_key] = end
            resolved["confirmed_by_user"] = True
            if not fixed_interval:
                if "interval" in resolved and resolved["interval"] in (
                    "day",
                    "month",
                    "week",
                ):
                    resolved["interval"] = kind
                if resolved.get("group_by") in ("day", "month", "week"):
                    resolved["group_by"] = kind
            return resolved
    if gf.get("start") and gf.get("end") and "period" in resolved:
        resolved.pop("period")
        resolved["start"] = gf["start"]
        resolved["end"] = gf["end"]
        resolved["confirmed_by_user"] = True
        return resolved
    if gf.get("start"):
        for k in _START_KEYS:
            if k in resolved:
                resolved[k] = gf["start"]
    if gf.get("end"):
        for k in _END_KEYS:
            if k in resolved:
                resolved[k] = gf["end"]
    return resolved


def _require_tool(script: dict | None, config_db: Session) -> None:
    """A saved chart may only call a TOOL (Sep 2026 — the same rule as the
    agent's; docs/tool-architecture-strategy.md). Endpoints are building blocks:
    their payloads are raw, unshaped and change with the API."""
    if not script or not script.get("action"):
        return
    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == script.get("connector"))
        .first()
    )
    if spec is None or spec_rows.find_tool(spec, script["action"]) is None:
        raise HTTPException(
            400,
            f"A chart must call a tool, not an API endpoint: "
            f"{script.get('connector')}.{script['action']} is not a tool.",
        )


_PLACEHOLDER_TOKEN = __import__("re").compile(
    r"\b(today_start|today_end|yesterday_start|yesterday_end|tomorrow_start|"
    r"tomorrow_end|week_start|month_start|12h_ago|now)\b"
)


def _resolve_date_placeholders(params: dict, venue=None) -> dict:
    """Replace placeholder strings with actual timestamps.

    Windows come from ``business_calendar`` — the single definition of a
    trading day — so a dashboard and the agent answer the same question the
    same way. Both the day start and the timezone follow the venue; passing
    None uses the configured defaults.
    """
    import datetime as _dt
    import re as _re

    from app.services import business_calendar as _bc

    tz = _bc.timezone_for(venue)
    # Whole seconds: Loaded's feeds reject a fractional-second instant
    # ("2026-10-18T13:45:26.665135+13:00 is not valid"), which emptied the
    # half-hour sales chart whose end is `now` (18 Oct 2026).
    now = _dt.datetime.now(tz).replace(microsecond=0)

    today = _bc.trading_day(venue, now, 0)
    yesterday = _bc.trading_day(venue, now, -1)
    tomorrow = _bc.trading_day(venue, now, 1)
    week = _bc.trading_week(venue, now, 0)
    month = _bc.trading_month(venue, now, 0)

    placeholders = {
        "today_start": today.start.isoformat(),
        "today_end": today.end.isoformat(),
        "yesterday_start": yesterday.start.isoformat(),
        "yesterday_end": yesterday.end.isoformat(),
        "tomorrow_start": tomorrow.start.isoformat(),
        "tomorrow_end": tomorrow.end.isoformat(),
        "week_start": week.start.isoformat(),
        "month_start": month.start.isoformat(),
        "12h_ago": (now - _dt.timedelta(hours=12)).isoformat(),
        "now": now.isoformat(),
    }

    round_30 = bool(params.get("_round_30"))

    resolved = {}
    for k, v in params.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str):
            # Whole tokens only. A plain substring replace turned the "now" in
            # status "Acknowledged" into a timestamp ("Ack2026-09-30T…ledged"),
            # so the Pending Deliveries chart matched nothing (29 Sep 2026).
            v = _PLACEHOLDER_TOKEN.sub(
                lambda m: placeholders.get(m.group(1), m.group(1)), v
            )
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


def _chart_venue_ids(db: Session, user, connector: str, venue_id: str | None) -> list:
    """The venues a chart reads: the one asked for — if this user may access
    it — or, for "all venues", every connected venue in the user's
    organisation that they may access. It took every connected venue in the
    DATABASE, and trusted whatever venue a filter sent (Oct 2026); then every
    venue in the organisation, so a team member with one venue saw them all
    on a dashboard left on "All venues" (21 Oct 2026)."""
    from app.db.models import Connection, Venue
    from app.services import caller_scope
    from app.services.venue_service import user_can_access_venue

    if venue_id:
        if getattr(user, "role", None) != "admin" and not user_can_access_venue(
            db, user.id, venue_id
        ):
            raise HTTPException(403, "You don't have access to that venue")
        return [venue_id]
    q = db.query(Connection.venue_id).filter(
        Connection.connector_name == connector,
        Connection.enabled == "true",
    )
    with caller_scope.use(caller_scope.for_user(db, user)):
        scoped = caller_scope.venue_query(db)
    if scoped is None:
        raise HTTPException(403, "Norm couldn't tell which organisation you're in")
    # "All venues" means the trading ones: a head office with its own login
    # (is_trading = false, Oct 2026) would otherwise put $0 days into every
    # group tile. Ask for it by venue_id to see it.
    trading = scoped.filter(Venue.is_trading.is_(True)).with_entities(Venue.id)
    q = q.filter((Connection.venue_id.in_(trading)) | Connection.venue_id.is_(None))
    ids = [vc.venue_id for vc in q.all()]
    if getattr(user, "role", None) != "admin":
        from app.services.venue_service import get_user_venues

        theirs = {v.id for v in get_user_venues(db, user.id)}
        ids = [v for v in ids if v is None or v in theirs]
    return ids or [None]


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
        organization_id=_org(db, user),
    )
    db.add(report)
    db.commit()
    _record(db, report, user.id)
    return _report_to_dict(report, user.id)


@router.get("")
async def list_reports(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    own = (
        db.query(Report)
        .filter(_visible_to(db, user), Report.user_id == user.id)
        .order_by(Report.updated_at.desc())
        .all()
    )
    shared = (
        db.query(Report)
        .filter(_visible_to(db, user), Report.user_id != user.id)
        .order_by(Report.updated_at.desc())
        .all()
    )
    return {
        "reports": [_report_to_dict(r, user.id) for r in own],
        "shared_reports": [_report_to_dict(r, user.id) for r in shared],
    }


# ---------------------------------------------------------------------------
# Dashboard templates (must be before /{report_id} catch-all)
# ---------------------------------------------------------------------------


@router.get("/templates")
async def list_templates(
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """List available dashboard templates from config DB."""
    from app.db.config_models import DashboardTemplate

    templates = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.enabled.is_(True))
        .order_by(DashboardTemplate.agent_slug, DashboardTemplate.title)
        .all()
    )
    return {
        "templates": [
            {
                "slug": t.slug,
                "agent_slug": t.agent_slug,
                "title": t.title,
                "description": t.description,
                "chart_count": len(t.charts or []),
            }
            for t in templates
        ]
    }


@router.post("/templates/{slug}/instantiate")
async def instantiate_template(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Create a real dashboard from a template."""
    from app.db.config_models import DashboardTemplate

    tmpl = (
        config_db.query(DashboardTemplate)
        .filter(DashboardTemplate.slug == slug)
        .first()
    )
    if not tmpl:
        raise HTTPException(404, "Template not found")
    return _report_to_dict(_copy_template(db, config_db, tmpl, user), user.id)


def _copy_template(db: Session, config_db: Session, tmpl, user: User) -> Report:
    """The person's own dashboard, copied from a template."""
    # A template may name the period its board opens on (Operations: today;
    # the cost boards: this month). It travels on the dashboard's filters.
    default_period = None
    for chart_def in tmpl.charts or []:
        if isinstance(chart_def, dict) and chart_def.get("_default_period"):
            default_period = chart_def["_default_period"]
    report = Report(
        user_id=user.id,
        title=tmpl.title,
        description=tmpl.description,
        is_dashboard=True,
        agent_slug=tmpl.agent_slug,
        refresh_interval_seconds=300,  # 5 min default
        global_filters={"default_period": default_period} if default_period else None,
        organization_id=_org(db, user),
    )
    db.add(report)
    db.flush()

    from app.services.chart_tools import to_tool_chart

    layout = []
    for chart_def in tmpl.charts or []:
        title = chart_def["title"]
        chart_type = chart_def["chart_type"]
        chart_spec = chart_def.get("chart_spec", {})
        script = chart_def.get("script", {})
        # A template written before Sep 2026 may name a raw endpoint; build the
        # chart on the tool that answers the same question instead.
        try:
            _require_tool(script, config_db)
        except HTTPException:
            mapped = to_tool_chart(title, chart_type, script, chart_spec)
            if isinstance(mapped, tuple):
                title, chart_type, script, chart_spec, _note = mapped
        chart = ReportChart(
            report_id=report.id,
            title=title,
            chart_type=chart_type,
            chart_spec=chart_spec,
            data=chart_def.get("data", []),
            script=script,
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
    _record(db, report, user.id, f"Copied from the {tmpl.title} template")
    return report


@router.get("/connector-list")
async def list_available_connectors(
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """List all connector names that have specs configured."""
    from app.db.config_models import ConnectionSpec

    specs = config_db.query(ConnectionSpec.connector_name).all()
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


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------
# The dashboard routes sit ABOVE the /{report_id} catch-all: declared after
# it, GET /reports/dashboards was read as a report id and 404'd (Oct 2026).


@router.get("/dashboards")
async def list_dashboards(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all dashboards visible to the current user."""
    dashboards = (
        db.query(Report)
        .filter(Report.is_dashboard.is_(True), _visible_to(db, user))
        .order_by(Report.agent_slug, Report.updated_at.desc())
        .all()
    )
    return {"dashboards": [_report_to_dict(d, user.id) for d in dashboards]}


def _dashboard_in_use(db: Session, user: User, agent_slug: str) -> Report | None:
    """The dashboard the person sees for an AI agent, short of copying a
    template for them: their choice, else their newest own, else the newest
    shared in their organisation."""
    # 1. The person's choice in this organisation
    pref_id = _choices(db, user).get(agent_slug)
    if pref_id:
        # A choice that is no longer visible (unshared, another
        # organisation's) falls through to the defaults below.
        dashboard = (
            db.query(Report).filter(Report.id == pref_id, _visible_to(db, user)).first()
        )
        if dashboard:
            return dashboard

    # 2. Fall back to their own dashboard in this organisation
    dashboard = (
        db.query(Report)
        .filter(
            Report.is_dashboard.is_(True),
            Report.agent_slug == agent_slug,
            Report.user_id == user.id,
            _visible_to(db, user),
        )
        .order_by(Report.updated_at.desc())
        .first()
    )
    if not dashboard:
        # 3. Fall back to a dashboard shared within their organisations
        dashboard = (
            db.query(Report)
            .filter(
                Report.is_dashboard.is_(True),
                Report.agent_slug == agent_slug,
                Report.is_published.is_(True),
                _visible_to(db, user),
            )
            .order_by(Report.updated_at.desc())
            .first()
        )
    return dashboard


@router.get("/dashboards/{agent_slug}")
async def get_dashboard_for_agent(
    agent_slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Get the active dashboard for a specific agent."""
    dashboard = _dashboard_in_use(db, user, agent_slug)
    if not dashboard:
        # 4. The AI agent's one template, copied for this person. Until 21 Oct
        # 2026 step 3 lent them the newest dashboard ANYONE had published, in
        # any organisation: six people outside Cook Brothers were looking at
        # the platform admin's. With two templates to choose from, they pick.
        from app.db.config_models import DashboardTemplate

        templates = (
            config_db.query(DashboardTemplate)
            .filter(
                DashboardTemplate.agent_slug == agent_slug,
                DashboardTemplate.enabled.is_(True),
            )
            .all()
        )
        if len(templates) == 1:
            dashboard = _copy_template(db, config_db, templates[0], user)
    if not dashboard:
        return {"dashboard": None}
    return {"dashboard": _report_to_dict(dashboard, user.id)}


@router.get("/dashboards/{agent_slug}/available")
async def get_available_dashboards(
    agent_slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """List all dashboards available for an agent: templates, own, shared."""
    from app.db.config_models import DashboardTemplate

    # Templates from config DB
    templates = (
        config_db.query(DashboardTemplate)
        .filter(
            DashboardTemplate.agent_slug == agent_slug,
            DashboardTemplate.enabled.is_(True),
        )
        .order_by(DashboardTemplate.title)
        .all()
    )

    # Their own dashboards in this organisation
    own = (
        db.query(Report)
        .filter(
            Report.is_dashboard.is_(True),
            Report.agent_slug == agent_slug,
            Report.user_id == user.id,
            _visible_to(db, user),
        )
        .order_by(Report.updated_at.desc())
        .all()
    )

    # Dashboards shared by people in the same organisations
    shared = (
        db.query(Report)
        .filter(
            Report.is_dashboard.is_(True),
            Report.agent_slug == agent_slug,
            _visible_to(db, user),
            Report.user_id != user.id,
        )
        .order_by(Report.updated_at.desc())
        .all()
    )

    return {
        "active_id": _choices(db, user).get(agent_slug),
        "templates": [
            {
                "slug": t.slug,
                "title": t.title,
                "description": t.description,
                "chart_count": len(t.charts or []),
            }
            for t in templates
        ],
        "own": [_report_to_dict(r, user.id) for r in own],
        "shared": [_report_to_dict(r, user.id) for r in shared],
    }


class SetActiveDashboardBody(BaseModel):
    dashboard_id: str


@router.post("/dashboards/{agent_slug}/set-active")
async def set_active_dashboard(
    agent_slug: str,
    body: SetActiveDashboardBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set or clear the user's active dashboard for an agent."""
    if not body.dashboard_id:
        _choose(db, user, agent_slug, None)
        db.commit()
        return {"dashboard": None}

    dashboard = (
        db.query(Report)
        .filter(
            Report.id == body.dashboard_id,
            Report.is_dashboard.is_(True),
            _visible_to(db, user),
        )
        .first()
    )
    if not dashboard:
        raise HTTPException(404, "Dashboard not found")

    _choose(db, user, agent_slug, body.dashboard_id)
    db.commit()
    return {"dashboard": _report_to_dict(dashboard, user.id)}


class TestChartBody(BaseModel):
    venue_id: str | None = None
    param_overrides: dict | None = None


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _report_to_dict(_report_for_viewer(db, report_id, user), user.id)


@router.patch("/{report_id}")
async def update_report(
    report_id: str,
    body: UpdateReportBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = _report_for_owner(db, report_id, user)
    _baseline(db, report)
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
    _record(db, report, user.id)
    return _report_to_dict(report, user.id)


@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = _report_for_owner(db, report_id, user)
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
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    report = _report_for_owner(db, report_id, user)
    _require_tool(body.script, config_db)
    _baseline(db, report)

    position = len(report.charts)
    chart = ReportChart(
        report_id=report_id,
        title=body.title,
        chart_type=body.chart_type,
        chart_spec=body.chart_spec,
        data=body.data,
        # The rows came from the person's own conversation: theirs to see,
        # and with no refresh time the page still re-reads them on open.
        data_meta={"user_id": user.id} if body.data else None,
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
    _record(db, report, user.id)
    return _report_to_dict(report, user.id)


@router.patch("/{report_id}/charts/{chart_id}")
async def update_chart(
    report_id: str,
    chart_id: str,
    body: UpdateChartBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    report = _report_for_owner(db, report_id, user)
    if body.script is not None:
        _require_tool(body.script, config_db)
    _baseline(db, report)
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
    _record(db, report, user.id)
    return _chart_to_dict(chart, user.id)


@router.delete("/{report_id}/charts/{chart_id}")
async def remove_chart(
    report_id: str,
    chart_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    report = _report_for_owner(db, report_id, user)
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
    _baseline(db, report)

    # Remove from grid layout
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
    _record(db, report, user.id)
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
    report = _report_for_viewer(db, report_id, user)

    from app.connectors.tool_executor import execute_connector_tool

    errors = []
    gf = (body.global_filters if body else None) or {}

    # The venue whose business calendar applies to these date placeholders.
    gf_venue = gf.get("venue_id")
    date_venue = _venue_for_dates(
        db, gf_venue if gf_venue and gf_venue != "__all__" else report.venue_id
    )

    def _resolve_params(params: dict) -> dict:
        """Resolve date placeholders and merge global date filters."""
        resolved = _resolve_date_placeholders(params, venue=date_venue)
        return _apply_global_dates(resolved, gf, venue=date_venue)

    debug_info: list[dict] = []

    for chart in report.charts:
        script = chart.script
        if not script or not script.get("connector") or not script.get("action"):
            continue
        refusal = _tool_refusal(db, config_db, user, script)
        if refusal:
            errors.append(
                {"chart_id": chart.id, "title": chart.title, "error": refusal}
            )
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
            venue_ids = _chart_venue_ids(db, user, script["connector"], venue_id)

            aggregated_rows: list[dict] = []
            chart_debug: dict = {
                "chart_id": chart.id,
                "title": chart.title,
                "action": script["action"],
                "params_resolved": resolved,
                "venues_queried": len(venue_ids),
            }
            any_success = False

            # Run venue calls in parallel for faster multi-venue refresh
            from concurrent.futures import ThreadPoolExecutor

            def _run_venue(vid):
                from app.db.engine import SessionLocal, _ConfigSessionLocal

                _db = SessionLocal()
                _cdb = _ConfigSessionLocal()
                try:
                    # A worker thread starts with no scope: the viewer is set
                    # again here, so a tool row's `requires` sees them.
                    with caller_scope.use(caller_scope.for_user(_db, user)):
                        return vid, execute_connector_tool(
                            connector_name=script["connector"],
                            action=script["action"],
                            params=resolved,
                            db=_db,
                            config_db=_cdb,
                            venue_id=vid,
                            strict_venue=True,
                            user=user,
                        )
                finally:
                    _db.close()
                    _cdb.close()

            if len(venue_ids) > 1:
                with ThreadPoolExecutor(max_workers=min(len(venue_ids), 4)) as pool:
                    venue_results_list = list(pool.map(_run_venue, venue_ids))
            else:
                with caller_scope.use(caller_scope.for_user(db, user)):
                    venue_results_list = [
                        (
                            venue_ids[0],
                            execute_connector_tool(
                                connector_name=script["connector"],
                                action=script["action"],
                                params=resolved,
                                db=db,
                                config_db=config_db,
                                venue_id=venue_ids[0],
                                strict_venue=True,
                                user=user,
                            ),
                        )
                    ]

            for vid, tool_result in venue_results_list:
                if tool_result.rendered_request:
                    chart_debug["url"] = tool_result.rendered_request.get("url")
                    chart_debug["method"] = tool_result.rendered_request.get("method")

                if tool_result.success and tool_result.payload:
                    any_success = True
                    rows = _rows_from_payload(tool_result.payload, script)
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
                chart.data_meta = _data_meta(gf, venue_id, user.id)
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(chart, "data")
                flag_modified(chart, "data_meta")
        except Exception as exc:
            errors.append(
                {"chart_id": chart.id, "title": chart.title, "error": str(exc)}
            )

    db.commit()
    db.refresh(report)
    result = _report_to_dict(report, user.id)
    if errors:
        result["refresh_errors"] = errors
    if debug_info:
        result["refresh_debug"] = debug_info
    return result


def run_tile(
    db: Session,
    config_db: Session,
    user: User,
    report: Report | None,
    script: dict,
    gf: dict | None,
) -> tuple[list[dict], list[str], str | None]:
    """Run one tile's tool as the page would: for the venue (or every venue
    the person may see), on the dashboard's period. Returns the rows, why any
    venue gave nothing, and the venue the rows are for (None: all of them).
    Used by a tile's refresh and by Norm trying a tile before it saves it."""
    from app.connectors.tool_executor import execute_connector_tool

    refusal = _tool_refusal(db, config_db, user, script)
    if refusal:
        return [], [refusal], None
    gf = gf or {}
    # The venue whose business calendar applies to these date placeholders.
    gf_venue = gf.get("venue_id")
    venue_id_for_day = (
        gf_venue
        if gf_venue and gf_venue != "__all__"
        else (report.venue_id if report else None)
    )
    date_venue = _venue_for_dates(db, venue_id_for_day)

    # Resolve venue
    if gf_venue == "__all__":
        venue_id = None
    elif gf_venue:
        venue_id = gf_venue
    else:
        venue_id = script.get("venue_id") or (report.venue_id if report else None)

    venue_ids = _chart_venue_ids(db, user, script["connector"], venue_id)
    if date_venue is None:
        # "All venues" on one calendar: when every venue in the fan-out keeps
        # the same timezone and day start, the period is theirs, not the
        # configured default's (a Sydney group was getting Auckland months).
        date_venue = _shared_calendar_venue(db, venue_ids)

    raw_params = script.get("params", {})
    resolved = _apply_global_dates(
        _resolve_date_placeholders(raw_params, venue=date_venue), gf, venue=date_venue
    )

    # Execute venues sequentially — charts are already parallel at the request level
    # so we don't need nested ThreadPoolExecutors that exhaust DB connections
    aggregated_rows: list[dict] = []
    # Why a tile is empty. A tool that failed, or answered with its own
    # {"error": …} document, used to leave the chart on its old data and the
    # page saying "no rows — check the chart's settings" (16 Oct 2026: every
    # purchase-based tile, because the nested sales read had no venue).
    errors: list[str] = []

    for vid in venue_ids:
        # The viewer is the person acting (a tool row's `requires`, Oct 2026).
        with caller_scope.use(caller_scope.for_user(db, user)):
            tool_result = execute_connector_tool(
                connector_name=script["connector"],
                action=script["action"],
                params=resolved,
                db=db,
                config_db=config_db,
                venue_id=vid,
                strict_venue=True,
                user=user,
            )

        payload = tool_result.payload
        if (
            isinstance(payload, dict)
            and payload.get("error")
            and not payload.get("rows")
        ):
            errors.append(str(payload["error"]))
            continue
        if not tool_result.success:
            errors.append(str(tool_result.error or "tool failed"))
            continue
        if payload:
            rows = _rows_from_payload(payload, script)
            if not venue_id and len(venue_ids) > 1:
                from app.db.models import Venue

                venue_obj = (
                    db.query(Venue).filter(Venue.id == vid).first() if vid else None
                )
                venue_name = venue_obj.name if venue_obj else (vid or "unknown")
                for row in rows:
                    if isinstance(row, dict):
                        row["venue"] = venue_name
            aggregated_rows.extend(rows)
    return aggregated_rows, errors, venue_id


@router.post("/{report_id}/charts/{chart_id}/refresh")
def refresh_single_chart(
    report_id: str,
    chart_id: str,
    body: RefreshBody | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Refresh a single chart and return its updated data."""
    _report_for_viewer(db, report_id, user)
    chart = (
        db.query(ReportChart)
        .filter(ReportChart.id == chart_id, ReportChart.report_id == report_id)
        .first()
    )
    if not chart:
        raise HTTPException(404, "Chart not found")

    report = db.query(Report).filter(Report.id == report_id).first()
    script = chart.script
    if not script or not script.get("connector") or not script.get("action"):
        return {"chart": _chart_to_dict(chart, user.id)}

    gf = (body.global_filters if body else None) or {}
    rows, errors, venue_id = run_tile(db, config_db, user, report, script, gf)

    if rows:
        chart.data = rows
        chart.data_meta = _data_meta(gf, venue_id, user.id)
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(chart, "data")
        flag_modified(chart, "data_meta")
        db.commit()
        db.refresh(chart)

    out = {"chart": _chart_to_dict(chart, user.id)}
    if errors and not rows:
        out["error"] = "; ".join(dict.fromkeys(errors))
    return out


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

    _report_for_viewer(db, report_id, user)
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
    refusal = _tool_refusal(db, config_db, user, script)
    if refusal:
        raise HTTPException(403, refusal)

    # Get tool metadata (accepted params, field descriptions)
    tool_info = get_tool_info(script["connector"], script["action"], config_db)
    if tool_info.get("error"):
        return {"error": tool_info["error"], "script": script, **tool_info}

    # Resolve params (placeholder substitution) — use business-day start if available
    raw_params = dict(script.get("params", {}))
    if body and body.param_overrides:
        raw_params.update(body.param_overrides)

    venue_id = (body.venue_id if body else None) or script.get("venue_id")
    resolved = _resolve_date_placeholders(
        raw_params, venue=_venue_for_dates(db, venue_id)
    )

    # Determine venues to test against
    venue_ids = _chart_venue_ids(db, user, script["connector"], venue_id)

    # Execute per venue and aggregate
    venue_results: list[dict] = []
    all_rows: list = []
    any_success = False
    last_rendered = None
    all_logs: list[str] = []

    for vid in venue_ids:
        with caller_scope.use(caller_scope.for_user(db, user)):
            tool_result = execute_connector_tool(
                connector_name=script["connector"],
                action=script["action"],
                params=resolved,
                db=db,
                config_db=config_db,
                venue_id=vid,
                strict_venue=True,
                user=user,
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
        if tool_result.logs:
            all_logs.extend(tool_result.logs)
        venue_results.append(vr)

        if tool_result.success and tool_result.payload:
            any_success = True
            rows = _rows_from_payload(tool_result.payload, script)
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
        "logs": all_logs if all_logs else None,
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
    report = _report_for_owner(db, report_id, user)
    _baseline(db, report)
    report.is_dashboard = True
    if body and body.agent_slug:
        report.agent_slug = body.agent_slug
    db.commit()
    _record(db, report, user.id)
    return _report_to_dict(report, user.id)


# ---------------------------------------------------------------------------
# Copies and history
# ---------------------------------------------------------------------------


class CopyReportBody(BaseModel):
    # Also make the copy the person's dashboard for its AI agent.
    make_active: bool = False


def _copy_report(db: Session, src: Report, user: User, make_active: bool) -> Report:
    """The person's own copy of ``src``, in the organisation they are working
    in, unshared; optionally their dashboard for its AI agent."""
    import copy as _copy

    mine = Report(
        user_id=user.id,
        organization_id=_org(db, user),
        venue_id=src.venue_id,
        title=src.title,
        description=src.description,
        status="saved",
        is_dashboard=src.is_dashboard,
        agent_slug=src.agent_slug,
        refresh_interval_seconds=src.refresh_interval_seconds,
        global_filters=_copy.deepcopy(src.global_filters),
    )
    db.add(mine)
    db.flush()
    new_ids = {}
    for c in src.charts:
        has_tool = bool((c.script or {}).get("action"))
        chart = ReportChart(
            report_id=mine.id,
            title=c.title,
            chart_type=c.chart_type,
            chart_spec=_copy.deepcopy(c.chart_spec),
            script=_copy.deepcopy(c.script),
            # Figures are the loader's (_chart_to_dict); a text tile's rows are
            # its content and come along.
            data=[] if has_tool else _copy.deepcopy(c.data),
            position=c.position,
        )
        db.add(chart)
        db.flush()
        new_ids[c.id] = chart.id
    mine.layout = [
        {**item, "chart_id": new_ids[item["chart_id"]]}
        for item in (src.layout or [])
        if isinstance(item, dict) and item.get("chart_id") in new_ids
    ]
    db.commit()
    _record(db, mine, user.id, f"Copied from {src.title}")
    if make_active and mine.is_dashboard and mine.agent_slug:
        _choose(db, user, mine.agent_slug, mine.id)
        db.commit()
    return mine


@router.post("/{report_id}/copy")
async def copy_report(
    report_id: str,
    body: CopyReportBody | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The person's own copy of a dashboard they can see, in the organisation
    they are working in. A shared dashboard is changed only by the person who
    shared it; everyone else copies it and changes their copy."""
    src = _report_for_viewer(db, report_id, user)
    mine = _copy_report(db, src, user, bool(body and body.make_active))
    return _report_to_dict(mine, user.id)


@router.get("/{report_id}/versions")
async def list_versions(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every version of the owner's dashboard, newest first."""
    from app.db.models import ReportVersion

    _report_for_owner(db, report_id, user)
    rows = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report_id)
        .order_by(ReportVersion.version.desc())
        .all()
    )
    authors = {r.created_by for r in rows if r.created_by}
    names = {
        u.id: u.full_name or u.email
        for u in db.query(User).filter(User.id.in_(authors)).all()
    }
    return {
        "versions": [
            {
                "version": r.version,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "by": names.get(r.created_by),
                "note": r.note,
                "title": (r.snapshot or {}).get("title"),
                "tiles": len((r.snapshot or {}).get("charts") or []),
                "current": i == 0,
            }
            for i, r in enumerate(rows)
        ]
    }


def _restore(db: Session, report: Report, version: int, user: User) -> Report:
    """Bring back ``version`` of the owner's ``report``, saved as the newest."""
    import copy as _copy

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.models import ReportVersion

    row = (
        db.query(ReportVersion)
        .filter(ReportVersion.report_id == report.id, ReportVersion.version == version)
        .first()
    )
    if not row:
        raise HTTPException(404, "No such version")
    _baseline(db, report)
    snap = _copy.deepcopy(row.snapshot)
    report.title = snap.get("title") or report.title
    report.description = snap.get("description")
    report.layout = snap.get("layout") or []
    report.is_dashboard = bool(snap.get("is_dashboard"))
    report.agent_slug = snap.get("agent_slug")
    report.refresh_interval_seconds = snap.get("refresh_interval_seconds")
    filters = _copy.deepcopy(report.global_filters or {})
    filters.pop("default_period", None)
    if snap.get("default_period"):
        filters["default_period"] = snap["default_period"]
    report.global_filters = filters or None
    flag_modified(report, "layout")
    flag_modified(report, "global_filters")

    wanted = {c["id"]: c for c in snap.get("charts") or []}
    for chart in [c for c in report.charts if c.id not in wanted]:
        report.charts.remove(chart)
    current = {c.id: c for c in report.charts}
    for chart_id, c in wanted.items():
        chart = current.get(chart_id)
        if chart is None:
            chart = ReportChart(id=chart_id, report_id=report.id, data=[])
            db.add(chart)
        elif chart.script != c.get("script"):
            chart.data, chart.data_meta = [], None
        chart.title = c.get("title") or ""
        chart.chart_type = c.get("chart_type") or "bar"
        chart.chart_spec = c.get("chart_spec") or {}
        chart.script = c.get("script") or {}
        chart.position = c.get("position") or 0
        for field in ("chart_spec", "script", "data"):
            flag_modified(chart, field)
    db.commit()
    _record(db, report, user.id, f"Brought back version {version}")
    return report


@router.post("/{report_id}/versions/{version}/restore")
async def restore_version(
    report_id: str,
    version: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bring back an earlier version. It is saved as the newest version, so
    nothing in between is lost and this too can be undone."""
    report = _report_for_owner(db, report_id, user)
    return _report_to_dict(_restore(db, report, version, user), user.id)
