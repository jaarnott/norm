"""The venue mailbox sync (services/functions/mailbox/sync.py) against a fake
Gmail — the failure modes the robustness spec is written around:

* the cursor checkpoints each history page at its largest id, takes the
  response's ``historyId`` only on the last page, and never moves on a 429,
  a 5xx or a deadline (Orbit dropped everything after its fifth page);
* initial sync and the 404 resync store the profile's history id FIRST, are
  resumable across ticks, and finish by removing window rows Gmail no longer
  has;
* leases: a second worker can't sync a leased mailbox, and a worker that
  lost its lease can't move the cursor;
* replays are idempotent; labels are replaced as sets; drafts are never
  mirrored; a message Gmail keeps failing on is parked, then retried;
* a dead grant marks the mailbox ``needs_reconnect``, stops polling and
  emails once; a re-authorised connection brings it back;
* the three flags status.py depends on, and the quote links in Gmail-sent
  mail that make an enquiry "quoted";
* placeholder accounts (the importer's) are never touched; health lags make
  the scheduler endpoint answer 503.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, update

from app.config import settings
from app.db.functions_models import (
    FnMailboxAccount,
    FnMailboxLabel,
    FnMailboxMessage,
    FnMailboxRetry,
    FnMailboxThread,
    FnProposalEvent,
    FnThreadFolder,
    FnVenueFunctionSettings,
)
from app.db.models import EmailLog
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import status as status_svc
from app.services.functions.mailbox import sync
from tests.functions_mailbox_fakes import (
    MAILBOX,
    FakeGmail,
    FakeTokens,
    install,
    mailbox_world,
    other_mailbox,
    sessions,
)
from tests.functions_world import add_contact, add_enquiry

UTC = dt.timezone.utc
CUSTOMER = "casey@clientco.nz"


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeGmail()
    install(monkeypatch, fake)
    monkeypatch.setattr(sync, "_clock", lambda: fake.clock)
    return fake


def _tick(db, w, fake, *, budget: float = 1000, **kw):
    out = sync.sync_account(
        w.account.id,
        session_factory=sessions(db),
        deadline=fake.clock + budget,
        **kw,
    )
    db.expire_all()
    return out


def _account(db, w) -> FnMailboxAccount:
    return db.query(FnMailboxAccount).filter(FnMailboxAccount.id == w.account.id).one()


def _messages(db, w) -> dict[str, FnMailboxMessage]:
    return {
        m.gmail_message_id: m
        for m in db.query(FnMailboxMessage).filter(
            FnMailboxMessage.account_id == w.account.id
        )
    }


def _inbound(fake, n=1, **kw) -> list[str]:
    return [
        fake.add_message(
            frm=f"Casey <{CUSTOMER}>", to=[MAILBOX], subject=f"Party {i}", **kw
        )
        for i in range(n)
    ]


class TestCursor:
    def test_pages_are_checkpointed_and_history_id_taken_only_at_the_end(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        ids = _inbound(fake, 5)  # five history records, two per page
        out = _tick(db_session, w, fake)
        assert out["ok"] and out["pages"] == 3
        assert set(_messages(db_session, w)) == set(ids)
        assert _account(db_session, w).history_id == fake.history_id

    def test_a_failure_mid_way_keeps_the_last_checkpoint_not_the_mailbox_position(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        ids = _inbound(fake, 5)
        first_page_max = int(fake.history[1]["id"])
        fake.fail["GET /history"] = [None, 503]  # page 1 fine, page 2 fails
        out = _tick(db_session, w, fake)
        assert out["ok"] is False and out["transient"]
        acct = _account(db_session, w)
        assert acct.history_id == first_page_max  # not fake.history_id
        assert set(_messages(db_session, w)) == set(ids[:2])
        assert acct.consecutive_failures == 1 and "Gmail" in acct.last_error

        out = _tick(db_session, w, fake)
        assert out["ok"]
        acct = _account(db_session, w)
        assert acct.history_id == fake.history_id
        assert set(_messages(db_session, w)) == set(ids)
        assert acct.consecutive_failures == 0 and acct.last_error is None

    def test_429_moves_nothing(self, db_session, fake):
        w = mailbox_world(db_session)
        _inbound(fake, 2)
        fake.fail["GET /history"] = [429]
        out = _tick(db_session, w, fake)
        assert out["ok"] is False
        assert _account(db_session, w).history_id == 1000
        assert _messages(db_session, w) == {}

    def test_running_out_of_time_mid_page_keeps_what_was_read_but_not_the_cursor(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        fake.page_size = 10
        ids = _inbound(fake, 4)
        # The identity check, the history page, then two message fetches.
        out = _tick(db_session, w, fake, budget=4)
        assert out["ok"] and out["complete"] is False
        assert _account(db_session, w).history_id == 1000
        assert set(_messages(db_session, w)) == set(ids[:2])
        _tick(db_session, w, fake)
        assert set(_messages(db_session, w)) == set(ids)

    def test_replaying_history_is_idempotent(self, db_session, fake):
        w = mailbox_world(db_session)
        ids = _inbound(fake, 3, thread_id="t-same")
        _tick(db_session, w, fake)
        before = {
            k: (v.id, v.label_ids, v.is_inbound)
            for k, v in _messages(db_session, w).items()
        }
        thread = (
            db_session.query(FnMailboxThread).filter_by(account_id=w.account.id).one()
        )
        db_session.execute(
            update(FnMailboxAccount)
            .where(FnMailboxAccount.id == w.account.id)
            .values(history_id=1000)
        )
        _tick(db_session, w, fake)
        after = {
            k: (v.id, v.label_ids, v.is_inbound)
            for k, v in _messages(db_session, w).items()
        }
        assert before == after and set(after) == set(ids)
        again = (
            db_session.query(FnMailboxThread).filter_by(account_id=w.account.id).one()
        )
        assert (again.id, again.message_count) == (thread.id, 3)


class TestMessages:
    def test_labels_are_replaced_and_gmail_filing_becomes_the_folder(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        (mid,) = _inbound(fake, 1)
        _tick(db_session, w, fake)
        fake.labels = fake.labels  # Actioned labels exist in the fake
        sync_labels = (
            db_session.query(FnMailboxLabel).filter_by(account_id=w.account.id).count()
        )
        fake.relabel(mid, add=["Label_res"], remove=["UNREAD", "INBOX"])
        _tick(db_session, w, fake)
        msg = _messages(db_session, w)[mid]
        assert sorted(msg.label_ids) == ["Label_res"]
        assert msg.unread is False
        thread = (
            db_session.query(FnMailboxThread).filter_by(account_id=w.account.id).one()
        )
        assert thread.unread_count == 0 and "INBOX" not in thread.label_ids
        folder = (
            db_session.query(FnThreadFolder).filter_by(account_id=w.account.id).one()
        )
        assert (folder.folder, folder.set_by_label) == ("reservation", "gmail")
        assert sync_labels >= 0

    def test_drafts_are_never_mirrored(self, db_session, fake):
        w = mailbox_world(db_session)
        fake.add_message(frm=MAILBOX, to=[CUSTOMER], labels=["DRAFT"])
        (real,) = _inbound(fake, 1)
        _tick(db_session, w, fake)
        assert set(_messages(db_session, w)) == {real}
        # The draft's id was never even fetched (history said DRAFT).
        assert not [r for r in fake.calls("GET", "/messages/m0001")]

    def test_deleted_messages_leave_the_mirror(self, db_session, fake):
        w = mailbox_world(db_session)
        a, b = _inbound(fake, 2, thread_id="t-del")
        _tick(db_session, w, fake)
        fake.delete_message(a)
        _tick(db_session, w, fake)
        assert set(_messages(db_session, w)) == {b}
        fake.delete_message(b)
        _tick(db_session, w, fake)
        assert _messages(db_session, w) == {}
        assert (
            db_session.query(FnMailboxThread).filter_by(account_id=w.account.id).count()
            == 0
        )

    def test_a_message_gone_before_it_is_fetched_is_dropped(self, db_session, fake):
        w = mailbox_world(db_session)
        (mid,) = _inbound(fake, 1)
        fake.messages.pop(mid)  # deleted without (yet) a history record
        _tick(db_session, w, fake)
        assert _messages(db_session, w) == {}

    def test_a_message_that_keeps_failing_is_parked_then_retried(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        bad, good = _inbound(fake, 2)
        fake.page_size = 10
        route = f"GET /messages/{bad}"
        fake.fail[route] = [500, 500, 500]
        for _ in range(2):  # two ticks abort without moving the cursor
            out = _tick(db_session, w, fake)
            assert out["ok"] is False
            assert _account(db_session, w).history_id == 1000
        out = _tick(db_session, w, fake)  # third: parked, the cursor moves on
        assert out["ok"] and out["parked"] == 1
        assert _account(db_session, w).history_id == fake.history_id
        assert set(_messages(db_session, w)) == {good}
        retry = (
            db_session.query(FnMailboxRetry)
            .filter_by(account_id=w.account.id, ref=bad)
            .one()
        )
        assert retry.parked_at is not None and retry.attempts == 3

        retry.next_attempt_at = dt.datetime.now(UTC) - dt.timedelta(seconds=1)
        db_session.flush()
        _tick(db_session, w, fake)
        assert set(_messages(db_session, w)) == {bad, good}
        assert (
            db_session.query(FnMailboxRetry).filter_by(account_id=w.account.id).count()
            == 0
        )


class TestFlags:
    def test_the_three_flags_and_the_replied_stage(self, db_session, fake):
        w = mailbox_world(db_session)
        t = "t-flags"
        inbound = fake.add_message(frm=f"Casey <{CUSTOMER}>", to=[MAILBOX], thread_id=t)
        internal = fake.add_message(
            frm="Manager <boss@cookbros.co.nz>", to=[MAILBOX], thread_id=t
        )
        auto = fake.add_message(
            frm=f"Casey <{CUSTOMER}>",
            to=[MAILBOX],
            thread_id=t,
            subject="Automatic reply: Party",
            headers={"Auto-Submitted": "auto-replied"},
        )
        _tick(db_session, w, fake)
        msgs = _messages(db_session, w)
        assert (msgs[inbound].is_inbound, msgs[inbound].is_internal) == (True, False)
        assert (msgs[internal].is_inbound, msgs[internal].is_internal) == (False, True)
        assert msgs[auto].is_auto_reply is True

        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        enquiry = add_enquiry(db_session, w.org.id, contact.id, w.venue.id)
        enquiries_svc.link_thread(
            db_session,
            w.ctx(),
            account_id=w.account.id,
            gmail_thread_id=t,
            enquiry_id=enquiry.id,
        )
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "new"  # the internal note and the auto-reply don't count

        reply = fake.add_message(
            frm=f"Brew Bar <{MAILBOX}>", to=[CUSTOMER], thread_id=t, labels=["SENT"]
        )
        _tick(db_session, w, fake)
        msg = _messages(db_session, w)[reply]
        assert (msg.is_inbound, msg.is_internal, msg.is_auto_reply) == (
            False,
            False,
            False,
        )
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "replied"

    def test_a_colleague_answering_from_their_own_address_is_a_reply(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        mid = fake.add_message(
            frm="Sam <sam@cookbros.co.nz>",
            to=[CUSTOMER],
            cc=[MAILBOX],
            labels=["INBOX"],
        )
        _tick(db_session, w, fake)
        msg = _messages(db_session, w)[mid]
        assert (msg.is_inbound, msg.is_internal) == (False, False)

    def test_a_form_notification_to_itself_is_the_customer_writing(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        mid = fake.add_message(
            frm=MAILBOX, to=[MAILBOX], reply_to=CUSTOMER, labels=["INBOX", "SENT"]
        )
        _tick(db_session, w, fake)
        msg = _messages(db_session, w)[mid]
        assert (msg.is_inbound, msg.is_internal) == (True, False)
        assert msg.reply_to_email == CUSTOMER


class TestResync:
    def test_initial_sync_captures_the_cursor_first_and_finishes_active(
        self, db_session, fake
    ):
        w = mailbox_world(db_session, history_id=None, status="resyncing")
        ids = _inbound(fake, 4, record=False)  # mail from before the connect
        position = fake.history_id
        out = _tick(db_session, w, fake)
        assert out["ok"] and out["complete"]
        acct = _account(db_session, w)
        assert (acct.status, acct.history_id) == ("active", position)
        assert set(_messages(db_session, w)) == set(ids)
        # Profile first, then the listing.
        paths = [r.url.path for r in fake.requests]
        assert paths.index("/gmail/v1/users/me/profile") < paths.index(
            "/gmail/v1/users/me/messages"
        )

    def test_a_resync_is_resumable_across_ticks(self, db_session, fake):
        w = mailbox_world(db_session, history_id=None, status="resyncing")
        ids = _inbound(fake, 6, record=False)
        out = _tick(db_session, w, fake, budget=8)
        assert out["ok"] and out["complete"] is False
        acct = _account(db_session, w)
        captured = acct.history_id
        assert acct.status == "resyncing" and captured == fake.history_id
        partial = set(_messages(db_session, w))
        assert partial and partial < set(ids)
        late = fake.add_message(
            frm=f"Casey <{CUSTOMER}>", to=[MAILBOX]
        )  # arrives meanwhile
        out = _tick(db_session, w, fake)
        assert out["complete"]
        acct = _account(db_session, w)
        assert (acct.status, acct.history_id) == ("active", captured)
        _tick(db_session, w, fake)  # the replay from the captured id
        assert set(_messages(db_session, w)) == set(ids) | {late}

    def test_an_expired_cursor_resyncs_and_drops_what_gmail_no_longer_has(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        keep, gone = _inbound(fake, 2)
        _tick(db_session, w, fake)
        fake.messages.pop(gone)  # removed while history was unavailable
        fake.expire_history()
        db_session.execute(
            update(FnMailboxAccount)
            .where(FnMailboxAccount.id == w.account.id)
            .values(history_id=500)
        )
        out = _tick(db_session, w, fake)
        assert out["ok"] and out["complete"]
        acct = _account(db_session, w)
        assert acct.status == "active" and acct.history_id == fake.history_id
        assert set(_messages(db_session, w)) == {keep}


class TestLeases:
    def test_a_leased_mailbox_is_not_synced_twice(self, db_session, fake):
        w = mailbox_world(db_session)
        now = dt.datetime.now(UTC)
        assert sync.claim(db_session, w.account.id, "worker-a", now) is not None
        assert sync.claim(db_session, w.account.id, "worker-b", now) is None
        assert _tick(db_session, w, fake, owner="worker-b") == {
            "account_id": w.account.id,
            "skipped": "busy",
        }

    def test_an_expired_lease_can_be_taken_and_fences_the_old_worker(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        now = dt.datetime.now(UTC)
        sync.claim(db_session, w.account.id, "worker-a", now - dt.timedelta(minutes=5))
        assert sync.claim(db_session, w.account.id, "worker-b", now) is not None
        with pytest.raises(sync.LostLease):
            sync._fenced(db_session, w.account.id, "worker-a", now, history_id=99999)
        assert _account(db_session, w).history_id == 1000

    def test_a_worker_that_loses_its_lease_mid_tick_moves_no_cursor(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        _inbound(fake, 2)

        def steal(request):
            if request.url.path.endswith("/history"):
                db_session.execute(
                    update(FnMailboxAccount)
                    .where(FnMailboxAccount.id == w.account.id)
                    .values(
                        lease_owner="worker-b",
                        lease_until=dt.datetime.now(UTC) + dt.timedelta(minutes=2),
                    )
                )

        fake.on_request = steal
        out = _tick(db_session, w, fake, owner="worker-a")
        assert out == {"account_id": w.account.id, "skipped": "lease_lost"}
        acct = _account(db_session, w)
        assert acct.history_id == 1000 and acct.lease_owner == "worker-b"
        assert _messages(db_session, w) == {}


class TestDeadGrant:
    def test_invalid_grant_stops_polling_and_emails_once(self, db_session, monkeypatch):
        fake = FakeGmail()
        install(
            monkeypatch,
            fake,
            FakeTokens(dead="Token refresh failed (400): invalid_grant"),
        )
        monkeypatch.setattr(sync, "_clock", lambda: fake.clock)
        w = mailbox_world(db_session)
        db_session.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).update(
            {"notify_emails": ["functions@brewbar.co.nz"]}
        )
        out = _tick(db_session, w, fake)
        assert out["needs_reconnect"]
        acct = _account(db_session, w)
        assert acct.status == "needs_reconnect"
        assert acct.reconnect_notified_at is not None
        assert "invalid_grant" in acct.last_error
        logs = (
            db_session.query(EmailLog)
            .filter(
                EmailLog.organization_id == w.org.id,
                EmailLog.template_name == "functions:mailbox_reconnect",
            )
            .all()
        )
        assert len(logs) == 1
        assert logs[0].status == "suppressed"  # never sent outside production
        assert "functions@brewbar.co.nz" in logs[0].to_addresses
        assert w.owner.email in logs[0].to_addresses
        assert w.staff.email not in logs[0].to_addresses  # can't reconnect

        # Polling stops: the scheduler no longer picks it up.
        assert w.account.id not in sync.due_accounts(db_session, dt.datetime.now(UTC))
        assert _tick(db_session, w, fake)["skipped"] == "busy"
        assert (
            db_session.query(func.count(EmailLog.id))
            .filter(
                EmailLog.organization_id == w.org.id,
                EmailLog.template_name == "functions:mailbox_reconnect",
            )
            .scalar()
            == 1
        )

    def test_a_reauthorised_connection_brings_it_back(self, db_session, fake):
        w = mailbox_world(db_session)
        acct = _account(db_session, w)
        acct.status = "needs_reconnect"
        acct.reconnect_notified_at = dt.datetime.now(UTC)
        db_session.flush()
        acct.updated_at = dt.datetime.now(UTC) - dt.timedelta(hours=1)
        w.conn.needs_reconnect = False
        w.conn.last_auth_checked_at = dt.datetime.now(UTC)
        db_session.flush()
        healed = sync._heal_reconnected(db_session, dt.datetime.now(UTC))
        assert w.account.id in healed
        db_session.expire_all()
        acct = _account(db_session, w)
        assert acct.status == "active" and acct.reconnect_notified_at is None


class TestScope:
    def test_placeholder_accounts_are_never_touched(self, db_session, fake):
        w = mailbox_world(db_session)
        acct = _account(db_session, w)
        acct.connection_id = None
        acct.status = "needs_reconnect"
        db_session.flush()
        assert w.account.id not in sync.due_accounts(db_session, dt.datetime.now(UTC))
        assert sync.claim(db_session, w.account.id, "x", dt.datetime.now(UTC)) is None

    def test_functions_off_is_not_synced_and_shadow_is_read_only(
        self, db_session, fake
    ):
        w = mailbox_world(db_session, mode="shadow")
        _inbound(fake, 1)
        out = _tick(db_session, w, fake)
        assert out["ok"]  # shadow mode mirrors mail
        assert all(r.method == "GET" for r in fake.requests)
        w.org.functions_mode = "off"
        db_session.flush()
        assert w.account.id not in sync.due_accounts(db_session, dt.datetime.now(UTC))

    def test_each_organisation_mirrors_into_its_own_rows(self, db_session, fake):
        w = mailbox_world(db_session)
        rival = other_mailbox(db_session, w)
        _inbound(fake, 1)
        _tick(db_session, w, fake)
        fake.address = "hello@rivalbar.co.nz"  # their own Google account
        sync.sync_account(
            rival.account.id,
            session_factory=sessions(db_session),
            deadline=fake.clock + 100,
        )
        db_session.expire_all()
        mine = (
            db_session.query(FnMailboxMessage).filter_by(account_id=w.account.id).all()
        )
        theirs = (
            db_session.query(FnMailboxMessage)
            .filter_by(account_id=rival.account.id)
            .all()
        )
        assert mine and all(m.organization_id == w.org.id for m in mine)
        assert theirs and all(m.organization_id == w.other_org.id for m in theirs)


class TestQuoteLinks:
    def test_a_quote_link_sent_from_gmail_is_a_quote_sent_once(self, db_session, fake):
        from app.services.functions import public_tokens

        w = mailbox_world(db_session)
        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        enquiry = add_enquiry(db_session, w.org.id, contact.id, w.venue.id)
        url = public_tokens.mint_quote_link(db_session, w.ctx(), enquiry.id)
        mid = fake.add_message(
            frm=f"Brew Bar <{MAILBOX}>",
            to=[CUSTOMER],
            labels=["SENT"],
            body=f"Here you go: {url}",
            html=f'<p>Here you go: <a href="{url}">your proposal</a></p>',
        )
        _tick(db_session, w, fake)
        facts = db_session.query(FnProposalEvent).filter_by(enquiry_id=enquiry.id).all()
        assert [(f.kind, f.source, f.gmail_message_id) for f in facts] == [
            ("sent", "gmail", mid)
        ]
        db_session.execute(
            update(FnMailboxAccount)
            .where(FnMailboxAccount.id == w.account.id)
            .values(history_id=1000)
        )
        _tick(db_session, w, fake)  # replay
        assert (
            db_session.query(FnProposalEvent).filter_by(enquiry_id=enquiry.id).count()
            == 1
        )
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "quoted"


class TestScheduler:
    def test_endpoint_answers_503_while_a_mailbox_lags(
        self, client, db_session, fake, monkeypatch
    ):
        from app.services.functions.mailbox import sync as sync_mod

        w = mailbox_world(db_session)
        acct = _account(db_session, w)
        acct.last_ok_at = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
        db_session.flush()
        fake.fail["GET /history"] = [503] * 5
        real = sync_mod.sync_mailboxes
        monkeypatch.setattr(
            sync_mod,
            "sync_mailboxes",
            lambda **kw: real(
                session_factory=sessions(db_session), max_workers=1, **kw
            ),
        )
        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "s3cret")
        assert client.post("/internal/sync-mailboxes").status_code == 403
        resp = client.post(
            "/internal/sync-mailboxes", headers={"X-Scheduler-Secret": "s3cret"}
        )
        assert resp.status_code == 503
        assert any(a["account_id"] == w.account.id for a in resp.json()["lagging"])

        fake.fail.clear()
        resp = client.post(
            "/internal/sync-mailboxes", headers={"X-Scheduler-Secret": "s3cret"}
        )
        assert resp.status_code == 200
        assert not any(a["account_id"] == w.account.id for a in resp.json()["lagging"])


class TestImporterTakeover:
    def _imported(self, db, w, gmail_id, thread_id, *, when, is_internal=False):
        """A row as the importer writes it: Orbit's copy, no Message-ID."""
        from app.db.functions_models import FnMailboxThread

        thread = (
            db.query(FnMailboxThread)
            .filter_by(account_id=w.account.id, gmail_thread_id=thread_id)
            .first()
        )
        if thread is None:
            thread = FnMailboxThread(
                organization_id=w.org.id,
                account_id=w.account.id,
                gmail_thread_id=thread_id,
                label_ids=[],
            )
            db.add(thread)
            db.flush()
        db.add(
            FnMailboxMessage(
                organization_id=w.org.id,
                account_id=w.account.id,
                thread_id=thread.id,
                gmail_message_id=gmail_id,
                gmail_thread_id=thread_id,
                internal_date=when,
                from_email="boss@cookbros.co.nz",
                is_inbound=True,  # Orbit's reading
                is_internal=is_internal,
                is_auto_reply=False,
                label_ids=["INBOX"],
            )
        )
        db.flush()

    def test_the_first_sync_rereads_every_imported_row(self, db_session, fake):
        w = mailbox_world(db_session, history_id=None, status="resyncing")
        now = dt.datetime.now(UTC)
        recent = fake.add_message(
            frm="Boss <boss@cookbros.co.nz>",
            to=[MAILBOX],
            thread_id="t-a",
            when=now - dt.timedelta(days=30),
            record=False,
        )
        old = fake.add_message(
            frm="Boss <boss@cookbros.co.nz>",
            to=[MAILBOX],
            thread_id="t-old",
            when=now - dt.timedelta(days=200),
            record=False,
        )
        self._imported(db_session, w, recent, "t-a", when=now - dt.timedelta(days=30))
        self._imported(db_session, w, old, "t-old", when=now - dt.timedelta(days=200))
        self._imported(db_session, w, "m-gone", "t-a", when=now - dt.timedelta(days=20))
        out = _tick(db_session, w, fake)
        assert out["ok"] and out["complete"]
        msgs = _messages(db_session, w)
        assert set(msgs) == {recent, old}  # the one Gmail no longer has is gone
        for mid in (recent, old):  # Norm's own reading replaced Orbit's
            assert msgs[mid].rfc822_message_id
            assert (msgs[mid].is_inbound, msgs[mid].is_internal) == (False, True)


class TestBinnedMail:
    def test_trash_and_spam_leave_the_mirror_and_come_back_when_restored(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        a, b = _inbound(fake, 2)
        _tick(db_session, w, fake)
        fake.relabel(a, add=["TRASH"], remove=["INBOX"])
        fake.relabel(b, add=["SPAM"], remove=["INBOX"])
        _tick(db_session, w, fake)
        assert _messages(db_session, w) == {}
        fake.relabel(a, add=["INBOX"], remove=["TRASH"])
        _tick(db_session, w, fake)
        assert set(_messages(db_session, w)) == {a}
