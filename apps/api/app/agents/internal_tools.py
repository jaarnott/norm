"""Registry of internal tool handlers.

Internal tools execute against the local database instead of external APIs.
Each handler receives (input_params, db, thread_id) and returns a result dict
compatible with the standard tool result format:
    {"success": bool, "data": ..., "error": str | None}
"""

import html
import logging
import uuid
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

InternalHandler = Callable[[dict, Session, str | None], dict]  # (params, db, thread_id)

_REGISTRY: dict[tuple[str, str], InternalHandler] = {}


def register(connector_name: str, action: str):
    """Decorator to register an internal tool handler."""

    def decorator(fn: InternalHandler) -> InternalHandler:
        _REGISTRY[(connector_name, action)] = fn
        logger.info("Registered internal handler: %s.%s", connector_name, action)
        return fn

    return decorator


def get_handler(connector_name: str, action: str) -> InternalHandler | None:
    """Look up an internal handler. Returns None if not found."""
    return _REGISTRY.get((connector_name, action))


# ---------------------------------------------------------------------------
# HR Criteria Handlers
# ---------------------------------------------------------------------------


@register("norm", "get_criteria")
def _get_criteria(params: dict, db: Session, thread_id: str | None) -> dict:
    """Return hiring criteria filtered by scope and/or position."""
    from app.db.models import HiringCriteria

    scope = params.get("scope", "all")
    position_name = params.get("position_name")

    query = db.query(HiringCriteria)
    if scope == "company":
        query = query.filter(HiringCriteria.scope == "company")
    elif scope == "position":
        query = query.filter(HiringCriteria.scope == "position")
        if position_name:
            query = query.filter(HiringCriteria.position_name == position_name)

    rows = query.all()

    result: dict = {"company": [], "positions": {}}
    for row in rows:
        if row.scope == "company":
            result["company"] = row.criteria or []
        else:
            result["positions"][row.position_name or "unknown"] = row.criteria or []

    return {"success": True, "data": result}


@register("norm_hr", "save_criteria")
def _save_criteria(params: dict, db: Session, thread_id: str | None) -> dict:
    """Create or update hiring criteria for a scope."""
    from app.db.models import HiringCriteria

    scope = params.get("scope")
    if not scope:
        return {"success": False, "data": {}, "error": "scope is required"}

    position_name = params.get("position_name")
    criteria = params.get("criteria", [])

    # Ensure each criterion has an id
    for c in criteria:
        if not c.get("id"):
            c["id"] = str(uuid.uuid4())[:8]

    # Find existing row
    query = db.query(HiringCriteria).filter(HiringCriteria.scope == scope)
    if scope == "position":
        if not position_name:
            return {
                "success": False,
                "data": {},
                "error": "position_name is required for position scope",
            }
        query = query.filter(HiringCriteria.position_name == position_name)
    else:
        query = query.filter(HiringCriteria.position_name.is_(None))

    existing = query.first()

    if existing:
        existing.criteria = criteria
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(existing, "criteria")
        db.flush()
        row = existing
    else:
        row = HiringCriteria(
            scope=scope,
            position_name=position_name if scope == "position" else None,
            criteria=criteria,
        )
        db.add(row)
        db.flush()

    return {
        "success": True,
        "data": {
            "id": row.id,
            "scope": row.scope,
            "position_name": row.position_name,
            "criteria": row.criteria,
        },
    }


# ---------------------------------------------------------------------------
# Hiring — Jobs, Candidates, Applications
# ---------------------------------------------------------------------------


