"""The mail mirror's writes: messages, thread rollups, labels, folders.

Everything here is idempotent, because the sync replays: a message is
upserted on ``(account, gmail message id)``, a thread's rollups are
recomputed from its messages (never incremented), and a mailbox's labels
are replaced as a set. Replaying any page of history, or the whole initial
window, converges on the same rows.

Gmail drafts are never mirrored: an unsent draft is not mail anyone sent,
and letting it into a thread made Orbit's live threads look archived and
unfiled. Drafts handed over from Norm are tracked by draft id instead
(``drafts.py``).
"""

from __future__ import annotations

import datetime as dt
import html as _html
import re
from dataclasses import dataclass, field

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnMailboxLabel,
    FnMailboxMessage,
    FnMailboxThread,
    FnThreadFolder,
)
from app.db.models import _now
from app.services.functions.mailbox import mime
from app.services.functions.mailbox.classify import OurAddresses, message_flags

GMAIL_SET = "gmail"


@dataclass(frozen=True)
class AccountRef:
    """What a sync or send needs about a mailbox — a snapshot, so no ORM row
    (and no session) is held while Gmail is being called."""

    id: str
    organization_id: str
    venue_id: str
    google_email: str
    connection_id: str | None
    history_id: int | None
    status: str
    display_name: str | None = None
    signature_html: str | None = None

    @classmethod
    def of(cls, row) -> "AccountRef":
        return cls(
            id=row.id,
            organization_id=row.organization_id,
            venue_id=row.venue_id,
            google_email=(row.google_email or "").strip().lower(),
            connection_id=row.connection_id,
            history_id=row.history_id,
            status=row.status,
            display_name=row.display_name,
            signature_html=row.signature_html,
        )


@dataclass
class MessageMeta:
    gmail_message_id: str
    gmail_thread_id: str
    internal_date: dt.datetime
    label_ids: list[str] = field(default_factory=list)
    snippet: str = ""
    from_email: str = ""
    from_name: str = ""
    to_emails: list[str] = field(default_factory=list)
    cc_emails: list[str] = field(default_factory=list)
    reply_to_email: str | None = None
    subject: str | None = None
    rfc822_message_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    has_attachments: bool = False

    @property
    def is_draft(self) -> bool:
        return "DRAFT" in self.label_ids

    @property
    def is_binned(self) -> bool:
        """In Trash or Spam: gone as far as the mirror is concerned (and
        back when restored — the label change brings it back)."""
        return "TRASH" in self.label_ids or "SPAM" in self.label_ids


def parse_message(resource: dict) -> MessageMeta:
    """A Gmail message resource (``metadata`` or ``full``) as mirror fields."""
    payload = resource.get("payload") or {}
    headers = mime.headers_map(payload)
    from_name, from_email = mime.parse_address(headers.get("from"))
    _, reply_to = mime.parse_address(headers.get("reply-to"))
    mime_type = str(payload.get("mimeType") or "").lower()
    has_attachments = mime_type.startswith("multipart/mixed") or any(
        (p.get("filename") or "") for p in (payload.get("parts") or [])
    )
    return MessageMeta(
        gmail_message_id=str(resource.get("id")),
        gmail_thread_id=str(resource.get("threadId")),
        internal_date=mime.internal_date(resource),
        label_ids=list(resource.get("labelIds") or []),
        snippet=_html.unescape(str(resource.get("snippet") or ""))[:1000],
        from_email=from_email,
        from_name=from_name,
        to_emails=[a for _, a in mime.parse_address_list(headers.get("to"))],
        cc_emails=[a for _, a in mime.parse_address_list(headers.get("cc"))],
        reply_to_email=reply_to or None,
        subject=mime.one_line((headers.get("subject") or "")[:2000])[:998] or None,
        # Never NULL for a row the sync writes ("" when the header is
        # missing): NULL marks a row the importer wrote, which the first
        # sync after connecting re-reads (sync.Tick.resync).
        rfc822_message_id=(headers.get("message-id") or "").strip(),
        headers=headers,
        has_attachments=has_attachments,
    )


