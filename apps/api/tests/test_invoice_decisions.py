"""One way to decide an invoice (services/invoice_decisions).

The owner's intent (20 Oct 2026): Autopilot does what a person does on the
card, and the switches only say which suggestions it may accept. So one
function makes every decision — the card sends "accept suggestion X" (a
``decide`` op on the working-document PATCH), Autopilot calls the same — and
readiness is worked out once, on the server, and sent with the document.
"""

import uuid

from app.db.models import WorkingDocument
from app.services import invoice_decisions as D
from app.services import invoice_review as IR


def _card(**over):
    data = {
        "invoice_id": "inv-1",
        "doc_schema": IR.DOC_SCHEMA,
        "reviewed_at": "2026-10-20T09:00:00+00:00",
        "linked_supplier_id": "s-1",
        "lines": [
            {
                "id": "ld-1",
                "description": "FRIES",
                "linked_item_id": "i-1",
                "linked_unit_id": None,
                "unit": None,
                "unit_ratio": None,
                "quantity_received": 4,
                "unit_cost": 10,
                "total_cost": 40,
            },
            {
                "id": "ld-2",
                "description": "SALT",
                "linked_item_id": "i-2",
                "linked_unit_id": "u-each",
                "unit": "Each",
                "quantity_received": 1,
                "unit_cost": 2,
                "total_cost": 2,
            },
        ],
        "suggestions": [
            {
                "id": "line_value:unit:ld-1",
                "kind": "line_value",
                "field": "unit",
                "line_id": "ld-1",
                "apply": {"linked_unit_id": "u-kg", "unit": "Kilo", "unit_ratio": 1},
                "explanation": "the invoice says Kilo",
            },
            {
                "id": "line_value:quantity_received:ld-2",
                "kind": "line_value",
                "field": "quantity_received",
                "line_id": "ld-2",
                "apply": {"quantity_received": 3},
                "explanation": "the invoice bills 3",
            },
            {
                "id": "strike:ld-2",
                "kind": "strike",
                "line_id": "ld-2",
                "apply": {"struck": True},
                "explanation": "not on the copy",
            },
            {
                "id": "add_line:rep-1",
                "kind": "add_line",
                "line_id": "rep-1",
                "payload": {
                    "id": "rep-1",
                    "description": "OIL",
                    "quantity_received": 1,
                },
                "explanation": "the invoice bills OIL",
            },
            {
                "id": "confirm:po_missing",
                "kind": "confirm",
                "field": "linked_purchase_order_id",
                "gate": "receive_without_po",
                "explanation": "no order — receive without one",
            },
        ],
        "issues": [
            {
                "id": "unit_missing:ld-1",
                "code": "unit_missing",
                "line_id": "ld-1",
                "blocking": True,
                "message": "FRIES has no unit",
                "fix": ["line_value:unit:ld-1"],
            },
            {
                "id": "po_missing",
                "code": "po_missing",
                "line_id": None,
                "blocking": True,
                "gate": "receive_without_po",
                "message": "no order is linked",
                "fix": ["confirm:po_missing"],
            },
        ],
        "suggestion_actions": [],
        "edits": [],
    }
    data.update(over)
    return data


def _last(data, sid):
    return IR._last_actions(data).get(sid) or {}


