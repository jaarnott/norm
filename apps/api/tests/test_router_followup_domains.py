"""The follow-up classifier must choose from the agents that actually exist.

`classify_followup` returns action "new_thread" plus a `domain`, and the
supervisor uses that domain to hand the conversation to another agent. The
prompt used to say only "set domain to the appropriate domain" without ever
listing them — and its one example mentioned "inventory", which is not an agent.

Observed live on 2026-07-20: asking a stock question inside a time_attendance
thread returned `domain=inventory`. `get_agent("inventory")` is None, so the
rebind refused and the conversation was split into a second thread instead of
being handed over. Stock lives in procurement.
"""

from app.agents.registry import registered_domains


def _followup_system_prompt(**kw):
    """Render classify_followup's system prompt without calling the API."""
    import anthropic

    from app.agents import router

    captured = {}

    class _FakeMessages:
        def create(self, **call_kw):
            captured["system"] = call_kw["system"]
            raise RuntimeError("stop before the network call")

    class _FakeClient:
        def __init__(self, *a, **k):
            self.messages = _FakeMessages()

    original = anthropic.Anthropic
    anthropic.Anthropic = _FakeClient
    try:
        # classify_followup swallows exceptions and falls back to "continue",
        # so the raised error above simply ends the call.
        router.classify_followup(
            kw.get("message", "what beer stock do we have"),
            kw.get("thread_domain", "time_attendance"),
            None,
            kw.get("recent_summary", "User: hours last week"),
            config_db=kw.get("config_db"),
        )
    finally:
        anthropic.Anthropic = original
    return captured.get("system", "")


class TestTheClassifierIsToldWhatEachDomainDoes:
    """Knowing the agents EXIST was never enough to pick the right one.

    Thread 46c17508 (15 Aug 2026): from a bare slug list the router sent a
    wage-cost question to `hr` — which is BambooHR recruitment, with no hours
    and no pay — while `time_attendance` held the timeclock tools and sat in
    the same list, unchosen. The descriptions that make the choice obvious
    already existed in agent_configs; nobody showed them to the router.
    """

    def test_each_domain_line_says_what_that_agent_does(self, db_session):
        from app.db.config_models import AgentConfig

        db_session.add(
            AgentConfig(
                agent_slug="time_attendance",
                display_name="Time & Attendance",
                description="Rosters, timeclock hours and wage cost",
            )
        )
        db_session.flush()

        prompt = _followup_system_prompt(config_db=db_session)

        assert "time_attendance: Rosters, timeclock hours and wage cost" in prompt

    def test_an_agent_with_no_description_is_still_routable(self, db_session):
        """A registry/config mismatch must not drop an agent off the menu."""
        prompt = _followup_system_prompt(config_db=db_session)
        for domain in registered_domains():
            assert domain in prompt

    def test_a_statement_of_context_is_named_as_a_reason_to_stay(self):
        prompt = _followup_system_prompt()
        assert "not a request" in prompt.lower()

    def test_the_classifier_is_asked_whether_this_is_even_a_request(self):
        assert "is_request" in _followup_system_prompt()

    def test_the_classifier_is_asked_whether_the_ask_needs_writes(self):
        """Consulting is read-only, so the stay-put-and-consult shortcut needs
        to know when the user wants data changed rather than read (the verdict
        field the supervisor gates the consult-stay on)."""
        assert "target_writes" in _followup_system_prompt()

    def test_consulting_is_offered_only_to_an_agent_that_can_do_it(self, db_session):
        from app.db.config_models import AgentConnectionBinding

        assert "consulting" not in _followup_system_prompt(config_db=db_session).lower()

        db_session.add(
            AgentConnectionBinding(
                agent_slug="time_attendance",
                connector_name="norm",
                capabilities=[{"action": "delegate_to_agent", "enabled": True}],
                enabled=True,
            )
        )
        db_session.flush()
        prompt = _followup_system_prompt(config_db=db_session)
        assert "consulting another agent" in prompt


class TestTheVerdictCarriesWhetherItWasARequest:
    def test_a_verdict_without_the_field_is_read_as_a_request(self):
        """Every existing caller and stub predates this field; absent must mean
        "they asked for something", which is how routing behaved before."""
        import anthropic

        from app.agents import router

        class _Messages:
            def create(self, **kw):
                class _R:
                    content = [
                        type(
                            "T",
                            (),
                            {"text": '{"action": "continue", "domain": "reports"}'},
                        )()
                    ]
                    usage = None

                return _R()

        class _Client:
            def __init__(self, *a, **k):
                self.messages = _Messages()

        original = anthropic.Anthropic
        anthropic.Anthropic = _Client
        try:
            out = router.classify_followup("anything", "reports", None, "")
        finally:
            anthropic.Anthropic = original

        assert out["is_request"] is True
        # Absent target_writes reads as "reading is enough" — the consult-stay
        # behaviour every verdict had before the field existed.
        assert out["target_writes"] is False


class TestTheClassifierIsToldWhichDomainsExist:
    def test_every_registered_domain_is_listed(self):
        prompt = _followup_system_prompt()
        for domain in registered_domains():
            assert domain in prompt, f"{domain} missing from the follow-up prompt"

    def test_it_is_told_not_to_invent_one(self):
        prompt = _followup_system_prompt()
        assert "never invent" in prompt.lower()

    def test_stock_is_pointed_at_procurement(self):
        """The word that actually caused the misroute."""
        prompt = _followup_system_prompt()
        assert "procurement" in prompt
        assert "inventory" in prompt.lower()
        # "inventory" must appear as a pointer to procurement, not as a domain
        # the model could copy out of an example.
        assert "asking about inventory in an HR thread" not in prompt

    def test_an_api_failure_keeps_the_conversation_where_it_is(self):
        """classify_followup defaults to 'continue' rather than raising.

        A router outage must not look like a topic change — that would hand the
        conversation to another agent for no reason.
        """
        import anthropic

        from app.agents import router

        class _FailingMessages:
            def create(self, **kw):
                raise RuntimeError("router unavailable")

        class _FailingClient:
            def __init__(self, *a, **k):
                self.messages = _FailingMessages()

        original = anthropic.Anthropic
        anthropic.Anthropic = _FailingClient
        try:
            result = router.classify_followup("msg", "reports", None, "summary")
        finally:
            anthropic.Anthropic = original

        assert result["action"] == "continue"
        assert result["domain"] == "reports"
