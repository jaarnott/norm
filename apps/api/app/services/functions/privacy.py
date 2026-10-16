"""Privacy requests — anonymising a contact.

A customer can ask to be forgotten (or for their details to be corrected
beyond repair). Their bookings are still the venue's financial history, so the
record is kept and the PERSON is taken out of it:

* **Blanked:** the contact's name, company, phone and notes; every email
  address on the contact; free text on their enquiries and events that is
  about them (subject, notes, client note, allergies, authorised bar-tab
  names, table plan, …); click identifiers and landing pages; the email a
  customer response or portal action was recorded under; their web-form
  submissions' payloads; portal access for their addresses.
* **Kept:** amounts, dates, guest counts, booking types, menus and the facts
  that derive every status — a report for last year still adds up.
* **Purged from the mail mirror:** every mirrored thread that has a message
  from or to one of their addresses (``fn_mailbox_threads`` /
  ``fn_mailbox_messages`` — owned by the mailbox sync, F4; deleting whole
  threads here is the one write this module makes there, agreed for privacy).
  The venue's Gmail itself is NOT touched: Norm mirrors the mailbox, it does
  not own it. Incremental sync only reads new history, so purged mail does not
  come back unless the venue's mailbox is fully resynced.

Anonymising is irreversible and reaches across venues (one contact, many
bookings, several mailboxes), so it is an owner's act.
"""

from __future__ import annotations

from sqlalchemy import Text, cast, false, func, or_
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnContactEmail,
    FnEnquiry,
    FnEvent,
    FnEventChange,
    FnEventSectionConfirmation,
    FnFormSubmission,
    FnMailboxMessage,
    FnMailboxThread,
    FnMinSpend,
    FnMinSpendResponse,
    FnPortalAccess,
    FnPortalSession,
)
from app.db.models import _now
from app.services.functions import contacts as contacts_svc
from app.services.functions.ctx import (
    FunctionsCtx,
    require_live,
    require_owner,
    require_staff,
)
from app.services.functions.errors import flush

ANONYMISED_NAME = "Anonymised contact"
#: Free text on an enquiry that is about the customer.
ENQUIRY_TEXT = (
    "subject",
    "client_note",
    "notes",
    "follow_up_reason",
    "gclid",
    "referrer",
    "landing_page",
)
#: Free text on an event that is about the customer or their guests. The
#: allergies are health data — the most sensitive field Functions holds.
EVENT_TEXT = (
    "description",
    "notes",
    "special_instructions",
    "allergies",
    "bar_tab_authorised_people",
    "bar_tab_restrictions",
    "bar_tab_notes",
    "table_plan",
    "timing_notes",
    "timing_preference",
    "food_notes",
)
#: Diff keys in the event change log whose values would still carry the text.
_LOGGED_PERSONAL = frozenset(EVENT_TEXT) | {"name"}


def anonymise_contact(db: Session, ctx: FunctionsCtx, contact_id: str) -> dict:
    """Take the person out of their records. Repeating it is harmless (the
    mirror purge simply finds nothing). Returns counts of what changed."""
    require_live(ctx)
    require_staff(ctx)
    require_owner(ctx)
    contact = contacts_svc.get_contact(db, ctx, contact_id)
    emails = sorted(
        {
            e.email.strip().lower()
            for e in db.query(FnContactEmail).filter(
                FnContactEmail.organization_id == ctx.org_id,
                FnContactEmail.contact_id == contact.id,
            )
            if e.email
        }
    )

    contact.name = ANONYMISED_NAME
    contact.company = None
    contact.phone = None
    contact.notes = None
    now = _now()
    contact.anonymised_at = contact.anonymised_at or now
    contact.archived_at = contact.archived_at or now
    db.query(FnContactEmail).filter(
        FnContactEmail.organization_id == ctx.org_id,
        FnContactEmail.contact_id == contact.id,
    ).delete(synchronize_session="fetch")

    enquiries = (
        db.query(FnEnquiry)
        .filter(
            FnEnquiry.organization_id == ctx.org_id, FnEnquiry.contact_id == contact.id
        )
        .all()
    )
    for e in enquiries:
        for name in ENQUIRY_TEXT:
            setattr(e, name, None)
    enquiry_ids = [e.id for e in enquiries]

    events = (
        db.query(FnEvent)
        .filter(
            FnEvent.organization_id == ctx.org_id,
            or_(
                FnEvent.contact_id == contact.id,
                FnEvent.enquiry_id.in_(enquiry_ids) if enquiry_ids else false(),
            ),
        )
        .all()
    )
    for ev in events:
        for name in EVENT_TEXT:
            setattr(ev, name, None)
        # The event name usually IS the person ("Casey's 40th").
        ev.name = "Anonymised booking"
    event_ids = [ev.id for ev in events]

    if event_ids:
        _scrub_event_logs(db, ctx, event_ids)
        portal = _purge_portal(db, ctx, event_ids)
    else:
        portal = 0

    option_ids = [
        r[0]
        for r in db.query(FnMinSpend.id).filter(
            FnMinSpend.organization_id == ctx.org_id,
            or_(
                FnMinSpend.enquiry_id.in_(enquiry_ids) if enquiry_ids else false(),
                FnMinSpend.event_id.in_(event_ids) if event_ids else false(),
            ),
        )
    ]
    if option_ids:
        db.query(FnMinSpendResponse).filter(
            FnMinSpendResponse.organization_id == ctx.org_id,
            FnMinSpendResponse.min_spend_id.in_(option_ids),
        ).update(
            {FnMinSpendResponse.customer_email: None, FnMinSpendResponse.note: None},
            synchronize_session="fetch",
        )

    submissions = 0
    if enquiry_ids:
        submissions = (
            db.query(FnFormSubmission)
            .filter(
                FnFormSubmission.organization_id == ctx.org_id,
                FnFormSubmission.enquiry_id.in_(enquiry_ids),
            )
            .update(
                {
                    FnFormSubmission.payload: {},
                    FnFormSubmission.ip_hash: None,
                    FnFormSubmission.user_agent: None,
                    FnFormSubmission.referrer: None,
                    FnFormSubmission.landing_page: None,
                    FnFormSubmission.gclid: None,
                    FnFormSubmission.gbraid: None,
                    FnFormSubmission.wbraid: None,
                },
                synchronize_session="fetch",
            )
        )

    threads, messages = purge_mail_mirror(db, ctx, emails)
    flush(db)
    return {
        "contact_id": contact.id,
        "emails_removed": len(emails),
        "enquiries_scrubbed": len(enquiries),
        "events_scrubbed": len(events),
        "form_submissions_scrubbed": submissions,
        "portal_access_removed": portal,
        "mail_threads_purged": threads,
        "mail_messages_purged": messages,
    }


