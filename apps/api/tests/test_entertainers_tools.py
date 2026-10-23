"""The two built-ins: venue-scoped through the person in the conversation,
never bank or IRD details, mark-paid refused without ``payroll:write``, a
scheduled task's run refused; the rows ``sync_norm_finance.py`` installs.
"""

from __future__ import annotations

import datetime as dt

from app.agents import entertainers_tools as tools
from app.agents.internal_tools import get_handler
from app.db.entertainers_models import EntPerformance
from tests.conftest import _make_thread
from tests.entertainers_world import (
    add_performance,
    add_performer,
    approved,
    assert_no_sensitive,
    make_world,
)


def _call(action, params, db, thread_id):
    return get_handler("norm_payroll", action)(params, db, thread_id)


class TestGetEntertainers:
    def test_performances_at_the_persons_venues_only_with_no_sensitive_fields(
        self, db_session
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        mine = add_performance(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 10)
        )
        theirs = add_performance(
            w.db, w.org, w.venue2, performer, date=dt.date(2026, 10, 10)
        )
        thread = _make_thread(w.db, w.team_member, intent="chat")
        out = _call(
            "get_entertainers",
            {"from": "2026-10-01", "to": "2026-10-31"},
            w.db,
            thread.id,
        )
        assert out["success"], out
        ids = {p["id"] for p in out["data"]["performances"]}
        assert mine.id in ids and theirs.id not in ids
        assert (
            out["data"]["performances"][0]["rate"] == "$100.00"
            and out["data"]["performances"][0]["link"]
            == f"/?page=ent-roster&performance={mine.id}"
        )
        assert_no_sensitive(out)
        # a venue they cannot see is refused by name
        out = _call("get_entertainers", {"venue": "Garden Bar"}, w.db, thread.id)
        assert not out["success"] and "Which venue" in out["error"]

    def test_onboarding_and_emails_views(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org, status="submitted")
        thread = _make_thread(w.db, w.team_member, intent="chat")
        out = _call(
            "get_entertainers", {"view": "onboarding", "q": "Sam"}, w.db, thread.id
        )
        row = out["data"]["performers"][0]
        assert (
            row["onboarding"] == "submitted"
            and row["payable"] is False
            and "bank_account" not in row
        )
        assert_no_sensitive(out)
        out = _call(
            "get_entertainers",
            {"view": "emails", "performer": "Sam Smith"},
            w.db,
            thread.id,
        )
        assert out["success"] and out["data"]["emails"] == []
        assert not _call("get_entertainers", {"view": "bogus"}, w.db, thread.id)[
            "success"
        ]
        assert performer.id

    def test_no_person_or_module_off_is_refused(self, db_session):
        w = make_world(db_session, mode="off")
        thread = _make_thread(w.db, w.team_member, intent="chat")
        assert (
            "isn't switched on"
            in _call("get_entertainers", {}, w.db, thread.id)["error"]
        )
        assert "No person" in _call("get_entertainers", {}, w.db, None)["error"]


