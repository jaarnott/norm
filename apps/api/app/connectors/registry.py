import logging
import os

from sqlalchemy.orm import Session

from app.connectors.base import BaseConnector
from app.connectors.mock_supplier import MockSupplierConnector
from app.connectors.mock_hr import MockHrConnector

logger = logging.getLogger(__name__)

_DOMAIN_CONNECTOR_MAP: dict[str, str] = {
    "procurement": "procurement",
    "hr": "bamboohr",
}


def _build_from_db(connector_name: str, db: Session) -> BaseConnector | None:
    from app.db.models import ConnectorConfig

    row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == connector_name,
        ConnectorConfig.enabled == "true",
    ).first()
    if not row:
        return None

    if connector_name == "bamboohr":
        from app.connectors.bamboohr import BambooHrConnector
        return BambooHrConnector(config=row.config)

    return None


def _build_from_env(domain: str) -> BaseConnector | None:
    if domain == "hr" and os.environ.get("BAMBOOHR_SUBDOMAIN") and os.environ.get("BAMBOOHR_API_KEY"):
        from app.connectors.bamboohr import BambooHrConnector
        return BambooHrConnector()
    return None


_MOCK_CONNECTORS: dict[str, BaseConnector] = {
    "procurement": MockSupplierConnector(),
    "hr": MockHrConnector(),
}


def get_connector(domain: str, db: Session | None = None) -> BaseConnector:
    # 1. Try DB config
    if db is not None:
        connector_name = _DOMAIN_CONNECTOR_MAP.get(domain)
        if connector_name:
            c = _build_from_db(connector_name, db)
            if c:
                return c

    # 2. Try env vars
    c = _build_from_env(domain)
    if c:
        return c

    # 3. Fall back to mock
    mock = _MOCK_CONNECTORS.get(domain)
    if mock:
        return mock

    raise ValueError(f"No connector registered for domain: {domain}")


# ---------------------------------------------------------------------------
# Spec-driven connector resolution
# ---------------------------------------------------------------------------

def resolve_connector(domain: str, action: str, db: Session) -> tuple | BaseConnector:
    """Resolve a connector spec for a domain + action.

    Iterates ALL enabled bindings for this domain so that multiple
    connector specs can coexist (e.g. HR = BambooHR + Deputy).

    Returns either:
    - (ConnectorSpec, credentials_dict, operation_dict) if a spec is found
    - BaseConnector instance as a legacy fallback
    """
    from app.db.models import ConnectorSpec, ConnectorConfig, AgentConnectorBinding

    # 1. Get ALL enabled bindings for this domain
    bindings = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == domain,
            AgentConnectorBinding.enabled == True,  # noqa: E712
        )
        .all()
    )

    # 2. For each binding, load the ConnectorSpec and check if it has this action
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

        for op in spec.operations or []:
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

    # 3. Fallback: try _DOMAIN_CONNECTOR_MAP for spec lookup
    fallback_name = _DOMAIN_CONNECTOR_MAP.get(domain)
    if fallback_name:
        spec = (
            db.query(ConnectorSpec)
            .filter(
                ConnectorSpec.connector_name == fallback_name,
                ConnectorSpec.enabled == True,  # noqa: E712
            )
            .first()
        )
        if spec:
            config_row = (
                db.query(ConnectorConfig)
                .filter(
                    ConnectorConfig.connector_name == fallback_name,
                    ConnectorConfig.enabled == "true",
                )
                .first()
            )
            credentials = config_row.config if config_row else {}

            # Find matching operation or use first
            operation = None
            for op in spec.operations or []:
                if op.get("action") == action:
                    operation = op
                    break
            if operation is None and spec.operations:
                operation = spec.operations[0]

            if operation is not None:
                logger.info(
                    "Resolved spec connector via fallback map: %s (mode=%s, action=%s)",
                    fallback_name,
                    spec.execution_mode,
                    action,
                )
                return spec, credentials, operation

    # 4. Fallback to legacy BaseConnector
    return get_connector(domain, db)
