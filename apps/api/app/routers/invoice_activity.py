"""Invoice activity: what Norm did with each invoice (owner, 19 Oct 2026).

Admins only. It replaces the Dojo's all-venue list of outstanding invoices,
which loaded every venue from Loaded on open and often failed. Everything here
reads Norm's own records (``services/invoice_activity.py``), never Loaded.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_db
from app.db.models import User
from app.services import invoice_activity as IA

router = APIRouter(prefix="/invoice-activity", tags=["invoice-activity"])

#: The default period, and the longest one a request may ask for.
DEFAULT_DAYS = 7
MAX_DAYS = 92


def _period(start: date | None, end: date | None, tz=None) -> tuple[date, date]:
    today = datetime.now(tz or timezone.utc).date()
    end = end or today
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if end < start:
        raise HTTPException(400, "end is before start")
    if (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(400, f"choose a period of {MAX_DAYS} days or less")
    return start, end


def _venue_list(db: Session) -> list[dict]:
    """The venues Norm has handled invoices for, to filter by."""
    from app.db.models import (
        InvoiceActivityEvent,
        InvoiceAutopilotOutcome,
        Organization,
        Venue,
    )

    ids = {
        v for (v,) in db.query(InvoiceAutopilotOutcome.venue_id).distinct().all() if v
    } | {v for (v,) in db.query(InvoiceActivityEvent.venue_id).distinct().all() if v}
    if not ids:
        return []
    rows = (
        db.query(Venue.id, Venue.name, Organization.name)
        .outerjoin(Organization, Organization.id == Venue.organization_id)
        .filter(Venue.id.in_(ids))
        .all()
    )
    return sorted(
        ({"id": v, "name": n, "organization": o} for v, n, o in rows),
        key=lambda x: ((x["organization"] or "").lower(), (x["name"] or "").lower()),
    )


@router.get("")
def activity(
    start: date | None = None,
    end: date | None = None,
    venue_ids: str | None = None,
    mode: str | None = None,
    outcome: str | None = None,
    supplier: str | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 100,
    tz: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    """The period's cards and stream. The cards follow the date and venue
    filters only; each card is one ``outcome``, and ``mode``, ``supplier`` and
    ``q`` narrow the stream further. ``tz`` (the viewer's IANA time zone)
    makes the dates the viewer's own calendar days."""
    zone = IA.zone(tz)
    start, end = _period(start, end, zone)
    venues = [v.strip() for v in (venue_ids or "").split(",") if v.strip()] or None
    rows = IA.period_rows(db, start, end, venues, zone)
    per_invoice = IA.unit_check_by_invoice(db, start, end, venues, zone)
    for r in rows:
        uc = per_invoice.get(f"{r['venue_id']}:{r['invoice_id']}")
        if uc:
            r["unit_check"] = uc
    stream = IA.filter_rows(rows, mode=mode, outcome=outcome, supplier=supplier, q=q)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "venues": _venue_list(db),
        "kpis": IA.kpis(rows),
        "rows": stream[offset : offset + limit],
        "total": len(stream),
        "offset": offset,
    }


@router.get("/invoice/{venue_id}/{invoice_id}")
def invoice_timeline(
    venue_id: str,
    invoice_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Everything Norm recorded about one invoice, oldest first."""
    out = IA.timeline(db, venue_id, invoice_id)
    if not out["items"] and not out["unit_check"]:
        raise HTTPException(404, "Norm has no record of this invoice")
    return out
