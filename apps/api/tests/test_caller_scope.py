"""Venue lookups stay inside the caller's organisation (Oct 2026).

Built-ins and the consolidator engine were never told who is calling, so every
lookup underneath ran across ALL organisations: norm.list_venues listed every
venue in the database (the venues='all' fan-outs), eight handlers took the
first venue whose name merely CONTAINED the text (invoice receiving and
split-order writes among them), a consolidator's venue name found its login
the same way, and a page route ran any endpoint — writes included — for any
signed-in user. One organisation in production today; each of these is a
leak the day a second one joins.

caller_scope carries the organisation in a context variable set where a call
comes in, so nothing a model sends can choose it. Two organisations here, with
similarly named venues, prove each door stays shut.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services import caller_scope
from tests.conftest import _make_membership, _make_thread
from tests.org_helpers import grant_venue, make_org, make_user, make_venue


@pytest.fixture()
def world(db_session):
    """Cook Brothers (ours) and Rival Co, each with a 'Glass Goose'."""
    ours, rival = (
        make_org(db_session, "Cook Brothers"),
        make_org(db_session, "Rival Co"),
    )
    w = SimpleNamespace(
        ours=ours,
        rival=rival,
        goose=make_venue(db_session, "The Glass Goose", ours),
        murdochs=make_venue(db_session, "Mr Murdochs", ours),
        rival_goose=make_venue(db_session, "Glass Goose Rival", rival),
        rival_zeppa=make_venue(db_session, "La Zeppa", rival),
        user=make_user(db_session),
    )
    _make_membership(db_session, w.user, ours)
    return w


def _in(scope_org):
    return caller_scope.use(caller_scope.Scope(scope_org.id))


class TestDerivingTheScope:
    def test_a_thread_runs_as_its_users_organisation(self, db_session, world):
        thread = _make_thread(db_session, world.user)
        assert caller_scope.for_thread(db_session, thread.id).org_id == world.ours.id

    def test_no_membership_falls_back_to_the_users_venues(self, db_session, world):
        u = make_user(db_session)
        grant_venue(db_session, u, world.rival_zeppa)
        assert caller_scope.for_user(db_session, u).org_id == world.rival.id

    def test_a_platform_admin_with_no_organisation_sees_everything(
        self, db_session, world
    ):
        scope = caller_scope.for_user(db_session, make_user(db_session, role="admin"))
        assert scope.unrestricted

    def test_a_venue_scopes_to_its_organisation(self, db_session, world):
        assert caller_scope.for_venue(db_session, world.rival_zeppa.id).org_id == (
            world.rival.id
        )

    def test_none_keeps_the_outer_scope(self, db_session, world):
        with _in(world.ours):
            with caller_scope.use(None):
                assert caller_scope.current().org_id == world.ours.id


class TestFindVenue:
    def test_a_whole_name_matches_case_and_the_aside(self, db_session, world):
        with _in(world.ours):
            for name in ("The Glass Goose", "the glass goose", "Glass Goose"):
                v, err = caller_scope.find_venue(db_session, name=name)
                assert v is world.goose, (name, err)
            v, _ = caller_scope.find_venue(db_session, name="Mr Murdoch's")
            assert v is world.murdochs

    def test_part_of_a_name_is_not_a_match(self, db_session, world):
        """ilike('%goose%').first() picked whichever came first."""
        with _in(world.ours):
            v, err = caller_scope.find_venue(db_session, name="Goose")
        assert v is None
        assert "Mr Murdochs, The Glass Goose" in err

    def test_another_organisations_venue_does_not_exist(self, db_session, world):
        with _in(world.ours):
            v, err = caller_scope.find_venue(db_session, name="La Zeppa")
            assert v is None and "Rival" not in err
            v, err = caller_scope.find_venue(db_session, venue_id=world.rival_zeppa.id)
            assert v is None

    def test_with_no_scope_a_name_never_picks_between_organisations(
        self, db_session, world
    ):
        make_venue(db_session, "Mr Murdochs", world.rival)
        v, err = caller_scope.find_venue(db_session, name="Mr Murdochs")
        assert v is None and "more than one venue" in err


class TestListVenues:
    def test_all_venues_means_the_callers_organisation(self, db_session, world):
        from app.agents.internal_tools import get_handler

        with _in(world.ours):
            out = get_handler("norm", "list_venues")({}, db_session, None)
        names = {v["name"] for v in out["data"]["venues"]}
        assert {"The Glass Goose", "Mr Murdochs"} <= names
        assert not names & {"La Zeppa", "Glass Goose Rival"}

    def test_an_unknown_caller_is_refused(self, db_session, world):
        from app.agents.internal_tools import get_handler

        out = get_handler("norm", "list_venues")({}, db_session, None)
        assert out["success"] is False and "which organisation" in out["error"]

    def test_the_agent_path_sets_the_scope_from_the_thread(
        self, db_session, world, monkeypatch
    ):
        """tool_loop._execute_tool_call runs a built-in as its thread's org."""
        import app.agents.internal_tools as it
        from app.agents.tool_loop import _execute_tool_call
        from app.db.config_models import ConnectionSpec
        from app.db.models import ToolCall

        seen = []
        monkeypatch.setattr(
            it,
            "get_handler",
            lambda c, a: (
                (lambda p, d, t: seen.append(caller_scope.current()) or {"data": {}})
                if (c, a) == ("scope_norm", "peek")
                else None
            ),
        )
        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="scope_norm",
                display_name="S",
                execution_mode="internal",
                auth_type="none",
                auth_config={},
                tools=[{"action": "peek"}],
                enabled=True,
            )
        )
        thread = _make_thread(db_session, world.user)
        tc = ToolCall(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            iteration=1,
            tool_name="scope_norm__peek",
            connector_name="scope_norm",
            action="peek",
            method="GET",
            status="pending",
            input_params={"_caller_org": world.rival.id},  # a model can't choose
        )
        db_session.add(tc)
        db_session.flush()
        _execute_tool_call(tc, db_session, config_db=db_session)
        assert seen and seen[0].org_id == world.ours.id
        assert caller_scope.current() is None


