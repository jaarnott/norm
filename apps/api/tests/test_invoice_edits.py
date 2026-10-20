"""A person's edits on an invoice card outlive a rebuild (services/invoice_edits).

The owner's rule (19 Oct 2026): what gets received is what Loaded holds, plus
the suggestions that were accepted, plus anything a person typed — never a
value some other code put on the card. A rebuild from Loaded (a re-review, a
chat or batch re-save, a reopen) used to keep accepted suggestions but drop a
typed quantity or a unit picked by hand, while carry_local_state copied the
old card's links across whoever had set them.
"""

import uuid

from app.db.models import WorkingDocument
from app.services import invoice_edits as IE


def _card(**over):
    data = {
        "invoice_id": "inv-1",
        "lines": [
            {
                "id": "ld-1",
                "description": "FRIES",
                "unit": "CTN",
                "quantity_received": 4,
            },
            {
                "id": "ld-2",
                "description": "SALT",
                "unit": "Each",
                "quantity_received": 1,
            },
        ],
    }
    data.update(over)
    return data


def _fresh():
    """The same invoice rebuilt from Loaded."""
    return _card()


class TestRecordingEdits:
    def test_each_field_keeps_the_value_it_replaced(self):
        data = _card()
        n = IE.record_edits(
            data,
            [{"op": "update_line", "index": 0, "fields": {"quantity_received": 6}}],
            by="u-1",
        )
        assert n == 1
        (e,) = data["edits"]
        assert (e["line_id"], e["field"], e["before"], e["after"]) == (
            "ld-1",
            "quantity_received",
            4,
            6,
        )
        assert e["by"] == "u-1" and e["seq"] == 1

    def test_an_index_is_read_before_a_removal_moves_the_lines(self):
        data = _card()
        IE.record_edits(
            data,
            [
                {"op": "remove_line", "index": 0},
                {"op": "update_line", "index": 0, "fields": {"unit": "Kilo"}},
            ],
        )
        removed, changed = data["edits"]
        assert removed == {**removed, "line_id": "ld-1", "removed": True}
        assert changed["line_id"] == "ld-2"  # index 0 after the removal

    def test_an_op_applying_or_undoing_a_suggestion_is_a_decision_not_an_edit(self):
        # Those are in suggestion_actions already; undo re-derives because
        # nothing else re-applies the undone value.
        data = _card()
        IE.record_edits(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"unit": "3x5kg"},
                    "suggestion_id": "create_unit:unit:ld-1",
                },
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"unit": "CTN"},
                    "undo_of": "create_unit:unit:ld-1",
                },
                {"op": "update_header", "fields": {"suggestion_actions": []}},
            ],
        )
        assert data["edits"] == []  # no person edit (the empty record marks the card)


class TestReplay:
    AT = "2026-10-19T09:00:00+00:00"

    def test_a_rebuild_keeps_what_the_person_did_and_nothing_else(self):
        old = _card()
        old["lines"][0]["linked_unit_id"] = "u-ghost"  # set by no one we know
        IE.record_edits(
            old,
            [
                {"op": "update_line", "line_id": "ld-2", "fields": {"struck": True}},
                {"op": "add_line", "fields": {"id": "new-1", "description": "OIL"}},
            ],
        )
        fresh = _fresh()
        carried, replayed = IE.rebuild_from_decisions(old, fresh)
        by_id = {ln["id"]: ln for ln in fresh["lines"]}
        assert (
            by_id["ld-2"]["struck"] is True and by_id["new-1"]["description"] == "OIL"
        )
        assert "linked_unit_id" not in by_id["ld-1"]  # no ghost copied across
        assert (carried, replayed) == (0, 2)
        # Replaying twice adds the line once.
        IE.replay_edits(fresh, fresh["edits"])
        assert [ln["id"] for ln in fresh["lines"]].count("new-1") == 1

    def test_the_last_change_to_a_field_stands(self):
        # Typed 5, then accepted Norm's 6: the accept wins. Accepted Norm's
        # 6, then typed 5: the typing wins.
        def card(edit_at, accept_at):
            fresh = _fresh()
            fresh["suggestions"] = [
                {
                    "id": "line_value:quantity_received:ld-1",
                    "kind": "line_value",
                    "line_id": "ld-1",
                    "apply": {"quantity_received": 6},
                }
            ]
            fresh["suggestion_actions"] = [
                {
                    "suggestion_id": "line_value:quantity_received:ld-1",
                    "action": "accepted",
                    "at": accept_at,
                }
            ]
            fresh["lines"][0]["quantity_received"] = 6  # the accept, carried
            edits = [
                {
                    "seq": 1,
                    "at": edit_at,
                    "line_id": "ld-1",
                    "field": "quantity_received",
                    "after": 5,
                }
            ]
            IE.replay_edits(fresh, edits)
            return fresh["lines"][0]["quantity_received"]

        assert card("2026-10-19T09:00:00Z", "2026-10-19T09:05:00.000Z") == 6
        assert card("2026-10-19T09:10:00Z", "2026-10-19T09:05:00.000Z") == 5

    def test_an_edit_to_a_line_loaded_no_longer_has_is_skipped(self):
        fresh = _fresh()
        n = IE.replay_edits(
            fresh,
            [
                {
                    "seq": 1,
                    "at": self.AT,
                    "line_id": "gone",
                    "field": "unit",
                    "after": "X",
                }
            ],
        )
        assert n == 0 and fresh["edits"]

    def test_a_reopen_leaves_decided_fields_alone(self):
        # "No brand", chosen by hand: the brand and its record together.
        data = _card(
            edits=[
                {"seq": 1, "at": self.AT, "line_id": "ld-1", "field": "brand"},
                {
                    "seq": 2,
                    "at": self.AT,
                    "line_id": "ld-1",
                    "field": "linked_brand_id",
                },
            ]
        )
        data["lines"][1]["brand_set"] = True
        decided = IE.decided_fields(data)
        assert ("ld-1", "brand") in decided
        assert ("ld-2", "linked_brand_id") in decided
        assert ("ld-2", "sale_tax_rate") not in decided


