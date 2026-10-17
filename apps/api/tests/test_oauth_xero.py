"""Xero connect: the consent's organisation is bound to the venue, one per venue.

Xero's token response names no organisation. The callback reads the access
token's ``authentication_event_id`` claim, asks ``GET /connections?authEventId=``
which organisation that consent covered, and writes it into the venue's
``Connection.config`` (``xero_tenant_id`` — every endpoint row's
``xero-tenant-id`` header renders from it) with the organisation's country,
currency and short code. These tests pin that, and the refusals: no venue, no
organisation, two organisations in one consent, or a different organisation
from the one the venue is already bound to (tokens wiped, row flagged, page
names both — the shape of LoadedHub's wrong-company check). Also: the success
page never echoes the id_token, disconnect revokes at Xero and drops the
binding, and the keep-alive leaves a fresh ``refresh_policy: expiring`` row
alone while still redeeming LoadedHub's on every run.
"""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.connectors import xero_hooks
from app.db.config_models import ConnectionSpec
from app.db.models import Connection, OAuthState, Venue
from app.services.oauth_service import refresh_all_tokens

EVENT = "7d47b95c-e1cb-401d-b2ed-d5e283b10671"
TENANT_A = {
    "id": "conn-a",
    "tenantId": "11829a5f-0a36-438c-b908-54db9d2b5147",
    "tenantName": "Cook Brothers Bars Federal Street Limited",
    "tenantType": "ORGANISATION",
    "authEventId": EVENT,
    "createdDateUtc": "2026-10-17T02:06:15.5827020",
    "updatedDateUtc": "2026-10-17T02:06:15.5841300",
}
TENANT_B = {
    "id": "conn-b",
    "tenantId": "bbbbbbbb-5555-6666-7777-888888888888",
    "tenantName": "Demo Company (NZ)",
    "tenantType": "ORGANISATION",
    "authEventId": "older-event",
    "createdDateUtc": "2026-09-01T00:00:00.0000000",
    "updatedDateUtc": "2026-09-01T00:00:00.0000000",
}
ORG_A = {
    "Name": TENANT_A["tenantName"],
    "CountryCode": "NZ",
    "BaseCurrency": "NZD",
    "ShortCode": "!cDQvM",
    "Timezone": "NEWZEALANDSTANDARDTIME",
}


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"hdr.{body}.sig"


ACCESS = _jwt({"authentication_event_id": EVENT, "xero_userid": "u1"})
TOKEN_DATA = {
    "access_token": ACCESS,
    "refresh_token": "rt-1",
    "expires_in": 1800,
    "token_type": "Bearer",
    "scope": "openid offline_access accounting.invoices",
    "id_token": "eyJ.id.token",
}


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        return self._body


def _fake_xero(connections, org=ORG_A, calls=None):
    """httpx.get stand-in: /connections (filtered by authEventId) and /Organisation."""

    def get(url, headers=None, params=None, timeout=None):
        if calls is not None:
            calls.append((url, dict(params or {}), dict(headers or {})))
        if url == xero_hooks.CONNECTIONS_URL:
            event = (params or {}).get("authEventId")
            rows = [
                c for c in connections if not event or c.get("authEventId") == event
            ]
            return _Resp(200, rows)
        if url == xero_hooks.ORGANISATION_URL:
            return _Resp(200, {"Organisations": [org] if org else []})
        return _Resp(404, {}, "no such url")

    return get


def _setup(db_session, *, stored_tenant=None, stored_name=None, state_venue=True):
    """A xero spec + one venue whose row already holds freshly-exchanged tokens
    (exchange_code stores before the callback binds), + the state."""
    spec = ConnectionSpec(
        connector_name="xero",
        display_name="Xero",
        execution_mode="template",
        auth_type="oauth2",
        auth_config={},
        oauth_config={"token_url": "https://example.test/token", "client_id": "x"},
        tools=[],
    )
    db_session.add(spec)
    venue = Venue(id=str(uuid.uuid4()), name="Glass Goose")
    db_session.add(venue)
    db_session.flush()
    meta = {"id_token": "eyJ.id.token"}
    if stored_name:
        meta["tenant_name"] = stored_name
    cfg = Connection(
        connector_name="xero",
        venue_id=venue.id if state_venue else None,
        enabled="true",
        config={"xero_tenant_id": stored_tenant} if stored_tenant else {},
        access_token=ACCESS,
        refresh_token="rt-1",
        oauth_metadata=meta,
    )
    db_session.add(cfg)
    state = OAuthState(
        connector_name="xero",
        venue_id=venue.id if state_venue else None,
        state=f"state-{uuid.uuid4().hex}",
    )
    db_session.add(state)
    db_session.flush()
    return venue, cfg, state


