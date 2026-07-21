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


def _day_names_between(start, end) -> dict[str, str]:
    """{"2026-07-18": "Saturday", ...} for every date the window touches."""
    import datetime as _dt

    out: dict[str, str] = {}
    day = start.date()
    last = end.date()
    while day <= last:
        out[day.isoformat()] = day.strftime("%A")
        day += _dt.timedelta(days=1)
    return out


@register("norm", "resolve_dates")
def _resolve_dates(params: dict, db: Session, thread_id: str | None) -> dict:
    """Resolve natural language time references into exact ISO 8601 timestamps.

    Uses a fast Haiku LLM call with Python-computed date context so the LLM
    doesn't have to figure out day-of-week math itself.
    """
    import datetime as _dt
    import json as _json

    from app.config import settings
    from app.interpreter.llm_interpreter import call_llm

    from types import SimpleNamespace

    from app.services import business_calendar as _bc

    query = params.get("query", "").strip()
    explicit_start = params.get("start")
    explicit_end = params.get("end")
    if not query and not (explicit_start and explicit_end):
        return {
            "success": False,
            "data": {},
            "error": "query is required (or an explicit start and end)",
        }

    # Whose calendar applies. A venue_id gets that venue's real day start and
    # timezone; otherwise fall back to the caller's timezone and the configured
    # default day start — never to another venue's settings.
    venue = None
    venue_id = params.get("venue_id")
    if not venue_id and thread_id:
        # Fall back to the thread's venue. Note this is set when the thread is
        # created and never refreshed, so it can be stale if the user switched
        # venue mid-conversation — a useful default, not a guarantee. An
        # explicit venue_id in params always wins.
        from app.db.models import Thread

        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        venue_id = thread.venue_id if thread else None
    if venue_id:
        from app.db.models import Venue

        venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if venue is None:
        venue = SimpleNamespace(
            timezone=params.get("timezone") or settings.SCHEDULER_TIMEZONE,
            day_start_time=None,
        )

    tz = _bc.timezone_for(venue)
    tz_name = str(tz)
    day_start = _bc.day_start_for(venue)

    # An explicit range is honoured verbatim — never snapped. Someone
    # reconciling against a bank statement legitimately wants civil days, and
    # overriding them would just be a different way of returning a wrong
    # number. What we DO say is whether the range lines up with the venue's
    # trading day, so a caller can tell an informed override from a mistake.
    if explicit_start and explicit_end:
        try:
            window = _bc.custom_window(
                venue,
                _dt.datetime.fromisoformat(str(explicit_start)),
                _dt.datetime.fromisoformat(str(explicit_end)),
            )
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "data": {},
                "error": f"start and end must be ISO 8601 datetimes: {exc}",
            }
        return {
            "success": True,
            "data": {
                "periods": [
                    {
                        "label": window.label,
                        "start": window.start.isoformat(),
                        "end": window.end.isoformat(),
                    }
                ],
                "timezone": tz_name,
                "date_reference": _day_names_between(window.start, window.end),
                "window": window.as_dict(),
            },
        }

    # The common vocabulary resolves in Python. It used to go to Haiku with the
    # business rules as prose, which meant the trading-day boundary was decided
    # by a model, per call, and could not be tested. Only genuinely fuzzy
    # phrases ("the week before the long weekend") still need the LLM.
    window = _bc.resolve_phrase(venue, query)
    if window is not None:
        return {
            "success": True,
            "data": {
                "periods": [
                    {
                        "label": window.label,
                        "start": window.start.isoformat(),
                        "end": window.end.isoformat(),
                    }
                ],
                "timezone": tz_name,
                "date_reference": _day_names_between(window.start, window.end),
                "window": window.as_dict(),
            },
        }

    now = _dt.datetime.now(tz)
    day_name = now.strftime("%A")  # e.g. "Thursday"
    date_str = now.strftime("%Y-%m-%d")  # e.g. "2026-03-26"

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

Today is {day_name} {date_str} (timezone: {tz_name}).

Reference dates (computed, use these as anchors):
- This Monday: {this_monday}
- Last Monday: {last_monday}
- First of this month: {first_of_month}
- First of last month: {first_of_last_month}

