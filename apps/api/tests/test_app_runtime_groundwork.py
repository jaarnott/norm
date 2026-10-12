"""Groundwork for built-in apps (Oct 2026): who's running, scheduled runs,
and email — each a door the sandbox can use but not widen.

Pins:
* the viewer (`params["_viewer"]`) is set by the platform; a caller can't
  supply it, and `_email_mode` accepts only its two safe values;
* the system actor (scheduled runs) works for built-ins only;
* `email` is off until an org manager turns it on; dry-run and test modes;
  suppressed outside production; never twice for one key; recipients must be
  the org's (unless the app declares external recipients); the sandbox gets
  plain functions, never the database session; user-built apps get no email;
* schedules run once per org per day, only where the App is on, each bound to
  its own org.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.db.config_models import MarketplaceApp
from app.db.models import (
    App,
    AppRecord,
    AppScheduleRun,
    AppVersion,
    EmailLog,
    OrgAppEntitlement,
    Role,
)
from app.services import app_runtime as AR
from app.services import builtin_apps as BA
from app.services.app_email import set_email_enabled
from app.services.app_schedules import _schedules, due_slot, run_app_schedules
from tests.conftest import _make_membership, _make_organization, _make_user

LOGIC = """
def run(params, call_api, log):
    op = params.get("op")
    if op == "who":
        return {"viewer": params.get("_viewer"), "mode": params.get("_email_mode"),
                "leak": params.get("_secret")}
    if op == "send":
        return email.send(params.get("to"), "Hello", "<p>Hi there</p>",
                          key=params.get("key"))
    if op == "toggle":
        return email.set_enabled(params.get("on"))
    if op == "nightly":
        store.put("people", {"name": "ran " + params["_schedule"]["slot"]})
        return {"ok": True}
    return {}
