"""The Orbit → Norm HR mirror (scripts/migrate_orbit_{training,hiring,files}.py).

Orbit stays the live HR system until cut-over, and these scripts keep Norm's
``hr_suite`` copy a faithful MIRROR of it: upsert by ``orbit_id``, delete what
Orbit lost, never touch a row created in Norm. They are run against
production's real staff, reviews, pay and CVs, so each property is pinned here
against a fake Orbit (no network):

- every new column/table lands, person ids remap to Norm people with the raw
  Orbit id kept beside them (and ONLY the raw id when nobody was carried);
- the delete pass removes rows Orbit no longer has — and nothing created in
  Norm, and nothing in another script's collections;
- soft-deleted performance reviews arrive marked, not dropped;
- a second run in a row changes nothing (no updates, no downloads);
- evidence links survive a refresh — the 21 Aug run reverted them to Orbit
  URLs and the files step, seeing them "already copied", never re-linked.
"""

from __future__ import annotations

import copy
import uuid

import pytest

from app.db.models import AppFile, AppRecord
from scripts import migrate_orbit_files as files_mod
from scripts import migrate_orbit_hiring as hiring_mod
from scripts import migrate_orbit_training as training_mod
from scripts.migrate_orbit_training import (
    Orbit,
    content_media_urls,
    current_rate,
    link_file_entry,
    storage_ref,
)
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
)

NS = "hr_suite"


