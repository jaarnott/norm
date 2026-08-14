"""Per-venue LoadedHub connect must refuse a wrong-company token.

The 14 Aug incident: reconnect clicks made while the Loaded session had Bessie
active minted Bessie tokens for four other venues, and the callback stored each
one and said "Connected successfully" — every venue then served Bessie's data
(Loaded scopes data by the token, not the x-loaded-company-id header). These
tests pin the callback validation: a token whose company doesn't match the
venue's stored ``x_loaded_company_id`` is rejected loudly (tokens wiped,
``needs_reconnect`` set, failure page); a matching token succeeds; a venue with
no stored company id adopts the token's.
"""

import uuid
from unittest.mock import patch

from app.db.models import ConnectorConfig, OAuthState, Venue
from app.db.config_models import ConnectorSpec

COMPANY_A = "aaaaaaaa-1111-2222-3333-444444444444"
COMPANY_B = "bbbbbbbb-5555-6666-7777-888888888888"


def _setup(db_session, *, stored_company, state_venue=True):
    """A loadedhub spec + one venue whose row already holds freshly-exchanged
    tokens (exchange_code stores before the callback validates), + the state."""
    spec = ConnectorSpec(
        connector_name="loadedhub",
        display_name="LoadedHub",
        execution_mode="template",
        auth_type="oauth2",
        auth_config={},
        oauth_config={"token_url": "https://example.test/token", "client_id": "x"},
        tools=[],
    )
    db_session.add(spec)
    venue = Venue(id=str(uuid.uuid4()), name="La Zeppa")
    db_session.add(venue)
    db_session.flush()  # venue row must exist before FK'd rows below
    cfg = ConnectorConfig(
        connector_name="loadedhub",
        venue_id=venue.id,
        enabled="true",
        config={"x_loaded_company_id": stored_company} if stored_company else {},
        access_token="freshly-exchanged-token",
        refresh_token="freshly-exchanged-refresh",
    )
    db_session.add(cfg)
    state = OAuthState(
        connector_name="loadedhub",
        venue_id=venue.id if state_venue else None,
        state=f"state-{uuid.uuid4().hex}",
    )
    db_session.add(state)
    db_session.flush()
    return venue, cfg, state


def _callback(client, state, token_data):
    with patch("app.routers.oauth.exchange_code", return_value=token_data):
        return client.get(
            f"/api/oauth/callback?code=abc&state={state.state}",
            follow_redirects=False,
        )


class TestWrongCompanyRejected:
    def test_mismatch_wipes_tokens_and_fails_loudly(self, client, db_session):
        venue, cfg, state = _setup(db_session, stored_company=COMPANY_A)
        res = _callback(
            client,
            state,
            {
                "access_token": "tok",
                "venue_id": COMPANY_B,
                "venue_name": "Bessie & Engineers",
            },
        )
        assert res.status_code == 400
        assert "Bessie &amp; Engineers" in res.text or "Bessie & Engineers" in res.text
        assert "La Zeppa" in res.text
        db_session.refresh(cfg)
        assert cfg.access_token is None
        assert cfg.refresh_token is None
        assert cfg.needs_reconnect is True
        assert "wrong Loaded company" in (cfg.last_auth_error or "")
        # The configured company id is untouched — only the tokens were bad.
        assert cfg.config["x_loaded_company_id"] == COMPANY_A

    def test_whitespace_in_stored_id_still_matches(self, client, db_session):
        # La Zeppa's real stored id carries a leading space — must not reject.
        venue, cfg, state = _setup(db_session, stored_company=f" {COMPANY_A}")
        res = _callback(client, state, {"access_token": "tok", "venue_id": COMPANY_A})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.access_token == "freshly-exchanged-token"
        assert not cfg.needs_reconnect


class TestAcceptedPaths:
    def test_matching_company_succeeds(self, client, db_session):
        venue, cfg, state = _setup(db_session, stored_company=COMPANY_A)
        res = _callback(client, state, {"access_token": "tok", "venue_id": COMPANY_A})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.access_token == "freshly-exchanged-token"
        assert not cfg.needs_reconnect

    def test_first_connect_adopts_company_id(self, client, db_session):
        venue, cfg, state = _setup(db_session, stored_company=None)
        res = _callback(client, state, {"access_token": "tok", "venue_id": COMPANY_B})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.config["x_loaded_company_id"] == COMPANY_B
        assert cfg.access_token == "freshly-exchanged-token"

    def test_no_company_in_token_response_keeps_legacy_behavior(
        self, client, db_session
    ):
        venue, cfg, state = _setup(db_session, stored_company=COMPANY_A)
        res = _callback(client, state, {"access_token": "tok"})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.access_token == "freshly-exchanged-token"