def _ensure_threads(db: Session, account: AccountRef, thread_ids: set[str]) -> dict:
    """``{gmail thread id: fn_mailbox_threads.id}``, creating missing rows."""
    if not thread_ids:
        return {}
    table = FnMailboxThread.__table__
    db.execute(
        insert(table)
        .values(
            [
                {
                    "organization_id": account.organization_id,
                    "account_id": account.id,
                    "gmail_thread_id": t,
                    "label_ids": [],
                }
                for t in sorted(thread_ids)
            ]
        )
        .on_conflict_do_nothing(constraint="uq_fn_mailbox_threads_gmail")
    )
    rows = db.execute(
        select(table.c.id, table.c.gmail_thread_id).where(
            table.c.account_id == account.id,
            table.c.gmail_thread_id.in_(sorted(thread_ids)),
        )
    ).all()
    return {r.gmail_thread_id: r.id for r in rows}


def upsert_messages(
    db: Session, account: AccountRef, metas: list[MessageMeta], ours: OurAddresses
) -> set[str]:
    """Write messages (drafts, Trash and Spam skipped); returns the Gmail
    thread ids touched."""
    metas = [m for m in metas if not m.is_draft and not m.is_binned]
    if not metas:
        return set()
    threads = _ensure_threads(db, account, {m.gmail_thread_id for m in metas})
    now = _now()
    rows = []
    for m in metas:
        inbound, internal, auto = message_flags(
            mailbox_email=account.google_email,
            from_email=m.from_email,
            to_emails=m.to_emails,
            cc_emails=m.cc_emails,
            label_ids=m.label_ids,
            headers=m.headers,
            subject=m.subject,
            ours=ours,
        )
        rows.append(
            {
                "organization_id": account.organization_id,
                "account_id": account.id,
                "thread_id": threads[m.gmail_thread_id],
                "gmail_message_id": m.gmail_message_id,
                "gmail_thread_id": m.gmail_thread_id,
                "rfc822_message_id": m.rfc822_message_id,
                "internal_date": m.internal_date,
                "from_email": m.from_email or None,
                "from_name": m.from_name or None,
                "to_emails": m.to_emails,
                "cc_emails": m.cc_emails,
                "reply_to_email": m.reply_to_email,
                "subject": m.subject,
                "snippet": m.snippet,
                "label_ids": m.label_ids,
                "is_inbound": inbound,
                "is_internal": internal,
                "is_auto_reply": auto,
                "unread": "UNREAD" in m.label_ids,
                "has_attachments": m.has_attachments,
                "updated_at": now,
            }
        )
    stmt = insert(FnMailboxMessage.__table__).values(rows)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_fn_mailbox_messages_gmail",
        set_={
            col: getattr(excluded, col)
            for col in (
                "thread_id",
                "gmail_thread_id",
                "rfc822_message_id",
                "internal_date",
                "from_email",
                "from_name",
                "to_emails",
                "cc_emails",
                "reply_to_email",
                "subject",
                "snippet",
                "label_ids",
                "is_inbound",
                "is_internal",
                "is_auto_reply",
                "unread",
                "has_attachments",
                "updated_at",
            )
        },
    )
    db.execute(stmt)
    return {m.gmail_thread_id for m in metas}


def delete_messages(db: Session, account: AccountRef, message_ids) -> set[str]:
    """Remove messages Gmail no longer has; returns the threads touched."""
    ids = sorted(set(message_ids))
    if not ids:
        return set()
    table = FnMailboxMessage.__table__
    gone = db.execute(
        delete(table)
        .where(table.c.account_id == account.id, table.c.gmail_message_id.in_(ids))
        .returning(table.c.gmail_thread_id)
    ).all()
    return {r[0] for r in gone}


