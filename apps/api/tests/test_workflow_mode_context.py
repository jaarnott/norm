"""A workflow's run mode is CONTEXT, not a tool call.

The two invoice playbooks opened with a mandatory `get_workflow_mode` call:
75 calls across 75 threads in the 60 days to 21 Sep 2026 — exactly one per
conversation — to learn a value the engine already had. Worse, receiving's
mode moved to the VENUE, so the per-user value that call returned was not
the effective setting at all.

These pin the replacement: the prompt states the live mode, the "unset"
gate survives, and an agent that cannot run a workflow is told nothing
about it.
"""

import uuid

from app.agents.prompt_builder import workflow_modes_guidance
from app.db.models import Organization, User, Venue

RECONCILE = "reconcile_received_invoices"
RECEIVE = "review_and_receive_invoices"


def _user(db, modes=None):
    u = User(
        id=str(uuid.uuid4()),
        email=f"u{uuid.uuid4().hex[:8]}@x.com",
        hashed_password="x",
        full_name="U",
        role="user",
        is_active=True,
        workflow_modes=modes or {},
    )
    db.add(u)
    db.flush()
    return u


def _venue(db, autopilot=None):
    org = Organization(
        id=str(uuid.uuid4()), name="Cook", slug=f"o{uuid.uuid4().hex[:6]}"
    )
    db.add(org)
    db.flush()
    v = Venue(
        id=str(uuid.uuid4()),
        name=f"Venue {uuid.uuid4().hex[:4]}",
        timezone="Pacific/Auckland",
        organization_id=org.id,
    )
    if autopilot is not None and hasattr(v, "autopilot_settings"):
        v.autopilot_settings = autopilot
    db.add(v)
    db.flush()
    return v


class TestStatesTheLiveMode:
    def test_a_set_personal_mode_is_stated_and_no_tool_is_needed(self, db_session):
        user = _user(db_session, {RECONCILE: "autopilot"})
        text = workflow_modes_guidance({RECONCILE}, db_session, user.id, None)
        assert "autopilot" in text
        assert RECONCILE in text
        # The whole point: the model must not go looking for a reader tool.
        assert "get_workflow_mode" not in text
        assert "do not need one" in text

    def test_unset_keeps_the_gate(self, db_session):
        """'unset' must still stop the run — that gate is why the playbook
        called the tool in the first place."""
        user = _user(db_session, {})
        text = workflow_modes_guidance({RECONCILE}, db_session, user.id, None)
        assert "unset" in text
        assert "do NOT run" in text
        assert "set_workflow_mode" in text, "the user's choice must still persist"

    def test_a_set_mode_does_not_emit_the_unset_warning(self, db_session):
        user = _user(db_session, {RECONCILE: "approve_fixes"})
        text = workflow_modes_guidance({RECONCILE}, db_session, user.id, None)
        assert "approve_fixes" in text
        assert "do NOT run" not in text


class TestOnlyWhatTheAgentCanRun:
    def test_an_agent_without_the_workflow_is_told_nothing(self, db_session):
        user = _user(db_session, {RECONCILE: "autopilot"})
        assert workflow_modes_guidance({"get_sales"}, db_session, user.id, None) == ""

    def test_no_actions_at_all_is_silent(self, db_session):
        user = _user(db_session, {RECONCILE: "autopilot"})
        assert workflow_modes_guidance(set(), db_session, user.id, None) == ""


class TestVenueScopedReceiving:
    """Receiving's mode belongs to the VENUE. Reporting the triggering
    user's personal value would resurrect the second, invisible setting
    that execute_consolidator's comment warns about."""

    def test_receiving_is_reported_as_the_venues_setting(self, db_session):
        user = _user(db_session, {RECEIVE: "autopilot"})  # personal value: ignored
        venue = _venue(db_session)
        text = workflow_modes_guidance({RECEIVE}, db_session, user.id, venue.name)
        assert "VENUE" in text
        assert venue.name in text

    def test_receiving_is_skipped_when_no_venue_is_active(self, db_session):
        user = _user(db_session, {RECEIVE: "autopilot"})
        assert workflow_modes_guidance({RECEIVE}, db_session, user.id, None) == ""
