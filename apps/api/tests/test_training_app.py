"""The Training app's server-side logic.

This is the first app to run on `app_records`, and it carries three rules from
Orbit that a naive rebuild silently loses. Each one below is a rule, not a
formatting detail — get any of them wrong and the tracker looks fine while
telling people the wrong thing about who is trained.

The logic is exec'd exactly as production does it (through the sandbox, with
`store` bound to the real door), so these also prove the fixture's source
actually runs under the AST guard.
"""

import pathlib
import uuid

import pytest

from app.db.models import App, AppRecord, AppVersion, Role
from app.services import app_runtime as AR
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "app" / "fixtures" / "apps"
SPEC = {
    "actions": [],
    "writes": [],
    "scopes": ["mcp:hr:read"],
    "storage": {
        "namespace": "hr_suite",
        "collections": [
            "people",
            "programs",
            "modules",
            "sections",
            "content",
            "assignments",
            "completions",
            "plans",
            "plan_sections",
            "capability_frameworks",
        ],
    },
}


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
        permissions=["hr:read"],
    )
    db_session.add(role)
    db_session.flush()
    mem.role_id = role.id
    db_session.flush()
    return user


@pytest.fixture()
def app_and_version(db_session, org, author):
    app = App(
        organization_id=org.id,
        created_by=author.id,
        slug="training",
        name="Training",
        agent="hr",
        visibility="private",
    )
    db_session.add(app)
    db_session.flush()
    version = AppVersion(
        app_id=app.id,
        version=1,
        spec=SPEC,
        ui_source="<div/>",
        logic_source=(FIXTURES / "training.py").read_text(),
        created_by=author.id,
    )
    db_session.add(version)
    db_session.flush()
    app.current_version_id = version.id
    db_session.flush()
    return app, version


def _rec(db, org, user, collection, data, venue_id=None):
    row = AppRecord(
        id=str(uuid.uuid4()),
        namespace="hr_suite",
        organization_id=org.id,
        venue_id=venue_id,
        collection=collection,
        data=data,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    return row.id


def _run(db, app, version, user, **params):
    out = AR.run_logic(
        db, None, app=app, version=version, user=user, venue_id=None, params=params
    )
    assert out["success"] is True, out.get("error")
    return out["data"]


def _program_with_two_items(db, org, author, name="Food Safety", venue_id=None):
    """One program, one module, one section, two content items."""
    program = _rec(
        db, org, author, "programs", {"name": name, "is_active": True}, venue_id
    )
    module = _rec(
        db,
        org,
        author,
        "modules",
        {"program_id": program, "name": "Hygiene", "sort_index": 0},
    )
    section = _rec(
        db,
        org,
        author,
        "sections",
        {"module_id": module, "name": "Handwashing", "sort_index": 0},
    )
    items = [
        _rec(
            db,
            org,
            author,
            "content",
            {"section_id": section, "module_id": module, "title": t, "sort_index": i},
        )
        for i, t in enumerate(("Why it matters", "The six steps"))
    ]
    return program, module, section, items


class TestInstanceModel:
    """An enrolment is (program, variant, venue) — not a program. Collapse that
    and a person trained on two variants shows one badge, with one of the two
    progress states silently discarded."""

    def test_two_variants_of_one_program_track_separately(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Cara", "is_active": True}
        )
        larder = _rec(
            db_session,
            org,
            author,
            "assignments",
            {
                "program_id": program,
                "person_id": person,
                "status": "assigned",
                "variant_id": "g:larder",
                "variant_name": "Larder",
            },
        )
        _rec(
            db_session,
            org,
            author,
            "assignments",
            {
                "program_id": program,
                "person_id": person,
                "status": "assigned",
                "variant_id": "g:grill",
                "variant_name": "Grill",
            },
        )
        # Larder is finished; Grill has not started.
        for item in items:
            _rec(
                db_session,
                org,
                author,
                "completions",
                {"assignment_id": larder, "content_id": item},
            )

        grid = _run(db_session, app, version, author, op="tracker")
        assert len(grid["rows"]) == 1
        cell = grid["rows"][0]["cells"][0]
        assert len(cell) == 2, "one badge per instance, not per program"
        by_instance = {c["instance"]: c["status"] for c in cell}
        assert by_instance == {"Larder": "complete", "Grill": "progress"}


class TestEffectiveCompletion:
    """Work that is done but not yet accepted does not count. Orbit applies
    this identically in three places; dropping it marks people trained who are
    waiting on a manager's sign-off."""

    def test_a_completion_awaiting_signoff_does_not_count(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Ben", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        _rec(
            db_session,
            org,
            author,
            "completions",
            {"assignment_id": assignment, "content_id": items[0]},
        )
        _rec(
            db_session,
            org,
            author,
            "completions",
            {
                "assignment_id": assignment,
                "content_id": items[1],
                "awaiting_signoff": True,
            },
        )
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ]
        assert cell[0]["status"] == "progress"
        assert cell[0]["label"] == "Hygiene"

    def test_a_signed_off_completion_does_count(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Ben", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        _rec(
            db_session,
            org,
            author,
            "completions",
            {"assignment_id": assignment, "content_id": items[0]},
        )
        _rec(
            db_session,
            org,
            author,
            "completions",
            {
                "assignment_id": assignment,
                "content_id": items[1],
                "awaiting_signoff": True,
                "signoff_at": "2026-08-17T00:00:00Z",
            },
        )
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ]
        assert cell[0]["status"] == "complete"

    def test_a_rejected_completion_does_not_count(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Ben", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        for item, extra in ((items[0], {}), (items[1], {"rejected": True})):
            _rec(
                db_session,
                org,
                author,
                "completions",
                {"assignment_id": assignment, "content_id": item, **extra},
            )
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ]
        assert cell[0]["status"] == "progress"