def _callback(client, state, token_data, connections, org=ORG_A, calls=None):
    with (
        patch("app.routers.oauth.exchange_code", return_value=token_data),
        patch(
            "app.connectors.xero_hooks.httpx.get",
            new=_fake_xero(connections, org, calls),
        ),
    ):
        return client.get(
            f"/api/oauth/callback?code=abc&state={state.state}",
            follow_redirects=False,
        )


class TestClaims:
    def test_the_event_id_comes_from_the_access_token(self):
        assert xero_hooks.authentication_event_id(ACCESS) == EVENT

    def test_anything_else_is_none(self):
        for bad in (None, "", "not.a.jwt", "a.!!!.c", _jwt({"sub": "x"})):
            assert xero_hooks.authentication_event_id(bad) is None


class TestFirstConnect:
    def test_binds_the_consents_organisation_to_the_venue(self, client, db_session):
        venue, cfg, state = _setup(db_session)
        calls: list = []
        res = _callback(client, state, TOKEN_DATA, [TENANT_B, TENANT_A], calls=calls)
        assert res.status_code == 200, res.text
        assert (
            "Connected to Cook Brothers Bars Federal Street Limited (NZ, NZD)"
            in res.text
        )
        # The page names the organisation, never the token response.
        assert "id_token" not in res.text
        assert "eyJ" not in res.text
        # /connections was asked for THIS consent's organisations only…
        assert any(
            url == xero_hooks.CONNECTIONS_URL and params.get("authEventId") == EVENT
            for url, params, _ in calls
        )
        # …and /Organisation carried the tenant header.
        assert any(
            url == xero_hooks.ORGANISATION_URL
            and headers.get("xero-tenant-id") == TENANT_A["tenantId"]
            for url, _, headers in calls
        )
        db_session.refresh(cfg)
        assert cfg.config["xero_tenant_id"] == TENANT_A["tenantId"]
        assert cfg.config["xero_connection_id"] == "conn-a"
        assert cfg.config["xero_country"] == "NZ"
        assert cfg.config["xero_currency"] == "NZD"
        assert cfg.config["xero_short_code"] == "!cDQvM"
        assert cfg.oauth_metadata["tenant_name"] == TENANT_A["tenantName"]
        assert cfg.oauth_metadata["auth_event_id"] == EVENT
        assert "id_token" not in cfg.oauth_metadata
        assert cfg.access_token == ACCESS
        assert cfg.needs_reconnect is False

    def test_without_an_event_id_the_newest_organisation_wins(self, client, db_session):
        venue, cfg, state = _setup(db_session)
        token_data = {**TOKEN_DATA, "access_token": _jwt({"sub": "no-event"})}
        res = _callback(client, state, token_data, [TENANT_B, TENANT_A])
        assert res.status_code == 200, res.text
        db_session.refresh(cfg)
        assert cfg.config["xero_tenant_id"] == TENANT_A["tenantId"]

    def test_an_organisation_lookup_failure_still_binds(self, client, db_session):
        venue, cfg, state = _setup(db_session)
        res = _callback(client, state, TOKEN_DATA, [TENANT_A], org=None)
        assert res.status_code == 200, res.text
        assert "Connected to Cook Brothers Bars Federal Street Limited" in res.text
        db_session.refresh(cfg)
        assert cfg.config["xero_tenant_id"] == TENANT_A["tenantId"]
        assert cfg.config["xero_country"] == ""


class TestRefusals:
    def _assert_wiped(self, db_session, cfg):
        db_session.refresh(cfg)
        assert cfg.access_token is None
        assert cfg.refresh_token is None
        assert cfg.oauth_metadata is None
        assert cfg.needs_reconnect is True
        assert cfg.last_auth_error

    def test_two_organisations_in_one_consent(self, client, db_session):
        venue, cfg, state = _setup(db_session)
        both = [TENANT_A, {**TENANT_B, "authEventId": EVENT}]
        res = _callback(client, state, TOKEN_DATA, both)
        assert res.status_code == 400
        assert "Authorise one organisation per venue" in res.text
        assert "Cook Brothers Bars Federal Street Limited" in res.text
        assert "Demo Company (NZ)" in res.text
        self._assert_wiped(db_session, cfg)
        assert "xero_tenant_id" not in cfg.config

    def test_no_organisation_at_all(self, client, db_session):
        venue, cfg, state = _setup(db_session)
        res = _callback(client, state, TOKEN_DATA, [])
        assert res.status_code == 400
        assert "no organisation" in res.text
        self._assert_wiped(db_session, cfg)

    def test_a_different_organisation_from_the_bound_one(self, client, db_session):
        venue, cfg, state = _setup(
            db_session,
            stored_tenant=TENANT_B["tenantId"],
            stored_name="Demo Company (NZ)",
        )
        # The consent covered A; this venue is bound to B.
        res = _callback(client, state, TOKEN_DATA, [TENANT_A, TENANT_B])
        assert res.status_code == 400
        assert "Demo Company (NZ)" in res.text
        assert "Cook Brothers Bars Federal Street Limited" in res.text
        assert "disconnect this venue first" in res.text
        self._assert_wiped(db_session, cfg)
        # The binding is untouched — only the tokens were wrong.
        assert cfg.config["xero_tenant_id"] == TENANT_B["tenantId"]

    def test_no_venue_in_the_flow(self, client, db_session):
        venue, cfg, state = _setup(db_session, state_venue=False)
        res = _callback(client, state, TOKEN_DATA, [TENANT_A])
        assert res.status_code == 400
        assert "per venue" in res.text
        self._assert_wiped(db_session, cfg)


