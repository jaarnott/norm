"""The quote page (``/q/<token>``): the customer's proposal, and Norm's own
option panel beneath it.

What the customer sees is the latest SENT proposal version — its frozen HTML
and CSS, sanitised again on the way out and wrapped in a CSP'd document for a
sandboxed iframe (``sanitize.proposal_document``) — and the options that
version offered, each with its CURRENT state. Confirm and Decline are drawn
from those option records, never from the HTML, so a figure in the document
can't be what gets booked.

**Confirming creates the event** (``min_spends.confirm_option``, the one
implementation): exactly once per enquiry, idempotent on a repeat, refused for
an option not on the sent quote, or one whose amount, date, times or area have
changed since it was sent — what the customer read is what they book. Staff
are emailed after the commit.

The page is up only while the organisation is ``live``; any other state, like
an unknown, revoked or expired link, is the same 404 (``resolve_quote``).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnContact,
    FnEnquiry,
    FnEvent,
    FnFile,
    FnMinSpend,
    FnProposal,
    FnProposalEvent,
    FnProposalImage,
    FnProposalVersion,
    FnPublicToken,
    FnVenueArea,
    FnVenueAreaPhoto,
    FnVenueBrand,
)
from app.db.models import Organization, Venue, _now
from app.services.functions import min_spends as min_spends_svc
from app.services.functions import notify
from app.services.functions import public_tokens
from app.services.functions import status as status_svc
from app.services.functions.company import format_money, get_company_settings
from app.services.functions.ctx import FunctionsCtx, for_quote
from app.services.functions.errors import Conflict, Invalid, NotFound, flush
from app.services.functions.events import BOOKING_LABELS
from app.services.functions.timeutil import local_now
from app.services.functions.sanitize import (
    PUBLIC_FILE_PATH,
    proposal_document,
    public_file_url,
)

VIEW_EVERY = dt.timedelta(hours=1)
MAX_NOTE = 1000
#: What an option's approval and booking are OF — if one changed after the
#: quote went out, the customer is looking at a figure that no longer exists.
_MATERIAL = (
    "venue_id",
    "area_id",
    "amount_cents",
    "event_date",
    "start_time",
    "end_time",
    "ends_next_day",
)
#: The only bytes the public files route serves (F3's uploads accept these;
#: SVG is refused there because it can carry script).
PUBLIC_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

_GONE = "This link isn't available."


def resolve_quote(db: Session, token: str) -> tuple[FnPublicToken, FunctionsCtx]:
    """The quote token and a customer ctx scoped to its enquiry — or NotFound
    for every way it can fail (including the organisation not being live, and
    the enquiry having been archived — Functions' "delete")."""
    row = public_tokens.resolve(db, token, "quote")
    if row is None:
        raise NotFound(_GONE)
    mode = (
        db.query(Organization.functions_mode)
        .filter(Organization.id == row.organization_id)
        .scalar()
    )
    archived = (
        db.query(FnEnquiry.archived_at)
        .filter(
            FnEnquiry.organization_id == row.organization_id,
            FnEnquiry.id == row.enquiry_id,
        )
        .scalar()
    )
    if mode != "live" or archived is not None:
        raise NotFound(_GONE)
    try:
        ctx = for_quote(db, row.organization_id, enquiry_id=row.enquiry_id)
    except NotFound as exc:
        raise NotFound(_GONE) from exc
    return row, ctx


def _latest_sent(db: Session, ctx: FunctionsCtx) -> FnProposalVersion | None:
    return (
        db.query(FnProposalVersion)
        .filter(
            FnProposalVersion.organization_id == ctx.org_id,
            FnProposalVersion.enquiry_id == ctx.client_enquiry_id,
            FnProposalVersion.sent_at.is_not(None),
        )
        .order_by(FnProposalVersion.version_number.desc())
        .first()
    )


def _norm(key: str, value):
    """Snapshot values are JSON; compare them with the row's typed values."""
    if value is None or value == "":
        return None
    if key == "event_date":
        return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)
    if key in ("start_time", "end_time"):
        return value if isinstance(value, dt.time) else dt.time.fromisoformat(value)
    if key == "amount_cents":
        return int(value)
    if key == "ends_next_day":
        return bool(value)
    return value


