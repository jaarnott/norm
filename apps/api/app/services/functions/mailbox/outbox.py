"""Sends through a venue mailbox — written down before Gmail hears of them.

``send`` writes an ``fn_outbox`` row (the caller's idempotency key, our own
RFC 822 Message-ID, status ``sending``) and COMMITS it before calling Gmail.
Then, in one transaction, the row is marked ``sent``, the sent message is
written to the mirror (so the enquiry is "replied" at once, not a minute
later), and the send's facts are recorded: a proposal version's ``sent_at``
and its ``fn_proposal_events`` row, an event's "link sent", a Norm draft's
"sent", and the thread linked to the enquiry or event it was about.

What that buys:

* **A double click or a retry is one email.** The key is unique per
  organisation; a second call with it returns the first send's outcome (or
  "still sending") instead of sending again.
* **A lost response is recoverable.** If Gmail times out or the process
  dies after sending, the row stays ``sending``; the reconciler (run by the
  sync, per mailbox) searches the mailbox for our Message-ID
  (``rfc822msgid:``) and either marks it sent — facts and all — or, once it
  is clearly not there, marks it ``failed`` so a person can retry. A retry of
  a failed row reuses the same Message-ID, so even a send that did slip out
  is collapsed by mail systems rather than delivered twice.
* **It fails closed.** Shadow mode, a mailbox that needs reconnecting, a
  grant Google refuses: nothing is sent, and the caller is told why.

How a message looks — "<First> from <Venue>", the mailbox's signature after
the body, Gmail-style quoted history on replies, threading headers — is
decided here once, for staff, the agent and the proposal path alike.
"""

from __future__ import annotations

import datetime as dt
import html as _html
import logging
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnEmailDraft,
    FnMailboxAccount,
    FnOutbox,
    FnProposalVersion,
    FnThreadLink,
)
from app.db.models import Venue, _now
from app.services.functions._rows import load, require_text
from app.services.functions.ctx import FunctionsCtx, require_staff
from app.services.functions.errors import (
    Conflict,
    FunctionsError,
    Invalid,
    NotFound,
    NotLive,
)
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import gmail_client, mime, mirror
from app.services.functions.mailbox.classify import our_addresses
from app.services.functions.mailbox.display import (
    clean_outbound_html,
    html_to_text,
    looks_like_html,
    safe_html,
    text_to_html,
)
from app.services.functions.mailbox.gmail_client import (
    GmailError,
    GmailNotFound,
    GmailTransient,
    MailboxAuthError,
)
from app.services.functions.mailbox.mirror import AccountRef

logger = logging.getLogger(__name__)

#: A ``sending`` row older than this is looked for in the mailbox.
STUCK = dt.timedelta(minutes=2)
#: …and one still not found after this is marked failed (safe to retry).
GIVE_UP = dt.timedelta(minutes=10)
MAX_RECIPIENTS = 50
#: Purposes that answer the customer on a thread (and link it).
_CUSTOMER_PURPOSES = ("reply", "compose", "proposal", "portal_link")


class SendFailed(FunctionsError):
    """Gmail refused the message; nothing went out. Retrying with the same
    idempotency key tries again."""

    status_code = 502
    code = "send_failed"


@dataclass
class OutboxResult:
    outbox_id: str
    status: str
    rfc822_message_id: str
    gmail_message_id: str | None = None
    gmail_thread_id: str | None = None
    duplicate: bool = False
    error: str | None = None

    @classmethod
    def of(cls, row: FnOutbox, *, duplicate: bool = False) -> "OutboxResult":
        return cls(
            outbox_id=row.id,
            status=row.status,
            rfc822_message_id=row.rfc822_message_id,
            gmail_message_id=row.gmail_message_id,
            gmail_thread_id=row.gmail_thread_id,
            duplicate=duplicate,
            error=row.error,
        )

    def as_dict(self) -> dict:
        return {
            "outbox_id": self.outbox_id,
            "status": self.status,
            "rfc822_message_id": self.rfc822_message_id,
            "gmail_message_id": self.gmail_message_id,
            "gmail_thread_id": self.gmail_thread_id,
            "duplicate": self.duplicate,
            "error": self.error,
        }


@dataclass
class Composed:
    """A message ready for Gmail — and everything the outbox row records."""

    raw: str
    rfc822_message_id: str
    gmail_thread_id: str | None
    in_reply_to: str | None
    references: str | None
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body_html: str
    from_name: str | None
    #: The message answered or forwarded (None for a new email).
    target_message_id: str | None = None


