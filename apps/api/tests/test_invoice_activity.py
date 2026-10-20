"""The invoice activity page (owner, 19 Oct 2026): what Norm did with each
invoice, read from Norm's own records and never from Loaded. It replaced the
Dojo, whose all-venue sweep of Loaded's outstanding invoices often failed."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models import (
    InvoiceActivityEvent,
    InvoiceAutopilotOutcome,
    UnitCheckProposal,
    UnitCheckRun,
)
from app.services import invoice_activity as IA

from .supplier_fakes import bind_sessions

NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


@pytest.fixture(autouse=True)
def _one_today(monkeypatch):
    """The router reads "today" when a request runs; these tests read it at
    import. One clock for both, so a run that crosses midnight can't move
    the period under an assertion (the 27 Sep 2026 deploy was blocked by
    exactly this kind of test)."""
    import app.routers.invoice_activity as R

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(R, "datetime", _Frozen)


def _doc(**over) -> dict:
    doc = {
        "invoice_id": "inv-1",
        "reference_number": "INV-1",
        "supplier_name": "Prime Fresh",
        "total": 120.5,
        "file_id": "f-1",
        "process": {"layout": "Prime Fresh & Co", "reread": True, "studied": False},
        "replica": {
            "lines": [
                {
                    "linked_item_id": "i1",
                    "matched_by": "supplier_code",
                    "linked_unit_id": "u1",
                    "unit_source": "copy",
                },
                # The finish step's own unit wins over where the build got it.
                {
                    "linked_item_id": "i2",
                    "matched_by": "llm",
                    "linked_unit_id": "u2",
                    "unit_source": "copy",
                    "unit_by": "resolver",
                },
                {"linked_item_id": None, "unit": "Kilo", "unit_source": "catalogue"},
            ]
        },
        "suggestions": [
            {"id": "s1", "kind": "create_item", "proposed": "Basil 100g"},
            {"id": "s2", "kind": "create_unit", "proposed": "6x700ml"},
            {
                "id": "s3",
                "kind": "update_line",
                "explanation": "with every other suggestion applied the invoice "
                "comes to 110.00, not its total 120.50 — the freight line",
            },
        ],
        "suggestion_actions": [
            {"suggestion_id": "s1", "action": "accepted"},
            {"suggestion_id": "s2", "action": "dismissed"},
        ],
        "autopilot_created": {"suppliers": ["Prime Fresh & Co"]},
    }
    doc.update(over)
    return doc


def _outcome(db, venue, invoice_id, *, at, **over):
    fields = {
        "reference_number": invoice_id.upper(),
        "supplier_name": "Prime Fresh",
        "outcome": "clean",
        "received": True,
        "mode": "autopilot",
        "actor": "norm",
        **over,
    }
    row = InvoiceAutopilotOutcome(
        venue_id=venue.id, invoice_id=invoice_id, created_at=at, **fields
    )
    db.add(row)
    db.flush()
    return row


def _event(db, venue, invoice_id, kind, *, at, **over):
    row = InvoiceActivityEvent(
        venue_id=venue.id,
        invoice_id=invoice_id,
        kind=kind,
        mode="autopilot",
        created_at=at,
        updated_at=at,
        **over,
    )
    db.add(row)
    db.flush()
    return row


class TestWhatNormDid:
    def test_the_summary_of_one_invoice(self):
        act = IA.activity_from_doc(_doc())
        assert act["layout"] == "Prime Fresh & Co" and act["reread"] is True
        assert "studied" not in act  # False is left out
        assert act["lines"] == 3 and act["new_items"] == 1
        assert act["matched"] == {"supplier code": 1, "Norm's model": 1}
        assert act["units"] == {
            "the invoice": 1,
            "Norm's estimate": 1,
            "Norm's catalogue": 1,
        }
        assert act["totals"] == "worked out"
        # Accepted creates and Autopilot's own; a dismissed create is not one.
        assert act["created"] == {
            "stock_items": ["Basil 100g"],
            "suppliers": ["Prime Fresh & Co"],
        }

    def test_only_records_that_were_new_count_once_each(self):
        # Two lines need the same new brand: Norm creates it once and uses it
        # once; a person's create that found the record made nothing new.
        doc = _doc(
            suggestions=[
                {"id": "b1", "kind": "create_brand", "proposed": "MCCAIN"},
                {"id": "b2", "kind": "create_brand", "proposed": "MCCAIN"},
                {"id": "u1", "kind": "create_unit", "proposed": "5 KG"},
            ],
            suggestion_actions=[
                {"suggestion_id": "b1", "action": "accepted", "by": "norm"},
                {"suggestion_id": "b2", "action": "accepted", "by": "norm"},
                {
                    "suggestion_id": "u1",
                    "action": "accepted",
                    "by": "user",
                    "after": {"created": False},
                },
            ],
            autopilot_created={"brands": ["MCCAIN"]},
        )
        assert IA.activity_from_doc(doc)["created"] == {"brands": ["MCCAIN"]}

    def test_totals_that_never_added_up(self):
        doc = _doc(
            suggestions=[],
            issues=[{"code": "totals_inconsistent", "blocking": True}],
        )
        assert IA.activity_from_doc(doc)["totals"] == "didn't add up"

    def test_a_bad_document_never_raises(self):
        assert IA.activity_from_doc({"replica": "nonsense", "suggestions": 4}) == {
            "lines": 0,
            "new_items": 0,
            "totals": "added up",
            "feed": True,
        }


class TestRecording:
    def test_an_outcome_keeps_the_level_and_what_norm_did(
        self, db_session, venue, monkeypatch
    ):
        from app.services.autopilot_metrics import record_receive_outcome

        bind_sessions(monkeypatch, db_session, "SessionLocal")
        record_receive_outcome(
            db_session,
            venue_id=venue.id,
            invoice_id="inv-1",
            data=_doc(),
            mode="autopilot",
            actor="norm",
            level={"mode": "autopilot", "switches_on": ["auto_create_items"]},
        )
        row = db_session.query(InvoiceAutopilotOutcome).one()
        assert row.detail["level"]["switches_on"] == ["auto_create_items"]
        assert row.detail["activity"]["layout"] == "Prime Fresh & Co"

    def test_a_persons_receive_takes_their_own_level(
        self, db_session, venue, admin_user, monkeypatch
    ):
        from app.services.autopilot_metrics import record_receive_outcome

        bind_sessions(monkeypatch, db_session, "SessionLocal")
        monkeypatch.setattr(
            "app.services.approvals.receiving_settings",
            lambda user: {"mode": "approve_fixes", "receive_without_po": True},
        )
        record_receive_outcome(
            db_session,
            venue_id=venue.id,
            invoice_id="inv-2",
            data=_doc(invoice_id="inv-2"),
            mode="interactive",
            user_id=admin_user.id,
        )
        row = db_session.query(InvoiceAutopilotOutcome).one()
        assert row.detail["level"] == {
            "mode": "approve_fixes",
            "switches_on": ["receive_without_po"],
        }

    def test_a_stop_is_one_row_refreshed_on_each_review(
        self, db_session, venue, monkeypatch
    ):
        bind_sessions(monkeypatch, db_session, "SessionLocal")
        for reasons in (["no purchase order"], ["the totals don't add up"]):
            IA.record_stop(
                venue_id=venue.id,
                invoice_id="inv-3",
                kind="stopped",
                data=_doc(invoice_id="inv-3"),
                mode="autopilot",
                reasons=reasons,
                gates_needed=["receive_without_po"],
                level={"mode": "autopilot", "switches_on": []},
            )
        (row,) = db_session.query(InvoiceActivityEvent).all()
        assert row.detail["reasons"] == ["the totals don't add up"]
        assert row.supplier_name == "Prime Fresh" and float(row.total) == 120.5
        assert row.organization_id == venue.organization_id

    def test_what_an_earlier_stopped_run_created_is_kept(
        self, db_session, venue, monkeypatch
    ):
        # Night 1 created a stock item and a supplier, then stopped; night 2
        # only stopped again. Both stay with the stop until it is settled.
        bind_sessions(monkeypatch, db_session, "SessionLocal")

        def stop(doc):
            IA.record_stop(
                venue_id=venue.id,
                invoice_id="inv-3",
                kind="stopped",
                data=doc,
                mode="autopilot",
            )
            return db_session.query(InvoiceActivityEvent).one()

        stop(_doc(invoice_id="inv-3"))
        quiet = _doc(invoice_id="inv-3", autopilot_created={}, suggestion_actions=[])
        made = stop(quiet).detail["activity"]["created"]
        assert made == {
            "stock_items": ["Basil 100g"],
            "suppliers": ["Prime Fresh & Co"],
        }
        # Settled since (they went with that receive): a new stop starts afresh.
        row = db_session.query(InvoiceActivityEvent).one()
        row.detail = {**row.detail, "settled_at": NOW.isoformat()}
        db_session.flush()
        assert "created" not in stop(quiet).detail["activity"]

    def test_a_review_is_kept_once_per_invoice(self, db_session, venue, monkeypatch):
        from app.db.models import WorkingDocument

        bind_sessions(monkeypatch, db_session, "SessionLocal")
        # A draft a person had open: the receive's review replaces it.
        db_session.add(
            WorkingDocument(
                doc_type="received_invoice",
                connector_name="loadedhub",
                venue_id=venue.id,
                data={"invoice_id": "inv-4", "lines": []},
                external_ref={"invoice_id": "inv-4", "venue_id": venue.id},
            )
        )
        db_session.flush()
        for n in (1, 2):
            IA.keep_review(
                venue_id=venue.id,
                invoice_id="inv-4",
                data=_doc(invoice_id="inv-4", is_received=True, total=n),
            )
        IA.keep_review(
            venue_id=venue.id, invoice_id="inv-5", data=_doc(invoice_id="inv-5")
        )
        docs = (
            db_session.query(WorkingDocument).order_by(WorkingDocument.created_at).all()
        )
        assert [(d.external_ref["invoice_id"], d.data.get("total")) for d in docs] == [
            ("inv-4", 2),
            ("inv-5", 120.5),
        ]
        assert docs[0].data["is_received"] is True and docs[1].thread_id is None

    def test_the_batch_never_writes_its_old_copy_over_the_kept_review(
        self, db_session, venue, monkeypatch
    ):
        # The batch reads the open card, then keep_review rewrites it through
        # a session of its own, as in production; marking the invoice's cards
        # received afterwards must not put the batch's old copy back.
        from sqlalchemy.orm import sessionmaker

        from app.db.models import WorkingDocument
        from app.services.invoice_review import read_open_card
        from app.services.received_invoice import invalidate_conflicting_drafts

        own = sessionmaker(bind=db_session.connection())()
        monkeypatch.setattr(own, "close", lambda: None)
        monkeypatch.setattr("app.db.engine.SessionLocal", lambda: own)
        # Held for the whole run, as the batch's loop held it: a session
        # forgets a clean object nothing references, which hides the bug.
        card = WorkingDocument(
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            data={"invoice_id": "inv-6", "lines": [], "note": "the open card"},
            external_ref={"invoice_id": "inv-6", "venue_id": venue.id},
        )
        db_session.add(card)
        db_session.flush()
        previous, _history = read_open_card(db_session, venue.id, "inv-6")
        assert previous["note"] == "the open card" and card not in db_session
        IA.keep_review(
            venue_id=venue.id,
            invoice_id="inv-6",
            data=_doc(invoice_id="inv-6", is_received=True),
        )
        invalidate_conflicting_drafts(db_session, venue.id, "inv-6")
        own.expire_all()
        (doc,) = own.query(WorkingDocument).filter_by(venue_id=venue.id).all()
        assert "note" not in doc.data and doc.data["total"] == 120.5

    def test_a_failed_note_never_raises(self, monkeypatch):
        def broken():
            raise RuntimeError("database down")

        monkeypatch.setattr("app.db.engine.SessionLocal", broken)
        IA.record_stop(
            venue_id="v", invoice_id="i", kind="failed", data=None, mode=None
        )


class TestThePage:
    def _world(self, db_session, venue):
        _outcome(
            db_session,
            venue,
            "inv-a",
            at=NOW - timedelta(hours=2),
            detail={
                "level": {"mode": "autopilot", "switches_on": []},
                "activity": IA.activity_from_doc(_doc()),
            },
        )
        _outcome(
            db_session,
            venue,
            "inv-b",
            at=NOW - timedelta(hours=3),
            mode="interactive",
            actor="user",
            outcome="edited",
            manual_edit_count=2,
            detail={
                "level": {"mode": "autopilot", "switches_on": []},
                "suggestion_kinds": {"line_value": 3, "create_item": 1},
            },
        )
        _outcome(
            db_session,
            venue,
            "inv-c",
            at=NOW - timedelta(hours=4),
            outcome="deleted",
            received=False,
            detail={"deleted": {"kind": "not_an_invoice", "reason": "a letter"}},
        )
        _outcome(
            db_session,
            venue,
            "inv-d",
            at=NOW - timedelta(hours=5),
            outcome="deleted",
            received=False,
            detail={"deleted": {"kind": "duplicate"}},
        )
        # Stopped, then received later: no longer a stop.
        _event(db_session, venue, "inv-a", "stopped", at=NOW - timedelta(days=1))
        # Stopped and still stopped.
        _event(
            db_session,
            venue,
            "inv-e",
            "stopped",
            at=NOW - timedelta(hours=1),
            reference_number="INV-E",
            detail={"reasons": ["the totals don't add up"], "level": None},
        )
        # Outside the period.
        _outcome(db_session, venue, "inv-old", at=NOW - timedelta(days=20))
        run = UnitCheckRun(slot=TODAY.isoformat())
        db_session.add(run)
        db_session.flush()
        db_session.add(
            UnitCheckProposal(
                run_id=run.id,
                venue_id=venue.id,
                invoice_id="inv-a",
                line_id="l1",
                kind="correct",
                status="open",
            )
        )
        db_session.flush()

    def test_admins_only(self, client, manager_headers):
        res = client.get("/api/invoice-activity", headers=manager_headers)
        assert res.status_code == 403

    def test_only_a_later_receive_or_delete_settles_a_stop(self, db_session, venue):
        # Deleted on day 1, restored in Loaded, stopped on day 5: the stop
        # stands — the delete came before it.
        _outcome(
            db_session,
            venue,
            "inv-r",
            at=NOW - timedelta(days=10),
            outcome="deleted",
            received=False,
        )
        _event(db_session, venue, "inv-r", "stopped", at=NOW - timedelta(days=1))
        rows = IA.period_rows(db_session, TODAY - timedelta(days=6), TODAY, [venue.id])
        assert [(r["invoice_id"], r["outcome"]) for r in rows] == [("inv-r", "stopped")]

    def test_records_made_in_a_run_that_stopped_count_with_the_receive(
        self, db_session, venue
    ):
        # Night 1 Norm created a unit and stopped; night 2 it received.
        _event(
            db_session,
            venue,
            "inv-m",
            "stopped",
            at=NOW - timedelta(days=2),
            detail={"activity": {"created": {"units": ["6x700ml"]}}},
        )
        _outcome(db_session, venue, "inv-m", at=NOW - timedelta(days=1))

        def view(first, last):
            rows = IA.period_rows(db_session, first, last, [venue.id])
            said = [(r["outcome"], r["activity"].get("created")) for r in rows]
            return said, IA.kpis(rows)["created"]

        made = {"units": ["6x700ml"]}
        night_1, night_2 = TODAY - timedelta(days=2), TODAY - timedelta(days=1)
        assert view(night_1 - timedelta(days=4), TODAY) == (
            [("received", made)],
            {"units": 1},
        )
        # Each night alone: the stop on its own night (the receive came
        # later), and what it created goes with the receive on the next.
        assert view(night_1, night_1) == ([("stopped", made)], {"units": 1})
        assert view(night_2, night_2) == ([("received", made)], {"units": 1})

    def test_a_stops_creations_go_with_the_first_receive_or_delete_after_it(
        self, db_session, venue
    ):
        # Stopped six days ago (creating Basil), deleted by a person the next
        # day, then restored and received by Norm yesterday: Basil was the
        # delete's run, not yesterday's receive.
        _event(
            db_session,
            venue,
            "inv-x",
            "stopped",
            at=NOW - timedelta(days=6),
            detail={"activity": {"created": {"stock_items": ["Basil 100g"]}}},
        )
        _outcome(
            db_session,
            venue,
            "inv-x",
            at=NOW - timedelta(days=5),
            outcome="deleted",
            received=False,
            actor="user",
        )
        _outcome(db_session, venue, "inv-x", at=NOW - timedelta(days=1))

        def created(day):
            rows = IA.period_rows(db_session, day, day, [venue.id])
            return [(r["outcome"], r["activity"].get("created")) for r in rows]

        assert created(TODAY - timedelta(days=1)) == [("received", None)]
        assert created(TODAY - timedelta(days=5)) == [
            ("deleted", {"stock_items": ["Basil 100g"]})
        ]

    def test_the_timeline_ends_on_what_happened_last(self, db_session, venue):
        # Received, then Loaded put it back and Norm stopped on it again: the
        # stop (first seen before the receive) is the latest thing.
        _outcome(db_session, venue, "inv-t", at=NOW - timedelta(days=4))
        stop = _event(db_session, venue, "inv-t", "stopped", at=NOW - timedelta(days=6))
        stop.updated_at = NOW - timedelta(days=2)
        db_session.flush()
        items = IA.timeline(db_session, venue.id, "inv-t")["items"]
        assert [i["outcome"] for i in items] == ["received", "stopped"]

    def test_a_second_delete_closes_the_stop_a_restore_opened(
        self, db_session, venue, monkeypatch
    ):
        # Norm deleted it; Loaded restored it; the batch stopped on it; a
        # person deleted it again. One outcome row per verdict, so the second
        # delete adds none — the stop must close all the same.
        from app.services.autopilot_metrics import record_receive_outcome

        bind_sessions(monkeypatch, db_session, "SessionLocal")
        _outcome(
            db_session,
            venue,
            "inv-r",
            at=NOW - timedelta(days=10),
            outcome="deleted",
            received=False,
        )
        stopped_at = NOW - timedelta(days=2)
        stop = _event(db_session, venue, "inv-r", "stopped", at=stopped_at)
        record_receive_outcome(
            db_session,
            venue_id=venue.id,
            invoice_id="inv-r",
            data={"invoice_id": "inv-r"},
            mode="interactive",
            received=False,
            outcome_override="deleted",
        )
        # The second time is its own row, at its own time; the first stays
        # on its day, and the stop between them is closed.
        rows = db_session.query(InvoiceAutopilotOutcome).order_by("occurrence").all()
        assert [(r.occurrence, r.actor) for r in rows] == [(0, "norm"), (1, "user")]
        db_session.expire_all()
        assert stop.updated_at == stopped_at
        week = IA.period_rows(db_session, TODAY - timedelta(days=6), TODAY, [venue.id])
        assert [(r["outcome"], r["by"]) for r in week] == [("deleted", "person")]
        said = IA.timeline(db_session, venue.id, "inv-r")["items"]
        assert [(i["outcome"], i["by"]) for i in said] == [
            ("deleted", "norm"),
            ("stopped", "norm"),
            ("deleted", "person"),
        ]

    def test_the_same_act_recorded_again_is_a_retry(
        self, db_session, venue, monkeypatch
    ):
        # A double press or a job picked up again: the same person or Norm,
        # the same way, within the hour, nothing in between — no second row.
        # Norm deleting again what the owner restored is a second time.
        from app.services.autopilot_metrics import record_receive_outcome

        bind_sessions(monkeypatch, db_session, "SessionLocal")

        def delete(actor, mode):
            record_receive_outcome(
                db_session,
                venue_id=venue.id,
                invoice_id="inv-d",
                data={"invoice_id": "inv-d"},
                mode=mode,
                actor=actor,
                received=False,
                outcome_override="deleted",
            )
            return (
                db_session.query(InvoiceAutopilotOutcome)
                .filter_by(invoice_id="inv-d")
                .count()
            )

        assert delete("norm", "autopilot") == 1
        assert delete("norm", "autopilot") == 1  # a retry
        assert delete("user", "interactive") == 2  # someone else: a second time
        row = db_session.query(InvoiceAutopilotOutcome).filter_by(occurrence=0).one()
        row.created_at = NOW - timedelta(hours=2)
        db_session.flush()
        assert delete("norm", "autopilot") == 3  # hours later: came back, went again

    def test_a_settled_stops_creations_stay_with_the_outcome(
        self, db_session, venue, monkeypatch
    ):
        # Night 1 stopped and created a supplier; a later night failed
        # (another kind of stop) and made nothing; then Norm received it.
        # The receive keeps what was made, even after the invoice comes back
        # and stops again — and that stop starts afresh.
        from app.services.autopilot_metrics import record_receive_outcome

        bind_sessions(monkeypatch, db_session, "SessionLocal")
        IA.record_stop(
            venue_id=venue.id,
            invoice_id="inv-c",
            kind="stopped",
            data=_doc(invoice_id="inv-c"),
            mode="autopilot",
        )
        quiet = _doc(invoice_id="inv-c", autopilot_created={}, suggestion_actions=[])
        IA.record_stop(
            venue_id=venue.id,
            invoice_id="inv-c",
            kind="failed",
            data=quiet,
            mode="autopilot",
        )
        failed = db_session.query(InvoiceActivityEvent).filter_by(kind="failed").one()
        assert failed.detail["activity"]["created"]["suppliers"] == ["Prime Fresh & Co"]
        record_receive_outcome(
            db_session,
            venue_id=venue.id,
            invoice_id="inv-c",
            data=quiet,
            mode="autopilot",
            actor="norm",
        )
        row = db_session.query(InvoiceAutopilotOutcome).one()
        assert row.detail["activity"]["created"]["suppliers"] == ["Prime Fresh & Co"]
        IA.record_stop(
            venue_id=venue.id,
            invoice_id="inv-c",
            kind="stopped",
            data=quiet,
            mode="autopilot",
        )
        db_session.expire_all()
        again = db_session.query(InvoiceActivityEvent).filter_by(kind="stopped").one()
        assert "created" not in again.detail["activity"]
        assert again.created_at >= row.created_at  # a new stop, after the receive

    def test_the_days_are_the_viewers_own(self, db_session, venue):
        # 02:30 on 20 Oct in Auckland is 13:30 on 19 Oct UTC. Asked for 20 Oct
        # by someone in Auckland, the night's receive is on 20 Oct.
        at = datetime(2026, 10, 19, 13, 30, tzinfo=timezone.utc)
        _outcome(db_session, venue, "inv-night", at=at)
        day = date(2026, 10, 20)

        def ids(tz):
            rows = IA.period_rows(db_session, day, day, [venue.id], IA.zone(tz))
            return [r["invoice_id"] for r in rows]

        assert ids("Pacific/Auckland") == ["inv-night"]
        assert ids(None) == []  # UTC's 20 Oct starts 11 hours later
        assert IA.zone("Not/AZone") == timezone.utc

    def test_cards_and_stream(self, client, admin_headers, db_session, venue):
        self._world(db_session, venue)
        res = client.get("/api/invoice-activity", headers=admin_headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["period"] == {
            "start": (TODAY - timedelta(days=6)).isoformat(),
            "end": TODAY.isoformat(),
        }
        keys = [r["invoice_id"] for r in body["rows"]]
        assert keys == ["inv-e", "inv-a", "inv-b", "inv-c", "inv-d"]
        assert body["kpis"] == {
            "invoices": 5,
            "received": 2,
            "received_by_norm": 1,
            "received_by_person": 1,
            "changed_by_person": 1,
            "not_finished": 1,
            "deleted": 2,
            "unusual_deletions": 1,
            # inv-a names its creates. inv-b (an older row) only records what
            # Norm suggested creating, which is never counted as created.
            "created_invoices": 1,
            "created": {"stock_items": 1, "suppliers": 1},
        }
        b = next(r for r in body["rows"] if r["invoice_id"] == "inv-b")
        assert b["changes"] == {"line_value": 3}  # the create is counted apart
        assert b["activity"] == {"suggested_creates": {"stock_items": 1}}
        a = next(r for r in body["rows"] if r["invoice_id"] == "inv-a")
        assert a["unit_check"] == {"flagged": 1, "open": 1}
        assert body["venues"][0]["id"] == venue.id

    def test_filters_narrow_the_stream_only(
        self, client, admin_headers, db_session, venue
    ):
        self._world(db_session, venue)
        res = client.get(
            "/api/invoice-activity?mode=app&outcome=changed_by_person",
            headers=admin_headers,
        )
        body = res.json()
        assert [r["invoice_id"] for r in body["rows"]] == ["inv-b"]
        assert body["kpis"]["invoices"] == 5  # the cards keep the whole period

        def ids(outcome):
            url = f"/api/invoice-activity?outcome={outcome}"
            return [
                r["invoice_id"]
                for r in client.get(url, headers=admin_headers).json()["rows"]
            ]

        # Each card's own filter.
        assert ids("not_finished") == ["inv-e"]
        assert ids("unusual_deletions") == ["inv-c"]  # inv-d was a duplicate
        assert ids("deleted") == ["inv-c", "inv-d"]
        assert ids("created") == ["inv-a"]
        other = client.get(
            "/api/invoice-activity?venue_ids=elsewhere", headers=admin_headers
        ).json()
        assert other["rows"] == [] and other["kpis"]["invoices"] == 0

    def test_an_older_period(self, client, admin_headers, db_session, venue):
        self._world(db_session, venue)
        day = (TODAY - timedelta(days=20)).isoformat()
        body = client.get(
            f"/api/invoice-activity?start={day}&end={day}", headers=admin_headers
        ).json()
        assert [r["invoice_id"] for r in body["rows"]] == ["inv-old"]
        too_long = client.get(
            f"/api/invoice-activity?start={date(2026, 1, 1)}&end={TODAY}",
            headers=admin_headers,
        )
        assert too_long.status_code == 400

    def test_one_invoices_timeline(self, client, admin_headers, db_session, venue):
        self._world(db_session, venue)
        res = client.get(
            f"/api/invoice-activity/invoice/{venue.id}/inv-a", headers=admin_headers
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert [i["type"] for i in body["items"]] == ["event", "outcome"]
        assert len(body["unit_check"]) == 1
        assert body["document"] is None  # nothing kept for this one
        from app.db.models import WorkingDocument

        db_session.add(
            WorkingDocument(
                doc_type="received_invoice",
                connector_name="loadedhub",
                venue_id=venue.id,
                data={"file_id": "f-9", "reviewed_at": "x", "is_received": True},
                external_ref={"invoice_id": "inv-a", "venue_id": venue.id},
            )
        )
        db_session.flush()
        doc = client.get(
            f"/api/invoice-activity/invoice/{venue.id}/inv-a", headers=admin_headers
        ).json()["document"]
        assert doc["file_id"] == "f-9" and doc["received"] and doc["reviewed"]
        missing = client.get(
            f"/api/invoice-activity/invoice/{venue.id}/nope", headers=admin_headers
        )
        assert missing.status_code == 404

    def test_the_unit_check_list_follows_the_page(
        self, client, admin_headers, db_session, venue
    ):
        self._world(db_session, venue)
        url = "/api/supplier-invoice-specs/dojo/unit-check"
        start = (TODAY - timedelta(days=6)).isoformat()
        body = client.get(
            f"{url}?start={start}&end={TODAY}&venue_ids={venue.id}",
            headers=admin_headers,
        ).json()
        assert [p["invoice_id"] for p in body["proposals"]] == ["inv-a"]
        assert len(body["runs"]) == 1  # when the check last ran
        # Dated by the receive, so a period before it shows nothing.
        early = (TODAY - timedelta(days=30)).isoformat()
        assert (
            client.get(
                f"{url}?start={early}&end={early}", headers=admin_headers
            ).json()["proposals"]
            == []
        )


class TestBackfillFromJobs:
    def test_a_jobs_stopped_list_becomes_events_once(self, db_session, venue):
        import importlib.util
        import pathlib

        from app.db.models import BackgroundJob

        path = (
            pathlib.Path(__file__).parent.parent
            / "scripts/backfill_invoice_activity.py"
        )
        spec = importlib.util.spec_from_file_location("backfill", path)
        backfill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backfill)

        db_session.add(
            BackgroundJob(
                kind="receive_invoices",
                venue_id=venue.id,
                params={"venue_id": venue.id, "mode": "autopilot"},
                progress={
                    "applied_mode": "autopilot",
                    "needs_you_list": [
                        {
                            "invoice_id": "inv-9",
                            "reference": "S-9",
                            "supplier": "Prime Fresh",
                            "outcome": "needs review",
                            "reasons": ["this document is a statement"],
                        },
                        {"invoice_id": "inv-10", "outcome": "something new"},
                    ],
                },
                created_at=NOW - timedelta(days=2),
            )
        )
        db_session.flush()
        (event,) = backfill.plan(db_session, 30)
        assert event["kind"] == "stopped" and event["invoice_id"] == "inv-9"
        assert event["detail"]["reasons"] == ["this document is a statement"]
        assert event["mode"] == "autopilot" and event["supplier_name"] == "Prime Fresh"
        assert backfill.plan(db_session, 1) == []  # outside the window
