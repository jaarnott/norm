"""The staff directory (docs/plans/staff-access.md, 11.6): the copy from
Loaded, the hourly slot, by-hand and spreadsheet edits, tablet PINs, and the
People, staff and device routes.

Loaded is never called: ``execute_connector_tool`` is faked with payloads in
the shape the real ``get_staff_members`` returns (a flat list with ``id``,
``name``, ``email``, ``phoneMobile``, ``datestampDeleted``,
``defaultMemberRoleRoleName`` and ``memberRoles[]``). The sign-in stream's
``services/staff_access`` (claim_account, ensure_membership, revoke) is
stubbed, so these tests say only that the directory CALLS it.
"""

from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.security import decode_access_token
from app.connectors import tool_executor
from app.connectors.tool_executor import ToolResult
from app.db.engine import get_config_db, get_db
from app.db.models import (
    AppRecord,
    Connection,
    OrganizationMembership,
    Role,
    StaffMember,
    StaffSyncRun,
    User,
    UserVenueAccess,
    VenueDevice,
)
from app.routers import staff as staff_router
from app.routers import tablet as tablet_router
from app.services import staff_directory, staff_sync
from app.services.active_org import ActiveOrgMiddleware
from app.services.staff_directory import DirectoryError
from tests.conftest import _make_membership, _make_user, _make_venue
from tests.org_helpers import auth_headers, make_org, seed_system_roles

# The shared test app doesn't mount the staff routers; this file's own does.
_app = FastAPI()
_app.add_middleware(ActiveOrgMiddleware)
_app.include_router(staff_router.router)
_app.include_router(tablet_router.router)


@pytest.fixture()
def staff_client(db_session):
    def _override():
        yield db_session

    _app.dependency_overrides[get_db] = _override
    _app.dependency_overrides[get_config_db] = _override
    with TestClient(_app, raise_server_exceptions=False) as c:
        yield c
    _app.dependency_overrides.clear()


@pytest.fixture()
def access_calls(monkeypatch):
    """A stand-in for services/staff_access that records what the directory
    asked of it. ``claim_account`` makes a real User so the tablet path has
    someone to issue a token for."""
    calls = {"revoke": [], "ensure": [], "claim": []}
    mod = types.ModuleType("app.services.staff_access")

    def revoke(db, staff):
        calls["revoke"].append(staff.id)

    def ensure_membership(db, user, staff):
        calls["ensure"].append((user.id, staff.id))

    def claim_account(db, email):
        calls["claim"].append(email)
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(
                id=str(uuid.uuid4()),
                email=email,
                hashed_password="x",
                full_name="Claimed",
                role="user",
                is_active=True,
                password_set=False,
            )
            db.add(user)
            db.flush()
        for staff in db.query(StaffMember).filter(StaffMember.email == email).all():
            staff.user_id = user.id
            ensure_membership(db, user, staff)
        db.flush()
        return user

    mod.revoke = revoke
    mod.ensure_membership = ensure_membership
    mod.claim_account = claim_account
    import app.services as services_pkg

    monkeypatch.setitem(sys.modules, "app.services.staff_access", mod)
    monkeypatch.setattr(services_pkg, "staff_access", mod, raising=False)
    return calls


def loaded_row(
    staff_id, name, email=None, roles=("BAR TEAM",), deleted=None, phone="021 000"
):
    """One row as Loaded's /api/staff-members returns it (seen 23 Oct 2026)."""
    return {
        "id": staff_id,
        "name": name,
        "email": email,
        "phoneMobile": phone,
        "datestampDeleted": deleted,
        "defaultMemberRoleRoleName": roles[0] if roles else None,
        "memberRoles": [{"roleName": r, "hourlyRate": 26.5} for r in roles],
        "remunerationTimeline": [],
        "showInRoster": True,
        "hasPin": True,
    }


def _connect(db, venue):
    db.add(
        Connection(
            connector_name="loadedhub",
            venue_id=venue.id,
            enabled="true",
            config={"x_loaded_company_id": "c-" + venue.id[:6]},
        )
    )
    db.flush()


def _fake_loaded(monkeypatch, payloads: dict):
    """``execute_connector_tool`` answering each venue from ``payloads``
    (venue id → rows, or an Exception / error string)."""
    calls = []

    def fake(connector, action, params, db, config_db, venue_id=None, **kw):
        calls.append((connector, action, params, venue_id, kw))
        assert connector == "loadedhub" and action == "get_staff_members"
        assert kw.get("strict_venue") is True
        answer = payloads.get(venue_id)
        if isinstance(answer, str):
            return ToolResult(success=False, payload=None, error=answer)
        return ToolResult(success=True, payload=answer or [])

    monkeypatch.setattr(tool_executor, "execute_connector_tool", fake)
    return calls


def _world(db):
    """System roles, an org with two venues, an owner and a team member."""
    roles = seed_system_roles(db)
    org = make_org(db, "Lott Group")
    a = _make_venue(db, name="La Zeppa", organization_id=org.id)
    b = _make_venue(db, name="Mr Murdochs", organization_id=org.id)
    owner = _make_user(db, full_name="Owner")
    _make_membership(db, owner, org).role_id = roles["owner"].id
    member = _make_user(db, full_name="Member")
    _make_membership(db, member, org).role_id = roles["team_member"].id
    db.flush()
    return types.SimpleNamespace(
        roles=roles, org=org, a=a, b=b, owner=owner, member=member
    )


