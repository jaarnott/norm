"""The closing Sales Manager — who closed the venue the night of a
performance, found in Loaded each morning, emailed a link that opens the
approval (plan §11.7, D22).

The lookup is F2's engine-only consolidator ``loadedhub.get_closing_manager``
(Orbit's rule: bucket shifts by trading day; the latest-ending clock-in in
the named roles wins, else the latest-ending rostered shift), called through
``execute_connector_tool`` with the venue pinned (``strict_venue``). The
answer is stored on the performance — name, Loaded staff id, email, the
matched Norm user (by email, case-blind, among the organisation's members)
and how it was found — so the Roster's "Closing manager" column reads from
the row, not from Loaded.

The email goes only to a closing manager with a Norm login: approving needs
one, with access to the venue (D15). Reminders three days apart, four at
most; then the performance joins the venue digest, which also names the
venues where nobody was found and the managers with no Norm login (so the
gap shows the next morning — risk 23), and the performer confirmations still
outstanding. The deep link is ``/?page=ent-roster&performance=<id>``: the
web app resolves ``?page=`` on load and the Roster opens that performance.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.entertainers_models import EntPerformance, EntPerformer
from app.db.models import (
    OrganizationMembership,
    Role,
    User,
    UserVenueAccess,
    Venue,
    _now,
)
from app.services.entertainers import email_links, emails
from app.services.entertainers import performances as performances_svc
from app.services.entertainers._shared import (
    has_ended,
    load_performance,
    require_any_venue,
    settings_for,
    venue_tz,
)
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Conflict, Forbidden

logger = logging.getLogger(__name__)

#: Performances older than this are left to the digest, not re-looked-up.
LOOKBACK = dt.timedelta(days=60)
ROSTER_DAYS = 7
MANAGER_KINDS = ("ent_manager_approval",)


def deep_link(performance_id: str) -> str:
    return (
        f"{settings.app_url.rstrip('/')}/?page=ent-roster&performance={performance_id}"
    )


def roster_link() -> str:
    return f"{settings.app_url.rstrip('/')}/?page=ent-roster"


# ---------------------------------------------------------------------------
# The Loaded lookup (the seam tests replace)
# ---------------------------------------------------------------------------


def lookup(db: Session, venue: Venue, dates: list[dt.date], roles: list[str]) -> dict:
    """``{"YYYY-MM-DD": {"name", "staff_member_id", "email", "source", …} | None}``
    from ``loadedhub.get_closing_manager`` — or ``{"error": …}``."""
    from app.connectors.tool_executor import execute_connector_tool
    from app.db.engine import _ConfigSessionLocal

    config_db = _ConfigSessionLocal()
    try:
        result = execute_connector_tool(
            "loadedhub",
            "get_closing_manager",
            {
                "venue_id": venue.id,
                "venue": venue.name,
                "dates": [d.isoformat() for d in dates],
                "roles": list(roles),
            },
            db,
            config_db,
            venue_id=venue.id,
            strict_venue=True,
        )
    finally:
        config_db.close()
    if not getattr(result, "success", False):
        return {"error": getattr(result, "error", None) or "lookup failed"}
    payload = getattr(result, "payload", None)
    data = (
        payload.get("data")
        if isinstance(payload, dict) and "data" in payload
        else payload
    )
    if not isinstance(data, dict):
        return {"error": "no answer"}
    if data.get("error"):
        return {"error": data["error"]}
    by_date = data.get("by_date")
    return by_date if isinstance(by_date, dict) else {}


def read_staff_roles(db: Session, ctx: FinanceCtx, venue: Venue):
    """``loadedhub.get_staff_roles`` for the venue's own token, as the person
    asking (the seam tests replace). Loaded answers a top-level list."""
    from app.connectors.tool_executor import execute_connector_tool
    from app.db.engine import _ConfigSessionLocal

    config_db = _ConfigSessionLocal()
    try:
        result = execute_connector_tool(
            "loadedhub",
            "get_staff_roles",
            {"venue_id": venue.id, "venue": venue.name},
            db,
            config_db,
            venue_id=venue.id,
            strict_venue=True,
            user=ctx.user,
        )
    finally:
        config_db.close()
    if not getattr(result, "success", False):
        return {"error": getattr(result, "error", None) or "Loaded did not answer"}
    return getattr(result, "payload", None)


def require_settings_editor(ctx: FinanceCtx) -> None:
    """The entertainer settings (the closing-manager roles, tolerances,
    reminders) are changed by a payroll writer or an owner of the
    organisation (owner, 23 Oct 2026)."""
    if not (ctx.holds("payroll:write") or ctx.is_owner):
        raise Forbidden(
            "Only a payroll writer or an owner of the organisation can change this."
        )


def loaded_roles(db: Session, ctx: FinanceCtx, venue_id: str, *, read=None) -> dict:
    """The role names Loaded holds at a venue — what the Settings dialog
    offers for the closing-manager roles (D22: the roles are the
    organisation's setting, picked from Loaded's real ones, free text
    allowed). ``{"venue_id", "roles": [names], "error": None | "…"}``: a
    Loaded failure is said, not raised, so the dialog still takes typed
    names."""
    require_settings_editor(ctx)
    require_venue(ctx, venue_id)
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    payload = (read or read_staff_roles)(db, ctx, venue)
    if isinstance(payload, dict) and payload.get("error"):
        return {"venue_id": venue_id, "roles": [], "error": str(payload["error"])}
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("items") or []
    names: dict[str, str] = {}
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict) or row.get("datestampDeleted"):
            continue
        name = " ".join(str(row.get("name") or "").split())
        if name:
            names.setdefault(name.lower(), name)
    return {
        "venue_id": venue_id,
        "roles": sorted(names.values(), key=str.lower),
        "error": None,
    }


def match_user(db: Session, org_id: str, email: str | None) -> User | None:
    """The organisation member with this email, case-blind."""
    if not email:
        return None
    return (
        db.query(User)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .filter(
            OrganizationMembership.organization_id == org_id,
            func.lower(User.email) == email.strip().lower(),
            User.is_active.is_(True),
        )
        .first()
    )


def _store(
    db: Session, perf: EntPerformance, found: dict | None, *, now: dt.datetime
) -> None:
    if found is None:
        perf.closing_manager_source = "none"
        perf.closing_manager_staff_id = None
        perf.closing_manager_name = None
        perf.closing_manager_email = None
        perf.closing_manager_user_id = None
    else:
        email = (found.get("email") or None) and str(found["email"]).strip().lower()
        user = match_user(db, perf.organization_id, email)
        perf.closing_manager_staff_id = found.get("staff_member_id")
        perf.closing_manager_name = found.get("name")
        perf.closing_manager_email = email
        perf.closing_manager_user_id = user.id if user else None
        source = found.get("source") or "clockin"
        perf.closing_manager_source = (
            source if source in ("clockin", "both", "rostered_only") else "clockin"
        )
    perf.closing_manager_resolved_at = now


def _needs_lookup(
    db: Session, org_id: str, venue: Venue, now: dt.datetime
) -> list[EntPerformance]:
    """Ended performances at the venue: unapproved ones (any age inside the
    look-back) and everything in the Roster's last seven days."""
    tz = venue_tz(venue)
    rows = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == org_id,
            EntPerformance.venue_id == venue.id,
            EntPerformance.performance_date >= (now - LOOKBACK).date(),
            EntPerformance.performance_date <= now.date(),
        )
        .all()
    )
    recent = (now - dt.timedelta(days=ROSTER_DAYS)).date()
    out = []
    for perf in rows:
        if not has_ended(perf, tz, now):
            continue
        unapproved = (
            perf.performance_status == "scheduled" and perf.payment_status == "pending"
        )
        if unapproved or perf.performance_date >= recent:
            if perf.closing_manager_resolved_at is None or unapproved:
                out.append(perf)
    return out