def sender_name(actor_name: str | None, venue_name: str | None, display_name=None):
    """Outbound mail is from "<first name> from <venue>" — the person, not
    the mailbox (Orbit's convention, which customers already see)."""
    first = (actor_name or "").strip().split(" ")[0] if actor_name else ""
    venue = (venue_name or "").strip()
    if first and venue:
        return f"{first} from {venue}"
    return first or display_name or venue or None


def _actor_name(ctx: FunctionsCtx) -> str | None:
    user = getattr(ctx, "user", None)
    return getattr(user, "full_name", None) if user is not None else None


def _venue_name(db: Session, venue_id: str | None) -> str | None:
    if not venue_id:
        return None
    return db.query(Venue.name).filter(Venue.id == venue_id).scalar()


def _target(client, thread_id: str, message_id: str | None) -> dict | None:
    """The message being answered (or forwarded): the one named, else the
    thread's last real (non-draft) message."""
    if message_id:
        try:
            return client.message(message_id, fmt="full")
        except GmailNotFound:
            raise NotFound("That message is no longer in the mailbox.")
    try:
        thread = client.thread(thread_id, fmt="full")
    except GmailNotFound:
        raise NotFound("That conversation is no longer in the mailbox.")
    real = [
        m
        for m in thread.get("messages") or []
        if "DRAFT" not in (m.get("labelIds") or [])
    ]
    return real[-1] if real else None


def _valid_addresses(values, drop: set[str]) -> list[str]:
    """Addresses taken from a message's headers: real addresses only, each
    once, never the mailbox itself (or whoever ``drop`` names)."""
    out: list[str] = []
    for value in values:
        if mime.is_email(value) and value not in drop and value not in out:
            out.append(value)
    return out


def reply_recipients(target: dict, mailbox: str) -> tuple[list[str], list[str]]:
    """``(to, everyone else)`` for answering ``target``.

    Whose message it was is decided by its From and Gmail's SENT label —
    NEVER by Reply-To, which anyone can set: an inbound message with
    ``Reply-To: <this mailbox>`` must not be treated as our own and answered
    to its To list. Our own message is answered to its To list; anyone
    else's (and ours when addressed only to ourselves, like a web-form
    notification) to its usable Reply-To addresses other than this mailbox,
    else its From. Every derived list is cleaned and never contains the
    mailbox."""
    mailbox = (mailbox or "").strip().lower()
    headers = mime.headers_map(target.get("payload"))

    def addresses(name: str) -> list[str]:
        return [a for _, a in mime.parse_address_list(headers.get(name))]

    _, sender = mime.parse_address(headers.get("from"))
    tos, ccs = addresses("to"), addresses("cc")
    ours = sender == mailbox or "SENT" in (target.get("labelIds") or [])
    to = _valid_addresses(tos, {mailbox}) if ours else []
    if not to:  # theirs — or ours but addressed only to ourselves (a web form)
        to = _valid_addresses(addresses("reply-to"), {mailbox}) or _valid_addresses(
            [sender], {mailbox}
        )
    return to, _valid_addresses(tos + ccs, {mailbox, *to})


def _quoted(target: dict, *, forward: bool) -> str:
    headers = mime.headers_map(target.get("payload"))
    parts = mime.bodies(target.get("payload"))
    body = safe_html(parts.html) if parts.html else text_to_html(parts.text or "")
    who = _html.escape(headers.get("from", ""))
    when = _html.escape(headers.get("date", ""))
    if forward:
        lines = [
            "---------- Forwarded message ---------",
            f"From: {who}",
            f"Date: {when}",
            f"Subject: {_html.escape(headers.get('subject', ''))}",
            f"To: {_html.escape(headers.get('to', ''))}",
        ]
        return (
            '<div class="gmail_quote">' + "<br>".join(lines) + f"<br><br>{body}</div>"
        )
    attribution = f"On {when}, {who} wrote:" if when and who else f"{who} wrote:"
    return (
        f'<div class="gmail_quote"><div>{attribution}</div>'
        '<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;'
        f'padding-left:1ex">{body}</blockquote></div>'
    )


