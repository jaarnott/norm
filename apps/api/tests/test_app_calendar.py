"""Calendar invites from the venue mailbox (services/app_calendar.py) — the
``calendar`` door Norm Hiring uses for interviews.

Google is never reached: ``_client`` is replaced by an httpx MockTransport that
plays Calendar, Drive and tokeninfo and records every request. Pinned:

* the event goes on the JOB VENUE's mailbox, guests are the interviewers
  (resolved through the app's people, the mailbox itself left out), updates go
  out to them (``sendUpdates=all``), a group hides its guests from each other;
* the event's ids land on the interview / group, a reschedule PATCHes that
  event, a cancel DELETEs it;
* the candidate pack is uploaded to Drive and shared with the guests only —
  never "anyone with the link" — and if Drive fails the invite still goes;
* no mailbox, a missing scope, the API switched off, an expired grant: a
  message a person can act on, never an exception, and the interview stands;
* tenancy: only this org's venues and venue mailboxes — another org's
  connection is never used, even when its id is handed in.
"""

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.db.models import AppFile, AppRecord, Connection, Role
from app.services import app_calendar as AC
from app.services import app_runtime as AR
from app.services.builtin_apps import bind, get_builtin
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)

FUTURE = (datetime.now(timezone.utc) + timedelta(days=20)).replace(microsecond=0)


class FakeGoogle:
    """Calendar + Drive + tokeninfo, recording what was asked."""

    def __init__(self):
        self.requests = []
        self.events = {}
        self.calendar_status = None  # (code, body) to force a failure
        self.drive_status = None
        self.patch_status = None
        self.n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = None
        if request.content and request.headers.get("content-type", "").startswith(
            "application/json"
        ):
            body = json.loads(request.content)
        self.requests.append((request.method, request.url, body, request))
        url = str(request.url)
        if url.startswith(AC.TOKENINFO):
            return httpx.Response(200, json={"email": "fed@venue.nz", "scope": "x"})
        if "/upload/drive/" in url:
            if self.drive_status:
                return httpx.Response(self.drive_status[0], text=self.drive_status[1])
            self.n += 1
            return httpx.Response(
                200,
                json={"id": f"drive{self.n}", "webViewLink": f"https://drive/{self.n}"},
            )
        if "/drive/v3/files/" in url:
            return httpx.Response(204 if request.method == "DELETE" else 200, json={})
        if "/calendar/v3/" in url:
            if request.method == "GET" and "/events/" in url:
                # The ownership check before an edit or delete: an event this
                # fake created comes back with the tag Norm stored on it.
                event_id = url.split("/events/")[1].split("?")[0]
                stored = self.events.get(event_id)
                if stored is None:
                    return httpx.Response(404, text="gone")
                return httpx.Response(200, json={"id": event_id, **stored})
            if request.method == "DELETE":
                return httpx.Response(204)
            if self.calendar_status:
                return httpx.Response(
                    self.calendar_status[0], text=self.calendar_status[1]
                )
            if request.method == "PATCH":
                if self.patch_status:
                    return httpx.Response(self.patch_status, text="gone")
                event_id = url.split("/events/")[1].split("?")[0]
            else:
                self.n += 1
                event_id = f"ev{self.n}"
            self.events[event_id] = body
            return httpx.Response(
                200,
                json={
                    "id": event_id,
                    "htmlLink": f"https://cal/{event_id}",
                    "organizer": {"email": "fed@venue.nz"},
                },
            )
        return httpx.Response(404, text="unexpected " + url)

    def calls(self, method, fragment):
        return [r for r in self.requests if r[0] == method and fragment in str(r[1])]


@pytest.fixture()
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(
        AC, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fake))
    )
    return fake


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
def venue(db_session, org, author):
    v = _make_venue(db_session, name="The Fed", organization_id=org.id)
    v.timezone = "Pacific/Auckland"
    _make_venue_access(db_session, author, v)
    db_session.flush()
    return v


