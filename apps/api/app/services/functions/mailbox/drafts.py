"""Drafts — written in Norm (by staff or the agent), optionally handed to
Gmail. One owner at a time, so the two copies never fight:

* ``owner = 'norm'`` — editable here, sendable through the outbox.
* **Send to Gmail** creates the draft on the thread in the venue mailbox
  (signature and quoted history included, so it is send-ready there) and
  hands it over: ``owner = 'gmail'``, the Norm copy locked.
* **Bring back** re-reads the Gmail draft (whatever was edited there), takes
  ownership back and deletes it from Gmail.
* The sync follows a handed-over draft: when it disappears from Gmail and a
  message went out on its thread after the hand-over, it was SENT from Gmail
  (status ``sent``, linked to that message — the mirror row of that message
  is the "replied" fact, and our quote link in it the "quoted" one); when it
  disappears with nothing sent, it was deleted there and the Norm copy is
  unlocked.

Hand-over claims the draft before Gmail is called (a version-checked update,
committed), so a double click can't create two Gmail drafts; a hand-over that
died half-way is unlocked by the sync after a few minutes.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.functions_models import DRAFT_KINDS, FnEmailDraft
from app.db.models import _now
from app.services.functions._rows import check_version, load
from app.services.functions.ctx import FunctionsCtx, require_live, require_staff
from app.services.functions.errors import Conflict, Invalid, NotFound, flush
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import gmail_client, mime
from app.services.functions.mailbox.display import (
    clean_outbound_html,
    looks_like_html,
    text_to_html,
)
from app.services.functions.mailbox.gmail_client import GmailError, GmailNotFound
from app.services.functions.mailbox.mirror import AccountRef

logger = logging.getLogger(__name__)

#: A hand-over that never got its Gmail draft id is unlocked after this.
STALE_HANDOVER = dt.timedelta(minutes=5)
DRAFT_FIELDS = (
    "account_id",
    "gmail_thread_id",
    "in_reply_to_gmail_message_id",
    "to_emails",
    "cc_emails",
    "bcc_emails",
    "subject",
    "body_html",
    "mode",
)
#: How a draft goes out. reply / reply_all answer ``gmail_thread_id`` (they
#: differ only in the recipients saved); forward sends the thread's message
#: ``in_reply_to_gmail_message_id`` (or its last) on to someone as a new
#: conversation; new starts one. Default as before a mode existed: reply when
#: the draft has a thread, else new.
DRAFT_MODES = ("reply", "reply_all", "forward", "new")
_PURPOSE = {
    "reply": "reply",
    "reply_all": "reply",
    "forward": "forward",
    "new": "compose",
}


def mode_of(draft: FnEmailDraft) -> str:
    return draft.mode or ("reply" if draft.gmail_thread_id else "new")


def _check_mode(mode: str | None, *, thread_id: str | None, kind: str) -> str:
    mode = mode or ("reply" if thread_id else "new")
    if mode not in DRAFT_MODES:
        raise Invalid(f"Mode must be one of {', '.join(DRAFT_MODES)}.")
    if mode == "new" and thread_id:
        raise Invalid("A new email doesn't belong to a conversation.")
    if mode != "new" and not thread_id:
        raise Invalid("Which conversation does this answer or forward?")
    if mode == "forward" and kind == "proposal":
        raise Invalid("A proposal goes to the customer — it can't be a forward.")
    return mode


def _visible(db: Session, ctx: FunctionsCtx, draft: FnEmailDraft) -> None:
    """A draft is visible through what it is attached to: its mailbox, else
    its enquiry or event."""
    from app.services.functions import enquiries as enquiries_svc
    from app.services.functions import events as events_svc

    if draft.account_id:
        accounts_svc.get_account(db, ctx, draft.account_id)
    elif draft.enquiry_id:
        enquiries_svc.get_enquiry(db, ctx, draft.enquiry_id)
    elif draft.event_id:
        events_svc.get_event(db, ctx, draft.event_id)
    elif draft.created_by_user_id != ctx.user_id:
        raise NotFound("Draft not found.")


def get_draft(
    db: Session, ctx: FunctionsCtx, draft_id: str, *, for_update: bool = False
) -> FnEmailDraft:
    draft = load(
        db,
        ctx,
        FnEmailDraft,
        draft_id,
        label="Draft",
        venue_attr=None,
        for_update=for_update,
    )
    try:
        _visible(db, ctx, draft)
    except NotFound:
        raise NotFound("Draft not found.")
    return draft


def list_drafts(
    db: Session,
    ctx: FunctionsCtx,
    *,
    enquiry_id: str | None = None,
    event_id: str | None = None,
    account_id: str | None = None,
    gmail_thread_id: str | None = None,
    include_closed: bool = False,
) -> list[FnEmailDraft]:
    """Open drafts (or all, with ``include_closed``) for a record or thread."""
    from app.services.functions import enquiries as enquiries_svc
    from app.services.functions import events as events_svc

    q = db.query(FnEmailDraft).filter(FnEmailDraft.organization_id == ctx.org_id)
    if enquiry_id:
        enquiries_svc.get_enquiry(db, ctx, enquiry_id)
        q = q.filter(FnEmailDraft.enquiry_id == enquiry_id)
    elif event_id:
        events_svc.get_event(db, ctx, event_id)
        q = q.filter(FnEmailDraft.event_id == event_id)
    elif account_id:
        accounts_svc.get_account(db, ctx, account_id)
        q = q.filter(FnEmailDraft.account_id == account_id)
    else:
        raise Invalid("Name an enquiry, an event or a mailbox.")
    if gmail_thread_id:
        q = q.filter(FnEmailDraft.gmail_thread_id == gmail_thread_id)
    if not include_closed:
        q = q.filter(FnEmailDraft.status == "open")
    rows = q.order_by(FnEmailDraft.updated_at.desc()).all()
    out = []
    for d in rows:
        try:
            _visible(db, ctx, d)
            out.append(d)
        except NotFound:
            continue
    return out


def _clean_fields(fields: dict) -> dict:
    out = dict(fields)
    for key in ("to_emails", "cc_emails", "bcc_emails"):
        if key in out:
            out[key] = mime.clean_addresses(out[key], key.split("_")[0].capitalize())
    if "subject" in out:
        out["subject"] = mime.one_line(out["subject"])
    if "body_html" in out:
        body = out["body_html"] or ""
        out["body_html"] = (
            clean_outbound_html(body) if looks_like_html(body) else text_to_html(body)
        )
    return out


def create_draft(
    db: Session,
    ctx: FunctionsCtx,
    *,
    account_id: str | None = None,
    enquiry_id: str | None = None,
    event_id: str | None = None,
    gmail_thread_id: str | None = None,
    in_reply_to_gmail_message_id: str | None = None,
    kind: str = "email",
    mode: str | None = None,
    to=(),
    cc=(),
    bcc=(),
    subject: str = "",
    body_html: str = "",
) -> FnEmailDraft:
    """A Norm draft. Without a mailbox, the enquiry's or event's venue
    mailbox is used when it has a connected one (it is needed to send)."""
    from app.services.functions import enquiries as enquiries_svc
    from app.services.functions import events as events_svc

    require_live(ctx)
    require_staff(ctx)
    if kind not in DRAFT_KINDS:
        raise Invalid(f"Kind must be one of {', '.join(DRAFT_KINDS)}.")
    venue_id = None
    if enquiry_id:
        venue_id = enquiries_svc.get_enquiry(db, ctx, enquiry_id).venue_id
    if event_id:
        venue_id = events_svc.get_event(db, ctx, event_id).venue_id
    if kind == "proposal" and not enquiry_id:
        raise Invalid("A proposal email belongs to an enquiry.")
    if account_id:
        accounts_svc.get_account(db, ctx, account_id)
    else:
        found = accounts_svc.connected_account_for_venue(db, ctx, venue_id)
        account_id = found.id if found else None
    if gmail_thread_id and not account_id:
        raise Invalid("A reply draft needs the mailbox the conversation is in.")
    mode = _check_mode(mode, thread_id=gmail_thread_id, kind=kind)
    if not (account_id or enquiry_id or event_id):
        raise Invalid("Attach the draft to a mailbox, an enquiry or an event.")
    fields = _clean_fields(
        {
            "to_emails": to,
            "cc_emails": cc,
            "bcc_emails": bcc,
            "subject": subject,
            "body_html": body_html,
        }
    )
    draft = FnEmailDraft(
        organization_id=ctx.org_id,
        account_id=account_id,
        enquiry_id=enquiry_id,
        event_id=event_id,
        gmail_thread_id=gmail_thread_id,
        in_reply_to_gmail_message_id=in_reply_to_gmail_message_id,
        kind=kind,
        mode=mode,
        owner="norm",
        status="open",
        created_by_user_id=ctx.user_id,
        created_by_label=ctx.actor_label,
        updated_by_user_id=ctx.user_id,
        **fields,
    )
    db.add(draft)
    flush(db)
    return draft


def _require_editable(draft: FnEmailDraft) -> None:
    if draft.status != "open":
        raise Conflict(f"This draft was already {draft.status}.")
    if draft.owner != "norm":
        raise Conflict(
            "This draft is in Gmail now — bring it back to edit it here, or "
            "send it from Gmail."
        )


def update_draft(
    db: Session,
    ctx: FunctionsCtx,
    draft_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> FnEmailDraft:
    require_live(ctx)
    require_staff(ctx)
    draft = get_draft(db, ctx, draft_id)
    check_version(draft, expected_version)
    _require_editable(draft)
    unknown = set(fields) - set(DRAFT_FIELDS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    if fields.get("account_id"):
        accounts_svc.get_account(db, ctx, fields["account_id"])
    if "mode" in fields or "gmail_thread_id" in fields:
        mode = _check_mode(
            fields.get("mode", mode_of(draft)),
            thread_id=fields.get("gmail_thread_id", draft.gmail_thread_id),
            kind=draft.kind,
        )
        fields = {**fields, "mode": mode}
    for key, value in _clean_fields(fields).items():
        setattr(draft, key, value)
    draft.updated_by_user_id = ctx.user_id
    flush(db)
    return draft


def _sending_account(db: Session, ctx: FunctionsCtx, draft: FnEmailDraft):
    if not draft.account_id:
        raise Invalid("Choose the mailbox this draft is sent from.")
    account = accounts_svc.get_account(db, ctx, draft.account_id)
    accounts_svc.require_sendable(db, ctx, account)
    return account


def send_draft(
    db: Session,
    ctx: FunctionsCtx,
    draft_id: str,
    *,
    idempotency_key: str,
    price_override_reason: str | None = None,
):
    """Send a Norm-owned draft through the outbox (a proposal draft goes
    through the proposal send, so it freezes a version and carries the quote
    link). The draft is marked sent when the outbox confirms."""
    from app.services.functions.mailbox import outbox, pricing, sending

    require_live(ctx)
    require_staff(ctx)
    draft = get_draft(db, ctx, draft_id)
    _require_editable(draft)
    account = _sending_account(db, ctx, draft)
    if draft.kind == "proposal":
        return sending.send_enquiry_email(
            db,
            ctx,
            draft.enquiry_id,
            mode="proposal",
            subject=draft.subject,
            body_html=draft.body_html,
            to=list(draft.to_emails or []),
            cc=list(draft.cc_emails or []),
            account_id=account.id,
            gmail_thread_id=draft.gmail_thread_id,
            idempotency_key=idempotency_key,
            email_draft_id=draft.id,
        )
    mode = mode_of(draft)
    override = None
    if mode != "forward":  # a forward is internal handling, never price-checked
        enquiry_id = draft.enquiry_id or pricing.linked_enquiry(
            db, ctx, account.id, draft.gmail_thread_id
        )
        override = pricing.check(
            db, ctx, enquiry_id, draft.body_html, price_override_reason
        )
    return outbox.send(
        db,
        ctx,
        account.id,
        to=list(draft.to_emails or []),
        cc=list(draft.cc_emails or []),
        bcc=list(draft.bcc_emails or []),
        subject=draft.subject or None,
        body_html=draft.body_html,
        thread_id=draft.gmail_thread_id,
        in_reply_to=draft.in_reply_to_gmail_message_id,
        purpose=_PURPOSE[mode],
        refs={
            "enquiry_id": draft.enquiry_id,
            "event_id": draft.event_id,
            "email_draft_id": draft.id,
        },
        idempotency_key=idempotency_key,
        price_override_reason=override,
    )


def send_to_gmail(
    db: Session,
    ctx: FunctionsCtx,
    draft_id: str,
    *,
    expected_version: int | None = None,
) -> FnEmailDraft:
    """Hand the draft to the venue mailbox as a Gmail draft (on its thread)."""
    from app.services.functions.mailbox import outbox, sending

    require_live(ctx)
    require_staff(ctx)
    draft = get_draft(db, ctx, draft_id)
    check_version(draft, expected_version)
    _require_editable(draft)
    account = _sending_account(db, ctx, draft)
    body = draft.body_html
    if draft.kind == "proposal":
        body = sending.prepare_proposal_handover(db, ctx, draft)
    claimed = db.execute(
        update(FnEmailDraft.__table__)
        .where(
            FnEmailDraft.id == draft.id,
            FnEmailDraft.owner == "norm",
            FnEmailDraft.status == "open",
            FnEmailDraft.version == draft.version,
        )
        .values(
            owner="gmail",
            handed_to_gmail_at=_now(),
            body_html=body,
            version=FnEmailDraft.version + 1,
        )
        .returning(FnEmailDraft.id)
    ).first()
    if claimed is None:
        raise Conflict(
            "This draft was changed by someone else since you opened it — reload."
        )
    acct = AccountRef.of(account)
    snapshot = {
        "to": list(draft.to_emails or []),
        "cc": list(draft.cc_emails or []),
        "bcc": list(draft.bcc_emails or []),
        "subject": draft.subject or None,
        "thread_id": draft.gmail_thread_id,
        "mode": mode_of(draft),
        "in_reply_to": draft.in_reply_to_gmail_message_id,
    }
    venue_name = outbox._venue_name(db, acct.venue_id)
    actor = outbox._actor_name(ctx)
    db.commit()  # claimed; no connection held while Gmail works
    try:
        with gmail_client.client_for(acct, writable=ctx.is_live, db=db) as client:
            composed = outbox.compose(
                account=acct,
                client=client,
                purpose=_PURPOSE[snapshot["mode"]],
                to=snapshot["to"],
                cc=snapshot["cc"],
                bcc=snapshot["bcc"],
                subject=snapshot["subject"],
                body_html=body,
                thread_id=snapshot["thread_id"],
                in_reply_to=snapshot["in_reply_to"],
                quote=True,
                actor_name=actor,
                venue_name=venue_name,
            )
            created = client.create_draft(composed.raw, composed.gmail_thread_id)
    except Exception:
        # Nothing went to Gmail: hand the draft back to Norm.
        db.execute(
            update(FnEmailDraft.__table__)
            .where(
                FnEmailDraft.id == draft_id,
                FnEmailDraft.owner == "gmail",
                FnEmailDraft.gmail_draft_id.is_(None),
            )
            .values(owner="norm", handed_to_gmail_at=None)
        )
        db.commit()
        raise
    message = created.get("message") or {}
    db.execute(
        update(FnEmailDraft.__table__)
        .where(FnEmailDraft.id == draft_id)
        .values(
            gmail_draft_id=str(created.get("id")),
            # The Gmail draft's own thread (a forward's is a new one): where
            # the sync looks for the send. The forwarded message is kept, so
            # a brought-back forward still knows what it forwards.
            gmail_thread_id=str(message.get("threadId") or "") or snapshot["thread_id"],
            in_reply_to_gmail_message_id=composed.target_message_id
            or snapshot["in_reply_to"],
            to_emails=composed.to,
            subject=composed.subject,
            version=FnEmailDraft.version + 1,
        )
    )
    db.commit()
    return get_draft(db, ctx, draft_id)


_MARKERS = ('<div class="norm-signature"', '<div class="gmail_quote"')


def _strip_composed(html: str) -> str:
    """The body as the person wrote it: everything before our signature or
    the quoted history (which ``send`` adds again)."""
    cut = len(html)
    for marker in _MARKERS:
        i = html.find(marker)
        if 0 <= i < cut:
            cut = i
    body = html[:cut]
    for tail in ("<br>", "<br/>", "<br />"):
        while body.rstrip().endswith(tail):
            body = body.rstrip()[: -len(tail)]
    return body.strip()


def bring_back(db: Session, ctx: FunctionsCtx, draft_id: str) -> FnEmailDraft:
    """Take a handed-over draft back from Gmail, with any edits made there."""
    require_live(ctx)
    require_staff(ctx)
    draft = get_draft(db, ctx, draft_id)
    if draft.status != "open":
        raise Conflict(f"This draft was already {draft.status}.")
    if draft.owner != "gmail":
        return draft
    if not draft.gmail_draft_id:
        _unlock(db, draft.id)
        db.commit()
        return get_draft(db, ctx, draft_id)
    account = accounts_svc.get_account(db, ctx, draft.account_id)
    acct = AccountRef.of(account)
    gmail_draft_id = draft.gmail_draft_id
    pending = _Pending.of(draft)
    forwarded = (
        draft.in_reply_to_gmail_message_id if mode_of(draft) == "forward" else None
    )
    db.commit()
    with gmail_client.client_for(acct, writable=ctx.is_live, db=db) as client:
        source_thread = None
        if forwarded:
            # Back on the conversation it forwards (the Gmail draft had a
            # thread of its own).
            try:
                source_thread = client.message(forwarded, fmt="minimal").get("threadId")
            except GmailError:
                source_thread = None
        try:
            got = client.draft(gmail_draft_id, fmt="full")
        except GmailNotFound:
            outcome = _settle_missing(client, pending)
            _apply(db, pending, outcome)
            db.commit()
            return get_draft(db, ctx, draft_id)
        message = got.get("message") or {}
        payload = message.get("payload") or {}
        headers = mime.headers_map(payload)
        parts = mime.bodies(payload)
        html = parts.html or text_to_html(parts.text)
        db.execute(
            update(FnEmailDraft.__table__)
            .where(FnEmailDraft.id == draft_id, FnEmailDraft.owner == "gmail")
            .values(
                to_emails=[a for _, a in mime.parse_address_list(headers.get("to"))],
                cc_emails=[a for _, a in mime.parse_address_list(headers.get("cc"))],
                bcc_emails=[a for _, a in mime.parse_address_list(headers.get("bcc"))],
                subject=mime.one_line(headers.get("subject")),
                body_html=clean_outbound_html(_strip_composed(html)),
                owner="norm",
                handed_to_gmail_at=None,
                updated_by_user_id=ctx.user_id,
                version=FnEmailDraft.version + 1,
                **({"gmail_thread_id": source_thread} if source_thread else {}),
            )
        )
        db.commit()
        try:
            client.delete_draft(gmail_draft_id)
        except GmailNotFound:
            pass
        except GmailError as exc:
            # Ownership is back in Norm either way; the Gmail copy lingers.
            logger.warning("draft %s: Gmail copy not deleted: %s", draft_id, exc)
            return get_draft(db, ctx, draft_id)
    db.execute(
        update(FnEmailDraft.__table__)
        .where(FnEmailDraft.id == draft_id, FnEmailDraft.owner == "norm")
        .values(gmail_draft_id=None)
    )
    db.commit()
    return get_draft(db, ctx, draft_id)


def discard_draft(
    db: Session,
    ctx: FunctionsCtx,
    draft_id: str,
    *,
    expected_version: int | None = None,
) -> FnEmailDraft:
    """Discard (a Gmail-held draft is deleted from Gmail too — unless it
    turns out to have been sent from there, which is then recorded)."""
    require_live(ctx)
    require_staff(ctx)
    draft = get_draft(db, ctx, draft_id)
    check_version(draft, expected_version)
    if draft.status != "open":
        raise Conflict(f"This draft was already {draft.status}.")
    if draft.owner == "gmail" and draft.gmail_draft_id:
        account = accounts_svc.get_account(db, ctx, draft.account_id)
        acct = AccountRef.of(account)
        pending = _Pending.of(draft)
        db.commit()
        with gmail_client.client_for(acct, writable=ctx.is_live, db=db) as client:
            try:
                client.delete_draft(pending.gmail_draft_id)
            except GmailNotFound:
                outcome = _settle_missing(client, pending)
                if outcome[0] == "sent":
                    _apply(db, pending, outcome)
                    db.commit()
                    raise Conflict("It had already been sent from Gmail.")
    db.execute(
        update(FnEmailDraft.__table__)
        .where(FnEmailDraft.id == draft_id, FnEmailDraft.status == "open")
        .values(
            status="discarded",
            owner="norm",
            gmail_draft_id=None,
            updated_by_user_id=ctx.user_id,
            version=FnEmailDraft.version + 1,
        )
    )
    db.commit()
    return get_draft(db, ctx, draft_id)


# ---------------------------------------------------------------------------
# Following handed-over drafts (the sync)
# ---------------------------------------------------------------------------


class _Pending:
    __slots__ = ("id", "gmail_draft_id", "gmail_thread_id", "handed_to_gmail_at")

    def __init__(self, id, gmail_draft_id, gmail_thread_id, handed_to_gmail_at):
        self.id = id
        self.gmail_draft_id = gmail_draft_id
        self.gmail_thread_id = gmail_thread_id
        self.handed_to_gmail_at = handed_to_gmail_at

    @classmethod
    def of(cls, d) -> "_Pending":
        return cls(d.id, d.gmail_draft_id, d.gmail_thread_id, d.handed_to_gmail_at)


def _settle_missing(
    client, pending: _Pending
) -> tuple[str, str | None, dt.datetime | None]:
    """A handed-over draft is gone from Gmail: ``("sent", message id, when)``
    if a message went out on its thread after the hand-over, else
    ``("deleted", None, None)``."""
    if not pending.gmail_thread_id:
        return ("deleted", None, None)
    try:
        thread = client.thread(pending.gmail_thread_id, fmt="minimal")
    except GmailNotFound:
        return ("deleted", None, None)
    since = (
        pending.handed_to_gmail_at or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    ) - (dt.timedelta(minutes=1))
    sent = sorted(
        (
            (mime.internal_date(m), m)
            for m in thread.get("messages") or []
            if "SENT" in (m.get("labelIds") or [])
            and "DRAFT" not in (m.get("labelIds") or [])
        ),
        key=lambda x: x[0],
    )
    for when, m in sent:
        if when >= since:
            return ("sent", str(m.get("id")), when)
    return ("deleted", None, None)


def _unlock(db: Session, draft_id: str) -> None:
    db.execute(
        update(FnEmailDraft.__table__)
        .where(FnEmailDraft.id == draft_id, FnEmailDraft.owner == "gmail")
        .values(
            owner="norm",
            gmail_draft_id=None,
            handed_to_gmail_at=None,
            version=FnEmailDraft.version + 1,
        )
    )


def _apply(db: Session, pending: _Pending, outcome) -> None:
    kind, message_id, when = outcome
    if kind == "sent":
        db.execute(
            update(FnEmailDraft.__table__)
            .where(FnEmailDraft.id == pending.id, FnEmailDraft.status == "open")
            .values(
                status="sent",
                sent_at=when,
                sent_gmail_message_id=message_id,
                version=FnEmailDraft.version + 1,
            )
        )
    else:
        _unlock(db, pending.id)


def reconcile_handovers(
    account: AccountRef, client, session_factory, now: dt.datetime, *, thread_ids=None
) -> int:
    """Settle this mailbox's handed-over drafts that Gmail no longer has
    (only those on ``thread_ids`` when given). Returns how many settled."""
    from app.services.functions.mailbox.sync import _session

    with _session(session_factory) as db:
        q = db.query(FnEmailDraft).filter(
            FnEmailDraft.account_id == account.id,
            FnEmailDraft.owner == "gmail",
            FnEmailDraft.status == "open",
        )
        if thread_ids is not None:
            if not thread_ids:
                return 0
            q = q.filter(FnEmailDraft.gmail_thread_id.in_(sorted(thread_ids)))
        pending = [_Pending.of(d) for d in q.limit(50)]
    settled = 0
    for p in pending:
        if not p.gmail_draft_id:
            if p.handed_to_gmail_at and p.handed_to_gmail_at < now - STALE_HANDOVER:
                with _session(session_factory) as db:
                    _unlock(db, p.id)
                settled += 1
            continue
        try:
            client.draft(p.gmail_draft_id, fmt="minimal")
            continue
        except GmailNotFound:
            outcome = _settle_missing(client, p)
        with _session(session_factory) as db:
            _apply(db, p, outcome)
        settled += 1
    return settled


def draft_dict(d: FnEmailDraft) -> dict:
    return {
        "id": d.id,
        "version": d.version,
        "kind": d.kind,
        "mode": mode_of(d),
        "status": d.status,
        "owner": d.owner,
        "locked": d.owner != "norm",
        "account_id": d.account_id,
        "enquiry_id": d.enquiry_id,
        "event_id": d.event_id,
        "gmail_thread_id": d.gmail_thread_id,
        "in_reply_to_gmail_message_id": d.in_reply_to_gmail_message_id,
        "to": list(d.to_emails or []),
        "cc": list(d.cc_emails or []),
        "bcc": list(d.bcc_emails or []),
        "subject": d.subject,
        "body_html": d.body_html,
        "gmail_draft_id": d.gmail_draft_id,
        "handed_to_gmail_at": d.handed_to_gmail_at.isoformat()
        if d.handed_to_gmail_at
        else None,
        "sent_at": d.sent_at.isoformat() if d.sent_at else None,
        "sent_gmail_message_id": d.sent_gmail_message_id,
        "created_by_label": d.created_by_label,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }
