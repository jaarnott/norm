"""The daily insight run: once per venue per local day.

Every tick of the existing 5-minute ``/internal/run-due-tasks`` job calls
:func:`run_due_insights` (on a background thread, as the app schedules do).
A venue is due once its trading day has closed (its day start + 30 minutes,
in its own timezone) and today's run hasn't been claimed. The claim is an
``insight_runs`` row unique on (venue, local date), inserted and committed
BEFORE any work, so a second API instance — or the next tick — moves on.

What runs depends on the day (the research found most money insights are
weekly patterns, docs/plans/insights-research.md):

* every day — yesterday against a usual day of the same weekday;
* Monday — the weekly set: sales, discounts, budgets, wages, cost of goods,
  supplier prices, and the group's price gaps between sister venues;
* Thursday to Sunday — next week's roster against the sales it should bring.

Everything is read through Norm's own tools (``execute_connector_tool``,
scoped to the venue's organisation), with explicit trading-day windows so no
model is asked to work out "last week". No model calls at all: the wording is
templated in the detectors.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.services.insights import detectors as det
from app.services.insights import wording
from app.services.insights.rules import render, threshold
from app.services.suggestions import config as suggestion_config

logger = logging.getLogger(__name__)

#: Minutes after the trading day closes before the run starts.
SETTLE_MINUTES = 30
#: Items whose receipt history is read for price rises, by spend.
HISTORY_ITEMS = 15
ROSTER_WEEKDAYS = {3, 4, 5, 6}  # Thu–Sun: next week's roster


def _hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(value).split(":")[:2])
        return h, m
    except ValueError:
        return 7, 0


class VenueClock:
    """A venue's local clock and trading-day windows."""

    def __init__(self, venue):
        from app.services.business_calendar import day_start_for, timezone_for

        self.tz = timezone_for(venue)
        self.day_start = _hhmm(day_start_for(venue))

    def local_now(self, now: datetime) -> datetime:
        return now.astimezone(self.tz)

    def due(self, now: datetime) -> date | None:
        """Today's local date once the run's time has passed, else None."""
        local = self.local_now(now)
        h, m = self.day_start
        ready = datetime.combine(local.date(), time(h, m), tzinfo=self.tz) + timedelta(
            minutes=SETTLE_MINUTES
        )
        return local.date() if local >= ready else None

    def at(self, d: date) -> str:
        h, m = self.day_start
        return datetime.combine(d, time(h, m), tzinfo=self.tz).isoformat()

    def window(self, first: date, last_excl: date) -> dict:
        return {
            "start": self.at(first),
            "end": self.at(last_excl),
            "confirmed_by_user": True,
        }


def cadences_for(as_of: date) -> list[str]:
    out = ["daily"]
    if as_of.weekday() == 0:
        out.append("weekly")
    if as_of.weekday() in ROSTER_WEEKDAYS:
        out.append("roster")
    return out


# ------------------------------------------------------------------ fetching
class Fetcher:
    """Runs one venue's tool reads; a failed read leaves its data empty and
    is noted, so one bad read never stops the other detectors."""

    def __init__(self, db, config_db, venue):
        self.db, self.config_db, self.venue = db, config_db, venue
        self.errors: list[str] = []
        self.calls = 0

    def payload(self, action: str, params: dict):
        from app.connectors.tool_executor import execute_connector_tool

        self.calls += 1
        try:
            res = execute_connector_tool(
                connector_name="loadedhub",
                action=action,
                params={"venues": [self.venue.name], **params},
                db=self.db,
                config_db=self.config_db,
                venue_id=self.venue.id,
                strict_venue=True,
            )
        except Exception as exc:  # noqa: BLE001 — recorded, not fatal
            self.errors.append(f"{action}: {exc}"[:300])
            return None
        if not res.success:
            self.errors.append(f"{action}: {res.error}"[:300])
            return None
        data = res.payload
        if isinstance(data, dict) and data.get("error"):
            self.errors.append(f"{action}: {data['error']}"[:300])
            return None
        return data

    def rows(self, action: str, params: dict) -> list[dict]:
        data = self.payload(action, params)
        rows = data.get("rows") if isinstance(data, dict) else data
        if isinstance(data, dict) and "rows" not in data and "clockins" in data:
            # get_labour's detail view: the clock-ins, or the roster where
            # the venue doesn't clock in.
            rows = data.get("clockins") or data.get("rostered")
        return [r for r in (rows or []) if isinstance(r, dict)]


