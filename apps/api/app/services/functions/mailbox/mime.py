"""Reading Gmail's message resources and writing RFC 822 messages.

Parsing uses the standard library's address parser (Orbit split address
lists on commas by hand and mis-read quoted names containing commas);
building uses ``email.message.EmailMessage`` so non-ASCII names and subjects
are encoded properly and no header can carry a line break.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, getaddresses, parseaddr

from app.services.functions import addresses
from app.services.functions.errors import Invalid
from app.services.functions.mailbox.display import html_to_text

#: Recipients and senders are judged by the one strict Functions rule
#: (services/functions/addresses.py), the same one contacts are stored by.
MAX_EMAIL = addresses.MAX_LENGTH

#: The headers the mirror asks Gmail for (``format=metadata``): enough for
#: the three message flags, threading, and the list view — never the body.
METADATA_HEADERS = (
    "From",
    "To",
    "Cc",
    "Reply-To",
    "Subject",
    "Date",
    "Message-ID",
    "In-Reply-To",
    "References",
    "Auto-Submitted",
    "Precedence",
    "X-Autoreply",
    "X-Autorespond",
    "X-Auto-Response-Suppress",
)


#: Address headers are cut to this before parsing (a To/Cc of a couple of
#: hundred people fits); the standard library's parsers slow down on a
#: crafted header of 100 KB.
MAX_HEADER = 8_000
#: More encoded words than any real header carries means it is left encoded.
MAX_ENCODED_WORDS = 50


def _decoded(value: str | None) -> str:
    value = (value or "")[:MAX_HEADER]
    if "=?" in value and value.count("=?") <= MAX_ENCODED_WORDS:
        try:
            return str(make_header(decode_header(value)))
        except Exception:  # noqa: BLE001 — a malformed encoded-word stays as sent
            return value
    return value


def headers_map(payload: dict | None) -> dict[str, str]:
    """``{lower-case name: value}`` — the first value of each header."""
    out: dict[str, str] = {}
    for h in (payload or {}).get("headers") or []:
        name = str(h.get("name") or "").lower()
        if name and name not in out:
            out[name] = str(h.get("value") or "")
    return out


def parse_address(raw: str | None) -> tuple[str, str]:
    """``(display name, lower-case address)`` — empty strings when absent."""
    name, addr = parseaddr(_decoded(raw))
    return name.strip().strip('"'), addr.strip().lower()


def parse_address_list(raw: str | None) -> list[tuple[str, str]]:
    pairs = getaddresses([_decoded(raw)]) if raw else []
    return [(n.strip().strip('"'), a.strip().lower()) for n, a in pairs if a.strip()]


def is_email(value: str | None) -> bool:
    """``addresses.is_email`` — recipient checks and stored addresses can't
    disagree. (Display names are parsed off above, never judged here.)"""
    return addresses.is_email(value)


def clean_addresses(values, what: str = "Recipient") -> list[str]:
    """Lower-cased, de-duplicated addresses; anything that isn't one (or
    carries a line break — header injection) is refused, not dropped."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in re.split(r"[,;]", values)]
    out: list[str] = []
    for v in values:
        raw = str(v or "").strip()
        if not raw:
            continue
        if "\n" in raw or "\r" in raw:
            raise Invalid(f"{what} '{raw[:60]}' is not an email address.")
        _, addr = parse_address(raw) if "<" in raw else ("", raw.lower())
        if not is_email(addr):
            raise Invalid(f"{what} '{raw[:60]}' is not an email address.")
        if addr not in out:
            out.append(addr)
    return out


def one_line(value: str | None) -> str:
    """A header value with no line breaks (subjects, names)."""
    return " ".join(str(value or "").split())


def b64url_decode(data: str | None) -> bytes:
    if not data:
        return b""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, TypeError):
        return b""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def internal_date(resource: dict) -> dt.datetime:
    try:
        ms = int(resource.get("internalDate") or 0)
    except (TypeError, ValueError):
        ms = 0
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)


