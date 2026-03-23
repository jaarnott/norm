"""Base domain agent interface."""

import logging
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.db.models import Task, Message

logger = logging.getLogger(__name__)


class BaseDomainAgent(ABC):
    """Abstract base for all domain-specialist agents."""

    @property
    @abstractmethod
    def domain(self) -> str:
        """Return the domain slug (e.g. 'procurement', 'hr', 'reports')."""
        ...

    @abstractmethod
    def handle_message(
        self,
        message: str,
        db: Session,
        user_id: str | None = None,
        task_id: str | None = None,
        venue_id: str | None = None,
        venue_name: str | None = None,
        venue_timezone: str | None = None,
    ) -> dict:
        """Process a new user message for this domain.

        Returns a task dict suitable for API response.
        """
        ...

    @abstractmethod
    def handle_followup(
        self,
        message: str,
        extracted: dict,
        open_task: dict,
        db: Session,
    ) -> dict:
        """Apply a follow-up or revision to an existing open task.

        Returns an updated task dict.
        """
        ...

    @abstractmethod
    def build_context(self, db: Session, user_id: str | None = None) -> dict:
        """Build domain-specific context for interpretation."""
        ...

    def get_system_prompt(self, db: Session) -> str:
        """Return the domain-specific system prompt for interpretation.

        Priority:
        1. Dynamic prompt built from connector specs (if any are bound)
        2. DB-stored prompt (via Settings UI)
        """
        from app.agents.prompt_builder import build_dynamic_prompt

        dynamic = build_dynamic_prompt(self.domain, db)
        if dynamic:
            return dynamic

        from app.services.agent_config_service import get_system_prompt as get_db_prompt

        return get_db_prompt(self.domain, db)

    def get_tool_definitions(self, db: Session, active_venue_name: str | None = None, venue_timezone: str | None = None) -> tuple[str, list[dict]]:
        """Return (system_prompt, anthropic_tools) for the agentic tool loop.

        Returns ("", []) if no tools are bound, meaning the agent should
        fall back to the classic interpretation path.
        """
        from app.agents.prompt_builder import build_tool_definitions
        return build_tool_definitions(self.domain, db, active_venue_name=active_venue_name, venue_timezone=venue_timezone)

    def handle_message_with_tools(
        self,
        message: str,
        db: Session,
        user_id: str | None = None,
        task_id: str | None = None,
        venue_id: str | None = None,
        venue_name: str | None = None,
        venue_timezone: str | None = None,
    ) -> dict:
        """Process a message using the agentic tool loop.

        Creates or loads a task, runs the tool loop, and returns the result.
        """
        from app.agents.tool_loop import run_tool_loop, _emit_event

        system_prompt, anthropic_tools = self.get_tool_definitions(db, active_venue_name=venue_name, venue_timezone=venue_timezone)
        ctx = self.build_context(db, user_id)

        # Load or create task
        if task_id:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise ValueError(f"Task not found: {task_id}")
            # Use task's venue if none provided
            if not venue_id and task.venue_id:
                venue_id = task.venue_id
            # Add the user message
            db.add(Message(task_id=task.id, role="user", content=message))
            db.flush()
        else:
            task = Task(
                user_id=user_id,
                venue_id=venue_id,
                domain=self.domain,
                intent=f"{self.domain}.tool_use",
                status="in_progress",
                raw_prompt=message,
                extracted_fields={},
                missing_fields=[],
            )
            db.add(task)
            db.flush()
            db.add(Message(task_id=task.id, role="user", content=message))
            db.flush()

        # Emit the real task ID immediately so the frontend can recover if
        # the SSE connection drops during a long LLM call.
        _emit_event({"type": "task_created", "task_id": task.id})

        return run_tool_loop(message, task, db, system_prompt, anthropic_tools, context=ctx)

    def interpret(self, message: str, context: dict, db: Session | None = None, task_id: str | None = None) -> tuple[dict, str | None]:
        """Call the LLM with this agent's prompt. Returns (parsed_json, llm_call_id).

        Uses the shared call_llm helper so all agents go through the
        same Anthropic API path.
        """
        from app.interpreter.llm_interpreter import call_llm

        return call_llm(
            system_prompt=self.get_system_prompt(db=db),
            user_prompt=self._build_user_prompt(message, context),
            db=db,
            task_id=task_id,
            call_type="interpretation",
        )

    def _build_user_prompt(self, message: str, context: dict) -> str:
        """Default user prompt builder. Subclasses can override."""
        import json

        parts = [f'USER MESSAGE: "{message}"']

        for key, value in context.items():
            if key == "open_task":
                parts.append(f"OPEN TASK (this message may be a follow-up):\n{json.dumps(value, indent=2, default=str)}")
            elif value:
                label = key.upper().replace("_", " ")
                parts.append(f"{label}: {json.dumps(value)}")

        parts.append("Respond with ONLY valid JSON. No markdown, no explanation.")
        return "\n\n".join(parts)