def _mailbox(db, venue, **kw):
    row = Connection(
        id=str(uuid.uuid4()),
        connector_name=AC.CONNECTOR,
        venue_id=venue.id,
        user_id=None,
        config={},
        enabled="true",
        access_token=kw.get("access_token", "tok"),
        refresh_token="refresh-1",
        token_expires_at=kw.get(
            "expires", datetime.now(timezone.utc) + timedelta(hours=1)
        ),
        oauth_metadata={},
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def hiring(org):
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


def _record(db, rid):
    return db.query(AppRecord).filter(AppRecord.id == rid).one().data


def _small_pdf():
    from app.services.app_pdf import text_pdf

    return text_pdf([("body", "My CV")])


def _world(db, org, author, venue, n=1, with_cv=True):
    job = _rec(
        db,
        org,
        author,
        "job_openings",
        {"title": "Bar Team", "status": "open"},
        venue.id,
    )
    stage = _rec(
        db,
        org,
        author,
        "pipeline_stages",
        {"job_id": job, "name": "Applied", "stage_type": "active", "sort_index": 0},
    )
    pat = _rec(db, org, author, "people", {"name": "Pat Panel", "email": "Pat@fed.nz"})
    box = _rec(
        db, org, author, "people", {"name": "The mailbox", "email": "fed@venue.nz"}
    )
    nobody = _rec(db, org, author, "people", {"name": "No Email"})
    apps = []
    for i in range(n):
        cand = _rec(
            db,
            org,
            author,
            "candidates",
            {"first_name": f"Cand{i}", "last_name": "X", "email": f"c{i}@x.com"},
        )
        apps.append(
            _rec(
                db,
                org,
                author,
                "applications",
                {
                    "job_id": job,
                    "candidate_id": cand,
                    "stage_id": stage,
                    "status": "active",
                    "cover_letter": "Kia ora, I love bars.",
                },
            )
        )
        if with_cv:
            doc = _rec(
                db,
                org,
                author,
                "candidate_documents",
                {
                    "candidate_id": cand,
                    "file_name": "cv.pdf",
                    "mime_type": "application/pdf",
                },
            )
            f = AppFile(
                namespace="hr_suite",
                organization_id=org.id,
                collection="candidate_documents",
                record_id=doc,
                filename="cv.pdf",
                content_type="application/pdf",
                size_bytes=1,
                data=_small_pdf(),
                created_by=author.id,
            )
            db.add(f)
            db.flush()
            rec = db.query(AppRecord).filter(AppRecord.id == doc).one()
            rec.data = {**rec.data, "file_id": f.id}
            db.flush()
    return job, apps, [pat, box, nobody]


class TestSingleInterview:
    def test_invite_from_the_venue_mailbox_to_the_panel(
        self, db_session, org, author, venue, hiring, google
    ):
        conn = _mailbox(db_session, venue)
        job, (app_id,), panel = _world(db_session, org, author, venue)
        out = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
            duration_minutes=45,
            location="Upstairs",
            interviewer_ids=panel,
        )
        cal = out["calendar"]
        assert cal["ok"] is True and cal["mailbox"] == "fed@venue.nz"
        assert (
            "to 1 guest" in out["message"]
            and "No email on file for: No Email" in out["message"]
        )
        method, url, body, _ = google.calls("POST", "/calendars/primary/events")[0]
        assert url.params["sendUpdates"] == "all"
        # guests: the panel, lower-cased; the mailbox itself never invited
        assert body["attendees"] == [
            {"email": "pat@fed.nz", "displayName": "Pat Panel"}
        ]
        assert (
            body["guestsCanSeeOtherGuests"] is True and body["guestsCanModify"] is False
        )
        assert body["summary"] == "Interview: Cand0 X — Bar Team"
        assert body["location"] == "Upstairs"
        start = datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.fromisoformat(body["end"]["dateTime"])
        assert end - start == timedelta(minutes=45) and start == FUTURE
        assert body["start"]["timeZone"] == "Pacific/Auckland"
        assert "Kia ora, I love bars." in body["description"]
        # ids stored on the interview, for the next save
        iv = _record(db_session, out["interview"]["id"])
        assert (
            iv["google_event_id"] == cal["event_id"]
            and iv["google_account_id"] == conn.id
        )
        assert iv["pack_file_id"] and iv["pack_drive_file_ids"] == ["drive1"]

    def test_the_pack_is_shared_with_guests_only(
        self, db_session, org, author, venue, hiring, google
    ):
        _mailbox(db_session, venue)
        job, (app_id,), panel = _world(db_session, org, author, venue)
        out = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
            interviewer_ids=panel,
        )
        perms = google.calls("POST", "/permissions")
        assert [p[2] for p in perms] == [
            {"role": "reader", "type": "user", "emailAddress": "pat@fed.nz"}
        ]
        assert not [p for p in perms if (p[2] or {}).get("type") == "anyone"]
        event = google.events[out["calendar"]["event_id"]]
        assert event["attachments"][0]["fileUrl"] == "https://drive/1"
        assert event["attachments"][0]["mimeType"] == "application/pdf"
        # the pack itself: the CV merged after the cover page
        upload = google.calls("POST", "/upload/drive/")[0][3]
        assert b"%PDF" in upload.content

    def test_a_reschedule_patches_the_same_event_and_drops_the_old_pack(
        self, db_session, org, author, venue, hiring, google
    ):
        _mailbox(db_session, venue)
        job, (app_id,), panel = _world(db_session, org, author, venue)
        made = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
            interviewer_ids=panel,
        )
        event_id = made["calendar"]["event_id"]
        _run(
            db_session,
            hiring,
            author,
            op="update_interview",
            interview_id=made["interview"]["id"],
            fields={"scheduled_at": (FUTURE + timedelta(hours=2)).isoformat()},
        )
        patches = google.calls("PATCH", f"/events/{event_id}")
        assert len(patches) == 1 and patches[0][1].params["sendUpdates"] == "all"
        assert google.calls("DELETE", "/drive/v3/files/drive1")
        iv = _record(db_session, made["interview"]["id"])
        assert iv["google_event_id"] == event_id and iv["pack_drive_file_ids"] == [
            "drive3"
        ]
        # one pack file kept on the interview, not one per save
        packs = (
            db_session.query(AppFile)
            .filter(
                AppFile.collection == "interviews",
                AppFile.record_id == made["interview"]["id"],
            )
            .all()
        )
        assert len(packs) == 1

    def test_an_event_that_has_gone_is_created_afresh(
        self, db_session, org, author, venue, hiring, google
    ):
        _mailbox(db_session, venue)
        job, (app_id,), panel = _world(db_session, org, author, venue, with_cv=False)
        made = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
            interviewer_ids=panel,
        )
        google.patch_status = 404
        moved = _run(
            db_session,
            hiring,
            author,
            op="update_interview",
            interview_id=made["interview"]["id"],
            fields={"location": "Bar"},
        )
        assert moved["calendar"]["ok"] is True
        assert moved["calendar"]["event_id"] != made["calendar"]["event_id"]

    def test_cancel_deletes_the_event(
        self, db_session, org, author, venue, hiring, google
    ):
        _mailbox(db_session, venue)
        job, (app_id,), panel = _world(db_session, org, author, venue, with_cv=False)
        made = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
            interviewer_ids=panel,
        )
        _run(
            db_session,
            hiring,
            author,
            op="cancel_interview",
            interview_id=made["interview"]["id"],
        )
        dels = google.calls("DELETE", f"/events/{made['calendar']['event_id']}")
        assert dels and dels[0][1].params["sendUpdates"] == "all"
        iv = _record(db_session, made["interview"]["id"])
        assert iv["status"] == "cancelled" and iv["google_event_id"] is None


