"""Token usage tracking and aggregation for billing."""

import datetime
import logging

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_usage(
    db: Session,
    user_id: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    """Upsert daily token usage for the user's organization.

    Writes through its OWN short-lived transaction, never the caller's.
    Riding the turn's session is how one usage row sank two whole turns in
    production (05 Aug 2026): the day's first insert stayed uncommitted — and
    row-locked — for the full duration of a 4½-minute turn, so a concurrent
    turn's insert blocked at flush and then died with UniqueViolation the
    moment the first turn committed, rolling back the user's message with it.
    A separate transaction holds the row lock for milliseconds and can never
    poison the turn's transaction (the caller's try/except can't help there:
    a dirty row in a shared session detonates at someone else's flush).
    """
    if not input_tokens and not output_tokens:
        return

    from app.db.models import OrganizationMembership

    # Org lookup is a plain read; the caller's session is fine for that.
    org_id = None
    if user_id:
        membership = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == user_id)
            .first()
        )
        if membership:
            org_id = membership.organization_id

    if not org_id:
        return  # Can't track without an org

    today = datetime.date.today().isoformat()

    # In production get_bind() is the engine, so this session gets its own
    # connection; under the test fixtures it is the per-test connection, so
    # the write stays inside the test transaction.
    session = Session(bind=db.get_bind())
    try:
        try:
            _upsert(session, org_id, user_id, today, input_tokens, output_tokens)
        except IntegrityError:
            # Two first-calls of the day raced on the unique (org, user, date)
            # row; the loser retries and lands as an update.
            session.rollback()
            _upsert(session, org_id, user_id, today, input_tokens, output_tokens)
    except Exception:
        # Billing aggregation must never break a turn.
        logger.warning("Token usage recording failed", exc_info=True)
        session.rollback()
    finally:
        session.close()


def _upsert(
    session: Session,
    org_id: str,
    user_id: str,
    today: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    """Update-first upsert, committed immediately."""
    from app.db.models import TokenUsage

    updated = (
        session.query(TokenUsage)
        .filter(
            TokenUsage.organization_id == org_id,
            TokenUsage.user_id == user_id,
            TokenUsage.date == today,
        )
        .update(
            {
                TokenUsage.input_tokens: func.coalesce(TokenUsage.input_tokens, 0)
                + (input_tokens or 0),
                TokenUsage.output_tokens: func.coalesce(TokenUsage.output_tokens, 0)
                + (output_tokens or 0),
                TokenUsage.llm_call_count: func.coalesce(TokenUsage.llm_call_count, 0)
                + 1,
            },
            synchronize_session=False,
        )
    )
    if not updated:
        session.add(
            TokenUsage(
                organization_id=org_id,
                user_id=user_id,
                date=today,
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                llm_call_count=1,
            )
        )
    session.commit()
