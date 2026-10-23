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

Every number above, the prompts, and which detectors count for which team
member are tuned site-wide on Settings → AI team → Suggestions; this reads
them from ``suggestions/config.py`` (defaults in code, overrides in the
config DB). ``explain`` says why each candidate was picked or not, for that
page's preview.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.suggestions import config as suggestion_config
from app.services.suggestions.catalog import Entry
from app.services import active_org

#: The member whose icon an insight carries.
AREA_ICON = {
    "sales": "reports",
    "wages": "time_attendance",
    "cogs": "procurement",
    "data": "norm",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def permissions_of(db: Session, user) -> set[str]:
    """The same answer as GET /auth/me."""
    from app.auth.permissions import ALL_ORG_PERMISSIONS
    from app.db.models import Role

    if getattr(user, "role", None) == "admin":
        return set(ALL_ORG_PERMISSIONS)
    m = active_org.membership_for(db, user.id)
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
        self.cfg = suggestion_config.current(config_db)
        self.settings = self.cfg.settings
        self.now = now or _now()
        self.member = member if member and member != "home" else None
        self.org_id = entitlements.org_id_for_user(user.id, db)
        self.permissions = permissions_of(db, user)
        # Trading venues only: nothing to suggest about a head office venue.
        self.venues = [v for v in (get_user_venues(db, user.id) or []) if v.is_trading]
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

        from app.services import connection_scope

        q = self.db.query(Connection).filter(
            Connection.connector_name == connector, Connection.enabled == "true"
        )
        own = q.filter(Connection.venue_id == venue.id).all() if venue else []
        # else the organisation's own organisation-wide row, never another's
        wide = connection_scope.org_wide(
            q,
            connection_scope.org_for(self.db, venue.id if venue else None, self.org_id),
        ).all()
        rows = own + wide
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

        since = self.now - timedelta(days=self.settings["dismiss_days"])
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
        since = self.now - timedelta(
            days=max(self.settings["expected_days"], self.settings["acting_days"]) + 365
        )
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
    s = ctx.settings
    if note.verdict == "acting":
        return age <= s["acting_days"] if not note.until else today <= note.until
    if note.verdict == "expected":
        still = today <= note.until if note.until else age <= s["expected_days"]
        if not still:
            return False
        base = float(note.baseline or 0)
        # Explained — speak again only if it has moved well past the level
        # that was explained.
        return not (
            base > 0 and float(insight.value or 0) >= base * s["expected_growth"]
        )
    return False


def _drop(log, kind, sid, member, text, reason, venue=None):
    if log is not None:
        log.append(
            {
                "id": sid,
                "kind": kind,
                "member": member,
                "text": text,
                "venue_name": venue,
                "reason": reason,
            }
        )


def insight_candidates(ctx: Context, log: list | None = None) -> list[dict]:
    from app.db.models import Insight

    s = ctx.settings
    if not ctx.org_id or not ctx.venues:
        return []
    if not s.get("insights_enabled", True):
        _drop(
            log,
            "insight",
            "insight:*",
            "norm",
            "Every insight",
            "Insights are switched off",
        )
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
        sid, member = f"insight:{r.id}", AREA_ICON.get(r.area, "norm")
        where = names.get(r.venue_id)

        def drop(reason, sid=sid, member=member, r=r, where=where):
            _drop(log, "insight", sid, member, r.prompt, reason, where)

        det = ctx.cfg.detector(r.detector)
        if not det.get("enabled", True):
            drop("Its detector is switched off")
            continue
        if r.audience not in ctx.permissions:
            drop(f"Needs the {r.audience} permission")
            continue
        if ctx.member and ctx.member not in ctx.cfg.members_for(r.detector, r.area):
            drop(f"Not one of {ctx.member}'s insights")
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
            drop("Someone told Norm about it (a note in What Norm has learned)")
            continue
        mine = [e for e in ctx.events if e.subject_key == r.subject_key]
        if any(e.event == "dismissed" for e in mine):
            drop("They dismissed it")
            continue
        shown = [
            e
            for e in mine
            if e.event in ("shown", "clicked")
            and (ctx.now - _aware(e.created_at)).days < s["repeat_days"]
        ]
        if shown:
            last = float(max(shown, key=lambda e: _aware(e.created_at)).value or 0)
            value = float(r.value or 0)
            if not last or abs(value - last) / last < s["repeat_move"]:
                # Shown this week already: keep it only on the day it was
                # first shown, so the list doesn't change under the person.
                first = min(_aware(e.created_at) for e in shown)
                if first.astimezone(ctx.tz).date() != ctx.local.date():
                    drop(f"Shown in the last {s['repeat_days']} days and hasn't moved")
                    continue
        value = float(r.value or 0)
        if value > 0:
            score = 3.0 + det.get("weight", 1.0) * math.log10(1 + value) / 1.5
        else:
            score = 3.0 + det.get("lift", 0.5)
        # The venue they are working in first; another of theirs only when it
        # is much bigger news.
        score += s["venue_boost"] if ctx.venue and r.venue_id == ctx.venue.id else 0
        out.append(
            {
                "id": sid,
                "kind": "insight",
                "text": r.headline,
                "prompt": r.prompt,
                "member": member,
                "detector": r.detector,
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
def pending_candidates(ctx: Context, log: list | None = None) -> list[dict]:
    """Invoices Norm reviewed that still need a person — only when there are
    some, and only for someone who can act on them."""
    from app.db.models import WorkingDocument

    if not ctx.settings.get("pending_slot", True):
        _drop(
            log,
            "pending",
            "pending:invoices",
            "procurement",
            "Invoices waiting",
            "The waiting-on-you slot is switched off",
        )
        return []
    if not ctx.venue or "orders:read" not in ctx.permissions:
        return []
    if not (ctx.has_member("procurement") and ctx.has_app("loaded-stock")):
        return []
    if ctx.member and ctx.member != "procurement":
        return []
    since = ctx.now - timedelta(days=21)
    from app.services.received_invoice import card_is_open

    docs = (
        ctx.db.query(WorkingDocument.data)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == ctx.venue.id,
            WorkingDocument.updated_at >= since,
            card_is_open(),
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


def catalog_candidates(ctx: Context, log: list | None = None) -> list[dict]:
    s = ctx.settings
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
    for e in ctx.cfg.prompts:
        sid = f"catalog:{e.id}"

        def drop(reason, sid=sid, e=e):
            _drop(log, "catalog", sid, e.member, e.text, reason)

        if ctx.member and e.member != ctx.member:
            continue
        if sid in dismissed:
            drop("They dismissed it")
            continue
        if not ctx.has_member(e.member):
            drop(f"{e.member} isn't on the team")
            continue
        if not ctx.has_app(e.app):
            drop(f"The {e.app} App is off")
            continue
        if e.permission and e.permission not in ctx.permissions:
            drop(f"Needs the {e.permission} permission")
            continue
        if e.multi_venue and len(ctx.venues) < 2:
            drop("Needs two or more venues")
            continue
        if "{venue}" in e.text and not ctx.venue:
            drop("Needs a venue")
            continue
        if not ctx.connection_ok(e.connection, ctx.venue):
            drop(f"The {e.connection} connection isn't working for this venue")
            continue
        timely = _timely(e, ctx)
        if timely is False:
            drop("Not its day or time")
            continue
        used = bool(set(e.tools) & ctx.used_tools)
        score = e.weight * (
            s["regular_boost"]
            if used and e.kind == "regular"
            else s["discover_boost"]
            if not used and e.kind == "discover"
            else 1.0
        )
        if timely:
            score += s["timely_bonus"]
        if sid in clicked:
            score += s["clicked_bonus"]
        if shown_counts.get(sid, 0) >= s["ignored_after"] and sid not in clicked:
            score *= s["ignored_factor"]
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


def choose(
    ctx: Context, limit: int = 4, log: list | None = None, full: bool = False
) -> list[dict]:
    """The suggestions to show. ``log`` collects why candidates were dropped
    (explain); ``full`` keeps the scores on what is returned."""
    s = ctx.settings
    insights = sorted(
        insight_candidates(ctx, log),
        key=lambda c: -(c["score"] + _jitter(ctx, c["id"])),
    )
    pending = pending_candidates(ctx, log)
    catalog = sorted(
        catalog_candidates(ctx, log),
        key=lambda c: -(c["score"] + _jitter(ctx, c["id"])),
    )
    if pending:  # the waiting invoices say it better than the general prompt
        catalog = [
            c for c in catalog if c["id"] != "catalog:procurement.receive_invoices"
        ]
    picked: list[dict] = []
    # Insights first, where the money is: up to three of four slots (two of
    # three), from different areas while there is a choice.
    most = (
        min(int(s["insight_slots_desktop"]), limit)
        if limit >= 4
        else min(int(s["insight_slots_phone"]), limit)
    )
    for c in insights:
        if len(picked) >= most:
            break
        if (
            s.get("area_diversity", True)
            and any(p["area"] == c["area"] for p in picked)
            and len({i["area"] for i in insights}) > len({p["area"] for p in picked})
        ):
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
    if full:
        return picked[:limit]
    return [{k: c.get(k) for k in keep} for c in picked[:limit]]


def explain(ctx: Context, limit: int = 4) -> dict:
    """What this person would see now, and why every other candidate wasn't
    picked — the preview on Settings → AI team → Suggestions."""
    log: list[dict] = []
    picked = choose(ctx, limit, log, full=True)
    ids = {p["id"] for p in picked}
    s = ctx.settings
    shown_areas = {p.get("area") for p in picked if p["kind"] == "insight"}
    shown_members = {p["member"] for p in picked if p["kind"] != "insight"}
    insight_slots = (
        min(int(s["insight_slots_desktop"]), limit)
        if limit >= 4
        else min(int(s["insight_slots_phone"]), limit)
    )

    def why(c) -> str:
        if c["kind"] == "insight":
            if s.get("area_diversity", True) and c.get("area") in shown_areas:
                return f"A {c.get('area')} insight already took a slot (one per area first)"
            if sum(p["kind"] == "insight" for p in picked) >= insight_slots:
                return "The insight slots were full (higher scores first, with a little daily variety)"
            return "Ranked below the ones shown"
        if c["member"] in shown_members:
            return f"A {c['member']} prompt was already shown (one per member first)"
        return (
            "The slots were full"
            if len(picked) >= limit
            else "Ranked below the ones shown"
        )

    others = []
    for c in (
        insight_candidates(ctx) + pending_candidates(ctx) + catalog_candidates(ctx)
    ):
        if c["id"] in ids:
            continue
        others.append(
            {
                **{
                    k: c.get(k)
                    for k in ("id", "kind", "member", "prompt", "venue_name")
                },
                "text": c.get("prompt") or c.get("text"),
                "score": round(c["score"], 2),
                "reason": why(c),
            }
        )
    dropped = [{**d, "score": None} for d in log]
    return {
        "picked": [
            {
                **{
                    k: p.get(k)
                    for k in ("id", "kind", "member", "venue_name", "reason")
                },
                "text": p.get("prompt") or p.get("text"),
                "score": round(p["score"], 2),
            }
            for p in picked
        ],
        "others": sorted(others, key=lambda c: -c["score"]) + dropped,
    }


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
