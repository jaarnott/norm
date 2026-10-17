"""Norm Hiring, Oct 2026 (Orbit parity, phase 4a): bulk candidate actions,
duplicate / reopen a job, group interviews, the Interviews list, and the
candidate emails — sent through the platform's ``email`` door, once.

What these pin, beyond "it works":

* bulk actions run one candidate at a time and report "N done, M failed" —
  one bad row never stops the rest; not progressing demands a reason;
* the email merge fields are all filled, ``venue_name`` included (Orbit's is
  always empty); a batch is keyed so a retry never reaches anyone twice;
  dry-run and test modes send nothing to candidates and log nothing;
* an interview invitation goes on create and reschedule ONLY (Orbit re-sends
  it on every save);
* a duplicate never carries the import's ``orbit_id`` (the next refresh would
  "correct" it), and a reopened job is approved but unpublished;
* a group interview gives every candidate their own interview row, keeps them
  in step with the group, and cancels cleanly;
* who did something comes from the platform's viewer, not the page.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.db.models import AppRecord, EmailLog, Role
from app.services import app_runtime as AR
from app.services.app_email import set_email_enabled
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)

FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
LATER = FUTURE + timedelta(days=1)


@pytest.fixture()
def org(db_session):
    return _make_organization(db_session)


@pytest.fixture()
def author(db_session, org):
    user = _make_user(
        db_session, email=f"{uuid.uuid4().hex[:8]}@t.local", full_name="Hannah Hiring"
    )
    mem = _make_membership(db_session, user, org)
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=f"r-{uuid.uuid4().hex[:6]}",
        display_name="r",
        permissions=["hr:read", "org:manage", "email:read"],
    )
    db_session.add(role)
    db_session.flush()
    mem.role_id = role.id
    db_session.flush()
    return user


@pytest.fixture()
def venue(db_session, org, author):
    v = _make_venue(db_session, name="The Fed", organization_id=org.id)
    _make_venue_access(db_session, author, v)
    return v


@pytest.fixture()
def hiring(org):
    from app.services.builtin_apps import bind, get_builtin

    return bind(get_builtin("hiring"), org.id)


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


def _run(db, hiring, user, **params):
    app, version = hiring
    out = AR.run_logic(
        db, None, app=app, version=version, user=user, venue_id=None, params=params
    )
    assert out["success"] is True, out.get("error")
    return out["data"]


def _get(db, hiring, user, collection, record_id):
    return AR.store_get(
        db,
        app=hiring[0],
        version=hiring[1],
        user=user,
        collection=collection,
        record_id=record_id,
    )


def _list(db, org, collection, **where):
    rows = (
        db.query(AppRecord)
        .filter(
            AppRecord.organization_id == org.id,
            AppRecord.namespace == "hr_suite",
            AppRecord.collection == collection,
        )
        .all()
    )
    return [
        {"id": r.id, **(r.data or {})}
        for r in rows
        if all((r.data or {}).get(k) == v for k, v in where.items())
    ]


def _job(db, org, user, title="Bar Team", venue_id=None, **extra):
    job = _rec(
        db,
        org,
        user,
        "job_openings",
        {"title": title, "status": "open", **extra},
        venue_id=venue_id,
    )
    stages = {}
    for i, (name, kind) in enumerate(
        [
            ("Applied", "active"),
            ("Trial", "active"),
            ("Hired", "hired"),
            ("Rejected", "rejected"),
        ]
    ):
        stages[name] = _rec(
            db,
            org,
            user,
            "pipeline_stages",
            {"job_id": job, "name": name, "stage_type": kind, "sort_index": i},
        )
    return job, stages


def _applicant(
    db, org, user, job, stage, first="Maia", email="maia@example.com", **app
):
    candidate = _rec(
        db,
        org,
        user,
        "candidates",
        {"first_name": first, "last_name": "Ngata", "email": email},
    )
    application = _rec(
        db,
        org,
        user,
        "applications",
        {
            "job_id": job,
            "candidate_id": candidate,
            "stage_id": stage,
            "status": "active",
            **app,
        },
    )
    return candidate, application


@pytest.fixture()
def mail_on(db_session, org, monkeypatch):
    """Email on for Hiring, live sends allowed here, delivery mocked."""
    set_email_enabled(db_session, org.id, "hiring", True, None)
    monkeypatch.setenv("APP_EMAIL_ALLOW_NONPROD", "1")
    with patch(
        "app.services.app_email.AppEmail._deliver", return_value=("sent", None, "pid")
    ) as deliver:
        yield deliver


class TestBulkActions:
    def test_move_to_stage_derives_status_and_reports_counts(
        self, db_session, org, author, hiring
    ):
        job, stages = _job(db_session, org, author)
        other, other_stages = _job(db_session, org, author, title="Kitchen")
        _, a1 = _applicant(
            db_session, org, author, job, stages["Applied"], email="a@x.com"
        )
        _, a2 = _applicant(
            db_session, org, author, job, stages["Applied"], email="b@x.com"
        )
        _, stranger = _applicant(
            db_session, org, author, other, other_stages["Applied"], email="c@x.com"
        )
        out = _run(
            db_session,
            hiring,
            author,
            op="bulk_move",
            application_ids=[a1, a2, stranger, "missing"],
            stage_id=stages["Hired"],
        )
        assert out["done"] == 2 and out["failed"] == 2
        assert {e["id"] for e in out["errors"]} == {stranger, "missing"}
        assert _get(db_session, hiring, author, "applications", a1)["status"] == "hired"
        # the derived fill count follows
        assert (
            _get(db_session, hiring, author, "job_openings", job)["positions_filled"]
            == 2
        )
        assert (
            _get(db_session, hiring, author, "applications", stranger)["status"]
            == "active"
        )

    def test_not_progressing_needs_a_reason_and_logs_it(
        self, db_session, org, author, hiring
    ):
        job, stages = _job(db_session, org, author)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        refused = _run(
            db_session,
            hiring,
            author,
            op="bulk_reject",
            application_ids=[a1],
            reason=" ",
        )
        assert "reason" in refused["error"]
        out = _run(
            db_session,
            hiring,
            author,
            op="bulk_reject",
            application_ids=[a1],
            reason="No weekend availability",
        )
        assert out == {"done": 1, "failed": 0, "errors": []}
        app = _get(db_session, hiring, author, "applications", a1)
        assert (
            app["status"] == "rejected"
            and app["rejection_reason"] == "No weekend availability"
        )
        log = _list(db_session, org, "activity", application_id=a1)
        assert log[0]["summary"] == "Not progressing: No weekend availability"
        assert log[0]["actor"] == "Hannah Hiring"  # the viewer, not 'Norm'

    def test_add_to_talent_pool(self, db_session, org, author, hiring):
        job, stages = _job(db_session, org, author)
        cand, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        out = _run(
            db_session, hiring, author, op="bulk_talent_pool", application_ids=[a1]
        )
        assert out["done"] == 1
        assert (
            _get(db_session, hiring, author, "candidates", cand)["in_talent_pool"]
            is True
        )

    def test_the_candidates_list_spans_jobs_and_filters_to_one(
        self, db_session, org, author, hiring
    ):
        job, stages = _job(db_session, org, author)
        other, other_stages = _job(db_session, org, author, title="Kitchen")
        _applicant(db_session, org, author, job, stages["Trial"], email="a@x.com")
        _applicant(
            db_session, org, author, other, other_stages["Applied"], email="b@x.com"
        )
        everyone = _run(db_session, hiring, author, op="candidates")
        assert len(everyone["applications"]) == 2
        assert everyone["stages"] == []
        one = _run(db_session, hiring, author, op="candidates", job_id=job)
        assert [a["stage_name"] for a in one["applications"]] == ["Trial"]
        assert [s["name"] for s in one["stages"]][:2] == ["Applied", "Trial"]


class TestBulkEmail:
    def _setup(self, db, org, author, venue):
        manager = _rec(
            db, org, author, "people", {"name": "Sam Boss", "email": "sam@fed.nz"}
        )
        job, stages = _job(
            db,
            org,
            author,
            title="Bar Team",
            venue_id=venue.id,
            hiring_manager_id=manager,
        )
        _, a1 = _applicant(
            db,
            org,
            author,
            job,
            stages["Applied"],
            first="Maia",
            email="maia@example.com",
        )
        _, a2 = _applicant(
            db, org, author, job, stages["Applied"], first="Tama", email=""
        )
        return job, a1, a2

    def test_merge_fields_are_all_filled_venue_name_included(
        self, db_session, org, author, hiring, venue
    ):
        _job_id, a1, a2 = self._setup(db_session, org, author, venue)
        out = _run(
            db_session, hiring, author, op="email_compose", application_ids=[a1, a2]
        )
        maia = out["recipients"][0]["values"]
        assert maia["candidate_first_name"] == "Maia"
        assert maia["job_title"] == "Bar Team"
        assert maia["venue_name"] == "The Fed"
        assert maia["hiring_manager"] == "Sam Boss"
        assert out["recipients"][1]["email"] in ("", None)
        assert out["enabled"] is False

    def test_one_copy_each_once_per_batch_and_logged(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        _job_id, a1, a2 = self._setup(db_session, org, author, venue)
        send = dict(
            op="bulk_email",
            application_ids=[a1, a2],
            subject="Hi {{candidate_first_name}}",
            body_html="<p>About {{job_title}} at {{venue_name}}</p>",
            batch_id="b1",
        )
        out = _run(db_session, hiring, author, **send)
        assert out["statuses"] == ["sent"] and out["no_email"] == 1
        args = mail_on.call_args.args  # (to, cc, reply_to, subject, html)
        assert args[0] == ["maia@example.com"] and args[3] == "Hi Maia"
        assert "About Bar Team at The Fed" in args[4]
        # the same batch again (a retry, a double click) reaches nobody
        again = _run(db_session, hiring, author, **send)
        assert again["statuses"] == ["skipped"] and mail_on.call_count == 1
        emails = _list(db_session, org, "hiring_emails", application_id=a1)
        assert len(emails) == 1 and emails[0]["status"] == "sent"
        assert (
            emails[0]["subject"] == "Hi Maia"
            and emails[0]["sent_by_name"] == "Hannah Hiring"
        )

    def test_dry_run_and_test_send_nothing_to_candidates(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        _job_id, a1, _a2 = self._setup(db_session, org, author, venue)
        base = dict(
            op="bulk_email",
            application_ids=[a1],
            subject="Hi {{candidate_first_name}}",
            body_html="<p>x</p>",
            batch_id="b2",
        )
        dry = _run(db_session, hiring, author, _email_mode="dry_run", **base)
        assert (
            dry["statuses"] == ["dry_run"]
            and dry["previews"][0]["subject"] == "Hi Maia"
        )
        assert mail_on.call_count == 0
        test = _run(db_session, hiring, author, _email_mode="test", **base)
        assert test["statuses"] == ["sent"]
        assert mail_on.call_args.args[0] == [author.email]  # only the tester
        assert mail_on.call_args.args[3] == "[TEST] Hi Maia"
        assert _list(db_session, org, "hiring_emails") == []

    def test_email_off_says_so_and_sends_nothing(
        self, db_session, org, author, hiring, venue
    ):
        _job_id, a1, _a2 = self._setup(db_session, org, author, venue)
        with patch("app.services.app_email.AppEmail._deliver") as deliver:
            out = _run(
                db_session,
                hiring,
                author,
                op="bulk_email",
                application_ids=[a1],
                subject="Hi",
                body_html="<p>x</p>",
                batch_id="b3",
            )
        assert out["statuses"] == ["disabled"] and "Email is off" in out["message"]
        deliver.assert_not_called()

    def test_merge_values_are_escaped_into_the_body(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        job, stages = _job(
            db_session, org, author, title="Bar <Team>", venue_id=venue.id
        )
        _, a1 = _applicant(
            db_session, org, author, job, stages["Applied"], first="<b>Mo</b>"
        )
        _run(
            db_session,
            hiring,
            author,
            op="bulk_email",
            application_ids=[a1],
            subject="{{job_title}}",
            body_html="<p>{{candidate_first_name}}</p>",
            batch_id="b4",
        )
        subject, html = mail_on.call_args.args[3], mail_on.call_args.args[4]
        assert subject == "Bar <Team>"  # a subject is text
        assert "&lt;b&gt;Mo&lt;/b&gt;" in html and "<b>Mo</b>" not in html


class TestInterviewInvitations:
    def test_sent_on_create_and_reschedule_only(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        job, stages = _job(db_session, org, author, venue_id=venue.id)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        made = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=a1,
            scheduled_at=FUTURE.isoformat(),
            location="Upstairs bar",
        )
        iv = made["interview"]
        assert made["email"]["status"] == "sent" and mail_on.call_count == 1
        assert "Interview invitation — Bar Team" == mail_on.call_args.args[3]
        assert "Upstairs bar" in mail_on.call_args.args[4]
        # an edit that doesn't move it: no second email
        _run(
            db_session,
            hiring,
            author,
            op="update_interview",
            interview_id=iv["id"],
            fields={"location": "Downstairs"},
        )
        assert mail_on.call_count == 1
        # a reschedule: a new invitation
        moved = _run(
            db_session,
            hiring,
            author,
            op="update_interview",
            interview_id=iv["id"],
            fields={"scheduled_at": LATER.isoformat()},
        )
        assert moved["email"]["status"] == "sent" and mail_on.call_count == 2
        assert "Downstairs" in mail_on.call_args.args[4]
        # no calendar mailbox here: the interview still saved, and it says why
        assert "no connected mailbox" in moved["calendar"]["message"]

    def test_saving_an_imported_time_without_its_seconds_is_not_a_move(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        """The page's date-time field has no seconds: opening an imported
        10:00:30 interview and saving it must not re-invite the candidate."""
        job, stages = _job(db_session, org, author, venue_id=venue.id)
        cand, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        at = FUTURE.replace(second=30)
        iv = _rec(
            db_session,
            org,
            author,
            "interviews",
            {
                "application_id": a1,
                "candidate_id": cand,
                "job_id": job,
                "scheduled_at": at.isoformat(),
                "status": "scheduled",
                "invite_candidate": True,
                "orbit_id": "o-iv",
            },
        )
        as_the_page_sends = at.replace(second=0).strftime("%Y-%m-%dT%H:%M:00.000Z")
        out = _run(
            db_session,
            hiring,
            author,
            op="update_interview",
            interview_id=iv,
            fields={"scheduled_at": as_the_page_sends, "location": "Bar"},
        )
        assert out["email"] is None and mail_on.call_count == 0

    def test_unticked_means_no_email(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        job, stages = _job(db_session, org, author, venue_id=venue.id)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        made = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=a1,
            scheduled_at=FUTURE.isoformat(),
            invite_candidate=False,
        )
        assert made["email"] is None and mail_on.call_count == 0

    def test_created_by_is_the_viewers_person(
        self, db_session, org, author, hiring, venue
    ):
        me = _rec(
            db_session,
            org,
            author,
            "people",
            {"name": "Hannah", "norm_user_id": author.id},
        )
        job, stages = _job(db_session, org, author, venue_id=venue.id)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        iv = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=a1,
            scheduled_at=FUTURE.isoformat(),
            interviewer_ids=[me],
        )["interview"]
        assert iv["created_by"] == me and iv["created_by_user_id"] == author.id
        assert iv["interviewer_ids"] == [me]


class TestJobs:
    def test_duplicate_copies_everything_but_identity(
        self, db_session, org, author, hiring
    ):
        job, stages = _job(
            db_session,
            org,
            author,
            title="Head Chef",
            orbit_id="orb-1",
            department="Kitchen",
            pay_min=30,
            slug="head-chef",
        )
        _rec(
            db_session,
            org,
            author,
            "application_fields",
            {
                "job_id": job,
                "field_key": "rtw",
                "label": "Right to work",
                "orbit_id": "f1",
            },
        )
        _rec(
            db_session,
            org,
            author,
            "hiring_team",
            {"job_id": job, "role": "hiring_manager", "person_id": "p1"},
        )
        copy = _run(db_session, hiring, author, op="duplicate_job", job_id=job)["job"]
        assert copy["title"] == "Head Chef (Copy)"
        assert copy["department"] == "Kitchen" and copy["pay_min"] == 30
        assert copy["status"] == "draft" and copy["is_published"] is False
        assert "orbit_id" not in copy and "slug" not in copy
        stages_copy = _list(db_session, org, "pipeline_stages", job_id=copy["id"])
        assert sorted(s["name"] for s in stages_copy) == sorted(stages)
        fields = _list(db_session, org, "application_fields", job_id=copy["id"])
        assert [f["field_key"] for f in fields] == ["rtw"] and "orbit_id" not in fields[
            0
        ]
        assert (
            _list(db_session, org, "hiring_team", job_id=copy["id"])[0]["person_id"]
            == "p1"
        )
        approval = _list(db_session, org, "job_approvals", job_id=copy["id"])[0]
        assert approval["action"] == "duplicated"
        assert approval["comment"] == 'Duplicated from "Head Chef"'
        assert approval["actor_name"] == "Hannah Hiring"

    def test_reopen_is_approved_and_unpublished(self, db_session, org, author, hiring):
        """Only a closed job reopens; a reopened one is approved, unpublished."""
        job, _ = _job(db_session, org, author)
        out = _run(db_session, hiring, author, op="reopen_job", job_id=job)
        assert "only a closed job" in out["error"]
        _run(
            db_session,
            hiring,
            author,
            op="set_job_status",
            job_id=job,
            is_published=True,
        )
        _run(
            db_session, hiring, author, op="set_job_status", job_id=job, status="closed"
        )
        out = _run(db_session, hiring, author, op="reopen_job", job_id=job)["job"]
        assert out["status"] == "approved" and out["is_published"] is False
        assert out["closed_at"] is None
        actions = [
            a["action"] for a in _list(db_session, org, "job_approvals", job_id=job)
        ]
        assert sorted(actions) == ["closed", "published", "reopened"]
        # publishing a reopened job opens it again
        again = _run(
            db_session,
            hiring,
            author,
            op="set_job_status",
            job_id=job,
            is_published=True,
        )
        assert again["job"]["status"] == "open"


class TestGroupInterviews:
    def _group(self, db, org, author, hiring, venue, n=2):
        job, stages = _job(db, org, author, venue_id=venue.id)
        apps = [
            _applicant(
                db,
                org,
                author,
                job,
                stages["Applied"],
                first=f"C{i}",
                email=f"c{i}@x.com",
            )[1]
            for i in range(n)
        ]
        panel = _rec(
            db, org, author, "people", {"name": "Pat Panel", "email": "pat@fed.nz"}
        )
        out = _run(
            db,
            hiring,
            author,
            op="save_group",
            job_id=job,
            application_ids=apps,
            fields={
                "scheduled_at": FUTURE.isoformat(),
                "location": "Function room",
                "interviewer_ids": [panel],
            },
        )
        return job, apps, panel, out

    def test_each_candidate_gets_their_own_interview(
        self, db_session, org, author, hiring, venue
    ):
        job, apps, panel, out = self._group(db_session, org, author, hiring, venue)
        group = out["group"]
        assert out["added"] == 2 and group["status"] == "scheduled"
        assert group["duration_minutes"] == 60  # a group's default
        members = _list(db_session, org, "interviews", group_id=group["id"])
        assert {m["application_id"] for m in members} == set(apps)
        assert all(
            m["location"] == "Function room" and m["interviewer_ids"] == [panel]
            for m in members
        )
        assert all(m["stage_id"] and m["job_id"] == job for m in members)
        log = _list(db_session, org, "activity", application_id=apps[0])
        assert log[0]["summary"] == "Added to group interview"

    def test_adding_again_skips_live_members_and_edits_flow_to_all(
        self, db_session, org, author, hiring, venue
    ):
        job, apps, _panel, out = self._group(db_session, org, author, hiring, venue)
        gid = out["group"]["id"]
        _, newbie = _applicant(
            db_session,
            org,
            author,
            job,
            _list(db_session, org, "pipeline_stages", job_id=job, name="Applied")[0][
                "id"
            ],
            first="New",
            email="new@x.com",
        )
        again = _run(
            db_session,
            hiring,
            author,
            op="save_group",
            group_id=gid,
            application_ids=apps + [newbie],
            fields={"location": "Courtyard"},
        )
        assert again["added"] == 1 and again["skipped"] == 2
        members = _list(db_session, org, "interviews", group_id=gid)
        assert len(members) == 3 and all(m["location"] == "Courtyard" for m in members)

    def test_a_candidate_from_another_job_is_not_added(
        self, db_session, org, author, hiring, venue
    ):
        _job_id, _apps, _panel, out = self._group(
            db_session, org, author, hiring, venue
        )
        other, stages = _job(db_session, org, author, title="Kitchen")
        _, stranger = _applicant(
            db_session, org, author, other, stages["Applied"], email="s@x.com"
        )
        res = _run(
            db_session,
            hiring,
            author,
            op="save_group",
            group_id=out["group"]["id"],
            application_ids=[stranger],
            fields={},
        )
        assert res["added"] == 0 and res["skipped"] == 1

    def test_remove_then_readd_gets_a_fresh_row(
        self, db_session, org, author, hiring, venue
    ):
        _job_id, apps, _panel, out = self._group(db_session, org, author, hiring, venue)
        gid = out["group"]["id"]
        first = [
            m
            for m in _list(db_session, org, "interviews", group_id=gid)
            if m["application_id"] == apps[0]
        ][0]
        _run(
            db_session,
            hiring,
            author,
            op="remove_group_member",
            interview_id=first["id"],
        )
        assert (
            _get(db_session, hiring, author, "interviews", first["id"])["status"]
            == "cancelled"
        )
        back = _run(
            db_session,
            hiring,
            author,
            op="save_group",
            group_id=gid,
            application_ids=[apps[0]],
            fields={},
        )
        assert back["added"] == 1
        rows = [
            m
            for m in _list(db_session, org, "interviews", group_id=gid)
            if m["application_id"] == apps[0]
        ]
        assert sorted(r["status"] for r in rows) == ["cancelled", "scheduled"]

    def test_cancel_cancels_the_group_and_every_member(
        self, db_session, org, author, hiring, venue
    ):
        _job_id, _apps, _panel, out = self._group(
            db_session, org, author, hiring, venue
        )
        gid = out["group"]["id"]
        _run(db_session, hiring, author, op="cancel_group", group_id=gid)
        assert (
            _get(db_session, hiring, author, "interview_groups", gid)["status"]
            == "cancelled"
        )
        assert {
            m["status"] for m in _list(db_session, org, "interviews", group_id=gid)
        } == {"cancelled"}
        refused = _run(
            db_session, hiring, author, op="save_group", group_id=gid, fields={}
        )
        assert "cancelled" in refused["error"]

    def test_invitations_on_join_and_on_reschedule_only(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        _job_id, apps, _panel, out = self._group(db_session, org, author, hiring, venue)
        gid = out["group"]["id"]
        assert mail_on.call_count == 2  # both members, once each
        _run(
            db_session,
            hiring,
            author,
            op="save_group",
            group_id=gid,
            fields={"location": "Bar"},
        )
        assert mail_on.call_count == 2
        _run(
            db_session,
            hiring,
            author,
            op="save_group",
            group_id=gid,
            fields={"scheduled_at": LATER.isoformat()},
        )
        assert mail_on.call_count == 4

    def test_a_group_member_is_edited_through_its_group(
        self, db_session, org, author, hiring, venue
    ):
        _job_id, _apps, _panel, out = self._group(
            db_session, org, author, hiring, venue
        )
        member = _list(db_session, org, "interviews", group_id=out["group"]["id"])[0]
        res = _run(
            db_session,
            hiring,
            author,
            op="update_interview",
            interview_id=member["id"],
            fields={"location": "x"},
        )
        assert "part of a group interview" in res["error"]

    def test_the_interviews_list_shows_a_group_as_one_row(
        self, db_session, org, author, hiring, venue
    ):
        job, apps, panel, out = self._group(db_session, org, author, hiring, venue)
        # plus one single interview in the past
        _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=apps[0],
            scheduled_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        )
        upcoming = _run(db_session, hiring, author, op="interviews", when="upcoming")
        rows = upcoming["rows"]
        assert len(rows) == 1 and rows[0]["group_id"] == out["group"]["id"]
        assert len(rows[0]["members"]) == 2
        assert rows[0]["interviewer_names"] == ["Pat Panel"]
        assert rows[0]["venue_name"] == "The Fed"
        past = _run(db_session, hiring, author, op="interviews", when="past")
        assert len(past["rows"]) == 1 and past["rows"][0]["group_id"] is None

    def test_add_mode_lists_upcoming_groups_with_counts(
        self, db_session, org, author, hiring, venue
    ):
        job, _apps, _panel, out = self._group(db_session, org, author, hiring, venue)
        form = _run(db_session, hiring, author, op="interview_form", job_id=job)
        assert [(g["id"], g["member_count"]) for g in form["groups"]] == [
            (out["group"]["id"], 2)
        ]
        assert [p["name"] for p in form["people"]] == ["Pat Panel"]


class TestSyncBanner:
    def test_silent_until_something_came_from_orbit(
        self, db_session, org, author, hiring
    ):
        assert _run(db_session, hiring, author, op="sync_status") == {
            "refreshed_at": None
        }
        job, stages = _job(db_session, org, author)
        _applicant(db_session, org, author, job, stages["Applied"], orbit_id="o-app")
        out = _run(db_session, hiring, author, op="sync_status")
        assert out["refreshed_at"]


class TestTenancy:
    def test_another_orgs_rows_are_never_touched(self, db_session, org, author, hiring):
        other = _make_organization(db_session)
        job, stages = _job(db_session, other, author)
        _, theirs = _applicant(db_session, other, author, job, stages["Applied"])
        out = _run(
            db_session,
            hiring,
            author,
            op="bulk_move",
            application_ids=[theirs],
            stage_id=_job(db_session, org, author)[1]["Hired"],
        )
        assert out["done"] == 0 and out["failed"] == 1
        sent = (
            db_session.query(EmailLog)
            .filter(EmailLog.organization_id == other.id)
            .count()
        )
        assert sent == 0


class TestPrePushReviewFixes:
    """Findings from the security review before push (12 Oct 2026)."""

    def test_only_someone_who_runs_hiring_emails_candidates(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        _job_id, a1, _a2 = TestBulkEmail()._setup(db_session, org, author, venue)
        member = _make_user(db_session, email=f"{uuid.uuid4().hex[:8]}@t.local")
        mem = _make_membership(db_session, member, org)
        role = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name=f"m-{uuid.uuid4().hex[:6]}",
            display_name="Team Member",
            permissions=["hr:read"],
        )
        db_session.add(role)
        db_session.flush()
        mem.role_id = role.id
        db_session.flush()
        out = _run(
            db_session,
            hiring,
            member,
            op="bulk_email",
            application_ids=[a1],
            subject="Hi",
            body_html="<p>x</p>",
            batch_id="b1",
        )
        assert "Only someone who runs hiring" in out["error"]
        assert not mail_on.called

    def test_a_typed_body_is_sanitised(self, db_session, org, author, hiring, venue):
        _job_id, a1, _a2 = TestBulkEmail()._setup(db_session, org, author, venue)
        out = _run(
            db_session,
            hiring,
            author,
            op="bulk_email",
            application_ids=[a1],
            subject="Hi",
            body_html='<p onclick="x()">Hello <b>{{candidate_first_name}}</b></p>'
            '<script>steal()</script><a href="javascript:alert(1)">x</a>'
            '<img src="https://track/p.gif">',
            batch_id="b2",
            _email_mode="dry_run",
        )
        html = out["previews"][0]["html"]
        assert "<b>Maia</b>" in html
        assert "steal()" not in html and "onclick" not in html
        assert "javascript:" not in html and "track/p.gif" not in html

    def _member(self, db, org):
        member = _make_user(db, email=f"{uuid.uuid4().hex[:8]}@t.local")
        mem = _make_membership(db, member, org)
        role = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name=f"m-{uuid.uuid4().hex[:6]}",
            display_name="Team Member",
            permissions=["hr:read"],
        )
        db.add(role)
        db.flush()
        mem.role_id = role.id
        db.flush()
        return member

    def test_invitations_need_someone_who_runs_hiring(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        job, stages = _job(db_session, org, author, venue_id=venue.id)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        member = self._member(db_session, org)
        made = _run(
            db_session,
            hiring,
            member,
            op="schedule_interview",
            application_id=a1,
            scheduled_at=FUTURE.isoformat(),
            location="Upstairs",
        )
        assert made["interview"]["id"]  # the interview is still saved
        assert made["email"]["status"] == "not_allowed" and not mail_on.called

    def test_a_meeting_link_must_be_a_web_link(
        self, db_session, org, author, hiring, venue, mail_on
    ):
        job, stages = _job(db_session, org, author, venue_id=venue.id)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=a1,
            scheduled_at=FUTURE.isoformat(),
            interview_type="video",
            meeting_url="javascript:alert(1)",
        )
        html = mail_on.call_args.args[4]
        assert 'href="javascript' not in html

    def test_hiring_someone_needs_someone_who_runs_hiring(
        self, db_session, org, author, hiring, venue
    ):
        job, stages = _job(db_session, org, author, venue_id=venue.id)
        _, a1 = _applicant(db_session, org, author, job, stages["Applied"])
        member = self._member(db_session, org)
        out = _run(db_session, hiring, member, op="hire", application_id=a1)
        assert "Only someone who runs hiring" in out["error"]
