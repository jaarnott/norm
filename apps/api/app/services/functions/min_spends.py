"""Minimum spends — options, manager approval, and the customer's answer.

The rules, in the order they bite:

1. **Approval means "OK to offer"**, nothing more. The venue's approver
   approves; the override approver may approve anything, with a reason; in an
   area that does not require approval, anyone with the venue may. A
   revoke is allowed to the same people, and only before the customer
   confirms. (Orbit checked all of this in the browser, and skipped it on the
   event screen; here it is the server's decision.)
2. **An approval covers exact terms.** It is of an amount, a venue, an area,
   a date and times (``terms_key``); edit any of them — in any state, a
   declined option included — and a live approval is revoked automatically,
   and the derived state stops counting it in any case.
3. **The customer's confirmation creates the event** — ``confirm_option``,
   from the quote page or recorded by staff from an email or call. Only an
   approved option (or one in a no-approval area) can be confirmed. It runs
   exactly once per enquiry: the enquiry row is locked, a repeat on the same
   option returns the same event, a confirm on a sibling is refused, and a
   partial unique index backs it up. The option and the enquiry's menus at
   that venue are LINKED to the event; the other options are withdrawn.
4. **Lowering a booked minimum is signed off** by the people who could have
   approved the option (owner, 16 Oct 2026): the approver, the override
   approver, or anyone in an area that needs no approval. The note is
   logged on the event and the sign-off in the approvals log. Raising it, or
   changing its label or notes, needs only the note.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.functions_models import (
    RESPONSE_CHANNELS,
    FnEvent,
    FnMinSpend,
    FnMinSpendApproval,
    FnMinSpendResponse,
    FnVenueArea,
    FnVenueFunctionSettings,
)
from app.db.models import _now
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import events as events_svc
from app.services.functions import status as status_svc
from app.services.functions._rows import apply_fields, check_version, load, require_text
from app.services.functions.ctx import (
    FunctionsCtx,
    require_live,
    require_staff,
    require_venue,
)
from app.services.functions.company import money_for_org
from app.services.functions.errors import Conflict, Forbidden, Invalid, NotFound, flush
from app.services.functions.timeutil import validate_window

MIN_SPEND_FIELDS = (
    "venue_id",
    "area_id",
    "label",
    "amount_cents",
    "event_date",
    "start_time",
    "end_time",
    "ends_next_day",
    "notes",
    "sort_order",
)
#: Fields an approval is OF — changing one revokes it.
MATERIAL_FIELDS = frozenset(
    {
        "venue_id",
        "area_id",
        "amount_cents",
        "event_date",
        "start_time",
        "end_time",
        "ends_next_day",
    }
)
#: What can still change once the customer has confirmed (the event owns
#: its own date, times and area from then on).
CONFIRMED_FIELDS = frozenset({"amount_cents", "label", "notes"})
CUSTOMER_CHANNELS = tuple(c for c in RESPONSE_CHANNELS if c != "import")


def get_min_spend(
    db: Session, ctx: FunctionsCtx, min_spend_id: str, *, for_update: bool = False
) -> FnMinSpend:
    """The option, if the caller can see BOTH its venue and the enquiry (or
    event) it belongs to — an option at your venue on an enquiry you can't
    see is not yours to edit, approve or answer (security review, Oct 2026).
    Either miss is the same ``NotFound``."""
    ms = load(
        db, ctx, FnMinSpend, min_spend_id, label="Minimum spend", for_update=for_update
    )
    try:
        if ms.enquiry_id is not None:
            enquiries_svc.get_enquiry(db, ctx, ms.enquiry_id)
        elif ms.event_id is not None:
            events_svc.get_event(db, ctx, ms.event_id)
    except NotFound:
        raise NotFound("Minimum spend not found.") from None
    return ms


def _facts(db: Session, ctx: FunctionsCtx, ms: FnMinSpend) -> status_svc.MinSpendFacts:
    return status_svc.load_min_spend_facts(db, ctx.org_id, [ms.id])[ms.id]


def min_spend_state(db: Session, ctx: FunctionsCtx, min_spend_id: str) -> str:
    ms = get_min_spend(db, ctx, min_spend_id)
    return status_svc.min_spend_state(_facts(db, ctx, ms))


def list_min_spends(
    db: Session,
    ctx: FunctionsCtx,
    *,
    enquiry_id: str | None = None,
    event_id: str | None = None,
) -> list[tuple[FnMinSpend, str, bool]]:
    """``(option, state, confirmable)`` for an enquiry's (or event's) options
    at the caller's venues."""
    q = db.query(FnMinSpend).filter(
        FnMinSpend.organization_id == ctx.org_id, ctx.venue_clause(FnMinSpend.venue_id)
    )
    if enquiry_id:
        enquiries_svc.get_enquiry(db, ctx, enquiry_id)
        q = q.filter(FnMinSpend.enquiry_id == enquiry_id)
    elif event_id:
        events_svc.get_event(db, ctx, event_id)
        q = q.filter(FnMinSpend.event_id == event_id)
    else:
        raise Invalid("Name an enquiry or an event.")
    rows = q.order_by(FnMinSpend.sort_order, FnMinSpend.created_at).all()
    facts = status_svc.load_min_spend_facts(db, ctx.org_id, [r.id for r in rows])
    return [
        (
            r,
            status_svc.min_spend_state(facts[r.id]),
            status_svc.min_spend_confirmable(facts[r.id]),
        )
        for r in rows
    ]


