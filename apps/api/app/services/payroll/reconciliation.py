"""The wages reconciliation: Loaded's labour cost against what Ace paid, and
every term that explains the gap (plan §10, report 03 §4).

Orbit's nine-term formula, computed from **stored facts** rather than live
Loaded reads: the week's close snapshot (F3's ``pay_shift_facts``), the
payouts, gratuities, accruals, adjustments and ACE inputs people keyed, and
the entertainer lines the entertainers module feeds into payroll::

    computed   = timeclock_total
               + intercompany_net        (In − Out, intercompany.py)
               + leave_paid
               − security_total
               − accrued_additions
               + accrued_deductions
               + other_adjustments
               + entertainer_payroll_total
               + gratuity_total
    difference = computed − ace_gross

Every term comes back with the row ids behind it, so the page can open the
rows and a parity run can show its working. What stops a journal is a
**blocker** named here — the Tuesday timing coupling (payouts, gratuities and
entertainer paid rows must be saved first) is shown on the page, not found in
the push.

Integer cents throughout. Rehearsal rows (keyed during the shadow weeks)
never feed a figure here.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Venue
from app.db.payroll_models import (
    PayAccrual,
    PayAceInput,
    PayAdjustment,
    PayEntity,
    PayGratuityAllocation,
    PayGratuityBatch,
    PayJournalDraft,
    PayLeavePayout,
    PayShiftFact,
    PaySnapshot,
    PayWeek,
)
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid, NotFound
from app.services.payroll import intercompany as ico_svc

#: |difference| under these (cents) → the overview's colour bands (Orbit's).
BAND_OK = 100
BAND_NEAR = 5000

#: How far back a NULL opening accrual walks to find the prior closing.
_ACCRUAL_LOOKBACK = 52


# ---------------------------------------------------------------------------
# Weeks and snapshots
# ---------------------------------------------------------------------------


def week_bounds(text) -> tuple[dt.date, dt.date]:
    """(Monday, Sunday) of the pay week holding ``text`` — a date, or
    ``YYYY-MM-DD``; any day of the week snaps to that week."""
    if isinstance(text, dt.date):
        d = text
    else:
        try:
            d = dt.date.fromisoformat(str(text or "")[:10])
        except ValueError:
            raise Invalid("week_ending must be a date (YYYY-MM-DD).")
    monday = d - dt.timedelta(days=d.weekday())
    return monday, monday + dt.timedelta(days=6)


def week_start_of(week_start=None, week_ending=None) -> dt.date:
    """The Monday from whichever of the two a caller gave."""
    if week_start is None and week_ending is None:
        raise Invalid("week_ending (YYYY-MM-DD) is required.")
    return week_bounds(week_start if week_start is not None else week_ending)[0]


def find_week(db: Session, org_id: str, venue_id: str, week_start: dt.date):
    return (
        db.query(PayWeek)
        .filter(
            PayWeek.organization_id == org_id,
            PayWeek.venue_id == venue_id,
            PayWeek.week_start == week_start,
        )
        .first()
    )


def current_snapshot(db: Session, org_id: str, week_id: str) -> PaySnapshot | None:
    """The week's current snapshot (``superseded_at IS NULL``); the latest
    taken when more than one is current."""
    return (
        db.query(PaySnapshot)
        .filter(
            PaySnapshot.organization_id == org_id,
            PaySnapshot.week_id == week_id,
            PaySnapshot.superseded_at.is_(None),
        )
        .order_by(PaySnapshot.taken_at.desc())
        .first()
    )


def close_snapshot(db: Session, org_id: str, week_id: str) -> PaySnapshot | None:
    """The latest CLOSE snapshot — what the reconciliation and the journal
    build from, superseded or not (F3's rule: a later manual retake shows as
    drift on the Payrun page; it does not move the close)."""
    return (
        db.query(PaySnapshot)
        .filter(
            PaySnapshot.organization_id == org_id,
            PaySnapshot.week_id == week_id,
            PaySnapshot.source == "close",
        )
        .order_by(PaySnapshot.taken_at.desc())
        .first()
    )


def basis_snapshot(db: Session, org_id: str, week_id: str) -> PaySnapshot | None:
    """The close snapshot, else the current one (so the page can show
    figures before the close — flagged as a blocker by ``compute``)."""
    return close_snapshot(db, org_id, week_id) or current_snapshot(db, org_id, week_id)


def facts_of(db: Session, org_id: str, snapshot_id: str) -> list[PayShiftFact]:
    return (
        db.query(PayShiftFact)
        .filter(
            PayShiftFact.organization_id == org_id,
            PayShiftFact.snapshot_id == snapshot_id,
        )
        .order_by(PayShiftFact.clockin_at, PayShiftFact.id)
        .all()
    )


def entity_of(db: Session, org_id: str, venue_id: str) -> PayEntity | None:
    return (
        db.query(PayEntity)
        .filter(PayEntity.organization_id == org_id, PayEntity.venue_id == venue_id)
        .first()
    )


def is_leave(fact: PayShiftFact) -> bool:
    return bool(fact.leave_request_id) or str(fact.shift_type or "") == "Leave"


def _hours(value) -> float:
    return float(value or 0)


def _num(value) -> float:
    return float(Decimal(str(value or 0)))


# ---------------------------------------------------------------------------
# The terms
# ---------------------------------------------------------------------------


def _timeclock(own: list[PayShiftFact], out: list[PayShiftFact]) -> dict:
    """Σ non-leave cost of the shifts worked at this venue: the entity's own
    staff here plus other entities' staff who worked here (Orbit's timeclock
    mode: worked-at, any payroll)."""
    worked = [f for f in own if not is_leave(f) and not f.is_from_other_venue] + [
        f for f in out if not is_leave(f)
    ]
    by_group = {"bar_floor": 0, "kitchen": 0, "other": 0}
    unapproved = 0
    for f in worked:
        group = f.cost_group if f.cost_group in by_group else "other"
        by_group[group] += int(f.cost_cents or 0)
        if f.is_approved is False:
            unapproved += 1
    return {
        "cents": sum(by_group.values()),
        "hours": round(sum(_hours(f.hours) for f in worked), 2),
        "shifts": len(worked),
        "by_cost_group": by_group,
        "fact_ids": [f.id for f in worked],
        "unapproved": unapproved,
    }


def _security(own: list[PayShiftFact], pay_code: str | None) -> dict:
    code = str(pay_code or "SECURITY").strip().lower()
    rows = [
        f
        for f in own
        if not is_leave(f) and str(f.pay_code or "").strip().lower() == code
    ]
    return {
        "cents": sum(int(f.cost_cents or 0) for f in rows),
        "pay_code": pay_code or "SECURITY",
        "fact_ids": [f.id for f in rows],
    }


def _leave(
    db: Session, org_id: str, venue_id: str, week_start: dt.date, own: list
) -> tuple[dict, list[str]]:
    payouts = (
        db.query(PayLeavePayout)
        .filter(
            PayLeavePayout.organization_id == org_id,
            PayLeavePayout.venue_id == venue_id,
            PayLeavePayout.week_start == week_start,
            PayLeavePayout.rehearsal.is_(False),
        )
        .order_by(PayLeavePayout.staff_name, PayLeavePayout.id)
        .all()
    )
    decided = {p.loaded_request_id for p in payouts}
    leave_facts = [f for f in own if is_leave(f)]
    undecided = sorted(
        {str(f.leave_request_id) for f in leave_facts if f.leave_request_id} - decided
    )
    blockers = []
    if undecided:
        n = len(undecided)
        blockers.append(
            f"{n} leave request{'s' if n != 1 else ''} in the week "
            f"{'have' if n != 1 else 'has'} no payout decided"
        )
    term = {
        "cents": sum(int(p.paid_cents or 0) for p in payouts),
        "requested_cents": sum(int(p.requested_cents or 0) for p in payouts),
        "loaded_leave_cents": sum(int(f.cost_cents or 0) for f in leave_facts),
        "rows": [
            {
                "id": p.id,
                "loaded_request_id": p.loaded_request_id,
                "staff_name": p.staff_name,
                "leave_type": p.leave_type,
                "requested_cents": int(p.requested_cents or 0),
                "paid_cents": int(p.paid_cents or 0),
                "bond_deducted_cents": int(p.bond_deducted_cents or 0),
            }
            for p in payouts
        ],
        "undecided_request_ids": undecided,
    }
    return term, blockers


def _resolve_opening(
    db: Session, org_id: str, venue_id: str, week_start: dt.date
) -> tuple[int, str]:
    """The prior week's closing balance, walking back while rows leave their
    opening NULL ("the prior week's closing")."""
    chain = []
    cursor = week_start
    for _ in range(_ACCRUAL_LOOKBACK):
        prior = (
            db.query(PayAccrual, PayWeek.week_start)
            .join(
                PayWeek,
                (PayWeek.organization_id == PayAccrual.organization_id)
                & (PayWeek.id == PayAccrual.week_id),
            )
            .filter(
                PayAccrual.organization_id == org_id,
                PayWeek.venue_id == venue_id,
                PayWeek.week_start < cursor,
            )
            .order_by(PayWeek.week_start.desc())
            .first()
        )
        if prior is None:
            break
        row, cursor = prior
        chain.append(row)
        if row.opening_cents is not None:
            break
    if not chain:
        return 0, "no prior week"
    # Roll forward from the oldest row found.
    opening = int(chain[-1].opening_cents or 0)
    for row in reversed(chain):
        opening = opening + int(row.accrued_cents or 0) - int(row.paid_cents or 0)
    return opening, "prior week"


def _accruals(db: Session, org_id: str, venue_id: str, week) -> dict:
    row = None
    if week is not None:
        row = (
            db.query(PayAccrual)
            .filter(PayAccrual.organization_id == org_id, PayAccrual.week_id == week.id)
            .first()
        )
    accrued = int(row.accrued_cents or 0) if row else 0
    paid = int(row.paid_cents or 0) if row else 0
    if row is not None and row.opening_cents is not None:
        opening, opening_from = int(row.opening_cents), "row"
    else:
        opening, opening_from = _resolve_opening(
            db, org_id, venue_id, week.week_start if week else dt.date.max
        )
    return {
        "row_id": row.id if row else None,
        "accrued_cents": accrued,
        "paid_cents": paid,
        "opening_cents": opening,
        "closing_cents": opening + accrued - paid,
        "opening_from": opening_from,
    }


def _adjustments(db: Session, org_id: str, week) -> list[PayAdjustment]:
    if week is None:
        return []
    return (
        db.query(PayAdjustment)
        .filter(
            PayAdjustment.organization_id == org_id,
            PayAdjustment.week_id == week.id,
            PayAdjustment.rehearsal.is_(False),
        )
        .order_by(PayAdjustment.sort_order, PayAdjustment.created_at, PayAdjustment.id)
        .all()
    )


def _ace(db: Session, org_id: str, week) -> PayAceInput | None:
    if week is None:
        return None
    return (
        db.query(PayAceInput)
        .filter(
            PayAceInput.organization_id == org_id,
            PayAceInput.week_id == week.id,
            PayAceInput.rehearsal.is_(False),
        )
        .first()
    )


def gratuity_rows(
    db: Session, org_id: str, venue_id: str, week_start: dt.date
) -> tuple[list[tuple[PayGratuityBatch, PayGratuityAllocation]], list[str]]:
    """Active, payable shares of the batches earned at ``venue_id`` and paid
    in this pay week, with the blockers a half-keyed batch raises."""
    batches = (
        db.query(PayGratuityBatch)
        .filter(
            PayGratuityBatch.organization_id == org_id,
            PayGratuityBatch.venue_id == venue_id,
            PayGratuityBatch.week_start == week_start,
            PayGratuityBatch.rehearsal.is_(False),
        )
        .order_by(PayGratuityBatch.created_at, PayGratuityBatch.id)
        .all()
    )
    rows: list[tuple[PayGratuityBatch, PayGratuityAllocation]] = []
    blockers = []
    for batch in batches:
        shares = (
            db.query(PayGratuityAllocation)
            .filter(
                PayGratuityAllocation.organization_id == org_id,
                PayGratuityAllocation.batch_id == batch.id,
                PayGratuityAllocation.excluded_at.is_(None),
                PayGratuityAllocation.is_payable.is_(True),
            )
            .order_by(PayGratuityAllocation.staff_name, PayGratuityAllocation.id)
            .all()
        )
        if not shares:
            blockers.append(
                f"the {batch.period} gratuity batch has no payable share yet"
            )
        rows.extend((batch, a) for a in shares)
    return rows, blockers


def _line_field(line, name: str, default=None):
    if isinstance(line, dict):
        return line.get(name, default)
    return getattr(line, name, default)


def entertainer_rows(
    db: Session, ctx: FinanceCtx, venue_id: str, week_start: dt.date
) -> tuple[list[dict], bool, list[str]]:
    """The entertainer performances paid through this entity's payroll this
    week — ``entertainers.payments.payroll_lines`` (F5). Zero, and said so,
    while that module is not built; a line approved but not yet marked paid
    is a blocker (the rows must be saved before the Tuesday push)."""
    try:
        from app.services.entertainers import payments as ent_payments
    except ModuleNotFoundError as exc:
        if exc.name and (
            exc.name == "app.services.entertainers"
            or exc.name.startswith("app.services.entertainers")
        ):
            return [], False, []
        raise
    lines = ent_payments.payroll_lines(db, ctx, venue_id, week_start) or []
    rows = []
    unpaid = 0
    for line in lines:
        hours = _num(_line_field(line, "hours", 0))
        rate = int(_line_field(line, "rate_cents", 0) or 0)
        amount = _line_field(line, "amount_cents", None)
        amount = int(amount) if amount is not None else int(round(hours * rate))
        status = _line_field(line, "payment_status", None)
        paid_at = _line_field(line, "paid_at", None)
        # A line still pending is "approved — carrying forward" (Orbit's
        # wording): it is not in this run's Ace gross yet, so it is listed
        # but not counted, and it blocks the journal until marked paid.
        pending = status is not None and status != "paid" and not paid_at
        if pending:
            unpaid += 1
        rows.append(
            {
                "performance_id": _line_field(line, "performance_id"),
                "share_id": _line_field(line, "share_id"),
                "performer": _line_field(line, "performer_name")
                or _line_field(line, "performer")
                or _line_field(line, "name"),
                "pay_code": _line_field(line, "pay_code"),
                "hours": round(hours, 2),
                "rate_cents": rate,
                "amount_cents": amount,
                "payment_status": status,
                "included": not pending,
            }
        )
    blockers = []
    if unpaid:
        blockers.append(
            f"{unpaid} entertainer performance{'s' if unpaid != 1 else ''} "
            f"approved for payroll {'are' if unpaid != 1 else 'is'} not marked paid"
        )
    return rows, True, blockers


# ---------------------------------------------------------------------------
# The formula
# ---------------------------------------------------------------------------


def _band(difference: int | None) -> str | None:
    if difference is None:
        return None
    size = abs(difference)
    return "ok" if size < BAND_OK else "near" if size < BAND_NEAR else "off"


def compute(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    week_start: dt.date,
    *,
    mapping_missing: list[str] | None = None,
) -> dict:
    """The reconciliation for one entity-week (§3 of xero-api.md).

    ``mapping_missing`` lets the journal builder add the mapping keys it found
    missing to the blockers without this module reading Xero settings.
    """
    ctx.require("payroll:read")
    require_venue(ctx, venue_id)
    venue = (
        db.query(Venue)
        .filter(Venue.organization_id == ctx.org_id, Venue.id == venue_id)
        .first()
    )
    if venue is None:
        raise NotFound("Venue not found.")
    monday, sunday = week_bounds(week_start)
    entity = entity_of(db, ctx.org_id, venue_id)
    week = find_week(db, ctx.org_id, venue_id, monday)
    snapshot = basis_snapshot(db, ctx.org_id, week.id) if week else None

    blockers: list[str] = []
    warnings: list[str] = []
    if snapshot is None:
        blockers.append("no close snapshot for this week")
        own: list[PayShiftFact] = []
    else:
        own = facts_of(db, ctx.org_id, snapshot.id)
        if snapshot.source != "close":
            blockers.append(
                f"no close snapshot yet — figures are from the {snapshot.source} snapshot"
            )

    picture = ico_svc.for_venue(
        db, ctx.org_id, venue_id, monday, own_facts=own, entity=entity
    )
    timeclock = _timeclock(own, picture["out_facts"])
    if timeclock["unapproved"]:
        n = timeclock["unapproved"]
        warnings.append(
            f"{n} clock-in{'s' if n != 1 else ''} in the snapshot "
            f"{'are' if n != 1 else 'is'} not approved"
        )
    warnings.extend(picture["warnings"])

    security = _security(own, entity.security_pay_code if entity else None)
    leave, leave_blockers = _leave(db, ctx.org_id, venue_id, monday, own)
    blockers.extend(leave_blockers)
    accrual = _accruals(db, ctx.org_id, venue_id, week)
    adjustments = _adjustments(db, ctx.org_id, week)
    for adj in adjustments:
        if not adj.xero_account_code:
            blockers.append(f"adjustment '{adj.label}' has no GL code")
    ent_rows, ent_available, ent_blockers = entertainer_rows(db, ctx, venue_id, monday)
    blockers.extend(ent_blockers)
    grat_rows, grat_blockers = gratuity_rows(db, ctx.org_id, venue_id, monday)
    blockers.extend(grat_blockers)
    ace = _ace(db, ctx.org_id, week)
    if ace is None:
        blockers.append("ACE inputs not entered for this week")
    blockers.extend(f"Xero mapping missing: {k}" for k in (mapping_missing or []))

    terms = {
        "timeclock": timeclock,
        "intercompany": {
            "cents": picture["net_cents"],
            "in_cents": picture["in_cents"],
            "out_cents": picture["out_cents"],
            "counterparties": picture["in"] + picture["out"],
        },
        "leave_paid": leave,
        "security": security,
        "accrued_additions": {
            "cents": accrual["accrued_cents"],
            "row_id": accrual["row_id"],
        },
        "accrued_deductions": {
            "cents": accrual["paid_cents"],
            "row_id": accrual["row_id"],
        },
        "accrual": {
            "opening_cents": accrual["opening_cents"],
            "closing_cents": accrual["closing_cents"],
            "opening_from": accrual["opening_from"],
        },
        "other_adjustments": {
            "cents": sum(int(a.amount_cents or 0) for a in adjustments),
            "rows": [
                {
                    "id": a.id,
                    "label": a.label,
                    "amount_cents": int(a.amount_cents or 0),
                    "xero_account_code": a.xero_account_code,
                    "xero_account_name": a.xero_account_name,
                    "report_target": a.report_target,
                    "notes": a.notes,
                }
                for a in adjustments
            ],
        },
        "entertainers": {
            "cents": sum(r["amount_cents"] for r in ent_rows if r["included"]),
            "rows": ent_rows,
            "available": ent_available,
        },
        "gratuities": {
            "cents": sum(int(a.amount_cents or 0) for _, a in grat_rows),
            "rows": [
                {
                    "allocation_id": a.id,
                    "batch_id": b.id,
                    "period": b.period,
                    "staff_member_id": a.staff_member_id,
                    "staff_name": a.staff_name,
                    "amount_cents": int(a.amount_cents or 0),
                    "payroll_venue_id": a.payroll_venue_id,
                }
                for b, a in grat_rows
            ],
        },
    }
    computed = (
        timeclock["cents"]
        + picture["net_cents"]
        + leave["cents"]
        - security["cents"]
        - accrual["accrued_cents"]
        + accrual["paid_cents"]
        + terms["other_adjustments"]["cents"]
        + terms["entertainers"]["cents"]
        + terms["gratuities"]["cents"]
    )
    difference = computed - int(ace.gross_cents or 0) if ace else None

    from app.services.payroll import xero_pushes

    live = xero_pushes.live_push(
        db, ctx.org_id, venue_id=venue_id, week_start=monday, kind="journal"
    )
    draft = None
    if week is not None:
        draft = (
            db.query(PayJournalDraft.id)
            .filter(
                PayJournalDraft.organization_id == ctx.org_id,
                PayJournalDraft.week_id == week.id,
                PayJournalDraft.kind == "journal",
            )
            .first()
        )
    return {
        "venue_id": venue_id,
        "venue": venue.name,
        "entity": {
            "payroll_on": bool(entity.payroll_on) if entity else False,
            "security_pay_code": entity.security_pay_code if entity else "SECURITY",
            "ico_leave_uplift_bp": int(entity.ico_leave_uplift_bp) if entity else 1200,
            "ico_leave_tax": entity.ico_leave_tax if entity else "account_default",
            "loaded_company_id": entity.loaded_company_id if entity else None,
        },
        "week_id": week.id if week else None,
        "week_start": monday.isoformat(),
        "week_ending": sunday.isoformat(),
        "snapshot": {
            "id": snapshot.id,
            "source": snapshot.source,
            "taken_at": snapshot.taken_at.isoformat() if snapshot.taken_at else None,
            "shift_count": snapshot.shift_count,
            "content_sha256": snapshot.content_sha256,
        }
        if snapshot
        else None,
        "terms": terms,
        "ace": {
            "id": ace.id,
            "gross_cents": int(ace.gross_cents or 0),
            "net_cents": int(ace.net_cents or 0),
            "esct_cents": int(ace.esct_cents or 0),
            "kiwisaver_cents": int(ace.kiwisaver_cents or 0),
            "payroll_taxes_cents": int(ace.gross_cents or 0)
            - int(ace.net_cents or 0)
            + int(ace.esct_cents or 0)
            + int(ace.kiwisaver_cents or 0),
        }
        if ace
        else None,
        "computed_cents": computed,
        "difference_cents": difference,
        "band": _band(difference),
        "blockers": blockers,
        "warnings": warnings,
        "status": "pushed" if live else ("draft" if draft else "none"),
        "live_push": xero_pushes.brief(live) if live else None,
        "notes": week.notes if week else None,
    }


def adjustments_total(recon: dict) -> int:
    """Everything but the timeclock total — the overview's "Adjustments"."""
    return recon["computed_cents"] - recon["terms"]["timeclock"]["cents"]