Business rules (this venue's configured trading day — do not assume midnight):
- A business day runs from {day_start} to one second before {day_start} the next day.
- A business week runs from {day_start} Monday to one second before {day_start} the following Monday.
- "Last week" means the most recent completed business week.
- "This week" means the current business week, starting this Monday at {day_start}.

Convert the user's time reference into exact ISO 8601 periods.
Return ONLY valid JSON — no markdown, no explanation:
{{"periods": [{{"label": "Mon 16 Mar", "start": "2026-03-16T07:00:00", "end": "2026-03-23T06:59:59"}}]}}

Rules:
- Return LOCAL wall-clock times with NO timezone offset and no "Z" suffix.
  Norm attaches the correct offset itself, because the right offset depends on
  the date being resolved (daylight saving), not on today's date.
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

        # Attach the timezone offset HERE, per period, rather than telling the
        # model which offset to use.
        #
        # The prompt used to carry today's UTC offset and instruct the model to
        # stamp it on every timestamp. That is wrong for any date on the other
        # side of a daylight-saving boundary: asked in July (NZST, +12:00) for a
        # week in October (NZDT, +13:00), it returned 07:00+12:00 — 08:00 local,
        # an hour past the trading-day start, for the whole NZ summer. Silent,
        # and the same failure shape as computing a civil day instead of a
        # trading day.
        #
        # ZoneInfo derives the offset from the local time itself, so replacing
        # tzinfo on a naive wall-clock value is correct on both sides of a
        # transition. A value the model still returns with an offset is honoured
        # as given rather than second-guessed.
        for p in periods:
            try:
                for key in ("start", "end"):
                    parsed_dt = _dt.datetime.fromisoformat(str(p[key]))
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=tz)
                    p[key] = parsed_dt.isoformat()
                start = _dt.datetime.fromisoformat(p["start"])
                # Override the model's label with a Python-computed one.
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
            # TypeError belongs here too: the loop above leaves a period it
            # could not parse exactly as the model sent it, so `start` may not
            # be a string at all. Skipping that period costs its day names;
            # letting the error escape costs the whole reply, including the
            # periods that resolved perfectly.
            except (KeyError, ValueError, TypeError):
                pass

        # Describe a single resolved period as a window, exactly as the
        # deterministic path does.
        #
        # Every `*_for_period` tool reads `window` and refuses the call when it
        # is absent — so without this, a phrase the Python vocabulary doesn't
        # know ("the week before the long weekend", "week beginning 13 July")
        # resolved perfectly here and then failed downstream with "could not
        # resolve that to a date range". The date WAS resolved; only the shape
        # was missing. Tools advertise "a period in plain English", so the two
        # resolver paths must answer in the same shape.
        #
        # kind="custom" is the honest label — an LLM-resolved range is not a
        # named business period. Alignment is not asserted either: Window
        # derives `trading_aligned` from the start time against the venue's
        # day_start, so a model that returns midnight is caught by the same
        # confirmation gate that guards an explicit start/end. Recurring
        # queries resolve to many periods and get no window: there is no one
        # range to hand a single-window caller, and the refusal is then true.
        data: dict = {
            "periods": periods,
            "timezone": tz_name,
            "date_reference": date_ref,
        }
        if len(periods) == 1:
            try:
                data["window"] = _bc.Window(
                    start=_dt.datetime.fromisoformat(periods[0]["start"]),
                    end=_dt.datetime.fromisoformat(periods[0]["end"]),
                    kind="custom",
                    label=periods[0].get("label") or query,
                    timezone=tz_name,
                    day_start=day_start,
                ).as_dict()
            except (KeyError, ValueError, TypeError) as ve:
                # The period survives even if it cannot be described: this is
                # an addition to the reply, and a caller reading `periods` must
                # not start failing because the extra key could not be built.
                logger.warning("Could not build window from %s – %s", periods[0], ve)

        return {"success": True, "data": data}
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
    from app.services.task_scheduler import apply_schedule

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

    # Refresh next_run_at from the new status/schedule before persisting.
    apply_schedule(task)
    db.flush()

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

    # Inject the caller's per-workflow run mode (approve_all / approve_fixes /
    # autopilot) resolved from the thread's user. The consolidator reads it
    # from params like dry_run; "unset" ⇒ the safest behaviour + an ask.
    action = config.get("action")
    if action and "mode" not in input_params:
        from app.services.workflow_modes import WORKFLOW_KEYS, user_mode

        if action in WORKFLOW_KEYS and thread_id:
            from app.db.models import Thread, User

            thread = db.query(Thread).filter(Thread.id == thread_id).first()
            user = (
                db.query(User).filter(User.id == thread.user_id).first()
                if thread and thread.user_id
                else None
            )
            input_params = {
                **input_params,
                "mode": (user_mode(user, action) if user else None) or "unset",
            }

    from app.connectors.function_executor import execute_function

    result = execute_function(
        function_code, input_params, db, thread_id, options=config
    )

    # Stamp the venue_id onto the result so the tool loop can set tc.venue_id
    # (interactive cards need it to write back). Prefer the venue named in the
    # tool params; otherwise fall back to the thread's active venue.
    data = result.get("data")
    if isinstance(data, dict) and not data.get("venue_id"):
        from app.db.models import Thread, Venue

        venue_id = None
        venue_name = input_params.get("venue")
        if venue_name:
            venue_obj = (
                db.query(Venue).filter(Venue.name.ilike(f"%{venue_name}%")).first()
            )
            venue_id = venue_obj.id if venue_obj else None
        if not venue_id and thread_id:
            thread = db.query(Thread).filter(Thread.id == thread_id).first()
            venue_id = thread.venue_id if thread else None
        if venue_id:
            data["venue_id"] = venue_id

    return result


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