def _loaded_staff(db, org_id):
    return {
        s.external_id: s
        for s in db.query(StaffMember)
        .filter(
            StaffMember.organization_id == org_id, StaffMember.source == "loadedhub"
        )
        .all()
    }


# ---------------------------------------------------------------------------
# upsert_from_loaded
# ---------------------------------------------------------------------------


class TestUpsertFromLoaded:
    def test_adds_people_with_their_roles_and_skips_deleted_rows(self, db_session):
        w = _world(db_session)
        now = datetime.now(timezone.utc)
        rows = [
            loaded_row("L1", "Sam Chef", "Sam@Example.com", ("KITCHEN", "Sous Chef")),
            loaded_row("L2", "Tess Bar", None, ("BAR TEAM",)),
            loaded_row("L3", "Gone Person", "gone@x.com", deleted="2026-01-01"),
        ]
        out = staff_directory.upsert_from_loaded(
            db_session, w.org.id, w.a, rows, now=now
        )
        assert (out.added, out.updated, out.deactivated, out.reactivated) == (
            2,
            0,
            0,
            0,
        )
        staff = _loaded_staff(db_session, w.org.id)
        assert set(staff) == {"L1", "L2"}
        sam = staff["L1"]
        assert sam.email == "sam@example.com"
        assert sam.phone == "021 000"
        assert sam.loaded_roles == ["KITCHEN", "Sous Chef"]
        assert sam.venue_ids == [w.a.id]
        assert sam.is_active and sam.last_synced_at == now

    def test_updates_details_and_maps_a_second_venue(self, db_session):
        w = _world(db_session)
        t1 = datetime.now(timezone.utc)
        staff_directory.upsert_from_loaded(
            db_session, w.org.id, w.a, [loaded_row("L1", "Sam Chef")], now=t1
        )
        # Same person listed at the second venue, now with a new role name.
        t2 = t1 + timedelta(hours=1)
        out = staff_directory.upsert_from_loaded(
            db_session,
            w.org.id,
            w.b,
            [loaded_row("L1", "Sam Chef", roles=("HEAD CHEF",))],
            now=t2,
        )
        assert (out.added, out.updated) == (0, 1)
        sam = _loaded_staff(db_session, w.org.id)["L1"]
        assert sam.venue_ids == [w.a.id, w.b.id]
        assert sam.loaded_roles == ["HEAD CHEF"]
        assert sam.last_synced_at == t2
        # Nothing changed: not counted as an update.
        again = staff_directory.upsert_from_loaded(
            db_session,
            w.org.id,
            w.b,
            [loaded_row("L1", "Sam Chef", roles=("HEAD CHEF",))],
            now=t2,
        )
        assert (again.added, again.updated) == (0, 0)

    def test_reads_roles_from_active_remuneration_too(self):
        row = {
            "id": "L9",
            "name": "Ana",
            "activeRemuneration": {
                "memberRoles": [{"roleName": "Barista"}],
                "venueAssignments": [
                    {
                        "defaultRoleName": "Supervisor",
                        "memberRoles": [{"roleName": "Barista"}],
                    }
                ],
            },
        }
        person = staff_directory._loaded_person(row)
        assert person["loaded_roles"] == ["Barista", "Supervisor"]
        assert (
            staff_directory._loaded_person(
                {**row, "activeRemuneration": {"isActive": False}}
            )
            is None
        )


# ---------------------------------------------------------------------------
# sync_org
# ---------------------------------------------------------------------------


