"""Mailbox sync — every connected venue mailbox, every minute.

Cloud Scheduler calls ``/internal/sync-mailboxes`` each minute; the endpoint
runs :func:`sync_mailboxes` in a threadpool with a ~45 second deadline. The
rules, each the fix for a way mail goes missing:

**One worker per mailbox.** A worker first claims the mailbox's lease
(``UPDATE … SET lease_until, lease_owner WHERE lease_until IS NULL OR
lease_until < now RETURNING``). Every cursor write afterwards is fenced
(``WHERE lease_owner = me``): a worker that overran its lease and lost it to
another cannot move the cursor, it just stops.

**The cursor only moves past what was written.** Gmail's history is read a
page at a time; each page's messages are written and the cursor checkpointed
at the page's largest history id in the same transaction. The response's own
``historyId`` (the mailbox's current position) is taken only on the last
page — taking it earlier skips every page not yet read, which is how Orbit
lost history after its fifth page. A 429 or 5xx stops the mailbox's tick
without moving anything; the next tick reads the same page again.

**Initial sync and resync are resumable.** The profile's history id is read
and stored FIRST (status ``resyncing``), then the 90-day window is listed
and fetched over as many ticks as it takes; only when every message is in
does the account go ``active``, and the history replay from the stored id
re-applies anything that changed meanwhile (writes are idempotent). An
expired cursor (history 404) starts the same resync; when it completes,
mirror rows in the window that Gmail no longer has are removed.

**Nothing blocks the cursor forever.** A message Gmail keeps failing on is
retried, then parked in ``fn_mailbox_retry`` (backing off) so the cursor can
move past it.

**A dead grant stops everything for that mailbox, loudly once.**
``invalid_grant`` (or a grant without Gmail) marks the account
``needs_reconnect``: it is no longer polled, sends refuse, and the venue's
people are emailed once. Reconnecting — from Functions or from Settings →
Venues, which store onto the same connection — brings it back.

**No session is held across Gmail.** Each write is its own short
transaction; the Gmail calls happen between them.

Placeholder accounts (the importer's, not connected) are never touched here.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import socket
import time
import uuid
from contextlib import contextmanager
from typing import Callable

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnMailboxAccount,
    FnMailboxMessage,
    FnMailboxRetry,
)
from app.db.models import Connection, Organization, Venue
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import gmail_client, mirror
from app.services.functions.mailbox.classify import OurAddresses, our_addresses
from app.services.functions.mailbox.gmail_client import (
    GmailError,
    GmailNotFound,
    GmailRateLimited,
    GmailTransient,
    MailboxAuthError,
)
from app.services.functions.mailbox.mirror import AccountRef

logger = logging.getLogger(__name__)

LEASE = dt.timedelta(seconds=120)
WINDOW_DAYS = 90
WINDOW_QUERY = f"newer_than:{WINDOW_DAYS}d -in:chats -in:drafts"
#: Messages already mirrored are re-read on a resync only when this recent —
#: their labels (unread, inbox, folder) are what may have moved meanwhile.
RESYNC_REFRESH = dt.timedelta(days=7)
MAX_WINDOW_MESSAGES = 50_000
#: A message failing this many ticks running is parked so the cursor moves on.
PARK_AFTER = 3
#: Parked items are retried this many times in all, backing off, then left.
MAX_ATTEMPTS = 8
BACKOFF_MINUTES = (1, 5, 15, 60, 240, 720)
#: Every this-many minutes a tick re-checks every draft handed to Gmail,
#: not just those whose thread changed.
DRAFT_SWEEP_MINUTES = 10
#: Outbound mail newer than this is read in full for our quote links.
QUOTE_SCAN = dt.timedelta(days=3)
SYNCING = accounts_svc.SYNCING

SessionFactory = Callable[[], Session]


def _clock() -> float:
    """The deadline clock (tests replace it)."""
    return time.monotonic()


class LostLease(Exception):
    """Another worker owns this mailbox now — stop without writing."""


class OutOfTime(Exception):
    pass


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def default_session_factory() -> Session:
    from app.db.engine import SessionLocal

    return SessionLocal()


@contextmanager
def _session(factory: SessionFactory):
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------

_T = FnMailboxAccount.__table__


def claim(
    db: Session, account_id: str, owner: str, now: dt.datetime
) -> AccountRef | None:
    """Take the mailbox's lease, or None when another worker holds it (or
    the mailbox isn't syncable)."""
    row = db.execute(
        update(_T)
        .where(
            _T.c.id == account_id,
            _T.c.connection_id.isnot(None),
            _T.c.status.in_(SYNCING),
            or_(_T.c.lease_until.is_(None), _T.c.lease_until < now),
        )
        .values(lease_until=now + LEASE, lease_owner=owner)
        .returning(*_T.c)
    ).first()
    return AccountRef.of(row) if row is not None else None


