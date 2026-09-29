"""The admin screen's view of a connector: tools vs endpoints, who exposes a
tool, what it's built on and what calls each endpoint (Sep 2026).

The admin screen used to show every row as a plain method + path — the
BambooHR CV reader looked like a raw GET although Norm code runs instead of it,
and nothing said which endpoints a consolidator was built from.
"""

from app.db.config_models import ConnectionSpec
from app.services import spec_inventory as si

CODE = """
def run(params, call_api, log, call_api_parallel=None):
    a = call_api("acme", "get_orders", {})
    b = call_api("norm", "resolve_dates", {"text": "today"})
    rows = call_api_parallel([
        ("acme", "get_items", {}),
        ("acme", "get_units", {}),
    ])
    other = {"connector": "acme", "action": "get_prices"}
    dynamic = call_api("acme", params["which"], {})  # not literal: not counted
    return {}
"""


def test_calls_in_code_reads_literal_targets_only():
    found = si.calls_in_code(CODE, {"acme", "norm"})
    assert found == {
        ("acme", "get_orders"),
        ("norm", "resolve_dates"),
        ("acme", "get_items"),
        ("acme", "get_units"),
        ("acme", "get_prices"),
    }


def test_unparseable_code_is_empty_not_an_error():
    assert si.calls_in_code("def run(:", {"acme"}) == set()


def test_inventory_endpoint(client, db_session, admin_headers, monkeypatch):
    monkeypatch.setattr(
        "app.services.entitlements.tool_owners",
        lambda cdb: {"inv_acme.get_orders_report": "acme-app"},
    )
    db_session.add(
        ConnectionSpec(
            connector_name="inv_acme",
            display_name="Acme",
            execution_mode="template",
            auth_type="none",
            auth_config={},
            tools=[
                {
                    "action": "get_orders_report",
                    "consolidator_config": {
                        "function_code": "def run(p, call_api, l):\n"
                        "    return call_api('inv_acme', 'get_orders', {})\n"
                    },
                }
            ],
            endpoints=[
                {"action": "get_orders", "method": "GET", "path_template": "/o"},
                {"action": "get_unused", "method": "GET", "path_template": "/u"},
            ],
        )
    )
    db_session.flush()
    resp = client.get("/api/connector-specs/inv_acme/inventory", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["split"] is True
    rows = {r["action"]: r for r in body["rows"]}
    tool = rows["get_orders_report"]
    assert (tool["kind"], tool["build"]) == ("tool", "consolidator")
    assert tool["app"]["slug"] == "acme-app"
    assert tool["uses"] == ["inv_acme.get_orders"]
    assert rows["get_orders"]["kind"] == "endpoint"
    assert rows["get_orders"]["used_by"] == ["inv_acme.get_orders_report"]
    assert rows["get_unused"]["used_by"] == []


def test_inventory_404_for_unknown_connector(client, admin_headers):
    resp = client.get(
        "/api/connector-specs/no_such_conn/inventory", headers=admin_headers
    )
    assert resp.status_code == 404
