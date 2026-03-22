import time

from sqlalchemy.orm import Session

from app.db.models import Task, IntegrationRun


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
