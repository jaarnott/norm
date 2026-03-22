"""Token usage tracking and aggregation for billing."""

import datetime
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_usage(db: Session, user_id: str | None, input_tokens: int | None, output_tokens: int | None) -> None:
    """Upsert daily token usage for the user's organization."""
    if not input_tokens and not output_tokens:
        return

    from app.db.models import TokenUsage, OrganizationMembership

    # Find user's organization
    org_id = None
    if user_id:
        membership = db.query(OrganizationMembership).filter(
            OrganizationMembership.user_id == user_id
        ).first()
        if membership:
            org_id = membership.organization_id

    if not org_id:
        return  # Can't track without an org

    today = datetime.date.today().isoformat()

    row = db.query(TokenUsage).filter(
        TokenUsage.organization_id == org_id,
        TokenUsage.user_id == user_id,
        TokenUsage.date == today,
    ).first()

    if row:
        row.input_tokens = (row.input_tokens or 0) + (input_tokens or 0)
        row.output_tokens = (row.output_tokens or 0) + (output_tokens or 0)
        row.llm_call_count = (row.llm_call_count or 0) + 1
    else:
        db.add(TokenUsage(
            organization_id=org_id,
            user_id=user_id,
            date=today,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            llm_call_count=1,
        ))