class TestThePatchRouteRecords:
    def test_a_web_edit_on_an_invoice_card_is_recorded(
        self, client, db_session, admin_user, admin_headers
    ):
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=None,
            doc_type="received_invoice",
            connector_name="loadedhub",
            sync_mode="submit",
            data=_card(),
            version=1,
        )
        db_session.add(doc)
        db_session.flush()
        resp = client.patch(
            f"/api/working-documents/{doc.id}",
            json={
                "ops": [
                    {
                        "op": "update_line",
                        "line_id": "ld-1",
                        "index": 0,
                        "fields": {"unit": "15 KG", "linked_unit_id": "u-15"},
                    }
                ],
                "version": 1,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        edits = resp.json()["data"]["edits"]
        assert {(e["field"], e["after"]) for e in edits} == {
            ("unit", "15 KG"),
            ("linked_unit_id", "u-15"),
        }
        assert all(e["by"] == admin_user.id for e in edits)


class TestRebuildsAreSafeToRepeat:
    """Found by the review of this build (19 Oct 2026)."""

    ADD = {
        "id": "add_line:rep-2",
        "kind": "add_line",
        "line_id": "rep-2",
        "payload": {"id": "rep-2", "description": "FREIGHT"},
    }

    def test_a_batch_save_never_adds_an_added_line_twice(self):
        # Autopilot added the line and the batch saved the card; the chat's
        # end-of-turn write then saves the same card again.
        accept = {"suggestion_id": "add_line:rep-2", "action": "accepted", "by": "norm"}
        saved = _card(suggestions=[self.ADD], suggestion_actions=[accept], edits=[])
        saved["lines"].append({"id": "rep-2", "description": "FREIGHT"})
        again = _card(suggestions=[self.ADD], suggestion_actions=[dict(accept)])
        again["lines"].append({"id": "rep-2", "description": "FREIGHT"})
        IE.rebuild_from_decisions(saved, again)
        assert [ln["id"] for ln in again["lines"]].count("rep-2") == 1

    def test_an_added_line_keeps_the_brand_made_for_it(self):
        # The line was added, its brand created; the next review links the
        # brand in the add's payload. Still the same decision.
        old = _card(
            suggestions=[
                self.ADD,
                {
                    "id": "create_brand:linked_brand_id:rep-2",
                    "kind": "create_brand",
                    "line_id": "rep-2",
                    "field": "linked_brand_id",
                    "payload": {"brand_name": "SEAFROST"},
                },
            ],
            suggestion_actions=[
                {"suggestion_id": "add_line:rep-2", "action": "accepted"},
                {
                    "suggestion_id": "create_brand:linked_brand_id:rep-2",
                    "action": "accepted",
                    "after": {"linked_brand_id": "b-sea", "brand": "SEAFROST"},
                },
            ],
            edits=[],
        )
        linked = dict(
            self.ADD,
            payload={
                **self.ADD["payload"],
                "brand": "SEAFROST",
                "linked_brand_id": "b-sea",
                "brand_set": True,
            },
        )
        fresh = _card(suggestions=[linked])
        IE.rebuild_from_decisions(old, fresh)
        (added,) = [ln for ln in fresh["lines"] if ln["id"] == "rep-2"]
        assert added["linked_brand_id"] == "b-sea"

    def test_every_card_built_from_loaded_has_a_record(self):
        from app.services.received_invoice import build_received_invoice_data

        assert build_received_invoice_data({"id": "inv-1", "lines": []})["edits"] == []


class TestCardsFromBeforeTheRecord:
    """A card from before the edit record is read once, and only for what
    nothing else explains (review of this build, 19 Oct 2026)."""

    STRIKE = {
        "id": "strike:ld-2",
        "kind": "strike",
        "line_id": "ld-2",
        "apply": {"struck": True},
    }

    def test_a_decided_strike_is_not_made_a_person_edit_and_undo_holds(self):
        old = _card(
            suggestions=[self.STRIKE],
            suggestion_actions=[{"suggestion_id": "strike:ld-2", "action": "accepted"}],
        )
        old["lines"][1]["struck"] = True
        first = _card(suggestions=[self.STRIKE])
        IE.rebuild_from_decisions(old, first)
        assert first["lines"][1]["struck"] is True  # the decision, carried
        assert first["edits"] == []  # not a person's edit, and now marked
        # The person undoes the strike; the next rebuild leaves it undone.
        first["suggestion_actions"].append(
            {"suggestion_id": "strike:ld-2", "action": "undone"}
        )
        first["lines"][1]["struck"] = False
        second = _card(suggestions=[self.STRIKE])
        IE.rebuild_from_decisions(first, second)
        assert not second["lines"][1].get("struck")

    def test_a_link_is_kept_only_when_the_card_shows_a_person_chose_it(self):
        old = _card()
        old["lines"][0].update(linked_unit_id="u-15kg", unit="15 KG", unit_ratio=15)
        ghost = _fresh()
        IE.rebuild_from_decisions(old, ghost)  # nothing shows who set it
        assert not ghost["lines"][0].get("linked_unit_id")
        chosen = _fresh()
        IE.rebuild_from_decisions(
            old,
            chosen,
            previous_ops=[
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"linked_unit_id": "u-15kg", "unit": "15 KG"},
                }
            ],
        )
        assert chosen["lines"][0]["linked_unit_id"] == "u-15kg"


