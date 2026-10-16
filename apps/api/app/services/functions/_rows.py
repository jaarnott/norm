"""Row plumbing every Functions service shares: load-if-visible, version
checks, and field updates that report exactly what changed (the change logs
and the optimistic-concurrency contract both depend on it)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.functions.ctx import FunctionsCtx
from app.services.functions.errors import Conflict, Invalid, NotFound


def load(
    db: Session,
    ctx: FunctionsCtx,
    model,
    row_id: str | None,
    *,
    label: str,
    venue_attr: str | None = "venue_id",
    allow_null_venue: bool = False,
    for_update: bool = False,
):
    """The row if it is in the caller's organisation and at one of their
    venues — otherwise ``NotFound`` (never "exists but not yours")."""
    if not row_id:
        raise NotFound(f"{label} not found.")
    q = db.query(model).filter(model.organization_id == ctx.org_id, model.id == row_id)
    if for_update:
        # populate_existing: after waiting for the lock, read what the other
        # transaction committed, not this session's cached copy.
        q = q.with_for_update().populate_existing()
    row = q.first()
    if row is None or not ctx.owns(model, row):
        raise NotFound(f"{label} not found.")
    if venue_attr is not None:
        venue_id = getattr(row, venue_attr)
        visible = (
            ctx.can_see_venue(venue_id)
            if allow_null_venue
            else venue_id is not None and venue_id in ctx.venue_ids
        )
        if not visible:
            raise NotFound(f"{label} not found.")
    return row


def check_version(row, expected_version: int | None) -> None:
    """Refuse an edit made against an older copy. ``None`` skips the check
    (callers that never saw a version, e.g. the agent); concurrent
    transactions are still caught by the mapper's version_id_col."""
    if expected_version is not None and int(expected_version) != row.version:
        raise Conflict(
            "This was changed by someone else since you opened it — reload and try again."
        )


def jsonable(value):
    if isinstance(value, (dt.date, dt.time, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def apply_fields(row, fields: dict, allowed) -> dict:
    """Set the allowed fields that actually differ; return ``{field: [old,
    new]}``. An unknown field is an error, not a silent drop — a typo would
    otherwise look like a successful save."""
    unknown = set(fields) - set(allowed)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    changes = {}
    for name, value in fields.items():
        old = getattr(row, name)
        if old != value:
            setattr(row, name, value)
            changes[name] = [jsonable(old), jsonable(value)]
    return changes


def require_text(value: str | None, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise Invalid(f"{what} is required.")
    return text
