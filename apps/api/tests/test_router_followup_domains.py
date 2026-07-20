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
        )
    finally:
        anthropic.Anthropic = original
    return captured.get("system", "")


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