class TestOneClock:
    def test_a_decision_is_ordered_by_its_arrival_at_the_server(self):
        # The device clock is minutes out; the server's arrival time decides.
        data = _card(suggestion_actions=[])
        log = [
            {"suggestion_id": "s-1", "action": "accepted", "at": "2020-01-01T00:00:00Z"}
        ]
        IE.record_edits(
            data, [{"op": "update_header", "fields": {"suggestion_actions": log}}]
        )
        assert log[0]["srv_at"] > "2026"
        # Already known: its arrival time is kept, not restamped.
        data["suggestion_actions"] = [dict(log[0])]
        again = [dict(log[0], srv_at=None)]
        again[0].pop("srv_at")
        IE.record_edits(
            data, [{"op": "update_header", "fields": {"suggestion_actions": again}}]
        )
        assert again[0]["srv_at"] == log[0]["srv_at"]


class TestSecondReview:
    """Found by the second review of this build (19 Oct 2026)."""

    def test_a_first_edit_after_the_deploy_keeps_earlier_hand_work(self):
        # A card open at deploy time: a line struck and a line added by hand,
        # no record yet. The person types a quantity; the next rebuild must
        # keep all three.
        old = _card()
        old["lines"][1]["struck"] = True
        old["lines"].append({"id": "new-1", "description": "OIL"})
        IE.record_edits(
            old,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"quantity_received": 6},
                }
            ],
        )
        fresh = _fresh()
        IE.rebuild_from_decisions(old, fresh)
        by_id = {ln["id"]: ln for ln in fresh["lines"]}
        assert by_id["ld-2"]["struck"] is True
        assert "new-1" in by_id
        assert by_id["ld-1"]["quantity_received"] == 6

    def test_undo_after_typing_over_an_accept_shows_what_the_card_shows(self):
        # Accept 6, type 5, Undo: the card writes back Loaded's 4 — and so
        # does the next rebuild (the typed 5 went with the undone decision).
        data = _card(
            suggestions=[
                {
                    "id": "line_value:quantity_received:ld-1",
                    "kind": "line_value",
                    "line_id": "ld-1",
                    "apply": {"quantity_received": 6},
                }
            ],
            suggestion_actions=[
                {
                    "suggestion_id": "line_value:quantity_received:ld-1",
                    "action": "accepted",
                    "at": "2026-10-19T09:00:00+00:00",
                    "before": {"quantity_received": 4},
                    "after": {"quantity_received": 6},
                }
            ],
            edits=[],
        )
        IE.record_edits(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"quantity_received": 5},
                }
            ],
        )
        log = data["suggestion_actions"] + [
            {"suggestion_id": "line_value:quantity_received:ld-1", "action": "undone"}
        ]
        IE.record_edits(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"quantity_received": 4},
                    "undo_of": "line_value:quantity_received:ld-1",
                },
                {"op": "update_header", "fields": {"suggestion_actions": log}},
            ],
        )
        data["suggestion_actions"] = log
        fresh = _fresh()
        fresh["suggestions"] = data["suggestions"]
        IE.rebuild_from_decisions(data, fresh)
        assert fresh["lines"][0]["quantity_received"] == 4

    def test_an_old_tabs_untagged_accept_is_a_decision(self):
        # A tab still running the old card sends its accept untagged, with
        # the accept in the same patch: it isn't kept as the person's edit.
        sug = {
            "id": "line_value:unit:ld-1",
            "kind": "line_value",
            "line_id": "ld-1",
            "apply": {"unit": "15 KG", "linked_unit_id": "u-15"},
        }
        data = _card(suggestions=[sug], suggestion_actions=[], edits=[])
        IE.record_edits(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"unit": "15 KG", "linked_unit_id": "u-15"},
                },
                {
                    "op": "update_header",
                    "fields": {
                        "suggestion_actions": [
                            {
                                "suggestion_id": "line_value:unit:ld-1",
                                "action": "accepted",
                                "after": {"unit": "15 KG", "linked_unit_id": "u-15"},
                            }
                        ]
                    },
                },
            ],
        )
        assert data["edits"] == []

    def test_a_hand_pick_stays_the_persons_when_norms_answer_changes(self):
        # Picked 3x5kg by hand, which was also Norm's answer then (answer_of).
        # The next review proposes 5kg: the person's pick stands.
        data = _card(edits=[])
        IE.record_edits(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"unit": "3x5kg", "linked_unit_id": "u-35"},
                    "answer_of": "line_value:unit:ld-1",
                }
            ],
        )
        fresh = _fresh()
        fresh["suggestions"] = [
            {
                "id": "line_value:unit:ld-1",
                "kind": "line_value",
                "line_id": "ld-1",
                "apply": {"unit": "5kg", "linked_unit_id": "u-5"},
            }
        ]
        IE.rebuild_from_decisions(data, fresh)
        assert fresh["lines"][0]["linked_unit_id"] == "u-35"

    def test_on_an_old_card_a_persons_later_override_and_restore_stand(self):
        unit = {
            "id": "line_value:unit:ld-1",
            "kind": "line_value",
            "line_id": "ld-1",
            "apply": {"unit": "15 KG", "linked_unit_id": "u-15"},
        }
        strike = {
            "id": "strike:ld-2",
            "kind": "strike",
            "line_id": "ld-2",
            "apply": {"struck": True},
        }
        old = _card(
            suggestions=[unit, strike],
            suggestion_actions=[
                {"suggestion_id": unit["id"], "action": "accepted"},
                {"suggestion_id": strike["id"], "action": "accepted"},
            ],
        )
        # Accepted Norm's 15 KG, then picked Kilo by hand; accepted the
        # strike, then pressed Restore.
        old["lines"][0].update(unit="Kilo", linked_unit_id="u-kilo")
        ops = [
            {
                "op": "update_line",
                "line_id": "ld-1",
                "fields": {"unit": "Kilo", "linked_unit_id": "u-kilo"},
            }
        ]
        fresh = _card(suggestions=[unit, strike])
        IE.rebuild_from_decisions(old, fresh, previous_ops=ops)
        assert fresh["lines"][0]["linked_unit_id"] == "u-kilo"
        assert not fresh["lines"][1].get("struck")

    def test_a_decision_whose_suggestion_was_cleared_is_still_a_decision(self):
        # Norm's strike was accepted; the suggestions were cleared since. The
        # strike is not read as the person's own edit.
        old = _card(
            suggestions=[],
            suggestion_actions=[{"suggestion_id": "strike:ld-2", "action": "accepted"}],
        )
        old["lines"][1]["struck"] = True
        assert IE.legacy_edits(old, _fresh()) == []