def _area(db: Session, ctx: FunctionsCtx, venue_id: str, area_id: str | None):
    if area_id is None:
        return None
    area = (
        db.query(FnVenueArea)
        .filter(
            FnVenueArea.organization_id == ctx.org_id,
            FnVenueArea.venue_id == venue_id,
            FnVenueArea.id == area_id,
        )
        .first()
    )
    if area is None:
        raise Invalid("That area is not at this venue.")
    return area


def _validate(db: Session, ctx: FunctionsCtx, values: dict) -> None:
    require_venue(ctx, values["venue_id"])
    events_svc._venue(db, ctx, values["venue_id"])
    _area(db, ctx, values["venue_id"], values.get("area_id"))
    amount = values.get("amount_cents")
    if amount is None or int(amount) < 0:
        raise Invalid("A minimum spend needs an amount of zero or more.")
    validate_window(
        values.get("start_time"),
        values.get("end_time"),
        bool(values.get("ends_next_day")),
    )


def add_min_spend(
    db: Session,
    ctx: FunctionsCtx,
    *,
    enquiry_id: str,
    venue_id: str,
    amount_cents: int,
    area_id: str | None = None,
    label: str | None = None,
    event_date: dt.date | None = None,
    start_time: dt.time | None = None,
    end_time: dt.time | None = None,
    ends_next_day: bool = False,
    notes: str | None = None,
    sort_order: int = 0,
) -> FnMinSpend:
    """Offer an option on an enquiry (proposed until approved)."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id)
    enquiries_svc.require_open_enquiry(db, ctx, enquiry)
    values = {
        "venue_id": venue_id,
        "area_id": area_id,
        "amount_cents": amount_cents,
        "start_time": start_time,
        "end_time": end_time,
        "ends_next_day": ends_next_day,
    }
    _validate(db, ctx, values)
    ms = FnMinSpend(
        organization_id=ctx.org_id,
        enquiry_id=enquiry.id,
        label=(label or "").strip() or None,
        event_date=event_date,
        notes=notes,
        sort_order=sort_order,
        created_by_user_id=ctx.user_id,
        created_by_label=ctx.actor_label,
        **{
            **values,
            "ends_next_day": bool(ends_next_day),
            "amount_cents": int(amount_cents),
        },
    )
    db.add(ms)
    flush(db)
    return ms


def _record_approval(
    db, ctx, ms, kind, *, reason=None, automatic=False
) -> FnMinSpendApproval:
    row = FnMinSpendApproval(
        organization_id=ctx.org_id,
        min_spend_id=ms.id,
        kind=kind,
        actor_user_id=ctx.user_id,
        actor_label=ctx.actor_label,
        reason=reason,
        amount_cents=ms.amount_cents,
        terms_key=status_svc.terms_key_of(ms),
        automatic=automatic,
        occurred_at=_now(),
    )
    db.add(row)
    ms.updated_at = _now()
    flush(db)
    return row


_TERM_LABELS = {
    "venue_id": "venue",
    "area_id": "area",
    "amount_cents": "amount",
    "event_date": "date",
    "start_time": "start time",
    "end_time": "end time",
    "ends_next_day": "next-day end",
}


def _terms_changed(db: Session, ctx: FunctionsCtx, changes: dict) -> str:
    """'Terms changed after approval: amount $9,000 -> $50, area' — the
    automatic revoke's reason, readable in the approvals log."""
    parts = []
    for name, label in _TERM_LABELS.items():
        if name not in changes:
            continue
        if name == "amount_cents":
            old, new = changes[name]
            parts.append(
                f"{label} {money_for_org(db, ctx.org_id, old)} -> "
                f"{money_for_org(db, ctx.org_id, new)}"
            )
        else:
            parts.append(label)
    return "Terms changed after approval: " + ", ".join(parts)