def _id(tag: str) -> str:
    """A stable uuid per tag, so fixtures read as names."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"orbit-test/{tag}"))


V1 = _id("venue-goose")
ALICE, BOB, CAROL, DAVE, ERIN = (
    _id(n) for n in ("alice", "bob", "carol", "dave", "erin")
)
P1, M1, S1, C1, C2 = (_id(n) for n in ("p1", "m1", "s1", "c1", "c2"))
A1, A2, CC1, CC2, PL1, PS1, MS1 = (
    _id(n) for n in ("a1", "a2", "cc1", "cc2", "pl1", "ps1", "ms1")
)
F1, CAT1, CAP1, RC1, R1, R2, RR1, G1, E1 = (
    _id(n) for n in ("f1", "cat1", "cap1", "rc1", "r1", "r2", "rr1", "g1", "e1")
)
R_GONE = _id("review-not-in-orbit")
T1, CR1, CR2, ET1, J1, ST1, ST2, AF1 = (
    _id(n) for n in ("t1", "cr1", "cr2", "et1", "j1", "st1", "st2", "af1")
)
CAN1, APP1, ANS1, IG1, I1, II1, II2, SC1, SR1, D1, HE1 = (
    _id(n)
    for n in (
        "can1",
        "app1",
        "ans1",
        "ig1",
        "i1",
        "ii1",
        "ii2",
        "sc1",
        "sr1",
        "d1",
        "he1",
    )
)

STORAGE = "https://orbitproj.supabase.co/storage/v1/object/public/training-media"
EVIDENCE_URL = f"{STORAGE}/uploads/u1/abc__IMG_0001.jpeg"
EVIDENCE_URL_2 = f"{STORAGE}/uploads/u1/def__IMG_0002.jpeg"
MEDIA_URL = f"{STORAGE}/images/x/pic.png"
VIDEO_URL = f"{STORAGE}/video/y/clip.mp4"
CV_PATH = "applications/app1/cv-file.pdf"


def orbit_world() -> dict[str, list[dict]]:
    """A small but complete Orbit: every table the mirror reads."""
    return {
        "venues": [{"id": V1, "name": "The Glass Goose"}],
        "team_members": [
            {
                "id": ALICE,
                "name": "Alice",
                "email": "alice@orbit.test",
                "role": "Bartender",
                "is_active": True,
                "manager_id": BOB,
                "payroll_company_id": "PC-1",
                "loadedhub_id": "lh-alice",
            },
            {"id": BOB, "name": "Bob", "email": "bob@orbit.test", "is_active": True},
            # Left, but has training history: carried, marked inactive.
            {"id": CAROL, "name": "Carol", "is_active": False},
            # Left, referenced only as a reviewer/assigner: NOT carried, so
            # every reference to him must fall back to his raw Orbit id.
            {"id": DAVE, "name": "Dave", "is_active": False},
            # Left, nothing hangs off her: not carried.
            {"id": ERIN, "name": "Erin", "is_active": False},
        ],
        "team_member_venues": [
            {"id": _id("tmv1"), "team_member_id": ALICE, "venue_id": V1}
        ],
        "training_programs": [
            {"id": P1, "name": "Bar basics", "is_active": True, "venue_id": None}
        ],
        "training_modules": [
            {
                "id": M1,
                "program_id": P1,
                "name": "Pouring",
                "requires_signoff": True,
                "sort_index": 0,
            }
        ],
        "training_sections": [
            {"id": S1, "module_id": M1, "name": "Online", "section_type": "online"}
        ],
        "training_content": [
            {
                "id": C1,
                "section_id": S1,
                "module_id": M1,
                "title": "How to pour",
                "content_type": "rich_text",
                "content": {"html": f'<p>Look:</p><img src="{MEDIA_URL}" style="">'},
            },
            {
                "id": C2,
                "section_id": S1,
                "module_id": M1,
                "title": "Watch",
                "content_type": "video",
                "content": {"url": VIDEO_URL, "duration_seconds": 51},
            },
        ],
        "training_assignments": [
            {
                "id": A1,
                "program_id": P1,
                "team_member_id": ALICE,
                "assigned_by": BOB,
                "status": "in_progress",
                "venue_id": V1,
            },
            {
                "id": A2,
                "program_id": P1,
                "team_member_id": CAROL,
                "assigned_by": DAVE,
                "status": "completed",
            },
        ],
        "training_content_completions": [
            {
                "id": CC1,
                "assignment_id": A1,
                "content_id": C1,
                "completed_at": "2026-09-01T00:00:00+00:00",
                "completed_by": ALICE,
                "result": {
                    "files": [EVIDENCE_URL],
                    "uploaded": True,
                    "awaiting_signoff": False,
                    "signoff_at": "2026-09-02T00:00:00Z",
                    "signoff_by": BOB,
                    "signoff_notes": None,
                },
            },
            {
                "id": CC2,
                "assignment_id": A2,
                "content_id": C2,
                "completed_by": CAROL,
                "result": {"files": [EVIDENCE_URL_2], "awaiting_signoff": True},
            },
        ],
        "training_plans": [
            {
                "id": PL1,
                "team_member_id": ALICE,
                "program_id": P1,
                "created_by": BOB,
                "status": "active",
            }
        ],
        "training_plan_sections": [
            {"id": PS1, "plan_id": PL1, "section_id": S1, "trainer_id": BOB}
        ],
        "training_module_signoffs": [
            {
                "id": MS1,
                "assignment_id": A1,
                "module_id": M1,
                "signed_off_by": DAVE,
                "signed_off_at": "2026-09-03T00:00:00Z",
            }
        ],
        "capability_categories": [
            {"id": CAT1, "framework_id": F1, "name": "Service", "sort_index": 0}
        ],
        "capabilities": [
            {"id": CAP1, "category_id": CAT1, "name": "Greets", "sort_index": 0}
        ],
        "capability_frameworks": [{"id": F1, "name": "Bar", "is_active": True}],
        "review_cycle_assignments": [],
        "review_cycles": [
            {"id": RC1, "name": "Annual", "is_active": True, "default_framework_id": F1}
        ],
        "performance_reviews": [
            {
                "id": R1,
                "team_member_id": ALICE,
                "cycle_id": RC1,
                "framework_id": F1,
                "status": "in_meeting",
                "manager_id": BOB,
                "manager_submitted_at": "2026-09-05T00:00:00Z",
                "completed_by": DAVE,
                "goals_email_sent_at": "2026-09-06T00:00:00Z",
                "goals_email_to": "alice@orbit.test",
                "current_pay_rate": 27.5,
                "current_pay_type": "hourly",
                "proposed_pay_rate": 29,
                "pay_notes": "solid year",
                "pay_proposed_by": BOB,
                "pay_proposed_at": "2026-09-05T01:00:00Z",
                "no_pay_change": False,
                "deleted_at": None,
                "deleted_by": None,
            },
            {
                "id": R2,
                "team_member_id": ALICE,
                "status": "employee_in_progress",
                "deleted_at": "2026-09-09T00:00:00Z",
                "deleted_by": BOB,
            },
        ],
        "performance_review_capability_ratings": [
            {"id": RR1, "review_id": R1, "capability_id": CAP1, "manager_level": 3}
        ],
        "performance_review_previous_goals": [
            {"id": G1, "review_id": R1, "text": "Learn cocktails", "sort_index": 0}
        ],
        "performance_review_emails": [
            {
                "id": E1,
                "review_id": R1,
                "team_member_id": ALICE,
                "kind": "ready",
                "recipient_role": "employee",
                "review_ids": [R1, R_GONE],
                "status": "sent",
                "is_test": False,
            }
        ],
        "performance_review_email_settings": [
            {"id": True, "enabled": True, "updated_at": "2026-09-28T00:00:00Z"}
        ],
        "team_member_remuneration_history": [
            {
                "id": _id("h1"),
                "team_member_id": ALICE,
                "valid_from": "2025-01-01",
                "valid_to": "2025-12-31",
                "is_active_in_loadedhub": True,
                "hourly_rate": 25,
                "remuneration_type": "HourlyRate",
            },
            {
                # The live row: flattened column empty, rate only in raw.
                "id": _id("h2"),
                "team_member_id": ALICE,
                "valid_from": "2026-01-01",
                "valid_to": None,
                "is_active_in_loadedhub": True,
                "hourly_rate": None,
                "remuneration_type": "HourlyRate",
                "raw": {
                    "type": "HourlyRate",
                    "memberRoles": [{"hourlyRate": 26}],
                    "venueAssignments": [{"memberRoles": [{"hourlyRate": 28.5}]}],
                },
                "synced_at": "2026-10-01T00:00:00Z",
            },
            {
                "id": _id("h3"),
                "team_member_id": BOB,
                "valid_from": "2026-06-01",
                "deleted_at_source": "2026-07-01T00:00:00Z",
                "is_active_in_loadedhub": True,
                "salary_rate": 99999,
                "remuneration_type": "Salary",
            },
            {
                "id": _id("h4"),
                "team_member_id": BOB,
                "valid_from": "2026-01-01",
                "valid_to": "2026-05-31",
                "is_active_in_loadedhub": False,
                "salary_rate": 70000,
                "remuneration_type": "Salary",
            },
            {
                "id": _id("h5"),
                "team_member_id": ERIN,
                "valid_from": "2024-01-01",
                "hourly_rate": 22,
            },
        ],
        "training_auto_enroll_rules": [
            {"id": _id("rule1"), "program_id": P1, "role": "Bartender", "venue_id": V1}
        ],
        "training_reminder_settings": [
            {"id": True, "enabled": True, "interval_days": 3}
        ],
        "training_reminder_emails": [
            {"id": _id("rem1"), "team_member_id": ALICE, "subject": "Reminder"}
        ],
        # ---- hiring ---------------------------------------------------------
        "scorecard_criteria": [
            {"id": CR1, "template_id": T1, "name": "Attitude", "sort_index": 0},
            {"id": CR2, "template_id": T1, "name": "Skill", "sort_index": 1},
        ],
        "scorecard_templates": [{"id": T1, "name": "Bar interview", "is_active": True}],
        "hiring_email_templates": [
            {"id": ET1, "name": "Invite", "template_key": "invite", "is_active": True}
        ],
        "job_openings": [
            {
                "id": J1,
                "title": "Bartender",
                "venue_id": V1,
                "status": "open",
                "hiring_manager_id": BOB,
                "owner_id": DAVE,
                "is_published": True,
            }
        ],
        "job_pipeline_stages": [
            {
                "id": ST1,
                "job_id": J1,
                "name": "Interview",
                "stage_type": "active",
                "scorecard_template_id": T1,
                "sort_index": 0,
            },
            {
                "id": ST2,
                "job_id": J1,
                "name": "Hired",
                "stage_type": "hired",
                "sort_index": 1,
            },
        ],
        "job_application_fields": [
            {
                "id": AF1,
                "job_id": J1,
                "field_key": "rsa",
                "label": "RSA?",
                "is_enabled": True,
            }
        ],
        "candidates": [{"id": CAN1, "first_name": "Cam", "last_name": "Didate"}],
        "candidate_applications": [
            {
                "id": APP1,
                "candidate_id": CAN1,
                "job_id": J1,
                "stage_id": ST2,
                "status": "active",
                "team_member_id": ALICE,
            }
        ],
        "candidate_application_answers": [
            {
                "id": ANS1,
                "application_id": APP1,
                "field_id": AF1,
                "field_key": "rsa",
                "value": "yes",
            }
        ],
        "candidate_notes": [],
        "candidate_activity": [],
        "interview_groups": [
            {
                "id": IG1,
                "job_id": J1,
                "title": "Group session",
                "interviewer_ids": [BOB, DAVE],
                "invite_candidate": True,
                "status": "scheduled",
            }
        ],
        "interview_interviewers": [
            {"id": II1, "interview_id": I1, "team_member_id": BOB},
            {"id": II2, "interview_id": I1, "team_member_id": DAVE},
        ],
        "interviews": [
            {
                "id": I1,
                "application_id": APP1,
                "candidate_id": CAN1,
                "job_id": J1,
                "stage_id": ST1,
                "group_id": IG1,
                "scorecard_template_id": T1,
                "invite_candidate": False,
                "created_by": BOB,
                "google_event_id": "evt-1",
                "status": "scheduled",
            }
        ],
        "scorecard_ratings": [
            {"id": SR1, "scorecard_id": SC1, "criterion_id": CR1, "rating": 4}
        ],
        "interview_scorecards": [
            {
                "id": SC1,
                "interview_id": I1,
                "application_id": APP1,
                "template_id": T1,
                "interviewer_id": BOB,
                "overall_recommendation": "yes",
            }
        ],
        "job_opening_approvals": [],
        "job_hiring_team": [
            {"id": _id("ht1"), "job_id": J1, "team_member_id": BOB, "role": "manager"}
        ],
        "job_description_templates": [],
        "careers_page_settings": [{"id": _id("careers"), "headline": "Join us"}],
        "candidate_documents": [
            {
                "id": D1,
                "candidate_id": CAN1,
                "application_id": APP1,
                "doc_type": "cv",
                "file_name": "Cam CV.pdf",
                "storage_path": CV_PATH,
                "mime_type": "application/pdf",
            }
        ],
        "hiring_emails": [
            {"id": HE1, "candidate_id": CAN1, "template_id": ET1, "subject": "Hi"}
        ],
    }


class FakeOrbit:
    """Orbit with no network. Honours ``select`` (so a column the mirror
    forgot to select is missing here too) and returns rows ordered by id, as
    the real keyset-paged client does."""

    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables
        self.blobs = {
            EVIDENCE_URL: b"evidence-1",
            EVIDENCE_URL_2: b"evidence-2",
            MEDIA_URL: b"png-bytes",
            VIDEO_URL: b"mp4-bytes",
        }
        self.objects = {("candidate-documents", CV_PATH): b"%PDF-cv"}
        self.fetched: list[str] = []

    def rows(self, table: str, select: str = "*") -> list[dict]:
        rows = sorted(
            copy.deepcopy(self.tables.get(table, [])), key=lambda r: str(r["id"])
        )
        if select != "*":
            cols = {c.strip() for c in select.split(",")} | {"id"}
            rows = [{k: v for k, v in r.items() if k in cols} for r in rows]
        return rows

    def fetch_url(self, url: str):
        self.fetched.append(url)
        if url not in self.blobs:
            raise OSError(f"404 {url}")
        return self.blobs[url], "image/jpeg"

    def fetch_object(self, bucket: str, path: str):
        self.fetched.append(f"{bucket}/{path}")
        return self.objects[(bucket, path)], "application/octet-stream"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def target(db_session):
    org = _make_organization(db_session, name="Orbit Test Org")
    user = _make_user(db_session, email=f"{uuid.uuid4().hex[:10]}@orbit-import.test")
    _make_membership(db_session, user, org)
    venue = _make_venue(db_session, name="Glass Goose", organization_id=org.id)
    # Bob has a Norm login in this org; a same-email user elsewhere must not
    # be linked.
    bob = _make_user(db_session, email="bob@orbit.test")
    _make_membership(db_session, bob, org)
    elsewhere = _make_organization(db_session, name="Elsewhere")
    _make_membership(
        db_session, _make_user(db_session, email="alice@orbit.test"), elsewhere
    )
    return {"org": org, "user": user, "venue": venue, "bob_user": bob}


@pytest.fixture()
def orbit():
    return FakeOrbit(orbit_world())


def _silent(*_a, **_k):
    pass


def _training(db, orbit, target, **kw):
    return training_mod.run(
        db, orbit, org_email=target["user"].email, log=_silent, **kw
    )


def _hiring(db, orbit, target, **kw):
    return hiring_mod.run(db, orbit, org_email=target["user"].email, log=_silent, **kw)


def _files(db, orbit, target, **kw):
    return files_mod.run(
        db, orbit, org_email=target["user"].email, log=_silent, workers=1, **kw
    )


def _all(db, target, collection=None):
    q = db.query(AppRecord).filter(
        AppRecord.namespace == NS, AppRecord.organization_id == target["org"].id
    )
    if collection:
        q = q.filter(AppRecord.collection == collection)
    return q.all()


def _by_orbit(db, target, collection) -> dict[str, AppRecord]:
    return {r.data.get("orbit_id"): r for r in _all(db, target, collection)}


def _changes(stats: dict) -> dict:
    """Only the counters that mean something changed."""
    return {
        c: {k: v for k, v in s.items() if k in ("new", "updated", "deleted") and v}
        for c, s in stats.items()
        if any(s.get(k) for k in ("new", "updated", "deleted"))
    }


# ---------------------------------------------------------------------------
# mapping, including everything the August import did not carry
# ---------------------------------------------------------------------------


class TestTrainingMapping:
    def test_people_carry_manager_and_payroll_and_only_who_matters(
        self, db_session, orbit, target
    ):
        """...and every person reference remaps to a Norm person, with the raw
        Orbit id kept beside it (folds the old person-id remap test)."""
        assert _training(db_session, orbit, target).code == 0
        people = _by_orbit(db_session, target, "people")
        # Active + anyone with history; Dave (only referenced as an actor) and
        # Erin (nothing at all) are not carried.
        assert set(people) == {ALICE, BOB, CAROL}
        alice = people[ALICE].data
        assert alice["manager_id"] == people[BOB].id
        assert alice["manager_orbit_id"] == BOB
        assert alice["payroll_company_id"] == "PC-1"
        assert alice["venues"] == [target["venue"].id]
        assert people[CAROL].data["is_active"] is False
        # A login is linked only within the target org.
        assert people[BOB].data["norm_user_id"] == target["bob_user"].id
        assert "norm_user_id" not in alice

        # Person ids remap, with the raw Orbit id as the fallback.
        people = {k: v.id for k, v in people.items()}
        a = _by_orbit(db_session, target, "assignments")
        assert a[A1].data["assigned_by"] == people[BOB]
        assert a[A1].data["assigned_by_orbit_id"] == BOB
        # Dave was not carried: the Norm field is ABSENT (never a raw Orbit id
        # posing as a Norm one) and his Orbit id is kept beside it.
        assert "assigned_by" not in a[A2].data
        assert a[A2].data["assigned_by_orbit_id"] == DAVE

        cc = _by_orbit(db_session, target, "completions")[CC1].data
        assert cc["completed_by"] == people[ALICE]
        assert cc["completed_by_orbit_id"] == ALICE
        assert cc["signoff_by"] == people[BOB]
        assert cc["signoff_by_orbit_id"] == BOB
        assert cc["awaiting_signoff"] is False
        plan = _by_orbit(db_session, target, "plans")[PL1].data
        assert plan["created_by"] == people[BOB]
        ps = _by_orbit(db_session, target, "plan_sections")[PS1].data
        assert ps["trainer_person_id"] == people[BOB]
        assert ps["trainer_orbit_id"] == BOB
        ms = _by_orbit(db_session, target, "module_signoffs")[MS1].data
        assert "signed_off_by" not in ms and ms["signed_off_by_orbit_id"] == DAVE

    def test_performance_reviews_emails_settings_and_remuneration(
        self, db_session, orbit, target
    ):
        """Every new review column; review emails, email settings and pay
        (folds the old new-columns, soft-delete and emails tests, one import).

        Soft-deleted reviews arrive marked: Orbit hides a soft-deleted review
        in its UI but keeps the row (and can restore it). Dropping it would
        make a restore in Orbit look like a brand-new review here."""
        _training(db_session, orbit, target)
        people = {k: v.id for k, v in _by_orbit(db_session, target, "people").items()}
        reviews = _by_orbit(db_session, target, "performance_reviews")
        r1 = reviews[R1].data
        assert r1["status"] == "ready_for_meeting"  # in_meeting renamed
        assert r1["manager_id"] == people[BOB]
        assert r1["manager_orbit_id"] == BOB
        assert r1["pay_proposed_by"] == people[BOB]
        assert "completed_by" not in r1 and r1["completed_by_orbit_id"] == DAVE
        assert r1["manager_submitted_at"] == "2026-09-05T00:00:00Z"
        assert r1["goals_email_to"] == "alice@orbit.test"
        assert r1["current_pay_rate"] == 27.5
        assert r1["proposed_pay_rate"] == 29
        assert r1["pay_notes"] == "solid year"
        assert r1["no_pay_change"] is False
        assert r1["capability_ratings"] == [{"capability_id": CAP1, "manager_level": 3}]
        assert r1["previous_goals"][0]["id"] == G1
        assert "deleted_at" not in r1

        r2 = reviews[R2].data
        assert r2["deleted_at"] == "2026-09-09T00:00:00Z"
        assert r2["deleted_by"] == people[BOB]
        assert r2["deleted_by_orbit_id"] == BOB

        email = _by_orbit(db_session, target, "performance_review_emails")[E1].data
        assert email["review_id"] == reviews[R1].id
        assert email["review_ids"] == [reviews[R1].id]
        assert email["review_orbit_ids"] == [R1, R_GONE]
        assert email["person_id"] == people[ALICE]

        settings = _by_orbit(db_session, target, "performance_review_email_settings")
        assert settings["True"].data["enabled"] is True

        pay = _by_orbit(db_session, target, "remuneration")
        # One row per CARRIED person — Erin's history stays behind.
        assert set(pay) == {ALICE, BOB}
        assert pay[ALICE].data["person_id"] == people[ALICE]
        assert pay[ALICE].data["pay_type"] == "hourly"
        assert pay[ALICE].data["rate"] == 28.5  # from raw, highest role rate
        assert pay[ALICE].data["history_id"] == _id("h2")
        # Bob: the newest row was deleted at source; the first surviving one wins.
        assert pay[BOB].data == {
            "person_id": people[BOB],
            "pay_type": "salary",
            "rate": 70000,
            "history_id": _id("h4"),
            "valid_from": "2026-01-01",
            "orbit_id": BOB,
        }


class TestCurrentRate:
    """Orbit's currentRateForMember, row by row."""

    def test_live_row_beats_newer_dead_one(self):
        rows = [
            {
                "id": "a",
                "valid_from": "2026-05-01",
                "deleted_at_source": "x",
                "hourly_rate": 40,
            },
            {
                "id": "b",
                "valid_from": "2026-01-01",
                "is_active_in_loadedhub": True,
                "hourly_rate": 30,
            },
        ]
        assert current_rate(rows)["rate"] == 30

    def test_salary_falls_back_to_raw(self):
        rows = [
            {
                "id": "a",
                "valid_from": "2026-01-01",
                "is_active_in_loadedhub": True,
                "raw": {"remunerationType": "SalaryAnnual", "salaryRate": 65000},
            }
        ]
        assert current_rate(rows) == {
            "pay_type": "salary",
            "rate": 65000,
            "history_id": "a",
            "valid_from": "2026-01-01",
        }

    def test_all_deleted_takes_newest(self):
        rows = [
            {
                "id": "a",
                "valid_from": "2025-01-01",
                "deleted_at_source": "x",
                "hourly_rate": 20,
            },
            {
                "id": "b",
                "valid_from": "2026-01-01",
                "deleted_at_source": "x",
                "hourly_rate": 21,
            },
        ]
        assert current_rate(rows)["history_id"] == "b"

    def test_no_rate_anywhere_is_none_not_zero(self):
        rows = [{"id": "a", "valid_from": "2026-01-01", "is_active_in_loadedhub": True}]
        out = current_rate(rows)
        assert out["pay_type"] == "hourly" and out["rate"] is None

    def test_ties_pick_the_same_row_every_time(self):
        rows = [
            {
                "id": "a",
                "valid_from": "2026-01-01",
                "is_active_in_loadedhub": True,
                "hourly_rate": 20,
            },
            {
                "id": "b",
                "valid_from": "2026-01-01",
                "is_active_in_loadedhub": True,
                "hourly_rate": 21,
            },
        ]
        assert (
            current_rate(rows)["history_id"] == current_rate(rows[::-1])["history_id"]
        )


