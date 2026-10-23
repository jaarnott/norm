"""The Reconciliation page's typed inputs (plan §9 step 7, §10): the Ace pay
run's totals (``pay_ace_inputs``, one per week), the signed manual lines
(``pay_adjustments``) and the accrual (``pay_accruals``, one per week). F4's
reconciliation computes from them; this module only keeps them, under the
shadow rule (``weeks.require_writable``) and the row's ``version``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import _now
from app.db.payroll_models import (
    REPORT_TARGETS,
    PayAccrual,
    PayAceInput,
    PayAdjustment,
    PayWeek,
)
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush
from app.services.payroll import weeks as weeks_svc


def _cents(value, what: str, *, signed: bool = False) -> int:
    if value is None or value == "" or isinstance(value, bool):
        raise Invalid(f"{what} must be a whole number of cents.")
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise Invalid(f"{what} must be a whole number of cents.")
    if out < 0 and not signed:
        raise Invalid(f"{what} cannot be negative.")
    return out


# ============================================================= ACE inputs ==


def get_ace_inputs(db: Session, ctx: FinanceCtx, week: PayWeek) -> PayAceInput | None:
    return (
        db.query(PayAceInput)
        .filter(
            PayAceInput.organization_id == ctx.org_id, PayAceInput.week_id == week.id
        )
        .first()
    )


def set_ace_inputs(
    db: Session,
    ctx: FinanceCtx,
    week: PayWeek,
    *,
    gross_cents,
    net_cents,
    esct_cents,
    kiwisaver_cents,
    rehearsal: bool = False,
    version: int | None = None,
) -> PayAceInput:
    ctx.require("payroll:write")
    weeks_svc.require_writable(ctx, rehearsal=rehearsal)
    row = get_ace_inputs(db, ctx, week)
    if row is None:
        row = PayAceInput(organization_id=ctx.org_id, week_id=week.id, source="norm")
        db.add(row)
    else:
        if version is not None and row.version != version:
            raise Conflict(
                "The ACE inputs were changed by someone else — reload and try again."
            )
        row.version = (row.version or 1) + 1
        row.source = "norm"
    row.gross_cents = _cents(gross_cents, "gross_cents")
    row.net_cents = _cents(net_cents, "net_cents")
    row.esct_cents = _cents(esct_cents, "esct_cents")
    row.kiwisaver_cents = _cents(kiwisaver_cents, "kiwisaver_cents")
    row.entered_by = ctx.user_id
    row.entered_at = _now()
    row.rehearsal = bool(rehearsal)
    flush(db)
    return row


def ace_inputs_dict(row: PayAceInput | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "week_id": row.week_id,
        "gross_cents": row.gross_cents,
        "net_cents": row.net_cents,
        "esct_cents": row.esct_cents,
        "kiwisaver_cents": row.kiwisaver_cents,
        # Payroll taxes payable = gross − net + ESCT + KiwiSaver (plan §10).
        "payroll_taxes_cents": row.gross_cents
        - row.net_cents
        + row.esct_cents
        + row.kiwisaver_cents,
        "entered_by": row.entered_by,
        "entered_at": row.entered_at.isoformat() if row.entered_at else None,
        "source": row.source,
        "rehearsal": row.rehearsal,
        "version": row.version,
    }


# ============================================================ adjustments ==


def list_adjustments(
    db: Session, ctx: FinanceCtx, week: PayWeek
) -> list[PayAdjustment]:
    return (
        db.query(PayAdjustment)
        .filter(
            PayAdjustment.organization_id == ctx.org_id,
            PayAdjustment.week_id == week.id,
        )
        .order_by(PayAdjustment.sort_order, PayAdjustment.created_at)
        .all()
    )


def get_adjustment(db: Session, ctx: FinanceCtx, adjustment_id: str) -> PayAdjustment:
    row = (
        db.query(PayAdjustment)
        .filter(
            PayAdjustment.organization_id == ctx.org_id,
            PayAdjustment.id == adjustment_id,
        )
        .first()
    )
    if row is None:
        raise NotFound("Adjustment not found.")
    weeks_svc.get(db, ctx, row.week_id)
    return row


def _apply_adjustment(row: PayAdjustment, fields: dict) -> None:
    if "label" in fields:
        label = str(fields.get("label") or "").strip()
        if not label:
            raise Invalid("An adjustment needs a label.")
        row.label = label
    if "amount_cents" in fields:
        row.amount_cents = _cents(fields["amount_cents"], "amount_cents", signed=True)
    if "report_target" in fields:
        target = fields["report_target"]
        if target is not None and target not in REPORT_TARGETS:
            raise Invalid(
                f"report_target is one of {', '.join(REPORT_TARGETS)} or null."
            )
        row.report_target = target
    for name in ("xero_account_code", "xero_account_name", "notes"):
        if name in fields:
            value = fields[name]
            row.__setattr__(
                name, str(value).strip() or None if value is not None else None
            )
    if "sort_order" in fields:
        try:
            row.sort_order = int(fields["sort_order"] or 0)
        except (TypeError, ValueError):
            raise Invalid("sort_order must be a whole number.")


def add_adjustment(
    db: Session, ctx: FinanceCtx, week: PayWeek, *, rehearsal: bool = False, **fields
) -> PayAdjustment:
    ctx.require("payroll:write")
    weeks_svc.require_writable(ctx, rehearsal=rehearsal)
    if "label" not in fields or "amount_cents" not in fields:
        raise Invalid("An adjustment needs a label and an amount.")
    row = PayAdjustment(
        organization_id=ctx.org_id,
        week_id=week.id,
        label="",
        amount_cents=0,
        created_by=ctx.user_id,
        source="norm",
        rehearsal=bool(rehearsal),
    )
    _apply_adjustment(row, fields)
    db.add(row)
    flush(db)
    return row


def update_adjustment(
    db: Session, ctx: FinanceCtx, adjustment_id: str, *, version: int, **fields
) -> PayAdjustment:
    ctx.require("payroll:write")
    row = get_adjustment(db, ctx, adjustment_id)
    weeks_svc.require_writable(ctx, rehearsal=bool(row.rehearsal))
    if row.version != version:
        raise Conflict(
            "This adjustment was changed by someone else — reload and try again."
        )
    _apply_adjustment(row, fields)
    row.source = "norm"
    row.version = version + 1
    flush(db)
    return row


def delete_adjustment(db: Session, ctx: FinanceCtx, adjustment_id: str) -> None:
    ctx.require("payroll:write")
    row = get_adjustment(db, ctx, adjustment_id)
    weeks_svc.require_writable(ctx, rehearsal=bool(row.rehearsal))
    db.delete(row)
    flush(db)


def adjustment_dict(row: PayAdjustment) -> dict:
    return {
        "id": row.id,
        "week_id": row.week_id,
        "label": row.label,
        "amount_cents": row.amount_cents,
        "xero_account_code": row.xero_account_code,
        "xero_account_name": row.xero_account_name,
        "report_target": row.report_target,
        "notes": row.notes,
        "sort_order": row.sort_order,
        "created_by": row.created_by,
        "source": row.source,
        "rehearsal": row.rehearsal,
        "version": row.version,
    }


# =============================================================== accruals ==


def get_accrual(db: Session, ctx: FinanceCtx, week: PayWeek) -> PayAccrual | None:
    return (
        db.query(PayAccrual)
        .filter(PayAccrual.organization_id == ctx.org_id, PayAccrual.week_id == week.id)
        .first()
    )


def set_accrual(
    db: Session,
    ctx: FinanceCtx,
    week: PayWeek,
    *,
    opening_cents,
    accrued_cents,
    paid_cents,
    rehearsal: bool = False,
) -> PayAccrual:
    ctx.require("payroll:write")
    weeks_svc.require_writable(ctx, rehearsal=rehearsal)
    row = get_accrual(db, ctx, week)
    if row is None:
        row = PayAccrual(organization_id=ctx.org_id, week_id=week.id, source="norm")
        db.add(row)
    row.source = "norm"
    row.opening_cents = (
        None
        if opening_cents in (None, "")
        else _cents(opening_cents, "opening_cents", signed=True)
    )
    row.accrued_cents = _cents(accrued_cents, "accrued_cents")
    row.paid_cents = _cents(paid_cents, "paid_cents")
    flush(db)
    return row


def prior_closing(db: Session, ctx: FinanceCtx, week: PayWeek) -> int | None:
    """The previous week's closing accrual at this entity, for an opening
    left NULL ("the prior week's closing")."""
    previous = (
        db.query(PayWeek)
        .filter(
            PayWeek.organization_id == ctx.org_id,
            PayWeek.venue_id == week.venue_id,
            PayWeek.week_start < week.week_start,
        )
        .order_by(PayWeek.week_start.desc())
        .first()
    )
    if previous is None:
        return None
    row = get_accrual(db, ctx, previous)
    if row is None:
        return None
    opening = (
        row.opening_cents
        if row.opening_cents is not None
        else (prior_closing(db, ctx, previous) or 0)
    )
    return opening + row.accrued_cents - row.paid_cents


def accrual_dict(
    db: Session, ctx: FinanceCtx, week: PayWeek, row: PayAccrual | None
) -> dict | None:
    if row is None:
        return None
    opening = (
        row.opening_cents
        if row.opening_cents is not None
        else prior_closing(db, ctx, week)
    )
    return {
        "id": row.id,
        "week_id": row.week_id,
        "opening_cents": row.opening_cents,
        "opening_effective_cents": opening or 0,
        "accrued_cents": row.accrued_cents,
        "paid_cents": row.paid_cents,
        "closing_cents": (opening or 0) + row.accrued_cents - row.paid_cents,
        "source": row.source,
    }
