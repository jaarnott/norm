"""Who may list, open, continue and delete a conversation.

Until Oct 2026 GET /threads/{id} checked only `tasks:read`, and POST /messages
took any thread id: anyone holding an id could read another person's
conversation — another organisation's included — or run a turn in it.

The thread list and delete tests came from test_tasks.py.
"""

import uuid

import pytest

from app.auth.security import create_access_token
from app.db.models import AutomatedTask, AutomatedTaskRun, Role, Thread
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_thread,
    _make_user,
)


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


@pytest.fixture()
def org(db_session):
    return _make_organization(db_session, name="Cook Brothers")


def _member(db, org, permissions=("tasks:read",)):
    """A user whose org role holds tasks:read (what GET /threads/{id} needs);
    deleting a thread also needs tasks:write."""
    user = _make_user(db)
    membership = _make_membership(db, user, org)
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=f"r-{uuid.uuid4().hex[:6]}",
        display_name="Reader",
        permissions=list(permissions),
    )
    db.add(role)
    db.flush()
    membership.role_id = role.id
    db.flush()
    return user


def _task_threads(db, creator):
    conversation = _make_thread(
        db, creator, domain="norm", intent="norm.automated_conversation"
    )
    task = AutomatedTask(
        id=str(uuid.uuid4()),
        title="Invoice Reconciliation",
        agent_slug="norm",
        prompt="reconcile",
        schedule_type="daily",
        schedule_config={},
        status="active",
        created_by=creator.id,
        conversation_thread_id=conversation.id,
    )
    db.add(task)
    run_thread = _make_thread(db, creator, domain="norm", intent="norm.automated_task")
    db.add(AutomatedTaskRun(automated_task_id=task.id, thread_id=run_thread.id))
    db.flush()
    return conversation, run_thread


class TestOpeningAConversation:
    def test_the_owner_opens_their_own(self, client, db_session, org):
        owner = _member(db_session, org)
        thread = _make_thread(db_session, owner, domain="norm", intent="norm.tool_use")
        r = client.get(f"/api/threads/{thread.id}", headers=_headers(owner))
        assert r.status_code == 200, r.text

    def test_a_teammate_cannot_open_someone_elses(self, client, db_session, org):
        owner, teammate = _member(db_session, org), _member(db_session, org)
        thread = _make_thread(db_session, owner, domain="norm", intent="norm.tool_use")
        r = client.get(f"/api/threads/{thread.id}", headers=_headers(teammate))
        assert r.status_code == 404

    def test_another_organisation_cannot_open_it(self, client, db_session, org):
        owner = _member(db_session, org)
        outsider = _member(db_session, _make_organization(db_session, name="Other"))
        thread = _make_thread(db_session, owner, domain="norm", intent="norm.tool_use")
        r = client.get(f"/api/threads/{thread.id}", headers=_headers(outsider))
        assert r.status_code == 404

    def test_a_platform_admin_opens_any(self, client, db_session, org, admin_user):
        owner = _member(db_session, org)
        thread = _make_thread(db_session, owner, domain="norm", intent="norm.tool_use")
        r = client.get(f"/api/threads/{thread.id}", headers=_headers(admin_user))
        assert r.status_code == 200, r.text

    def test_get_thread_not_found_returns_404(self, client, admin_headers):
        """An unknown id is not found, to read or to delete."""
        missing = f"/api/threads/{uuid.uuid4()}"
        assert client.get(missing, headers=admin_headers).status_code == 404
        assert client.delete(missing, headers=admin_headers).status_code == 404


class TestScheduledTaskThreadsAreShared:
    def test_a_teammate_opens_the_task_conversation_and_runs(
        self, client, db_session, org
    ):
        creator, teammate = _member(db_session, org), _member(db_session, org)
        conversation, run_thread = _task_threads(db_session, creator)
        for t in (conversation, run_thread):
            r = client.get(f"/api/threads/{t.id}", headers=_headers(teammate))
            assert r.status_code == 200, r.text

    def test_another_organisation_cannot(self, client, db_session, org):
        creator = _member(db_session, org)
        outsider = _member(db_session, _make_organization(db_session, name="Other"))
        conversation, run_thread = _task_threads(db_session, creator)
        for t in (conversation, run_thread):
            r = client.get(f"/api/threads/{t.id}", headers=_headers(outsider))
            assert r.status_code == 404


class TestContinuingAConversation:
    @pytest.mark.parametrize("path", ["/api/messages", "/api/messages/stream"])
    def test_a_message_into_someone_elses_thread_is_refused(
        self, client, db_session, org, monkeypatch, path
    ):
        import app.routers.messages as messages_router

        ran = []
        monkeypatch.setattr(
            messages_router, "handle_message", lambda *a, **k: ran.append(1)
        )
        owner, teammate = _member(db_session, org), _member(db_session, org)
        thread = _make_thread(db_session, owner, domain="norm", intent="norm.tool_use")

        r = client.post(
            path,
            headers=_headers(teammate),
            json={"message": "hello", "thread_id": thread.id},
        )

        assert r.status_code == 404
        assert ran == []


def _old_thread(db, user, **kw):
    thread = Thread(
        id=str(uuid.uuid4()),
        user_id=user.id,
        domain=kw.get("domain", "procurement"),
        status="awaiting_user_input",
        intent=kw.get("intent", "place_order"),
        raw_prompt=kw.get("raw_prompt", "Order milk"),
    )
    db.add(thread)
    db.flush()
    return thread


class TestListThreads:
    """GET /api/threads"""

    def test_list_threads_returns_user_threads(
        self, client, db_session, admin_user, admin_headers
    ):
        _old_thread(db_session, admin_user)
        _old_thread(
            db_session,
            admin_user,
            domain="hr",
            intent="new_employee",
            raw_prompt="Hire someone",
        )
        resp = client.get("/api/threads", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "threads" in data
        assert len(data["threads"]) == 2

    def test_list_threads_does_not_return_other_users_threads(
        self, client, db_session, admin_user, org
    ):
        member = _member(db_session, org)
        _old_thread(db_session, admin_user)
        # The member should not see the admin's threads
        resp = client.get("/api/threads", headers=_headers(member))
        assert resp.status_code == 200
        assert len(resp.json()["threads"]) == 0


class TestDeleteThread:
    """DELETE /api/threads/{thread_id}"""

    def test_delete_own_thread(self, client, db_session, admin_user, admin_headers):
        thread = _old_thread(db_session, admin_user)
        resp = client.delete(f"/api/threads/{thread.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_other_users_thread_returns_404(
        self, client, db_session, admin_user, org
    ):
        member = _member(db_session, org, ("tasks:read", "tasks:write"))
        thread = _old_thread(db_session, admin_user)
        resp = client.delete(f"/api/threads/{thread.id}", headers=_headers(member))
        assert resp.status_code == 404