class TestDeciding:
    def test_accept_applies_records_and_retires_the_persons_earlier_edit(self):
        data = _card()
        D.decide(
            data,
            {"edit": {"line_id": "ld-2", "fields": {"quantity_received": 5}}},
            by="u-1",
        )
        assert data["lines"][1]["total_cost"] == 10  # the total follows
        out = D.decide(data, {"accept": "line_value:quantity_received:ld-2"}, by="u-1")
        assert out["ok"]
        assert data["lines"][1]["quantity_received"] == 3
        act = _last(data, "line_value:quantity_received:ld-2")
        assert act["action"] == "accepted" and act["by"] == "user"
        assert act["before"]["quantity_received"] == 5
        assert data["edits"][0]["superseded_by"] == "line_value:quantity_received:ld-2"
        # Twice is once.
        assert (
            D.decide(data, {"accept": "line_value:quantity_received:ld-2"})["did"]
            is None
        )

    def test_undo_puts_back_what_the_decision_replaced_and_the_edit_it_retired(self):
        data = _card()
        D.decide(
            data, {"edit": {"line_id": "ld-2", "fields": {"quantity_received": 5}}}
        )
        D.decide(data, {"accept": "line_value:quantity_received:ld-2"})
        out = D.decide(data, {"undo": "line_value:quantity_received:ld-2"})
        assert out["ok"] and data["lines"][1]["quantity_received"] == 5
        assert _last(data, "line_value:quantity_received:ld-2")["action"] == "undone"
        assert "superseded_by" not in data["edits"][0]
        # An added line's undo takes the line away.
        D.decide(data, {"accept": "add_line:rep-1"})
        assert any(ln["id"] == "rep-1" for ln in data["lines"])
        D.decide(data, {"undo": "add_line:rep-1"})
        assert not any(ln["id"] == "rep-1" for ln in data["lines"])

    def test_decline_records_and_a_missing_suggestion_is_refused(self):
        data = _card()
        assert D.decide(data, {"decline": "strike:ld-2"})["ok"]
        assert _last(data, "strike:ld-2")["action"] == "dismissed"
        out = D.decide(data, {"accept": "line_value:unit:ld-9"})
        assert out == {
            "ok": False,
            "detail": "that suggestion is no longer on the invoice",
        }

    def test_a_hand_pick_answers_norms_suggestion_for_that_field(self):
        # Picked what Norm proposed: the suggestion reads accepted, by hand —
        # and the value stays the person's own edit.
        data = _card()
        data["lines"][0].update(linked_unit_id="u-each", unit="Each", unit_ratio=1)
        D.decide(
            data,
            {
                "edit": {
                    "line_id": "ld-1",
                    "fields": {
                        "linked_unit_id": "u-kg",
                        "unit": "Kilo",
                        "unit_ratio": 1,
                    },
                }
            },
            by="u-1",
        )
        act = _last(data, "line_value:unit:ld-1")
        assert act["action"] == "accepted" and act["after"]["by_hand"] is True
        assert all("superseded_by" not in e for e in data["edits"])
        # The record holds what the line had before the pick, not blanks...
        assert act["before"] == {
            "linked_unit_id": "u-each",
            "unit": "Each",
            "unit_ratio": 1,
        }
        # ...and the pick is the person's own: it changes by picking again,
        # never by an Undo that would blank the line under a live edit.
        out = D.decide(data, {"undo": "line_value:unit:ld-1"}, by="u-1")
        assert out["ok"] is False and "your own pick" in out["detail"]
        assert data["lines"][0]["linked_unit_id"] == "u-kg"
        # Picked something else: Norm's suggestion still stands beside it,
        # for Accept all or Decline (owner, 20 Oct 2026).
        other = _card()
        D.decide(
            other,
            {
                "edit": {
                    "line_id": "ld-1",
                    "fields": {"linked_unit_id": "u-5", "unit": "5kg"},
                }
            },
        )
        assert _last(other, "line_value:unit:ld-1") == {}
        assert D.annotate(other)["person_can_receive"] is True
        assert other["suggestion_states"]["line_value:unit:ld-1"]["open"] is True

    def test_accept_all_takes_norms_value_and_a_confirm_never_retires_one(self):
        # A quantity typed over Norm's leaves Norm's suggestion standing:
        # Accept all takes Norm's, over the person's (owner, 20 Oct 2026).
        data = _card()
        D.decide(
            data,
            {"edit": {"line_id": "ld-2", "fields": {"quantity_received": 5}}},
            by="u-1",
        )
        sid = "line_value:quantity_received:ld-2"
        assert D.annotate(data) and data["suggestion_states"][sid]["open"] is True
        D.decide(data, {"accept": sid}, by="u-1")
        assert data["lines"][1]["quantity_received"] == 3
        qty = [e for e in data["edits"] if e["field"] == "quantity_received"]
        assert qty and all(e.get("superseded_by") == sid for e in qty)
        # An order linked by hand settles its stop; the "receive without an
        # order" confirm has nothing left to decide...
        data["issues"][1]["code"] = "po_unresolved"
        D.decide(
            data, {"edit": {"fields": {"linked_purchase_order_id": "po-9"}}}, by="u-1"
        )
        D.annotate(data)
        assert data["issue_states"]["po_missing"] == {
            "state": "settled",
            "how": "by_hand",
        }
        assert data["suggestion_states"]["confirm:po_missing"]["open"] is False
        # ...and accepting it anyway (an old "Accept all") keeps the person's
        # order: a confirm writes nothing, so it replaces nothing.
        D.decide(data, {"accept": "confirm:po_missing"}, by="u-1")
        po = [e for e in data["edits"] if e["field"] == "linked_purchase_order_id"]
        assert po and all("superseded_by" not in e for e in po)
        assert data["linked_purchase_order_id"] == "po-9"

    def test_the_switch_decides_a_kind_clash_on_a_persons_unit(self):
        # A unit the person picked that clashes in kind with the item: the
        # venue's switch decides whether Autopilot receives it, as for Norm's
        # own unit (owner, 20 Oct 2026). Their pick is never changed, and it
        # never greys out a person's Receive.
        from app.services import venue_autopilot as VA

        def card():
            data = _card()
            data["suggestions"] = [
                x for x in data["suggestions"] if x["kind"] != "add_line"
            ]
            gate = VA.RECEIVE_UNIT_KIND_MISMATCH
            data["issues"][0] = {
                "id": "unit_kind_mismatch:ld-1",
                "code": "unit_kind_mismatch",
                "line_id": "ld-1",
                "blocking": True,
                "gate": gate,
                "message": "FRIES is received by weight but counted by count",
                "fix": ["confirm:unit_kind_mismatch:ld-1"],
            }
            data["suggestions"].append(
                {
                    "id": "confirm:unit_kind_mismatch:ld-1",
                    "kind": "confirm",
                    "field": "unit",
                    "line_id": "ld-1",
                    "gate": gate,
                    "explanation": "receive it anyway",
                }
            )
            D.decide(
                data,
                {
                    "edit": {
                        "line_id": "ld-1",
                        "fields": {"linked_unit_id": "u-5", "unit": "5kg"},
                    }
                },
                by="u-1",
            )
            return data

        on = card()
        settings = VA.settings_from(
            {VA.RECEIVE_UNIT_KIND_MISMATCH: True, VA.RECEIVE_WITHOUT_PO: True}
        )
        IR.autopilot_accept(None, None, "v-1", "inv-1", on, settings)
        assert D.annotate(on)["ready"] is True
        assert on["lines"][0]["linked_unit_id"] == "u-5"

        off = card()
        settings = VA.settings_from({VA.RECEIVE_WITHOUT_PO: True})
        IR.autopilot_accept(None, None, "v-1", "inv-1", off, settings)
        receive = D.annotate(off)
        assert receive["ready"] is False, receive
        assert receive["person_can_receive"] is True, receive
        assert off["issue_states"]["unit_kind_mismatch:ld-1"]["state"] == "open"

    def test_a_create_writes_to_loaded_then_takes_the_record(self, monkeypatch):
        from app.services import invoice_catalog as catalog

        made = []

        def fake_unit(lh, name, db, *, ratio=None, unit_type=None):
            made.append((name, ratio, unit_type))
            return {"id": "u-new", "name": name, "ratio": ratio or 1}, True

        monkeypatch.setattr(catalog, "get_or_create_unit", fake_unit)
        data = _card()
        data["suggestions"].append(
            {
                "id": "create_unit:unit:ld-1",
                "kind": "create_unit",
                "field": "unit",
                "line_id": "ld-1",
                "proposed": "3x5kg",
                "payload": {"unit_name": "3x5kg"},
            }
        )
        out = D.decide(data, {"accept": "create_unit:unit:ld-1"}, lh=object())
        assert out["ok"] and out["created"] and out["did"] == "created unit '3x5kg'"
        assert data["lines"][0]["linked_unit_id"] == "u-new"
        # From the card's own form: the person's edit, with their size.
        own = _card()
        out = D.decide(
            own,
            {
                "create": {
                    "kind": "unit",
                    "line_id": "ld-1",
                    "name": "6x700ml",
                    "ratio": 4.2,
                    "unit_type": "Volume",
                }
            },
            lh=object(),
            by="u-1",
        )
        assert out["ok"] and made[-1] == ("6x700ml", 4.2, "Volume")
        assert own["lines"][0]["unit"] == "6x700ml"
        assert any(e["field"] == "linked_unit_id" for e in own["edits"])
        # Without Loaded, or when Loaded says no, it is refused — never half-made.
        assert not D.decide(
            _card(), {"create": {"kind": "unit", "line_id": "ld-1", "name": "x"}}
        )["ok"]

        def broken(*a, **k):
            raise RuntimeError("Loaded said no")

        monkeypatch.setattr(catalog, "get_or_create_unit", broken)
        out = D.decide(
            _card(),
            {"create": {"kind": "unit", "line_id": "ld-1", "name": "x"}},
            lh=object(),
        )
        assert out == {"ok": False, "detail": "Loaded said no"}

    def test_the_shared_order_of_work(self):
        data = _card()
        data["suggestions"] += [
            {
                "id": "create_item:linked_item_id:ld-1",
                "kind": "create_item",
                "line_id": "ld-1",
            },
            {"id": "create_unit:unit:ld-1", "kind": "create_unit", "line_id": "ld-1"},
            {"id": "delete_invoice", "kind": "delete_invoice"},
        ]
        ordered = D.order_decisions(
            [
                {"accept": "create_item:linked_item_id:ld-1"},
                {"decline": "strike:ld-2"},
                {"accept": "line_value:unit:ld-1"},
                {"accept": "create_unit:unit:ld-1"},
                {"accept": "add_line:rep-1"},
                {"accept": "delete_invoice"},
            ],
            data,
        )
        assert [next(iter(d.values())) for d in ordered] == [
            "delete_invoice",
            "add_line:rep-1",
            "line_value:unit:ld-1",
            "create_unit:unit:ld-1",
            "create_item:linked_item_id:ld-1",
            "strike:ld-2",
        ]