class TestGroupInterview:
    def test_one_event_guests_hidden_then_cancelled(
        self, db_session, org, author, venue, hiring, google
    ):
        _mailbox(db_session, venue)
        job, apps, panel = _world(db_session, org, author, venue, n=2)
        out = _run(
            db_session,
            hiring,
            author,
            op="save_group",
            job_id=job,
            application_ids=apps,
            fields={"scheduled_at": FUTURE.isoformat(), "interviewer_ids": panel[:1]},
        )
        posts = google.calls("POST", "/calendars/primary/events")
        assert len(posts) == 1
        body = posts[0][2]
        assert body["guestsCanSeeOtherGuests"] is False
        assert body["summary"] == "Group interview: Bar Team (2 candidates)"
        start = datetime.fromisoformat(body["start"]["dateTime"])
        assert datetime.fromisoformat(body["end"]["dateTime"]) - start == timedelta(
            minutes=60
        )
        assert (
            "Cand0 X</a>" in body["description"]
            and "Cand1 X</a>" in body["description"]
        )
        assert (
            "/apps/hiring?candidate=" in body["description"]
        )  # a link to each in Norm
        group = _record(db_session, out["group"]["id"])
        assert group["google_event_id"] == out["calendar"]["event_id"]
        _run(db_session, hiring, author, op="cancel_group", group_id=out["group"]["id"])
        assert google.calls("DELETE", f"/events/{group['google_event_id']}")
        assert _record(db_session, out["group"]["id"])["google_event_id"] is None

    def test_removing_the_last_member_removes_the_event(
        self, db_session, org, author, venue, hiring, google
    ):
        _mailbox(db_session, venue)
        job, apps, panel = _world(db_session, org, author, venue, n=1, with_cv=False)
        out = _run(
            db_session,
            hiring,
            author,
            op="save_group",
            job_id=job,
            application_ids=apps,
            fields={"scheduled_at": FUTURE.isoformat()},
        )
        member = [
            r
            for r in db_session.query(AppRecord).filter(
                AppRecord.collection == "interviews",
                AppRecord.organization_id == org.id,
            )
            if (r.data or {}).get("group_id") == out["group"]["id"]
        ][0]
        _run(
            db_session, hiring, author, op="remove_group_member", interview_id=member.id
        )
        assert google.calls("DELETE", f"/events/{out['calendar']['event_id']}")


