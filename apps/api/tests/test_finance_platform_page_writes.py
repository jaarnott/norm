"""A page's write button — previewed, then confirmed against what was seen
(services/page_writes; plan §5.1). Through the two routes on the connectors
router, with a write consolidator in the shape of xero.create_bill and the
HTTP layer faked.

Non-admin users: the person pressing is who the executor's `requires` check
and the Approval row see."""

import uuid
from types import SimpleNamespace

import pytest

from app.db.config_models import ConnectionSpec
from app.db.models import Approval, Connection
from tests.conftest import _make_membership
from tests.org_helpers import (
    auth_headers,
    grant_venue,
    make_org,
    make_user,
    make_venue,
    seed_system_roles,
)

CONNECTOR = "pwhub"
CODE = (
    "def run(params, call_api, log):\n"
    "    cur = call_api('pwhub', 'read_thing', {'venue': params.get('venue')})\n"
    "    preview({'title': 'Set a thing', 'venue': params.get('venue'),\n"
    "             'changes': [{'field': 'Amount', 'before': cur.get('amount'),\n"
    "                          'after': params.get('amount')}]}, basis=cur)\n"
    "    out = call_api('pwhub', 'put_thing',\n"
    "                   {'venue': params.get('venue'), 'amount': params.get('amount')})\n"
    "    return {'ok': True, 'sent': out}\n"
)


def _write_tool(action, requires):
    return {
        "action": action,
        "method": "GET",
        "effect": "write",
        "approval": {"default": "ask", "label": "Set a thing"},
        "requires": requires,
        "consolidator_config": {
            "function_code": CODE,
            "allowed_write_actions": ["put_thing"],
            "max_api_calls": 4,
        },
    }


@pytest.fixture()
def world(db_session, monkeypatch):
    import app.connectors.spec_executor as spec_mod
    import app.db.engine as engine

    roles = seed_system_roles(db_session)
    org = make_org(db_session, "Cook Brothers")
    venue = make_venue(db_session, "La Zeppa", org)
    other = make_venue(db_session, "Bessie", org)
    w = SimpleNamespace(
        org=org, venue=venue, other=other, state={"amount": 10}, puts=[]
    )
    for name in ("owner", "manager", "team_member"):
        u = make_user(db_session)
        m = _make_membership(db_session, u, org)
        m.role_id = roles[name].id
        grant_venue(db_session, u, venue)
        setattr(w, name, u)
    grant_venue(db_session, w.owner, other)  # two venues: must choose
    db_session.add(
        ConnectionSpec(
            id=str(uuid.uuid4()),
            connector_name=CONNECTOR,
            display_name="PW",
            execution_mode="template",
            auth_type="none",
            auth_config={},
            base_url_template="https://pw.example",
            tools=[
                _write_tool("set_thing", ["budgets:write"]),
                _write_tool("set_pay", ["payroll:write"]),
                {"action": "read_thing", "method": "GET", "path_template": "/t"},
                {"action": "put_thing", "method": "PUT", "path_template": "/t"},
            ],
            enabled=True,
        )
    )
    db_session.add(
        Connection(
            id=str(uuid.uuid4()),
            connector_name=CONNECTOR,
            venue_id=venue.id,
            config={"k": "zeppa"},
            enabled="true",
        )
    )
    db_session.flush()

    class _Cfg:
        def query(self, *a):
            return db_session.query(*a)

        def expunge(self, obj):
            pass

        def close(self):
            pass

    monkeypatch.setattr(engine, "_ConfigSessionLocal", lambda: _Cfg())

    def fake_execute_spec(spec, tool_def, params, credentials, db, *a, **kw):
        if tool_def["action"] == "read_thing":
            payload = {"amount": w.state["amount"]}
        else:
            w.puts.append(dict(params))
            payload = {"ok": True}
        return SimpleNamespace(
            success=True, response_payload=payload, error_message=None, reference=None
        ), None

    monkeypatch.setattr(spec_mod, "execute_spec", fake_execute_spec)
    return w


def _preview(client, w, user, action="set_thing", **body):
    return client.post(
        f"/api/connectors/{CONNECTOR}/execute/{action}/preview",
        json={"params": {"amount": 25}, "venue_id": w.venue.id, **body},
        headers=auth_headers(user),
    )


def _confirm(client, w, user, fingerprint, action="set_thing", **body):
    return client.post(
        f"/api/connectors/{CONNECTOR}/execute/{action}/confirm",
        json={
            "params": {"amount": 25},
            "venue_id": w.venue.id,
            "fingerprint": fingerprint,
            **body,
        },
        headers=auth_headers(user),
    )