class TestGlobalPrograms:
    """A program with no venue belongs to everyone. Orbit's own API filters
    programs by `venue_id IN (...)`, so its group-wide programs — which is most
    of them — are invisible through it, and every assignment hanging off one
    vanishes with them."""

    def test_a_group_wide_program_is_visible_from_a_venue(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        venue = _make_venue(db_session, organization_id=org.id, name="Glass Goose")
        _make_venue_access(db_session, author, venue)
        _program_with_two_items(db_session, org, author, name="Group induction")
        _program_with_two_items(
            db_session, org, author, name="Venue only", venue_id=venue.id
        )
        names = {
            p["name"]
            for p in _run(db_session, app, version, author, op="overview")["programs"]
        }
        assert names == {"Group induction", "Venue only"}


class TestPlansDoNotDuplicateEnrolments:
    def test_a_plan_covered_by_an_assignment_is_not_shown_twice(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Dev", "is_active": True}
        )
        _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ]
        assert len(cell) == 1, "the plan duplicates an enrolment on the same instance"

    def test_a_plan_on_its_own_appears_with_its_next_section(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, module, section, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Dev", "is_active": True}
        )
        plan = _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        _rec(
            db_session,
            org,
            author,
            "plan_sections",
            {
                "plan_id": plan,
                "section_id": section,
                "due_date": "2026-08-22",
                "status": "pending",
            },
        )
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ]
        assert cell[0]["status"] == "progress"
        assert cell[0]["label"] == "Handwashing"
        assert cell[0]["scheduled"] is True

    def test_an_unnamed_next_section_still_says_something(
        self, db_session, org, author, app_and_version
    ):
        """Orbit renders the section name verbatim, so an unnamed section
        produces an amber badge with no text at all."""
        app, version = app_and_version
        program = _rec(
            db_session, org, author, "programs", {"name": "P", "is_active": True}
        )
        module = _rec(
            db_session, org, author, "modules", {"program_id": program, "name": "M"}
        )
        section = _rec(db_session, org, author, "sections", {"module_id": module})
        person = _rec(
            db_session, org, author, "people", {"name": "Dev", "is_active": True}
        )
        plan = _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        _rec(
            db_session,
            org,
            author,
            "plan_sections",
            {"plan_id": plan, "section_id": section, "status": "pending"},
        )
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ]
        assert cell[0]["label"] == "In progress"


class TestEmptyStructure:
    def test_a_program_with_no_content_is_not_complete(
        self, db_session, org, author, app_and_version
    ):
        """Structure with nothing in it is a program nobody can finish, not one
        everybody has finished."""
        app, version = app_and_version
        program = _rec(
            db_session, org, author, "programs", {"name": "Empty", "is_active": True}
        )
        _rec(db_session, org, author, "modules", {"program_id": program, "name": "M"})
        person = _rec(
            db_session, org, author, "people", {"name": "Ana", "is_active": True}
        )
        _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ]
        assert cell[0]["status"] == "none"