class TestVenuelessTiles:
    def test_an_all_venues_call_runs_as_the_signed_in_users_organisation(
        self, db_session, world, monkeypatch
    ):
        """Every HR dashboard tile reading app records said "couldn't tell which
        organisation is asking" on All venues (18 Oct 2026): the executor
        derived the scope from a venue or thread only, and a tile with no
        venue has neither."""
        import app.connectors.tool_executor as te

        seen = []
        monkeypatch.setattr(
            te,
            "_execute_connector_tool",
            lambda *a, **k: seen.append(caller_scope.current()) or None,
        )
        te.execute_connector_tool("norm", "app_records", {}, db_session, db_session)
        assert seen == [None]  # nobody identified: stays refused
        te.execute_connector_tool(
            "norm", "app_records", {}, db_session, db_session, user=world.user
        )
        assert seen[1].org_id == world.ours.id


class TestHandlers:
    def test_invoice_handlers_refuse_another_organisations_venue(
        self, db_session, world
    ):
        from app.agents.internal_tools import _scoped_venue_id

        with _in(world.ours):
            vid, err = _scoped_venue_id({"venue": "La Zeppa"}, db_session)
            assert vid is None and "No venue called 'La Zeppa'" in err
            vid, _ = _scoped_venue_id({"venue_id": world.rival_zeppa.id}, db_session)
            assert vid is None
            vid, _ = _scoped_venue_id({"venue": "Mr Murdochs"}, db_session)
            assert vid == world.murdochs.id

    def test_scheduled_tasks_list_only_the_organisations(self, db_session, world):
        from app.agents.internal_tools import get_handler
        from app.db.models import AutomatedTask

        def task(title, venue, creator):
            t = AutomatedTask(
                id=str(uuid.uuid4()),
                agent_slug="reports",
                title=title,
                prompt="x",
                venue_id=venue.id if venue else None,
                created_by=creator.id if creator else None,
            )
            db_session.add(t)
            db_session.flush()

        rival_user = make_user(db_session)
        _make_membership(db_session, rival_user, world.rival)
        task("ours by venue", world.goose, None)
        task("ours by creator", None, world.user)
        task("theirs", world.rival_zeppa, rival_user)
        with _in(world.ours):
            out = get_handler("norm", "list_automated_tasks")({}, db_session, None)
        titles = {t["title"] for t in out["data"]["tasks"]}
        assert {"ours by venue", "ours by creator"} <= titles
        assert "theirs" not in titles


