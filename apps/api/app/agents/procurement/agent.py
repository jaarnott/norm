"""Procurement domain agent."""

import logging

from sqlalchemy.orm import Session

from app.agents.base import BaseDomainAgent
from app.agents.procurement.context import build_procurement_context
from app.db.models import Thread, Message, LlmCall
from app.services.product_resolver import resolve_product
from app.services.venue_resolver import resolve_venue
from app.services.order_service import create_draft_order, update_order

logger = logging.getLogger(__name__)


class ProcurementAgent(BaseDomainAgent):
    @property
    def domain(self) -> str:
        return "procurement"

    def build_context(self, db: Session, user_id: str | None = None) -> dict:
        return build_procurement_context(db, user_id)

    def handle_message(
        self,
        message: str,
        db: Session,
        user_id: str | None = None,
        thread_id: str | None = None,
        venue_id: str | None = None,
        venue_name: str | None = None,
        venue_timezone: str | None = None,
        config_db: Session | None = None,
    ) -> dict:
        # Try the agentic tool loop first (if tools are bound)
        system_prompt, anthropic_tools = self.get_tool_definitions(
            db,
            active_venue_name=venue_name,
            venue_timezone=venue_timezone,
            user_id=user_id,
        )
        if anthropic_tools:
            return self.handle_message_with_tools(
                message,
                db,
                user_id,
                thread_id,
                venue_id=venue_id,
                venue_name=venue_name,
                venue_timezone=venue_timezone,
            )

        # Classic single-shot interpretation (no tools bound)
        ctx = self.build_context(db, user_id)

        # If thread_id provided, load it as open task for follow-up
        if thread_id:
            from app.services.order_service import _thread_to_dict as order_to_dict

            task = db.query(Thread).filter(Thread.id == thread_id).first()
            if task and task.domain == "procurement":
                ctx["open_task"] = order_to_dict(task)

        # Interpret — pass thread_id if this is a follow-up
        parsed, llm_call_id = self.interpret(message, ctx, db=db, thread_id=thread_id)

        is_followup = parsed.get("is_followup", False)
        extracted = parsed.get("extracted_fields", {})
        candidates = parsed.get("candidate_matches", {})
        clarification_question = parsed.get("clarification_question")

        # Capture action + connector from dynamic prompt response
        action = parsed.get("action")
        connector = parsed.get("connector")
        if action:
            extracted["_action"] = action
        if connector:
            extracted["_connector"] = connector

        # Promote candidate matches
        if not extracted.get("venue_name") and candidates.get("venue_candidate"):
            extracted["venue_name"] = candidates["venue_candidate"]
        if not extracted.get("product_name") and candidates.get("product_candidate"):
            extracted["product_name"] = candidates["product_candidate"]

        # Force follow-up if thread_id was provided
        if thread_id and ctx.get("open_task"):
            is_followup = True

        # Follow-up path
        if is_followup and ctx.get("open_task"):
            return self.handle_followup(message, extracted, ctx["open_task"], db)

        # Derive intent from action if provided by dynamic prompt
        intent = parsed.get("intent", "procurement.order")

        # New task path
        result = self._create(
            message, extracted, clarification_question, db, user_id, intent=intent
        )

        # Back-fill thread_id on the LLM call record
        if llm_call_id and result.get("id"):
            llm_call = db.query(LlmCall).filter(LlmCall.id == llm_call_id).first()
            if llm_call:
                llm_call.thread_id = result["id"]
                db.commit()

        return result

    def handle_followup(
        self,
        message: str,
        extracted: dict,
        open_task: dict,
        db: Session,
    ) -> dict:
        db.add(Message(thread_id=open_task["id"], role="user", content=message))
        db.flush()

        product = None
        venue = None
        product_name = extracted.get("product_name")
        if product_name:
            product = resolve_product(product_name, db)
        venue_name = extracted.get("venue_name")
        if venue_name:
            venue = resolve_venue(venue_name, db)
        quantity = extracted.get("quantity")

        return update_order(db, open_task["id"], product, venue, quantity)

    def _create(
        self,
        message: str,
        extracted: dict,
        clarification_question: str | None,
        db: Session,
        user_id: str | None,
        intent: str = "procurement.order",
    ) -> dict:
        product = None
        venue = None

        product_name = extracted.get("product_name")
        if product_name:
            product = resolve_product(product_name, db)

        venue_name = extracted.get("venue_name")
        if venue_name:
            venue = resolve_venue(venue_name, db)

        quantity = extracted.get("quantity")

        order = create_draft_order(
            db=db,
            message=message,
            intent=intent,
            product=product,
            venue=venue,
            quantity=quantity,
            user_id=user_id,
            extracted_extra={k: v for k, v in extracted.items() if k.startswith("_")},
        )

        # Use LLM clarification question if available
        if clarification_question and order.get("status") == "awaiting_user_input":
            task = db.query(Thread).filter(Thread.id == order["id"]).first()
            if task:
                task.clarification_question = clarification_question
                msgs = list(task.messages)
                if msgs and msgs[-1].role == "assistant":
                    msgs[-1].content = clarification_question
                db.commit()
                order["clarification_question"] = clarification_question
                order["conversation"][-1]["text"] = clarification_question

        return order
