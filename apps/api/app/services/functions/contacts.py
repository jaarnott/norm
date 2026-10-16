"""Contacts — the customers behind enquiries and events.

A contact belongs to the organisation, not to a venue, and is visible to a
caller who can see one of its enquiries or events (or to anyone in the org
while it has none, so a contact just created is not invisible to its
creator). Email addresses are unique per organisation, case-insensitively —
so an inbound email or a form lead resolves to at most one contact, and
``find_or_create_contact`` is how every entry point attaches to it.

**Other venues' customers are not disclosed** (security review, Oct 2026).
Attaching an enquiry or booking by email always attaches to the org's one
contact for that address — the same answer whether or not another venue
already knows them (the contact then becomes visible through the new
record, which is the visibility rule above). ``find_contact_by_email`` only
finds contacts the caller can already see, and creating a contact (or adding
an address) that another contact uses says so only when the caller can see
that contact; otherwise the refusal is generic and confirms nothing.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, func, or_, true
from sqlalchemy.orm import Session

from app.db.functions_models import FnContact, FnContactEmail, FnEnquiry, FnEvent
from app.db.models import _now
from app.services.functions import addresses
from app.services.functions._rows import apply_fields, check_version, load, require_text
from app.services.functions.ctx import FunctionsCtx, require_live, require_staff
from app.services.functions.errors import Conflict, Invalid, NotFound, flush

#: Kept for callers that imported it here; the rule lives in addresses.py.
EMAIL_MAX_LENGTH = addresses.MAX_LENGTH
CONTACT_FIELDS = ("name", "company", "phone", "notes")
#: The refusal when an address belongs to a contact the caller can't see.
_ADDRESS_UNAVAILABLE = (
    "That email address can't be used here — ask a manager to add it."
)


def normalise_email(email: str | None) -> str:
    """The address trimmed and lower-cased, or ``Invalid`` — the strict check
    in addresses.py (dot-atom local part, letter/digit/hyphen domain labels,
    254 characters), since these addresses are sent to."""
    value = addresses.normalise(email)
    if value is None:
        raise Invalid(f"'{(email or '')[:80]}' is not an email address.")
    return value


def contact_visible_clause(ctx: FunctionsCtx):
    """SQL condition on ``FnContact`` for this caller."""
    if ctx.is_owner:
        return true()
    c = FnContact
    own_enquiry = (
        exists()
        .where(
            FnEnquiry.organization_id == c.organization_id,
            FnEnquiry.contact_id == c.id,
            ctx.venue_clause(FnEnquiry.venue_id, allow_null=True),
        )
        .correlate(c)
    )
    own_event = (
        exists()
        .where(
            FnEvent.organization_id == c.organization_id,
            or_(FnEvent.contact_id == c.id, FnEvent.invoice_contact_id == c.id),
            ctx.venue_clause(FnEvent.venue_id),
        )
        .correlate(c)
    )
    any_enquiry = (
        exists()
        .where(
            FnEnquiry.organization_id == c.organization_id, FnEnquiry.contact_id == c.id
        )
        .correlate(c)
    )
    any_event = (
        exists()
        .where(
            FnEvent.organization_id == c.organization_id,
            or_(FnEvent.contact_id == c.id, FnEvent.invoice_contact_id == c.id),
        )
        .correlate(c)
    )
    return or_(own_enquiry, own_event, and_(~any_enquiry, ~any_event))


def get_contact(db: Session, ctx: FunctionsCtx, contact_id: str) -> FnContact:
    contact = load(db, ctx, FnContact, contact_id, label="Contact", venue_attr=None)
    visible = (
        db.query(FnContact.id)
        .filter(FnContact.id == contact.id, contact_visible_clause(ctx))
        .first()
    )
    if visible is None:
        raise NotFound("Contact not found.")
    return contact


def contact_emails(
    db: Session, ctx: FunctionsCtx, contact_id: str
) -> list[FnContactEmail]:
    get_contact(db, ctx, contact_id)
    return (
        db.query(FnContactEmail)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == contact_id,
        )
        .order_by(FnContactEmail.is_primary.desc(), FnContactEmail.created_at)
        .all()
    )


def find_contact_by_email(
    db: Session, ctx: FunctionsCtx, email: str
) -> FnContact | None:
    """The contact for an address — if the caller can see it (else None, the
    same answer as no contact at all)."""
    contact = _org_contact_for_email(db, ctx, email)
    if contact is None:
        return None
    visible = (
        db.query(FnContact.id)
        .filter(FnContact.id == contact.id, contact_visible_clause(ctx))
        .first()
    )
    return contact if visible is not None else None


def _org_contact_for_email(db: Session, ctx: FunctionsCtx, email: str):
    """The org's one contact for an address, whoever can see it — for
    de-duplication only; never returned to a caller who can't see it."""
    try:
        value = normalise_email(email)
    except Invalid:
        return None
    return (
        db.query(FnContact)
        .join(
            FnContactEmail,
            and_(
                FnContactEmail.organization_id == FnContact.organization_id,
                FnContactEmail.contact_id == FnContact.id,
            ),
        )
        .filter(
            FnContact.organization_id == ctx.org_id,
            func.lower(FnContactEmail.email) == value,
        )
        .first()
    )


