"""The access log for the pay records' most sensitive fields (plan §6.5 item 3).

Every read of a bank account, an IRD number or a date of birth — on the
Register (the "account ending 34" values), on the one route that returns the
stored values, and the Loaded staff-detail read whose payload carries IRD,
bank and KiwiSaver fields — writes a ``fin_access_log`` row: who (the
person, or the actor label when nothing but a job was running), when, which
performer or staff member, which field, and the route that read it. The row
is the answer to "who looked at this person's bank account, and when" — a
question the Privacy Act lets the person ask.

Flushes only; the request or job commits. A read that fails to log is a
bug, not a reason to hide the value, so ``record`` raises on nothing but
a bad subject kind.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.finance_models import ACCESS_SUBJECT_KINDS, FinAccessLog
from app.db.models import _now
from app.services.finance_core.errors import Invalid


def record(
    db: Session,
    ctx,
    *,
    subject_kind: str,
    subject_id: str,
    field: str,
    route: str,
) -> FinAccessLog:
    """One row for one field of one person, read by the ctx's caller now."""
    if subject_kind not in ACCESS_SUBJECT_KINDS:
        raise Invalid(f"Unknown access-log subject kind {subject_kind!r}.")
    row = FinAccessLog(
        organization_id=ctx.org_id,
        subject_kind=subject_kind,
        subject_id=str(subject_id),
        field=field,
        user_id=ctx.user_id,
        actor_label=None if ctx.user_id else (ctx.actor_label or ctx.actor_kind),
        route=route,
        at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def reads_of(
    db: Session, org_id: str, *, subject_kind: str, subject_id: str, limit: int = 100
) -> list[FinAccessLog]:
    """The most recent reads of one person's sensitive fields, newest first."""
    return (
        db.query(FinAccessLog)
        .filter(
            FinAccessLog.organization_id == org_id,
            FinAccessLog.subject_kind == subject_kind,
            FinAccessLog.subject_id == str(subject_id),
        )
        .order_by(FinAccessLog.at.desc())
        .limit(limit)
        .all()
    )
