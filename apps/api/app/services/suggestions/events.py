"""Suggestion events: shown, clicked, dismissed (services/suggestions)."""

from __future__ import annotations

from sqlalchemy.orm import Session


def record_event(
    db: Session, user, suggestion_id: str, event: str, venue_id: str | None = None
) -> None:
    """One event. An insight's subject and value ride along, so a repeat is
    judged per subject and a material change can bring it back."""
    from app.db.models import Insight, SuggestionEvent
    from app.services.entitlements import org_id_for_user

    org_id = org_id_for_user(user.id, db)
    subject_key = value = None
    if suggestion_id.startswith("insight:"):
        row = (
            db.query(Insight)
            .filter(Insight.id == suggestion_id.split(":", 1)[1])
            .first()
        )
        if row is None or row.organization_id != org_id:
            return
        subject_key, value, venue_id = row.subject_key, row.value, row.venue_id
    db.add(
        SuggestionEvent(
            user_id=user.id,
            organization_id=org_id,
            suggestion_id=suggestion_id[:200],
            subject_key=subject_key,
            event=event,
            venue_id=venue_id,
            value=value,
        )
    )
