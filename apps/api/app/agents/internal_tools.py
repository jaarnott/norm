"""Registry of internal tool handlers.

Internal tools execute against the local database instead of external APIs.
Each handler receives (input_params, db, thread_id) and returns a result dict
compatible with the standard tool result format:
    {"success": bool, "data": ..., "error": str | None}
"""

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
    subject = params.get("subject", "")
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
    subject = params.get("subject", "")
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


@register("norm_email", "send_notification")
def _system_send_notification(params: dict, db: Session, thread_id: str | None) -> dict:
    """Send a system notification email from noreply@norm.com."""
    from app.services.email_service import send_system_email

    to = params.get("to", "")
    if isinstance(to, str):
        to = [addr.strip() for addr in to.split(",") if addr.strip()]
    template_name = params.get("template_name", "")
    template_context = params.get("template_context", {})

    if not to or not template_name:
        return {
            "success": False,
            "data": {},
            "error": "to and template_name are required",
        }

    if isinstance(template_context, str):
        import json

        try:
            template_context = json.loads(template_context)
        except (json.JSONDecodeError, TypeError):
            template_context = {}

    log_id = send_system_email(
        template_name, to, template_context, db, thread_id=thread_id
    )
    from app.db.models import EmailLog

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

    # Chart config
    title = params.get("title", "Chart")
    chart_type = params.get("chart_type", "bar")
    x_axis_key = params.get("x_axis_key", "")
    x_axis_label = params.get("x_axis_label", field_labels.get(x_axis_key, x_axis_key))
    series = params.get("series", [])
    orientation = params.get("orientation", "vertical")

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

    result = _search_tool_result(
        params.get("tool_call_id", ""),
        params.get("query", ""),
        params.get("fields"),
        db,
    )
    return {"success": True, "data": result}


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
    """Create an automated task (draft) for an agent."""
    from app.db.models import AutomatedTask

    title = params.get("title")
    prompt = params.get("prompt")
    agent_slug = params.get("agent_slug")
    if not title or not prompt or not agent_slug:
        return {
            "success": False,
            "data": {},
            "error": "title, prompt, and agent_slug are required",
        }

    # Resolve user from the parent task
    from app.db.models import Thread

    created_by = None
    if thread_id:
        parent = db.query(Thread).filter(Thread.id == thread_id).first()
        if parent:
            created_by = parent.user_id

    task = AutomatedTask(
        title=title,
        description=params.get("description"),
        agent_slug=agent_slug,
        prompt=prompt,
        schedule_type=params.get("schedule_type", "manual"),
        schedule_config=params.get("schedule_config") or {},
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


def execute_consolidator(
    config: dict, input_params: dict, db: Session, thread_id: str | None
) -> dict:
    """Execute a consolidator config — call sub-tools and aggregate results.

    A consolidator defines a sequence of connector tool calls (steps),
    optional keyword search across results, and output field filtering.
    """
    import datetime
    import json
    import re
    import time
    from app.db.models import ConnectorSpec, ConnectorConfig
    from app.connectors.spec_executor import execute_spec

    steps = config.get("steps", [])
    search_config = config.get("search")
    output_fields = config.get("output_fields")

    if not steps:
        return {
            "success": False,
            "data": {},
            "error": "No steps defined in consolidator config",
        }

    # Build template context for variable resolution
    # Resolve timezone from venue if available
    tz_offset = "%2B00:00"  # Default UTC
    try:
        venue_name = input_params.get("venue") or input_params.get("venue_name")
        if venue_name:
            from app.services.venue_service import resolve_venue_id

            venue_id = resolve_venue_id(venue_name, db)
            if venue_id:
                from app.db.models import Venue

                venue = db.query(Venue).filter(Venue.id == venue_id).first()
                if venue and venue.timezone:
                    from zoneinfo import ZoneInfo

                    tz = ZoneInfo(venue.timezone)
                    offset = datetime.datetime.now(tz).strftime("%z")
                    tz_offset = (
                        f"%2B{offset[1:3]}:{offset[3:]}"
                        if offset[0] == "+"
                        else f"-{offset[1:3]}:{offset[3:]}"
                    )
        elif not venue_name:
            # No venue specified — try to find any venue with a timezone
            from app.db.models import Venue as _V

            any_venue = db.query(_V).filter(_V.timezone.isnot(None)).first()
            if any_venue and any_venue.timezone:
                from zoneinfo import ZoneInfo

                tz = ZoneInfo(any_venue.timezone)
                offset = datetime.datetime.now(tz).strftime("%z")
                tz_offset = (
                    f"%2B{offset[1:3]}:{offset[3:]}"
                    if offset[0] == "+"
                    else f"-{offset[1:3]}:{offset[3:]}"
                )
    except Exception:
        pass  # Fall back to UTC

    today = datetime.date.today()
    four_weeks_ago = today - datetime.timedelta(weeks=4)
    one_week_ago = today - datetime.timedelta(weeks=1)

    template_ctx = {
        "today_iso": f"{today.isoformat()}T00:00:00{tz_offset}",
        "four_weeks_ago_iso": f"{four_weeks_ago.isoformat()}T00:00:00{tz_offset}",
        "one_week_ago_iso": f"{one_week_ago.isoformat()}T00:00:00{tz_offset}",
        "today": today.isoformat(),
        "four_weeks_ago": four_weeks_ago.isoformat(),
        "one_week_ago": one_week_ago.isoformat(),
        **input_params,
    }

    def resolve_template(value: str) -> str:
        """Replace {{var}} with values from template context or step results."""

        def replacer(match: re.Match) -> str:
            key = match.group(1).strip()
            # 1. Flat lookup in template context (backwards compatible)
            if key in template_ctx:
                return str(template_ctx[key])
            # 2. Step-result path resolution: {{step_id.field.path}}
            if "." in key:
                step_id, rest_path = key.split(".", 1)
                if step_id in step_results:
                    sr = step_results[step_id]
                    # Auto-skip into .data wrapper
                    data = sr.get("data", sr) if isinstance(sr, dict) else sr
                    resolved = _resolve_path(data, rest_path)
                    if resolved is not None:
                        return str(resolved)
            # 3. Step ID alone (no dot) — return whole data payload
            if key in step_results:
                sr = step_results[key]
                data = sr.get("data", sr) if isinstance(sr, dict) else sr
                return json.dumps(data) if not isinstance(data, str) else data
            # 4. No match — return original placeholder
            return match.group(0)

        return re.sub(r"\{\{(.+?)\}\}", replacer, value)

    def resolve_params(params: dict) -> dict:
        return {
            k: resolve_template(str(v)) if isinstance(v, str) else v
            for k, v in params.items()
        }

    # --- Step execution helper (used by both sequential and parallel paths) ---
    def _execute_single_step(
        step: dict,
        step_results: dict,
        step_meta: list,
        db_session: Session,
    ) -> None:
        step_id = step.get("id", f"step_{len(step_results)}")

        # --- Filter step: narrow a previous step's results (no API call) ---
        source_step = step.get("source")
        filter_config = step.get("filter")
        if source_step and filter_config:
            from app.agents.tool_loop import _unwrap_array

            source_data = step_results.get(source_step, {})
            raw = (
                source_data.get("data", source_data)
                if isinstance(source_data, dict)
                else source_data
            )
            arr = _unwrap_array(raw) if isinstance(raw, (dict, list)) else None

            if arr is None:
                step_results[step_id] = {
                    "error": f"Source step '{source_step}' has no array data"
                }
                step_meta.append(
                    {
                        "id": step_id,
                        "status": "error",
                        "type": "filter",
                        "error": f"No data from '{source_step}'",
                    }
                )
                return

            field = filter_config.get("field", "")
            keyword = resolve_template(filter_config.get("contains", ""))
            matches = [
                item
                for item in arr
                if isinstance(item, dict)
                and keyword.lower() in str(item.get(field, "")).lower()
            ]

            if len(matches) == 1:
                step_results[step_id] = {"success": True, "data": matches[0]}
            else:
                step_results[step_id] = {"success": True, "data": matches}
            step_meta.append(
                {
                    "id": step_id,
                    "status": "success",
                    "type": "filter",
                    "match_count": len(matches),
                    "result_preview": _step_result_preview(step_results[step_id]),
                }
            )
            return

        # --- API step: call a connector tool ---
        connector_name = step.get("connector")
        action = step.get("action")
        step_params = resolve_params(step.get("params", {}))

        if not connector_name or not action:
            step_results[step_id] = {"error": "Missing connector or action"}
            step_meta.append(
                {
                    "id": step_id,
                    "status": "error",
                    "error": "Missing connector or action",
                }
            )
            return

        # Look up spec and tool def (from config DB)
        from app.db.engine import _ConfigSessionLocal as _CfgSL

        _cfg = _CfgSL()
        spec = (
            _cfg.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == connector_name)
            .first()
        )
        _cfg.close()
        if not spec:
            step_results[step_id] = {"error": f"Connector not found: {connector_name}"}
            step_meta.append(
                {
                    "id": step_id,
                    "status": "error",
                    "error": f"Connector not found: {connector_name}",
                }
            )
            return

        tool_def = None
        for t in spec.tools or []:
            if t.get("action") == action:
                tool_def = t
                break
        if not tool_def:
            step_results[step_id] = {"error": f"Tool not found: {action}"}
            step_meta.append(
                {
                    "id": step_id,
                    "status": "error",
                    "error": f"Tool not found: {action}",
                }
            )
            return

        # Get credentials (venue-aware)
        from app.agents.tool_loop import _resolve_venue_config

        venue_lookup = {**input_params, **step_params}
        config_row = _resolve_venue_config(connector_name, venue_lookup, db_session)

        if (
            not config_row
            and not venue_lookup.get("venue")
            and not venue_lookup.get("venue_name")
            and not venue_lookup.get("venue_id")
        ):
            config_row = (
                db_session.query(ConnectorConfig)
                .filter(
                    ConnectorConfig.connector_name == connector_name,
                    ConnectorConfig.enabled == "true",
                )
                .first()
            )

        credentials = config_row.config if config_row else {}
        venue_id_val = config_row.venue_id if config_row else None

        step_params.pop("venue", None)
        step_params.pop("venue_name", None)
        step_params.pop("venue_id", None)

        t0 = time.time()
        try:
            result, rendered = execute_spec(
                spec,
                tool_def,
                step_params,
                credentials,
                db_session,
                thread_id,
                venue_id=venue_id_val,
            )
            duration_ms = int((time.time() - t0) * 1000)

            step_payload = result.response_payload
            step_transform = tool_def.get("response_transform")
            if step_transform and step_transform.get("enabled"):
                from app.connectors.response_transform import apply_response_transform

                wrapped = (
                    {"data": step_payload}
                    if isinstance(step_payload, list)
                    else (
                        step_payload
                        if isinstance(step_payload, dict)
                        else {"data": step_payload}
                    )
                )
                transformed = apply_response_transform(wrapped, step_transform)
                step_payload = (
                    transformed.get("data", transformed)
                    if isinstance(transformed, dict)
                    else transformed
                )

            step_results[step_id] = {
                "success": result.success,
                "data": step_payload,
                "error": result.error_message,
            }
            meta_entry: dict = {
                "id": step_id,
                "status": "success" if result.success else "error",
                "duration_ms": duration_ms,
                "params_sent": step_params,
            }
            if result.error_message:
                meta_entry["error"] = result.error_message
            payload = result.response_payload
            if payload is not None:
                from app.agents.tool_loop import _unwrap_array

                arr = (
                    _unwrap_array(payload)
                    if isinstance(payload, (dict, list))
                    else None
                )
                if arr is not None and len(arr) == 0:
                    meta_entry["result_empty"] = True
                    meta_entry["note"] = (
                        "API returned successfully but with no data for these parameters"
                    )
            meta_entry["result_preview"] = _step_result_preview(step_results[step_id])
            step_meta.append(meta_entry)
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            step_results[step_id] = {"error": str(exc)}
            step_meta.append(
                {
                    "id": step_id,
                    "status": "error",
                    "duration_ms": duration_ms,
                    "error": str(exc),
                }
            )

    # --- Group steps by parallel ID ---
    def _group_steps(
        steps: list[dict],
    ) -> list[dict]:
        """Group consecutive steps with the same 'parallel' value together."""
        groups: list[dict] = []
        for step in steps:
            par = step.get("parallel")
            if par and groups and groups[-1].get("parallel") == par:
                groups[-1]["steps"].append(step)
            else:
                groups.append({"parallel": par, "steps": [step]})
        return groups

    # Execute steps — with parallel group support
    step_results: dict[str, dict] = {}
    step_meta: list[dict] = []

    for group in _group_steps(steps):
        if group["parallel"] and len(group["steps"]) >= 2:
            # Parallel execution using ThreadPoolExecutor
            from concurrent.futures import ThreadPoolExecutor
            from app.db.engine import SessionLocal as _SL

            db.commit()  # Flush so worker threads see current state

            thread_results: dict[str, dict] = {}
            thread_meta: dict[str, list] = {}

            def _run_step(s: dict) -> None:
                sid = s.get("id", "?")
                local_results: dict[str, dict] = {**step_results}
                local_meta: list[dict] = []
                local_db = _SL()
                try:
                    _execute_single_step(s, local_results, local_meta, local_db)
                finally:
                    local_db.close()
                thread_results[sid] = local_results.get(sid, {"error": "no result"})
                thread_meta[sid] = local_meta

            max_workers = min(len(group["steps"]), 8)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_run_step, s) for s in group["steps"]]
                for f in futures:
                    f.result()  # Wait + propagate exceptions

            # Merge results back in step order
            for s in group["steps"]:
                sid = s.get("id", "?")
                if sid in thread_results:
                    step_results[sid] = thread_results[sid]
                if sid in thread_meta:
                    step_meta.extend(thread_meta[sid])
        else:
            # Sequential execution (single step or no parallel flag)
            for step in group["steps"]:
                _execute_single_step(step, step_results, step_meta, db)

    # Apply search if configured
    if search_config:
        keyword = resolve_template(search_config.get("keyword", ""))
        search_steps = search_config.get("steps", list(step_results.keys()))

        if keyword:
            from app.agents.tool_loop import _unwrap_array

            filtered_results: dict[str, list] = {}

            for step_id in search_steps:
                sr = step_results.get(step_id, {})
                data = sr.get("data", sr)
                arr = _unwrap_array(data) if isinstance(data, (dict, list)) else None

                if arr:
                    matches = []
                    for item in arr:
                        if keyword.lower() in json.dumps(item).lower():
                            if output_fields:
                                matches.append(
                                    {k: item.get(k) for k in output_fields if k in item}
                                )
                            else:
                                matches.append(item)
                    filtered_results[step_id] = matches
                elif arr is not None:
                    # Array found but empty — API returned no data
                    filtered_results[step_id] = []
                else:
                    filtered_results[step_id] = []

            # Check for overall empty results and add diagnostic info
            all_empty = all(
                len(v) == 0 if isinstance(v, list) else True
                for v in filtered_results.values()
            )
            result_data: dict = filtered_results
            if all_empty:
                result_data = {
                    **filtered_results,
                    "_diagnostic": {
                        "message": f"No results found matching '{keyword}' across searched steps.",
                        "searched_steps": search_steps,
                        "steps_with_empty_api_response": [
                            s["id"] for s in step_meta if s.get("result_empty")
                        ],
                        "steps_with_errors": [
                            {"id": s["id"], "error": s.get("error")}
                            for s in step_meta
                            if s.get("status") == "error"
                        ],
                        "hint": "This may mean no data exists for the given parameters (e.g., date too far in the past, item not found in this template).",
                    },
                }

            return {
                "success": True,
                "data": result_data,
                "_steps": step_meta,
            }

    # No search — return raw step results (optionally filtered to output_fields)
    if output_fields:
        for step_id, sr in step_results.items():
            data = sr.get("data", sr)
            arr = _unwrap_array(data) if isinstance(data, (dict, list)) else None
            if arr:
                step_results[step_id] = [
                    {k: item.get(k) for k in output_fields if k in item} for item in arr
                ]

    return {
        "success": all(
            sr.get("success", False) or "error" not in sr
            for sr in step_results.values()
        ),
        "data": step_results,
        "_steps": step_meta,
    }


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
