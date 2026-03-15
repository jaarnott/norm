import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health, venues, messages, orders, tasks, connectors, connector_specs, auth, agents, oauth

app = FastAPI(
    title="Norm API",
    description="Hospitality AI Orchestration Platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.on_event("startup")
def _validate_api_key() -> None:
    import logging
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logging.getLogger(__name__).warning(
            "ANTHROPIC_API_KEY not set in environment. "
            "The key can be configured at runtime via PUT /api/connectors/anthropic."
        )
