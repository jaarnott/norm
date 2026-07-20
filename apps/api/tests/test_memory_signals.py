"""Capturing the corrections Norm was throwing away.

Two signals existed in the product and neither was ever read:

1. Working-document edit deltas — the difference between what Norm drafted and
   what the human actually wanted. These passed through `_apply_op` into
   `pending_ops`, a sync outbox that is **drained and cleared** once the
   connector accepts the change. The evidence was destroyed on success.
2. `Approval.notes` — which, it turns out, no call site ever wrote. It was
   untapped because nothing populated it, not because nothing read it.
"""

import pytest

from app.db.models import Memory, MemorySignal
from app.services.memory_signals import (
    REPEAT_THRESHOLD,
    promote_repeated_edits,
    propose_from_rejection,
    record_draft_edit,
    record_rejection,
)
from tests.conftest import _make_organization, _make_user


@pytest.fixture
def principal(db_session):
    org = _make_organization(db_session)
    user = _make_user(db_session)
    db_session.flush()
    return org, user


class TestDraftEdits:
    def test_a_correction_is_banked_rather_than_discarded(self, db_session, principal):
        org, user = principal
        signal = record_draft_edit(
            db_session,
            organization_id=org.id,
            user_id=user.id,
            thread_id=None,
            document_kind="purchase_order",
            ops=[{"op": "set_line_quantity", "field": "quantity", "value": 8}],
        )
        assert signal is not None
        assert db_session.query(MemorySignal).count() == 1
        assert "quantity" in signal.summary

    def test_a_single_edit_does_not_become_a_memory(self, db_session, principal):
        """One edit is evidence, not a conclusion. Proposing on every edit would
        fill the review queue with noise and teach people to approve blindly."""
        org, user = principal
        record_draft_edit(
            db_session, organization_id=org.id, user_id=user.id, thread_id=None,
            document_kind="purchase_order",
            ops=[{"op": "set_line_quantity", "field": "quantity", "value": 8}],
        )
        promote_repeated_edits(db_session, organization_id=org.id)
        assert db_session.query(Memory).count() == 0

    def test_a_repeated_correction_is_proposed(self, db_session, principal):
        org, user = principal
        for _ in range(REPEAT_THRESHOLD):
            record_draft_edit(
                db_session, organization_id=org.id, user_id=user.id, thread_id=None,
                document_kind="purchase_order",
                ops=[{"op": "set_line_quantity", "field": "quantity", "value": 8}],
            )
        proposed = promote_repeated_edits(db_session, organization_id=org.id)
        assert any(p.get("stored") for p in proposed)

    def test_a_proposal_is_a_candidate_not_an_active_memory(self, db_session, principal):
        """Rule 4: auto-write is only for explicit and correction triggers. An
        inference from behaviour waits for review."""
        org, user = principal
        for _ in range(REPEAT_THRESHOLD):
            record_draft_edit(
                db_session, organization_id=org.id, user_id=user.id, thread_id=None,
                document_kind="roster", ops=[{"op": "move_shift", "field": "start"}],
            )
        promote_repeated_edits(db_session, organization_id=org.id)
        stored = db_session.query(Memory).first()
        assert stored is not None
        assert stored.status == "candidate"
        assert stored.trigger == "draft_edit"

    def test_the_same_evidence_is_not_proposed_twice(self, db_session, principal):
        org, user = principal
        for _ in range(REPEAT_THRESHOLD):
            record_draft_edit(
                db_session, organization_id=org.id, user_id=user.id, thread_id=None,
                document_kind="roster", ops=[{"op": "move_shift", "field": "start"}],
            )
        promote_repeated_edits(db_session, organization_id=org.id)
        again = promote_repeated_edits(db_session, organization_id=org.id)
        assert again == []

    def test_capture_never_breaks_the_edit_itself(self, db_session):
        """Observation must never fail the action the user actually asked for."""
        assert record_draft_edit(
            db_session, organization_id=None, user_id=None, thread_id=None,
            document_kind="x", ops=[{"op": "y"}],
        ) is None


class TestRejections:
    def test_a_reason_is_banked(self, db_session, principal):
        org, user = principal
        signal = record_rejection(
            db_session, organization_id=org.id, user_id=user.id, thread_id=None,
            notes="We never order kegs from this supplier",
        )
        assert signal is not None
        assert signal.kind == "rejection"

    def test_a_rejection_with_no_reason_is_not_recorded(self, db_session, principal):
        """'No' on its own carries nothing to learn from."""
        org, user = principal
        assert record_rejection(
            db_session, organization_id=org.id, user_id=user.id,
            thread_id=None, notes="   ",
        ) is None
        assert db_session.query(MemorySignal).count() == 0

    def test_a_reason_can_become_a_candidate_memory(self, db_session, principal):
        org, user = principal
        signal = record_rejection(
            db_session, organization_id=org.id, user_id=user.id, thread_id=None,
            notes="We never order kegs from this supplier",
        )
        result = propose_from_rejection(db_session, signal)
        assert result and result.get("stored")
        assert signal.promoted_to_memory_id

    def test_a_reason_that_breaks_the_rules_is_still_refused(self, db_session, principal):
        """The signal path is not a way around admission control."""
        org, user = principal
        signal = record_rejection(
            db_session, organization_id=org.id, user_id=user.id, thread_id=None,
            notes="Orders over $5000 need approval first",
        )
        result = propose_from_rejection(db_session, signal)
        assert result["stored"] is False
        assert db_session.query(Memory).count() == 0