def fetch(db, config_db, venue, as_of: date, cadences: list[str], siblings: set[str]):
    clock = VenueClock(venue)
    f = Fetcher(db, config_db, venue)
    v = det.VenueData(
        venue_id=venue.id,
        venue_name=venue.name,
        as_of=as_of,
        internal_suppliers=siblings,
        day_start_hour=clock.day_start[0],
        rules=suggestion_config.current(config_db).rules,
    )
    monday = det.week_start(as_of)
    weekly = "weekly" in cadences
    # Sales: 9 weeks on a Monday (8 complete weeks + this one), else 6 weeks of
    # the same weekday for "usual"; plus the week ahead's wages on roster days.
    days_back = 63 if weekly else (63 if "roster" in cadences else 43)
    sales_window = clock.window(as_of - timedelta(days=days_back), as_of)
    v.sales_daily = f.rows(
        "get_sales",
        {
            **sales_window,
            "breakdown": "daily",
            "compare": ["last_year"] if weekly else [],
            "tax": "exclude",
        },
    )
    if weekly or "roster" in cadences:
        v.labour_daily = f.rows(
            "get_labour",
            {
                **clock.window(as_of - timedelta(days=63), as_of),
                "view": "attendance",
                "group_by": "day",
                "include": "both",
            },
        )
    if weekly:
        v.sales_daily_incl = f.rows(
            "get_sales",
            {
                **clock.window(monday - timedelta(weeks=8), monday),
                "breakdown": "daily",
                "compare": ["budget"],
                "tax": "include",
            },
        )
        v.orders_daily = f.rows(
            "get_sales",
            {
                **clock.window(monday - timedelta(weeks=8), monday),
                "breakdown": "daily",
                "measure": "orders",
                "tax": "exclude",
            },
        )
        v.labour_staff = f.rows(
            "get_labour",
            {
                **clock.window(monday - timedelta(weeks=4), monday),
                "view": "attendance",
                "group_by": "staff",
                "include": "both",
            },
        )
        v.roster_next = f.rows(
            "get_labour",
            {
                **clock.window(monday, monday + timedelta(days=7)),
                "view": "attendance",
                "group_by": "day",
                "include": "rostered",
            },
        )
        # Hour by hour, the last 4 complete weeks: who was on against the sales.
        v.shifts = f.rows(
            "get_labour",
            {
                **clock.window(monday - timedelta(weeks=4), monday),
                "view": "attendance",
                "group_by": "detail",
            },
        )
        v.sales_hourly = f.rows(
            "get_sales",
            {
                **clock.window(monday - timedelta(weeks=4), monday),
                "breakdown": "daily",
                "interval": "01:00:00",
                "tax": "exclude",
            },
        )
        v.pcogs_weekly = f.rows(
            "get_purchase_cogs",
            {
                **clock.window(monday - timedelta(weeks=13), monday),
                "interval": "week",
                "compare_previous": False,
            },
        )
        v.discounts_recent = f.rows(
            "get_sales",
            {
                **clock.window(as_of - timedelta(days=28), as_of),
                "breakdown": "discounts",
                "tax": "exclude",
            },
        )
        v.discounts_prior = f.rows(
            "get_sales",
            {
                **clock.window(as_of - timedelta(days=56), as_of - timedelta(days=28)),
                "breakdown": "discounts",
                "tax": "exclude",
            },
        )
        v.item_supplier = f.rows(
            "get_received_items_for_period",
            {
                **clock.window(as_of - timedelta(weeks=13), as_of),
                "group_by": "item_supplier",
                "limit": 150,
            },
        )
        varied = [
            r
            for r in sorted(v.item_supplier, key=lambda r: -float(r.get("spend") or 0))
            if r.get("item_id") and r.get("unit_cost_min") != r.get("unit_cost_max")
        ]
        for r in varied[:HISTORY_ITEMS]:
            v.histories[r["item_id"]] = f.rows(
                "get_received_items_for_period",
                {
                    **clock.window(as_of - timedelta(weeks=13), as_of),
                    "group_by": "history",
                    "item_id": r["item_id"],
                    "limit": 200,
                },
            )
    if "roster" in cadences:
        nxt = monday + timedelta(days=7)
        v.roster_next = f.rows(
            "get_labour",
            {
                **clock.window(nxt, nxt + timedelta(days=7)),
                "view": "attendance",
                "group_by": "day",
                "include": "rostered",
            },
        )
    return v, f