def _apply(data, ops, history=None):
    """What the PATCH route does: record the person's edits, then apply."""
    from app.routers.working_documents import _apply_op

    IE.record_edits(data, ops, history=history)
    for op in ops:
        data = _apply_op(data, op)
    return data


def _log(data, *entries):
    log = list(data.get("suggestion_actions") or []) + list(entries)
    return {"op": "update_header", "fields": {"suggestion_actions": log}}


UNIT_MISSING = {
    "id": "unit_missing:ld-1",
    "code": "unit_missing",
    "line_id": "ld-1",
    "blocking": True,
    "message": "no unit",
}


def _reviewed(**over):
    from app.services.invoice_review import DOC_SCHEMA

    return _card(doc_schema=DOC_SCHEMA, issues=[dict(UNIT_MISSING)], **over)


def _unit(name, uid, ratio=1):
    return {"linked_unit_id": uid, "unit": name, "unit_ratio": ratio}


class TestThirdReview:
    """Found by the third review of this build (19 Oct 2026)."""

    def test_an_old_cards_blocker_accept_is_the_persons_own_pick(self):
        # The old card logged a unit picked by hand as an accept of the
        # blocker: it is the person's pick, not one of Norm's decisions.
        old = _reviewed(
            suggestions=[],
            suggestion_actions=[
                {
                    "suggestion_id": "unit_missing:ld-1",
                    "action": "accepted",
                    "after": _unit("10 KG", "u-10", 10),
                }
            ],
        )
        old["lines"][0].update(_unit("10 KG", "u-10", 10))
        history = [
            {
                "op": "update_line",
                "line_id": "ld-1",
                "fields": _unit("10 KG", "u-10", 10),
            }
        ]
        fresh = _reviewed(suggestions=[])
        IE.rebuild_from_decisions(old, fresh, previous_ops=history)
        assert fresh["lines"][0]["linked_unit_id"] == "u-10"
        assert fresh["confidence"] == "ready"
        # The old chat re-save emptied the history but kept the record: the
        # blocker's accept, with the pick the line still shows, is proof.
        fresh = _reviewed(suggestions=[])
        IE.rebuild_from_decisions(old, fresh, previous_ops=[])
        assert fresh["lines"][0]["linked_unit_id"] == "u-10"

    def test_a_blocker_answered_by_hand_stays_answered(self):
        # Picked A (the blocker recorded), then B; and a pick that was also
        # Norm's answer (answer_of), after Norm's answer changed. Either way
        # the person decided, so a rebuild keeps the blocker settled.
        data = _reviewed(suggestions=[], suggestion_actions=[], edits=[])
        pick_a = {
            "suggestion_id": "unit_missing:ld-1",
            "action": "accepted",
            "after": _unit("A", "u-a"),
        }
        data = _apply(
            data,
            [
                {"op": "update_line", "line_id": "ld-1", "fields": _unit("A", "u-a")},
                _log(data, pick_a),
            ],
        )
        data = _apply(
            data,
            [{"op": "update_line", "line_id": "ld-1", "fields": _unit("B", "u-b", 2)}],
        )
        fresh = _reviewed(suggestions=[])
        IE.rebuild_from_decisions(data, fresh)
        assert fresh["lines"][0]["linked_unit_id"] == "u-b"
        assert fresh["confidence"] == "ready"

        answer = _reviewed(suggestions=[], suggestion_actions=[], edits=[])
        answer = _apply(
            answer,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": _unit("A", "u-a"),
                    "answer_of": "line_value:unit:ld-1",
                }
            ],
        )
        fresh = _reviewed(
            suggestions=[
                {
                    "id": "line_value:unit:ld-1",
                    "kind": "line_value",
                    "line_id": "ld-1",
                    "field": "unit",
                    "apply": _unit("C", "u-c"),
                }
            ]
        )
        fresh["issues"][0]["fix"] = ["line_value:unit:ld-1"]
        IE.rebuild_from_decisions(answer, fresh)
        assert fresh["lines"][0]["linked_unit_id"] == "u-a"
        assert fresh["confidence"] == "ready"
        # Only while the line holds a unit: no unit is no answer.
        fresh["lines"][0]["linked_unit_id"] = None
        assert not IE.hand_settles(fresh, fresh["issues"][0])

    def test_an_old_tabs_accept_and_undo_are_decisions(self):
        # The old card sends a quantity accept with the total it works out,
        # and an Undo of an added line as a bare removal. Neither is the
        # person's own edit.
        qty = {
            "id": "line_value:quantity_received:ld-1",
            "kind": "line_value",
            "line_id": "ld-1",
            "apply": {"quantity_received": 6},
        }
        add = {
            "id": "add_line:rep-9",
            "kind": "add_line",
            "line_id": "rep-9",
            "payload": {"id": "rep-9", "description": "OIL"},
        }
        data = _card(suggestions=[qty, add], suggestion_actions=[], edits=[])
        accepted = {
            "suggestion_id": qty["id"],
            "action": "accepted",
            "at": "2026-10-19T09:00:00Z",
            "after": {"quantity_received": 6},
        }
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "index": 0,
                    "fields": {"quantity_received": 6, "total_cost": 60},
                },
                _log(data, accepted),
            ],
        )
        added = {
            "suggestion_id": add["id"],
            "action": "accepted",
            "at": "2026-10-19T09:01:00Z",
        }
        data = _apply(
            data,
            [{"op": "add_line", "fields": dict(add["payload"])}, _log(data, added)],
        )
        undone = {
            "suggestion_id": add["id"],
            "action": "undone",
            "at": "2026-10-19T09:02:00Z",
        }
        data = _apply(data, [{"op": "remove_line", "index": 2}, _log(data, undone)])
        assert data["edits"] == []

    def test_undoing_an_added_line_takes_its_edits_with_it(self):
        add = {
            "id": "add_line:rep-3",
            "kind": "add_line",
            "line_id": "rep-3",
            "payload": {"id": "rep-3", "description": "OIL", "quantity_received": 1},
        }
        data = _card(suggestions=[add], suggestion_actions=[], edits=[])
        accept = {
            "suggestion_id": add["id"],
            "action": "accepted",
            "before": {"added_line_id": "rep-3"},
        }
        data = _apply(
            data,
            [
                {
                    "op": "add_line",
                    "fields": dict(add["payload"]),
                    "suggestion_id": add["id"],
                },
                _log(data, accept),
            ],
        )
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "rep-3",
                    "fields": {"quantity_received": 2},
                }
            ],
        )
        undo = {"suggestion_id": add["id"], "action": "undone"}
        data = _apply(
            data,
            [{"op": "remove_line", "index": 2, "undo_of": add["id"]}, _log(data, undo)],
        )
        assert IE.hand_fields(data) == set() and all(
            e.get("undone_at") for e in data["edits"]
        )

    def test_an_old_card_keeps_what_was_typed_before_an_accept_for_its_undo(self):
        # Typed 3, then accepted Norm's 6, on a card from before the record.
        # Undo after the deploy writes 3 back: the next rebuild must agree.
        sug = {
            "id": "line_value:quantity_received:ld-1",
            "kind": "line_value",
            "line_id": "ld-1",
            "apply": {"quantity_received": 6},
        }
        accepted = {
            "suggestion_id": sug["id"],
            "action": "accepted",
            "at": "2026-10-19T09:00:00Z",
            "before": {"quantity_received": 3},
            "after": {"quantity_received": 6},
        }
        old = _card(suggestions=[sug], suggestion_actions=[accepted])
        old["loaded_snapshot"] = {"lines": [dict(ln) for ln in _card()["lines"]]}
        old["lines"][0]["quantity_received"] = 6
        typed = [{"op": "update_line", "index": 0, "fields": {"quantity_received": 3}}]
        undo = {"suggestion_id": sug["id"], "action": "undone"}
        old = _apply(
            old,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"quantity_received": 3},
                    "undo_of": sug["id"],
                },
                _log(old, undo),
            ],
            history=typed,
        )
        assert old["lines"][0]["quantity_received"] == 3
        fresh = _card(suggestions=[sug])
        IE.rebuild_from_decisions(old, fresh)
        assert fresh["lines"][0]["quantity_received"] == 3

    def test_a_value_given_up_for_norms_answer_does_not_come_back(self):
        # Typed 5, then accepted Norm's 4.95. The next review proposes 4.9:
        # the accept isn't carried, and the abandoned 5 doesn't return.
        sug = {
            "id": "line_value:quantity_received:ld-1",
            "kind": "line_value",
            "line_id": "ld-1",
            "apply": {"quantity_received": 4.95},
        }
        data = _card(suggestions=[sug], suggestion_actions=[], edits=[])
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"quantity_received": 5},
                }
            ],
        )
        accept = {
            "suggestion_id": sug["id"],
            "action": "accepted",
            "before": {"quantity_received": 5},
        }
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"quantity_received": 4.95},
                    "suggestion_id": sug["id"],
                },
                _log(data, accept),
            ],
        )
        fresh = _card(suggestions=[{**sug, "apply": {"quantity_received": 4.9}}])
        IE.rebuild_from_decisions(data, fresh)
        assert fresh["lines"][0]["quantity_received"] == 4  # Loaded's; 4.9 pending
        assert IE.hand_fields(fresh) == set()

    def test_an_edit_on_an_added_line_stays_with_that_invoice_line(self):
        data = _card(edits=[])
        data["lines"].append(
            {"id": "rep-2", "description": "LEMONS 2KG", "quantity_received": 2}
        )
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "rep-2",
                    "fields": {"quantity_received": 3},
                }
            ],
        )
        fresh = _card()
        fresh["lines"].append(
            {"id": "rep-2", "description": "LIMES 1KG", "quantity_received": 1}
        )
        IE.replay_edits(fresh, data["edits"])
        assert fresh["lines"][2]["quantity_received"] == 1
        fresh["lines"][2]["description"] = "LEMONS 2KG"
        IE.replay_edits(fresh, data["edits"])
        assert fresh["lines"][2]["quantity_received"] == 3

    def test_reading_an_old_card_twice_keeps_a_line_added_by_hand(self):
        # The batch rebuilds from the old card, then its save rebuilds from
        # the same old card again, onto a payload that already has the line.
        old = _card()
        old["lines"][1]["struck"] = True
        old["lines"].append({"id": "new-1", "description": "OIL"})
        batch = _fresh()
        IE.rebuild_from_decisions(old, batch)
        IE.rebuild_from_decisions(old, batch)
        assert any(e.get("added") for e in batch["edits"])
        again = _fresh()
        IE.rebuild_from_decisions(batch, again)
        assert "new-1" in {ln["id"] for ln in again["lines"]}

    def test_an_undo_takes_back_the_whole_brand_choice(self):
        # A card that leaves brand_set out of what it writes back: the
        # server still cancels everything the choice set.
        data = _card(
            suggestions=[
                {
                    "id": "line_value:linked_brand_id:ld-1",
                    "kind": "line_value",
                    "line_id": "ld-1",
                    "field": "linked_brand_id",
                    "apply": {
                        "linked_brand_id": "b1",
                        "brand": "McCain",
                        "brand_set": True,
                    },
                }
            ],
            suggestion_actions=[],
            edits=[],
        )
        sid = "line_value:linked_brand_id:ld-1"
        chose = {"linked_brand_id": "b1", "brand": "McCain", "brand_set": True}
        accept = {
            "suggestion_id": sid,
            "action": "accepted",
            "before": {"linked_brand_id": None, "brand": None},
        }
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": chose,
                    "answer_of": sid,
                },
                _log(data, accept),
            ],
        )
        undo = {"suggestion_id": sid, "action": "undone"}
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"linked_brand_id": None, "brand": None},
                    "undo_of": sid,
                },
                _log(data, undo),
            ],
        )
        assert [e for e in data["edits"] if IE._live(e)] == []

    def test_a_plain_re_read_keeps_the_old_review(self):
        from app.services.received_invoice import build_received_invoice_data

        old = _reviewed(suggestions=[], suggestion_actions=[], edits=[])
        fresh = build_received_invoice_data({"id": "inv-1", "lines": []})
        IE.rebuild_from_decisions(old, fresh)  # no error
        assert fresh["doc_schema"] == old["doc_schema"]


