import logging

from sqlalchemy.orm import Session

from app.connectors.base import BaseConnector

logger = logging.getLogger(__name__)


def get_connector(domain: str, db: Session) -> BaseConnector:
    """Build a BaseConnector instance from DB config.

    Used only for legacy code paths that need a BaseConnector.
    Prefer resolve_connector() for spec-driven resolution.
    """
    from app.db.models import Connection

    # Look up the connector config for this domain
    row = (
        db.query(Connection)
        .filter(
            Connection.connector_name == domain,
            Connection.enabled == "true",
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


def resolve_connector(
    domain: str, action: str, db: Session, config_db: Session | None = None
) -> tuple:
    """Resolve a connector spec for a domain + action.

    Iterates ALL enabled bindings for this domain so that multiple
    connector specs can coexist (e.g. HR = BambooHR + Deputy).

    Returns (ConnectionSpec, credentials_dict, operation_dict).
    Raises ValueError if no matching spec/action is found.

    config_db is used for ConnectionSpec and AgentConnectionBinding queries.
    db is used for Connection (credentials) queries.
    """
    from app.db.models import ConnectionSpec, Connection, AgentConnectionBinding

    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )

    # Get ALL enabled bindings (tools are no longer agent-scoped)
    bindings = (
        _cdb.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.enabled == True,  # noqa: E712
        )
        .all()
    )

    # For each binding, load the ConnectionSpec and check if it has this action
    for binding in bindings:
        spec = (
            _cdb.query(ConnectionSpec)
            .filter(
                ConnectionSpec.connector_name == binding.connector_name,
                ConnectionSpec.enabled == True,  # noqa: E712
            )
            .first()
        )
        if not spec:
            continue

        for op in spec.tools or []:
            if op.get("action") == action:
                config_row = (
                    db.query(Connection)
                    .filter(
                        Connection.connector_name == binding.connector_name,
                        Connection.enabled == "true",
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
