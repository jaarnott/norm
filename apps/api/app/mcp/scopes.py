"""The ``mcp:*`` scope vocabulary.

A dedicated vocabulary, mapped onto — but distinct from — the 23 org
permission scopes in ``app.auth.permissions``. Four reasons it isn't just a
reuse of those:

1. **The org vocabulary has no `draft` concept.** It jumps straight from
   ``orders:read`` to ``orders:write``. Reusing it would mean granting
   ``orders:write`` for Claude to *draft* an order — which also authorizes
   *submitting* one to a supplier. ``mcp:orders:draft`` requires
   ``orders:write`` but grants strictly less. That asymmetry is the whole
   point of a draft-only v1, and it is unrepresentable in the org vocabulary.

2. **Consent screens are read by humans.** "Claude wants: settings:connectors"
   is not informed consent. Every MCP scope carries a label and description
   written for the person clicking Approve.

3. **Blast radius.** Adding ``mcp:*`` to ``PERMISSION_SCOPES`` would put them
   in ``ALL_ORG_PERMISSIONS`` → auto-granted to every Owner → rendered in the
   roles UI. That is a modification to shared auth, which this layer must not
   make. This module is a separate vocabulary; permissions.py is never edited.

4. **Granting is not having.** An MCP scope is a projection of what the user's
   role already allows, always a subset. Keeping the vocabularies distinct
   makes "what can the MCP surface ever do?" answerable by reading one file.

``requires`` names org scopes the user's role must actually hold. There is no
platform-admin bypass here — unlike ``require_permission``, which returns
early for ``User.role == "admin"``. The admin bypass exists for platform
operations; it must not hand a third-party AI client god-mode over every org
because an operator happened to click Connect.
"""

from __future__ import annotations

from dataclasses import dataclass

# Access levels. v1 ships read + draft; "write" exists in the type so the
# boundary is expressible, but no scope below declares it.
ACCESS_READ = "read"
ACCESS_DRAFT = "draft"
ACCESS_WRITE = "write"

V1_ACCESS_LEVELS = frozenset({ACCESS_READ, ACCESS_DRAFT})


@dataclass(frozen=True)
class McpScope:
    name: str
    label: str  # consent screen title
    description: str  # consent screen body
    access_level: str  # read | draft | write
    requires: frozenset[str]  # org scopes from app.auth.permissions


MCP_SCOPES: dict[str, McpScope] = {
    "mcp:venues:read": McpScope(
        name="mcp:venues:read",
        label="See your venues",
        description="View the list of venues you have access to, with their "
        "location and timezone.",
        access_level=ACCESS_READ,
        requires=frozenset({"org:read"}),
    ),
    "mcp:reports:read": McpScope(
        name="mcp:reports:read",
        label="View sales and performance data",
        description="Read sales figures, trading performance and saved reports "
        "for your venues.",
        access_level=ACCESS_READ,
        requires=frozenset({"reports:read"}),
    ),
    "mcp:orders:read": McpScope(
        name="mcp:orders:read",
        label="View purchase orders and stock",
        description="See order history, line items, suppliers, delivery status "
        "and stock levels.",
        access_level=ACCESS_READ,
        requires=frozenset({"orders:read"}),
    ),
    "mcp:orders:draft": McpScope(
        name="mcp:orders:draft",
        label="Draft purchase orders",
        description="Prepare draft orders for you to review and approve in Norm. "
        "Claude cannot submit orders to suppliers.",
        access_level=ACCESS_DRAFT,
        # Requires orders:write, but grants strictly less: a draft, never a submit.
        requires=frozenset({"orders:read", "orders:write"}),
    ),
    "mcp:roster:read": McpScope(
        name="mcp:roster:read",
        label="View rosters",
        description="See published shifts and staffing levels for your venues.",
        access_level=ACCESS_READ,
        requires=frozenset({"roster:read"}),
    ),
    "mcp:hr:read": McpScope(
        name="mcp:hr:read",
        label="View HR records",
        description="See employee records, leave balances and contract details.",
        access_level=ACCESS_READ,
        requires=frozenset({"hr:read"}),
    ),
    "mcp:tasks:read": McpScope(
        name="mcp:tasks:read",
        label="View tasks and scheduled reports",
        description="See your tasks, scheduled reports and their run history.",
        access_level=ACCESS_READ,
        requires=frozenset({"tasks:read"}),
    ),
    "mcp:tasks:draft": McpScope(
        name="mcp:tasks:draft",
        label="Draft tasks and scheduled reports",
        description="Create or change scheduled tasks and reports for you to "
        "review. Claude cannot approve or run them.",
        access_level=ACCESS_DRAFT,
        requires=frozenset({"tasks:read", "tasks:write"}),
    ),
}


def scopes_grantable_by(role_permissions: set[str] | frozenset[str]) -> set[str]:
    """MCP scopes whose `requires` are fully covered by this role.

    No admin bypass — see the module docstring.
    """
    perms = set(role_permissions)
    return {name for name, scope in MCP_SCOPES.items() if scope.requires <= perms}


def validate_scope_vocabulary() -> list[str]:
    """Check every `requires` names a real org permission scope.

    A `requires` entry that isn't in PERMISSION_SCOPES can never be satisfied
    by any role, so the scope silently becomes ungrantable and its tools become
    invisible — surfacing only as one confused user. This is the same failure
    class as the `email:read` / `email:manage` drift, where routers gate on
    scopes that PERMISSION_SCOPES doesn't define and no role can hold.

    Called at import time (below) so it fails at boot, not at authz time.
    """
    from app.auth.permissions import PERMISSION_SCOPES

    problems: list[str] = []
    for name, scope in MCP_SCOPES.items():
        if name != scope.name:
            problems.append(f"{name}: key does not match scope.name ({scope.name})")
        unknown = scope.requires - set(PERMISSION_SCOPES)
        if unknown:
            problems.append(
                f"{name}: requires unknown org scope(s) {sorted(unknown)} — "
                "no role can ever grant this"
            )
        if not scope.requires:
            problems.append(
                f"{name}: has no `requires` — it would be granted to any role"
            )
        if scope.access_level not in V1_ACCESS_LEVELS:
            problems.append(
                f"{name}: access_level '{scope.access_level}' is not "
                f"permitted in v1 (read/draft only)"
            )
    return problems


_problems = validate_scope_vocabulary()
if _problems:  # pragma: no cover - fails at import, before any request
    raise RuntimeError("Invalid MCP scope vocabulary:\n  - " + "\n  - ".join(_problems))
