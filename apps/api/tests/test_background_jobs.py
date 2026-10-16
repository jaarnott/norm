"""Background jobs — long work that runs outside the chat request.

14 Oct 2026: "receive all outstanding invoices" for a new venue meant ~200
invoices. The chat ran them inside its request; Cloud Run cut it off at 25
minutes; nothing was saved, the thread showed nothing, and asking "how are we
going?" started the whole batch again (~$15, twice). These pin the fix: a big
batch becomes a job that is claimed by one worker, saves every invoice as it
goes, picks up again after a restart, can be cancelled, and posts its result
into the conversation.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.db.models import BackgroundJob, Message, Venue
from app.services import background_jobs as bj
from app.services import invoice_receive_job as irj
from tests.conftest import _make_thread


@pytest.fixture
def sessions(db_session, monkeypatch):
    """Every session the job code opens joins the test's transaction."""

    def factory():
        return Session(bind=db_session.get_bind())

    monkeypatch.setattr(bj, "_session", factory)
    monkeypatch.setattr("app.db.engine.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine._ConfigSessionLocal", factory)
    return factory


@pytest.fixture
def venue(db_session):
    v = Venue(id=str(uuid.uuid4()), name="Applegum Cafe", timezone="Australia/Sydney")
    db_session.add(v)
    db_session.flush()
    return v


def _job(db, **over):
    job = BackgroundJob(
        kind=over.pop("kind", "noop"),
        title=over.pop("title", "A job"),
        params=over.pop("params", {}),
        progress=over.pop("progress", {}),
        status=over.pop("status", "queued"),
        **over,
    )
    db.add(job)
    db.flush()
    return job


class TestTheQueue:
    def test_one_worker_claims_a_job_and_no_other_can(self, db_session, sessions):
        job = _job(db_session)
        ctx = bj.claim("worker-a")
        assert ctx is not None and ctx.job_id == job.id
        db_session.refresh(job)
        assert (job.status, job.claimed_by, job.attempts) == ("running", "worker-a", 1)
        assert bj.claim("worker-b") is None

    def test_asking_again_returns_the_job_already_running(self, db_session, venue):
        first, created = bj.enqueue(
            db_session, "receive_invoices", title="t", params={}, venue_id=venue.id
        )
        again, created_again = bj.enqueue(
            db_session, "receive_invoices", title="t", params={}, venue_id=venue.id
        )
        assert created and not created_again and again.id == first.id

    def test_a_job_whose_worker_died_is_picked_up_again(self, db_session, sessions):
        # A deploy restarted the instance: no heartbeat for minutes.
        job = _job(
            db_session,
            status="running",
            claimed_by="dead-worker",
            attempts=1,
            heartbeat_at=datetime.now(timezone.utc)
            - timedelta(seconds=bj.HEARTBEAT_STALE_SECONDS + 60),
            progress={"done": 37, "done_ids": ["a"]},
        )
        ctx = bj.claim("worker-b")
        assert ctx is not None and ctx.job_id == job.id
        assert ctx.progress["done"] == 37  # it carries on from its progress
        db_session.refresh(job)
        assert (job.claimed_by, job.attempts) == ("worker-b", 2)

    def test_a_live_job_is_not_taken_over(self, db_session, sessions):
        _job(
            db_session,
            status="running",
            claimed_by="worker-a",
            attempts=1,
            heartbeat_at=datetime.now(timezone.utc),
        )
        assert bj.claim("worker-b") is None

    def test_a_job_that_keeps_dying_is_given_up_on(self, db_session, sessions):
        job = _job(
            db_session,
            status="running",
            claimed_by="dead",
            attempts=bj.MAX_ATTEMPTS,
            heartbeat_at=datetime.now(timezone.utc)
            - timedelta(seconds=bj.HEARTBEAT_STALE_SECONDS + 60),
        )
        assert bj.claim("worker-b") is None
        db_session.refresh(job)
        assert job.status == "failed" and "not retried again" in job.error

    def test_restarts_that_come_with_progress_never_use_it_up(
        self, db_session, sessions
    ):
        """Every deploy restarts the instance. A job that gets further each
        time is not failing — only claims without progress count."""
        job = _job(db_session, progress={"done": 0})
        stale = datetime.now(timezone.utc) - timedelta(
            seconds=bj.HEARTBEAT_STALE_SECONDS + 60
        )
        for n in range(1, bj.MAX_ATTEMPTS + 3):
            ctx = bj.claim(f"worker-{n}")
            assert ctx is not None, f"given up on restart {n}"
            ctx.update(done=n)  # it got one invoice further, then died
            db_session.refresh(job)
            job.heartbeat_at = stale
            db_session.flush()
        assert job.status == "running"

    def test_a_worker_that_lost_the_job_stops_before_the_next_invoice(
        self, db_session, sessions
    ):
        """16 Oct 2026: a worker taken over mid-run kept going, and two
        workers did the same ten invoices — deleting drafts twice and
        flagging an invoice it had already received."""
        job = _job(db_session, status="running", claimed_by="worker-b")
        mine = bj.JobContext(job.id, "worker-a", {}, {})
        theirs = bj.JobContext(job.id, "worker-b", {}, {})
        assert mine.cancelled() is True
        assert theirs.cancelled() is False

    def test_the_old_worker_stops_once_another_has_taken_over(
        self, db_session, sessions
    ):
        job = _job(db_session, status="running", claimed_by="worker-b")
        ctx = bj.JobContext(job.id, "worker-a", {}, {})
        with pytest.raises(bj.LostClaim):
            ctx.update(done=1)

    def test_progress_is_saved_and_is_the_heartbeat(self, db_session, sessions):
        _job(db_session)
        ctx = bj.claim("w")
        ctx.update(done=3, total=10)
        job = db_session.query(BackgroundJob).get(ctx.job_id)
        db_session.refresh(job)
        assert job.progress == {"done": 3, "total": 10}
        assert job.heartbeat_at is not None