class TestReadiness:
    def test_states_and_who_can_receive(self):
        data = _card()
        out = D.annotate(data)
        assert data["confidence"] == "needs_review"
        assert data["issue_states"]["unit_missing:ld-1"] == {
            "state": "open",
            "how": None,
        }
        assert data["suggestion_states"]["line_value:unit:ld-1"] == {
            "state": "pending",
            "open": True,
        }
        # A person is stopped by what Loaded would refuse (FRIES has no
        # unit), not by the gated order stop.
        assert out["ready"] is False
        assert out["blocked_by"] == ["FRIES has no unit"]
        D.decide(data, {"accept": "line_value:unit:ld-1"})
        out = D.annotate(data)
        assert data["issue_states"]["unit_missing:ld-1"]["how"] == "fixed"
        assert out["person_can_receive"] is True and out["ready"] is False
        D.decide(data, {"accept": "confirm:po_missing"})
        assert D.annotate(data)["ready"] is True

    def test_a_declined_answer_on_a_field_still_new_keeps_its_row_open(self):
        data = _card()
        D.decide(data, {"decline": "line_value:unit:ld-1"})
        D.annotate(data)
        assert data["suggestion_states"]["line_value:unit:ld-1"] == {
            "state": "dismissed",
            "open": True,
        }
        data["lines"][0]["linked_unit_id"] = "u-x"
        D.annotate(data)
        assert data["suggestion_states"]["line_value:unit:ld-1"]["open"] is False

    def test_a_persons_unit_of_another_kind_is_a_warning_not_a_stop(self):
        data = _card()
        D.decide(
            data,
            {
                "edit": {
                    "line_id": "ld-1",
                    "fields": {"linked_unit_id": "u-kg", "unit": "Kilo"},
                }
            },
        )
        units = [
            {"id": "u-kg", "name": "Kilo", "stockUnitType": "Weight"},
            {"id": "u-each", "name": "Each", "stockUnitType": "Count"},
        ]
        catalogue = [{"id": "i-1", "countingUnitId": "u-each"}]
        warnings = D.warn_unit_kinds(data, catalogue, units)
        assert [w["line_id"] for w in warnings] == ["ld-1"]
        assert "a weight" in warnings[0]["text"] and "a count" in warnings[0]["text"]
        assert D.annotate(data)["person_can_receive"] is True
        # Norm's own unit (no hand edit) gets no warning here: the review
        # raises its stop instead.
        data["edits"] = []
        assert D.warn_unit_kinds(data, catalogue, units) == []


