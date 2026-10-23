"""The ``norm_finance`` built-ins (plan §12, DESIGN-CONTRACT §4/§5): the
mode answer that refuses a task-run thread, the ledger's source derived
from the door and never a parameter, ``record_budget`` previewed and
refused unattended, and the two reads.
"""

from __future__ import annotations

import datetime as dt

from app.agents import finance_tools as tools
from app.agents.internal_tools import get_handler, get_previewer
from app.db.finance_models import FinBudgetPush
from app.services import caller_scope
from app.services.finance import actuals as actuals_svc
from app.services.finance import budgets, drivers as drivers_svc
from app.services.finance import forecast as engine
from app.services.finance import pushes
from tests.finance_f6_world import (
    chat_thread,
    make_world,
    task_run_thread,
    weekday_series,
)

GET_MODE = get_handler("norm_finance", "get_mode")
RECORD_PUSH = get_handler("norm_finance", "record_budget_push")
RECORD = get_handler("norm_finance", "record_budget")
PREVIEW = get_previewer("norm_finance", "record_budget")
GET_PLAN = get_handler("norm_finance", "get_budget_plan")
GET_FORECAST = get_handler("norm_finance", "get_forecast")

DAYS = [
    {
        "date": "2026-11-16",
        "before": 1978.0,
        "after": 2100.0,
        "action": "update",
        "loaded_id": "b1",
        "ok": True,
        "error": None,
    },
    {
        "date": "2026-11-17",
        "before": None,
        "after": 500.0,
        "action": "create",
        "loaded_id": "b2",
        "ok": True,
        "error": None,
    },
    {
        "date": "2026-11-18",
        "before": 100.0,
        "after": 100.0,
        "action": "unchanged",
        "loaded_id": "b3",
        "ok": True,
        "error": None,
    },
    {
        "date": "2026-11-19",
        "before": 100.0,
        "after": 0.0,
        "action": "skipped",
        "loaded_id": None,
        "ok": True,
        "error": None,
    },
    {
        "date": "2026-11-20",
        "before": 300.0,
        "after": 350.0,
        "action": "update",
        "loaded_id": "b5",
        "ok": False,
        "error": "API error 500",
    },
]


class TestGetMode:
    def test_a_chat_thread_gets_the_mode_a_task_run_an_error_too(self, db_session):
        w = make_world(db_session, finance="shadow")
        chat = chat_thread(db_session, w.owner)
        assert GET_MODE({}, db_session, chat.id) == {
            "success": True,
            "data": {"mode": "shadow"},
        }
        run = task_run_thread(db_session, w.owner)
        out = GET_MODE({}, db_session, run.id)
        assert out["data"] == {"mode": "shadow", "error": "unattended run"}
        # The intent alone marks a scheduled run too.
        chat.intent = "finance.automated_task"
        db_session.flush()
        assert GET_MODE({}, db_session, chat.id)["data"]["error"] == "unattended run"

    def test_without_a_thread_the_caller_scope_names_the_organisation(self, db_session):
        w = make_world(db_session, finance="live")
        with caller_scope.use(caller_scope.for_user(db_session, w.owner)):
            assert GET_MODE({}, db_session, None)["data"] == {"mode": "live"}
        assert GET_MODE({}, db_session, None)["data"]["mode"] == "off"