def _job_to_dict(job, include_applications: bool = False) -> dict:
    d: dict = {
        "id": job.id,
        "title": job.title,
        "department": job.department,
        "status": job.status,
        "description": job.description,
        "criteria_id": job.criteria_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "candidate_count": len([a for a in job.applications if a.status != "rejected"])
        if job.applications
        else 0,
    }
    if include_applications:
        d["applications"] = [
            {
                "id": a.id,
                "candidate_id": a.candidate_id,
                "candidate_name": a.candidate.name if a.candidate else "",
                "candidate_email": a.candidate.email if a.candidate else "",
                "candidate_source": a.candidate.source if a.candidate else "",
                "status": a.status,
                "score": a.score,
                "notes": a.notes,
                "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            }
            for a in sorted(job.applications, key=lambda x: x.applied_at or x.id)
        ]
    return d


@register("norm_hr", "get_jobs")
def _get_jobs(params: dict, db: Session, thread_id: str | None) -> dict:
    """List all jobs with optional status filter."""
    from app.db.models import Job

    q = db.query(Job)
    status = params.get("status")
    if status:
        q = q.filter(Job.status == status)
    jobs = q.order_by(Job.created_at.desc()).all()
    return {"success": True, "data": {"jobs": [_job_to_dict(j) for j in jobs]}}


@register("norm_hr", "get_job")
def _get_job(params: dict, db: Session, thread_id: str | None) -> dict:
    """Get a single job with its applications and candidate info."""
    from app.db.models import Job

    job_id = params.get("job_id")
    if not job_id:
        return {"success": False, "data": {}, "error": "job_id is required"}
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return {"success": False, "data": {}, "error": f"Job not found: {job_id}"}
    return {"success": True, "data": _job_to_dict(job, include_applications=True)}


@register("norm_hr", "create_job")
def _create_job(params: dict, db: Session, thread_id: str | None) -> dict:
    """Create a new job position."""
    from app.db.models import Job

    title = params.get("title")
    if not title:
        return {"success": False, "data": {}, "error": "title is required"}
    job = Job(
        title=title,
        department=params.get("department"),
        status=params.get("status", "open"),
        description=params.get("description"),
        criteria_id=params.get("criteria_id"),
    )
    db.add(job)
    db.flush()
    return {"success": True, "data": _job_to_dict(job)}


@register("norm_hr", "update_job")
def _update_job(params: dict, db: Session, thread_id: str | None) -> dict:
    """Update a job's fields."""
    from app.db.models import Job

    job_id = params.get("job_id")
    if not job_id:
        return {"success": False, "data": {}, "error": "job_id is required"}
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return {"success": False, "data": {}, "error": f"Job not found: {job_id}"}
    for field in ("title", "department", "status", "description", "criteria_id"):
        if field in params:
            setattr(job, field, params[field])
    db.flush()
    return {"success": True, "data": _job_to_dict(job)}


@register("norm_hr", "get_candidate")
def _get_candidate(params: dict, db: Session, thread_id: str | None) -> dict:
    """Get a candidate with their application details."""
    from app.db.models import Candidate

    candidate_id = params.get("candidate_id")
    if not candidate_id:
        return {"success": False, "data": {}, "error": "candidate_id is required"}
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        return {
            "success": False,
            "data": {},
            "error": f"Candidate not found: {candidate_id}",
        }

    apps = candidate.applications
    job_id = params.get("job_id")
    if job_id:
        apps = [a for a in apps if a.job_id == job_id]

    return {
        "success": True,
        "data": {
            "id": candidate.id,
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
            "source": candidate.source,
            "notes": candidate.notes,
            "applications": [
                {
                    "id": a.id,
                    "job_id": a.job_id,
                    "job_title": a.job.title if a.job else "",
                    "status": a.status,
                    "score": a.score,
                    "notes": a.notes,
                    "applied_at": a.applied_at.isoformat() if a.applied_at else None,
                }
                for a in apps
            ],
        },
    }


@register("norm_hr", "create_candidate")
def _create_candidate(params: dict, db: Session, thread_id: str | None) -> dict:
    """Add a candidate to a job."""
    from app.db.models import Candidate, Application

    job_id = params.get("job_id")
    name = params.get("name")
    if not job_id or not name:
        return {"success": False, "data": {}, "error": "job_id and name are required"}

    # Find existing candidate by email or create new
    email = params.get("email")
    candidate = None
    if email:
        candidate = db.query(Candidate).filter(Candidate.email == email).first()
    if not candidate:
        candidate = Candidate(
            name=name,
            email=email,
            phone=params.get("phone"),
            source=params.get("source"),
            notes=params.get("notes"),
        )
        db.add(candidate)
        db.flush()

    app = Application(
        job_id=job_id,
        candidate_id=candidate.id,
        status=params.get("status", "applied"),
    )
    db.add(app)
    db.flush()

    return {
        "success": True,
        "data": {
            "candidate_id": candidate.id,
            "candidate_name": candidate.name,
            "application_id": app.id,
            "status": app.status,
        },
    }


@register("norm_hr", "update_application")
def _update_application(params: dict, db: Session, thread_id: str | None) -> dict:
    """Update an application's status, score, or notes."""
    from app.db.models import Application

    app_id = params.get("application_id")
    if not app_id:
        return {"success": False, "data": {}, "error": "application_id is required"}
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        return {
            "success": False,
            "data": {},
            "error": f"Application not found: {app_id}",
        }
    for field in ("status", "score", "notes"):
        if field in params:
            setattr(app, field, params[field])
    db.flush()
    return {
        "success": True,
        "data": {
            "id": app.id,
            "job_id": app.job_id,
            "candidate_id": app.candidate_id,
            "status": app.status,
            "score": app.score,
            "notes": app.notes,
        },
    }


# ---------------------------------------------------------------------------
# BambooHR — File Access (hybrid: internal handler calling external API)
# ---------------------------------------------------------------------------


@register("bamboohr", "get_applicant_resume")
def _get_applicant_resume(params: dict, db: Session, thread_id: str | None) -> dict:
    """Fetch an applicant's resume from BambooHR and return as a document block for the LLM."""
    import base64
    import httpx
    from app.db.models import ConnectorConfig

    file_id = params.get("file_id") or params.get("resume_file_id")
    if not file_id:
        return {"success": False, "data": {}, "error": "file_id is required"}

    config = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.connector_name == "bamboohr")
        .first()
    )
    if not config:
        return {
            "success": False,
            "data": {},
            "error": "BambooHR connector not configured",
        }

    subdomain = config.config.get("subdomain", "")
    api_key = config.config.get("api_key", "")
    url = f"https://{subdomain}.bamboohr.com/api/gateway.php/{subdomain}/v1/files/{file_id}"

    try:
        resp = httpx.get(url, auth=(api_key, "x"), timeout=30.0)
    except httpx.HTTPError as exc:
        return {"success": False, "data": {}, "error": f"Failed to fetch file: {exc}"}

    if resp.status_code != 200:
        return {
            "success": False,
            "data": {},
            "error": f"BambooHR returned {resp.status_code}",
        }

    content_type = (
        resp.headers.get("content-type", "application/pdf").split(";")[0].strip()
    )
    # Parse filename from Content-Disposition header
    cd = resp.headers.get("content-disposition", "")
    filename = "resume.pdf"
    if "filename=" in cd:
        filename = cd.split("filename=")[-1].strip().strip('"')

    b64 = base64.b64encode(resp.content).decode()

    return {
        "success": True,
        "data": {
            "filename": filename,
            "size_bytes": len(resp.content),
            "content_type": content_type,
        },
        "_document": {
            "type": "document",
            "source": {"type": "base64", "media_type": content_type, "data": b64},
        },
    }


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Email — Send on behalf + system notifications
# ---------------------------------------------------------------------------


@register("gmail", "send_email")
def _gmail_send_email(params: dict, db: Session, thread_id: str | None) -> dict:
    """Send an email from the user's connected Gmail account."""
    from app.db.models import Thread
    from app.services.email_service import send_on_behalf_gmail

    to = params.get("to", "")
    if isinstance(to, str):
        to = [addr.strip() for addr in to.split(",") if addr.strip()]
    subject = html.unescape(params.get("subject", ""))
    body_html = params.get("body_html", "")
    cc = params.get("cc")
    if isinstance(cc, str):
        cc = [addr.strip() for addr in cc.split(",") if addr.strip()] if cc else None
    bcc = params.get("bcc")
    if isinstance(bcc, str):
        bcc = [addr.strip() for addr in bcc.split(",") if addr.strip()] if bcc else None

    if not to or not subject:
        return {"success": False, "data": {}, "error": "to and subject are required"}

    # Resolve user_id from the task
    user_id = None
    if thread_id:
        task = db.query(Thread).filter(Thread.id == thread_id).first()
        if task:
            user_id = task.user_id
    if not user_id:
        return {
            "success": False,
            "data": {},
            "error": "Cannot determine user for email sending",
        }

    return send_on_behalf_gmail(
        user_id, to, subject, body_html, db, cc=cc, bcc=bcc, thread_id=thread_id
    )


