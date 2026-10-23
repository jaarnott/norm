"""The push ledger — every document Norm put in Xero for payroll
(``pay_xero_pushes``: wage journals, intercompany invoices and bills,
entertainer bills).

Two guards live in the table and are surfaced here as ``Conflict``:

* ``uq_pay_xero_pushes_key`` — one row per idempotency key. The same key sent
  twice (a retried confirm, a double approval) returns the existing row and
  writes nothing: ``record_push`` is idempotent on the key, as the Xero call
  it records is (``Idempotency-Key``).
* ``uq_pay_xero_pushes_live`` — one **live** document per (venue, week, kind,
  counterparty, performance). A second live row is refused; a re-push voids
  the old one first (``void_push``), which is what the consolidators'
  ``replace='true'`` does.

Who pushed is the caller's ctx (``pushed_by`` / ``voided_by``), never a
parameter — the built-in takes it from the caller scope, the routes from the
signed-in user. The entertainers module (F5) records its bills through
``record_push`` directly with ``kind="entertainer_bill"``.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import _now
from app.db.payroll_models import PUSH_KINDS, PayJournalDraft, PayXeroPush
from app.services.finance_core import errors
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid, NotFound

#: Xero statuses that mean the document is no longer live there.
GONE_STATUSES = ("VOIDED", "DELETED")


def tenant_of(db: Session, venue_id: str) -> str:
    """The Xero organisation this venue's documents land in."""
    from app.services.payroll.xero_mapping import connection

    row = connection(db, venue_id)
    tenant = str((row.config or {}).get("xero_tenant_id") or "") if row else ""
    if not tenant:
        raise Invalid("This venue is not connected to Xero.")
    return tenant


def _tenant(db: Session, venue_id: str, given: str | None) -> str:
    """The venue's tenant, else the one the caller knows."""
    try:
        return tenant_of(db, venue_id)
    except Invalid:
        if given:
            return str(given)
        raise


def live_push(
    db: Session,
    org_id: str,
    *,
    venue_id: str,
    kind: str,
    week_start: dt.date | None = None,
    counterparty_venue_id: str | None = None,
    entertainer_performance_id: str | None = None,
) -> PayXeroPush | None:
    """The one live row for this document slot, or None."""
    return (
        db.query(PayXeroPush)
        .filter(
            PayXeroPush.organization_id == org_id,
            PayXeroPush.venue_id == venue_id,
            PayXeroPush.kind == kind,
            PayXeroPush.week_start == week_start,
            PayXeroPush.counterparty_venue_id == counterparty_venue_id,
            PayXeroPush.entertainer_performance_id == entertainer_performance_id,
            PayXeroPush.voided_at.is_(None),
        )
        .first()
    )


def live_pushes_for_week(
    db: Session, org_id: str, venue_id: str, week_start: dt.date
) -> list[PayXeroPush]:
    return (
        db.query(PayXeroPush)
        .filter(
            PayXeroPush.organization_id == org_id,
            PayXeroPush.venue_id == venue_id,
            PayXeroPush.week_start == week_start,
            PayXeroPush.voided_at.is_(None),
        )
        .order_by(PayXeroPush.pushed_at)
        .all()
    )


def count_for_slot(
    db: Session,
    org_id: str,
    *,
    venue_id: str,
    kind: str,
    week_start: dt.date | None = None,
    counterparty_venue_id: str | None = None,
    entertainer_performance_id: str | None = None,
) -> int:
    """How many ledger rows (voided included) this document slot already
    has. A re-push after a void or a replace must carry a NEW idempotency
    key — the same key would make Xero answer with the document just
    voided and the ledger with the row just closed — so the consolidators
    append ``:r<n>`` to the key when ``n`` is above zero."""
    return (
        db.query(PayXeroPush)
        .filter(
            PayXeroPush.organization_id == org_id,
            PayXeroPush.venue_id == venue_id,
            PayXeroPush.kind == kind,
            PayXeroPush.week_start == week_start,
            PayXeroPush.counterparty_venue_id == counterparty_venue_id,
            PayXeroPush.entertainer_performance_id == entertainer_performance_id,
        )
        .count()
    )


def by_key(db: Session, org_id: str, idempotency_key: str) -> PayXeroPush | None:
    return (
        db.query(PayXeroPush)
        .filter(
            PayXeroPush.organization_id == org_id,
            PayXeroPush.idempotency_key == idempotency_key,
        )
        .first()
    )


def get_push(db: Session, ctx: FinanceCtx, push_id: str) -> PayXeroPush:
    ctx.require("payroll:read")
    row = (
        db.query(PayXeroPush)
        .filter(PayXeroPush.organization_id == ctx.org_id, PayXeroPush.id == push_id)
        .first()
    )
    if row is None or not ctx.can_see_venue(row.venue_id):
        raise NotFound("Xero push not found.")
    return row