class TestRunningAJob:
    def test_a_finished_job_records_its_result_and_reports(
        self, db_session, sessions, monkeypatch
    ):
        posted, after = [], []
        monkeypatch.setitem(bj._HANDLERS, "t-ok", lambda ctx: {"n": 1})
        # The summary is written with the end state (the job is already
        # "done" in that transaction); the email-style hook runs after.
        monkeypatch.setitem(
            bj._ON_FINISH, "t-ok", lambda db, job: posted.append(job.status)
        )
        monkeypatch.setitem(bj._AFTER_FINISH, "t-ok", after.append)
        job = _job(db_session, kind="t-ok")
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        assert (job.status, job.result) == ("done", {"n": 1})
        assert posted == ["done"] and after == [job.id]

    def test_a_summary_that_fails_never_stops_the_job_ending(
        self, db_session, sessions, monkeypatch
    ):
        def broken(db, job):
            raise RuntimeError("template missing")

        monkeypatch.setitem(bj._HANDLERS, "t-ok", lambda ctx: None)
        monkeypatch.setitem(bj._ON_FINISH, "t-ok", broken)
        job = _job(db_session, kind="t-ok")
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        assert job.status == "done"

    def test_a_handler_that_raises_fails_the_job_with_the_reason(
        self, db_session, sessions, monkeypatch
    ):
        def boom(ctx):
            raise RuntimeError("Loaded is down")

        monkeypatch.setitem(bj._HANDLERS, "t-boom", boom)
        job = _job(db_session, kind="t-boom")
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        assert job.status == "failed" and "Loaded is down" in job.error

    def test_cancelling_a_queued_job_means_it_never_starts(self, db_session, sessions):
        job = _job(db_session)
        bj.request_cancel(db_session, job.id)
        db_session.flush()
        assert job.status == "cancelled"
        assert bj.claim("w") is None

    def test_a_running_job_stops_when_cancelled(
        self, db_session, sessions, monkeypatch
    ):
        def handler(ctx):
            bj.request_cancel(db_session, ctx.job_id)
            db_session.flush()
            assert ctx.cancelled()

        monkeypatch.setitem(bj._HANDLERS, "t-cancel", handler)
        job = _job(db_session, kind="t-cancel")
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        assert job.status == "cancelled"