"""

FAKE = BA.BuiltinApp(
    slug="fakeapp",
    name="Fake App",
    icon=None,
    description=None,
    purpose=None,
    agent="hr",
    spec={
        "scopes": [],
        "storage": {"namespace": "fake_suite", "collections": ["people"]},
        "email": {"external": False},
        "schedules": [{"op": "nightly", "at": "19:00", "tz": "UTC"}],
    },
    ui_source="<div/>",
    logic_source=LOGIC,
    build="deadbeef",
)


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    real = dict(BA.builtin_apps())
    monkeypatch.setattr(BA, "builtin_apps", lambda: {**real, "fakeapp": FAKE})


def _member(db, org, perms=None, email=None):
    user = _make_user(db, email=email or f"{uuid.uuid4().hex[:8]}@t.local")
    mem = _make_membership(db, user, org)
    if perms is not None:
        role = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name=f"r-{uuid.uuid4().hex[:6]}",
            display_name="Manager",
            permissions=perms,
        )
        db.add(role)
        db.flush()
        mem.role_id = role.id
        db.flush()
    return user


def _run(db, user, org, **params):
    app, version = BA.bind(FAKE, org.id)
    out = AR.run_logic(
        db, db, app=app, version=version, user=user, venue_id=None, params=params
    )
    assert out["success"] is True, out.get("error")
    return out["data"]


def _person(db, org, email):
    row = AppRecord(
        id=str(uuid.uuid4()),
        namespace="fake_suite",
        organization_id=org.id,
        collection="people",
        data={"name": "P", "email": email},
    )
    db.add(row)
    db.flush()
    return row.id


class TestTheViewer:
    def test_set_by_the_platform_and_not_spoofable(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org, perms=["hr:read"])
        out = _run(
            db_session,
            user,
            org,
            op="who",
            _viewer={"user_id": "someone-else"},
            _secret="x",
            _email_mode="live",
        )
        assert out["viewer"]["user_id"] == user.id
        assert (
            out["viewer"]["role"] == "Manager"
            and "hr:read" in out["viewer"]["permissions"]
        )
        assert out["leak"] is None
        assert out["mode"] == "live"
        assert (
            _run(db_session, user, org, op="who", _email_mode="test")["mode"] == "test"
        )

    def test_the_system_actor_only_runs_builtins(self, db_session):
        org = _make_organization(db_session)
        app, _ = BA.bind(FAKE, org.id)
        assert AR.resolve_access(db_session, app, AR.SYSTEM).can_run
        custom = App(
            organization_id=org.id, slug="mine", name="Mine", visibility="private"
        )
        db_session.add(custom)
        db_session.flush()
        assert not AR.resolve_access(db_session, custom, AR.SYSTEM).can_run


class TestEmail:
    def test_off_until_turned_on(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        out = _run(db_session, user, org, op="send", to=[user.email])
        assert out["status"] == "disabled"

    def test_dry_run_renders_and_logs_nothing(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        out = _run(
            db_session, user, org, op="send", to=[user.email], _email_mode="dry_run"
        )
        assert out["status"] == "dry_run" and "Hi there" in out["html"]
        assert db_session.query(EmailLog).filter_by(app_slug="fakeapp").count() == 0

    def test_test_mode_sends_only_to_the_person_testing(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        pid = _person(db_session, org, "colleague@t.local")
        with patch(
            "app.services.app_email.AppEmail._deliver",
            return_value=("sent", None, "p1"),
        ):
            out = _run(
                db_session,
                user,
                org,
                op="send",
                to=[f"person:{pid}"],
                _email_mode="test",
            )
        assert out["status"] == "sent" and out["to"] == [user.email]
        log = db_session.query(EmailLog).filter_by(id=out["id"]).one()
        assert log.is_test and log.subject.startswith("[TEST]")

    def test_live_sends_are_suppressed_outside_production(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        set_email_enabled(db_session, org.id, "fakeapp", True, None)
        with patch("app.services.app_email.AppEmail._deliver") as deliver:
            out = _run(db_session, user, org, op="send", to=[user.email])
        assert out["status"] == "suppressed" and not deliver.called

    def test_a_key_is_never_sent_twice(self, db_session, monkeypatch):
        monkeypatch.setenv("APP_EMAIL_ALLOW_NONPROD", "1")
        org = _make_organization(db_session)
        user = _member(db_session, org)
        set_email_enabled(db_session, org.id, "fakeapp", True, None)
        with patch(
            "app.services.app_email.AppEmail._deliver",
            return_value=("sent", None, "p1"),
        ) as deliver:
            first = _run(db_session, user, org, op="send", to=[user.email], key="k1")
            second = _run(db_session, user, org, op="send", to=[user.email], key="k1")
        assert first["status"] == "sent" and second["status"] == "skipped"
        assert deliver.call_count == 1

    def test_recipients_must_belong_to_the_org(self, db_session, monkeypatch):
        monkeypatch.setenv("APP_EMAIL_ALLOW_NONPROD", "1")
        org = _make_organization(db_session)
        user = _member(db_session, org)
        set_email_enabled(db_session, org.id, "fakeapp", True, None)
        pid = _person(db_session, org, "staff@t.local")
        with patch(
            "app.services.app_email.AppEmail._deliver",
            return_value=("sent", None, "p1"),
        ):
            out = _run(
                db_session,
                user,
                org,
                op="send",
                to=["stranger@elsewhere.com", f"person:{pid}", "staff@t.local"],
            )
        assert out["to"] == ["staff@t.local"]
        assert out["refused"] == ["stranger@elsewhere.com"]

    def test_the_sandbox_gets_functions_not_the_session(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        app, version = BA.bind(FAKE, org.id)
        door = AR._email_door(db_session, app, version, user, "live")
        assert not hasattr(door, "db") and callable(door.send)

    def test_user_built_apps_get_no_email(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        app = App(
            organization_id=org.id,
            slug="mine",
            name="Mine",
            visibility="private",
            created_by=user.id,
        )
        db_session.add(app)
        db_session.flush()
        ver = AppVersion(
            app_id=app.id,
            version=1,
            created_by=user.id,
            ui_source="<div/>",
            spec={"email": {}},
            logic_source="def run(params, call_api, log):\n    return {'has': email is not None}\n",
        )
        db_session.add(ver)
        db_session.flush()
        out = AR.run_logic(
            db_session,
            db_session,
            app=app,
            version=ver,
            user=user,
            venue_id=None,
            params={},
        )
        assert out["success"] is False  # `email` is simply not defined for it

    def test_only_an_org_manager_can_turn_it_on(self, db_session):
        org = _make_organization(db_session)
        staff = _member(db_session, org, perms=["hr:read"])
        boss = _member(db_session, org, perms=["org:manage"])
        assert _run(db_session, staff, org, op="toggle", on=True)["ok"] is False
        assert _run(db_session, boss, org, op="toggle", on=True)["ok"] is True


class TestSchedules:
    def _catalog(self, db, org, on=True):
        db.add_all(
            [
                MarketplaceApp(
                    slug="hr-agent",
                    name="HR",
                    description="",
                    tier="agent",
                    bundled=True,
                    composition={"owns_agents": ["hr"]},
                ),
                MarketplaceApp(
                    slug="fake",
                    name="Fake",
                    description="",
                    tier="app",
                    bundled=True,
                    composition={"member": "hr", "app_slug": "fakeapp"},
                ),
            ]
        )
        if not on:
            db.add(
                OrgAppEntitlement(
                    organization_id=org.id, app_slug="fake", enabled=False
                )
            )
        db.flush()

    def test_due_after_its_time_in_its_zone(self):
        sched = _schedules(FAKE.spec)[0]
        assert (
            due_slot(sched, datetime(2026, 10, 11, 18, 59, tzinfo=timezone.utc)) is None
        )
        assert (
            due_slot(sched, datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc))
            == "2026-10-11"
        )

    def test_once_per_org_per_day_in_its_own_org(self, db_session):
        a, b = _make_organization(db_session), _make_organization(db_session)
        self._catalog(db_session, a)
        now = datetime(2026, 10, 11, 19, 5, tzinfo=timezone.utc)
        with (
            patch.object(db_session, "commit", db_session.flush),
            patch.object(db_session, "close", lambda: None),
        ):
            first = run_app_schedules(now, db=db_session, config_db=db_session)
            second = run_app_schedules(now, db=db_session, config_db=db_session)
        mine = [
            r
            for r in first["ran"]
            if r["org"] in (a.id, b.id) and r["app"] == "fakeapp"
        ]
        assert {r["org"] for r in mine} == {a.id, b.id} and all(r["ok"] for r in mine)
        assert not [
            r
            for r in second["ran"]
            if r["org"] in (a.id, b.id) and r["app"] == "fakeapp"
        ]
        for org in (a, b):
            rows = (
                db_session.query(AppRecord)
                .filter_by(namespace="fake_suite", organization_id=org.id)
                .all()
            )
            assert [r.data["name"] for r in rows] == ["ran 2026-10-11"]

    def test_not_for_an_org_with_the_app_off(self, db_session):
        org = _make_organization(db_session)
        self._catalog(db_session, org, on=False)
        now = datetime(2026, 10, 11, 19, 5, tzinfo=timezone.utc)
        with (
            patch.object(db_session, "commit", db_session.flush),
            patch.object(db_session, "close", lambda: None),
        ):
            out = run_app_schedules(now, db=db_session, config_db=db_session)
        assert not [
            r for r in out["ran"] if r["org"] == org.id and r["app"] == "fakeapp"
        ]
        assert (
            not db_session.query(AppScheduleRun)
            .filter_by(organization_id=org.id, app_slug="fakeapp")
            .count()
        )


class TestLogicOnlyCollections:
    """A collection a spec lists in storage.logic_only is readable only by the
    app's own logic — so its field rules (pay, private review answers) can't be
    sidestepped through the direct records endpoints or norm.storage."""

    SPEC = {
        "scopes": [],
        "storage": {
            "namespace": "fake_suite",
            "collections": ["people", "secrets"],
            "logic_only": ["secrets"],
        },
    }

    def _app(self, org):
        b = BA.BuiltinApp(
            slug="fakeapp",
            name="Fake",
            icon=None,
            description=None,
            purpose=None,
            agent="hr",
            spec=self.SPEC,
            ui_source="<div/>",
            logic_source=(
                "def run(params, call_api, log):\n"
                "    return {'n': len(store.list('secrets'))}\n"
            ),
            build="x",
        )
        return BA.bind(b, org.id)

    def test_direct_reads_are_refused(self, db_session):
        from fastapi import HTTPException

        org = _make_organization(db_session)
        user = _member(db_session, org)
        app, version = self._app(org)
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session, app=app, version=version, user=user, collection="secrets"
            )
        assert e.value.status_code == 403
        # an ordinary collection is unaffected
        AR.store_list(
            db_session, app=app, version=version, user=user, collection="people"
        )

    def test_the_apps_own_logic_can_read_them(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        app, version = self._app(org)
        out = AR.run_logic(
            db_session,
            db_session,
            app=app,
            version=version,
            user=user,
            venue_id=None,
            params={},
        )
        assert out["success"] is True and out["data"] == {"n": 0}


class TestEmailHardening:
    def test_test_sends_have_their_own_small_cap(self, db_session, monkeypatch):
        import app.services.app_email as AE

        monkeypatch.setattr(AE, "PER_USER_TEST_DAY_LIMIT", 2)
        org = _make_organization(db_session)
        user = _member(db_session, org)
        with patch(
            "app.services.app_email.AppEmail._deliver", return_value=("sent", None, "p")
        ):
            outs = [
                _run(
                    db_session,
                    user,
                    org,
                    op="send",
                    to=[user.email],
                    _email_mode="test",
                )
                for _ in range(3)
            ]
        assert [o["status"] for o in outs] == ["sent", "sent", "limited"]

    def test_a_crashed_claim_stops_blocking_after_a_while(
        self, db_session, monkeypatch
    ):
        from datetime import timedelta

        monkeypatch.setenv("APP_EMAIL_ALLOW_NONPROD", "1")
        org = _make_organization(db_session)
        user = _member(db_session, org)
        set_email_enabled(db_session, org.id, "fakeapp", True, None)
        stuck = EmailLog(
            organization_id=org.id,
            sender_type="system",
            sender_email="x@y.z",
            to_addresses=[user.email],
            subject="s",
            status="sending",
            app_slug="fakeapp",
            idempotency_key="k9",
            is_test=False,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db_session.add(stuck)
        db_session.flush()
        with patch(
            "app.services.app_email.AppEmail._deliver", return_value=("sent", None, "p")
        ):
            out = _run(db_session, user, org, op="send", to=[user.email], key="k9")
        assert out["status"] == "sent"
        db_session.refresh(stuck)
        assert stuck.status == "failed"