class TestFriendlyRefusals:
    def test_no_mailbox(self, db_session, org, author, venue, hiring, google):
        job, (app_id,), panel = _world(db_session, org, author, venue)
        out = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
        )
        assert out["calendar"]["ok"] is False
        assert "The Fed has no connected mailbox" in out["message"]
        assert out["interview"]["status"] == "scheduled"  # saved regardless
        assert google.requests == []

    @pytest.mark.parametrize(
        "status,text,words",
        [
            (
                403,
                '{"error": {"status": "PERMISSION_DENIED", "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}',
                "hasn't allowed calendar access",
            ),
            (
                403,
                '{"error": {"errors": [{"reason": "accessNotConfigured"}]}}',
                "Calendar API is switched off",
            ),
            (401, "invalid credentials", "needs reconnecting"),
            (500, "boom", "refused the invite (500)"),
        ],
    )
    def test_google_says_no(
        self, db_session, org, author, venue, hiring, google, status, text, words
    ):
        _mailbox(db_session, venue)
        job, (app_id,), panel = _world(db_session, org, author, venue, with_cv=False)
        google.calendar_status = (status, text)
        out = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
            interviewer_ids=panel,
        )
        assert out["calendar"]["ok"] is False and words in out["calendar"]["message"]
        assert (
            _record(db_session, out["interview"]["id"]).get("google_event_id") is None
        )

    def test_drive_failing_never_stops_the_invite(
        self, db_session, org, author, venue, hiring, google
    ):
        _mailbox(db_session, venue)
        job, (app_id,), panel = _world(db_session, org, author, venue)
        google.drive_status = (403, "insufficient authentication scopes")
        out = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
            interviewer_ids=panel,
        )
        cal = out["calendar"]
        assert cal["ok"] is True and cal["attached"] is False
        assert "hasn't allowed Google Drive" in cal["attachment_note"]
        assert "attachments" not in google.events[cal["event_id"]]

    def test_an_expired_grant_is_refreshed_or_reported(
        self, db_session, org, author, venue, hiring, google, monkeypatch
    ):
        _mailbox(
            db_session, venue, expires=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        job, (app_id,), panel = _world(db_session, org, author, venue, with_cv=False)
        monkeypatch.setattr(AC, "_refresh_token", lambda cfg, venue_id: None)
        out = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
        )
        assert "needs reconnecting" in out["calendar"]["message"]
        monkeypatch.setattr(AC, "_refresh_token", lambda cfg, venue_id: "fresh-token")
        out = _run(
            db_session,
            hiring,
            author,
            op="schedule_interview",
            application_id=app_id,
            scheduled_at=FUTURE.isoformat(),
        )
        assert out["calendar"]["ok"] is True
        post = google.calls("POST", "/calendars/primary/events")[-1][3]
        assert post.headers["Authorization"] == "Bearer fresh-token"


