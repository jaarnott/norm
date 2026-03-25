"""Centralized application configuration.

All environment variables are read here via Pydantic BaseSettings.
Other modules should ``from app.config import settings`` instead of
calling ``os.environ.get()`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Core ────────────────────────────────────────────────────────────
    ENVIRONMENT: Literal["local", "testing", "staging", "production"] = "local"
    DATABASE_URL: str = "postgresql://norm:norm@localhost:5432/norm"
    DATABASE_READ_URL: str = ""  # Read replica URL; empty = use primary for reads
    JWT_SECRET: str = "dev-secret-change-in-production"
    CORS_ALLOWED_ORIGINS: str = "*"  # comma-separated list; "*" for dev only

    # ── LLM ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    LLM_INTERPRETER_MODEL: str = "claude-opus-4-20250514"
    ROUTER_MODEL: str = "claude-haiku-4-5-20251001"

    # ── Stripe / Billing ────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    BILLING_ENFORCEMENT: bool = False

    # Stripe price IDs (set per environment)
    STRIPE_PRICE_BASIC: str = ""
    STRIPE_PRICE_STANDARD: str = ""
    STRIPE_PRICE_MAX: str = ""
    STRIPE_PRICE_HR: str = ""
    STRIPE_PRICE_PROCUREMENT: str = ""
    STRIPE_PRICE_VENUE: str = ""

    # ── Connectors ──────────────────────────────────────────────────────
    OAUTH_REDIRECT_URI: str = ""
    BAMBOOHR_SUBDOMAIN: str = ""
    BAMBOOHR_API_KEY: str = ""

    # ── Email ─────────────────────────────────────────────────────────
    RESEND_API_KEY: str = ""
    EMAIL_FROM_ADDRESS: str = "noreply@cbhg.co.nz"
    EMAIL_FROM_NAME: str = "Norm"
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    APP_URL: str = "https://bettercallnorm.com"

    # ── Observability (Phase 4) ─────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"
    SENTRY_DSN: str = ""
    GCP_PROJECT_ID: str = ""

    # ── Helpers ─────────────────────────────────────────────────────────

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        if self.CORS_ALLOWED_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_local(self) -> bool:
        return self.ENVIRONMENT == "local"

    def validate_for_deploy(self) -> None:
        """Fail fast if required secrets are missing in non-local environments."""
        if self.is_local:
            return
        errors: list[str] = []
        if self.JWT_SECRET == "dev-secret-change-in-production":
            errors.append("JWT_SECRET must be changed from the dev default")
        if self.CORS_ALLOWED_ORIGINS == "*":
            errors.append(
                "CORS_ALLOWED_ORIGINS must not be '*' in deployed environments"
            )
        if errors:
            raise RuntimeError(
                "Configuration errors for "
                f"{self.ENVIRONMENT} environment:\n  - " + "\n  - ".join(errors)
            )

    def get_stripe_price_id(self, plan: str) -> str | None:
        """Look up a Stripe price ID by plan name."""
        return getattr(self, f"STRIPE_PRICE_{plan.upper()}", None) or None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — call this from anywhere."""
    return Settings()


# Convenience alias
settings = get_settings()
