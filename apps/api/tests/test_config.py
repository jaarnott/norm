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
        assert s.LLM_INTERPRETER_MODEL == "claude-opus-5-5"
        assert s.BILLING_ENFORCEMENT is False
        assert s.is_local is True
        assert s.is_production is False


class TestLLMModelConfig:
    """Guard against retired Anthropic model IDs.

    Retired snapshots (e.g. ``claude-opus-4-20250514``) return HTTP 404
    ``not_found_error`` at request time, which is not caught by lint or
    typecheck. These tests fail fast if a dead ID is reintroduced.
    """

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
        """Every problem in one message, not the first one found — a deploy
        that fails on JWT_SECRET must not fail again on CORS next time."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET="dev-secret-change-in-production",
            CORS_ALLOWED_ORIGINS="*",
            _env_file=None,
        )
        with pytest.raises(RuntimeError) as err:
            s.validate_for_deploy()
        assert "JWT_SECRET must be changed" in str(err.value)
        assert "CORS_ALLOWED_ORIGINS must not be '*'" in str(err.value)

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
    """get_stripe_price_id is a getattr on STRIPE_PRICE_<PLAN>: known plans
    (core tiers and the add-ons), any case, an empty ID and an unknown plan
    both None. (Folds five one-line tests of the same lookup.)"""

    def test_plan_lookup(self):
        s = Settings(
            STRIPE_PRICE_BASIC="price_basic_123",
            STRIPE_PRICE_STANDARD="price_std_456",
            STRIPE_PRICE_MAX="price_max_789",
            STRIPE_PRICE_HR="price_hr_001",
            STRIPE_PRICE_PROCUREMENT="price_proc_002",
            STRIPE_PRICE_AGENT="price_agent_003",
            _env_file=None,
        )
        assert s.get_stripe_price_id("basic") == "price_basic_123"
        assert s.get_stripe_price_id("standard") == "price_std_456"
        assert s.get_stripe_price_id("max") == "price_max_789"
        assert s.get_stripe_price_id("MAX") == "price_max_789"
        assert s.get_stripe_price_id("hr") == "price_hr_001"
        assert s.get_stripe_price_id("procurement") == "price_proc_002"
        # Every hired team member shares one price since Oct 2026.
        assert s.get_stripe_price_id("agent") == "price_agent_003"
        assert s.get_stripe_price_id("nonexistent") is None
        # When the price ID is an empty string, return None.
        assert (
            Settings(STRIPE_PRICE_BASIC="", _env_file=None).get_stripe_price_id("basic")
            is None
        )
