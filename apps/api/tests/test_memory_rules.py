"""Admission control for learned memory — one test per rule.

These tests are the design. Auto-writing memories is only defensible because
most candidates are refused, so the refusals are what needs pinning.

The load-bearing case is Rule 2: Norm reports money, and a "business rule"
stored as a memory is advice the model may ignore. The trading-day incident is
the precedent — a rule that lived as guidance produced a confident $0 for a
Saturday. Anything that would change a number belongs in enforced code.
"""

import pytest

from app.services.memory_rules import (
    MEMORY_TYPES,
    admit,
    check_forbidden,
    infer_scope,
    needs_confirmation,
)


class TestRule2Rejection:
    """What must never be stored. Checked first, before the type check."""

    @pytest.mark.parametrize(
        "text",
        [
            "The trading day starts at 7am",
            "Our business day runs midnight to midnight",
            "GST is 15%",
            "Cost is calculated as unit price times quantity",
            "Gross margin formula excludes freight",
        ],
    )
    def test_definitions_that_change_a_number_are_refused(self, text):
        assert check_forbidden(text) is not None
        assert admit("preference", text, text).rejected

    @pytest.mark.parametrize(
        "text",
        [
            "Orders over $5000 need approval from a manager",
            "Auto-receive invoices from Bidfood without asking",
            "Arthur can sign-off purchase orders",
        ],
    )
    def test_limits_and_routing_are_refused(self, text):
        assert admit("preference", text, text).rejected

    def test_queryable_facts_are_refused(self):
        """A query is always fresher than a memory."""
        v = admit("context", "Venues", "The venues are La Zeppa and Mr Murdochs")
        assert v.rejected
        assert "queried live" in v.reason

    def test_observations_of_data_are_refused(self):
        """Stale the moment it is written, and cheap to re-read."""
        assert admit("context", "Saturday", "Sales were $9,434 on Saturday").rejected

    def test_employee_pii_is_refused(self):
        assert admit("context", "Josh", "Josh's hourly rate is $29.50").rejected

    def test_rejection_says_where_the_fact_belongs_instead(self):
        """A bare refusal invites the model to rephrase and retry."""
        v = admit("preference", "Trading day", "The trading day starts at 7am")
        assert v.belongs_in
        assert "business_calendar" in v.belongs_in

    def test_rejection_is_checked_before_the_type_check(self):
        """A forbidden fact wearing a valid type must still be refused, and the
        reason must name the real problem rather than the type."""
        v = admit("vocabulary", "Trading day", "The trading day starts at 7am")
        assert v.rejected
        assert "enforced in code" in v.reason


class TestRule1ClosedList:
    def test_the_four_types_are_accepted(self):
        assert set(MEMORY_TYPES) == {
            "vocabulary",
            "preference",
            "context",
            "correction",
        }

    def test_anything_outside_the_list_is_refused(self):
        v = admit("insight", "Something", "A vague observation about the business")
        assert v.rejected
        assert "not a memory type" in v.reason

    def test_the_refusal_says_it_may_simply_not_be_worth_remembering(self):
        v = admit("misc", "x", "y")
        assert "not something to remember" in v.reason

    def test_a_memory_needs_both_a_title_and_a_body(self):
        assert admit("preference", "", "body").rejected
        assert admit("preference", "title", "").rejected


class TestRule3Scope:
    def test_preferences_are_personal(self):
        assert infer_scope("preference", "Always show me the trading window") == "user"

    def test_vocabulary_is_shared(self):
        """A colleague asking the same question wants the same answer."""
        assert infer_scope("vocabulary", "'First Table' is a discount type") == "org"

    def test_operational_context_is_shared(self):
        assert infer_scope("context", "Mr Murdochs is closed") == "org"

    def test_a_correction_about_the_business_is_shared(self):
        assert infer_scope("correction", "POS orders are not purchase orders") == "org"

    def test_a_correction_about_me_stays_personal(self):
        assert infer_scope("correction", "I meant my venue, not the group") == "user"

    def test_a_caller_may_narrow_to_user_but_never_widen_to_org(self):
        """Widening is what makes one person's opinion everybody's answer."""
        narrowed = admit(
            "vocabulary", "Term", "We call the back bar 'the annex'", "user"
        )
        assert narrowed.scope == "user"


