"""The monthly Ask Norm cap for venue staff (docs/plans/staff-access.md,
sections 4 and 11.4).

Staff chats count toward the organisation's usage allowance like any chat. To
keep that predictable, a person whose role reaches no AI agent at all
(``Access.staff``) has a cap of their own: so many cents of usage per
calendar month, in the organisation's timezone. The organisation sets it
(``Organization.staff_chat_cap_cents``); unset means the platform default
(``settings.STAFF_CHAT_DEFAULT_CAP_CENTS``); zero switches staff chat off.

Managers and owners are never capped here — their role has agents, so
``check`` does nothing for them. The organisation's own allowance
(billing_service.check_quota_for_user) still applies to everyone.
"""

from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Organization, TokenUsage

logger = logging.getLogger(__name__)


class StaffCapExceeded(Exception):
    """Raised before any model call when a venue staff member's chat is off
    or their month's usage has reached the cap. ``str(exc)`` is the sentence
    shown to the person."""

    def __init__(self, message: str, *, used_cents: int = 0, cap_cents: int = 0):
        super().__init__(message)
        self.message = message
        self.used_cents = used_cents
        self.cap_cents = cap_cents


OFF_MESSAGE = "Ask Norm isn't switched on for venue staff here."
REACHED_MESSAGE = (
    "You've reached this month's Ask Norm limit. It starts again next month, "
    "or a manager can raise it."
)


def cap_cents(org: Organization | None) -> int:
    """The organisation's cap in cents: its own setting, else the default."""
    if org is not None and org.staff_chat_cap_cents is not None:
        return max(0, int(org.staff_chat_cap_cents))
    return max(0, int(settings.STAFF_CHAT_DEFAULT_CAP_CENTS))


def today_in(
    tz_name: str | None, now: datetime.datetime | None = None
) -> datetime.date:
    """Today in the organisation's timezone, falling back to UTC when the
    name is missing or unknown. ``now`` is an aware datetime (tests)."""
    try:
        tz = ZoneInfo(tz_name) if tz_name else datetime.timezone.utc
    except Exception:
        tz = datetime.timezone.utc
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(tz).date()


def month_cost_usd(
    db: Session,
    org_id: str,
    user_id: str,
    *,
    tz_name: str | None = None,
    now: datetime.datetime | None = None,
) -> float:
    """What this person's chats in ``org_id`` have cost so far this calendar
    month. Reads the daily ``TokenUsage`` rows, which carry ``cost_usd`` per
    (org, user, day).

    The month is the UTC one, because that is how ``usage_service`` dates the
    rows (the API's clock, UTC on Cloud Run). Picking it in the organisation's
    timezone instead read the new month up to thirteen hours before the rows
    changed over, so an Auckland organisation's capped staff got a free
    window on the 1st and that day never counted. ``tz_name`` is accepted for
    callers that still pass it and ignored; ``now`` is for tests."""
    del tz_name
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    day = moment.astimezone(datetime.timezone.utc).date()
    total = (
        db.query(func.coalesce(func.sum(TokenUsage.cost_usd), 0))
        .filter(
            TokenUsage.organization_id == org_id,
            TokenUsage.user_id == user_id,
            TokenUsage.date.like(f"{day.year:04d}-{day.month:02d}-%"),
        )
        .scalar()
    )
    return float(total or 0)


def check(db: Session, user_id: str | None) -> None:
    """Raise ``StaffCapExceeded`` when a venue staff member may not chat right
    now: the cap is zero (staff chat is off) or the month's cost has reached
    it. Does nothing for anyone whose role has an AI agent, for platform
    admins, and for people with no membership."""
    from app.services import active_org, role_access

    if not user_id or not role_access.for_user(db, user_id).staff:
        return
    membership = active_org.membership_for(db, user_id)
    if membership is None:
        return
    org = (
        db.query(Organization)
        .filter(Organization.id == membership.organization_id)
        .first()
    )
    cap = cap_cents(org)
    if cap == 0:
        raise StaffCapExceeded(OFF_MESSAGE, used_cents=0, cap_cents=0)
    used = month_cost_usd(
        db,
        membership.organization_id,
        user_id,
        tz_name=org.default_timezone if org is not None else None,
    )
    if used * 100 >= cap:
        raise StaffCapExceeded(
            REACHED_MESSAGE, used_cents=int(round(used * 100)), cap_cents=cap
        )
