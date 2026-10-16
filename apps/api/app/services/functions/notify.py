"""Email the Functions public surface sends.

To **staff** when a customer acts: an option confirmed or declined on the
quote page, a portal section confirmed, a client editing after they confirmed,
a web-form lead in live mode. Recipients are the enquiry's owner plus the
venue's ``notify_emails`` (Functions settings). Short and plain, with a link
into Norm.

To **customers**: the portal's one-time code and a colleague's invitation, and
(shadow mode) a web-form submission forwarded to the venue mailbox with
``Reply-To`` set to the customer, as Orbit did.

**Nothing is sent outside production.** The message is logged instead
(``email_logs`` row with status ``suppressed``) — the same rule as app email:
a local database holds real customers, and a dev server must never email one.
Portal codes are the exception to the log: the code is never written down
anywhere (that is the point of hashing it), so outside production the code is
written to the server log for whoever is testing — and in production only
"a code was sent" is logged.

Callers send AFTER they commit the fact the email reports (a failed email must
never undo a customer's confirmation), via ``run_after_commit``.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from email.utils import formataddr
from typing import Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.db.functions_models import FnVenueFunctionSettings
from app.db.models import EmailLog, User, _now

logger = logging.getLogger(__name__)

#: A deferred send: built inside the request, run once its facts are committed.
Notice = Callable[[Session], object]


@dataclass
class Delivery:
    #: sent | suppressed | failed | skipped (no recipients)
    status: str
    provider_id: str | None = None
    error: str | None = None
    log_id: str | None = None


def suppressed() -> bool:
    """True outside production (tests patch this to exercise the send path)."""
    return not settings.is_production


def _resend(payload: dict) -> str | None:
    """The one place that talks to Resend (tests replace it)."""
    import resend

    resend.api_key = settings.RESEND_API_KEY
    result = resend.Emails.send(payload)
    return result.get("id") if isinstance(result, dict) else str(result)


def send_email(
    db: Session | None,
    *,
    to: list[str],
    subject: str,
    html_body: str,
    template: str,
    from_name: str | None = None,
    reply_to: str | None = None,
    org_id: str | None = None,
    venue_id: str | None = None,
    log_body: bool = True,
) -> Delivery:
    """Send (or, outside production, suppress) one email, logging it to
    ``email_logs`` when ``db`` is given. Never raises: a failed email is
    reported in the result and logged, not thrown at the caller."""
    recipients = sorted({a.strip().lower() for a in to if a and a.strip()})
    if not recipients:
        return Delivery("skipped", error="No recipients.")
    # Subjects carry names customers can edit: never let one span lines.
    subject = " ".join(subject.split())[:250]
    log = None
    if db is not None:
        log = EmailLog(
            organization_id=org_id,
            venue_id=venue_id,
            sender_type="system",
            sender_email=settings.EMAIL_FROM_ADDRESS,
            to_addresses=recipients,
            subject=subject,
            template_name=f"functions:{template}",
            html_body=html_body if log_body else None,
            provider="resend",
            status="queued",
        )
        db.add(log)
        db.flush()

    def finish(result: Delivery) -> Delivery:
        if log is not None:
            log.status = result.status
            log.error_message = result.error
            log.provider_message_id = result.provider_id
            if result.status == "sent":
                log.sent_at = _now()
            db.flush()
            result.log_id = log.id
        return result

    if suppressed():
        logger.info(
            "functions email suppressed outside production: %s to %s",
            template,
            recipients,
        )
        return finish(Delivery("suppressed", error="Suppressed outside production."))
    if not settings.RESEND_API_KEY:
        return finish(Delivery("failed", error="RESEND_API_KEY not configured."))
    payload = {
        "from": formataddr(
            (from_name or settings.EMAIL_FROM_NAME, settings.EMAIL_FROM_ADDRESS)
        ),
        "to": recipients,
        "subject": subject,
        "html": html_body,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        provider_id = _resend(payload)
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        logger.warning("functions email failed (%s): %s", template, exc)
        return finish(Delivery("failed", error=str(exc)[:500]))
    return finish(Delivery("sent", provider_id=provider_id))


def run_after_commit(db: Session, notices: list[Notice]) -> None:
    """Send the notices a request collected, then commit their log rows. One
    failing notice never stops the others."""
    for notice in notices:
        try:
            notice(db)
        except Exception:  # noqa: BLE001 — the facts are committed; log and go on
            logger.exception("functions notice failed")
            db.rollback()
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("functions notice log commit failed")
        db.rollback()


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def page(heading: str, paragraphs: list[str], *, button=None, rows=None) -> str:
    """A plain, inline-styled email body. ``paragraphs`` are HTML the caller
    escaped; ``rows`` are ``(label, value)`` pairs (escaped here); ``button``
    is ``(label, url)``."""
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,'
        'sans-serif;color:#222;max-width:560px;margin:0 auto;padding:24px;">',
        f'<h2 style="margin:0 0 16px;font-size:19px;">{_e(heading)}</h2>',
    ]
    parts += [
        f'<p style="margin:0 0 12px;line-height:1.5;">{p}</p>' for p in paragraphs
    ]
    if rows:
        parts.append(
            '<table style="border-collapse:collapse;width:100%;margin:8px 0 16px;">'
        )
        for label, value in rows:
            parts.append(
                '<tr><td style="padding:6px 10px 6px 0;border-bottom:1px solid #eee;'
                f'font-weight:600;vertical-align:top;white-space:nowrap;">{_e(label)}</td>'
                '<td style="padding:6px 0;border-bottom:1px solid #eee;'
                f'white-space:pre-wrap;">{_e(value)}</td></tr>'
            )
        parts.append("</table>")
    if button:
        label, url = button
        parts.append(
            f'<p style="margin:20px 0;"><a href="{_e(url)}" style="background:#49556A;'
            "color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;"
            f'font-weight:600;">{_e(label)}</a></p>'
        )
    parts.append("</div>")
    return "".join(parts)


def staff_link(*, enquiry_id: str | None = None, event_id: str | None = None) -> str:
    """A link into Norm's Functions pages (``?page=`` + the record)."""
    base = settings.app_url.rstrip("/")
    if event_id:
        return f"{base}/app?page=functions-events&event={event_id}"
    if enquiry_id:
        return f"{base}/app?page=functions-enquiries&enquiry={enquiry_id}"
    return f"{base}/app?page=functions-dashboard"


