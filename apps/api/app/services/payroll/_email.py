"""The one way a payroll email leaves: ``send_system_email`` with the plan's
recipient rule (D25, DESIGN-CONTRACT §5).

* The payroll contact is the entity's ``payroll_contact_email`` override,
  else the organisation's ``payroll_contact_email``.
* In shadow mode EVERY payroll email goes to that address, whoever it was
  meant for (a sick-leave note to a venue manager included) — nobody outside
  payroll hears from Norm until the module is live.
* No address at all → nothing is sent and an ``email_logs`` row with status
  ``suppressed`` records that it would have been, so the Shadow tab can show
  it.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import EmailLog, Organization
from app.services.finance_core.ctx import FinanceCtx
from app.services.payroll import entities as entities_svc

logger = logging.getLogger(__name__)


def payroll_contact(db: Session, ctx: FinanceCtx, venue_id: str | None) -> str | None:
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    entity = entities_svc.get_or_create(db, ctx, venue_id) if venue_id else None
    return entities_svc.contact_email(org, entity)


def send(
    db: Session,
    ctx: FinanceCtx,
    template: str,
    context: dict,
    *,
    to: str | None,
    venue_id: str | None = None,
) -> dict:
    """Send ``template`` to ``to`` under the recipient rule. Returns
    ``{"status": "sent"|"failed"|"suppressed", "to", "email_log_id"}``."""
    from app.services.email_service import send_system_email

    intended = (to or "").strip() or None
    recipient = intended
    if not ctx.is_live("payroll"):
        recipient = payroll_contact(db, ctx, venue_id)
    if not recipient:
        log = EmailLog(
            organization_id=ctx.org_id,
            venue_id=venue_id,
            sender_type="system",
            sender_email=settings.EMAIL_FROM_ADDRESS,
            to_addresses=[],
            subject=context.get("subject") or template.replace("_", " "),
            template_name=template,
            provider="resend",
            status="suppressed",
            error_message="no payroll contact email for this organisation",
        )
        db.add(log)
        db.flush()
        logger.info(
            "payroll email %s suppressed: no payroll contact (org %s)",
            template,
            ctx.org_id,
        )
        return {"status": "suppressed", "to": None, "email_log_id": log.id}
    context = dict(context)
    if recipient != intended and intended:
        context["redirected_from"] = intended
    log_id = send_system_email(
        template, [recipient], context, db, organization_id=ctx.org_id
    )
    log = db.query(EmailLog).filter(EmailLog.id == log_id).first() if log_id else None
    if log is not None and venue_id and not log.venue_id:
        log.venue_id = venue_id
        db.flush()
    status = log.status if log is not None else "failed"
    return {
        "status": "sent" if status == "sent" else status,
        "to": recipient,
        "email_log_id": log_id,
    }