class TestRecordBudgetPush:
    def _payload(self, w, tool="set_budgets"):
        return {
            "tool": tool,
            "venue": "La Zeppa",
            "venue_id": w.laz.id,
            "from": "2026-11-16",
            "to": "2026-11-20",
            "allow_zero": False,
            "vintage_id": None,
            "days": DAYS,
        }

    def test_the_source_comes_from_the_row_and_the_door_never_a_parameter(
        self, db_session
    ):
        w = make_world(db_session)
        chat = chat_thread(db_session, w.owner)
        out = RECORD_PUSH(self._payload(w, "set_budgets_typed"), db_session, chat.id)
        assert out["data"] == {"success": True, "recorded": 3, "source": "manual"}
        assert (
            RECORD_PUSH(self._payload(w, "set_budgets"), db_session, chat.id)["data"][
                "source"
            ]
            == "chat"
        )
        with caller_scope.use(caller_scope.for_user(db_session, w.owner)):
            assert (
                RECORD_PUSH(self._payload(w), db_session, None)["data"]["source"]
                == "forecast"
            )
            with pushes.restoring():
                assert (
                    RECORD_PUSH(self._payload(w), db_session, None)["data"]["source"]
                    == "restore"
                )
        rows = (
            db_session.query(FinBudgetPush)
            .filter(FinBudgetPush.venue_id == w.laz.id)
            .order_by(FinBudgetPush.date, FinBudgetPush.pushed_at)
            .all()
        )
        assert len(rows) == 12
        first = rows[0]
        assert (
            first.date,
            first.before_cents,
            first.after_cents,
            first.method,
            first.pushed_by,
        ) == (dt.date(2026, 11, 16), 197800, 210000, "PUT", w.owner.id)
        created = next(r for r in rows if r.date == dt.date(2026, 11, 17))
        assert (
            created.method == "POST"
            and created.before_cents is None
            and created.loaded_id == "b2"
        )
        failed = next(r for r in rows if r.date == dt.date(2026, 11, 20))
        assert (
            failed.ok is False
            and failed.result["error"] == "API error 500"
            and failed.result["tool"] == "set_budgets_typed"
        )
        assert not any(
            r.date in (dt.date(2026, 11, 18), dt.date(2026, 11, 19)) for r in rows
        )

    def test_a_task_run_and_a_wrong_venue_are_refused(self, db_session):
        w = make_world(db_session)
        run = task_run_thread(db_session, w.owner)
        assert (
            RECORD_PUSH(self._payload(w), db_session, run.id)["error"]
            == "unattended run"
        )
        chat = chat_thread(db_session, w.owner)
        bad = {**self._payload(w), "venue": "Rival Bar", "venue_id": w.other_venue.id}
        out = RECORD_PUSH(bad, db_session, chat.id)
        assert out["success"] is False
        assert "venue" in out["error"].lower() and (
            "Rival" in out["error"] or "not found" in out["error"]
        )
        assert db_session.query(FinBudgetPush).count() == 0

    def test_the_ledger_feeds_the_restore_plan(self, db_session):
        w = make_world(db_session)
        chat = chat_thread(db_session, w.owner)
        RECORD_PUSH(self._payload(w, "set_budgets_typed"), db_session, chat.id)
        plan = pushes.restore_plan(
            db_session, w.ctx(), w.laz.id, dt.date(2026, 11, 16), dt.date(2026, 11, 20)
        )
        assert plan["days"] == [
            {
                "date": "2026-11-16",
                "amount_cents": 197800,
                "amount": 1978.0,
                "current_cents": 210000,
            },
        ]
        assert plan["cannot_restore"] == [
            "2026-11-17 (created by Norm — Loaded has no delete)"
        ]
        ledger = pushes.ledger(db_session, w.ctx(w.manager))
        assert len(ledger) == 3 and ledger[0]["pushed_by_name"]


class TestRecordBudget:
    def test_a_driver_is_previewed_then_written_and_refused_unattended(
        self, db_session
    ):
        w = make_world(db_session)
        chat = chat_thread(db_session, w.manager)
        params = {
            "op": "driver",
            "venue": "La Zeppa",
            "date": "2026-11-21",
            "kind": "manual",
            "label": "Round the Bays",
            "uplift_pct": 10,
        }
        shown = PREVIEW(params, db_session, chat.id)
        assert shown["preview"]["title"] == "Add a forecast driver"
        assert shown["preview"]["changes"] == [
            {"field": "2026-11-21 · Round the Bays", "before": "—", "after": "+10.0%"}
        ]
        out = RECORD(params, db_session, chat.id)
        assert (
            out["success"] is True
            and out["data"]["uplift_bp"] == 1000
            and out["data"]["kind"] == "manual"
        )
        run = task_run_thread(db_session, w.manager)
        assert RECORD(params, db_session, run.id)["error"] == tools.UNATTENDED
        assert PREVIEW(params, db_session, run.id)["error"] == tools.UNATTENDED
        # A team member lacks budgets:write; the manager cannot reach Dunedin.
        assert (
            "budgets:write"
            in RECORD(params, db_session, chat_thread(db_session, w.team_member).id)[
                "error"
            ]
        )
        assert (
            "venue"
            in RECORD({**params, "venue": "Dunedin Social Club"}, db_session, chat.id)[
                "error"
            ].lower()
        )

    def test_lines_days_and_overrides(self, db_session):
        w = make_world(db_session)
        chat = chat_thread(db_session, w.owner)
        out = RECORD(
            {
                "op": "lines",
                "venue_id": w.laz.id,
                "fiscal_year": 2027,
                "month": 11,
                "lines": '{"wage_pct": {"value_ratio": 0.29}}',
            },
            db_session,
            chat.id,
        )
        assert (
            out["success"] is True
            and out["data"]["lines"]["wage_pct"]["value_ratio"] == 0.29
        )
        out = RECORD(
            {
                "op": "days",
                "venue_id": w.laz.id,
                "days": [
                    {"date": "2026-11-16", "amount_cents": 197800, "note": "quiz"}
                ],
            },
            db_session,
            chat.id,
        )
        assert out["success"] is True and out["data"]["saved"] == 1
        version = budgets.get_version(db_session, w.ctx(), out["data"]["version_id"])
        assert version.locked_at is None and version.source == "manual"
        out = RECORD(
            {
                "op": "override",
                "venue_id": w.laz.id,
                "date": "2026-11-21",
                "amount": "2500",
                "reason": "gig",
            },
            db_session,
            chat.id,
        )
        assert out["success"] is True and out["data"]["amount_cents"] == 250000
        shown = PREVIEW(
            {
                "op": "override",
                "venue_id": w.laz.id,
                "date": "2026-11-21",
                "clear": "true",
            },
            db_session,
            chat.id,
        )
        assert shown["preview"]["changes"][0]["before"] == "$2,500.00"
        out = RECORD(
            {
                "op": "override",
                "venue_id": w.laz.id,
                "date": "2026-11-21",
                "clear": "true",
            },
            db_session,
            chat.id,
        )
        assert out["data"]["cleared"] == 1
        assert (
            RECORD({"op": "bogus", "venue_id": w.laz.id}, db_session, chat.id)[
                "success"
            ]
            is False
        )