def _user_for_thread(thread_id: str | None, db: Session):
    from app.db.models import Thread, User

    if not thread_id:
        return None
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread or not thread.user_id:
        return None
    return db.query(User).filter(User.id == thread.user_id).first()


@register("norm", "get_workflow_mode")
def _get_workflow_mode(params: dict, db: Session, thread_id: str | None) -> dict:
    """Return the caller's run mode for a workflow (or 'unset')."""
    from app.services.workflow_modes import WORKFLOW_KEYS, user_mode

    workflow = params.get("workflow", "")
    if workflow not in WORKFLOW_KEYS:
        return {"success": False, "data": {}, "error": f"unknown workflow: {workflow}"}
    user = _user_for_thread(thread_id, db)
    return {
        "success": True,
        "data": {"workflow": workflow, "mode": user_mode(user, workflow) or "unset"},
    }


@register("norm", "set_workflow_mode")
def _set_workflow_mode(params: dict, db: Session, thread_id: str | None) -> dict:
    """Set the caller's run mode for a workflow."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.workflow_modes import MODE_IDS, WORKFLOW_KEYS

    workflow = params.get("workflow", "")
    mode = params.get("mode", "")
    if workflow not in WORKFLOW_KEYS:
        return {"success": False, "data": {}, "error": f"unknown workflow: {workflow}"}
    if mode not in MODE_IDS:
        return {"success": False, "data": {}, "error": f"unknown mode: {mode}"}
    user = _user_for_thread(thread_id, db)
    if not user:
        return {"success": False, "data": {}, "error": "no user for this conversation"}
    modes = dict(user.workflow_modes or {})
    modes[workflow] = mode
    user.workflow_modes = modes
    flag_modified(user, "workflow_modes")
    db.flush()
    return {"success": True, "data": {"workflow": workflow, "mode": mode}}


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


@register("norm", "delegate_to_agent")
def _delegate_to_agent(params: dict, db: Session, thread_id: str | None) -> dict:
    """Ask another Norm agent a question and get its answer back.

    The sub-agent runs read-only in its own thread with its own prompt and no
    sight of this conversation, and only its final answer comes back — see
    app/services/delegation.py for why it is shaped that way.

    Guard failures (unknown target, loop, depth, budget) come back as a normal
    error string rather than an exception: the calling model should read it and
    answer with what it has, not have its turn die.
    """
    from app.db.engine import _ConfigSessionLocal
    from app.db.models import Thread
    from app.services.delegation import DelegationError, delegate

    target = (params.get("target") or "").strip()
    question = (params.get("question") or "").strip()
    context = params.get("context")

    if not target or not question:
        return {
            "success": False,
            "data": {},
            "error": "target and question are both required",
        }
    if not thread_id:
        return {
            "success": False,
            "data": {},
            "error": "Delegation needs a thread to hang the sub-run from.",
        }

    parent = db.query(Thread).filter(Thread.id == thread_id).first()
    if not parent:
        return {"success": False, "data": {}, "error": "Parent thread not found"}

    # Its own config session, as the other handlers here do — the tool-loop
    # dispatch doesn't pass one through (InternalHandler is (params, db, tid)).
    config_db = _ConfigSessionLocal()
    try:
        result = delegate(parent, target, question, context, db, config_db)
    except DelegationError as exc:
        return {"success": False, "data": {}, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a failed consult must not kill the turn
        logger.exception("Delegation to %s failed", target)
        return {
            "success": False,
            "data": {},
            "error": f"Could not consult {target}: {exc}",
        }
    finally:
        config_db.close()

    return {"success": True, "data": result, "error": None}


def _principal_for_memory(thread_id: str | None, db: Session):
    """(user_id, organization_id) for the thread, or (None, None).

    Memory is scoped to a person and an organisation; without both we cannot
    store or recall safely, so the tools no-op rather than guessing.
    """
    from app.db.models import OrganizationMembership, Thread

    if not thread_id:
        return None, None
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread or not thread.user_id:
        return None, None
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == thread.user_id)
        .first()
    )
    return thread.user_id, (membership.organization_id if membership else None)


@register("norm", "remember")
def _remember(params: dict, db: Session, thread_id: str | None) -> dict:
    """Store something durable about this user or organisation.

    Admission control runs server-side (app.services.memory_rules), so a
    refusal here is authoritative — it names the rule and where the fact
    belongs instead. Do not rephrase and retry a refused memory.
    """
    from app.services.memory_service import remember

    user_id, org_id = _principal_for_memory(thread_id, db)
    if not user_id or not org_id:
        return {
            "success": False,
            "data": {},
            "error": "No user/organisation context for this conversation.",
        }

    result = remember(
        db,
        user_id=user_id,
        organization_id=org_id,
        memory_type=(params.get("type") or "").strip(),
        title=(params.get("title") or "").strip(),
        body=(params.get("body") or "").strip(),
        why=params.get("why"),
        how_to_apply=params.get("how_to_apply"),
        thread_id=thread_id,
        trigger=params.get("trigger") or "explicit",
        requested_scope=params.get("scope"),
        venue_id=params.get("venue_id"),
    )
    if not result.get("stored"):
        return {"success": False, "data": result, "error": result.get("reason")}
    return {"success": True, "data": result}


@register("norm", "recall_memory")
def _recall_memory(params: dict, db: Session, thread_id: str | None) -> dict:
    """Fetch the full detail of one memory listed in the index."""
    from app.services.memory_service import get_memory

    _user_id, org_id = _principal_for_memory(thread_id, db)
    memory_id = (params.get("memory_id") or "").strip()
    if not org_id or not memory_id:
        return {"success": False, "data": {}, "error": "memory_id is required"}

    memory = get_memory(db, memory_id, org_id)
    if not memory:
        return {"success": False, "data": {}, "error": f"No memory {memory_id}"}

    from app.db.models import _now

    memory.last_used_at = _now()
    db.flush()
    return {
        "success": True,
        "data": {
            "id": memory.id,
            "type": memory.type,
            "scope": memory.scope,
            "title": memory.title,
            "body": memory.body,
            "why": memory.why,
            "how_to_apply": memory.how_to_apply,
            "recorded": memory.created_at.isoformat() if memory.created_at else None,
            "note": (
                "This was true when recorded. Verify anything that names a "
                "venue, tool or field before acting on it."
            ),
        },
    }