def compose(
    *,
    account: AccountRef,
    client,
    purpose: str,
    to,
    cc=(),
    bcc=(),
    subject: str | None,
    body_html: str,
    thread_id: str | None,
    in_reply_to: str | None,
    quote: bool,
    actor_name: str | None,
    venue_name: str | None,
    message_id: str | None = None,
) -> Composed:
    """Build the message: body, signature, quoted history, threading. Reads
    the thread from Gmail for replies and forwards (no DB access here)."""
    body = (
        clean_outbound_html(body_html)
        if looks_like_html(body_html)
        else text_to_html(body_html)
    )
    if not html_to_text(body).strip():
        raise Invalid("The message is empty.")
    to_list = mime.clean_addresses(to, "To")
    cc_list = mime.clean_addresses(cc, "Cc")
    bcc_list = mime.clean_addresses(bcc, "Bcc")
    references = header_in_reply_to = None
    send_thread = target_id = None
    quote_html = ""
    subj = mime.one_line(subject)

    if thread_id and purpose in ("reply", "proposal", "portal_link"):
        target = _target(client, thread_id, in_reply_to)
        if target is not None:
            target_id = target.get("id")
            headers = mime.headers_map(target.get("payload"))
            mid = (headers.get("message-id") or "").strip()
            header_in_reply_to = mid or None
            references = (
                " ".join(
                    x for x in [(headers.get("references") or "").strip(), mid] if x
                )
                or None
            )
            if not to_list:
                to_list, _ = reply_recipients(target, account.google_email)
            subj = subj or mime.reply_subject(headers.get("subject"))
            if quote:
                quote_html = _quoted(target, forward=False)
        send_thread = thread_id
    elif thread_id and purpose == "forward":
        target = _target(client, thread_id, in_reply_to)
        if target is not None:
            target_id = target.get("id")
            headers = mime.headers_map(target.get("payload"))
            subj = subj or mime.forward_subject(headers.get("subject"))
            quote_html = _quoted(target, forward=True)
    if not to_list:
        raise Invalid("Who is this to? Add at least one recipient.")
    if len(to_list) + len(cc_list) + len(bcc_list) > MAX_RECIPIENTS:
        raise Invalid(f"At most {MAX_RECIPIENTS} recipients per email.")
    if not subj:
        raise Invalid("The email needs a subject.")

    full = f"<div>{body}</div>"
    if account.signature_html:
        full += f'<br><div class="norm-signature">{account.signature_html}</div>'
    if quote_html:
        full += f"<br>{quote_html}"
    from_name = sender_name(actor_name, venue_name, account.display_name)
    message_id = message_id or mime.new_message_id(account.google_email)
    raw = mime.b64url_encode(
        mime.build_message(
            from_email=account.google_email,
            from_name=from_name,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            subject=subj,
            html=full,
            message_id=message_id,
            in_reply_to=header_in_reply_to,
            references=references,
        )
    )
    return Composed(
        raw=raw,
        rfc822_message_id=message_id,
        gmail_thread_id=send_thread,
        in_reply_to=header_in_reply_to,
        references=references,
        to=to_list,
        cc=cc_list,
        bcc=bcc_list,
        subject=subj,
        body_html=full,
        from_name=from_name,
        target_message_id=target_id,
    )


def compose_error(exc: GmailError) -> FunctionsError:
    """A Gmail failure while preparing a message (nothing was sent), as the
    domain error callers handle."""
    if isinstance(exc, MailboxAuthError):
        return accounts_svc.MailboxUnavailable(
            "The mailbox needs reconnecting — nothing was sent. " + exc.message
        )
    if isinstance(exc, GmailTransient):
        return SendFailed("Gmail is busy or unreachable — nothing was sent. Try again.")
    return SendFailed(f"Gmail refused: {exc.message}")


def _rebuild(row: FnOutbox, account: AccountRef, from_name: str | None) -> str:
    """The stored send as a message again (a retry of a failed row — same
    Message-ID, same body)."""
    return mime.b64url_encode(
        mime.build_message(
            from_email=account.google_email,
            from_name=from_name,
            to=list(row.to_emails or []),
            cc=list(row.cc_emails or []),
            bcc=list(row.bcc_emails or []),
            subject=row.subject,
            html=row.body_html,
            message_id=row.rfc822_message_id,
            in_reply_to=row.in_reply_to,
            references=row.references_header,
        )
    )


def _by_key(db: Session, org_id: str, key: str) -> FnOutbox | None:
    return (
        db.query(FnOutbox)
        .filter(FnOutbox.organization_id == org_id, FnOutbox.idempotency_key == key)
        .populate_existing()
        .first()
    )


