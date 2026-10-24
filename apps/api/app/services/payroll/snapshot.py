"""Snapshots: Loaded's pay week, frozen (plan §9 step 1, D24).

``loadedhub.get_payrun`` (F2's consolidator) is THE one shaping of a pay
week; a snapshot is its stored output. ``take`` calls it with ``view=all``
through the connector engine for the entity's own token and stores every row
as a ``pay_shift_facts`` row, hashed, under a ``pay_snapshots`` row that
supersedes the week's previous current snapshot (older ones are kept, so a
close can be diffed against the Monday run — "drift since close").

Two ways into the engine, one seam (``loaded``):

* **A person** (a page, a built-in): ``execute_connector_tool(..., user=…)``
  — the executor checks the row's ``requires`` against THEIR role, as every
  door does.
* **Nobody** (the tick's jobs): the executor refuses a gated row on nobody's
  authority (``caller_scope.missing_scopes`` — pay data never leaves for a
  caller it cannot name), and the Monday snapshot is exactly such a caller.
  So a system ctx runs the same consolidator row, read from the same config
  DB, through ``execute_consolidator`` under the organisation's own scope.
  The data stays inside Norm (facts behind ``payroll:read``); nothing is
  shown to anyone here. A platform-level system scope would make this the
  executor's job — see build-notes.md.

Everything else here is arithmetic over stored facts: the diff of two
snapshots by ``source_shift_id``, and the per-staff / security / leave /
salaried / intercompany sections the Payroll page and ``get_week`` show, which
mirror the consolidator's own sections so the page reads the same whether it
looks at a snapshot or at Loaded live.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Venue, _now
from app.db.payroll_models import (
    COST_GROUPS,
    SNAPSHOT_SOURCES,
    PayEntity,
    PayShiftFact,
    PaySnapshot,
    PayWeek,
)
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import Invalid, NotFound, flush
from app.services.payroll import entities as entities_svc
from app.services.payroll import weeks as weeks_svc

logger = logging.getLogger(__name__)

#: The fields a diff compares, in the order they are reported.
DIFF_FIELDS = (
    "staff_member_id",
    "pay_code",
    "shift_type",
    "role_name",
    "worked_venue_loaded_id",
    "clockin_at",
    "clockout_at",
    "hours",
    "cost_cents",
    "ph_hours",
    "ph_cost_cents",
    "leave_type_name",
    "is_approved",
)


# ================================================================ the seam ==


@contextmanager
def _config_session(config_db):
    """The config DB (the ``loadedhub`` spec lives there). A caller with a
    session passes it; a job opens one."""
    if config_db is not None:
        yield config_db
        return
    from app.db.engine import _ConfigSessionLocal

    cdb = _ConfigSessionLocal()
    try:
        yield cdb
    finally:
        cdb.close()


def _payload(result) -> dict:
    """The data of a tool result, or ``Invalid`` with Loaded's message."""
    success = getattr(result, "success", None)
    payload = getattr(result, "payload", None)
    error = getattr(result, "error", None)
    if isinstance(result, dict):
        success, payload, error = (
            result.get("success", True),
            result.get("data"),
            result.get("error"),
        )
    if not success:
        raise Invalid(f"Loaded could not be read: {error or 'unknown error'}")
    if isinstance(payload, dict) and payload.get("error"):
        raise Invalid(f"Loaded could not be read: {payload['error']}")
    # A consolidator answers with a dict; a raw Loaded endpoint such as
    # get_leave_types or get_staff_members answers with a top-level array.
    if not isinstance(payload, (dict, list)):
        raise Invalid("Loaded returned nothing usable.")
    return payload


