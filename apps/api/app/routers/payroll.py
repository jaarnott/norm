"""Payroll — the staff API (``/api/payroll/...``), the Payroll page's side of
``app/services/payroll`` (plan §9; the route contract is
``payroll-api.md`` in the build folder).

Every endpoint does the same four things, written once:

1. **Who and where** — ``require_permission("payroll:read")`` (writes
   ``payroll:write``), then ``build_ctx``: the person's organisation from
   membership, their venues (fail closed), their scopes, the module's mode.
   ``payroll_mode = off`` is a 404 — the pages do not exist for that
   organisation, so neither do the endpoints.
2. **The rule** — a service; the router shapes what it returns.
3. **Errors** — the finance domain errors become
   ``{"detail": {"code", "message"}}`` with the service's status through the
   one handler (``http_exception``).
4. **One request, one transaction** — writes run in a savepoint and commit
   once; a refused write leaves nothing behind.

Trial run (mode ``shadow``): a Norm-side input (payout, gratuity batch, ACE inputs,
adjustment, accrual) is refused with 409 ``not_live`` unless the body says
``rehearsal: true`` (the rehearsal weeks). Snapshots, exports, settings,
leave styles, parity runs and the payroll-contact emails are allowed in
shadow — they read Loaded and write only Norm's own tables.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_config_db, get_db
from app.db.models import Organization, User, Venue
from app.services.finance_core import parity as core_parity
from app.services.finance_core.ctx import FinanceCtx, build_ctx
from app.services.finance_core.errors import FinanceError, Invalid, http_exception
from app.services.payroll import entities as entities_svc
from app.services.payroll import exports as exports_svc
from app.services.payroll import gratuities as gratuities_svc
from app.services.payroll import hygiene as hygiene_svc
from app.services.payroll import inputs as inputs_svc
from app.services.payroll import leave as leave_svc
from app.services.payroll import parity as parity_svc
from app.services.payroll import rules as rules_svc
from app.services.payroll import snapshot as snapshot_svc
from app.services.payroll import weeks as weeks_svc

router = APIRouter(prefix="/payroll", tags=["payroll"])


# ===========================================================================
# Plumbing
# ===========================================================================


def _ctx(user: User, db: Session) -> FinanceCtx:
    try:
        ctx = build_ctx(db, user)
    except FinanceError as exc:
        raise http_exception(exc) from exc
    if ctx.mode("payroll") == "off":
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": "Payroll is not switched on for your organisation.",
            },
        )
    return ctx


def read_ctx(
    user: User = Depends(require_permission("payroll:read")),
    db: Session = Depends(get_db),
) -> FinanceCtx:
    return _ctx(user, db)


def write_ctx(
    user: User = Depends(require_permission("payroll:write")),
    db: Session = Depends(get_db),
) -> FinanceCtx:
    return _ctx(user, db)


@contextmanager
def _reading():
    try:
        yield
    except FinanceError as exc:
        raise http_exception(exc) from exc


@contextmanager
def _writing(db: Session):
    """A savepoint around the write, then one commit."""
    try:
        with db.begin_nested():
            yield
    except FinanceError as exc:
        raise http_exception(exc) from exc
    db.commit()


class _Body(BaseModel):
    """Request bodies refuse unknown fields: a typo must not look like a
    successful save."""

    model_config = ConfigDict(extra="forbid")


def _venue(db: Session, ctx: FinanceCtx, venue_id: str) -> Venue:
    return weeks_svc.venue_of(db, ctx, venue_id)


def _names(db: Session, ctx: FinanceCtx) -> dict[str, str]:
    return snapshot_svc.venue_names(db, ctx.org_id)


# ===========================================================================
# Meta, entities, rules
# ===========================================================================


@router.get("/meta")
def meta(ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    with _reading():
        entities = entities_svc.list_entities(db, ctx)
        db.commit()  # rows made on first use stay made
    venues = {
        v.id: v for v in db.query(Venue).filter(Venue.organization_id == ctx.org_id)
    }
    first = next((venues[e.venue_id] for e in entities if e.venue_id in venues), None)
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "mode": ctx.mode("payroll"),
        "scopes": sorted(
            s for s in ctx.scopes if s.startswith(("payroll:", "finance:", "budgets:"))
        ),
        "can_write": ctx.holds("payroll:write"),
        "organization": {
            "id": ctx.org_id,
            "name": org.name if org else None,
            "payroll_contact_email": getattr(org, "payroll_contact_email", None),
            "currency": getattr(org, "currency", None),
        },
        "entities": [entities_svc.as_dict(e, venues.get(e.venue_id)) for e in entities],
        # payroll_on starts false on every entity: with none on, the jobs,
        # the hygiene count and the group overview have nothing to show.
        "entities_on": sum(1 for e in entities if e.payroll_on),
        "entities_total": len(entities),
        "expected_classes": dict(core_parity.EXPECTED_CLASSES),
        "this_week_start": weeks_svc.week_start_for(first, now).isoformat(),
        "last_week_start": weeks_svc.last_completed_week_start(first, now).isoformat(),
    }


@router.get("/entities")
def list_entities(ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)):
    with _reading():
        entities = entities_svc.list_entities(db, ctx)
        db.commit()
    venues = {
        v.id: v for v in db.query(Venue).filter(Venue.organization_id == ctx.org_id)
    }
    return {
        "entities": [entities_svc.as_dict(e, venues.get(e.venue_id)) for e in entities]
    }


@router.get("/entities/{venue_id}")
def get_entity(
    venue_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        entity = entities_svc.get_or_create(db, ctx, venue_id)
        db.commit()
    return entities_svc.as_dict(entity, _venue(db, ctx, venue_id))


class EntityBody(_Body):
    version: int
    pay_week_start_weekday: int | None = None
    payroll_system: str | None = None
    ace_pay_type_code: str | None = None
    ace_gratuity_allowance_code: str | None = None
    security_pay_code: str | None = None
    uniform_bond_cents: int | None = None
    ico_leave_uplift_bp: int | None = None
    ico_leave_tax: str | None = None
    sick_approver_name: str | None = None
    payroll_contact_email: str | None = None
    payroll_on: bool | None = None


@router.put("/entities/{venue_id}")
def update_entity(
    venue_id: str,
    body: EntityBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    fields = body.model_dump(exclude_unset=True)
    version = fields.pop("version")
    with _writing(db):
        entity = entities_svc.update_settings(
            db, ctx, venue_id, version=version, **fields
        )
    return entities_svc.as_dict(entity, _venue(db, ctx, venue_id))


@router.get("/rules")
def list_rules(ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)):
    with _reading():
        rows = rules_svc.list_rules(db, ctx)
        db.commit()
    return {"rules": [rules_svc.as_dict(r) for r in rows]}


class RuleBody(_Body):
    rule: str
    effective_from: dt.date
    params: dict
    source: str | None = None


@router.post("/rules")
def set_rule(
    body: RuleBody, ctx: FinanceCtx = Depends(write_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        row = rules_svc.set_rule(
            db,
            ctx,
            rule=body.rule,
            effective_from=body.effective_from,
            params=body.params,
            source=body.source,
        )
    return rules_svc.as_dict(row)


# ===========================================================================
# Weeks
# ===========================================================================


def _week_detail(db: Session, ctx: FinanceCtx, week, *, config_db=None) -> dict:
    out = weeks_svc.as_dict(db, week)
    current = weeks_svc.current_snapshot(db, week)
    close = weeks_svc.close_snapshot(db, week)
    out["current_snapshot"] = (
        snapshot_svc.snapshot_dict(db, current) if current else None
    )
    out["close_snapshot"] = snapshot_svc.snapshot_dict(db, close) if close else None
    out["drift"] = snapshot_svc.drift(db, week)
    out["exports"] = [
        exports_svc.export_dict(e) for e in weeks_svc.exports_of(db, week)
    ]
    payouts = leave_svc.list_payouts(
        db, ctx, venue_id=week.venue_id, week_start=week.week_start
    )
    shares = gratuities_svc.payable_by(
        db, ctx, week.venue_id, week.week_start, include_rehearsal=True
    )
    out["inputs"] = {
        "ace_inputs": inputs_svc.ace_inputs_dict(
            inputs_svc.get_ace_inputs(db, ctx, week)
        ),
        "adjustments": [
            inputs_svc.adjustment_dict(a)
            for a in inputs_svc.list_adjustments(db, ctx, week)
        ],
        "accruals": inputs_svc.accrual_dict(
            db, ctx, week, inputs_svc.get_accrual(db, ctx, week)
        ),
        "payouts": {
            "count": len(payouts),
            "requested_cents": sum(p.requested_cents for p in payouts),
            "paid_cents": sum(p.paid_cents for p in payouts),
        },
        "gratuities": {
            "batches": len({b.id for b, _ in shares}),
            "payable_cents": sum(a.amount_cents for b, a in shares if not b.rehearsal),
        },
    }
    out["pushes"] = [
        {
            "id": p.id,
            "kind": p.kind,
            "xero_number": p.xero_number,
            "xero_status": p.xero_status,
            "pushed_at": p.pushed_at.isoformat() if p.pushed_at else None,
            "voided_at": p.voided_at.isoformat() if p.voided_at else None,
        }
        for p in weeks_svc.pushes_of(db, week)
    ]
    return out


@router.get("/weeks")
def list_weeks(
    venue_id: str | None = None,
    limit: int = Query(12, ge=1, le=200),
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows = weeks_svc.list_weeks(db, ctx, venue_id=venue_id, limit=limit)
    names = _names(db, ctx)
    venues = {v.id: v for v in db.query(Venue).filter(Venue.id.in_(list(names)))}
    out = []
    for w in rows:
        d = weeks_svc.as_dict(db, w, venues.get(w.venue_id))
        current = weeks_svc.current_snapshot(db, w)
        close = weeks_svc.close_snapshot(db, w)
        d["current_snapshot"] = (
            snapshot_svc.snapshot_dict(db, current) if current else None
        )
        d["close_snapshot"] = snapshot_svc.snapshot_dict(db, close) if close else None
        out.append(d)
    return {"weeks": out}


class WeekBody(_Body):
    venue_id: str
    week_start: dt.date


@router.post("/weeks")
def open_week(
    body: WeekBody, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        week = weeks_svc.get_or_create(db, ctx, body.venue_id, body.week_start)
    return _week_detail(db, ctx, week)


@router.get("/weeks/{week_id}")
def get_week(
    week_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        return _week_detail(db, ctx, week)


class NotesBody(_Body):
    notes: str | None = None
    version: int


@router.put("/weeks/{week_id}")
def update_week(
    week_id: str,
    body: NotesBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        weeks_svc.update_notes(db, ctx, week, notes=body.notes, version=body.version)
    return _week_detail(db, ctx, week)


@router.post("/weeks/{week_id}/start-close")
def start_close(
    week_id: str,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        snapshot_svc.take(
            db, ctx, week.venue_id, week.week_start, source="close", config_db=config_db
        )
    return _week_detail(db, ctx, week)


class VersionBody(_Body):
    version: int


@router.post("/weeks/{week_id}/close")
def close_week(
    week_id: str,
    body: VersionBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        weeks_svc.close(db, ctx, week, version=body.version)
    return _week_detail(db, ctx, week)


# ===========================================================================
# Snapshots, payrun, hygiene
# ===========================================================================


class SnapshotBody(_Body):
    source: str = "manual"


@router.post("/weeks/{week_id}/snapshots")
def take_snapshot(
    week_id: str,
    body: SnapshotBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    if body.source not in ("manual", "close"):
        raise http_exception(Invalid("A page takes a manual or a close snapshot."))
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        snap = snapshot_svc.take(
            db,
            ctx,
            week.venue_id,
            week.week_start,
            source=body.source,
            config_db=config_db,
        )
    return snapshot_svc.snapshot_dict(db, snap)


@router.get("/weeks/{week_id}/snapshots")
def list_snapshots(
    week_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
    return {
        "snapshots": [
            snapshot_svc.snapshot_dict(db, s)
            for s in snapshot_svc.list_for_week(db, week)
        ]
    }


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(
    snapshot_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        snap = snapshot_svc.get(db, ctx, snapshot_id)
    facts = snapshot_svc.facts_of(db, snap)
    names = _names(db, ctx)
    out = snapshot_svc.snapshot_dict(db, snap, facts)
    out["facts"] = [snapshot_svc.fact_dict(f, names) for f in facts]
    return out


@router.get("/weeks/{week_id}/diff")
def diff_snapshots(
    week_id: str,
    a: str | None = None,
    b: str | None = None,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        left = snapshot_svc.get(db, ctx, a) if a else weeks_svc.close_snapshot(db, week)
        right = (
            snapshot_svc.get(db, ctx, b) if b else weeks_svc.current_snapshot(db, week)
        )
        if left is None or right is None:
            raise http_exception(
                Invalid("Two snapshots are needed to diff (a close and a current one).")
            )
        if left.week_id != week.id or right.week_id != week.id:
            raise http_exception(Invalid("Both snapshots must belong to this week."))
        return snapshot_svc.diff(db, left, right)


@router.get("/weeks/{week_id}/payrun")
def payrun(
    week_id: str,
    snapshot: str = Query("current", pattern="^(current|close)$"),
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        snap = (
            weeks_svc.close_snapshot(db, week)
            if snapshot == "close"
            else weeks_svc.current_snapshot(db, week)
        )
        entity = entities_svc.get_or_create(db, ctx, week.venue_id)
        venue = _venue(db, ctx, week.venue_id)
        if snap is None:
            # The page's empty state: no snapshot yet, nothing to show (F8).
            empty = {"rows": [], "totals": snapshot_svc.totals([])}
            out = {
                "staff": dict(empty),
                "security": {"pay_code": entity.security_pay_code, **empty},
                "leave": dict(empty),
                "salaried": {"rows": []},
                "intercompany": {"counterparties": [], "rows": []},
                "facts": [],
            }
        else:
            out = snapshot_svc.sections(db, snap, entity)
        # Each leave row's Ace style comes from Loaded's leave types (read
        # only when the snapshot holds leave); a failed read leaves the rows
        # unmarked and says why.
        out["leave"]["setup_error"] = exports_svc.annotate_leave_rows(
            db, ctx, venue, out["leave"]["rows"]
        )
        names = _names(db, ctx)
        batches = gratuities_svc.list_batches(
            db, ctx, venue_id=venue.id, week_start=week.week_start
        )
        shares = gratuities_svc.payable_by(
            db, ctx, venue.id, week.week_start, include_rehearsal=True
        )
        items, available = exports_svc.entertainer_items(
            db, ctx, venue.id, week.week_start
        )
        out["snapshot"] = snapshot_svc.snapshot_dict(db, snap) if snap else None
        out["entity"] = entities_svc.as_dict(entity, venue)

        def _share(b, a):
            return {
                "allocation_id": a.id,
                "batch_id": b.id,
                "period": b.period,
                "earned_at_venue_id": b.venue_id,
                "earned_at_venue_name": names.get(b.venue_id),
                "payroll_venue_id": a.payroll_venue_id,
                "payroll_venue_name": names.get(a.payroll_venue_id or ""),
                "staff_member_id": a.staff_member_id,
                "staff_name": a.staff_name,
                "pay_code": a.pay_code,
                "hours": float(a.hours or 0),
                "amount_cents": a.amount_cents,
                "rehearsal": b.rehearsal,
            }

        # Earned here, paid by another entity's payroll (an intercompany recharge).
        paid_elsewhere = [
            _share(b, a)
            for b in batches
            for a in gratuities_svc.allocations_of(db, b)
            if a.is_payable and a.excluded_at is None and a.payroll_venue_id != venue.id
        ]
        out["gratuities"] = {
            "batches": [gratuities_svc.batch_dict(db, b, names) for b in batches],
            "payable_here": [_share(b, a) for b, a in shares],
            "paid_elsewhere": paid_elsewhere,
        }
        out["entertainers"] = {
            "available": available,
            "lines": [
                {
                    "performance_id": exports_svc._attr(i, "performance_id"),
                    "pay_code": exports_svc._attr(i, "pay_code"),
                    "hours": exports_svc._attr(i, "hours"),
                    "rate_cents": exports_svc._attr(i, "rate_cents"),
                    "amount_cents": exports_svc._attr(i, "amount_cents"),
                }
                for i in items
            ],
        }
        db.commit()
        return out


@router.get("/weeks/{week_id}/hygiene")
def hygiene(
    week_id: str,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        out = hygiene_svc.compute(db, ctx, week, config_db=config_db)
        db.commit()
        return out


@router.post("/weeks/{week_id}/hygiene/email")
def hygiene_email(
    week_id: str,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        out = hygiene_svc.send_email(db, ctx, week, config_db=config_db)
    return out


# ===========================================================================
# Leave
# ===========================================================================


@router.get("/leave/requested")
def leave_requested(
    venue_id: str,
    week_start: dt.date,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _reading():
        return leave_svc.requested(db, ctx, venue_id, week_start, config_db=config_db)


@router.get("/leave/payouts")
def leave_payouts(
    venue_id: str | None = None,
    week_start: dt.date | None = None,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows = leave_svc.list_payouts(db, ctx, venue_id=venue_id, week_start=week_start)
    return {"payouts": [leave_svc.payout_dict(p, ctx) for p in rows]}


class PayoutBody(_Body):
    venue_id: str
    week_start: dt.date
    loaded_request_id: str
    staff_member_id: str | None = None
    staff_name: str | None = None
    leave_type: str | None = None
    requested_cents: int = 0
    workflow_kind: str | None = None
    inputs: dict[str, Any] | None = None
    paid_cents: int | None = None
    notes: str | None = None
    rehearsal: bool = False
    version: int | None = None


@router.post("/leave/payouts")
def create_payout(
    body: PayoutBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        payout = leave_svc.record_payout(db, ctx, **body.model_dump())
    return leave_svc.payout_dict(payout, ctx)


@router.put("/leave/payouts/{payout_id}")
def update_payout(
    payout_id: str,
    body: PayoutBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        payout = leave_svc.record_payout(
            db, ctx, payout_id=payout_id, **body.model_dump()
        )
    return leave_svc.payout_dict(payout, ctx)


@router.delete("/leave/payouts/{payout_id}", status_code=204)
def delete_payout(
    payout_id: str, ctx: FinanceCtx = Depends(write_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        leave_svc.delete_payout(db, ctx, payout_id)
    return Response(status_code=204)


@router.get("/leave/sick-eligibility")
def sick_eligibility(
    venue_id: str,
    staff_member_id: str,
    as_of: dt.date,
    start_date: dt.date | None = None,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _reading():
        out = leave_svc.sick_eligibility(
            db,
            ctx,
            venue_id,
            staff_member_id,
            as_of,
            start_date=start_date,
            config_db=config_db,
        )
        db.commit()
        return out


class SickEmailBody(_Body):
    kind: str
    to: str


@router.post("/leave/payouts/{payout_id}/email")
def sick_email(
    payout_id: str,
    body: SickEmailBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        payout = leave_svc.get_payout(db, ctx, payout_id)
        out = leave_svc.send_sick_email(db, ctx, payout, kind=body.kind, to=body.to)
    return out


@router.post("/files/bond-consent")
async def upload_bond_consent(
    file: UploadFile = File(...),
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    # One byte past the PDF check's cap is enough to refuse it.
    data = await file.read(10 * 1024 * 1024 + 1)
    with _writing(db):
        row = leave_svc.store_bond_consent(
            db, ctx, data=data, filename=file.filename, content_type=file.content_type
        )
    return {
        "id": row.id,
        "sha256": row.sha256,
        "filename": row.filename,
        "size_bytes": row.size_bytes,
    }


# ===========================================================================
# Gratuities
# ===========================================================================


@router.get("/gratuities")
def list_gratuities(
    venue_id: str | None = None,
    week_start: dt.date | None = None,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        batches = gratuities_svc.list_batches(
            db, ctx, venue_id=venue_id, week_start=week_start
        )
    names = _names(db, ctx)
    return {"batches": [gratuities_svc.batch_dict(db, b, names) for b in batches]}


class GratuityPreviewBody(_Body):
    venue_id: str
    period: str
    week_start: dt.date
    total_cents: int
    excluded_staff_ids: list[str] = []


class GratuityBody(GratuityPreviewBody):
    gl_code: str | None = None
    notes: str | None = None
    rehearsal: bool = False


@router.post("/gratuities/preview")
def preview_gratuities(
    body: GratuityPreviewBody,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _reading():
        return gratuities_svc.preview(db, ctx, config_db=config_db, **body.model_dump())


@router.post("/gratuities")
def create_gratuities(
    body: GratuityBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _writing(db):
        batch = gratuities_svc.create_batch(
            db, ctx, config_db=config_db, **body.model_dump()
        )
    return gratuities_svc.batch_dict(db, batch)


@router.get("/gratuities/{batch_id}")
def get_gratuities(
    batch_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        batch = gratuities_svc.get_batch(db, ctx, batch_id)
    return gratuities_svc.batch_dict(db, batch)


@router.post("/gratuities/{batch_id}/allocations/{allocation_id}/exclude")
def exclude_allocation(
    batch_id: str,
    allocation_id: str,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        batch = gratuities_svc.exclude(db, ctx, batch_id, allocation_id)
    return gratuities_svc.batch_dict(db, batch)


@router.delete("/gratuities/{batch_id}", status_code=204)
def delete_gratuities(
    batch_id: str, ctx: FinanceCtx = Depends(write_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        gratuities_svc.delete_batch(db, ctx, batch_id)
    return Response(status_code=204)


# ===========================================================================
# Exports and leave styles
# ===========================================================================


@router.get("/weeks/{week_id}/exports")
def list_exports(
    week_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
    return {
        "exports": [exports_svc.export_dict(e) for e in weeks_svc.exports_of(db, week)]
    }


class ExportBody(_Body):
    kinds: list[str] | None = None


@router.post("/weeks/{week_id}/exports")
def generate_exports(
    week_id: str,
    body: ExportBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        out = exports_svc.generate(db, ctx, week, kinds=body.kinds or None)
    return {
        "exports": [exports_svc.export_dict(e) for e in out["exports"]],
        "diffs": out["diffs"],
    }


@router.get("/exports/{export_id}")
def get_export(
    export_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        row = exports_svc.get_export(db, ctx, export_id)
    return exports_svc.export_dict(row, content=True)


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        row = exports_svc.get_export(db, ctx, export_id)
    return Response(
        content=row.content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{row.filename}"'},
    )


@router.get("/weeks/{week_id}/fallback-pack")
def fallback_pack(
    week_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        return exports_svc.fallback_pack(db, ctx, week)


@router.get("/weeks/{week_id}/fallback-pack/{filename}")
def fallback_file(
    week_id: str,
    filename: str,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        pack = exports_svc.fallback_pack(db, ctx, week)
    if filename not in pack["files"]:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "No such file in the pack."},
        )
    media = (
        "text/csv; charset=utf-8"
        if filename.endswith(".csv")
        else "text/plain; charset=utf-8"
    )
    return Response(
        content=pack["files"][filename].encode("utf-8"),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/leave-types")
def leave_types(
    venue_id: str,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Loaded's leave types for the venue, read now, with the Ace style the
    leave export uses for each and where it comes from."""
    with _reading():
        rows = exports_svc.leave_types(db, ctx, venue_id, config_db=config_db)
    return {"leave_types": rows}