def existing_send(
    db: Session,
    ctx: FunctionsCtx,
    key: str,
    *,
    account_id: str | None = None,
    enquiry_id: str | None = None,
    event_id: str | None = None,
) -> OutboxResult | None:
    """The send an idempotency key already made, reported (or retried) — but
    only to a caller who can see its mailbox, and only for the same mailbox
    and record. Keys are unique per organisation, so without this a key
    reused from another venue would report (or re-send) that venue's
    email; it is a Conflict instead, and says nothing about the other send."""
    row = _by_key(db, ctx.org_id, (key or "").strip()[:200])
    if row is None:
        return None
    taken = Conflict("That idempotency key was already used for a different email.")
    try:
        accounts_svc.get_account(db, ctx, row.account_id)
    except NotFound:
        raise taken
    for wanted, actual in (
        (account_id, row.account_id),
        (enquiry_id, row.enquiry_id),
        (event_id, row.event_id),
    ):
        if wanted is not None and wanted != actual:
            raise taken
    return _resume(db, ctx, row)


def _validate_refs(db: Session, ctx: FunctionsCtx, refs: dict) -> dict:
    from app.services.functions import enquiries as enquiries_svc
    from app.services.functions import events as events_svc

    allowed = {"enquiry_id", "event_id", "proposal_version_id", "email_draft_id"}
    unknown = set(refs) - allowed
    if unknown:
        raise Invalid(f"Unknown references: {', '.join(sorted(unknown))}.")
    out = {k: v for k, v in refs.items() if v}
    if out.get("enquiry_id"):
        enquiries_svc.get_enquiry(db, ctx, out["enquiry_id"])
    if out.get("event_id"):
        events_svc.get_event(db, ctx, out["event_id"])
    if out.get("proposal_version_id"):
        version = (
            db.query(FnProposalVersion)
            .filter(
                FnProposalVersion.organization_id == ctx.org_id,
                FnProposalVersion.id == out["proposal_version_id"],
            )
            .first()
        )
        if version is None or (
            out.get("enquiry_id") and version.enquiry_id != out["enquiry_id"]
        ):
            raise NotFound("Proposal version not found.")
        out.setdefault("enquiry_id", version.enquiry_id)
    if out.get("email_draft_id"):
        load(
            db, ctx, FnEmailDraft, out["email_draft_id"], label="Draft", venue_attr=None
        )
    return out


def _record_override(row: FnOutbox, reason: str | None) -> None:
    if not reason:
        return
    if hasattr(FnOutbox, "price_override_reason"):
        row.price_override_reason = reason
    logger.warning(
        "functions.mailbox.pricing_override org=%s outbox=%s enquiry=%s user=%s reason=%r",
        row.organization_id,
        row.id,
        row.enquiry_id,
        row.created_by_user_id,
        reason[:500],
    )


