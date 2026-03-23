import logging

from sqlalchemy.orm import Session

from app.connectors.base import BaseConnector

logger = logging.getLogger(__name__)


def get_connector(domain: str, db: Session) -> BaseConnector:
    """Build a BaseConnector instance from DB config.

    Used only for legacy code paths that need a BaseConnector.
    Prefer resolve_connector() for spec-driven resolution.
    """
    from app.db.models import ConnectorConfig

    # Look up the connector config for this domain
    row = (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == domain,
            ConnectorConfig.enabled == "true",
        )
        .first()
    )

    if row and domain == "bamboohr":
        from app.connectors.bamboohr import BambooHrConnector

        return BambooHrConnector(config=row.config)

    raise ValueError(f"No connector configured for domain: {domain}")


# ---------------------------------------------------------------------------
# Spec-driven connector resolution
# ---------------------------------------------------------------------------


def resolve_connector(domain: str, action: str, db: Session) -> tuple:
    """Resolve a connector spec for a domain + action.

    Iterates ALL enabled bindings for this domain so that multiple
    connector specs can coexist (e.g. HR = BambooHR + Deputy).

    Returns (ConnectorSpec, credentials_dict, operation_dict).
    Raises ValueError if no matching spec/action is found.
    """
    from app.db.models import ConnectorSpec, ConnectorConfig, AgentConnectorBinding

    # Get ALL enabled bindings for this domain
    bindings = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == domain,
            AgentConnectorBinding.enabled == True,  # noqa: E712
        )
        .all()
    )

    # For each binding, load the ConnectorSpec and check if it has this action
    for binding in bindings:
        spec = (
            db.query(ConnectorSpec)
            .filter(
                ConnectorSpec.connector_name == binding.connector_name,
                ConnectorSpec.enabled == True,  # noqa: E712
            )
            .first()
        )
        if not spec:
            continue

        for op in spec.tools or []:
            if op.get("action") == action:
                config_row = (
                    db.query(ConnectorConfig)
                    .filter(
                        ConnectorConfig.connector_name == binding.connector_name,
                        ConnectorConfig.enabled == "true",
                    )
                    .first()
                )
                credentials = config_row.config if config_row else {}
                logger.info(
                    "Resolved spec connector: %s (mode=%s, action=%s)",
                    binding.connector_name,
                    spec.execution_mode,
                    action,
                )
                return spec, credentials, op

    raise ValueError(
        f"No connector spec found for domain={domain}, action={action}. "
        f"Check that a connector spec with this action is bound and enabled."
    )