def detect(v: det.VenueData, cadences: list[str], cfg=None) -> list[det.Finding]:
    """Every finding of today's sets, less those of detectors an admin turned
    off (Settings → AI team → Suggestions)."""
    sets = []
    if "daily" in cadences:
        sets += det.DAILY
    if "weekly" in cadences:
        sets += det.WEEKLY
    if "roster" in cadences:
        sets += det.ROSTER
    out: list[det.Finding] = []
    for fn in sets:
        try:
            out.extend(fn(v))
        except Exception:  # noqa: BLE001 — one rule never stops the rest
            logger.exception(
                "insight detector %s failed for %s", fn.__name__, v.venue_name
            )
    return switched_on(out, cfg)


def switched_on(findings: list[det.Finding], cfg=None) -> list[det.Finding]:
    if cfg is None:
        return findings
    return [f for f in findings if cfg.detector(f.detector).get("enabled", True)]


# ------------------------------------------------------------------ saving
def subject_key(venue_id: str, detector: str, subject: str) -> str:
    return f"{venue_id}:{detector}:{subject}"[:500]


def save(
    db,
    org_id: str,
    venue_id: str,
    as_of: date,
    findings: list[det.Finding],
    now: datetime,
    wording_version=None,
) -> int:
    from app.db.models import Insight

    version = wording.VERSION if wording_version is None else wording_version
    saved = 0
    for f in findings:
        key = subject_key(venue_id, f.detector, f.subject)
        row = db.query(Insight).filter(Insight.subject_key == key).first()
        if row is None:
            row = Insight(
                organization_id=org_id,
                venue_id=venue_id,
                subject_key=key,
                detector=f.detector,
                subject=f.subject[:500],
                first_seen_at=now,
            )
            db.add(row)
        row.area, row.audience = f.area, f.audience
        kept = (row.data or {}).get("wording") or {}
        row.headline, row.data = f.headline, dict(f.data)
        if kept.get("template") == f.prompt and kept.get("v") == version:
            # The same words as last run: keep their rewording (wording.py).
            row.prompt = kept.get("prompt") or f.prompt
            row.data["wording"] = kept
        else:
            row.prompt = f.prompt
        row.value = round(float(f.value), 2)
        row.as_of = (as_of - timedelta(days=1)).isoformat()
        row.updated_at = now
        row.expires_at = now + timedelta(days=f.lasts_days, hours=6)
        saved += 1
    return saved


def _store_item_supplier(run, v: det.VenueData) -> None:
    """Keep the week's per-item prices on the run, so a sister venue's run
    can compare prices across the group without reading them again."""
    keep = (
        "item_name",
        "base_unit",
        "supplier_name",
        "unit_cost_last",
        "invoice_count",
        "quantity_base",
    )
    run.summary = {
        **(run.summary or {}),
        "item_supplier": [{k: r.get(k) for k in keep} for r in v.item_supplier],
    }


def _group_gaps(db, org_id: str, now: datetime, cfg=None) -> int:
    """Price gaps across the org's venues, from each venue's latest weekly run
    (within 8 days)."""
    from app.db.models import InsightRun, Venue

    # Trading venues only: a head office venue (is_trading = false) buys
    # nothing to compare prices on (Oct 2026).
    venues = (
        db.query(Venue)
        .filter(Venue.organization_id == org_id, Venue.is_trading.is_(True))
        .all()
    )
    if len(venues) < 2:
        return 0
    names = {v.name for v in venues}
    data = []
    since = now - timedelta(days=8)
    for venue in venues:
        run = (
            db.query(InsightRun)
            .filter(InsightRun.venue_id == venue.id, InsightRun.started_at >= since)
            .order_by(InsightRun.started_at.desc())
            .all()
        )
        rows = next(
            (
                (r.summary or {}).get("item_supplier")
                for r in run
                if (r.summary or {}).get("item_supplier")
            ),
            None,
        )
        if rows:
            data.append(
                det.VenueData(
                    venue_id=venue.id,
                    venue_name=venue.name,
                    as_of=now.date(),
                    item_supplier=rows,
                    internal_suppliers=names,
                    rules=cfg.rules if cfg is not None else None,
                )
            )
    if len(data) < 2:
        return 0
    saved = 0
    for vid, findings in det.group_price_gaps(data).items():
        saved += save(
            db,
            org_id,
            vid,
            now.date(),
            switched_on(findings, cfg),
            now,
            cfg.wording_version if cfg is not None else None,
        )
    return saved


# ------------------------------------------------------------------ follow-ups


