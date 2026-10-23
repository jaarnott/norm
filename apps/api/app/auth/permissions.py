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
        "agents": None,
        "apps": None,
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
        "agents": None,
        "apps": None,
    },
    "team_member": {
        "display_name": "Team Member",
        "description": "Venue staff: their own shifts, training and reviews, "
        "and the team apps their organisation switches on.",
        # Trimmed for venue staff (staff access, Oct 2026): it used to read
        # orders, the roster, HR and reports, which a chef should not.
        "permissions": [
            "tasks:read",
            "tasks:write",
            "org:read",
            # Building an app is open to everyone — it can only ever do what
            # its author could already do by hand. Sharing it is not here.
            "apps:build",
        ],
        # No AI agent at all; the apps are whichever the owner ticks.
        "agents": [],
        "apps": [],
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
        "agents": None,
        "apps": None,
    },
}

# ── Venue roles ─────────────────────────────────────────────────────
# Created in an organisation as ordinary custom roles (is_system=False) when
# staff access is switched on (services/role_access.ensure_venue_roles), so an
# owner can rename them, change them or add more. ``agents``/``apps``: None
# means every hired agent / every app that is on; a list means only those.
# ``loaded_roles`` are the Loaded role names that land a new starter in this
# role — editable guesses. kitchen, front_of_house and managers are apps that
# arrive with the Orbit port; a role may name an app that doesn't exist yet.
# Training and Reviews (``performance``) join these lists when their member
# views ship; until then their pages show only a manager's view.
VENUE_ROLES: dict[str, dict] = {
    "kitchen": {
        "display_name": "Kitchen",
        "description": "Kitchen staff: the Kitchen app.",
        "permissions": list(STANDARD_ROLES["team_member"]["permissions"]),
        "agents": [],
        "apps": ["kitchen"],
        "loaded_roles": [
            "Chef",
            "Cook",
            "Sous Chef",
            "Chef de Partie",
            "Commis Chef",
            "Apprentice Chef",
            "Kitchen Hand",
            "Kitchenhand",
            "Dishwasher",
        ],
    },
    "front_of_house": {
        "display_name": "Front of house",
        "description": "Floor and bar staff: the Front of house app.",
        "permissions": list(STANDARD_ROLES["team_member"]["permissions"]),
        "agents": [],
        "apps": ["front_of_house"],
        "loaded_roles": [
            "Bartender",
            "Barista",
            "Waiter",
            "Waitress",
            "Wait Staff",
            "Floor Staff",
            "Bar Staff",
            "Host",
            "Hostess",
            "Supervisor",
        ],
    },
    "head_chef": {
        "display_name": "Head chef",
        "description": "Runs the kitchen: the Kitchen and Managers apps.",
        "permissions": list(STANDARD_ROLES["team_member"]["permissions"]),
        "agents": [],
        "apps": ["kitchen", "managers"],
        "loaded_roles": ["Head Chef", "Executive Chef"],
    },
    "venue_manager": {
        "display_name": "Venue manager",
        "description": "Runs a venue: a manager's permissions, every AI agent and every app.",
        "permissions": list(STANDARD_ROLES["manager"]["permissions"]),
        "agents": None,
        "apps": None,
        "loaded_roles": [
            "Venue Manager",
            "General Manager",
            "Assistant Manager",
            "Restaurant Manager",
            "Bar Manager",
            "Duty Manager",
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