class TestTheDoorDirectly:
    def _door(self, db, org, user):
        app, version = bind(get_builtin("hiring"), org.id)
        return AC.calendar_door(db, None, app, version, user)

    def test_another_orgs_mailbox_is_never_used(
        self, db_session, org, author, venue, google
    ):
        other = _make_organization(db_session)
        theirs = _make_venue(db_session, name="Not Ours", organization_id=other.id)
        their_box = _mailbox(db_session, theirs)
        door = self._door(db_session, org, author)
        event = {"summary": "x", "start": FUTURE.isoformat()}
        # their venue isn't a venue here
        assert door.status(theirs.id)["connected"] is False
        out = door.upsert(theirs.id, event)
        assert out["ok"] is False and google.requests == []
        # nor is their connection, even by id, even for an existing event
        out = door.upsert(venue.id, event, event_id="ev9", account_id=their_box.id)
        assert out["ok"] is False and "no connected mailbox" in out["message"]
        assert google.requests == []
        assert [v["id"] for v in door.venues()] == [venue.id]

    def test_guests_must_belong_to_the_org(
        self, db_session, org, author, venue, google
    ):
        _mailbox(db_session, venue)
        door = self._door(db_session, org, author)
        out = door.upsert(
            venue.id,
            {
                "summary": "x",
                "start": FUTURE.isoformat(),
                "attendees": ["stranger@elsewhere.com", author.email, "person:missing"],
            },
        )
        assert out["ok"] is True
        assert sorted(out["refused"]) == ["person:missing", "stranger@elsewhere.com"]
        body = google.calls("POST", "/events")[0][2]
        assert body["attendees"] == [{"email": author.email.lower()}]

    def test_a_viewer_without_the_venue_cannot_send_from_it(
        self, db_session, org, venue, google
    ):
        _mailbox(db_session, venue)
        outsider = _make_user(db_session, email=f"{uuid.uuid4().hex[:8]}@t.local")
        _make_membership(db_session, outsider, org)
        door = self._door(db_session, org, outsider)
        out = door.upsert(venue.id, {"summary": "x", "start": FUTURE.isoformat()})
        assert out["ok"] is False and "don't have access to The Fed" in out["message"]
        assert google.requests == []

    def test_status_learns_the_mailbox_address_once(
        self, db_session, org, author, venue, google
    ):
        conn = _mailbox(db_session, venue)
        door = self._door(db_session, org, author)
        assert door.status(venue.id)["email"] == "fed@venue.nz"
        assert door.status(venue.id)["email"] == "fed@venue.nz"
        assert len(google.calls("GET", "tokeninfo")) == 1
        assert conn.oauth_metadata["email"] == "fed@venue.nz"
        assert door.venues()[0]["mailbox"] == {
            "connected": True,
            "email": "fed@venue.nz",
            "needs_reconnect": False,
        }

    def test_local_time_is_the_venues(self, db_session, org, author, venue):
        door = self._door(db_session, org, author)
        # 01:30 UTC on 14 Oct 2026 is 2:30pm in Auckland (NZDT, +13)
        assert door.local_time("2026-10-14T01:30:00+00:00", venue.id) == (
            "Wednesday 14 October 2026, 2:30pm"
        )

    def test_the_sandbox_gets_functions_not_the_session(self, db_session, org, author):
        door = self._door(db_session, org, author)
        assert not hasattr(door, "db") and not hasattr(door, "impl")
        assert set(vars(door)) == {"venues", "status", "local_time", "upsert", "delete"}

    def test_user_built_apps_get_no_calendar_or_pdf(self, db_session, org, author):
        from app.connectors import function_executor as FE

        seen = {}
        real = FE.execute_function

        def spy(*a, **kw):
            seen.update(kw)
            return real(*a, **kw)

        import unittest.mock as um

        def ran(builtin):
            seen.clear()
            app, version = bind(get_builtin("hiring"), org.id)
            app.builtin = builtin  # the same spec, as if someone had built it
            with (
                um.patch.object(FE, "execute_function", spy),
                um.patch.object(
                    AR, "resolve_access", lambda *a: AR.AppAccess(True, "owner", True)
                ),
            ):
                AR.run_logic(
                    db_session,
                    None,
                    app=app,
                    version=version,
                    user=author,
                    venue_id=None,
                    params={"op": "sync_status"},
                )
            return dict(seen)

        assert ran(True)["calendar_override"] is not None
        assert ran(True)["pdf_override"] is not None
        built = ran(False)
        assert built["calendar_override"] is None and built["pdf_override"] is None


