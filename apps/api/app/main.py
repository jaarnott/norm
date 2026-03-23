
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import setup_logging

# ── Observability init (before app creation) ─────────────────────────
setup_logging()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=0.1 if settings.ENVIRONMENT == "production" else 0.5,
    )

from app.routers import health, venues, messages, orders, tasks, connectors, connector_specs, auth, agents, oauth, working_documents, automated_tasks, organizations, billing, billing_webhooks, reports_crud, admin  # noqa: E402

app = FastAPI(
    title="Norm API",
    description="Hospitality AI Orchestration Platform",
    version="0.1.0",
)

from app.middleware.request_tracing import RequestTracingMiddleware  # noqa: E402
from app.middleware.metrics import MetricsMiddleware  # noqa: E402

app.add_middleware(RequestTracingMiddleware)
app.add_middleware(MetricsMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
app.include_router(venues.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(connectors.router, prefix="/api")
app.include_router(connector_specs.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(oauth.router, prefix="/api")
app.include_router(working_documents.router, prefix="/api")
app.include_router(automated_tasks.router, prefix="/api")
app.include_router(organizations.router, prefix="/api")
app.include_router(billing.router, prefix="/api")
app.include_router(billing_webhooks.router, prefix="/api")
app.include_router(reports_crud.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.on_event("startup")
def _start_scheduler() -> None:
    from app.services.task_scheduler import init_scheduler
    init_scheduler()


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    from app.services.task_scheduler import scheduler
    scheduler.shutdown(wait=False)


@app.on_event("startup")
def _ensure_norm_search_tool() -> None:
    """Ensure the norm ConnectorSpec includes the search_tool_result tool.

    Also ensures existing norm bindings get the capability enabled by default
    for backward compatibility (the tool was previously always-on).
    """
    import logging
    from sqlalchemy.orm.attributes import flag_modified
    from app.db.engine import SessionLocal
    from app.db.models import ConnectorSpec, AgentConnectorBinding

    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        spec = db.query(ConnectorSpec).filter(
            ConnectorSpec.connector_name == "norm",
        ).first()
        if not spec:
            log.info("norm ConnectorSpec not found — skipping search tool setup")
            return

        # Check if the tool already exists in the spec
        existing_actions = {t.get("action") for t in (spec.tools or [])}
        if "search_tool_result" not in existing_actions:
            tools = list(spec.tools or [])
            tools.append({
                "action": "search_tool_result",
                "method": "GET",
                "description": "Search through a previous tool call's full result by keyword. Use when a result was too large or slimmed and you need to find specific items.",
                "required_fields": ["tool_call_id", "query"],
                "optional_fields": ["fields"],
                "field_descriptions": {
                    "tool_call_id": "The _tool_call_id from the slimmed/large result",
                    "query": "Search keyword (case-insensitive match across all field values)",
                    "fields": "Optional: comma-separated field names to return. Omit for all fields.",
                },
            })
            spec.tools = tools
            flag_modified(spec, "tools")
            log.info("Added search_tool_result to norm ConnectorSpec")

        # Ensure existing norm bindings include this capability (enabled by default)
        bindings = db.query(AgentConnectorBinding).filter(
            AgentConnectorBinding.connector_name == "norm",
        ).all()
        for binding in bindings:
            caps = list(binding.capabilities or [])
            cap_actions = {c["action"] for c in caps}
            if "search_tool_result" not in cap_actions:
                caps.append({
                    "action": "search_tool_result",
                    "label": "Search through a previous tool call's full result by keyword. Use when a result was too large or slimmed and you need to find specific items.",
                    "enabled": True,
                })
                binding.capabilities = caps
                flag_modified(binding, "capabilities")
                log.info("Added search_tool_result capability to norm binding for %s", binding.agent_slug)

        db.commit()
    except Exception:
        db.rollback()
        log.exception("Failed to ensure norm search tool")
    finally:
        db.close()


@app.on_event("startup")
def _ensure_norm_reports_spec() -> None:
    """Ensure the norm_reports ConnectorSpec exists with the render_chart tool."""
    import logging
    from sqlalchemy.orm.attributes import flag_modified
    from app.db.engine import SessionLocal
    from app.db.models import ConnectorSpec

    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        spec = db.query(ConnectorSpec).filter(
            ConnectorSpec.connector_name == "norm_reports",
        ).first()
        if not spec:
            spec = ConnectorSpec(
                connector_name="norm_reports",
                display_name="Norm Reports",
                category="reports",
                execution_mode="internal",
                auth_type="none",
                tools=[{
                    "action": "render_chart",
                    "method": "GET",
                    "description": "Render data as a visual chart. Use after fetching data to present it visually. Pass the data rows, chart type, axes, and series configuration.",
                    "required_fields": ["title", "chart_type", "x_axis_key", "series", "source_tool_call_id"],
                    "optional_fields": ["x_axis_label", "orientation", "select_fields", "field_labels"],
                    "field_descriptions": {
                        "title": "Chart title (e.g., Daily Sales - La Zeppa)",
                        "chart_type": "bar, line, pie, stacked_bar, scatter, bubble, or table",
                        "source_tool_call_id": "The tool_use ID of the GET tool call whose data to visualize. The chart pulls data from this tool call's stored result.",
                        "x_axis_key": "Field name from the data for x-axis (e.g., startTime)",
                        "x_axis_label": "Display label for x-axis (e.g., Date)",
                        "series": "Array of {key, label} objects for data series to plot",
                        "orientation": "vertical or horizontal (default: vertical)",
                        "select_fields": "Array of field names to include from the raw data. Omit to include all fields. (e.g., [\"startTime\", \"invoices\"])",
                        "field_labels": "Object mapping raw field names to readable display labels. (e.g., {\"startTime\": \"Date\", \"invoices\": \"Sales ($)\"})",
                    },
                    "field_schema": {
                        "series": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"key": {"type": "string"}, "label": {"type": "string"}}},
                            "description": "Data series to plot. Each has a key (field name) and label (display name).",
                        },
                        "select_fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of field names to include from the raw data.",
                        },
                        "field_labels": {
                            "type": "object",
                            "description": "Object mapping raw field names to readable display labels.",
                        },
                    },
                    "display_component": "chart",
                }],
            )
            db.add(spec)
            log.info("Created norm_reports ConnectorSpec with render_chart tool")
        else:
            # Update render_chart tool to latest definition
            updated_tool = {
                "action": "render_chart",
                "method": "GET",
                "description": "Render data as a visual chart by referencing a prior tool call.",
                "required_fields": ["title", "chart_type", "x_axis_key", "series", "source_tool_call_id"],
                "optional_fields": ["x_axis_label", "orientation", "select_fields", "field_labels"],
                "field_descriptions": {
                    "source_tool_call_id": "The tool_use ID of the GET tool call whose data to visualize.",
                    "title": "Chart title",
                    "chart_type": "bar, line, pie, stacked_bar, scatter, bubble, or table",
                    "x_axis_key": "Field name from the data for x-axis",
                    "series": "Array of {key, label} objects for data series to plot",
                    "select_fields": "Array of field names to include from the raw data",
                    "field_labels": "Object mapping raw field names to display labels",
                },
                "field_schema": {
                    "series": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"key": {"type": "string"}, "label": {"type": "string"}}},
                    },
                    "select_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "field_labels": {
                        "type": "object",
                    },
                },
                "display_component": "chart",
            }
            tools = [t for t in (spec.tools or []) if t.get("action") != "render_chart"]
            tools.append(updated_tool)
            spec.tools = tools
            flag_modified(spec, "tools")
            log.info("Updated render_chart in norm_reports ConnectorSpec")

        # Ensure the reports agent has a binding to norm_reports
        from app.db.models import AgentConnectorBinding
        binding = db.query(AgentConnectorBinding).filter(
            AgentConnectorBinding.agent_slug == "reports",
            AgentConnectorBinding.connector_name == "norm_reports",
        ).first()
        if not binding:
            binding = AgentConnectorBinding(
                agent_slug="reports",
                connector_name="norm_reports",
                enabled=True,
                capabilities=[{
                    "action": "render_chart",
                    "label": "Render data as a visual chart",
                    "enabled": True,
                }],
            )
            db.add(binding)
            log.info("Bound norm_reports to reports agent with render_chart enabled")

        db.commit()
    except Exception:
        db.rollback()
        log.exception("Failed to ensure norm_reports spec")
    finally:
        db.close()


@app.on_event("startup")
def _ensure_org_subscriptions() -> None:
    """Create trial Subscription rows for any org that doesn't have one."""
    import logging
    from app.db.engine import SessionLocal
    from app.db.models import Organization, Subscription

    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        from app.services.billing_service import PLAN_QUOTAS
        orgs = db.query(Organization).all()
        for org in orgs:
            existing = db.query(Subscription).filter(
                Subscription.organization_id == org.id,
            ).first()
            if not existing:
                db.add(Subscription(
                    organization_id=org.id,
                    token_plan="basic",
                    token_quota=PLAN_QUOTAS["basic"]["tokens"],
                    status="trialing",
                ))
                log.info("Created trial subscription for org %s", org.slug)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Failed to ensure org subscriptions")
    finally:
        db.close()


@app.on_event("startup")
def _validate_config() -> None:
    import logging
    log = logging.getLogger(__name__)

    # Fail fast if required secrets are missing in deployed environments
    settings.validate_for_deploy()

    if not settings.ANTHROPIC_API_KEY:
        log.warning(
            "ANTHROPIC_API_KEY not set in environment. "
            "The key can be configured at runtime via PUT /api/connectors/anthropic."
        )
