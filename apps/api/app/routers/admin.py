"""Admin API — deployment management and (Phase 4) test management."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import Deployment, User

router = APIRouter(tags=["admin"])


# ── Helpers ─────────────────────────────────────────────────────────

def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")


# ── Schemas ─────────────────────────────────────────────────────────

class DeployWebhookPayload(BaseModel):
    environment: str
    image_tag: str
    git_sha: str
    status: str  # pending | running | success | failed
    commit_message: str | None = None
    logs_url: str | None = None
    triggered_by: str | None = None


class PromoteRequest(BaseModel):
    image_tag: str
    target_environment: str = "production"


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/admin/deployments")
def list_deployments(
    environment: str | None = None,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List recent deployments, optionally filtered by environment."""
    _require_admin(user)
    q = db.query(Deployment).order_by(Deployment.started_at.desc())
    if environment:
        q = q.filter(Deployment.environment == environment)
    rows = q.limit(limit).all()
    return {
        "deployments": [
            {
                "id": d.id,
                "environment": d.environment,
                "image_tag": d.image_tag,
                "git_sha": d.git_sha,
                "commit_message": d.commit_message,
                "status": d.status,
                "started_at": d.started_at.isoformat() if d.started_at else None,
                "completed_at": d.completed_at.isoformat() if d.completed_at else None,
                "logs_url": d.logs_url,
                "triggered_by": d.triggered_by,
            }
            for d in rows
        ]
    }


@router.get("/admin/environments")
def list_environments(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all environments with their latest deployment status."""
    _require_admin(user)
    envs = []
    for env_name in ("testing", "staging", "production"):
        latest = (
            db.query(Deployment)
            .filter(Deployment.environment == env_name)
            .order_by(Deployment.started_at.desc())
            .first()
        )
        envs.append({
            "name": env_name,
            "latest_deploy": {
                "image_tag": latest.image_tag,
                "git_sha": latest.git_sha,
                "status": latest.status,
                "started_at": latest.started_at.isoformat() if latest.started_at else None,
                "commit_message": latest.commit_message,
            } if latest else None,
        })
    return {"environments": envs}


@router.post("/admin/deploy-webhook")
def deploy_webhook(
    payload: DeployWebhookPayload,
    db: Session = Depends(get_db),
):
    """Receive deploy status from GitHub Actions.

    This endpoint is called by the CD pipeline after each deployment.
    No auth required — will be secured via webhook secret in Phase 3.
    """
    # Check if we already have a deployment record for this sha+env
    existing = (
        db.query(Deployment)
        .filter(
            Deployment.git_sha == payload.git_sha,
            Deployment.environment == payload.environment,
        )
        .first()
    )

    if existing:
        existing.status = payload.status
        existing.logs_url = payload.logs_url
        if payload.status in ("success", "failed"):
            existing.completed_at = datetime.now(timezone.utc)
    else:
        dep = Deployment(
            environment=payload.environment,
            image_tag=payload.image_tag,
            git_sha=payload.git_sha,
            commit_message=payload.commit_message,
            status=payload.status,
            logs_url=payload.logs_url,
            triggered_by=payload.triggered_by or "ci",
        )
        db.add(dep)

    db.commit()
    return {"ok": True}


@router.post("/admin/promote")
def promote(
    body: PromoteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger a production deployment via GitHub Actions workflow_dispatch.

    TODO (Phase 3): Integrate with GitHub API to trigger the deploy workflow.
    """
    _require_admin(user)
    # For now, create a pending deployment record
    dep = Deployment(
        environment=body.target_environment,
        image_tag=body.image_tag,
        git_sha=body.image_tag,  # SHA is the image tag in our convention
        status="pending",
        triggered_by=user.email,
    )
    db.add(dep)
    db.commit()

    # TODO (Phase 3): Call GitHub API
    # import httpx
    # httpx.post(
    #     f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/deploy.yml/dispatches",
    #     headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
    #     json={"ref": "main", "inputs": {"environment": body.target_environment, "image_tag": body.image_tag}},
    # )

    return {"ok": True, "deployment_id": dep.id}
