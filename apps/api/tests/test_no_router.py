"""No router: every message goes to the one Norm agent (Sep 2026).

A Haiku router used to pick one of seven domain agents for each new message and
re-ask on every follow-up whether to switch. Once Apps v3 gave every
conversation the full entitled tool union, the pick changed only a label — for
~1.5s a turn. In production's last 30 days with it, 50 of 51 follow-up verdicts
were "continue", and a question spanning two Apps ("the chefs at La Zeppa, and
the open kitchen roles") scored "unknown" and got a capability menu instead of
an answer — the router's own clarifying question was dropped on the way.

What these pin:
- no model call stands between the user and the agent, new thread or follow-up;
- a follow-up ALWAYS stays in its thread. The router-era incidents were all
  about leaving one: a remark ("Murdoch's is closed due to a fire") moving a
  sales conversation to the recipes agent (thread 46c17508, 15 Aug 2026); a
  pending approval stranded by a move; a venue picker replaying question one
  and deleting a thread whose tool calls still pointed at it (ff5ae525);
- the venue comes from the message when it names exactly one of the user's
  venues; the title is drafted beside the reply, never in front of it;
- a task filed under Norm (no member) may read but not write when unattended;
- a thread is labelled by the Apps whose tools it used.
"""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.db.config_models import ConnectionSpec, MarketplaceApp
from app.db.models import LlmCall, Thread, ToolCall
from app.services import agent_catalog
from tests.conftest import _make_thread, _make_venue, _make_venue_access


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


@pytest.fixture()
def no_model(monkeypatch):
    """Any Anthropic client construction fails the test — nothing may call a
    model before the agent (the agent itself is faked where it runs)."""
    import anthropic

    def _boom(*a, **k):
        raise AssertionError("a model was called outside the agent")

    monkeypatch.setattr(anthropic, "Anthropic", _boom)
    monkeypatch.setattr("app.services.thread_titles.start", lambda *a, **k: None)


def _fake_agent(db_session, user):
    """Stands in for the Norm agent: records the call, makes the thread a
    real agent would (for a new message), and answers."""
    agent = MagicMock()

    def handle(message, db, user_id=None, thread_id=None, **kw):
        if thread_id is None:
            t = Thread(
                user_id=user.id,
                domain="norm",
                intent="norm.tool_use",
                status="completed",
                raw_prompt=message,
                venue_id=kw.get("venue_id"),
                extracted_fields={},
                missing_fields=[],
            )
            db.add(t)
            db.flush()
            thread_id = t.id
        return {"id": thread_id, "message": "answer"}

    agent.handle_message.side_effect = handle
    return agent


def _send(db_session, user, message, *, thread_id=None, agent=None):
    from app.services import supervisor

    agent = agent or _fake_agent(db_session, user)
    with patch("app.services.supervisor.norm_agent", return_value=agent):
        result = supervisor.handle_message(
            message,
            db_session,
            config_db=db_session,
            user_id=user.id,
            thread_id=thread_id,
        )
    return result, agent


class TestNewMessages:
    def test_a_cross_app_question_reaches_the_agent(
        self, db_session, admin_user, no_model
    ):
        # The question the router turned into a capability menu (28 Sep 2026).
        q = (
            "Who are the chefs at La Zeppa, and which open kitchen roles are we "
            "hiring for right now with the most new applicants?"
        )
        result, agent = _send(db_session, admin_user, q)
        assert agent.handle_message.call_count == 1
        assert agent.handle_message.call_args.args[0] == q
        thread = db_session.query(Thread).filter(Thread.id == result["id"]).one()
        assert thread.domain == "norm"

    def test_the_title_falls_back_to_the_opening_words(
        self, db_session, admin_user, no_model
    ):
        result, _ = _send(db_session, admin_user, "what were sales yesterday")
        assert result["title"] == "what were sales yesterday"

    def test_the_named_venue_is_the_threads_venue(
        self, db_session, admin_user, no_model
    ):
        lz = _make_venue(db_session, name="La Zeppa")
        gg = _make_venue(db_session, name="The Glass Goose")
        _make_venue_access(db_session, admin_user, lz)
        _make_venue_access(db_session, admin_user, gg)
        _, agent = _send(db_session, admin_user, "sales at glass goose yesterday?")
        kw = agent.handle_message.call_args.kwargs
        assert kw["venue_id"] == gg.id and kw["venue_name"] == "The Glass Goose"

    def test_no_single_venue_named_leaves_it_to_the_agent(
        self, db_session, admin_user, no_model
    ):
        lz = _make_venue(db_session, name="La Zeppa")
        gg = _make_venue(db_session, name="The Glass Goose")
        _make_venue_access(db_session, admin_user, lz)
        _make_venue_access(db_session, admin_user, gg)
        for q in ("compare La Zeppa and Glass Goose sales", "sales yesterday"):
            _, agent = _send(db_session, admin_user, q)
            assert agent.handle_message.call_args.kwargs["venue_id"] is None, q

    def test_a_users_only_venue_is_used(self, db_session, admin_user, no_model):
        lz = _make_venue(db_session, name="La Zeppa")
        _make_venue_access(db_session, admin_user, lz)
        _, agent = _send(db_session, admin_user, "sales yesterday")
        assert agent.handle_message.call_args.kwargs["venue_id"] == lz.id

    def test_a_connect_request_is_an_ordinary_norm_thread(self, db_session):
        from app.services.supervisor import _create_connect_response

        out = _create_connect_response("connect bamboo", "bamboohr", db_session)
        assert out["domain"] == "norm" and out["intent"] == "norm.tool_use"
        blocks = out["conversation"][-1]["display_blocks"]
        assert blocks[0]["component"] == "connector_connect"


