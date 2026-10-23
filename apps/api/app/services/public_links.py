"""Unguessable, re-derivable public links — the derivation every module shares.

Norm Functions' quote and portal links (``functions/public_tokens.py``) and the
entertainers' portal, onboarding and band-member links (``entertainers/
tokens.py``) are the same idea: a token is ``HMAC-SHA256(key, "<kind>:<row
id>")`` — 256 bits, base64url, 43 characters — where the row id is a uuid4 from
the OS CSPRNG and the key is derived from the server's ``JWT_SECRET`` for one
*purpose*. Only the token's SHA-256 is stored, so a copy of the database yields
no working link; and because a token can be re-derived from its row, minting
again returns the SAME link (staff can copy it twice, a re-send doesn't orphan
the first email).

Only the derivation lives here (Oct 2026): Functions keeps its own mint,
expiry and ctx code, which is bound to its enquiries, events and portal
sessions; the entertainers keep theirs. ``resolve`` is the one generic reader —
unknown, revoked, expired and wrong-kind all look the same, None, so a public
route answers every one of them with the same 404.

Purposes are separate keys: a Functions quote token can never open an
entertainer portal, whatever row ids happen to collide. ``"functions"`` keeps
the exact key Functions has always used, so no link in a customer's inbox
changes hands.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import _now

#: What a token looks like in a URL (``tokens_in_text`` in Functions).
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def key(purpose: str = "functions") -> bytes:
    """The HMAC key for one purpose, derived from ``JWT_SECRET`` at call time:
    the startup hook may replace the secret with the config DB's value after
    this module is imported."""
    return hmac.new(
        settings.JWT_SECRET.encode(),
        f"norm-{purpose}/public-token/v1".encode(),
        "sha256",
    ).digest()


def hash_token(token: str) -> str:
    """What a ``token_hash`` column stores (SHA-256 hex)."""
    return hashlib.sha256(token.encode()).hexdigest()


def derive(kind: str, row_id: str, *, purpose: str = "functions") -> str:
    """The token for ``kind:row_id`` under ``purpose``'s key."""
    digest = hmac.new(key(purpose), f"{kind}:{row_id}".encode(), "sha256").digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def link_url(path: str) -> str:
    """``{app_url}/<path>`` — the public page (``q/<token>``, ``p/<token>``)."""
    return f"{settings.app_url.rstrip('/')}/{path.lstrip('/')}"


def matches(token: str, row_id: str, kind: str, *, purpose: str = "functions") -> bool:
    """Was ``token`` derived from this row under the current key? (False once
    the secret has rotated — the row still resolves by hash until it expires,
    but can't be shown again.)"""
    return hmac.compare_digest(
        hash_token(derive(kind, row_id, purpose=purpose)), hash_token(token)
    )


def resolve(
    db: Session,
    model,
    token: str | None,
    *,
    kind: str | None = None,
    now: dt.datetime | None = None,
):
    """The live row of ``model`` for ``token``, or None.

    ``model`` is any table with ``token_hash``, ``revoked_at`` and
    ``expires_at`` columns (``fn_public_tokens``, ``ent_public_tokens``);
    ``kind`` narrows to one of its ``kind`` values when given. Fails closed:
    a malformed token, an unknown hash, a revoked or expired row and the wrong
    kind are all None, so the caller can't tell them apart and neither can
    anyone probing the route.
    """
    if not token or not TOKEN_RE.fullmatch(token):
        return None
    q = db.query(model).filter(model.token_hash == hash_token(token))
    if kind is not None:
        q = q.filter(model.kind == kind)
    row = q.first()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= (now or _now()):
        return None
    return row