class LeaveStyleBody(_Body):
    venue_id: str
    ace_style: str | None = None
    version: int | None = None


@router.put("/leave-types/{loaded_leave_type_id}/ace-style")
def set_leave_style(
    loaded_leave_type_id: str,
    body: LeaveStyleBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """The Ace style for a leave type Loaded gives no payroll meaning (a
    company's own type); a blank style clears it."""
    with _writing(db):
        return exports_svc.set_leave_style(
            db,
            ctx,
            body.venue_id,
            loaded_leave_type_id,
            ace_style=body.ace_style,
            version=body.version,
            config_db=config_db,
        )


# ===========================================================================
# Reconciliation inputs
# ===========================================================================


@router.get("/weeks/{week_id}/ace-inputs")
def get_ace_inputs(
    week_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        return inputs_svc.ace_inputs_dict(inputs_svc.get_ace_inputs(db, ctx, week))


class AceBody(_Body):
    gross_cents: int
    net_cents: int
    esct_cents: int
    kiwisaver_cents: int
    rehearsal: bool = False
    version: int | None = None


@router.put("/weeks/{week_id}/ace-inputs")
def set_ace_inputs(
    week_id: str,
    body: AceBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        row = inputs_svc.set_ace_inputs(db, ctx, week, **body.model_dump())
    return inputs_svc.ace_inputs_dict(row)


@router.get("/weeks/{week_id}/adjustments")
def list_adjustments(
    week_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        return {
            "adjustments": [
                inputs_svc.adjustment_dict(a)
                for a in inputs_svc.list_adjustments(db, ctx, week)
            ]
        }


class AdjustmentBody(_Body):
    label: str | None = None
    amount_cents: int | None = None
    xero_account_code: str | None = None
    xero_account_name: str | None = None
    report_target: str | None = None
    notes: str | None = None
    sort_order: int | None = None
    rehearsal: bool = False
    version: int | None = None


@router.post("/weeks/{week_id}/adjustments")
def add_adjustment(
    week_id: str,
    body: AdjustmentBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    fields = body.model_dump(exclude_unset=True)
    fields.pop("version", None)
    rehearsal = fields.pop("rehearsal", False)
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        row = inputs_svc.add_adjustment(db, ctx, week, rehearsal=rehearsal, **fields)
    return inputs_svc.adjustment_dict(row)


@router.put("/adjustments/{adjustment_id}")
def update_adjustment(
    adjustment_id: str,
    body: AdjustmentBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    fields = body.model_dump(exclude_unset=True)
    version = fields.pop("version", None)
    fields.pop("rehearsal", None)
    if version is None:
        raise http_exception(Invalid("version is required to change an adjustment."))
    with _writing(db):
        row = inputs_svc.update_adjustment(
            db, ctx, adjustment_id, version=version, **fields
        )
    return inputs_svc.adjustment_dict(row)


@router.delete("/adjustments/{adjustment_id}", status_code=204)
def delete_adjustment(
    adjustment_id: str,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        inputs_svc.delete_adjustment(db, ctx, adjustment_id)
    return Response(status_code=204)


@router.get("/weeks/{week_id}/accruals")
def get_accruals(
    week_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        week = weeks_svc.get(db, ctx, week_id)
        return inputs_svc.accrual_dict(
            db, ctx, week, inputs_svc.get_accrual(db, ctx, week)
        )


class AccrualBody(_Body):
    opening_cents: int | None = None
    accrued_cents: int
    paid_cents: int
    rehearsal: bool = False


@router.put("/weeks/{week_id}/accruals")
def set_accruals(
    week_id: str,
    body: AccrualBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        row = inputs_svc.set_accrual(db, ctx, week, **body.model_dump())
        return inputs_svc.accrual_dict(db, ctx, week, row)


# ===========================================================================
# Trial run tab, jobs
# ===========================================================================


@router.get("/shadow")
def shadow(
    since: dt.datetime | None = None,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        return parity_svc.shadow(db, ctx, since=since)


@router.get("/shadow/runs/{run_id}")
def shadow_run(
    run_id: str, ctx: FinanceCtx = Depends(read_ctx), db: Session = Depends(get_db)
):
    with _reading():
        run = core_parity.get_run(db, ctx, run_id)
        return parity_svc.run_dict(db, ctx, run, items=True)


class ParityBody(_Body):
    module: str
    venue_id: str
    week_start: dt.date


@router.post("/shadow/runs")
def run_parity(
    body: ParityBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    with _writing(db):
        week = weeks_svc.get_or_create(db, ctx, body.venue_id, body.week_start)
        run = parity_svc.run_module(
            db, ctx, body.module, week, trigger="manual", config_db=config_db
        )
    return parity_svc.run_dict(db, ctx, run, items=True)


@router.post("/shadow/exports-check")
async def exports_check(
    week_id: str = Form(...),
    import_file: UploadFile | None = File(None, alias="import"),
    leave_file: UploadFile | None = File(None, alias="leave"),
    gratuities_file: UploadFile | None = File(None, alias="gratuities"),
    entertainers_file: UploadFile | None = File(None, alias="entertainers"),
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    uploaded = {}
    for kind, upload in (
        ("import", import_file),
        ("leave", leave_file),
        ("gratuities", gratuities_file),
        ("entertainers", entertainers_file),
    ):
        if upload is not None:
            uploaded[kind] = await upload.read()
    with _writing(db):
        week = weeks_svc.get(db, ctx, week_id)
        run = parity_svc.payroll_check(
            db, ctx, week, uploaded=uploaded, trigger="manual", config_db=config_db
        )
    return parity_svc.run_dict(db, ctx, run, items=True)


class SignOffBody(_Body):
    explanation: str | None = None


@router.post("/shadow/items/{item_id}/sign-off")
def sign_off_item(
    item_id: str,
    body: SignOffBody,
    ctx: FinanceCtx = Depends(write_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        item = core_parity.sign_off(db, ctx, item_id, explanation=body.explanation)
    return parity_svc.item_dict(item, _names(db, ctx))


@router.get("/jobs")
def jobs(
    since: dt.datetime | None = None,
    ctx: FinanceCtx = Depends(read_ctx),
    db: Session = Depends(get_db),
):
    since = since or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14))
    return {"runs": parity_svc.job_runs(db, ctx.org_id, since)}