class TestVenueMatching:
    VENUES = [
        SimpleNamespace(name="La Zeppa"),
        SimpleNamespace(name="The Glass Goose"),
        SimpleNamespace(name="Mr Murdoch's"),
    ]

    def _hit(self, message):
        from app.services.supervisor import _venue_named_in

        v = _venue_named_in(message, self.VENUES)
        return v.name if v else None

    def test_case_punctuation_and_the_are_ignored(self):
        assert self._hit("labour at LA ZEPPA, last week") == "La Zeppa"
        assert self._hit("the glass goose roster") == "The Glass Goose"
        assert self._hit("Glass Goose roster") == "The Glass Goose"
        assert self._hit("mr murdochs sales") == "Mr Murdoch's"

    def test_whole_words_only(self):
        assert self._hit("la zeppas") is None

    def test_two_venues_is_nobodys_venue(self):
        assert self._hit("la zeppa vs mr murdochs") is None


class TestFollowUps:
    @pytest.mark.parametrize(
        "domain,intent,status",
        [
            ("reports", "reports.tool_use", "completed"),
            # the prod thread whose "check again" failed twice (27 Sep 2026)
            ("meta", "meta.connect", "completed"),
            ("unknown", "error", "completed"),
            ("procurement", "procurement.tool_use", "awaiting_tool_approval"),
            ("reports", "reports.mcp_playbook", "completed"),
        ],
    )
    def test_every_followup_stays_in_its_thread(
        self, db_session, admin_user, no_model, domain, intent, status
    ):
        t = _make_thread(
            db_session, admin_user, domain=domain, intent=intent, status=status
        )
        # A remark, not a request — the router once moved threads on these.
        _, agent = _send(
            db_session,
            admin_user,
            "Murdoch's is closed due to a fire and reopens on the 11th",
            thread_id=t.id,
        )
        assert agent.handle_message.call_args.args[3] == t.id
        assert db_session.query(Thread).filter(Thread.id == t.id).one().domain == (
            domain
        )

    def test_the_threads_venue_carries_on(self, db_session, admin_user, no_model):
        lz = _make_venue(db_session, name="La Zeppa")
        t = _make_thread(
            db_session, admin_user, intent="reports.tool_use", venue_id=lz.id
        )
        _, agent = _send(db_session, admin_user, "and the week before?", thread_id=t.id)
        kw = agent.handle_message.call_args.kwargs
        assert kw["venue_id"] == lz.id and kw["venue_name"] == "La Zeppa"

    def test_an_open_venue_picker_becomes_an_ordinary_thread(
        self, db_session, admin_user, no_model
    ):
        t = _make_thread(
            db_session,
            admin_user,
            domain="time_attendance",
            intent="venue_clarification",
            status="needs_clarification",
        )
        _send(db_session, admin_user, "La Zeppa", thread_id=t.id)
        db_session.refresh(t)
        assert t.intent == "time_attendance.tool_use"
        assert t.status == "in_progress"


