"""Tests for the automated-task diagnostics endpoint.

This endpoint exists so a scheduled task that "isn't working" can be explained
without opening the production database (which is private-IP only). It is
read-only and must never expose credentials.
"""

import uuid

from app.db.models import AutomatedTask, AutomatedTaskRun


def _make_task(db, owner, **kwargs):
    task = AutomatedTask(
        id=str(uuid.uuid4()),
        title=kwargs.pop("title", "Reconcile invoices"),
        agent_slug="procurement",
        prompt=kwargs.pop("prompt", "Reconcile received invoices"),
        schedule_type=kwargs.pop("schedule_type", "daily"),
        schedule_config=kwargs.pop("schedule_config", {"hour": 8, "minute": 0}),
        status=kwargs.pop("status", "active"),
        created_by=owner.id,
        **kwargs,
    )
    db.add(task)
    db.flush()
    return task


class TestAuth:
    def test_requires_authentication(self, client, db_session, admin_user):
        task = _make_task(db_session, admin_user)
        resp = client.get(f"/api/admin/automated-tasks/{task.id}/diagnostics")
        assert resp.status_code == 401

    def test_non_admin_forbidden(self, client, db_session, admin_user, manager_headers):
        task = _make_task(db_session, admin_user)
        resp = client.get(
            f"/api/admin/automated-tasks/{task.id}/diagnostics",
            headers=manager_headers,
        )
        assert resp.status_code == 403

    def test_unknown_task_404(self, client, admin_headers):
        resp = client.get(
            f"/api/admin/automated-tasks/{uuid.uuid4()}/diagnostics",
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestPayload:
    def test_reports_schedule_and_owner_run_modes(
        self, client, db_session, admin_user, admin_headers
    ):
        # The owner's run mode decides whether the consolidators write anything;
        # no other endpoint exposes another user's preference.
        admin_user.workflow_modes = {"reconcile_received_invoices": "autopilot"}
        task = _make_task(db_session, admin_user)

        resp = client.get(
            f"/api/admin/automated-tasks/{task.id}/diagnostics", headers=admin_headers
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["task"]["status"] == "active"
        assert body["task"]["schedule_type"] == "daily"
        assert body["task"]["schedule_config"] == {"hour": 8, "minute": 0}
        assert body["owner"]["email"] == admin_user.email
        assert body["owner"]["workflow_modes"] == {
            "reconcile_received_invoices": "autopilot"
        }
        modes = body["derived"]["effective_run_modes"]
        assert modes["reconcile_received_invoices"]["effective"] == "autopilot"
        assert body["derived"]["is_scheduled"] is True

    def test_unset_mode_falls_back_to_the_read_only_default(
        self, client, db_session, admin_user, admin_headers
    ):
        # The "runs every morning but nothing happens" case: no stored mode, so
        # the consolidator runs read-only.
        admin_user.workflow_modes = None
        task = _make_task(db_session, admin_user)

        body = client.get(
            f"/api/admin/automated-tasks/{task.id}/diagnostics", headers=admin_headers
        ).json()
        modes = body["derived"]["effective_run_modes"]["reconcile_received_invoices"]
        assert modes["stored"] is None
        assert modes["effective"] == body["derived"]["default_run_mode"] == "approve_all"

    def test_email_tool_flag_tracks_the_tool_filter(
        self, client, db_session, admin_user, admin_headers
    ):
        # A task only emails its owner if the email tool is permitted.
        without = _make_task(db_session, admin_user, tool_filter=["reconcile"])
        with_email = _make_task(db_session, admin_user, tool_filter=["send_report_email"])
        unrestricted = _make_task(db_session, admin_user, tool_filter=None)

        def flag(task):
            return client.get(
                f"/api/admin/automated-tasks/{task.id}/diagnostics",
                headers=admin_headers,
            ).json()["derived"]["email_tool_enabled"]

        assert flag(without) is False
        assert flag(with_email) is True
        assert flag(unrestricted) is True  # null filter = every tool allowed

    def test_includes_run_history_with_errors(
        self, client, db_session, admin_user, admin_headers
    ):
        task = _make_task(db_session, admin_user)
        db_session.add(
            AutomatedTaskRun(
                id=str(uuid.uuid4()),
                automated_task_id=task.id,
                status="error",
                mode="live",
                error_message="boom",
            )
        )
        db_session.flush()

        runs = client.get(
            f"/api/admin/automated-tasks/{task.id}/diagnostics", headers=admin_headers
        ).json()["runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "error"
        assert runs[0]["error_message"] == "boom"

    def test_does_not_leak_credentials(
        self, client, db_session, admin_user, admin_headers
    ):
        task = _make_task(db_session, admin_user)
        raw = client.get(
            f"/api/admin/automated-tasks/{task.id}/diagnostics", headers=admin_headers
        ).text.lower()
        for forbidden in (
            "password",
            "hashed_password",
            "access_token",
            "refresh_token",
            "client_secret",
            "credentials",
        ):
            assert forbidden not in raw, forbidden