def _scrub_event_logs(db: Session, ctx: FunctionsCtx, event_ids: list[str]) -> None:
    """The change log keeps that a field changed and when — not what it said
    — for the personal fields, and forgets the client's address."""
    for row in db.query(FnEventChange).filter(
        FnEventChange.organization_id == ctx.org_id,
        FnEventChange.event_id.in_(event_ids),
    ):
        row.client_email = None
        row.note = None
        changes = row.changes
        if isinstance(changes, dict):
            cleaned = {}
            for key, diff in changes.items():
                personal = key in _LOGGED_PERSONAL or key.startswith("contact.")
                cleaned[key] = [None, None] if personal else diff
            if cleaned != changes:
                row.changes = cleaned
    db.query(FnEventSectionConfirmation).filter(
        FnEventSectionConfirmation.organization_id == ctx.org_id,
        FnEventSectionConfirmation.event_id.in_(event_ids),
    ).update(
        {
            FnEventSectionConfirmation.client_email: None,
            FnEventSectionConfirmation.note: None,
        },
        synchronize_session="fetch",
    )


def _purge_portal(db: Session, ctx: FunctionsCtx, event_ids: list[str]) -> int:
    """Nobody keeps a way into these bookings once the person is gone."""
    db.query(FnPortalSession).filter(
        FnPortalSession.organization_id == ctx.org_id,
        FnPortalSession.event_id.in_(event_ids),
    ).delete(synchronize_session="fetch")
    return (
        db.query(FnPortalAccess)
        .filter(
            FnPortalAccess.organization_id == ctx.org_id,
            FnPortalAccess.event_id.in_(event_ids),
        )
        .delete(synchronize_session="fetch")
    )


def purge_mail_mirror(
    db: Session, ctx: FunctionsCtx, emails: list[str]
) -> tuple[int, int]:
    """Delete every mirrored thread with a message from or to one of
    ``emails`` (whole threads: the subject and snippet of a conversation with
    the customer are about them too). Returns ``(threads, messages)``.

    Candidates are found in SQL (sender match, or the address inside the
    recipients' JSON text) and confirmed in Python against the parsed lists,
    so ``ann@example.com`` never purges ``joann@example.com``'s mail.
    """
    wanted = {e.strip().lower() for e in emails if e and e.strip()}
    if not wanted:
        return 0, 0
    m = FnMailboxMessage
    text_match = [
        func.lower(cast(col, Text)).contains(addr)
        for addr in wanted
        for col in (m.to_emails, m.cc_emails)
    ]
    candidates = (
        db.query(m)
        .filter(
            m.organization_id == ctx.org_id,
            or_(
                func.lower(func.coalesce(m.from_email, "")).in_(wanted),
                func.lower(func.coalesce(m.reply_to_email, "")).in_(wanted),
                *text_match,
            ),
        )
        .all()
    )
    thread_ids = {
        msg.thread_id
        for msg in candidates
        if _addresses(msg.from_email, msg.reply_to_email, msg.to_emails, msg.cc_emails)
        & wanted
    }
    if not thread_ids:
        return 0, 0
    messages = (
        db.query(m)
        .filter(m.organization_id == ctx.org_id, m.thread_id.in_(thread_ids))
        .delete(synchronize_session="fetch")
    )
    threads = (
        db.query(FnMailboxThread)
        .filter(
            FnMailboxThread.organization_id == ctx.org_id,
            FnMailboxThread.id.in_(thread_ids),
        )
        .delete(synchronize_session="fetch")
    )
    return threads, messages


def _addresses(*values) -> set[str]:
    """Bare lowercased addresses from mirror fields — a string, or a JSON list
    of strings / ``{email, name}`` objects, possibly ``"Name <addr>"``."""
    out: set[str] = set()
    for value in values:
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                item = item.get("email") or item.get("address")
            if not isinstance(item, str):
                continue
            text = item.strip()
            if "<" in text and ">" in text:
                text = text[text.rfind("<") + 1 : text.rfind(">")]
            if "@" in text:
                out.add(text.strip().lower())
    return out
