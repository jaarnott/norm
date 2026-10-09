"""Email management endpoints — logs, templates, connections, test send.

Two kinds of thing live here. An organisation's own mail — its sent-email
log, retrying a failed send, your own connected mailboxes — is gated on
``email:read`` / ``email:manage``, which Owners and Managers hold. The
platform's mail — the email templates every organisation shares, and test
sends from Norm's own address — is ``admin:system`` only.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Query, Session

from app.db.engine import get_db
from app.db.models import (
    Connection,
    EmailLog,
    EmailTemplate,
    OrganizationMembership,
    Thread,
    User,
    Venue,
)
from app.auth.dependencies import require_permission

router = APIRouter(prefix="/email", tags=["email"])


def _visible_logs(db: Session, user: User, scope: str) -> Query:
    """The email logs this caller may see: all of them for a platform admin,
    otherwise only mail belonging to an organisation where they hold
    ``scope`` — sent by one of its members, or from one of its venues or
    threads (a thread with no venue counts by who started it).

    ``email_logs`` is one table for every organisation, and system mail with
    none of those links — invites and password resets — carries a live
    sign-in link in its body. Unscoped, granting ``email:read`` to owners
    would have let any owner read any user's password-reset link.
    """
    query = db.query(EmailLog)
    if user.role == "admin":
        return query
    org_ids = [
        m.organization_id
        for m in db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .all()
        if m.role_obj and scope in (m.role_obj.permissions or [])
    ]
    members = select(OrganizationMembership.user_id).where(
        OrganizationMembership.organization_id.in_(org_ids)
    )
    venues = select(Venue.id).where(Venue.organization_id.in_(org_ids))
    threads = select(Thread.id).where(
        or_(
            Thread.venue_id.in_(venues),
            and_(Thread.venue_id.is_(None), Thread.user_id.in_(members)),
        )
    )
    return query.filter(
        or_(
            EmailLog.organization_id.in_(org_ids),
            EmailLog.venue_id.in_(venues),
            EmailLog.sender_user_id.in_(members),
            EmailLog.thread_id.in_(threads),
        )
    )


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
    """List recent email logs (the caller's organisation's — see above)."""
    query = _visible_logs(db, user, "email:read").order_by(EmailLog.created_at.desc())
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
    log = _visible_logs(db, user, "email:read").filter(EmailLog.id == log_id).first()
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
    log = _visible_logs(db, user, "email:manage").filter(EmailLog.id == log_id).first()
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
    user: User = Depends(require_permission("admin:system")),
):
    """Update an email template. Platform admin only: the templates are shared
    by every organisation (an invite or password reset renders from them)."""
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
        db.query(Connection)
        .filter(
            Connection.user_id == user.id,
            Connection.connector_name.in_(["gmail", "microsoft_outlook"]),
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
    user: User = Depends(require_permission("admin:system")),
):
    """Send a test email (admin only): it goes out from Norm's own address, to
    any recipient, with caller-supplied template context."""
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