class TestEnrolments:
    def test_the_available_list_is_per_instance(
        self, db_session, org, author, app_and_version
    ):
        """A person already on the Larder variant must still be offerable for
        Grill, or a second variant could never be added."""
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Cara", "is_active": True}
        )
        _rec(
            db_session,
            org,
            author,
            "assignments",
            {
                "program_id": program,
                "person_id": person,
                "status": "assigned",
                "variant_id": "g:larder",
                "variant_name": "Larder",
            },
        )
        same = _run(
            db_session,
            app,
            version,
            author,
            op="enrollments",
            program_id=program,
            variant_id="g:larder",
        )
        assert [p["name"] for p in same["available"]] == []
        other = _run(
            db_session,
            app,
            version,
            author,
            op="enrollments",
            program_id=program,
            variant_id="g:grill",
        )
        assert [p["name"] for p in other["available"]] == ["Cara"]


class TestAuthoring:
    """Building a program. Storage has no foreign keys, so every delete has to
    walk its own children — the tests that matter here are the cascades, since
    a missed one leaves rows nothing can reach and progress denominators that
    count content belonging to a deleted section."""

    def test_a_program_needs_a_name(self, db_session, org, author, app_and_version):
        app, version = app_and_version
        out = _run(
            db_session, app, version, author, op="save_program", fields={"name": "  "}
        )
        assert "needs a name" in out["error"]

    def test_create_then_edit_keeps_the_record_clean(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        created = _run(
            db_session,
            app,
            version,
            author,
            op="save_program",
            fields={"name": "Barista Basics", "default_due_days": 14},
        )["program"]
        edited = _run(
            db_session,
            app,
            version,
            author,
            op="save_program",
            program_id=created["id"],
            fields={"name": "Barista Basics II", "is_active": False},
        )["program"]
        assert edited["name"] == "Barista Basics II"
        assert edited["default_due_days"] == 14  # untouched fields survive
        assert edited["is_active"] is False
        # The envelope storage adds must never be written back INTO the record.
        raw = db_session.query(AppRecord).filter(AppRecord.id == created["id"]).first()
        assert "created_at" not in raw.data and "id" not in raw.data

    def test_an_on_shift_section_gets_its_evidence_item(
        self, db_session, org, author, app_and_version
    ):
        """On-shift work is signed off against uploaded evidence. Without an
        item to hang it on, the section has nothing to complete and the tracker
        can never move past it."""
        app, version = app_and_version
        program = _run(
            db_session, app, version, author, op="save_program", fields={"name": "P"}
        )["program"]
        module = _run(
            db_session, app, version, author, op="add_module", program_id=program["id"]
        )["module"]
        _run(
            db_session,
            app,
            version,
            author,
            op="add_section",
            module_id=module["id"],
            section_type="on_shift",
        )
        tree = _run(
            db_session, app, version, author, op="program", program_id=program["id"]
        )
        assert [c["content_type"] for c in tree["content"]] == ["file_upload"]

    def test_an_online_section_does_not(self, db_session, org, author, app_and_version):
        app, version = app_and_version
        program = _run(
            db_session, app, version, author, op="save_program", fields={"name": "P"}
        )["program"]
        module = _run(
            db_session, app, version, author, op="add_module", program_id=program["id"]
        )["module"]
        _run(
            db_session,
            app,
            version,
            author,
            op="add_section",
            module_id=module["id"],
            section_type="online",
        )
        tree = _run(
            db_session, app, version, author, op="program", program_id=program["id"]
        )
        assert tree["content"] == []

    def test_deleting_a_section_takes_its_content_and_schedule(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, module, section, items = _program_with_two_items(
            db_session, org, author
        )
        person = _rec(
            db_session, org, author, "people", {"name": "Dev", "is_active": True}
        )
        plan = _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        _rec(
            db_session,
            org,
            author,
            "plan_sections",
            {
                "plan_id": plan,
                "section_id": section,
                "due_date": "2026-08-22",
                "status": "pending",
            },
        )
        _run(db_session, app, version, author, op="delete_section", section_id=section)
        remaining = {
            c: len(
                AR.store_list(
                    db_session, app=app, version=version, user=author, collection=c
                )
            )
            for c in ("sections", "content", "plan_sections")
        }
        # A plan row whose section no longer exists is a due date nobody can
        # ever complete.
        assert remaining == {"sections": 0, "content": 0, "plan_sections": 0}

    def test_deleting_a_module_walks_the_whole_branch(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, module, section, items = _program_with_two_items(
            db_session, org, author
        )
        _run(db_session, app, version, author, op="delete_module", module_id=module)
        tree = _run(db_session, app, version, author, op="program", program_id=program)
        assert (
            tree["modules"] == [] and tree["sections"] == [] and tree["content"] == []
        )

    def test_deleting_a_program_takes_enrolments_and_completions(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Ana", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        _rec(
            db_session,
            org,
            author,
            "completions",
            {"assignment_id": assignment, "content_id": items[0]},
        )
        _run(db_session, app, version, author, op="delete_program", program_id=program)
        left = {
            c: len(
                AR.store_list(
                    db_session, app=app, version=version, user=author, collection=c
                )
            )
            for c in (
                "programs",
                "modules",
                "sections",
                "content",
                "assignments",
                "completions",
            )
        }
        assert left == {
            "programs": 0,
            "modules": 0,
            "sections": 0,
            "content": 0,
            "assignments": 0,
            "completions": 0,
        }
        # …and the person is NOT deleted with it: they are shared with Hiring.
        assert (
            len(
                AR.store_list(
                    db_session,
                    app=app,
                    version=version,
                    user=author,
                    collection="people",
                )
            )
            == 1
        )

    def test_reorder_renumbers_the_whole_set(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program = _run(
            db_session, app, version, author, op="save_program", fields={"name": "P"}
        )["program"]
        ids = [
            _run(
                db_session,
                app,
                version,
                author,
                op="add_module",
                program_id=program["id"],
                name=n,
            )["module"]["id"]
            for n in ("A", "B", "C")
        ]
        _run(
            db_session,
            app,
            version,
            author,
            op="reorder",
            collection="modules",
            ids=[ids[2], ids[0], ids[1]],
        )
        tree = _run(
            db_session, app, version, author, op="program", program_id=program["id"]
        )
        assert [m["name"] for m in tree["modules"]] == ["C", "A", "B"]

    def test_reorder_refuses_a_collection_that_is_not_ordered(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        out = _run(
            db_session,
            app,
            version,
            author,
            op="reorder",
            collection="assignments",
            ids=[],
        )
        assert "only modules, sections and content" in out["error"]


class TestAssignmentStatus:
    """Admin completion moved server-side: it is the only way anything reaches
    'complete' in a build without the trainee experience, and doing it as a
    browser read-modify-write meant two managers on one assignment silently
    lost an edit."""

    def test_mark_complete_and_reopen(self, db_session, org, author, app_and_version):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Ana", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        done = _run(
            db_session,
            app,
            version,
            author,
            op="set_assignment_status",
            assignment_id=assignment,
            status="completed",
        )
        assert done["assignment"]["status"] == "completed"
        assert done["assignment"]["completed_at"]
        back = _run(
            db_session,
            app,
            version,
            author,
            op="set_assignment_status",
            assignment_id=assignment,
            status="assigned",
        )
        assert back["assignment"]["status"] == "assigned"
        assert back["assignment"]["completed_at"] is None

    def test_an_unknown_status_is_refused(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Ana", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        out = _run(
            db_session,
            app,
            version,
            author,
            op="set_assignment_status",
            assignment_id=assignment,
            status="finished",
        )
        assert "not an assignment status" in out["error"]


class TestEnrolment:
    """The enrol path — the hole that made the app read-only. It also sets the
    due date the way Orbit's DB trigger did, in visible app logic."""

    def test_enrol_puts_a_person_on_an_instance(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "New", "is_active": True}
        )
        out = _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=person,
        )
        assert out["assignment"]["status"] == "assigned"

    def test_due_date_comes_from_default_due_days(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program = _rec(
            db_session,
            org,
            author,
            "programs",
            {"name": "P", "is_active": True, "default_due_days": 30},
        )
        person = _rec(
            db_session, org, author, "people", {"name": "New", "is_active": True}
        )
        out = _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=person,
        )
        assert out["assignment"]["due_date"] is not None

    def test_no_default_due_days_means_no_due_date(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "New", "is_active": True}
        )
        out = _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=person,
        )
        assert out["assignment"].get("due_date") is None

    def test_cannot_enrol_twice_on_the_same_instance(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "New", "is_active": True}
        )
        _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=person,
        )
        out = _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=person,
        )
        assert "already enrolled" in out["error"]

    def test_but_a_second_variant_is_allowed(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "New", "is_active": True}
        )
        _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=person,
            variant_id="g:larder",
            variant_name="Larder",
        )
        out = _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=person,
            variant_id="g:grill",
            variant_name="Grill",
        )
        assert out["assignment"]["status"] == "assigned"

    def test_bulk_enrol_skips_the_already_enrolled(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, _ = _program_with_two_items(db_session, org, author)
        a = _rec(db_session, org, author, "people", {"name": "A", "is_active": True})
        b = _rec(db_session, org, author, "people", {"name": "B", "is_active": True})
        _run(
            db_session,
            app,
            version,
            author,
            op="enrol",
            program_id=program,
            person_id=a,
        )
        out = _run(
            db_session,
            app,
            version,
            author,
            op="bulk_enrol",
            program_id=program,
            person_ids=[a, b],
        )
        assert out["enrolled"] == 1  # a was already on

    def test_unenrol_removes_completions_too(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "New", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        _rec(
            db_session,
            org,
            author,
            "completions",
            {"assignment_id": assignment, "content_id": items[0]},
        )
        _run(db_session, app, version, author, op="unenrol", assignment_id=assignment)
        left = {
            c: len(
                AR.store_list(
                    db_session, app=app, version=version, user=author, collection=c
                )
            )
            for c in ("assignments", "completions")
        }
        assert left == {"assignments": 0, "completions": 0}


class TestMemberAndPlans:
    def test_member_detail_shows_module_progress(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(
            db_session, org, author, "people", {"name": "Ana", "is_active": True}
        )
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        _rec(
            db_session,
            org,
            author,
            "completions",
            {"assignment_id": assignment, "content_id": items[0]},
        )
        d = _run(db_session, app, version, author, op="member", person_id=person)
        assert d["person"]["name"] == "Ana"
        assert d["assignments"][0]["percent"] == 50  # 1 of 2 items

    def test_content_op_returns_the_body(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program = _rec(
            db_session, org, author, "programs", {"name": "P", "is_active": True}
        )
        module = _rec(
            db_session, org, author, "modules", {"program_id": program, "name": "M"}
        )
        section = _rec(
            db_session, org, author, "sections", {"module_id": module, "name": "S"}
        )
        content = _rec(
            db_session,
            org,
            author,
            "content",
            {
                "section_id": section,
                "module_id": module,
                "title": "Lesson",
                "content_type": "rich_text",
                "body": {"html": "<p>Hello</p>"},
            },
        )
        out = _run(db_session, app, version, author, op="content", content_id=content)
        assert out["content"]["body"]["html"] == "<p>Hello</p>"

    def test_plans_list_names_person_and_program(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program = _rec(
            db_session,
            org,
            author,
            "programs",
            {"name": "Bar", "is_active": True, "requires_plan": True},
        )
        person = _rec(
            db_session, org, author, "people", {"name": "Dev", "is_active": True}
        )
        _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        d = _run(db_session, app, version, author, op="plans", status="active")
        assert d["plans"][0]["person_name"] == "Dev"
        assert d["plans"][0]["program_name"] == "Bar"

    def test_schedule_section_preserves_completion(
        self, db_session, org, author, app_and_version
    ):
        """Orbit's editor deleted and reinserted, resetting completion. This
        upserts and keeps the status."""
        app, version = app_and_version
        program = _rec(
            db_session,
            org,
            author,
            "programs",
            {"name": "Bar", "is_active": True, "requires_plan": True},
        )
        module = _rec(
            db_session, org, author, "modules", {"program_id": program, "name": "M"}
        )
        section = _rec(
            db_session,
            org,
            author,
            "sections",
            {"module_id": module, "name": "Opening", "section_type": "on_shift"},
        )
        person = _rec(
            db_session, org, author, "people", {"name": "Dev", "is_active": True}
        )
        plan = _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        _rec(
            db_session,
            org,
            author,
            "plan_sections",
            {
                "plan_id": plan,
                "section_id": section,
                "status": "completed",
                "due_date": "2026-08-01",
            },
        )
        # reschedule the same section — completion must survive
        _run(
            db_session,
            app,
            version,
            author,
            op="schedule_section",
            plan_id=plan,
            section_id=section,
            due_date="2026-09-01",
        )
        rows = AR.store_list(
            db_session,
            app=app,
            version=version,
            user=author,
            collection="plan_sections",
        )
        assert len(rows) == 1 and rows[0]["status"] == "completed"
        assert rows[0]["due_date"] == "2026-09-01"


class TestSignoffQueue:
    """P3: the manager sign-off queue. A trainee submits an item (Orbit set
    `awaiting_signoff`); a manager accepts or rejects it. The queue is the
    JSONB query made reachable — awaiting and not yet signed — and it is how
    396 of the migrated completions already behave."""

    def _pending_setup(self, db, org, author):
        program, _, _, items = _program_with_two_items(db, org, author)
        person = _rec(db, org, author, "people", {"name": "Eden", "is_active": True})
        assignment = _rec(
            db,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
        )
        return items, person, assignment

    def test_queue_lists_only_awaiting_and_unsigned(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        items, _, assignment = self._pending_setup(db_session, org, author)
        # awaiting, unsigned -> in the queue
        _rec(
            db_session,
            org,
            author,
            "completions",
            {
                "assignment_id": assignment,
                "content_id": items[0],
                "awaiting_signoff": True,
            },
        )
        # awaiting but already signed -> excluded (signoff_at present)
        _rec(
            db_session,
            org,
            author,
            "completions",
            {
                "assignment_id": assignment,
                "content_id": items[1],
                "awaiting_signoff": True,
                "signoff_at": "2026-08-01T00:00:00+00:00",
            },
        )
        # not awaiting -> excluded
        _rec(
            db_session,
            org,
            author,
            "completions",
            {
                "assignment_id": assignment,
                "content_id": items[0],
                "awaiting_signoff": False,
            },
        )
        d = _run(db_session, app, version, author, op="signoffs")
        assert len(d["pending"]) == 1
        row = d["pending"][0]
        assert row["person"] == "Eden"
        assert row["program"] == "Food Safety"
        assert row["item"] == "Why it matters"
        assert row["files"] == []

    def test_sign_off_stamps_and_removes_from_queue(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        items, _, assignment = self._pending_setup(db_session, org, author)
        cid = _rec(
            db_session,
            org,
            author,
            "completions",
            {
                "assignment_id": assignment,
                "content_id": items[0],
                "awaiting_signoff": True,
                "rejected": True,  # a prior rejection must be cleared on accept
            },
        )
        d = _run(
            db_session,
            app,
            version,
            author,
            op="sign_off",
            completion_id=cid,
            by="Manager Sam",
        )
        c = d["completion"]
        assert c["awaiting_signoff"] is False
        assert c["signoff_at"]
        assert c["signoff_by"] == "Manager Sam"
        assert "rejected" not in c  # cleared on acceptance
        # and it is gone from the queue
        assert _run(db_session, app, version, author, op="signoffs")["pending"] == []

    def test_reject_records_reason_and_keeps_it_pending(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        items, _, assignment = self._pending_setup(db_session, org, author)
        cid = _rec(
            db_session,
            org,
            author,
            "completions",
            {
                "assignment_id": assignment,
                "content_id": items[0],
                "awaiting_signoff": True,
            },
        )
        d = _run(
            db_session,
            app,
            version,
            author,
            op="reject_signoff",
            completion_id=cid,
            notes="The photo is blurry — please retake it.",
        )
        c = d["completion"]
        assert c["rejected"] is True
        assert c["rejection_notes"] == "The photo is blurry — please retake it."
        assert c["rejected_at"]
        # still awaiting: a rejection sends it back, it does not sign it off
        assert c.get("signoff_at") is None
        assert (
            len(_run(db_session, app, version, author, op="signoffs")["pending"]) == 1
        )


class TestCapabilityFrameworks:
    """P4: capability frameworks — a role's competency map (Orbit's
    `capability_frameworks`), carried across whole with categories, capabilities
    and L1/L2/L3 descriptors nested inside."""

    def test_list_counts_categories_and_sorts_by_name(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        _rec(
            db_session,
            org,
            author,
            "capability_frameworks",
            {"name": "Sales Manager", "categories": [{"name": "A"}, {"name": "B"}]},
        )
        _rec(
            db_session,
            org,
            author,
            "capability_frameworks",
            {"name": "Bar Host", "categories": []},
        )
        d = _run(db_session, app, version, author, op="frameworks")
        assert [f["name"] for f in d["frameworks"]] == ["Bar Host", "Sales Manager"]
        by_name = {f["name"]: f for f in d["frameworks"]}
        assert by_name["Sales Manager"]["category_count"] == 2
        assert by_name["Bar Host"]["category_count"] == 0

    def test_get_returns_nested_capabilities(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        fid = _rec(
            db_session,
            org,
            author,
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
        d = _run(db_session, app, version, author, op="framework", framework_id=fid)
        cap = d["framework"]["categories"][0]["capabilities"][0]
        assert cap["name"] == "Appearance"
        assert cap["level1_descriptor"] == "Tidy"

    def test_create_seeds_empty_then_update_preserves_name(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        created = _run(
            db_session,
            app,
            version,
            author,
            op="save_framework",
            fields={"name": "New Role", "is_active": True},
        )["framework"]
        assert created["name"] == "New Role"
        assert created["categories"] == []  # a new framework starts empty
        updated = _run(
            db_session,
            app,
            version,
            author,
            op="save_framework",
            framework_id=created["id"],
            fields={"role_label": "Floor Lead", "is_active": False},
        )["framework"]
        assert updated["role_label"] == "Floor Lead"
        assert updated["is_active"] is False
        assert updated["name"] == "New Role"  # untouched fields survive
        assert updated["categories"] == []  # and so does the category map

    def test_save_requires_a_name(self, db_session, org, author, app_and_version):
        app, version = app_and_version
        d = _run(
            db_session,
            app,
            version,
            author,
            op="save_framework",
            fields={"is_active": True},
        )
        assert "name" in d["error"]


class TestPlanCompletion:
    """B1: a plan-led program must be completable from Norm. The `plan` op
    returns a real plan_section_id, and `set_plan_status` closes the plan."""

    def test_plan_op_returns_real_plan_section_id(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program = _rec(
            db_session,
            org,
            author,
            "programs",
            {"name": "Bar", "is_active": True, "requires_plan": True},
        )
        module = _rec(
            db_session, org, author, "modules", {"program_id": program, "name": "M"}
        )
        section = _rec(
            db_session, org, author, "sections", {"module_id": module, "name": "Open"}
        )
        person = _rec(db_session, org, author, "people", {"name": "Dev"})
        plan = _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        ps = _rec(
            db_session,
            org,
            author,
            "plan_sections",
            {"plan_id": plan, "section_id": section, "status": "pending"},
        )
        d = _run(db_session, app, version, author, op="plan", plan_id=plan)
        row = next(r for r in d["sections"] if r["section_id"] == section)
        assert row["plan_section_id"] == ps  # was always None before the fix

    def test_set_plan_status_completes_and_tracker_reads_it(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program = _rec(
            db_session,
            org,
            author,
            "programs",
            {"name": "Bar", "is_active": True, "requires_plan": True},
        )
        module = _rec(
            db_session, org, author, "modules", {"program_id": program, "name": "M"}
        )
        section = _rec(
            db_session, org, author, "sections", {"module_id": module, "name": "Open"}
        )
        person = _rec(db_session, org, author, "people", {"name": "Dev"})
        plan = _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        _rec(
            db_session,
            org,
            author,
            "plan_sections",
            {"plan_id": plan, "section_id": section, "status": "pending"},
        )
        # A plan with a still-pending section reads as in-progress...
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ][0]
        assert cell["status"] == "progress"
        # ...until the plan itself is marked complete.
        out = _run(
            db_session,
            app,
            version,
            author,
            op="set_plan_status",
            plan_id=plan,
            status="completed",
        )
        assert out["plan"]["status"] == "completed"
        cell = _run(db_session, app, version, author, op="tracker")["rows"][0]["cells"][
            0
        ][0]
        assert cell["status"] == "complete"

    def test_set_plan_status_rejects_bad_status(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program = _rec(db_session, org, author, "programs", {"name": "P"})
        person = _rec(db_session, org, author, "people", {"name": "D"})
        plan = _rec(
            db_session,
            org,
            author,
            "plans",
            {"program_id": program, "person_id": person, "status": "active"},
        )
        d = _run(
            db_session,
            app,
            version,
            author,
            op="set_plan_status",
            plan_id=plan,
            status="nonsense",
        )
        assert "not a plan status" in d["error"]


class TestTrackerVenueFilter:
    """B3: tracker cells must carry venue_id so the UI's venue filter works."""

    def test_cells_carry_venue_id(self, db_session, org, author, app_and_version):
        app, version = app_and_version
        venue = _make_venue(db_session, organization_id=org.id)
        _make_venue_access(db_session, author, venue)
        program, _, _, items = _program_with_two_items(db_session, org, author)
        person = _rec(db_session, org, author, "people", {"name": "Ana"})
        _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "assigned"},
            venue_id=venue.id,
        )
        grid = _run(db_session, app, version, author, op="tracker")
        cell = grid["rows"][0]["cells"][0][0]
        assert cell["venue_id"] == venue.id


class TestGrandfathering:
    """B7: adding content to a program must not drag already-complete enrollees
    back to incomplete — Orbit writes a grandfathered completion; so must we."""

    def test_add_content_grandfathers_completed_assignments(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, module, section, items = _program_with_two_items(
            db_session, org, author
        )
        person = _rec(db_session, org, author, "people", {"name": "Ana"})
        assignment = _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "completed"},
        )
        # Both existing items done → the assignment reads complete.
        for item in items:
            _rec(
                db_session,
                org,
                author,
                "completions",
                {"assignment_id": assignment, "content_id": item},
            )
        before = _run(db_session, app, version, author, op="tracker")["rows"][0][
            "cells"
        ][0][0]
        assert before["status"] == "complete"
        # Add a new item; the completed assignment must be grandfathered.
        _run(
            db_session,
            app,
            version,
            author,
            op="add_content",
            section_id=section,
            title="New rule",
            content_type="rich_text",
        )
        after = _run(db_session, app, version, author, op="tracker")["rows"][0][
            "cells"
        ][0][0]
        assert after["status"] == "complete"  # not dragged back to progress
        # and it is recorded as grandfathered, not a real completion
        comps = AR.store_list(
            db_session, app=app, version=version, user=author, collection="completions"
        )
        gf = [c for c in comps if (c.get("result") or {}).get("grandfathered")]
        assert len(gf) == 1

    def test_incomplete_assignment_is_not_grandfathered(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        program, module, section, items = _program_with_two_items(
            db_session, org, author
        )
        person = _rec(db_session, org, author, "people", {"name": "Ana"})
        _rec(
            db_session,
            org,
            author,
            "assignments",
            {"program_id": program, "person_id": person, "status": "in_progress"},
        )
        _run(
            db_session,
            app,
            version,
            author,
            op="add_content",
            section_id=section,
            title="New",
            content_type="rich_text",
        )
        comps = AR.store_list(
            db_session, app=app, version=version, user=author, collection="completions"
        )
        assert not [c for c in comps if (c.get("result") or {}).get("grandfathered")]


class TestFrameworkCategoryEditing:
    """B5: capability frameworks must be editable to the category/capability
    level — the UI sends the whole tree and every node gets a stable id."""

    def test_save_categories_assigns_ids_and_persists(
        self, db_session, org, author, app_and_version
    ):
        app, version = app_and_version
        created = _run(
            db_session,
            app,
            version,
            author,
            op="save_framework",
            fields={"name": "Bar Host"},
        )["framework"]
        fid = created["id"]
        # Save a category with one capability, no ids supplied.
        saved = _run(
            db_session,
            app,
            version,
            author,
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
            author,
            op="save_framework",
            framework_id=fid,
            fields={"categories": saved["categories"]},
        )["framework"]
        assert again["categories"][0]["id"] == cat_id
        assert again["categories"][0]["capabilities"][0]["id"] == cap_id