def loaded(
    db: Session,
    ctx: FinanceCtx,
    venue: Venue,
    action: str,
    params: dict,
    *,
    config_db=None,
) -> dict:
    """Run one ``loadedhub`` tool for a venue's own token and return its data.

    Tests replace this function (``monkeypatch.setattr(snapshot, "loaded",
    …)``) — it is the only place payroll touches the connector engine.
    """
    from app.connectors import spec_rows
    from app.connectors.tool_executor import execute_connector_tool
    from app.db.config_models import ConnectionSpec
    from app.services import caller_scope

    with _config_session(config_db) as cdb:
        if ctx.user is not None:
            return _payload(
                execute_connector_tool(
                    "loadedhub",
                    action,
                    dict(params),
                    db,
                    cdb,
                    venue_id=venue.id,
                    strict_venue=True,
                    user=ctx.user,
                )
            )
        spec = (
            cdb.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        row = next((r for r in spec_rows.rows(spec) if r.get("action") == action), None)
        if row is None:
            raise Invalid(f"loadedhub.{action} is not installed.")
        if not caller_scope.required_scopes(row):
            return _payload(
                execute_connector_tool(
                    "loadedhub",
                    action,
                    dict(params),
                    db,
                    cdb,
                    venue_id=venue.id,
                    strict_venue=True,
                )
            )
        cfg = row.get("consolidator_config")
        if not isinstance(cfg, dict) or not cfg.get("function_code"):
            raise Invalid(
                f"loadedhub.{action} needs a person to run it (it requires "
                f"{', '.join(caller_scope.required_scopes(row))})."
            )
        from app.agents.internal_tools import execute_consolidator

        call = {**params, "venue_id": venue.id}
        call.setdefault("venue", venue.name)
        with caller_scope.use(caller_scope.Scope(ctx.org_id)):
            return _payload(
                execute_consolidator({**cfg, "action": action}, call, db, None)
            )


def payrun(
    db: Session,
    ctx: FinanceCtx,
    venue: Venue,
    week_start: dt.date,
    *,
    view: str = "all",
    config_db=None,
    **extra,
) -> dict:
    """``loadedhub.get_payrun`` for one entity-week."""
    entity = entities_svc.get_or_create(db, ctx, venue.id)
    params = {
        "week_ending": weeks_svc.week_ending(week_start).isoformat(),
        "view": view,
        "security_pay_code": entity.security_pay_code,
        **extra,
    }
    return loaded(db, ctx, venue, "get_payrun", params, config_db=config_db)


# ================================================================= storing ==


def _instant(text) -> dt.datetime | None:
    if not text:
        return None
    s = str(text).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _date(text) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _hours(value) -> Decimal:
    try:
        return Decimal(str(round(float(value or 0), 2)))
    except (TypeError, ValueError):
        return Decimal("0")


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def row_hash(row: dict) -> str:
    """A stable hash of one consolidator row (breakdown included)."""
    text = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(row_hashes) -> str:
    return hashlib.sha256("\n".join(sorted(row_hashes)).encode("utf-8")).hexdigest()


def loaded_id_map(db: Session, org_id: str) -> dict[str, str]:
    """Loaded company id → Norm venue id, from the entities' stored ids and
    the connections' — Loaded's ``venueId`` on a shift resolves through
    this, never through a name."""
    out: dict[str, str] = {}
    for entity in db.query(PayEntity).filter(PayEntity.organization_id == org_id).all():
        if entity.loaded_company_id:
            out[entity.loaded_company_id] = entity.venue_id
    for venue in db.query(Venue).filter(Venue.organization_id == org_id).all():
        company = entities_svc.loaded_company_id(db, venue.id)
        if company and company not in out:
            out[company] = venue.id
    return out


def take(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    week_start,
    *,
    source: str,
    config_db=None,
    result: dict | None = None,
) -> PaySnapshot:
    """Read the week from Loaded (``get_payrun view=all``) and store it as the
    week's current snapshot. ``result`` lets a caller that already holds the
    consolidator's answer store it (a test, a replay).

    Allowed in shadow: a snapshot is a read of Loaded and a write to Norm's
    own tables, which is exactly what the shadow period exists to do.
    """
    if source not in SNAPSHOT_SOURCES:
        raise Invalid(f"A snapshot's source is one of {', '.join(SNAPSHOT_SOURCES)}.")
    ctx.require("payroll:read")
    venue = weeks_svc.venue_of(db, ctx, venue_id)
    week = weeks_svc.get_or_create(db, ctx, venue.id, week_start)
    if result is None:
        result = payrun(
            db, ctx, venue, week.week_start, view="all", config_db=config_db
        )
    rows = [r for r in (result.get("rows") or []) if isinstance(r, dict)]
    dropped = result.get("dropped") if isinstance(result.get("dropped"), dict) else {}
    venue_ids = loaded_id_map(db, ctx.org_id)
    now = _now()

    hashes = [row_hash(r) for r in rows]
    snapshot = PaySnapshot(
        organization_id=ctx.org_id,
        week_id=week.id,
        taken_at=now,
        taken_by=ctx.user_id,
        source=source,
        shift_count=len(rows),
        dropped_deleted=_int(dropped.get("deleted")),
        dropped_unfinished=_int(dropped.get("unfinished")),
        content_sha256=content_hash(hashes),
    )
    previous = weeks_svc.current_snapshot(db, week)
    if previous is not None:
        previous.superseded_at = now
    db.add(snapshot)
    flush(db)

    seen: set[str] = set()
    facts = []
    for row, digest in zip(rows, hashes):
        sid = str(row.get("source_shift_id") or "")
        if not sid or sid in seen:
            # The consolidator promises one row per Loaded shift; a repeat
            # would break the unique and is dropped rather than stored twice.
            logger.warning(
                "snapshot %s: repeated or blank shift id %r", snapshot.id, sid
            )
            continue
        seen.add(sid)
        cost_group = (
            row.get("cost_group") if row.get("cost_group") in COST_GROUPS else "other"
        )
        loaded_venue = str(row.get("worked_venue_loaded_id") or "") or None
        facts.append(
            PayShiftFact(
                organization_id=ctx.org_id,
                snapshot_id=snapshot.id,
                source_shift_id=sid,
                staff_member_id=str(row.get("staff_member_id") or "") or None,
                staff_name=row.get("staff_name"),
                pay_code=row.get("pay_code") or None,
                shift_type=row.get("shift_type"),
                remuneration_type=row.get("remuneration_type"),
                role_id=row.get("role_id"),
                role_name=row.get("role_name"),
                cost_group=cost_group,
                worked_venue_loaded_id=loaded_venue,
                worked_venue_id=venue_ids.get(loaded_venue) if loaded_venue else None,
                is_from_other_venue=bool(row.get("is_from_other_venue")),
                clockin_at=_instant(row.get("clockin_at")),
                clockout_at=_instant(row.get("clockout_at")),
                trading_date=_date(row.get("trading_date")),
                hours=_hours(row.get("hours")),
                cost_cents=_int(row.get("cost_cents")),
                ph_hours=_hours(row.get("ph_hours")),
                ph_cost_cents=_int(row.get("ph_cost_cents")),
                leave_request_id=row.get("leave_request_id") or None,
                leave_type_name=row.get("leave_type_name") or None,
                is_approved=row.get("is_approved"),
                breakdown=row.get("breakdown")
                if isinstance(row.get("breakdown"), dict)
                else None,
                payload_sha256=digest,
            )
        )
    db.add_all(facts)
    snapshot.shift_count = len(facts)
    flush(db)
    return snapshot


# ================================================================= reading ==


def get(db: Session, ctx: FinanceCtx, snapshot_id: str) -> PaySnapshot:
    snapshot = (
        db.query(PaySnapshot)
        .filter(
            PaySnapshot.organization_id == ctx.org_id, PaySnapshot.id == snapshot_id
        )
        .first()
    )
    if snapshot is None:
        raise NotFound("Snapshot not found.")
    weeks_svc.get(db, ctx, snapshot.week_id)  # the venue check
    return snapshot


def list_for_week(db: Session, week: PayWeek) -> list[PaySnapshot]:
    return (
        db.query(PaySnapshot)
        .filter(
            PaySnapshot.organization_id == week.organization_id,
            PaySnapshot.week_id == week.id,
        )
        .order_by(PaySnapshot.taken_at.desc())
        .all()
    )


def facts_of(db: Session, snapshot: PaySnapshot) -> list[PayShiftFact]:
    return (
        db.query(PayShiftFact)
        .filter(
            PayShiftFact.organization_id == snapshot.organization_id,
            PayShiftFact.snapshot_id == snapshot.id,
        )
        .order_by(PayShiftFact.staff_name, PayShiftFact.clockin_at)
        .all()
    )


def totals(facts) -> dict:
    worked = [f for f in facts if f.shift_type != "Leave"]
    leave = [f for f in facts if f.shift_type == "Leave"]
    return {
        "shifts": len(worked),
        "hours": float(sum((f.hours for f in worked), Decimal("0"))),
        "cost_cents": sum(f.cost_cents for f in worked),
        "ph_hours": float(sum((f.ph_hours for f in worked), Decimal("0"))),
        "ph_cost_cents": sum(f.ph_cost_cents for f in worked),
        "leave_hours": float(sum((f.hours for f in leave), Decimal("0"))),
        "leave_cost_cents": sum(f.cost_cents for f in leave),
    }


def snapshot_dict(db: Session, snapshot: PaySnapshot, facts=None) -> dict:
    facts = facts_of(db, snapshot) if facts is None else facts
    return {
        "id": snapshot.id,
        "week_id": snapshot.week_id,
        "taken_at": snapshot.taken_at.isoformat() if snapshot.taken_at else None,
        "taken_by": snapshot.taken_by,
        "source": snapshot.source,
        "shift_count": snapshot.shift_count,
        "dropped_deleted": snapshot.dropped_deleted,
        "dropped_unfinished": snapshot.dropped_unfinished,
        "content_sha256": snapshot.content_sha256,
        "superseded_at": snapshot.superseded_at.isoformat()
        if snapshot.superseded_at
        else None,
        "totals": totals(facts),
    }


def fact_dict(fact: PayShiftFact, venue_names: dict[str, str] | None = None) -> dict:
    names = venue_names or {}
    return {
        "id": fact.id,
        "source_shift_id": fact.source_shift_id,
        "staff_member_id": fact.staff_member_id,
        "staff_name": fact.staff_name,
        "pay_code": fact.pay_code,
        "shift_type": fact.shift_type,
        "remuneration_type": fact.remuneration_type,
        "role_id": fact.role_id,
        "role_name": fact.role_name,
        "cost_group": fact.cost_group,
        "worked_venue_loaded_id": fact.worked_venue_loaded_id,
        "worked_venue_id": fact.worked_venue_id,
        "worked_venue_name": names.get(fact.worked_venue_id or ""),
        "is_from_other_venue": fact.is_from_other_venue,
        "clockin_at": fact.clockin_at.isoformat() if fact.clockin_at else None,
        "clockout_at": fact.clockout_at.isoformat() if fact.clockout_at else None,
        "trading_date": fact.trading_date.isoformat() if fact.trading_date else None,
        "hours": float(fact.hours or 0),
        "cost_cents": fact.cost_cents,
        "ph_hours": float(fact.ph_hours or 0),
        "ph_cost_cents": fact.ph_cost_cents,
        "leave_request_id": fact.leave_request_id,
        "leave_type_name": fact.leave_type_name,
        "is_approved": fact.is_approved,
    }


def venue_names(db: Session, org_id: str) -> dict[str, str]:
    return {
        v.id: v.name
        for v in db.query(Venue).filter(Venue.organization_id == org_id).all()
    }


# ==================================================================== diff ==


def _field(fact: PayShiftFact, name: str):
    value = getattr(fact, name)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


def diff(db: Session, a: PaySnapshot, b: PaySnapshot) -> dict:
    """What changed between two snapshots of a week, by Loaded's shift id:
    shifts added, removed, and changed (field by field), with the totals of
    each side — the "drift since close" the Payroll page shows."""
    names = venue_names(db, a.organization_id)
    fa = {f.source_shift_id: f for f in facts_of(db, a)}
    fb = {f.source_shift_id: f for f in facts_of(db, b)}
    added = [fact_dict(fb[k], names) for k in sorted(set(fb) - set(fa))]
    removed = [fact_dict(fa[k], names) for k in sorted(set(fa) - set(fb))]
    changed = []
    for key in sorted(set(fa) & set(fb)):
        fields = {}
        for name in DIFF_FIELDS:
            before, after = _field(fa[key], name), _field(fb[key], name)
            if before != after:
                fields[name] = [before, after]
        if fields:
            changed.append(
                {
                    "source_shift_id": key,
                    "staff_name": fb[key].staff_name,
                    "fields": fields,
                }
            )
    ta, tb = totals(fa.values()), totals(fb.values())
    return {
        "a": a.id,
        "b": b.id,
        "same": a.content_sha256 == b.content_sha256,
        "added": added,
        "removed": removed,
        "changed": changed,
        "totals": {
            "a": ta,
            "b": tb,
            "delta": {
                "hours": round(tb["hours"] - ta["hours"], 2),
                "cost_cents": tb["cost_cents"] - ta["cost_cents"],
                "leave_cost_cents": tb["leave_cost_cents"] - ta["leave_cost_cents"],
            },
        },
    }


def drift(db: Session, week: PayWeek) -> dict | None:
    """The diff between the close snapshot and a later current snapshot that
    differs from it; None when there is nothing to show."""
    close = weeks_svc.close_snapshot(db, week)
    current = weeks_svc.current_snapshot(db, week)
    if close is None or current is None or current.id == close.id:
        return None
    if current.taken_at <= close.taken_at:
        return None
    if current.content_sha256 == close.content_sha256:
        return None
    return diff(db, close, current)


# ================================================================ sections ==


def _per_staff(facts) -> list[dict]:
    out: dict[str, dict] = {}
    for f in facts:
        key = f.staff_member_id or f.staff_name or ""
        acc = out.get(key)
        if acc is None:
            acc = out[key] = {
                "staff_member_id": f.staff_member_id,
                "staff_name": f.staff_name,
                "pay_code": f.pay_code,
                "remuneration_type": f.remuneration_type,
                "shifts": 0,
                "hours": Decimal("0"),
                "cost_cents": 0,
                "ph_hours": Decimal("0"),
                "ph_cost_cents": 0,
                "other_venue_hours": Decimal("0"),
                "all_approved": True,
                "shift_ids": [],
            }
        acc["shifts"] += 1
        acc["hours"] += f.hours or 0
        acc["cost_cents"] += f.cost_cents
        acc["ph_hours"] += f.ph_hours or 0
        acc["ph_cost_cents"] += f.ph_cost_cents
        acc["shift_ids"].append(f.source_shift_id)
        if f.is_from_other_venue:
            acc["other_venue_hours"] += f.hours or 0
        if f.is_approved is False:
            acc["all_approved"] = False
        if f.remuneration_type == "Salary":
            acc["remuneration_type"] = "Salary"
        if not acc["pay_code"] and f.pay_code:
            acc["pay_code"] = f.pay_code
    people = sorted(out.values(), key=lambda p: (p["staff_name"] or "").casefold())
    for p in people:
        for k in ("hours", "ph_hours", "other_venue_hours"):
            p[k] = float(p[k])
    return people


def _section_totals(facts) -> dict:
    return {
        "shifts": len(facts),
        "hours": float(sum((f.hours for f in facts), Decimal("0"))),
        "cost_cents": sum(f.cost_cents for f in facts),
        "ph_hours": float(sum((f.ph_hours for f in facts), Decimal("0"))),
        "ph_cost_cents": sum(f.ph_cost_cents for f in facts),
    }


def leave_rows(facts) -> list[dict]:
    """Leave facts grouped by request (the consolidator's leave section)."""
    grouped: dict[tuple, dict] = {}
    for f in facts:
        if f.shift_type != "Leave":
            continue
        key = (f.leave_request_id or f.source_shift_id, f.staff_member_id)
        acc = grouped.get(key)
        if acc is None:
            acc = grouped[key] = {
                "leave_request_id": f.leave_request_id,
                "staff_member_id": f.staff_member_id,
                "staff_name": f.staff_name,
                "pay_code": f.pay_code,
                "remuneration_type": f.remuneration_type,
                "leave_type_name": f.leave_type_name,
                "dates": [],
                "hours": Decimal("0"),
                "cost_cents": 0,
            }
        day = f.trading_date.isoformat() if f.trading_date else None
        if day and day not in acc["dates"]:
            acc["dates"].append(day)
        acc["hours"] += f.hours or 0
        acc["cost_cents"] += f.cost_cents
    rows = sorted(
        grouped.values(), key=lambda r: ((r["staff_name"] or "").casefold(), r["dates"])
    )
    for r in rows:
        r["hours"] = float(r["hours"])
        r["dates"].sort()
    return rows


def sections(db: Session, snapshot: PaySnapshot, entity: PayEntity) -> dict:
    """The Payroll page's view of a snapshot: the consolidator's sections
    rebuilt from the stored facts."""
    facts = facts_of(db, snapshot)
    names = venue_names(db, snapshot.organization_id)
    security_code = entity.security_pay_code or "SECURITY"
    worked = [f for f in facts if f.shift_type != "Leave"]
    leave = [f for f in facts if f.shift_type == "Leave"]
    security = [f for f in worked if (f.pay_code or "") == security_code]
    main = [f for f in worked if (f.pay_code or "") != security_code]
    salaried_ids = {f.staff_member_id for f in facts if f.remuneration_type == "Salary"}
    salaried: dict[str, dict] = {}
    for f in worked:
        if f.staff_member_id not in salaried_ids:
            continue
        acc = salaried.setdefault(
            f.staff_member_id,
            {
                "staff_member_id": f.staff_member_id,
                "staff_name": f.staff_name,
                "pay_code": f.pay_code,
                "hours": Decimal("0"),
                "cost_cents": 0,
                "by_shift_type": {},
            },
        )
        acc["hours"] += f.hours or 0
        acc["cost_cents"] += f.cost_cents
        by = acc["by_shift_type"].setdefault(
            f.shift_type, {"shifts": 0, "hours": 0.0, "cost_cents": 0}
        )
        by["shifts"] += 1
        by["hours"] = round(by["hours"] + float(f.hours or 0), 2)
        by["cost_cents"] += f.cost_cents
    for acc in salaried.values():
        acc["hours"] = float(acc["hours"])
    other = [f for f in worked if f.is_from_other_venue]
    counterparties: dict[str, dict] = {}
    for f in other:
        key = f.worked_venue_loaded_id or ""
        acc = counterparties.setdefault(
            key,
            {
                "worked_venue_loaded_id": key,
                "worked_venue_id": f.worked_venue_id,
                "worked_venue_name": names.get(f.worked_venue_id or ""),
                "hours": Decimal("0"),
                "cost_cents": 0,
                "shifts": 0,
            },
        )
        acc["hours"] += f.hours or 0
        acc["cost_cents"] += f.cost_cents
        acc["shifts"] += 1
    for acc in counterparties.values():
        acc["hours"] = float(acc["hours"])
    return {
        "staff": {"rows": _per_staff(main), "totals": _section_totals(main)},
        "security": {
            "pay_code": security_code,
            "rows": _per_staff(security),
            "totals": _section_totals(security),
        },
        "leave": {"rows": leave_rows(leave), "totals": _section_totals(leave)},
        "salaried": {
            "rows": sorted(
                salaried.values(), key=lambda p: (p["staff_name"] or "").casefold()
            )
        },
        "intercompany": {
            "counterparties": sorted(
                counterparties.values(),
                key=lambda c: (
                    c["worked_venue_name"] or "",
                    c["worked_venue_loaded_id"],
                ),
            ),
            "rows": [fact_dict(f, names) for f in other],
        },
        "facts": [fact_dict(f, names) for f in facts],
    }
