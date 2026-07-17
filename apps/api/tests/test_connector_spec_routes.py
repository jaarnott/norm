"""Tests for the connector-spec admin routes (the Settings → Connectors buttons).

Both regressions here were found while chasing a LoadedHub incident, and both
live in the router rather than the executor — which is why executor-level tests
missed them:

- "Test" (`/{name}/test`) accepted a venue_id and never passed it on, so
  `get_valid_access_token` fell back to an unfiltered `.first()` and
  authenticated as an arbitrary venue. LoadedHub scopes data by the token
  itself (it ignores `x-loaded-company-id`), so this silently returned a
  different venue's data — or an empty result that looks like a data problem.

- "Dry run" (`/{name}/dry-run`) renders a preview that never reaches the wire,
  but went through the executor's require-a-real-token guard and so failed with
  "Reconnect loadedhub" for anyone previewing a template.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.db.config_models import ConnectorSpec
from app.db.models import ConnectorConfig


TOOL = {
    "action": "get_stock_on_hand",
    "method": "GET",
    "path_template": "/stock",
    "required_fields": [],
}


@pytest.fixture()
def config_client(client, db_session):
    """`client` only overrides get_db; these routes also resolve get_config_db.

    Point it at the same rolled-back session so the spec fixture is visible.
    Tests already run with CONFIG_DATABASE_URL on the same throwaway Postgres.
    """
    from app.db.engine import get_config_db

    from tests.conftest import _test_app

    _test_app.dependency_overrides[get_config_db] = lambda: db_session
    yield client
    _test_app.dependency_overrides.pop(get_config_db, None)


@pytest.fixture()
def spec(db_session):
    row = ConnectorSpec(
        connector_name="loadedhub",
        display_name="LoadedHub",
        execution_mode="template",
        auth_type="oauth2",
        auth_config={},
        oauth_config={
            "token_url": "https://auth.example.com/token",
            "client_id": "cid",
            "client_secret": "secret",
        },
        base_url_template="https://api.example.com",
        tools=[TOOL],
    )
    db_session.add(row)
    db_session.flush()
    yield row


def _venue_config(db_session, venue_id):
    row = ConnectorConfig(
        connector_name="loadedhub",
        venue_id=venue_id,
        enabled="true",
        config={},
    )
    db_session.add(row)
    db_session.flush()
    return row


class TestTestButtonUsesTheRequestedVenuesToken:
    def test_venue_id_reaches_the_token_lookup(
        self, config_client, db_session, admin_headers, spec, organization
    ):
        from tests.conftest import _make_venue

        other = _make_venue(
            db_session, name="Other Venue", organization_id=organization.id
        )
        target = _make_venue(
            db_session, name="The Glass Goose", organization_id=organization.id
        )
        _venue_config(db_session, other.id)
        _venue_config(db_session, target.id)

        with (
            patch(
                "app.services.oauth_service.get_valid_access_token", return_value="tok"
            ) as get_token,
            patch(
                "app.connectors.spec_executor.httpx.request",
                return_value=MagicMock(status_code=200, json=lambda: {}, text="{}"),
            ),
        ):
            resp = config_client.post(
                "/api/connector-specs/loadedhub/test",
                headers=admin_headers,
                json={
                    "extracted_fields": {},
                    "tool_action": "get_stock_on_hand",
                    "venue_id": str(target.id),
                },
            )

        assert resp.status_code == 200, resp.text
        assert get_token.called, "the test button must resolve an OAuth token"
        # The bug: venue_id was dropped, so this was None and the token lookup
        # picked whichever venue's row came back first.
        assert get_token.call_args.kwargs.get("venue_id") == str(target.id)


class TestDryRunPreviewsWithoutALiveToken:
    def test_dry_run_renders_when_no_token_is_stored(
        self, config_client, db_session, admin_headers, spec
    ):
        """A preview must not require a working OAuth connection."""
        resp = config_client.post(
            "/api/connector-specs/loadedhub/dry-run",
            headers=admin_headers,
            json={"extracted_fields": {}, "tool_action": "get_stock_on_hand"},
        )

        assert resp.status_code == 200, resp.text
        rendered = resp.json()["rendered_request"]
        assert rendered["url"] == "https://api.example.com/stock"
        # And it still must not print a token — real or placeholder.
        assert rendered["headers"]["Authorization"] == "••••••••"
