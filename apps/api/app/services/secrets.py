"""Shared secret-lookup helper.

Checks ConnectorConfig in DB first, then falls back to environment variables.
"""

import os

from sqlalchemy.orm import Session

# Mapping: (connector_name, key) -> environment variable name
_ENV_VAR_MAP: dict[tuple[str, str], str] = {
    ("anthropic", "api_key"): "ANTHROPIC_API_KEY",
    ("bamboohr", "api_key"): "BAMBOOHR_API_KEY",
    ("bamboohr", "subdomain"): "BAMBOOHR_SUBDOMAIN",
}


def get_api_key(connector_name: str, key: str, db: Session | None = None) -> str | None:
    """Check DB (ConnectorConfig) first, then fall back to env var."""
    if db is not None:
        from app.db.models import ConnectorConfig

        row = db.query(ConnectorConfig).filter(
            ConnectorConfig.connector_name == connector_name,
            ConnectorConfig.enabled == "true",
        ).first()
        if row and row.config.get(key):
            return row.config[key]

    env_var = _ENV_VAR_MAP.get((connector_name, key))
    if env_var:
        return os.environ.get(env_var)

    return None
