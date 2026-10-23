"""The performer's link (``/p/<token>``, plan §11.4).

**The render contract:** upcoming and recent performances as venue, date,
start, end and status, with Confirm / My times differed / I didn't perform
and an invoice upload. **No amounts, rates, bank, IRD or contact details on
the link** — a forwarded email must not price a gig (Orbit's session
returned ``agreed_amount`` and the bank account). Figures reach the
performer by email, to the registered address (``ent_approved_amount``).

The link is off until the module is live (``entertainers_mode``): in shadow
the public route answers 404 "not open yet" — no performer reaches Norm
before the cut-over. Detail changes never happen here: a fresh onboarding
link with the code replaces "My details" (D32).

**Uploads:** a PDF (``%PDF-`` magic, a ``pypdf`` parse, no scripts or forms —
``files/pdf_check``) or an image (``functions/uploads.sniff_image``, called
unchanged), 10 MB at most, one file covering one or more of the performer's
own approved performances, at most five uploads per link per day. The bytes
go into ``ent_files`` behind the access check; the next tick's invoice check
reads them (§11.5).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    EntFile,
    EntPerformance,
    EntPerformer,
    EntPublicToken,
)
from app.db.models import Organization, Venue, _now
from app.services.entertainers import performances as performances_svc
from app.services.entertainers import status as status_svc
from app.services.entertainers import tokens
from app.services.entertainers._shared import (
    day,
    has_ended,
    hm,
    iso,
    settings_for,
    venue_tz,
)
from app.services.entertainers.performers import store_file
from app.services.finance_core.ctx import for_performer
from app.services.finance_core.errors import Invalid, NotFound, flush

RECENT_DAYS = 90
UPLOADS_PER_DAY = 5
#: $1,000,000 — no entertainer invoice is that; anything larger is a typo
#: or a probe, refused before it reaches an integer column.
MAX_INVOICE_CENTS = 100_000_000


class NotYet(NotFound):
    """The module is in shadow: the link exists but isn't open."""

    code = "not_yet"


def resolve(
    db: Session, token: str | None, *, now: dt.datetime | None = None
) -> tuple[EntPublicToken, EntPerformer]:
    """The live portal link and its performer — only when the module is
    live. Every other answer is the one 404."""
    row = tokens.resolve(db, token, purposes=("portal",), now=now)
    if row is None:
        raise NotFound("This link isn't valid.")
    org = db.query(Organization).filter(Organization.id == row.organization_id).first()
    mode = getattr(org, "entertainers_mode", "off") if org else "off"
    if mode == "off":
        raise NotFound("This link isn't valid.")
    if mode != "live":
        raise NotYet(
            "This link isn't open yet — you'll hear from the venue when it is."
        )
    performer = (
        db.query(EntPerformer)
        .filter(
            EntPerformer.organization_id == row.organization_id,
            EntPerformer.id == row.performer_id,
            EntPerformer.is_active.is_(True),
        )
        .first()
    )
    if performer is None:
        raise NotFound("This link isn't valid.")
    return row, performer


def ctx_for(db: Session, row: EntPublicToken):
    return for_performer(db, row.organization_id, row.performer_id)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _portal_status(perf: EntPerformance, derived: str, *, ended: bool) -> str:
    if derived == "paid":
        return "paid"
    if derived == "did_not_perform":
        return "did_not_perform"
    if derived == "mismatch":
        return "times_differ"
    if perf.performer_confirmed_at is not None:
        return "confirmed"
    return "awaiting_your_confirmation" if ended else "scheduled"


def _row(
    db: Session,
    perf: EntPerformance,
    venue: Venue | None,
    *,
    tolerance: int,
    now: dt.datetime,
) -> dict:
    tz = venue_tz(venue)
    ended = has_ended(perf, tz, now)
    derived = status_svc.derive(perf, tz=tz, tolerance=tolerance, now=now)
    invoice_number = None
    if perf.invoice_file_id:
        invoice_number = (
            db.query(EntFile.invoice_number)
            .filter(EntFile.id == perf.invoice_file_id)
            .scalar()
        )
    return {
        "id": perf.id,
        "venue_name": venue.name if venue else None,
        "performance_date": day(perf.performance_date),
        "start_time": hm(perf.start_time),
        "end_time": hm(perf.end_time),
        "ends_next_day": perf.ends_next_day,
        "status": _portal_status(perf, derived, ended=ended),
        "can_confirm": ended and perf.payment_status != "paid",
        "approved": perf.performance_status == "approved",
        "confirmed_at": iso(perf.performer_confirmed_at),
        "your_times": (
            {
                "start": hm(perf.performer_actual_start_time),
                "end": hm(perf.performer_actual_end_time),
                "ends_next_day": perf.performer_actual_ends_next_day,
            }
            if perf.performer_actual_start_time
            else None
        ),
        "did_not_perform": perf.performer_dnp,
        "invoice": {"uploaded": bool(perf.invoice_file_id), "number": invoice_number},
    }


