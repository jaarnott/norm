"""Quotes and portal links, sent through the outbox — and our quote links
recognised in mail sent straight from Gmail.

* **A proposal send** (``send_enquiry_email(mode="proposal")``) freezes the
  proposal as a version (its HTML, CSS and the options on offer —
  ``proposals.freeze_version``), puts the enquiry's quote link into the email
  (``{{quote_link}}`` where the writer put it, else a line after the body),
  and sends it on the enquiry's thread. When the outbox confirms, the
  version is stamped sent and the "sent" fact makes the enquiry quoted.
  ``mode="email"`` is a plain reply — with the pricing warning.
* **Send to client** (``send_event_portal_link``) allows the client's emails
  into the event's portal, emails them the link from the venue mailbox, and
  records "link sent" when the outbox confirms — the event becomes "sent to
  client". (Orbit's button never actually emailed.)
* **Quotes sent from Gmail** — a person pasting the quote link into a reply
  in Gmail, or sending a proposal draft handed over from Norm — are
  recognised by the sync: an outbound message carrying the link is a quote
  sent (``fn_proposal_events`` source ``gmail``), once per message.

A retried send (same idempotency key) returns the first one's outcome — it
never freezes a second version or mints anything again.
"""

from __future__ import annotations

import html as _html
import logging

from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnContactEmail,
    FnOutbox,
    FnProposalEvent,
    FnProposalVersion,
    FnThreadLink,
)
from app.db.models import Venue
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import events as events_svc
from app.services.functions.ctx import FunctionsCtx, require_live, require_staff
from app.services.functions.errors import Invalid
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import mime, outbox, pricing
from app.services.functions.mailbox.display import looks_like_html, text_to_html
from app.services.functions.mailbox.gmail_client import (
    GmailError,
    GmailRateLimited,
    MailboxAuthError,
)

logger = logging.getLogger(__name__)

QUOTE_PLACEHOLDER = "{{quote_link}}"
PORTAL_PLACEHOLDER = "{{portal_link}}"


def _body(body_html: str | None) -> str:
    body = body_html or ""
    return body if looks_like_html(body) else text_to_html(body)


def insert_link(body_html: str | None, url: str, placeholder: str, line: str) -> str:
    """The link where the writer put ``placeholder``, else ``line`` + link
    after the body."""
    body = _body(body_html)
    anchor = f'<a href="{_html.escape(url)}">{_html.escape(url)}</a>'
    if placeholder in body:
        return body.replace(placeholder, anchor)
    return f"{body}<p>{_html.escape(line)}<br>{anchor}</p>"


def _route(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str | None,
    account_id: str | None,
    gmail_thread_id: str | None,
    link_filter,
):
    """``(mailbox, thread or None)`` for a send about a record: the thread
    named, else the record's newest linked thread whose mailbox is connected,
    else a new conversation from the named mailbox or the venue's."""
    account = accounts_svc.get_account(db, ctx, account_id) if account_id else None
    if gmail_thread_id:
        if account is None:
            raise Invalid("Which mailbox is that conversation in?")
        return account, gmail_thread_id
    links = (
        db.query(FnThreadLink)
        .filter(
            FnThreadLink.organization_id == ctx.org_id,
            link_filter,
            FnThreadLink.unlinked_at.is_(None),
        )
        .order_by(FnThreadLink.linked_at.desc())
        .all()
    )
    for link in links:
        if account is not None and link.account_id != account.id:
            continue
        try:
            candidate = accounts_svc.get_account(db, ctx, link.account_id)
        except Exception:  # noqa: BLE001 — a mailbox at a venue the caller can't see
            continue
        if accounts_svc.is_connected(candidate):
            return candidate, link.gmail_thread_id
    if account is not None:
        return account, None
    found = accounts_svc.connected_account_for_venue(db, ctx, venue_id)
    if found is None:
        name = (
            db.query(Venue.name).filter(Venue.id == venue_id).scalar()
            if venue_id
            else None
        )
        raise accounts_svc.MailboxUnavailable(
            f"{name or 'This venue'} has no connected mailbox to send from — "
            "connect it in the Inbox first."
        )
    return found, None


def _contact_email(db: Session, ctx: FunctionsCtx, contact_id: str) -> list[str]:
    row = (
        db.query(FnContactEmail.email)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == contact_id,
        )
        .order_by(FnContactEmail.is_primary.desc(), FnContactEmail.created_at)
        .first()
    )
    return [row[0]] if row else []