class TestWhatTheModelSees:
    def test_a_conversation_lists_its_jobs_and_their_progress(
        self, db_session, admin_user
    ):
        thread = _make_thread(db_session, admin_user)
        _job(
            db_session,
            title="Receiving Applegum Cafe invoices",
            status="running",
            thread_id=thread.id,
            progress={"total": 212, "done": 37, "received": 12, "needs_you": 3},
        )
        text = bj.thread_manifest(thread, db_session)
        assert "Receiving Applegum Cafe invoices: running — 37 of 212 done" in text
        assert "12 received" in text and "3 need the person" in text
        assert "never start the same work again" in text

    def test_no_jobs_no_manifest(self, db_session, admin_user):
        assert (
            bj.thread_manifest(_make_thread(db_session, admin_user), db_session) == ""
        )

    def test_the_card_never_sees_the_bookkeeping(self, db_session):
        job = _job(db_session, progress={"done": 2, "done_ids": ["a", "b"]})
        out = bj.serialize(job)
        assert out["progress"] == {"done": 2}


class TestTheReceiveJob:
    """The invoices themselves go through the same batch review the chat uses;
    the job adds saving-as-it-goes, progress, resume and the closing report."""

    def _start(self, db_session, venue, thread, ids):
        job, created = irj.start(
            db_session,
            venue_id=venue.id,
            venue_name=venue.name,
            thread_id=thread.id,
            user_id=thread.user_id,
            rows=[{"id": i} for i in ids],
            mode="autopilot",
            require_valid_po=True,
            from_date="2025-10-01",
            to_date="2026-10-15",
        )
        assert created
        return db_session.query(BackgroundJob).get(job.id)

    def _fake_review(self, monkeypatch, outcomes, seen, extra=None):
        """``extra``: per invoice, ``(record fields, data fields)`` to add."""

        def review(db, cdb, venue_id, ids, **kw):
            seen.append(list(ids))
            for iid in ids:
                if kw["should_stop"]():
                    break
                outcome = outcomes.get(iid, "received")
                more_record, more_data = (extra or {}).get(iid, ({}, {}))
                data = {"invoice_id": iid, "reference_number": f"R-{iid}", **more_data}
                kw["on_invoice"](
                    iid,
                    {
                        "invoice_id": iid,
                        "outcome": outcome,
                        "reference_number": f"R-{iid}",
                        "supplier_name": "Alsco",
                        **more_record,
                    },
                    None if outcome == "review failed" else data,
                )
            return {"sensei": [], "mode": "autopilot"}

        monkeypatch.setattr("app.services.invoice_review.review_invoices", review)
        monkeypatch.setattr(
            irj, "save_draft", lambda db, t, v, data: f"doc-{data['invoice_id']}"
        )

    def test_a_deleted_draft_is_counted_as_deleted_not_received(
        self, db_session, admin_user, venue, sessions, monkeypatch
    ):
        """It used to share the "received" count, so the card and the summary
        told the person a deleted draft had been booked (16 Oct 2026)."""
        thread = _make_thread(db_session, admin_user)
        job = self._start(db_session, venue, thread, ["i0", "i1", "i2"])
        self._fake_review(monkeypatch, {"i1": "deleted"}, [])
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        assert (job.progress["received"], job.progress["deleted"]) == (2, 1)
        assert "3 of 3 reviewed — 2 received, 1 deleted" in irj.summary_text(job)

    def test_the_summary_says_why_drafts_went_and_what_was_created(
        self, db_session, admin_user, venue, sessions, monkeypatch
    ):
        """'84 deleted' alone sent the person back through every document to
        check they were right to go (Little West, 16 Oct 2026)."""
        thread = _make_thread(db_session, admin_user)
        job = self._start(db_session, venue, thread, [f"i{n}" for n in range(6)])
        made = {
            "stock_items": ["Oat Milk", "Soy Milk"],
            "suppliers": ["Acme"],
            "units": [],
        }
        self._fake_review(
            monkeypatch,
            {"i1": "deleted", "i2": "deleted", "i3": "deleted", "i4": "needs review"},
            [],
            extra={
                "i1": ({"deleted_kind": "statement"}, {}),
                "i2": ({"deleted_kind": "statement"}, {}),
                "i3": ({"deleted_kind": "duplicate"}, {}),
                "i0": ({}, {"autopilot_created": made}),
                # a parked invoice's creates happened in Loaded too
                "i4": ({}, {"autopilot_created": {"units": ["Box 6"]}}),
            },
        )
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        p = job.progress
        assert p["deleted_reasons"] == {"statement": 2, "duplicate": 1}
        assert p["created"] == {"stock_items": 2, "suppliers": 1, "units": 1}
        text = irj.summary_text(job)
        assert (
            "Deleted: 1 duplicate of an invoice already received and 2 statements."
            in text
        )
        assert "Created in Loaded: 2 stock items, 1 supplier and 1 unit." in text
        assert job.result["created"] == p["created"]

    def test_every_invoice_is_saved_and_counted_as_it_goes(
        self, db_session, admin_user, venue, sessions, monkeypatch
    ):
        thread = _make_thread(db_session, admin_user)
        ids = [f"i{n}" for n in range(12)]
        job = self._start(db_session, venue, thread, ids)
        seen: list = []
        self._fake_review(
            monkeypatch, {"i3": "needs review", "i7": "review failed"}, seen
        )
        sent = []
        monkeypatch.setattr(
            "app.services.email_service.send_system_email",
            lambda *a, **k: sent.append((a, k)),
        )
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        p = job.progress
        assert job.status == "done"
        assert (p["total"], p["done"], p["received"], p["needs_you"], p["failed"]) == (
            12,
            12,
            10,
            1,
            1,
        )
        assert [len(c) for c in seen] == [irj.CHUNK, 12 - irj.CHUNK]
        assert p["needs_you_list"][0]["doc_id"] == "doc-i3"
        # The closing report lands in the conversation, with the card that
        # needs the person, and the person is emailed.
        msg = (
            db_session.query(Message)
            .filter(Message.thread_id == thread.id, Message.role == "assistant")
            .one()
        )
        assert msg.content.startswith("Finished: Receiving Applegum Cafe invoices.")
        assert (
            "12 of 12 reviewed — 10 received, 1 need you, 1 couldn't be reviewed"
            in (msg.content)
        )
        assert msg.display_blocks == [
            {
                "component": "receive_invoice_editor",
                "data": {"working_document_id": "doc-i3", "venue_id": venue.id},
                "props": {"compact": True, "activeVenueId": venue.id},
            }
        ]
        assert sent and sent[0][0][0] == "job_finished"

    def test_a_job_picked_up_again_carries_on_from_the_next_invoice(
        self, db_session, admin_user, venue, sessions, monkeypatch
    ):
        thread = _make_thread(db_session, admin_user)
        ids = ["a", "b", "c", "d"]
        job = self._start(db_session, venue, thread, ids)
        job.progress = {"total": 4, "done": 2, "done_ids": ["a", "b"], "received": 2}
        db_session.flush()
        seen: list = []
        self._fake_review(monkeypatch, {}, seen)
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        assert seen == [["c", "d"]]  # the first two are not done again
        assert (job.progress["done"], job.progress["received"]) == (4, 4)

    def test_cancelling_stops_between_invoices_and_says_so(
        self, db_session, admin_user, venue, sessions, monkeypatch
    ):
        thread = _make_thread(db_session, admin_user)
        job = self._start(db_session, venue, thread, ["a", "b", "c"])
        seen: list = []
        self._fake_review(monkeypatch, {}, seen)
        monkeypatch.setattr(
            "app.services.email_service.send_system_email", lambda *a, **k: None
        )
        real_update = bj.JobContext.update

        def update(ctx, **changes):
            real_update(ctx, **changes)
            if changes.get("done") == 1:
                bj.request_cancel(db_session, ctx.job_id)
                db_session.flush()

        monkeypatch.setattr(bj.JobContext, "update", update)
        bj.run(bj.claim("w"))
        db_session.refresh(job)
        assert job.status == "cancelled" and job.progress["done"] == 1
        msg = db_session.query(Message).filter(Message.thread_id == thread.id).one()
        assert "as you asked" in msg.content and "1 of 3 reviewed" in msg.content


