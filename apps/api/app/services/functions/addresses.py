"""Email addresses — one strict check for everything Functions stores or sends to.

Addresses typed by staff, submitted on public web forms or entered in the
portal end up as Resend ``to`` / ``reply_to`` values and Gmail recipients, so
"something@something.something" is not enough: commas, angle brackets and
quotes in an address are how a value becomes two recipients or a header.
This accepts the address forms real customers use and nothing else:

* local part — a dot-atom (RFC 5322 atext, single dots between runs), at
  most 64 characters; no quoted strings, no comments;
* domain — two or more labels of letters, digits and inner hyphens (at most
  63 each), the last not all digits; no IP literals, no Unicode (punycode
  is fine);
* 254 characters in all, checked BEFORE any pattern runs (security review:
  the earlier pattern backtracked quadratically on hostile input).

Both patterns are linear: atext and label characters exclude ".", so there
is only one way to split the input.

Shared by contacts.normalise_email (every service path) and the mailbox's
recipient checks (``mailbox/mime.py``), so the two can never disagree.
"""

from __future__ import annotations

import re

MAX_LENGTH = 254
MAX_LOCAL_LENGTH = 64

_ATEXT = r"[a-z0-9!#$%&'*+/=?^_`{|}~-]"
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_ADDRESS = re.compile(rf"({_ATEXT}+(?:\.{_ATEXT}+)*)@({_LABEL}(?:\.{_LABEL})+)")


def normalise(value: str | None) -> str | None:
    """The address trimmed and lower-cased if it is acceptable, else None."""
    candidate = (value or "").strip().lower()
    if not candidate or len(candidate) > MAX_LENGTH:
        return None
    match = _ADDRESS.fullmatch(candidate)
    if match is None or len(match.group(1)) > MAX_LOCAL_LENGTH:
        return None
    if match.group(2).rsplit(".", 1)[-1].isdigit():
        return None
    return candidate


def is_email(value: str | None) -> bool:
    return normalise(value) is not None
