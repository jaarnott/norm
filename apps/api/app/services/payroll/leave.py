"""Leave (plan §9 step 3): what Loaded says a leave request is worth
("requested") beside what payroll decided to pay ("paid"), one
``pay_leave_payouts`` row per request per pay week, decided by one of three
workflows. Norm never writes to Loaded here.

* **annual** — ``paid = min(requested, holiday pay available − bond)``; the
  balance is typed (it lives in Ace); the $150 uniform bond is deducted only
  with a written consent on file (``bond_consent_file_id``) or stated as
  held in Ace (Wages Protection Act 1983 s 5, plan §9.1).
* **sick** — four gates: qualification (six months' current continuous
  employment, ≥ 10 h/week on average, ≥ 1 h every week or ≥ 40 h every
  month — the dated ``pay_rules`` thresholds, computed from the person's
  start date and their OWN look-back of clock-ins), balance (days × the
  hours that person would otherwise work a day — not a flat 8), certificate,
  and the pay-out (per-day hours × rate). ``paid`` is 0 unless every gate
  passes; each gate takes a named override with a note.
* **generic** — paid ≤ requested.

Sick-leave inputs and notes are health information (Health Information
Privacy Code 2020): ``payroll:write`` to read, and every read is logged.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from app.db.models import Organization, _now
from app.db.payroll_models import (
    LEAVE_WORKFLOW_KINDS,
    PayFile,
    PayLeavePayout,
)
from app.services.business_calendar import timezone_for
from app.services.finance_core import access_log
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush
from app.services.payroll import _email, rules as rules_svc, snapshot as snapshot_svc
from app.services.payroll import entities as entities_svc
from app.services.payroll import weeks as weeks_svc

logger = logging.getLogger(__name__)

SICK_EMAILS = {
    "approver": "payroll_sick_approver",
    "staff": "payroll_sick_staff",
    "certificate": "payroll_sick_certificate",
}


# ================================================================ helpers ==


def workflow_kind_for(leave_type: str | None) -> str:
    """Orbit's rule, kept: the type's name decides the workflow."""
    name = (leave_type or "").strip().lower()
    if "annual" in name:
        return "annual"
    if "sick" in name:
        return "sick"
    return "generic"


def _cents(value, what: str, *, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise Invalid(f"{what} must be a whole number of cents.")
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise Invalid(f"{what} must be a whole number of cents.")
    if out < 0:
        raise Invalid(f"{what} cannot be negative.")
    return out


def _number(value, what: str, *, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise Invalid(f"{what} must be a number.")
    if out < 0:
        raise Invalid(f"{what} cannot be negative.")
    return out


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


# ============================================================== decisions ==


def decide_annual(
    requested_cents: int,
    inputs: dict,
    *,
    entity,
    paid_override: int | None,
    has_consent_file: bool,
) -> dict:
    holiday_pay = _cents(
        inputs.get("holiday_pay_available_cents"), "holiday_pay_available_cents"
    )
    if holiday_pay is None:
        raise Invalid("Annual leave needs the holiday pay available (it lives in Ace).")
    bond = _cents(
        inputs.get("uniform_bond_cents"),
        "uniform_bond_cents",
        default=int(entity.uniform_bond_cents or 0),
    )
    held_in_ace = _flag(inputs.get("bond_held_in_ace"))
    file_id = _text(inputs.get("bond_consent_file_id"))
    if bond > 0 and not (has_consent_file or held_in_ace):
        raise Invalid(
            "The uniform bond can only be deducted from holiday pay with the "
            "person's written consent — attach the consent file or state that it "
            "is held in Ace (Wages Protection Act 1983 s 5)."
        )
    max_payable = max(0, holiday_pay - bond)
    calculated = min(requested_cents, max_payable)
    paid = calculated if paid_override is None else paid_override
    if paid > max_payable:
        raise Invalid(
            f"Paid amount exceeds the holiday pay available after the bond "
            f"({max_payable} cents)."
        )
    if paid > requested_cents:
        raise Invalid("Paid amount exceeds the requested amount.")
    bond_deducted = min(bond, holiday_pay) if bond > 0 else 0
    return {
        "paid_cents": paid,
        "bond_deducted_cents": bond_deducted,
        "bond_consent_file_id": file_id if has_consent_file else None,
        "inputs": {
            "holiday_pay_available_cents": holiday_pay,
            "uniform_bond_cents": bond,
            "bond_consent_file_id": file_id if has_consent_file else None,
            "bond_held_in_ace": held_in_ace,
            "decision": {
                "max_payable_cents": max_payable,
                "calculated_cents": calculated,
                "bond_consent": "file"
                if has_consent_file
                else ("held_in_ace" if held_in_ace else None),
            },
        },
    }


def decide_sick(
    requested_cents: int,
    inputs: dict,
    *,
    default_day_hours: float,
    paid_override: int | None,
) -> dict:
    qual = dict(inputs.get("qualification") or {})
    bal = dict(inputs.get("balance") or {})
    cert = dict(inputs.get("certificate") or {})
    pay = dict(inputs.get("payout") or {})

    day_hours = []
    for row in pay.get("day_hours") or []:
        if not isinstance(row, dict) or not row.get("date"):
            raise Invalid("Each sick-leave day needs a date and the hours.")
        day_hours.append(
            {
                "date": str(row["date"])[:10],
                "hours": _number(row.get("hours"), "hours", default=0.0),
            }
        )
    requested_hours = round(sum(d["hours"] for d in day_hours), 2)
    daily_hours = _number(bal.get("daily_hours"), "daily_hours") or default_day_hours
    available_days = _number(bal.get("available_days"), "available_days", default=0.0)
    available_hours = round(available_days * daily_hours, 2)

    qual_pass = _flag(qual.get("override")) or (
        _flag(qual.get("six_months"))
        and _flag(qual.get("avg_hours"))
        and _flag(qual.get("continuous"))
    )
    balance_pass = _flag(bal.get("override")) or (
        available_hours + 1e-6 >= requested_hours
    )
    cert_pass = _flag(cert.get("override")) or _flag(cert.get("received"))
    all_pass = qual_pass and balance_pass and cert_pass

    rate = _cents(pay.get("hourly_rate_cents"), "hourly_rate_cents")
    if rate is None:
        rate = (
            int(round(requested_cents / requested_hours)) if requested_hours > 0 else 0
        )
    calculated = int(round(sum(d["hours"] * rate for d in day_hours)))
    paid = calculated if paid_override is None else paid_override
    if not all_pass:
        paid = 0
    return {
        "paid_cents": paid,
        "bond_deducted_cents": 0,
        "bond_consent_file_id": None,
        "inputs": {
            "qualification": {
                "employment_start_date": _text(qual.get("employment_start_date")),
                "six_months": _flag(qual.get("six_months")),
                "avg_hours": _flag(qual.get("avg_hours")),
                "continuous": _flag(qual.get("continuous")),
                "override": _flag(qual.get("override")),
                "override_notes": _text(qual.get("override_notes")),
                "computed": qual.get("computed")
                if isinstance(qual.get("computed"), dict)
                else None,
            },
            "balance": {
                "available_days": available_days,
                "daily_hours": daily_hours,
                "override": _flag(bal.get("override")),
                "override_notes": _text(bal.get("override_notes")),
            },
            "certificate": {
                "received": _flag(cert.get("received")),
                "override": _flag(cert.get("override")),
                "override_notes": _text(cert.get("override_notes")),
            },
            "payout": {"day_hours": day_hours, "hourly_rate_cents": rate},
            "decision": {
                "qualification": qual_pass,
                "balance": balance_pass,
                "certificate": cert_pass,
                "all_gates_passed": all_pass,
                "requested_hours": requested_hours,
                "available_hours": available_hours,
                "daily_hours": daily_hours,
                "hourly_rate_cents": rate,
                "calculated_cents": calculated,
            },
        },
    }


def decide_generic(requested_cents: int, paid_override: int | None) -> dict:
    paid = requested_cents if paid_override is None else paid_override
    if paid > requested_cents:
        raise Invalid("Paid amount exceeds the requested amount.")
    return {
        "paid_cents": paid,
        "bond_deducted_cents": 0,
        "bond_consent_file_id": None,
        "inputs": {"decision": {"calculated_cents": requested_cents}},
    }


# ================================================================ payouts ==


def _consent_file(db: Session, ctx: FinanceCtx, file_id: str | None) -> bool:
    if not file_id:
        return False
    row = (
        db.query(PayFile.id)
        .filter(
            PayFile.organization_id == ctx.org_id,
            PayFile.id == file_id,
            PayFile.kind == "bond_consent",
        )
        .first()
    )
    if row is None:
        raise NotFound("Bond consent file not found.")
    return True


def record_payout(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str,
    week_start,
    loaded_request_id: str,
    staff_member_id: str | None = None,
    staff_name: str | None = None,
    leave_type: str | None = None,
    requested_cents: int = 0,
    workflow_kind: str | None = None,
    inputs: dict | None = None,
    paid_cents: int | None = None,
    notes: str | None = None,
    rehearsal: bool = False,
    payout_id: str | None = None,
    version: int | None = None,
) -> PayLeavePayout:
    """Create or update the decision for one request in one week
    (``payroll:write``; the shadow rule with ``rehearsal``)."""
    ctx.require("payroll:write")
    weeks_svc.require_writable(ctx, rehearsal=rehearsal)
    venue = weeks_svc.venue_of(db, ctx, venue_id)
    week_start = weeks_svc.validate_week_start(week_start)
    loaded_request_id = _text(loaded_request_id)
    if not loaded_request_id:
        raise Invalid("A payout names the Loaded leave request it decides.")
    requested = _cents(requested_cents, "requested_cents", default=0)
    kind = workflow_kind or workflow_kind_for(leave_type)
    if kind not in LEAVE_WORKFLOW_KINDS:
        raise Invalid(f"workflow_kind is one of {', '.join(LEAVE_WORKFLOW_KINDS)}.")
    inputs = dict(inputs or {})
    paid_override = _cents(paid_cents, "paid_cents")
    entity = entities_svc.get_or_create(db, ctx, venue.id)

    if kind == "annual":
        decided = decide_annual(
            requested,
            inputs,
            entity=entity,
            paid_override=paid_override,
            has_consent_file=_consent_file(
                db, ctx, _text(inputs.get("bond_consent_file_id"))
            ),
        )
    elif kind == "sick":
        on_date = week_start
        default_day = float(
            rules_svc.params_for(db, ctx.org_id, "sick_day_hours", on_date).get("hours")
            or 8
        )
        decided = decide_sick(
            requested,
            inputs,
            default_day_hours=default_day,
            paid_override=paid_override,
        )
    else:
        decided = decide_generic(requested, paid_override)

    if payout_id:
        payout = get_payout(db, ctx, payout_id)
    else:
        payout = (
            db.query(PayLeavePayout)
            .filter(
                PayLeavePayout.organization_id == ctx.org_id,
                PayLeavePayout.loaded_request_id == loaded_request_id,
                PayLeavePayout.week_start == week_start,
            )
            .first()
        )
    if payout is None:
        payout = PayLeavePayout(
            organization_id=ctx.org_id,
            venue_id=venue.id,
            week_start=week_start,
            loaded_request_id=loaded_request_id,
            source="norm",
        )
        db.add(payout)
    else:
        if version is not None and payout.version != version:
            raise Conflict(
                "This payout was changed by someone else since you opened it — "
                "reload and try again."
            )
        if payout.source != "norm":
            # An Orbit-imported decision becomes Norm's once a person edits it.
            payout.source = "norm"
        payout.version = (payout.version or 1) + 1
        payout.venue_id = venue.id
    payout.staff_member_id = _text(staff_member_id) or payout.staff_member_id
    payout.staff_name = _text(staff_name) or payout.staff_name
    payout.leave_type = _text(leave_type) or payout.leave_type
    payout.requested_cents = requested
    payout.paid_cents = decided["paid_cents"]
    payout.bond_deducted_cents = decided["bond_deducted_cents"]
    payout.bond_consent_file_id = decided["bond_consent_file_id"]
    payout.workflow_kind = kind
    payout.workflow_inputs = decided["inputs"]
    payout.notes = _text(notes)
    payout.decided_by = ctx.user_id
    payout.decided_at = _now()
    payout.rehearsal = bool(rehearsal)
    flush(db)
    return payout


def get_payout(db: Session, ctx: FinanceCtx, payout_id: str) -> PayLeavePayout:
    payout = (
        db.query(PayLeavePayout)
        .filter(
            PayLeavePayout.organization_id == ctx.org_id, PayLeavePayout.id == payout_id
        )
        .first()
    )
    if payout is None or not ctx.can_see_venue(payout.venue_id):
        raise NotFound("Payout not found.")
    return payout


def list_payouts(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str | None = None,
    week_start=None,
    include_rehearsal: bool = True,
) -> list[PayLeavePayout]:
    q = db.query(PayLeavePayout).filter(PayLeavePayout.organization_id == ctx.org_id)
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(PayLeavePayout.venue_id == venue_id)
    else:
        q = q.filter(ctx.venue_clause(PayLeavePayout.venue_id))
    if week_start is not None:
        q = q.filter(
            PayLeavePayout.week_start == weeks_svc.validate_week_start(week_start)
        )
    if not include_rehearsal:
        q = q.filter(PayLeavePayout.rehearsal.is_(False))
    return q.order_by(PayLeavePayout.week_start.desc(), PayLeavePayout.staff_name).all()


def delete_payout(db: Session, ctx: FinanceCtx, payout_id: str) -> None:
    ctx.require("payroll:write")
    payout = get_payout(db, ctx, payout_id)
    weeks_svc.require_writable(ctx, rehearsal=bool(payout.rehearsal))
    db.delete(payout)
    flush(db)


def payout_dict(payout: PayLeavePayout, ctx: FinanceCtx) -> dict:
    """A payout as the pages see it. Sick-leave inputs and notes are health
    information: shown only to ``payroll:write`` holders, and the read is
    logged (plan §6.5 item 3)."""
    restricted = payout.workflow_kind == "sick" and not ctx.holds("payroll:write")
    inputs = payout.workflow_inputs
    notes = payout.notes
    if restricted:
        inputs, notes = {"restricted": True}, None
    elif payout.workflow_kind == "sick":
        logger.info(
            "sick-leave record read: payout=%s staff=%s by user=%s",
            payout.id,
            payout.staff_member_id,
            ctx.user_id or ctx.actor_label,
        )
    consent = None
    if isinstance(payout.workflow_inputs, dict):
        consent = (payout.workflow_inputs.get("decision") or {}).get("bond_consent")
    return {
        "id": payout.id,
        "venue_id": payout.venue_id,
        "week_start": payout.week_start.isoformat(),
        "loaded_request_id": payout.loaded_request_id,
        "staff_member_id": payout.staff_member_id,
        "staff_name": payout.staff_name,
        "leave_type": payout.leave_type,
        "workflow_kind": payout.workflow_kind,
        "requested_cents": payout.requested_cents,
        "paid_cents": payout.paid_cents,
        "bond_deducted_cents": payout.bond_deducted_cents,
        "bond_consent_file_id": payout.bond_consent_file_id,
        "bond_consent": consent,
        "workflow_inputs": inputs,
        "notes": notes,
        "decided_by": payout.decided_by,
        "decided_at": payout.decided_at.isoformat() if payout.decided_at else None,
        "source": payout.source,
        # Orbit's hundred legacy rows carried no pay week; the importer
        # inferred it from the Tuesday they were keyed (F7) — say so.
        "week_inferred": payout.source == "orbit_legacy",
        "rehearsal": payout.rehearsal,
        "version": payout.version,
    }


# ============================================================== requested ==


def requested(
    db: Session, ctx: FinanceCtx, venue_id: str, week_start, *, config_db=None
) -> dict:
    """The week's leave as Loaded prices it (``get_payrun view=leave``), each
    row beside the stored payout for that request and week."""
    ctx.require("payroll:read")
    venue = weeks_svc.venue_of(db, ctx, venue_id)
    week_start = weeks_svc.validate_week_start(week_start)
    result = snapshot_svc.payrun(
        db, ctx, venue, week_start, view="leave", config_db=config_db
    )
    section = (result.get("sections") or {}).get("leave") or {}
    payouts = {
        p.loaded_request_id: p
        for p in list_payouts(db, ctx, venue_id=venue.id, week_start=week_start)
    }
    rows = []
    total_requested = total_paid = 0
    for r in section.get("rows") or []:
        if not isinstance(r, dict):
            continue
        request_id = r.get("leave_request_id") or ""
        payout = payouts.get(request_id)
        requested_cents = int(r.get("cost_cents") or 0)
        total_requested += requested_cents
        if payout is not None:
            total_paid += payout.paid_cents
        rows.append(
            {
                "leave_request_id": request_id or None,
                "staff_member_id": r.get("staff_member_id"),
                "staff_name": r.get("staff_name"),
                "pay_code": r.get("pay_code"),
                "leave_type_name": r.get("leave_type_name"),
                "dates": r.get("dates") or [],
                "hours": r.get("hours"),
                "requested_cents": requested_cents,
                "is_paid_type": r.get("is_paid_type"),
                "workflow_kind": workflow_kind_for(r.get("leave_type_name")),
                "payout": payout_dict(payout, ctx) if payout is not None else None,
            }
        )
    return {
        "venue_id": venue.id,
        "week_start": week_start.isoformat(),
        "rows": rows,
        "totals": {"requested_cents": total_requested, "paid_cents": total_paid},
        "notes": result.get("notes") or [],
    }


# ============================================================ eligibility ==


def _months_between(start: dt.date, end: dt.date) -> float:
    return round((end - start).days / 30.4375, 2)


def sick_eligibility(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    staff_member_id: str,
    as_of,
    *,
    start_date=None,
    config_db=None,
) -> dict:
    """The qualification facts for one person: the dated rule's thresholds
    against their start date (``datestampStarted`` from Loaded's staff
    detail unless the approver gives one) and their OWN clock-ins over the
    look-back (``get_payrun weeks=N staff_member_ids=[id]``)."""
    ctx.require("payroll:read")
    venue = weeks_svc.venue_of(db, ctx, venue_id)
    staff_member_id = _text(staff_member_id)
    if not staff_member_id:
        raise Invalid("staff_member_id is required.")
    as_of = (
        as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of)[:10])
    )
    rule = rules_svc.params_for(db, ctx.org_id, "sick_eligibility", as_of)
    rule_row = rules_svc.rule_for(db, ctx.org_id, "sick_eligibility", as_of)
    lookback = int(rule.get("lookback_weeks") or 26)

    start: dt.date | None = None
    source = None
    if start_date:
        start = (
            start_date
            if isinstance(start_date, dt.date)
            else dt.date.fromisoformat(str(start_date)[:10])
        )
        source = "approver"
    else:
        try:
            detail = snapshot_svc.loaded(
                db,
                ctx,
                venue,
                "get_staff_member_raw",
                {"staff_member_id": staff_member_id},
                config_db=config_db,
            )
            access_log.record(
                db,
                ctx,
                subject_kind="staff_member",
                subject_id=staff_member_id,
                field="staff_detail",
                route="payroll.leave.eligibility",
            )
            started = (
                detail.get("datestampStarted") if isinstance(detail, dict) else None
            )
            if started:
                start = dt.date.fromisoformat(str(started)[:10])
                source = "loaded"
            # Only the start date is kept from a payload that also carries
            # IRD, bank and KiwiSaver fields; the read itself is logged.
            logger.info(
                "staff detail read for sick-leave eligibility: staff=%s by user=%s",
                staff_member_id,
                ctx.user_id or ctx.actor_label,
            )
        except Invalid as exc:
            logger.info(
                "staff detail unavailable for %s: %s", staff_member_id, exc.message
            )

    week_start = weeks_svc.monday_of(as_of)
    result = snapshot_svc.payrun(
        db,
        ctx,
        venue,
        week_start,
        view="staff",
        config_db=config_db,
        weeks=lookback,
        staff_member_ids=[staff_member_id],
        rows="true",
    )
    by_week: dict[dt.date, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    days: set[str] = set()
    total = 0.0
    for r in result.get("rows") or []:
        if not isinstance(r, dict) or r.get("shift_type") == "Leave":
            continue
        if str(r.get("staff_member_id") or "") != staff_member_id:
            continue
        day = r.get("trading_date")
        if not day:
            continue
        d = dt.date.fromisoformat(str(day)[:10])
        hours = float(r.get("hours") or 0)
        if hours <= 0:
            continue
        by_week[weeks_svc.monday_of(d)] += hours
        by_month[d.strftime("%Y-%m")] += hours
        days.add(str(day)[:10])
        total += hours

    first_monday = week_start - dt.timedelta(days=7 * (lookback - 1))
    weeks = [first_monday + dt.timedelta(days=7 * i) for i in range(lookback)]
    if start is not None:
        weeks = [w for w in weeks if w + dt.timedelta(days=6) >= start] or weeks
    months = sorted({w.strftime("%Y-%m") for w in weeks})
    weekly = [by_week.get(w, 0.0) for w in weeks]
    avg = round(total / max(len(weeks), 1), 2)
    every_week = all(h >= float(rule.get("min_hours_per_week") or 1) for h in weekly)
    every_month = all(
        by_month.get(m, 0.0) >= float(rule.get("min_hours_per_month") or 40)
        for m in months
    )
    months_employed = _months_between(start, as_of) if start is not None else None
    usual_daily = round(total / len(days), 2) if days else None
    return {
        "staff_member_id": staff_member_id,
        "as_of": as_of.isoformat(),
        "employment_start_date": start.isoformat() if start else None,
        "start_date_source": source,
        "months_employed": months_employed,
        "lookback_weeks": lookback,
        "weeks_counted": len(weeks),
        "weeks_with_hours": sum(1 for h in weekly if h >= 1),
        "total_hours": round(total, 2),
        "avg_hours_per_week": avg,
        "usual_daily_hours": usual_daily,
        "qualifies_six_months": (
            months_employed is not None
            and months_employed >= float(rule.get("months_employed") or 6)
        ),
        "qualifies_avg_hours": avg >= float(rule.get("avg_hours_per_week") or 10),
        "qualifies_continuous": bool(weeks) and (every_week or every_month),
        "rule": {
            **rule,
            "effective_from": rule_row.effective_from.isoformat() if rule_row else None,
        },
    }


# ================================================================= emails ==


def send_sick_email(
    db: Session, ctx: FinanceCtx, payout: PayLeavePayout, *, kind: str, to: str
) -> dict:
    """One of the three sick-leave notes (Orbit's copy-to-clipboard texts as
    templates). In shadow it goes to the payroll contact instead."""
    ctx.require("payroll:write")
    template = SICK_EMAILS.get(kind)
    if template is None:
        raise Invalid(f"kind is one of {', '.join(SICK_EMAILS)}.")
    if not _text(to):
        raise Invalid("A recipient is required.")
    venue = weeks_svc.venue_of(db, ctx, payout.venue_id)
    entity = entities_svc.get_or_create(db, ctx, venue.id)
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    decision = (payout.workflow_inputs or {}).get("decision") or {}
    context = {
        "org_name": org.name if org else "",
        "venue_name": venue.name,
        "staff_name": payout.staff_name or "the staff member",
        "approver_name": entity.sick_approver_name or "the payroll approver",
        "week_start": payout.week_start.isoformat(),
        "qualification": decision.get("qualification"),
        "balance": decision.get("balance"),
        "certificate": decision.get("certificate"),
        "subject": f"Sick leave — {payout.staff_name or 'staff member'} ({venue.name})",
    }
    return _email.send(db, ctx, template, context, to=to.strip(), venue_id=venue.id)


# ================================================================== files ==


def store_bond_consent(
    db: Session,
    ctx: FinanceCtx,
    *,
    data: bytes,
    filename: str | None,
    content_type: str | None,
) -> PayFile:
    """A signed uniform-bond consent (PDF only, through the PDF check);
    content-addressed per organisation."""
    ctx.require("payroll:write")
    from app.services.files.pdf_check import check_pdf

    check_pdf(data)
    digest = hashlib.sha256(data).hexdigest()
    existing = (
        db.query(PayFile)
        .filter(PayFile.organization_id == ctx.org_id, PayFile.sha256 == digest)
        .first()
    )
    if existing is not None:
        return existing
    row = PayFile(
        organization_id=ctx.org_id,
        sha256=digest,
        kind="bond_consent",
        filename=_text(filename),
        content_type="application/pdf",
        size_bytes=len(data),
        data=data,
        created_by=ctx.user_id,
    )
    db.add(row)
    flush(db)
    return row


def local_date(moment: dt.datetime | None, venue) -> str | None:
    """The venue-local calendar date of an instant (the leave file's day)."""
    if moment is None:
        return None
    return moment.astimezone(timezone_for(venue)).date().isoformat()