class TestHiringMapping:
    def test_new_hiring_columns_and_tables(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        assert _hiring(db_session, orbit, target).code == 0
        people = {k: v.id for k, v in _by_orbit(db_session, target, "people").items()}

        job = _by_orbit(db_session, target, "job_openings")[J1]
        assert job.venue_id == target["venue"].id
        assert job.data["hiring_manager_id"] == people[BOB]
        assert "owner_id" not in job.data and job.data["owner_orbit_id"] == DAVE

        template = _by_orbit(db_session, target, "scorecard_templates")[T1].data
        assert [c["id"] for c in template["criteria"]] == [CR1, CR2]

        group = _by_orbit(db_session, target, "interview_groups")[IG1]
        assert group.data["job_id"] == job.id
        assert group.data["interviewer_ids"] == [people[BOB]]
        assert group.data["interviewer_orbit_ids"] == [BOB, DAVE]
        assert group.data["invite_candidate"] is True

        interview = _by_orbit(db_session, target, "interviews")[I1].data
        apps = _by_orbit(db_session, target, "applications")
        assert interview["group_id"] == group.id
        assert interview["group_orbit_id"] == IG1
        assert (
            interview["candidate_id"]
            == _by_orbit(db_session, target, "candidates")[CAN1].id
        )
        assert interview["job_id"] == job.id
        assert (
            interview["stage_id"]
            == _by_orbit(db_session, target, "pipeline_stages")[ST1].id
        )
        assert interview["created_by"] == people[BOB]
        assert interview["interviewer_ids"] == [people[BOB]]
        assert interview["interviewer_orbit_ids"] == [BOB, DAVE]
        assert interview["invite_candidate"] is False
        assert interview["google_event_id"] == "evt-1"

        card = _by_orbit(db_session, target, "interview_scorecards")[SC1].data
        assert (
            card["interview_id"] == _by_orbit(db_session, target, "interviews")[I1].id
        )
        assert card["application_id"] == apps[APP1].id
        assert card["interviewer_id"] == people[BOB]
        # The rating names a criterion that exists on the template.
        assert card["ratings"] == [{"id": SR1, "criterion_id": CR1, "rating": 4}]
        assert card["ratings"][0]["criterion_id"] in {
            c["id"] for c in template["criteria"]
        }

        app = apps[APP1].data
        assert app["status"] == "hired"  # derived from the stage type
        assert app["person_id"] == people[ALICE]
        answer = _by_orbit(db_session, target, "answers")[ANS1].data
        assert (
            answer["field_id"]
            == _by_orbit(db_session, target, "application_fields")[AF1].id
        )

    def test_hiring_never_writes_people(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        before = {r.id: dict(r.data) for r in _all(db_session, target, "people")}
        res = _hiring(db_session, orbit, target)
        assert "people" not in res.stats
        after = {r.id: dict(r.data) for r in _all(db_session, target, "people")}
        assert before == after


# ---------------------------------------------------------------------------
# the delete pass
# ---------------------------------------------------------------------------


class TestDeletePass:
    def test_deletes_what_orbit_lost_and_nothing_else(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        # Rows created in Norm — no orbit_id — in collections both scripts own.
        own_program = AppRecord(
            namespace=NS,
            organization_id=target["org"].id,
            collection="programs",
            data={"name": "Made in Norm"},
        )
        own_person = AppRecord(
            namespace=NS,
            organization_id=target["org"].id,
            collection="people",
            data={"name": "Hired in Norm"},
        )
        db_session.add_all([own_program, own_person])
        db_session.flush()

        # Orbit deletes a content item (and its completion) and an assignment.
        orbit.tables["training_content"] = [
            c for c in orbit.tables["training_content"] if c["id"] != C2
        ]
        orbit.tables["training_content_completions"] = [
            c for c in orbit.tables["training_content_completions"] if c["id"] != CC2
        ]
        orbit.tables["training_assignments"] = [
            a for a in orbit.tables["training_assignments"] if a["id"] != A2
        ]
        res = _training(db_session, orbit, target)
        assert res.code == 0
        assert res.stats["content"]["deleted"] == 1
        assert res.stats["assignments"]["deleted"] == 1
        assert res.stats["completions"]["deleted"] == 1
        assert C2 not in _by_orbit(db_session, target, "content")
        assert A2 not in _by_orbit(db_session, target, "assignments")
        assert C1 in _by_orbit(db_session, target, "content")
        # Norm's own rows survive, and so do hiring's (training does not own them).
        ids = {r.id for r in _all(db_session, target)}
        assert own_program.id in ids and own_person.id in ids
        assert J1 in _by_orbit(db_session, target, "job_openings")

    def test_a_person_who_left_is_kept_not_deleted(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        # Carol's history is gone in Orbit but she still exists there.
        orbit.tables["training_assignments"] = [
            a for a in orbit.tables["training_assignments"] if a["id"] != A2
        ]
        orbit.tables["training_content_completions"] = [
            c for c in orbit.tables["training_content_completions"] if c["id"] != CC2
        ]
        res = _training(db_session, orbit, target)
        assert "deleted" not in res.stats.get("people", {})
        assert CAROL in _by_orbit(db_session, target, "people")
        # Deleted from Orbit entirely: deleted here too.
        orbit.tables["team_members"] = [
            m for m in orbit.tables["team_members"] if m["id"] != CAROL
        ]
        res = _training(db_session, orbit, target)
        assert res.stats["people"]["deleted"] == 1
        assert CAROL not in _by_orbit(db_session, target, "people")

    def test_dry_run_reports_but_writes_nothing(self, db_session, orbit, target):
        res = _training(db_session, orbit, target, dry_run=True)
        assert res.code == 0
        assert res.stats["people"]["new"] == 3
        assert res.stats["completions"]["new"] == 2
        assert _all(db_session, target) == []

        _training(db_session, orbit, target)
        orbit.tables["training_reminder_emails"] = []
        orbit.tables["training_plans"] = []
        res = _training(db_session, orbit, target, dry_run=True)
        assert res.stats["plans"]["deleted"] == 1
        assert PL1 in _by_orbit(db_session, target, "plans")

    def test_an_emptied_collection_stops_the_run(self, db_session, orbit, target):
        """An empty read for a collection Norm has rows for is far likelier a
        broken read (renamed table, revoked grant) than Orbit deleting it all."""
        _training(db_session, orbit, target)
        orbit.tables["training_reminder_emails"] = []
        res = _training(db_session, orbit, target)
        assert res.code == 2
        assert _id("rem1") in _by_orbit(db_session, target, "reminder_emails")
        res = _training(db_session, orbit, target, allow_mass_delete=True)
        assert res.code == 0
        assert _id("rem1") not in _by_orbit(db_session, target, "reminder_emails")

    def test_hiring_deletes_only_its_own(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        orbit.tables["interview_scorecards"] = []
        orbit.tables["scorecard_ratings"] = []
        res = _hiring(db_session, orbit, target, allow_mass_delete=True)
        assert res.stats["interview_scorecards"]["deleted"] == 1
        assert _by_orbit(db_session, target, "interview_scorecards") == {}
        assert len(_by_orbit(db_session, target, "people")) == 3


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_full_run_changes_nothing(self, db_session, orbit, target):
        first = [
            _training(db_session, orbit, target),
            _hiring(db_session, orbit, target),
            _files(db_session, orbit, target),
        ]
        assert all(r.code == 0 for r in first)
        snapshot = {r.id: copy.deepcopy(r.data) for r in _all(db_session, target)}
        files_before = (
            db_session.query(AppFile)
            .filter(AppFile.organization_id == target["org"].id)
            .count()
        )
        downloads = len(orbit.fetched)

        t = _training(db_session, orbit, target)
        h = _hiring(db_session, orbit, target)
        f = _files(db_session, orbit, target)
        assert _changes(t.stats) == {}
        assert _changes(h.stats) == {}
        assert f.stats["copied"] == {} and f.stats["relinked"] == {}
        assert f.stats["orphans"] == 0
        assert len(orbit.fetched) == downloads  # nothing re-downloaded
        assert {r.id: r.data for r in _all(db_session, target)} == snapshot
        assert (
            db_session.query(AppFile)
            .filter(AppFile.organization_id == target["org"].id)
            .count()
            == files_before
        )


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------


class TestFiles:
    def test_copies_evidence_cvs_and_media_and_links_them(
        self, db_session, orbit, target
    ):
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        res = _files(db_session, orbit, target)
        assert res.code == 0
        assert res.stats["copied"] == {"evidence": 2, "cvs": 1, "media": 2}

        files = {
            f.source_ref: f
            for f in db_session.query(AppFile).filter(
                AppFile.organization_id == target["org"].id
            )
        }
        cc1 = _by_orbit(db_session, target, "completions")[CC1]
        ev = files[EVIDENCE_URL]
        assert (ev.collection, ev.record_id, bytes(ev.data)) == (
            "completions",
            cc1.id,
            b"evidence-1",
        )
        assert cc1.data["result"]["files"] == [
            {"file_id": ev.id, "name": "abc__IMG_0001.jpeg", "source_url": EVIDENCE_URL}
        ]

        doc = _by_orbit(db_session, target, "candidate_documents")[D1]
        cv = files[storage_ref("candidate-documents", CV_PATH)]
        assert doc.data["file_id"] == cv.id
        assert (cv.collection, cv.record_id, cv.filename, cv.content_type) == (
            "candidate_documents",
            doc.id,
            "Cam CV.pdf",
            "application/pdf",
        )
        assert bytes(cv.data) == b"%PDF-cv"

        content = _by_orbit(db_session, target, "content")
        c1 = content[C1].data
        assert c1["files"] == [
            {"file_id": files[MEDIA_URL].id, "name": "pic.png", "source_url": MEDIA_URL}
        ]
        # The body still renders: its URLs are untouched.
        assert MEDIA_URL in c1["body"]["html"]
        assert content[C2].data["files"][0]["file_id"] == files[VIDEO_URL].id
        assert files[MEDIA_URL].record_id == content[C1].id

    def test_links_survive_a_refresh(self, db_session, orbit, target):
        """The 21 Aug failure: the training run rewrote result.files back to
        Orbit URLs and the files run skipped them as already copied."""
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        _files(db_session, orbit, target)
        downloads = len(orbit.fetched)

        # A refresh now keeps the links by itself …
        res = _training(db_session, orbit, target)
        assert _changes(res.stats) == {}
        cc1 = _by_orbit(db_session, target, "completions")[CC1]
        assert cc1.data["result"]["files"][0]["file_id"]

        # … and a record reverted the old way is re-linked without a download.
        data = copy.deepcopy(cc1.data)
        data["result"]["files"] = [EVIDENCE_URL]
        cc1.data = data
        db_session.flush()
        res = _files(db_session, orbit, target)
        assert res.stats["relinked"] == {"evidence": 1}
        assert res.stats["copied"] == {}
        assert len(orbit.fetched) == downloads
        relinked = _by_orbit(db_session, target, "completions")[CC1]
        assert relinked.data["result"]["files"][0]["source_url"] == EVIDENCE_URL
        assert relinked.data["result"]["files"][0]["file_id"]

    def test_new_evidence_is_copied_on_the_next_run(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        _files(db_session, orbit, target)
        new_url = f"{STORAGE}/uploads/u2/new.jpeg"
        orbit.blobs[new_url] = b"new"
        cc = orbit.tables["training_content_completions"][0]
        cc["result"]["files"].append(new_url)
        res = _training(db_session, orbit, target)
        assert res.stats["completions"]["updated"] == 1
        res = _files(db_session, orbit, target)
        assert res.stats["copied"] == {"evidence": 1}
        entries = _by_orbit(db_session, target, "completions")[CC1].data["result"][
            "files"
        ]
        assert [e["source_url"] for e in entries] == [EVIDENCE_URL, new_url]
        assert all(e.get("file_id") for e in entries)

    def test_a_failed_download_stays_on_orbit_and_retries(
        self, db_session, orbit, target
    ):
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        del orbit.blobs[EVIDENCE_URL_2]
        res = _files(db_session, orbit, target)
        assert res.stats["failed"] == {"evidence": 1}
        cc2 = _by_orbit(db_session, target, "completions")[CC2]
        assert cc2.data["result"]["files"] == [EVIDENCE_URL_2]
        orbit.blobs[EVIDENCE_URL_2] = b"evidence-2"
        res = _files(db_session, orbit, target)
        assert res.stats["copied"] == {"evidence": 1}

    def test_dry_run_downloads_nothing(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        res = _files(db_session, orbit, target, dry_run=True)
        assert res.stats["pending"] == {"evidence": 2, "cvs": 1, "media": 2}
        assert orbit.fetched == []
        assert (
            db_session.query(AppFile)
            .filter(AppFile.organization_id == target["org"].id)
            .count()
            == 0
        )

    def test_files_of_a_deleted_record_are_removed(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        _hiring(db_session, orbit, target)
        _files(db_session, orbit, target)
        orbit.tables["training_content_completions"] = [
            c for c in orbit.tables["training_content_completions"] if c["id"] != CC2
        ]
        _training(db_session, orbit, target)
        res = _files(db_session, orbit, target)
        assert res.stats["orphans"] == 1
        refs = {
            f.source_ref
            for f in db_session.query(AppFile.source_ref).filter(
                AppFile.organization_id == target["org"].id
            )
        }
        assert EVIDENCE_URL_2 not in refs and EVIDENCE_URL in refs

    def test_files_uploaded_in_norm_are_never_touched(self, db_session, orbit, target):
        _training(db_session, orbit, target)
        own = AppFile(
            namespace=NS,
            organization_id=target["org"].id,
            collection="completions",
            record_id="no-such-record",
            filename="mine.jpg",
            size_bytes=2,
            data=b"hi",
        )
        db_session.add(own)
        db_session.flush()
        _files(db_session, orbit, target)
        assert db_session.get(AppFile, own.id) is not None


# ---------------------------------------------------------------------------
# helpers, the target check, the client
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_link_file_entry(self):
        copied = {EVIDENCE_URL: {"id": "f1", "filename": "a.jpeg"}}
        linked = {"file_id": "f1", "name": "a.jpeg", "source_url": EVIDENCE_URL}
        assert link_file_entry(EVIDENCE_URL, copied) == linked
        assert link_file_entry(linked, copied) == linked
        assert link_file_entry(EVIDENCE_URL_2, copied) == EVIDENCE_URL_2
        # A link whose copy is gone falls back to the Orbit URL.
        dead = {"file_id": "gone", "name": "b", "source_url": EVIDENCE_URL_2}
        assert link_file_entry(dead, copied) == EVIDENCE_URL_2
        # A Norm-native entry (no Orbit origin) is left alone.
        native = {"file_id": "n1", "name": "c"}
        assert link_file_entry(native, copied) == native

    def test_content_media_urls(self):
        body = {
            "html": f"<img src=\"{MEDIA_URL}\"><img src='{MEDIA_URL}'>"
            f'<a href="https://example.com/x.png">x</a>',
            "url": VIDEO_URL,
        }
        assert content_media_urls(body) == [MEDIA_URL, VIDEO_URL]
        assert content_media_urls(None) == []


class TestTarget:
    def test_prints_the_target_org(self, db_session, orbit, target, capsys):
        code = training_mod.main(
            ["--org-email", target["user"].email, "--dry-run"],
            orbit=orbit,
            db=db_session,
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "target org: Orbit Test Org" in out
        assert "--dry-run: nothing written" in out

    def test_expect_org_mismatch_refuses(self, db_session, orbit, target):
        res = _training(db_session, orbit, target, expect_org="Someone Else")
        assert res.code == 1
        assert _all(db_session, target) == []

    def test_a_member_of_two_orgs_must_choose(self, db_session, orbit, target):
        other = _make_organization(db_session, name="Other Org")
        _make_membership(db_session, target["user"], other)
        assert _training(db_session, orbit, target).code == 1
        res = _training(db_session, orbit, target, org_id=target["org"].id)
        assert res.code == 0 and res.org_name == "Orbit Test Org"

    def test_entry_points_are_callable(self, db_session, orbit, target):
        email = ["--org-email", target["user"].email]
        assert training_mod.main(email, orbit=orbit, db=db_session) == 0
        assert hiring_mod.main(email, orbit=orbit, db=db_session) == 0
        assert (
            files_mod.main(email + ["--workers", "1"], orbit=orbit, db=db_session) == 0
        )


class TestOrbitClient:
    def test_reads_are_keyset_paged_on_id(self, monkeypatch, tmp_path):
        creds = tmp_path / "orbit.json"
        creds.write_text('{"url": "https://p.supabase.co", "service_role_key": "k"}')
        seen = []
        pages = [[{"id": "a"}, {"id": "b"}], [{"id": "c"}]]

        def fake_get(url, headers=None, **_):
            seen.append(url)
            import json as _json

            return _json.dumps(pages[len(seen) - 1]).encode(), {}

        monkeypatch.setattr(training_mod, "_http_get", fake_get)
        rows = Orbit(creds).rows("training_assignments", "status", page=2)
        assert [r["id"] for r in rows] == ["a", "b", "c"]
        assert "select=id,status&order=id.asc&limit=2" in seen[0]
        assert "&id=gt." not in seen[0]
        assert seen[1].endswith("&id=gt.b")

    def test_creds_env_override(self, monkeypatch, tmp_path):
        target_file = tmp_path / "x.json"
        monkeypatch.setenv(training_mod.CREDS_ENV, str(target_file))
        assert training_mod.creds_path() == target_file

    def test_a_worktree_finds_the_main_checkout(self, tmp_path):
        main = tmp_path / "norm"
        (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt\n")
        assert training_mod._main_checkout(wt) == main
        assert training_mod._main_checkout(main) is None


class TestFetchHardening:
    """Pre-push review: row content must not steer the importer — no path
    outside a bucket for the service-keyed read, no URL outside Orbit's public
    storage for the unauthenticated one."""

    def _orbit(self):
        from scripts import migrate_orbit_training as T

        o = T.Orbit.__new__(T.Orbit)
        o.base = "https://abc.supabase.co/rest/v1"
        o.storage = "https://abc.supabase.co/storage/v1"
        o.public_prefix = o.storage + "/object/public/"
        o.headers = {"apikey": "k", "Authorization": "Bearer k"}
        return T, o

    def test_a_climbing_storage_path_is_refused(self):
        T, o = self._orbit()
        for bad in ("../secret", "a/../../b", "/abs", ""):
            with pytest.raises(T.OrbitHTTPError):
                o.fetch_object("candidate-documents", bad)

    def test_only_orbits_public_storage_is_fetched(self):
        T, o = self._orbit()
        for bad in (
            "http://169.254.169.254/latest",
            "https://evil.example/x.png",
            "https://abc.supabase.co/storage/v1/object/public/../x",
        ):
            with pytest.raises(T.OrbitHTTPError):
                o.fetch_url(bad)
