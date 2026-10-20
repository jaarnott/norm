"""Approval settings per organisation (Oct 2026).

A person in three organisations had one set of settings: Autopilot with every
switch on in one was the same in all of them. And an automated task had no
organisation, so an unattended run acted in its creator's OLDEST one — another
organisation's venues and settings. These pin both: settings are kept and read
per organisation, a task remembers where it was made, and its runs act there.
"""

import uuid

import pytest

from app.db.models import AutomatedTask, Thread
from app.services import active_org, approvals, caller_scope
from tests.conftest import _make_membership
from tests.org_helpers import make_org, make_user, make_venue

KEY = approvals.RECEIVE_KEY
ROW = {
    "action": "review_and_receive_invoices",
    "effect": "write",
    "approval": {
        "levels": [{"id": lv} for lv in approvals.LEVELS],
        "options": [{"id": "receive_without_po"}, {"id": "auto_create_brands"}],
    },
}
AUTOPILOT = {"level": "autopilot", "options": {"receive_without_po": True}}


@pytest.fixture()
def person(db_session):
    a, b = make_org(db_session, "Org A"), make_org(db_session, "Org B")
    user = make_user(db_session)
    _make_membership(db_session, user, a)
    _make_membership(db_session, user, b)
    db_session.flush()
    return type("P", (), dict(a=a, b=b, user=user))


class TestSettingsPerOrganisation:
    def test_the_older_one_store_still_reads_in_every_organisation(
        self, db_session, person
    ):
        person.user.approval_preferences = {KEY: AUTOPILOT}
        for org in (person.a, person.b):
            assert approvals.level(person.user, KEY, org.id) == "autopilot"

    def test_saving_in_one_organisation_leaves_the_other_alone(
        self, db_session, person
    ):
        # Today's single setting is kept in both, then B is changed on its own.
        person.user.approval_preferences = {KEY: AUTOPILOT}
        approvals.set_preference(
            db_session,
            person.user,
            KEY,
            ROW,
            "approve_all",
            via="settings",
            org_id=person.b.id,
        )
        assert approvals.level(person.user, KEY, person.a.id) == "autopilot"
        assert approvals.level(person.user, KEY, person.b.id) == "approve_all"
        assert (
            approvals.receiving_settings(person.user, person.a.id)["receive_without_po"]
            is True
        )

    def test_the_request_and_the_conversation_decide_the_organisation(
        self, db_session, person
    ):
        approvals.set_preference(
            db_session,
            person.user,
            KEY,
            ROW,
            AUTOPILOT,
            via="settings",
            org_id=person.b.id,
        )
        with active_org.use(person.b.id):
            assert approvals.level(person.user, KEY) == "autopilot"
        with active_org.use(person.a.id):
            assert approvals.level(person.user, KEY) == "approve_all"
        # a conversation in B reads B's, whatever the request has open
        t = Thread(
            user_id=person.user.id,
            domain="norm",
            intent="x",
            organization_id=person.b.id,
        )
        db_session.add(t)
        db_session.flush()
        with active_org.use(person.a.id):
            got = approvals.receiving_settings_for_thread(db_session, t.id)
        assert (
            approvals.LEVELS.index(got["mode"]) == 2 or got.get("mode") == "autopilot"
        )

    def test_an_organisation_never_set_starts_from_asking(self, db_session, person):
        approvals.set_preference(
            db_session,
            person.user,
            KEY,
            ROW,
            AUTOPILOT,
            via="settings",
            org_id=person.a.id,
        )
        c = make_org(db_session, "Joined later")
        _make_membership(db_session, person.user, c)
        assert approvals.level(person.user, KEY, c.id) == "approve_all"


class TestTasksKnowTheirOrganisation:
    def _task(self, db_session, user, **kw):
        t = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Hourly receive",
            agent_slug="norm",
            prompt="receive",
            schedule_type="hourly",
            status="draft",
            created_by=user.id,
            **kw,
        )
        db_session.add(t)
        db_session.flush()
        return t

    def test_a_task_is_made_in_the_organisation_its_creator_is_acting_in(
        self, db_session, person
    ):
        with caller_scope.use(caller_scope.Scope(person.b.id, user_id=person.user.id)):
            t = self._task(db_session, person.user)
        assert t.organization_id == person.b.id
        with active_org.use(person.b.id):
            t2 = self._task(db_session, person.user)
        assert t2.organization_id == person.b.id

    def test_a_task_with_a_venue_belongs_to_the_venues_organisation(
        self, db_session, person
    ):
        vb = make_venue(db_session, "B bar", person.b)
        with active_org.use(person.a.id):
            t = self._task(db_session, person.user, venue_id=vb.id)
        assert t.organization_id == person.b.id

    def test_a_run_acts_in_the_tasks_organisation(
        self, db_session, person, monkeypatch
    ):
        from app.services import task_scheduler

        with active_org.use(person.b.id):
            t = self._task(db_session, person.user)
        seen = {}

        def fake(task_id, **kw):
            seen["org"] = active_org.org_id_for(db_session, person.user.id)
            return {"success": True}

        monkeypatch.setattr(task_scheduler, "_execute_task_now", fake)
        # no request around it (the scheduler): the oldest membership is A
        assert active_org.org_id_for(db_session, person.user.id) == person.a.id
        task_scheduler.execute_task_now(t.id, db=db_session)
        assert seen["org"] == person.b.id


