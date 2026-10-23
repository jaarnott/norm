"""``xero.status_refresh`` — the weekly read of every live push's status in
Xero (plan §10, DESIGN-CONTRACT §6).

Orbit pushed journals as DRAFT and never looked again: all 81 live rows still
said DRAFT months later, whatever the accountant had done. This job reads
each live document — journals through ``get_manual_journal``, invoices,
bills and entertainer bills through ``get_invoice`` — with that venue's own
token, updates ``xero_status``, and marks a row voided when Xero says the
document is VOIDED or DELETED (so the "already pushed" guard releases the
slot and the page stops reporting a document that is gone).

Runs Wednesday 03:00 in the organisation's timezone, once per
organisation-week (``fin_job_runs`` slot = the ISO week's Monday), after the
Tuesday close pushes and before the 04:00 parity run. One Xero failure is
recorded on the run and the rest continue. F3's ``payroll.jobs.run_due``
calls ``run_due`` here (build-notes).
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.finance_models import FinJobRun
from app.db.models import Organization
from app.db.payroll_models import PayXeroPush
from app.services.finance_core import jobs
from app.services.finance_core.ctx import FinanceCtx, for_org, require_venue
from app.services.payroll import xero_pushes

logger = logging.getLogger(__name__)

JOB = "xero.status_refresh"
#: ISO weekday and local hour the refresh is due.
DUE_WEEKDAY, DUE_HOUR = 3, 3

_READ = {
    "journal": ("get_manual_journal", "manual_journal_id", "ManualJournals"),
    "ico_invoice": ("get_invoice", "invoice_id", "Invoices"),
    "ico_bill": ("get_invoice", "invoice_id", "Invoices"),
    "entertainer_bill": ("get_invoice", "invoice_id", "Invoices"),
}


def _read_status(db: Session, config_db: Session, push: PayXeroPush) -> str | None:
    """The document's status in Xero, or None when Xero gave no answer
    (raises on a hard error so the run records it)."""
    from app.connectors.tool_executor import execute_connector_tool

    action, field, collection = _READ[push.kind]
    result = execute_connector_tool(
        connector_name="xero",
        action=action,
        params={"venue_id": push.venue_id, field: push.xero_id},
        db=db,
        config_db=config_db,
        venue_id=push.venue_id,
        strict_venue=True,
    )
    payload = result.payload if isinstance(result.payload, dict) else {}
    if not result.success:
        if "404" in str(result.error or ""):
            return "DELETED"
        raise RuntimeError(result.error or "Xero gave no answer")
    rows = payload.get(collection) or []
    if not rows or not isinstance(rows[0], dict):
        return None
    return str(rows[0].get("Status") or "") or None


def refresh(
    db: Session,
    ctx: FinanceCtx,
    *,
    config_db: Session,
    venue_id: str | None = None,
) -> dict:
    """Read every live push's status now. Returns counts and the errors."""
    ctx.require("payroll:read")
    q = db.query(PayXeroPush).filter(
        PayXeroPush.organization_id == ctx.org_id,
        PayXeroPush.voided_at.is_(None),
        PayXeroPush.xero_id.is_not(None),
        ctx.venue_clause(PayXeroPush.venue_id),
    )
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(PayXeroPush.venue_id == venue_id)
    out = {"checked": 0, "updated": 0, "voided": 0, "errors": []}
    for push in q.order_by(PayXeroPush.pushed_at).all():
        out["checked"] += 1
        try:
            status = _read_status(db, config_db, push)
        except Exception as exc:  # noqa: BLE001 — one document's failure never stops the rest
            out["errors"].append({"push_id": push.id, "error": str(exc)[:300]})
            continue
        if not status:
            continue
        if status != push.xero_status:
            push.xero_status = status
            out["updated"] += 1
        if status in xero_pushes.GONE_STATUSES:
            xero_pushes.void_push(db, ctx, push.id, xero_status=status, by_system=True)
            out["voided"] += 1
    db.flush()
    return out


def _slot_due(now_local: dt.datetime) -> str | None:
    """The ISO week's Monday when the refresh is due at ``now_local``."""
    monday = (now_local - dt.timedelta(days=now_local.weekday())).date()
    due_at = dt.datetime.combine(
        monday + dt.timedelta(days=DUE_WEEKDAY - 1),
        dt.time(DUE_HOUR),
        tzinfo=now_local.tzinfo,
    )
    return monday.isoformat() if now_local >= due_at else None


def run_due(db: Session, now: dt.datetime, *, config_db: Session | None = None) -> dict:
    """The tick hook: for every organisation with a Finance module on, claim
    this week's slot once it is Wednesday 03:00 local and refresh."""
    if config_db is None:
        from app.db.engine import _ConfigSessionLocal

        config_db = _ConfigSessionLocal()
        own_config = True
    else:
        own_config = False
    ran = {}
    try:
        orgs = (
            db.query(Organization)
            .filter(
                (Organization.payroll_mode != "off")
                | (Organization.entertainers_mode != "off")
            )
            .all()
        )
        for org in orgs:
            try:
                tz = ZoneInfo(org.default_timezone or "Pacific/Auckland")
            except Exception:  # noqa: BLE001 — a bad timezone string must not stop the tick
                tz = ZoneInfo("Pacific/Auckland")
            slot = _slot_due(now.astimezone(tz))
            if slot is None:
                continue
            run = jobs.claim(
                db, org_id=org.id, job=JOB, scope_id=jobs.ORG_SCOPE, slot=slot
            )
            if run is None:
                continue
            db.commit()  # the claim must be visible before the slow part
            try:
                result = refresh(
                    db, for_org(db, org.id, actor_label="tick"), config_db=config_db
                )
                jobs.finish(
                    db,
                    run,
                    error="; ".join(e["error"] for e in result["errors"]) or None,
                )
                ran[org.id] = result
            except Exception as exc:  # noqa: BLE001 — recorded on the run
                logger.exception("xero.status_refresh failed for %s", org.id)
                jobs.finish(db, run, error=str(exc))
                ran[org.id] = {"error": str(exc)[:300]}
            db.commit()
    finally:
        if own_config:
            config_db.close()
    return ran


def last_run(db: Session, ctx: FinanceCtx) -> dict | None:
    """The most recent refresh for the organisation (the page's "last
    checked" line)."""
    ctx.require("payroll:read")
    run = (
        db.query(FinJobRun)
        .filter(FinJobRun.organization_id == ctx.org_id, FinJobRun.job == JOB)
        .order_by(FinJobRun.started_at.desc())
        .first()
    )
    if run is None:
        return None
    return {
        "slot": run.slot,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
    }