def send(
    db: Session,
    ctx: FunctionsCtx,
    account_id: str,
    *,
    to,
    cc=(),
    bcc=(),
    subject: str | None,
    body_html: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    purpose: str,
    refs: dict | None = None,
    idempotency_key: str,
    quote: bool = True,
    price_override_reason: str | None = None,
) -> OutboxResult:
    """Send one email from a venue mailbox, exactly once per key.

    ``purpose``: reply (on ``thread_id``, answering ``in_reply_to`` or the
    thread's last message) | compose (a new conversation) | forward (the
    thread's last message, as a new conversation) | proposal | portal_link
    (on ``thread_id`` when given, else new). ``refs``: enquiry_id, event_id,
    proposal_version_id, email_draft_id — the facts recorded when it is
    sent. Returns status ``sent``, or ``sending`` when Gmail's answer was
    lost (the reconciler settles it); raises ``SendFailed`` when Gmail
    refused it. Commits (more than once — see the module docstring)."""
    from app.db.functions_models import OUTBOX_PURPOSES

    require_staff(ctx)
    key = require_text(idempotency_key, "An idempotency key")[:200]
    if purpose not in OUTBOX_PURPOSES:
        raise Invalid(f"Purpose must be one of {', '.join(OUTBOX_PURPOSES)}.")
    account = accounts_svc.get_account(db, ctx, account_id)
    # Before the mailbox checks: a retry of a send that already went reports
    # it, whatever has happened to the mailbox since.
    previous = existing_send(db, ctx, key, account_id=account.id)
    if previous is not None:
        return previous
    accounts_svc.require_sendable(db, ctx, account)
    clean_refs = _validate_refs(db, ctx, refs or {})
    if purpose == "reply" and not thread_id:
        raise Invalid("A reply needs the conversation it answers.")
    acct = AccountRef.of(account)
    venue_name = _venue_name(db, acct.venue_id)
    actor_name = _actor_name(ctx)
    db.commit()  # no connection held while Gmail works

    client = gmail_client.client_for(acct, writable=ctx.is_live, db=db)
    try:
        try:
            composed = compose(
                account=acct,
                client=client,
                purpose=purpose,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body_html=body_html,
                thread_id=thread_id,
                in_reply_to=in_reply_to,
                quote=quote,
                actor_name=actor_name,
                venue_name=venue_name,
            )
        except GmailError as exc:
            raise compose_error(exc) from exc
        row = FnOutbox(
            organization_id=ctx.org_id,
            idempotency_key=key,
            rfc822_message_id=composed.rfc822_message_id,
            account_id=acct.id,
            gmail_thread_id=composed.gmail_thread_id,
            in_reply_to=composed.in_reply_to,
            references_header=composed.references,
            to_emails=composed.to,
            cc_emails=composed.cc,
            bcc_emails=composed.bcc,
            subject=composed.subject,
            body_html=composed.body_html,
            status="sending",
            purpose=purpose,
            attempts=1,
            created_by_user_id=ctx.user_id,
            created_by_label=ctx.actor_label,
            **clean_refs,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # A concurrent call with the same key won the insert.
            return existing_send(db, ctx, key, account_id=acct.id)
        _record_override(row, price_override_reason)
        outbox_id = row.id
        db.commit()  # written down BEFORE Gmail is called
        return _deliver(
            db, ctx, outbox_id, composed.raw, composed.gmail_thread_id, client
        )
    finally:
        client.close()


def _resume(db: Session, ctx: FunctionsCtx, row: FnOutbox) -> OutboxResult:
    """A second call with a key already used: report — or, for a failed
    send, retry it with the same Message-ID."""
    if row.status != "failed":
        return OutboxResult.of(row, duplicate=True)
    account = (
        db.query(FnMailboxAccount).filter(FnMailboxAccount.id == row.account_id).one()
    )
    accounts_svc.require_sendable(db, ctx, account)
    claimed = db.execute(
        update(FnOutbox.__table__)
        .where(FnOutbox.id == row.id, FnOutbox.status == "failed")
        .values(status="sending", attempts=FnOutbox.attempts + 1, error=None)
        .returning(FnOutbox.id)
    ).first()
    if claimed is None:
        db.refresh(row)
        return OutboxResult.of(row, duplicate=True)
    acct = AccountRef.of(account)
    from_name = sender_name(
        _actor_name(ctx), _venue_name(db, acct.venue_id), acct.display_name
    )
    raw = _rebuild(row, acct, from_name)
    outbox_id, thread = row.id, row.gmail_thread_id
    db.commit()
    with gmail_client.client_for(acct, writable=ctx.is_live, db=db) as client:
        return _deliver(db, ctx, outbox_id, raw, thread, client)


def _fail(db: Session, outbox_id: str, error: str) -> None:
    db.execute(
        update(FnOutbox.__table__)
        .where(FnOutbox.id == outbox_id, FnOutbox.status == "sending")
        .values(status="failed", error=error[:1000])
    )
    db.commit()


def _deliver(
    db: Session, ctx: FunctionsCtx, outbox_id: str, raw: str, thread_id, client
) -> OutboxResult:
    try:
        resp = client.send(raw, thread_id)
    except GmailTransient as exc:
        if exc.ambiguous:
            # Gmail may have sent it: leave ``sending`` for the reconciler.
            db.execute(
                update(FnOutbox.__table__)
                .where(FnOutbox.id == outbox_id)
                .values(error=f"Waiting for Gmail to confirm: {exc.message}"[:1000])
            )
            db.commit()
            row = db.get(FnOutbox, outbox_id)
            db.refresh(row)
            return OutboxResult.of(row)
        _fail(db, outbox_id, f"Gmail was unavailable: {exc.message}")
        raise SendFailed("Gmail is busy or unreachable — nothing was sent. Try again.")
    except MailboxAuthError as exc:
        _fail(db, outbox_id, exc.message)
        raise accounts_svc.MailboxUnavailable(
            "The mailbox needs reconnecting — nothing was sent. " + exc.message
        )
    except NotLive:
        _fail(db, outbox_id, "Functions is not live.")
        raise
    except GmailError as exc:
        _fail(db, outbox_id, f"Gmail refused it: {exc.message}")
        raise SendFailed(f"Gmail refused the email: {exc.message}")
    sent_at = _now()
    for attempt in (0, 1):
        try:
            with db.begin_nested():
                mark_sent(
                    db,
                    ctx,
                    outbox_id,
                    gmail_message_id=str(resp.get("id") or ""),
                    gmail_thread_id=str(resp.get("threadId") or "") or None,
                    label_ids=list(resp.get("labelIds") or ["SENT"]),
                    sent_at=sent_at,
                )
            db.commit()
            break
        except Exception:
            if attempt:
                # It WAS sent; the reconciler will record it from the mailbox.
                logger.exception("outbox %s sent but not recorded", outbox_id)
    row = db.get(FnOutbox, outbox_id)
    db.refresh(row)
    return OutboxResult.of(row)


def mark_sent(
    db: Session,
    ctx: FunctionsCtx,
    outbox_id: str,
    *,
    gmail_message_id: str,
    gmail_thread_id: str | None,
    label_ids: list[str],
    sent_at: dt.datetime,
) -> bool:
    """Sent: the row, the mirror and the facts, in the caller's transaction.
    False when another path (the reconciler, a racing call) got there first."""
    done = db.execute(
        update(FnOutbox.__table__)
        .where(FnOutbox.id == outbox_id, FnOutbox.status.in_(("sending", "failed")))
        .values(
            status="sent",
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
            sent_at=sent_at,
            error=None,
        )
        .returning(FnOutbox.id)
    ).first()
    if done is None:
        return False
    row = db.query(FnOutbox).filter(FnOutbox.id == outbox_id).populate_existing().one()
    account = (
        db.query(FnMailboxAccount).filter(FnMailboxAccount.id == row.account_id).one()
    )
    acct = AccountRef.of(account)
    if gmail_thread_id:
        meta = mirror.MessageMeta(
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
            internal_date=sent_at,
            label_ids=label_ids,
            snippet=html_to_text(row.body_html)[:200],
            from_email=acct.google_email,
            from_name="",
            to_emails=list(row.to_emails or []),
            cc_emails=list(row.cc_emails or []),
            subject=row.subject,
            rfc822_message_id=row.rfc822_message_id,
        )
        touched = mirror.upsert_messages(
            db, acct, [meta], our_addresses(db, acct.organization_id)
        )
        mirror.recompute_threads(db, acct, touched)
    _record_facts(db, ctx, row, acct, sent_at)
    db.flush()
    return True


def _fact(db: Session, outbox_id: str, what: str, fn) -> None:
    """Record one fact in its own savepoint: the send happened, so a fact
    that the org's mode, visibility or a conflict now refuses is logged —
    it never undoes the record of the send, nor the other facts."""
    try:
        with db.begin_nested():
            fn()
    except FunctionsError as exc:
        logger.warning("outbox %s: %s not recorded: %s", outbox_id, what, exc.message)


def _record_facts(
    db: Session, ctx: FunctionsCtx, row: FnOutbox, acct: AccountRef, sent_at
) -> None:
    from app.services.functions import enquiries as enquiries_svc
    from app.services.functions import events as events_svc
    from app.services.functions import proposals as proposals_svc

    if row.proposal_version_id:
        # The version's sent_at and the "sent" fact that makes the enquiry
        # quoted (idempotent per outbox row).
        _fact(
            db,
            row.id,
            "proposal sent",
            lambda: proposals_svc.mark_version_sent(
                db,
                ctx,
                row.proposal_version_id,
                sent_at=sent_at,
                outbox_id=row.id,
                account_id=row.account_id,
                gmail_message_id=row.gmail_message_id,
            ),
        )
    if row.email_draft_id:
        db.execute(
            update(FnEmailDraft.__table__)
            .where(FnEmailDraft.id == row.email_draft_id)
            .values(
                status="sent",
                sent_at=sent_at,
                sent_gmail_message_id=row.gmail_message_id,
                version=FnEmailDraft.version + 1,
            )
        )
    target = (
        {"enquiry_id": row.enquiry_id}
        if row.enquiry_id
        else {"event_id": row.event_id}
        if row.event_id
        else None
    )
    if target and row.purpose in _CUSTOMER_PURPOSES and row.gmail_thread_id:
        linked = (
            db.query(FnThreadLink.id)
            .filter(
                FnThreadLink.organization_id == row.organization_id,
                FnThreadLink.account_id == row.account_id,
                FnThreadLink.gmail_thread_id == row.gmail_thread_id,
                FnThreadLink.unlinked_at.is_(None),
            )
            .first()
        )
        if linked is None:
            # A new conversation about an enquiry belongs to it, so the
            # customer's answer counts there too.
            _fact(
                db,
                row.id,
                "thread link",
                lambda: enquiries_svc.link_thread(
                    db,
                    ctx,
                    account_id=row.account_id,
                    gmail_thread_id=row.gmail_thread_id,
                    **target,
                ),
            )
    if row.purpose == "portal_link" and row.event_id:
        _fact(
            db,
            row.id,
            "link sent",
            lambda: events_svc.record_link_sent(
                db,
                ctx,
                row.event_id,
                to_emails=list(row.to_emails or []),
                outbox_id=row.id,
            ),
        )


# ---------------------------------------------------------------------------
# Reconciler (run by the sync, per mailbox)
# ---------------------------------------------------------------------------


def _find_sent(client, row) -> dict | None:
    """The message our row sent, found in the mailbox — by our Message-ID,
    else (Gmail can replace a Message-ID) the one Sent message to the same
    first recipient with the same subject in the send's minutes."""
    bare = mime.bare_message_id(row.rfc822_message_id)
    hits = (
        client.list_messages(f"rfc822msgid:{bare}", max_results=5).get("messages") or []
    )
    if hits:
        return client.message(hits[0]["id"])
    if not row.to_emails:
        return None
    created = row.created_at
    after = int((created - dt.timedelta(minutes=2)).timestamp())
    found = []
    candidates = (
        client.list_messages(
            f"in:sent after:{after} to:{row.to_emails[0]}", max_results=10
        ).get("messages")
        or []
    )
    for item in candidates:
        msg = client.message(item["id"])
        meta = mirror.parse_message(msg)
        if mime.one_line(meta.subject) == mime.one_line(
            row.subject
        ) and created - dt.timedelta(
            minutes=2
        ) <= meta.internal_date <= created + dt.timedelta(minutes=15):
            found.append(msg)
    return found[0] if len(found) == 1 else None


def reconcile_account(
    account: AccountRef, client, session_factory, now: dt.datetime
) -> int:
    """Settle this mailbox's stuck ``sending`` rows; returns how many."""
    from app.services.functions.ctx import for_org
    from app.services.functions.mailbox.sync import _session

    with _session(session_factory) as db:
        stuck = [
            (r.id, r.created_at)
            for r in db.query(FnOutbox)
            .filter(
                FnOutbox.account_id == account.id,
                FnOutbox.status == "sending",
                FnOutbox.created_at < now - STUCK,
            )
            .order_by(FnOutbox.created_at)
            .limit(10)
        ]
    settled = 0
    for outbox_id, created_at in stuck:
        with _session(session_factory) as db:
            row = db.get(FnOutbox, outbox_id)
            snapshot = SimpleNamespace(
                rfc822_message_id=row.rfc822_message_id,
                to_emails=list(row.to_emails or []),
                subject=row.subject,
                created_at=row.created_at,
            )
        try:
            msg = _find_sent(client, snapshot)
        except GmailNotFound:
            msg = None
        if msg is not None:
            with _session(session_factory) as db:
                ctx = for_org(db, account.organization_id, actor_label="mailbox sync")
                mark_sent(
                    db,
                    ctx,
                    outbox_id,
                    gmail_message_id=str(msg.get("id")),
                    gmail_thread_id=str(msg.get("threadId") or "") or None,
                    label_ids=list(msg.get("labelIds") or ["SENT"]),
                    sent_at=mime.internal_date(msg),
                )
            settled += 1
        elif created_at < now - GIVE_UP:
            with _session(session_factory) as db:
                db.execute(
                    update(FnOutbox.__table__)
                    .where(FnOutbox.id == outbox_id, FnOutbox.status == "sending")
                    .values(
                        status="failed",
                        error="It never reached the mailbox's Sent mail, so it "
                        "was not sent — safe to try again.",
                    )
                )
            settled += 1
    return settled
