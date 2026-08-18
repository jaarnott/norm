"""The Hiring app, and the shared-storage boundary it exists to prove.

Two things are under test. First the pipeline state machine, where the rule is
that **stages are data and status is derived** — a job's stages are rows the
user renames freely, so nothing may switch on a stage name, only on its
``stage_type``. Second the hand-off: hiring someone has to put them where the
Training app can enrol them, which is the entire reason these two apps share a
storage namespace instead of each owning a copy of the staff list.
"""

import pathlib
import uuid

import pytest
from fastapi import HTTPException

from app.db.models import App, AppRecord, AppVersion, Role
from app.services import app_runtime as AR
from tests.conftest import _make_membership, _make_organization, _make_user

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "app" / "fixtures" / "apps"


def _spec(slug):
    import json

    return json.loads((FIXTURES / f"{slug}.json").read_text())["spec"]


@pytest.fixture()
def org(db_session):
    return _make_organization(db_session)


@pytest.fixture()
def author(db_session, org):
    user = _make_user(db_session, email=f"{uuid.uuid4().hex[:8]}@t.local")
    mem = _make_membership(db_session, user, org)
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=f"r-{uuid.uuid4().hex[:6]}",
        display_name="r",
        permissions=["hr:read", "apps:build"],
    )
    db_session.add(role)
    db_session.flush()
    mem.role_id = role.id
    db_session.flush()
    return user


def _install(db, org, author, slug):
    app = App(
        organization_id=org.id,
        created_by=author.id,
        slug=slug,
        name=slug.title(),
        agent="hr",
        visibility="private",
    )
    db.add(app)
    db.flush()
    version = AppVersion(
        app_id=app.id,
        version=1,
        spec=_spec(slug),
        ui_source="<div/>",
        logic_source=(FIXTURES / f"{slug}.py").read_text(),
        created_by=author.id,
    )
    db.add(version)
    db.flush()
    app.current_version_id = version.id
    db.flush()
    return app, version


@pytest.fixture()
def hiring(db_session, org, author):
    return _install(db_session, org, author, "hiring")


