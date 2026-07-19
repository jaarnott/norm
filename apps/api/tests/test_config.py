"""Tests for the Settings configuration module."""

import pytest

from app.config import Settings
from app.services.models import RETIRED_MODEL_IDS


class TestDefaultConfig:
    """Verify default values load correctly."""

    def test_defaults_load(self, monkeypatch):
        # Clear CI env vars so we test actual defaults
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        s = Settings(
            DATABASE_URL="postgresql://test:test@localhost/test",
            _env_file=None,
        )
        assert s.ENVIRONMENT == "local"
        assert s.JWT_SECRET == "dev-secret-change-in-production"
        assert s.CORS_ALLOWED_ORIGINS == "*"
        assert s.LLM_INTERPRETER_MODEL == "claude-opus-4-8"
        assert s.BILLING_ENFORCEMENT is False

    def test_is_local_true_by_default(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        s = Settings(_env_file=None)
        assert s.is_local is True

    def test_is_production_false_by_default(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        s = Settings(_env_file=None)
        assert s.is_production is False


class TestLLMModelConfig:
    """Guard against retired Anthropic model IDs.

    Retired snapshots (e.g. ``claude-opus-4-20250514``) return HTTP 404
    ``not_found_error`` at request time, which is not caught by lint or
    typecheck. These tests fail fast if a dead ID is reintroduced.
    """

    def test_interpreter_default_is_a_current_model(self, monkeypatch):
        monkeypatch.delenv("LLM_INTERPRETER_MODEL", raising=False)
        s = Settings(_env_file=None)
        assert s.LLM_INTERPRETER_MODEL not in RETIRED_MODEL_IDS
        assert s.LLM_INTERPRETER_MODEL == "claude-opus-4-8"

    def test_router_and_date_resolver_defaults_are_current(self, monkeypatch):
        monkeypatch.delenv("ROUTER_MODEL", raising=False)
        monkeypatch.delenv("DATE_RESOLVER_MODEL", raising=False)
        s = Settings(_env_file=None)
        assert s.ROUTER_MODEL not in RETIRED_MODEL_IDS
        assert s.DATE_RESOLVER_MODEL not in RETIRED_MODEL_IDS

    def test_no_retired_model_ids_referenced_in_source(self):
        """Scan the whole app package so a dead ID anywhere fails CI."""
        import pathlib

        app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
        # models.py is where RETIRED_MODEL_IDS is defined, so it necessarily
        # contains these strings — it's the denylist, not a usage.
        definition_file = app_dir / "services" / "models.py"
        offenders = []
        for path in app_dir.rglob("*.py"):
            if path == definition_file:
                continue
            text = path.read_text(encoding="utf-8")
            for retired in RETIRED_MODEL_IDS:
                if retired in text:
                    offenders.append(f"{path.relative_to(app_dir.parent)}: {retired}")
        assert not offenders, "Retired model IDs found in source:\n" + "\n".join(
            offenders
        )


class TestCorsOriginsList:
    """Test cors_origins_list property parsing."""

    def test_wildcard(self):
        s = Settings(CORS_ALLOWED_ORIGINS="*", _env_file=None)
        assert s.cors_origins_list == ["*"]

    def test_multiple_origins(self):
        s = Settings(
            CORS_ALLOWED_ORIGINS="https://app.norm.dev, https://staging.norm.dev",
            _env_file=None,
        )
        assert s.cors_origins_list == [
            "https://app.norm.dev",
            "https://staging.norm.dev",
        ]

    def test_strips_whitespace_and_ignores_empty_entries(self):
        s = Settings(
            CORS_ALLOWED_ORIGINS="  https://a.com , ,https://b.com,  ",
            _env_file=None,
        )
        assert s.cors_origins_list == ["https://a.com", "https://b.com"]


class TestValidateForDeploy:
    """Test validate_for_deploy raises in non-local environments."""

    def test_local_env_passes_with_dev_defaults(self):
        s = Settings(ENVIRONMENT="local", _env_file=None)
        # Should not raise
        s.validate_for_deploy()

    def test_staging_with_dev_jwt_secret_raises(self):
        s = Settings(
            ENVIRONMENT="staging",
            JWT_SECRET="dev-secret-change-in-production",
            CORS_ALLOWED_ORIGINS="https://staging.norm.dev",
            _env_file=None,
        )
        with pytest.raises(RuntimeError, match="JWT_SECRET must be changed"):
            s.validate_for_deploy()

    def test_production_with_wildcard_cors_raises(self):
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET="real-production-secret-key-here",
            CORS_ALLOWED_ORIGINS="*",
            _env_file=None,
        )
        with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
            s.validate_for_deploy()

    def test_production_with_both_bad_raises_both_errors(self):
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET="dev-secret-change-in-production",
            CORS_ALLOWED_ORIGINS="*",
            _env_file=None,
        )
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            s.validate_for_deploy()

    def test_staging_with_proper_config_passes(self):
        s = Settings(
            ENVIRONMENT="staging",
            JWT_SECRET="a-real-secret-for-staging",
            CORS_ALLOWED_ORIGINS="https://staging.norm.dev",
            _env_file=None,
        )
        # Should not raise
        s.validate_for_deploy()


class TestGetStripePriceId:
    """Test get_stripe_price_id returns correct values."""

    def test_known_plan(self):
        s = Settings(
            STRIPE_PRICE_BASIC="price_basic_123",
            STRIPE_PRICE_STANDARD="price_std_456",
            _env_file=None,
        )
        assert s.get_stripe_price_id("basic") == "price_basic_123"
        assert s.get_stripe_price_id("standard") == "price_std_456"

    def test_case_insensitive(self):
        s = Settings(STRIPE_PRICE_MAX="price_max_789", _env_file=None)
        assert s.get_stripe_price_id("max") == "price_max_789"
        assert s.get_stripe_price_id("MAX") == "price_max_789"

    def test_empty_string_returns_none(self):
        """When the price ID is an empty string, return None."""
        s = Settings(STRIPE_PRICE_BASIC="", _env_file=None)
        assert s.get_stripe_price_id("basic") is None

    def test_unknown_plan_returns_none(self):
        s = Settings(_env_file=None)
        assert s.get_stripe_price_id("nonexistent") is None

    def test_addon_plans(self):
        s = Settings(
            STRIPE_PRICE_HR="price_hr_001",
            STRIPE_PRICE_PROCUREMENT="price_proc_002",
            STRIPE_PRICE_VENUE="price_venue_003",
            _env_file=None,
        )
        assert s.get_stripe_price_id("hr") == "price_hr_001"
        assert s.get_stripe_price_id("procurement") == "price_proc_002"
        assert s.get_stripe_price_id("venue") == "price_venue_003"
