"""The roster card's writes carry the whole shift (Oct 2026).

Loaded's shift writes are read-modify-write: a PUT replaces the whole shift,
and a delete is a PUT that stamps datestampDeleted. The card sends a delete as
the shift id alone, so the sync filled nothing in: the delete had no stamp
(refused as a missing required field) and, had it gone, would have blanked
the shift's roster, staff member and times. document_sync now fills a roster
op from the shift as the document holds it — after the op was applied, which
is how the PATCH route orders it.
"""

import copy

from app.routers.working_documents import _apply_op
from app.services.document_sync import _build_params

SHIFT = {
    "id": "s-1",
    "rosterId": "r-1",
    "staffMemberId": "st-1",
    "roleId": "role-1",
    "roleName": "Chef",
    "clockinTime": "2026-10-02T08:00:00+13:00",
    "clockoutTime": "2026-10-02T16:00:00+13:00",
    "venueId": "v-1",
    "hourlyRate": 26.5,
    "breaks": [{"id": "b-1", "breakStart": "x", "breakEnd": "y", "paid": False}],
    "rules": [],
    "remunerationType": "HourlyRate",
    "datestampDeleted": None,
}
DATA = [{"id": "r-1", "rosteredShifts": [SHIFT]}]
FULL = {
    "rosterId": "roster_id",
    "staffMemberId": "staff_member_id",
    "roleId": "role_id",
    "clockinTime": "clockin_time",
    "clockoutTime": "clockout_time",
    "venueId": "venue_id",
    "hourlyRate": "hourly_rate",
    "breaks": "breaks",
    "rules": "rules",
    "remunerationType": "remuneration_type",
    "datestampDeleted": "datestamp_deleted",
}


class Doc:
    def __init__(self, data, doc_type="roster"):
        self.data = data
        self.doc_type = doc_type
        self.external_ref = {"start_datetime": "a", "end_datetime": "b"}


def _sync_params(op, mapping, doc_type="roster"):
    doc = Doc(_apply_op(copy.deepcopy(DATA), copy.deepcopy(op)), doc_type)
    return _build_params(op, doc, mapping)


def test_a_delete_carries_the_whole_shift_and_its_stamp():
    p = _sync_params(
        {"op": "delete_shift", "shift_id": "s-1"},
        {"field_mapping": FULL, "ref_fields": {}, "id_field": "shift_id"},
    )
    assert p["shift_id"] == "s-1" and p["datestamp_deleted"]
    assert p["roster_id"] == "r-1" and p["staff_member_id"] == "st-1"
    assert p["clockin_time"] == SHIFT["clockinTime"]
    assert p["venue_id"] == "v-1" and p["hourly_rate"] == 26.5
    assert p["breaks"] == SHIFT["breaks"]


def test_an_update_keeps_what_the_op_did_not_send():
    p = _sync_params(
        {
            "op": "update_shift",
            "shift_id": "s-1",
            "fields": {"clockoutTime": "2026-10-02T17:00:00+13:00"},
        },
        {"field_mapping": FULL, "ref_fields": {}, "id_field": "shift_id"},
    )
    assert p["clockout_time"] == "2026-10-02T17:00:00+13:00"
    assert p["clockin_time"] == SHIFT["clockinTime"]
    assert p["breaks"] == SHIFT["breaks"] and p["role_id"] == "role-1"
    assert p["datestamp_deleted"] is None


def test_the_op_wins_over_the_document():
    p = _sync_params(
        {"op": "update_shift", "shift_id": "s-1", "fields": {"roleId": "role-2"}},
        {"field_mapping": FULL, "ref_fields": {}, "id_field": "shift_id"},
    )
    assert p["role_id"] == "role-2"


def test_an_add_has_no_shift_to_fill_from():
    p = _sync_params(
        {
            "op": "add_shift",
            "fields": {"rosterId": "r-1", "roleId": "role-1", "clockinTime": "t"},
        },
        {
            "field_mapping": {
                "rosterId": "roster_id",
                "roleId": "role_id",
                "clockinTime": "clockin_time",
            },
            "ref_fields": {},
            "id_field": None,
        },
    )
    assert p == {"roster_id": "r-1", "role_id": "role-1", "clockin_time": "t"}