@register("microsoft_outlook", "send_email")
def _outlook_send_email(params: dict, db: Session, thread_id: str | None) -> dict:
    """Send an email from the user's connected Outlook account."""
    from app.db.models import Thread
    from app.services.email_service import send_on_behalf_outlook

    to = params.get("to", "")
    if isinstance(to, str):
        to = [addr.strip() for addr in to.split(",") if addr.strip()]
    subject = html.unescape(params.get("subject", ""))
    body_html = params.get("body_html", "")
    cc = params.get("cc")
    if isinstance(cc, str):
        cc = [addr.strip() for addr in cc.split(",") if addr.strip()] if cc else None
    bcc = params.get("bcc")
    if isinstance(bcc, str):
        bcc = [addr.strip() for addr in bcc.split(",") if addr.strip()] if bcc else None

    if not to or not subject:
        return {"success": False, "data": {}, "error": "to and subject are required"}

    user_id = None
    if thread_id:
        task = db.query(Thread).filter(Thread.id == thread_id).first()
        if task:
            user_id = task.user_id
    if not user_id:
        return {
            "success": False,
            "data": {},
            "error": "Cannot determine user for email sending",
        }

    return send_on_behalf_outlook(
        user_id, to, subject, body_html, db, cc=cc, bcc=bcc, thread_id=thread_id
    )


@register("norm_email", "send_report_email")
def _send_report_email(params: dict, db: Session, thread_id: str | None) -> dict:
    """Send a formatted report email with the agent's response content."""
    from app.services.email_service import send_system_email
    from app.services.email_content_builder import build_report_html
    from app.db.models import Message, EmailLog
    from app.config import settings

    to = params.get("to", "")
    if isinstance(to, str):
        to = [addr.strip() for addr in to.split(",") if addr.strip()]
    subject = params.get("subject", "Report from Norm")

    if not to:
        return {"success": False, "data": {}, "error": "to is required"}

    # Get content: either from content_markdown param or from last assistant message
    content_markdown = params.get("content_markdown")
    display_blocks = None

    if not content_markdown and thread_id:
        last_msg = (
            db.query(Message)
            .filter(Message.thread_id == thread_id, Message.role == "assistant")
            .order_by(Message.created_at.desc())
            .first()
        )
        if last_msg:
            content_markdown = last_msg.content
            display_blocks = last_msg.display_blocks

    if not content_markdown:
        return {
            "success": False,
            "data": {},
            "error": "No content to send — provide content_markdown or ensure thread has an assistant message",
        }

    # Build HTML from markdown + display blocks
    report_html = build_report_html(content_markdown, display_blocks)

    # Build thread URL for "View in Norm" button
    domain = settings.CORS_ALLOWED_ORIGINS.split(",")[0].strip().rstrip("/")
    if domain == "*":
        domain = "https://bettercallnorm.com"
    thread_url = f"{domain}/app" if not thread_id else f"{domain}/app"

    # Render via template
    import datetime

    template_context = {
        "subject": subject,
        "report_html": report_html,
        "thread_url": thread_url,
        "generated_at": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
    }

    log_id = send_system_email("report", to, template_context, db, thread_id=thread_id)

    log = db.query(EmailLog).filter(EmailLog.id == log_id).first() if log_id else None

    return {
        "success": log.status == "sent" if log else False,
        "data": {"email_log_id": log_id},
        "error": log.error_message if log else "Failed to send",
    }


# Date Resolution
# ---------------------------------------------------------------------------


@register("norm", "resolve_dates")
def _resolve_dates(params: dict, db: Session, thread_id: str | None) -> dict:
    """Resolve natural language time references into exact ISO 8601 timestamps.

    Uses a fast Haiku LLM call with Python-computed date context so the LLM
    doesn't have to figure out day-of-week math itself.
    """
    import datetime as _dt
    import json as _json

    import zoneinfo

    from app.config import settings
    from app.interpreter.llm_interpreter import call_llm

    query = params.get("query", "").strip()
    if not query:
        return {"success": False, "data": {}, "error": "query is required"}

    tz_name = params.get("timezone", "Pacific/Auckland")
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = zoneinfo.ZoneInfo("Pacific/Auckland")
        tz_name = "Pacific/Auckland"

    now = _dt.datetime.now(tz)
    day_name = now.strftime("%A")  # e.g. "Thursday"
    date_str = now.strftime("%Y-%m-%d")  # e.g. "2026-03-26"
    utc_offset = now.strftime("%z")  # e.g. "+1300"
    offset_formatted = f"{utc_offset[:3]}:{utc_offset[3:]}"  # "+13:00"

    # Compute reference dates in Python so Haiku has anchors
    # Monday of current week
    days_since_monday = now.weekday()  # 0=Mon
    this_monday = (now - _dt.timedelta(days=days_since_monday)).strftime("%Y-%m-%d")
    last_monday = (now - _dt.timedelta(days=days_since_monday + 7)).strftime("%Y-%m-%d")
    first_of_month = now.replace(day=1).strftime("%Y-%m-%d")
    if now.month == 1:
        first_of_last_month = now.replace(year=now.year - 1, month=12, day=1).strftime(
            "%Y-%m-%d"
        )
    else:
        first_of_last_month = now.replace(month=now.month - 1, day=1).strftime(
            "%Y-%m-%d"
        )

    system_prompt = f"""You are a date resolver for a hospitality platform in New Zealand.

Today is {day_name} {date_str} (timezone: {tz_name}, UTC offset: {offset_formatted}).

Reference dates (computed, use these as anchors):
- This Monday: {this_monday}
- Last Monday: {last_monday}
- First of this month: {first_of_month}
- First of last month: {first_of_last_month}

Business rules:
- A business week runs from 7:00am Monday to 6:59am the following Monday.
- A business day runs from 7:00am to 6:59am the next day.
- "Last week" means the most recent completed week (last Monday 7am to this Monday 6:59am).
- "This week" means the current week starting this Monday 7am.

Convert the user's time reference into exact ISO 8601 periods.
Return ONLY valid JSON — no markdown, no explanation:
{{"periods": [{{"label": "Mon 16 Mar", "start": "2026-03-16T07:00:00{offset_formatted}", "end": "2026-03-23T06:59:59{offset_formatted}"}}]}}

Rules:
- All timestamps MUST include the timezone offset ({offset_formatted})
- For "last week": one period, last Monday 7am to this Monday 6:59am
- For "last month": one period, 1st 00:00:00 to last day 23:59:59
- For recurring periods (e.g., "every Friday 5pm-9pm for 12 weeks"): one period per occurrence
- Order periods chronologically, oldest first
- Use 24-hour time"""

    try:
        parsed, _llm_call_id = call_llm(
            system_prompt=system_prompt,
            user_prompt=query,
            model=settings.DATE_RESOLVER_MODEL,
            db=db,
            thread_id=thread_id,
            call_type="date_resolution",
            max_tokens=4096,
        )
        # call_llm returns the already-parsed JSON dict directly
        periods = parsed.get("periods", [])
        if not periods:
            return {
                "success": False,
                "data": {},
                "error": "No periods resolved from the query",
            }

        # Validate and fix labels with Python-computed day names
        for p in periods:
            try:
                start = _dt.datetime.fromisoformat(p["start"])
                _dt.datetime.fromisoformat(p["end"])
                # Override Haiku-generated label with correct Python-computed one
                p["label"] = start.strftime("%A %d %b")  # e.g., "Monday 23 Mar"
            except (KeyError, ValueError) as ve:
                logger.warning("Invalid period in resolve_dates: %s – %s", p, ve)

        # Build date-to-day lookup so the LLM can reference correct day names
        date_ref: dict[str, str] = {}
        for p in periods:
            try:
                s = _dt.datetime.fromisoformat(p["start"]).date()
                e = _dt.datetime.fromisoformat(p["end"]).date()
                d = s
                while d <= e:
                    date_ref[d.isoformat()] = d.strftime("%A")
                    d += _dt.timedelta(days=1)
            except (KeyError, ValueError):
                pass

        return {
            "success": True,
            "data": {
                "periods": periods,
                "timezone": tz_name,
                "date_reference": date_ref,
            },
        }
    except _json.JSONDecodeError as e:
        logger.error("resolve_dates JSON parse error: %s", e)
        return {
            "success": False,
            "data": {},
            "error": f"Failed to parse date resolution response: {e}",
        }
    except Exception as e:
        logger.error("resolve_dates error: %s", e)
        return {"success": False, "data": {}, "error": str(e)}


