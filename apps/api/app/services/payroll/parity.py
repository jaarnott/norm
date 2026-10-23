"""Parity for the payroll module during the shadow period (plan §14): Norm's
figures beside Orbit's, every difference named, through
``finance_core.parity.record_run``.

Module ``payroll`` — the exports: each stored Ace file byte-diffed against
the owner's file for the week (uploaded on the Shadow tab), the close
snapshot's totals against Loaded's own pay summary, and the clock-ins the
snapshot dropped (Orbit mapped them as $0 rows). Module ``inputs`` — the
rehearsal weeks: Norm-keyed payouts, gratuity shares, ACE inputs,
adjustments and accruals (``source = norm``) against the rows the nightly
importer carried from Orbit (``source = orbit``).

The weekly run (``finance.parity_weekly``, Wednesday 04:00, after the
importer) records both modules for every entity's last completed week and
emails the payroll contact a summary that lists every failed job run of the
week (``payroll_parity``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging

from sqlalchemy.orm import Session

from app.db.finance_models import FinJobRun, FinParityItem, FinParityRun
from app.db.models import Organization, Venue
from app.db.payroll_models import (
    EXPORT_KINDS,
    PayAccrual,
    PayAceInput,
    PayAdjustment,
    PayGratuityAllocation,
    PayGratuityBatch,
    PayLeavePayout,
    PayWeek,
)
from app.services.finance_core import jobs as jobs_svc
from app.services.finance_core import parity as core_parity
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import FinanceError, Invalid
from app.services.payroll import (
    _email,
    exports as exports_svc,
    snapshot as snapshot_svc,
)
from app.services.payroll import weeks as weeks_svc

logger = logging.getLogger(__name__)

TEMPLATE = "payroll_parity"
#: Lines of one differing file an item lists before it stops.
MAX_LINE_ITEMS = 40


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lines(text: str) -> list[str]:
    return [ln for ln in text.replace("\r\n", "\n").split("\n") if ln != ""]


def _style_only(a: list[str], b: list[str]) -> bool:
    """Do the two leave files differ only in the style number of each line?"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        xa, ya = x.split(",", 1), y.split(",", 1)
        if len(xa) != 2 or len(ya) != 2 or xa[1] != ya[1]:
            return False
    return True


