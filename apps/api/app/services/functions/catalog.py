"""Norm Functions in the marketplace catalog — its team member and its App,
declared once.

``scripts/sync_marketplace_catalog.py`` seeds both rows into the shared config
DB (``_member(**MEMBER)``, ``_app(**APP)``). Until it has, the team payload and
the entitlement checks stand in unsaved rows built from this same declaration
(``stand_in_rows``), so an organisation whose ``functions_mode`` is on sees the
Functions tab and pages without anyone writing to a database production reads
(the fallback ``entitlement.py`` describes for the App). Once the seeded rows
exist they win, and ``functions_mode != 'off'`` still has the last word.
"""

from __future__ import annotations

from app.services.functions.entitlement import FUNCTIONS_APP_SLUG

#: The team member's slug: its agent_configs row, app/agents/registry.MEMBERS,
#: and the ``agent`` of the Functions pages in the web's pageRegistry.
MEMBER_SLUG = "functions"

#: Keyword arguments for the seed's ``_member``. No price here: the seed's
#: defaults price every member alike.
MEMBER = {
    "slug": "functions-agent",
    "owns": MEMBER_SLUG,
    "name": "Functions",
    "icon": "🥂",
    "description": (
        "Coordinates functions and events when you ask: enquiries, bookings, "
        "proposals and the venue inboxes."
    ),
    "tagline": "Enquiries, events, proposals, the client portal and venue inboxes.",
}


def _page(key: str, page_id: str, label: str, icon: str, description: str) -> dict:
    """A component with its menu page, in the shape the seed's ``_c`` writes.
    Every Functions page loads its own data from /api/functions/*."""
    return {
        "key": key,
        "page": {"id": page_id, "label": label, "icon": icon},
        "full_width": True,
        "description": description,
    }


#: The Functions member's tools: built-ins in app/agents/functions_tools.py
#: (``ACTIONS``), rows on the ``norm_functions`` spec written by
#: scripts/sync_norm_functions.py. Claimed here, they reach the agent only
#: while the App is on (the App Map is exposure). tests pin all three equal.
TOOLS = tuple(
    f"norm_functions.{action}"
    for action in (
        "find_enquiries",
        "get_enquiry",
        "record_enquiry",
        "find_events",
        "get_event",
        "record_event",
        "find_threads",
        "read_thread",
        "file_thread",
        "draft_email",
        "send_email",
        "build_proposal",
        "send_proposal",
        "get_venue_profile",
    )
)

#: The owner's functions-desk skills, ported from claude.ai to Norm's tools
#: (playbooks written by scripts/sync_norm_functions.py).
SKILLS = (
    "functions_inbox_sweep",
    "functions_call_email_proposal",
    "functions_proposal_build",
    "functions_email_tone",
    "functions_runsheet",
)

#: Keyword arguments for the seed's ``_app``. The pages mirror the web's
#: pageRegistry ``functions-*`` entries (tests/test_functions_catalog.py pins
#: ids, labels and icons).
APP = {
    "slug": FUNCTIONS_APP_SLUG,
    "name": "Norm Functions",
    "member": MEMBER_SLUG,
    "icon": "🛎️",
    "description": (
        "Function enquiries, events, proposals, the client portal and venue "
        "inboxes — runs on Norm."
    ),
    "note": (
        "Native to Norm: work it with the norm_functions tools (not open_app); "
        "staff edit on the Functions pages. Never quote a minimum spend, price "
        "or date you haven't read from Functions. Drafts and proposals are "
        "free; a customer email or proposal goes only from an approval card."
    ),
    "tools": list(TOOLS),
    "skills": list(SKILLS),
    "components": [
        _page(
            "functions_dashboard",
            "functions-dashboard",
            "Dashboard",
            "LayoutDashboard",
            "What needs attention, the pipeline and the next four weeks of functions.",
        ),
        _page(
            "functions_enquiries",
            "functions-enquiries",
            "Enquiries",
            "ClipboardList",
            "Function enquiries with their stage, next action and minimum-spend options.",
        ),
        _page(
            "functions_events",
            "functions-events",
            "Events",
            "CalendarDays",
            "Booked events: calendar, run sheets, client confirmations and prints.",
        ),
        _page(
            "functions_inbox",
            "functions-inbox",
            "Inbox",
            "Inbox",
            "The venue mailboxes — threads filed, linked to enquiries and answered.",
        ),
        _page(
            "functions_contacts",
            "functions-contacts",
            "Contacts",
            "Contact",
            "Customers, with every enquiry and event they've made.",
        ),
        _page(
            "functions_reports",
            "functions-reports",
            "Reports",
            "BarChart3",
            "Enquiries over time, conversion, upcoming sales and lead sources.",
        ),
        _page(
            "functions_settings",
            "functions-settings",
            "Settings",
            "Settings",
            "Each venue's functions profile: areas, approvers, templates, brand and forms.",
        ),
    ],
}

#: Both catalog rows. ``functions_mode == 'off'`` takes them out of an
#: organisation's entitlements whatever the catalog says.
CATALOG_SLUGS = frozenset({MEMBER["slug"], APP["slug"]})


def stand_in_rows(missing) -> list:
    """Unsaved ``MarketplaceApp`` rows, shaped as the seed writes them, for
    whichever of the two slugs in ``missing`` the config DB doesn't hold yet.

    Never priced: nothing bills for a row that doesn't exist. Never added to a
    session — they are read like catalog rows and thrown away."""
    from app.db.config_models import MarketplaceApp

    rows = []
    if MEMBER["slug"] in missing:
        rows.append(
            MarketplaceApp(
                slug=MEMBER["slug"],
                name=MEMBER["name"],
                description=MEMBER["description"],
                icon=MEMBER["icon"],
                tier="agent",
                bundled=True,
                price_cents=0,
                stripe_price_key=None,
                status="active",
                composition={
                    "owns_agents": [MEMBER["owns"]],
                    "tagline": MEMBER["tagline"],
                },
            )
        )
    if APP["slug"] in missing:
        composition = {
            "member": APP["member"],
            "tools": list(APP.get("tools", ())),
            "components": [dict(c) for c in APP["components"]],
            "skills": list(APP.get("skills", ())),
        }
        if APP.get("note"):
            composition["note"] = APP["note"]
        rows.append(
            MarketplaceApp(
                slug=APP["slug"],
                name=APP["name"],
                description=APP["description"],
                icon=APP["icon"],
                tier="app",
                bundled=True,
                price_cents=0,
                stripe_price_key=None,
                status="active",
                composition=composition,
            )
        )
    return rows
