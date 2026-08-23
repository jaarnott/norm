"""A remark is not a domain switch, and "I can't" is not an answer.

Thread 46c17508, 15 Aug 2026. A user asked for the week's projected sales and
wage %. It worked on `reports` until they mentioned, in passing, that one venue
was shut:

    User:  "Murdoch's is closed due to a fire and expected to reopen on the 11th"
    Router: {"action": "new_thread", "domain": "executive_chef",
             "reason": "operational/venue status information ... belongs in the
                        executive_chef domain"}

That is a statement, not a request — context for the report in hand. It moved
the conversation to the recipes-and-menus agent, and everything after it
inherited that. The wage question was then answered by the menu agent, handed
on to `hr` (BambooHR recruitment — no hours, no pay, and no `delegate_to_agent`
at the time), and died there. The user routed it by hand — "Ask the reports
agent" — and the right answer arrived 80 seconds later.

What makes it expensive is that the conversation SURVIVES a rebind while the
toolset does not: all 16 messages stayed on the one thread, so each new agent
went on quoting the sales table it had inherited while holding nothing that
could fetch a wage cost. Context stays, capability vanishes, nothing says so.

So: three guards, in the order the supervisor applies them.
"""

import pytest

from app.db.config_models import AgentConfig, AgentConnectorBinding, ConnectorSpec
from app.services import supervisor
from tests.conftest import _make_thread

from .test_supervisor_domain_switch import _StubAgent


@pytest.fixture()
def no_quota_check(monkeypatch):
    monkeypatch.setattr(
        "app.services.billing_service.check_quota_for_user", lambda db, user_id: None
    )


@pytest.fixture()
def stub_loop(monkeypatch):
    """run_tool_loop that reports which thread it actually ran on."""
    monkeypatch.setattr(
        "app.agents.tool_loop.run_tool_loop",
        lambda message, thread, db, *a, **kw: {
            "id": thread.id,
            "message": message,
            "domain": thread.domain,
        },
    )


def _bind(db, slug, connector, actions):
    db.add(
        AgentConnectorBinding(
            agent_slug=slug,
            connector_name=connector,
            capabilities=[{"action": a, "enabled": True} for a in actions],
            enabled=True,
        )
    )
    db.flush()


@pytest.fixture()
def roster(db_session):
    """`reports` can consult; `time_attendance` has something worth asking."""
    db_session.add(
        ConnectorSpec(
            connector_name="norm",
            display_name="Norm",
            auth_type="none",
            execution_mode="internal",
            enabled=True,
            tools=[
                {"action": "delegate_to_agent", "method": "GET", "read_only": True},
                {"action": "get_roster", "method": "GET", "read_only": True},
                {"action": "create_rostered_shift", "method": "POST"},
            ],
        )
    )
    db_session.flush()
    _bind(db_session, "reports", "norm", ["delegate_to_agent"])
    _bind(db_session, "time_attendance", "norm", ["get_roster"])
    db_session.commit()


def _followup(monkeypatch, **verdict):
    base = {"action": "new_thread", "domain": "executive_chef", "playbook": None}
    base.update(verdict)
    monkeypatch.setattr("app.agents.router.classify_followup", lambda *a, **kw: base)


def _run(db_session, user, monkeypatch, message, *, thread, agent=None):
    agent = agent or _StubAgent()
    monkeypatch.setattr(supervisor, "get_agent", lambda domain: agent)
    return supervisor.handle_message(
        message,
        db_session,
        config_db=db_session,
        user_id=user.id,
        thread_id=thread.id,
    )


def _reports_thread(db_session, user):
    return _make_thread(
        db_session,
        user,
        domain="reports",
        intent="reports.tool_use",
        status="completed",
        raw_prompt="weekly sales",
    )


class TestAStatementOfContextDoesNotMoveTheThread:
    def test_a_fact_the_user_volunteers_is_not_a_domain_switch(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop
    ):
        """The 22:20 message, verbatim — the pivot the whole failure hangs off."""
        thread = _reports_thread(db_session, admin_user)
        _followup(monkeypatch, is_request=False)

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "Murdoch's is closed due to a fire and expected to reopen on the 11th September",
            thread=thread,
        )

        assert thread.domain == "reports"

    def test_a_playbook_chosen_for_the_other_agent_is_dropped(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop
    ):
        """The playbook lookup matches on slug alone, not on agent — a slug
        picked while the classifier was thinking about another agent would
        otherwise load straight into this one."""
        thread = _reports_thread(db_session, admin_user)
        seen = {}
        _followup(monkeypatch, is_request=False, playbook="menu_costing")

        class _Recorder(_StubAgent):
            def get_tool_definitions(self, db, **kw):
                seen["playbook"] = kw.get("playbook")
                return ("sys", [{"name": "x"}])

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "the kitchen flooded last night",
            thread=thread,
            agent=_Recorder(),
        )

        assert seen.get("playbook") is None

    def test_a_verdict_without_the_field_still_switches(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop
    ):
        """Absent means "they asked for something" — the reading that leaves
        every existing caller, stub and error path behaving as before."""
        thread = _reports_thread(db_session, admin_user)
        _followup(monkeypatch)  # no is_request key at all

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "how do I cost this menu",
            thread=thread,
        )

        assert thread.domain == "executive_chef"