def _fenced(
    db: Session, account_id: str, owner: str, now: dt.datetime, **values
) -> None:
    """Write sync state only while still holding the lease (extending it)."""
    done = db.execute(
        update(_T)
        .where(_T.c.id == account_id, _T.c.lease_owner == owner)
        .values(lease_until=now + LEASE, **values)
        .returning(_T.c.id)
    ).first()
    if done is None:
        raise LostLease(account_id)


def release(db: Session, account_id: str, owner: str, **values) -> bool:
    done = db.execute(
        update(_T)
        .where(_T.c.id == account_id, _T.c.lease_owner == owner)
        .values(lease_until=None, lease_owner=None, **values)
        .returning(_T.c.id)
    ).first()
    return done is not None


# ---------------------------------------------------------------------------
# One mailbox, one tick
# ---------------------------------------------------------------------------


class _Tick:
    """The state of one mailbox's tick (no ORM rows, no open session)."""

    def __init__(
        self,
        account: AccountRef,
        *,
        owner: str,
        deadline: float,
        session_factory: SessionFactory,
        client,
        ours: OurAddresses,
        live: bool,
        now: Callable[[], dt.datetime],
    ):
        self.account = account
        self.owner = owner
        self.deadline = deadline
        self.sf = session_factory
        self.client = client
        self.ours = ours
        self.live = live
        self.now = now
        self.stats = {
            "pages": 0,
            "messages": 0,
            "deleted": 0,
            "parked": 0,
            "threads": 0,
        }
        self.touched: set[str] = set()
        self.labels_refreshed = False

    def check_time(self) -> None:
        if _clock() >= self.deadline:
            raise OutOfTime()

    # -- fetching -----------------------------------------------------------
    def fetch(self, ids: list[str]) -> tuple[list[mirror.MessageMeta], set[str], bool]:
        """Metadata for ``ids`` → ``(metas, gone, complete)``. ``complete``
        is False when the deadline came first (what was fetched is still
        returned, to be written). A rate limit stops the tick; a message that
        keeps failing is parked and skipped."""
        metas: list[mirror.MessageMeta] = []
        gone: set[str] = set()
        for message_id in ids:
            if _clock() >= self.deadline:
                return metas, gone, False
            try:
                meta = mirror.parse_message(self.client.message(message_id))
            except GmailNotFound:
                gone.add(message_id)
                continue
            except GmailRateLimited:
                raise
            except GmailTransient as exc:
                if self.note_failure("message", message_id, exc.message) < PARK_AFTER:
                    raise
                self.stats["parked"] += 1
                continue
            except MailboxAuthError:
                raise
            except GmailError as exc:
                self.note_failure("message", message_id, exc.message, park=True)
                self.stats["parked"] += 1
                continue
            if meta.is_binned:
                gone.add(message_id)  # binned or spammed: not mail any more
            else:
                metas.append(meta)
        return metas, gone, True

    def note_failure(self, kind: str, ref: str, error: str, *, park=False) -> int:
        now = self.now()
        with _session(self.sf) as db:
            table = FnMailboxRetry.__table__
            stmt = insert(table).values(
                organization_id=self.account.organization_id,
                account_id=self.account.id,
                kind=kind,
                ref=ref,
                attempts=1,
                last_error=error[:1000],
                next_attempt_at=now + dt.timedelta(minutes=BACKOFF_MINUTES[0]),
                parked_at=now if park else None,
            )
            attempts = table.c.attempts + 1
            row = db.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_fn_mailbox_retry_ref",
                    set_={
                        "attempts": attempts,
                        "last_error": stmt.excluded.last_error,
                        "updated_at": now,
                    },
                ).returning(table.c.id, table.c.attempts, table.c.parked_at)
            ).first()
            n = row.attempts
            if (park or n >= PARK_AFTER) and row.parked_at is None:
                db.execute(
                    update(table)
                    .where(table.c.id == row.id)
                    .values(parked_at=now, next_attempt_at=now + _backoff(n))
                )
        return n

    # -- writing --------------------------------------------------------------
    def write(
        self,
        metas: list[mirror.MessageMeta],
        gone: set[str],
        *,
        cursor: int | None = None,
        status: str | None = None,
    ) -> None:
        """One transaction: fence + cursor first, then the rows."""
        now = self.now()
        values: dict = {}
        if cursor is not None:
            values["history_id"] = cursor
        if status is not None:
            values["status"] = status
        with _session(self.sf) as db:
            _fenced(db, self.account.id, self.owner, now, **values)
            touched = mirror.delete_messages(db, self.account, gone)
            touched |= mirror.upsert_messages(db, self.account, metas, self.ours)
            if metas or gone:
                ok_ids = [m.gmail_message_id for m in metas] + sorted(gone)
                db.execute(
                    FnMailboxRetry.__table__.delete().where(
                        FnMailboxRetry.account_id == self.account.id,
                        FnMailboxRetry.kind == "message",
                        FnMailboxRetry.ref.in_(ok_ids),
                    )
                )
            mirror.recompute_threads(db, self.account, touched)
            unknown = self._unknown_labels(db, metas)
            mirror.sync_folders_from_labels(db, self.account, touched)
        self.touched |= touched
        self.stats["messages"] += len([m for m in metas if not m.is_draft])
        self.stats["deleted"] += len(gone)
        self.stats["threads"] = len(self.touched)
        if unknown and not self.labels_refreshed:
            self.refresh_labels()
            with _session(self.sf) as db:
                mirror.sync_folders_from_labels(db, self.account, touched)
        self.scan_quote_links(metas)

    def _unknown_labels(self, db: Session, metas) -> bool:
        seen = {label for m in metas for label in m.label_ids}
        return bool(seen - mirror.known_label_ids(db, self.account.id))

    def refresh_labels(self) -> None:
        labels = self.client.labels()
        with _session(self.sf) as db:
            mirror.replace_labels(db, self.account, labels)
        self.labels_refreshed = True

    def scan_quote_links(self, metas) -> None:
        """Outbound mail sent outside Norm that carries our quote link is a
        quote sent (``fn_proposal_events`` source ``gmail``)."""
        from app.services.functions.mailbox import sending

        cutoff = self.now() - QUOTE_SCAN
        candidates = [
            m
            for m in metas
            if not m.is_draft
            and m.internal_date >= cutoff
            and (m.from_email == self.account.google_email or "SENT" in m.label_ids)
        ]
        if candidates:
            sending.record_gmail_quote_links(
                self.account, self.client, self.sf, candidates
            )

    # -- the two modes ----------------------------------------------------------
    def incremental(self) -> None:
        start = int(self.account.history_id)
        page_token = None
        while True:
            self.check_time()
            try:
                resp = self.client.history(start, page_token)
            except GmailNotFound:
                raise _HistoryExpired()
            records = resp.get("history") or []
            changed: list[str] = []
            deleted: set[str] = set()
            for h in records:
                for item in h.get("messagesAdded") or []:
                    msg = item.get("message") or {}
                    if "DRAFT" in (msg.get("labelIds") or []):
                        continue
                    if msg.get("id") and msg["id"] not in changed:
                        changed.append(msg["id"])
                for key in ("labelsAdded", "labelsRemoved"):
                    for item in h.get(key) or []:
                        msg = item.get("message") or {}
                        if msg.get("id") and msg["id"] not in changed:
                            changed.append(msg["id"])
                for item in h.get("messagesDeleted") or []:
                    msg = item.get("message") or {}
                    if msg.get("id"):
                        deleted.add(msg["id"])
                        if msg.get("threadId"):
                            self.touched.add(msg["threadId"])
                for item in h.get("messages") or []:
                    if item.get("threadId"):
                        self.touched.add(item["threadId"])
            changed = [i for i in changed if i not in deleted]
            metas, gone, complete = self.fetch(changed)
            if not complete:
                # Out of time mid-page: keep what was read, move no cursor —
                # the next tick reads this page again (idempotently).
                self.write(metas, gone | deleted)
                raise OutOfTime()
            next_page = resp.get("nextPageToken")
            if next_page:
                ids = [int(h["id"]) for h in records if h.get("id")]
                cursor = max(ids) if ids else None
            else:
                cursor = int(resp.get("historyId") or 0) or None
            if cursor is not None and cursor < start:
                cursor = None
            self.write(metas, gone | deleted, cursor=cursor)
            self.stats["pages"] += 1
            if not next_page:
                return
            page_token = next_page

    def resync(self) -> bool:
        """Initial sync / 404 resync. True when the window is complete."""
        account = self.account
        if account.status != "resyncing" or account.history_id is None:
            profile = self.client.profile()
            captured = int(profile.get("historyId") or 0)
            with _session(self.sf) as db:
                _fenced(
                    db,
                    account.id,
                    self.owner,
                    self.now(),
                    status="resyncing",
                    history_id=captured,
                )
            self.account = account = _replace(
                account, status="resyncing", history_id=captured
            )
        if not self.labels_refreshed:
            self.refresh_labels()

        # What is mirrored BEFORE listing: a message sent while the window is
        # being listed must not look like one Gmail no longer has. Rows the
        # importer wrote (rfc822_message_id NULL — Orbit's copy, flags
        # included) count as not mirrored: the first sync re-reads them all.
        now = self.now()
        with _session(self.sf) as db:
            have: dict = {}
            imported: dict = {}
            for gmail_id, when, rfc822 in db.execute(
                select(
                    FnMailboxMessage.gmail_message_id,
                    FnMailboxMessage.internal_date,
                    FnMailboxMessage.rfc822_message_id,
                ).where(FnMailboxMessage.account_id == account.id)
            ):
                (imported if rfc822 is None else have)[gmail_id] = when
            parked = {
                r[0]
                for r in db.execute(
                    select(FnMailboxRetry.ref).where(
                        FnMailboxRetry.account_id == account.id,
                        FnMailboxRetry.kind == "message",
                        FnMailboxRetry.parked_at.isnot(None),
                    )
                )
            }

        # Listing and fetching interleave, a page at a time: each tick lists
        # again from the newest (already-mirrored pages cost one cheap call
        # each) and carries on where the last tick stopped, so even a huge
        # window makes progress every minute.
        recent = now - RESYNC_REFRESH
        listed: list[str] = []
        page_token = None
        while True:
            self.check_time()
            resp = self.client.list_messages(WINDOW_QUERY, page_token=page_token)
            ids = [m["id"] for m in resp.get("messages") or [] if m.get("id")]
            listed += ids
            todo = [
                i
                for i in ids
                if i not in parked and (i not in have or have[i] >= recent)
            ]
            for start in range(0, len(todo), 50):
                metas, gone, complete = self.fetch(todo[start : start + 50])
                self.write(metas, gone)
                self.stats["pages"] += 1
                if not complete:
                    raise OutOfTime()
            page_token = resp.get("nextPageToken")
            if not page_token or len(listed) >= MAX_WINDOW_MESSAGES:
                break
        if page_token is not None:
            logger.warning(
                "mailbox %s: the %d-day window holds over %d messages; the "
                "oldest are not mirrored",
                account.id,
                WINDOW_DAYS,
                MAX_WINDOW_MESSAGES,
            )
            self.write([], set(), status="active")
            self.account = _replace(self.account, status="active")
            return True
        listed_set = set(listed)
        # Imported rows the window didn't cover (older linked threads) are
        # re-read one by one: after this, every row is the sync's own.
        leftover = [i for i in imported if i not in listed_set and i not in parked]
        for start in range(0, len(leftover), 50):
            metas, gone, complete = self.fetch(leftover[start : start + 50])
            self.write(metas, gone)
            self.stats["pages"] += 1
            if not complete:
                raise OutOfTime()
        cutoff = now - dt.timedelta(days=WINDOW_DAYS - 1)
        stale = [
            i for i, when in have.items() if when >= cutoff and i not in listed_set
        ]
        self.write([], set(stale), status="active")
        self.account = _replace(self.account, status="active")
        return True

    def retry_parked(self) -> None:
        now = self.now()
        with _session(self.sf) as db:
            due = [
                (r.ref, r.attempts)
                for r in db.query(FnMailboxRetry)
                .filter(
                    FnMailboxRetry.account_id == self.account.id,
                    FnMailboxRetry.kind == "message",
                    FnMailboxRetry.parked_at.isnot(None),
                    FnMailboxRetry.attempts < MAX_ATTEMPTS,
                    FnMailboxRetry.next_attempt_at <= now,
                )
                .order_by(FnMailboxRetry.next_attempt_at)
                .limit(20)
            ]
        for ref, attempts in due:
            self.check_time()
            try:
                meta = mirror.parse_message(self.client.message(ref))
                self.write([meta], set())
            except GmailNotFound:
                self.write([], {ref})
            except (GmailRateLimited, MailboxAuthError):
                raise
            except GmailError as exc:
                with _session(self.sf) as db:
                    db.execute(
                        update(FnMailboxRetry.__table__)
                        .where(
                            FnMailboxRetry.account_id == self.account.id,
                            FnMailboxRetry.kind == "message",
                            FnMailboxRetry.ref == ref,
                        )
                        .values(
                            attempts=attempts + 1,
                            last_error=exc.message[:1000],
                            next_attempt_at=(
                                now + _backoff(attempts + 1)
                                if attempts + 1 < MAX_ATTEMPTS
                                else None
                            ),
                        )
                    )


