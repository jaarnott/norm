"""Email sending service — system emails via Resend, send-on-behalf via Gmail/Outlook."""

import base64
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText

import httpx
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def send_system_email(
    template_name: str,
    to: list[str],
    context: dict,
    db: Session,
    organization_id: str | None = None,
    task_id: str | None = None,
) -> str | None:
    """Send a system email via Resend. Returns the email log ID or None on failure."""
    from app.db.models import EmailLog
    from app.services.email_templates import render_template

    subject, html_body = render_template(template_name, context, db)

    log = EmailLog(
        organization_id=organization_id,
        task_id=task_id,
        sender_type="system",
        sender_email=settings.EMAIL_FROM_ADDRESS,
        to_addresses=to,
        subject=subject,
        template_name=template_name,
        html_body=html_body,
        provider="resend",
        status="queued",
    )
    db.add(log)
    db.flush()

    if not settings.RESEND_API_KEY:
        log.status = "failed"
        log.error_message = "RESEND_API_KEY not configured"
        db.flush()
        logger.warning("Cannot send email — RESEND_API_KEY not set")
        return log.id

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        result = resend.Emails.send(
            {
                "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>",
                "to": to,
                "subject": subject,
                "html": html_body,
            }
        )
        log.status = "sent"
        log.provider_message_id = (
            result.get("id") if isinstance(result, dict) else str(result)
        )
        log.sent_at = datetime.now(timezone.utc)
        logger.info("System email sent: %s to %s", template_name, to)
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        logger.exception("Failed to send system email: %s", template_name)

    db.flush()
    return log.id


def send_on_behalf_gmail(
    user_id: str,
    to: list[str],
    subject: str,
    html_body: str,
    db: Session,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    organization_id: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Send an email from a user's Gmail account via the Gmail API."""
    from app.db.models import ConnectorConfig, EmailLog, User
    from app.services.oauth_service import get_valid_access_token

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"success": False, "error": "User not found"}

    # Find the user's Gmail connector config
    config = (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == "gmail",
            ConnectorConfig.user_id == user_id,
        )
        .first()
    )
    if not config or not config.access_token:
        return {
            "success": False,
            "error": "Gmail not connected. Please connect your Gmail account in Settings.",
        }

    # Get a valid access token (auto-refreshes if expired)
    access_token = get_valid_access_token(db, config)
    if not access_token:
        return {
            "success": False,
            "error": "Gmail token expired. Please reconnect your Gmail account.",
        }

    # Build MIME message
    msg = MIMEText(html_body, "html")
    msg["to"] = ", ".join(to)
    msg["from"] = user.email
    msg["subject"] = subject
    if cc:
        msg["cc"] = ", ".join(cc)
    if bcc:
        msg["bcc"] = ", ".join(bcc)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    # Log the email
    log = EmailLog(
        organization_id=organization_id,
        task_id=task_id,
        sender_type="on_behalf",
        sender_email=user.email,
        sender_user_id=user_id,
        to_addresses=to,
        cc_addresses=cc,
        bcc_addresses=bcc,
        subject=subject,
        html_body=html_body,
        provider="gmail",
        status="queued",
    )
    db.add(log)
    db.flush()

    try:
        resp = httpx.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw},
            timeout=30.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            log.status = "sent"
            log.provider_message_id = data.get("id")
            log.sent_at = datetime.now(timezone.utc)
            logger.info("Gmail email sent on behalf of %s to %s", user.email, to)
        else:
            log.status = "failed"
            log.error_message = (
                f"Gmail API returned {resp.status_code}: {resp.text[:500]}"
            )
            logger.error("Gmail send failed: %s", log.error_message)
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        logger.exception("Gmail send error")

    db.flush()
    return {
        "success": log.status == "sent",
        "data": {"email_log_id": log.id, "message_id": log.provider_message_id},
        "error": log.error_message,
    }


def send_on_behalf_outlook(
    user_id: str,
    to: list[str],
    subject: str,
    html_body: str,
    db: Session,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    organization_id: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Send an email from a user's Outlook account via Microsoft Graph API."""
    from app.db.models import ConnectorConfig, EmailLog, User
    from app.services.oauth_service import get_valid_access_token

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"success": False, "error": "User not found"}

    config = (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == "microsoft_outlook",
            ConnectorConfig.user_id == user_id,
        )
        .first()
    )
    if not config or not config.access_token:
        return {
            "success": False,
            "error": "Outlook not connected. Please connect your Outlook account in Settings.",
        }

    access_token = get_valid_access_token(db, config)
    if not access_token:
        return {
            "success": False,
            "error": "Outlook token expired. Please reconnect your Outlook account.",
        }

    # Build Microsoft Graph mail payload
    mail_body = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
        }
    }
    if cc:
        mail_body["message"]["ccRecipients"] = [
            {"emailAddress": {"address": addr}} for addr in cc
        ]
    if bcc:
        mail_body["message"]["bccRecipients"] = [
            {"emailAddress": {"address": addr}} for addr in bcc
        ]

    log = EmailLog(
        organization_id=organization_id,
        task_id=task_id,
        sender_type="on_behalf",
        sender_email=user.email,
        sender_user_id=user_id,
        to_addresses=to,
        cc_addresses=cc,
        bcc_addresses=bcc,
        subject=subject,
        html_body=html_body,
        provider="microsoft_graph",
        status="queued",
    )
    db.add(log)
    db.flush()

    try:
        resp = httpx.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=mail_body,
            timeout=30.0,
        )
        if resp.status_code == 202:
            log.status = "sent"
            log.sent_at = datetime.now(timezone.utc)
            logger.info("Outlook email sent on behalf of %s to %s", user.email, to)
        else:
            log.status = "failed"
            log.error_message = (
                f"Graph API returned {resp.status_code}: {resp.text[:500]}"
            )
            logger.error("Outlook send failed: %s", log.error_message)
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        logger.exception("Outlook send error")

    db.flush()
    return {
        "success": log.status == "sent",
        "data": {"email_log_id": log.id},
        "error": log.error_message,
    }