class TestConsultingBeatsBeingReplaced:
    def test_a_wage_question_in_a_sales_thread_stays_put(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop, roster
    ):
        """22:21. `reports` can ask `time_attendance`; handing the whole
        conversation over throws away the tools and playbook it is using."""
        thread = _reports_thread(db_session, admin_user)
        _followup(monkeypatch, domain="time_attendance", is_request=True)

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "can you grab the wage cost as well so we can see the wage %age",
            thread=thread,
        )

        assert thread.domain == "reports"

    def test_write_work_hands_over_even_when_consulting_is_possible(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop, roster
    ):
        """Consulting is read-only, so it only substitutes for a hand-over when
        reading is enough. On 20 Aug 2026 (thread bb7010c3) a recipe/stock WRITE
        workflow was pinned on an agent because it could consult the right one —
        but a consult can never perform the write, so the thread was stranded on
        an agent without the tools, which then told the user its earlier (real)
        writes had never happened."""
        thread = _reports_thread(db_session, admin_user)
        _followup(
            monkeypatch,
            domain="time_attendance",
            is_request=True,
            target_writes=True,
        )

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "add a shift for Sam on Friday 5pm to close",
            thread=thread,
        )

        assert thread.domain == "time_attendance"

    def test_an_agent_that_cannot_consult_still_hands_over(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop, roster
    ):
        """Staying put is only better when there is a way to get the answer."""
        thread = _make_thread(
            db_session,
            admin_user,
            domain="executive_chef",  # no delegate_to_agent binding
            intent="executive_chef.tool_use",
            status="completed",
        )
        _followup(monkeypatch, domain="time_attendance", is_request=True)

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "what were the rostered hours",
            thread=thread,
        )

        assert thread.domain == "time_attendance"

    def test_a_target_with_nothing_readable_is_no_reason_to_stay(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop, roster
    ):
        """`delegate()` refuses a target with no read-only tools. Pinning the
        user on an agent that would only get that refusal back is the D3 shape
        of this incident: `hr` passed the "has tools?" check and still could
        not help."""
        _bind(db_session, "marketing", "norm", ["create_rostered_shift"])
        db_session.commit()
        thread = _reports_thread(db_session, admin_user)
        _followup(monkeypatch, domain="marketing", is_request=True)

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "schedule the campaign",
            thread=thread,
        )

        assert thread.domain == "marketing"

    def test_an_exhausted_delegation_budget_is_no_reason_to_stay(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop, roster
    ):
        thread = _reports_thread(db_session, admin_user)
        _followup(monkeypatch, domain="time_attendance", is_request=True)
        monkeypatch.setattr(
            "app.services.delegation.check_guards",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("budget exhausted")),
        )

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "what were the rostered hours",
            thread=thread,
        )

        assert thread.domain == "time_attendance"


class TestAnExplicitHandoverAlwaysWins:
    def test_naming_an_agent_hands_over_even_when_the_classifier_says_continue(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop, roster
    ):
        """22:22:38 — the message that finally worked. When the user routes by
        hand they are overruling the classifier, and that has to win."""
        thread = _reports_thread(db_session, admin_user)
        _followup(monkeypatch, action="continue", domain="reports")

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "You can get it from the loaded connection. Ask the time attendance agent",
            thread=thread,
        )

        assert thread.domain == "time_attendance"

    @pytest.mark.parametrize(
        "message",
        [
            "the reports looked wrong",
            "can you report on wages",
            "ask the kitchen about the special",
        ],
    )
    def test_an_ordinary_mention_does_not_hand_over(
        self,
        db_session,
        admin_user,
        monkeypatch,
        no_quota_check,
        stub_loop,
        roster,
        message,
    ):
        thread = _make_thread(
            db_session,
            admin_user,
            domain="time_attendance",
            intent="time_attendance.tool_use",
            status="completed",
        )
        _followup(monkeypatch, action="continue", domain="time_attendance")

        _run(db_session, admin_user, monkeypatch, message, thread=thread)

        assert thread.domain == "time_attendance"

    def test_a_pending_approval_still_pins_the_thread(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop, roster
    ):
        """The suspended write belongs to THIS agent and its state is in this
        thread's columns. Naming another agent must not take it away — that is
        how an approval card ends up pointing at a thread nobody owns."""
        thread = _reports_thread(db_session, admin_user)
        thread.status = "awaiting_tool_approval"
        db_session.flush()
        _followup(monkeypatch, action="continue", domain="reports")

        _run(
            db_session,
            admin_user,
            monkeypatch,
            "ask the time attendance agent instead",
            thread=thread,
        )

        assert thread.domain == "reports"


class TestTheAgentIsToldConsultingIsItsJob:
    def test_an_agent_that_can_consult_is_told_not_to_apologise(self):
        from app.agents.prompt_builder import delegation_guidance

        text = delegation_guidance(True)
        assert "delegate_to_agent" in text
        # The exact behaviour the user saw and disliked.
        assert "I can't do that" in text

    def test_an_agent_that_cannot_is_told_nothing(self):
        from app.agents.prompt_builder import delegation_guidance

        assert delegation_guidance(False) == ""


class TestNormCanSayWhatItDoes:
    def test_the_fallback_names_the_agents_that_exist(self, db_session):
        """Not three frozen examples. The user picked "generate a report" off
        that list, which is how a sales question became a five-agent tour."""
        db_session.add(
            AgentConfig(
                agent_slug="reports",
                display_name="Reports Agent",
                description="Generates sales and inventory reports",
            )
        )
        db_session.commit()

        hint = supervisor._capability_hint(db_session)

        assert "Generates sales and inventory reports" in hint

    def test_the_routers_own_question_is_preferred(
        self, db_session, admin_user, monkeypatch
    ):
        """The router said "are you looking at a specific product, time period,
        or promotion?" and the user was shown a generic list instead."""
        result = supervisor._create_unknown(
            "what will sales be for this",
            db_session,
            admin_user.id,
            routing={"venue_question": "Which product did you mean?"},
            config_db=db_session,
        )

        assert result["clarification_question"] == "Which product did you mean?"