# Reports — Chart Rendering
# ---------------------------------------------------------------------------


def _auto_detect_chart_config(
    rows: list[dict],
    x_axis_key: str | None = None,
    series: list | None = None,
    chart_type: str | None = None,
    field_labels: dict | None = None,
) -> tuple[str, list, str, dict]:
    """Infer chart configuration from data shape when the LLM doesn't specify.

    Returns (x_axis_key, series, chart_type, field_labels).
    """
    if not rows or not isinstance(rows[0], dict):
        return x_axis_key or "", series or [], chart_type or "bar", field_labels or {}

    labels = dict(field_labels or {})
    keys = list(rows[0].keys())
    # Separate fields by type
    date_keys = [k for k in keys if "time" in k.lower() or "date" in k.lower()]
    label_keys = [
        k
        for k in keys
        if k in ("label", "name", "staff", "category", "group", "venue")
        or k.endswith("_name")
    ]
    numeric_keys = [
        k
        for k in keys
        if isinstance(rows[0].get(k), (int, float))
        and k not in ("id", "count")
        and not k.startswith("_")
    ]
    # Exclude internal fields
    numeric_keys = [k for k in numeric_keys if k not in date_keys]

    # Determine x-axis
    if not x_axis_key:
        if date_keys:
            x_axis_key = date_keys[0]
        elif label_keys:
            x_axis_key = label_keys[0]
        elif keys:
            x_axis_key = keys[0]
        else:
            x_axis_key = ""

    # Determine series (numeric fields that aren't the x-axis)
    if not series:
        series_keys = [k for k in numeric_keys if k != x_axis_key]
        if not series_keys and numeric_keys:
            series_keys = numeric_keys[:3]
        series = [{"key": k, "label": labels.get(k, k)} for k in series_keys[:4]]

    # Determine chart type
    if not chart_type:
        if date_keys and x_axis_key in date_keys:
            chart_type = "line" if len(rows) > 5 else "bar"
        elif label_keys and x_axis_key in label_keys:
            chart_type = "bar"
        else:
            chart_type = "bar"

    # Auto-generate labels for common field names
    _label_map = {
        "startTime": "Date",
        "amount": "Sales ($)",
        "invoices": "Sales ($)",
        "quantity": "Quantity",
        "count": "Count",
        "label": "Name",
    }
    for k in [x_axis_key] + [s["key"] if isinstance(s, dict) else s for s in series]:
        if k and k not in labels and k in _label_map:
            labels[k] = _label_map[k]

    return x_axis_key, series, chart_type, labels


