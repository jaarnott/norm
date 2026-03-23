import os
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.engine import get_db

router = APIRouter()


@router.get("/health")
async def health_check():
    """Lightweight liveness probe."""
    return {"status": "ok", "service": "norm-api"}


@router.get("/health/ready")
def readiness_check(db: Session = Depends(get_db)):
    """Deep readiness check — verifies DB and critical config."""
    checks = {}

    # Database connectivity
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}

    # Anthropic API key present
    checks["anthropic_api_key"] = {
        "status": "ok" if settings.ANTHROPIC_API_KEY else "missing"
    }

    overall = "ok" if all(c.get("status") == "ok" for c in checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 503

    return JSONResponse(
        {
            "status": overall,
            "environment": settings.ENVIRONMENT,
            "checks": checks,
        },
        status_code=status_code,
    )