def _rec(db, org, user, collection, data):
    row = AppRecord(
        id=str(uuid.uuid4()),
        namespace="hr_suite",
        organization_id=org.id,
        collection=collection,
        data=data,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    return row.id


def _run(db, app_version, user, **params):
    app, version = app_version
    out = AR.run_logic(
        db, None, app=app, version=version, user=user, venue_id=None, params=params
    )
    assert out["success"] is True, out.get("error")
    return out["data"]


def _job_with_stages(db, org, author, title="Bar Team"):
    job = _rec(db, org, author, "job_openings", {"title": title, "status": "open"})
    stages = {}
    for i, (name, kind) in enumerate(
        [
            ("Applied", "active"),
            ("Trial Shift", "active"),
            ("Hired", "hired"),
            ("Rejected", "rejected"),
        ]
    ):
        stages[name] = _rec(
            db,
            org,
            author,
            "pipeline_stages",
            {"job_id": job, "name": name, "stage_type": kind, "sort_index": i},
        )
    return job, stages


def _applicant(db, org, author, job, stage_id, email="maia@example.com"):
    candidate = _rec(
        db,
        org,
        author,
        "candidates",
        {"first_name": "Maia", "last_name": "Ngata", "email": email},
    )
    application = _rec(
        db,
        org,
        author,
        "applications",
        {
            "job_id": job,
            "candidate_id": candidate,
            "stage_id": stage_id,
            "status": "active",
        },
    )
    return candidate, application


class TestStatusIsDerivedFromStageType:
    """Stage NAMES are user data. Only `stage_type` may drive status."""

    def test_moving_to_a_hired_stage_sets_hired(self, db_session, org, author, hiring):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(
            db_session,
            hiring,
            author,
            op="move",
            application_id=application,
            stage_id=stages["Hired"],
        )
        assert out["status"] == "hired"
        row = AR.store_get(
            db_session,
            app=hiring[0],
            version=hiring[1],
            user=author,
            collection="applications",
            record_id=application,
        )
        assert row["hired_at"] and row["rejected_at"] is None

    def test_moving_to_a_rejected_stage_sets_rejected(
        self, db_session, org, author, hiring
    ):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(
            db_session,
            hiring,
            author,
            op="move",
            application_id=application,
            stage_id=stages["Rejected"],
        )
        assert out["status"] == "rejected"

    def test_coming_back_to_an_active_stage_clears_the_stamps(
        self, db_session, org, author, hiring
    ):
        """The half Orbit's API forgets: its UI clears these on the way back and
        its API does not, so a rejected-then-revived candidate keeps a
        rejection date forever."""
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        _run(
            db_session,
            hiring,
            author,
            op="move",
            application_id=application,
            stage_id=stages["Rejected"],
        )
        _run(
            db_session,
            hiring,
            author,
            op="move",
            application_id=application,
            stage_id=stages["Trial Shift"],
        )
        row = AR.store_get(
            db_session,
            app=hiring[0],
            version=hiring[1],
            user=author,
            collection="applications",
            record_id=application,
        )
        assert row["status"] == "active"
        assert row["rejected_at"] is None and row["hired_at"] is None

    def test_a_stage_from_another_job_is_refused(self, db_session, org, author, hiring):
        """Otherwise a candidate can be moved into another job's pipeline and
        disappear from both boards."""
        job_a, stages_a = _job_with_stages(db_session, org, author, title="Bar")
        _, stages_b = _job_with_stages(db_session, org, author, title="Kitchen")
        _, application = _applicant(db_session, org, author, job_a, stages_a["Applied"])
        out = _run(
            db_session,
            hiring,
            author,
            op="move",
            application_id=application,
            stage_id=stages_b["Applied"],
        )
        assert "different job" in out["error"]

    def test_every_move_is_recorded(self, db_session, org, author, hiring):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        _run(
            db_session,
            hiring,
            author,
            op="move",
            application_id=application,
            stage_id=stages["Trial Shift"],
        )
        detail = _run(
            db_session, hiring, author, op="candidate", application_id=application
        )
        assert any(a["activity_type"] == "stage_change" for a in detail["activity"])
        assert "Trial Shift" in detail["activity"][0]["summary"]


class TestHireIsTheHandoff:
    """Hiring writes into the namespace the Training app reads. This is the
    behaviour that justifies shared storage rather than two staff lists."""

    def test_hiring_creates_the_person_and_links_them(
        self, db_session, org, author, hiring
    ):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(
            db_session,
            hiring,
            author,
            op="hire",
            application_id=application,
            start_date="2026-09-01",
        )
        assert out["ok"] is True and out["person_id"]
        person = AR.store_get(
            db_session,
            app=hiring[0],
            version=hiring[1],
            user=author,
            collection="people",
            record_id=out["person_id"],
        )
        assert person["name"] == "Maia Ngata"
        assert person["start_date"] == "2026-09-01"
        assert person["is_active"] is True
        row = AR.store_get(
            db_session,
            app=hiring[0],
            version=hiring[1],
            user=author,
            collection="applications",
            record_id=application,
        )
        assert row["status"] == "hired"
        assert row["person_id"] == out["person_id"]

    def test_hiring_twice_does_not_mint_a_second_person(
        self, db_session, org, author, hiring
    ):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        first = _run(db_session, hiring, author, op="hire", application_id=application)
        second = _run(db_session, hiring, author, op="hire", application_id=application)
        assert first["person_id"] == second["person_id"]
        people = AR.store_list(
            db_session,
            app=hiring[0],
            version=hiring[1],
            user=author,
            collection="people",
        )
        assert len(people) == 1

    def test_an_existing_person_with_that_email_is_reused(
        self, db_session, org, author, hiring
    ):
        """Rehiring someone already on the team must not create a duplicate —
        their training history hangs off the person record."""
        existing = _rec(
            db_session,
            org,
            author,
            "people",
            {"name": "Maia Ngata", "email": "maia@example.com", "is_active": True},
        )
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(db_session, hiring, author, op="hire", application_id=application)
        assert out["person_id"] == existing

    def test_a_job_with_no_hired_stage_says_so(self, db_session, org, author, hiring):
        job = _rec(
            db_session, org, author, "job_openings", {"title": "Odd", "status": "open"}
        )
        stage = _rec(
            db_session,
            org,
            author,
            "pipeline_stages",
            {"job_id": job, "name": "Applied", "stage_type": "active", "sort_index": 0},
        )
        _, application = _applicant(db_session, org, author, job, stage)
        out = _run(db_session, hiring, author, op="hire", application_id=application)
        assert "no 'hired' stage" in out["error"]

    def test_the_training_app_sees_someone_hiring_created(
        self, db_session, org, author, hiring
    ):
        """The cross-app read, end to end: two separately-authored apps, one
        namespace, no copying."""
        training = _install(db_session, org, author, "training")
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        _run(db_session, hiring, author, op="hire", application_id=application)

        seen = AR.store_list(
            db_session,
            app=training[0],
            version=training[1],
            user=author,
            collection="people",
        )
        assert [p["name"] for p in seen] == ["Maia Ngata"]

    def test_an_uninvited_app_still_cannot_read_that_namespace(
        self, db_session, org, author, hiring
    ):
        """The sharing is by invitation, not by naming. Training owns hr_suite
        and lists 'hiring'; anything else is refused at save time."""
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session,
                author,
                {
                    "name": "Nosy",
                    "slug": "nosy",
                    "agent": "hr",
                    "spec": {
                        "actions": [],
                        "scopes": [],
                        "storage": {
                            "namespace": "hr_suite",
                            "collections": ["people"],
                        },
                    },
                    "ui_source": "<div/>",
                },
            )
        assert "shared_with" in str(e.value.detail)