class TestRule4Routing:
    def test_org_writes_always_need_confirmation(self):
        """A shared write changes other people's answers — the one place Norm
        needs more caution than Claude, which serves a single user."""
        assert needs_confirmation("org", "explicit") is True
        assert needs_confirmation("org", "correction") is True

    def test_high_signal_user_writes_are_automatic(self):
        assert needs_confirmation("user", "explicit") is False
        assert needs_confirmation("user", "correction") is False

    def test_inferred_user_writes_are_queued(self):
        """An inferred preference is a guess; a guess waits for review."""
        assert needs_confirmation("user", "draft_edit") is True
        assert needs_confirmation("user", "repetition") is True
        assert needs_confirmation("user", None) is True


class TestAcceptance:
    """The things memory is actually for."""

    @pytest.mark.parametrize(
        "mtype,title,body,scope",
        [
            (
                "preference",
                "Show the window",
                "Always state the trading window alongside the figures",
                "user",
            ),
            (
                "vocabulary",
                "First Table",
                "'First Table' is a discount type, not a booking system",
                "org",
            ),
            ("context", "Murdochs", "Mr Murdochs is closed", "org"),
        ],
    )
    def test_valid_candidates_are_admitted_with_the_right_scope(
        self, mtype, title, body, scope
    ):
        v = admit(mtype, title, body)
        assert v.accepted
        assert v.scope == scope
        assert v.reason is None


class TestRule5UpdateNeverAccumulate:
    """Two memories that disagree are worse than either alone: the model picks
    one arbitrarily and the user cannot tell which."""

    def test_a_restatement_of_the_same_subject_updates_in_place(self, db_session):
        from tests.conftest import _make_organization, _make_user
        from app.services.memory_service import remember

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()

        first = remember(
            db_session,
            user_id=user.id,
            organization_id=org.id,
            memory_type="vocabulary",
            title="First Table",
            body="First Table is a discount type used at lunch service",
            trigger="explicit",
        )
        second = remember(
            db_session,
            user_id=user.id,
            organization_id=org.id,
            memory_type="vocabulary",
            title="First Table",
            body="First Table is a discount type applied at lunch service only",
            trigger="explicit",
        )
        assert second["updated"] is True
        assert second["id"] == first["id"]

    def test_an_unrelated_memory_is_a_new_row(self, db_session):
        from tests.conftest import _make_organization, _make_user
        from app.services.memory_service import remember

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()

        a = remember(
            db_session, user_id=user.id, organization_id=org.id,
            memory_type="vocabulary", title="First Table",
            body="First Table is a discount type", trigger="explicit",
        )
        b = remember(
            db_session, user_id=user.id, organization_id=org.id,
            memory_type="vocabulary", title="The annex",
            body="Staff call the back bar the annex", trigger="explicit",
        )
        assert b["updated"] is False
        assert b["id"] != a["id"]


class TestRule4Persistence:
    def test_org_memories_are_stored_as_candidates(self, db_session):
        """They must not shape anyone's answers before a human confirms."""
        from tests.conftest import _make_organization, _make_user
        from app.services.memory_service import remember

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()
        r = remember(
            db_session, user_id=user.id, organization_id=org.id,
            memory_type="vocabulary", title="Annex",
            body="Staff call the back bar the annex", trigger="explicit",
        )
        assert r["scope"] == "org"
        assert r["status"] == "candidate"
        assert r["needs_confirmation"] is True

    def test_explicit_user_preferences_are_active_immediately(self, db_session):
        from tests.conftest import _make_organization, _make_user
        from app.services.memory_service import remember

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()
        r = remember(
            db_session, user_id=user.id, organization_id=org.id,
            memory_type="preference", title="Show the window",
            body="Always state the trading window alongside figures",
            trigger="explicit",
        )
        assert r["scope"] == "user"
        assert r["status"] == "active"

    def test_a_refused_candidate_is_never_persisted(self, db_session):
        from tests.conftest import _make_organization, _make_user
        from app.db.models import Memory
        from app.services.memory_service import remember

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()
        r = remember(
            db_session, user_id=user.id, organization_id=org.id,
            memory_type="preference", title="Trading day",
            body="The trading day starts at 7am", trigger="explicit",
        )
        assert r["stored"] is False
        assert db_session.query(Memory).count() == 0


