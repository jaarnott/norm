"""Resolve an MCP bearer token to a principal.

Phase 1 ships a **dev token** so the tool surface can be exercised before the
OAuth authorization server exists. Phase 2 replaces `resolve_principal` with
the real opaque-token lookup; everything above this seam is unchanged by that.

The dev path is fenced three ways, because a development shortcut that reaches
a deployed environment is exactly how this goes wrong:

1. It requires `MCP_ENABLED`, which is False by default.
2. It requires `MCP_DEV_TOKEN` to be explicitly set — no default value.
3. It refuses to run outside `ENVIRONMENT=local`, unconditionally.
"""

from __future__ import annotations

import hmac
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.mcp.principal import McpPrincipal
from app.mcp.scopes import scopes_grantable_by

logger = logging.getLogger(__name__)


class McpAuthError(Exception):
    """401. Carries the RFC 9728 challenge the client needs to re-auth."""

    def __init__(self, message: str, error: str = "invalid_token") -> None:
        self.message = message
        self.error = error
        super().__init__(message)

    def www_authenticate(self) -> str:
        """The header claude.ai uses to discover the authorization server.

        Without `resource_metadata`, a client cannot find the AS and the
        connector simply cannot be added — so this is load-bearing, not
        decoration.
        """
        return (
            f'Bearer realm="norm", error="{self.error}", '
            f'error_description="{self.message}", '
            f'resource_metadata="{settings.mcp_issuer}'
            f'/.well-known/oauth-protected-resource/mcp"'
        )


def extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def resolve_principal(token: str | None, db: Session) -> McpPrincipal:
    """Resolve a bearer token to a principal, or raise McpAuthError."""
    if not token:
        raise McpAuthError("No access token provided")

    # Dev shortcut (local only). Returns None when it doesn't apply.
    principal = _resolve_dev_principal(token, db)
    if principal is not None:
        return principal

    return _resolve_oauth_principal(token, db)


def _resolve_oauth_principal(token: str, db: Session) -> McpPrincipal:
    """Resolve a real opaque access token against mcp_tokens.

    The token row freezes org/venue/scope at issuance. We re-read the grant to
    confirm it hasn't been revoked, but do NOT re-derive scopes here — that
    happens at refresh. Revoking a grant kills its tokens immediately (opaque
    tokens + a revoked_at check), so a revoked grant never reaches this point
    with a live token; the grant re-read is belt-and-braces for the window
    between grant.revoked_at being set and the token rows being updated.
    """
    from app.db.mcp_models import McpClient, McpGrant
    from app.mcp.tokens import resolve_access_token

    row = resolve_access_token(db, token)
    if row is None:
        raise McpAuthError("Invalid or expired access token")

    grant = db.query(McpGrant).filter(McpGrant.id == row.grant_id).first()
    if grant is None or grant.revoked_at is not None:
        raise McpAuthError("Access has been revoked", error="invalid_grant")

    client = db.query(McpClient).filter(McpClient.client_id == row.client_id).first()

    # Touch last_used_at opportunistically (best-effort; never block on it).
    try:
        from datetime import datetime, timezone

        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()

    return McpPrincipal(
        user_id=row.user_id,
        organization_id=row.organization_id,
        venue_ids=tuple(row.venue_ids or []),
        scopes=frozenset(row.scopes or []),
        client_id=row.client_id,
        client_name=client.client_name if client else row.client_id,
        token_id=row.id,
        grant_id=row.grant_id,
        expires_at=row.expires_at,
    )


def _resolve_dev_principal(token: str, db: Session) -> McpPrincipal | None:
    """Local-only shortcut: act as the first admin, with their real scopes.

    Returns None (not an error) when the dev path doesn't apply, so the caller
    falls through to real token resolution.
    """
    if not settings.MCP_DEV_TOKEN:
        return None
    if settings.ENVIRONMENT != "local":
        # Belt-and-braces: even if the secret leaked into a deployed config.
        logger.error(
            "MCP_DEV_TOKEN is set in a non-local environment (%s) and was ignored",
            settings.ENVIRONMENT,
        )
        return None
    if not hmac.compare_digest(token, settings.MCP_DEV_TOKEN):
        return None

    from app.db.models import (
        OrganizationMembership,
        Role,
        User,
        UserVenueAccess,
    )

    user = (
        db.query(User)
        .filter(User.role == "admin", User.is_active == True)  # noqa: E712
        .order_by(User.created_at)
        .first()
    )
    if not user:
        raise McpAuthError("Dev token is set but no active admin user exists")

    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if not membership:
        raise McpAuthError(f"Dev user {user.email} has no organization membership")

    # Resolve the role's real permissions — the dev token is a shortcut past
    # the OAuth dance, not past authorization.
    role = db.query(Role).filter(Role.id == membership.role_id).first()
    role_perms = set(role.permissions or []) if role else set()
    scopes = scopes_grantable_by(role_perms)

    # Explicit UserVenueAccess join. Never venue_service.get_user_venues —
    # it fails open and would hand a dev token every venue on the platform.
    venue_ids = tuple(
        row.venue_id
        for row in db.query(UserVenueAccess)
        .filter(UserVenueAccess.user_id == user.id)
        .all()
    )

    logger.warning(
        "MCP dev token used — acting as %s (org=%s, %d venues, %d scopes)",
        user.email,
        membership.organization_id,
        len(venue_ids),
        len(scopes),
    )

    return McpPrincipal(
        user_id=user.id,
        organization_id=membership.organization_id,
        venue_ids=venue_ids,
        scopes=frozenset(scopes),
        client_id="dev",
        client_name="Development (dev token)",
        is_dev=True,
    )