def test_pack_bytes_are_a_real_pdf():
    from pypdf import PdfReader

    data = _small_pdf()
    assert PdfReader(io.BytesIO(data)).pages[0].extract_text().strip() == "My CV"


class TestNormOnlyTouchesItsOwnEvents:
    """Pre-push review: a stored event id must not let Norm edit or delete an
    event it didn't make (Orbit's, or anyone's); and preview/test runs never
    reach a real calendar."""

    def _impl(self, db_session, mode="live"):
        from app.services.builtin_apps import bind, get_builtin

        org = _make_organization(db_session)
        app, version = bind(get_builtin("hiring"), org.id)
        return AC.VenueCalendar(
            db_session, None, app=app, version=version, user=None, mode=mode
        )

    def test_a_foreign_event_is_never_deleted(self, db_session, google, monkeypatch):
        impl = self._impl(db_session)
        venue = SimpleNamespace(id="v1", name="Venue", timezone=None)
        conn = SimpleNamespace(
            id="c1", access_token="t", token_expires_at=None, oauth_metadata={}
        )
        monkeypatch.setattr(impl, "_venue", lambda vid: venue)
        monkeypatch.setattr(impl, "_allowed", lambda v: None)
        monkeypatch.setattr(impl, "_connection", lambda v, a=None: conn)
        google.events["orbit-ev"] = {
            "summary": "Orbit's",
            "extendedProperties": {"private": {}},
        }
        out = impl.delete("v1", "orbit-ev")
        assert out["ok"] is False and "wasn't made by Norm" in out["message"]
        assert not google.calls("DELETE", "/events/")

    def test_preview_runs_never_reach_google(self, db_session, google):
        impl = self._impl(db_session, mode="dry_run")
        out = impl.upsert("v1", {"summary": "x", "start": "2026-10-20T10:00"})
        assert out["calendar_synced"] is False and not google.requests
        assert impl.delete("v1", "ev1")["ok"] and not google.requests
