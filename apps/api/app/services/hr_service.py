"""HR setup service backed by Postgres."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Task, Message, HrSetup, Approval, IntegrationRun
from app.connectors.base import BaseConnector
from app.connectors.registry import get_connector, resolve_connector
from app.services.integration_service import execute_submission, execute_submission_v2


def create_employee_setup(
    db: Session,
    message: str,
    employee_name: str | None,
    venue: dict | None,
    role: str | None,
    start_date: str | None,
    user_id: str | None = None,
    intent: str = "hr.employee_setup",
    extracted_extra: dict | None = None,
) -> dict:
    core_missing = _calc_core_missing(employee_name, venue, role, start_date)
    has_basics = bool(venue and (employee_name or role))

    status = "awaiting_approval" if (has_basics and not core_missing) else "awaiting_user_input"
    question = _hr_question(core_missing) if core_missing else None

    extracted = {}
    if employee_name:
        extracted["employee_name"] = employee_name
    if venue:
        extracted["venue"] = venue
    if role:
        extracted["role"] = role
    if start_date:
        extracted["start_date"] = start_date
    # Merge in extra fields from dynamic prompt (e.g. _action, _connector)
    if extracted_extra:
        extracted.update(extracted_extra)

    task = Task(
        user_id=user_id,
        intent=intent,
        domain="hr",
        status=status,
        raw_prompt=message,
        extracted_fields=extracted,
        missing_fields=core_missing + ["email", "phone", "employment_type"],
        clarification_question=question,
    )
    db.add(task)
    db.flush()

    db.add(Message(task_id=task.id, role="user", content=message))
    if question:
        db.add(Message(task_id=task.id, role="assistant", content=question))

    hr = HrSetup(
        task_id=task.id,
        employee_name=employee_name,
        role=role,
        venue_id=venue["id"] if venue else None,
        start_date=start_date,
        status=status,
    )
    db.add(hr)

    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


def update_employee_setup(
    db: Session,
    task_id: str,
    employee_name: str | None,
    venue: dict | None,
    role: str | None,
    start_date: str | None,
) -> dict | None:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return None

    extracted = dict(task.extracted_fields or {})
    revisions: list[str] = []

    # Name: overwrite if provided and different
    if employee_name:
        old = extracted.get("employee_name")
        if old and old != employee_name:
            revisions.append(f"Name changed from {old} to {employee_name}")
        elif not old:
            revisions.append(f"Name set to {employee_name}")
        extracted["employee_name"] = employee_name

    # Venue: overwrite if provided and different
    if venue:
        old_venue = extracted.get("venue")
        if old_venue and old_venue.get("id") != venue["id"]:
            revisions.append(f"Venue changed from {old_venue['name']} to {venue['name']}")
        elif not old_venue:
            revisions.append(f"Venue set to {venue['name']}")
        extracted["venue"] = venue

    # Role: overwrite if provided and different
    if role:
        old = extracted.get("role")
        if old and old != role:
            revisions.append(f"Role changed from {old} to {role}")
        elif not old:
            revisions.append(f"Role set to {role}")
        extracted["role"] = role

    # Start date: overwrite if provided and different
    if start_date:
        old = extracted.get("start_date")
        if old and old != start_date:
            revisions.append(f"Start date changed from {old} to {start_date}")
        elif not old:
            revisions.append(f"Start date set to {start_date}")
        extracted["start_date"] = start_date

    task.extracted_fields = extracted

    core_missing = _calc_core_missing(
        extracted.get("employee_name"),
        extracted.get("venue"),
        extracted.get("role"),
        extracted.get("start_date"),
    )
    task.missing_fields = core_missing + ["email", "phone", "employment_type"]

    # Build assistant message
    if revisions:
        revision_text = ". ".join(revisions) + "."
        if not core_missing:
            assistant_msg = f"{revision_text} Employee setup updated and ready for approval."
        else:
            q = _hr_question(core_missing)
            assistant_msg = f"{revision_text} {q}"
    elif not core_missing:
        assistant_msg = "Thanks. Employee setup is ready for your approval."
    else:
        assistant_msg = _hr_question(core_missing)

    if not core_missing:
        task.status = "awaiting_approval"
        task.clarification_question = None
    else:
        task.clarification_question = _hr_question(core_missing)

    db.add(Message(task_id=task.id, role="assistant", content=assistant_msg))
    task.updated_at = datetime.now(timezone.utc)

    # Update HR record
    hr = db.query(HrSetup).filter(HrSetup.task_id == task_id).first()
    if hr:
        hr.employee_name = extracted.get("employee_name")
        hr.role = extracted.get("role")
        hr.start_date = extracted.get("start_date")
        v = extracted.get("venue")
        if v:
            hr.venue_id = v["id"]
        hr.status = task.status

    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


def get_task(db: Session, task_id: str) -> dict | None:
    task = db.query(Task).filter(Task.id == task_id, Task.domain == "hr").first()
    if not task:
        return None
    return _task_to_dict(task)


def approve_task(db: Session, task_id: str, user=None) -> dict | None:
    result = _set_status(db, task_id, "approved")
    if result:
        db.add(Approval(
            task_id=task_id,
            action="approved",
            performed_by=user.email if user else "system",
            user_id=user.id if user else None,
        ))
        db.commit()
    return result


def reject_task(db: Session, task_id: str, user=None) -> dict | None:
    result = _set_status(db, task_id, "rejected")
    if result:
        db.add(Approval(
            task_id=task_id,
            action="rejected",
            performed_by=user.email if user else "system",
            user_id=user.id if user else None,
        ))
        db.commit()
    return result


def submit_task(db: Session, task_id: str) -> dict | None:
    task = db.query(Task).filter(Task.id == task_id, Task.domain == "hr").first()
    if not task or task.status != "approved":
        return None

    action = (task.extracted_fields or {}).get("_action", "create_employee")
    resolved = resolve_connector("hr", action, db)
    if isinstance(resolved, BaseConnector):
        run = execute_submission(db, task, resolved)
    else:
        spec, creds, operation = resolved
        run = execute_submission_v2(db, task, spec, creds, operation)

    if run.status == "success":
        task.status = "submitted"
        hr = db.query(HrSetup).filter(HrSetup.task_id == task_id).first()
        if hr:
            hr.status = "submitted"
    else:
        task.status = "approved"  # keep approved so user can retry

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


def list_tasks(db: Session, user_id: str | None = None) -> list[dict]:
    q = db.query(Task).filter(Task.domain == "hr")
    if user_id:
        q = q.filter(Task.user_id == user_id)
    tasks = q.order_by(Task.created_at.desc()).all()
    return [_task_to_dict(t) for t in tasks]


def find_open_task(db: Session, user_id: str | None = None) -> dict | None:
    q = db.query(Task).filter(
        Task.domain == "hr",
        Task.status.in_(["awaiting_user_input", "awaiting_approval"]),
    )
    if user_id:
        q = q.filter(Task.user_id == user_id)
    task = q.order_by(Task.created_at.desc()).first()
    if not task:
        return None
    return _task_to_dict(task)


def _set_status(db: Session, task_id: str, status: str) -> dict | None:
    task = db.query(Task).filter(Task.id == task_id, Task.domain == "hr").first()
    if not task:
        return None
    task.status = status
    task.updated_at = datetime.now(timezone.utc)
    hr = db.query(HrSetup).filter(HrSetup.task_id == task_id).first()
    if hr:
        hr.status = status
    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


# -- helpers --

def _calc_core_missing(employee_name, venue, role, start_date) -> list[str]:
    m = []
    if not employee_name:
        m.append("employee_name")
    if not venue:
        m.append("venue")
    if not role:
        m.append("role")
    if not start_date:
        m.append("start_date")
    return m


def _hr_question(missing: list[str]) -> str:
    labels = {
        "employee_name": "the employee's name",
        "venue": "which venue",
        "role": "what role",
        "start_date": "when they start",
    }
    parts = [labels.get(f, f) for f in missing]
    joined = " and ".join(parts)
    return f"I still need {joined}. Can you provide that?"


def _build_checklist(extracted: dict) -> list[dict]:
    return [
        {"item": "Employee name", "done": bool(extracted.get("employee_name"))},
        {"item": "Venue assignment", "done": bool(extracted.get("venue"))},
        {"item": "Role", "done": bool(extracted.get("role"))},
        {"item": "Start date", "done": bool(extracted.get("start_date"))},
        {"item": "Email address", "done": False},
        {"item": "Phone number", "done": False},
        {"item": "Employment type", "done": False},
        {"item": "Payroll setup", "done": False},
        {"item": "System access", "done": False},
    ]


def _task_to_dict(task: Task) -> dict:
    extracted = task.extracted_fields or {}
    venue = extracted.get("venue")

    # Latest integration run
    integration_run = None
    if task.integration_runs:
        latest_run = task.integration_runs[-1]
        integration_run = {
            "connector": latest_run.connector_name,
            "status": latest_run.status,
            "reference": (latest_run.response_payload or {}).get("employee_id"),
            "submitted_at": latest_run.created_at.isoformat() if latest_run.created_at else None,
            "error": latest_run.error_message,
        }

    # Latest approval
    approval = None
    if task.approvals:
        latest_approval = task.approvals[-1]
        approval = {
            "action": latest_approval.action,
            "performed_by": latest_approval.performed_by or "system",
            "performed_at": latest_approval.performed_at.isoformat() if latest_approval.performed_at else None,
        }

    return {
        "id": task.id,
        "domain": "hr",
        "intent": task.intent,
        "message": task.raw_prompt,
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "employee_name": extracted.get("employee_name"),
        "venue": {"id": venue["id"], "name": venue["name"]} if venue else None,
        "role": extracted.get("role"),
        "start_date": extracted.get("start_date"),
        "missing_fields": task.missing_fields or [],
        "checklist": _build_checklist(extracted),
        "clarification_question": task.clarification_question,
        "conversation": [
            {"role": m.role, "text": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in sorted(task.messages, key=lambda x: x.created_at)
        ],
        "integration_run": integration_run,
        "approval": approval,
        "llm_calls": [
            {
                "id": c.id,
                "call_type": c.call_type,
                "model": c.model,
                "system_prompt": c.system_prompt,
                "user_prompt": c.user_prompt,
                "raw_response": c.raw_response,
                "parsed_response": c.parsed_response,
                "status": c.status,
                "error_message": c.error_message,
                "duration_ms": c.duration_ms,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in task.llm_calls
        ],
    }
