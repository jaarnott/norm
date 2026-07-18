"""OAuth authorization-server helpers.

The security-load-bearing pieces that the router endpoints share: redirect-URI
validation, org/venue/scope resolution for consent, and the grant upsert. Kept
out of the router so each rule has one home and one test.
"""

from __future__ import annotations

import fnmatch
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.mcp.scopes import MCP_SCOPES, scopes_grantable_by


def redirect_uri_allowed(redirect_uri: str) -> bool:
    """Whether a redirect URI is permitted.

    Host must match ``MCP_ALLOWED_REDIRECT_HOSTS`` (glob), and the scheme must
    be https except for loopback. This is what stops a dynamically-registered
    client from pointing the authorization code at an attacker's server, so it
    is checked at BOTH registration and /authorize — and always by exact host
    match, never a prefix/substring test (that's how redirect_uri bypasses
    happen).
    """
    try:
        parsed = urlparse(redirect_uri)
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False

    is_loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        return False

    return any(
        fnmatch.fnmatch(host, pattern.lower())
        for pattern in settings.mcp_allowed_redirect_hosts
    )


def client_redirect_matches(client, redirect_uri: str) -> bool:
    """Exact membership check against the client's registered URIs."""
    return redirect_uri in (client.redirect_uris or [])


def resolve_consent_context(user, db: Session) -> list[dict]:
    """The orgs, venues, and grantable scopes this user may consent to.

    Deliberately NOT built with require_permission (not org-aware; admin
    bypass) or venue_service.get_user_venues (fails open). Every org membership
    is returned — not `.first()` — and venues come from an explicit
    UserVenueAccess join, so an empty list means empty.
    """
    from app.db.models import (
        OrganizationMembership,
        Role,
        UserVenueAccess,
        Venue,
    )

    memberships = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .all()
    )

    out: list[dict] = []
    for m in memberships:
        role = db.query(Role).filter(Role.id == m.role_id).first()
        role_perms = set(role.permissions or []) if role else set()

        venues = (
            db.query(Venue)
            .join(UserVenueAccess, UserVenueAccess.venue_id == Venue.id)
            .filter(
                UserVenueAccess.user_id == user.id,
                Venue.organization_id == m.organization_id,
            )
            .order_by(Venue.name)
            .all()
        )

        out.append(
            {
                "organization_id": m.organization_id,
                "organization_name": m.organization.name if m.organization else "",
                "role_display_name": role.display_name if role else m.role,
                "venues": [{"id": v.id, "name": v.name} for v in venues],
                "grantable_scopes": sorted(scopes_grantable_by(role_perms)),
            }
        )
    return out


def validate_and_downscope(
    user,
    organization_id: str,
    venue_ids: list[str],
    approved_scopes: list[str],
    db: Session,
) -> tuple[list[str], list[str]]:
    """Validate a consent submission against live state and downscope it.

    The whole body is attacker-controlled, so nothing is trusted:
      - the org membership must actually exist (the uq_user_org lookup that
        require_permission fails to do),
      - every venue must have a live UserVenueAccess row AND belong to this org
        (a NULL-org venue is refused, not defaulted — it would bridge orgs),
      - granted scopes are intersected with what the role actually permits.

    Returns (effective_venue_ids, effective_scopes). Raises ValueError with a
    user-safe message on any violation.
    """
    from app.db.models import (
        OrganizationMembership,
        Role,
        UserVenueAccess,
        Venue,
    )

    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == organization_id,
        )
        .first()
    )
    if not membership:
        raise ValueError("You are not a member of that organization.")

    # Venues: live access AND same org. NULL-org venues rejected.
    permitted_ids = set()
    for vid in venue_ids:
        access = (
            db.query(UserVenueAccess)
            .filter(
                UserVenueAccess.user_id == user.id,
                UserVenueAccess.venue_id == vid,
            )
            .first()
        )
        if not access:
            raise ValueError("You do not have access to one of the selected venues.")
        venue = db.query(Venue).filter(Venue.id == vid).first()
        if not venue or venue.organization_id != organization_id:
            raise ValueError("One of the selected venues is not in this organization.")
        permitted_ids.add(vid)

    # Scopes: intersect requested with what the current role grants.
    role = db.query(Role).filter(Role.id == membership.role_id).first()
    role_perms = set(role.permissions or []) if role else set()
    grantable = scopes_grantable_by(role_perms)

    unknown = set(approved_scopes) - set(MCP_SCOPES)
    if unknown:
        raise ValueError(f"Unknown scope(s): {', '.join(sorted(unknown))}")

    effective = sorted(set(approved_scopes) & grantable)
    if not effective:
        raise ValueError("None of the requested permissions are allowed by your role.")

    return sorted(permitted_ids), effective


def upsert_grant(
    user,
    client_id: str,
    organization_id: str,
    venue_ids: list[str],
    scopes: list[str],
    db: Session,
):
    """Create or update the durable consent record. Returns the McpGrant."""
    from app.db.mcp_models import McpGrant

    grant = (
        db.query(McpGrant)
        .filter(
            McpGrant.user_id == user.id,
            McpGrant.client_id == client_id,
            McpGrant.organization_id == organization_id,
        )
        .first()
    )
    if grant is None:
        grant = McpGrant(
            user_id=user.id,
            client_id=client_id,
            organization_id=organization_id,
        )
        db.add(grant)

    grant.venue_ids = venue_ids
    grant.scopes = scopes
    grant.revoked_at = None
    db.flush()
    return grant
