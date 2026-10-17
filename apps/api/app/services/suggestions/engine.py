"""Choose the suggestions one person sees under the new-chat box (Oct 2026).

Three kinds compete for at most ``limit`` slots (four on a desktop, two on a
phone — the web asks for what it shows):

* **insights** — what the daily run noticed in the numbers
  (services/insights). Up to three of four slots, from different areas: they
  are where the money is.
* **waiting on you** — real pending work with a count, only when there is
  some (invoices Norm reviewed that still need a person).
* **the catalog** — prompts this person asks often ("regular") or a hired
  team member could do and they've never tried ("discover").

Everything is a local query: no outside system and no model is called, so
Home never waits on it. A suggestion is only offered when it would work for
this person — the member hired, the App on, the permission held, the venue's
connection healthy — because a suggestion that fails is worse than none.

Repeats: an insight subject is shown to a person at most once a week unless
its value moves 25%+; a dismissed one stays away 30 days; what someone said
about it (``insight_notes``) quietens it for everyone in the organisation.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.suggestions.catalog import CATALOG, Entry

#: Which members' new chats show an insight of each area.
AREA_MEMBERS = {
    "sales": ("reports",),
    "wages": ("reports", "time_attendance"),
    "cogs": ("procurement", "reports"),
    "data": (),  # Home only
}
#: The member whose icon an insight carries.
AREA_ICON = {
    "sales": "reports",
    "wages": "time_attendance",
    "cogs": "procurement",
    "data": "norm",
}

#: How much an insight's dollars count towards its rank: what a person can
#: act on (wages, discounts, supplier prices, next week's roster) before
#: news (sales up on last year). A finding without dollars gets a fixed lift.
INSIGHT_WEIGHT = {
    "follow_up": 1.3,  # did the change they made work?
    "roster_ahead": 1.25,
    "quiet_day_wages": 1.0,
    "quiet_hours": 1.1,  # names the hours, so the easiest to act on
    "over_roster": 1.0,
    "wage_rate_up": 1.0,
    "staff_over_roster": 0.9,
    "discount": 1.0,
    "cogs_trend": 1.1,
    "price_rise": 1.4,
    "group_price_gap": 1.4,
    "unusual_day": 0.6,
    "average_order": 0.35,
    "sales_trend": 0.3,
}
NO_VALUE_LIFT = {
    "budgets_out_of_date": 0.9,
    "data_pay_rates": 1.0,
    "data_no_labour": 1.0,
    "data_no_clock_ins": 0.6,
    "data_budgets": 0.5,
}

REPEAT_DAYS = 7
DISMISS_DAYS = 30
EXPECTED_DAYS = 60  # an "expected" note with no end date
ACTING_DAYS = 28


def _now() -> datetime:
    return datetime.now(timezone.utc)


def permissions_of(db: Session, user) -> set[str]:
    """The same answer as GET /auth/me."""
    from app.auth.permissions import ALL_ORG_PERMISSIONS
    from app.db.models import OrganizationMembership, Role

    if getattr(user, "role", None) == "admin":
        return set(ALL_ORG_PERMISSIONS)
    m = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if m and m.role_id:
        role = db.query(Role).filter(Role.id == m.role_id).first()
        return set((role.permissions or []) if role else [])
    return set()


class Context:
    """What one request needs to judge every candidate."""

    def __init__(
        self,
        db: Session,
        config_db: Session,
        user,
        venue_id: str | None,
        member: str | None,
        now: datetime | None = None,
    ):
        from app.services import entitlements
        from app.services.venue_service import get_user_venues

        self.db, self.config_db, self.user = db, config_db, user
        self.now = now or _now()
        self.member = member if member and member != "home" else None
        self.org_id = entitlements.org_id_for_user(user.id, db)
        self.permissions = permissions_of(db, user)
        self.venues = list(get_user_venues(db, user.id) or [])
        self.venue = next((v for v in self.venues if v.id == venue_id), None) or (
            self.venues[0] if self.venues else None
        )
        hired = (
            entitlements.hired_agent_slugs(self.org_id, db, config_db)
            if self.org_id
            else None
        )
        self.hired = (
            None
            if hired is None
            else set(hired) | set(entitlements.ALWAYS_INCLUDED_AGENTS)
        )
        apps = entitlements.apps_on(self.org_id, db, config_db) if self.org_id else None
        self.apps = None if apps is None else set(apps)
        self.tz = (
            ZoneInfo(self.venue.timezone)
            if self.venue and self.venue.timezone
            else timezone.utc
        )
        self.local = self.now.astimezone(self.tz)
        self.used_tools = self._used_tools()
        self.events = self._events()
        self.notes = self._notes()

    # ---- lookups
    def has_member(self, member: str) -> bool:
        return member == "norm" or self.hired is None or member in self.hired

    def has_app(self, app: str) -> bool:
        return app == "norm-core" or self.apps is None or app in self.apps

    def connection_ok(self, connector: str | None, venue) -> bool:
        if not connector:
            return True
        from app.db.models import Connection

        rows = (
            self.db.query(Connection)
            .filter(
                Connection.connector_name == connector, Connection.enabled == "true"
            )
            .filter(
                (Connection.venue_id == (venue.id if venue else None))
                | Connection.venue_id.is_(None)
            )
            .all()
        )
        return any(not getattr(r, "needs_reconnect", False) for r in rows)

    def _used_tools(self) -> set[str]:
        from app.db.models import Thread, ToolCall

        since = self.now - timedelta(days=30)
        rows = (
            self.db.query(ToolCall.connector_name, ToolCall.action)
            .join(Thread, Thread.id == ToolCall.thread_id)
            .filter(Thread.user_id == self.user.id, ToolCall.created_at >= since)
            .distinct()
            .all()
        )
        return {f"{c}.{a}" for c, a in rows if c and a}

    def _events(self) -> list:
        from app.db.models import SuggestionEvent

        since = self.now - timedelta(days=DISMISS_DAYS)
        return (
            self.db.query(SuggestionEvent)
            .filter(
                SuggestionEvent.user_id == self.user.id,
                SuggestionEvent.created_at >= since,
            )
            .all()
        )

    def _notes(self) -> list:
        from app.db.models import InsightNote

        if not self.org_id:
            return []
        since = self.now - timedelta(days=max(EXPECTED_DAYS, ACTING_DAYS) + 365)
        return (
            self.db.query(InsightNote)
            .filter(
                InsightNote.organization_id == self.org_id,
                InsightNote.created_at >= since,
            )
            .all()
        )


# ------------------------------------------------------------------ insights
def _note_quietens(note, insight, ctx: Context) -> bool:
    today = ctx.local.date().isoformat()
    made = note.created_at or ctx.now
    if made.tzinfo is None:
        made = made.replace(tzinfo=timezone.utc)
    age = (ctx.now - made).days
    if note.verdict == "not_useful":
        return note.created_by == ctx.user.id or note.created_by is None
    if note.verdict == "acting":
        return age <= ACTING_DAYS if not note.until else today <= note.until
    if note.verdict == "expected":
        still = today <= note.until if note.until else age <= EXPECTED_DAYS
        if not still:
            return False
        base = float(note.baseline or 0)
        # Explained — speak again only if it has moved well past the level
        # that was explained.
        return not (base > 0 and float(insight.value or 0) >= base * 1.5)
    return False


def insight_candidates(ctx: Context) -> list[dict]:
    from app.db.models import Insight

    if not ctx.org_id or not ctx.venues:
        return []
    venue_ids = {v.id for v in ctx.venues}
    rows = (
        ctx.db.query(Insight)
        .filter(
            Insight.organization_id == ctx.org_id,
            Insight.venue_id.in_(venue_ids),
            Insight.expires_at > ctx.now,
        )
        .all()
    )
    names = {v.id: v.name for v in ctx.venues}
    out = []
    for r in rows:
        if r.audience not in ctx.permissions:
            continue
        if ctx.member and ctx.member not in AREA_MEMBERS.get(r.area, ()):
            continue
        if r.detector.startswith("not_useful"):
            continue
        notes = [
            n
            for n in ctx.notes
            if n.subject_key == r.subject_key
            or (n.verdict == "not_useful" and n.detector == r.detector)
        ]
        if any(_note_quietens(n, r, ctx) for n in notes):
            continue
        mine = [e for e in ctx.events if e.subject_key == r.subject_key]
        if any(e.event == "dismissed" for e in mine):
            continue
        shown = [
            e
            for e in mine
            if e.event in ("shown", "clicked")
            and (ctx.now - _aware(e.created_at)).days < REPEAT_DAYS
        ]
        if shown:
            last = float(max(shown, key=lambda e: _aware(e.created_at)).value or 0)
            value = float(r.value or 0)
            if not last or abs(value - last) / last < 0.25:
                # Shown this week already: keep it only on the day it was
                # first shown, so the list doesn't change under the person.
                first = min(_aware(e.created_at) for e in shown)
                if first.astimezone(ctx.tz).date() != ctx.local.date():
                    continue
        value = float(r.value or 0)
        if value > 0:
            score = (
                3.0 + INSIGHT_WEIGHT.get(r.detector, 1.0) * math.log10(1 + value) / 1.5
            )
        else:
            score = 3.0 + NO_VALUE_LIFT.get(r.detector, 0.5)
        # The venue they are working in first; another of theirs only when it
        # is much bigger news.
        score += 1.5 if ctx.venue and r.venue_id == ctx.venue.id else 0
        out.append(
            {
                "id": f"insight:{r.id}",
                "kind": "insight",
                "text": r.headline,
                "prompt": r.prompt,
                "member": AREA_ICON.get(r.area, "norm"),
                "venue_id": r.venue_id,
                "venue_name": names.get(r.venue_id),
                "subject_key": r.subject_key,
                "value": value,
                "area": r.area,
                "score": score,
                "reason": "Something Norm noticed in this venue's numbers",
            }
        )
    return out


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ waiting on you
def pending_candidates(ctx: Context) -> list[dict]:
    """Invoices Norm reviewed that still need a person — only when there are
    some, and only for someone who can act on them."""
    from app.db.models import WorkingDocument

    if not ctx.venue or "orders:read" not in ctx.permissions:
        return []
    if not (ctx.has_member("procurement") and ctx.has_app("loaded-stock")):
        return []
    if ctx.member and ctx.member != "procurement":
        return []
    since = ctx.now - timedelta(days=21)
    docs = (
        ctx.db.query(WorkingDocument.data)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == ctx.venue.id,
            WorkingDocument.updated_at >= since,
        )
        .all()
    )
    n = 0
    for (data,) in docs:
        d = data or {}
        if (
            d.get("status") in ("received", "deleted")
            or d.get("is_received")
            or d.get("is_deleted")
        ):
            continue
        if d.get("confidence") == "needs_review":
            n += 1
    if not n:
        return []
    text = f"Review the {n} invoice{'s' if n > 1 else ''} waiting at {ctx.venue.name}"
    return [
        {
            "id": "pending:invoices",
            "kind": "pending",
            "text": text,
            "prompt": text,
            "member": "procurement",
            "venue_id": ctx.venue.id,
            "venue_name": ctx.venue.name,
            "score": 2.5 + min(n, 10) / 5,
            "reason": f"{n} invoices need a person",
        }
    ]


# ------------------------------------------------------------------ the catalog
def _timely(e: Entry, ctx: Context) -> bool | None:
    """True when the entry is for today, None when it has no timing, False
    when it is for another time."""
    local = ctx.local
    ok = None
    if e.days is not None:
        ok = local.weekday() in e.days
    if e.month_start:
        ok = (ok is not False) and local.day <= 7
    if e.part:
        h = local.hour
        part_ok = {
            "morning": 5 <= h < 12,
            "afternoon": 12 <= h < 18,
            "evening": h >= 17,
        }[e.part]
        ok = part_ok if ok is None else (ok and part_ok)
    return ok


def catalog_candidates(ctx: Context) -> list[dict]:
    out = []
    shown_counts: dict[str, int] = {}
    clicked: set[str] = set()
    dismissed: set[str] = set()
    for ev in ctx.events:
        if ev.event == "shown" and (ctx.now - _aware(ev.created_at)).days < 14:
            shown_counts[ev.suggestion_id] = shown_counts.get(ev.suggestion_id, 0) + 1
        elif ev.event == "clicked":
            clicked.add(ev.suggestion_id)
        elif ev.event == "dismissed":
            dismissed.add(ev.suggestion_id)
    for e in CATALOG:
        sid = f"catalog:{e.id}"
        if sid in dismissed:
            continue
        if ctx.member and e.member != ctx.member:
            continue
        if not ctx.has_member(e.member) or not ctx.has_app(e.app):
            continue
        if e.permission and e.permission not in ctx.permissions:
            continue
        if e.multi_venue and len(ctx.venues) < 2:
            continue
        if "{venue}" in e.text and not ctx.venue:
            continue
        if not ctx.connection_ok(e.connection, ctx.venue):
            continue
        timely = _timely(e, ctx)
        if timely is False:
            continue
        used = bool(set(e.tools) & ctx.used_tools)
        score = e.weight * (
            1.6
            if used and e.kind == "regular"
            else 1.4
            if not used and e.kind == "discover"
            else 1.0
        )
        if timely:
            score += 0.4
        if sid in clicked:
            score += 0.2
        if shown_counts.get(sid, 0) >= 3 and sid not in clicked:
            score *= 0.5
        text = e.text.replace("{venue}", ctx.venue.name if ctx.venue else "")
        out.append(
            {
                "id": sid,
                "kind": "regular" if used else e.kind,
                "text": text,
                "prompt": text,
                "member": e.member,
                "venue_id": ctx.venue.id if ctx.venue else None,
                "venue_name": ctx.venue.name if ctx.venue else None,
                "score": score,
                "reason": "You ask this often"
                if used
                else "Something your team can do"
                if e.kind == "discover"
                else "Timely",
            }
        )
    return out


# ------------------------------------------------------------------ choosing
def _jitter(ctx: Context, sid: str) -> float:
    """Stable for a person on a day, so the list doesn't reshuffle on every
    visit."""
    h = hashlib.sha1(f"{ctx.user.id}:{ctx.local.date()}:{sid}".encode()).hexdigest()
    return int(h[:4], 16) / 65535 * 0.3


def choose(ctx: Context, limit: int = 4) -> list[dict]:
    insights = sorted(
        insight_candidates(ctx), key=lambda c: -(c["score"] + _jitter(ctx, c["id"]))
    )
    pending = pending_candidates(ctx)
    catalog = sorted(
        catalog_candidates(ctx), key=lambda c: -(c["score"] + _jitter(ctx, c["id"]))
    )
    if pending:  # the waiting invoices say it better than the general prompt
        catalog = [
            c for c in catalog if c["id"] != "catalog:procurement.receive_invoices"
        ]
    picked: list[dict] = []
    # Insights first, where the money is: up to three of four slots (two of
    # three), from different areas while there is a choice.
    most = 3 if limit >= 4 else min(2, limit)
    for c in insights:
        if len(picked) >= most:
            break
        if any(p["area"] == c["area"] for p in picked) and len(
            {i["area"] for i in insights}
        ) > len({p["area"] for p in picked}):
            continue
        picked.append(c)
    for c in pending[:1]:
        if len(picked) < limit:
            picked.append(c)
    members = {p["member"] for p in picked}
    for c in catalog:
        if len(picked) >= limit:
            break
        if c["member"] in members:
            continue
        picked.append(c)
        members.add(c["member"])
    for c in catalog:  # fill if every member is taken
        if len(picked) >= limit:
            break
        if c not in picked:
            picked.append(c)
    keep = (
        "id",
        "kind",
        "text",
        "prompt",
        "member",
        "venue_id",
        "venue_name",
        "reason",
    )
    return [{k: c.get(k) for k in keep} for c in picked[:limit]]


def suggestions_for(
    db: Session,
    config_db: Session,
    user,
    member: str | None = None,
    venue_id: str | None = None,
    limit: int = 4,
    now: datetime | None = None,
) -> list[dict]:
    ctx = Context(db, config_db, user, venue_id, member, now)
    return choose(ctx, max(1, min(limit, 4)))
