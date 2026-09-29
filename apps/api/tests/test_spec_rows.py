"""Endpoints and tools: the accessor module and the two-list storage.

Sep 2026 decision (docs/tool-architecture-strategy.md): an LLM only ever sees
tools — consolidators and built-ins. API endpoints are building blocks, kept in
their own list (``ConnectionSpec.endpoints``). Until a connector is split its
``endpoints`` is NULL and every row is still in ``tools``; readers go through
``spec_rows.rows()`` so that the move between lists changes nothing for them.
"""

from types import SimpleNamespace

import pytest

from app.connectors import spec_rows
from app.db.config_models import ConnectionSpec

CONSOLIDATOR = {
    "action": "get_things",
    "consolidator_config": {
        "function_code": "def run(params, call_api, log):\n    return {}\n"
    },
}
CONFIG_WITHOUT_CODE = {"action": "half_built", "consolidator_config": {"wraps": "x"}}
ENDPOINT = {"action": "get_raw_things", "method": "GET", "path_template": "/things"}
ENDPOINT_2 = {"action": "post_thing", "method": "POST", "path_template": "/things"}


class TestClassify:
    def test_a_consolidator_with_code_is_a_tool(self):
        assert spec_rows.classify("acme", CONSOLIDATOR) == "tool"
        assert spec_rows.build_of("acme", CONSOLIDATOR) == "consolidator"

    def test_consolidator_config_without_code_is_not_a_consolidator(self):
        assert spec_rows.classify("acme", CONFIG_WITHOUT_CODE) == "endpoint"

    def test_a_registered_handler_makes_the_row_a_built_in(self):
        row = {"action": "resolve_dates"}
        assert spec_rows.build_of("norm", row) == "built-in"
        assert spec_rows.classify("norm", row) == "tool"

    def test_the_cv_reader_is_a_built_in_not_an_endpoint(self):
        """Its row carries an HTTP template, but a registered handler runs
        instead of it — the admin screen used to show it as a plain GET."""
        row = {
            "action": "get_applicant_resume",
            "method": "GET",
            "path_template": "/files/{{ file_id }}",
        }
        assert spec_rows.build_of("bamboohr", row) == "built-in"

    def test_a_plain_http_row_is_an_endpoint(self):
        assert spec_rows.classify("acme", ENDPOINT) == "endpoint"

    def test_an_internal_row_without_a_handler_is_never_an_endpoint(self):
        row = {"action": "no_such_handler"}
        assert spec_rows.classify("norm", row, execution_mode="internal") == "tool"

    def test_partition_keeps_order(self):
        t, e = spec_rows.partition("acme", [ENDPOINT, CONSOLIDATOR, ENDPOINT_2])
        assert [r["action"] for r in t] == ["get_things"]
        assert [r["action"] for r in e] == ["get_raw_things", "post_thing"]


class TestReadingBothStates:
    def test_unsplit_spec_reads_exactly_as_before(self):
        spec = SimpleNamespace(
            connector_name="acme",
            execution_mode="template",
            tools=[CONSOLIDATOR, ENDPOINT],
        )
        assert spec_rows.rows(spec) == [CONSOLIDATOR, ENDPOINT]
        assert spec_rows.find(spec, "get_raw_things") is ENDPOINT
        assert spec_rows.tools(spec) == [CONSOLIDATOR]
        assert spec_rows.endpoints(spec) == [ENDPOINT]
        assert not spec_rows.is_split(spec)

    def test_split_spec_reads_both_lists(self):
        spec = SimpleNamespace(
            connector_name="acme",
            execution_mode="template",
            tools=[CONSOLIDATOR],
            endpoints=[ENDPOINT],
        )
        assert spec_rows.is_split(spec)
        assert spec_rows.rows(spec) == [CONSOLIDATOR, ENDPOINT]
        assert spec_rows.find(spec, "get_raw_things") is ENDPOINT
        assert spec_rows.find_tool(spec, "get_raw_things") is None
        assert spec_rows.find_endpoint(spec, "get_raw_things") is ENDPOINT
        assert spec_rows.kind_of(spec, ENDPOINT) == "endpoint"
        assert spec_rows.kind_of(spec, CONSOLIDATOR) == "tool"

    def test_an_empty_endpoints_list_still_counts_as_split(self):
        spec = SimpleNamespace(
            connector_name="acme",
            execution_mode="template",
            tools=[CONSOLIDATOR],
            endpoints=[],
        )
        assert spec_rows.is_split(spec)
        assert spec_rows.endpoints(spec) == []

    def test_none_spec_is_empty(self):
        assert spec_rows.rows(None) == []
        assert spec_rows.find(None, "x") is None

    def test_upsert_replaces_in_place_and_appends_new(self):
        old = [{"action": "a", "v": 1}, {"action": "b", "v": 1}]
        out = spec_rows.upsert(old, [{"action": "b", "v": 2}, {"action": "c", "v": 1}])
        assert out == [
            {"action": "a", "v": 1},
            {"action": "b", "v": 2},
            {"action": "c", "v": 1},
        ]


def _spec(db_session, *, tools, endpoints=None, name="split_test_conn"):
    row = ConnectionSpec(
        connector_name=name,
        display_name="Split Test",
        execution_mode="template",
        auth_type="none",
        auth_config={},
        tools=tools,
        endpoints=endpoints,
    )
    db_session.add(row)
    db_session.flush()
    return row