def record_push(
    db: Session,
    ctx: FinanceCtx,
    *,
    kind: str,
    venue_id: str,
    idempotency_key: str,
    xero_id: str | None,
    xero_number: str | None = None,
    xero_status: str | None = None,
    week_start: dt.date | None = None,
    week_id: str | None = None,
    draft_id: str | None = None,
    counterparty_venue_id: str | None = None,
    entertainer_performance_id: str | None = None,
    request_payload: dict | None = None,
    response: dict | None = None,
    source: str = "norm",
    xero_tenant_id: str | None = None,
    pushed_by: str | None = None,
) -> PayXeroPush:
    """Write one ledger row. The same ``idempotency_key`` again returns the
    row already written. Flushes; the caller commits.

    ``xero_tenant_id`` is read from the venue's Xero connection; the caller's
    value is used only when the venue holds no live connection (a bill pushed
    before a reconnect). ``pushed_by`` is the ctx's person; the keyword is
    honoured only for a system ctx (no person), which is how the entertainers
    module names the person it acted for."""
    ctx.require("payroll:write")
    require_venue(ctx, venue_id)
    if kind not in PUSH_KINDS:
        raise Invalid(
            f"A push's kind is one of {', '.join(PUSH_KINDS)} — not {kind!r}."
        )
    key = str(idempotency_key or "").strip()
    if not key:
        raise Invalid("A push needs its idempotency key.")
    existing = by_key(db, ctx.org_id, key)
    if existing is not None:
        return existing
    if week_start is not None and week_id is None:
        from app.services.payroll.reconciliation import find_week

        week = find_week(db, ctx.org_id, venue_id, week_start)
        week_id = week.id if week else None
    if draft_id is not None:
        draft = (
            db.query(PayJournalDraft.id)
            .filter(
                PayJournalDraft.organization_id == ctx.org_id,
                PayJournalDraft.id == draft_id,
            )
            .first()
        )
        if draft is None:
            raise NotFound("Journal draft not found.")
    row = PayXeroPush(
        organization_id=ctx.org_id,
        week_id=week_id,
        draft_id=draft_id,
        kind=kind,
        venue_id=venue_id,
        week_start=week_start,
        counterparty_venue_id=counterparty_venue_id,
        entertainer_performance_id=entertainer_performance_id,
        xero_tenant_id=_tenant(db, venue_id, xero_tenant_id),
        xero_id=xero_id,
        xero_number=xero_number,
        xero_status=xero_status,
        request_payload=request_payload,
        response=response,
        idempotency_key=key[:128],
        pushed_by=ctx.user_id or pushed_by,
        source=source,
    )
    db.add(row)
    errors.flush(db)
    return row


def void_push(
    db: Session,
    ctx: FinanceCtx,
    push_id: str,
    *,
    xero_status: str | None = None,
    by_system: bool = False,
) -> PayXeroPush:
    """Mark a ledger row voided (after the document was voided or deleted in
    Xero, or found gone there by the status refresh — ``by_system``)."""
    ctx.require("payroll:write")
    row = get_push(db, ctx, push_id)
    if row.voided_at is None:
        row.voided_at = _now()
        row.voided_by = None if by_system else ctx.user_id
    if xero_status:
        row.xero_status = str(xero_status)
    db.flush()
    return row


def list_pushes(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str | None = None,
    week_start: dt.date | None = None,
    kind: str | None = None,
    include_voided: bool = False,
    limit: int = 200,
) -> list[PayXeroPush]:
    ctx.require("payroll:read")
    q = db.query(PayXeroPush).filter(
        PayXeroPush.organization_id == ctx.org_id,
        ctx.venue_clause(PayXeroPush.venue_id),
    )
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(PayXeroPush.venue_id == venue_id)
    if week_start is not None:
        q = q.filter(PayXeroPush.week_start == week_start)
    if kind:
        q = q.filter(PayXeroPush.kind == kind)
    if not include_voided:
        q = q.filter(PayXeroPush.voided_at.is_(None))
    return q.order_by(PayXeroPush.pushed_at.desc()).limit(limit).all()


def brief(row: PayXeroPush) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "xero_id": row.xero_id,
        "xero_number": row.xero_number,
        "xero_status": row.xero_status,
        "idempotency_key": row.idempotency_key,
        "pushed_at": row.pushed_at.isoformat() if row.pushed_at else None,
        "voided_at": row.voided_at.isoformat() if row.voided_at else None,
    }


def to_dict(row: PayXeroPush, names: dict[str, str] | None = None) -> dict:
    """The ledger row for a page; ``names`` (venue id → name) puts the
    counterparty's name beside its id."""
    return {
        **brief(row),
        "counterparty": (names or {}).get(row.counterparty_venue_id or ""),
        "venue_id": row.venue_id,
        "week_id": row.week_id,
        "week_start": row.week_start.isoformat() if row.week_start else None,
        "draft_id": row.draft_id,
        "counterparty_venue_id": row.counterparty_venue_id,
        "entertainer_performance_id": row.entertainer_performance_id,
        "xero_tenant_id": row.xero_tenant_id,
        "pushed_by": row.pushed_by,
        "voided_by": row.voided_by,
        "source": row.source,
    }