def search_contacts(
    db: Session,
    ctx: FunctionsCtx,
    q: str | None = None,
    *,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[FnContact]:
    """Contacts matching a name, company, phone or email fragment, by name
    (id breaks ties, so pages never overlap); ``offset`` skips that many
    before the cap of ``limit`` (at most 500)."""
    query = db.query(FnContact).filter(
        FnContact.organization_id == ctx.org_id,
        contact_visible_clause(ctx),
        ctx.record_clause(FnContact),
    )
    if not include_archived:
        query = query.filter(FnContact.archived_at.is_(None))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        email_match = (
            exists()
            .where(
                FnContactEmail.organization_id == FnContact.organization_id,
                FnContactEmail.contact_id == FnContact.id,
                func.lower(FnContactEmail.email).like(like),
            )
            .correlate(FnContact)
        )
        query = query.filter(
            or_(
                func.lower(FnContact.name).like(like),
                func.lower(func.coalesce(FnContact.company, "")).like(like),
                func.coalesce(FnContact.phone, "").like(like),
                email_match,
            )
        )
    return (
        query.order_by(FnContact.name, FnContact.id)
        .offset(max(0, int(offset)))
        .limit(min(int(limit), 500))
        .all()
    )


def _add_email(
    db, ctx, contact, email, *, primary=False, source="manual"
) -> FnContactEmail:
    value = normalise_email(email)
    existing = _org_contact_for_email(db, ctx, value)
    if existing is not None and existing.id != contact.id:
        _refuse_taken(db, ctx, existing)
    if existing is not None:
        row = (
            db.query(FnContactEmail)
            .filter(
                FnContactEmail.organization_id == ctx.org_id,
                FnContactEmail.contact_id == contact.id,
                func.lower(FnContactEmail.email) == value,
            )
            .first()
        )
    else:
        row = FnContactEmail(
            organization_id=ctx.org_id,
            contact_id=contact.id,
            email=value,
            source=source,
        )
        db.add(row)
    has_primary = (
        db.query(FnContactEmail.id)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == contact.id,
            FnContactEmail.is_primary.is_(True),
        )
        .first()
    )
    if primary or not has_primary:
        _set_primary(db, ctx, contact.id, row)
    flush(db)
    return row


def _set_primary(db, ctx, contact_id, row) -> None:
    db.query(FnContactEmail).filter(
        FnContactEmail.organization_id == ctx.org_id,
        FnContactEmail.contact_id == contact_id,
        FnContactEmail.is_primary.is_(True),
    ).update({FnContactEmail.is_primary: False}, synchronize_session="fetch")
    row.is_primary = True


def _refuse_taken(db: Session, ctx: FunctionsCtx, owner: FnContact) -> None:
    """An address another contact uses: name the clash only to a caller who
    can see that contact."""
    visible = (
        db.query(FnContact.id)
        .filter(FnContact.id == owner.id, contact_visible_clause(ctx))
        .first()
    )
    if visible is not None:
        raise Conflict("Another contact already uses that email address.")
    raise Conflict(_ADDRESS_UNAVAILABLE)


