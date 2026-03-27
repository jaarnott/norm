"""Admin API — deployment management, secrets, and (Phase 4) test management."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_db, get_config_db_rw
from app.db.models import (
    Deployment,
    E2ETest,
    E2ETestRun,
    SystemSecret,
    User,
)

router = APIRouter(tags=["admin"])
log = logging.getLogger(__name__)


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
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """List recent deployments, optionally filtered by environment."""
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
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """List all environments with their latest deployment status."""
    envs = []
    for env_name in ("testing", "staging", "production"):
        latest = (
            db.query(Deployment)
            .filter(Deployment.environment == env_name)
            .order_by(Deployment.started_at.desc())
            .first()
        )
        envs.append(
            {
                "name": env_name,
                "latest_deploy": {
                    "image_tag": latest.image_tag,
                    "git_sha": latest.git_sha,
                    "status": latest.status,
                    "started_at": latest.started_at.isoformat()
                    if latest.started_at
                    else None,
                    "commit_message": latest.commit_message,
                }
                if latest
                else None,
            }
        )
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
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """Trigger a production deployment via GitHub Actions workflow_dispatch.

    TODO (Phase 3): Integrate with GitHub API to trigger the deploy workflow.
    """
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


@router.post("/admin/rollback")
def rollback(
    body: PromoteRequest,
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """Roll back an environment to a previous known-good deployment.

    Finds the last successful deployment for the target environment
    and re-deploys that image tag.
    """

    # Find the last successful deploy for this environment (excluding the current one)
    last_good = (
        db.query(Deployment)
        .filter(
            Deployment.environment == body.target_environment,
            Deployment.status == "success",
            Deployment.image_tag != body.image_tag,
        )
        .order_by(Deployment.started_at.desc())
        .first()
    )

    if not last_good:
        raise HTTPException(
            404, "No previous successful deployment found to roll back to"
        )

    # Create a pending rollback deployment
    dep = Deployment(
        environment=body.target_environment,
        image_tag=last_good.image_tag,
        git_sha=last_good.git_sha,
        commit_message=f"Rollback to {last_good.git_sha[:7]} (triggered by {user.email})",
        status="pending",
        triggered_by=user.email,
    )
    db.add(dep)
    db.commit()

    return {
        "ok": True,
        "deployment_id": dep.id,
        "rolling_back_to": {
            "image_tag": last_good.image_tag,
            "git_sha": last_good.git_sha,
            "deployed_at": last_good.started_at.isoformat()
            if last_good.started_at
            else None,
        },
    }


# ── Secrets CRUD ──────────────────────────────────────────────────


class SecretUpdateBody(BaseModel):
    value: str
    description: str | None = None


@router.get("/admin/secrets")
def list_secrets(
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """List all system secrets with masked values."""
    rows = config_db.query(SystemSecret).order_by(SystemSecret.key).all()
    return {
        "secrets": [
            {
                "key": s.key,
                "value": s.value[:4] + "***"
                if s.value and len(s.value) >= 4
                else "***",
                "description": s.description,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in rows
        ]
    }


@router.put("/admin/secrets/{key}")
def upsert_secret(
    key: str,
    body: SecretUpdateBody,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Create or update a system secret."""
    row = config_db.query(SystemSecret).filter(SystemSecret.key == key).first()
    if row:
        row.value = body.value
        if body.description is not None:
            row.description = body.description
    else:
        row = SystemSecret(key=key, value=body.value, description=body.description)
        config_db.add(row)
    config_db.commit()
    config_db.refresh(row)
    return {
        "key": row.key,
        "value": row.value[:4] + "***" if row.value and len(row.value) >= 4 else "***",
        "description": row.description,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.delete("/admin/secrets/{key}")
def delete_secret(
    key: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Delete a system secret."""
    row = config_db.query(SystemSecret).filter(SystemSecret.key == key).first()
    if not row:
        raise HTTPException(404, f"Secret not found: {key}")
    config_db.delete(row)
    config_db.commit()
    return {"deleted": True}


# ── E2E Test Schemas ──────────────────────────────────────────────


class GenerateTestRequest(BaseModel):
    description: str


class SaveTestRequest(BaseModel):
    name: str
    description: str
    playwright_script: str
    steps: list = []


class UpdateTestRequest(BaseModel):
    name: str | None = None
    playwright_script: str | None = None
    steps: list | None = None


class RunTestsRequest(BaseModel):
    environment: str = "testing"
    test_ids: list[str] | None = None  # None means all


class TestRunWebhookPayload(BaseModel):
    test_id: str | None = None
    environment: str
    status: str
    duration_ms: int | None = None
    error_message: str | None = None
    screenshots: list = []
    video_url: str | None = None
    git_sha: str | None = None


# ── E2E Test Endpoints ────────────────────────────────────────────


@router.post("/admin/tests/generate")
async def generate_test(
    body: GenerateTestRequest,
    user: User = Depends(require_permission("admin:tests")),
):
    """Generate a Playwright test from a natural language description."""
    from app.services.test_generator import generate_test as _generate

    result = await _generate(body.description)
    return result


@router.post("/admin/tests")
def save_test(
    body: SaveTestRequest,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Save a generated test to the suite."""
    test = E2ETest(
        name=body.name,
        description=body.description,
        playwright_script=body.playwright_script,
        steps_json=body.steps,
        created_by=user.id,
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    return {
        "id": test.id,
        "name": test.name,
        "description": test.description,
        "playwright_script": test.playwright_script,
        "steps": test.steps_json,
        "created_at": test.created_at.isoformat() if test.created_at else None,
    }


@router.get("/admin/tests")
def list_tests(
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """List all E2E tests with last run status."""
    rows = db.query(E2ETest).order_by(E2ETest.created_at.desc()).all()
    return {
        "tests": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "playwright_script": t.playwright_script,
                "steps": t.steps_json,
                "last_run_status": t.last_run_status,
                "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in rows
        ]
    }


@router.get("/admin/tests/{test_id}")
def get_test(
    test_id: str,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Get a single E2E test detail."""
    test = db.query(E2ETest).filter(E2ETest.id == test_id).first()
    if not test:
        raise HTTPException(404, "Test not found")
    return {
        "id": test.id,
        "name": test.name,
        "description": test.description,
        "playwright_script": test.playwright_script,
        "steps": test.steps_json,
        "last_run_status": test.last_run_status,
        "last_run_at": test.last_run_at.isoformat() if test.last_run_at else None,
        "created_at": test.created_at.isoformat() if test.created_at else None,
        "updated_at": test.updated_at.isoformat() if test.updated_at else None,
    }


@router.put("/admin/tests/{test_id}")
def update_test(
    test_id: str,
    body: UpdateTestRequest,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Update an existing E2E test."""
    test = db.query(E2ETest).filter(E2ETest.id == test_id).first()
    if not test:
        raise HTTPException(404, "Test not found")
    if body.name is not None:
        test.name = body.name
    if body.playwright_script is not None:
        test.playwright_script = body.playwright_script
    if body.steps is not None:
        test.steps_json = body.steps
    db.commit()
    db.refresh(test)
    return {
        "id": test.id,
        "name": test.name,
        "description": test.description,
        "playwright_script": test.playwright_script,
        "steps": test.steps_json,
        "updated_at": test.updated_at.isoformat() if test.updated_at else None,
    }


@router.delete("/admin/tests/{test_id}")
def delete_test(
    test_id: str,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Delete an E2E test."""
    test = db.query(E2ETest).filter(E2ETest.id == test_id).first()
    if not test:
        raise HTTPException(404, "Test not found")
    db.delete(test)
    db.commit()
    return {"ok": True}


@router.post("/admin/tests/run")
def run_tests(
    body: RunTestsRequest,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Create pending test run records. Actual execution happens externally (CI/CD)."""
    if body.test_ids:
        tests = db.query(E2ETest).filter(E2ETest.id.in_(body.test_ids)).all()
    else:
        tests = db.query(E2ETest).all()

    if not tests:
        raise HTTPException(404, "No tests found")

    runs = []
    for t in tests:
        run = E2ETestRun(
            test_id=t.id,
            environment=body.environment,
            status="pending",
            triggered_by="manual",
        )
        db.add(run)
        runs.append(run)

    db.commit()
    return {
        "ok": True,
        "runs": [{"id": r.id, "test_id": r.test_id, "status": r.status} for r in runs],
    }


@router.get("/admin/test-runs")
def list_test_runs(
    environment: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """List test runs, optionally filtered by environment."""
    q = db.query(E2ETestRun).order_by(E2ETestRun.started_at.desc())
    if environment:
        q = q.filter(E2ETestRun.environment == environment)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {
        "total": total,
        "runs": [
            {
                "id": r.id,
                "test_id": r.test_id,
                "environment": r.environment,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "duration_ms": r.duration_ms,
                "error_message": r.error_message,
                "screenshots": r.screenshots_json,
                "video_url": r.video_url,
                "triggered_by": r.triggered_by,
                "git_sha": r.git_sha,
            }
            for r in rows
        ],
    }


@router.get("/admin/test-runs/{run_id}")
def get_test_run(
    run_id: str,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Get a single test run detail."""
    run = db.query(E2ETestRun).filter(E2ETestRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Test run not found")
    return {
        "id": run.id,
        "test_id": run.test_id,
        "environment": run.environment,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "error_message": run.error_message,
        "screenshots": run.screenshots_json,
        "video_url": run.video_url,
        "triggered_by": run.triggered_by,
        "git_sha": run.git_sha,
    }


@router.post("/admin/test-runs/webhook")
def test_run_webhook(
    payload: TestRunWebhookPayload,
    db: Session = Depends(get_db),
):
    """Receive test run results from CI/CD.

    No auth required — will be secured via webhook secret.
    """
    # Find pending run for this test+environment, or create one
    q = db.query(E2ETestRun).filter(
        E2ETestRun.environment == payload.environment,
        E2ETestRun.status.in_(["pending", "running"]),
    )
    if payload.test_id:
        q = q.filter(E2ETestRun.test_id == payload.test_id)
    run = q.order_by(E2ETestRun.started_at.desc()).first()

    if run:
        run.status = payload.status
        run.duration_ms = payload.duration_ms
        run.error_message = payload.error_message
        run.screenshots_json = payload.screenshots
        run.video_url = payload.video_url
        run.git_sha = payload.git_sha
        if payload.status in ("passed", "failed", "error"):
            run.completed_at = datetime.now(timezone.utc)
    else:
        run = E2ETestRun(
            test_id=payload.test_id,
            environment=payload.environment,
            status=payload.status,
            duration_ms=payload.duration_ms,
            error_message=payload.error_message,
            screenshots_json=payload.screenshots,
            video_url=payload.video_url,
            git_sha=payload.git_sha,
            triggered_by="ci",
        )
        if payload.status in ("passed", "failed", "error"):
            run.completed_at = datetime.now(timezone.utc)
        db.add(run)

    # Update the test's last_run fields
    if payload.test_id:
        test = db.query(E2ETest).filter(E2ETest.id == payload.test_id).first()
        if test:
            test.last_run_status = payload.status
            test.last_run_at = datetime.now(timezone.utc)

    db.commit()
    return {"ok": True}
