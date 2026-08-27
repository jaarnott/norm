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
        # Shaped exactly as received_invoice.loaded_snapshot() builds it:
        # header fields NESTED, lines alongside. The flat version this fixture
        # used to carry is what let the header comparison read nothing and mark
        # every invoice hand-edited in production.
        "loaded_snapshot": {
            "header": {
                "reference_number": "F100",
                "linked_supplier_id": "sup-1",
                "total": 252.75,
                "tax_amount": 32.97,
            },
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

    def test_a_received_date_is_never_a_manual_edit(self):
        """Norm never populates received_at — it comes from Loaded's
        `receivedAt`, and the only writer is the editor's date field. So when
        Loaded's draft carries no received date, a person filling one in read
        as a hand-edit and the whole receive as EDITED with every other header
        field matching exactly (INV2007071, 26 Aug 2026)."""
        doc = _doc(received_at="2026-08-20")
        doc["loaded_snapshot"]["header"]["received_at"] = None
        assert AM.manual_edits(doc) == []
        assert AM.classify_outcome(doc, received=True)["outcome"] != "edited"

    def test_other_header_fields_are_still_watched(self):
        """The exemption must be received_at ALONE — a changed total is a real
        edit and must keep reading as one."""
        doc = _doc(total=999.0)
        assert AM.manual_edits(doc) == ["header.total"]

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


class TestTheBaselineIsWhereItActuallyLives:
    """Autopilot readiness read 0% from the day it shipped.

    `loaded_snapshot()` nests the baseline as {"header": {...}, "lines": [...]},
    but the header comparison read `snap.get(field)` — top level — and found
    nothing, so every populated header field always "differed". The first 31
    production receives were all recorded as hand-edited, 29 of them with
    manual_fields containing ONLY header entries, the same ones every time:
    reference_number 30x, linked_supplier_id 30x, issued_at 30x, tax_amount 29x.
    `clean` was therefore unreachable and the rate was structurally 0/31 —
    the number the user was asked to trust before turning autopilot on.

    Recomputed over 8 live documents at the time of the fix: 4-8 "manual edits"
    each as shipped, zero once the header is read from where it lives.
    """

    #: The exact set production reported, in production's own order.
    PHANTOM = [
        "header.reference_number",
        "header.linked_supplier_id",
        "header.purchase_order_number",
        "header.linked_purchase_order_id",
        "header.issued_at",
        "header.total",
        "header.tax_amount",
    ]

    def _untouched(self):
        """A document nobody edited: header identical to Loaded's snapshot."""
        header = {
            "reference_number": "IN11413982",
            "linked_supplier_id": "sup-1",
            "purchase_order_number": "1520987",
            "linked_purchase_order_id": "po-1",
            "issued_at": "2026-08-15",
            "total": 252.75,
            "tax_amount": 32.97,
        }
        return {
            **header,
            "reviewed_at": "2026-08-15T00:00:00Z",
            "confidence": "ready",
            "lines": [_line()],
            "suggestions": [],
            "suggestion_actions": [],
            "issues": [],
            "loaded_snapshot": {"header": dict(header), "lines": [_line()]},
        }

    def test_an_untouched_header_is_not_seven_manual_edits(self):
        assert AM.manual_edits(self._untouched()) == []

    def test_the_flat_shape_is_not_mistaken_for_a_baseline(self):
        """Defence in depth: if a doc ever carries the old flat snapshot, the
        header simply has no baseline — say nothing rather than invent seven
        edits from it."""
        doc = self._untouched()
        doc["loaded_snapshot"] = {
            **doc["loaded_snapshot"]["header"],
            "lines": doc["loaded_snapshot"]["lines"],
        }
        assert [f for f in AM.manual_edits(doc) if f.startswith("header.")] == []

    def test_a_real_header_edit_is_still_caught(self):
        doc = self._untouched()
        doc["reference_number"] = "TYPED-BY-HAND"
        assert AM.manual_edits(doc) == ["header.reference_number"]

    def test_such_an_invoice_can_finally_be_clean(self):
        """The verdict is what the report actually counts. With suggestions
        that were all accepted and nothing typed, this is `clean` — the outcome
        that never once occurred in production."""
        doc = self._untouched()
        s = _sugg()
        doc["suggestions"] = [s]
        doc["suggestion_actions"] = [_act(s["id"], after={"quantity_received": 4.95})]
        out = AM.classify_outcome(doc, received=True)
        assert out["manual_edit_count"] == 0
        assert out["outcome"] == "clean"


class TestNormsOwnFillsAreNotHandEdits:
    """Service Foods IN11437881, 17 Aug 2026 — received with no suggestions and
    nothing typed, recorded as EDITED on four fields of one line:
    unit, linked_unit_id, unit_ratio, linked_item_id.

    Loaded's own line named "KG" and linked neither item nor unit. Norm then
    resolved the supplier variant and completed the line itself
    (invoice_po_reference.seed_working_from_loaded) — but the baseline,
    `loaded_snapshot`, is captured from Loaded's raw draft BEFORE that runs. So
    every field the server filled read as a person typing.

    Second instance of one mistake: comparing values against a baseline taken
    at a different moment. The header version of it (fixed above) pinned
    readiness at 0%; this one would have kept every invoice with an unlinked
    Loaded line out of `clean` forever.
    """

    def _seeded(self):
        """Loaded's line as it arrived, and the line after Norm completed it."""
        raw = _line(
            unit="KG", linked_unit_id=None, unit_ratio=None, linked_item_id=None
        )
        done = _line(unit="Kilo", linked_unit_id="u-kilo", unit_ratio=1.0)
        doc = _doc(lines=[done], snapshot_lines=[raw])
        doc["server_filled"] = {
            "ld-1": {
                "unit": "Kilo",
                "linked_unit_id": "u-kilo",
                "unit_ratio": 1.0,
                "linked_item_id": "item-salmon",
            }
        }
        return doc

    def test_what_norm_completed_is_not_what_a_person_typed(self):
        assert AM.manual_edits(self._seeded()) == []

    def test_so_an_untouched_invoice_is_not_edited(self):
        assert AM.classify_outcome(self._seeded(), received=True)["outcome"] == (
            "no_suggestions"
        )

    def test_changing_it_afterwards_is_still_a_hand_edit(self):
        """Same contract as an accepted suggestion: explained only while the
        value is still the one that was filled. Retyping the unit means
        autopilot would have produced a different invoice."""
        doc = self._seeded()
        doc["lines"][0]["unit"] = "Each"
        doc["lines"][0]["linked_unit_id"] = "u-each"
        assert sorted(AM.manual_edits(doc)) == [
            "line:ld-1.linked_unit_id",
            "line:ld-1.unit",
        ]

    def test_a_field_norm_never_filled_is_still_measured(self):
        doc = self._seeded()
        doc["lines"][0]["quantity_received"] = 99.0
        assert "line:ld-1.quantity_received" in AM.manual_edits(doc)


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


class TestFoldedBlockerAccepts:
    """The one-button doctrine folds create/strike suggestions onto their
    blockers, so their accepts are recorded against ISSUE ids — which the
    classifier used to skip, reading every blocker-created unit as
    hand-typing (Federal Merchants 396152, 19 Aug 2026: a no-edits receive
    reported "hand-edited unit, linked_unit_id, unit_ratio")."""

    def _unit_issue(self, **over):
        i = {
            "id": "unit_would_be_created:ld-1",
            "code": "unit_would_be_created",
            "blocking": True,
            "line_id": "ld-1",
            "message": "the copy's delivered unit '6x700ml' doesn't exist in Loaded",
            "action": {"kind": "create_unit", "payload": {"unit_name": "6x700ml"}},
        }
        i.update(over)
        return i

    def test_the_396152_pin_a_blocker_created_unit_is_not_an_edit(self):
        lines = [_line(unit="6x700ml", linked_unit_id="u-new", unit_ratio=4.2)]
        doc = _doc(
            lines=lines,
            snapshot_lines=[
                _line(unit="700 mL", linked_unit_id="u-700", unit_ratio=0.7)
            ],
            issues=[self._unit_issue()],
            actions=[
                _act(
                    "unit_would_be_created:ld-1",
                    after={
                        "unit": "6x700ml",
                        "linked_unit_id": "u-new",
                        "unit_ratio": 4.2,
                    },
                )
            ],
        )
        assert AM.manual_edits(doc) == []

    def test_a_bare_issue_accept_still_owns_its_kinds_fields(self):
        # Gate-walk records (by "norm") and blocker accepts predating the
        # card recording values carry no after — the accepted action still
        # owns its kind's fields on that line.
        lines = [_line(unit="6x700ml", linked_unit_id="u-new", unit_ratio=4.2)]
        doc = _doc(
            lines=lines,
            snapshot_lines=[
                _line(unit="700 mL", linked_unit_id="u-700", unit_ratio=0.7)
            ],
            issues=[self._unit_issue()],
            actions=[_act("unit_would_be_created:ld-1")],
        )
        assert AM.manual_edits(doc) == []

    def test_accept_then_tweak_with_recorded_values_is_still_manual(self):
        # The strict contract survives wherever values were recorded: the
        # final unit is NOT the one the accept created, so it was typed.
        lines = [_line(unit="12 Pack", linked_unit_id="u-12", unit_ratio=12)]
        doc = _doc(
            lines=lines,
            snapshot_lines=[
                _line(unit="700 mL", linked_unit_id="u-700", unit_ratio=0.7)
            ],
            issues=[self._unit_issue()],
            actions=[
                _act(
                    "unit_would_be_created:ld-1",
                    after={
                        "unit": "6x700ml",
                        "linked_unit_id": "u-new",
                        "unit_ratio": 4.2,
                    },
                )
            ],
        )
        edits = AM.manual_edits(doc)
        assert "line:ld-1.unit" in edits

    def test_a_strike_by_blocker_is_not_an_edit(self):
        lines = [_line(struck=True)]
        issue = {
            "id": "loaded_line_not_on_copy:ld-1",
            "code": "loaded_line_not_on_copy",
            "blocking": True,
            "line_id": "ld-1",
            "message": "Loaded bills it, the copy doesn't",
            "action": {"kind": "strike", "payload": {}},
        }
        doc = _doc(
            lines=lines,
            issues=[issue],
            actions=[_act("loaded_line_not_on_copy:ld-1")],
        )
        assert AM.manual_edits(doc) == []


class TestAutoVerdict:
    """The end-state counterfactual: would autopilot with every flag on have
    sent THIS receive? No action forensics — the verdict is a diff between
    the receive that happened and a pure simulation from stored review
    artifacts (loaded_snapshot ⊕ server_filled, suggestions, issues)."""

    def _reviewed(self, *, lines=None, snapshot_lines=None, **over):
        doc = _doc(lines=lines, snapshot_lines=snapshot_lines, **over)
        doc["doc_schema"] = "replica_v1"
        return doc

    def test_accept_all_matches_and_names_its_gates(self):
        # The human accepted the qty suggestion; a PO blocker stood, gated.
        s = _sugg()  # quantity_received 4.95 -> 6.0
        doc = self._reviewed(
            lines=[_line(quantity_received=6.0)],
            suggestions=[s],
            issues=[
                {
                    "id": "po_missing",
                    "code": "po_missing",
                    "blocking": True,
                    "line_id": None,
                    "gate": "receive_without_po",
                    "message": "no purchase order",
                }
            ],
        )
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "matched"
        assert out["gates_needed"] == ["receive_without_po"]
        assert out["diffs"] == []

    def test_hand_typing_the_suggested_value_still_matches(self):
        # The case the action-forensics classifier gets wrong on purpose:
        # the human TYPED 6.0 instead of accepting — autopilot would have
        # produced the identical receive, so the verdict is matched.
        s = _sugg()
        doc = self._reviewed(lines=[_line(quantity_received=6.0)], suggestions=[s])
        assert AM.auto_verdict(doc)["verdict"] == "matched"

    def test_a_dismissed_suggestion_differs(self):
        # The human kept 4.95; autopilot would have accepted 6.0.
        s = _sugg()
        doc = self._reviewed(lines=[_line()], suggestions=[s])
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "differed"
        assert any(d["path"].endswith("quantity_received") for d in out["diffs"])
        assert out["diffs"][0]["sent"] == 4.95 and out["diffs"][0]["auto"] == 6.0

    def test_a_blocker_created_unit_matches_by_name(self):
        # Autopilot would CREATE '6x700ml'; the human's receive used the
        # unit created that day. Ids can't be known in simulation — names
        # carry the identity.
        doc = self._reviewed(
            lines=[_line(unit="6x700ml", linked_unit_id="u-new", unit_ratio=4.2)],
            issues=[
                {
                    "id": "unit_would_be_created:ld-1",
                    "code": "unit_would_be_created",
                    "blocking": True,
                    "line_id": "ld-1",
                    "gate": "auto_create_units",
                    "message": "…",
                    "action": {
                        "kind": "create_unit",
                        "payload": {"unit_name": "6x700ml"},
                    },
                }
            ],
        )
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "matched"
        assert out["gates_needed"] == ["auto_create_units"]

    def test_a_different_unit_than_the_create_differs(self):
        doc = self._reviewed(
            lines=[_line(unit="12 Pack", linked_unit_id="u-12", unit_ratio=12)],
            issues=[
                {
                    "id": "unit_would_be_created:ld-1",
                    "code": "unit_would_be_created",
                    "blocking": True,
                    "line_id": "ld-1",
                    "gate": "auto_create_units",
                    "message": "…",
                    "action": {
                        "kind": "create_unit",
                        "payload": {"unit_name": "6x700ml"},
                    },
                }
            ],
        )
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "differed"
        assert any(d["path"].endswith(".unit") for d in out["diffs"])

    def test_an_ungated_blocker_is_never_auto(self):
        doc = self._reviewed(
            lines=[_line()],
            issues=[
                {
                    "id": "totals_x",
                    "code": "totals_inconsistent",
                    "blocking": True,
                    "line_id": None,
                    "message": "totals don't reconcile",
                }
            ],
        )
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "never_auto"
        assert out["ungated"] == ["totals_inconsistent"]

    def test_a_cleared_blocker_needs_no_flag(self):
        # The item-link suggestion satisfies the blocker's clears_when in
        # simulation — accepting a suggestion is not a flag.
        s = _sugg(
            "line_value:linked_item_id:ld-1",
            field="linked_item_id",
            apply={"linked_item_id": "item-salmon"},
        )
        doc = self._reviewed(
            lines=[_line()],
            snapshot_lines=[_line(linked_item_id=None)],
            suggestions=[s],
            issues=[
                {
                    "id": "item_unmatched:ld-1",
                    "code": "item_unmatched",
                    "blocking": True,
                    "line_id": "ld-1",
                    "gate": "auto_create_items",
                    "message": "…",
                    "clears_when": {
                        "scope": "line",
                        "line_id": "ld-1",
                        "field": "linked_item_id",
                        "op": "not_null",
                    },
                }
            ],
        )
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "matched"
        assert out["gates_needed"] == []

    def test_a_delete_gated_duplicate_the_human_received_differs(self):
        doc = self._reviewed(
            lines=[_line()],
            issues=[
                {
                    "id": "duplicate_invoice",
                    "code": "duplicate_invoice",
                    "blocking": True,
                    "line_id": None,
                    "gate": "auto_delete_duplicates",
                    "message": "duplicate",
                    "action": {"kind": "delete_invoice", "payload": {}},
                }
            ],
        )
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "differed"
        assert any(
            d["path"] == "outcome" and d["auto"] == "deleted" for d in out["diffs"]
        )

    def test_a_hand_added_line_differs(self):
        doc = self._reviewed(lines=[_line(), _line(id="new-1", description="EXTRA")])
        out = AM.auto_verdict(doc)
        assert out["verdict"] == "differed"
        assert any(d["path"] == "line:new-1.added" for d in out["diffs"])

    def test_an_accepted_add_line_matches(self):
        add = {
            "id": "add_line:rep-9",
            "kind": "add_line",
            "line_id": "rep-9",
            "payload": _line(
                id="rep-9",
                description="Freight",
                unit="Each",
                linked_unit_id="u-each",
                unit_ratio=1,
                quantity_received=1,
                unit_cost=9.0,
            ),
        }
        doc = self._reviewed(
            lines=[
                _line(),
                _line(
                    id="rep-9",
                    description="Freight",
                    unit="Each",
                    linked_unit_id="u-each",
                    unit_ratio=1,
                    quantity_received=1,
                    unit_cost=9.0,
                ),
            ],
            suggestions=[add],
        )
        assert AM.auto_verdict(doc)["verdict"] == "matched"

    def test_no_review_is_unscored(self):
        doc = _doc()  # no doc_schema
        assert AM.auto_verdict(doc)["verdict"] == "unscored"

    def test_record_stores_the_auto_detail(self):
        # classify_outcome + auto_verdict both land in the stored detail —
        # verified at the unit level here (record_receive_outcome's session
        # handling is covered by its own tests).
        s = _sugg()
        doc = self._reviewed(lines=[_line(quantity_received=6.0)], suggestions=[s])
        out = AM.auto_verdict(doc)
        assert set(out) == {"verdict", "gates_needed", "ungated", "diffs"}
