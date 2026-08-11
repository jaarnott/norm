"""The autopilot-confidence taxonomy — would autopilot have got this right?

Pure tests over injected working-document payloads: no DB, no network. The
whole feature rests on one distinction — a human who only ACCEPTED Norm's
suggestions did what autopilot would have done; a human who also typed a value
did not — so that case is pinned explicitly (see
``test_hand_edited_quantity_with_all_accepted_is_edited``).
"""

from app.services import autopilot_metrics as AM


def _line(**over):
    ln = {
        "id": "ld-1",
        "code": "PBO0.7",
        "description": "Salmon Fillet",
        "quantity_received": 4.95,
        "unit_cost": 44.4,
        "total_cost": 219.78,
        "unit": "Kilo",
        "linked_unit_id": "u-kilo",
        "unit_ratio": 1,
        "linked_item_id": "item-salmon",
        "sale_tax_rate": 0.15,
    }
    ln.update(over)
    return ln


def _doc(*, lines=None, snapshot_lines=None, suggestions=None, actions=None, **over):
    """A reviewed working document whose values match Loaded's snapshot."""
    lines = lines if lines is not None else [_line()]
    snap = snapshot_lines if snapshot_lines is not None else [_line()]
    data = {
        "reference_number": "F100",
        "linked_supplier_id": "sup-1",
        "total": 252.75,
        "tax_amount": 32.97,
        "reviewed_at": "2026-08-10T00:00:00Z",
        "confidence": "ready",
        "lines": lines,
        "suggestions": suggestions or [],
        "suggestion_actions": actions or [],
        "issues": [],
        "loaded_snapshot": {
            "reference_number": "F100",
            "linked_supplier_id": "sup-1",
            "total": 252.75,
            "tax_amount": 32.97,
            "lines": snap,
        },
    }
    data.update(over)
    return data


def _sugg(sid="line_value:quantity_received:ld-1", **over):
    s = {
        "id": sid,
        "kind": "line_value",
        "field": "quantity_received",
        "line_id": "ld-1",
        "apply": {"quantity_received": 6.0},
    }
    s.update(over)
    return s


def _act(sid, action="accepted", after=None, before=None):
    return {
        "suggestion_id": sid,
        "action": action,
        "by": "user",
        "at": "2026-08-10T01:00:00Z",
        "before": before,
        "after": after,
    }


class TestOutcomes:
    def test_all_suggestions_accepted_is_clean(self):
        s = _sugg()
        doc = _doc(
            lines=[_line(quantity_received=6.0)],
            suggestions=[s],
            actions=[_act(s["id"], after={"quantity_received": 6.0})],
        )
        out = AM.classify_outcome(doc, received=True)
        assert out["outcome"] == "clean"
        assert out["accepted_count"] == 1 and out["manual_edit_count"] == 0

    def test_one_dismissed_is_edited(self):
        s = _sugg()
        doc = _doc(suggestions=[s], actions=[_act(s["id"], action="dismissed")])
        out = AM.classify_outcome(doc, received=True)
        assert out["outcome"] == "edited" and out["dismissed_count"] == 1

    def test_pending_suggestion_is_edited(self):
        # Receiving without answering a suggestion is a silent rejection.
        doc = _doc(suggestions=[_sugg()])
        out = AM.classify_outcome(doc, received=True)
        assert out["outcome"] == "edited" and out["pending_count"] == 1

    def test_hand_edited_quantity_with_all_accepted_is_edited(self):
        # THE honesty case: every suggestion accepted, but the human also
        # retyped a value on another line. Autopilot would NOT have done that.
        s = _sugg()
        doc = _doc(
            lines=[
                _line(quantity_received=6.0),
                _line(id="ld-2", quantity_received=9.0),
            ],
            snapshot_lines=[_line(), _line(id="ld-2", quantity_received=3.0)],
            suggestions=[s],
            actions=[_act(s["id"], after={"quantity_received": 6.0})],
        )
        out = AM.classify_outcome(doc, received=True)
        assert out["outcome"] == "edited"
        assert out["detail"]["manual_fields"] == ["line:ld-2.quantity_received"]

    def test_accepted_then_tweaked_is_manual(self):
        # Accepted 6.0, then typed 7.0 — autopilot would have stopped at 6.0.
        s = _sugg()
        doc = _doc(
            lines=[_line(quantity_received=7.0)],
            suggestions=[s],
            actions=[_act(s["id"], after={"quantity_received": 6.0})],
        )
        out = AM.classify_outcome(doc, received=True)
        assert out["outcome"] == "edited"
        assert out["detail"]["manual_fields"] == ["line:ld-1.quantity_received"]

    def test_undone_accept_reads_as_pending(self):
        s = _sugg()
        doc = _doc(
            suggestions=[s],
            actions=[
                _act(s["id"], after={"quantity_received": 6.0}),
                _act(s["id"], action="undone"),
            ],
        )
        out = AM.classify_outcome(doc, received=True)
        assert out["pending_count"] == 1 and out["outcome"] == "edited"

    def test_zero_suggestions_is_no_suggestions(self):
        out = AM.classify_outcome(_doc(), received=True)
        assert out["outcome"] == "no_suggestions" and out["suggestion_count"] == 0

    def test_zero_suggestions_with_a_hand_edit_is_edited(self):
        doc = _doc(lines=[_line(unit_cost=50.0)])
        out = AM.classify_outcome(doc, received=True)
        assert out["outcome"] == "edited"
        assert out["detail"]["manual_fields"] == ["line:ld-1.unit_cost"]

    def test_never_reviewed_is_its_own_bucket(self):
        # Legacy/reset docs must not be flattered into "Norm agreed".
        out = AM.classify_outcome(_doc(reviewed_at=None), received=True)
        assert out["outcome"] == "not_reviewed"

    def test_not_received_is_dojo(self):
        out = AM.classify_outcome(_doc(), received=False)
        assert out["outcome"] == "dojo"

    def test_delete_invoice_is_excluded_from_the_counts(self):
        # Autopilot skips it, so a pending one must not mark the invoice edited.
        doc = _doc(suggestions=[_sugg("delete_invoice", kind="delete_invoice")])
        out = AM.classify_outcome(doc, received=True)
        assert out["suggestion_count"] == 0 and out["outcome"] == "no_suggestions"
        assert out["detail"]["suggestion_kinds"] == {"delete_invoice": 1}

    def test_waved_blocking_issue_is_not_a_dismissal(self):
        doc = _doc(
            issues=[
                {"id": "no_copy_attached", "code": "no_copy_attached", "blocking": True}
            ],
            actions=[_act("no_copy_attached", action="dismissed")],
        )
        out = AM.classify_outcome(doc, received=True)
        assert out["issues_waved_count"] == 1
        assert out["dismissed_count"] == 0
        assert out["outcome"] == "no_suggestions"  # not demoted


