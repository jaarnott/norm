"""Tests for send-on-behalf email (Gmail / Outlook).

Regression cover for a bug that made both paths raise instead of send:

    access_token = get_valid_access_token(db, config)   # (spec, db, ...) expected

`db` bound to `spec` and the ConnectorConfig bound to `db`, so the function ran
`config.query(...)` — AttributeError. Both call sites' try/except blocks start
*after* that line, so it propagated instead of degrading to the intended
"token expired, please reconnect" response.

It shipped because nothing tested these functions. The token lookup is now
`_access_token_for()`, which resolves the ConnectorSpec from the config DB (where
specs live) and passes tokens from the main DB — the two-database split is the
part that made the original call easy to get wrong.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.email_service import (
    _access_token_for,
    send_on_behalf_gmail,
    send_on_behalf_outlook,
)


@pytest.fixture
def gmail_user(db_session):
    from app.db.models import ConnectorConfig
    from tests.conftest import _make_user

    user = _make_user(db_session, email="sender@test.com")
    db_session.add(
        ConnectorConfig(
            connector_name="gmail",
            user_id=user.id,
            venue_id=None,
            config={},
            enabled="true",
            access_token="stored-token",
            refresh_token="refresh",
        )
    )
    db_session.flush()
    return user


@pytest.fixture
def outlook_user(db_session):
    from app.db.models import ConnectorConfig
    from tests.conftest import _make_user

    user = _make_user(db_session, email="sender2@test.com")
    db_session.add(
        ConnectorConfig(
            connector_name="microsoft_outlook",
            user_id=user.id,
            venue_id=None,
            config={},
            enabled="true",
            access_token="stored-token",
            refresh_token="refresh",
        )
    )
    db_session.flush()
    return user


class TestAccessTokenLookup:
    """_access_token_for must call get_valid_access_token correctly."""

    def test_passes_spec_and_db_in_the_right_order(self, db_session):
        """The original bug: args were swapped, so config.query() blew up."""
        spec = MagicMock()
        config_db = MagicMock()
        config_db.query.return_value.filter.return_value.first.return_value = spec

        with (
            patch("app.db.engine._ConfigSessionLocal", return_value=config_db),
            patch(
                "app.services.oauth_service.get_valid_access_token",
                return_value="fresh-token",
            ) as mock_get,
        ):
            token = _access_token_for("gmail", db_session, "user-1")

        assert token == "fresh-token"
        # spec first, db second — and scoped by user, since email connectors
        # are per-user rather than per-venue.
        assert mock_get.call_args.args[0] is spec
        assert mock_get.call_args.args[1] is db_session
        assert mock_get.call_args.kwargs["user_id"] == "user-1"

    def test_missing_spec_returns_none_rather_than_raising(self, db_session):
        config_db = MagicMock()
        config_db.query.return_value.filter.return_value.first.return_value = None

        with patch("app.db.engine._ConfigSessionLocal", return_value=config_db):
            assert _access_token_for("gmail", db_session, "user-1") is None

    def test_config_session_is_always_closed(self, db_session):
        config_db = MagicMock()
        config_db.query.return_value.filter.return_value.first.return_value = (
            MagicMock()
        )

        with (
            patch("app.db.engine._ConfigSessionLocal", return_value=config_db),
            patch(
                "app.services.oauth_service.get_valid_access_token",
                side_effect=ValueError("boom"),
            ),
            pytest.raises(ValueError),
        ):
            _access_token_for("gmail", db_session, "user-1")

        config_db.close.assert_called_once()


class TestGmailSendOnBehalf:
    def test_sends_with_a_refreshed_token(self, db_session, gmail_user):
        with (
            patch(
                "app.services.email_service._access_token_for",
                return_value="fresh-token",
            ),
            patch("app.services.email_service.httpx.post") as mock_post,
        ):
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"id": "msg-1"}
            )
            result = send_on_behalf_gmail(
                user_id=gmail_user.id,
                to=["recipient@test.com"],
                subject="Hi",
                html_body="<p>Hi</p>",
                db=db_session,
            )

        assert result["success"] is True
        # The refreshed token must be the one on the wire, not the stored one.
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer fresh-token"
        )

    def test_token_failure_returns_reconnect_message_not_an_exception(
        self, db_session, gmail_user
    ):
        """This is the regression: it used to raise AttributeError."""
        with patch(
            "app.services.email_service._access_token_for",
            side_effect=ValueError("Token refresh failed (400)"),
        ):
            result = send_on_behalf_gmail(
                user_id=gmail_user.id,
                to=["recipient@test.com"],
                subject="Hi",
                html_body="<p>Hi</p>",
                db=db_session,
            )

        assert result["success"] is False
        assert "reconnect" in result["error"].lower()

    def test_not_connected_is_reported_cleanly(self, db_session):
        from tests.conftest import _make_user

        user = _make_user(db_session, email="nogmail@test.com")
        result = send_on_behalf_gmail(
            user_id=user.id,
            to=["r@test.com"],
            subject="Hi",
            html_body="<p>Hi</p>",
            db=db_session,
        )
        assert result["success"] is False
        assert "not connected" in result["error"].lower()

    def test_api_error_is_recorded_not_raised(self, db_session, gmail_user):
        with (
            patch("app.services.email_service._access_token_for", return_value="tok"),
            patch("app.services.email_service.httpx.post") as mock_post,
        ):
            mock_post.return_value = MagicMock(status_code=403, text="Forbidden")
            result = send_on_behalf_gmail(
                user_id=gmail_user.id,
                to=["r@test.com"],
                subject="Hi",
                html_body="<p>Hi</p>",
                db=db_session,
            )

        assert result["success"] is False
        assert "403" in result["error"]


class TestOutlookSendOnBehalf:
    def test_sends_with_a_refreshed_token(self, db_session, outlook_user):
        with (
            patch(
                "app.services.email_service._access_token_for",
                return_value="fresh-token",
            ),
            patch("app.services.email_service.httpx.post") as mock_post,
        ):
            mock_post.return_value = MagicMock(status_code=202, text="")
            result = send_on_behalf_outlook(
                user_id=outlook_user.id,
                to=["recipient@test.com"],
                subject="Hi",
                html_body="<p>Hi</p>",
                db=db_session,
            )

        assert mock_post.call_args.kwargs["headers"]["Authorization"] == (
            "Bearer fresh-token"
        )
        assert result["success"] is True

    def test_token_failure_returns_reconnect_message_not_an_exception(
        self, db_session, outlook_user
    ):
        with patch(
            "app.services.email_service._access_token_for",
            side_effect=ValueError("Token refresh failed (400)"),
        ):
            result = send_on_behalf_outlook(
                user_id=outlook_user.id,
                to=["recipient@test.com"],
                subject="Hi",
                html_body="<p>Hi</p>",
                db=db_session,
            )

        assert result["success"] is False
        assert "reconnect" in result["error"].lower()
