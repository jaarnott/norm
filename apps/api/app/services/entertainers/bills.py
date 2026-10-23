"""Xero bills for the invoice and exemption lanes (plan §11.1, §11.5).

One bill per performance (a shared invoice is attached to each), pushed
through ``page_writes.confirm(tool="xero.create_bill", …)`` — the card is
shown first (``preview``) and the person's click is the approval. The key is
``norm:ent-bill:<performance id>`` (``:<member id>`` for a members_split
share), so a retried push returns the same bill. GST comes from the
performer's registration and the tenant's rates (``create_bill`` reads the
rates; this module only says ``gst: yes|no`` per line); the account is the
venue's ``entertainment_expense`` mapping. The invoice's own lines go on the
bill when the check found they add up, else one line per performance.
Nothing is rescaled.

Refusals: the performer not payable; an exemption past its expiry date;
the performance not approved or mismatched; a queried invoice not waived; no
Xero connection at the venue; the module not live. A re-push voids the
earlier bill first (``xero.void_payroll_document``), as Orbit's ``force``
did. The push is recorded through F4's ``xero_pushes.record_push`` when it
exists, else straight into ``pay_xero_pushes``.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    EntFile,
    EntPerformance,
    EntPerformanceShare,
    EntPerformer,
    EntPerformerXeroContact,
)
from app.db.models import Connection, Venue, _now
from app.db.payroll_models import PayXeroPush
from app.services.entertainers import status as status_svc
from app.services.entertainers._shared import (
    iso,
    load,
    load_performance,
    settings_for,
)
from app.services.entertainers.invoice_check import invoice_bill_lines
from app.services.entertainers.performers import payable
from app.services.finance_core.ctx import FinanceCtx, require_staff
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush

logger = logging.getLogger(__name__)

TOOL = "xero.create_bill"
VOID_TOOL = "xero.void_payroll_document"
KIND = "entertainer_bill"
DUE_DAYS = 14


def push_dict(push: PayXeroPush) -> dict:
    return {
        "id": push.id,
        "kind": push.kind,
        "xero_tenant_id": push.xero_tenant_id,
        "xero_id": push.xero_id,
        "xero_number": push.xero_number,
        "xero_status": push.xero_status,
        "idempotency_key": push.idempotency_key,
        "pushed_at": iso(push.pushed_at),
        "pushed_by": push.pushed_by,
        "voided_at": iso(push.voided_at),
    }


def _ref(performance_id: str, member_id: str | None) -> str:
    return f"{performance_id}:{member_id}" if member_id else performance_id


def idempotency_key(performance_id: str, member_id: str | None = None) -> str:
    return f"norm:ent-bill:{_ref(performance_id, member_id)}"


def live_push(
    db: Session, org_id: str, performance_id: str, member_id: str | None = None
) -> PayXeroPush | None:
    return (
        db.query(PayXeroPush)
        .filter(
            PayXeroPush.organization_id == org_id,
            PayXeroPush.kind == KIND,
            PayXeroPush.entertainer_performance_id == _ref(performance_id, member_id),
            PayXeroPush.voided_at.is_(None),
        )
        .first()
    )


def tenant_for(db: Session, venue_id: str) -> str | None:
    """The venue's Xero organisation, from its live connection row."""
    row = (
        db.query(Connection)
        .filter(Connection.connector_name == "xero", Connection.venue_id == venue_id)
        .first()
    )
    if (
        row is None
        or row.enabled != "true"
        or not row.access_token
        or getattr(row, "needs_reconnect", False)
    ):
        return None
    return str((row.config or {}).get("xero_tenant_id") or "") or None


def _contact_id(db: Session, performer: EntPerformer, tenant: str) -> str | None:
    row = (
        db.query(EntPerformerXeroContact)
        .filter(
            EntPerformerXeroContact.organization_id == performer.organization_id,
            EntPerformerXeroContact.performer_id == performer.id,
            EntPerformerXeroContact.xero_tenant_id == tenant,
        )
        .first()
    )
    return row.xero_contact_id if row else None