class TestSyncOrg:
    def test_deactivates_the_unseen_revokes_and_reactivates_on_return(
        self, db_session, monkeypatch, access_calls
    ):
        w = _world(db_session)
        _connect(db_session, w.a)
        _connect(db_session, w.b)
        monkeypatch.setattr(
            "app.services.builtin_apps.is_on", lambda slug, org, db, cdb: True
        )
        payloads = {
            w.a.id: [
                loaded_row("L1", "Sam Chef", "sam@x.com"),
                loaded_row("L2", "Tess Bar"),
            ],
            w.b.id: [loaded_row("L1", "Sam Chef", "sam@x.com")],
        }
        calls = _fake_loaded(monkeypatch, payloads)
        t1 = datetime.now(timezone.utc)
        out = staff_directory.sync_org(db_session, db_session, w.org.id, now=t1)
        assert out.added == 2 and out.deactivated == 0 and out.errors == []
        assert {c[3] for c in calls} == {w.a.id, w.b.id}
        assert calls[0][2] == {
            "include_deleted": "false",
            "include_last_clocks": "false",
        }
        staff = _loaded_staff(db_session, w.org.id)
        assert sorted(staff["L1"].venue_ids) == sorted([w.a.id, w.b.id])
        assert staff["L2"].venue_ids == [w.a.id]

        # Mirrored into the HR apps' people records.
        people = {
            r.data["loadedhub_id"]: r
            for r in db_session.query(AppRecord)
            .filter(
                AppRecord.namespace == "hr_suite",
                AppRecord.collection == "people",
                AppRecord.organization_id == w.org.id,
            )
            .all()
        }
        assert set(people) == {"L1", "L2"}
        assert people["L1"].data["email"] == "sam@x.com"
        assert people["L1"].data["role"] == "team_member"
        assert people["L1"].data["loadedhub_role"] == "BAR TEAM"
        assert sorted(people["L1"].data["venues"]) == sorted([w.a.id, w.b.id])
        assert people["L1"].data["is_active"] is True

        # Tess leaves Loaded; Sam drops Mr Murdochs.
        payloads[w.a.id] = [loaded_row("L1", "Sam Chef", "sam@x.com")]
        payloads[w.b.id] = []
        t2 = t1 + timedelta(hours=1)
        out = staff_directory.sync_org(db_session, db_session, w.org.id, now=t2)
        assert out.deactivated == 1
        tess = staff["L2"]
        assert tess.is_active is False and tess.removed_at == t2
        assert access_calls["revoke"] == [tess.id]
        assert staff["L1"].venue_ids == [w.a.id]
        db_session.refresh(people["L2"])
        assert people["L2"].data["is_active"] is False

        # Tess is back, with a Norm account already linked: reactivated and
        # her access restored.
        user = _make_user(db_session, full_name="Tess")
        tess.user_id = user.id
        payloads[w.a.id].append(loaded_row("L2", "Tess Bar"))
        t3 = t2 + timedelta(hours=1)
        out = staff_directory.sync_org(db_session, db_session, w.org.id, now=t3)
        assert out.reactivated == 1 and out.deactivated == 0
        assert tess.is_active is True and tess.removed_at is None
        assert access_calls["ensure"] == [(user.id, tess.id)]
        db_session.refresh(people["L2"])
        assert people["L2"].data["is_active"] is True
        assert people["L2"].data["norm_user_id"] == user.id

    def test_a_manager_switching_someone_off_holds_through_the_sync(
        self, db_session, monkeypatch, access_calls
    ):
        """Remove / is_active=false on a Loaded row is the manager's call;
        the next hourly copy must not switch them (and their access) back on
        while Loaded still lists them. Switching them on again lifts it."""
        w = _world(db_session)
        _connect(db_session, w.a)
        monkeypatch.setattr(
            "app.services.builtin_apps.is_on", lambda slug, org, db, cdb: False
        )
        payloads = {w.a.id: [loaded_row("L1", "Sam Chef", "sam@x.com")]}
        _fake_loaded(monkeypatch, payloads)
        t1 = datetime.now(timezone.utc)
        staff_directory.sync_org(db_session, db_session, w.org.id, now=t1)
        sam = _loaded_staff(db_session, w.org.id)["L1"]
        sam.user_id = _make_user(db_session, full_name="Sam").id

        staff_directory.update(db_session, sam, is_active=False)
        assert sam.is_active is False and sam.held_at is not None
        assert access_calls["revoke"] == [sam.id]
        out = staff_directory.sync_org(
            db_session, db_session, w.org.id, now=t1 + timedelta(hours=1)
        )
        assert out.reactivated == 0 and out.deactivated == 0
        assert sam.is_active is False and access_calls["ensure"] == []

        # Remove does the same for a Loaded row.
        staff_directory.update(db_session, sam, is_active=True)
        assert sam.is_active is True and sam.held_at is None
        assert access_calls["ensure"] == [(sam.user_id, sam.id)]
        staff_directory.remove(db_session, sam)
        assert sam.is_active is False and sam.held_at is not None
        staff_directory.sync_org(
            db_session, db_session, w.org.id, now=t1 + timedelta(hours=2)
        )
        assert sam.is_active is False and len(access_calls["ensure"]) == 1

        # A row the sync itself switched off comes back when Loaded lists it.
        payloads[w.a.id] = []
        staff_directory.sync_org(
            db_session, db_session, w.org.id, now=t1 + timedelta(hours=3)
        )
        sam.held_at = None  # as a manager switching them on would do
        sam.is_active = False
        payloads[w.a.id] = [loaded_row("L1", "Sam Chef", "sam@x.com")]
        out = staff_directory.sync_org(
            db_session, db_session, w.org.id, now=t1 + timedelta(hours=4)
        )
        assert out.reactivated == 1 and sam.is_active is True

    def test_a_failed_venue_call_never_reads_as_everyone_left(
        self, db_session, monkeypatch, access_calls
    ):
        w = _world(db_session)
        _connect(db_session, w.a)
        _connect(db_session, w.b)
        monkeypatch.setattr(
            "app.services.builtin_apps.is_on", lambda slug, org, db, cdb: False
        )
        payloads = {
            w.a.id: [loaded_row("L1", "Sam")],
            w.b.id: [loaded_row("L2", "Tess")],
        }
        _fake_loaded(monkeypatch, payloads)
        t1 = datetime.now(timezone.utc)
        staff_directory.sync_org(db_session, db_session, w.org.id, now=t1)
        payloads[w.b.id] = "token expired"
        out = staff_directory.sync_org(
            db_session, db_session, w.org.id, now=t1 + timedelta(hours=1)
        )
        assert out.errors == ["Mr Murdochs: token expired"]
        assert out.deactivated == 0
        staff = _loaded_staff(db_session, w.org.id)
        assert staff["L2"].is_active and staff["L2"].venue_ids == [w.b.id]
        assert access_calls["revoke"] == []
        # Training off: no people records were created.
        assert (
            db_session.query(AppRecord)
            .filter(
                AppRecord.namespace == "hr_suite", AppRecord.organization_id == w.org.id
            )
            .count()
            == 0
        )

    def test_matches_an_existing_people_record_by_email(
        self, db_session, monkeypatch, access_calls
    ):
        w = _world(db_session)
        _connect(db_session, w.a)
        monkeypatch.setattr(
            "app.services.builtin_apps.is_on", lambda slug, org, db, cdb: True
        )
        rec = AppRecord(
            namespace="hr_suite",
            organization_id=w.org.id,
            collection="people",
            data={
                "name": "S. Chef",
                "email": "sam@x.com",
                "role": "manager",
                "is_active": True,
            },
        )
        db_session.add(rec)
        db_session.flush()
        _fake_loaded(monkeypatch, {w.a.id: [loaded_row("L1", "Sam Chef", "SAM@x.com")]})
        staff_directory.sync_org(db_session, db_session, w.org.id)
        db_session.refresh(rec)
        assert rec.data["loadedhub_id"] == "L1"
        assert rec.data["name"] == "Sam Chef"
        assert rec.data["role"] == "manager"  # the HR apps' own field is kept
        assert (
            db_session.query(AppRecord)
            .filter(
                AppRecord.collection == "people", AppRecord.organization_id == w.org.id
            )
            .count()
            == 1
        )


