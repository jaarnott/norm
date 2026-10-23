"""The executor's rule for a call naming NO venue (Finance Phase 0, plan §5.2
item 5, §7.1). Until Oct 2026 it ran on the organisation's first login —
another venue's data, and for a write, another venue's change (a venue-less
set_budgets would have landed on Mr Murdochs' series). Now a consolidator
that declares writes never falls back, and a read uses the organisation's
login only when it is the ONLY one for that connector.

Real get_budgets source at the end: refused venue-less at a two-venue
organisation, answered at a one-venue one."""

import uuid
from types import SimpleNamespace

import pytest

from app.connectors.function_executor import execute_function
from app.db.config_models import ConnectionSpec
from app.db.models import Connection
from app.services import caller_scope
from tests import consolidator_harness as harness
from tests.conftest import _make_membership
from tests.org_helpers import make_org, make_user, make_venue

READ = (
    "def run(params, call_api, log):\n    return call_api('rulehub', 'read', params)\n"
)
WRITER = (
    "def run(params, call_api, log):\n"
    "    return call_api('rulehub', 'read', params)\n"  # a read, inside a writer
)


@pytest.fixture()
def world(db_session, monkeypatch):
    """Cook Brothers with two rulehub logins; Solo Co with one. The spec lives
    in this test's session; HTTP is faked and records whose login was used."""
    import app.connectors.spec_executor as spec_mod
    import app.db.engine as engine

    ours, solo = make_org(db_session, "Cook Brothers"), make_org(db_session, "Solo Co")
    w = SimpleNamespace(
        ours=ours,
        solo=solo,
        goose=make_venue(db_session, "The Glass Goose", ours),
        murdochs=make_venue(db_session, "Mr Murdochs", ours),
        only=make_venue(db_session, "Only Bar", solo),
        sent=[],
    )
    _make_membership(db_session, make_user(db_session), ours)
    db_session.add(
        ConnectionSpec(
            id=str(uuid.uuid4()),
            connector_name="rulehub",
            display_name="R",
            execution_mode="template",
            auth_type="none",
            auth_config={},
            base_url_template="https://rule.example",
            tools=[
                {"action": "read", "method": "GET", "path_template": "/r"},
                {"action": "write", "method": "PUT", "path_template": "/w"},
            ],
            enabled=True,
        )
    )
    for venue in (w.goose, w.murdochs, w.only):
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="rulehub",
                venue_id=venue.id,
                config={"k": venue.name},
                enabled="true",
            )
        )
    db_session.flush()

    class _Cfg:
        def query(self, *a):
            return db_session.query(*a)

        def expunge(self, obj):
            pass

        def close(self):
            pass

    monkeypatch.setattr(engine, "_ConfigSessionLocal", lambda: _Cfg())

    def fake_execute_spec(spec, tool_def, params, credentials, db, *a, **kw):
        w.sent.append((tool_def["action"], (credentials or {}).get("k")))
        return SimpleNamespace(
            success=True, response_payload={"ok": True}, error_message=None
        ), None

    monkeypatch.setattr(spec_mod, "execute_spec", fake_execute_spec)
    return w


def _call(db, org, code, params, **options):
    with caller_scope.use(caller_scope.Scope(org.id)):
        return execute_function(
            code, params, db, None, options={"max_api_calls": 3, **options}
        )["data"]


class TestVenueLessReads:
    def test_two_logins_refuse_rather_than_guess(self, db_session, world):
        out = _call(db_session, world.ours, READ, {})
        assert "needs a venue" in out["error"] and "more than one venue" in out["error"]
        assert world.sent == []

    def test_one_login_answers(self, db_session, world):
        assert _call(db_session, world.solo, READ, {}) == {"ok": True}
        assert world.sent == [("read", "Only Bar")]

    def test_a_named_venue_still_runs_on_its_own_login(self, db_session, world):
        assert _call(db_session, world.ours, READ, {"venue": "Mr Murdochs"}) == {
            "ok": True
        }
        assert world.sent == [("read", "Mr Murdochs")]


class TestWritersNeverFallBack:
    def test_even_a_read_inside_a_writing_consolidator_needs_a_venue(
        self, db_session, world
    ):
        """One login or two: a consolidator that can write never has a venue
        guessed for it — the read it does first would be the wrong venue's."""
        out = _call(db_session, world.solo, WRITER, {}, allowed_write_actions=["write"])
        assert "needs a venue" in out["error"] and "can write" in out["error"]
        assert world.sent == []

    def test_named_it_writes_where_told(self, db_session, world):
        out = _call(
            db_session,
            world.ours,
            "def run(p, call_api, log):\n    return call_api('rulehub', 'write', p)\n",
            {"venue": "The Glass Goose"},
            allowed_write_actions=["write"],
        )
        assert out == {"ok": True} and world.sent == [("write", "The Glass Goose")]


class TestGetBudgets:
    """The plan's own check: venue-less get_budgets at a multi-venue
    organisation errors; at a single-venue one it answers."""

    @pytest.fixture()
    def loaded(self, db_session, world):
        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="loadedhub",
                display_name="Loaded",
                execution_mode="template",
                auth_type="none",
                auth_config={},
                base_url_template="https://loaded.example",
                tools=[
                    {
                        "action": "get_budgets_raw",
                        "method": "GET",
                        "path_template": "/b",
                    },
                ],
                enabled=True,
            )
        )
        for venue in (world.goose, world.murdochs, world.only):
            db_session.add(
                Connection(
                    id=str(uuid.uuid4()),
                    connector_name="loadedhub",
                    venue_id=venue.id,
                    config={"k": venue.name},
                    enabled="true",
                )
            )
        db_session.flush()
        return harness.source("get_budgets")

    def test_refused_at_a_two_venue_organisation(self, db_session, world, loaded):
        out = _call(db_session, world.ours, loaded, {"from_date": "2026-10-19"})
        assert "needs a venue" in out["error"]
        assert world.sent == []

    def test_answers_at_a_one_venue_organisation(self, db_session, world, loaded):
        out = _call(db_session, world.solo, loaded, {"from_date": "2026-10-19"})
        assert not out.get("error"), out
        assert world.sent == [("get_budgets_raw", "Only Bar")]