def page(
    db: Session,
    row: EntPublicToken,
    performer: EntPerformer,
    *,
    now: dt.datetime | None = None,
) -> dict:
    """What the link shows. Keys are the contract in entertainers-api.md —
    and ``tests/test_entertainers_portal.py`` asserts no amount, rate, bank,
    IRD or contact detail is in the response."""
    now = now or _now()
    today = now.date()
    since = today - dt.timedelta(days=RECENT_DAYS)
    rows = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == row.organization_id,
            EntPerformance.performer_id == performer.id,
            EntPerformance.performance_date >= since,
        )
        .order_by(
            EntPerformance.performance_date.desc(), EntPerformance.start_time.desc()
        )
        .all()
    )
    venues = (
        {
            v.id: v
            for v in db.query(Venue).filter(Venue.id.in_({r.venue_id for r in rows}))
        }
        if rows
        else {}
    )
    tol = settings_for(db, row.organization_id).mismatch_tolerance_minutes
    upcoming, recent = [], []
    for perf in rows:
        item = _row(db, perf, venues.get(perf.venue_id), tolerance=tol, now=now)
        (
            recent
            if has_ended(perf, venue_tz(venues.get(perf.venue_id)), now)
            else upcoming
        ).append(item)
    upcoming.reverse()
    needs_invoice = (
        performer.lane in ("invoice", "exemption")
        or performer.band_mode == "members_split"
    )
    return {
        "performer_name": performer.name,
        "upcoming": upcoming,
        "recent": recent,
        "can_upload_invoice": needs_invoice,
        "lane_needs_invoice": needs_invoice,
        "uploads_left_today": max(0, UPLOADS_PER_DAY - uploads_today(db, row.id, now)),
    }


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def confirm(
    db: Session,
    row: EntPublicToken,
    performance_id: str,
    *,
    actual_start_time=None,
    actual_end_time=None,
    actual_ends_next_day: bool = False,
    did_not_perform: bool = False,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    now: dt.datetime | None = None,
) -> EntPerformance:
    ctx = ctx_for(db, row)
    return performances_svc.performer_confirm(
        db,
        ctx,
        performance_id,
        actual_start_time=actual_start_time,
        actual_end_time=actual_end_time,
        actual_ends_next_day=actual_ends_next_day,
        did_not_perform=did_not_perform,
        reason=reason,
        ip=ip,
        user_agent=user_agent,
        now=now,
    )


def _upload_key(token_id: str) -> str:
    return f"entpub:uploads:{token_id}"


def _day_end(now: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(
        now.astimezone(dt.timezone.utc).date() + dt.timedelta(days=1),
        dt.time.min,
        tzinfo=dt.timezone.utc,
    )


def uploads_today(db: Session, token_id: str, now: dt.datetime) -> int:
    found = db.execute(
        text(
            "SELECT count FROM mcp_rate_limits WHERE bucket_key = :key AND window_start = :until"
        ),
        {"key": _upload_key(token_id), "until": _day_end(now)},
    ).first()
    return found[0] if found else 0


def _count_upload(db: Session, token_id: str, now: dt.datetime) -> int:
    found = db.execute(
        text(
            "INSERT INTO mcp_rate_limits (id, bucket_key, window_start, count) "
            "VALUES (gen_random_uuid()::text, :key, :until, 1) "
            "ON CONFLICT (bucket_key, window_start) "
            "DO UPDATE SET count = mcp_rate_limits.count + 1 RETURNING count"
        ),
        {"key": _upload_key(token_id), "until": _day_end(now)},
    ).first()
    return found[0] if found else 1


class QuotaExceeded(Invalid):
    """More than five uploads on one link today — the router answers 429."""

    status_code = 429
    code = "quota"


def upload_invoice(
    db: Session,
    row: EntPublicToken,
    performer: EntPerformer,
    *,
    performance_ids: list[str],
    data: bytes,
    filename: str | None,
    content_type: str | None,
    invoice_number: str | None = None,
    invoice_amount: str | None = None,
    now: dt.datetime | None = None,
) -> EntFile:
    """One file for one or more of the performer's own approved
    performances. The number and amount the performer typed are kept on the
    file for the check; nothing is echoed back."""
    now = now or _now()
    ids = [str(i) for i in (performance_ids or []) if str(i).strip()]
    if not ids:
        raise Invalid("Pick the performance(s) this invoice covers.")
    rows = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == row.organization_id,
            EntPerformance.performer_id == performer.id,
            EntPerformance.id.in_(ids),
        )
        .with_for_update()
        .all()
    )
    if len(rows) != len(set(ids)):
        raise NotFound("Performance not found.")
    for perf in rows:
        if perf.payment_status == "paid":
            raise Invalid("That performance has already been paid.")
        if perf.performance_status != "approved":
            raise Invalid(
                "Upload the invoice once the venue has approved the performance."
            )
    if _count_upload(db, row.id, now) > UPLOADS_PER_DAY:
        raise QuotaExceeded("You've uploaded five files today — try again tomorrow.")
    file = store_file(
        db,
        row.organization_id,
        kind="invoice",
        data=data,
        filename=filename,
        content_type=content_type,
    )
    if file.kind != "invoice":
        raise Invalid("That file is already on record as a different kind of document.")
    number = " ".join(str(invoice_number or "").split())[:100] or None
    cents = None
    if invoice_amount not in (None, ""):
        try:
            cents = int(
                round(
                    float(str(invoice_amount).replace("$", "").replace(",", "")) * 100
                )
            )
        except (ValueError, OverflowError) as exc:
            # "nan" and "1e400" are numbers to float(); neither is an amount.
            raise Invalid("The invoice amount must be a number.") from exc
        if cents < 0:
            raise Invalid("The invoice amount can't be negative.")
        if cents > MAX_INVOICE_CENTS:
            raise Invalid("The invoice amount is larger than Norm accepts.")
    file.invoice_number = number or file.invoice_number
    file.invoice_cents = cents if cents is not None else file.invoice_cents
    if file.check_status in ("matched", "query", "unreadable"):
        file.check_status = "pending"  # new performances to check against
        file.check_findings = None
        file.checked_at = None
    for perf in rows:
        perf.invoice_file_id = file.id
    flush(db)
    return file