def recompute_threads(db: Session, account: AccountRef, thread_ids) -> None:
    """Rebuild each thread's rollups from its messages; drop empty threads.

    INBOX belongs to the conversation and our own sent reply never carries
    it, so it is read from the newest inbound message (else the newest);
    every other label is the union across the thread (Gmail applies filing
    labels thread-wide, but a later reply arrives without them)."""
    ids = sorted(set(thread_ids))
    if not ids:
        return
    m = FnMailboxMessage.__table__
    rows = db.execute(
        select(
            m.c.gmail_thread_id,
            m.c.internal_date,
            m.c.subject,
            m.c.snippet,
            m.c.from_email,
            m.c.from_name,
            m.c.label_ids,
            m.c.unread,
            m.c.is_inbound,
            m.c.is_internal,
        )
        .where(m.c.account_id == account.id, m.c.gmail_thread_id.in_(ids))
        .order_by(m.c.gmail_thread_id, m.c.internal_date.desc(), m.c.id.desc())
    ).all()
    by_thread: dict[str, list] = {}
    for r in rows:
        by_thread.setdefault(r.gmail_thread_id, []).append(r)
    t = FnMailboxThread.__table__
    empty = [tid for tid in ids if tid not in by_thread]
    if empty:
        db.execute(
            delete(t).where(
                t.c.account_id == account.id, t.c.gmail_thread_id.in_(empty)
            )
        )
    now = _now()
    for tid, msgs in by_thread.items():
        latest = msgs[0]
        inbound = next((x for x in msgs if x.is_inbound and not x.is_internal), None)
        labels: set[str] = set()
        for x in msgs:
            labels.update(label for label in (x.label_ids or []) if label != "INBOX")
        if "INBOX" in ((inbound or latest).label_ids or []):
            labels.add("INBOX")
        shown = inbound or latest
        db.execute(
            update(t)
            .where(t.c.account_id == account.id, t.c.gmail_thread_id == tid)
            .values(
                subject=latest.subject or (inbound.subject if inbound else None),
                snippet=latest.snippet,
                last_message_at=latest.internal_date,
                last_from_email=shown.from_email,
                last_from_name=shown.from_name,
                message_count=len(msgs),
                unread_count=sum(1 for x in msgs if x.unread),
                label_ids=sorted(labels),
                updated_at=now,
            )
        )


# ---------------------------------------------------------------------------
# Labels and folders
# ---------------------------------------------------------------------------

#: The "Actioned - …" labels staff already file with, by folder; the first
#: name is the one Norm files into when a mailbox has several.
FOLDER_LABEL_NAMES = {
    "enquiry": (
        "Actioned - Enquiry",
        "Actioned - Enquiries",
        "Actioned - Booking",
        "Actioned - Bookings",
    ),
    "reservation": (
        "Actioned - Reservation",
        "Actioned - Reservations",
        "Actioned - Event",
        "Actioned - Events",
    ),
    "other": (
        "Actioned - All Other Mail",
        "Actioned - Uncategorised",
        "Actioned - Uncategorized",
        "Actioned - Other",
    ),
}


def _normalise_label(name: str | None) -> str:
    text = (name or "").lower()
    text = re.sub("[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


_LABEL_FOLDER = {
    _normalise_label(n): folder
    for folder, names in FOLDER_LABEL_NAMES.items()
    for n in names
}


def folder_for_label(name: str | None) -> str | None:
    return _LABEL_FOLDER.get(_normalise_label(name))


def replace_labels(db: Session, account: AccountRef, labels: list[dict]) -> None:
    """The mailbox's labels, replaced as a set (renamed and deleted labels
    included), each mapped to its folder when it is an "Actioned" label."""
    table = FnMailboxLabel.__table__
    seen: list[str] = []
    now = _now()
    for label in labels:
        label_id = str(label.get("id") or "")
        if not label_id:
            continue
        seen.append(label_id)
        stmt = insert(table).values(
            organization_id=account.organization_id,
            account_id=account.id,
            gmail_label_id=label_id,
            name=str(label.get("name") or label_id),
            label_type=label.get("type"),
            folder=folder_for_label(label.get("name")),
        )
        db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_fn_mailbox_labels_gmail",
                set_={
                    "name": stmt.excluded.name,
                    "label_type": stmt.excluded.label_type,
                    "folder": stmt.excluded.folder,
                    "updated_at": now,
                },
            )
        )
    stale = delete(table).where(table.c.account_id == account.id)
    if seen:
        stale = stale.where(table.c.gmail_label_id.not_in(seen))
    db.execute(stale)


def folder_labels(db: Session, account_id: str) -> dict[str, str]:
    """``{folder: gmail label id}`` — the label Norm files into, per folder."""
    rows = db.execute(
        select(
            FnMailboxLabel.name, FnMailboxLabel.gmail_label_id, FnMailboxLabel.folder
        ).where(
            FnMailboxLabel.account_id == account_id,
            FnMailboxLabel.folder.isnot(None),
        )
    ).all()
    out: dict[str, str] = {}
    for folder, names in FOLDER_LABEL_NAMES.items():
        ranked = sorted(
            (r for r in rows if r.folder == folder),
            key=lambda r: next(
                (
                    i
                    for i, n in enumerate(names)
                    if _normalise_label(n) == _normalise_label(r.name)
                ),
                99,
            ),
        )
        if ranked:
            out[folder] = ranked[0].gmail_label_id
    return out


