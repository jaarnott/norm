"""App Builder agent — turns a described app into a running Norm app.

The conversation clarifies purpose → data → actions, then authors the spec and
UI and saves through ``norm.save_app`` (the same implementation as the web
endpoint, so the author-permission intersection holds). Everything the agent
may declare comes from ``norm.list_app_capabilities`` — the ground truth that
keeps invented action names out of specs.
"""

import logging

from sqlalchemy.orm import Session

from app.agents.app_builder.context import build_app_builder_context
from app.agents.base import BaseDomainAgent

logger = logging.getLogger(__name__)


class AppBuilderAgent(BaseDomainAgent):
    @property
    def domain(self) -> str:
        return "app_builder"

    def build_context(self, db: Session, user_id: str | None = None) -> dict:
        return build_app_builder_context(db, user_id)

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
        automated_task: dict | None = None,
    ) -> dict:
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
            automated_task=automated_task,
        )

    def handle_followup(
        self,
        message: str,
        extracted: dict,
        open_task: dict,
        db: Session,
    ) -> dict:
        return open_task
