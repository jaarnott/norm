"""The page-component door: ``POST /api/connectors/{name}/execute/{action}``.

Found 29 Sep 2026 while mapping every path by which a connector row can run:

- It ran ANY registered built-in handler for any logged-in user — including
  ``gmail.send_email``, ``norm.save_app`` and ``norm.run_automated_task`` —
  with no thread, venue or approval gate. The web app only ever calls one
  handler through it (the criteria editor's ``norm_hr.save_criteria``).
- Its endpoint branch took the FIRST enabled connection of any venue, so a
  page in one organisation could be answered with another organisation's
  credentials — and so its data.

Also here: the two callers of ``_execute_tool_call`` that never passed a
config session (the widget-action route and working-document sync). The
function refuses to run without one, so both paths failed every time.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.db.config_models import ConnectionSpec
from app.db.models import Connection
from tests.conftest import _make_venue, _make_venue_access

ENDPOINT = {
    "action": "get_jobs",
    "method": "GET",
    "path_template": "/applicant_tracking/jobs",
    "required_fields": [],
}


@pytest.fixture()
def spec(db_session):
    row = ConnectionSpec(
        connector_name="exec_test_conn",
        display_name="Exec Test",
        execution_mode="template",
        auth_type="api_key",
        auth_config={},
        base_url_template="https://api.example.com",
        tools=[ENDPOINT],
    )
    db_session.add(row)
    db_session.flush()
    return row


def _connection(db_session, venue_id, marker):
    row = Connection(
        id=str(uuid.uuid4()),
        connector_name="exec_test_conn",
        venue_id=venue_id,
        enabled="true",
        config={"marker": marker},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ok_spec_call():
    result = SimpleNamespace(success=True, response_payload={"ok": 1}, error_message=None)
    return MagicMock(return_value=(result, {}))


class TestBuiltInHandlers:
    def test_a_handler_off_the_allowlist_is_refused_and_never_runs(
        self, client, manager_headers
    ):
        handler = MagicMock(return_value={"success": True})
        with patch("app.agents.internal_tools.get_handler", return_value=handler):
            resp = client.post(
                "/api/connectors/gmail/execute/send_email",
                json={"params": {"to": "x@example.com"}},
                headers=manager_headers,
            )
        assert resp.status_code == 403
        handler.assert_not_called()

    def test_the_criteria_editor_save_still_runs(self, client, manager_headers):
        handler = MagicMock(return_value={"success": True, "data": {"id": "c1"}})
        with patch("app.agents.internal_tools.get_handler", return_value=handler):
            resp = client.post(
                "/api/connectors/norm_hr/execute/save_criteria",
                json={"params": {"name": "Chef"}},
                headers=manager_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["data"] == {"id": "c1"}
        handler.assert_called_once()


class TestEndpointCredentials:
    def test_another_venues_connection_is_never_used(
        self, client, db_session, manager_headers, spec
    ):
        other = _make_venue(db_session, name="Someone Else's Venue")
        _connection(db_session, other.id, "other-org")
        call = _ok_spec_call()
        with patch("app.connectors.spec_executor.execute_spec", call):
            resp = client.post(
                "/api/connectors/exec_test_conn/execute/get_jobs",
                json={"params": {}},
                headers=manager_headers,
            )
        assert resp.status_code == 400
        call.assert_not_called()

    def test_a_venue_the_caller_does_not_hold_is_refused(
        self, client, db_session, manager_headers, spec
    ):
        other = _make_venue(db_session, name="Not Mine")
        _connection(db_session, other.id, "not-mine")
        call = _ok_spec_call()
        with patch("app.connectors.spec_executor.execute_spec", call):
            resp = client.post(
                "/api/connectors/exec_test_conn/execute/get_jobs",
                json={"params": {"venue_id": other.id}},
                headers=manager_headers,
            )
        assert resp.status_code == 403
        call.assert_not_called()

    def test_the_callers_own_venue_connection_is_used(
        self, client, db_session, manager_user, manager_headers, spec
    ):
        mine = _make_venue(db_session, name="Mine")
        _make_venue_access(db_session, manager_user, mine)
        _connection(db_session, mine.id, "mine")
        call = _ok_spec_call()
        with patch("app.connectors.spec_executor.execute_spec", call):
            resp = client.post(
                "/api/connectors/exec_test_conn/execute/get_jobs",
                json={"params": {"venue_id": mine.id, "status": "open"}},
                headers=manager_headers,
            )
        assert resp.status_code == 200
        _spec, _tool, params, credentials, _db = call.call_args.args
        assert credentials == {"marker": "mine"}
        # venue routing keys are not API fields
        assert params == {"status": "open"}

    def test_a_platform_wide_connection_serves_a_venue_less_call(
        self, client, db_session, manager_headers, spec
    ):
        _connection(db_session, None, "platform")
        call = _ok_spec_call()
        with patch("app.connectors.spec_executor.execute_spec", call):
            resp = client.post(
                "/api/connectors/exec_test_conn/execute/get_jobs",
                json={"params": {}},
                headers=manager_headers,
            )
        assert resp.status_code == 200
        assert call.call_args.args[3] == {"marker": "platform"}


class TestConfigSessionIsPassed:
    def test_document_sync_opens_and_closes_its_own_config_session(self):
        from app.services import document_sync

        cdb = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch("app.db.engine._ConfigSessionLocal", return_value=cdb) as factory:
            document_sync.sync_document("missing-doc", db)
        factory.assert_called_once()
        cdb.close.assert_called_once()

    def test_document_sync_hands_the_config_session_to_each_op(self):
        from app.services import document_sync

        doc = SimpleNamespace(
            id="d1",
            thread_id="t1",
            connector_name="loadedhub",
            doc_type="roster",
            sync_status="dirty",
            sync_error=None,
            pending_ops=[{"op": "delete_shift", "shift_id": "s1"}],
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = doc
        cdb = MagicMock()
        captured = {}

        def fake_execute(tc, db_, config_db=None):
            captured["config_db"] = config_db
            tc.status = "executed"
            return {"ok": True}

        with (
            patch.object(
                document_sync,
                "_get_mapping",
                return_value={"target_action": "delete_shift", "method": "DELETE"},
            ),
            patch.object(document_sync, "_build_params", return_value={"shift_id": "s1"}),
            patch("app.agents.tool_loop._execute_tool_call", side_effect=fake_execute),
            patch("app.services.document_sync.flag_modified"),
        ):
            document_sync.sync_document("d1", db, config_db=cdb)

        assert captured["config_db"] is cdb
        assert doc.sync_status == "synced"
