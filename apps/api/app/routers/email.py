"""Email management endpoints — logs, templates, connections, test send."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import EmailLog, EmailTemplate, ConnectorConfig, User
from app.auth.dependencies import require_permission

router = APIRouter(prefix="/email", tags=["email"])


# ---------------------------------------------------------------------------
# Email Logs
# ---------------------------------------------------------------------------


@router.get("/logs")
async def list_email_logs(
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("email:read")),
):
    """List recent email logs."""
    query = db.query(EmailLog).order_by(EmailLog.created_at.desc())
    if status:
        query = query.filter(EmailLog.status == status)
    logs = query.limit(limit).all()
    return {
        "logs": [
            {
                "id": log.id,
                "sender_type": log.sender_type,
                "sender_email": log.sender_email,
                "to_addresses": log.to_addresses,
                "subject": log.subject,
                "template_name": log.template_name,
                "status": log.status,
                "provider": log.provider,
                "error_message": log.error_message,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            }
            for log in logs
        ]
    }


@router.get("/logs/{log_id}")
async def get_email_log(
    log_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("email:read")),
):
    """Get a single email log with full details."""
    log = db.query(EmailLog).filter(EmailLog.id == log_id).first()
    if not log:
        raise HTTPException(404, "Email log not found")
    return {
        "id": log.id,
        "sender_type": log.sender_type,
        "sender_email": log.sender_email,
        "to_addresses": log.to_addresses,
        "cc_addresses": log.cc_addresses,
        "bcc_addresses": log.bcc_addresses,
        "subject": log.subject,
        "template_name": log.template_name,
        "html_body": log.html_body,
        "status": log.status,
        "provider": log.provider,
        "provider_message_id": log.provider_message_id,
        "error_message": log.error_message,
        "retry_count": log.retry_count,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
    }


@router.post("/retry/{log_id}")
async def retry_email(
    log_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("email:manage")),
):
    """Retry a failed email."""
    log = db.query(EmailLog).filter(EmailLog.id == log_id).first()
    if not log:
        raise HTTPException(404, "Email log not found")
    if log.status != "failed":
        raise HTTPException(400, "Only failed emails can be retried")

    if log.sender_type == "system" and log.template_name:
        log.retry_count += 1
        log.status = "queued"
        db.flush()
        # Re-send using the stored template
        from app.services.email_templates import render_template

        subject, html = render_template(log.template_name, {}, db)
        try:
            import resend
            from app.config import settings

            resend.api_key = settings.RESEND_API_KEY
            result = resend.Emails.send(
                {
                    "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>",
                    "to": log.to_addresses,
                    "subject": log.subject,
                    "html": log.html_body or html,
                }
            )
            log.status = "sent"
            log.provider_message_id = str(
                result.get("id") if isinstance(result, dict) else result
            )
            from datetime import datetime, timezone

            log.sent_at = datetime.now(timezone.utc)
        except Exception as exc:
            log.status = "failed"
            log.error_message = str(exc)
        db.commit()
        return {"status": log.status, "error": log.error_message}

    raise HTTPException(400, "Retry not supported for this email type")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@router.get("/templates")
async def list_templates(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("email:read")),
):
    """List available email templates."""
    templates = (
        db.query(EmailTemplate)
        .order_by(EmailTemplate.category, EmailTemplate.name)
        .all()
    )
    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "category": t.category,
                "subject_template": t.subject_template,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in templates
        ]
    }


class UpdateTemplateBody(BaseModel):
    subject_template: str | None = None
    html_template: str | None = None


@router.put("/templates/{name}")
async def update_template(
    name: str,
    body: UpdateTemplateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("email:manage")),
):
    """Update an email template."""
    tpl = db.query(EmailTemplate).filter(EmailTemplate.name == name).first()
    if not tpl:
        raise HTTPException(404, "Template not found")
    if body.subject_template is not None:
        tpl.subject_template = body.subject_template
    if body.html_template is not None:
        tpl.html_template = body.html_template
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


@router.get("/connections")
async def list_connections(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("email:read")),
):
    """List the current user's connected email accounts."""
    configs = (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.user_id == user.id,
            ConnectorConfig.connector_name.in_(["gmail", "microsoft_outlook"]),
        )
        .all()
    )
    return {
        "connections": [
            {
                "connector_name": c.connector_name,
                "connected": bool(c.access_token),
                "email": c.oauth_metadata.get("email") if c.oauth_metadata else None,
            }
            for c in configs
        ]
    }


# ---------------------------------------------------------------------------
# Test Send
# ---------------------------------------------------------------------------


class TestSendBody(BaseModel):
    to: str
    template_name: str = "task_complete"
    context: dict = {}


@router.post("/send-test")
async def send_test_email(
    body: TestSendBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("email:manage")),
):
    """Send a test email (admin only)."""
    from app.services.email_service import send_system_email

    context = {
        "user_name": user.full_name,
        "org_name": "Test Organization",
        **body.context,
    }
    log_id = send_system_email(body.template_name, [body.to], context, db)
    db.commit()

    log = db.query(EmailLog).filter(EmailLog.id == log_id).first() if log_id else None
    return {
        "status": log.status if log else "failed",
        "email_log_id": log_id,
        "error": log.error_message if log else "Unknown error",
    }