def staff_recipients(
    db: Session, org_id: str, venue_id: str | None, owner_user_id: str | None = None
) -> list[str]:
    """The enquiry owner's address plus the venue's ``notify_emails``."""
    out: list[str] = []
    if owner_user_id:
        row = db.query(User.email).filter(User.id == owner_user_id).first()
        if row and row[0]:
            out.append(row[0])
    if venue_id:
        settings_row = (
            db.query(FnVenueFunctionSettings.notify_emails)
            .filter(
                FnVenueFunctionSettings.organization_id == org_id,
                FnVenueFunctionSettings.venue_id == venue_id,
            )
            .first()
        )
        if settings_row and isinstance(settings_row[0], list):
            out += [a for a in settings_row[0] if isinstance(a, str)]
    return sorted({a.strip().lower() for a in out if a and "@" in a})


def _notify_staff(
    db: Session,
    *,
    org_id: str,
    venue_id: str | None,
    owner_user_id: str | None,
    subject: str,
    body: str,
    template: str,
    reply_to: str | None = None,
) -> Delivery:
    to = staff_recipients(db, org_id, venue_id, owner_user_id)
    if not to:
        logger.info(
            "functions %s: no staff recipients for venue %s", template, venue_id
        )
        return Delivery("skipped", error="No staff recipients.")
    return send_email(
        db,
        to=to,
        subject=subject,
        html_body=body,
        template=template,
        reply_to=reply_to,
        org_id=org_id,
        venue_id=venue_id,
    )


# ---------------------------------------------------------------------------
# To staff
# ---------------------------------------------------------------------------


def option_responded(
    db: Session,
    *,
    org_id: str,
    venue_id: str,
    owner_user_id: str | None,
    enquiry_id: str,
    event_id: str | None,
    kind: str,
    contact_name: str,
    summary_rows: list[tuple[str, str]],
    note: str | None,
) -> Delivery:
    """A customer confirmed (event created) or declined an option on the
    quote page."""
    if kind == "confirm":
        subject = f"Confirmed: {contact_name} booked on the quote page"
        lead = (
            f"<strong>{_e(contact_name)}</strong> confirmed a minimum-spend option on "
            "their quote page. Norm has created the event — send them the event "
            "portal when you're ready."
        )
        button = ("Open the event", staff_link(event_id=event_id))
    else:
        subject = f"Declined: {contact_name} declined an option"
        lead = f"<strong>{_e(contact_name)}</strong> declined an option on their quote page."
        button = ("Open the enquiry", staff_link(enquiry_id=enquiry_id))
    rows = list(summary_rows) + ([("Their note", note)] if note else [])
    return _notify_staff(
        db,
        org_id=org_id,
        venue_id=venue_id,
        owner_user_id=owner_user_id,
        subject=subject,
        body=page(subject, [lead], rows=rows, button=button),
        template=f"quote_{kind}",
    )


def sections_confirmed(
    db: Session,
    *,
    org_id: str,
    venue_id: str,
    owner_user_id: str | None,
    event_id: str,
    event_name: str,
    event_date: str,
    client_email: str,
    sections: list[str],
    all_confirmed: bool,
    note: str | None,
) -> Delivery:
    names = ", ".join(s.capitalize() for s in sections)
    subject = (
        f"Confirmed: {event_name} — all details confirmed"
        if all_confirmed
        else f"{event_name}: client confirmed {names}"
    )
    lead = (
        f"{_e(client_email)} confirmed <strong>{_e(names)}</strong> in the event portal."
        + (" Every section is now confirmed." if all_confirmed else "")
    )
    rows = [("Event", event_name), ("Date", event_date)]
    if note:
        rows.append(("Their note", note))
    return _notify_staff(
        db,
        org_id=org_id,
        venue_id=venue_id,
        owner_user_id=owner_user_id,
        subject=subject,
        body=page(
            subject,
            [lead],
            rows=rows,
            button=("Open the event", staff_link(event_id=event_id)),
        ),
        template="portal_confirmed",
        reply_to=client_email,
    )