def label_folders(db: Session, account_id: str) -> dict[str, str]:
    """``{gmail label id: folder}`` for every Actioned label."""
    rows = db.execute(
        select(FnMailboxLabel.gmail_label_id, FnMailboxLabel.folder).where(
            FnMailboxLabel.account_id == account_id,
            FnMailboxLabel.folder.isnot(None),
        )
    ).all()
    return {r[0]: r[1] for r in rows}


def known_label_ids(db: Session, account_id: str) -> set[str]:
    return {
        r[0]
        for r in db.execute(
            select(FnMailboxLabel.gmail_label_id).where(
                FnMailboxLabel.account_id == account_id
            )
        )
    }


def sync_folders_from_labels(db: Session, account: AccountRef, thread_ids) -> None:
    """Follow filing done in Gmail: a thread's one "Actioned" label is its
    folder. A folder chosen in Norm that has not reached Gmail yet (or failed
    to) is left alone until it does — Norm's choice is the newer one."""
    ids = sorted(set(thread_ids))
    if not ids:
        return
    mapping = label_folders(db, account.id)
    t = FnMailboxThread.__table__
    threads = db.execute(
        select(t.c.gmail_thread_id, t.c.label_ids).where(
            t.c.account_id == account.id, t.c.gmail_thread_id.in_(ids)
        )
    ).all()
    present = {
        r.gmail_thread_id: {mapping[x] for x in (r.label_ids or []) if x in mapping}
        for r in threads
    }
    rows = {
        r.gmail_thread_id: r
        for r in db.query(FnThreadFolder).filter(
            FnThreadFolder.organization_id == account.organization_id,
            FnThreadFolder.account_id == account.id,
            FnThreadFolder.gmail_thread_id.in_(ids),
        )
    }
    now = _now()
    for tid in ids:
        folders = present.get(tid, set())
        row = rows.get(tid)
        synced = row is not None and (
            row.gmail_synced_at is not None or row.set_by_label == GMAIL_SET
        )
        if len(folders) == 1:
            (folder,) = folders
            if row is None:
                db.add(
                    FnThreadFolder(
                        organization_id=account.organization_id,
                        account_id=account.id,
                        gmail_thread_id=tid,
                        folder=folder,
                        set_by_label=GMAIL_SET,
                        set_at=now,
                        gmail_synced_at=now,
                    )
                )
            elif row.folder == folder:
                if row.gmail_synced_at is None:
                    row.gmail_synced_at = now
                    row.gmail_sync_error = None
            elif synced:
                row.folder = folder
                row.set_by_user_id = None
                row.set_by_label = GMAIL_SET
                row.set_at = now
                row.gmail_synced_at = now
                row.gmail_sync_error = None
        elif not folders and row is not None and synced:
            db.delete(row)
    db.flush()


def set_thread_labels(
    db: Session, account: AccountRef, gmail_thread_id: str, *, add=(), remove=()
) -> None:
    """Apply a label change Gmail has just confirmed to the mirror, so the
    Inbox shows it before the next sync."""
    m = FnMailboxMessage.__table__
    remove_set, add_list = set(remove), list(add)
    rows = db.execute(
        select(m.c.id, m.c.label_ids).where(
            m.c.account_id == account.id, m.c.gmail_thread_id == gmail_thread_id
        )
    ).all()
    for r in rows:
        labels = [x for x in (r.label_ids or []) if x not in remove_set]
        labels += [x for x in add_list if x not in labels]
        db.execute(update(m).where(m.c.id == r.id).values(label_ids=labels))
    recompute_threads(db, account, [gmail_thread_id])


def thread_message_rows(db: Session, account_id: str, gmail_thread_id: str):
    return (
        db.query(FnMailboxMessage)
        .filter(
            and_(
                FnMailboxMessage.account_id == account_id,
                FnMailboxMessage.gmail_thread_id == gmail_thread_id,
            )
        )
        .order_by(FnMailboxMessage.internal_date)
        .all()
    )
