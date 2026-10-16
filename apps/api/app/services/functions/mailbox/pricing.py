"""The pricing warning: an email that quotes a price before any proposal has
gone out for its enquiry.

Prices belong in a proposal — that is what freezes the options offered and
marks the enquiry "quoted" (and lets the customer confirm). But a text
heuristic is not a reason to block a person, so this is a WARNING the sender
can override with a reason; the override is recorded with the send (and
logged), never silent.

Applied to what the person wrote — the quoted history Norm appends is not
part of the check.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.functions_models import FnProposalEvent, FnThreadLink
from app.services.functions.ctx import FunctionsCtx
from app.services.functions.errors import Conflict
from app.services.functions.mailbox.display import html_to_text

#: A money figure: a currency sign or code before digits, or digits with a
#: per-head / plus-GST marker (Orbit's MONEY_PATTERNS, kept).
_MONEY = (
    re.compile(r"(?:\$|nzd\s*|aud\s*|usd\s*)\s?\d[\d,]*(?:\.\d{1,2})?", re.I),
    re.compile(
        r"\b\d{2,5}(?:\.\d{1,2})?\s?(?:\+\s?gst|per\s?head|per\s?person|pp)\b", re.I
    ),
    re.compile(r"\bminimum spend\b[^\n]{0,40}\d", re.I),
)


class PricingWarning(Conflict):
    """Answered with 409 ``pricing_warning``: resend with a reason to go
    ahead."""

    code = "pricing_warning"


def contains_price(text: str | None) -> bool:
    value = (text or "")[:200_000].strip()
    return bool(value) and any(p.search(value) for p in _MONEY)


def quote_sent(db: Session, org_id: str, enquiry_id: str) -> bool:
    return (
        db.query(FnProposalEvent.id)
        .filter(
            FnProposalEvent.organization_id == org_id,
            FnProposalEvent.enquiry_id == enquiry_id,
            FnProposalEvent.kind == "sent",
        )
        .first()
        is not None
    )


def linked_enquiry(
    db: Session, ctx: FunctionsCtx, account_id: str | None, gmail_thread_id: str | None
) -> str | None:
    """The enquiry a thread is linked to (the newest live link), if any."""
    if not (account_id and gmail_thread_id):
        return None
    row = (
        db.query(FnThreadLink.enquiry_id)
        .filter(
            FnThreadLink.organization_id == ctx.org_id,
            FnThreadLink.account_id == account_id,
            FnThreadLink.gmail_thread_id == gmail_thread_id,
            FnThreadLink.enquiry_id.isnot(None),
            FnThreadLink.unlinked_at.is_(None),
        )
        .order_by(FnThreadLink.linked_at.desc())
        .first()
    )
    return row[0] if row else None


def check(
    db: Session,
    ctx: FunctionsCtx,
    enquiry_id: str | None,
    body_html: str | None,
    override_reason: str | None,
) -> str | None:
    """Raise ``PricingWarning`` when the body quotes a price on an enquiry
    with no proposal sent and no override reason was given. Returns the
    reason to record with the send when an override was actually needed
    (None otherwise)."""
    if not enquiry_id or not contains_price(html_to_text(body_html)):
        return None
    if quote_sent(db, ctx.org_id, enquiry_id):
        return None
    reason = (override_reason or "").strip()
    if reason:
        return reason[:500]
    raise PricingWarning(
        "This email quotes a price, but no proposal has been sent for this "
        "enquiry. Send the price as a proposal (so the enquiry is marked "
        "quoted and the customer can confirm it), or give a reason to send "
        "this email anyway."
    )