# ---------------------------------------------------------------------------
# run_due
# ---------------------------------------------------------------------------


class TestRunDue:
    def test_claims_one_slot_per_org_per_hour(self, db_session, monkeypatch):
        w = _world(db_session)
        w.org.staff_access_enabled = True
        other = make_org(db_session, "No staff access")
        db_session.flush()
        ran = []

        def fake_sync(db, config_db, org_id, *, now=None):
            ran.append(org_id)
            return staff_directory.SyncSummary(added=3)

        monkeypatch.setattr(staff_directory, "sync_org", fake_sync)
        now = datetime(2026, 10, 23, 9, 40, tzinfo=timezone.utc)
        assert staff_sync.run_due(now, db=db_session, config_db=db_session) == 1
        assert ran == [w.org.id]
        later = now + timedelta(minutes=15)
        assert staff_sync.run_due(later, db=db_session, config_db=db_session) == 0
        assert ran == [w.org.id]
        runs = (
            db_session.query(StaffSyncRun)
            .filter(StaffSyncRun.organization_id == w.org.id)
            .all()
        )
        assert [r.slot for r in runs] == ["2026-10-23T09"]
        assert runs[0].summary["added"] == 3 and runs[0].finished_at is not None
        assert (
            db_session.query(StaffSyncRun)
            .filter(StaffSyncRun.organization_id == other.id)
            .count()
            == 0
        )
        # The next hour runs again.
        assert (
            staff_sync.run_due(
                now + timedelta(hours=1), db=db_session, config_db=db_session
            )
            == 1
        )

    def test_a_failure_is_kept_on_the_row_not_raised(self, db_session, monkeypatch):
        w = _world(db_session)
        w.org.staff_access_enabled = True
        db_session.flush()

        def boom(db, config_db, org_id, *, now=None):
            raise RuntimeError("Loaded is down")

        monkeypatch.setattr(staff_directory, "sync_org", boom)
        now = datetime(2026, 10, 23, 10, 5, tzinfo=timezone.utc)
        assert staff_sync.run_due(now, db=db_session, config_db=db_session) == 1
        run = (
            db_session.query(StaffSyncRun)
            .filter(StaffSyncRun.organization_id == w.org.id)
            .one()
        )
        assert run.summary == {"error": "Loaded is down"}


# ---------------------------------------------------------------------------
# By hand, spreadsheet, delete
# ---------------------------------------------------------------------------


class TestManualRows:
    def test_add_keeps_every_write_inside_the_org(self, db_session):
        w = _world(db_session)
        elsewhere = make_org(db_session, "Other")
        foreign_venue = _make_venue(
            db_session, name="Far", organization_id=elsewhere.id
        )
        foreign_role = Role(
            id=str(uuid.uuid4()),
            organization_id=elsewhere.id,
            name="x",
            display_name="X",
            permissions=[],
        )
        db_session.add(foreign_role)
        db_session.flush()
        with pytest.raises(DirectoryError):
            staff_directory.add_manual(
                db_session, w.org.id, name="Pat", venue_ids=[foreign_venue.id]
            )
        with pytest.raises(DirectoryError):
            staff_directory.add_manual(
                db_session, w.org.id, name="Pat", role_id=foreign_role.id
            )
        with pytest.raises(DirectoryError):
            staff_directory.add_manual(db_session, w.org.id, name="  ")
        staff = staff_directory.add_manual(
            db_session,
            w.org.id,
            name=" Pat  Cook ",
            email="PAT@x.com",
            venue_ids=[w.a.id, w.a.id],
            role_id=w.roles["team_member"].id,
        )
        assert staff.source == "manual" and staff.name == "Pat Cook"
        assert staff.email == "pat@x.com" and staff.venue_ids == [w.a.id]

    def test_update_and_delete_rules_for_manual_and_loaded_rows(
        self, db_session, access_calls
    ):
        w = _world(db_session)
        manual = staff_directory.add_manual(
            db_session, w.org.id, name="Pat", venue_ids=[w.a.id]
        )
        staff_directory.upsert_from_loaded(
            db_session, w.org.id, w.a, [loaded_row("L1", "Sam Chef")]
        )
        loaded = _loaded_staff(db_session, w.org.id)["L1"]

        staff_directory.update(db_session, manual, name="Pat Cook", venue_ids=[w.b.id])
        assert manual.name == "Pat Cook" and manual.venue_ids == [w.b.id]
        with pytest.raises(DirectoryError):
            staff_directory.update(db_session, loaded, name="Renamed")
        staff_directory.update(db_session, loaded, role_id=w.roles["manager"].id)
        assert loaded.role_id == w.roles["manager"].id

        # Switching someone off revokes; back on restores a linked account.
        user = _make_user(db_session)
        loaded.user_id = user.id
        staff_directory.update(db_session, loaded, is_active=False)
        assert not loaded.is_active and access_calls["revoke"] == [loaded.id]
        staff_directory.update(db_session, loaded, is_active=True)
        assert loaded.is_active and access_calls["ensure"] == [(user.id, loaded.id)]

        staff_directory.remove(db_session, manual)
        staff_directory.remove(db_session, loaded)
        assert (
            db_session.query(StaffMember).filter(StaffMember.id == manual.id).first()
            is None
        )
        assert loaded.is_active is False and loaded.removed_at is not None
        assert access_calls["revoke"] == [loaded.id, manual.id, loaded.id]

    def test_import_csv(self, db_session):
        w = _world(db_session)
        from app.services.role_access import ensure_venue_roles

        ensure_venue_roles(db_session, w.org.id)
        existing = staff_directory.add_manual(
            db_session, w.org.id, name="Old Name", email="pat@x.com", venue_ids=[w.a.id]
        )
        text = (
            "name,email,phone,venues,role\n"
            "Sam Chef,SAM@x.com,021 1,La Zeppa; Mr Murdochs,Kitchen\n"
            "Pat Cook,pat@x.com,,,team_member\n"
            "No Venue,nv@x.com,,Nowhere,\n"
            "Bad Role,br@x.com,,La Zeppa,Astronaut\n"
            ",blank@x.com,,,\n"
        )
        out = staff_directory.import_csv(db_session, w.org.id, text)
        assert (out.created, out.updated) == (1, 1)
        assert [(e["line"], e["message"]) for e in out.errors] == [
            (4, "There's no venue called 'Nowhere'."),
            (5, "There's no role called 'Astronaut'."),
            (6, "This row has no name."),
        ]
        sam = (
            db_session.query(StaffMember)
            .filter(
                StaffMember.organization_id == w.org.id,
                StaffMember.email == "sam@x.com",
            )
            .one()
        )
        kitchen = (
            db_session.query(Role)
            .filter(Role.organization_id == w.org.id, Role.name == "kitchen")
            .one()
        )
        assert sam.venue_ids == [w.a.id, w.b.id] and sam.role_id == kitchen.id
        assert sam.phone == "021 1" and sam.source == "manual"
        assert existing.name == "Pat Cook"
        assert existing.role_id == w.roles["team_member"].id
        assert existing.venue_ids == [w.a.id]  # no venues given: kept


