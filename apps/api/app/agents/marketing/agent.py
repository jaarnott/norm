"""Marketing domain agent — social media, email campaigns, content scheduling, analytics."""

import logging

from sqlalchemy.orm import Session

from app.agents.base import BaseDomainAgent
from app.agents.marketing.context import build_marketing_context

logger = logging.getLogger(__name__)


class MarketingAgent(BaseDomainAgent):
    @property
    def domain(self) -> str:
        return "marketing"

    def build_context(self, db: Session, user_id: str | None = None) -> dict:
        return build_marketing_context(db, user_id)

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
        playbook=None,
    ) -> dict:
        system_prompt, anthropic_tools = self.get_tool_definitions(
            db,
            active_venue_name=venue_name,
            venue_timezone=venue_timezone,
            user_id=user_id,
            config_db=config_db,
            page_context=page_context,
            playbook=playbook,
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
                page_context=page_context,
                playbook=playbook,
            )

        # No tools bound
        from app.db.models import Thread, Message

        thread = Thread(
            user_id=user_id,
            venue_id=venue_id,
            domain=self.domain,
            intent="marketing.no_tools",
            status="completed",
            raw_prompt=message,
            extracted_fields={},
            missing_fields=[],
        )
        db.add(thread)
        db.flush()
        reply = (
            "The Marketing agent needs connector tools to be configured. "
            "Please set up Metricool, Brevo, or Meta connectors in Settings."
        )
        db.add(Message(thread_id=thread.id, role="user", content=message))
        db.add(Message(thread_id=thread.id, role="assistant", content=reply))
        db.commit()
        db.refresh(thread)
        return {
            "id": thread.id,
            "domain": self.domain,
            "intent": thread.intent,
            "title": None,
            "message": message,
            "status": "completed",
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
            "conversation": [
                {"role": "user", "text": message},
                {"role": "assistant", "text": reply},
            ],
        }

    def handle_followup(
        self,
        message: str,
        extracted: dict,
        open_task: dict,
        db: Session,
    ) -> dict:
        return open_task