class TestThePatchRouteDecides:
    def test_a_decide_op_is_made_on_the_server_and_the_response_says_so(
        self, client, db_session, admin_user, admin_headers, venue
    ):
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=None,
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            sync_mode="submit",
            data=_card(),
            external_ref={"invoice_id": "inv-1", "venue_id": venue.id},
            version=1,
        )
        db_session.add(doc)
        db_session.flush()
        resp = client.patch(
            f"/api/working-documents/{doc.id}",
            json={
                "ops": [
                    {"op": "decide", "decision": "not a decision"},
                    {"op": "decide", "decision": {"accept": "line_value:unit:ld-1"}},
                    {
                        "op": "decide",
                        "decision": {
                            "edit": {
                                "line_id": "ld-2",
                                "fields": {"quantity_received": 7},
                            }
                        },
                    },
                    {"op": "decide", "decision": {"accept": "line_value:unit:ld-9"}},
                ],
                "version": 1,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [d["ok"] for d in body["decisions"]] == [False, True, True, False]
        assert body["decisions"][0]["detail"] == "not a decision"
        data = body["data"]
        assert data["lines"][0]["linked_unit_id"] == "u-kg"
        assert (
            data["lines"][1]["quantity_received"] == 7
            and data["lines"][1]["total_cost"] == 14
        )
        assert data["issue_states"]["unit_missing:ld-1"]["state"] == "settled"
        assert data["receive"]["person_can_receive"] is True
        assert [e["field"] for e in data["edits"]] == [
            "quantity_received",
            "total_cost",
        ]
        # Read back: readiness rides with the document.
        got = client.get(
            f"/api/working-documents/{doc.id}", headers=admin_headers
        ).json()
        assert got["data"]["receive"]["ready"] is False  # the order stop, gated

    def test_loaded_is_reached_only_for_a_write_or_a_unit_change(
        self, client, db_session, admin_headers, venue, monkeypatch
    ):
        # A price typed in reaches nobody; a unit picked by hand gets the
        # kind warning (which needs Loaded's lists).
        made = []

        class FakeLoaded:
            def __init__(self, *a, **k):
                made.append(a[2] if len(a) > 2 else None)

        monkeypatch.setattr(
            "app.services.received_invoice.LoadedInvoiceClient", FakeLoaded
        )
        monkeypatch.setattr(
            "app.services.invoice_po_reference.loaded_reference",
            lambda *a, **k: (
                [{"id": "i-1", "countingUnitId": "u-each"}],
                [
                    {"id": "u-kg", "name": "Kilo", "stockUnitType": "Weight"},
                    {"id": "u-each", "name": "Each", "stockUnitType": "Count"},
                ],
            ),
        )
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=None,
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            sync_mode="submit",
            data=_card(),
            external_ref={"invoice_id": "inv-1", "venue_id": venue.id},
            version=1,
        )
        db_session.add(doc)
        db_session.flush()

        def edit(version, fields):
            return client.patch(
                f"/api/working-documents/{doc.id}",
                json={
                    "ops": [
                        {
                            "op": "decide",
                            "decision": {"edit": {"line_id": "ld-1", "fields": fields}},
                        }
                    ],
                    "version": version,
                },
                headers=admin_headers,
            ).json()

        body = edit(1, {"unit_cost": 12})
        assert made == [] and not body["data"].get("warnings")
        body = edit(2, {"linked_unit_id": "u-kg", "unit": "Kilo"})
        assert len(made) == 1
        assert [w["line_id"] for w in body["data"]["warnings"]] == ["ld-1"]