class TestTheReviewToolStartsAJob:
    """In a person's conversation, a window with more than a handful of
    invoices becomes a job; a small one (or a scheduled task's run) still runs
    in place — saving each draft as it goes, inside a time budget."""

    def _call(self, monkeypatch, db_session, thread_id, venue, n_rows):
        from app.agents import internal_tools as it

        monkeypatch.setattr(it, "_scoped_venue_id", lambda params, db: (venue.id, None))
        monkeypatch.setattr(
            "app.services.received_invoice.LoadedInvoiceClient",
            lambda *a, **k: object(),
        )
        monkeypatch.setattr(
            "app.services.invoice_review.list_window_invoices",
            lambda lh, **kw: [{"id": f"i{n}"} for n in range(n_rows)],
        )
        calls = []

        def review(db, cdb, venue_id, ids, **kw):
            calls.append((ids, kw))
            return {"cards": [], "received": [], "skipped": [], "mode": "autopilot"}

        monkeypatch.setattr("app.services.invoice_review.review_invoices", review)
        monkeypatch.setattr("app.db.engine._ConfigSessionLocal", lambda: db_session)
        out = it._review_invoices(
            {"venue": "Applegum Cafe", "mode": "autopilot", "from_date": "2025-10-01"},
            db_session,
            thread_id,
        )
        return out, calls

    def test_a_big_window_becomes_a_job(
        self, db_session, admin_user, venue, monkeypatch
    ):
        thread = _make_thread(db_session, admin_user)
        out, calls = self._call(
            monkeypatch, db_session, thread.id, venue, irj.JOB_THRESHOLD + 1
        )
        assert calls == []  # nothing reviewed in the chat request
        job = out["data"]["job"]
        assert job["title"] == "Receiving Applegum Cafe invoices"
        assert job["progress"]["total"] == irj.JOB_THRESHOLD + 1
        row = db_session.query(BackgroundJob).get(job["id"])
        assert row.params["invoice_ids"][0] == "i0" and row.thread_id == thread.id

    def test_a_small_window_runs_here_saving_as_it_goes(
        self, db_session, admin_user, venue, monkeypatch
    ):
        thread = _make_thread(db_session, admin_user)
        out, calls = self._call(monkeypatch, db_session, thread.id, venue, 3)
        ((ids, kw),) = calls
        assert ids == ["i0", "i1", "i2"]
        assert kw["on_invoice"] is not None and kw["deadline"] is not None
        assert "job" not in out["data"]

    def test_a_scheduled_run_keeps_running_in_place_with_no_time_budget(
        self, db_session, admin_user, venue, monkeypatch
    ):
        """A scheduled task's run isn't a request: no job, no budget."""
        from app.db.models import AutomatedTask, AutomatedTaskRun

        thread = _make_thread(db_session, admin_user)
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Receive invoices",
            agent_slug="norm",
            prompt="receive",
            schedule_type="daily",
            schedule_config={},
            status="active",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()
        db_session.add(
            AutomatedTaskRun(
                automated_task_id=task.id, thread_id=thread.id, status="running"
            )
        )
        db_session.flush()
        out, calls = self._call(
            monkeypatch, db_session, thread.id, venue, irj.JOB_THRESHOLD + 5
        )
        ((ids, kw),) = calls
        assert ids is None  # the batch lists the window itself, as before
        assert kw["deadline"] is None
        assert "job" not in out["data"]
        assert (
            not db_session.query(BackgroundJob).filter_by(thread_id=thread.id).count()
        )