KILO = {"linked_unit_id": "u-kilo", "unit": "Kilo", "unit_ratio": 1}


def _on(line_id, description, values, seq=1):
    """A person's edits on a line, as the record keeps them."""
    return [
        {
            "seq": seq + n,
            "at": "2026-10-19T09:00:00+00:00",
            "line_id": line_id,
            "field": f,
            "after": v,
            "line_key": {"code": None, "description": description},
        }
        for n, (f, v) in enumerate(values.items())
    ]


class TestFourthReview:
    """Found by the fourth review of this build (19 Oct 2026)."""

    def test_a_persons_value_counts_on_its_own_invoice_line_by_its_record(self):
        # Kilo picked on LEMONS (rep-2). Tonight rep-2 is LIMES, read as Each
        # with the same ratio: that is neither the person's line nor their
        # unit, so it settles nothing and Autopilot is free to answer it.
        each = {"linked_unit_id": "u-each", "unit": "Each", "unit_ratio": 1}
        data = {
            "lines": [{"id": "rep-2", "description": "LIMES", **each}],
            "edits": _on("rep-2", "LEMONS", KILO),
        }
        issue = {"code": "unit_unconfirmed", "line_id": "rep-2"}
        assert IE.hand_fields(data) == set() and not IE.hand_settles(data, issue)
        data["lines"][0]["description"] = "LEMONS"  # the same line, Each
        assert IE.hand_fields(data) == set()  # a matching ratio isn't the unit
        data["lines"][0].update(KILO)
        assert IE.hand_settles(data, issue)
        # A kind mismatch is between the unit and the item: a unit picked
        # before Norm's item answer made one is no decision about it.
        kind = {"code": "unit_kind_mismatch", "line_id": "rep-2"}
        assert not IE.hand_settles(data, kind)
        data["lines"][0]["linked_item_id"] = "i-kg"
        data["edits"] += _on("rep-2", "LEMONS", {"linked_item_id": "i-kg"}, seq=9)
        assert IE.hand_settles(data, kind)
        # A unit name sent without its record (a chat could) is not an answer.
        data["edits"] = _on("rep-2", "LEMONS", {"unit": "Kilo"})
        assert not IE.hand_settles(data, issue)

    def test_a_supplier_picked_by_hand_answers_it_but_another_deliverys_order_does_not(
        self,
    ):
        data = {
            "lines": [],
            "linked_supplier_id": "s-city",
            "edits": [{"seq": 1, "field": "linked_supplier_id", "after": "s-city"}],
        }
        assert IE.hand_settles(data, {"code": "supplier_unresolved", "line_id": None})
        data.update(
            linked_purchase_order_id="po-9",
            edits=[{"seq": 1, "field": "linked_purchase_order_id", "after": "po-9"}],
        )
        assert IE.hand_settles(data, {"code": "po_unresolved", "line_id": None})
        assert not IE.hand_settles(data, {"code": "po_split_order", "line_id": None})

    def test_a_decision_about_another_invoice_line_under_the_same_id_is_dropped(self):
        previous = {"lines": [{"id": "rep-2", "description": "LEMONS"}]}

        def fresh(description):
            return {
                "lines": [],
                "suggestions": [
                    {
                        "id": "add_line:rep-2",
                        "kind": "add_line",
                        "line_id": "rep-2",
                        "payload": {"id": "rep-2", "description": description},
                    }
                ],
                "issues": [
                    {
                        "id": "unit_unconfirmed:rep-2",
                        "code": "unit_unconfirmed",
                        "line_id": "rep-2",
                    }
                ],
                "suggestion_actions": [
                    {"suggestion_id": "unit_unconfirmed:rep-2", "action": "dismissed"}
                ],
            }

        limes = fresh("LIMES")
        IE.drop_stale_blocker_accepts(limes, 0, previous)
        assert limes["suggestion_actions"] == []
        lemons = fresh("LEMONS")
        IE.drop_stale_blocker_accepts(lemons, 0, previous)
        assert lemons["suggestion_actions"] != []

    def test_undoing_a_create_carried_as_its_link_brings_back_the_pick(self):
        create = {
            "id": "create_unit:unit:ld-1",
            "kind": "create_unit",
            "field": "unit",
            "line_id": "ld-1",
            "proposed": "3x5kg",
        }
        made = {"linked_unit_id": "u-35", "unit": "3x5kg", "unit_ratio": 15}
        data = _card(suggestions=[create], suggestion_actions=[], edits=[])
        data = _apply(data, [{"op": "update_line", "line_id": "ld-1", "fields": KILO}])
        accept = {
            "suggestion_id": create["id"],
            "action": "accepted",
            "before": KILO,
            "after": {**made, "created": True},
        }
        data = _apply(
            data,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": made,
                    "suggestion_id": create["id"],
                },
                _log(data, accept),
            ],
        )
        link = {
            "id": "line_value:unit:ld-1",
            "kind": "line_value",
            "field": "unit",
            "line_id": "ld-1",
            "apply": made,
        }
        card = _card(suggestions=[link])
        IE.rebuild_from_decisions(data, card)
        assert card["lines"][0]["linked_unit_id"] == "u-35"
        undo = {"suggestion_id": link["id"], "action": "undone"}
        card = _apply(
            card,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": KILO,
                    "undo_of": link["id"],
                },
                _log(card, undo),
            ],
        )
        again = _card(suggestions=[link])
        IE.rebuild_from_decisions(card, again)
        assert again["lines"][0]["linked_unit_id"] == "u-kilo"

    def test_accepting_an_added_line_again_replaces_what_was_typed_on_it(self):
        add = {
            "id": "add_line:rep-3",
            "kind": "add_line",
            "line_id": "rep-3",
            "payload": {"id": "rep-3", "description": "OIL", "quantity_received": 1},
        }
        # Typed 2 on OIL when an earlier review added it; this review's add
        # is a new question, so the card no longer has the line.
        data = _card(
            suggestions=[add],
            suggestion_actions=[],
            edits=_on("rep-3", "OIL", {"quantity_received": 2}),
        )
        accept = {"suggestion_id": add["id"], "action": "accepted"}
        data = _apply(
            data,
            [
                {
                    "op": "add_line",
                    "fields": dict(add["payload"]),
                    "suggestion_id": add["id"],
                },
                _log(data, accept),
            ],
        )
        fresh = _card(suggestions=[add])
        IE.rebuild_from_decisions(data, fresh)
        assert fresh["lines"][2]["quantity_received"] == 1  # as the card shows

    def test_an_old_cards_work_on_a_line_norm_added_stays_with_that_line(self):
        add = {
            "id": "add_line:rep-2",
            "kind": "add_line",
            "line_id": "rep-2",
            "payload": {
                "id": "rep-2",
                "description": "FREIGHT",
                "brand": "Mainfreight",
                "linked_brand_id": "b-mf",
                "brand_set": True,
            },
        }
        old = _card(suggestions=[add])
        old["lines"].append({**add["payload"], "struck": True})
        # Read even though this rebuild hasn't added the line: Autopilot may.
        # Norm's own add (its brand) is not the person's.
        edits = IE.legacy_edits(old, _fresh())
        assert [
            (e["line_id"], e["field"], e["line_key"]["description"]) for e in edits
        ] == [("rep-2", "struck", "FREIGHT")]

    def test_an_old_card_keeps_a_brand_chosen_before_an_accept_for_its_undo(self):
        sug = {
            "id": "line_value:linked_brand_id:ld-1",
            "kind": "line_value",
            "line_id": "ld-1",
            "field": "linked_brand_id",
            "apply": {"brand": "McCain", "linked_brand_id": "b1", "brand_set": True},
        }
        chose = {"brand": "Edgell", "linked_brand_id": "b2", "brand_set": True}
        accepted = {
            "suggestion_id": sug["id"],
            "action": "accepted",
            "at": "2026-10-19T09:00:00Z",
            "before": {"brand": "Edgell", "linked_brand_id": "b2"},
            "after": sug["apply"],
        }
        old = _card(suggestions=[sug], suggestion_actions=[accepted])
        old["loaded_snapshot"] = {"lines": [dict(ln) for ln in _card()["lines"]]}
        old["lines"][0].update(sug["apply"])
        undo = {"suggestion_id": sug["id"], "action": "undone"}
        old = _apply(
            old,
            [
                {
                    "op": "update_line",
                    "line_id": "ld-1",
                    "fields": {"brand": "Edgell", "linked_brand_id": "b2"},
                    "undo_of": sug["id"],
                },
                _log(old, undo),
            ],
            history=[{"op": "update_line", "line_id": "ld-1", "fields": chose}],
        )
        fresh = _card(suggestions=[sug])
        IE.rebuild_from_decisions(old, fresh)
        assert fresh["lines"][0]["linked_brand_id"] == "b2"


