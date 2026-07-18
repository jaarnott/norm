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
    CONFIG_DATABASE_URL: str = ""  # Shared config DB; REQUIRED in all environments
    JWT_SECRET: str = "dev-secret-change-in-production"
    CORS_ALLOWED_ORIGINS: str = "*"  # comma-separated list; "*" for dev only

    # ── Scheduler ───────────────────────────────────────────────────────
    # Shared secret required to invoke /internal/run-due-tasks (set by Cloud
    # Scheduler as a request header). Empty ⇒ endpoint rejects all callers.
    SCHEDULER_SECRET: str = ""
    # Timezone for cron-style schedules (daily/weekly/monthly) unless a task
    # overrides it via schedule_config["timezone"].
    SCHEDULER_TIMEZONE: str = "Pacific/Auckland"

    # ── LLM ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    LLM_INTERPRETER_MODEL: str = "claude-opus-4-8"
    ROUTER_MODEL: str = "claude-haiku-4-5-20251001"
    DATE_RESOLVER_MODEL: str = "claude-haiku-4-5-20251001"

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
    APP_URL: str = ""  # Auto-derived from ENVIRONMENT if not set

    # ── MCP server ──────────────────────────────────────────────────────
    # Norm's outward-facing MCP surface, for external AI clients (Claude)
    # acting on behalf of an authenticated user. Off by default: it stays
    # dark in every environment until deliberately switched on.
    MCP_ENABLED: bool = False
    # OAuth issuer. Must EXACTLY match the URL prefix the well-known metadata
    # is fetched from (RFC 8414 §3.3) — a mismatch makes clients reject the
    # document outright. Derived explicitly rather than from request.base_url,
    # which reports the internal host behind nginx.
    MCP_ISSUER: str = ""
    # Exact-match allowlist for OAuth redirect hosts. This is what stops a
    # dynamically-registered client from redirecting authorization codes to an
    # attacker's server.
    MCP_ALLOWED_REDIRECT_HOSTS: str = "claude.ai,*.claude.ai,localhost,127.0.0.1"
    # Development shortcut: a static bearer accepted at /mcp, acting as the
    # first admin user with that user's real scopes and venues. Refused unless
    # ENVIRONMENT == "local". No default — it does not exist unless set.
    MCP_DEV_TOKEN: str = ""

    # ── Observability (Phase 4) ─────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"
    SENTRY_DSN: str = ""
    GCP_PROJECT_ID: str = ""

    # ── Helpers ─────────────────────────────────────────────────────────

    @property
    def app_url(self) -> str:
        """Resolve APP_URL from explicit setting or derive from ENVIRONMENT."""
        if self.APP_URL:
            return self.APP_URL
        urls = {
            "production": "https://bettercallnorm.com",
            "staging": "https://staging.bettercallnorm.com",
            "testing": "https://testing.bettercallnorm.com",
            "local": "http://localhost:3000",
        }
        return urls.get(self.ENVIRONMENT, "http://localhost:3000")

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        if self.CORS_ALLOWED_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def mcp_issuer(self) -> str:
        """OAuth issuer for the MCP authorization server.

        Deployed environments serve the API **same-origin** with the app — the
        GCP load balancer path-routes /api, /mcp and /.well-known to the API
        backend on the app domain (there is no api.* subdomain). So the issuer
        must match app_url, or discovery advertises a host that doesn't resolve
        and RFC 8414 §3.3's issuer-matches-fetch-origin check fails. Locally the
        API is a separate port, so app_url (:3000) is wrong there — use :8000.
        """
        if self.MCP_ISSUER:
            return self.MCP_ISSUER.rstrip("/")
        if self.is_local:
            return "http://localhost:8000"
        return self.app_url.rstrip("/")

    @property
    def mcp_resource_url(self) -> str:
        """Canonical MCP resource identifier (RFC 8707 audience)."""
        return f"{self.mcp_issuer}/mcp"

    @property
    def mcp_allowed_redirect_hosts(self) -> list[str]:
        return [
            h.strip() for h in self.MCP_ALLOWED_REDIRECT_HOSTS.split(",") if h.strip()
        ]

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
