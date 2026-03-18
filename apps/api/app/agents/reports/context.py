"""Build reports-specific context from the database."""

from sqlalchemy.orm import Session

from app.db.models import Task, Venue, Product

AVAILABLE_DATA_SOURCES = ["sales", "inventory"]
METRIC_OPTIONS = ["revenue", "quantity", "cost", "margin", "stock_level"]


def build_reports_context(db: Session, user_id: str | None = None) -> dict:
    """Return context dict for the reports agent."""
    venues = db.query(Venue).all()
    venue_names = [v.name for v in venues]

    products = db.query(Product).all()
    product_candidates = [p.name for p in products]

    ctx: dict = {
        "venue_names": venue_names,
        "product_candidates": product_candidates,
        "available_data_sources": AVAILABLE_DATA_SOURCES,
        "metric_options": METRIC_OPTIONS,
    }

    return ctx


def _report_task_to_dict(task: Task) -> dict:
    """Convert a reports Task to a response dict."""
    extracted = task.extracted_fields or {}

    # Latest approval
    approval = None
    if task.approvals:
        latest_approval = task.approvals[-1]
        approval = {
            "action": latest_approval.action,
            "performed_by": latest_approval.performed_by or "system",
            "performed_at": latest_approval.performed_at.isoformat() if latest_approval.performed_at else None,
        }

    return {
        "id": task.id,
        "domain": "reports",
        "intent": task.intent,
        "title": task.title,
        "message": task.raw_prompt,
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "report_type": extracted.get("report_type"),
        "data_sources": extracted.get("data_sources", []),
        "metrics": extracted.get("metrics", []),
        "time_range": extracted.get("time_range"),
        "venue_name": extracted.get("venue_name"),
        "product_name": extracted.get("product_name"),
        "group_by": extracted.get("group_by"),
        "report_plan": extracted.get("report_plan"),
        "report_result": extracted.get("report_result"),
        "missing_fields": task.missing_fields or [],
        "clarification_question": task.clarification_question,
        "conversation": [
            {"role": m.role, "text": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in sorted(task.messages, key=lambda x: x.created_at)
        ],
        "approval": approval,
        "tool_calls": [
            {
                "id": tc.id,
                "iteration": tc.iteration,
                "tool_name": tc.tool_name,
                "connector_name": tc.connector_name,
                "action": tc.action,
                "method": tc.method,
                "input_params": tc.input_params,
                "status": tc.status,
                "result_payload": tc.result_payload,
                "error_message": tc.error_message,
                "duration_ms": tc.duration_ms,
                "created_at": tc.created_at.isoformat() if tc.created_at else None,
            }
            for tc in sorted(task.tool_calls, key=lambda x: x.created_at)
        ],
        "llm_calls": [
            {
                "id": c.id,
                "call_type": c.call_type,
                "model": c.model,
                "system_prompt": c.system_prompt,
                "user_prompt": c.user_prompt,
                "raw_response": c.raw_response,
                "parsed_response": c.parsed_response,
                "status": c.status,
                "error_message": c.error_message,
                "duration_ms": c.duration_ms,
                "tools_provided": c.tools_provided,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in task.llm_calls
        ],
        "thinking_steps": task.thinking_steps or [],
    }