class TestDisconnect:
    def _bound(self, db_session):
        venue, cfg, state = _setup(db_session, stored_tenant=TENANT_A["tenantId"])
        cfg.config = {
            **cfg.config,
            "xero_connection_id": "conn-a",
            "xero_country": "NZ",
            "other_setting": "keep",
        }
        cfg.token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=25)
        db_session.flush()
        return venue, cfg

    def test_revokes_at_xero_and_drops_the_binding(
        self, client, db_session, admin_headers
    ):
        venue, cfg = self._bound(db_session)
        deleted: list = []

        def fake_delete(url, headers=None, timeout=None):
            deleted.append((url, dict(headers or {})))
            return _Resp(204)

        with patch("app.connectors.xero_hooks.httpx.delete", new=fake_delete):
            res = client.post(
                f"/api/oauth/disconnect/xero?venue_id={venue.id}", headers=admin_headers
            )
        assert res.status_code == 200, res.text
        assert res.json() == {"disconnected": True, "revoked": True}
        assert [u for u, _ in deleted] == [f"{xero_hooks.CONNECTIONS_URL}/conn-a"]
        assert deleted[0][1]["Authorization"] == f"Bearer {ACCESS}"
        db_session.refresh(cfg)
        assert cfg.access_token is None
        assert cfg.refresh_token is None
        assert "xero_tenant_id" not in cfg.config
        assert "xero_connection_id" not in cfg.config
        assert cfg.config["other_setting"] == "keep"

    def test_a_xero_error_still_clears_the_tokens(
        self, client, db_session, admin_headers
    ):
        venue, cfg = self._bound(db_session)
        with patch(
            "app.connectors.xero_hooks.httpx.delete",
            new=lambda *a, **k: _Resp(500, text="boom"),
        ):
            res = client.post(
                f"/api/oauth/disconnect/xero?venue_id={venue.id}", headers=admin_headers
            )
        assert res.status_code == 200
        assert res.json() == {"disconnected": True, "revoked": False}
        db_session.refresh(cfg)
        assert cfg.access_token is None
        assert "xero_tenant_id" not in cfg.config


class TestKeepAlivePolicy:
    def test_expiring_rows_wait_while_fresh_and_forced_rows_never_do(self, db_session):
        db_session.add(
            ConnectionSpec(
                connector_name="xero",
                display_name="Xero",
                execution_mode="template",
                auth_type="oauth2",
                auth_config={},
                oauth_config={
                    "token_url": "https://example.test/token",
                    "client_id": "x",
                    "refresh_policy": "expiring",
                },
                tools=[],
            )
        )
        db_session.add(
            ConnectionSpec(
                connector_name="loadedhub",
                display_name="LoadedHub",
                execution_mode="template",
                auth_type="oauth2",
                auth_config={},
                oauth_config={
                    "token_url": "https://example.test/token",
                    "client_id": "x",
                },
                tools=[],
            )
        )
        now = datetime.now(timezone.utc)
        rows = {
            "Fresh": ("xero", now + timedelta(minutes=25)),
            "Stale": ("xero", now + timedelta(minutes=5)),
            "Loaded": ("loadedhub", now + timedelta(days=13)),
        }
        for name, (connector, expires) in rows.items():
            venue = Venue(id=str(uuid.uuid4()), name=name)
            db_session.add(venue)
            db_session.flush()
            db_session.add(
                Connection(
                    connector_name=connector,
                    venue_id=venue.id,
                    enabled="true",
                    config={},
                    access_token="at",
                    refresh_token="rt",
                    token_expires_at=expires,
                )
            )
        db_session.flush()

        with patch("app.services.oauth_service.refresh_access_token") as refresh:
            out = refresh_all_tokens(db=db_session, config_db=db_session)

        assert "xero (Fresh)" in out["skipped"]
        assert "xero (Stale)" in out["refreshed"]
        assert "loadedhub (Loaded)" in out["refreshed"]
        assert refresh.call_count == 2
        assert all(call.kwargs["force"] is True for call in refresh.call_args_list)
