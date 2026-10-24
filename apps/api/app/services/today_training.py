"""The Training provider for Home's Today block (services/today.py): a
person's outstanding training, read through the Training app's own logic
(its ``member`` view) when the app is on and they have a people record.
This is the logic ``GET /api/me/today`` carried before providers existed,
moved here unchanged: the items it makes are the same.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.services.today import TodayItem, register

logger = logging.getLogger(__name__)


def _due(value) -> date | datetime | None:
    """A plan section's ``due_date`` (``YYYY-MM-DD``) as a date, so the item
    serialises back to the same string; anything else reads as undated."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return (
            date.fromisoformat(value)
            if len(value) == 10
            else datetime.fromisoformat(value)
        )
    except ValueError:
        return None


@register("training")
def training(
    db: Session, config_db: Session, user, organization_id: str, venue_ids: list[str]
) -> list[TodayItem]:
    from app.db.models import AppRecord, Venue
    from app.services import staff_directory
    from app.services.app_runtime import run_logic
    from app.services.builtin_apps import bind, get_builtin, is_on

    if not organization_id or not is_on("training", organization_id, db, config_db):
        return []
    person = (
        db.query(AppRecord)
        .filter(
            AppRecord.namespace == staff_directory.PEOPLE_NAMESPACE,
            AppRecord.collection == staff_directory.PEOPLE_COLLECTION,
            AppRecord.organization_id == organization_id,
            AppRecord.data["norm_user_id"].astext == user.id,
        )
        .first()
    )
    if person is None:
        return []
    b = get_builtin("training")
    if b is None:
        return []
    app, version = bind(b, organization_id)
    venues = [
        {"id": v.id, "name": v.name}
        for v in db.query(Venue).filter(Venue.organization_id == organization_id).all()
    ]
    out = run_logic(
        db,
        config_db,
        app=app,
        version=version,
        user=user,
        venue_id=None,
        params={"op": "member", "person_id": person.id, "venues": venues},
    )
    if not out.get("success"):
        logger.warning("today: training member view failed: %s", out.get("error"))
        return []
    data = out.get("data") or {}
    today = datetime.now(timezone.utc).date().isoformat()
    items: list[TodayItem] = []
    for a in data.get("assignments") or []:
        if a.get("status") == "completed":
            continue
        percent = a.get("percent") or 0
        detail = (f"{percent}% done" if percent else "Not started") + (
            f" · {a['instance']}" if a.get("instance") else ""
        )
        items.append(
            TodayItem(
                kind="training",
                title=a.get("program") or "Training",
                detail=detail,
                due_at=None,
                tone="info",
                app_slug="training",
                page_id="training",
            )
        )
    for plan in data.get("plans") or []:
        for section in plan.get("sections") or []:
            if section.get("status") == "completed":
                continue
            due = section.get("due_date")
            items.append(
                TodayItem(
                    kind="training",
                    title=section.get("name") or "Training section",
                    detail=plan.get("program") or "Training plan",
                    due_at=_due(due),
                    tone="overdue" if due and due < today else "due",
                    app_slug="training",
                    page_id="training",
                )
            )
    return items