class TestTheTasksBoard:
    def test_a_task_filed_under_no_member_shows_under_the_member_its_tools_belong_to(
        self, client, db_session, person, monkeypatch
    ):
        """The hourly invoice task was filed under "norm" and showed on no
        member's Tasks board (20 Oct 2026)."""
        import app.routers.automated_tasks as R
        from tests.org_helpers import auth_headers

        monkeypatch.setattr(
            R,
            "task_member",
            lambda t, cdb: (
                "procurement"
                if "review_and_receive_invoices" in (t.tool_filter or [])
                else t.agent_slug
            ),
        )
        with active_org.use(person.b.id):
            t = AutomatedTask(
                id=str(uuid.uuid4()),
                title="Hourly receive",
                agent_slug="norm",
                prompt="p",
                schedule_type="hourly",
                status="draft",
                created_by=person.user.id,
                tool_filter=["review_and_receive_invoices", "send_email"],
            )
            db_session.add(t)
            db_session.flush()
        db_session.commit()
        h = {**auth_headers(person.user), "X-Organization": person.b.id}
        listed = client.get("/api/automated-tasks?agent_slug=procurement", headers=h)
        assert [x["id"] for x in listed.json()["tasks"]] == [t.id]
        other = client.get("/api/automated-tasks?agent_slug=hr", headers=h)
        assert other.json()["tasks"] == []
        # and only in the organisation it was made in
        h_a = {**auth_headers(person.user), "X-Organization": person.a.id}
        in_a = client.get("/api/automated-tasks?agent_slug=procurement", headers=h_a)
        assert in_a.json()["tasks"] == []

    def test_the_member_comes_from_the_apps_that_own_the_tools(self, db_session):
        from types import SimpleNamespace

        import app.routers.automated_tasks as R
        from app.services import entitlements as E

        apps = [
            SimpleNamespace(
                slug="loaded-stock",
                tier="app",
                composition={"tools": ["loadedhub.review_and_receive_invoices"]},
            ),
            SimpleNamespace(
                slug="norm-core", tier="app", composition={"tools": ["norm.send_email"]}
            ),
        ]
        members = {"loaded-stock": "procurement", "norm-core": E.ALL_MEMBERS}
        orig = (E._catalog, E.app_member)
        E._catalog = lambda cdb: apps
        E.app_member = lambda a: members[a.slug]
        try:
            task = SimpleNamespace(
                agent_slug="norm",
                tool_filter=["review_and_receive_invoices", "send_email"],
            )
            assert R.task_member(task, None) == "procurement"
            assert (
                R.task_member(SimpleNamespace(agent_slug="hr", tool_filter=[]), None)
                == "hr"
            )
        finally:
            E._catalog, E.app_member = orig


class TestTheTasksPage:
    def test_the_pages_task_list_shows_a_chat_made_task_in_its_own_organisation(
        self, db_session, person, monkeypatch
    ):
        """The Tasks pages load through norm.list_automated_tasks, which kept
        matching the member name exactly — the hourly invoice task (filed under
        "norm") stayed off the Procurement page after the API was fixed."""
        from app.agents.internal_tools import get_handler
        from app.services import task_scheduler

        monkeypatch.setattr(
            task_scheduler,
            "task_member",
            lambda t, cdb: (
                "procurement"
                if "review_and_receive_invoices" in (t.tool_filter or [])
                else t.agent_slug
            ),
        )
        with active_org.use(person.b.id):
            t = AutomatedTask(
                id=str(uuid.uuid4()),
                title="Hourly receive",
                agent_slug="norm",
                prompt="p",
                schedule_type="hourly",
                status="draft",
                created_by=person.user.id,
                tool_filter=["review_and_receive_invoices", "send_report_email"],
            )
            db_session.add(t)
            db_session.flush()
        handler = get_handler("norm", "list_automated_tasks")

        def listed(org, slug):
            with caller_scope.use(caller_scope.Scope(org.id, user_id=person.user.id)):
                out = handler({"agent_slug": slug}, db_session, None)
            return [x["id"] for x in out["data"]["tasks"]]

        assert listed(person.b, "procurement") == [t.id]
        assert listed(person.b, "hr") == []
        assert listed(person.a, "procurement") == []