def create_contact(
    db: Session,
    ctx: FunctionsCtx,
    *,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> FnContact:
    """A new contact. An email another contact already has is a ``Conflict``
    — use ``find_or_create_contact`` to attach to them instead."""
    require_live(ctx)
    require_staff(ctx)
    if email:
        # Before anything is written, so a refusal leaves nothing behind.
        owner = _org_contact_for_email(db, ctx, normalise_email(email))
        if owner is not None:
            _refuse_taken(db, ctx, owner)
    contact = FnContact(
        organization_id=ctx.org_id,
        name=require_text(name, "A name"),
        phone=(phone or "").strip() or None,
        company=(company or "").strip() or None,
        notes=notes,
        created_by_user_id=ctx.user_id,
    )
    db.add(contact)
    flush(db)
    if email:
        _add_email(db, ctx, contact, email, primary=True, source=source)
    return contact


def find_or_create_contact(
    db: Session,
    ctx: FunctionsCtx,
    *,
    email: str | None,
    name: str | None,
    phone: str | None = None,
    company: str | None = None,
    source: str = "manual",
) -> tuple[FnContact, bool]:
    """``(contact, created)``: the org's contact for ``email`` if there is
    one (filling a missing phone/company, never overwriting), else a new one.
    Matching is by email only — two people sharing ``events@`` is reported by
    the importer, not merged here. The match is org-wide whoever can see it:
    this is how a repeat customer at another venue is attached rather than
    refused or duplicated (see the module note)."""
    require_live(ctx)
    if email:
        existing = _org_contact_for_email(db, ctx, email)
        if existing is not None:
            if phone and not existing.phone:
                existing.phone = phone.strip()
            if company and not existing.company:
                existing.company = company.strip()
            flush(db)
            return existing, False
    fallback = name or (email.split("@")[0] if email else None)
    return (
        create_contact(
            db,
            ctx,
            name=fallback or "",
            email=email,
            phone=phone,
            company=company,
            source=source,
        ),
        True,
    )


def update_contact(
    db: Session,
    ctx: FunctionsCtx,
    contact_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> dict:
    """Edit name / company / phone / notes. Returns ``{field: [old, new]}``."""
    require_live(ctx)
    require_staff(ctx)
    contact = get_contact(db, ctx, contact_id)
    check_version(contact, expected_version)
    if "name" in fields:
        fields["name"] = require_text(fields["name"], "A name")
    changes = apply_fields(contact, fields, CONTACT_FIELDS)
    flush(db)
    return changes


def add_contact_email(
    db: Session,
    ctx: FunctionsCtx,
    contact_id: str,
    email: str,
    *,
    primary: bool = False,
    source: str = "manual",
) -> FnContactEmail:
    require_live(ctx)
    require_staff(ctx)
    contact = get_contact(db, ctx, contact_id)
    return _add_email(db, ctx, contact, email, primary=primary, source=source)


def set_primary_email(
    db: Session, ctx: FunctionsCtx, contact_id: str, email: str
) -> None:
    require_live(ctx)
    require_staff(ctx)
    get_contact(db, ctx, contact_id)
    row = (
        db.query(FnContactEmail)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == contact_id,
            func.lower(FnContactEmail.email) == normalise_email(email),
        )
        .first()
    )
    if row is None:
        raise NotFound("That address is not on this contact.")
    _set_primary(db, ctx, contact_id, row)
    flush(db)


def remove_contact_email(
    db: Session, ctx: FunctionsCtx, contact_id: str, email: str
) -> None:
    require_live(ctx)
    require_staff(ctx)
    get_contact(db, ctx, contact_id)
    row = (
        db.query(FnContactEmail)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == contact_id,
            func.lower(FnContactEmail.email) == normalise_email(email),
        )
        .first()
    )
    if row is None:
        raise NotFound("That address is not on this contact.")
    was_primary = row.is_primary
    db.delete(row)
    flush(db)
    if was_primary:
        nxt = (
            db.query(FnContactEmail)
            .filter(
                FnContactEmail.organization_id == ctx.org_id,
                FnContactEmail.contact_id == contact_id,
            )
            .order_by(FnContactEmail.created_at)
            .first()
        )
        if nxt is not None:
            nxt.is_primary = True
            flush(db)


def archive_contact(db: Session, ctx: FunctionsCtx, contact_id: str) -> FnContact:
    """Hide a contact from lists. Its enquiries, events and money stay."""
    require_live(ctx)
    require_staff(ctx)
    contact = get_contact(db, ctx, contact_id)
    if contact.archived_at is None:
        contact.archived_at = _now()
        flush(db)
    return contact


def unarchive_contact(db: Session, ctx: FunctionsCtx, contact_id: str) -> FnContact:
    require_live(ctx)
    require_staff(ctx)
    contact = get_contact(db, ctx, contact_id)
    if contact.archived_at is not None:
        contact.archived_at = None
        flush(db)
    return contact


def delete_contact(db: Session, ctx: FunctionsCtx, contact_id: str) -> None:
    """Only a contact with no enquiries or events (a typo, a duplicate) can be
    deleted. The database refuses the rest; this says why first."""
    require_live(ctx)
    require_staff(ctx)
    contact = get_contact(db, ctx, contact_id)
    used = (
        db.query(FnEnquiry.id)
        .filter(
            FnEnquiry.organization_id == ctx.org_id, FnEnquiry.contact_id == contact.id
        )
        .first()
        or db.query(FnEvent.id)
        .filter(
            FnEvent.organization_id == ctx.org_id,
            or_(
                FnEvent.contact_id == contact.id,
                FnEvent.invoice_contact_id == contact.id,
            ),
        )
        .first()
    )
    if used:
        raise Conflict(
            "This contact has enquiries or events, so it can't be deleted — archive it instead."
        )
    db.delete(contact)
    flush(db)
