"""``ent_email_links`` — the record behind the Emails tab (D32).

One row per email the module sent, pointing at its ``email_logs`` row
(where the exact HTML, the provider id and the delivery status live), plus
the Orbit send dates the importer carried over (``source = 'orbit'``, no log
row, date only). The page follows the Register — venue access — but the BODY
of an email that carries money (the approved amount, an invoice query) opens
only for ``payroll:write`` holders, as Payments does.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import cast, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    EntBandMember,
    EntEmailLink,
    EntPerformer,
    EntPublicToken,
)
from app.db.models import EmailLog, Venue, _now
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import Conflict, Forbidden, Invalid, NotFound

#: The kinds whose body carries a figure (plan §11.6 "who sees what").
MONEY_KINDS = frozenset({"ent_approved_amount", "ent_invoice_query"})
#: A manual resend of the same kind to the same target inside this window is
#: refused — "inside the throttle".
RESEND_QUIET = dt.timedelta(hours=24)


def record(
    db: Session,
    log: EmailLog | None,
    *,
    org_id: str,
    kind: str,
    recipient_kind: str,
    recipient_email: str | None,
    performer_id: str | None = None,
    band_member_id: str | None = None,
    venue_id: str | None = None,
    performance_ids: list[str] | None = None,
    sent_at: dt.datetime | None = None,
    source: str = "norm",
) -> EntEmailLink:
    row = EntEmailLink(
        organization_id=org_id,
        email_log_id=log.id if log is not None else None,
        performer_id=performer_id,
        band_member_id=band_member_id,
        venue_id=venue_id,
        recipient_kind=recipient_kind,
        kind=kind,
        recipient_email=recipient_email,
        subject=log.subject if log is not None else None,
        performance_ids=list(performance_ids) if performance_ids else None,
        sent_at=sent_at or _now(),
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def _target_filter(q, target_id: str | None):
    """Rows about ``target_id`` — a performance in ``performance_ids``, or
    the performer or band member the email went to."""
    if not target_id:
        return q
    return q.filter(
        or_(
            EntEmailLink.performer_id == target_id,
            EntEmailLink.band_member_id == target_id,
            _about_performance(target_id),
        )
    )


def _about_performance(performance_id: str):
    """``performance_ids @> '["<id>"]'`` — the column is JSON, so the
    containment operator needs a JSONB cast."""
    return cast(EntEmailLink.performance_ids, JSONB).contains([performance_id])


def _spellings(kinds: str | tuple[str, ...]) -> tuple[str, ...]:
    """Each kind with and without the ``ent_`` prefix: Norm's rows carry the
    template name (``ent_confirm_request``), the rows the importer carried
    from Orbit the bare kind (``confirm_request``)."""
    kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    out: list[str] = []
    for kind in kinds:
        bare = kind[4:] if kind.startswith("ent_") else kind
        out.extend((f"ent_{bare}", bare))
    return tuple(dict.fromkeys(out))


def send_count(
    db: Session, org_id: str, kinds: str | tuple[str, ...], *, target_id: str | None
) -> int:
    q = db.query(EntEmailLink).filter(
        EntEmailLink.organization_id == org_id, EntEmailLink.kind.in_(_spellings(kinds))
    )
    return _target_filter(q, target_id).count()


def last_sent_at(
    db: Session, org_id: str, kinds: str | tuple[str, ...], *, target_id: str | None
) -> dt.datetime | None:
    q = db.query(EntEmailLink.sent_at).filter(
        EntEmailLink.organization_id == org_id, EntEmailLink.kind.in_(_spellings(kinds))
    )
    row = _target_filter(q, target_id).order_by(EntEmailLink.sent_at.desc()).first()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Reading (the Emails tab)
# ---------------------------------------------------------------------------

_PURPOSE_FOR_KIND = {
    "ent_invite": "onboarding",
    "ent_invite_reminder": "onboarding",
    "ent_declaration_request": "declaration",
    "ent_band_invite": "band_member",
    "ent_band_reminder": "band_member",
    "ent_confirm_request": "portal",
    "ent_confirm_reminder": "portal",
    "ent_mismatch": "portal",
}


def _opened_at(db: Session, row: EntEmailLink) -> dt.datetime | None:
    """When the link inside was opened: the newest token of the email's
    purpose for its performer or member, used after the send."""
    purpose = _PURPOSE_FOR_KIND.get(row.kind)
    if purpose is None:
        return None
    q = db.query(EntPublicToken.last_used_at).filter(
        EntPublicToken.organization_id == row.organization_id,
        EntPublicToken.purpose == purpose,
        EntPublicToken.last_used_at.is_not(None),
        EntPublicToken.created_at <= row.sent_at + dt.timedelta(minutes=5),
    )
    if purpose == "band_member":
        q = q.filter(EntPublicToken.band_member_id == row.band_member_id)
    else:
        q = q.filter(EntPublicToken.performer_id == row.performer_id)
    found = q.order_by(EntPublicToken.created_at.desc()).first()
    if not found or found[0] is None or found[0] < row.sent_at:
        return None
    return found[0]


def link_dict(
    db: Session,
    row: EntEmailLink,
    *,
    log: EmailLog | None = None,
    names: dict | None = None,
) -> dict:
    names = names or {}
    if log is None and row.email_log_id:
        log = db.query(EmailLog).filter(EmailLog.id == row.email_log_id).first()
    to = (log.to_addresses or [None])[0] if log is not None else None
    return {
        "id": row.id,
        "kind": row.kind,
        "recipient_kind": row.recipient_kind,
        "recipient_email": row.recipient_email,
        "sent_to": to,
        "subject": row.subject or (log.subject if log is not None else None),
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "status": log.status if log is not None else None,
        "error": log.error_message if log is not None else None,
        "performer_id": row.performer_id,
        "performer_name": names.get(("performer", row.performer_id)),
        "band_member_id": row.band_member_id,
        "band_member_name": names.get(("member", row.band_member_id)),
        "venue_id": row.venue_id,
        "venue_name": names.get(("venue", row.venue_id)),
        "performance_ids": row.performance_ids or [],
        "source": row.source,
        "link_opened_at": opened.isoformat()
        if (opened := _opened_at(db, row))
        else None,
        "is_money": row.kind in MONEY_KINDS,
        "shadow_redirected": bool(
            to and row.recipient_email and to.lower() != row.recipient_email.lower()
        ),
    }


def _names(db: Session, org_id: str, rows: list[EntEmailLink]) -> dict:
    performer_ids = {r.performer_id for r in rows if r.performer_id}
    member_ids = {r.band_member_id for r in rows if r.band_member_id}
    venue_ids = {r.venue_id for r in rows if r.venue_id}
    out: dict = {}
    if performer_ids:
        for pid, name in db.query(EntPerformer.id, EntPerformer.name).filter(
            EntPerformer.organization_id == org_id, EntPerformer.id.in_(performer_ids)
        ):
            out[("performer", pid)] = name
    if member_ids:
        for mid, name in db.query(EntBandMember.id, EntBandMember.name).filter(
            EntBandMember.organization_id == org_id, EntBandMember.id.in_(member_ids)
        ):
            out[("member", mid)] = name
    if venue_ids:
        for vid, name in db.query(Venue.id, Venue.name).filter(Venue.id.in_(venue_ids)):
            out[("venue", vid)] = name
    return out


def _visible(ctx: FinanceCtx):
    """Venue-scoped rows at the caller's venues; organisation-wide rows
    (invites, codes) to everyone in the organisation."""
    return or_(
        EntEmailLink.venue_id.is_(None),
        EntEmailLink.venue_id.in_(sorted(ctx.venue_ids)),
    )


def list_links(
    db: Session,
    ctx: FinanceCtx,
    *,
    performer_id: str | None = None,
    band_member_id: str | None = None,
    venue_id: str | None = None,
    kind: str | None = None,
    recipient: str | None = None,
    performance_id: str | None = None,
    since: dt.date | None = None,
    until: dt.date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    if ctx.is_performer:
        raise Forbidden("Only venue staff can see sent emails.")
    q = db.query(EntEmailLink).filter(
        EntEmailLink.organization_id == ctx.org_id, _visible(ctx)
    )
    if performer_id:
        q = q.filter(EntEmailLink.performer_id == performer_id)
    if band_member_id:
        q = q.filter(EntEmailLink.band_member_id == band_member_id)
    if venue_id:
        if venue_id not in ctx.venue_ids:
            raise Forbidden("You do not have access to that venue.")
        q = q.filter(EntEmailLink.venue_id == venue_id)
    if kind:
        q = q.filter(EntEmailLink.kind.in_(_spellings(kind)))
    if recipient:
        q = q.filter(EntEmailLink.recipient_email.ilike(f"%{recipient.strip()}%"))
    if performance_id:
        q = q.filter(_about_performance(performance_id))
    if since:
        q = q.filter(
            EntEmailLink.sent_at
            >= dt.datetime.combine(since, dt.time.min, dt.timezone.utc)
        )
    if until:
        q = q.filter(
            EntEmailLink.sent_at
            < dt.datetime.combine(
                until + dt.timedelta(days=1), dt.time.min, dt.timezone.utc
            )
        )
    total = q.count()
    rows = (
        q.order_by(EntEmailLink.sent_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 500)))
        .all()
    )
    logs = {}
    log_ids = [r.email_log_id for r in rows if r.email_log_id]
    if log_ids:
        for log in db.query(EmailLog).filter(EmailLog.id.in_(log_ids)):
            logs[log.id] = log
    names = _names(db, ctx.org_id, rows)
    return [
        link_dict(db, r, log=logs.get(r.email_log_id), names=names) for r in rows
    ], total


def get_link(db: Session, ctx: FinanceCtx, link_id: str, *, with_body: bool) -> dict:
    """One sent email — with its exact HTML when asked for; a money email's
    body only to ``payroll:write``."""
    if ctx.is_performer:
        raise Forbidden("Only venue staff can see sent emails.")
    row = (
        db.query(EntEmailLink)
        .filter(
            EntEmailLink.organization_id == ctx.org_id,
            EntEmailLink.id == link_id,
            _visible(ctx),
        )
        .first()
    )
    if row is None:
        raise NotFound("Email not found.")
    log = (
        db.query(EmailLog).filter(EmailLog.id == row.email_log_id).first()
        if row.email_log_id
        else None
    )
    out = link_dict(db, row, log=log, names=_names(db, ctx.org_id, [row]))
    if with_body:
        if row.kind in MONEY_KINDS and not ctx.holds("payroll:write"):
            raise Forbidden(
                "This email carries payment figures — you need payroll:write to open it."
            )
        out["html_body"] = log.html_body if log is not None else None
        out["from_orbit"] = row.source == "orbit"
    return out


def resend(db: Session, ctx: FinanceCtx, link_id: str) -> dict:
    """Send the same kind of email again, where that makes sense, inside the
    throttle. Dispatches to the service that owns the kind."""
    from app.services.entertainers import (
        band,
        closing_manager,
        performances,
        performers,
    )

    if ctx.is_performer:
        raise Forbidden("Only venue staff can resend emails.")
    row = (
        db.query(EntEmailLink)
        .filter(
            EntEmailLink.organization_id == ctx.org_id,
            EntEmailLink.id == link_id,
            _visible(ctx),
        )
        .first()
    )
    if row is None:
        raise NotFound("Email not found.")
    resendable = (
        "ent_invite",
        "ent_invite_reminder",
        "ent_band_invite",
        "ent_band_reminder",
        "ent_confirm_request",
        "ent_confirm_reminder",
        "ent_manager_approval",
    )
    if row.kind not in resendable:
        raise Invalid(
            "That kind of email can't be resent — it is sent by an event, not by hand."
        )
    target = (
        (row.performance_ids or [None])[0] or row.performer_id or row.band_member_id
    )
    last = last_sent_at(db, ctx.org_id, row.kind, target_id=target)
    if last is not None and _now() - last < RESEND_QUIET:
        raise Conflict(
            "That email went out less than a day ago — wait before sending it again."
        )
    if row.kind in ("ent_invite", "ent_invite_reminder"):
        return performers.send_invite(db, ctx, row.performer_id)
    if row.kind in ("ent_band_invite", "ent_band_reminder"):
        return band.invite_member(db, ctx, row.performer_id, row.band_member_id)
    if row.kind in ("ent_confirm_request", "ent_confirm_reminder"):
        return performances.send_confirm_request(db, ctx, target, resend=True)
    if row.kind == "ent_manager_approval":
        return closing_manager.send_approval_request(db, ctx, target, resend=True)
    raise Invalid(
        "That kind of email can't be resent — it is sent by an event, not by hand."
    )