def test_other_documents_are_left_alone():
    p = _sync_params(
        {"op": "delete_shift", "shift_id": "s-1"},
        {"field_mapping": FULL, "ref_fields": {}, "id_field": "shift_id"},
        doc_type="order",
    )
    assert p == {"shift_id": "s-1"}


def test_an_unknown_shift_fills_nothing():
    p = _sync_params(
        {"op": "delete_shift", "shift_id": "nope"},
        {"field_mapping": FULL, "ref_fields": {}, "id_field": "shift_id"},
    )
    assert p == {"shift_id": "nope"}


def test_the_sync_writes_with_the_documents_venue(db_session, monkeypatch):
    """Without the document's venue the login lookup had none and fell back to
    whichever venue's connection came first."""
    import uuid

    import app.agents.tool_loop as TL
    import app.services.document_sync as DS
    from app.db.models import WorkingDocument
    from tests.conftest import _make_thread, _make_user, _make_venue

    venue = _make_venue(db_session, name="Sync Venue")
    thread = _make_thread(db_session, _make_user(db_session))
    doc = WorkingDocument(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        doc_type="roster",
        connector_name="loadedhub",
        venue_id=venue.id,
        sync_mode="auto",
        data=copy.deepcopy(DATA),
        external_ref={},
        sync_status="dirty",
        pending_ops=[{"op": "delete_shift", "shift_id": "s-1"}],
        version=2,
    )
    db_session.add(doc)
    db_session.flush()
    seen = []

    def fake_execute(tc, db, config_db=None):
        seen.append(dict(tc.input_params))
        return {"success": True}

    monkeypatch.setattr(TL, "_execute_tool_call", fake_execute)
    monkeypatch.setattr(
        DS,
        "_get_mapping",
        lambda op, d, db: {
            "target_action": "delete_shift",
            "method": "PUT",
            "field_mapping": FULL,
            "ref_fields": {},
            "id_field": "shift_id",
        },
    )
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    DS.sync_document(doc.id, db_session, config_db=db_session)
    assert seen and seen[0]["venue_id"] == venue.id
    assert seen[0]["shift_id"] == "s-1" and seen[0]["roster_id"] == "r-1"
    assert doc.sync_status == "synced" and doc.pending_ops == []


def test_a_document_outside_a_conversation_saves(db_session, monkeypatch):
    """The Roster page's document has no thread. tool_calls.thread_id was NOT
    NULL, so every page edit failed on the insert; it is optional now, and the
    write is still recorded."""
    import uuid

    import app.agents.tool_loop as TL
    import app.services.document_sync as DS
    from app.db.models import ToolCall, WorkingDocument
    from tests.conftest import _make_venue

    venue = _make_venue(db_session, name="Page Venue")
    doc = WorkingDocument(
        id=str(uuid.uuid4()),
        thread_id=None,
        doc_type="roster",
        connector_name="loadedhub",
        venue_id=venue.id,
        sync_mode="auto",
        data=copy.deepcopy(DATA),
        external_ref={},
        sync_status="dirty",
        pending_ops=[{"op": "delete_shift", "shift_id": "s-1"}],
        version=2,
    )
    db_session.add(doc)
    db_session.flush()
    seen = []

    def fake_execute(tc, db, config_db=None):
        seen.append(tc.id)
        return {"success": True}

    monkeypatch.setattr(TL, "_execute_tool_call", fake_execute)
    monkeypatch.setattr(
        DS,
        "_get_mapping",
        lambda op, d, db: {
            "target_action": "delete_shift",
            "method": "PUT",
            "field_mapping": FULL,
            "ref_fields": {},
            "id_field": "shift_id",
        },
    )
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    DS.sync_document(doc.id, db_session, config_db=db_session)
    assert doc.sync_status == "synced" and doc.pending_ops == []
    recorded = db_session.query(ToolCall).filter(ToolCall.id == seen[0]).one()
    assert recorded.thread_id is None and recorded.input_params["venue_id"] == venue.id
