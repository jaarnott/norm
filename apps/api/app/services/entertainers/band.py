"""Band members of a "pay each member separately" band (plan §11.3, D14e).

The band record holds the bookings and the contact who confirms times;
each listed member (up to 20) gets a personal link, fills in the same form
for themselves and becomes their own performer record
(``member_performer_id``). Finance sets each member's share of the fee
(``share_bp``, basis points summing to 10,000 — equal by default); on
approval the amount is split into ``ent_performance_shares`` and each share
is paid through that member's own lane. The band's review flag clears when
every member has submitted; the band is payable only when every member is
accepted (``performers.payable``).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.entertainers_models import EntBandMember, EntPerformer
from app.db.models import _now
from app.services.entertainers import emails, tokens
from app.services.entertainers._shared import (
    iso,
    load_performer,
    require_any_venue,
)
from app.services.entertainers.performers import clean_email, clean_text, members_of
from app.services.finance_core.ctx import FinanceCtx, require_staff
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush

MAX_MEMBERS = 20
MEMBERS_PENDING = "Pay each band member separately — pending member onboarding"


def equal_shares(n: int) -> list[int]:
    """``n`` shares summing to 10,000, the remainder on the first."""
    if n <= 0:
        return []
    base, rem = divmod(10_000, n)
    return [base + rem] + [base] * (n - 1)


def member_dict(db: Session, member: EntBandMember) -> dict:
    accepted = False
    if member.member_performer_id:
        status = (
            db.query(EntPerformer.onboarding_status)
            .filter(
                EntPerformer.organization_id == member.organization_id,
                EntPerformer.id == member.member_performer_id,
            )
            .scalar()
        )
        accepted = status == "accepted"
    return {
        "id": member.id,
        "name": member.name,
        "email": member.email,
        "phone": member.phone,
        "share_bp": member.share_bp,
        "invite_status": member.invite_status,
        "member_performer_id": member.member_performer_id,
        "invite_sent_at": iso(member.invite_sent_at),
        "reminders_sent": member.reminders_sent,
        "last_reminder_at": iso(member.last_reminder_at),
        "sort_order": member.sort_order,
        "accepted": accepted,
    }


def _band(
    db: Session, ctx: FinanceCtx, performer_id: str, *, for_update=False
) -> EntPerformer:
    band = load_performer(db, ctx, performer_id, for_update=for_update)
    if band.band_mode != "members_split":
        raise Invalid("Only a 'pay each member separately' band has members.")
    return band


def list_members(db: Session, ctx: FinanceCtx, performer_id: str) -> list[dict]:
    require_staff(ctx)
    band = _band(db, ctx, performer_id)
    return [member_dict(db, m) for m in members_of(db, band)]


def set_members(
    db: Session,
    band: EntPerformer,
    members: list[dict],
    *,
    ctx: FinanceCtx | None = None,
    invite: bool = True,
) -> list[EntBandMember]:
    """Replace the band's member list: existing members (by id) keep their
    share and link; new ones get an equal share of what is unassigned and an
    invite; those left out are removed. Called from the form (no ctx) and
    from the Register."""
    if band.band_mode != "members_split":
        raise Invalid("Only a 'pay each member separately' band has members.")
    if not 1 <= len(members) <= MAX_MEMBERS:
        raise Invalid(f"List between 1 and {MAX_MEMBERS} band members.")
    current = {m.id: m for m in members_of(db, band)}
    kept: list[EntBandMember] = []
    new_rows: list[EntBandMember] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(members):
        name = clean_text(item.get("name"), "Member name", required=True)
        email = clean_email(item.get("email"))
        phone = clean_text(item.get("phone"), "Phone", limit=50)
        member_id = item.get("id")
        if member_id and member_id in current:
            row = current[member_id]
            row.name, row.email, row.phone, row.sort_order = name, email, phone, i
            kept.append(row)
            seen_ids.add(member_id)
            continue
        row = EntBandMember(
            organization_id=band.organization_id,
            band_performer_id=band.id,
            name=name,
            email=email,
            phone=phone,
            share_bp=0,
            sort_order=i,
        )
        db.add(row)
        new_rows.append(row)
    now = _now()
    for member_id, row in current.items():
        if member_id not in seen_ids:
            row.removed_at = now
    all_rows = kept + new_rows
    if not kept:
        for row, bp in zip(all_rows, equal_shares(len(all_rows))):
            row.share_bp = bp
    elif new_rows:
        # Spread what the kept members don't hold over the new ones.
        unassigned = 10_000 - sum(r.share_bp for r in kept)
        base, rem = divmod(max(unassigned, 0), len(new_rows))
        for i, row in enumerate(new_rows):
            row.share_bp = base + (rem if i == 0 else 0)
    flush(db)
    if not all(r.member_performer_id for r in all_rows):
        band.needs_review = True
        band.review_reason = MEMBERS_PENDING
    flush(db)
    if invite:
        for row in new_rows:
            if row.email:
                _send_member_invite(
                    db, band, row, created_by=ctx.user_id if ctx else None
                )
    return all_rows


def update_members(
    db: Session, ctx: FinanceCtx, performer_id: str, members: list[dict]
) -> list[dict]:
    require_any_venue(ctx)
    band = _band(db, ctx, performer_id, for_update=True)
    rows = set_members(db, band, members, ctx=ctx)
    return [member_dict(db, m) for m in rows]


def set_shares(
    db: Session, ctx: FinanceCtx, performer_id: str, shares: dict
) -> list[dict]:
    """Finance sets the split. Must cover every member and sum to 10,000."""
    require_staff(ctx)
    ctx.require("payroll:write")
    band = _band(db, ctx, performer_id, for_update=True)
    rows = members_of(db, band)
    if set(shares) != {r.id for r in rows}:
        raise Invalid("Give a share for every member of the band — no more, no fewer.")
    total = 0
    for row in rows:
        try:
            bp = int(shares[row.id])
        except (TypeError, ValueError) as exc:
            raise Invalid("Shares are whole basis points (10000 = 100%).") from exc
        if bp < 0:
            raise Invalid("A share can't be negative.")
        total += bp
        row.share_bp = bp
    if total != 10_000:
        raise Invalid(
            f"Shares must add up to 100% (10000 basis points) — these add up to {total}."
        )
    flush(db)
    return [member_dict(db, m) for m in rows]


def _send_member_invite(
    db: Session,
    band: EntPerformer,
    member: EntBandMember,
    *,
    created_by=None,
    reminder=False,
) -> dict:
    if not member.email:
        raise Invalid("Add the member's email address first.")
    _row, _token, url = tokens.mint(
        db,
        org_id=band.organization_id,
        purpose="band_member",
        performer_id=band.id,
        band_member_id=member.id,
        created_by=created_by,
    )
    now = _now()
    if reminder:
        member.reminders_sent = (member.reminders_sent or 0) + 1
        member.last_reminder_at = now
    else:
        member.invite_sent_at = now
    flush(db)
    org_id, member_id, band_id = band.organization_id, member.id, band.id
    address, name, band_name = member.email, member.name, band.name
    kind = "ent_band_reminder" if reminder else "ent_band_invite"

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind=kind,
            to=address,
            context={
                "name": name,
                "first_name": emails.first_name(name),
                "band_name": band_name,
                "url": url,
                "expires_days": tokens.TTL.days,
            },
            performer_id=band_id,
            band_member_id=member_id,
            target_id=member_id,
        )

    emails.later(db, _send)
    return {"sent": True, "url": url}


def invite_member(
    db: Session, ctx: FinanceCtx, performer_id: str, member_id: str
) -> dict:
    """Resend a member's link from the Register (a new link, the old revoked)."""
    require_any_venue(ctx)
    band = _band(db, ctx, performer_id, for_update=True)
    member = next((m for m in members_of(db, band) if m.id == member_id), None)
    if member is None:
        raise NotFound("Band member not found.")
    if member.member_performer_id:
        raise Conflict("This member has already submitted their form.")
    return _send_member_invite(db, band, member, created_by=ctx.user_id)


def member_submitted(
    db: Session, band: EntPerformer, member: EntBandMember, performer: EntPerformer
) -> None:
    """A member's form came back: link the new performer record and clear
    the band's review flag once every member is in."""
    member.member_performer_id = performer.id
    member.invite_status = "submitted"
    flush(db)
    if all(m.member_performer_id for m in members_of(db, band)):
        if band.review_reason == MEMBERS_PENDING:
            band.needs_review = False
            band.review_reason = None
    flush(db)


def all_members_accepted(db: Session, band: EntPerformer) -> bool:
    rows = members_of(db, band)
    if not rows:
        return False
    for member in rows:
        if not member.member_performer_id:
            return False
        status = (
            db.query(EntPerformer.onboarding_status)
            .filter(EntPerformer.id == member.member_performer_id)
            .scalar()
        )
        if status != "accepted":
            return False
    return True