class TestTheListeners:
    def test_construction_is_never_rerouted(self, db_session):
        spec = _spec(db_session, tools=[CONSOLIDATOR, ENDPOINT], endpoints=[ENDPOINT_2])
        assert [r["action"] for r in spec.tools] == ["get_things", "get_raw_things"]

    def test_an_endpoint_written_into_tools_moves_to_endpoints_once_split(
        self, db_session
    ):
        spec = _spec(
            db_session,
            tools=[CONSOLIDATOR],
            endpoints=[{**ENDPOINT, "added_at": "2026-01-01"}],
        )
        edited = {**ENDPOINT, "description": "edited by an old sync script"}
        spec.tools = [CONSOLIDATOR, edited, ENDPOINT_2]
        assert [r["action"] for r in spec.tools] == ["get_things"]
        by_action = {r["action"]: r for r in spec.endpoints}
        assert set(by_action) == {"get_raw_things", "post_thing"}
        assert (
            by_action["get_raw_things"]["description"] == "edited by an old sync script"
        )
        # it was already an endpoint: keeps its stamp, is not "new"
        assert by_action["get_raw_things"]["added_at"] == "2026-01-01"
        # genuinely new: stamped
        assert by_action["post_thing"].get("added_at")

    def test_an_unsplit_spec_keeps_one_list(self, db_session):
        spec = _spec(db_session, tools=[CONSOLIDATOR])
        spec.tools = [CONSOLIDATOR, ENDPOINT]
        assert spec.endpoints is None
        assert [r["action"] for r in spec.tools] == ["get_things", "get_raw_things"]

    def test_a_new_endpoint_is_stamped(self, db_session):
        spec = _spec(db_session, tools=[CONSOLIDATOR], endpoints=[ENDPOINT])
        spec.endpoints = [ENDPOINT, ENDPOINT_2]
        new = {r["action"]: r for r in spec.endpoints}["post_thing"]
        assert new.get("added_at")

    def test_a_move_neither_reroutes_nor_restamps(self, db_session):
        spec = _spec(db_session, tools=[CONSOLIDATOR, ENDPOINT])
        with spec_rows.moving():
            spec.endpoints = [ENDPOINT]
            spec.tools = [CONSOLIDATOR]
        assert spec.endpoints == [ENDPOINT]
        assert "added_at" not in spec.endpoints[0]  # pre-stamping row stays unstamped
        assert spec.tools == [CONSOLIDATOR]


class TestAdminApi:
    @pytest.fixture()
    def admin_client(self, client, db_session):
        return client

    def test_get_returns_both_lists_as_stored(
        self, admin_client, db_session, admin_headers
    ):
        _spec(
            db_session,
            tools=[CONSOLIDATOR],
            endpoints=[ENDPOINT],
            name="api_split_conn",
        )
        body = admin_client.get(
            "/api/connector-specs/api_split_conn", headers=admin_headers
        ).json()
        assert [t["action"] for t in body["tools"]] == ["get_things"]
        assert [t["action"] for t in body["endpoints"]] == ["get_raw_things"]

    def test_unsplit_get_has_null_endpoints(
        self, admin_client, db_session, admin_headers
    ):
        _spec(db_session, tools=[CONSOLIDATOR, ENDPOINT], name="api_legacy_conn")
        body = admin_client.get(
            "/api/connector-specs/api_legacy_conn", headers=admin_headers
        ).json()
        assert body["endpoints"] is None
        assert len(body["tools"]) == 2

    def test_put_replaces_endpoints_on_a_split_connector(
        self, admin_client, db_session, admin_headers
    ):
        _spec(
            db_session, tools=[CONSOLIDATOR], endpoints=[ENDPOINT], name="api_put_split"
        )
        resp = admin_client.put(
            "/api/connector-specs/api_put_split",
            json={"tools": [CONSOLIDATOR], "endpoints": [ENDPOINT, ENDPOINT_2]},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [t["action"] for t in body["endpoints"]] == [
            "get_raw_things",
            "post_thing",
        ]
        assert [t["action"] for t in body["tools"]] == ["get_things"]

    def test_put_without_endpoints_leaves_them_alone(
        self, admin_client, db_session, admin_headers
    ):
        _spec(
            db_session, tools=[CONSOLIDATOR], endpoints=[ENDPOINT], name="api_put_keep"
        )
        resp = admin_client.put(
            "/api/connector-specs/api_put_keep",
            json={"tools": [CONSOLIDATOR]},
            headers=admin_headers,
        )
        assert [t["action"] for t in resp.json()["endpoints"]] == ["get_raw_things"]

    def test_endpoints_sent_for_an_unsplit_connector_fold_into_tools(
        self, admin_client, db_session, admin_headers
    ):
        _spec(db_session, tools=[CONSOLIDATOR], name="api_put_legacy")
        resp = admin_client.put(
            "/api/connector-specs/api_put_legacy",
            json={"tools": [CONSOLIDATOR], "endpoints": [ENDPOINT]},
            headers=admin_headers,
        )
        body = resp.json()
        assert body["endpoints"] is None
        assert [t["action"] for t in body["tools"]] == ["get_things", "get_raw_things"]
