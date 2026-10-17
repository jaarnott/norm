"""Norm Performance: performance reviews, review cycles, the manager digest
and capability frameworks (Oct 2026 — Orbit parity, phase 3; moved out of Norm
Training into their own built-in app, unchanged).

Orbit decided who may see and change what in a review by the URL alone: its
database let any manager edit any review, and a team member write every
column of their own, pay included. Norm enforces the FIELD RULES in the app's
logic, so these tests run the real `performance.py` through the real sandbox
and storage door, as different viewers:

* the manager's side, the two private questions and pay — only the review's
  effective manager (review.manager_id, else the person's) or a review admin;
* private answers and pay are never returned to anyone else;
* each side's answers stay hidden from the other until both are in;
* the workflow, completion rules, unlock, review cycles (roll forward, enrol,
  carry goals forward, withdraw untouched orphans), the nightly run as the
  system actor, and the manager digest's idempotency;
* capability frameworks (a role's competency map), and that Training no
  longer has any of it.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.db.models import AppRecord, EmailLog, Role
from app.services import app_runtime as AR
from app.services.app_email import set_email_enabled
from tests.conftest import _make_membership, _make_organization, _make_user

TODAY = datetime.now(timezone.utc).date()


def _iso(days=0):
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture()
def org(db_session):
    return _make_organization(db_session)


def _user(db, org, perms, email=None, name="Test User"):
    user = _make_user(
        db, email=email or f"{uuid.uuid4().hex[:8]}@t.local", full_name=name
    )
    mem = _make_membership(db, user, org)
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=f"r-{uuid.uuid4().hex[:6]}",
        display_name="r",
        permissions=perms,
    )
    db.add(role)
    db.flush()
    mem.role_id = role.id
    db.flush()
    return user


@pytest.fixture()
def admin(db_session, org):
    return _user(db_session, org, ["hr:read", "hr:write"], name="Hana Admin")


@pytest.fixture()
def boss(db_session, org):
    """A manager by relationship, not by role: no hr:write."""
    return _user(db_session, org, ["hr:read"], name="Mere Manager")


@pytest.fixture()
def other(db_session, org):
    return _user(db_session, org, ["hr:read"], name="Tama Other")


@pytest.fixture()
def app_and_version(db_session, org, admin):
    from app.services.builtin_apps import bind, get_builtin

    return bind(get_builtin("performance"), org.id)


@pytest.fixture()
def training(db_session, org):
    """Norm Training, which keeps the people (and who manages whom)."""
    from app.services.builtin_apps import bind, get_builtin

    return bind(get_builtin("training"), org.id)


def _rec(db, org, collection, data, venue_id=None):
    row = AppRecord(
        id=str(uuid.uuid4()),
        namespace="hr_suite",
        organization_id=org.id,
        venue_id=venue_id,
        collection=collection,
        data=data,
    )
    db.add(row)
    db.flush()
    return row.id


def _data(db, record_id):
    db.expire_all()
    return db.query(AppRecord).filter_by(id=record_id).one().data


def _raw(db, app, version, user, **params):
    return AR.run_logic(
        db, None, app=app, version=version, user=user, venue_id=None, params=params
    )


def _run(db, app, version, user, **params):
    out = _raw(db, app, version, user, **params)
    assert out["success"] is True, out.get("error")
    return out["data"]


FRAMEWORK = {
    "name": "Bar team",
    "is_active": True,
    "categories": [
        {
            "id": "cat-1",
            "name": "Service",
            "capabilities": [
                {"id": "cap-1", "name": "Greeting", "level1_descriptor": "Says hi"},
                {"id": "cap-2", "name": "Upselling"},
            ],
        }
    ],
}


@pytest.fixture()
def world(db_session, org, admin, boss, other):
    """A manager (linked to `boss`), a team member under them, an unrelated
    person (linked to `other`) and one open review with a previous goal."""
    db = db_session
    fw = _rec(db, org, "capability_frameworks", FRAMEWORK)
    mgr = _rec(
        db,
        org,
        "people",
        {
            "name": "Mere Manager",
            "role": "manager",
            "is_active": True,
            "email": boss.email,
            "norm_user_id": boss.id,
        },
    )
    sam = _rec(
        db,
        org,
        "people",
        {
            "name": "Sam Tui",
            "role": "team_member",
            "is_active": True,
            "email": "sam@t.local",
            "manager_id": mgr,
        },
    )
    tama = _rec(
        db,
        org,
        "people",
        {"name": "Tama Other", "is_active": True, "norm_user_id": other.id},
    )
    review = _rec(
        db,
        org,
        "performance_reviews",
        {
            "person_id": sam,
            "framework_id": fw,
            "due_date": _iso(10),
            "status": "employee_in_progress",
            "employee_reflection": {"does_well": "I'm fast", "self_rating": 3},
            "manager_feedback": {
                "job_offer_reaction": "Be relieved. It's not likely to work out anyway.",
                "engagement": "Low engagement and is burned out.",
                "does_well": "Draft",
            },
            "capability_ratings": [
                {"capability_id": "cap-1", "employee_level": 2, "manager_level": None}
            ],
            "previous_goals": [
                {
                    "id": "g1",
                    "text": "Learn the wine list",
                    "employee_outcome": "partial",
                }
            ],
            "next_goals": [],
            "proposed_pay_rate": 31.5,
            "no_pay_change": False,
        },
    )
    _rec(
        db, org, "remuneration", {"person_id": sam, "pay_type": "hourly", "rate": 28.0}
    )
    return {"fw": fw, "mgr": mgr, "sam": sam, "tama": tama, "review": review}


def _fill_manager_side(db, app, version, user, review):
    for cap in ("cap-1", "cap-2"):
        _run(
            db,
            app,
            version,
            user,
            op="review_rate",
            review_id=review,
            capability_id=cap,
            level=2,
        )
    _run(
        db,
        app,
        version,
        user,
        op="review_goal_outcome",
        review_id=review,
        goal_id="g1",
        outcome="achieved",
    )
    return {
        "does_well": "Warm with guests",
        "improve": "Stock rotation",
        "mgr_rating": 3,
        "capability_overall_level": 2,
        "job_offer_reaction": "Need to think about it. Sam Tui does a decent job.",
        "engagement": "Good engagement and a mostly positive attitude.",
    }


# ---------------------------------------------------------------------------
# Field rules
# ---------------------------------------------------------------------------


class TestWhoSeesWhat:
    def test_someone_unrelated_cannot_open_it(
        self, db_session, other, world, app_and_version
    ):
        app, version = app_and_version
        out = _run(
            db_session, app, version, other, op="review", review_id=world["review"]
        )
        assert out == {"error": "You don't have access to this review."}

    def test_the_manager_sees_private_answers_and_pay_but_not_the_self_assessment_yet(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        d = _run(db_session, app, version, boss, op="review", review_id=world["review"])
        r = d["review"]
        assert d["access"]["manager"] and d["access"]["write"]
        assert r["manager_feedback"]["engagement"].startswith("Low engagement")
        assert r["proposed_pay_rate"] == 31.5
        assert d["current_rate"] == {
            "pay_type": "hourly",
            "rate": 28.0,
            "synced_at": None,
        }
        # Neither side sees the other's answers until both are in.
        assert r["employee_reflection"] == {}
        assert r["capability_ratings"][0]["employee_level"] is None
        assert r["previous_goals"][0]["employee_outcome"] is None

    def test_the_person_reviewed_never_gets_private_answers_or_pay(
        self, db_session, org, world, app_and_version
    ):
        app, version = app_and_version
        sam_user = _user(db_session, org, ["hr:read"], name="Sam Tui")
        rec = db_session.query(AppRecord).filter_by(id=world["sam"]).one()
        rec.data = {**rec.data, "norm_user_id": sam_user.id}
        rec2 = db_session.query(AppRecord).filter_by(id=world["review"]).one()
        rec2.data = {
            **rec2.data,
            "employee_submitted_at": "2026-10-01T00:00:00+00:00",
            "manager_submitted_at": "2026-10-02T00:00:00+00:00",
            "status": "ready_for_meeting",
        }
        db_session.flush()
        d = _run(
            db_session, app, version, sam_user, op="review", review_id=world["review"]
        )
        r = d["review"]
        assert d["access"]["reviewee"] and not d["access"]["private"]
        assert "job_offer_reaction" not in r["manager_feedback"]
        assert "engagement" not in r["manager_feedback"]
        assert not any(
            k in r for k in ("proposed_pay_rate", "no_pay_change", "pay_notes")
        )
        assert d["current_rate"] is None
        # Both in: the shared parts are visible to them.
        assert r["manager_feedback"]["does_well"] == "Draft"
        assert r["employee_reflection"]["does_well"] == "I'm fast"

    def test_an_admin_sees_both_sides_and_drafts(
        self, db_session, admin, world, app_and_version
    ):
        app, version = app_and_version
        d = _run(
            db_session, app, version, admin, op="review", review_id=world["review"]
        )
        assert d["access"]["admin"] and d["access"]["private"]
        assert d["review"]["employee_reflection"]["does_well"] == "I'm fast"
        assert d["review"]["manager_feedback"]["engagement"]

    def test_an_admin_looking_at_their_own_review_gets_no_private_answers(
        self, db_session, admin, world, app_and_version
    ):
        app, version = app_and_version
        rec = db_session.query(AppRecord).filter_by(id=world["sam"]).one()
        rec.data = {**rec.data, "norm_user_id": admin.id}
        db_session.flush()
        d = _run(
            db_session, app, version, admin, op="review", review_id=world["review"]
        )
        assert d["access"]["reviewee"] and not d["access"]["private"]
        assert "engagement" not in d["review"]["manager_feedback"]
        out = _run(
            db_session,
            app,
            version,
            admin,
            op="review_save_pay",
            review_id=world["review"],
            pay={"proposed_pay_rate": 99},
        )
        assert "your own review" in out["error"]

    def test_an_admin_with_an_unlinked_record_is_still_the_reviewee(
        self, db_session, admin, world, app_and_version
    ):
        """Pre-push review: an admin whose person record isn't linked to their
        login (or a second linked row, or one outside their venues) must not
        become "not the reviewee" and see their own pay. Matching email is
        enough."""
        app, version = app_and_version
        rec = db_session.query(AppRecord).filter_by(id=world["sam"]).one()
        rec.data = {**rec.data, "email": admin.email.upper(), "norm_user_id": None}
        db_session.flush()
        d = _run(
            db_session, app, version, admin, op="review", review_id=world["review"]
        )
        assert d["access"]["reviewee"] and not d["access"]["private"]
        assert "proposed_pay_rate" not in d["review"]

    def test_an_unresolvable_reviewee_gets_no_private_access(
        self, db_session, admin, world, app_and_version
    ):
        """If the reviewee's record can't be seen, an HR admin is refused the
        private parts rather than trusted on a guess."""
        app, version = app_and_version
        db_session.query(AppRecord).filter_by(id=world["sam"]).delete()
        db_session.flush()
        d = _run(
            db_session, app, version, admin, op="review", review_id=world["review"]
        )
        # Refused outright, or shown without the private parts — never pay.
        assert "error" in d or not d["access"]["private"]
        assert "proposed_pay_rate" not in (d.get("review") or {})


class TestWhoWritesWhat:
    @pytest.mark.parametrize(
        "op,extra",
        [
            ("review_save_manager", {"fields": {"engagement": "x"}}),
            ("review_save_pay", {"pay": {"proposed_pay_rate": 50}}),
            ("review_rate", {"capability_id": "cap-1", "level": 3}),
            ("review_goal_outcome", {"goal_id": "g1", "outcome": "not"}),
            ("review_submit_manager", {}),
        ],
    )
    def test_someone_who_isnt_the_manager_cannot_write_the_managers_side(
        self, db_session, other, world, app_and_version, op, extra
    ):
        app, version = app_and_version
        before = _data(db_session, world["review"])
        out = _run(
            db_session, app, version, other, op=op, review_id=world["review"], **extra
        )
        assert "Only this review's manager" in out["error"]
        assert _data(db_session, world["review"]) == before

    def test_the_effective_manager_falls_back_to_the_profile_and_a_review_manager_wins(
        self, db_session, org, boss, other, world, app_and_version
    ):
        app, version = app_and_version
        ok = _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_manager",
            review_id=world["review"],
            fields={"does_well": "Great"},
        )
        assert ok["ok"]
        # Now the review names Tama as its own manager: Mere loses the pen.
        rec = db_session.query(AppRecord).filter_by(id=world["review"]).one()
        rec.data = {**rec.data, "manager_id": world["tama"]}
        db_session.flush()
        refused = _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_manager",
            review_id=world["review"],
            fields={"does_well": "Mine"},
        )
        assert "error" in refused
        took = _run(
            db_session,
            app,
            version,
            other,
            op="review_save_manager",
            review_id=world["review"],
            fields={"does_well": "Tama's"},
        )
        assert took["ok"]

    def test_the_team_member_side_cannot_be_written_from_norm(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_manager",
            review_id=world["review"],
            fields={
                "does_well": "Mgr",
                "employee_reflection": {"does_well": "forged"},
                "recognize_value": "forged",
            },
        )
        data = _data(db_session, world["review"])
        assert data["employee_reflection"] == {
            "does_well": "I'm fast",
            "self_rating": 3,
        }
        assert "recognize_value" not in data["manager_feedback"]

    def test_only_a_review_admin_changes_someones_manager_and_no_manager_clears(
        self, db_session, admin, boss, other, world, app_and_version, training
    ):
        # People — and so a person's manager — are Norm Training's.
        app, version = training
        refused = _run(
            db_session,
            app,
            version,
            other,
            op="save_person",
            person_id=world["sam"],
            fields={"manager_id": world["tama"]},
        )
        assert "Only an HR admin" in refused["error"]
        # So is the rest of a person (pre-push review): their email routes
        # digests, their login link decides who they are to the app.
        for fields in (
            {"name": "Sam T"},
            {"email": "me@evil.test"},
            {"is_active": False},
        ):
            out = _run(
                db_session,
                app,
                version,
                other,
                op="save_person",
                person_id=world["sam"],
                fields=fields,
            )
            assert "Only an HR admin" in out["error"]
        cleared = _run(
            db_session,
            app,
            version,
            admin,
            op="save_person",
            person_id=world["sam"],
            fields={"manager_id": None},
        )
        assert cleared["person"]["manager_id"] is None
        people = _run(db_session, app, version, admin, op="people")
        sam = next(p for p in people["people"] if p["id"] == world["sam"])
        assert sam["manager_id"] is None and sam["manager_name"] is None
        # ...and Performance reads it: Sam's review no longer has a manager.
        perf, perf_version = app_and_version
        assert (
            _run(db_session, perf, perf_version, boss, op="my_team_reviews")["current"]
            == []
        )


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class TestWorkflow:
    def test_submit_is_refused_until_every_step_is_done(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        out = _run(
            db_session,
            app,
            version,
            boss,
            op="review_submit_manager",
            review_id=world["review"],
        )
        assert out["error"].startswith("Still to finish:")
        assert out["missing"] == ["Capabilities", "Assessment", "Goals"]

    def test_manager_first_then_waits_and_locks(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        rid = world["review"]
        fields = _fill_manager_side(db_session, app, version, boss, rid)
        out = _run(
            db_session,
            app,
            version,
            boss,
            op="review_submit_manager",
            review_id=rid,
            fields=fields,
            pay={"proposed_pay_rate": "30.00", "pay_notes": "Earned it"},
        )
        assert out == {"ok": True, "status": "employee_in_progress"}
        data = _data(db_session, rid)
        assert data["manager_submitted_at"]
        assert data["current_pay_rate"] == 28.0 and data["proposed_pay_rate"] == 30.0
        assert data["pay_proposed_by"] == world["mgr"]
        d = _run(db_session, app, version, boss, op="review", review_id=rid)
        assert d["labels"]["manager"] == "Waiting on Sam"
        # Submitted: the manager's answers are locked.
        again = _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_manager",
            review_id=rid,
            fields={"does_well": "changed"},
        )
        assert again["error"] == "Your part is already submitted."
        assert (
            _run(
                db_session,
                app,
                version,
                boss,
                op="review_rate",
                review_id=rid,
                capability_id="cap-1",
                level=1,
            )["error"]
            == "Your part is already submitted."
        )

    def test_submitting_after_the_team_member_makes_it_ready_to_meet(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        rid = world["review"]
        rec = db_session.query(AppRecord).filter_by(id=rid).one()
        rec.data = {
            **rec.data,
            "employee_submitted_at": "2026-10-01T00:00:00+00:00",
            "status": "manager_in_progress",
        }
        db_session.flush()
        fields = _fill_manager_side(db_session, app, version, boss, rid)
        out = _run(
            db_session,
            app,
            version,
            boss,
            op="review_submit_manager",
            review_id=rid,
            fields=fields,
            pay={"no_pay_change": True},
        )
        assert out["status"] == "ready_for_meeting"
        d = _run(db_session, app, version, boss, op="review", review_id=rid)
        assert d["labels"]["manager"] == "Ready to meet and finish"
        assert d["state"]["both"] is True
        # Both in: the self-assessment is now visible to the manager.
        assert d["review"]["employee_reflection"]["does_well"] == "I'm fast"

    def test_a_review_with_no_framework_has_nothing_to_rate(
        self, db_session, org, boss, world, app_and_version
    ):
        app, version = app_and_version
        rid = _rec(
            db_session,
            org,
            "performance_reviews",
            {
                "person_id": world["sam"],
                "due_date": _iso(5),
                "status": "employee_in_progress",
            },
        )
        out = _run(
            db_session,
            app,
            version,
            boss,
            op="review_submit_manager",
            review_id=rid,
            fields={
                "does_well": "a",
                "improve": "b",
                "mgr_rating": 0,
                "job_offer_reaction": "x",
                "engagement": "y",
            },
            pay={"no_pay_change": True},
        )
        assert out["ok"] is True

    def test_pay_must_be_a_real_rate(self, db_session, boss, world, app_and_version):
        app, version = app_and_version
        for bad in (-1, "abc", True):
            out = _run(
                db_session,
                app,
                version,
                boss,
                op="review_save_pay",
                review_id=world["review"],
                pay={"proposed_pay_rate": bad},
            )
            assert out["error"] == "Enter a valid pay rate"
        ok = _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_pay",
            review_id=world["review"],
            pay={"no_pay_change": True, "proposed_pay_rate": "40"},
        )
        assert ok["ok"]
        data = _data(db_session, world["review"])
        assert data["no_pay_change"] is True and data["proposed_pay_rate"] is None

    def _both_in(self, db, rid):
        rec = db.query(AppRecord).filter_by(id=rid).one()
        rec.data = {
            **rec.data,
            "employee_submitted_at": "2026-10-01T00:00:00+00:00",
            "manager_submitted_at": "2026-10-02T00:00:00+00:00",
            "status": "ready_for_meeting",
        }
        db.flush()

    def test_next_goals_are_set_only_in_the_meeting(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        rid = world["review"]
        early = _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_manager",
            review_id=rid,
            fields={"next_goals": ["Run a shift"]},
        )
        assert "review meeting" in early["error"]
        self._both_in(db_session, rid)
        _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_manager",
            review_id=rid,
            fields={"next_goals": ["Run a shift", "  ", {"text": "Close up"}]},
        )
        assert _data(db_session, rid)["next_goals"] == [
            {"text": "Run a shift"},
            {"text": "Close up"},
        ]

    def test_complete_needs_both_sides(self, db_session, boss, world, app_and_version):
        app, version = app_and_version
        out = _run(
            db_session,
            app,
            version,
            boss,
            op="review_complete",
            review_id=world["review"],
        )
        assert "Both sides" in out["error"]

    def test_complete_sends_the_goals_email_cc_the_manager(
        self, db_session, org, boss, world, app_and_version, monkeypatch
    ):
        app, version = app_and_version
        rid = world["review"]
        self._both_in(db_session, rid)
        set_email_enabled(db_session, org.id, "performance", True, None)
        monkeypatch.setenv("APP_EMAIL_ALLOW_NONPROD", "1")
        with patch(
            "app.services.app_email.AppEmail._deliver",
            return_value=("sent", None, "pid"),
        ) as deliver:
            out = _run(
                db_session,
                app,
                version,
                boss,
                op="review_complete",
                review_id=rid,
                to="sam@t.local",
                cc=boss.email,
                intro="Hi Sam,\n\nThanks <for> meeting.",
                sign_off="Thanks,\nMere",
                next_goals=["Run a shift <solo>"],
                pay={"no_pay_change": True},
            )
        assert out["email"] == "sent"
        to, cc, reply_to, subject, html = deliver.call_args.args
        assert to == ["sam@t.local"] and cc == [boss.email] and reply_to == boss.email
        assert subject == "Your performance review — goals for the next quarter"
        assert (
            "Run a shift &lt;solo&gt;" in html and "Thanks &lt;for&gt; meeting." in html
        )
        data = _data(db_session, rid)
        assert data["status"] == "completed" and data["completed_at"]
        assert data["completed_by"] == world["mgr"]
        assert data["goals_email_to"] == "sam@t.local" and data["goals_email_sent_at"]
        assert "Run a shift &lt;solo&gt;" in data["summary_email_html"]
        log = (
            db_session.query(EmailLog)
            .filter_by(organization_id=org.id, app_slug="performance")
            .one()
        )
        assert log.idempotency_key == f"goals:{rid}:0"

    def test_while_email_is_off_it_completes_without_sending(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        rid = world["review"]
        self._both_in(db_session, rid)
        with patch("app.services.app_email.AppEmail._deliver") as deliver:
            out = _run(
                db_session,
                app,
                version,
                boss,
                op="review_complete",
                review_id=rid,
                to="sam@t.local",
                next_goals=["One"],
            )
        assert out["email"] == "disabled"
        deliver.assert_not_called()
        data = _data(db_session, rid)
        assert data["status"] == "completed"
        assert "goals_email_sent_at" not in data
        assert "One" in data["summary_email_html"]

    def test_a_failed_send_leaves_the_review_open(
        self, db_session, org, boss, world, app_and_version, monkeypatch
    ):
        app, version = app_and_version
        rid = world["review"]
        self._both_in(db_session, rid)
        set_email_enabled(db_session, org.id, "performance", True, None)
        monkeypatch.setenv("APP_EMAIL_ALLOW_NONPROD", "1")
        with patch(
            "app.services.app_email.AppEmail._deliver",
            return_value=("failed", "boom", None),
        ):
            out = _run(
                db_session,
                app,
                version,
                boss,
                op="review_complete",
                review_id=rid,
                to="sam@t.local",
            )
        assert "still open" in out["error"]
        assert _data(db_session, rid)["status"] == "ready_for_meeting"

    def test_complete_without_email_and_unlock_reopens_the_managers_side(
        self, db_session, boss, world, app_and_version
    ):
        app, version = app_and_version
        rid = world["review"]
        self._both_in(db_session, rid)
        done = _run(
            db_session, app, version, boss, op="review_complete", review_id=rid, to=""
        )
        assert done == {"ok": True, "email": None}
        assert _data(db_session, rid)["status"] == "completed"
        _run(db_session, app, version, boss, op="review_unlock", review_id=rid)
        data = _data(db_session, rid)
        assert data["status"] == "manager_in_progress"
        assert data["manager_submitted_at"] is None and data["completed_at"] is None
        assert data["employee_submitted_at"] and data["reopened_count"] == 1
        # The manager can genuinely resubmit now (Orbit's unlock couldn't).
        again = _run(
            db_session,
            app,
            version,
            boss,
            op="review_save_manager",
            review_id=rid,
            fields={"does_well": "Revised"},
        )
        assert again["ok"]


# ---------------------------------------------------------------------------
# Lists and admin actions
# ---------------------------------------------------------------------------


class TestAdmin:
    def test_every_review_is_for_admins_only(
        self, db_session, admin, boss, world, app_and_version
    ):
        app, version = app_and_version
        assert (
            "Only HR admins"
            in _run(db_session, app, version, boss, op="reviews")["error"]
        )
        rows = _run(db_session, app, version, admin, op="reviews")["reviews"]
        assert [r["id"] for r in rows] == [world["review"]]
        assert rows[0]["manager_name"] == "Mere Manager"
        assert rows[0]["label"] == "Both to complete"

    def test_no_manager_for_this_review_only_really_clears_it(
        self, db_session, admin, boss, world, app_and_version
    ):
        app, version = app_and_version
        rid = world["review"]
        _run(
            db_session,
            app,
            version,
            admin,
            op="review_set_manager",
            review_id=rid,
            manager_id=None,
            scope="review",
        )
        # Orbit's bug: the profile manager quietly applied anyway.
        row = _run(db_session, app, version, admin, op="reviews")["reviews"][0]
        assert row["manager_id"] is None
        assert (
            _run(db_session, app, version, boss, op="my_team_reviews")["current"] == []
        )
        # The profile is untouched.
        assert _data(db_session, world["sam"])["manager_id"] == world["mgr"]

    def test_review_plus_profile_moves_the_person_too(
        self, db_session, admin, other, world, app_and_version
    ):
        app, version = app_and_version
        _run(
            db_session,
            app,
            version,
            admin,
            op="review_set_manager",
            review_id=world["review"],
            manager_id=world["tama"],
            scope="profile",
        )
        assert _data(db_session, world["sam"])["manager_id"] == world["tama"]
        mine = _run(db_session, app, version, other, op="my_team_reviews")
        assert [r["id"] for r in mine["current"]] == [world["review"]]

    def test_delete_hides_and_restore_brings_back(
        self, db_session, admin, world, app_and_version
    ):
        app, version = app_and_version
        rid = world["review"]
        _run(db_session, app, version, admin, op="review_delete", review_id=rid)
        assert _run(db_session, app, version, admin, op="reviews")["reviews"] == []
        shown = _run(
            db_session, app, version, admin, op="reviews", include_deleted=True
        )
        assert shown["reviews"][0]["deleted_by_name"] == "Hana Admin"
        _run(db_session, app, version, admin, op="review_restore", review_id=rid)
        data = _data(db_session, rid)
        assert (
            "deleted_at" not in data
            and data["manager_feedback"]["does_well"] == "Draft"
        )

    def test_framework_switch_keeps_old_ratings(
        self, db_session, org, admin, world, app_and_version
    ):
        app, version = app_and_version
        fw2 = _rec(
            db_session,
            org,
            "capability_frameworks",
            {"name": "Kitchen", "categories": []},
        )
        _run(
            db_session,
            app,
            version,
            admin,
            op="review_set_framework",
            review_id=world["review"],
            framework_id=fw2,
        )
        data = _data(db_session, world["review"])
        assert data["framework_id"] == fw2
        assert data["capability_ratings"][0]["employee_level"] == 2

    def test_start_review_carries_goals_from_the_last_completed_not_deleted(
        self, db_session, org, admin, world, app_and_version
    ):
        app, version = app_and_version
        _rec(
            db_session,
            org,
            "performance_reviews",
            {
                "person_id": world["sam"],
                "status": "completed",
                "completed_at": "2026-06-01T00:00:00+00:00",
                "next_goals": [{"text": "Old goal"}],
            },
        )
        _rec(
            db_session,
            org,
            "performance_reviews",
            {
                "person_id": world["sam"],
                "status": "completed",
                "completed_at": "2026-09-01T00:00:00+00:00",
                "deleted_at": "2026-09-02T00:00:00+00:00",
                "next_goals": [{"text": "Deleted goal"}],
            },
        )
        out = _run(
            db_session,
            app,
            version,
            admin,
            op="review_start",
            person_id=world["sam"],
            framework_id=world["fw"],
            due_date=_iso(30),
        )
        data = _data(db_session, out["review"]["id"])
        assert [g["text"] for g in data["previous_goals"]] == ["Old goal"]
        assert data["status"] == "employee_in_progress"


class TestMyTeam:
    def test_my_team_lists_ready_to_meet_first_and_counts_the_badge(
        self, db_session, org, boss, world, app_and_version
    ):
        app, version = app_and_version
        both = _rec(
            db_session,
            org,
            "performance_reviews",
            {
                "person_id": world["sam"],
                "due_date": _iso(20),
                "status": "ready_for_meeting",
                "employee_submitted_at": "2026-10-01T00:00:00+00:00",
                "manager_submitted_at": "2026-10-02T00:00:00+00:00",
            },
        )
        waiting = _rec(
            db_session,
            org,
            "performance_reviews",
            {
                "person_id": world["sam"],
                "due_date": _iso(-3),
                "status": "employee_in_progress",
                "manager_submitted_at": "2026-10-02T00:00:00+00:00",
            },
        )
        d = _run(db_session, app, version, boss, op="my_team_reviews")
        assert [r["id"] for r in d["current"]] == [both, world["review"], waiting]
        assert d["current"][2]["overdue"] is True
        badge = _run(db_session, app, version, boss, op="review_badge")
        # Mine to do: the open one (not submitted) and the ready-to-meet one.
        assert badge == {"team_todo": 2, "linked": True, "admin": False}

    def test_a_login_with_no_person_has_no_team(
        self, db_session, admin, world, app_and_version
    ):
        app, version = app_and_version
        d = _run(db_session, app, version, admin, op="my_team_reviews")
        assert d["linked"] is False


# ---------------------------------------------------------------------------
# Review cycles and the nightly run
# ---------------------------------------------------------------------------


def _system(db, app, version, **params):
    out = AR.run_logic(
        db, None, app=app, version=version, user=AR.SYSTEM, venue_id=None, params=params
    )
    assert out["success"] is True, out.get("error")
    return out["data"]


class TestCycles:
    @pytest.fixture()
    def cyc(self, db_session, org, world):
        db = db_session
        fw = world["fw"]
        # These tests are about Norm running the cycles — switched on, as at
        # cut-over (off by default while Orbit is live).
        _rec(db, org, "app_settings", {"norm_runs_cycles": True})
        ana = _rec(
            db, org, "people", {"name": "Ana", "role": "team_member", "is_active": True}
        )
        bo = _rec(
            db, org, "people", {"name": "Bo", "role": "manager", "is_active": True}
        )
        gone = _rec(
            db,
            org,
            "people",
            {"name": "Cal", "role": "team_member", "is_active": False},
        )
        leaver = _rec(
            db, org, "people", {"name": "Dee", "role": "team_member", "is_active": True}
        )
        _rec(
            db,
            org,
            "performance_reviews",
            {
                "person_id": ana,
                "status": "completed",
                "completed_at": "2026-05-01T00:00:00+00:00",
                "next_goals": [{"text": "Carry me"}],
            },
        )
        # A first date long gone, so the roll-forward has work to do; a lead
        # long enough that the next round's window is already open.
        cycle = _rec(
            db,
            org,
            "review_cycles",
            {
                "name": "Quarterly",
                "cadence": "quarterly",
                "due_dates": ["2025-01-15"],
                "lead_days": 120,
                "is_active": True,
                "default_framework_id": fw,
                "assignments": [
                    {"person_id": ana, "framework_id": None},
                    {"person_id": gone, "framework_id": None},
                ],
                "auto_enroll_filter": {
                    "include": [
                        {
                            "conditions": [
                                {"attr": "role", "op": "any_of", "value": ["manager"]}
                            ]
                        }
                    ],
                    "exclude": [
                        {
                            "conditions": [
                                {"attr": "name", "op": "is", "value": "Mere Manager"}
                            ]
                        }
                    ],
                },
            },
        )
        return {"cycle": cycle, "ana": ana, "bo": bo, "gone": gone, "leaver": leaver}

    def _round(self, db, org, cycle):
        data = _data(db, cycle)
        return next(d for d in sorted(data["due_dates"]) if d >= TODAY.isoformat())

    def test_preview_writes_nothing(self, db_session, org, admin, cyc, app_and_version):
        app, version = app_and_version
        before = _data(db_session, cyc["cycle"])
        d = _run(
            db_session,
            app,
            version,
            admin,
            op="cycle_preview",
            cycle_id=cyc["cycle"],
            person_ids=[cyc["ana"], cyc["leaver"]],
        )
        plan = d["plan"]
        assert plan["window_open"] is True
        assert sorted(p["name"] for p in plan["created"]) == ["Ana", "Bo", "Dee"]
        assert _data(db_session, cyc["cycle"]) == before
        assert (
            not db_session.query(AppRecord)
            .filter(
                AppRecord.organization_id == org.id,
                AppRecord.collection == "performance_reviews",
                AppRecord.data["cycle_id"].astext == cyc["cycle"],
            )
            .count()
        )

    def test_manual_enrol_saves_but_creates_nothing_while_orbit_is_live(
        self, db_session, org, world, admin, app_and_version
    ):
        app, version = app_and_version
        cycle = _rec(
            db_session,
            org,
            "review_cycles",
            {
                "name": "Q",
                "cadence": "quarterly",
                "due_dates": [TODAY.isoformat()],
                "lead_days": 400,
                "is_active": True,
                "assignments": [],
                "default_framework_id": world["fw"],
            },
        )
        out = _run(
            db_session,
            app,
            version,
            admin,
            op="cycle_apply",
            cycle_id=cycle,
            person_ids=[world["sam"]],
        )
        assert out["saved_only"] is True
        made = (
            db_session.query(AppRecord)
            .filter(
                AppRecord.organization_id == org.id,
                AppRecord.collection == "performance_reviews",
                AppRecord.data["cycle_id"].astext == cycle,
            )
            .count()
        )
        assert made == 0

    def test_nightly_leaves_cycles_to_orbit_until_switched_on(
        self, db_session, org, world, app_and_version
    ):
        # No app_settings row: Orbit is still live, so Norm creates nothing.
        app, version = app_and_version
        _rec(
            db_session,
            org,
            "review_cycles",
            {
                "name": "Q",
                "cadence": "quarterly",
                "due_dates": [TODAY.isoformat()],
                "is_active": True,
                "assignments": [],
            },
        )
        out = _system(
            db_session,
            app,
            version,
            op="nightly",
            _schedule={"slot": TODAY.isoformat()},
        )
        assert out["cycles"] == [] and "Orbit" in out["cycles_skipped"]

    def test_the_nightly_run_rolls_forward_enrols_and_carries_goals(
        self, db_session, org, cyc, app_and_version
    ):
        app, version = app_and_version
        out = _system(
            db_session,
            app,
            version,
            op="nightly",
            _schedule={"slot": TODAY.isoformat()},
        )
        assert (
            out["cycles"][0]["created"] == 2
        )  # Ana (manual) + Bo (filter); not Cal (left)
        due = self._round(db_session, org, cyc["cycle"])
        data = _data(db_session, cyc["cycle"])
        assert len([d for d in data["due_dates"] if d >= TODAY.isoformat()]) >= 2
        made = {
            r.data["person_id"]: r.data
            for r in db_session.query(AppRecord).filter(
                AppRecord.organization_id == org.id,
                AppRecord.collection == "performance_reviews",
                AppRecord.data["cycle_id"].astext == cyc["cycle"],
            )
        }
        assert set(made) == {cyc["ana"], cyc["bo"]}
        assert made[cyc["ana"]]["due_date"] == due
        assert made[cyc["ana"]]["framework_id"] == data["default_framework_id"]
        assert [g["text"] for g in made[cyc["ana"]]["previous_goals"]] == ["Carry me"]
        # A second night is a no-op.
        again = _system(
            db_session,
            app,
            version,
            op="nightly",
            _schedule={"slot": TODAY.isoformat()},
        )
        assert again["cycles"][0]["created"] == 0

    def test_a_deleted_review_blocks_the_round_and_an_untouched_orphan_is_withdrawn(
        self, db_session, org, admin, cyc, app_and_version
    ):
        app, version = app_and_version
        _system(
            db_session,
            app,
            version,
            op="nightly",
            _schedule={"slot": TODAY.isoformat()},
        )
        due = self._round(db_session, org, cyc["cycle"])
        # Dee had a review this round but isn't in the cycle; Ana's was deleted.
        orphan = _rec(
            db_session,
            org,
            "performance_reviews",
            {
                "person_id": cyc["leaver"],
                "cycle_id": cyc["cycle"],
                "due_date": due,
                "status": "employee_in_progress",
                "employee_reflection": {},
                "manager_feedback": {},
            },
        )
        touched = _rec(
            db_session,
            org,
            "performance_reviews",
            {
                "person_id": cyc["leaver"],
                "cycle_id": cyc["cycle"],
                "due_date": due,
                "status": "employee_in_progress",
                "manager_feedback": {"does_well": "started"},
            },
        )
        ana_review = next(
            r
            for r in db_session.query(AppRecord).filter(
                AppRecord.organization_id == org.id,
                AppRecord.collection == "performance_reviews",
                AppRecord.data["cycle_id"].astext == cyc["cycle"],
            )
            if r.data["person_id"] == cyc["ana"]
        )
        ana_review.data = {**ana_review.data, "deleted_at": "2026-10-01T00:00:00+00:00"}
        db_session.flush()
        plan = _run(
            db_session, app, version, admin, op="cycle_preview", cycle_id=cyc["cycle"]
        )["plan"]
        assert [p["name"] for p in plan["deletion_blocked"]] == ["Ana"]
        assert [p["review_id"] for p in plan["removed"]] == [orphan]
        applied = _run(
            db_session, app, version, admin, op="cycle_apply", cycle_id=cyc["cycle"]
        )
        assert applied["plan"]["created"] == []
        withdrawn = _data(db_session, orphan)
        assert withdrawn["deleted_at"] and withdrawn["withdrawn"] is True
        assert "deleted_at" not in _data(db_session, touched)

    def test_apply_saves_the_assignments(self, db_session, admin, cyc, app_and_version):
        app, version = app_and_version
        _run(
            db_session,
            app,
            version,
            admin,
            op="cycle_apply",
            cycle_id=cyc["cycle"],
            person_ids=[cyc["leaver"]],
            auto_enroll_filter=None,
        )
        data = _data(db_session, cyc["cycle"])
        assert data["assignments"] == [
            {"person_id": cyc["leaver"], "framework_id": None}
        ]
        assert data["auto_enroll_filter"] is None

    def test_cycles_are_for_admins_and_the_nightly_only_for_norm(
        self, db_session, admin, boss, cyc, app_and_version
    ):
        app, version = app_and_version
        assert (
            "Only an HR admin"
            in _run(db_session, app, version, boss, op="review_cycles")["error"]
        )
        assert (
            "its schedule"
            in _run(db_session, app, version, admin, op="nightly")["error"]
        )
        listed = _run(db_session, app, version, admin, op="review_cycles")
        assert listed["cycles"][0]["plan"]["matched_count"] == 2

    def test_month_steps_clamp_to_the_month_end(
        self, db_session, org, admin, app_and_version
    ):
        app, version = app_and_version
        cycle = _rec(
            db_session,
            org,
            "review_cycles",
            {
                "name": "Monthly",
                "cadence": "custom:1:months",
                "due_dates": ["2025-01-31"],
                "lead_days": 0,
                "is_active": True,
            },
        )
        plan = _run(
            db_session, app, version, admin, op="cycle_preview", cycle_id=cycle
        )["plan"]
        assert "2025-02-28" in plan["added_due_dates"]
        assert "2025-03-31" in plan["added_due_dates"]

    def test_edit_keeps_rolled_dates_unless_the_first_date_changes(
        self, db_session, admin, cyc, app_and_version
    ):
        app, version = app_and_version
        _run(db_session, app, version, admin, op="cycle_apply", cycle_id=cyc["cycle"])
        rolled = _data(db_session, cyc["cycle"])["due_dates"]
        assert len(rolled) > 1
        fields = {
            "name": "Q",
            "cadence": "quarterly",
            "first_date": "2025-01-15",
            "lead_days": 120,
        }
        _run(
            db_session,
            app,
            version,
            admin,
            op="save_review_cycle",
            cycle_id=cyc["cycle"],
            fields=fields,
        )
        assert _data(db_session, cyc["cycle"])["due_dates"] == rolled
        fields["first_date"] = _iso(60)
        _run(
            db_session,
            app,
            version,
            admin,
            op="save_review_cycle",
            cycle_id=cyc["cycle"],
            fields=fields,
        )
        assert _data(db_session, cyc["cycle"])["due_dates"] == [_iso(60)]


class TestAutoEnrolFilter:
    def test_include_groups_or_exclude_wins_empty_matches_nobody(
        self, db_session, org, admin, app_and_version
    ):
        app, version = app_and_version
        _rec(
            db_session,
            org,
            "people",
            {
                "name": "Kiri",
                "role": "manager",
                "is_active": True,
                "orbit_id": "o-kiri",
            },
        )
        _rec(
            db_session,
            org,
            "people",
            {
                "name": "Lou",
                "role": "team_member",
                "is_active": True,
                "email": "lou@t.local",
            },
        )
        _rec(
            db_session,
            org,
            "people",
            {"name": "Max", "role": "manager", "is_active": False},
        )

        def names(f):
            d = _run(
                db_session,
                app,
                version,
                admin,
                op="auto_enroll_preview",
                auto_enroll_filter=f,
            )
            return sorted(p["name"] for p in d["people"])

        assert names({"include": [], "exclude": []}) == []
        assert names({"include": [{"conditions": []}]}) == ["Kiri", "Lou"]
        assert names(
            {
                "include": [
                    {"conditions": [{"attr": "role", "op": "is", "value": "manager"}]},
                    {
                        "conditions": [
                            {"attr": "has_email", "op": "is", "value": "true"}
                        ]
                    },
                ]
            }
        ) == ["Kiri", "Lou"]
        assert names(
            {
                "include": [{"conditions": []}],
                "exclude": [
                    {
                        "conditions": [
                            {"attr": "team_member", "op": "is", "value": "o-kiri"}
                        ]
                    }
                ],
            }
        ) == ["Lou"]


# ---------------------------------------------------------------------------
# The manager digest
# ---------------------------------------------------------------------------


class TestDigest:
    @pytest.fixture()
    def team(self, db_session, org, world):
        db = db_session
        both = _rec(
            db,
            org,
            "performance_reviews",
            {
                "person_id": world["sam"],
                "due_date": _iso(3),
                "status": "ready_for_meeting",
                "employee_submitted_at": "2026-10-01T00:00:00+00:00",
                "manager_submitted_at": "2026-10-02T00:00:00+00:00",
            },
        )
        waiting = _rec(
            db,
            org,
            "performance_reviews",
            {
                "person_id": world["sam"],
                "due_date": _iso(3),
                "status": "employee_in_progress",
                "manager_submitted_at": "2026-10-02T00:00:00+00:00",
            },
        )
        return {"both": both, "waiting": waiting}

    def test_preview_renders_one_digest_without_sending_or_logging(
        self, db_session, org, admin, boss, world, team, app_and_version
    ):
        app, version = app_and_version
        with patch("app.services.app_email.AppEmail._deliver") as deliver:
            d = _run(
                db_session,
                app,
                version,
                admin,
                op="review_digest_run",
                _email_mode="dry_run",
            )
        deliver.assert_not_called()
        assert d["planned"] == 1
        e = d["emails"][0]
        assert e["status"] == "dry_run" and e["to"] == [boss.email]
        assert e["items"] == 2  # the open one + ready to meet; not the waiting one
        assert e["subject"] == "You have 1 performance review to complete"
        assert "Ready for your one-on-one" in e["html"] and "Sam Tui" in e["html"]
        # The links open Norm Performance on that review.
        assert "/apps/performance?view=my_team" in e["html"]
        assert world["review"] in e["html"]
        assert (
            not db_session.query(AppRecord)
            .filter_by(organization_id=org.id, collection="performance_review_emails")
            .count()
        )

    def test_live_digests_come_only_from_the_nightly(
        self, db_session, admin, team, app_and_version
    ):
        app, version = app_and_version
        out = _run(db_session, app, version, admin, op="review_digest_run")
        assert "nightly run" in out["error"]

    def test_the_nightly_sends_once_a_day(
        self, db_session, org, boss, world, team, app_and_version, monkeypatch
    ):
        app, version = app_and_version
        set_email_enabled(db_session, org.id, "performance", True, None)
        monkeypatch.setenv("APP_EMAIL_ALLOW_NONPROD", "1")
        slot = TODAY.isoformat()
        with patch(
            "app.services.app_email.AppEmail._deliver",
            return_value=("sent", None, "pid"),
        ) as deliver:
            first = _system(
                db_session, app, version, op="nightly", _schedule={"slot": slot}
            )
            second = _system(
                db_session, app, version, op="nightly", _schedule={"slot": slot}
            )
        assert first["digests"] == {"planned": 1, "statuses": {"sent": 1}}
        assert second["digests"]["planned"] == 0
        assert deliver.call_count == 1
        assert deliver.call_args.args[0] == [boss.email]
        logged = (
            db_session.query(AppRecord)
            .filter_by(organization_id=org.id, collection="performance_review_emails")
            .one()
        )
        assert logged.data["kind"] == "manager_digest"
        assert logged.data["person_id"] == world["mgr"]
        assert sorted(logged.data["review_ids"]) == sorted(
            [world["review"], team["both"]]
        )
        key = (
            db_session.query(EmailLog)
            .filter_by(organization_id=org.id, app_slug="performance")
            .one()
        )
        assert key.idempotency_key == f"digest:{world['mgr']}:{slot}"

    def test_while_email_is_off_nothing_is_sent_or_logged(
        self, db_session, org, world, team, app_and_version
    ):
        app, version = app_and_version
        with patch("app.services.app_email.AppEmail._deliver") as deliver:
            out = _system(
                db_session,
                app,
                version,
                op="nightly",
                _schedule={"slot": TODAY.isoformat()},
            )
        deliver.assert_not_called()
        assert out["digests"]["statuses"] == {"disabled": 1}
        assert (
            not db_session.query(AppRecord)
            .filter_by(organization_id=org.id, collection="performance_review_emails")
            .count()
        )

    def test_no_digest_until_something_changes_or_the_interval_passes(
        self, db_session, org, admin, world, team, app_and_version
    ):
        app, version = app_and_version
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _rec(
            db_session,
            org,
            "performance_review_emails",
            {
                "person_id": world["mgr"],
                "kind": "manager_digest",
                "status": "sent",
                "is_test": False,
                "sent_at": yesterday,
                "review_ids": [world["review"], team["both"]],
            },
        )
        # Sam's open review is due in 10 days: a 3-day interval, 1 day since.
        quiet = _run(
            db_session,
            app,
            version,
            admin,
            op="review_digest_run",
            _email_mode="dry_run",
        )
        assert quiet["planned"] == 0
        # Sam submits: the manager hears about it.
        rec = db_session.query(AppRecord).filter_by(id=world["review"]).one()
        rec.data = {
            **rec.data,
            "employee_submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        db_session.flush()
        loud = _run(
            db_session,
            app,
            version,
            admin,
            op="review_digest_run",
            _email_mode="dry_run",
        )
        assert loud["planned"] == 1

    def test_send_test_goes_to_the_viewer_only(
        self, db_session, admin, world, team, app_and_version
    ):
        app, version = app_and_version
        with patch(
            "app.services.app_email.AppEmail._deliver",
            return_value=("sent", None, "pid"),
        ) as deliver:
            d = _run(
                db_session,
                app,
                version,
                admin,
                op="review_digest_run",
                _email_mode="test",
            )
        assert d["emails"][0]["status"] == "sent"
        to, cc, _reply, subject, _html = deliver.call_args.args
        assert to == [admin.email] and cc == [] and subject.startswith("[TEST] ")


class TestRawRowsStayBehindTheFieldRules:
    """`storage.logic_only`: the review rows (pay, private answers), pay rates
    and the email history are readable only through performance.py's ops — the
    direct records endpoints (and the page's norm.storage) are refused, or
    anyone in the org could read the raw rows and skip every rule above."""

    def test_the_direct_door_refuses_and_the_ops_still_work(
        self, db_session, admin, boss, world, app_and_version
    ):
        """Each logic-only collection, in one world (it was four parametrize
        cases, each building the world)."""
        from fastapi import HTTPException

        app, version = app_and_version
        for collection in (
            "performance_reviews",
            "remuneration",
            "performance_review_emails",
            "performance_review_email_settings",
        ):
            with pytest.raises(HTTPException) as err:
                AR.store_list(
                    db_session,
                    app=app,
                    version=version,
                    user=admin,
                    collection=collection,
                )
            assert err.value.status_code == 403, collection
        with pytest.raises(HTTPException) as err:
            AR.store_get(
                db_session,
                app=app,
                version=version,
                user=boss,
                collection="performance_reviews",
                record_id=world["review"],
            )
        assert err.value.status_code == 403
        # ...while the app's own logic still reads them, with its rules applied.
        d = _run(db_session, app, version, boss, op="review", review_id=world["review"])
        assert d["review"]["proposed_pay_rate"] == 31.5
        assert d["current_rate"]["rate"] == 28.0

    def test_people_stay_on_the_ordinary_door(
        self, db_session, admin, world, app_and_version
    ):
        app, version = app_and_version
        rows = AR.store_list(
            db_session, app=app, version=version, user=admin, collection="people"
        )
        assert {r["id"] for r in rows} >= {world["sam"], world["mgr"]}


# ---------------------------------------------------------------------------
# Capability frameworks (moved from Norm Training with the reviews)
# ---------------------------------------------------------------------------


class TestCapabilityFrameworks:
    """P4: capability frameworks — a role's competency map (Orbit's
    `capability_frameworks`), carried across whole with categories, capabilities
    and L1/L2/L3 descriptors nested inside."""

    def test_list_counts_categories_and_sorts_by_name(
        self, db_session, org, admin, app_and_version
    ):
        app, version = app_and_version
        _rec(
            db_session,
            org,
            "capability_frameworks",
            {"name": "Sales Manager", "categories": [{"name": "A"}, {"name": "B"}]},
        )
        _rec(
            db_session,
            org,
            "capability_frameworks",
            {"name": "Bar Host", "categories": []},
        )
        d = _run(db_session, app, version, admin, op="frameworks")
        assert [f["name"] for f in d["frameworks"]] == ["Bar Host", "Sales Manager"]
        by_name = {f["name"]: f for f in d["frameworks"]}
        assert by_name["Sales Manager"]["category_count"] == 2
        assert by_name["Bar Host"]["category_count"] == 0

    def test_get_returns_nested_capabilities(
        self, db_session, org, admin, app_and_version
    ):
        app, version = app_and_version
        fid = _rec(
            db_session,
            org,
            "capability_frameworks",
            {
                "name": "Sales Manager",
                "categories": [
                    {
                        "name": "Standards",
                        "capabilities": [
                            {"name": "Appearance", "level1_descriptor": "Tidy"}
                        ],
                    }
                ],
            },
        )
        d = _run(db_session, app, version, admin, op="framework", framework_id=fid)
        cap = d["framework"]["categories"][0]["capabilities"][0]
        assert cap["name"] == "Appearance"
        assert cap["level1_descriptor"] == "Tidy"

    def test_create_seeds_empty_then_update_preserves_name(
        self, db_session, admin, app_and_version
    ):
        app, version = app_and_version
        created = _run(
            db_session,
            app,
            version,
            admin,
            op="save_framework",
            fields={"name": "New Role", "is_active": True},
        )["framework"]
        assert created["name"] == "New Role"
        assert created["categories"] == []  # a new framework starts empty
        updated = _run(
            db_session,
            app,
            version,
            admin,
            op="save_framework",
            framework_id=created["id"],
            fields={"role_label": "Floor Lead", "is_active": False},
        )["framework"]
        assert updated["role_label"] == "Floor Lead"
        assert updated["is_active"] is False
        assert updated["name"] == "New Role"  # untouched fields survive
        assert updated["categories"] == []  # and so does the category map

    def test_save_requires_a_name(self, db_session, admin, app_and_version):
        app, version = app_and_version
        d = _run(
            db_session,
            app,
            version,
            admin,
            op="save_framework",
            fields={"is_active": True},
        )
        assert "name" in d["error"]

    def test_save_categories_assigns_ids_and_persists(
        self, db_session, admin, app_and_version
    ):
        """B5: frameworks are editable to the category/capability level — the
        UI sends the whole tree and every node gets a stable id."""
        app, version = app_and_version
        created = _run(
            db_session,
            app,
            version,
            admin,
            op="save_framework",
            fields={"name": "Bar Host"},
        )["framework"]
        fid = created["id"]
        # Save a category with one capability, no ids supplied.
        saved = _run(
            db_session,
            app,
            version,
            admin,
            op="save_framework",
            framework_id=fid,
            fields={
                "categories": [
                    {
                        "name": "Standards",
                        "capabilities": [
                            {"name": "Appearance", "level1_descriptor": "Tidy"}
                        ],
                    }
                ]
            },
        )["framework"]
        cat = saved["categories"][0]
        assert cat.get("id")  # server assigned an id
        cap = cat["capabilities"][0]
        assert cap.get("id") and cap["level1_descriptor"] == "Tidy"
        # Re-saving with the ids present must preserve them.
        cat_id, cap_id = cat["id"], cap["id"]
        again = _run(
            db_session,
            app,
            version,
            admin,
            op="save_framework",
            framework_id=fid,
            fields={"categories": saved["categories"]},
        )["framework"]
        assert again["categories"][0]["id"] == cat_id
        assert again["categories"][0]["capabilities"][0]["id"] == cap_id


# ---------------------------------------------------------------------------
# Opening the app, and its Orbit banner
# ---------------------------------------------------------------------------


class TestLocateAndSync:
    def test_a_review_id_resolves_and_a_missing_one_says_so(
        self, db_session, admin, world, app_and_version
    ):
        app, version = app_and_version
        out = _run(db_session, app, version, admin, op="locate", review=world["review"])
        assert out == {"review_id": world["review"]}
        gone = _run(
            db_session, app, version, admin, op="locate", review=str(uuid.uuid4())
        )
        assert "no longer exists" in gone["error"]

    def test_sync_status_is_the_newest_row_an_orbit_refresh_brought(
        self, db_session, org, admin, app_and_version
    ):
        app, version = app_and_version
        assert _run(db_session, app, version, admin, op="sync_status") == {
            "refreshed_at": None
        }
        old = _rec(db_session, org, "people", {"name": "A", "orbit_id": "o1"})
        new = _rec(db_session, org, "performance_reviews", {"orbit_id": "o2"})
        mine = _rec(db_session, org, "review_cycles", {"name": "Made in Norm"})
        # A Training-only collection isn't this app's to read.
        _rec(db_session, org, "completions", {"orbit_id": "o3"})
        stamps = {
            old: datetime(2026, 10, 1, tzinfo=timezone.utc),
            new: datetime(2026, 10, 9, tzinfo=timezone.utc),
            mine: datetime(2026, 10, 11, tzinfo=timezone.utc),
        }
        for row in db_session.query(AppRecord).filter(AppRecord.id.in_(list(stamps))):
            row.created_at = stamps[row.id]
        db_session.flush()
        out = _run(db_session, app, version, admin, op="sync_status")
        assert out["refreshed_at"].startswith("2026-10-09")


# ---------------------------------------------------------------------------
# The move: Training no longer has any of this
# ---------------------------------------------------------------------------


class TestTrainingNoLongerHasReviews:
    def test_its_review_ops_are_gone(self, db_session, admin, world, training):
        """Every review op Training used to have, in one world (it was fifteen
        parametrize cases, each building the world and both bindings)."""
        app, version = training
        for op in (
            "review_badge",
            "reviews",
            "my_team_reviews",
            "review",
            "review_save_manager",
            "review_start",
            "review_cycles",
            "save_review_cycle",
            "cycle_preview",
            "auto_enroll_preview",
            "review_emails",
            "review_digest_run",
            "set_norm_runs_cycles",
            "frameworks",
            "save_framework",
        ):
            out = _run(
                db_session, app, version, admin, op=op, review_id=world["review"]
            )
            assert out == {"error": f"unknown op '{op}'"}

    def test_the_nightly_is_performances_now(self, db_session, training):
        app, version = training
        assert not version.spec.get("schedules")
        assert not version.spec.get("email")
        out = _system(db_session, app, version, op="nightly")
        assert out == {"error": "unknown op 'nightly'"}

    def test_it_cannot_read_the_review_rows(self, db_session, admin, world, training):
        """Each review collection, in one world (it was five parametrize cases)."""
        from fastapi import HTTPException

        app, version = training
        for collection in (
            "performance_reviews",
            "remuneration",
            "review_cycles",
            "capability_frameworks",
            "performance_review_emails",
        ):
            with pytest.raises(HTTPException) as err:
                AR.store_list(
                    db_session,
                    app=app,
                    version=version,
                    user=admin,
                    collection=collection,
                )
            assert err.value.status_code == 403, collection
            assert "not a collection this app declares" in err.value.detail

    def test_a_review_link_is_not_an_input_training_takes(
        self, db_session, admin, world, training
    ):
        from app.services.app_components import declared_components

        app, version = training
        comp = declared_components(version.spec, slug="training", name="Training")[0]
        assert {i["name"] for i in comp["inputs"]} == {"view", "program", "person"}
        out = _run(db_session, app, version, admin, op="locate", review=world["review"])
        assert out == {"program_id": None, "person_id": None}

    def test_training_shares_the_namespace_with_performance(self, training):
        _, version = training
        storage = version.spec["storage"]
        assert storage["namespace"] == "hr_suite"
        assert set(storage["shared_with"]) == {"hiring", "performance"}
