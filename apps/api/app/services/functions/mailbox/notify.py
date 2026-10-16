"""The two emails a venue mailbox causes.

* **"Reconnect me."** When Google refuses a venue mailbox's grant, syncing
  stops and sends fail closed — so somebody who can fix it must hear about
  it, once (the account's ``reconnect_notified_at``; a reconnect clears it).
  The people told are the venue's Functions notification addresses plus the
  organisation's members who can connect mailboxes (``settings:connectors``,
  or an owner/admin membership) and can see the venue.
* **"You were connected."** After a connect, the mailbox address itself is
  told who connected it, for which venue and organisation, and when — the
  owner of an address connected without their knowledge hears at once.

Both go through the Functions email path (``services/functions/notify.py``):
outside production nothing is sent — local and staging data are real
people's — the email is logged as suppressed instead (and a dead local
mailbox still counts as told, so it doesn't log every minute).
"""

from __future__ import annotations

import html
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _recipients(db: Session, org_id: str, venue_id: str) -> list[str]:
    from app.db.functions_models import FnVenueFunctionSettings
    from app.db.models import OrganizationMembership, Role, User, UserVenueAccess

    out: list[str] = []
    settings_row = (
        db.query(FnVenueFunctionSettings)
        .filter(
            FnVenueFunctionSettings.organization_id == org_id,
            FnVenueFunctionSettings.venue_id == venue_id,
        )
        .first()
    )
    for email in (settings_row.notify_emails if settings_row else None) or []:
        value = str(email or "").strip().lower()
        if value and value not in out:
            out.append(value)

    venue_users = {
        r[0]
        for r in db.query(UserVenueAccess.user_id).filter(
            UserVenueAccess.venue_id == venue_id
        )
    }
    rows = (
        db.query(User, OrganizationMembership, Role)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .outerjoin(Role, Role.id == OrganizationMembership.role_id)
        .filter(
            OrganizationMembership.organization_id == org_id,
            User.is_active.is_(True),
        )
        .all()
    )
    for user, membership, role in rows:
        owner = membership.role in ("owner", "admin") or (role and role.name == "owner")
        can_connect = owner or "settings:connectors" in set(
            (role.permissions if role else None) or []
        )
        if not can_connect or not (owner or user.id in venue_users):
            continue
        email = (user.email or "").strip().lower()
        if email and email not in out:
            out.append(email)
    return out


def send_reconnect_notice(db: Session, account, venue_name: str, reason: str):
    """Tell the people who can fix it, through the Functions email path
    (``services/functions/notify.py``: suppressed and logged outside
    production). Returns the delivery (None when nobody can be told). The
    caller records ``reconnect_notified_at``."""
    from app.config import settings
    from app.services.functions import notify as fn_notify

    to = _recipients(db, account.organization_id, account.venue_id)
    if not to:
        logger.warning(
            "Mailbox %s needs reconnecting but nobody can be told", account.id
        )
        return None
    body = fn_notify.page(
        f"Reconnect {venue_name}'s mailbox",
        [
            "Google has stopped accepting Norm's connection to "
            f"<strong>{html.escape(account.google_email)}</strong>, the mailbox "
            f"for {html.escape(venue_name)}.",
            "Until it is reconnected, Norm Functions isn't seeing new mail for "
            "this venue and can't send from it. Reconnect it from the Inbox "
            "(or Settings → Venues).",
            f"Google said: {html.escape(reason[:300])}",
        ],
        button=(
            "Open the Inbox",
            f"{settings.app_url.rstrip('/')}/app?page=functions-inbox",
        ),
    )
    return fn_notify.send_email(
        db,
        to=to,
        subject=f"Reconnect {venue_name}'s mailbox in Norm",
        html_body=body,
        template="mailbox_reconnect",
        org_id=account.organization_id,
        venue_id=account.venue_id,
    )


def _local(when, tz_name: str | None) -> str:
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(tz_name or "Pacific/Auckland")
    except Exception:  # noqa: BLE001 — an unknown zone reads as UTC
        zone = ZoneInfo("UTC")
    local = when.astimezone(zone)
    hour = local.hour % 12 or 12
    return (
        f"{local:%A} {local.day} {local:%B} {local.year}, "
        f"{hour}:{local:%M}{'am' if local.hour < 12 else 'pm'}"
    )


def send_connected_notice(db: Session, *, account, venue, org_name: str, by, when):
    """Tell the mailbox itself that Norm Functions now reads it, and who
    connected it — so the owner of an address connected without their
    knowledge hears at once. Through the Functions email path (suppressed
    and logged outside production)."""
    from app.services.functions import notify as fn_notify

    who = (getattr(by, "full_name", None) or getattr(by, "email", None)) or "someone"
    who_email = getattr(by, "email", None)
    by_line = html.escape(who) + (
        f" ({html.escape(who_email)})" if who_email and who_email != who else ""
    )
    body = fn_notify.page(
        "Your mailbox was connected to Norm",
        [
            f"<strong>{html.escape(account.google_email)}</strong> was connected "
            f"to Norm Functions for {html.escape(venue.name)}, "
            f"{html.escape(org_name)} by {by_line} on "
            f"{html.escape(_local(when, venue.timezone))}.",
            "Norm now reads this mailbox's email, and can send from it and "
            "file its conversations, for the venue's functions and events.",
            "If you didn't expect this, tell your manager.",
        ],
    )
    return fn_notify.send_email(
        db,
        to=[account.google_email],
        subject=f"{account.google_email} was connected to Norm Functions",
        html_body=body,
        template="mailbox_connected",
        org_id=account.organization_id,
        venue_id=account.venue_id,
    )