def update_min_spend(
    db: Session,
    ctx: FunctionsCtx,
    min_spend_id: str,
    *,
    expected_version: int | None = None,
    note: str | None = None,
    **fields,
) -> dict:
    """Edit an option. Returns ``{field: [old, new]}``.

    * Approved, and a material field changed -> automatic revoke (rule 2).
    * Confirmed -> only amount / label / notes, a ``note`` is required, and
      the edit is logged on the event (it changes the booked minimum).
      Lowering the amount needs an approver (rule 4).
    * Withdrawn -> refused.
    """
    require_live(ctx)
    require_staff(ctx)
    ms = get_min_spend(db, ctx, min_spend_id, for_update=True)
    check_version(ms, expected_version)
    facts = _facts(db, ctx, ms)
    state = status_svc.min_spend_state(facts)
    signoff = None
    if state == "withdrawn":
        raise Conflict("This option was withdrawn.")
    if state == "confirmed":
        refused = set(fields) - CONFIRMED_FIELDS
        if refused:
            raise Conflict(
                "This option is booked — change the event instead of: "
                + ", ".join(sorted(refused))
                + "."
            )
        note = require_text(note, "A note saying why the booked minimum changed")
        new_amount = fields.get("amount_cents")
        if new_amount is not None and int(new_amount) < ms.amount_cents:
            rights = approval_rights(db, ctx, ms)
            if not rights["lower"]:
                raise Forbidden(rights["why"] or "You cannot lower this minimum.")
            signoff = _signoff_kind(db, ctx, ms, facts)
    merged = {name: getattr(ms, name) for name in MIN_SPEND_FIELDS} | fields
    _validate(db, ctx, merged)
    if "label" in fields:
        fields["label"] = (fields["label"] or "").strip() or None
    changes = apply_fields(ms, fields, MIN_SPEND_FIELDS)
    flush(db)
    if not changes:
        return changes
    # An approval covers exact terms. Moving any of them retires a live
    # approval in EVERY state — not only "approved": a declined option
    # edited and then confirmed was exactly the bypass (security review).
    # (The derived state also stops counting a stale approval on its own;
    # this fact is the audit trail.) A booked option's changes are logged on
    # its event below instead.
    if state != "confirmed" and MATERIAL_FIELDS & set(changes):
        latest = _latest_approval(db, ctx, ms)
        if latest is not None and latest.kind in ("approve", "override"):
            _record_approval(
                db,
                ctx,
                ms,
                "revoke",
                reason=_terms_changed(db, ctx, changes),
                automatic=True,
            )
    if state == "confirmed":
        event = (
            db.query(FnEvent)
            .filter(FnEvent.organization_id == ctx.org_id, FnEvent.id == ms.event_id)
            .first()
        )
        confirmed = (
            status_svc.event_status_for(db, ctx.org_id, event.id).status == "confirmed"
        )
        events_svc._log(
            db,
            ctx,
            event,
            "edited",
            section="details",
            changes={f"min_spend.{k}": v for k, v in changes.items()},
            after_confirmation=confirmed,
            note=note,
        )
        event.updated_at = _now()
        flush(db)
        if signoff and "amount_cents" in changes:
            _record_approval(
                db, ctx, ms, signoff, reason=f"Booked minimum lowered: {note}"
            )
    return changes


def withdraw_min_spend(
    db: Session, ctx: FunctionsCtx, min_spend_id: str, *, reason: str | None = None
) -> FnMinSpend:
    """Staff take an option off the table. Not once it is booked."""
    require_live(ctx)
    require_staff(ctx)
    ms = get_min_spend(db, ctx, min_spend_id, for_update=True)
    if ms.event_id is not None:
        raise Conflict("This option is booked — cancel the event instead.")
    if ms.withdrawn_at is None:
        ms.withdrawn_at = _now()
        ms.withdrawn_by_user_id = ctx.user_id
        ms.withdraw_reason = (reason or "").strip() or None
        flush(db)
    return ms


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def _settings(
    db: Session, ctx: FunctionsCtx, venue_id: str
) -> FnVenueFunctionSettings | None:
    return (
        db.query(FnVenueFunctionSettings)
        .filter(
            FnVenueFunctionSettings.organization_id == ctx.org_id,
            FnVenueFunctionSettings.venue_id == venue_id,
        )
        .first()
    )