class _HistoryExpired(Exception):
    pass


def _replace(account: AccountRef, **changes) -> AccountRef:
    from dataclasses import replace

    return replace(account, **changes)


def _backoff(attempts: int) -> dt.timedelta:
    i = max(0, min(attempts - 1, len(BACKOFF_MINUTES) - 1))
    return dt.timedelta(minutes=BACKOFF_MINUTES[i])


def _client_factory(account: AccountRef, *, writable: bool, db_for_tokens=None):
    return gmail_client.client_for(account, writable=writable, db=db_for_tokens)


def sync_account(
    account_id: str,
    *,
    owner: str | None = None,
    deadline: float | None = None,
    session_factory: SessionFactory | None = None,
    client_factory=None,
    now: Callable[[], dt.datetime] | None = None,
    token_db: Session | None = None,
) -> dict:
    """One tick for one mailbox. Never raises: the outcome is in the result
    and in the account's health columns."""
    sf = session_factory or default_session_factory
    owner = owner or worker_id()
    deadline = deadline if deadline is not None else _clock() + 45
    now_fn = now or _utcnow
    make_client = client_factory or _client_factory
    result = {"account_id": account_id}

    with _session(sf) as db:
        account = claim(db, account_id, owner, now_fn())
        if account is not None:
            mode = (
                db.query(Organization.functions_mode)
                .filter(Organization.id == account.organization_id)
                .scalar()
            )
            ours = our_addresses(db, account.organization_id)
    if account is None:
        return {**result, "skipped": "busy"}
    if mode not in ("shadow", "live"):
        with _session(sf) as db:
            release(db, account.id, owner)
        return {**result, "skipped": "functions_off"}

    client = None
    try:
        client = make_client(account, writable=(mode == "live"), db_for_tokens=token_db)
        tick = _Tick(
            account,
            owner=owner,
            deadline=deadline,
            session_factory=sf,
            client=client,
            ours=ours,
            live=(mode == "live"),
            now=now_fn,
        )
        from app.services.functions.mailbox import drafts as drafts_svc
        from app.services.functions.mailbox import outbox as outbox_svc

        complete = True
        try:
            # Every tick, uncached: the connection must still sign in as this
            # mailbox (a grant swapped underneath raises MailboxAuthError →
            # needs_reconnect).
            client.verify_identity(fresh=True)
            outbox_svc.reconcile_account(account, client, sf, now_fn())
            if account.status == "resyncing" or account.history_id is None:
                complete = tick.resync()
            else:
                try:
                    tick.incremental()
                except _HistoryExpired:
                    tick.account = _replace(
                        tick.account, status="active", history_id=None
                    )
                    complete = tick.resync()
            tick.retry_parked()
            sweep = now_fn().minute % DRAFT_SWEEP_MINUTES == 0 or not complete
            drafts_svc.reconcile_handovers(
                tick.account,
                client,
                sf,
                now_fn(),
                thread_ids=None if sweep else tick.touched,
            )
        except OutOfTime:
            complete = False
        with _session(sf) as db:
            release(
                db,
                account.id,
                owner,
                last_ok_at=now_fn(),
                last_error=None,
                consecutive_failures=0,
            )
        return {
            **result,
            "ok": True,
            "complete": complete,
            "status": tick.account.status,
            **tick.stats,
        }
    except LostLease:
        return {**result, "skipped": "lease_lost"}
    except MailboxAuthError as exc:
        _dead_grant(sf, account, owner, exc.message, now_fn())
        return {**result, "ok": False, "needs_reconnect": True, "error": exc.message}
    except GmailTransient as exc:
        _failed(sf, account.id, owner, f"Gmail: {exc.message}")
        return {**result, "ok": False, "transient": True, "error": exc.message}
    except GmailError as exc:
        # Not the mailbox's fault and not passing (the API switched off, the
        # connector unset): recorded on the account, where health shows it.
        _failed(sf, account.id, owner, f"Gmail: {exc.message}")
        return {**result, "ok": False, "error": exc.message}
    except Exception as exc:  # noqa: BLE001 — one mailbox must not stop the others
        logger.exception("mailbox sync failed for %s", account.id)
        _failed(sf, account.id, owner, f"{type(exc).__name__}: {exc}"[:500])
        return {**result, "ok": False, "error": str(exc)[:300]}
    finally:
        if client is not None:
            client.close()