def resolve_for_org(
    db: Session, org_id: str, *, now: dt.datetime | None = None, lookup_fn=None
) -> dict:
    """The morning lookup, per venue. One Loaded call per venue with dates
    to resolve; a failed venue is reported, not raised."""
    now = now or _now()
    lookup_fn = lookup_fn or lookup
    roles = list(settings_for(db, org_id).closing_manager_roles or ["Sales Manager"])
    venues = db.query(Venue).filter(Venue.organization_id == org_id).all()
    report = {"venues": 0, "resolved": 0, "none": 0, "failed": []}
    for venue in venues:
        rows = _needs_lookup(db, org_id, venue, now)
        if not rows:
            continue
        dates = sorted({r.performance_date for r in rows})
        report["venues"] += 1
        try:
            by_date = lookup_fn(db, venue, dates[-31:], roles)
        except Exception as exc:  # noqa: BLE001 — one venue's failure is reported, not raised
            by_date = {"error": str(exc)[:300]}
        if "error" in by_date and len(by_date) == 1:
            report["failed"].append({"venue": venue.name, "error": by_date["error"]})
            logger.warning(
                "closing manager lookup failed at %s: %s", venue.name, by_date["error"]
            )
            continue
        for perf in rows:
            found = by_date.get(perf.performance_date.isoformat())
            _store(db, perf, found if isinstance(found, dict) else None, now=now)
            report["resolved" if found else "none"] += 1
        db.flush()
    return report


