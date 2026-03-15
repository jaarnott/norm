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

    def _default_prompt(self) -> str:
        """Return the hardcoded default prompt for this domain. Override in subclasses."""
        return ""

    def get_system_prompt(self, db: Session | None = None) -> str:
        """Return the domain-specific system prompt for interpretation.

        Priority:
        1. Dynamic prompt built from connector specs (if any are bound)
        2. DB-stored custom prompt (via agent_config_service)
        3. Hardcoded default from _default_prompt()
        """
        if db:
            from app.agents.prompt_builder import build_dynamic_prompt

            dynamic = build_dynamic_prompt(self.domain, db)
            if dynamic:
                return dynamic

            from app.services.agent_config_service import get_system_prompt as get_db_prompt

            return get_db_prompt(self.domain, db)
        return self._default_prompt()

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
