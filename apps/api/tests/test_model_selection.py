"""Tests for central model selection (app/services/models.py).

Every LLM call resolves its model here so the Settings selectors govern the
whole app and no model ID is hardcoded at a call site. These tests pin the
resolution order: explicit override → DB selector → Settings default.
"""

from app.config import settings
from app.db.models import ConnectorConfig
from app.services.models import agent_model, router_model


def _set_selector(db_session, **config):
    """Create the Anthropic platform connector config the selector writes to."""
    row = ConnectorConfig(
        connector_name="anthropic",
        venue_id=None,
        config=config,
        enabled="true",
    )
    db_session.add(row)
    db_session.flush()
    return row


class TestAgentModel:
    def test_defaults_to_settings_without_db(self):
        assert agent_model() == settings.LLM_INTERPRETER_MODEL

    def test_override_wins_without_db(self):
        assert agent_model(override="claude-sonnet-5") == "claude-sonnet-5"

    def test_reads_selector_from_db(self, db_session):
        _set_selector(db_session, interpreter_model="claude-sonnet-5")
        assert agent_model(db_session) == "claude-sonnet-5"

    def test_override_beats_db_selector(self, db_session):
        _set_selector(db_session, interpreter_model="claude-sonnet-5")
        assert agent_model(db_session, override="claude-opus-4-8") == "claude-opus-4-8"

    def test_falls_back_to_settings_when_selector_absent(self, db_session):
        # Anthropic config exists (e.g. just the api_key) but no model selected.
        _set_selector(db_session, api_key="sk-ant-test")
        assert agent_model(db_session) == settings.LLM_INTERPRETER_MODEL


class TestRouterModel:
    def test_defaults_to_settings_without_db(self):
        assert router_model() == settings.ROUTER_MODEL

    def test_reads_selector_from_db(self, db_session):
        _set_selector(db_session, router_model="claude-haiku-4-5-20251001")
        assert router_model(db_session) == "claude-haiku-4-5-20251001"

    def test_override_wins(self, db_session):
        _set_selector(db_session, router_model="claude-haiku-4-5-20251001")
        assert router_model(db_session, override="claude-sonnet-5") == "claude-sonnet-5"
