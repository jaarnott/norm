"""Base agent interface — the one Norm agent (app/agents/norm.py) implements it."""

import logging
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.db.models import Thread, Message

logger = logging.getLogger(__name__)


class BaseDomainAgent(ABC):
    """Abstract base for the Norm agent.

    There used to be seven of these, one per team member, and a router picking
    between them. Since Apps v3 every conversation holds the full entitled tool
    union, so the choice changed nothing but a label — see app/agents/norm.py.
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """The slug new threads are filed under ("norm")."""
        ...

    @abstractmethod
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
        page_context: dict | None = None,
        automated_task: dict | None = None,
    ) -> dict:
        """Process a user message. Returns a thread dict for the API response."""
        ...

    @abstractmethod
    def build_context(self, db: Session, user_id: str | None = None) -> dict:
        """Context appended to the user's message (the venues they can see)."""
        ...

    def get_tool_definitions(
        self,
        db: Session,
        active_venue_name: str | None = None,
        venue_timezone: str | None = None,
        user_id: str | None = None,
        config_db: Session | None = None,
        page_context: dict | None = None,
        tool_filter: list[str] | None = None,
        automated_task: dict | None = None,
    ) -> tuple[str, list[dict]]:
        """Return (system_prompt, anthropic_tools) for the agentic tool loop.

        Returns ("", []) if the user holds no tools at all.
        """
        from app.agents.prompt_builder import build_tool_definitions

        return build_tool_definitions(
            self.domain,
            db,
            active_venue_name=active_venue_name,
            venue_timezone=venue_timezone,
            user_id=user_id,
            config_db=config_db,
            page_context=page_context,
            tool_filter=tool_filter,
            automated_task=automated_task,
        )

    def handle_message_with_tools(
        self,
        message: str,
        db: Session,
        user_id: str | None = None,
        thread_id: str | None = None,
        venue_id: str | None = None,
        venue_name: str | None = None,
        venue_timezone: str | None = None,
        config_db: Session | None = None,
        page_context: dict | None = None,
        automated_task: dict | None = None,
    ) -> dict:
        """Process a message using the agentic tool loop.

        Creates or loads a task, runs the tool loop, and returns the result.
        """
        from app.agents.tool_loop import (
            run_tool_loop,
            _emit_event,
            current_turn_attachments,
        )
        from app.services.attachments import link_chat_attachments

        turn_attachment_ids = current_turn_attachments()

        system_prompt, anthropic_tools = self.get_tool_definitions(
            db,
            active_venue_name=venue_name,
            venue_timezone=venue_timezone,
            user_id=user_id,
            config_db=config_db,
            page_context=page_context,
            automated_task=automated_task,
        )
        ctx = self.build_context(db, user_id)

        # Load or create thread. COMMIT the thread and user message before any
        # LLM work: a turn can run for minutes and used to hold both as
        # uncommitted transaction state the whole time, so the conversation was
        # invisible to every other request until the turn ended, and any error
        # (or instance death) rolled the user's message — and a new thread —
        # out of existence. Durability first, then think.
        if thread_id:
            thread = db.query(Thread).filter(Thread.id == thread_id).first()
            if not thread:
                raise ValueError(f"Thread not found: {thread_id}")
            # Use thread's venue if none provided
            if not venue_id and thread.venue_id:
                venue_id = thread.venue_id
            # Add the user message (+ any files attached to this turn)
            attachments = (
                link_chat_attachments(turn_attachment_ids, thread.id, user_id, db)
                or None
            )
            db.add(
                Message(
                    thread_id=thread.id,
                    role="user",
                    content=message,
                    attachments=attachments,
                )
            )
            db.commit()
        else:
            thread = Thread(
                user_id=user_id,
                venue_id=venue_id,
                domain=self.domain,
                intent=f"{self.domain}.tool_use",
                status="in_progress",
                raw_prompt=message,
                extracted_fields={},
                missing_fields=[],
            )
            db.add(thread)
            db.flush()
            attachments = (
                link_chat_attachments(turn_attachment_ids, thread.id, user_id, db)
                or None
            )
            db.add(
                Message(
                    thread_id=thread.id,
                    role="user",
                    content=message,
                    attachments=attachments,
                )
            )
            db.commit()

        # Emit the real thread ID immediately so the frontend can recover if
        # the SSE connection drops during a long LLM call.
        _emit_event({"type": "thread_created", "thread_id": thread.id})

        return run_tool_loop(
            message,
            thread,
            db,
            system_prompt,
            anthropic_tools,
            context=ctx,
            config_db=config_db,
        )