class TestReads:
    def test_budget_plan_and_forecast_with_explain(self, db_session):
        w = make_world(db_session)
        chat = chat_thread(db_session, w.manager)
        plan = GET_PLAN(
            {"venue": "La Zeppa", "week_ending": "2026-11-22"}, db_session, chat.id
        )
        assert plan["success"] is True and plan["data"]["week"]["budget_cents"] == 0
        assert (
            GET_PLAN({"venue": "Dunedin Social Club"}, db_session, chat.id)["success"]
            is False
        )

        start = dt.date(2026, 1, 5)
        series = weekday_series(start, 300)
        actuals_svc.upsert_days(
            db_session,
            w.org.id,
            w.laz.id,
            [
                {"date": d, "sales_incl_cents": c, "sales_ex_cents": c, "orders": 1}
                for d, c in series.items()
            ],
        )
        as_of = start + dt.timedelta(days=300)
        drivers_svc.set_override(
            db_session,
            w.ctx(),
            w.laz.id,
            as_of + dt.timedelta(days=2),
            123400,
            reason="gig",
        )
        pushes.append_loaded_log(
            db_session,
            w.org.id,
            w.laz.id,
            as_of,
            {as_of + dt.timedelta(days=2): (100000, "b")},
        )
        engine.build_vintage(db_session, w.org.id, w.laz.id, as_of)
        out = GET_FORECAST(
            {
                "venue_id": w.laz.id,
                "start": as_of.isoformat(),
                "end": (as_of + dt.timedelta(days=6)).isoformat(),
            },
            db_session,
            chat.id,
        )
        assert out["success"] is True, out
        rows = out["data"]["days"]
        assert len(rows) == 7 and out["data"]["vintage"]["as_of"] == as_of.isoformat()
        day = rows[2]
        assert (
            day["override_cents"] == 123400
            and day["final_cents"] == 123400
            and day["loaded_cents"] == 100000
        )
        assert day["delta_cents"] == 23400 and day["forecast_cents"] != 123400
        assert out["data"]["forecast_push_ready"] is False
        explained = GET_FORECAST(
            {
                "venue_id": w.laz.id,
                "explain": (as_of + dt.timedelta(days=5)).isoformat(),
            },
            db_session,
            chat.id,
        )["data"]
        assert explained["explain"]["why"].startswith("Median of the last 8")
        assert explained["explain"]["components"]["median_of"] == 8
        # A scheduled run may read.
        run = task_run_thread(db_session, w.manager)
        assert (
            GET_FORECAST({"venue_id": w.laz.id}, db_session, run.id)["success"] is True
        )
        assert (
            GET_FORECAST(
                {"venue_id": w.laz.id, "start": "2026-01-01", "end": "2026-12-31"},
                db_session,
                chat.id,
            )["success"]
            is False
        )


def test_the_rows_match_the_handlers_and_carry_schemas():
    actions = {r["action"] for r in tools.TOOL_ROWS}
    assert actions == {
        "get_budget_plan",
        "get_forecast",
        "record_budget",
        "get_mode",
        "record_budget_push",
    }
    for row in tools.TOOL_ROWS:
        assert get_handler("norm_finance", row["action"]) is not None
        assert isinstance(row["field_schema"], dict)
        assert set(
            row.get("optional_fields", []) + row.get("required_fields", [])
        ) <= set(row["field_schema"]) | {"op"}
    by_action = {r["action"]: r for r in tools.TOOL_ROWS}
    assert by_action["record_budget"]["requires"] == ["budgets:write"]
    assert by_action["get_forecast"]["requires"] == ["finance:read"]
    assert (
        by_action["get_mode"]["engine_only"]
        and by_action["record_budget_push"]["engine_only"]
    )
