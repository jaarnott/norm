"""Every email the entertainers module sends — one sender, one record.

* **One path.** ``deliver`` renders the template (``app/templates/email/
  <kind>.html`` + ``<kind>_subject.txt``), sends through
  ``email_service.send_system_email`` (which writes the ``email_logs`` row:
  recipients, subject, the full HTML as sent, the provider's message id —
  where the delivery webhook later lands ``delivered`` / ``bounced``), and
  adds the ``ent_email_links`` row the Emails page reads (D32).
* **Idempotent per kind, target and send number.** The n-th send of a kind
  for a performance (or performer, or band member) carries
  ``idempotency_key = ent:<kind>:<target>:<n>`` on its log row; a retried job
  that finds the key already sent does not send again.
* **Shadow routes everything to the payroll contact.** Until
  ``entertainers_mode`` is ``live``, no performer, band member or manager is
  emailed: the message goes to the organisation's ``payroll_contact_email``
  with a note saying who it was for, and with no contact set it is logged
  ``suppressed`` and not sent (DESIGN-CONTRACT §5).
* **From Norm, in the organisation's name.** No sender of its own (D26,
  settled 23 Oct 2026): Norm's platform address (``EMAIL_FROM_ADDRESS``)
  with the organisation's name as the display name — how app and Functions
  emails name the business — and ``Reply-To`` the organisation's
  ``payroll_contact_email`` when set, so a performer's reply reaches a
  person, not the no-reply box.
* **Nothing is sent outside production.** The row is logged ``suppressed``
  (``functions/notify.py``'s rule): a local database holds real performers.
  The one-time code is never written down anywhere — its log row keeps no
  body, and outside production the code goes to the server log for whoever
  is testing.
* **Send after the facts are committed.** A service calls ``later(db, fn)``;
  inside a router's ``collecting()`` block the sends wait until the request
  has committed (``run_pending``), so a refused write never emails anyone.
  Outside such a block (jobs, tests) ``later`` sends at once.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import logging
from contextlib import contextmanager
from typing import Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.db.entertainers_models import EntEmailLink
from app.db.models import EmailLog, Organization, _now
from app.services.entertainers import email_links
from app.services.finance_core.errors import flush

logger = logging.getLogger(__name__)

#: kind -> (recipient_kind, carries money)
KINDS: dict[str, tuple[str, bool]] = {
    "ent_invite": ("performer", False),
    "ent_invite_reminder": ("performer", False),
    "ent_band_invite": ("band_member", False),
    "ent_band_reminder": ("band_member", False),
    "ent_code": ("performer", False),
    "ent_details_changed": ("performer", False),
    "ent_declaration_request": ("performer", False),
    "ent_confirm_request": ("performer", False),
    "ent_confirm_reminder": ("performer", False),
    "ent_mismatch": ("performer", False),
    "ent_approved_amount": ("performer", True),
    "ent_invoice_query": ("performer", True),
    "ent_manager_approval": ("closing_manager", False),
    "ent_manager_digest": ("venue_manager", False),
}
MONEY_KINDS = frozenset(k for k, (_, money) in KINDS.items() if money)
#: Kinds the Emails page may resend, and the service that does it.
RESENDABLE = frozenset(
    {"ent_invite", "ent_band_invite", "ent_confirm_request", "ent_manager_approval"}
)
APP_SLUG = "entertainers"

Notice = Callable[[Session], object]
_PENDING: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "ent_pending_emails", default=None
)


# ---------------------------------------------------------------------------
# Deferral
# ---------------------------------------------------------------------------


@contextmanager
def collecting():
    """Collect the sends the block's services ask for; the caller runs them
    with ``run_pending`` once its transaction has committed."""
    token = _PENDING.set([])
    try:
        yield _PENDING.get()
    finally:
        _PENDING.reset(token)


def later(db: Session, fn: Notice) -> None:
    """Send ``fn`` after the current request commits — or now, when nothing
    is collecting (a job, a test)."""
    pending = _PENDING.get()
    if pending is None:
        fn(db)
    else:
        pending.append(fn)


def run_pending(db: Session, pending: list[Notice]) -> None:
    """Run the collected sends, then commit their log rows. One failing
    notice never stops the others, and never undoes the committed facts."""
    for fn in pending:
        try:
            fn(db)
            db.commit()
        except Exception:  # noqa: BLE001 — logged; the facts are already committed
            logger.exception("entertainers email failed")
            db.rollback()


# ---------------------------------------------------------------------------
# The sender
# ---------------------------------------------------------------------------


def suppressed() -> bool:
    """True outside production (tests patch this to exercise the send path)."""
    return not settings.is_production


def sender(org: Organization) -> tuple[str, str | None]:
    """``(display name, Reply-To)`` for the organisation's emails: its name
    on Norm's address, replies to its payroll contact (None: no Reply-To)."""
    name = " ".join(str(getattr(org, "name", None) or "").split()) or "Norm"
    reply = (getattr(org, "payroll_contact_email", None) or "").strip() or None
    return name, reply