def send_enquiry_email(
    db: Session,
    ctx: FunctionsCtx,
    enquiry_id: str,
    *,
    mode: str,
    body_html: str,
    subject: str | None = None,
    to=None,
    cc=None,
    account_id: str | None = None,
    gmail_thread_id: str | None = None,
    idempotency_key: str,
    price_override_reason: str | None = None,
    email_draft_id: str | None = None,
) -> dict:
    """Email the enquiry's customer: ``mode`` ``proposal`` (freeze a version,
    carry the quote link — makes it quoted) or ``email`` (a plain reply —
    makes it replied; pricing warning applies). Refused in shadow mode."""
    from app.services.functions import proposals as proposals_svc
    from app.services.functions import public_tokens

    require_live(ctx)
    require_staff(ctx)
    if mode not in ("email", "proposal"):
        raise Invalid("Mode must be 'email' or 'proposal'.")
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id)
    previous = outbox.existing_send(db, ctx, idempotency_key, enquiry_id=enquiry.id)
    if previous is not None:
        return {**previous.as_dict(), "mode": mode}
    account, thread_id = _route(
        db,
        ctx,
        venue_id=enquiry.venue_id,
        account_id=account_id,
        gmail_thread_id=gmail_thread_id,
        link_filter=FnThreadLink.enquiry_id == enquiry.id,
    )
    accounts_svc.require_sendable(db, ctx, account)
    recipients = to or (
        [] if thread_id else _contact_email(db, ctx, enquiry.contact_id)
    )
    if not thread_id and not recipients:
        # Checked before anything is frozen or minted.
        raise Invalid("Who is this to? The enquiry's contact has no email address.")
    quote_url = version_id = override = None
    if mode == "proposal":
        enquiries_svc.require_open_enquiry(db, ctx, enquiry)
        version = proposals_svc.freeze_version(db, ctx, enquiry.id)
        version_id = version.id
        quote_url = public_tokens.mint_quote_link(db, ctx, enquiry.id)
        body = insert_link(
            body_html,
            quote_url,
            QUOTE_PLACEHOLDER,
            "View your proposal and confirm here:",
        )
        purpose = "proposal"
    else:
        override = pricing.check(db, ctx, enquiry.id, body_html, price_override_reason)
        body = _body(body_html)
        purpose = "reply" if thread_id else "compose"
    result = outbox.send(
        db,
        ctx,
        account.id,
        to=recipients,
        cc=cc or [],
        subject=subject or (None if thread_id else (enquiry.subject or "Your enquiry")),
        body_html=body,
        thread_id=thread_id,
        purpose=purpose,
        refs={
            "enquiry_id": enquiry.id,
            "proposal_version_id": version_id,
            "email_draft_id": email_draft_id,
        },
        idempotency_key=idempotency_key,
        price_override_reason=override,
    )
    return {
        **result.as_dict(),
        "mode": mode,
        "quote_url": quote_url,
        "proposal_version_id": version_id,
        "account_id": account.id,
    }


def send_event_portal_link(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    *,
    to=None,
    cc=None,
    subject: str | None = None,
    body_html: str | None = None,
    account_id: str | None = None,
    gmail_thread_id: str | None = None,
    idempotency_key: str,
) -> dict:
    """ "Send to client": allow ``to`` (default: the event's contact) into the
    portal and email them its link from the venue mailbox. The event is
    "sent to client" once the outbox confirms. Refused in shadow mode."""
    from app.services.functions import public_tokens

    require_live(ctx)
    require_staff(ctx)
    event = events_svc.get_event(db, ctx, event_id)
    previous = outbox.existing_send(db, ctx, idempotency_key, event_id=event.id)
    if previous is not None:
        return previous.as_dict()
    recipients = mime.clean_addresses(to, "To") or _contact_email(
        db, ctx, event.contact_id
    )
    if not recipients:
        raise Invalid(
            "Who should get the link? The event's contact has no email address."
        )
    link_filter = FnThreadLink.event_id == event.id
    if event.enquiry_id:
        link_filter = (FnThreadLink.event_id == event.id) | (
            FnThreadLink.enquiry_id == event.enquiry_id
        )
    account, thread_id = _route(
        db,
        ctx,
        venue_id=event.venue_id,
        account_id=account_id,
        gmail_thread_id=gmail_thread_id,
        link_filter=link_filter,
    )
    accounts_svc.require_sendable(db, ctx, account)
    url = public_tokens.mint_portal_link(db, ctx, event.id, emails=recipients)
    venue = (
        db.query(Venue.name).filter(Venue.id == event.venue_id).scalar() or "the venue"
    )
    if body_html:
        body = insert_link(body_html, url, PORTAL_PLACEHOLDER, "Your event details:")
    else:
        body = (
            f"<p>Hi,</p><p>Your event at {_html.escape(venue)} is booked. Please "
            "check the details, add anything we need to know, and confirm "
            "each section here:</p>"
            f'<p><a href="{_html.escape(url)}">{_html.escape(url)}</a></p>'
            "<p>You'll be asked for a one-time code sent to this email address.</p>"
        )
    result = outbox.send(
        db,
        ctx,
        account.id,
        to=recipients,
        cc=cc or [],
        subject=subject
        or (
            None if thread_id else f"{event.name or 'Your event'} — details to confirm"
        ),
        body_html=body,
        thread_id=thread_id,
        purpose="portal_link",
        refs={"event_id": event.id},
        idempotency_key=idempotency_key,
    )
    return {**result.as_dict(), "portal_url": url, "account_id": account.id}


