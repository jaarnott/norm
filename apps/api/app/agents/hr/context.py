"""Build HR-specific context from the database."""

from sqlalchemy.orm import Session

from app.db.models import Venue
HR_ROLES = [
    "bartender", "barista", "chef", "head chef", "sous chef",
    "kitchen hand", "dishwasher", "waiter", "waitress", "host",
    "hostess", "manager", "duty manager", "floor manager",
    "bar manager", "kitchen manager", "server",
]


def build_hr_context(db: Session, user_id: str | None = None) -> dict:
    """Return context dict with venue names, HR roles, and open HR task."""
    venues = db.query(Venue).all()
    venue_names = [v.name for v in venues]

    ctx: dict = {
        "venue_names": venue_names,
        "hr_roles": HR_ROLES,
    }

    return ctx
