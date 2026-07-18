"""Token minting, hashing, verification, and rotation.

Pure-ish functions over the OAuth models. The security decisions live here:

- **Opaque, not JWT.** Tokens are random; the DB row is the source of truth.
  A JWT signed with JWT_SECRET would authenticate against every /api route via
  decode_access_token's missing type check. An opaque token can't be decoded at
  all, so that class of confusion is structurally impossible.
- **SHA-256 for tokens, bcrypt for client secrets.** See the module docstring
  in db/mcp_models.py — tokens are 256-bit random and need an indexable hash on
  a hot path; client secrets are low-frequency and low-entropy.
- **The type discriminator is enforced three ways** (prefix, query filter,
  explicit assert). This codebase has shipped the "wrong token type accepted"
  bug twice; belt and braces is warranted.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from passlib.hash import bcrypt
from sqlalchemy.orm import Session

from app.db.mcp_models import McpToken

ACCESS_TOKEN_PREFIX = "norm_mcp_at_"
REFRESH_TOKEN_PREFIX = "norm_mcp_rt_"

ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=60)
AUTH_CODE_TTL = timedelta(seconds=60)

_TOKEN_BYTES = 32  # 256 bits of CSPRNG


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Hashing ──────────────────────────────────────────────────────────


def hash_token(token: str) -> str:
    """SHA-256 hex of a token or code.

    Indexable (fixed width, B-tree) and sub-millisecond, which matters because
    a bearer token is the only lookup key we have. A single pass is
    cryptographically sufficient for a 256-bit random value — a KDF would
    defend against a dictionary attack that cannot exist here.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def hash_client_secret(secret: str) -> str:
    """bcrypt for client secrets — verified rarely (once/hour at /token), and
    DCR clients may treat them loosely, so the cost is appropriate here."""
    return bcrypt.hash(secret)


def verify_client_secret(secret: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(secret, hashed)
    except (ValueError, TypeError):
        return False


# ── Generation ───────────────────────────────────────────────────────


def _random() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def new_access_token() -> str:
    return ACCESS_TOKEN_PREFIX + _random()


def new_refresh_token() -> str:
    return REFRESH_TOKEN_PREFIX + _random()


def new_authorization_code() -> str:
    return _random()


def is_access_token(token: str) -> bool:
    return token.startswith(ACCESS_TOKEN_PREFIX)


def is_refresh_token(token: str) -> bool:
    return token.startswith(REFRESH_TOKEN_PREFIX)


# ── PKCE ─────────────────────────────────────────────────────────────


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """S256 only. base64url(sha256(verifier)) == challenge, constant-time.

    plain is not supported — OAuth 2.1 §4.1.1 requires S256, and accepting
    plain would let a network attacker who sees the challenge forge the verify.
    """
    import base64

    digest = hashlib.sha256(code_verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(expected, code_challenge)


# ── Minting ──────────────────────────────────────────────────────────


def mint_token_pair(
    db: Session,
    *,
    grant,
    client_id: str,
    audience: str | None,
    parent_token_id: str | None = None,
) -> tuple[str, str]:
    """Create an access+refresh pair from a grant. Returns the plaintext pair.

    org/venue/scope are copied from the grant and frozen on the tokens.
    """
    access = new_access_token()
    refresh = new_refresh_token()
    now = _now()

    access_row = McpToken(
        token_hash=hash_token(access),
        kind="access",
        grant_id=grant.id,
        client_id=client_id,
        user_id=grant.user_id,
        organization_id=grant.organization_id,
        venue_ids=list(grant.venue_ids or []),
        scopes=list(grant.scopes or []),
        audience=audience,
        expires_at=now + ACCESS_TOKEN_TTL,
        parent_token_id=parent_token_id,
    )
    refresh_row = McpToken(
        token_hash=hash_token(refresh),
        kind="refresh",
        grant_id=grant.id,
        client_id=client_id,
        user_id=grant.user_id,
        organization_id=grant.organization_id,
        venue_ids=list(grant.venue_ids or []),
        scopes=list(grant.scopes or []),
        audience=audience,
        expires_at=now + REFRESH_TOKEN_TTL,
        parent_token_id=parent_token_id,
    )
    db.add(access_row)
    db.add(refresh_row)
    db.flush()
    return access, refresh


# ── Access-token resolution ──────────────────────────────────────────


def resolve_access_token(db: Session, token: str) -> McpToken | None:
    """Look up a *valid, unexpired, unrevoked access* token, or None.

    The type discriminator is enforced three times over:
      1. Wire prefix — a refresh token presented as a bearer is rejected here,
         before any DB hit.
      2. Query filter — kind == "access".
      3. Explicit assert below — kept even though (2) covers it, because a
         refresh token's hash IS in the same table, and a future refactor that
         drops the filter must not silently start accepting them. This is the
         exact bug (invite/reset tokens accepted as access tokens) that this
         codebase has shipped before.
    """
    if not is_access_token(token):
        return None

    row = (
        db.query(McpToken)
        .filter(
            McpToken.token_hash == hash_token(token),
            McpToken.kind == "access",
        )
        .first()
    )
    if row is None:
        return None

    assert row.kind == "access", "non-access token resolved as access"

    if row.revoked_at is not None:
        return None
    if row.expires_at <= _now():
        return None

    return row


def resolve_refresh_token(db: Session, token: str) -> McpToken | None:
    """Look up a refresh token by hash, regardless of state.

    Returns even revoked/rotated rows so the caller can detect reuse — a
    presented refresh token whose row is already rotated means the token
    leaked, and the whole family must be revoked (RFC 9700 §4.14.2).
    """
    if not is_refresh_token(token):
        return None
    return (
        db.query(McpToken)
        .filter(
            McpToken.token_hash == hash_token(token),
            McpToken.kind == "refresh",
        )
        .first()
    )


# ── Revocation ───────────────────────────────────────────────────────


def revoke_grant_tokens(db: Session, grant_id: str) -> int:
    """Revoke every token on a grant. Returns the count revoked."""
    now = _now()
    n = (
        db.query(McpToken)
        .filter(McpToken.grant_id == grant_id, McpToken.revoked_at.is_(None))
        .update({McpToken.revoked_at: now}, synchronize_session=False)
    )
    return n