def _failed(sf: SessionFactory, account_id: str, owner: str, error: str) -> None:
    try:
        with _session(sf) as db:
            release(
                db,
                account_id,
                owner,
                last_error=error[:1000],
                consecutive_failures=_T.c.consecutive_failures + 1,
            )
    except Exception:  # noqa: BLE001
        logger.exception("could not record mailbox failure for %s", account_id)


def _dead_grant(
    sf: SessionFactory, account: AccountRef, owner: str, reason: str, now: dt.datetime
) -> None:
    """``needs_reconnect``: polling stops, sends refuse, people are told once."""
    from app.services.functions.mailbox.notify import send_reconnect_notice

    with _session(sf) as db:
        released = release(
            db,
            account.id,
            owner,
            status="needs_reconnect",
            last_error=reason[:1000],
            consecutive_failures=_T.c.consecutive_failures + 1,
        )
        if not released:
            return
        row = db.query(FnMailboxAccount).filter(FnMailboxAccount.id == account.id).one()
        if row.reconnect_notified_at is None:
            venue = db.query(Venue.name).filter(Venue.id == row.venue_id).scalar()
            try:
                send_reconnect_notice(db, row, venue or "the venue", reason)
            except Exception:  # noqa: BLE001 — the flag matters more than the email
                logger.exception("reconnect notice failed for mailbox %s", account.id)
            db.execute(
                update(_T)
                .where(_T.c.id == account.id)
                .values(reconnect_notified_at=now)
            )