def prepare_proposal_handover(db: Session, ctx: FunctionsCtx, draft) -> str:
    """A proposal draft about to be handed to Gmail: freeze the version the
    customer will see, and put the quote link in the body. The version is
    stamped sent when the sync sees the link go out."""
    from app.services.functions import proposals as proposals_svc
    from app.services.functions import public_tokens

    enquiry = enquiries_svc.get_enquiry(db, ctx, draft.enquiry_id)
    enquiries_svc.require_open_enquiry(db, ctx, enquiry)
    proposals_svc.freeze_version(db, ctx, enquiry.id)
    url = public_tokens.mint_quote_link(db, ctx, enquiry.id)
    return insert_link(
        draft.body_html, url, QUOTE_PLACEHOLDER, "View your proposal and confirm here:"
    )


# ---------------------------------------------------------------------------
# Quote links in mail sent from Gmail (called by the sync)
# ---------------------------------------------------------------------------


def record_gmail_quote_links(account, client, session_factory, metas) -> int:
    """For each outbound message (not one our outbox sent), read its body
    and record a quote sent for every enquiry whose quote link it carries.
    Returns how many facts were written. Gmail trouble on one message skips
    it (the sync goes on); a rate limit or dead grant stops the tick."""
    from app.services.functions import public_tokens
    from app.services.functions.mailbox.sync import _session

    written = 0
    for meta in metas:
        with _session(session_factory) as db:
            ours = (
                db.query(FnOutbox.id)
                .filter(
                    FnOutbox.account_id == account.id,
                    (FnOutbox.gmail_message_id == meta.gmail_message_id)
                    | (FnOutbox.rfc822_message_id == (meta.rfc822_message_id or "-")),
                )
                .first()
            )
            seen = (
                db.query(FnProposalEvent.id)
                .filter(
                    FnProposalEvent.organization_id == account.organization_id,
                    FnProposalEvent.gmail_message_id == meta.gmail_message_id,
                    FnProposalEvent.kind == "sent",
                )
                .first()
            )
        if ours is not None or seen is not None:
            continue
        try:
            full = client.message(meta.gmail_message_id, fmt="full")
        except (GmailRateLimited, MailboxAuthError):
            raise
        except GmailError as exc:
            logger.info("quote-link scan skipped %s: %s", meta.gmail_message_id, exc)
            continue
        parts = mime.bodies(full.get("payload"))
        tokens = public_tokens.tokens_in_text(f"{parts.html}\n{parts.text}", "quote")
        if not tokens:
            continue
        with _session(session_factory) as db:
            for token in tokens:
                row = public_tokens.lookup(db, token, "quote")
                if row is None or row.organization_id != account.organization_id:
                    continue
                dup = (
                    db.query(FnProposalEvent.id)
                    .filter(
                        FnProposalEvent.organization_id == account.organization_id,
                        FnProposalEvent.enquiry_id == row.enquiry_id,
                        FnProposalEvent.gmail_message_id == meta.gmail_message_id,
                        FnProposalEvent.kind == "sent",
                    )
                    .first()
                )
                if dup is not None:
                    continue
                version = (
                    db.query(FnProposalVersion)
                    .filter(
                        FnProposalVersion.organization_id == account.organization_id,
                        FnProposalVersion.enquiry_id == row.enquiry_id,
                        FnProposalVersion.sent_at.is_(None),
                        FnProposalVersion.created_at <= meta.internal_date,
                    )
                    .order_by(FnProposalVersion.version_number.desc())
                    .first()
                )
                if version is not None:
                    # The version frozen for a hand-over: now it was sent.
                    version.sent_at = meta.internal_date
                    version.sent_by_label = version.sent_by_label or "Gmail"
                db.add(
                    FnProposalEvent(
                        organization_id=account.organization_id,
                        enquiry_id=row.enquiry_id,
                        proposal_version_id=version.id if version else None,
                        kind="sent",
                        source="gmail",
                        account_id=account.id,
                        gmail_message_id=meta.gmail_message_id,
                        actor_label="mailbox sync",
                        occurred_at=meta.internal_date,
                    )
                )
                written += 1
    return written
