"""A card's own button acts only at the presser's venues, and is on the record.

Place Order, a menu or roster Save, Accept and Receive on an invoice — each is
a person making a change themselves, so pressing the button is the approval
(services/card_actions.py). Until Oct 2026 these routes took any venue_id the
browser sent and used that venue's Loaded login — with none, whichever venue's
login came first, another organisation's included — and left no trace of who
pressed what.
"""

import pytest

from app.auth.security import create_access_token
from app.db.config_models import ComponentApiConfig
from app.db.models import Approval
from tests.conftest import (
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


@pytest.fixture()
def world(db_session):
    org, other = _make_organization(db_session), _make_organization(db_session)
    mine = _make_venue(db_session, name="Freeman & Grey", organization_id=org.id)
    theirs = _make_venue(db_session, name="Elsewhere", organization_id=other.id)
    me = _make_user(db_session)
    _make_venue_access(db_session, me, mine)
    return me, mine, theirs


@pytest.fixture()
def component(db_session, monkeypatch):
    for action, method in (("place_order", "POST"), ("get_suppliers", "GET")):
        db_session.add(
            ComponentApiConfig(
                component_key="purchase_order_editor",
                connector_name="loadedhub",
                action_name=action,
                method=method,
                path_template="//api.example/x",
            )
        )
    db_session.flush()
    calls = []

    def fake(component_key, action_name, params, venue_id, db, config_db):
        calls.append((action_name, venue_id))
        return {"data": {"ok": True}, "status_code": 200}

    monkeypatch.setattr("app.services.component_api.execute_component_action", fake)
    return calls


def _call(client, user, action, venue_id=None):
    return client.post(
        f"/api/component-api/purchase_order_editor/{action}",
        json={"venue_id": venue_id, "params": {}},
        headers=_headers(user),
    )


class TestComponentButtons:
    def test_another_venues_login_is_refused(self, client, world, component):
        me, _, theirs = world
        r = _call(client, me, "place_order", theirs.id)
        assert r.status_code == 403
        assert component == []

    def test_no_venue_means_your_only_one_never_the_first_login(
        self, client, db_session, world, component
    ):
        me, mine, _ = world
        assert _call(client, me, "get_suppliers").status_code == 200
        assert component == [("get_suppliers", mine.id)]

        second = _make_venue(
            db_session, name="La Zeppa", organization_id=mine.organization_id
        )
        _make_venue_access(db_session, me, second)
        r = _call(client, me, "get_suppliers")
        assert r.status_code == 400 and "Choose a venue" in r.json()["detail"]

    def test_a_write_is_recorded_a_read_is_not(
        self, client, db_session, world, component
    ):
        me, mine, _ = world
        assert _call(client, me, "get_suppliers", mine.id).status_code == 200
        assert _call(client, me, "place_order", mine.id).status_code == 200
        rows = db_session.query(Approval).filter(Approval.user_id == me.id).all()
        assert [(r.action, r.notes) for r in rows] == [
            (
                "card_write",
                "purchase_order_editor.place_order at Freeman & Grey: HTTP 200",
            )
        ]

    def test_a_platform_admin_may_act_anywhere(
        self, client, world, component, admin_headers
    ):
        _, _, theirs = world
        r = client.post(
            "/api/component-api/purchase_order_editor/get_suppliers",
            json={"venue_id": theirs.id, "params": {}},
            headers=admin_headers,
        )
        assert r.status_code == 200


class TestInvoiceButtons:
    def test_every_route_refuses_another_venue(self, client, world):
        me, _, theirs = world
        h = _headers(me)
        assert (
            client.get(
                f"/api/invoice-fixes/outstanding?venue_id={theirs.id}", headers=h
            ).status_code
            == 403
        )
        for path, body in (
            ("/api/invoice-fixes/create-unit", {"venue_id": theirs.id, "name": "Kilo"}),
            (
                "/api/invoice-fixes/receive",
                {"venue_id": theirs.id, "invoice_id": "i-1"},
            ),
            (
                "/api/invoice-fixes/accept",
                {"venue_id": theirs.id, "invoice_id": "i-1", "fix": {}},
            ),
        ):
            assert client.post(path, json=body, headers=h).status_code == 403, path

    def test_a_created_unit_is_recorded(self, client, db_session, world, monkeypatch):
        import app.routers.invoice_fixes as IF

        me, mine, _ = world
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        monkeypatch.setattr(
            IF,
            "_get_or_create_unit",
            lambda lh, name, db: ({"id": "u-1", "name": name, "ratio": 1}, True),
        )
        r = client.post(
            "/api/invoice-fixes/create-unit",
            json={"venue_id": mine.id, "name": "Kilo"},
            headers=_headers(me),
        )
        assert r.status_code == 200, r.text
        row = db_session.query(Approval).filter(Approval.user_id == me.id).one()
        assert row.notes == "created a unit at Freeman & Grey: 'Kilo'"

    def test_a_receive_is_recorded(self, client, db_session, world, monkeypatch):
        import app.routers.invoice_fixes as IF

        me, mine, _ = world
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: object())
        monkeypatch.setattr(
            IF, "_do_receive", lambda lh, req: {"ok": True, "received": False}
        )
        r = client.post(
            "/api/invoice-fixes/receive",
            json={
                "venue_id": mine.id,
                "invoice_id": "i-1",
                "lines": [{"id": "l-1"}],
                "receive": False,
            },
            headers=_headers(me),
        )
        assert r.status_code == 200, r.text
        row = db_session.query(Approval).filter(Approval.user_id == me.id).one()
        assert row.notes == "saved changes to an invoice at Freeman & Grey: invoice i-1"