# ---------------------------------------------------------------------------
# Every mailbox
# ---------------------------------------------------------------------------


def _heal_reconnected(db: Session, now: dt.datetime) -> list[str]:
    """Mailboxes marked dead whose connection has since been re-authorised
    (e.g. reconnected from Settings → Venues, which stores onto the same
    connection): back to syncing."""
    healed = (
        db.query(FnMailboxAccount.id, FnMailboxAccount.history_id)
        .join(Connection, Connection.id == FnMailboxAccount.connection_id)
        .filter(
            FnMailboxAccount.status == "needs_reconnect",
            Connection.needs_reconnect.is_(False),
            Connection.access_token.isnot(None),
            Connection.last_auth_checked_at > FnMailboxAccount.updated_at,
        )
        .all()
    )
    for account_id, history_id in healed:
        db.execute(
            update(_T)
            .where(_T.c.id == account_id, _T.c.status == "needs_reconnect")
            .values(
                status="active" if history_id else "resyncing",
                reconnect_notified_at=None,
            )
        )
    return [h[0] for h in healed]


def due_accounts(db: Session, now: dt.datetime) -> list[str]:
    return [
        r[0]
        for r in db.query(FnMailboxAccount.id)
        .join(Organization, Organization.id == FnMailboxAccount.organization_id)
        .filter(
            FnMailboxAccount.connection_id.isnot(None),
            FnMailboxAccount.status.in_(SYNCING),
            Organization.functions_mode.in_(("shadow", "live")),
            or_(
                FnMailboxAccount.lease_until.is_(None),
                FnMailboxAccount.lease_until < now,
            ),
        )
        .order_by(FnMailboxAccount.last_ok_at.asc().nulls_first())
    ]


