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
    # Email — your own connected mailboxes and the organisation's sent mail.
    # The routers gated on these for months while no role could hold them, so
    # owners got 403 on the Email tab. Platform-wide email (the shared
    # templates, test sends from Norm's address) stays admin-only.
    "email:read",
    "email:manage",
    # Apps — building one is open to anyone with this scope; SHARING it beyond
    # yourself is separate, because a share is what puts your app in front of
    # someone else's data with their permissions.
    "apps:build",
    "apps:share",
    # Receiving rules — how an organisation's invoices are received where the
    # invoice leaves a choice (a due date), set by an owner or a manager.
    "receiving:rules",
    # Payroll and finance (Oct 2026, Loaded Payroll + Norm Finance). Pay data
    # — rates, per-shift costs, payruns, leave payouts, wage journals — is for
    # the owner and the payroll administrator only; a venue manager does not
    # open the Payrun for their own venue (owner decision D8). The group's
    # finances — weekly summaries, budgets, forecasts — are for owners and
    # managers; never a team member.
    "payroll:read",
    "payroll:write",
    "finance:read",
    "budgets:write",
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
        # Everything but billing, custom roles and pay: a new scope lands on
        # managers by default, which is why payroll:* is named here — a venue
        # manager never sees rates, payruns or journals (D8).
        "permissions": [
            p
            for p in ALL_ORG_PERMISSIONS
            if p not in {"billing:manage", "org:roles", "payroll:read", "payroll:write"}
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
            # Building an app is open to everyone — it can only ever do what
            # its author could already do by hand. Sharing it is not here.
            "apps:build",
        ],
    },
    "payroll_admin": {
        "display_name": "Payroll Administrator",
        "description": (
            "Manage HR, roster, employee and payroll data, and the group's finances."
        ),
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
            "payroll:read",
            "payroll:write",
            "finance:read",
            "budgets:write",
        ],
    },
}

# Permission scope groupings for the UI — the custom-role editor offers only
# what is in a group, so every org scope belongs in exactly one (apps:* were
# missing until Oct 2026, so no custom role could build or share an app).
# Platform-admin scopes stay out: they are checked on User.role, never a role.
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
    "Email": ["email:read", "email:manage"],
    "Apps": ["apps:build", "apps:share"],
    "Receiving": ["receiving:rules"],
    "Payroll": ["payroll:read", "payroll:write"],
    "Finance": ["finance:read", "budgets:write"],
}
