"""Reports domain agent."""

import logging

from sqlalchemy.orm import Session

from app.agents.base import BaseDomainAgent
from app.agents.reports.context import build_reports_context, _report_thread_to_dict
from app.agents.reports.planner import create_report_plan
from app.db.models import Thread, Message, LlmCall

logger = logging.getLogger(__name__)


class ReportsAgent(BaseDomainAgent):
    @property
    def domain(self) -> str:
        return "reports"

    def build_context(self, db: Session, user_id: str | None = None) -> dict:
        return build_reports_context(db, user_id)

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
            config_db=config_db,
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
                config_db=config_db,
            )

        # Classic interpretation path (no tools bound)
        ctx = self.build_context(db, user_id)

        # If thread_id provided, load it as open task for follow-up
        if thread_id:
            task = db.query(Thread).filter(Thread.id == thread_id).first()
            if task and task.domain == "reports":
                ctx["open_task"] = _report_thread_to_dict(task)

        # Interpret — pass thread_id if this is a follow-up
        parsed, llm_call_id = self.interpret(message, ctx, db=db, thread_id=thread_id)

        is_followup = parsed.get("is_followup", False)
        extracted = parsed.get("extracted_fields", {})
        clarification_question = parsed.get("clarification_question")

        # Force follow-up if thread_id was provided
        if thread_id and ctx.get("open_task"):
            is_followup = True

        # Follow-up path
        if is_followup and ctx.get("open_task"):
            return self.handle_followup(message, extracted, ctx["open_task"], db)

        # New report task
        result = self._create(message, extracted, clarification_question, db, user_id)

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
        task = db.query(Thread).filter(Thread.id == open_task["id"]).first()
        if not task:
            return open_task

        db.add(Message(thread_id=task.id, role="user", content=message))
        db.flush()

        # Merge new fields into existing extracted_fields
        current = dict(task.extracted_fields or {})
        for key, value in extracted.items():
            if value is not None:
                current[key] = value
        task.extracted_fields = current

        # Re-plan and execute with updated fields
        plan = create_report_plan({"extracted_fields": current})
        result = self._execute_plan(plan, current)

        current["report_plan"] = [s["step"] for s in plan]
        current["report_result"] = result
        task.extracted_fields = current
        task.status = "awaiting_approval"
        task.clarification_question = None
        task.missing_fields = []

        summary = self._format_summary(result)
        db.add(Message(thread_id=task.id, role="assistant", content=summary))

        db.commit()
        db.refresh(task)
        return _report_thread_to_dict(task)

    def _create(
        self,
        message: str,
        extracted: dict,
        clarification_question: str | None,
        db: Session,
        user_id: str | None,
    ) -> dict:
        # Check minimum requirements
        report_type = extracted.get("report_type")
        data_sources = extracted.get("data_sources", [])
        missing = []
        if not report_type:
            missing.append("report_type")
        if not data_sources:
            missing.append("data_sources")

        if missing and not clarification_question:
            clarification_question = "What kind of report would you like? (e.g. sales summary, inventory check)"

        # Build plan and execute even with partial info
        plan = create_report_plan({"extracted_fields": extracted})
        result = self._execute_plan(plan, extracted)

        extracted["report_plan"] = [s["step"] for s in plan]
        extracted["report_result"] = result

        status = "awaiting_user_input" if missing else "awaiting_approval"

        task = Thread(
            user_id=user_id,
            intent="reports.generate",
            domain="reports",
            status=status,
            raw_prompt=message,
            extracted_fields=extracted,
            missing_fields=missing,
            clarification_question=clarification_question if missing else None,
        )
        db.add(task)
        db.flush()

        db.add(Message(thread_id=task.id, role="user", content=message))

        if missing and clarification_question:
            db.add(
                Message(
                    thread_id=task.id, role="assistant", content=clarification_question
                )
            )
        else:
            summary = self._format_summary(result)
            db.add(Message(thread_id=task.id, role="assistant", content=summary))

        db.commit()
        db.refresh(task)
        return _report_thread_to_dict(task)

    def _execute_plan(self, plan: list[dict], extracted: dict) -> dict:
        """Execute a report plan. Requires connector specs to be bound."""
        raise NotImplementedError(
            "Classic report execution is no longer supported. "
            "Bind connector specs to the reports agent and use the tool loop."
        )

    def _format_summary(self, result: dict) -> str:
        """Format a report result into a human-readable summary."""
        totals = result.get("totals", {})
        period_count = result.get("period_count", 0)
        report_type = result.get("report_type", "report")

        parts = [f"Here's your {report_type} report ({period_count} periods):"]
        for metric, value in totals.items():
            parts.append(
                f"  {metric}: ${value:,.2f}"
                if "revenue" in metric or "cost" in metric
                else f"  {metric}: {value:,.0f}"
            )
        parts.append("Ready for your review.")
        return "\n".join(parts)