def approval_rights(db: Session, ctx: FunctionsCtx, ms: FnMinSpend) -> dict:
    """What this caller may do to this option's approval — ``{approve,
    override, revoke, why}``. The UI shows buttons from this; the actions
    below enforce the same answers."""
    facts = _facts(db, ctx, ms)
    state = status_svc.min_spend_state(facts)
    settings = _settings(db, ctx, ms.venue_id)
    approver = settings.approver_user_id if settings else None
    override = settings.override_approver_user_id if settings else None
    user = ctx.user_id
    no_approval_needed = not status_svc.needs_approval(facts)
    is_approver = bool(user) and user == approver
    is_override = bool(user) and user == override
    open_ = state in ("proposed", "approved", "declined")
    rights = {
        "approve": bool(user)
        and open_
        and state != "approved"
        and (no_approval_needed or is_approver),
        "override": bool(user) and open_ and state != "approved" and is_override,
        "revoke": bool(user)
        and state == "approved"
        and (no_approval_needed or is_approver or is_override),
        # Lowering a booked minimum (rule 4).
        "lower": bool(user)
        and state == "confirmed"
        and (no_approval_needed or is_approver or is_override),
        "why": None,
    }
    if not user:
        rights["why"] = "Approvals are made by a person."
    elif state == "confirmed" and not rights["lower"]:
        rights["why"] = (
            "This venue has no minimum-spend approver set (Functions settings)."
            if not approver and not override
            else "Only the venue's minimum-spend approver can lower a booked minimum."
        )
    elif state in ("confirmed", "withdrawn"):
        rights["why"] = f"This option is {state}."
    elif not no_approval_needed and not approver and not override:
        rights["why"] = (
            "This venue has no minimum-spend approver set (Functions settings)."
        )
    elif not (no_approval_needed or is_approver or is_override):
        rights["why"] = "Only the venue's minimum-spend approver can approve this."
    return rights


def _signoff_kind(db, ctx, ms, facts) -> str:
    """How a permitted sign-off is logged: ``override`` when only the
    override approver could give it, else ``approve``."""
    settings = _settings(db, ctx, ms.venue_id)
    if (
        status_svc.needs_approval(facts)
        and settings is not None
        and ctx.user_id != settings.approver_user_id
        and ctx.user_id == settings.override_approver_user_id
    ):
        return "override"
    return "approve"


def approve_min_spend(
    db: Session, ctx: FunctionsCtx, min_spend_id: str
) -> FnMinSpendApproval:
    """Standard approval by the venue's approver (anyone, in an area that
    needs none). Approving an approved option returns the existing fact."""
    require_live(ctx)
    require_staff(ctx)
    ms = get_min_spend(db, ctx, min_spend_id, for_update=True)
    rights = approval_rights(db, ctx, ms)
    if (
        status_svc.min_spend_state(_facts(db, ctx, ms)) == "approved"
        and rights["revoke"]
    ):
        return _latest_approval(db, ctx, ms)
    if not rights["approve"]:
        raise Forbidden(rights["why"] or "You cannot approve this option.")
    return _record_approval(db, ctx, ms, "approve")


def override_min_spend(
    db: Session, ctx: FunctionsCtx, min_spend_id: str, *, reason: str
) -> FnMinSpendApproval:
    """The override approver approves, saying why."""
    require_live(ctx)
    require_staff(ctx)
    text = require_text(reason, "A reason for the override")
    ms = get_min_spend(db, ctx, min_spend_id, for_update=True)
    rights = approval_rights(db, ctx, ms)
    if not rights["override"]:
        if status_svc.min_spend_state(_facts(db, ctx, ms)) == "approved":
            raise Conflict("This option is already approved.")
        raise Forbidden("Only the venue's override approver can override.")
    return _record_approval(db, ctx, ms, "override", reason=text)