def first_name(name: str | None) -> str:
    return (name or "there").strip().split(" ")[0] or "there"


def _org(db: Session, org_id: str) -> Organization | None:
    return db.query(Organization).filter(Organization.id == org_id).first()


def _already_sent(db: Session, org_id: str, key: str) -> EmailLog | None:
    return (
        db.query(EmailLog)
        .filter(
            EmailLog.organization_id == org_id,
            EmailLog.app_slug == APP_SLUG,
            EmailLog.idempotency_key == key,
            EmailLog.status.in_(("sending", "sent", "suppressed", "delivered")),
        )
        .first()
    )


def deliver(
    db: Session,
    *,
    org_id: str,
    kind: str,
    to: str | None,
    context: dict,
    performer_id: str | None = None,
    band_member_id: str | None = None,
    venue_id: str | None = None,
    performance_ids: list[str] | None = None,
    target_id: str | None = None,
    now: dt.datetime | None = None,
) -> EntEmailLink | None:
    """Send one email of ``kind`` and record it. Returns the
    ``ent_email_links`` row, or None when nothing was recorded (no address,
    or this send number already went out)."""
    if kind not in KINDS:
        raise ValueError(f"Unknown entertainer email kind {kind!r}")
    recipient_kind, _money = KINDS[kind]
    org = _org(db, org_id)
    if org is None:
        return None
    now = now or _now()
    address = (to or "").strip().lower() or None
    target = (
        target_id or (performance_ids or [None])[0] or performer_id or band_member_id
    )
    n = email_links.send_count(db, org_id, kind, target_id=target) + 1
    key = f"ent:{kind}:{target}:{n}"
    if _already_sent(db, org_id, key) is not None:
        return None

    shadow = getattr(org, "entertainers_mode", "off") != "live"
    intended = address
    reason: str | None = None
    if shadow:
        contact = (getattr(org, "payroll_contact_email", None) or "").strip().lower()
        if contact:
            address = contact
        else:
            address = None
            reason = "no payroll contact to redirect to in shadow mode"
    if not address and reason is None:
        reason = "no recipient address"
    status = "suppressed" if reason else None
    ctx = {
        **context,
        "org_name": org.name,
        "year": now.year,
        "shadow_original_to": intended if shadow and intended != address else None,
    }

    from app.services.email_templates import render_template

    log: EmailLog | None = None
    if status == "suppressed" or suppressed():
        subject, html = render_template(kind, ctx, db)
        log = EmailLog(
            organization_id=org_id,
            venue_id=venue_id,
            sender_type="system",
            sender_email=settings.EMAIL_FROM_ADDRESS,
            to_addresses=[address or intended or ""],
            subject=subject,
            template_name=kind,
            html_body=None if kind == "ent_code" else html,
            provider="resend",
            status="suppressed",
            error_message=reason or "suppressed outside production",
            app_slug=APP_SLUG,
            idempotency_key=key,
        )
        db.add(log)
        db.flush()
        if kind == "ent_code":
            logger.info(
                "entertainer code (not sent, %s): %s for link %s",
                log.error_message,
                context.get("code"),
                target,
            )
    else:
        from app.services.email_service import send_system_email

        from_name, reply_to = sender(org)
        log_id = send_system_email(
            kind,
            [address],
            ctx,
            db,
            organization_id=org_id,
            from_name=from_name,
            reply_to=reply_to,
        )
        log = (
            db.query(EmailLog).filter(EmailLog.id == log_id).first() if log_id else None
        )
        if log is not None:
            log.app_slug = APP_SLUG
            log.idempotency_key = key
            log.venue_id = venue_id
            if kind == "ent_code":
                # The code is never written down: not even in the sent copy.
                log.html_body = None
            flush(db)
    if log is not None and kind == "ent_code" and context.get("code"):
        # Nor in the stored subject, whatever a template (a DB-stored one
        # included) put there: the Emails page shows subjects to anyone
        # with venue access (security review, 20 Oct 2026).
        log.subject = (log.subject or "").replace(str(context["code"]), "••••••")
        flush(db)
    if log is None:
        return None
    return email_links.record(
        db,
        log,
        org_id=org_id,
        kind=kind,
        recipient_kind=recipient_kind,
        recipient_email=intended,
        performer_id=performer_id,
        band_member_id=band_member_id,
        venue_id=venue_id,
        performance_ids=performance_ids,
        sent_at=now,
    )


# ---------------------------------------------------------------------------
# Wording helpers the senders share
# ---------------------------------------------------------------------------


def date_text(day: dt.date) -> str:
    return day.strftime("%A %-d %B %Y")


def time_text(start: dt.time | None, end: dt.time | None) -> str:
    if start is None or end is None:
        return ""
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def money_text(cents: int | None, currency: str = "NZD") -> str:
    if cents is None:
        return "—"
    symbol = "$" if currency in ("NZD", "AUD", "USD") else f"{currency} "
    return f"{symbol}{cents / 100:,.2f}"
