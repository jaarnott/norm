"""McpPrincipal — the resolved caller behind an MCP request.

Everything authorization needs, resolved once per request and never inferred
again. Three fields exist specifically because the platform's own auth can't
supply them:

- ``organization_id`` is **explicit**. ``require_permission`` loads an
  OrganizationMembership filtered only by user_id and takes ``.first()``, so a
  multi-org user's permissions in one org silently apply in another. An MCP
  token names its org.
- ``venue_ids`` is the **consented subset**, not "every venue the user can
  reach". A caller-supplied venue_id is checked against this, never trusted.
- ``scopes`` is the **granted subset**, not the role's full set. The user chose
  it at the consent screen and may have deselected some.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class McpPrincipal:
    user_id: str
    organization_id: str
    venue_ids: tuple[str, ...]
    scopes: frozenset[str]

    # Which external client is acting. Denormalised onto every audit row so the
    # trail survives the client being renamed or deleted.
    client_id: str = "dev"
    client_name: str = "Development"

    # Identifies the token for audit and revocation. Populated in phase 2.
    token_id: str | None = None
    grant_id: str | None = None
    expires_at: datetime | None = None

    # Set only by the dev-token path; must never be true in a deployed env.
    is_dev: bool = field(default=False)

    def has_scopes(self, required: frozenset[str] | set[str]) -> bool:
        return frozenset(required) <= self.scopes