def follow_ups(
    db, venue, findings: list[det.Finding], now: datetime, cfg=None
) -> list[det.Finding]:
    """Close the loop on "we'll fix it": three weeks after someone said they
    would act on an insight, say whether it worked — today's value of the
    same subject against the value when they said it. Once per note (the
    follow-up is an insight keyed by the note)."""
    from app.db.models import Insight, InsightNote

    notes = (
        db.query(InsightNote)
        .filter(InsightNote.venue_id == venue.id, InsightNote.verdict == "acting")
        .all()
    )
    now_by_key = {subject_key(venue.id, f.detector, f.subject): f for f in findings}
    out = []
    for n in notes:
        made = (
            n.created_at
            if n.created_at.tzinfo
            else n.created_at.replace(tzinfo=timezone.utc)
        )
        age = (now - made).days
        after_days = threshold(cfg.rules if cfg else None, "follow_up", "after_days")
        if age < after_days or age > after_days + 14:
            continue
        key = subject_key(venue.id, "follow_up", n.id)
        if db.query(Insight).filter(Insight.subject_key == key).first():
            continue  # already followed up
        before = float(n.baseline or 0)
        current = now_by_key.get(n.subject_key)
        after = float(current.value) if current else 0.0
        original = (
            db.query(Insight).filter(Insight.subject_key == n.subject_key).first()
        )
        about = original.headline if original else n.reason or "the insight"
        if before and after <= before * 0.5:
            verdict = "It worked"
            line = f"was about {det.money(before)} a week, now {det.money(after)}"
        elif before and after < before:
            verdict = "Partly there"
            line = f"down from {det.money(before)} to {det.money(after)} a week"
        else:
            verdict = "No change yet"
            line = f"still about {det.money(after or before)} a week"
        facts = {
            "change": n.reason or "the change we made",
            "venue": venue.name,
            "about": about.rstrip("."),
            "result": line,
        }
        out.append(
            det.Finding(
                "follow_up",
                n.id,
                original.area if original else "wages",
                original.audience if original else "reports:read",
                headline=f"{verdict}: {about.rstrip('.')} — {line}",
                prompt=render(cfg.rules if cfg else None, "follow_up", "prompt", facts),
                value=max(before - after, 0.0),
                data={
                    "note_id": n.id,
                    "before": before,
                    "after": after,
                    "facts": facts,
                },
                lasts_days=7,
            )
        )
    return out


# ------------------------------------------------------------------ the run
def run_venue(
    db, config_db, venue, as_of: date, cadences: list[str], now: datetime
) -> dict:
    from app.db.models import Venue

    siblings = {
        v.name
        for v in db.query(Venue)
        .filter(
            Venue.organization_id == venue.organization_id,
            Venue.is_trading.is_(True),
        )
        .all()
    }
    cfg = suggestion_config.current(config_db)
    v, f = fetch(db, config_db, venue, as_of, cadences, siblings)
    findings = detect(v, cadences, cfg)
    if "weekly" in cadences:
        findings += switched_on(follow_ups(db, venue, findings, now, cfg), cfg)
    saved = save(
        db, venue.organization_id, venue.id, as_of, findings, now, cfg.wording_version
    )
    return {
        "venue": venue.name,
        "cadences": cadences,
        "calls": f.calls,
        "found": len(findings),
        "saved": saved,
        "errors": f.errors,
        "_data": v,
    }


def due_venues(
    db, now: datetime, venue_ids: list[str] | None = None, any_time: bool = False
):
    """Venues with a working LoadedHub connection whose run is due today
    (``any_time``: today's local date whatever the hour — an admin's run now)."""
    from app.db.models import Connection, Venue

    q = (
        db.query(Venue)
        .join(Connection, Connection.venue_id == Venue.id)
        .filter(Connection.connector_name == "loadedhub", Connection.enabled == "true")
        # A venue that trades nothing has no sales to find insights in.
        .filter(Venue.is_trading.is_(True))
    )
    if venue_ids:
        q = q.filter(Venue.id.in_(venue_ids))
    out = []
    for venue in q.distinct().all():
        conn = next(
            (
                c
                for c in db.query(Connection)
                .filter(
                    Connection.venue_id == venue.id,
                    Connection.connector_name == "loadedhub",
                )
                .all()
            ),
            None,
        )
        if conn is not None and getattr(conn, "needs_reconnect", False):
            continue
        clock = VenueClock(venue)
        slot = clock.due(now) or (clock.local_now(now).date() if any_time else None)
        if slot:
            out.append((venue, slot))
    return out