def lagging_accounts(db: Session, now: dt.datetime) -> list[dict]:
    """Active mailboxes (in an org using Functions) that haven't synced
    cleanly for LAG_ALERT — the endpoint answers non-200 while any exist."""
    rows = (
        db.query(FnMailboxAccount)
        .join(Organization, Organization.id == FnMailboxAccount.organization_id)
        .filter(
            FnMailboxAccount.connection_id.isnot(None),
            FnMailboxAccount.status.in_(SYNCING),
            Organization.functions_mode.in_(("shadow", "live")),
            or_(
                FnMailboxAccount.last_ok_at.is_(None),
                FnMailboxAccount.last_ok_at < now - accounts_svc.LAG_ALERT,
            ),
        )
        .all()
    )
    return [
        {
            "account_id": a.id,
            "google_email": a.google_email,
            "last_ok_at": a.last_ok_at.isoformat() if a.last_ok_at else None,
            "last_error": a.last_error,
        }
        for a in rows
    ]


def sync_mailboxes(
    *,
    deadline_seconds: float = 45,
    session_factory: SessionFactory | None = None,
    client_factory=None,
    max_workers: int = 3,
    now: Callable[[], dt.datetime] | None = None,
    token_db: Session | None = None,
) -> dict:
    """The scheduler's tick: every due mailbox, a few at a time, inside the
    deadline. Returns what happened and which mailboxes lag."""
    sf = session_factory or default_session_factory
    now_fn = now or _utcnow
    deadline = _clock() + deadline_seconds
    owner = worker_id()
    with _session(sf) as db:
        healed = _heal_reconnected(db, now_fn())
    with _session(sf) as db:
        ids = due_accounts(db, now_fn())

    def run(account_id: str) -> dict:
        return sync_account(
            account_id,
            owner=owner,
            deadline=deadline,
            session_factory=sf,
            client_factory=client_factory,
            now=now_fn,
            token_db=token_db,
        )

    if max_workers <= 1 or len(ids) <= 1:
        results = [run(i) for i in ids]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(run, ids))
    with _session(sf) as db:
        lagging = lagging_accounts(db, now_fn())
    return {
        "mailboxes": len(ids),
        "healed": healed,
        "results": results,
        "lagging": lagging,
    }