@register("norm_reports", "render_chart")
def _render_chart(params: dict, db: Session, thread_id: str | None) -> dict:
    """Render a chart by referencing a prior tool call's data.

    The LLM specifies which tool call to visualize (by ID) and how to
    configure the chart (type, axes, series). The handler pulls the actual
    data from the referenced ToolCall's result_payload in the DB.
    """
    from app.db.models import ToolCall

    source_id = params.get("source_tool_call_id", "")
    if not source_id:
        return {
            "success": False,
            "data": {},
            "error": "source_tool_call_id is required",
        }

    tc = db.query(ToolCall).filter(ToolCall.id == source_id).first()

    # Fallback: if exact ID not found, find the most recent GET tool call
    # for this task (the LLM sometimes hallucinates the ID)
    if (not tc or not tc.result_payload) and thread_id:
        tc = (
            db.query(ToolCall)
            .filter(
                ToolCall.thread_id == thread_id,
                ToolCall.method == "GET",
                ToolCall.status == "executed",
                ToolCall.connector_name != "norm_reports",  # not another render_chart
                ToolCall.connector_name != "norm",  # not internal tools
                ToolCall.result_payload.isnot(None),
            )
            .order_by(ToolCall.created_at.desc())
            .first()
        )
        if tc:
            logger.info(
                "render_chart: ID %s not found, falling back to most recent GET: %s (%s.%s)",
                source_id,
                tc.id,
                tc.connector_name,
                tc.action,
            )

    if not tc or not tc.result_payload:
        return {
            "success": False,
            "data": {},
            "error": f"Tool call not found or has no data: {source_id}",
        }

    # Extract the data array from the tool call's result.
    # The result_payload is already transformed (transforms are applied during
    # tool execution in the tool loop) — do NOT re-apply transforms here.
    payload = tc.result_payload
    rows = _find_data_array(payload)

    # Build replayable script from the source tool call
    script = {
        "connector": tc.connector_name,
        "action": tc.action,
        "params": tc.input_params or {},
    }

    # Filter to selected fields only (handle JSON string or list)
    select_fields = params.get("select_fields")
    if isinstance(select_fields, str):
        try:
            import json

            select_fields = json.loads(select_fields)
        except (json.JSONDecodeError, TypeError):
            select_fields = None
    if select_fields and rows:
        rows = [{k: row.get(k) for k in select_fields if k in row} for row in rows]

    # Field labels for display (handle JSON string or dict)
    field_labels = params.get("field_labels") or {}
    if isinstance(field_labels, str):
        try:
            import json

            field_labels = json.loads(field_labels)
        except (json.JSONDecodeError, TypeError):
            field_labels = {}

    # Chart config — auto-detect from data if the LLM didn't specify
    title = params.get("title", "Chart")
    chart_type = params.get("chart_type")
    x_axis_key = params.get("x_axis_key")
    series = params.get("series")
    orientation = params.get("orientation", "vertical")

    # Auto-detect chart configuration from data shape
    if rows and (not x_axis_key or not series):
        x_axis_key, series, chart_type, field_labels = _auto_detect_chart_config(
            rows,
            x_axis_key=x_axis_key,
            series=series,
            chart_type=chart_type,
            field_labels=field_labels,
        )

    chart_type = chart_type or "bar"
    x_axis_key = x_axis_key or ""
    series = series or []
    x_axis_label = params.get("x_axis_label", field_labels.get(x_axis_key, x_axis_key))

    default_colors = [
        "#d4c4ae",
        "#a8cfc0",
        "#b8c8dc",
        "#e0c8a8",
        "#c8b8d4",
        "#a8d0b8",
        "#d8c0b8",
        "#b8d0d4",
    ]
    formatted_series = []
    for i, s in enumerate(series):
        if isinstance(s, dict):
            key = s.get("key", "")
            formatted_series.append(
                {
                    "key": key,
                    "label": s.get("label", field_labels.get(key, key)),
                    "color": s.get("color", default_colors[i % len(default_colors)]),
                }
            )
        elif isinstance(s, str):
            formatted_series.append(
                {
                    "key": s,
                    "label": field_labels.get(s, s),
                    "color": default_colors[i % len(default_colors)],
                }
            )

    return {
        "success": True,
        "data": {
            "rows": rows or [],
            "script": script,
        },
        "_chart_props": {
            "chart_type": chart_type,
            "title": title,
            "x_axis": {"key": x_axis_key, "label": x_axis_label},
            "series": formatted_series,
            "orientation": orientation,
            "field_labels": field_labels,
        },
    }


def _find_data_array(payload):
    """Extract the primary data array from a tool result payload."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "lines", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if key == "data" and isinstance(val, dict):
                for inner in ("items", "lines", "results", "data"):
                    inner_val = val.get(inner)
                    if isinstance(inner_val, list):
                        return inner_val
    return None


# ---------------------------------------------------------------------------
# Search Tool Result (slimmed result search)
# ---------------------------------------------------------------------------


@register("norm", "search_tool_result")
def _handle_search_tool_result(
    params: dict, db: Session, thread_id: str | None
) -> dict:
    """Search through a stored tool call's result payload by keyword."""
    from app.agents.tool_loop import _search_tool_result

    top_n = params.get("top_n")
    if top_n is not None:
        try:
            top_n = int(top_n)
        except (ValueError, TypeError):
            top_n = None

    result = _search_tool_result(
        params.get("tool_call_id", ""),
        params.get("query", ""),
        params.get("fields"),
        db,
        thread_id=thread_id,
        sort_by=params.get("sort_by"),
        sort_order=params.get("sort_order"),
        top_n=top_n,
    )
    return {"success": True, "data": result}


# ---------------------------------------------------------------------------
# Display Components (LLM-triggered visual output)
# ---------------------------------------------------------------------------


def _show_component(
    params: dict, db: Session, thread_id: str | None, expected_action: str | None = None
) -> dict:
    """Generic handler that retrieves a prior tool call's data for display.

    The LLM calls this when it wants to show a visual component to the user,
    referencing a previous data-fetching tool call by ID.
    """
    from app.db.models import ToolCall

    source_id = params.get("source_tool_call_id", "").strip()

    tc = None
    if source_id:
        tc = db.query(ToolCall).filter(ToolCall.id == source_id).first()

    # Fallback: find the most recent matching GET call for this thread
    if (not tc or not tc.result_payload) and thread_id:
        q = db.query(ToolCall).filter(
            ToolCall.thread_id == thread_id,
            ToolCall.method == "GET",
            ToolCall.status == "executed",
            ToolCall.connector_name != "norm",
        )
        if expected_action:
            q = q.filter(ToolCall.action == expected_action)
        tc = q.order_by(ToolCall.created_at.desc()).first()

    if not tc or not tc.result_payload:
        return {"success": False, "data": {}, "error": "Source tool call not found"}

    return {"success": True, "data": tc.result_payload}


@register("norm", "show_roster")
def _show_roster(params: dict, db: Session, thread_id: str | None) -> dict:
    """Display the roster as a visual weekly grid."""
    return _show_component(params, db, thread_id, expected_action="get_roster")


@register("norm", "show_orders")
def _show_orders(params: dict, db: Session, thread_id: str | None) -> dict:
    """Display the orders dashboard."""
    return _show_component(
        params, db, thread_id, expected_action="get_purchase_orders_summary"
    )


# ---------------------------------------------------------------------------
# Automated Tasks
# ---------------------------------------------------------------------------