class TestBoardAndPipeline:
    def test_the_board_counts_by_derived_status(self, db_session, org, author, hiring):
        job, stages = _job_with_stages(db_session, org, author)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"], "a@x.com")
        _, a2 = _applicant(db_session, org, author, job, stages["Applied"], "b@x.com")
        _run(
            db_session,
            hiring,
            author,
            op="move",
            application_id=a2,
            stage_id=stages["Hired"],
        )
        jobs = _run(db_session, hiring, author, op="board")["jobs"]
        assert jobs[0]["application_count"] == 2
        assert jobs[0]["active_count"] == 1
        assert jobs[0]["hired_count"] == 1

    def test_the_pipeline_groups_candidates_under_their_stage(
        self, db_session, org, author, hiring
    ):
        job, stages = _job_with_stages(db_session, org, author)
        _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(db_session, hiring, author, op="pipeline", job_id=job)
        names = {
            s["name"]: [a["candidate_name"] for a in s["applications"]]
            for s in out["stages"]
        }
        assert names["Applied"] == ["Maia Ngata"]
        assert names["Hired"] == []
        # stages come back in the order the user arranged them
        assert [s["name"] for s in out["stages"]] == [
            "Applied",
            "Trial Shift",
            "Hired",
            "Rejected",
        ]

    def test_a_note_is_recorded_with_its_author(self, db_session, org, author, hiring):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        _run(
            db_session,
            hiring,
            author,
            op="note",
            application_id=application,
            body="Strong trial shift",
            actor="Jane",
        )
        detail = _run(
            db_session, hiring, author, op="candidate", application_id=application
        )
        assert detail["notes"][0]["body"] == "Strong trial shift"
        assert detail["notes"][0]["author"] == "Jane"

    def test_an_empty_note_is_refused(self, db_session, org, author, hiring):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(
            db_session,
            hiring,
            author,
            op="note",
            application_id=application,
            body="   ",
        )
        assert "needs some text" in out["error"]


class TestHireDetails:
    def test_the_first_hired_stage_wins(self, db_session, org, author, hiring):
        """The loop used to keep overwriting, so a job with two hired stages
        landed the candidate in the last one."""
        job = _rec(
            db_session, org, author, "job_openings", {"title": "J", "status": "open"}
        )
        first = _rec(
            db_session,
            org,
            author,
            "pipeline_stages",
            {"job_id": job, "name": "Hired", "stage_type": "hired", "sort_index": 1},
        )
        _rec(
            db_session,
            org,
            author,
            "pipeline_stages",
            {
                "job_id": job,
                "name": "Hired (late)",
                "stage_type": "hired",
                "sort_index": 2,
            },
        )
        _rec(
            db_session,
            org,
            author,
            "pipeline_stages",
            {"job_id": job, "name": "Applied", "stage_type": "active", "sort_index": 0},
        )
        _, application = _applicant(db_session, org, author, job, None)
        _run(db_session, hiring, author, op="hire", application_id=application)
        row = AR.store_get(
            db_session,
            app=hiring[0],
            version=hiring[1],
            user=author,
            collection="applications",
            record_id=application,
        )
        assert row["stage_id"] == first

    def test_role_and_start_date_reach_the_person(
        self, db_session, org, author, hiring
    ):
        job, stages = _job_with_stages(db_session, org, author)
        _, application = _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(
            db_session,
            hiring,
            author,
            op="hire",
            application_id=application,
            role="manager",
            start_date="2026-09-15",
        )
        person = AR.store_get(
            db_session,
            app=hiring[0],
            version=hiring[1],
            user=author,
            collection="people",
            record_id=out["person_id"],
        )
        assert person["role"] == "manager"
        assert person["start_date"] == "2026-09-15"