def resolve_one(
    db: Session, ctx: FinanceCtx, performance_id: str, *, lookup_fn=None
) -> EntPerformance:
    """Staff press "find the closing manager" on one performance."""
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    venue = db.query(Venue).filter(Venue.id == perf.venue_id).first()
    roles = list(
        settings_for(db, ctx.org_id).closing_manager_roles or ["Sales Manager"]
    )
    by_date = (lookup_fn or lookup)(db, venue, [perf.performance_date], roles)
    if "error" in by_date and len(by_date) == 1:
        raise Conflict(f"Loaded could not answer: {by_date['error']}")
    found = by_date.get(perf.performance_date.isoformat())
    _store(db, perf, found if isinstance(found, dict) else None, now=_now())
    db.flush()
    return perf


# ---------------------------------------------------------------------------
# The approval email and its reminders
# ---------------------------------------------------------------------------


def _queue_approval_email(
    db: Session,
    perf: EntPerformance,
    *,
    send_number: int,
    now: dt.datetime | None = None,
) -> bool:
    if not perf.closing_manager_email or not perf.closing_manager_user_id:
        return False
    performer = (
        db.query(EntPerformer).filter(EntPerformer.id == perf.performer_id).first()
    )
    venue = db.query(Venue).filter(Venue.id == perf.venue_id).first()
    org_id, perf_id, performer_id, venue_id = (
        perf.organization_id,
        perf.id,
        perf.performer_id,
        perf.venue_id,
    )
    address = perf.closing_manager_email
    context = {
        "first_name": emails.first_name(perf.closing_manager_name),
        "performer_name": performer.name if performer else "the performer",
        "venue_name": venue.name if venue else "the venue",
        "date_text": emails.date_text(perf.performance_date),
        "time_text": emails.time_text(perf.start_time, perf.end_time),
        "url": deep_link(perf.id),
        "reminder": send_number > 1,
        "send_number": send_number,
    }

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind="ent_manager_approval",
            to=address,
            context=context,
            performer_id=performer_id,
            venue_id=venue_id,
            performance_ids=[perf_id],
            target_id=perf_id,
            now=now,
        )

    emails.later(db, _send)
    return True


def send_approval_requests(
    db: Session, org_id: str, *, now: dt.datetime | None = None
) -> dict:
    """Ended, unapproved performances: the closing manager's email, then
    reminders ``reminder_interval_days`` apart up to ``reminder_max``. Returns
    what the digest needs: the ones past their emails, the ones with no
    closing manager, and the ones whose manager has no Norm login."""
    now = now or _now()
    settings_row = settings_for(db, org_id)
    rows = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == org_id,
            EntPerformance.performance_status == "scheduled",
            EntPerformance.payment_status == "pending",
            EntPerformance.performance_date >= (now - LOOKBACK).date(),
            EntPerformance.performance_date <= now.date(),
        )
        .order_by(EntPerformance.performance_date)
        .all()
    )
    venues = {v.id: v for v in db.query(Venue).filter(Venue.organization_id == org_id)}
    out = {"sent": 0, "exhausted": [], "no_manager": [], "no_login": []}
    for perf in rows:
        if not has_ended(perf, venue_tz(venues.get(perf.venue_id)), now):
            continue
        if perf.closing_manager_resolved_at is None:
            continue  # not looked up yet (the lookup failed) — next morning
        if not perf.closing_manager_staff_id or perf.closing_manager_source == "none":
            out["no_manager"].append(perf)
            continue
        if not perf.closing_manager_user_id or not perf.closing_manager_email:
            out["no_login"].append(perf)
            continue
        count = email_links.send_count(db, org_id, MANAGER_KINDS, target_id=perf.id)
        if count >= settings_row.reminder_max:
            out["exhausted"].append(perf)
            continue
        last = email_links.last_sent_at(db, org_id, MANAGER_KINDS, target_id=perf.id)
        if last is not None and now - last < dt.timedelta(
            days=settings_row.reminder_interval_days
        ):
            continue
        if _queue_approval_email(db, perf, send_number=count + 1, now=now):
            out["sent"] += 1
    return out