def _automated_task_to_dict(t) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "agent_slug": t.agent_slug,
        "prompt": t.prompt,
        "schedule_type": t.schedule_type,
        "schedule_config": t.schedule_config,
        "status": t.status,
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@register("norm", "create_automated_task")
def _create_automated_task(params: dict, db: Session, thread_id: str | None) -> dict:
    """Create an automated task using a sub-LLM call to draft the task prompt.

    Gathers conversation context (playbooks used, tools called) and uses Haiku
    to produce a well-structured, self-contained task prompt. Also infers
    tool_filter from the conversation's actual tool usage.
    """
    import json as _json

    from app.config import settings
    from app.db.models import AutomatedTask, LlmCall, Thread, ToolCall, User
    from app.interpreter.llm_interpreter import call_llm

    intent = params.get("intent", "").strip()
    agent_slug = params.get("agent_slug", "").strip()

    schedule_hint = params.get("schedule", "").strip()

    # Look up user context and auto-detect agent_slug from the thread's domain
    created_by = None
    user_email = "the user"
    parent = None
    if thread_id:
        parent = db.query(Thread).filter(Thread.id == thread_id).first()
        if parent:
            # Always use the thread's domain as agent_slug — the LLM often guesses wrong
            if parent.domain:
                agent_slug = parent.domain
            if parent.user_id:
                created_by = parent.user_id
                user = db.query(User).filter(User.id == parent.user_id).first()
                if user:
                    user_email = user.email

    if not intent or not agent_slug:
        return {
            "success": False,
            "data": {},
            "error": "intent and agent_slug are required",
        }

    # Gather playbooks used in the conversation
    playbook_section = ""
    if thread_id:
        routing_calls = (
            db.query(LlmCall)
            .filter(LlmCall.thread_id == thread_id, LlmCall.call_type == "routing")
            .all()
        )
        playbook_slugs = set()
        for lc in routing_calls:
            slug = (lc.parsed_response or {}).get("playbook")
            if slug:
                bare = slug.split("/")[-1] if "/" in slug else slug
                playbook_slugs.add(bare)

        if playbook_slugs:
            from app.db.config_models import Playbook
            from app.db.engine import _ConfigSessionLocal

            config_db = _ConfigSessionLocal()
            try:
                playbooks = (
                    config_db.query(Playbook)
                    .filter(
                        Playbook.slug.in_(playbook_slugs),
                        Playbook.enabled == True,  # noqa: E712
                    )
                    .all()
                )
                if playbooks:
                    pb_parts = [
                        f"## {pb.display_name}\n{pb.instructions}" for pb in playbooks
                    ]
                    playbook_section = (
                        "\nPlaybooks used in this conversation (include relevant "
                        "workflow steps, but user-specific requests always take "
                        "priority over playbook defaults):\n\n" + "\n\n".join(pb_parts)
                    )
            finally:
                config_db.close()

    # Gather tools used in conversation → build tool_filter
    tool_filter = None
    tool_list_str = "all available tools"
    if thread_id:
        used = (
            db.query(ToolCall.action)
            .filter(ToolCall.thread_id == thread_id, ToolCall.status == "executed")
            .distinct()
            .all()
        )
        actions = {a[0] for a in used if a[0]}
        # Always include utility + email tools
        actions |= {"resolve_dates", "search_tool_result"}
        actions |= {"send_notification", "send_email"}
        # Remove internal-only tools not useful for scheduled runs
        internal_only = {
            "update_task_config",
            "set_override",
            "update_thread_summary",
            "create_automated_task",
            "show_roster",
            "show_orders",
        }
        actions -= internal_only
        # Only set tool_filter if we found meaningful domain tools
        # (not just utilities like resolve_dates, search_tool_result)
        utility_tools = {
            "resolve_dates",
            "search_tool_result",
            "send_notification",
            "send_email",
        }
        domain_actions = actions - utility_tools
        if len(domain_actions) >= 1:
            tool_filter = sorted(actions)
            tool_list_str = ", ".join(tool_filter)
        else:
            logger.info("No domain tools found in conversation — skipping tool_filter")

    # Sub-LLM call to draft the task
    system_prompt = f"""You are a task prompt writer for Norm, a hospitality operations platform.

Given the user's intent, create a well-structured automated task that an AI agent will execute on a schedule.

Rules for writing task prompts:
- The prompt must be completely self-contained — no conversation context is available during scheduled runs.
- Include specific venue names, employee names, item names — never use pronouns like "this" or "the same".
- If the user wants email delivery, include explicit instructions to email results to {user_email}.
- Write the prompt as clear step-by-step instructions the agent should follow.
- Be specific about what data to fetch, how to present it, and what to do with the results.
- User-specific requests ALWAYS take priority over playbook defaults (e.g. formatting preferences).
{playbook_section}

Tools available for this task: {tool_list_str}

Schedule hints:
- If the user said "daily at 9am" → schedule_type: "daily", schedule_config: {{"hour": 9, "minute": 0}}
- If "every monday at 8am" → schedule_type: "weekly", schedule_config: {{"day_of_week": "monday", "hour": 8, "minute": 0}}
- If "monthly on the 1st" → schedule_type: "monthly", schedule_config: {{"day_of_month": 1, "hour": 9, "minute": 0}}
- If no schedule mentioned → schedule_type: "manual", schedule_config: {{}}

Return ONLY valid JSON — no markdown, no explanation:
{{"title": "Short descriptive title (3-6 words)", "description": "One-line summary of what the task does", "prompt": "The full self-contained task prompt with step-by-step instructions...", "schedule_type": "manual|daily|weekly|monthly", "schedule_config": {{}}}}"""

    user_prompt = f"Intent: {intent}"
    if schedule_hint:
        user_prompt += f"\nSchedule: {schedule_hint}"

    try:
        parsed, _llm_call_id = call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=settings.DATE_RESOLVER_MODEL,  # Haiku — fast and cheap
            db=db,
            thread_id=thread_id,
            call_type="task_creation",
            max_tokens=1024,
        )

        title = parsed.get("title", "Automated Task")
        prompt = parsed.get("prompt", intent)
        description = parsed.get("description")
        schedule_type = parsed.get("schedule_type", "manual")
        schedule_config = parsed.get("schedule_config") or {}

    except (_json.JSONDecodeError, Exception) as e:
        logger.warning("Task creation sub-LLM failed, using intent as prompt: %s", e)
        title = intent[:60]
        prompt = intent
        description = None
        schedule_type = "manual"
        schedule_config = {}

    task = AutomatedTask(
        title=title,
        description=description,
        agent_slug=agent_slug,
        prompt=prompt,
        schedule_type=schedule_type,
        schedule_config=schedule_config,
        tool_filter=tool_filter,
        status="draft",
        created_by=created_by,
    )
    db.add(task)
    db.flush()

    return {"success": True, "data": _automated_task_to_dict(task)}


@register("norm", "list_automated_tasks")
def _list_automated_tasks(params: dict, db: Session, thread_id: str | None) -> dict:
    """List automated tasks, optionally filtered by agent or status."""
    from app.db.models import AutomatedTask

    query = db.query(AutomatedTask)
    agent_slug = params.get("agent_slug")
    status = params.get("status")
    if agent_slug:
        query = query.filter(AutomatedTask.agent_slug == agent_slug)
    if status:
        query = query.filter(AutomatedTask.status == status)

    tasks = query.order_by(AutomatedTask.created_at.desc()).all()
    return {
        "success": True,
        "data": {"tasks": [_automated_task_to_dict(t) for t in tasks]},
    }