def client_edited(
    db: Session,
    *,
    org_id: str,
    venue_id: str,
    owner_user_id: str | None,
    event_id: str,
    event_name: str,
    event_date: str,
    client_email: str,
    changed: list[str],
) -> Delivery:
    """The client changed a confirmed event (the event stays confirmed and is
    flagged "edited since confirmed")."""
    subject = f"{event_name}: changed by the client after confirming"
    lead = (
        f"{_e(client_email)} changed details in the event portal after the event "
        "was confirmed. It is still confirmed, and flagged as edited — the change "
        "log on the event shows exactly what changed."
    )
    rows = [
        ("Event", event_name),
        ("Date", event_date),
        ("Changed", ", ".join(changed)),
    ]
    return _notify_staff(
        db,
        org_id=org_id,
        venue_id=venue_id,
        owner_user_id=owner_user_id,
        subject=subject,
        body=page(
            subject,
            [lead],
            rows=rows,
            button=("Open the event", staff_link(event_id=event_id)),
        ),
        template="portal_edited",
        reply_to=client_email,
    )


def form_lead(
    db: Session,
    *,
    org_id: str,
    venue_id: str,
    form_name: str,
    enquiry_id: str,
    rows: list[tuple[str, str]],
    reply_to: str | None,
    fallback_to: str | None,
) -> Delivery:
    """Live mode: a web form created an enquiry. To the venue's notify list,
    or — when it has none — the form's recipient / venue mailbox."""
    subject = f"New web enquiry — {form_name}"
    body = page(
        subject,
        ["Norm has created an enquiry from this web form submission."],
        rows=rows,
        button=("Open the enquiry", staff_link(enquiry_id=enquiry_id)),
    )
    to = staff_recipients(db, org_id, venue_id) or (
        [fallback_to] if fallback_to else []
    )
    return send_email(
        db,
        to=to,
        subject=subject,
        html_body=body,
        template="form_lead",
        reply_to=reply_to,
        org_id=org_id,
        venue_id=venue_id,
    )


# ---------------------------------------------------------------------------
# To customers and the venue mailbox
# ---------------------------------------------------------------------------


def portal_code(*, to: str, code: str, venue_name: str, event_id: str) -> Delivery:
    """The one-time code. Not logged to ``email_logs`` (it would store the
    code); outside production the code goes to the server log instead."""
    if suppressed():
        logger.warning(
            "DEV ONLY (not emailed outside production) — portal code for %s on "
            "event %s: %s",
            to,
            event_id,
            code,
        )
        return Delivery("suppressed")
    body = page(
        "Your verification code",
        [
            f"Enter this code to open your event details with {_e(venue_name)}:",
            f'<span style="font-size:30px;font-weight:700;letter-spacing:6px;">{_e(code)}</span>',
            "It expires in 10 minutes. If you didn't ask for it, you can ignore this email.",
        ],
    )
    result = send_email(
        None,
        to=[to],
        subject=f"{code} is your code for {venue_name}",
        html_body=body,
        template="portal_code",
        from_name=venue_name,
    )
    logger.info("portal code for event %s: %s", event_id, result.status)
    return result


def portal_invite(
    db: Session,
    *,
    org_id: str,
    venue_id: str,
    to: str,
    inviter_email: str,
    venue_name: str,
    event_name: str,
    url: str,
) -> Delivery:
    subject = f"{inviter_email} invited you to plan {event_name}"
    body = page(
        f"You're invited to {event_name}",
        [
            f"{_e(inviter_email)} has given you access to the event details for "
            f"<strong>{_e(event_name)}</strong> at {_e(venue_name)}.",
            "Open the link and enter this email address — we'll send you a code to sign in.",
        ],
        button=("Open the event", url),
    )
    return send_email(
        db,
        to=[to],
        subject=subject,
        html_body=body,
        template="portal_invite",
        from_name=venue_name,
        org_id=org_id,
        venue_id=venue_id,
        reply_to=inviter_email,
    )


def form_submission(
    db: Session,
    *,
    org_id: str,
    venue_id: str,
    to: str | None,
    subject: str,
    form_name: str,
    ref_code: str,
    rows: list[tuple[str, str]],
    reply_to: str | None,
) -> Delivery:
    """Shadow mode: the submission, emailed to the venue mailbox with
    Reply-To = the customer (Orbit's behaviour — a reply goes straight to
    them)."""
    body = page(
        f"New submission — {form_name}",
        [
            f"Ref {_e(ref_code)} — sent from the web form <strong>{_e(form_name)}</strong>."
        ],
        rows=rows,
    )
    return send_email(
        db,
        to=[to] if to else [],
        subject=subject,
        html_body=body,
        template="form_submission",
        reply_to=reply_to,
        org_id=org_id,
        venue_id=venue_id,
    )