# ---------------------------------------------------------------------------
# PINs
# ---------------------------------------------------------------------------


class TestPins:
    def test_set_verify_clear(self, db_session, monkeypatch):
        w = _world(db_session)
        sam = staff_directory.add_manual(
            db_session, w.org.id, name="Sam", venue_ids=[w.a.id]
        )
        tess = staff_directory.add_manual(
            db_session, w.org.id, name="Tess", venue_ids=[w.b.id]
        )
        # Sam draws 7; Tess draws 7 (taken), then 42.
        draws = iter([7, 7, 42])
        monkeypatch.setattr(staff_directory.secrets, "randbelow", lambda n: next(draws))
        pin = staff_directory.set_pin(db_session, sam)
        assert pin == "00007" and len(pin) == 5 and pin.isdigit()
        assert sam.pin_hash and sam.pin_hash != pin and sam.pin_set_at is not None
        assert staff_directory.set_pin(db_session, tess) == "00042"
        assert staff_directory.verify_pin(db_session, w.org.id, w.a.id, "00007") is sam
        # Another venue, another org, wrong digits, a leaver: none of them.
        assert staff_directory.verify_pin(db_session, w.org.id, w.b.id, "00007") is None
        assert (
            staff_directory.verify_pin(db_session, "other-org", w.a.id, "00007") is None
        )
        assert staff_directory.verify_pin(db_session, w.org.id, w.a.id, "00008") is None
        assert staff_directory.verify_pin(db_session, w.org.id, w.a.id, "abc") is None
        sam.is_active = False
        assert staff_directory.verify_pin(db_session, w.org.id, w.a.id, "00007") is None
        sam.is_active = True
        staff_directory.clear_pin(db_session, sam)
        assert sam.pin_hash is None
        assert staff_directory.verify_pin(db_session, w.org.id, w.a.id, "00007") is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestPeopleRoutes:
    def test_people_merges_members_and_staff_and_leaves_devices_out(
        self, db_session, staff_client
    ):
        w = _world(db_session)
        url = f"/api/organizations/{w.org.id}/people"
        # The owner is also on Loaded's list under the same email; a chef is
        # staff only; a venue device is nobody.
        staff_directory.upsert_from_loaded(
            db_session,
            w.org.id,
            w.a,
            [
                loaded_row("L1", "Owner Person", w.owner.email.upper(), ("GM",)),
                loaded_row("L2", "Sam Chef", "sam@x.com", ("KITCHEN",)),
            ],
        )
        device_user = _make_user(db_session, full_name="La Zeppa kitchen")
        device_user.is_device = True
        _make_membership(db_session, device_user, w.org)
        db_session.flush()

        # The directory carries everyone's email, phone and PIN status, so it
        # is a manager's page (org:members): a Team Member — every venue
        # staff role copies that permission set — is refused.
        assert staff_client.get(url, headers=auth_headers(w.member)).status_code == 403
        r = staff_client.get(url, headers=auth_headers(w.owner))
        assert r.status_code == 200, r.text
        body = r.json()
        by_name = {p["name"]: p for p in body["people"]}
        assert set(by_name) == {"Owner", "Member", "Sam Chef"}
        both = by_name["Owner"]
        assert (
            both["kind"] == "both"
            and both["staff_id"]
            and both["user_id"] == w.owner.id
        )
        assert both["role_name"] == "Owner" and both["loaded_roles"] == ["GM"]
        assert both["venue_ids"] == [w.a.id]
        sam = by_name["Sam Chef"]
        assert (
            sam["kind"] == "staff"
            and sam["signed_in"] is False
            and sam["has_pin"] is False
        )
        assert sam["source"] == "loadedhub"
        assert by_name["Member"]["kind"] == "member" and by_name["Member"]["signed_in"]
        assert body["loaded_role_names"] == ["GM", "KITCHEN"]
        assert {v["name"] for v in body["venues"]} == {"La Zeppa", "Mr Murdochs"}
        assert any(r_["name"] == "owner" for r_ in body["roles"])
        assert body["sync"] == {
            "enabled": False,
            "loaded_connected": False,
            "last_run_at": None,
            "last_summary": None,
        }
        # Not a member of this org: refused.
        stranger = _make_user(db_session)
        _make_membership(
            db_session, stranger, make_org(db_session, "Elsewhere")
        ).role_id = w.roles["owner"].id
        db_session.flush()
        assert staff_client.get(url, headers=auth_headers(stranger)).status_code == 403

    def test_the_permission_is_checked_in_the_organisation_in_the_path(
        self, db_session, staff_client
    ):
        """An Owner of A who is a Team Member of B must not act in B by
        sending X-Organization: A. require_permission reads the active
        organisation's role; these routes check the path's."""
        w = _world(db_session)
        org_a = make_org(db_session, "Elsewhere")
        _make_membership(db_session, w.member, org_a).role_id = w.roles["owner"].id
        db_session.flush()
        headers = {**auth_headers(w.member), "X-Organization": org_a.id}
        base = f"/api/organizations/{w.org.id}"
        body = {"name": "Pat", "email": "pat@x.com", "venue_ids": [w.a.id]}
        assert (
            staff_client.post(f"{base}/staff", json=body, headers=headers).status_code
            == 403
        )
        assert (
            staff_client.put(
                f"{base}/staff-access", json={"enabled": True}, headers=headers
            ).status_code
            == 403
        )
        assert staff_client.get(f"{base}/people", headers=headers).status_code == 403
        device = {
            "venue_id": w.a.id,
            "name": "Tablet",
            "role_id": w.roles["team_member"].id,
        }
        assert (
            staff_client.post(
                f"{base}/devices", json=device, headers=headers
            ).status_code
            == 403
        )
        # Nothing was written to B.
        assert db_session.query(StaffMember).count() == 0
        # The same person acting in A, where they are the Owner, is fine.
        r = staff_client.post(
            f"/api/organizations/{org_a.id}/staff",
            json={"name": "Pat", "venue_ids": []},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    def test_staff_writes_need_org_members(
        self, db_session, staff_client, access_calls
    ):
        w = _world(db_session)
        base = f"/api/organizations/{w.org.id}/staff"
        body = {"name": "Pat", "email": "pat@x.com", "venue_ids": [w.a.id]}
        assert (
            staff_client.post(
                base, json=body, headers=auth_headers(w.member)
            ).status_code
            == 403
        )
        r = staff_client.post(base, json=body, headers=auth_headers(w.owner))
        assert r.status_code == 200, r.text
        staff_id = r.json()["id"]
        assert r.json()["source"] == "manual"
        # A venue outside the org is a 400, not a silent write.
        other_venue = _make_venue(
            db_session, name="Far", organization_id=make_org(db_session, "O").id
        )
        r = staff_client.put(
            f"{base}/{staff_id}",
            json={"venue_ids": [other_venue.id]},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 400
        r = staff_client.put(
            f"{base}/{staff_id}",
            json={"name": "Pat Cook", "role_id": w.roles["team_member"].id},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200 and r.json()["name"] == "Pat Cook"
        # PIN: returned once, then only has_pin.
        r = staff_client.post(f"{base}/{staff_id}/pin", headers=auth_headers(w.owner))
        assert r.status_code == 200 and len(r.json()["pin"]) == 5
        assert (
            staff_client.post(
                f"{base}/{staff_id}/pin", headers=auth_headers(w.member)
            ).status_code
            == 403
        )
        assert (
            staff_client.delete(
                f"{base}/{staff_id}/pin", headers=auth_headers(w.owner)
            ).status_code
            == 200
        )
        # Import.
        r = staff_client.post(
            f"{base}/import",
            json={"csv": "name,email,phone,venues,role\nTess,tess@x.com,,La Zeppa,\n"},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200 and r.json() == {
            "created": 1,
            "updated": 0,
            "errors": [],
        }
        # Delete a manual row: gone, access revoked.
        r = staff_client.delete(f"{base}/{staff_id}", headers=auth_headers(w.owner))
        assert r.status_code == 200 and r.json()["deleted"] is True
        assert access_calls["revoke"] == [staff_id]
        assert (
            staff_client.delete(
                f"{base}/{staff_id}", headers=auth_headers(w.owner)
            ).status_code
            == 404
        )

    def test_loaded_rows_take_only_role_and_active_over_http(
        self, db_session, staff_client, access_calls
    ):
        w = _world(db_session)
        staff_directory.upsert_from_loaded(
            db_session, w.org.id, w.a, [loaded_row("L1", "Sam")]
        )
        sam = _loaded_staff(db_session, w.org.id)["L1"]
        url = f"/api/organizations/{w.org.id}/staff/{sam.id}"
        r = staff_client.put(
            url, json={"name": "Renamed"}, headers=auth_headers(w.owner)
        )
        assert r.status_code == 400
        r = staff_client.put(
            url,
            json={"role_id": w.roles["manager"].id, "is_active": False},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200, r.text
        assert (
            r.json()["role_id"] == w.roles["manager"].id
            and r.json()["is_active"] is False
        )
        r = staff_client.delete(url, headers=auth_headers(w.owner))
        assert r.status_code == 200 and r.json()["deleted"] is False
        assert (
            db_session.query(StaffMember).filter(StaffMember.id == sam.id).first()
            is not None
        )

    def test_staff_access_toggle_and_sync_now(
        self, db_session, staff_client, monkeypatch, access_calls
    ):
        w = _world(db_session)
        _connect(db_session, w.a)
        monkeypatch.setattr("app.services.builtin_apps.is_on", lambda *a: False)
        _fake_loaded(monkeypatch, {w.a.id: [loaded_row("L1", "Sam")]})
        toggle = f"/api/organizations/{w.org.id}/staff-access"
        assert (
            staff_client.put(
                toggle, json={"enabled": True}, headers=auth_headers(w.member)
            ).status_code
            == 403
        )
        r = staff_client.put(
            toggle, json={"enabled": True}, headers=auth_headers(w.owner)
        )
        assert r.status_code == 200, r.text
        assert (
            r.json()["sync"]["enabled"] is True
            and r.json()["sync"]["loaded_connected"] is True
        )
        names = {
            role.name
            for role in db_session.query(Role)
            .filter(Role.organization_id == w.org.id)
            .all()
        }
        assert {"kitchen", "front_of_house", "head_chef", "venue_manager"} <= names
        r = staff_client.post(
            f"/api/organizations/{w.org.id}/staff/sync", headers=auth_headers(w.owner)
        )
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["added"] == 1
        assert (
            staff_client.post(
                f"/api/organizations/{w.org.id}/staff/sync",
                headers=auth_headers(w.member),
            ).status_code
            == 403
        )

    def test_me_pin_finds_a_roster_row_by_email_and_links_it(
        self, db_session, staff_client
    ):
        """A manager on Loaded's list who signed in with a password: /auth/me
        reports the unclaimed row with their email, so /api/me/pin must find
        the same row (it answered 404 before) and link it to them."""
        w = _world(db_session)
        staff_directory.upsert_from_loaded(
            db_session,
            w.org.id,
            w.a,
            [loaded_row("L1", "Owner Person", w.owner.email.upper(), ("GM",))],
        )
        staff = db_session.query(StaffMember).one()
        assert staff.user_id is None
        r = staff_client.post("/api/me/pin", headers=auth_headers(w.owner))
        assert r.status_code == 200 and len(r.json()["pin"]) == 5
        db_session.refresh(staff)
        assert staff.user_id == w.owner.id

    def test_me_pin_needs_a_staff_record(self, db_session, staff_client):
        w = _world(db_session)
        assert (
            staff_client.post("/api/me/pin", headers=auth_headers(w.member)).status_code
            == 404
        )
        staff = staff_directory.add_manual(
            db_session, w.org.id, name="Member", venue_ids=[w.a.id]
        )
        staff.user_id = w.member.id
        db_session.flush()
        r = staff_client.post("/api/me/pin", headers=auth_headers(w.member))
        assert r.status_code == 200 and len(r.json()["pin"]) == 5
        assert (
            staff_directory.verify_pin(db_session, w.org.id, w.a.id, r.json()["pin"])
            is staff
        )
        assert (
            staff_client.delete(
                "/api/me/pin", headers=auth_headers(w.member)
            ).status_code
            == 200
        )
        assert staff.pin_hash is None


class TestToday:
    def test_nothing_due_is_an_empty_list(self, db_session, staff_client, monkeypatch):
        w = _world(db_session)
        headers = auth_headers(w.member)
        monkeypatch.setattr("app.services.builtin_apps.is_on", lambda *a: False)
        assert staff_client.get("/api/me/today", headers=headers).json() == {
            "items": []
        }
        # Training on, but no people record for this person.
        monkeypatch.setattr("app.services.builtin_apps.is_on", lambda *a: True)
        assert staff_client.get("/api/me/today", headers=headers).json() == {
            "items": []
        }
        # A people record with nothing assigned: still nothing due.
        db_session.add(
            AppRecord(
                namespace="hr_suite",
                organization_id=w.org.id,
                collection="people",
                data={
                    "name": "Member",
                    "role": "team_member",
                    "is_active": True,
                    "norm_user_id": w.member.id,
                },
            )
        )
        db_session.flush()
        r = staff_client.get("/api/me/today", headers=headers)
        assert r.status_code == 200 and r.json() == {"items": []}
        # An assignment still in progress is due: Training's own member view
        # answers it.
        person = (
            db_session.query(AppRecord).filter(AppRecord.collection == "people").one()
        )
        program = AppRecord(
            namespace="hr_suite",
            organization_id=w.org.id,
            collection="programs",
            data={"name": "Food safety", "is_active": True},
        )
        db_session.add(program)
        db_session.flush()
        db_session.add(
            AppRecord(
                namespace="hr_suite",
                organization_id=w.org.id,
                collection="assignments",
                data={
                    "person_id": person.id,
                    "program_id": program.id,
                    "status": "in_progress",
                },
            )
        )
        db_session.flush()
        items = staff_client.get("/api/me/today", headers=headers).json()["items"]
        assert [(i["kind"], i["title"], i["app_slug"]) for i in items] == [
            ("training", "Food safety", "training")
        ]
        assert items[0]["detail"] == "Not started"


class TestDevices:
    def test_a_tablet_never_acts_as_owner_or_above_its_maker(
        self, db_session, staff_client
    ):
        """org:manage sets tablets up; a Manager holds that but not
        billing:manage or org:roles, so a tablet 'acting under' Owner would be
        a year-long token with more than the manager has. Refused for
        everyone (Owner), and for any role beyond the caller's own."""
        w = _world(db_session)
        manager = _make_user(db_session, full_name="Manager")
        _make_membership(db_session, manager, w.org).role_id = w.roles["manager"].id
        db_session.flush()
        base = f"/api/organizations/{w.org.id}/devices"
        body = {"venue_id": w.a.id, "name": "Tablet", "role_id": w.roles["owner"].id}
        for who in (w.owner, manager):
            r = staff_client.post(base, json=body, headers=auth_headers(who))
            assert r.status_code == 400 and "Owner" in r.json()["detail"]
        # A custom role holding billing:manage, which a manager does not.
        payroll = Role(
            id=str(uuid.uuid4()),
            organization_id=w.org.id,
            name="money",
            display_name="Money",
            is_system=False,
            permissions=["org:read", "billing:manage"],
        )
        db_session.add(payroll)
        db_session.flush()
        r = staff_client.post(
            base, json={**body, "role_id": payroll.id}, headers=auth_headers(manager)
        )
        assert r.status_code == 400 and "billing:manage" in r.json()["detail"]
        # The manager's own role, and anything under it, is fine.
        r = staff_client.post(
            base,
            json={**body, "role_id": w.roles["manager"].id},
            headers=auth_headers(manager),
        )
        assert r.status_code == 200, r.text
        device_id = r.json()["device"]["id"]
        r = staff_client.put(
            f"{base}/{device_id}",
            json={"role_id": payroll.id},
            headers=auth_headers(manager),
        )
        assert r.status_code == 400
        assert db_session.query(VenueDevice).count() == 1

    def test_create_list_update_revoke(self, db_session, staff_client):
        w = _world(db_session)
        base = f"/api/organizations/{w.org.id}/devices"
        body = {
            "venue_id": w.a.id,
            "name": "La Zeppa kitchen",
            "role_id": w.roles["team_member"].id,
            "pin_required": True,
        }
        assert (
            staff_client.post(
                base, json=body, headers=auth_headers(w.member)
            ).status_code
            == 403
        )
        other_venue = _make_venue(
            db_session, name="Far", organization_id=make_org(db_session, "O").id
        )
        assert (
            staff_client.post(
                base,
                json={**body, "venue_id": other_venue.id},
                headers=auth_headers(w.owner),
            ).status_code
            == 400
        )
        r = staff_client.post(base, json=body, headers=auth_headers(w.owner))
        assert r.status_code == 200, r.text
        out = r.json()
        token = out["token"]
        device = out["device"]
        assert (
            device["name"] == "La Zeppa kitchen" and device["venue_name"] == "La Zeppa"
        )
        assert (
            device["pin_required"] is True
            and device["role_id"] == w.roles["team_member"].id
        )
        claims = decode_access_token(token)
        assert claims["type"] == "device" and claims["device"] == device["id"]
        assert claims["sub"] == device["user_id"]
        # A year, not the usual hours.
        assert claims["exp"] - datetime.now(timezone.utc).timestamp() > 300 * 86400

        user = db_session.query(User).filter(User.id == device["user_id"]).one()
        assert user.is_device and not user.password_set and user.is_active
        assert user.email == f"device-{device['id']}@devices.norm.local"
        membership = (
            db_session.query(OrganizationMembership)
            .filter(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == w.org.id,
            )
            .one()
        )
        assert membership.role_id == w.roles["team_member"].id
        assert [
            a.venue_id
            for a in db_session.query(UserVenueAccess).filter(
                UserVenueAccess.user_id == user.id
            )
        ] == [w.a.id]
        # The token is never listed again.
        r = staff_client.get(base, headers=auth_headers(w.owner))
        assert r.status_code == 200
        assert [d["id"] for d in r.json()["devices"]] == [device["id"]]
        assert "token" not in r.json()["devices"][0]
        # The tablet can use it.
        r = staff_client.get(
            "/api/tablet/context", headers={"Authorization": f"Bearer {token}"}
        )
        assert (
            r.status_code == 200 and r.json()["device"]["venue"]["name"] == "La Zeppa"
        )
        # Update: no PIN, a new role.
        r = staff_client.put(
            f"{base}/{device['id']}",
            json={
                "pin_required": False,
                "role_id": w.roles["manager"].id,
                "name": "Kitchen pass",
            },
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200, r.text
        assert (
            r.json()["pin_required"] is False
            and r.json()["role_id"] == w.roles["manager"].id
        )
        assert r.json()["name"] == "Kitchen pass"
        # Revoke: gone from the list, user switched off, token dead.
        r = staff_client.delete(f"{base}/{device['id']}", headers=auth_headers(w.owner))
        assert r.status_code == 200
        row = db_session.query(VenueDevice).filter(VenueDevice.id == device["id"]).one()
        assert row.revoked_at is not None and row.user.is_active is False
        assert (
            staff_client.get(base, headers=auth_headers(w.owner)).json()["devices"]
            == []
        )
        r = staff_client.get(
            "/api/tablet/context", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 401