@register("norm", "update_automated_task")
def _update_automated_task(params: dict, db: Session, thread_id: str | None) -> dict:
    """Update an automated task's fields."""
    from app.db.models import AutomatedTask
    from app.services.task_scheduler import schedule_task, unschedule_task

    atask_id = params.get("task_id")
    if not atask_id:
        return {"success": False, "data": {}, "error": "task_id is required"}

    task = db.query(AutomatedTask).filter(AutomatedTask.id == atask_id).first()
    if not task:
        return {
            "success": False,
            "data": {},
            "error": f"Automated task not found: {atask_id}",
        }

    for field in (
        "title",
        "description",
        "prompt",
        "schedule_type",
        "schedule_config",
        "status",
    ):
        if field in params:
            setattr(task, field, params[field])

    db.flush()

    # Update scheduler
    if task.status == "active":
        schedule_task(task)
    else:
        unschedule_task(task.id)

    return {"success": True, "data": _automated_task_to_dict(task)}


@register("norm", "run_automated_task")
def _run_automated_task(params: dict, db: Session, thread_id: str | None) -> dict:
    """Trigger a manual run of an automated task."""
    from app.services.task_scheduler import execute_task_now

    atask_id = params.get("task_id")
    if not atask_id:
        return {"success": False, "data": {}, "error": "task_id is required"}

    mode = params.get("mode", "live")
    return execute_task_now(atask_id, mode=mode, db=db)


# ---------------------------------------------------------------------------
# Consolidator Execution Engine
# ---------------------------------------------------------------------------


def _resolve_path(obj, path_str: str):
    """Walk a dotted path with optional [N] array indexing into a nested object.

    Examples:
        "id"            -> obj["id"]
        "items[0].name" -> obj["items"][0]["name"]
        "name"          -> obj[0]["name"] if obj is a list (auto-unwrap first element)
    """
    import re as _re

    segments = path_str.split(".")
    current = obj
    for seg in segments:
        if current is None:
            return None
        m = _re.match(r"^(\w+)\[(\d+)\]$", seg)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(seg)
            elif (
                isinstance(current, list)
                and len(current) > 0
                and isinstance(current[0], dict)
            ):
                current = current[0].get(seg)
            else:
                return None
    return current


def _step_result_preview(step_result: dict) -> dict:
    """Build a compact preview of a step result for debugging."""
    data = step_result.get("data", step_result)
    if isinstance(data, list):
        preview: dict = {"_type": "array", "_count": len(data)}
        if data and isinstance(data[0], dict):
            preview["_fields"] = list(data[0].keys())
            preview["_sample"] = data[0]
        return preview
    if isinstance(data, dict):
        # Show keys and nested structure hints
        preview = {}
        for k, v in data.items():
            if isinstance(v, list):
                preview[k] = f"[{len(v)} items]"
                if v and isinstance(v[0], dict):
                    preview[k] = (
                        f"[{len(v)} items: {{{', '.join(list(v[0].keys())[:5])}}}]"
                    )
            elif isinstance(v, dict):
                preview[k] = f"{{{', '.join(list(v.keys())[:5])}}}"
            else:
                s = str(v)
                preview[k] = s if len(s) <= 80 else s[:77] + "..."
        return preview
    return {"_value": str(data)[:200]}


