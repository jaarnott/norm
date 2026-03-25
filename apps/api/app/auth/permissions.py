"""Permission scopes and standard role definitions.

This is the single source of truth for what permissions exist in the system
and what the built-in roles grant.  Custom roles are stored in the database
but can only use scopes from PERMISSION_SCOPES.
"""

# ── All valid permission scopes ─────────────────────────────────
PERMISSION_SCOPES: set[str] = {
    # Tasks & Conversations
    "tasks:read",
    "tasks:write",
    "tasks:approve",
    # Orders
    "orders:read",
    "orders:write",
    "orders:approve",
    "orders:submit",
    # Roster
    "roster:read",
    "roster:write",
    # HR
    "hr:read",
    "hr:write",
    "hr:hire",
    # Reports
    "reports:read",
    "reports:create",
    # Billing
    "billing:read",
    "billing:manage",
    # Organization settings
    "org:read",
    "org:manage",
    "org:members",
    "org:roles",
    "org:venues",
    # Connector / agent settings (org-level)
    "settings:connectors",
    "settings:agents",
    # Platform admin (checked via User.role, listed for completeness)
    "admin:deployments",
    "admin:tests",
    "admin:system",
}

# Scopes that require User.role == "admin" (platform-level, not org-level)
PLATFORM_ADMIN_SCOPES: set[str] = {
    "admin:deployments",
    "admin:tests",
    "admin:system",
}

# All non-admin scopes (for the Owner role)
ALL_ORG_PERMISSIONS: list[str] = sorted(PERMISSION_SCOPES - PLATFORM_ADMIN_SCOPES)

# ── Standard (system) roles ─────────────────────────────────────
STANDARD_ROLES: dict[str, dict] = {
    "owner": {
        "display_name": "Owner",
        "description": "Full access to everything in the organization.",
        "permissions": ALL_ORG_PERMISSIONS,
    },
    "manager": {
        "display_name": "Manager",
        "description": "Manage day-to-day operations. Cannot change billing or create custom roles.",
        "permissions": [
            p for p in ALL_ORG_PERMISSIONS if p not in {"billing:manage", "org:roles"}
        ],
    },
    "team_member": {
        "display_name": "Team Member",
        "description": "View data and create tasks. Read-only access to most areas.",
        "permissions": [
            "tasks:read",
            "tasks:write",
            "orders:read",
            "roster:read",
            "hr:read",
            "reports:read",
            "org:read",
        ],
    },
    "payroll_admin": {
        "display_name": "Payroll Administrator",
        "description": "Manage HR, roster, and employee data.",
        "permissions": [
            "hr:read",
            "hr:write",
            "hr:hire",
            "roster:read",
            "roster:write",
            "tasks:read",
            "tasks:write",
            "reports:read",
            "org:read",
        ],
    },
}

# Permission scope groupings for the UI
PERMISSION_GROUPS: dict[str, list[str]] = {
    "Tasks": ["tasks:read", "tasks:write", "tasks:approve"],
    "Orders": ["orders:read", "orders:write", "orders:approve", "orders:submit"],
    "Roster": ["roster:read", "roster:write"],
    "HR": ["hr:read", "hr:write", "hr:hire"],
    "Reports": ["reports:read", "reports:create"],
    "Billing": ["billing:read", "billing:manage"],
    "Organization": [
        "org:read",
        "org:manage",
        "org:members",
        "org:roles",
        "org:venues",
    ],
    "Settings": ["settings:connectors", "settings:agents"],
}
