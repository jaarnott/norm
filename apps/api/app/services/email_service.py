"""Email sending service — system emails via Resend, send-on-behalf via Gmail/Outlook."""

import base64
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr

import httpx
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def _access_token_for(connector_name: str, db: Session, user_id: str) -> str | None:
    """Return a valid access token for a per-user email connector, refreshing if due.

    ``get_valid_access_token`` needs the ConnectionSpec (for oauth_config) from the
    **config** database, while the tokens live in the per-environment database —
    so this opens the config session the rest of this module doesn't need.

    Email connectors are scoped per user, not per venue (see the
    ``category == "email"`` branch in ``routers/oauth.py``), hence ``user_id``.
    """
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services.oauth_service import get_valid_access_token

    config_db = _ConfigSessionLocal()
    try:
        spec = (
            config_db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == connector_name)
            .first()
        )
        if not spec:
            logger.warning("No connector spec found for %s", connector_name)
            return None
        return get_valid_access_token(spec, db, user_id=user_id)
    finally:
        config_db.close()


def send_system_email(
    template_name: str,
    to: list[str],
    context: dict,
    db: Session,
    organization_id: str | None = None,
    thread_id: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
    log_body: bool = True,
) -> str | None:
    """Send a system email via Resend. Returns the email log ID or None on failure.

    Always from Norm's own address (``EMAIL_FROM_ADDRESS``); ``from_name``
    replaces the display name (an organisation's emails carry its name, as
    app and Functions emails do) and ``reply_to`` sends a reply to a person
    rather than the no-reply address.

    ``log_body=False`` leaves ``EmailLog.html_body`` empty: for a sign-in code,
    whose body would otherwise sit in the log in clear.
    """
    from app.db.models import EmailLog
    from app.services.email_templates import render_template

    subject, html_body = render_template(template_name, context, db)

    log = EmailLog(
        organization_id=organization_id,
        thread_id=thread_id,
        sender_type="system",
        sender_email=settings.EMAIL_FROM_ADDRESS,
        to_addresses=to,
        subject=subject,
        template_name=template_name,
        html_body=html_body if log_body else "",
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
        payload = {
            # formataddr quotes a name with a comma or quote in it.
            "from": formataddr(
                (from_name or settings.EMAIL_FROM_NAME, settings.EMAIL_FROM_ADDRESS)
            ),
            "to": to,
            "subject": subject,
            "html": html_body,
        }
        if reply_to:
            payload["reply_to"] = reply_to
        result = resend.Emails.send(payload)
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


#: Delivery states a provider's webhook may move a log row to (the row's
#: ``status`` after the send: sent → delivered | bounced | complained).
DELIVERY_STATUSES = ("delivered", "bounced", "complained")


def update_delivery_status(
    db: Session,
    provider_message_id: str | None,
    status: str,
    *,
    detail: str | None = None,
):
    """Record what the provider's webhook says happened to an email Norm
    sent (``POST /api/public/email-events/resend``): the ``email_logs`` row
    with that ``provider_message_id`` moves to ``delivered``, ``bounced`` or
    ``complained``. Returns the row, or None when no such email was sent —
    an id Norm never saw is ignored, not an error. ``bounced`` and
    ``complained`` keep the provider's reason in ``error_message``; a
    ``delivered`` never overwrites a later bounce."""
    from app.db.models import EmailLog

    if status not in DELIVERY_STATUSES:
        raise ValueError(f"unknown delivery status {status!r}")
    if not provider_message_id:
        return None
    log = (
        db.query(EmailLog)
        .filter(EmailLog.provider_message_id == str(provider_message_id))
        .first()
    )
    if log is None:
        return None
    if status == "delivered" and log.status in ("bounced", "complained"):
        return log
    log.status = status
    if status != "delivered":
        log.error_message = (detail or status)[:500]
    db.flush()
    return log


def send_on_behalf_gmail(
    user_id: str,
    to: list[str],
    subject: str,
    html_body: str,
    db: Session,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    organization_id: str | None = None,
    thread_id: str | None = None,
) -> dict:
    """Send an email from a user's Gmail account via the Gmail API."""
    from app.db.models import Connection, EmailLog, User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"success": False, "error": "User not found"}

    # Find the user's Gmail connector config
    config = (
        db.query(Connection)
        .filter(
            Connection.connector_name == "gmail",
            Connection.user_id == user_id,
        )
        .first()
    )
    if not config or not config.access_token:
        return {
            "success": False,
            "error": "Gmail not connected. Please connect your Gmail account in Settings.",
        }

    # Get a valid access token (auto-refreshes if expired)
    try:
        access_token = _access_token_for("gmail", db, user_id)
    except Exception as exc:
        logger.warning("Gmail token retrieval failed for user %s: %s", user_id, exc)
        access_token = None
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
        thread_id=thread_id,
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
    thread_id: str | None = None,
) -> dict:
    """Send an email from a user's Outlook account via Microsoft Graph API."""
    from app.db.models import Connection, EmailLog, User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"success": False, "error": "User not found"}

    config = (
        db.query(Connection)
        .filter(
            Connection.connector_name == "microsoft_outlook",
            Connection.user_id == user_id,
        )
        .first()
    )
    if not config or not config.access_token:
        return {
            "success": False,
            "error": "Outlook not connected. Please connect your Outlook account in Settings.",
        }

    try:
        access_token = _access_token_for("microsoft_outlook", db, user_id)
    except Exception as exc:
        logger.warning("Outlook token retrieval failed for user %s: %s", user_id, exc)
        access_token = None
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
        thread_id=thread_id,
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