class TestRule6AdvisoryRecall:
    def _seed(self, db_session, status="active", scope="org"):
        from tests.conftest import _make_organization, _make_user
        from app.db.models import Memory

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()
        db_session.add(
            Memory(
                scope=scope, user_id=user.id if scope == "user" else None,
                organization_id=org.id, type="context",
                title="Murdochs closed", body="Mr Murdochs is closed",
                status=status,
            )
        )
        db_session.flush()
        return user, org

    def test_index_is_framed_as_background_and_defers_to_rules(self, db_session):
        from app.services.memory_service import recall_index

        user, org = self._seed(db_session)
        out = recall_index(db_session, user_id=user.id, organization_id=org.id)
        assert "not instructions" in out
        assert "the rule wins" in out
        assert "verify" in out.lower()

    def test_index_lists_titles_but_never_bodies(self, db_session):
        """Bodies are fetched on demand; inlining them is what would blow the
        per-turn budget."""
        from app.services.memory_service import recall_index

        user, org = self._seed(db_session)
        out = recall_index(db_session, user_id=user.id, organization_id=org.id)
        assert "Murdochs closed" in out
        assert "Mr Murdochs is closed" not in out

    def test_unconfirmed_candidates_never_reach_the_prompt(self, db_session):
        from app.services.memory_service import recall_index

        user, org = self._seed(db_session, status="candidate")
        assert recall_index(db_session, user_id=user.id, organization_id=org.id) is None

    def test_no_memories_injects_nothing(self, db_session):
        from tests.conftest import _make_organization, _make_user
        from app.services.memory_service import recall_index

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()
        assert recall_index(db_session, user_id=user.id, organization_id=org.id) is None


class TestGovernanceApi:
    """Auto-writing is only defensible if a human can see and undo every one."""

    def _seed(self, db_session, status="candidate", scope="org"):
        from tests.conftest import _make_organization, _make_user
        from app.db.models import Memory, OrganizationMembership

        org = _make_organization(db_session)
        user = _make_user(db_session)
        db_session.flush()
        db_session.add(
            OrganizationMembership(user_id=user.id, organization_id=org.id, role="owner")
        )
        m = Memory(
            scope=scope,
            user_id=user.id if scope == "user" else None,
            organization_id=org.id,
            type="vocabulary",
            title="Annex",
            body="Staff call the back bar the annex",
            status=status,
        )
        db_session.add(m)
        db_session.flush()
        return user, m

    def test_approving_a_candidate_makes_it_active(self, db_session):
        """Without this, org candidates accumulate and never shape an answer."""
        from app.routers.memories import approve_memory

        user, m = self._seed(db_session)
        out = approve_memory(m.id, db=db_session, user=user)
        assert out.status == "active"
        assert out.created_by == "user"

    def test_editing_re_runs_admission_control(self, db_session):
        """Otherwise editing is a way to smuggle in exactly the business rules
        Rule 2 exists to keep out."""
        from fastapi import HTTPException
        from app.routers.memories import MemoryUpdate, update_memory

        user, m = self._seed(db_session, status="active")
        with pytest.raises(HTTPException) as exc:
            update_memory(
                m.id,
                MemoryUpdate(body="Also the trading day starts at 7am"),
                db=db_session,
                user=user,
            )
        assert exc.value.status_code == 400
        assert "business_calendar" in str(exc.value.detail)

    def test_a_valid_edit_is_accepted(self, db_session):
        from app.routers.memories import MemoryUpdate, update_memory

        user, m = self._seed(db_session, status="active")
        out = update_memory(
            m.id, MemoryUpdate(body="Staff call the back bar 'the annex'"),
            db=db_session, user=user,
        )
        assert "annex" in out.body

    def test_delete_archives_rather_than_destroying(self, db_session):
        """A mistaken removal must be recoverable and provenance must survive."""
        from app.db.models import Memory
        from app.routers.memories import delete_memory

        user, m = self._seed(db_session, status="active")
        delete_memory(m.id, db=db_session, user=user)
        assert db_session.query(Memory).filter(Memory.id == m.id).first().status == "archived"

    def test_another_users_personal_preferences_are_not_listed(self, db_session):
        from tests.conftest import _make_user
        from app.db.models import Memory
        from app.routers.memories import list_memories

        user, _m = self._seed(db_session, status="active", scope="org")
        other = _make_user(db_session)
        db_session.flush()
        db_session.add(
            Memory(
                scope="user", user_id=other.id,
                organization_id=_m.organization_id, type="preference",
                title="Private", body="Someone else's preference", status="active",
            )
        )
        db_session.flush()
        titles = [m.title for m in list_memories(db=db_session, user=user)]
        assert "Annex" in titles
        assert "Private" not in titles