def word_pending(db, org_ids: set[str], now: datetime, config_db=None) -> dict:
    """Reword every live insight of these organisations not yet worded under
    the current instructions (wording.py) — after all their venues ran, so a
    sister venue's price gaps are worded too. Never fails the run."""
    from app.db.models import Insight, Venue

    out: dict = {}
    cfg = suggestion_config.current(config_db)
    try:
        rows = (
            db.query(Insight)
            .filter(Insight.organization_id.in_(org_ids), Insight.expires_at > now)
            .all()
        )
        by_venue: dict[str, list] = {}
        for r in rows:
            if wording.needs_wording(r, cfg.wording_version):
                by_venue.setdefault(r.venue_id, []).append(r)
        for vid, pending in by_venue.items():
            venue = db.get(Venue, vid)
            if venue is not None:
                out[venue.name] = wording.word(db, venue, pending, cfg)
                db.commit()
    except Exception as exc:  # noqa: BLE001 — the templates stand
        db.rollback()
        logger.exception("insight wording failed")
        out["error"] = str(exc)[:300]
    return out


def run_due_insights(
    now: datetime | None = None,
    db=None,
    config_db=None,
    venue_ids: list[str] | None = None,
    force_cadences: list[str] | None = None,
    rerun: bool = False,
) -> dict:
    """Run every due venue once. ``force_cadences`` (admin "run now") runs
    those sets regardless of the day or hour; ``rerun`` first clears that
    forced slot's claim so it can run again."""
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import InsightRun

    now = now or datetime.now(timezone.utc)
    own_db, own_cdb = db is None, config_db is None
    db = db or SessionLocal()
    config_db = config_db or _ConfigSessionLocal()
    ran: list[dict] = []
    if not suggestion_config.current(config_db).settings.get("insights_enabled", True):
        # Switched off on Settings → AI team → Suggestions.
        if own_db:
            db.close()
        if own_cdb:
            config_db.close()
        return {"ran": [], "off": True}
    try:
        for venue, slot in due_venues(
            db, now, venue_ids, any_time=bool(force_cadences)
        ):
            cadences = force_cadences or cadences_for(slot)
            slot_key = slot.isoformat() + (
                "+" + "+".join(force_cadences) if force_cadences else ""
            )
            if rerun and force_cadences:
                db.query(InsightRun).filter(
                    InsightRun.venue_id == venue.id, InsightRun.slot == slot_key
                ).delete()
                db.commit()
            claim = InsightRun(
                venue_id=venue.id, slot=slot_key, cadences=cadences, started_at=now
            )
            try:
                with db.begin_nested():
                    db.add(claim)
                    db.flush()
            except IntegrityError:
                continue  # another tick or instance has it
            db.commit()
            entry: dict = {
                "venue": venue.name,
                "slot": claim.slot,
                "cadences": cadences,
                "org_id": venue.organization_id,
            }
            try:
                # A savepoint: a venue that fails leaves none of its partial
                # writes behind, and nothing else in this session is touched.
                with db.begin_nested():
                    out = run_venue(db, config_db, venue, slot, cadences, now)
                v = out.pop("_data")
                claim = db.merge(claim)
                if "weekly" in cadences:
                    _store_item_supplier(claim, v)
                claim.ok = True
                claim.summary = {
                    **(claim.summary or {}),
                    **{k: out[k] for k in ("calls", "found", "saved", "errors")},
                }
                db.commit()
                if "weekly" in cadences:
                    entry["group_saved"] = _group_gaps(
                        db,
                        venue.organization_id,
                        now,
                        suggestion_config.current(config_db),
                    )
                    db.commit()
                entry.update(
                    ok=True,
                    found=out["found"],
                    calls=out["calls"],
                    errors=out["errors"],
                )
            except Exception as exc:  # noqa: BLE001 — one venue never stops the rest
                # The claim was committed before the work; find it again (or,
                # if it is gone, put it back) to record why.
                claim = (
                    db.query(InsightRun)
                    .filter(
                        InsightRun.venue_id == venue.id, InsightRun.slot == slot_key
                    )
                    .first()
                )
                if claim is None:
                    claim = InsightRun(
                        venue_id=venue.id,
                        slot=slot_key,
                        cadences=cadences,
                        started_at=now,
                    )
                    db.add(claim)
                claim.ok = False
                claim.error = str(exc)[:2000]
                logger.exception("insight run failed for %s", venue.name)
                entry.update(ok=False, error=str(exc)[:300])
            claim.finished_at = datetime.now(timezone.utc)
            db.commit()
            ran.append(entry)
        out = {"ran": ran}
        if ran:
            out["wording"] = word_pending(
                db, {e["org_id"] for e in ran}, now, config_db
            )
        for e in ran:
            e.pop("org_id", None)
        return out
    finally:
        if own_db:
            db.close()
        if own_cdb:
            config_db.close()
