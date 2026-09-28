"""The Norm agent — the one agent every conversation talks to.

Until Sep 2026 a Haiku router picked one of seven domain agents for every new
message, and asked again on every follow-up whether to switch. Apps v3 gave the
agent the org's FULL entitled tool union and the unified prompt, so by then the
choice changed only a label and a tone line — while still costing ~1.5s per
turn and, now and then, a lost question (a cross-App ask scored "unknown" and
the user got a capability menu instead of an answer).

Team members survive as packaging: hiring one turns its Apps on, and the
sidebar files threads under the members whose Apps they used. None of that
needs a router. New threads are filed under ``NORM_DOMAIN``; threads created
before this keep their member domain and are answered by this same agent.
"""

import logging

from sqlalchemy.orm import Session

from app.agents.base import BaseDomainAgent
from app.db.models import Message, Thread

logger = logging.getLogger(__name__)

NORM_DOMAIN = "norm"

_NO_TOOLS = (
    "I don't have any Apps to work with yet. Hire a team member on the Team "
    "page — that switches their Apps on — and ask me again."
)


class NormAgent(BaseDomainAgent):
    @property
    def domain(self) -> str:
        return NORM_DOMAIN

    def build_context(self, db: Session, user_id: str | None = None) -> dict:
        from app.services.venue_service import get_user_venues

        venues = get_user_venues(db, user_id)
        return {"venues": [{"id": v.id, "name": v.name} for v in venues]}

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
        _, tools = self.get_tool_definitions(
            db,
            active_venue_name=venue_name,
            venue_timezone=venue_timezone,
            user_id=user_id,
            config_db=config_db,
            page_context=page_context,
            automated_task=automated_task,
        )
        if not tools:
            return self._no_tools(message, db, user_id, thread_id, venue_id)
        return self.handle_message_with_tools(
            message,
            db,
            user_id,
            thread_id,
            venue_id=venue_id,
            venue_name=venue_name,
            venue_timezone=venue_timezone,
            config_db=config_db,
            page_context=page_context,
            automated_task=automated_task,
        )

    def _no_tools(
        self,
        message: str,
        db: Session,
        user_id: str | None,
        thread_id: str | None,
        venue_id: str | None,
    ) -> dict:
        """An org with nothing switched on. Say so in the thread rather than
        fail — the old per-agent fallback asked a model to reply in JSON."""
        thread = (
            db.query(Thread).filter(Thread.id == thread_id).first()
            if thread_id
            else None
        )
        if thread is None:
            thread = Thread(
                user_id=user_id,
                venue_id=venue_id,
                domain=NORM_DOMAIN,
                intent=f"{NORM_DOMAIN}.tool_use",
                status="completed",
                raw_prompt=message,
                extracted_fields={},
                missing_fields=[],
            )
            db.add(thread)
            db.flush()
        db.add(Message(thread_id=thread.id, role="user", content=message))
        db.add(Message(thread_id=thread.id, role="assistant", content=_NO_TOOLS))
        db.commit()
        db.refresh(thread)
        return {
            "id": thread.id,
            "domain": thread.domain,
            "intent": thread.intent,
            "title": thread.title,
            "message": message,
            "status": thread.status,
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
            "conversation": [
                {
                    "role": m.role,
                    "text": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in sorted(thread.messages, key=lambda x: x.created_at)
            ],
        }