def _cents_only(a: list[str], b: list[str], tolerance: int = 2) -> bool:
    """Do two gratuity files differ only by a cent or two per line?"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        xa, ya = x.rsplit(",", 1), y.rsplit(",", 1)
        if len(xa) != 2 or len(ya) != 2 or xa[0] != ya[0]:
            return False
        try:
            if abs(round(float(xa[1]) * 100) - round(float(ya[1]) * 100)) > tolerance:
                return False
        except ValueError:
            return False
    return True


# ================================================================ payroll ==


def export_items(
    db: Session, ctx: FinanceCtx, week: PayWeek, uploaded: dict
) -> list[dict]:
    """One item per Ace file given: identical, a named class of difference,
    or unexplained with the differing lines listed."""
    latest = {}
    for e in weeks_svc.exports_of(db, week):
        latest.setdefault(e.kind, e)
    items: list[dict] = []
    for kind in EXPORT_KINDS:
        if kind not in uploaded:
            continue
        data = uploaded[kind]
        if isinstance(data, str):
            data = data.encode("utf-8")
        orbit_sha = _sha(data)
        stored = latest.get(kind)
        label = exports_svc.FILENAMES[kind]
        if stored is None:
            items.append(
                {
                    "entity": week.venue_id,
                    "key": f"export:{kind}",
                    "norm_value": None,
                    "orbit_value": orbit_sha,
                    "explanation": f"No {label} stored for this week — generate the exports first.",
                }
            )
            continue
        if stored.content_sha256 == orbit_sha:
            items.append(
                {
                    "entity": week.venue_id,
                    "key": f"export:{kind}",
                    "norm_value": stored.content_sha256,
                    "orbit_value": orbit_sha,
                    "class": "expected",
                    "explanation": f"{label} is byte-identical to the owner's Ace file.",
                }
            )
            continue
        norm_lines = _lines(stored.content)
        orbit_lines = _lines(data.decode("utf-8", errors="replace"))
        item = {
            "entity": week.venue_id,
            "key": f"export:{kind}",
            "norm_value": f"{stored.content_sha256} ({len(norm_lines)} lines)",
            "orbit_value": f"{orbit_sha} ({len(orbit_lines)} lines)",
        }
        if norm_lines == orbit_lines:
            item["class"] = "expected"
            item["explanation"] = (
                f"{label}: the same lines; only the line endings or a trailing "
                "newline differ."
            )
        elif kind == "leave" and _style_only(norm_lines, orbit_lines):
            item["code"] = "leave_style_default"
        elif kind == "gratuities" and _cents_only(norm_lines, orbit_lines):
            item["code"] = "gratuity_residual"
        else:
            added = [ln for ln in norm_lines if ln not in orbit_lines]
            removed = [ln for ln in orbit_lines if ln not in norm_lines]
            item["explanation"] = (
                f"{label} differs: {len(added)} line(s) only in Norm's file, "
                f"{len(removed)} only in the owner's."
            )
            items.append(item)
            for n, ln in enumerate(added[:MAX_LINE_ITEMS]):
                items.append(
                    {
                        "entity": week.venue_id,
                        "key": f"export:{kind}:norm-only:{n + 1}",
                        "norm_value": ln,
                        "orbit_value": None,
                    }
                )
            for n, ln in enumerate(removed[:MAX_LINE_ITEMS]):
                items.append(
                    {
                        "entity": week.venue_id,
                        "key": f"export:{kind}:orbit-only:{n + 1}",
                        "norm_value": None,
                        "orbit_value": ln,
                    }
                )
            continue
        items.append(item)
    return items


def snapshot_items(
    db: Session, ctx: FinanceCtx, week: PayWeek, *, config_db=None
) -> list[dict]:
    """The close snapshot's totals against Loaded's pay summary (read live),
    and the clock-ins it dropped."""
    items: list[dict] = []
    snapshot = weeks_svc.close_snapshot(db, week) or weeks_svc.current_snapshot(
        db, week
    )
    if snapshot is None:
        items.append(
            {
                "entity": week.venue_id,
                "key": "snapshot",
                "norm_value": None,
                "orbit_value": None,
                "explanation": "No snapshot for this week.",
            }
        )
        return items
    totals = snapshot_svc.totals(snapshot_svc.facts_of(db, snapshot))
    if snapshot.dropped_deleted or snapshot.dropped_unfinished:
        items.append(
            {
                "entity": week.venue_id,
                "key": "snapshot:dropped",
                "norm_value": (
                    f"deleted {snapshot.dropped_deleted}, unfinished "
                    f"{snapshot.dropped_unfinished} (dropped)"
                ),
                "orbit_value": "mapped as $0 rows",
                "code": "dropped_clockins",
            }
        )
    venue = weeks_svc.venue_of(db, ctx, week.venue_id)
    try:
        live = snapshot_svc.payrun(
            db, ctx, venue, week.week_start, view="hygiene", config_db=config_db
        )
    except FinanceError as exc:
        items.append(
            {
                "entity": week.venue_id,
                "key": "snapshot:pay_summary",
                "norm_value": f"{totals['cost_cents'] / 100:.2f}",
                "orbit_value": None,
                "explanation": f"Loaded's pay summary could not be read: {exc.message}",
            }
        )
        return items
    parity = live.get("parity") or {}
    summary = parity.get("pay_summary_total_wages")
    if summary is None:
        items.append(
            {
                "entity": week.venue_id,
                "key": "snapshot:pay_summary",
                "norm_value": f"{totals['cost_cents'] / 100:.2f}",
                "orbit_value": None,
                "explanation": "Loaded's pay summary gave no total.",
            }
        )
        return items
    norm = round(totals["cost_cents"] / 100.0, 2)
    diff = round(norm - float(summary), 2)
    tolerance = float(parity.get("tolerance") or 0.05)
    item = {
        "entity": week.venue_id,
        "key": "snapshot:pay_summary",
        "norm_value": f"{norm:.2f}",
        "orbit_value": f"{float(summary):.2f}",
    }
    if abs(diff) <= tolerance:
        item["class"] = "expected"
        item["explanation"] = (
            f"The snapshot's wages match Loaded's pay summary within ${tolerance:.2f}."
        )
    elif snapshot_svc.drift(db, week) is not None:
        item["code"] = "loaded_changed"
    else:
        item["explanation"] = (
            f"The snapshot's wages differ from Loaded's pay summary by ${diff:.2f}."
        )
    items.append(item)
    return items


def payroll_check(
    db: Session,
    ctx: FinanceCtx,
    week: PayWeek,
    *,
    uploaded: dict | None = None,
    trigger: str = "manual",
    config_db=None,
) -> FinParityRun:
    items = export_items(db, ctx, week, uploaded or {}) + snapshot_items(
        db, ctx, week, config_db=config_db
    )
    return core_parity.record_run(
        db,
        ctx,
        module="payroll",
        period=f"{week.venue_id}:{week.week_start.isoformat()}",
        trigger=trigger,
        items=items,
        totals={
            "venue_id": week.venue_id,
            "week_start": week.week_start.isoformat(),
            "files_checked": sorted(k for k in (uploaded or {}) if k in EXPORT_KINDS),
            "items": len(items),
        },
    )


# ================================================================= inputs ==


def _payout_items(db, ctx, week) -> list[dict]:
    rows = (
        db.query(PayLeavePayout)
        .filter(
            PayLeavePayout.organization_id == ctx.org_id,
            PayLeavePayout.venue_id == week.venue_id,
            PayLeavePayout.week_start == week.week_start,
        )
        .all()
    )
    norm = {p.loaded_request_id: p for p in rows if p.source == "norm"}
    orbit = {p.loaded_request_id: p for p in rows if p.source != "norm"}
    items = []
    for request_id in sorted(set(norm) | set(orbit)):
        a, b = norm.get(request_id), orbit.get(request_id)
        if a is not None and b is not None and a.paid_cents == b.paid_cents:
            continue
        item = {
            "entity": week.venue_id,
            "key": f"payout:{request_id}:paid_cents",
            "norm_value": a.paid_cents if a else None,
            "orbit_value": b.paid_cents if b else None,
            "explanation": None
            if (a and b)
            else ("Keyed in Norm only." if a else "Keyed in Orbit only."),
        }
        if b is not None and b.source == "orbit_legacy":
            # Orbit keyed these hundred payouts by request id alone; the
            # importer inferred the pay week (the week before the Tuesday it
            # was keyed), so a mismatch here is the inference, not the figure.
            item["class"] = "expected"
            item["explanation"] = (
                "Orbit's row carried no pay week — the importer inferred it from "
                "the day it was keyed (source orbit_legacy)."
            )
        items.append(item)
    return items


def _gratuity_items(db, ctx, week) -> list[dict]:
    batches = (
        db.query(PayGratuityBatch)
        .filter(
            PayGratuityBatch.organization_id == ctx.org_id,
            PayGratuityBatch.venue_id == week.venue_id,
            PayGratuityBatch.week_start == week.week_start,
        )
        .all()
    )
    items = []
    by_source = {
        "norm": [b for b in batches if b.source == "norm"],
        "orbit": [b for b in batches if b.source != "norm"],
    }
    for period in sorted({b.period for b in batches}):
        a = next((b for b in by_source["norm"] if b.period == period), None)
        b = next((b for b in by_source["orbit"] if b.period == period), None)
        if a is None or b is None:
            items.append(
                {
                    "entity": week.venue_id,
                    "key": f"gratuities:{period}:total_cents",
                    "norm_value": a.total_cents if a else None,
                    "orbit_value": b.total_cents if b else None,
                    "explanation": "Keyed in Norm only."
                    if a
                    else "Keyed in Orbit only.",
                }
            )
            continue
        if a.total_cents != b.total_cents:
            items.append(
                {
                    "entity": week.venue_id,
                    "key": f"gratuities:{period}:total_cents",
                    "norm_value": a.total_cents,
                    "orbit_value": b.total_cents,
                }
            )
        shares_a = {
            x.staff_member_id: x
            for x in db.query(PayGratuityAllocation).filter(
                PayGratuityAllocation.organization_id == ctx.org_id,
                PayGratuityAllocation.batch_id == a.id,
            )
        }
        shares_b = {
            x.staff_member_id: x
            for x in db.query(PayGratuityAllocation).filter(
                PayGratuityAllocation.organization_id == ctx.org_id,
                PayGratuityAllocation.batch_id == b.id,
            )
        }
        for staff in sorted(set(shares_a) | set(shares_b)):
            x, y = shares_a.get(staff), shares_b.get(staff)
            if x is not None and y is not None and x.amount_cents == y.amount_cents:
                continue
            item = {
                "entity": week.venue_id,
                "key": f"gratuities:{period}:{staff}:amount_cents",
                "norm_value": x.amount_cents if x else None,
                "orbit_value": y.amount_cents if y else None,
            }
            if (
                x is not None
                and y is not None
                and abs(x.amount_cents - y.amount_cents) <= 2
            ):
                item["code"] = "gratuity_residual"
            items.append(item)
    return items


def _single_row_items(db, ctx, week) -> list[dict]:
    """ACE inputs and accruals are one row per week, so a Norm-keyed
    rehearsal row has no Orbit twin in the table: the person checks the
    figure against Orbit by hand and signs the item off."""
    items = []
    ace = (
        db.query(PayAceInput)
        .filter(
            PayAceInput.organization_id == ctx.org_id, PayAceInput.week_id == week.id
        )
        .first()
    )
    if ace is not None and ace.source == "norm":
        items.append(
            {
                "entity": week.venue_id,
                "key": "ace_inputs:gross_cents",
                "norm_value": ace.gross_cents,
                "orbit_value": None,
                "explanation": (
                    "One ACE inputs row per week: Orbit's figure could not be "
                    "imported beside Norm's — check gross / net / ESCT / KiwiSaver "
                    "against Orbit by hand and sign off."
                ),
            }
        )
    acc = (
        db.query(PayAccrual)
        .filter(PayAccrual.organization_id == ctx.org_id, PayAccrual.week_id == week.id)
        .first()
    )
    if acc is not None and acc.source == "norm":
        items.append(
            {
                "entity": week.venue_id,
                "key": "accruals:accrued_cents",
                "norm_value": acc.accrued_cents,
                "orbit_value": None,
                "explanation": (
                    "One accrual row per week: check the accrued and paid figures "
                    "against Orbit by hand and sign off."
                ),
            }
        )
    return items


def _adjustment_items(db, ctx, week) -> list[dict]:
    rows = (
        db.query(PayAdjustment)
        .filter(
            PayAdjustment.organization_id == ctx.org_id,
            PayAdjustment.week_id == week.id,
        )
        .all()
    )

    def key(r):
        return (r.label or "").strip().casefold()

    norm = {key(r): r for r in rows if r.source == "norm"}
    orbit = {key(r): r for r in rows if r.source != "norm"}
    items = []
    for label in sorted(set(norm) | set(orbit)):
        a, b = norm.get(label), orbit.get(label)
        if a is not None and b is not None and a.amount_cents == b.amount_cents:
            continue
        items.append(
            {
                "entity": week.venue_id,
                "key": f"adjustment:{label}:amount_cents",
                "norm_value": a.amount_cents if a else None,
                "orbit_value": b.amount_cents if b else None,
                "explanation": None
                if (a and b)
                else ("Keyed in Norm only." if a else "Keyed in Orbit only."),
            }
        )
    return items


def inputs_check(
    db: Session, ctx: FinanceCtx, week: PayWeek, *, trigger: str = "manual"
) -> FinParityRun:
    items = (
        _payout_items(db, ctx, week)
        + _gratuity_items(db, ctx, week)
        + _single_row_items(db, ctx, week)
        + _adjustment_items(db, ctx, week)
    )
    return core_parity.record_run(
        db,
        ctx,
        module="inputs",
        period=f"{week.venue_id}:{week.week_start.isoformat()}",
        trigger=trigger,
        items=items,
        totals={
            "venue_id": week.venue_id,
            "week_start": week.week_start.isoformat(),
            "items": len(items),
        },
    )


def run_module(
    db: Session,
    ctx: FinanceCtx,
    module: str,
    week: PayWeek,
    *,
    trigger: str = "manual",
    uploaded: dict | None = None,
    config_db=None,
) -> FinParityRun:
    if module == "payroll":
        return payroll_check(
            db, ctx, week, uploaded=uploaded, trigger=trigger, config_db=config_db
        )
    if module == "inputs":
        return inputs_check(db, ctx, week, trigger=trigger)
    raise Invalid("The payroll Shadow tab runs the payroll and inputs modules.")


# ================================================================= shadow ==


def run_dict(
    db: Session, ctx: FinanceCtx, run: FinParityRun, *, items: bool = False
) -> dict:
    rows = core_parity.items_of(db, ctx, run.id)
    names = snapshot_svc.venue_names(db, ctx.org_id)
    # A payroll/inputs run's period is "<venue id>:<week start>" (F3's runs);
    # the Shadow page shows the venue's name and the week.
    venue_id, _, week_start = (run.period or "").partition(":")
    out = {
        "id": run.id,
        "module": run.module,
        "period": run.period,
        "venue_id": venue_id if venue_id in names else None,
        "venue_name": names.get(venue_id),
        "week_start": week_start or None,
        "ran_at": run.ran_at.isoformat() if run.ran_at else None,
        "trigger": run.trigger,
        "totals": run.totals,
        "ran_by": run.ran_by,
        "item_count": len(rows),
        "unexplained": sum(
            1 for r in rows if r.class_ == "unexplained" and r.signed_off_at is None
        ),
    }
    if items:
        out["items"] = [item_dict(r, names) for r in rows]
    return out


def item_dict(item: FinParityItem, names: dict | None = None) -> dict:
    names = names or {}
    return {
        "id": item.id,
        "entity": item.entity,
        "entity_name": names.get(item.entity),
        "key": item.key,
        "norm_value": item.norm_value,
        "orbit_value": item.orbit_value,
        "class": item.class_,
        "explanation": item.explanation,
        "signed_off_by": item.signed_off_by,
        "signed_off_at": item.signed_off_at.isoformat() if item.signed_off_at else None,
    }


def failed_jobs(db: Session, org_id: str, since: dt.datetime) -> list[dict]:
    names = snapshot_svc.venue_names(db, org_id)
    return [
        {
            "id": r.id,
            "job": r.job,
            "scope_id": r.scope_id,
            "venue_name": names.get(r.scope_id),
            "slot": r.slot,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error": r.error or "did not finish",
        }
        for r in jobs_svc.failed_since(db, org_id, since)
    ]


def job_runs(db: Session, org_id: str, since: dt.datetime) -> list[dict]:
    names = snapshot_svc.venue_names(db, org_id)
    rows = (
        db.query(FinJobRun)
        .filter(FinJobRun.organization_id == org_id, FinJobRun.started_at >= since)
        .order_by(FinJobRun.started_at.desc())
        .limit(500)
        .all()
    )
    return [
        {
            "id": r.id,
            "job": r.job,
            "scope_id": r.scope_id,
            "venue_name": names.get(r.scope_id),
            "slot": r.slot,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error": r.error,
        }
        for r in rows
    ]


def shadow(db: Session, ctx: FinanceCtx, *, since: dt.datetime | None = None) -> dict:
    ctx.require("payroll:read")
    since = since or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7))
    runs = [
        run_dict(db, ctx, r)
        for r in core_parity.list_runs(db, ctx, limit=50)
        if r.module in ("payroll", "inputs", "journal")
    ]
    return {
        "mode": ctx.mode("payroll"),
        "runs": runs,
        "failed_jobs": failed_jobs(db, ctx.org_id, since),
        "expected_classes": dict(core_parity.EXPECTED_CLASSES),
    }


# ================================================================= weekly ==


def send_parity_email(
    db: Session,
    ctx: FinanceCtx,
    *,
    week_start: dt.date,
    runs: list[dict],
    failed: list[dict],
) -> dict:
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    context = {
        "org_name": org.name if org else "",
        "week_start": week_start.isoformat(),
        "week_ending": weeks_svc.week_ending(week_start).isoformat(),
        "runs": runs,
        "unexplained": sum(r.get("unexplained", 0) for r in runs),
        "failed": failed,
        "subject": (
            f"Payroll parity — week {week_start.isoformat()}: "
            f"{sum(r.get('unexplained', 0) for r in runs)} unexplained, "
            f"{len(failed)} failed job(s)"
        ),
    }
    to = _email.payroll_contact(db, ctx, None)
    return _email.send(db, ctx, TEMPLATE, context, to=to)


def weekly(
    db: Session, ctx: FinanceCtx, *, week_start: dt.date, config_db=None
) -> dict:
    """Both modules for every entity's week, then the email."""
    from app.db.payroll_models import PayEntity

    runs: list[dict] = []
    for entity in (
        db.query(PayEntity)
        .filter(PayEntity.organization_id == ctx.org_id, PayEntity.payroll_on.is_(True))
        .all()
    ):
        venue = db.query(Venue).filter(Venue.id == entity.venue_id).first()
        if venue is None:
            continue
        week = weeks_svc.get_or_create(db, ctx, venue.id, week_start)
        for module in ("payroll", "inputs"):
            try:
                run = run_module(
                    db, ctx, module, week, trigger="weekly", config_db=config_db
                )
                runs.append({**run_dict(db, ctx, run), "venue_name": venue.name})
            except FinanceError as exc:
                logger.warning(
                    "parity %s for %s failed: %s", module, venue.name, exc.message
                )
                runs.append(
                    {
                        "module": module,
                        "venue_name": venue.name,
                        "period": f"{venue.id}:{week_start.isoformat()}",
                        "error": exc.message,
                        "unexplained": 0,
                        "item_count": 0,
                    }
                )
    since = dt.datetime.combine(week_start, dt.time(0, 0), tzinfo=dt.timezone.utc)
    failed = failed_jobs(db, ctx.org_id, since)
    email = send_parity_email(db, ctx, week_start=week_start, runs=runs, failed=failed)
    return {"runs": runs, "failed_jobs": failed, "email": email}