class TestFifthReview:
    """Found by the fifth review of this build (19 Oct 2026)."""

    def test_checking_off_a_blocker_only_asking_again_can_answer_settles_nothing(self):
        # The card's Accept all marks every row without a fix "checked".
        from app.services import invoice_review as IR

        data = _reviewed(suggestions=[], edits=[])
        data["issues"] = [
            {
                "id": "unit_answer_missing:ld-1",
                "code": "unit_answer_missing",
                "line_id": "ld-1",
                "blocking": True,
                "fix": [],
            }
        ]
        data["suggestion_actions"] = [
            {
                "suggestion_id": "unit_answer_missing:ld-1",
                "action": "accepted",
                "by": "user",
            }
        ]
        assert IR.compute_confidence(data) == "needs_review"
        data["lines"][0].update(KILO)  # a unit the person picks does answer it
        data["edits"] = _on("ld-1", "FRIES", KILO)
        assert IR.compute_confidence(data) == "ready"

    def test_an_old_cards_strike_norm_proposed_needs_proof_it_was_a_person(self):
        # The old batch save kept Autopilot's strike but not its decision.
        strike = {
            "id": "strike:ld-2",
            "kind": "strike",
            "line_id": "ld-2",
            "apply": {"struck": True},
        }
        old = _card(suggestions=[strike], suggestion_actions=[])
        old["lines"][1]["struck"] = True
        assert IE.legacy_edits(old, _fresh()) == []
        typed = [{"op": "update_line", "line_id": "ld-2", "fields": {"struck": True}}]
        assert [e["field"] for e in IE.legacy_edits(old, _fresh(), typed)] == ["struck"]
