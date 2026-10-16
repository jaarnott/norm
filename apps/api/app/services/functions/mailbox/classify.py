"""The three flags on every mirrored message — the one place they are decided.

``status.py`` derives an enquiry's "replied" stage and its next action from
these rows: an outbound message that is neither internal nor an auto-reply,
on a thread linked to the enquiry, is a reply; the matching inbound message
is the customer writing. Getting a flag wrong is silent — an enquiry sits at
"new" with a reply already sent, or nags for a reply nobody owes — so:

* **is_inbound** — the message did not come from us. "Us" is the mailbox
  itself, Gmail's ``SENT`` label (covers send-as aliases), any of the
  organisation's other mailboxes, and anyone at the organisation's own
  domains: a manager answering the customer from their own address with the
  venue in copy has replied, even though Gmail delivered it to the venue.
  One exception, kept from Orbit: the mailbox writing to itself and nobody
  outside — that is a web-form notification standing in for the customer,
  so it is inbound.
* **is_internal** — between our own people: from us, and every visible
  recipient is us too (a forward to a manager, a colleague's question).
  Neither a customer message nor a reply to one.
* **is_auto_reply** — machine-made either way: ``Auto-Submitted`` (anything
  but ``no``), ``Precedence: bulk/junk/list/auto_reply``, the ``X-Autoreply``
  family, bounces, and the subjects autoresponders use. Orbit's rule, so
  replied/quoted parity against Orbit can be checked like for like. Senders
  like ``noreply@`` are deliberately NOT auto-replies: web-form
  notifications come from them and are the enquiry itself.

"The organisation's own domains" are the domains of its venue mailboxes and
its members' addresses, minus the public mail providers (a member on
gmail.com must not make every gmail.com customer "internal").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

#: Free and ISP mail domains — never "ours", whoever in the org uses one.
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "hotmail.co.nz",
        "hotmail.co.uk",
        "live.com",
        "live.co.nz",
        "msn.com",
        "yahoo.com",
        "yahoo.co.nz",
        "yahoo.com.au",
        "yahoo.co.uk",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "zoho.com",
        "fastmail.com",
        "hey.com",
        # New Zealand and Australian ISPs
        "xtra.co.nz",
        "slingshot.co.nz",
        "orcon.net.nz",
        "vodafone.co.nz",
        "vodafone.net.nz",
        "clear.net.nz",
        "paradise.net.nz",
        "ihug.co.nz",
        "actrix.co.nz",
        "maxnet.co.nz",
        "snap.net.nz",
        "inspire.net.nz",
        "kinect.co.nz",
        "farmside.co.nz",
        "bigpond.com",
        "bigpond.net.au",
        "optusnet.com.au",
        "iinet.net.au",
        "tpg.com.au",
    }
)

_AUTO_SUBJECT = (
    re.compile(r"^\s*automatic reply\s*:", re.I),
    re.compile(r"^\s*auto(matic)?[- ]?response\s*:", re.I),
    re.compile(r"^\s*out of (the )?office", re.I),
    re.compile(r"^\s*undeliverable\s*:", re.I),
    re.compile(r"^\s*delivery status notification", re.I),
    re.compile(r"^\s*(re:\s*)?read\s*:", re.I),
)
_BOUNCE_SENDER = re.compile(r"^(mailer-daemon|postmaster)@", re.I)


@dataclass(frozen=True)
class OurAddresses:
    mailboxes: frozenset[str]
    domains: frozenset[str]

    def is_ours(self, email: str | None) -> bool:
        address = (email or "").strip().lower()
        if not address:
            return False
        if address in self.mailboxes:
            return True
        domain = address.rsplit("@", 1)[-1]
        return any(domain == d or domain.endswith("." + d) for d in self.domains)


def our_addresses(db: Session, org_id: str) -> OurAddresses:
    """The organisation's mailboxes and domains (computed per sync run)."""
    from app.db.functions_models import FnMailboxAccount
    from app.db.models import OrganizationMembership, User

    mailboxes = {
        (r[0] or "").strip().lower()
        for r in db.query(FnMailboxAccount.google_email).filter(
            FnMailboxAccount.organization_id == org_id
        )
    } - {""}
    members = {
        (r[0] or "").strip().lower()
        for r in db.query(User.email)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .filter(OrganizationMembership.organization_id == org_id)
    } - {""}
    domains = {a.rsplit("@", 1)[-1] for a in mailboxes | members if "@" in a}
    return OurAddresses(
        mailboxes=frozenset(mailboxes),
        domains=frozenset(d for d in domains if d and d not in PUBLIC_EMAIL_DOMAINS),
    )


def is_auto_reply(
    headers: dict[str, str], subject: str | None, from_email: str
) -> bool:
    """``headers``: lower-case names (``mime.headers_map``)."""
    auto = (headers.get("auto-submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if (headers.get("precedence") or "").strip().lower() in {
        "bulk",
        "auto_reply",
        "junk",
        "list",
    }:
        return True
    for name in ("x-autoreply", "x-autorespond", "x-auto-response-suppress"):
        if (headers.get(name) or "").strip():
            return True
    if _BOUNCE_SENDER.match(from_email or ""):
        return True
    return any(p.search(subject or "") for p in _AUTO_SUBJECT)


def message_flags(
    *,
    mailbox_email: str,
    from_email: str,
    to_emails: list[str],
    cc_emails: list[str],
    label_ids: list[str],
    headers: dict[str, str],
    subject: str | None,
    ours: OurAddresses,
) -> tuple[bool, bool, bool]:
    """``(is_inbound, is_internal, is_auto_reply)`` for one message."""
    mailbox = (mailbox_email or "").strip().lower()
    sender = (from_email or "").strip().lower()
    recipients = [r for r in (to_emails or []) + (cc_emails or []) if r]
    from_mailbox = sender == mailbox or "SENT" in (label_ids or [])
    from_us = from_mailbox or ours.is_ours(sender)
    outsiders = [r for r in recipients if not ours.is_ours(r) and r != mailbox]

    if from_mailbox and mailbox in recipients and not outsiders:
        # The mailbox writing to itself: a form notification, i.e. the
        # customer's enquiry arriving.
        inbound, internal = True, False
    else:
        inbound = not from_us
        internal = from_us and bool(recipients) and not outsiders
    return inbound, internal, is_auto_reply(headers, subject, sender)
