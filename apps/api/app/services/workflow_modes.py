"""Per-user run modes for consolidator workflows.

A workflow (currently the two invoice consolidators) runs in one of three
modes, chosen per user and stored on ``User.workflow_modes`` (a JSON map keyed
by the consolidator's action name). The catalog here is the single source of
truth for the router, the agent set/get tools, and validation. Adding a new
workflow or mode is a data change here — the framework is generic.
"""

from __future__ import annotations

# Mode ids, ordered safest → most autonomous.
MODE_APPROVE_ALL = "approve_all"
MODE_APPROVE_FIXES = "approve_fixes"
MODE_AUTOPILOT = "autopilot"

# Unset resolves to the safest behaviour (approve_all) until the user picks one.
DEFAULT_MODE = MODE_APPROVE_ALL

MODES: list[dict] = [
    {
        "id": MODE_APPROVE_ALL,
        "label": "Approve all",
        "description": "Norm changes nothing on its own — every action is "
        "presented for your approval first.",
    },
    {
        "id": MODE_APPROVE_FIXES,
        "label": "Approve fixes",
        "description": "Norm auto-completes the safe, exact matches; anything "
        "that needs a fix or a change is presented for your approval.",
    },
    {
        "id": MODE_AUTOPILOT,
        "label": "Autopilot",
        "description": "Norm also applies the fixes it can resolve confidently "
        "and completes them automatically; anything ambiguous still waits for you.",
    },
]

MODE_IDS = {m["id"] for m in MODES}

# Workflows that honour a run mode. Key == the consolidator tool's action name
# (== the fix_invoices workflow key) so mode injection can map action → mode.
WORKFLOWS: list[dict] = [
    {
        "key": "review_and_receive_invoices",
        "label": "Receive invoices",
        "description": "Reviewing and receiving outstanding supplier invoices.",
    },
    {
        "key": "reconcile_received_invoices",
        "label": "Reconcile invoices",
        "description": "Reconciling received invoices against supplier statements.",
    },
]

WORKFLOW_KEYS = {w["key"] for w in WORKFLOWS}


def user_mode(user, workflow_key: str) -> str | None:
    """The user's mode for a workflow, or None if unset."""
    modes = getattr(user, "workflow_modes", None) or {}
    mode = modes.get(workflow_key)
    return mode if mode in MODE_IDS else None


def catalog() -> dict:
    """Static catalog for the settings UI and the agent's mode question."""
    return {"workflows": WORKFLOWS, "modes": MODES, "default": DEFAULT_MODE}