class TestTheSummaryWording:
    """'Below' only when the cards are there — and never in the email."""

    def _job(self, needs):
        return BackgroundJob(
            title="Receiving X invoices",
            status="done",
            progress={"total": 30, "done": 30, "needs_you": needs},
        )

    def test_all_shown(self):
        assert "need you are below" in irj.summary_text(self._job(2), cards=2)

    def test_some_shown(self):
        text = irj.summary_text(self._job(25), cards=20)
        assert "The first 20 that need you are below; all 25" in text

    def _ran(self, **progress):
        return BackgroundJob(
            title="Receiving Little West invoices",
            status="done",
            progress={"total": 222, "done": 222, **progress},
        )

    def test_every_delete_reason_reads_as_words(self):
        job = self._ran(
            received=138,
            deleted=84,
            deleted_reasons={
                "not_an_invoice": 36,
                "statement": 22,
                "duplicate": 24,
                "no_copy": 1,
                "unreadable": 1,
            },
        )
        assert irj.summary_text(job) == (
            "Finished: Receiving Little West invoices. 222 of 222 reviewed — "
            "138 received, 84 deleted. Deleted: 24 duplicates of invoices "
            "already received, 22 statements, 36 other documents that weren't "
            "invoices, 1 draft with no invoice attached and 1 copy Norm "
            "couldn't read."
        )

    def test_a_reason_it_doesnt_know_still_counts(self):
        job = self._ran(deleted=2, deleted_reasons={"other": 1, "statement": 1})
        assert "Deleted: 1 statement and 1 for another reason." in irj.summary_text(job)

    def test_nothing_created_on_autopilot_is_said(self):
        job = self._ran(
            received=3,
            created={"stock_items": 0, "suppliers": 0, "units": 0},
            applied_mode="autopilot",
        )
        assert "No new stock items, suppliers or units were needed." in (
            irj.summary_text(job)
        )

    def test_a_job_from_before_the_breakdown_reads_as_it_did(self):
        job = self._ran(received=138, deleted=84)
        assert irj.summary_text(job) == (
            "Finished: Receiving Little West invoices. 222 of 222 reviewed — "
            "138 received, 84 deleted."
        )

    def test_none_shown_as_in_the_email(self):
        text = irj.summary_text(self._job(2))
        assert "below" not in text and "on the Invoices page" in text


class TestEverythingNotReceivedIsNamed:
    """The summary names each invoice that couldn't be reviewed and why —
    it used to give only a count (16 Oct 2026: two Allie's Foods documents
    hid behind "couldn't be reviewed" for four runs)."""

    def test_failed_invoices_are_listed_with_their_reason(self):
        job = BackgroundJob(
            title="Receiving X invoices",
            status="done",
            progress={
                "total": 2,
                "done": 2,
                "received": 1,
                "failed": 1,
                "needs_you_list": [
                    {
                        "invoice_id": "i1",
                        "supplier": "Allie's Foods",
                        "reference": None,
                        "outcome": "could not fetch from Loaded",
                        "reasons": ["could not fetch this invoice from Loaded: 404"],
                    }
                ],
            },
        )
        text = irj.summary_text(job)
        assert "Couldn't be reviewed:\n- Allie's Foods: could not fetch this invoice" in text
