import time

from sqlalchemy.orm import Session

from app.connectors.base import BaseConnector
from app.db.models import Task, IntegrationRun


def execute_submission(db: Session, task: Task, connector: BaseConnector) -> IntegrationRun:
    """Call a legacy connector and record the integration run."""
    payload = _build_payload(task)

    run = IntegrationRun(
        task_id=task.id,
        connector_name=connector.name,
        request_payload=payload,
        status="pending",
        execution_mode="legacy",
    )
    db.add(run)
    db.flush()

    start = time.monotonic()
    try:
        result = connector.submit(payload)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        run.response_payload = result.response_payload
        run.duration_ms = elapsed_ms

        if result.success:
            run.status = "success"
        else:
            run.status = "failed"
            run.error_message = result.error_message
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        run.status = "failed"
        run.error_message = str(exc)
        run.duration_ms = elapsed_ms

    db.flush()
    return run


def execute_submission_v2(db: Session, task: Task, spec, credentials: dict, operation: dict) -> IntegrationRun:
    """Execute a spec-driven connector and record the integration run."""
    from app.connectors.spec_executor import execute_spec

    run = IntegrationRun(
        task_id=task.id,
        connector_name=spec.connector_name,
        request_payload=task.extracted_fields,
        status="pending",
        execution_mode=spec.execution_mode,
        spec_version=spec.version,
    )
    db.add(run)
    db.flush()

    start = time.monotonic()
    try:
        result, rendered = execute_spec(
            spec, operation, task.extracted_fields or {}, credentials, db, task.id,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        run.rendered_request = rendered.to_audit_dict()
        run.response_payload = result.response_payload
        run.duration_ms = elapsed_ms

        if result.success:
            run.status = "success"
        else:
            run.status = "failed"
            run.error_message = result.error_message
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        run.status = "failed"
        run.error_message = str(exc)
        run.duration_ms = elapsed_ms

    db.flush()
    return run


def _build_payload(task: Task) -> dict:
    """Build a submission payload from task data."""
    extracted = task.extracted_fields or {}

    if task.domain == "procurement":
        product = extracted.get("product", {})
        venue = extracted.get("venue", {})
        return {
            "supplier": product.get("supplier", "Unknown"),
            "venue": venue.get("name", "Unknown"),
            "items": [
                {
                    "product": product.get("name", "Unknown"),
                    "quantity": extracted.get("quantity"),
                    "unit": product.get("unit", "case"),
                }
            ],
        }

    if task.domain == "hr":
        venue = extracted.get("venue", {})
        return {
            "employee_name": extracted.get("employee_name", "Unknown"),
            "venue": venue.get("name", "Unknown"),
            "role": extracted.get("role", "Unknown"),
            "start_date": extracted.get("start_date"),
            "email": extracted.get("email"),
            "phone": extracted.get("phone"),
            "employment_type": extracted.get("employment_type"),
        }

    return {"task_id": task.id, "domain": task.domain}