def bill_params(
    db: Session,
    *,
    perf: EntPerformance,
    performer: EntPerformer,
    payee: EntPerformer,
    venue: Venue | None,
    file: EntFile | None,
    tenant: str,
    share: EntPerformanceShare | None = None,
    status: str = "DRAFT",
) -> dict:
    """The ``create_bill`` parameters for one performance (or one share)."""
    gst = "yes" if (payee.gst_registered and payee.lane != "exemption") else "no"
    account = {"category": "entertainment_expense", "gst": gst}
    amount = share.amount_cents if share is not None else (perf.actual_cents or 0)
    own_lines = invoice_bill_lines(db, file, perf) if share is None else None
    label = f"{performer.name} — performance {perf.performance_date.isoformat()}" + (
        f" @ {venue.name}" if venue else ""
    )
    if share is not None:
        label = (
            f"{payee.name} ({performer.name}) — performance {perf.performance_date.isoformat()}"
            + (f" @ {venue.name}" if venue else "")
        )
    if own_lines:
        lines = [
            {
                "description": ln["description"],
                "quantity": 1,
                "unit_amount": round(ln["amount_cents"] / 100, 2),
                **account,
            }
            for ln in own_lines
        ]
    else:
        lines = [
            {
                "description": label,
                "quantity": 1,
                "unit_amount": round(amount / 100, 2),
                **account,
            }
        ]
    supplier = (
        payee.company_name or payee.legal_name or payee.trading_name or payee.name
    )
    number = (
        file.invoice_number if file is not None else None
    ) or f"NORM-{perf.id[:8].upper()}"
    params = {
        "supplier": supplier,
        "invoice_number": number,
        "reference": f"Entertainer {performer.name} {perf.performance_date.isoformat()}",
        "date": perf.performance_date.isoformat(),
        "due_date": (perf.performance_date + dt.timedelta(days=DUE_DAYS)).isoformat(),
        "status": status,
        "line_amount_types": "NoTax" if gst == "no" else "Exclusive",
        "lines": lines,
        "create_contact": "true",
        "idempotency_key": idempotency_key(
            perf.id, share.band_member_id if share is not None else None
        ),
    }
    contact = _contact_id(db, payee, tenant)
    if contact:
        params["supplier_contact_id"] = contact
    if file is not None:
        params["attachment_file_id"] = file.id
    return params


def _target(db: Session, ctx: FinanceCtx, performance_id: str, member_id: str | None):
    """The rows a push needs, with every refusal the plan names."""
    require_staff(ctx)
    ctx.require("payroll:write")
    ctx.require_live("entertainers")
    perf = load_performance(db, ctx, performance_id)
    if perf.performance_status != "approved":
        raise Conflict("Only an approved performance can be billed.")
    tol = settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    if status_svc.times_match(perf, tol) is False:
        raise Conflict(
            "The performer's times differ from the approved ones — resolve the mismatch first."
        )
    performer = load(db, ctx, EntPerformer, perf.performer_id, label="Performer")
    share = None
    payee = performer
    if member_id:
        share = (
            db.query(EntPerformanceShare)
            .filter(
                EntPerformanceShare.organization_id == ctx.org_id,
                EntPerformanceShare.performance_id == perf.id,
                EntPerformanceShare.band_member_id == member_id,
            )
            .first()
        )
        if share is None:
            raise NotFound("Share not found.")
        if not share.member_performer_id:
            raise Conflict("This member has not submitted their form yet.")
        payee = load(
            db, ctx, EntPerformer, share.member_performer_id, label="Performer"
        )
    elif performer.band_mode == "members_split":
        raise Invalid(
            "A 'pay each member separately' band is billed per member — name the member."
        )
    if payee.lane not in ("invoice", "exemption"):
        raise Invalid(
            "Only the invoice and exemption lanes are billed in Xero; payroll lanes go through the pay run."
        )
    ok, why = payable(db, payee)
    if not ok:
        raise Conflict(f"This performer isn't payable yet: {why}.")
    today = _now().date()
    if (
        payee.lane == "exemption"
        and payee.exemption_expiry_date
        and payee.exemption_expiry_date < today
    ):
        raise Conflict(
            "The exemption certificate has expired — no bill without withholding tax until a new one is on record."
        )
    file = (
        db.query(EntFile).filter(EntFile.id == perf.invoice_file_id).first()
        if perf.invoice_file_id
        else None
    )
    if file is not None and file.check_status in ("query", "unreadable"):
        raise Conflict(
            "The invoice check raised a query — resolve or waive it before billing."
        )
    tenant = tenant_for(db, perf.venue_id)
    if not tenant:
        raise Conflict("This venue has no live Xero connection.")
    venue = db.query(Venue).filter(Venue.id == perf.venue_id).first()
    return perf, performer, payee, venue, file, share, tenant