def sync_now(
    db: Session,
    ctx,
    *,
    account_ids: list[str] | None = None,
    deadline_seconds: float = 10,
    fresh_seconds: int = 20,
    client_factory=None,
) -> list[dict]:
    """ "Sync now" when someone opens the Inbox: each visible connected
    mailbox not synced in the last ``fresh_seconds``, lease-guarded (a
    mailbox another worker is syncing is skipped, not waited for). Commits
    the caller's session first — no connection is held across Gmail."""
    from app.services.functions.mailbox._sessions import independent_session

    now = _utcnow()
    q = accounts_svc.visible_accounts(db, ctx).filter(
        FnMailboxAccount.connection_id.isnot(None),
        FnMailboxAccount.status.in_(SYNCING),
    )
    if account_ids:
        q = q.filter(FnMailboxAccount.id.in_(account_ids))
    targets = [(a.id, a.last_ok_at) for a in q.all()]
    db.commit()
    deadline = _clock() + deadline_seconds
    out = []
    for account_id, last_ok in targets:
        if last_ok and (now - last_ok).total_seconds() < fresh_seconds:
            out.append({"account_id": account_id, "skipped": "fresh"})
            continue
        out.append(
            sync_account(
                account_id,
                deadline=deadline,
                session_factory=lambda: independent_session(db),
                client_factory=client_factory,
                token_db=db,
            )
        )
    return out