def _resolve_stock_items(
    requested_items: list[dict], stock_items: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Resolve requested items against stock items by name, stock_code, or itemId.

    Returns (resolved, ambiguous, failed) where:
    - resolved: items with confirmed itemId + quantity
    - ambiguous: items with multiple candidates needing user selection
    - failed: items with no match
    """
    # Build lookup indexes
    by_id = {s["id"]: s for s in stock_items if s.get("id")}
    by_name_lower: dict[str, list[dict]] = {}
    for s in stock_items:
        name = (s.get("name") or "").lower().strip()
        if name:
            by_name_lower.setdefault(name, []).append(s)

    resolved = []
    ambiguous = []
    failed = []

    for item in requested_items:
        if not isinstance(item, dict):
            continue
        quantity = item.get("quantity", item.get("orderQty", item.get("qty", 1)))

        # Path 1: itemId provided — validate and pass through
        if item.get("itemId"):
            if item["itemId"] in by_id:
                resolved.append(
                    {
                        "itemId": item["itemId"],
                        "quantity": quantity,
                        "matched_name": by_id[item["itemId"]].get("name", ""),
                    }
                )
            else:
                failed.append(
                    {
                        "name": item.get("name", item["itemId"]),
                        "reason": "Invalid itemId",
                    }
                )
            continue

        query = (item.get("name") or "").strip()
        if not query:
            continue

        query_lower = query.lower()

        # Path 2: stock_code match
        if item.get("stock_code"):
            code = item["stock_code"].strip().lower()
            for s in stock_items:
                # Check stock code across all supplier variants
                for sup in s.get("suppliers", []):
                    if (sup.get("stockCode") or "").lower() == code:
                        resolved.append(
                            {
                                "itemId": s["id"],
                                "quantity": quantity,
                                "matched_name": s.get("name", ""),
                            }
                        )
                        break
                else:
                    continue
                break
            else:
                failed.append(
                    {
                        "name": query,
                        "reason": f"Stock code '{item['stock_code']}' not found",
                    }
                )
            continue

        # Path 3: exact name match (case-insensitive)
        exact = by_name_lower.get(query_lower, [])
        if len(exact) == 1:
            resolved.append(
                {
                    "itemId": exact[0]["id"],
                    "quantity": quantity,
                    "matched_name": exact[0].get("name", ""),
                }
            )
            continue

        # Path 4: substring match — query in item name or item name in query
        candidates = []
        for s in stock_items:
            s_name = (s.get("name") or "").lower()
            if query_lower in s_name or s_name in query_lower:
                candidates.append(s)

        # Path 5: word-level match — all query words found in item name
        # Handles plurals (kegs→keg) and partial words (peroni→PERONI)
        if not candidates:
            query_words = [w for w in query_lower.split() if len(w) >= 2]
            if query_words:
                for s in stock_items:
                    s_name = (s.get("name") or "").lower()
                    s_words = s_name.split()
                    matched = 0
                    for qw in query_words:
                        # Check if query word is a substring of any item word
                        # or item word is a substring of query word (handles plurals)
                        if any(qw in sw or sw in qw for sw in s_words):
                            matched += 1
                    if matched == len(query_words):
                        candidates.append(s)

        if len(candidates) == 1:
            resolved.append(
                {
                    "itemId": candidates[0]["id"],
                    "quantity": quantity,
                    "matched_name": candidates[0].get("name", ""),
                }
            )
        elif len(candidates) > 1:
            ambiguous.append(
                {
                    "query": query,
                    "quantity": quantity,
                    "candidates": [
                        {
                            "id": c["id"],
                            "name": c.get("name", ""),
                            "group": c.get("groupName", ""),
                        }
                        for c in candidates[:10]
                    ],
                }
            )
        else:
            failed.append({"name": query, "reason": "No match found"})

    return resolved, ambiguous, failed


@register("norm", "create_purchase_order")
def _create_purchase_order(params: dict, db: Session, thread_id: str | None) -> dict:
    """Create a purchase order with items resolved by name.

    Items can be specified by name (auto-resolved) or by itemId (direct).
    Returns resolved order_lines for the PurchaseOrderEditor, plus a
    resolution report and any ambiguous items needing user selection.
    """
    venue = params.get("venue", "")
    if not venue:
        return {"success": False, "data": {}, "error": "venue is required"}

    from app.db.models import Venue

    venue_obj = db.query(Venue).filter(Venue.name.ilike(f"%{venue}%")).first()
    venue_id = venue_obj.id if venue_obj else None

    items = params.get("items", [])
    if not isinstance(items, list):
        items = []

    # Fetch stock items for name resolution
    stock_items = []
    if items and venue_id:
        try:
            from app.connectors.function_executor import execute_function

            fetch_code = (
                "def run(params, call_api, log):\n"
                "    items = call_api('loadedhub', 'get_stock_items', {'venue': params['venue']})\n"
                "    if isinstance(items, list):\n"
                "        return items\n"
                "    return []\n"
            )
            result = execute_function(fetch_code, {"venue": venue}, db, thread_id)
            raw = result.get("data", [])
            if isinstance(raw, list):
                stock_items = raw
        except Exception:
            logger.warning("Failed to fetch stock items for PO resolution")

    # Resolve items by name/stockCode/itemId
    resolved, ambiguous, failed_items = _resolve_stock_items(items, stock_items)

    # Build order_lines from resolved items
    order_lines = []
    for r in resolved:
        line: dict = {"itemId": r["itemId"], "quantity": r["quantity"]}
        order_lines.append(line)

    # Build resolution report
    resolution_report: dict = {}
    if resolved:
        resolution_report["resolved"] = [
            {"name": r.get("matched_name", ""), "quantity": r["quantity"]}
            for r in resolved
        ]
    if failed_items:
        resolution_report["failed"] = failed_items

    # Build response
    data: dict = {
        "message": f"Purchase order editor opened for {venue}"
        + (
            f" with {len(order_lines)} item{'s' if len(order_lines) != 1 else ''}"
            if order_lines
            else ""
        ),
        "venue": venue,
        "venue_id": venue_id,
        "order_lines": order_lines,
    }

    if resolution_report:
        data["resolution_report"] = resolution_report
    if ambiguous:
        data["needs_selection"] = ambiguous
        data["message"] += (
            f". {len(ambiguous)} item{'s' if len(ambiguous) != 1 else ''} need clarification"
        )

    return {"success": True, "data": data}


def execute_consolidator(
    config: dict, input_params: dict, db: Session, thread_id: str | None
) -> dict:
    """Execute a consolidator tool — dispatches to function executor.

    The config must contain a `function_code` key with a Python function
    that defines `run(params, call_api, log)`.
    """
    import json as _json

    # Handle double-encoded JSON string configs
    if isinstance(config, str):
        try:
            config = _json.loads(config)
        except _json.JSONDecodeError:
            cleaned = config.rstrip("}") + "}"
            config = _json.loads(cleaned)

    function_code = config.get("function_code")
    if not function_code:
        return {
            "success": False,
            "data": {},
            "error": "consolidator_config must contain function_code — see Settings > Connectors to edit",
        }

    from app.connectors.function_executor import execute_function

    return execute_function(function_code, input_params, db, thread_id)


# Legacy consolidator code removed — all consolidators now use function_code
# executed via function_executor.py. See docs/architecture.md section 8.


# ---------------------------------------------------------------------------
# Automated Task Config Handlers
# ---------------------------------------------------------------------------


def _get_automated_task_for_conversation(thread_id: str | None, db: Session):
    """Look up the AutomatedTask linked to a conversation thread_id."""
    from app.db.models import AutomatedTask, Thread

    if not thread_id:
        return None
    task = db.query(Thread).filter(Thread.id == thread_id).first()
    if not task:
        return None
    return (
        db.query(AutomatedTask)
        .filter(AutomatedTask.conversation_thread_id == thread_id)
        .first()
    )


@register("norm", "update_task_config")
def _update_task_config(params: dict, db: Session, thread_id: str | None) -> dict:
    """Update a persistent configuration field on the automated task."""
    key = params.get("key", "")
    value = params.get("value")
    if not key:
        return {"success": False, "data": {}, "error": "key is required"}

    at = _get_automated_task_for_conversation(thread_id, db)
    if not at:
        return {
            "success": False,
            "data": {},
            "error": "No automated task found for this conversation",
        }

    config = dict(at.task_config or {})
    if value is None:
        config.pop(key, None)
    else:
        config[key] = value
    at.task_config = config
    db.flush()

    return {"success": True, "data": {"task_config": config}}


@register("norm", "set_override")
def _set_override(params: dict, db: Session, thread_id: str | None) -> dict:
    """Set a one-off instruction for the next scheduled run only."""
    instruction = params.get("instruction", "")
    if not instruction:
        return {"success": False, "data": {}, "error": "instruction is required"}

    at = _get_automated_task_for_conversation(thread_id, db)
    if not at:
        return {
            "success": False,
            "data": {},
            "error": "No automated task found for this conversation",
        }

    at.overrides_next_run = {"instruction": instruction}
    db.flush()

    return {"success": True, "data": {"overrides_next_run": at.overrides_next_run}}


@register("norm", "update_thread_summary")
def _update_thread_summary(params: dict, db: Session, thread_id: str | None) -> dict:
    """Update the rolling summary of key decisions and instructions."""
    summary = params.get("summary", "")
    if not summary:
        return {"success": False, "data": {}, "error": "summary is required"}

    at = _get_automated_task_for_conversation(thread_id, db)
    if not at:
        return {
            "success": False,
            "data": {},
            "error": "No automated task found for this conversation",
        }

    at.thread_summary = summary
    db.flush()

    return {"success": True, "data": {"thread_summary": summary}}