def matches_snapshot(option: FnMinSpend, snap: dict) -> bool:
    """Whether the option still says what the sent quote said (keys the
    snapshot doesn't carry can't be compared and are skipped)."""
    for key in _MATERIAL:
        if key not in snap:
            continue
        try:
            if _norm(key, snap[key]) != _norm(key, getattr(option, key)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _brand(db: Session, org_id: str, venue_id: str | None) -> dict | None:
    if venue_id is None:
        return None
    b = (
        db.query(FnVenueBrand)
        .filter(
            FnVenueBrand.organization_id == org_id, FnVenueBrand.venue_id == venue_id
        )
        .first()
    )
    if b is None:
        return None
    return {
        "logo_url": public_file_url(b.logo_file_id) if b.logo_file_id else None,
        "wordmark": b.wordmark,
        "tagline": b.tagline,
        "colours": b.colours or {},
        "fonts": b.fonts or {},
        "footer_contact": b.footer_contact,
    }


def venue_summary(db: Session, org_id: str, venue_id: str | None) -> dict | None:
    """Name and brand basics for a public page's chrome."""
    if venue_id is None:
        return None
    venue = (
        db.query(Venue)
        .filter(Venue.id == venue_id, Venue.organization_id == org_id)
        .first()
    )
    if venue is None:
        return None
    return {
        "id": venue.id,
        "name": venue.name,
        "brand": _brand(db, org_id, venue.id),
    }


def _when(value) -> str | None:
    return value.isoformat() if value is not None else None


def quote_payload(
    db: Session,
    ctx: FunctionsCtx,
    token_row: FnPublicToken,
    *,
    record_view: bool = True,
    now: dt.datetime | None = None,
) -> dict:
    """Everything the quote page shows. Records a ``viewed`` fact at most
    once an hour (only once a version has been sent)."""
    now = now or _now()
    enquiry = (
        db.query(FnEnquiry)
        .filter(
            FnEnquiry.organization_id == ctx.org_id,
            FnEnquiry.id == ctx.client_enquiry_id,
        )
        .one()
    )
    contact = (
        db.query(FnContact)
        .filter(
            FnContact.organization_id == ctx.org_id, FnContact.id == enquiry.contact_id
        )
        .first()
    )
    company = get_company_settings(db, ctx.org_id)
    version = _latest_sent(db, ctx)
    snapshots = [
        o for o in (version.options if version else []) or [] if isinstance(o, dict)
    ]
    ids = [o.get("min_spend_id") for o in snapshots if o.get("min_spend_id")]
    rows = {
        r.id: r
        for r in db.query(FnMinSpend).filter(
            FnMinSpend.organization_id == ctx.org_id,
            FnMinSpend.enquiry_id == enquiry.id,
            FnMinSpend.id.in_(ids or [""]),
        )
    }
    facts = status_svc.load_min_spend_facts(db, ctx.org_id, list(rows))
    enquiry_facts = status_svc.load_enquiry_facts(db, ctx.org_id, [enquiry.id])[
        enquiry.id
    ]
    lost = enquiry_facts.closure_kind == "lost"
    # Booked by any route (this page, a phone confirmation, another option).
    booked = enquiry_facts.has_live_event
    names = _names(db, ctx.org_id, rows.values())

    options = []
    for snap in snapshots:
        row = rows.get(snap.get("min_spend_id"))
        if row is None:
            continue  # deleted since — nothing to confirm
        options.append(
            _option_view(
                row,
                snap,
                facts[row.id],
                names,
                company.currency,
                lost=lost,
                booked=booked,
            )
        )

    # A group-wide enquiry is branded by the first venue its quote offered.
    venue_id = enquiry.venue_id or next(
        (rows[i].venue_id for i in ids if i in rows), None
    )
    proposal = None
    if version is not None:
        valid_until = None
        brand_row = (
            db.query(FnVenueBrand.valid_days)
            .filter(
                FnVenueBrand.organization_id == ctx.org_id,
                FnVenueBrand.venue_id == venue_id,
            )
            .first()
            if venue_id
            else None
        )
        valid_days = brand_row[0] if brand_row else 14
        if version.sent_at is not None and valid_days:
            sent_on = _local_date(db, ctx.org_id, venue_id, version.sent_at)
            valid_until = (sent_on + dt.timedelta(days=valid_days)).isoformat()
        title = enquiry.subject or "Your proposal"
        try:
            document = proposal_document(
                version.html or "", version.css or "", title=title
            )
        except Invalid:
            # Unrenderable (it predates the sanitiser's limits): the options
            # below still work, so show a note rather than fail the page.
            document = proposal_document(
                "<p>This proposal can't be shown here — please contact the venue.</p>",
                "",
                title=title,
            )
        proposal = {
            "version_number": version.version_number,
            "sent_at": _when(version.sent_at),
            "valid_until": valid_until,
            "document": document,
        }
        if record_view:
            _record_view(db, ctx, enquiry.id, version.id, now)
    public_tokens.touch(db, token_row, now=now)
    first_name = ((contact.name if contact else "") or "").strip().split(" ")[0] or None
    return {
        "enquiry": {
            "subject": enquiry.subject,
            "guests": enquiry.guests,
            "booking_type": enquiry.booking_type,
            "booking_label": BOOKING_LABELS.get(enquiry.booking_type or ""),
            "contact_first_name": first_name,
            "open": not lost,
        },
        "venue": venue_summary(db, ctx.org_id, venue_id),
        "proposal": proposal,
        "options": options,
        "booked": booked,
        "money": {"currency": company.currency, "tax_label": company.tax_label},
    }


def _local_date(db: Session, org_id: str, venue_id: str | None, instant) -> dt.date:
    """The venue's calendar date at ``instant`` (company zone as fallback)."""
    zone = (
        db.query(Venue.timezone).filter(Venue.id == venue_id).scalar()
        if venue_id
        else None
    ) or get_company_settings(db, org_id).default_timezone
    try:
        return local_now(zone, instant).date()
    except Exception:  # noqa: BLE001 — a bad zone name must not break the page
        return instant.date()


def _names(db: Session, org_id: str, rows) -> dict:
    rows = list(rows)
    areas = {
        a.id: a.name
        for a in db.query(FnVenueArea).filter(
            FnVenueArea.organization_id == org_id,
            FnVenueArea.id.in_({r.area_id for r in rows if r.area_id} or {""}),
        )
    }
    venues = {
        v.id: v.name
        for v in db.query(Venue).filter(
            Venue.organization_id == org_id,
            Venue.id.in_({r.venue_id for r in rows} or {""}),
        )
    }
    return {"areas": areas, "venues": venues}


def _option_view(row, snap, facts, names, currency, *, lost, booked) -> dict:
    state = status_svc.min_spend_state(facts)
    unchanged = matches_snapshot(row, snap)
    open_ = not lost and not booked
    can_confirm = (
        open_
        and unchanged
        and row.event_date is not None
        and status_svc.min_spend_confirmable(facts)
    )
    if state in ("confirmed", "withdrawn", "declined"):
        shown = state
    else:
        shown = "available" if can_confirm else "unavailable"

    def pick(key, current):
        return snap[key] if snap.get(key) not in (None, "") else _when(current)

    amount = snap.get("amount_cents", row.amount_cents)
    return {
        "id": row.id,
        "label": snap.get("label") or row.label,
        "venue_name": names["venues"].get(row.venue_id),
        "area_name": snap.get("area_name") or names["areas"].get(row.area_id),
        "event_date": pick("event_date", row.event_date),
        "start_time": pick("start_time", row.start_time),
        "end_time": pick("end_time", row.end_time),
        "ends_next_day": bool(snap.get("ends_next_day", row.ends_next_day)),
        "amount_cents": amount,
        "food_cents": snap.get("food_cents"),
        "total_cents": snap.get("total_cents"),
        "amount_text": format_money(amount, currency),
        "total_text": format_money(snap.get("total_cents"), currency)
        if snap.get("total_cents") is not None
        else None,
        "state": shown,
        "changed_since_sent": not unchanged and state not in ("confirmed", "withdrawn"),
        "can_confirm": can_confirm,
        "can_decline": open_ and state in ("proposed", "approved"),
    }


def _record_view(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, version_id: str, now
) -> None:
    recent = db.query(
        exists().where(
            FnProposalEvent.organization_id == ctx.org_id,
            FnProposalEvent.enquiry_id == enquiry_id,
            FnProposalEvent.kind == "viewed",
            FnProposalEvent.occurred_at > now - VIEW_EVERY,
        )
    ).scalar()
    if recent:
        return
    db.add(
        FnProposalEvent(
            organization_id=ctx.org_id,
            enquiry_id=enquiry_id,
            proposal_version_id=version_id,
            kind="viewed",
            source="quote_page",
            actor_label="customer",
            occurred_at=now,
        )
    )
    flush(db)


# ---------------------------------------------------------------------------
# The customer's answer
# ---------------------------------------------------------------------------


def respond(
    db: Session,
    ctx: FunctionsCtx,
    token_row: FnPublicToken,
    min_spend_id: str,
    kind: str,
    *,
    note: str | None = None,
) -> tuple[dict, list[notify.Notice]]:
    """Confirm (creating the event) or decline one offered option. Repeats
    are harmless: the same event, the same decline, and no second email.
    Returns the refreshed page and the staff emails to send after commit."""
    version = _latest_sent(db, ctx)
    snap = next(
        (
            o
            for o in (version.options if version else []) or []
            if isinstance(o, dict) and o.get("min_spend_id") == min_spend_id
        ),
        None,
    )
    if snap is None:
        raise NotFound("That option isn't on your quote.")
    option = min_spends_svc.get_min_spend(db, ctx, min_spend_id)
    text = (note or "").strip()[:MAX_NOTE] or None
    enquiry = (
        db.query(FnEnquiry)
        .filter(
            FnEnquiry.organization_id == ctx.org_id, FnEnquiry.id == option.enquiry_id
        )
        .one()
    )
    state = min_spends_svc.min_spend_state(db, ctx, option.id)
    _refuse_politely(db, ctx, enquiry, option, snap, kind, state)

    notices: list[notify.Notice] = []
    if kind == "confirm":
        event, created = min_spends_svc.confirm_option(
            db,
            ctx,
            option.id,
            channel="quote_page",
            note=text,
            public_token_id=token_row.id,
        )
        if created:
            notices.append(
                _staff_notice(db, ctx, enquiry, option, "confirm", text, event.id)
            )
    else:
        min_spends_svc.decline_option(
            db,
            ctx,
            option.id,
            channel="quote_page",
            note=text,
            public_token_id=token_row.id,
        )
        if state != "declined":
            notices.append(
                _staff_notice(db, ctx, enquiry, option, "decline", text, None)
            )
    flush(db)
    return quote_payload(db, ctx, token_row, record_view=False), notices


def _refuse_politely(db, ctx, enquiry, option, snap, kind, state) -> None:
    """Say no in the customer's terms before the service says it in staff's
    ("reinstate the event", "reopen it first")."""
    if state == "confirmed":
        event = (
            db.query(FnEvent)
            .filter(
                FnEvent.organization_id == ctx.org_id, FnEvent.id == option.event_id
            )
            .first()
        )
        if event is not None and event.cancelled_at is not None:
            raise Conflict("This booking was cancelled — please contact the venue.")
        if kind == "decline":
            raise Conflict(
                "You've confirmed this option — contact the venue to change it."
            )
        return  # a repeat confirm: the service returns the same event
    facts = status_svc.load_enquiry_facts(db, ctx.org_id, [enquiry.id])[enquiry.id]
    if facts.closure_kind == "lost":
        raise Conflict("This quote is no longer open — please contact the venue.")
    other_booked = (
        db.query(FnMinSpend.id)
        .filter(
            FnMinSpend.organization_id == ctx.org_id,
            FnMinSpend.enquiry_id == enquiry.id,
            FnMinSpend.event_id.is_not(None),
            FnMinSpend.id != option.id,
        )
        .first()
    )
    if other_booked is not None:
        raise Conflict(
            "You've already confirmed another option — contact the venue to change it."
        )
    if state == "withdrawn":
        raise Conflict("This option is no longer available.")
    if kind == "confirm":
        if not matches_snapshot(option, snap):
            raise Conflict(
                "This option has changed since your quote was sent — the venue "
                "will send you an updated one."
            )
        if option.event_date is None:
            raise Conflict(
                "This option needs a date before it can be confirmed — please contact the venue."
            )


def _staff_notice(db, ctx, enquiry, option, kind, note, event_id) -> notify.Notice:
    company = get_company_settings(db, ctx.org_id)
    contact = (
        db.query(FnContact.name)
        .filter(
            FnContact.organization_id == ctx.org_id, FnContact.id == enquiry.contact_id
        )
        .scalar()
    )
    names = _names(db, ctx.org_id, [option])
    when = option.event_date.strftime("%a %d %b %Y") if option.event_date else "No date"
    if option.start_time:
        when += f", {option.start_time.strftime('%H:%M')}"
        if option.end_time:
            when += f"–{option.end_time.strftime('%H:%M')}"
    place = " — ".join(
        p
        for p in (
            names["venues"].get(option.venue_id),
            names["areas"].get(option.area_id),
        )
        if p
    )
    rows = [
        ("Enquiry", enquiry.subject or "—"),
        ("Option", option.label or "—"),
        ("Where", place or "—"),
        ("When", when),
        ("Minimum spend", format_money(option.amount_cents, company.currency)),
    ]
    args = dict(
        org_id=ctx.org_id,
        venue_id=option.venue_id,
        owner_user_id=enquiry.owner_user_id,
        enquiry_id=enquiry.id,
        event_id=event_id,
        kind=kind,
        contact_name=contact or "The customer",
        summary_rows=rows,
        note=note,
    )
    return lambda session: notify.option_responded(session, **args)


# ---------------------------------------------------------------------------
# Public images
# ---------------------------------------------------------------------------


def public_file(db: Session, file_id: str) -> FnFile | None:
    """An image the public may load — only one that something public points
    at: a venue's brand images, its proposal image library, an area photo, or
    a proposal (draft or sent) that embeds it. Anything else in ``fn_files``
    (and anything of an organisation not using Functions) is None, which the
    route answers with the same 404 as a file that doesn't exist."""
    if not file_id or len(file_id) > 64:
        return None
    row = db.query(FnFile).filter(FnFile.id == file_id).first()
    if row is None or row.content_type not in PUBLIC_IMAGE_TYPES:
        return None
    mode = (
        db.query(Organization.functions_mode)
        .filter(Organization.id == row.organization_id)
        .scalar()
    )
    if mode not in ("shadow", "live"):
        return None
    org = row.organization_id
    path = f"{PUBLIC_FILE_PATH}{row.id}"
    referenced = or_(
        exists().where(
            FnVenueBrand.organization_id == org,
            or_(
                FnVenueBrand.logo_file_id == row.id,
                FnVenueBrand.cover_file_id == row.id,
                FnVenueBrand.closing_file_id == row.id,
            ),
        ),
        exists().where(
            FnProposalImage.organization_id == org, FnProposalImage.file_id == row.id
        ),
        exists().where(
            FnVenueAreaPhoto.organization_id == org, FnVenueAreaPhoto.file_id == row.id
        ),
        exists().where(
            FnProposal.organization_id == org,
            or_(
                FnProposal.html.contains(path, autoescape=True),
                FnProposal.css.contains(path, autoescape=True),
            ),
        ),
        exists().where(
            FnProposalVersion.organization_id == org,
            or_(
                FnProposalVersion.html.contains(path, autoescape=True),
                FnProposalVersion.css.contains(path, autoescape=True),
            ),
        ),
    )
    return row if db.execute(select(referenced)).scalar() else None
