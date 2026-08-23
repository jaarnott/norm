"""Roster display document — the shared shaping primitive.

The source of truth for the payload the roster editor renders. Mirrors the
pattern in ``received_invoice.build_received_invoice_data``: a pure shaping
function that takes already-fetched (already-transformed) connector payloads and
assembles the single ``data`` dict the working document holds and the
``roster_editor`` React component reads.

Kept in one place so both callers agree:

  * the web read path (``routers/working_documents`` / a roster draft endpoint),
    which fetches via the connector executor and calls this; and
  * the ``prepare_roster`` consolidator, which runs in the no-import sandbox and
    therefore *duplicates* this shaping inline — KEEP THE TWO IN SYNC, this
    function is the source of truth (same discipline as
    the retired ``prepare_receive_invoice`` consolidator).

It is LoadedHub-specific by design: the connector components are tied to their
connector rather than pretending to be connector-agnostic. A second rostering
connector would get its own shaping + consolidator, not a generic abstraction.

The roster body itself (``rosteredShifts`` + meta) is already shaped by the
``get_roster`` tool's ``response_transform``; the staff / roles / leave /
unavailability lists are already shaped by their own tools. So this function is
a *combiner*, not a re-implementation of those transforms — it nests the
reference lists under ``_``-prefixed keys (mirroring how ``receive_display``
bakes ``_units`` / ``_purchase_orders`` into its block) so the editor gets
everything from the one document instead of four extra round-trips.
"""

from __future__ import annotations

from typing import Any


def _roster_base(roster: Any) -> dict:
    """The roster object carrying meta + rosteredShifts.

    ``get_roster`` returns the transformed roster; depending on the venue/window
    that is either a single roster object or a one-element list of them (the
    editor's ``extractShifts``/``extractRosterMeta`` already tolerate both). We
    normalise to the dict so the reference lists can be attached alongside it,
    and preserve an empty roster as an empty dict rather than inventing fields.
    """
    if isinstance(roster, list):
        return dict(roster[0]) if roster and isinstance(roster[0], dict) else {}
    if isinstance(roster, dict):
        return dict(roster)
    return {}


def build_roster_data(
    roster: Any,
    staff: Any = None,
    roles: Any = None,
    leave: Any = None,
    unavailability: Any = None,
) -> dict:
    """Assemble the roster editor's ``data`` from the five fetched payloads.

    Args are the already-transformed results of ``get_roster`` and the four
    reference reads. Returns the working-document ``data`` dict: the roster meta
    and ``rosteredShifts`` at top level (what the editor's extractors read), plus
    the reference lists nested under ``_staff`` / ``_roles`` / ``_leave`` /
    ``_unavailability`` so the component never has to fetch them separately.
    """
    base = _roster_base(roster)
    base["_staff"] = staff if isinstance(staff, list) else []
    base["_roles"] = roles if isinstance(roles, list) else []
    base["_leave"] = leave if isinstance(leave, list) else []
    base["_unavailability"] = unavailability if isinstance(unavailability, list) else []
    return base