def preview(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    *,
    member_id: str | None = None,
    status: str = "DRAFT",
    config_db=None,
) -> dict:
    from app.services import page_writes

    perf, performer, payee, venue, file, share, tenant = _target(
        db, ctx, performance_id, member_id
    )
    params = bill_params(
        db,
        perf=perf,
        performer=performer,
        payee=payee,
        venue=venue,
        file=file,
        tenant=tenant,
        share=share,
        status=status,
    )
    out = page_writes.preview(
        db,
        tool=TOOL,
        params=params,
        user=ctx.user,
        venue_id=perf.venue_id,
        config_db=config_db,
    )
    existing = live_push(db, ctx.org_id, perf.id, member_id)
    out["replaces"] = push_dict(existing) if existing else None
    out["params"] = params
    return out


def _void(
    db: Session, ctx: FinanceCtx, push: PayXeroPush, venue_id: str, config_db
) -> None:
    from app.services import page_writes

    shown = page_writes.preview(
        db,
        tool=VOID_TOOL,
        params={"push_id": push.id},
        user=ctx.user,
        venue_id=venue_id,
        config_db=config_db,
    )
    if shown.get("card") is None:
        return  # nothing to void (already voided in Xero)
    page_writes.confirm(
        db,
        tool=VOID_TOOL,
        params={"push_id": push.id},
        user=ctx.user,
        venue_id=venue_id,
        fingerprint=shown.get("fingerprint"),
        config_db=config_db,
    )
    db.refresh(push)
    if push.voided_at is None:
        push.voided_at = _now()
        push.voided_by = ctx.user_id
        flush(db)


def record_push(
    db: Session,
    ctx: FinanceCtx,
    *,
    perf: EntPerformance,
    member_id: str | None,
    tenant: str,
    params: dict,
    result: dict,
) -> PayXeroPush:
    """Through F4's ledger when it exists (lazy import), else the row itself."""
    key = params["idempotency_key"]
    bill = (result or {}).get("invoice") or (result or {}).get("bill") or {}
    fields = {
        "kind": KIND,
        "venue_id": perf.venue_id,
        "week_start": None,
        "entertainer_performance_id": _ref(perf.id, member_id),
        "xero_tenant_id": tenant,
        "xero_id": bill.get("InvoiceID")
        or bill.get("invoice_id")
        or (result or {}).get("invoice_id"),
        "xero_number": bill.get("InvoiceNumber")
        or bill.get("invoice_number")
        or (result or {}).get("invoice_number"),
        "xero_status": bill.get("Status")
        or bill.get("status")
        or (result or {}).get("status"),
        "request_payload": params,
        "response": result,
        "idempotency_key": key,
        "pushed_by": ctx.user_id,
    }
    try:
        from app.services.payroll import xero_pushes  # F4

        recorder = getattr(xero_pushes, "record_push", None)
    except ImportError:
        recorder = None
    if recorder is not None:
        try:
            row = recorder(db, ctx, **fields)
            if isinstance(row, PayXeroPush):
                return row
        except TypeError:
            logger.warning(
                "payroll.xero_pushes.record_push has a different signature — recording directly"
            )
    row = PayXeroPush(organization_id=ctx.org_id, **fields)
    db.add(row)
    flush(db)
    return row


def confirm(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    *,
    fingerprint: str | None,
    member_id: str | None = None,
    status: str = "DRAFT",
    config_db=None,
) -> PayXeroPush:
    from app.services import page_writes

    perf, performer, payee, venue, file, share, tenant = _target(
        db, ctx, performance_id, member_id
    )
    params = bill_params(
        db,
        perf=perf,
        performer=performer,
        payee=payee,
        venue=venue,
        file=file,
        tenant=tenant,
        share=share,
        status=status,
    )
    existing = live_push(db, ctx.org_id, perf.id, member_id)
    if existing is not None:
        _void(db, ctx, existing, perf.venue_id, config_db)
        params["idempotency_key"] = f"{params['idempotency_key']}:{existing.id[:8]}"
    out = page_writes.confirm(
        db,
        tool=TOOL,
        params=params,
        user=ctx.user,
        venue_id=perf.venue_id,
        fingerprint=fingerprint,
        config_db=config_db,
    )
    data = out.get("data") if isinstance(out, dict) else {}
    return record_push(
        db,
        ctx,
        perf=perf,
        member_id=member_id,
        tenant=tenant,
        params=params,
        result=data if isinstance(data, dict) else {},
    )
