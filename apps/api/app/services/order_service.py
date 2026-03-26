"""Order store backed by Postgres."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Thread, Message, Order, OrderLine, Approval
from app.connectors.registry import resolve_connector
from app.services.integration_service import execute_submission_v2


def create_draft_order(
    db: Session,
    message: str,
    intent: str,
    product: dict | None,
    venue: dict | None,
    quantity: int | None,
    user_id: str | None = None,
    extracted_extra: dict | None = None,
) -> dict:
    missing = _calc_missing(product, venue, quantity)
    fully_resolved = len(missing) == 0
    question = _procurement_question(missing) if missing else None

    extracted = {}
    if product:
        extracted["product"] = product
    if venue:
        extracted["venue"] = venue
    if quantity is not None:
        extracted["quantity"] = quantity
    # Merge in extra fields from dynamic prompt (e.g. _action, _connector)
    if extracted_extra:
        extracted.update(extracted_extra)

    task = Thread(
        user_id=user_id,
        intent=intent,
        domain="procurement",
        status="awaiting_approval" if fully_resolved else "awaiting_user_input",
        raw_prompt=message,
        extracted_fields=extracted,
        missing_fields=missing,
        clarification_question=question,
    )
    db.add(task)
    db.flush()

    # User message
    db.add(Message(thread_id=task.id, role="user", content=message))
    if question:
        db.add(Message(thread_id=task.id, role="assistant", content=question))

    # Order
    order = Order(
        thread_id=task.id,
        venue_id=venue["id"] if venue else None,
        supplier_id=product["supplier_id"]
        if product and product.get("supplier_id")
        else None,
        status=task.status,
    )
    db.add(order)
    db.flush()

    # Order line
    if product and quantity is not None:
        db.add(
            OrderLine(
                order_id=order.id,
                product_id=product["id"],
                quantity_cases=quantity,
            )
        )

    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


def update_order(
    db: Session,
    thread_id: str,
    product: dict | None,
    venue: dict | None,
    quantity: int | None,
) -> dict | None:
    task = db.query(Thread).filter(Thread.id == thread_id).first()
    if not task:
        return None

    extracted = dict(task.extracted_fields or {})
    revisions: list[str] = []

    # Product: overwrite if provided and different
    if product:
        old_product = extracted.get("product")
        if old_product and old_product.get("id") != product["id"]:
            revisions.append(
                f"Product changed from {old_product['name']} to {product['name']}"
            )
        elif not old_product:
            revisions.append(f"Product set to {product['name']}")
        extracted["product"] = product

    # Venue: overwrite if provided and different
    if venue:
        old_venue = extracted.get("venue")
        if old_venue and old_venue.get("id") != venue["id"]:
            revisions.append(
                f"Venue changed from {old_venue['name']} to {venue['name']}"
            )
        elif not old_venue:
            revisions.append(f"Venue set to {venue['name']}")
        extracted["venue"] = venue

    # Quantity: overwrite if provided and different
    if quantity is not None:
        old_qty = extracted.get("quantity")
        if old_qty is not None and old_qty != quantity:
            revisions.append(f"Quantity updated from {old_qty} to {quantity} cases")
        elif old_qty is None:
            revisions.append(f"Quantity set to {quantity} cases")
        extracted["quantity"] = quantity

    task.extracted_fields = extracted

    missing = _calc_missing(
        extracted.get("product"), extracted.get("venue"), extracted.get("quantity")
    )
    task.missing_fields = missing

    # Build assistant message
    if revisions:
        revision_text = ". ".join(revisions) + "."
        if not missing:
            assistant_msg = (
                f"{revision_text} Draft order updated and ready for approval."
            )
        else:
            q = _procurement_question(missing)
            assistant_msg = f"{revision_text} {q}"
    elif not missing:
        assistant_msg = "Got it. Draft order is ready for your approval."
    else:
        assistant_msg = _procurement_question(missing)

    if not missing:
        task.status = "awaiting_approval"
        task.clarification_question = None
    else:
        task.clarification_question = _procurement_question(missing)

    db.add(Message(thread_id=task.id, role="assistant", content=assistant_msg))
    task.updated_at = datetime.now(timezone.utc)

    # Update order record
    order = db.query(Order).filter(Order.thread_id == thread_id).first()
    if order:
        p = extracted.get("product")
        v = extracted.get("venue")
        if v:
            order.venue_id = v["id"]
        if p and p.get("supplier_id"):
            order.supplier_id = p["supplier_id"]
        order.status = task.status

        # Upsert order line
        if p and extracted.get("quantity") is not None:
            existing = (
                db.query(OrderLine).filter(OrderLine.order_id == order.id).first()
            )
            if existing:
                existing.product_id = p["id"]
                existing.quantity_cases = extracted["quantity"]
            else:
                db.add(
                    OrderLine(
                        order_id=order.id,
                        product_id=p["id"],
                        quantity_cases=extracted["quantity"],
                    )
                )

    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


def get_order(db: Session, thread_id: str) -> dict | None:
    task = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "procurement")
        .first()
    )
    if not task:
        return None
    return _task_to_dict(task)


def approve_order(db: Session, thread_id: str, user=None) -> dict | None:
    result = _set_status(db, thread_id, "approved")
    if result:
        db.add(
            Approval(
                thread_id=thread_id,
                action="approved",
                performed_by=user.email if user else "system",
                user_id=user.id if user else None,
            )
        )
        db.commit()
    return result


def reject_order(db: Session, thread_id: str, user=None) -> dict | None:
    result = _set_status(db, thread_id, "rejected")
    if result:
        db.add(
            Approval(
                thread_id=thread_id,
                action="rejected",
                performed_by=user.email if user else "system",
                user_id=user.id if user else None,
            )
        )
        db.commit()
    return result


def submit_order(db: Session, thread_id: str) -> dict | None:
    task = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "procurement")
        .first()
    )
    if not task or task.status != "approved":
        return None

    action = (task.extracted_fields or {}).get("_action", "create_order")
    spec, creds, operation = resolve_connector("procurement", action, db)
    run = execute_submission_v2(db, task, spec, creds, operation)

    if run.status == "success":
        task.status = "submitted"
        order = db.query(Order).filter(Order.thread_id == thread_id).first()
        if order:
            order.status = "submitted"
    else:
        task.status = "approved"  # keep approved so user can retry

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


def list_orders(db: Session, user_id: str | None = None) -> list[dict]:
    q = db.query(Thread).filter(Thread.domain == "procurement")
    if user_id:
        q = q.filter(Thread.user_id == user_id)
    tasks = q.order_by(Thread.created_at.desc()).all()
    return [_task_to_dict(t) for t in tasks]


def find_open_order(db: Session, user_id: str | None = None) -> dict | None:
    q = db.query(Thread).filter(
        Thread.domain == "procurement",
        Thread.status.in_(["awaiting_user_input", "awaiting_approval"]),
    )
    if user_id:
        q = q.filter(Thread.user_id == user_id)
    task = q.order_by(Thread.created_at.desc()).first()
    if not task:
        return None
    return _task_to_dict(task)


def _set_status(db: Session, thread_id: str, status: str) -> dict | None:
    task = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "procurement")
        .first()
    )
    if not task:
        return None
    task.status = status
    task.updated_at = datetime.now(timezone.utc)
    order = db.query(Order).filter(Order.thread_id == thread_id).first()
    if order:
        order.status = status
    db.commit()
    db.refresh(task)
    return _task_to_dict(task)


# -- helpers --


def _calc_missing(product, venue, quantity) -> list[str]:
    m = []
    if not product:
        m.append("product")
    if not venue:
        m.append("venue")
    if quantity is None:
        m.append("quantity")
    return m


def _procurement_question(missing: list[str]) -> str:
    parts = []
    if "product" in missing:
        parts.append("which product")
    if "venue" in missing:
        parts.append("which venue")
    if "quantity" in missing:
        parts.append("how many")
    joined = " and ".join(parts)
    return f"I need a bit more info -- {joined}?"


def _task_to_dict(task: Thread) -> dict:
    extracted = task.extracted_fields or {}
    product = extracted.get("product")
    venue = extracted.get("venue")
    quantity = extracted.get("quantity")

    line_summary = None
    if product and venue:
        qty = quantity or "?"
        supplier = product.get("supplier", "?")
        line_summary = f"{qty} x {product['name']} -> {venue['name']} via {supplier}"

    # Latest integration run
    integration_run = None
    if task.integration_runs:
        latest_run = task.integration_runs[-1]
        integration_run = {
            "connector": latest_run.connector_name,
            "status": latest_run.status,
            "reference": (latest_run.response_payload or {}).get("order_reference"),
            "submitted_at": latest_run.created_at.isoformat()
            if latest_run.created_at
            else None,
            "error": latest_run.error_message,
        }

    # Latest approval
    approval = None
    if task.approvals:
        latest_approval = task.approvals[-1]
        approval = {
            "action": latest_approval.action,
            "performed_by": latest_approval.performed_by or "system",
            "performed_at": latest_approval.performed_at.isoformat()
            if latest_approval.performed_at
            else None,
        }

    return {
        "id": task.id,
        "domain": "procurement",
        "message": task.raw_prompt,
        "intent": task.intent,
        "title": task.title,
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "venue": {"id": venue["id"], "name": venue["name"]} if venue else None,
        "product": {
            "id": product["id"],
            "name": product["name"],
            "unit": product.get("unit", "case"),
            "category": product.get("category"),
        }
        if product
        else None,
        "supplier": product.get("supplier") if product else None,
        "quantity": quantity,
        "line_summary": line_summary,
        "missing_fields": task.missing_fields or [],
        "clarification_question": task.clarification_question,
        "conversation": [
            {
                "role": m.role,
                "text": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in sorted(task.messages, key=lambda x: x.created_at)
        ],
        "integration_run": integration_run,
        "approval": approval,
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
        ]
        if task.tool_calls
        else [],
        "thinking_steps": task.thinking_steps or [],
    }