class TestRegistry:
    def test_one_agent_answers_for_norm_and_every_member(self):
        from app.agents.registry import MEMBERS, get_agent, norm_agent

        assert get_agent("norm") is norm_agent()
        assert all(get_agent(m) is norm_agent() for m in MEMBERS)
        assert get_agent("meta") is None and get_agent("inventory") is None

    def test_new_threads_are_filed_under_norm(self):
        from app.agents.registry import norm_agent

        assert norm_agent().domain == "norm"


class TestTitles:
    def test_a_drafted_title_is_used_and_logged(self, db_session, admin_user):
        from app.services import thread_titles

        t = _make_thread(db_session, admin_user, intent="norm.tool_use")
        job = Future()
        job.set_result(
            {
                "title": "Kitchen hiring overview",
                "model": "haiku",
                "duration_ms": 900,
                "input_tokens": 40,
                "output_tokens": 5,
            }
        )
        title = thread_titles.finish(job, "who are the chefs", t.id, db_session)
        assert title == "Kitchen hiring overview"
        call = db_session.query(LlmCall).filter(LlmCall.thread_id == t.id).one()
        assert call.call_type == "title"

    def test_a_failed_draft_never_fails_the_turn(self, db_session, admin_user):
        from app.services import thread_titles

        t = _make_thread(db_session, admin_user, intent="norm.tool_use")
        job = Future()
        job.set_exception(RuntimeError("overloaded"))
        long = "please " * 30
        title = thread_titles.finish(job, long, t.id, db_session)
        assert title.endswith("…") and len(title) <= 61


def _spec(db, name, tools):
    db.add(
        ConnectionSpec(
            connector_name=name,
            display_name=name,
            auth_type="none",
            execution_mode="internal",
            enabled=True,
            tools=tools,
        )
    )
    db.flush()


def _app(db, slug, member, tools, name=None):
    db.add(
        MarketplaceApp(
            slug=slug,
            name=name or slug,
            description="",
            tier="app",
            bundled=True,
            composition={"member": member, "tools": tools},
        )
    )
    db.flush()


def _catalog(db):
    _spec(
        db,
        "lh",
        [
            {"action": "get_sales", "method": "GET", "read_only": True},
            # a write registered as GET — method alone must not admit it
            {"action": "create_po", "method": "GET", "read_only": False},
            {"action": "receive", "method": "GET"},
        ],
    )
    _spec(db, "nm", [{"action": "send_email", "method": "POST"}])
    _app(db, "core", "*", ["nm.send_email"], name="Norm Core")
    _app(db, "reps", "reports", ["lh.get_sales"], name="Loaded Reports")
    _app(db, "stock", "procurement", ["lh.create_po", "lh.receive"], name="Stock")


class TestUnattendedScope:
    def test_a_norm_task_may_read_anything_and_write_nothing(self, db_session):
        from app.services.agent_config_service import default_tool_filter

        _catalog(db_session)
        assert default_tool_filter("norm", db_session) == ["get_sales", "send_email"]

    def test_a_member_task_keeps_its_members_scope(self, db_session):
        from app.services.agent_config_service import default_tool_filter

        _catalog(db_session)
        assert set(default_tool_filter("procurement", db_session)) == {
            "create_po",
            "receive",
            "send_email",
        }


class TestThreadsAreLabelledByTheirApps:
    def test_apps_in_first_use_order_without_norm_core(self, db_session, admin_user):
        from app.routers.threads import _apps_by_thread

        _catalog(db_session)
        t = _make_thread(db_session, admin_user, domain="norm", intent="norm.tool_use")
        for connector, action in (
            ("lh", "get_sales"),
            ("nm", "send_email"),
            ("lh", "create_po"),
            ("lh", "get_sales"),
        ):
            db_session.add(
                ToolCall(
                    thread_id=t.id,
                    iteration=1,
                    tool_name=f"{connector}__{action}",
                    connector_name=connector,
                    action=action,
                    method="GET",
                    status="executed",
                )
            )
            db_session.flush()
        apps = _apps_by_thread([t.id], db_session, db_session)[t.id]
        assert apps == [
            {"slug": "reps", "name": "Loaded Reports", "member": "reports"},
            {"slug": "stock", "name": "Stock", "member": "procurement"},
        ]

    def test_a_thread_with_no_tools_has_no_apps(self, db_session, admin_user):
        from app.routers.threads import _apps_by_thread

        _catalog(db_session)
        t = _make_thread(db_session, admin_user, domain="norm", intent="norm.tool_use")
        assert _apps_by_thread([t.id], db_session, db_session) == {}