def revoke_approval(
    db: Session, ctx: FunctionsCtx, min_spend_id: str, *, reason: str | None = None
) -> FnMinSpendApproval:
    """Take an approval back — only before the customer confirms."""
    require_live(ctx)
    require_staff(ctx)
    ms = get_min_spend(db, ctx, min_spend_id, for_update=True)
    state = status_svc.min_spend_state(_facts(db, ctx, ms))
    if state == "confirmed":
        raise Conflict(
            "The customer has confirmed this option — it can't be un-approved."
        )
    if state != "approved":
        raise Conflict("This option is not approved.")
    if not approval_rights(db, ctx, ms)["revoke"]:
        raise Forbidden("Only the venue's approvers can revoke this approval.")
    return _record_approval(
        db, ctx, ms, "revoke", reason=(reason or "").strip() or None
    )


def _latest_approval(db, ctx, ms) -> FnMinSpendApproval | None:
    return (
        db.query(FnMinSpendApproval)
        .filter(
            FnMinSpendApproval.organization_id == ctx.org_id,
            FnMinSpendApproval.min_spend_id == ms.id,
        )
        .order_by(
            FnMinSpendApproval.occurred_at.desc(),
            FnMinSpendApproval.created_at.desc(),
            FnMinSpendApproval.id.desc(),
        )
        .first()
    )


def approvals(
    db: Session, ctx: FunctionsCtx, min_spend_id: str
) -> list[FnMinSpendApproval]:
    get_min_spend(db, ctx, min_spend_id)
    return (
        db.query(FnMinSpendApproval)
        .filter(
            FnMinSpendApproval.organization_id == ctx.org_id,
            FnMinSpendApproval.min_spend_id == min_spend_id,
        )
        .order_by(FnMinSpendApproval.occurred_at, FnMinSpendApproval.created_at)
        .all()
    )


# ---------------------------------------------------------------------------
# The customer's answer
# ---------------------------------------------------------------------------


def _check_channel(ctx: FunctionsCtx, channel: str) -> None:
    if channel not in CUSTOMER_CHANNELS:
        raise Invalid(f"Channel must be one of {', '.join(CUSTOMER_CHANNELS)}.")
    if channel != "quote_page":
        # Staff recording what the customer said by email / phone / in person.
        require_staff(ctx)
        if not ctx.user_id:
            raise Forbidden("A confirmation by email or phone is recorded by a person.")


def _response(db, ctx, ms, kind, channel, customer_email, note, public_token_id):
    row = FnMinSpendResponse(
        organization_id=ctx.org_id,
        min_spend_id=ms.id,
        kind=kind,
        channel=channel,
        recorded_by_user_id=ctx.user_id if channel != "quote_page" else None,
        customer_email=(customer_email or ctx.client_email or "").strip().lower()
        or None,
        public_token_id=public_token_id,
        note=(note or "").strip() or None,
        occurred_at=_now(),
    )
    db.add(row)
    return row


