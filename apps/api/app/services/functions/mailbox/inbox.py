"""What the Inbox screen and the Functions agent read and do with mail.

Lists come from the mirror (fast, filterable, visible venues only); a
thread's bodies are fetched live from Gmail when it is opened and shown
sanitised (``display.safe_html``). Older mail outside the mirror's 90 days is
found with a live Gmail search, and linking such a thread pulls its messages
into the mirror so the enquiry's facts see them.

One folder per thread (Enquiry / Reservation / Other): set here, it is
written to ``fn_thread_folders`` and mirrored to the venue's "Actioned - …"
Gmail label; filed in Gmail, the sync follows it.

Agent tools (F10b) call ``find_threads`` / ``read_thread`` / ``file_thread``
and ``drafts.create_draft``; every function takes ``(db, ctx, …)`` and
returns plain dicts.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FOLDERS,
    FnContact,
    FnEmailDraft,
    FnEnquiry,
    FnEvent,
    FnMailboxAccount,
    FnMailboxMessage,
    FnMailboxThread,
    FnThreadFolder,
    FnThreadLink,
)
from app.db.models import Venue, _now
from app.services.functions import enquiries as enquiries_svc
from app.services.functions.ctx import FunctionsCtx, require_live, require_staff
from app.services.functions.errors import FunctionsError, Invalid, NotFound
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import gmail_client, mime, mirror
from app.services.functions.mailbox.classify import our_addresses
from app.services.functions.mailbox.display import html_to_text, safe_html
from app.services.functions.mailbox.gmail_client import GmailError, GmailNotFound
from app.services.functions.mailbox.mirror import AccountRef

logger = logging.getLogger(__name__)

MAX_PAGE = 200


def _iso(value):
    return value.isoformat() if value is not None else None


def _visible_account_ids(
    db: Session, ctx: FunctionsCtx, account_id=None, venue_id=None
):
    q = accounts_svc.visible_accounts(db, ctx)
    if account_id:
        q = q.filter(FnMailboxAccount.id == account_id)
    if venue_id:
        q = q.filter(FnMailboxAccount.venue_id == venue_id)
    return {a.id: a for a in q.all()}


def _links_for(db: Session, ctx: FunctionsCtx, pairs) -> dict:
    """``{(account, thread): [link dicts]}`` — only links to records the
    caller can see are described."""
    if not pairs:
        return {}
    accounts = {a for a, _ in pairs}
    threads = {t for _, t in pairs}
    rows = (
        db.query(FnThreadLink, FnEnquiry, FnEvent, FnContact)
        .outerjoin(
            FnEnquiry,
            and_(
                FnEnquiry.organization_id == FnThreadLink.organization_id,
                FnEnquiry.id == FnThreadLink.enquiry_id,
            ),
        )
        .outerjoin(
            FnEvent,
            and_(
                FnEvent.organization_id == FnThreadLink.organization_id,
                FnEvent.id == FnThreadLink.event_id,
            ),
        )
        .outerjoin(
            FnContact,
            and_(
                FnContact.organization_id == FnThreadLink.organization_id,
                FnContact.id == func.coalesce(FnEnquiry.contact_id, FnEvent.contact_id),
            ),
        )
        .filter(
            FnThreadLink.organization_id == ctx.org_id,
            FnThreadLink.account_id.in_(accounts),
            FnThreadLink.gmail_thread_id.in_(threads),
            FnThreadLink.unlinked_at.is_(None),
        )
        .all()
    )
    out: dict = {}
    for link, enquiry, event, contact in rows:
        key = (link.account_id, link.gmail_thread_id)
        if key not in pairs:
            continue
        visible = (enquiry is not None and ctx.can_see_venue(enquiry.venue_id)) or (
            event is not None and event.venue_id in ctx.venue_ids
        )
        item = {"link_id": link.id, "visible": visible}
        if visible:
            item.update(
                {
                    "enquiry_id": link.enquiry_id,
                    "event_id": link.event_id,
                    "label": (event.name if event is not None else None)
                    or (enquiry.subject if enquiry is not None else None)
                    or (contact.name if contact is not None else None),
                    "contact_name": contact.name if contact is not None else None,
                }
            )
        out.setdefault(key, []).append(item)
    return out


def _awaiting(db: Session, pairs) -> dict:
    """``{(account, thread): bool}`` — the customer wrote last (ignoring
    internal mail and auto-replies)."""
    if not pairs:
        return {}
    m = FnMailboxMessage
    rows = db.execute(
        select(m.account_id, m.gmail_thread_id, m.is_inbound, m.internal_date)
        .where(
            m.account_id.in_({a for a, _ in pairs}),
            m.gmail_thread_id.in_({t for _, t in pairs}),
            m.is_internal.is_(False),
            m.is_auto_reply.is_(False),
        )
        .order_by(m.internal_date.desc())
    ).all()
    out: dict = {}
    for r in rows:
        key = (r.account_id, r.gmail_thread_id)
        if key in pairs and key not in out:
            out[key] = bool(r.is_inbound)
    return out


def _thread_row(t, account, venue_names, folder, links, awaiting, drafts) -> dict:
    key = (t.account_id, t.gmail_thread_id)
    return {
        "account_id": t.account_id,
        "mailbox": account.google_email,
        "venue_id": account.venue_id,
        "venue_name": venue_names.get(account.venue_id),
        "gmail_thread_id": t.gmail_thread_id,
        "subject": t.subject,
        "snippet": t.snippet,
        "last_message_at": _iso(t.last_message_at),
        "last_from_email": t.last_from_email,
        "last_from_name": t.last_from_name,
        "message_count": t.message_count,
        "unread_count": t.unread_count,
        "in_inbox": "INBOX" in (t.label_ids or []),
        "folder": folder,
        "links": links.get(key, []),
        "linked": bool(links.get(key)),
        "awaiting_reply": awaiting.get(key, False),
        "has_norm_draft": key in drafts,
    }


def list_threads(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str | None = None,
    account_id: str | None = None,
    folder: str | None = None,
    linked: bool | None = None,
    unread: bool | None = None,
    q: str | None = None,
    since: dt.datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Mirrored threads, newest first. ``folder``: enquiry | reservation |
    other | unfiled. ``linked``: only threads linked (or not) to an enquiry
    or event. ``q`` searches subject, snippet and correspondents within the
    mirror (use ``search_gmail`` for older mail)."""
    if folder is not None and folder not in (*FOLDERS, "unfiled"):
        raise Invalid(f"Folder must be one of {', '.join((*FOLDERS, 'unfiled'))}.")
    accounts = _visible_account_ids(db, ctx, account_id, venue_id)
    if account_id and account_id not in accounts:
        raise NotFound("Mailbox not found.")
    if not accounts:
        return {"threads": [], "has_more": False}
    t = FnMailboxThread
    f = FnThreadFolder
    live_link = (
        exists()
        .where(
            FnThreadLink.organization_id == t.organization_id,
            FnThreadLink.account_id == t.account_id,
            FnThreadLink.gmail_thread_id == t.gmail_thread_id,
            FnThreadLink.unlinked_at.is_(None),
        )
        .correlate(t)
    )
    query = (
        db.query(t, f.folder)
        .outerjoin(
            f,
            and_(
                f.organization_id == t.organization_id,
                f.account_id == t.account_id,
                f.gmail_thread_id == t.gmail_thread_id,
            ),
        )
        .filter(t.organization_id == ctx.org_id, t.account_id.in_(sorted(accounts)))
    )
    if folder == "unfiled":
        query = query.filter(f.id.is_(None))
    elif folder:
        query = query.filter(f.folder == folder)
    if linked is True:
        query = query.filter(live_link)
    elif linked is False:
        query = query.filter(~live_link)
    if unread is True:
        query = query.filter(t.unread_count > 0)
    if since is not None:
        query = query.filter(t.last_message_at >= since)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        m = FnMailboxMessage
        correspondent = (
            exists()
            .where(
                m.account_id == t.account_id,
                m.gmail_thread_id == t.gmail_thread_id,
                or_(
                    func.lower(func.coalesce(m.from_email, "")).like(like),
                    func.lower(func.coalesce(m.from_name, "")).like(like),
                    func.lower(cast(m.to_emails, String)).like(like),
                ),
            )
            .correlate(t)
        )
        query = query.filter(
            or_(
                func.lower(func.coalesce(t.subject, "")).like(like),
                func.lower(func.coalesce(t.snippet, "")).like(like),
                correspondent,
            )
        )
    limit = max(1, min(int(limit or 50), MAX_PAGE))
    rows = (
        query.order_by(t.last_message_at.desc().nulls_last(), t.id)
        .offset(max(0, int(offset or 0)))
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    pairs = {(r[0].account_id, r[0].gmail_thread_id) for r in rows}
    links = _links_for(db, ctx, pairs)
    awaiting = _awaiting(db, pairs)
    drafts = {
        (d[0], d[1])
        for d in db.query(FnEmailDraft.account_id, FnEmailDraft.gmail_thread_id).filter(
            FnEmailDraft.organization_id == ctx.org_id,
            FnEmailDraft.status == "open",
            FnEmailDraft.account_id.in_({a for a, _ in pairs} or {""}),
            FnEmailDraft.gmail_thread_id.in_({th for _, th in pairs} or {""}),
        )
    }
    venue_names = {
        v.id: v.name
        for v in db.query(Venue).filter(
            Venue.id.in_({a.venue_id for a in accounts.values()})
        )
    }
    return {
        "threads": [
            _thread_row(
                row,
                accounts[row.account_id],
                venue_names,
                folder_,
                links,
                awaiting,
                drafts,
            )
            for row, folder_ in rows
        ],
        "has_more": has_more,
    }


find_threads = list_threads


def _client(db: Session, ctx: FunctionsCtx, account) -> gmail_client.GmailClient:
    return gmail_client.client_for(AccountRef.of(account), writable=ctx.is_live, db=db)


def read_thread(
    db: Session, ctx: FunctionsCtx, account_id: str, gmail_thread_id: str
) -> dict:
    """A thread with its bodies (live from Gmail, sanitised), its links, its
    folder and the Norm drafts on it. A mailbox that isn't connected shows
    the mirrored summary only (``bodies_available`` false). Opening a thread
    also refreshes its messages in the mirror."""
    account = accounts_svc.get_account(db, ctx, account_id)
    acct = AccountRef.of(account)
    mirrored = {
        m.gmail_message_id: m
        for m in mirror.thread_message_rows(db, acct.id, gmail_thread_id)
    }
    links = _links_for(db, ctx, {(acct.id, gmail_thread_id)}).get(
        (acct.id, gmail_thread_id), []
    )
    folder_row = (
        db.query(FnThreadFolder)
        .filter(
            FnThreadFolder.organization_id == ctx.org_id,
            FnThreadFolder.account_id == acct.id,
            FnThreadFolder.gmail_thread_id == gmail_thread_id,
        )
        .first()
    )
    from app.services.functions.mailbox import drafts as drafts_svc

    drafts = [
        drafts_svc.draft_dict(d)
        for d in db.query(FnEmailDraft).filter(
            FnEmailDraft.organization_id == ctx.org_id,
            FnEmailDraft.account_id == acct.id,
            FnEmailDraft.gmail_thread_id == gmail_thread_id,
            FnEmailDraft.status == "open",
        )
    ]
    if not mirrored and not accounts_svc.is_connected(account):
        raise NotFound("Conversation not found.")
    out = {
        "account_id": acct.id,
        "mailbox": acct.google_email,
        "venue_id": acct.venue_id,
        "gmail_thread_id": gmail_thread_id,
        "folder": folder_row.folder if folder_row else None,
        "folder_sync_error": folder_row.gmail_sync_error if folder_row else None,
        "links": links,
        "drafts": drafts,
        "bodies_available": False,
        "bodies_error": None,
        "has_gmail_draft": False,
        "messages": [],
    }

    def summary(m) -> dict:
        return {
            "gmail_message_id": m.gmail_message_id,
            "internal_date": _iso(m.internal_date),
            "from_email": m.from_email,
            "from_name": m.from_name,
            "to": list(m.to_emails or []),
            "cc": list(m.cc_emails or []),
            "subject": m.subject,
            "snippet": m.snippet,
            "is_inbound": m.is_inbound,
            "is_internal": m.is_internal,
            "is_auto_reply": m.is_auto_reply,
            "unread": m.unread,
            "html": None,
            "text": None,
            "attachments": [],
        }

    if not accounts_svc.is_connected(account):
        out["messages"] = [
            summary(m) for m in sorted(mirrored.values(), key=lambda x: x.internal_date)
        ]
        out["bodies_error"] = "Connect this mailbox to read its messages."
        return out

    db.commit()  # no connection held while Gmail works
    try:
        with _client(db, ctx, account) as client:
            thread = client.thread(gmail_thread_id, fmt="full")
    except GmailNotFound:
        raise NotFound("That conversation is no longer in the mailbox.")
    except GmailError as exc:
        out["messages"] = [
            summary(m) for m in sorted(mirrored.values(), key=lambda x: x.internal_date)
        ]
        out["bodies_error"] = f"Gmail couldn't be reached: {exc.message}"
        return out

    resources = thread.get("messages") or []
    metas = [mirror.parse_message(r) for r in resources]
    real = [mt for mt in metas if not mt.is_draft]
    out["has_gmail_draft"] = len(real) != len(metas)
    try:
        with db.begin_nested():
            touched = mirror.upsert_messages(
                db, acct, real, our_addresses(db, acct.organization_id)
            )
            mirror.recompute_threads(db, acct, touched)
        db.commit()
    except Exception:  # noqa: BLE001 — showing the thread matters more
        logger.exception("mirror refresh failed for thread %s", gmail_thread_id)
    rows = {
        m.gmail_message_id: m
        for m in mirror.thread_message_rows(db, acct.id, gmail_thread_id)
    }
    messages = []
    for resource, meta in zip(resources, metas):
        if meta.is_draft:
            continue
        parts = mime.bodies(resource.get("payload"))
        row = rows.get(meta.gmail_message_id)
        item = (
            summary(row)
            if row is not None
            else {
                "gmail_message_id": meta.gmail_message_id,
                "internal_date": _iso(meta.internal_date),
                "from_email": meta.from_email,
                "from_name": meta.from_name,
                "to": meta.to_emails,
                "cc": meta.cc_emails,
                "subject": meta.subject,
                "snippet": meta.snippet,
                "is_inbound": None,
                "is_internal": None,
                "is_auto_reply": None,
                "unread": "UNREAD" in meta.label_ids,
                "attachments": [],
            }
        )
        item["html"] = safe_html(parts.html) if parts.html else None
        item["text"] = parts.text or (html_to_text(parts.html) if parts.html else "")
        item["attachments"] = parts.attachments
        messages.append(item)
    out["messages"] = messages
    out["bodies_available"] = True
    return out


def set_folder(
    db: Session,
    ctx: FunctionsCtx,
    account_id: str,
    gmail_thread_id: str,
    folder: str | None,
) -> dict:
    """File a thread (None = unfiled), in Norm and on the venue's Gmail
    labels. A Gmail failure is recorded on the folder row (and shown), not
    raised: the choice stands in Norm and the sync retries nothing — the
    next filing, here or in Gmail, settles it."""
    require_live(ctx)
    require_staff(ctx)
    if folder is not None and folder not in FOLDERS:
        raise Invalid(f"Folder must be one of {', '.join(FOLDERS)}.")
    thread_id = (gmail_thread_id or "").strip()
    if not thread_id:
        raise Invalid("A thread is required.")
    account = accounts_svc.get_account(db, ctx, account_id)
    acct = AccountRef.of(account)
    row = (
        db.query(FnThreadFolder)
        .filter(
            FnThreadFolder.organization_id == ctx.org_id,
            FnThreadFolder.account_id == acct.id,
            FnThreadFolder.gmail_thread_id == thread_id,
        )
        .first()
    )
    now = _now()
    if folder is None:
        if row is not None:
            db.delete(row)
    else:
        if row is None:
            row = FnThreadFolder(
                organization_id=ctx.org_id,
                account_id=acct.id,
                gmail_thread_id=thread_id,
            )
            db.add(row)
        row.folder = folder
        row.set_by_user_id = ctx.user_id
        row.set_by_label = ctx.actor_label
        row.set_at = now
        row.gmail_synced_at = None
        row.gmail_sync_error = None
    db.flush()
    labels = mirror.folder_labels(db, acct.id)
    target = labels.get(folder) if folder else None
    remove = [lid for f, lid in labels.items() if f != folder]
    error = None
    if not accounts_svc.is_connected(account):
        error = "The mailbox isn't connected, so Gmail wasn't updated."
    elif folder and not target:
        error = (
            f"The mailbox has no '{mirror.FOLDER_LABEL_NAMES[folder][0]}' label, "
            "so Gmail wasn't updated."
        )
    if error or (not target and not remove):
        if row is not None and folder is not None:
            row.gmail_sync_error = error
        db.commit()
        return {"folder": folder, "gmail_synced": False, "gmail_error": error}
    db.commit()  # no connection held while Gmail works
    add = [target] if target else []
    try:
        with _client(db, ctx, account) as client:
            client.modify_thread(thread_id, add=add, remove=remove)
    except GmailError as exc:
        error = (
            "That conversation is no longer in the mailbox."
            if isinstance(exc, GmailNotFound)
            else f"Gmail refused the label change: {exc.message}"
        )
        if folder is not None:
            db.query(FnThreadFolder).filter(
                FnThreadFolder.organization_id == ctx.org_id,
                FnThreadFolder.account_id == acct.id,
                FnThreadFolder.gmail_thread_id == thread_id,
            ).update({"gmail_sync_error": error[:1000]}, synchronize_session=False)
        db.commit()
        return {"folder": folder, "gmail_synced": False, "gmail_error": error}
    if folder is not None:
        db.query(FnThreadFolder).filter(
            FnThreadFolder.organization_id == ctx.org_id,
            FnThreadFolder.account_id == acct.id,
            FnThreadFolder.gmail_thread_id == thread_id,
        ).update(
            {"gmail_synced_at": _now(), "gmail_sync_error": None},
            synchronize_session=False,
        )
    mirror.set_thread_labels(db, acct, thread_id, add=add, remove=remove)
    db.commit()
    return {"folder": folder, "gmail_synced": True, "gmail_error": None}


file_thread = set_folder


def backfill_thread(
    db: Session, ctx: FunctionsCtx, account, gmail_thread_id: str
) -> int:
    """Pull a thread's messages into the mirror (a thread linked from a live
    search may be older than the 90-day window — its facts must count).
    Best effort: returns how many messages were written."""
    if not accounts_svc.is_connected(account):
        return 0
    acct = AccountRef.of(account)
    db.commit()
    try:
        with _client(db, ctx, account) as client:
            thread = client.thread(gmail_thread_id, fmt="metadata")
    except GmailError as exc:
        logger.info("backfill of thread %s failed: %s", gmail_thread_id, exc)
        return 0
    metas = [mirror.parse_message(r) for r in thread.get("messages") or []]
    real = [m for m in metas if not m.is_draft]
    touched = mirror.upsert_messages(
        db, acct, real, our_addresses(db, acct.organization_id)
    )
    mirror.recompute_threads(db, acct, touched)
    mirror.sync_folders_from_labels(db, acct, touched)
    db.commit()
    return len(real)


def link_thread(
    db: Session,
    ctx: FunctionsCtx,
    account_id: str,
    gmail_thread_id: str,
    *,
    enquiry_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    """Link (F1's ``enquiries.link_thread``) and make sure the thread's mail
    is mirrored."""
    link = enquiries_svc.link_thread(
        db,
        ctx,
        account_id=account_id,
        gmail_thread_id=gmail_thread_id,
        enquiry_id=enquiry_id,
        event_id=event_id,
    )
    out = {
        "link_id": link.id,
        "account_id": link.account_id,
        "gmail_thread_id": link.gmail_thread_id,
        "enquiry_id": link.enquiry_id,
        "event_id": link.event_id,
    }
    account = accounts_svc.get_account(db, ctx, account_id)
    db.commit()
    out["mirrored"] = backfill_thread(db, ctx, account, gmail_thread_id)
    return out


def unlink(db: Session, ctx: FunctionsCtx, link_id: str) -> dict:
    link = enquiries_svc.unlink_thread(db, ctx, link_id)
    return {"link_id": link.id, "unlinked_at": _iso(link.unlinked_at)}


def create_enquiry_from_thread(
    db: Session,
    ctx: FunctionsCtx,
    account_id: str,
    gmail_thread_id: str,
    *,
    booking_type: str | None = None,
    subject: str | None = None,
    venue_id: str | None = None,
    contact_name: str | None = None,
    contact_email: str | None = None,
) -> dict:
    """A new enquiry from a thread: the customer is whoever wrote in (the
    newest customer message's Reply-To, else From — a web form's
    notification carries the customer in Reply-To), matched to an existing
    contact by email. The thread is linked to the new enquiry."""
    require_live(ctx)
    require_staff(ctx)
    account = accounts_svc.get_account(db, ctx, account_id)
    rows = mirror.thread_message_rows(db, account.id, gmail_thread_id)
    if not rows:
        # Found by a live search: mirror it first, to see who wrote in.
        backfill_thread(db, ctx, account, gmail_thread_id)
        account = accounts_svc.get_account(db, ctx, account_id)
        rows = mirror.thread_message_rows(db, account.id, gmail_thread_id)
    customer = next(
        (
            m
            for m in reversed(rows)
            if m.is_inbound and not m.is_internal and not m.is_auto_reply
        ),
        None,
    )
    email = (contact_email or "").strip().lower() or (
        (customer.reply_to_email or customer.from_email)
        if customer is not None
        else None
    )
    if not email:
        raise Invalid("Who is the customer? Give their email address.")
    name = (
        (contact_name or "").strip()
        or (customer.from_name if customer is not None and customer.from_name else None)
        or email.split("@")[0]
    )
    thread_row = (
        db.query(FnMailboxThread)
        .filter(
            FnMailboxThread.account_id == account.id,
            FnMailboxThread.gmail_thread_id == gmail_thread_id,
        )
        .first()
    )
    enquiry = enquiries_svc.create_enquiry(
        db,
        ctx,
        contact={"name": name, "email": email},
        venue_id=venue_id or account.venue_id,
        booking_type=booking_type,
        subject=subject or (thread_row.subject if thread_row else None),
        source="email",
    )
    link = enquiries_svc.link_thread(
        db,
        ctx,
        account_id=account.id,
        gmail_thread_id=gmail_thread_id,
        enquiry_id=enquiry.id,
    )
    out = {
        "enquiry_id": enquiry.id,
        "contact_id": enquiry.contact_id,
        "link_id": link.id,
        "messages": len(rows),
    }
    db.commit()
    return out


def search_gmail(
    db: Session, ctx: FunctionsCtx, account_id: str, q: str, *, limit: int = 20
) -> list[dict]:
    """Live Gmail search (``q`` in Gmail's syntax) for mail older than the
    mirror — threads, newest first, marked when they are already mirrored."""
    query = (q or "").strip()
    if not query:
        raise Invalid("Search for something.")
    account = accounts_svc.get_account(db, ctx, account_id)
    if not accounts_svc.is_connected(account):
        raise accounts_svc.MailboxUnavailable(
            f"{account.google_email} isn't connected, so Gmail can't be searched."
        )
    mirrored = {
        r[0]
        for r in db.query(FnMailboxThread.gmail_thread_id).filter(
            FnMailboxThread.account_id == account.id
        )
    }
    limit = max(1, min(int(limit or 20), 50))
    db.commit()
    out: list[dict] = []
    with _client(db, ctx, account) as client:
        found = client.list_messages(query, max_results=limit * 3).get("messages") or []
        seen: list[str] = []
        for item in found:
            tid = item.get("threadId")
            if tid and tid not in seen:
                seen.append(tid)
            if len(seen) >= limit:
                break
        for tid in seen:
            try:
                thread = client.thread(tid, fmt="metadata")
            except GmailNotFound:
                continue
            metas = [
                mirror.parse_message(r)
                for r in thread.get("messages") or []
                if "DRAFT" not in (r.get("labelIds") or [])
            ]
            if not metas:
                continue
            first, last = metas[0], metas[-1]
            out.append(
                {
                    "account_id": account.id,
                    "gmail_thread_id": tid,
                    "subject": first.subject,
                    "snippet": last.snippet,
                    "last_message_at": _iso(last.internal_date),
                    "last_from_email": last.from_email,
                    "last_from_name": last.from_name,
                    "message_count": len(metas),
                    "mirrored": tid in mirrored,
                }
            )
    return out


def prepare_compose(
    db: Session,
    ctx: FunctionsCtx,
    account_id: str,
    *,
    mode: str,
    gmail_thread_id: str | None = None,
    message_id: str | None = None,
) -> dict:
    """Defaults for the composer — recipients, subject and the history that
    will be quoted — for ``mode`` reply | reply_all | forward | new."""
    if mode not in ("reply", "reply_all", "forward", "new"):
        raise Invalid("Mode must be reply, reply_all, forward or new.")
    account = accounts_svc.get_account(db, ctx, account_id)
    acct = AccountRef.of(account)
    out = {
        "account_id": acct.id,
        "mode": mode,
        "from": acct.google_email,
        "to": [],
        "cc": [],
        "subject": "",
        "gmail_thread_id": gmail_thread_id if mode in ("reply", "reply_all") else None,
        "in_reply_to_gmail_message_id": None,
        "quoted_html": None,
        "signature_html": acct.signature_html,
    }
    if mode == "new":
        return out
    if not gmail_thread_id:
        raise Invalid("Which conversation?")
    if not accounts_svc.is_connected(account):
        raise accounts_svc.MailboxUnavailable(
            f"{acct.google_email} isn't connected yet."
        )
    from app.services.functions.mailbox import outbox

    db.commit()
    with _client(db, ctx, account) as client:
        target = outbox._target(client, gmail_thread_id, message_id)
    if target is None:
        raise NotFound("That conversation has no messages.")
    headers = mime.headers_map(target.get("payload"))
    out["in_reply_to_gmail_message_id"] = str(target.get("id"))
    if mode == "forward":
        out["subject"] = mime.forward_subject(headers.get("subject"))
        out["quoted_html"] = outbox._quoted(target, forward=True)
        return out
    # Resolved here, before anyone approves the send — Reply-To never decides
    # whose message it was (outbox.reply_recipients).
    to, everyone_else = outbox.reply_recipients(target, acct.google_email)
    out["to"] = to
    out["subject"] = mime.reply_subject(headers.get("subject"))
    out["quoted_html"] = outbox._quoted(target, forward=False)
    if mode == "reply_all":
        out["cc"] = everyone_else
    return out


def compose_send(
    db: Session,
    ctx: FunctionsCtx,
    account_id: str,
    *,
    mode: str,
    to=(),
    cc=(),
    bcc=(),
    subject: str | None = None,
    body_html: str,
    gmail_thread_id: str | None = None,
    in_reply_to_gmail_message_id: str | None = None,
    enquiry_id: str | None = None,
    event_id: str | None = None,
    idempotency_key: str,
    price_override_reason: str | None = None,
    quote: bool = True,
):
    """Send from the composer (reply / reply all / forward / new) through the
    outbox. Replies and new emails about an enquiry get the pricing warning;
    a forward is internal handling and does not."""
    from app.services.functions.mailbox import outbox, pricing

    purposes = {
        "reply": "reply",
        "reply_all": "reply",
        "forward": "forward",
        "new": "compose",
    }
    if mode not in purposes:
        raise Invalid("Mode must be reply, reply_all, forward or new.")
    override = None
    if mode != "forward":
        about = enquiry_id or pricing.linked_enquiry(
            db, ctx, account_id, gmail_thread_id
        )
        override = pricing.check(db, ctx, about, body_html, price_override_reason)
    return outbox.send(
        db,
        ctx,
        account_id,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_html=body_html,
        thread_id=gmail_thread_id,
        in_reply_to=in_reply_to_gmail_message_id,
        purpose=purposes[mode],
        refs={"enquiry_id": enquiry_id, "event_id": event_id},
        idempotency_key=idempotency_key,
        price_override_reason=override,
        quote=quote,
    )


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

#: Gmail's own attachment limit; anything bigger is refused before download.
MAX_ATTACHMENT = 25 * 1024 * 1024
#: Types a browser would run or render as a page: served as bytes instead
#: (the download is same-origin with Norm, so an attached .html must never
#: be able to act as a Norm page).
_ACTIVE_TYPES = ("html", "xml", "javascript", "ecmascript", "script", "svg")


class AttachmentTooLarge(FunctionsError):
    status_code = 413
    code = "attachment_too_large"


@dataclass
class AttachmentFile:
    filename: str
    mime_type: str
    data: bytes


def safe_content_type(mime_type: str | None) -> str:
    value = (mime_type or "").strip().lower()
    if not re.fullmatch(
        r"[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,127}", value
    ) or any(word in value for word in _ACTIVE_TYPES):
        return "application/octet-stream"
    return value


def download_attachment(
    db: Session,
    ctx: FunctionsCtx,
    account_id: str,
    gmail_thread_id: str,
    gmail_message_id: str,
    attachment_ref: str,
) -> AttachmentFile:
    """One attachment of one message, fetched live from Gmail. Visible where
    the thread is (the mailbox at one of the caller's venues); a read, so
    shadow mode allows it. ``attachment_ref`` is the attachment id or the
    part id from the thread view. The message must be on that thread."""
    account = accounts_svc.get_account(db, ctx, account_id)
    if not accounts_svc.is_connected(account):
        raise accounts_svc.MailboxUnavailable(
            f"{account.google_email} isn't connected, so its attachments can't be fetched."
        )
    db.commit()  # no connection held while Gmail works
    missing = NotFound("That attachment isn't on the message any more.")
    with _client(db, ctx, account) as client:
        try:
            message = client.message(gmail_message_id, fmt="full")
        except GmailNotFound:
            raise NotFound("That message is no longer in the mailbox.")
        if str(message.get("threadId")) != gmail_thread_id:
            raise NotFound("That message is no longer in the mailbox.")
        part = mime.find_attachment(message.get("payload"), attachment_ref)
        if part is None:
            raise missing
        body = part.get("body") or {}
        if int(body.get("size") or 0) > MAX_ATTACHMENT:
            raise AttachmentTooLarge(
                "That attachment is over 25 MB — open it in Gmail."
            )
        try:
            got = client.attachment(gmail_message_id, body["attachmentId"])
        except GmailNotFound:
            raise missing
    data = mime.b64url_decode(got.get("data"))
    if len(data) > MAX_ATTACHMENT:
        raise AttachmentTooLarge("That attachment is over 25 MB — open it in Gmail.")
    return AttachmentFile(
        filename=part.get("filename") or "attachment",
        mime_type=safe_content_type(part.get("mimeType")),
        data=data,
    )