def group_overview(db: Session, ctx: FinanceCtx, week_start: dt.date) -> dict:
    """One row per payroll entity the caller may see, and the group total."""
    ctx.require("payroll:read")
    monday, sunday = week_bounds(week_start)
    entities = (
        db.query(PayEntity, Venue)
        .join(
            Venue,
            (Venue.organization_id == PayEntity.organization_id)
            & (Venue.id == PayEntity.venue_id),
        )
        .filter(
            PayEntity.organization_id == ctx.org_id,
            PayEntity.payroll_on.is_(True),
            ctx.venue_clause(PayEntity.venue_id),
        )
        .order_by(Venue.name)
        .all()
    )
    rows = []
    for entity, venue in entities:
        recon = compute(db, ctx, venue.id, monday)
        rows.append(
            {
                "venue_id": venue.id,
                "venue": venue.name,
                "timeclock_cents": recon["terms"]["timeclock"]["cents"],
                "adjustments_cents": adjustments_total(recon),
                "ace_gross_cents": recon["ace"]["gross_cents"]
                if recon["ace"]
                else None,
                "computed_cents": recon["computed_cents"],
                "difference_cents": recon["difference_cents"],
                "band": recon["band"],
                "status": recon["status"],
                "blockers": len(recon["blockers"]),
                "snapshot_source": recon["snapshot"]["source"]
                if recon["snapshot"]
                else None,
            }
        )
    ace_known = [r for r in rows if r["ace_gross_cents"] is not None]
    group_diff = sum(r["difference_cents"] for r in ace_known) if ace_known else None
    return {
        "week_start": monday.isoformat(),
        "week_ending": sunday.isoformat(),
        "venues": rows,
        "group": {
            "timeclock_cents": sum(r["timeclock_cents"] for r in rows),
            "adjustments_cents": sum(r["adjustments_cents"] for r in rows),
            "ace_gross_cents": sum(r["ace_gross_cents"] for r in ace_known),
            "computed_cents": sum(r["computed_cents"] for r in rows),
            "difference_cents": group_diff,
            "band": _band(group_diff),
            "entities_without_ace": len(rows) - len(ace_known),
        },
    }


def week_adjustments(
    db: Session, org_id: str, venue_id: str, week_start: dt.date
) -> dict:
    """The week's adjustment totals by Weekly Summary line — what Norm
    Finance adds to actual wages and leave (F6, ``report_target``). Totals
    only: the lines themselves stay behind ``payroll:read``."""
    monday, _ = week_bounds(week_start)
    week = find_week(db, org_id, venue_id, monday)
    out = {"wages_cents": 0, "leave_cents": 0}
    for adj in _adjustments(db, org_id, week):
        if adj.report_target == "wages":
            out["wages_cents"] += int(adj.amount_cents or 0)
        elif adj.report_target == "leave":
            out["leave_cents"] += int(adj.amount_cents or 0)
    return out