def confirm_option(
    db: Session,
    ctx: FunctionsCtx,
    min_spend_id: str,
    *,
    channel: str,
    customer_email: str | None = None,
    note: str | None = None,
    public_token_id: str | None = None,
    name: str | None = None,
) -> tuple[FnEvent, bool]:
    """The customer said yes to this option: create the event, exactly once.

    Returns ``(event, created)``. A second call for the same option returns
    the same event with ``created`` False (double clicks, retries, a staff
    member recording an email the customer also clicked). A call for a
    DIFFERENT option of an enquiry that is already booked is a ``Conflict``.
    """
    require_live(ctx)
    _check_channel(ctx, channel)
    probe = get_min_spend(db, ctx, min_spend_id)
    if probe.enquiry_id is None:
        raise Invalid("This option is not on an enquiry.")
    # Lock order: enquiry, then option. Every confirmation of this enquiry
    # queues here, so the checks below see the winner's commit.
    enquiry = enquiries_svc.get_enquiry(db, ctx, probe.enquiry_id, for_update=True)
    ms = get_min_spend(db, ctx, min_spend_id, for_update=True)

    if ms.event_id is not None:
        event = (
            db.query(FnEvent)
            .filter(FnEvent.organization_id == ctx.org_id, FnEvent.id == ms.event_id)
            .first()
        )
        if event is not None and event.cancelled_at is not None:
            raise Conflict("This booking was cancelled — reinstate the event instead.")
        return event, False
    live = (
        db.query(FnEvent)
        .filter(
            FnEvent.organization_id == ctx.org_id,
            FnEvent.enquiry_id == enquiry.id,
            FnEvent.cancelled_at.is_(None),
        )
        .first()
    )
    if live is not None:
        raise Conflict("This enquiry is already booked with another option.")
    enquiries_svc.require_open_enquiry(db, ctx, enquiry)

    facts = _facts(db, ctx, ms)
    if ms.withdrawn_at is not None:
        raise Conflict("This option is no longer available.")
    # facts were read under the row lock, so the approval is checked against
    # the terms being booked right now.
    if not status_svc.min_spend_confirmable(facts):
        if facts.approval_kind in ("approve", "override") and not facts.approval_covers:
            raise Forbidden(
                "This option changed after it was approved — it needs approving "
                "again before it can be confirmed."
            )
        raise Forbidden(
            "This option hasn't been approved yet, so it can't be confirmed."
        )
    if ms.event_date is None:
        raise Invalid("This option has no date — add one before it can be booked.")

    event = events_svc.new_event(
        db,
        ctx,
        venue_id=ms.venue_id,
        contact_id=enquiry.contact_id,
        event_date=ms.event_date,
        created_via="min_spend",
        enquiry=enquiry,
        name=name,
        booking_type=enquiry.booking_type,
        area_id=ms.area_id,
        start_time=ms.start_time,
        end_time=ms.end_time,
        ends_next_day=ms.ends_next_day,
        guests=enquiry.guests,
        confirmation_channel=channel,
        note=note or f"Confirmed by the customer ({channel.replace('_', ' ')}).",
    )
    ms.event_id = event.id
    _response(db, ctx, ms, "confirm", channel, customer_email, note, public_token_id)
    siblings = (
        db.query(FnMinSpend)
        .filter(
            FnMinSpend.organization_id == ctx.org_id,
            FnMinSpend.enquiry_id == enquiry.id,
            FnMinSpend.id != ms.id,
            FnMinSpend.event_id.is_(None),
            FnMinSpend.withdrawn_at.is_(None),
        )
        .all()
    )
    for other in siblings:
        other.withdrawn_at = _now()
        other.withdraw_reason = "Another option was confirmed"
    flush(db)
    return event, True


def decline_option(
    db: Session,
    ctx: FunctionsCtx,
    min_spend_id: str,
    *,
    channel: str,
    customer_email: str | None = None,
    note: str | None = None,
    public_token_id: str | None = None,
) -> FnMinSpendResponse:
    """The customer said no to this option. Repeating returns the same fact."""
    require_live(ctx)
    _check_channel(ctx, channel)
    ms = get_min_spend(db, ctx, min_spend_id, for_update=True)
    state = status_svc.min_spend_state(_facts(db, ctx, ms))
    if state == "confirmed":
        raise Conflict("This option is booked — cancel the event instead.")
    if state == "withdrawn":
        raise Conflict("This option is no longer available.")
    if state == "declined":
        latest = (
            db.query(FnMinSpendResponse)
            .filter(
                FnMinSpendResponse.organization_id == ctx.org_id,
                FnMinSpendResponse.min_spend_id == ms.id,
            )
            .order_by(
                FnMinSpendResponse.occurred_at.desc(),
                FnMinSpendResponse.created_at.desc(),
            )
            .first()
        )
        if latest is not None:
            return latest
    row = _response(
        db, ctx, ms, "decline", channel, customer_email, note, public_token_id
    )
    ms.updated_at = _now()
    flush(db)
    return row


def responses(
    db: Session, ctx: FunctionsCtx, min_spend_id: str
) -> list[FnMinSpendResponse]:
    get_min_spend(db, ctx, min_spend_id)
    return (
        db.query(FnMinSpendResponse)
        .filter(
            FnMinSpendResponse.organization_id == ctx.org_id,
            FnMinSpendResponse.min_spend_id == min_spend_id,
        )
        .order_by(FnMinSpendResponse.occurred_at, FnMinSpendResponse.created_at)
        .all()
    )


def booked_option(db: Session, ctx: FunctionsCtx, event_id: str) -> FnMinSpend | None:
    """The option an event was booked on, if any."""
    events_svc.get_event(db, ctx, event_id)
    return (
        db.query(FnMinSpend)
        .filter(
            FnMinSpend.organization_id == ctx.org_id, FnMinSpend.event_id == event_id
        )
        .first()
    )
