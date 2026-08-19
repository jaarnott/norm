"""How far a venue lets Norm go when receiving invoices.

A ladder with three rungs, and the top rung has switches:

    approve_all    Norm reviews and proposes; a person receives everything.
    approve_fixes  Norm receives an invoice it had nothing to say about.
    autopilot      Norm applies its own suggestions and receives — and may
                   create the things Loaded is missing, but ONLY the kinds
                   this venue has ticked.

Every toggle defaults OFF and every venue starts at ``approve_all``, because
each one authorises an irreversible write in someone else's system: a stock
item, a unit, a brand or a supplier created in Loaded cannot be taken back from
here. The ladder is meant to be climbed on evidence — the readiness report says
how often Norm would have been right — rather than switched on hopefully.

Venue-scoped, not user-scoped: invoices belong to a venue, and venues differ in
how clean their Loaded catalogue is. `users.workflow_modes` still owns the
statement-reconciliation workflow; only receiving moved here.
"""

from __future__ import annotations

MODE_APPROVE_ALL = "approve_all"
MODE_APPROVE_FIXES = "approve_fixes"
MODE_AUTOPILOT = "autopilot"

#: Least to most trusting. Order is load-bearing: see `at_most`.
MODES = (MODE_APPROVE_ALL, MODE_APPROVE_FIXES, MODE_AUTOPILOT)
DEFAULT_MODE = MODE_APPROVE_ALL

AUTO_CREATE_UNITS = "auto_create_units"
AUTO_CREATE_ITEMS = "auto_create_items"
AUTO_CREATE_BRANDS = "auto_create_brands"
AUTO_CREATE_SUPPLIERS = "auto_create_suppliers"
RECEIVE_WITHOUT_UNIT = "receive_without_unit"
RECEIVE_WITH_UNCONFIRMED_UNIT = "receive_with_unconfirmed_unit"
RECEIVE_WITHOUT_PO = "receive_without_po"
RECEIVE_UNRECONCILED_TOTALS = "receive_unreconciled_totals"
AUTO_STRIKE_PHANTOM_LINES = "auto_strike_phantom_lines"
# The delete gates authorise autopilot's only DESTRUCTIVE writes — three
# separate toggles on purpose: deleting a duplicate is evidence-backed,
# deleting an unreadable-copy draft could discard a real delivery behind a
# bad scan. A venue opts into each risk on its own.
AUTO_DELETE_DUPLICATES = "auto_delete_duplicates"
AUTO_DELETE_NON_INVOICES = "auto_delete_non_invoices"
AUTO_DELETE_UNREADABLE = "auto_delete_unreadable"

#: Toggle -> what it authorises. The keys are also the `gate` names carried on
#: a blocker, so a card can say "this needs X, and X is off" without a second
#: mapping to drift out of step.
GATES: dict[str, str] = {
    AUTO_CREATE_UNITS: "create a unit in Loaded",
    AUTO_CREATE_ITEMS: "create a stock item in Loaded",
    AUTO_CREATE_BRANDS: "create a brand in Loaded",
    AUTO_CREATE_SUPPLIERS: "create a supplier in Loaded",
    RECEIVE_WITHOUT_UNIT: "receive when no unit can be found",
    # Distinct from the one above: there IS a unit, it just came from Loaded
    # rather than from the paper. Different question, different answer.
    RECEIVE_WITH_UNCONFIRMED_UNIT: (
        "receive when the unit came from Loaded rather than the copy"
    ),
    RECEIVE_WITHOUT_PO: "receive without a valid purchase order",
    RECEIVE_UNRECONCILED_TOTALS: ("receive when the copy's totals don't reconcile"),
    AUTO_STRIKE_PHANTOM_LINES: "strike lines the copy doesn't bill",
    AUTO_DELETE_DUPLICATES: "delete a duplicate invoice draft",
    AUTO_DELETE_NON_INVOICES: (
        "delete drafts that aren't invoices (statements, letters)"
    ),
    AUTO_DELETE_UNREADABLE: "delete drafts with no readable invoice copy",
}

DEFAULTS: dict[str, object] = {"mode": DEFAULT_MODE, **{g: False for g in GATES}}


def settings_for(venue) -> dict:
    """This venue's settings, filled out and safe to read blind.

    Unknown keys are dropped and a bad mode falls back to ``approve_all`` — a
    typo in stored JSON must never read as more permission than was granted.
    """
    stored = getattr(venue, "invoice_autopilot", None) or {}
    if not isinstance(stored, dict):
        stored = {}
    out = dict(DEFAULTS)
    mode = stored.get("mode")
    if mode in MODES:
        out["mode"] = mode
    for gate in GATES:
        out[gate] = bool(stored.get(gate))
    return out


def normalise(payload: dict | None) -> dict:
    """Validate a settings payload from the API before it is stored."""
    payload = payload if isinstance(payload, dict) else {}
    mode = payload.get("mode", DEFAULT_MODE)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    out: dict = {"mode": mode}
    for gate in GATES:
        out[gate] = bool(payload.get(gate))
    return out


def at_most(mode: str, ceiling: str | None) -> str:
    """The lower of two rungs.

    Callers may ask for LESS than the venue allows and never more: reviewing a
    single invoice passes ``approve_all`` so opening one in the card can never
    write to Loaded, and a chat request cannot talk a venue into autopilot it
    was never put on.

    Only a RECOGNISED mode lowers anything. Treating an unrecognised ceiling as
    ``approve_all`` sounds like the safe reading and is actually a way to
    silently disable the feature: the mode injected for a user who has set no
    personal preference is the literal string "unset", so every venue would
    have been pinned to approve_all no matter what it was set to. "I did not
    ask for a limit" is not "limit this to nothing".
    """
    mode = mode if mode in MODES else DEFAULT_MODE
    if ceiling not in MODES:
        return mode
    return MODES[min(MODES.index(mode), MODES.index(ceiling))]


def gate_open(settings: dict, gate: str | None) -> bool:
    """Is this blocker's gate ticked? An unknown gate is never open."""
    if not gate or gate not in GATES:
        return False
    return bool(settings.get(gate))


def describe_gate(gate: str | None) -> str:
    """The toggle in the user's words, for a blocker that names why it stopped."""
    return GATES.get(gate or "", "")
