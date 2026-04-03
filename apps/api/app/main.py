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

from app.routers import (  # noqa: E402
    health,
    venues,
    messages,
    orders,
    threads,
    connectors,
    connector_specs,
    auth,
    agents,
    oauth,
    working_documents,
    automated_tasks,
    organizations,
    billing,
    billing_webhooks,
    reports_crud,
    admin,
    roles,
    email,
    component_apis,
    playbooks,
)

app = FastAPI(
    title="Norm API",
    description="Hospitality AI Orchestration Platform",
    version="0.1.0",
)

from app.middleware.request_tracing import RequestTracingMiddleware  # noqa: E402
from app.middleware.metrics import MetricsMiddleware  # noqa: E402
from app.middleware.rate_limit import limiter  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
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
app.include_router(threads.router, prefix="/api")
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
app.include_router(roles.router, prefix="/api")
app.include_router(email.router, prefix="/api")
app.include_router(component_apis.router, prefix="/api")
app.include_router(playbooks.router, prefix="/api")


@app.on_event("startup")
def _load_system_secrets() -> None:
    """Load system secrets from config DB and override settings attributes."""
    import logging

    log = logging.getLogger(__name__)

    LOADABLE_SECRETS = [
        "ANTHROPIC_API_KEY",
        "JWT_SECRET",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "RESEND_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
    ]

    try:
        from app.db.engine import _ConfigSessionLocal, SessionLocal
        from app.db.models import SystemSecret

        factory = _ConfigSessionLocal or SessionLocal
        db = factory()
        try:
            secrets = db.query(SystemSecret).all()
            loaded = 0
            for secret in secrets:
                if secret.key in LOADABLE_SECRETS and secret.value:
                    setattr(settings, secret.key, secret.value)
                    loaded += 1
            if loaded:
                log.info("Loaded %d system secrets from config DB", loaded)
        finally:
            db.close()
    except Exception:
        log.warning(
            "Could not load system secrets from config DB — falling back to env vars"
        )


@app.on_event("startup")
def _start_scheduler() -> None:
    from app.services.task_scheduler import init_scheduler

    init_scheduler()


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    from app.services.task_scheduler import scheduler

    scheduler.shutdown(wait=False)


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
            existing = (
                db.query(Subscription)
                .filter(
                    Subscription.organization_id == org.id,
                )
                .first()
            )
            if not existing:
                db.add(
                    Subscription(
                        organization_id=org.id,
                        token_plan="basic",
                        token_quota=PLAN_QUOTAS["basic"]["tokens"],
                        status="trialing",
                    )
                )
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