class TestCredentials:
    def test_a_consolidators_venue_name_finds_only_the_callers_login(
        self, db_session, world
    ):
        """resolve_venue matched every organisation's venues, both ways."""
        from app.services.venue_resolver import resolve_venue

        with _in(world.ours):
            assert resolve_venue("La Zeppa", db_session) is None
            assert resolve_venue("Glass Goose", db_session)["id"] == world.goose.id

    def test_another_organisations_venue_id_picks_no_login(self, db_session, world):
        from app.agents.tool_loop import _resolve_venue_config
        from app.db.models import Connection

        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="scopehub",
                venue_id=world.rival_zeppa.id,
                config={"k": "theirs"},
                enabled="true",
            )
        )
        db_session.flush()
        with _in(world.ours):
            row = _resolve_venue_config(
                "scopehub", {"venue_id": world.rival_zeppa.id}, db_session
            )
        assert row is None

    def test_a_named_venue_without_a_login_never_borrows_another(
        self, db_session, world, monkeypatch
    ):
        """A consolidator call naming a venue that doesn't resolve ran on the
        organisation's FIRST login — another venue's data, or for a write,
        another venue's change (17 Oct 2026: set_revenue_group's preview for
        Applegum Cafe read La Zeppa's POS items). Naming no venue still falls
        back, as single-venue organisations rely on."""
        import app.connectors.spec_executor as spec_mod
        import app.db.engine as engine
        from app.connectors.function_executor import execute_function
        from app.db.config_models import ConnectionSpec
        from app.db.models import Connection

        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="scopehub",
                display_name="S",
                execution_mode="template",
                auth_type="none",
                auth_config={},
                base_url_template="https://",
                tools=[{"action": "read", "method": "GET", "path_template": "//x"}],
                enabled=True,
            )
        )
        for venue in (world.goose, world.murdochs):
            db_session.add(
                Connection(
                    id=str(uuid.uuid4()),
                    connector_name="scopehub",
                    venue_id=venue.id,
                    config={"k": venue.name},
                    enabled="true",
                )
            )
        db_session.flush()

        class _Cfg:  # the spec lookup's own session, pointed at this test's
            def query(self, *a):
                return db_session.query(*a)

            def expunge(self, obj):
                pass

            def close(self):
                pass

        monkeypatch.setattr(engine, "_ConfigSessionLocal", lambda: _Cfg())
        sent = []

        def fake_execute_spec(spec, tool_def, params, credentials, db, *a, **kw):
            sent.append((credentials or {}).get("k"))
            return SimpleNamespace(
                success=True, response_payload={"ok": True}, error_message=None
            ), None

        monkeypatch.setattr(spec_mod, "execute_spec", fake_execute_spec)

        def call(venue):
            code = (
                "def run(params, call_api, log):\n"
                "    return call_api('scopehub', 'read', params)\n"
            )
            with _in(world.ours):
                return execute_function(
                    code, venue, db_session, None, options={"max_api_calls": 2}
                )["data"]

        out = call({"venue": "Murdochs Rooftop"})  # no venue of ours has that name
        assert "isn't a venue you can use scopehub" in out["error"] and sent == []
        assert "error" in call({"venue": "La Zeppa"})  # another organisation's
        assert sent == []
        assert call({"venue": "Mr Murdochs"}) == {"ok": True}
        assert sent == ["Mr Murdochs"]
        assert call({}) == {"ok": True}  # no venue named: the scoped fallback
        assert len(sent) == 2

    def test_parallel_workers_keep_the_scope(self, db_session, world, monkeypatch):
        """call_api_parallel runs workers in threads; a bare thread starts
        with no scope at all."""
        import app.agents.internal_tools as it
        import app.db.engine as engine
        from app.connectors.function_executor import execute_function
        from app.db.config_models import ConnectionSpec

        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="scope_norm",
                display_name="S",
                execution_mode="internal",
                auth_type="none",
                auth_config={},
                tools=[{"action": "peek", "method": "GET"}],
                enabled=True,
            )
        )
        db_session.flush()

        class _Cfg:  # the spec lookup's own session, pointed at this test's
            def query(self, *a):
                return db_session.query(*a)

            def expunge(self, obj):
                pass

            def close(self):
                pass

        monkeypatch.setattr(engine, "_ConfigSessionLocal", lambda: _Cfg())
        seen = []

        def peek(params, db, thread_id):
            seen.append(caller_scope.current())
            return {"success": True, "data": {}}

        real = it.get_handler
        monkeypatch.setattr(
            it,
            "get_handler",
            lambda c, a: peek if (c, a) == ("scope_norm", "peek") else real(c, a),
        )
        code = (
            "def run(params, call_api, log, call_api_parallel):\n"
            "    call_api_parallel([('scope_norm', 'peek', {}), ('scope_norm', 'peek', {})])\n"
            "    return {}\n"
        )
        with _in(world.ours):
            out = execute_function(
                code, {}, db_session, None, options={"max_api_calls": 5}
            )
        assert len(seen) == 2, out
        assert all(s is not None and s.org_id == world.ours.id for s in seen)