class TestPreviewThenConfirm:
    def test_the_preview_shows_the_change_and_writes_nothing(
        self, client, db_session, world
    ):
        out = _preview(client, world, world.owner)
        assert out.status_code == 200, out.text
        card = out.json()["card"]
        assert card["title"] == "Set a thing" and card["venue"] == "La Zeppa"
        assert card["changes"] == [{"field": "Amount", "before": "10", "after": "25"}]
        assert (
            out.json()["fingerprint"]
            and card["fingerprint"] == out.json()["fingerprint"]
        )
        assert world.puts == []

    def test_confirm_writes_once_and_records_who_pressed(
        self, client, db_session, world
    ):
        fp = _preview(client, world, world.owner).json()["fingerprint"]
        out = _confirm(client, world, world.owner, fp)
        assert out.status_code == 200, out.text
        assert out.json()["success"] is True and out.json()["data"]["ok"] is True
        assert world.puts == [{"amount": 25}]
        row = (
            db_session.query(Approval)
            .filter(Approval.action == "card_write", Approval.user_id == world.owner.id)
            .one()
        )
        assert "Set a thing at La Zeppa" in row.notes and "pwhub.set_thing" in row.notes
        assert row.performed_by == world.owner.email

    def test_confirm_refuses_when_live_data_moved(self, client, db_session, world):
        fp = _preview(client, world, world.owner).json()["fingerprint"]
        world.state["amount"] = 11  # Loaded changed between look and press
        out = _confirm(client, world, world.owner, fp)
        assert out.status_code == 409, out.text
        detail = out.json()["detail"]
        assert "changed" in detail["error"]
        # A fresh card, re-confirmable: what it is now, with its fingerprint.
        assert detail["changed"]["changes"][0]["before"] == "11"
        assert detail["fingerprint"] and detail["fingerprint"] != fp
        assert world.puts == []
        again = _confirm(client, world, world.owner, detail["fingerprint"])
        assert again.status_code == 200 and world.puts == [{"amount": 25}]
        assert (
            db_session.query(Approval).filter(Approval.action == "card_write").count()
            == 1
        )


class TestWhoMayPress:
    def test_requires_is_checked_against_the_presser(self, client, db_session, world):
        assert _preview(client, world, world.team_member).status_code == 403
        assert _confirm(client, world, world.team_member, "x").status_code == 403
        assert world.puts == []
        # A manager holds budgets:write but never payroll:write.
        assert _preview(client, world, world.manager).status_code == 200
        pay = _preview(client, world, world.manager, action="set_pay")
        assert pay.status_code == 403 and "payroll:write" in pay.json()["detail"]
        assert (
            _confirm(client, world, world.manager, "x", action="set_pay").status_code
            == 403
        )

    def test_the_venue_is_picked_fail_closed(self, client, db_session, world):
        two = _preview(client, world, world.owner, venue_id=None)
        assert two.status_code == 400 and "Choose a venue" in two.json()["detail"]
        stranger = make_venue(db_session, "Elsewhere", make_org(db_session, "Rival"))
        assert (
            _preview(client, world, world.owner, venue_id=stranger.id).status_code
            == 403
        )
        assert world.puts == []

    def test_only_a_write_tool_comes_through_here(self, client, db_session, world):
        out = client.post(
            f"/api/connectors/{CONNECTOR}/execute/read_thing/preview",
            json={"params": {}, "venue_id": world.venue.id},
            headers=auth_headers(world.owner),
        )
        assert out.status_code == 404  # an endpoint, not a tool

    def test_the_generic_execute_route_stays_read_only(self, client, db_session, world):
        """A GET-declared WRITE (effect write, declared write actions) is not
        a read: the page's write goes through preview/confirm, never here."""
        out = client.post(
            f"/api/connectors/{CONNECTOR}/execute/set_thing",
            json={"params": {"venue_id": world.venue.id, "amount": 25}},
            headers=auth_headers(world.owner),
        )
        assert out.status_code == 400 and "read-only" in out.json()["detail"]
        assert world.puts == []


class TestExecuteRouteReads:
    """The generic execute route runs reads only, but a read consolidator may
    list a READ in allowed_write_actions (Xero's bills and profit-and-loss
    listed resolve_dates): that must not make it a write. Both pages refused
    from 23 to 24 Oct 2026."""

    def _add(self, db_session, action, declared):
        spec = (
            db_session.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        spec.tools = list(spec.tools) + [
            {
                "action": action,
                "method": "GET",
                "effect": "read",
                "consolidator_config": {
                    "function_code": "def run(params, call_api, log):\n"
                    "    return {'ok': True}\n",
                    "allowed_write_actions": declared,
                    "max_api_calls": 2,
                },
            }
        ]
        db_session.flush()

    def _run(self, client, world, action):
        return client.post(
            f"/api/connectors/{CONNECTOR}/execute/{action}",
            json={"params": {"venue_id": world.venue.id}},
            headers=auth_headers(world.owner),
        )

    def test_a_read_that_declares_a_read_still_runs(self, client, db_session, world):
        # Norm's own resolve_dates row, as the live norm spec has it.
        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="norm",
                display_name="Norm",
                execution_mode="internal",
                auth_type="none",
                auth_config={},
                tools=[{"action": "resolve_dates", "method": "GET", "read_only": True}],
                enabled=True,
            )
        )
        self._add(db_session, "read_with_dates", ["read_thing", "resolve_dates"])
        out = self._run(client, world, "read_with_dates")
        assert out.status_code == 200, out.text

    def test_a_read_that_declares_a_write_is_refused(self, client, db_session, world):
        self._add(db_session, "read_then_put", ["put_thing"])
        out = self._run(client, world, "read_then_put")
        assert out.status_code == 400 and "read-only" in out.json()["detail"]

    def test_an_unknown_declared_action_counts_as_a_write(
        self, client, db_session, world
    ):
        self._add(db_session, "read_odd", ["no_such_action"])
        assert self._run(client, world, "read_odd").status_code == 400