class TestRecordPerformance:
    def test_book_and_approve_in_chat(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        thread = _make_thread(w.db, w.team_member, intent="chat")
        out = _call(
            "record_performance",
            {
                "op": "book",
                "venue": "Brew Bar",
                "performer": "Sam",
                "date": "2026-10-10",
                "start_time": "21:00",
                "end_time": "01:00",
                "rate": "120",
            },
            w.db,
            thread.id,
        )
        assert out["success"], out
        perf = (
            w.db.query(EntPerformance)
            .filter(EntPerformance.performer_id == performer.id)
            .one()
        )
        assert (
            perf.rate_cents == 12_000
            and perf.agreed_cents == 48_000
            and perf.created_by == w.team_member.id
        )
        out = _call(
            "record_performance",
            {
                "op": "approve",
                "performance_id": perf.id,
                "actual_start_time": "21:00",
                "actual_end_time": "00:00",
            },
            w.db,
            thread.id,
        )
        assert (
            out["success"] and out["data"]["approved"]["approved_amount"] == "$360.00"
        )
        out = _call(
            "record_performance",
            {
                "op": "book",
                "venue": "Garden Bar",
                "performer": "Sam",
                "date": "2026-10-11",
                "start_time": "21:00",
                "end_time": "01:00",
                "rate": "120",
            },
            w.db,
            thread.id,
        )
        assert not out["success"]

    def test_mark_paid_refuses_without_payroll_write(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = approved(w.db, w.org, w.venue, performer)
        team = _make_thread(w.db, w.team_member, intent="chat")
        out = _call(
            "record_performance",
            {"op": "mark_paid", "performance_id": perf.id},
            w.db,
            team.id,
        )
        assert not out["success"] and "payroll:write" in out["error"]
        finance = _make_thread(w.db, w.payroll_person, intent="chat")
        out = _call(
            "record_performance",
            {"op": "mark_paid", "performance_id": perf.id, "reference": "paid"},
            w.db,
            finance.id,
        )
        assert out["success"] and out["data"]["paid"]["paid"] is True

    def test_a_scheduled_run_may_read_but_not_act(self, db_session):
        w = make_world(db_session)
        add_performer(w.db, w.org)
        run = _make_thread(w.db, w.payroll_person, intent="finance.automated_task")
        assert _call("get_entertainers", {}, w.db, run.id)["success"]
        out = _call("record_performance", {"op": "book"}, w.db, run.id)
        assert not out["success"] and "scheduled task" in out["error"]


class TestRows:
    def test_every_row_has_a_handler_a_schema_and_no_requires(self):
        actions = {r["action"] for r in tools.TOOL_ROWS}
        assert actions == {"get_entertainers", "record_performance"}
        for row in tools.TOOL_ROWS:
            assert get_handler("norm_payroll", row["action"]) is not None
            assert row["requires"] == [] and row["engine_only"] is False
            for field in row["required_fields"] + row["optional_fields"]:
                assert field in row["field_schema"], (row["action"], field)
        write = next(r for r in tools.TOOL_ROWS if r["action"] == "record_performance")
        assert write["method"] == "POST" and write["field_schema"]["op"]["enum"] == [
            "book",
            "approve",
            "did_not_perform",
            "mark_paid",
        ]


class TestPreview:
    def test_the_card_shows_the_change_and_writes_nothing(self, db_session):
        from app.agents.internal_tools import get_previewer
        from app.db.entertainers_models import EntEmailLink

        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)
        thread = _make_thread(w.db, w.team_member, intent="chat")
        preview = get_previewer("norm_payroll", "record_performance")
        out = preview(
            {
                "op": "approve",
                "performance_id": perf.id,
                "actual_start_time": "21:00",
                "actual_end_time": "00:00",
            },
            w.db,
            thread.id,
        )
        card = out["preview"]
        assert card["title"] == "Approve a performance" and "Brew Bar" in card["target"]
        fields = {c["field"]: c for c in card["changes"]}
        assert fields["Approved amount"]["after"] == "$300.00"
        assert (
            fields["Status"]["before"] == "awaiting_approvals"
            and fields["Status"]["after"] == "awaiting_performer"
        )
        w.db.refresh(perf)
        assert perf.performance_status == "scheduled" and perf.actual_cents is None
        assert w.db.query(EntEmailLink).count() == 0
        book = preview(
            {
                "op": "book",
                "venue": "Brew Bar",
                "performer": "Sam",
                "date": "2026-11-01",
                "start_time": "20:00",
                "end_time": "23:00",
                "rate": "100",
            },
            w.db,
            thread.id,
        )["preview"]
        assert {c["field"]: c["after"] for c in book["changes"]}["Agreed"] == "$300.00"
        assert (
            w.db.query(EntPerformance)
            .filter(EntPerformance.performer_id == performer.id)
            .count()
            == 1
        )
        refused = preview(
            {"op": "mark_paid", "performance_id": perf.id}, w.db, thread.id
        )
        assert "payroll:write" in refused["error"]
