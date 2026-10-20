"""A task's schedule must be one it can run on (20 Oct 2026).

"Every hour" came back from the task-drafting model as "daily at hour -1".
The task was saved; its owner pressed Activate twice and got a server error
each time, the task stayed a draft, and the board then emptied — its reload
asked for the first task's member ("norm") instead of the page's.
"""

import uuid

import pytest

import app.agents.internal_tools as it
from app.db.models import AutomatedTask, Thread, ToolCall
from app.services.task_scheduler import schedule_problem
from tests.org_helpers import auth_headers, make_user

BAD = {"hour": -1, "minute": 0}


class TestTheCheck:
    @pytest.mark.parametrize(
        "kind,config",
        [
            ("hourly", {"interval_minutes": 60}),
            ("manual", {}),
            ("daily", {"hour": 7}),
            ("weekly", {"hour": 0, "day_of_week": "friday"}),
            ("monthly", {"hour": 23, "minute": 59, "day_of_month": 31}),
        ],
    )
    def test_runnable_schedules_pass(self, kind, config):
        assert schedule_problem(kind, config) is None

    @pytest.mark.parametrize(
        "kind,config,words",
        [
            ("daily", BAD, "hour"),
            ("daily", {"hour": 24}, "hour"),
            ("daily", {"hour": 7, "minute": 60}, "minute"),
            ("weekly", {"hour": 7, "day_of_week": "someday"}, "day of the week"),
            ("monthly", {"hour": 7, "day_of_month": 0}, "month"),
            ("fortnightly", {}, "schedule type"),
        ],
    )
    def test_the_rest_say_why(self, kind, config, words):
        assert words in schedule_problem(kind, config)


def _task(db, user, **kw):
    t = AutomatedTask(
        id=str(uuid.uuid4()),
        title="Lott receive",
        agent_slug="norm",
        prompt="p",
        status="draft",
        created_by=user.id,
        **kw,
    )
    db.add(t)
    db.commit()
    return t


class TestActivate:
    def test_a_broken_schedule_is_a_clear_refusal_not_a_server_error(
        self, client, db_session
    ):
        user = make_user(db_session)
        t = _task(db_session, user, schedule_type="daily", schedule_config=BAD)
        r = client.post(
            f"/api/automated-tasks/{t.id}/resume", headers=auth_headers(user)
        )
        assert r.status_code == 400
        assert (
            "can't be switched on" in r.json()["detail"] and "-1" in r.json()["detail"]
        )
        db_session.refresh(t)
        assert t.status == "draft"

    def test_a_good_schedule_switches_on(self, client, db_session):
        user = make_user(db_session)
        t = _task(
            db_session,
            user,
            schedule_type="hourly",
            schedule_config={"interval_minutes": 60},
        )
        r = client.post(
            f"/api/automated-tasks/{t.id}/resume", headers=auth_headers(user)
        )
        assert r.status_code == 200 and r.json()["status"] == "active"


class TestCreation:
    def _create(self, db_session, monkeypatch, intent, schedule):
        user = make_user(db_session)
        thread = Thread(user_id=user.id, domain="norm", intent="x")
        db_session.add(thread)
        db_session.flush()
        db_session.add(
            ToolCall(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                iteration=1,
                tool_name="loadedhub__review_and_receive_invoices",
                connector_name="loadedhub",
                action="review_and_receive_invoices",
                method="GET",
                status="executed",
                input_params={},
            )
        )
        db_session.flush()
        import app.interpreter.llm_interpreter as li

        monkeypatch.setattr(
            li,
            "call_llm",
            lambda *a, **k: (
                {
                    "title": "T",
                    "prompt": "p",
                    "schedule_type": schedule[0],
                    "schedule_config": schedule[1],
                },
                None,
            ),
        )
        return it._create_automated_task({"intent": intent}, db_session, thread.id)

    def test_every_hour_read_as_hour_minus_one_is_saved_as_hourly(
        self, db_session, monkeypatch
    ):
        out = self._create(
            db_session,
            monkeypatch,
            "Every hour, receive outstanding invoices",
            ("daily", BAD),
        )
        task = db_session.query(AutomatedTask).filter_by(id=out["data"]["id"]).one()
        assert (task.schedule_type, task.schedule_config) == (
            "hourly",
            {"interval_minutes": 60},
        )

    def test_another_unreadable_schedule_goes_back_to_the_model(
        self, db_session, monkeypatch
    ):
        out = self._create(
            db_session, monkeypatch, "Receive invoices each morning", ("daily", BAD)
        )
        assert out["success"] is False and "couldn't be read" in out["error"]


class TestTheListSaysWhichMember:
    def test_the_page_list_returns_the_member_it_was_asked_for(self, db_session):
        out = it.get_handler("norm", "list_automated_tasks")(
            {"agent_slug": "procurement"}, db_session, None
        )
        assert out["data"]["agent_slug"] == "procurement"