class TestManualEdits:
    def test_derived_fields_are_not_manual(self):
        # total_cost is recomputed from qty x cost; subtotal is re-derived by
        # the receive itself. Neither means a human touched anything.
        doc = _doc(lines=[_line(total_cost=999.0)], subtotal=1.0)
        assert AM.manual_edits(doc) == []

    def test_enricher_fields_are_not_manual(self):
        doc = _doc(
            lines=[
                _line(
                    item_name="SALMON FILLET",
                    quantity_ordered=10,
                    reference_cost=40.0,
                    display_code="X",
                )
            ]
        )
        assert AM.manual_edits(doc) == []

    def test_hand_struck_line_is_manual_but_an_accepted_strike_is_not(self):
        doc = _doc(lines=[_line(struck=True)])
        assert AM.manual_edits(doc) == ["line:ld-1.struck"]

        s = _sugg("strike:ld-1", kind="strike", apply={"struck": True})
        ok = _doc(
            lines=[_line(struck=True)],
            suggestions=[s],
            actions=[_act(s["id"], after={"struck": True})],
        )
        assert AM.manual_edits(ok) == []

    def test_hand_added_line_is_manual_but_an_accepted_add_line_is_not(self):
        doc = _doc(lines=[_line(), _line(id="new-1")])
        assert AM.manual_edits(doc) == ["line:new-1.added"]

        s = _sugg("add_line:new-1", kind="add_line", line_id=None, apply=None)
        ok = _doc(
            lines=[_line(), _line(id="new-1")],
            suggestions=[s],
            actions=[
                _act(s["id"], before={"added_line_id": "new-1"}, after={"added": True})
            ],
        )
        assert AM.manual_edits(ok) == []

    def test_hand_linked_item_is_manual(self):
        # Linking a stock item Norm never suggested is exactly the case where
        # autopilot would have produced a different invoice.
        doc = _doc(
            lines=[_line(linked_item_id="item-other")],
            snapshot_lines=[_line(linked_item_id=None)],
        )
        assert AM.manual_edits(doc) == ["line:ld-1.linked_item_id"]

    def test_header_edit_is_named(self):
        doc = _doc(reference_number="TYPED-1")
        assert AM.manual_edits(doc) == ["header.reference_number"]

    def test_no_snapshot_means_no_claims(self):
        doc = _doc()
        doc.pop("loaded_snapshot")
        assert AM.manual_edits(doc) == []

    def test_money_tolerance(self):
        assert AM.manual_edits(_doc(total=252.755)) == []
        assert AM.manual_edits(_doc(total=253.75)) == ["header.total"]


class TestRecorder:
    def test_never_raises_and_leaves_the_caller_alone(self, monkeypatch):
        # Loaded has already accepted the receive by the time this runs — a
        # metric bug must never surface as a failed receive.
        def boom(*a, **k):
            raise RuntimeError("db is on fire")

        monkeypatch.setattr("app.db.engine.SessionLocal", boom)
        assert (
            AM.record_receive_outcome(
                None,
                venue_id="v-1",
                invoice_id="inv-1",
                data=_doc(),
                mode="interactive",
            )
            is None
        )

    def test_a_doc_less_receive_records_nothing(self):
        # The legacy client-built path has no working document, so there is
        # nothing honest to say about it.
        assert (
            AM.record_receive_outcome(
                None, venue_id="v-1", invoice_id="inv-1", data={}, mode="interactive"
            )
            is None
        )