def _charset(part: dict) -> str:
    content_type = headers_map(part).get("content-type", "")
    m = re.search(r'charset="?([\w\-.:]+)"?', content_type, re.I)
    return m.group(1) if m else "utf-8"


def _text(part: dict) -> str:
    data = b64url_decode(((part.get("body") or {}).get("data")))
    try:
        return data.decode(_charset(part), errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


@dataclass
class Bodies:
    text: str = ""
    html: str = ""
    attachments: list[dict] = field(default_factory=list)


def bodies(payload: dict | None) -> Bodies:
    """The first text/plain and text/html parts, and the attachments, of a
    ``format=full`` payload (walked depth-first, as mail clients do)."""
    out = Bodies()

    def walk(part: dict | None) -> None:
        if not part:
            return
        mime = str(part.get("mimeType") or "").lower()
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        if filename or body.get("attachmentId"):
            if filename:
                out.attachments.append(
                    {
                        "filename": filename,
                        "mime_type": mime,
                        "size": body.get("size") or 0,
                        "attachment_id": body.get("attachmentId"),
                        # Stable where Gmail's attachment ids are not: the
                        # download accepts either.
                        "part_id": part.get("partId"),
                    }
                )
        elif mime == "text/plain" and not out.text and body.get("data"):
            out.text = _text(part)
        elif mime == "text/html" and not out.html and body.get("data"):
            out.html = _text(part)
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return out


def find_attachment(payload: dict | None, ref: str) -> dict | None:
    """The part of a ``format=full`` payload that is the attachment ``ref``
    names — by Gmail attachment id, else by part id (attachment ids can
    change between fetches of the same message; part ids don't)."""
    found: dict | None = None

    def walk(part: dict | None) -> None:
        nonlocal found
        if not part or found is not None:
            return
        body = part.get("body") or {}
        if body.get("attachmentId") and ref in (
            body["attachmentId"],
            part.get("partId"),
        ):
            found = part
            return
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return found


def new_message_id(mailbox_email: str) -> str:
    """Our own RFC 822 Message-ID — the outbox finds a lost send by it
    (``rfc822msgid:``), so it must be unique and recorded before sending."""
    domain = (mailbox_email or "").rsplit("@", 1)[-1] or "norm.local"
    return f"<norm.{uuid.uuid4().hex}@{domain}>"


def bare_message_id(value: str | None) -> str:
    return (value or "").strip().strip("<>").strip()


def reply_subject(subject: str | None) -> str:
    s = one_line(subject)
    return s if re.match(r"^\s*re\s*:", s, re.I) else f"Re: {s}".strip()


def forward_subject(subject: str | None) -> str:
    s = one_line(re.sub(r"^\s*re\s*:\s*", "", subject or "", flags=re.I))
    return s if re.match(r"^\s*(fwd?|fw)\s*:", s, re.I) else f"Fwd: {s}".strip()


def _address(email: str, name: str | None = None) -> Address:
    local, _, domain = email.partition("@")
    return Address(display_name=one_line(name), username=local, domain=domain)


def build_message(
    *,
    from_email: str,
    from_name: str | None,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str,
    html: str,
    message_id: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    date: dt.datetime | None = None,
) -> bytes:
    """An RFC 822 message (multipart/alternative: text + HTML), CRLF line
    endings, ready for Gmail's ``raw``. Bcc is a header Gmail strips before
    delivery."""
    msg = EmailMessage(policy=SMTP)
    msg["From"] = _address(from_email, from_name)
    if to:
        msg["To"] = [_address(a) for a in to]
    if cc:
        msg["Cc"] = [_address(a) for a in cc]
    if bcc:
        msg["Bcc"] = [_address(a) for a in bcc]
    msg["Subject"] = one_line(subject)
    msg["Date"] = format_datetime(date or dt.datetime.now(dt.timezone.utc))
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = one_line(in_reply_to)
    if references:
        msg["References"] = one_line(references)
    msg.set_content(html_to_text(html) or " ")
    msg.add_alternative(html or "<div></div>", subtype="html")
    return msg.as_bytes()