def send_approval_request(
    db: Session, ctx: FinanceCtx, performance_id: str, *, resend: bool = False
) -> dict:
    """Staff resend the closing manager's request from the Emails page."""
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id)
    if perf.performance_status != "scheduled":
        raise Conflict("This performance has already been approved.")
    if not perf.closing_manager_user_id:
        raise Conflict(
            "No closing manager with a Norm login is recorded for this performance."
        )
    count = email_links.send_count(db, ctx.org_id, MANAGER_KINDS, target_id=perf.id)
    return {"sent": _queue_approval_email(db, perf, send_number=count + 1)}


# ---------------------------------------------------------------------------
# The venue digest
# ---------------------------------------------------------------------------


def venue_managers(db: Session, org_id: str, venue_id: str) -> list[User]:
    """People to tell when a venue's approvals stall: members with access to
    the venue whose role runs it (owner, manager, payroll admin)."""
    rows = (
        db.query(User, OrganizationMembership, Role)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .outerjoin(Role, Role.id == OrganizationMembership.role_id)
        .join(UserVenueAccess, UserVenueAccess.user_id == User.id)
        .filter(
            OrganizationMembership.organization_id == org_id,
            UserVenueAccess.venue_id == venue_id,
            User.is_active.is_(True),
        )
        .all()
    )
    out = []
    for user, membership, role in rows:
        role_name = role.name if role else None
        if membership.role in ("owner", "admin") or role_name in (
            "owner",
            "manager",
            "payroll_admin",
        ):
            out.append(user)
    return out


def _item(db: Session, perf: EntPerformance, names: dict) -> dict:
    return {
        "date_text": emails.date_text(perf.performance_date),
        "performer_name": names.get(perf.performer_id, "the performer"),
        "time_text": emails.time_text(perf.start_time, perf.end_time),
        "closing_manager": perf.closing_manager_name,
        "url": deep_link(perf.id),
    }


def send_digest(
    db: Session, org_id: str, buckets: dict, *, now: dt.datetime | None = None
) -> int:
    """One digest per venue with something open, to that venue's managers,
    once per day."""
    now = now or _now()
    by_venue: dict[str, dict] = {}
    for key in ("exhausted", "no_manager", "no_login"):
        for perf in buckets.get(key, []):
            by_venue.setdefault(
                perf.venue_id,
                {"exhausted": [], "no_manager": [], "no_login": [], "unconfirmed": []},
            )[key].append(perf)
    venues = {v.id: v for v in db.query(Venue).filter(Venue.organization_id == org_id)}
    for venue_id in venues:
        unconfirmed = performances_svc.unconfirmed_for_venue(
            db, org_id, venue_id, now=now
        )
        if unconfirmed:
            by_venue.setdefault(
                venue_id,
                {"exhausted": [], "no_manager": [], "no_login": [], "unconfirmed": []},
            )["unconfirmed"] = unconfirmed
    sent = 0
    for venue_id, lists in by_venue.items():
        venue = venues.get(venue_id)
        if venue is None:
            continue
        perfs = [p for group in lists.values() for p in group]
        names = {
            pid: name
            for pid, name in db.query(EntPerformer.id, EntPerformer.name).filter(
                EntPerformer.id.in_({p.performer_id for p in perfs})
            )
        }
        context = {
            "venue_name": venue.name,
            "awaiting": [_item(db, p, names) for p in lists["exhausted"]],
            "no_closing_manager": [_item(db, p, names) for p in lists["no_manager"]],
            "no_login": [_item(db, p, names) for p in lists["no_login"]],
            "unconfirmed": [_item(db, p, names) for p in lists["unconfirmed"]],
            "roster_url": roster_link(),
        }
        recipients = [u.email for u in venue_managers(db, org_id, venue_id) if u.email]
        if not recipients:
            recipients = [
                None
            ]  # shadow routes it to the contact; live logs it suppressed
        ids = [p.id for p in perfs]
        for address in recipients:
            first = emails.first_name(
                next(
                    (
                        u.full_name
                        for u in venue_managers(db, org_id, venue_id)
                        if u.email == address
                    ),
                    None,
                )
            )
            emails.deliver(
                db,
                org_id=org_id,
                kind="ent_manager_digest",
                to=address,
                context={**context, "first_name": first},
                venue_id=venue_id,
                performance_ids=ids,
                target_id=f"digest:{venue_id}:{now.date().isoformat()}:{address or 'nobody'}",
                now=now,
            )
            sent += 1
    return sent
