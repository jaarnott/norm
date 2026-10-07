"""What a model call really used is recorded, and the cache is used well (Oct 2026).

Norm stored only ``usage.input_tokens`` — the full-price part of a call's input.
Once the whole conversation was cached (4 Oct 2026) that was ~2 tokens on a
~100,000-token call, so thread and Settings figures read close to zero while
the bill did not, and plan limits measured a fraction of real use. Calls with
no thread (invoice extraction, summarisation) reached no organisation at all,
summaries whose reply failed to parse were billed but saved as free errors,
direct SDK calls recorded nothing, and deleting a thread deleted its usage.

On the cache side: each new turn replayed earlier user turns as their bare
text, though they had been sent with context appended — so the prompt changed
at the first message and every turn rewrote the whole history; and the
wrap-up call dropped the tool list, which rewrote everything.
"""

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import LlmCall, Message, TokenUsage
from app.services.llm_cost import (
    Usage,
    billable_tokens,
    cost_usd,
    price_for,
    usage_fields,
)
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_thread,
    _make_user,
)


def _usage(**kw):
    base = dict(
        input_tokens=2,
        output_tokens=500,
        cache_read_input_tokens=100_000,
        cache_creation_input_tokens=4_000,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestPricesAndBillableTokens:
    def test_a_dated_model_id_finds_its_family(self):
        assert price_for("claude-haiku-4-5-20251001").input == 1.00
        # longest prefix wins: opus-5-5 is not opus-5
        assert price_for("claude-opus-5-5").cache_read == 0.20
        assert price_for("claude-opus-5").cache_read == 0.50

    def test_an_unknown_model_is_never_free(self):
        assert price_for("claude-something-new").input > 0

    def test_cost_counts_every_part_of_the_input(self):
        u = Usage.from_response(_usage())
        assert u.total_input == 104_002
        # Opus 5.5: $4 in, $20 out, $0.20 cache read, $5 cache write (per M)
        expected = (2 * 4 + 500 * 20 + 100_000 * 0.20 + 4_000 * 5) / 1e6
        assert cost_usd("claude-opus-5-5", u) == pytest.approx(expected)

    def test_billable_tokens_weight_the_cache_by_price(self):
        u = Usage.from_response(_usage())
        # read 0.05x and write 1.25x of the input price on Opus 5.5
        assert billable_tokens("claude-opus-5-5", u) == round(2 + 500 + 5_000 + 5_000)
        # read 0.1x on Opus 4.8
        assert billable_tokens("claude-opus-4-8", u) == round(2 + 500 + 10_000 + 5_000)

    def test_without_cache_billable_is_input_plus_output_as_before(self):
        u = Usage(input=1_000, output=200)
        assert billable_tokens("claude-opus-5-5", u) == 1_200


def _org_user(db):
    org = _make_organization(db, name=f"Org {uuid.uuid4().hex[:6]}")
    user = _make_user(db)
    _make_membership(db, user, org)
    return org, user


class TestRecording:
    def test_a_call_keeps_its_cache_billable_cost_and_organisation(self, db_session):
        from app.interpreter.llm_interpreter import _persist_llm_call

        org, user = _org_user(db_session)
        thread = _make_thread(db_session, user)
        call_id = _persist_llm_call(
            db_session,
            thread_id=thread.id,
            call_type="tool_use",
            model="claude-opus-5-5",
            system_prompt="s",
            user_prompt="u",
            raw_response="r",
            parsed_response=None,
            status="success",
            usage=Usage.from_response(_usage()),
        )
        row = db_session.get(LlmCall, call_id)
        assert row.input_tokens == 2 and row.cache_read_tokens == 100_000
        assert row.cache_write_tokens == 4_000 and row.organization_id == org.id
        assert row.billable_tokens == 10_502
        assert float(row.cost_usd) == pytest.approx(0.050008)
        day = (
            db_session.query(TokenUsage)
            .filter(TokenUsage.organization_id == org.id)
            .one()
        )
        assert day.cache_read_tokens == 100_000 and day.billable_tokens == 10_502
        assert usage_fields(row)["cost_usd"] == pytest.approx(0.050008)

    def test_a_call_with_no_thread_counts_to_the_callers_organisation(self, db_session):
        """An invoice extraction has no thread or user; it ran for a venue."""
        from app.interpreter.llm_interpreter import _persist_llm_call
        from app.services import caller_scope

        org, _ = _org_user(db_session)
        with caller_scope.use(caller_scope.Scope(org.id)):
            call_id = _persist_llm_call(
                db_session,
                thread_id=None,
                call_type="extraction",
                model="claude-opus-5-5",
                system_prompt="s",
                user_prompt="u",
                raw_response="r",
                parsed_response=None,
                status="success",
                usage=Usage(input=3_000, output=400),
            )
        assert db_session.get(LlmCall, call_id).organization_id == org.id
        day = (
            db_session.query(TokenUsage)
            .filter(TokenUsage.organization_id == org.id, TokenUsage.user_id.is_(None))
            .one()
        )
        assert day.input_tokens == 3_000 and day.billable_tokens == 3_400

    def test_a_reply_that_fails_to_parse_is_still_billed(self, monkeypatch):
        """89 summaries in a week were answered, billed, and saved as free errors."""
        from app.interpreter import llm_interpreter as li

        recorded = []
        monkeypatch.setattr(
            li, "_persist_llm_call_isolated", lambda **kw: recorded.append(kw)
        )
        monkeypatch.setattr(
            "app.services.secrets.get_api_key", lambda *a, **k: "sk-test"
        )
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="not json at all")],
            usage=_usage(cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(Exception):
                li.call_llm("sys", "user", model="claude-haiku-4-5", db=MagicMock())
        assert recorded and recorded[0]["status"] == "error"
        assert recorded[0]["usage"].output == 500

    def test_deleting_a_thread_keeps_its_calls(self, db_session):
        from app.interpreter.llm_interpreter import _persist_llm_call

        _, user = _org_user(db_session)
        thread = _make_thread(db_session, user)
        call_id = _persist_llm_call(
            db_session,
            thread_id=thread.id,
            call_type="tool_use",
            model="claude-opus-5-5",
            system_prompt="s",
            user_prompt="u",
            raw_response="r",
            parsed_response=None,
            status="success",
            usage=Usage(input=10, output=10),
        )
        db_session.refresh(thread)
        db_session.delete(thread)
        db_session.flush()
        row = db_session.get(LlmCall, call_id)
        assert row is not None and row.thread_id is None

    def test_a_direct_sdk_call_is_recorded(self, monkeypatch):
        from app.interpreter import llm_interpreter as li

        recorded = []
        monkeypatch.setattr(
            li, "_persist_llm_call_isolated", lambda **kw: recorded.append(kw)
        )
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"unit_id": "u1"}')],
            usage=_usage(cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )
        li.record_direct_call(
            response, model="claude-haiku-4-5", call_type="unit_guess"
        )
        assert recorded[0]["call_type"] == "unit_guess"
        assert recorded[0]["usage"].output == 500 and recorded[0]["status"] == "success"


class TestPlanLimits:
    def test_the_month_counts_billable_tokens(self, db_session):
        from app.services.billing_service import get_monthly_usage

        org, user = _org_user(db_session)
        today = dt.date.today().isoformat()
        db_session.add(
            TokenUsage(
                organization_id=org.id,
                user_id=user.id,
                date=today,
                input_tokens=2,
                output_tokens=500,
                cache_read_tokens=100_000,
                billable_tokens=5_502,
            )
        )
        older = (dt.date.today().replace(day=1)).isoformat()
        if older != today:
            # a day recorded before billable tokens existed counts input + output
            db_session.add(
                TokenUsage(
                    organization_id=org.id,
                    user_id=None,
                    date=older,
                    input_tokens=1_000,
                    output_tokens=200,
                    billable_tokens=None,
                )
            )
        db_session.flush()
        expected = 5_502 + (1_200 if older != today else 0)
        assert get_monthly_usage(db_session, org.id) == expected


class TestUsageApi:
    def _headers(self, user):
        from app.auth.security import create_access_token

        return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}

    def test_another_organisations_usage_is_refused(self, client, db_session):
        org, _ = _org_user(db_session)
        _, outsider = _org_user(db_session)
        for path in ("usage", "usage/daily", "usage/breakdown"):
            r = client.get(
                f"/api/organizations/{org.id}/{path}", headers=self._headers(outsider)
            )
            assert r.status_code == 403, path

    def test_the_breakdown_splits_by_kind_and_ranks_chats(self, client, db_session):
        org, user = _org_user(db_session)
        thread = _make_thread(db_session, user)
        thread.title = "Recipe import"
        now = dt.datetime.now(dt.timezone.utc)
        for call_type, cost, tid in (
            ("tool_use", 1.50, thread.id),
            ("tool_use", 0.50, thread.id),
            ("extraction", 0.25, None),
            ("title", 0.01, thread.id),
        ):
            db_session.add(
                LlmCall(
                    thread_id=tid,
                    organization_id=org.id,
                    call_type=call_type,
                    model="claude-opus-5-5",
                    system_prompt="",
                    user_prompt="",
                    status="success",
                    cost_usd=cost,
                    billable_tokens=1000,
                    cache_read_tokens=4000,
                    cache_write_tokens=1000,
                    created_at=now,
                )
            )
        db_session.flush()
        r = client.get(
            f"/api/organizations/{org.id}/usage/breakdown", headers=self._headers(user)
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["by_kind"]["chat"]["cost_usd"] == pytest.approx(2.0)
        assert body["by_kind"]["invoice extraction"]["calls"] == 1
        assert body["by_kind"]["other"]["calls"] == 1
        top = body["top_threads"][0]
        assert top["title"] == "Recipe import" and top["calls"] == 3
        assert top["cache_ratio"] == 4.0


class TestTheCachedPrefixHolds:
    def test_a_user_turn_goes_back_exactly_as_it_was_sent(self, db_session):
        """Replaying the bare text changed the prompt at the first message, so
        every turn rewrote the whole history into the cache."""
        from app.agents.context_builder import build_conversation_messages

        _, user = _org_user(db_session)
        thread = _make_thread(db_session, user)
        t0 = dt.datetime.now(dt.timezone.utc)
        first = Message(
            thread_id=thread.id, role="user", content="How were sales?", created_at=t0
        )
        db_session.add(first)
        db_session.flush()
        sent = build_conversation_messages(
            [first],
            "How were sales?",
            context={"venue": "La Zeppa"},
            thread=thread,
            db=db_session,
        )
        first_turn = sent[-1]["content"]
        assert "[Context]" in first_turn and first.sent_content == first_turn

        answer = Message(
            thread_id=thread.id,
            role="assistant",
            content="$6,110.",
            created_at=t0 + dt.timedelta(seconds=5),
        )
        second = Message(
            thread_id=thread.id,
            role="user",
            content="And last week?",
            created_at=t0 + dt.timedelta(seconds=9),
        )
        db_session.add_all([answer, second])
        db_session.flush()
        again = build_conversation_messages(
            [first, answer, second],
            "And last week?",
            context={"venue": "La Zeppa"},
            thread=thread,
            db=db_session,
        )
        assert again[0]["content"] == first_turn  # the same prefix as last time
        assert again[1]["content"] == "$6,110."

    def test_an_older_row_without_it_falls_back_to_its_text(self, db_session):
        from app.agents.context_builder import build_conversation_messages

        _, user = _org_user(db_session)
        thread = _make_thread(db_session, user)
        t0 = dt.datetime.now(dt.timezone.utc)
        rows = [
            Message(thread_id=thread.id, role="user", content="hi", created_at=t0),
            Message(
                thread_id=thread.id,
                role="assistant",
                content="hello",
                created_at=t0 + dt.timedelta(seconds=1),
            ),
        ]
        db_session.add_all(rows)
        db_session.flush()
        out = build_conversation_messages(rows, "next", thread=thread, db=db_session)
        assert out[0]["content"] == "hi"

    def test_the_wrap_up_keeps_the_tools_and_switches_tool_use_off(self, monkeypatch):
        """tools=[] changed the start of the prompt — the whole cached
        conversation was written again on every wrap-up."""
        from app.interpreter import llm_interpreter as li

        monkeypatch.setattr(
            "app.services.secrets.get_api_key", lambda *a, **k: "sk-test"
        )
        monkeypatch.setattr(
            "app.services.models.agent_model", lambda *a, **k: "claude-opus-5-5"
        )
        monkeypatch.setattr("app.agents.tool_loop._emit_event", lambda *a, **k: None)
        from tests.test_llm_retry import _FakeStreamCtx, _final, _text_delta

        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _FakeStreamCtx(events=[_text_delta("ok")], final=_final("ok"))
        ]
        tools = [
            {
                "name": "get_sales",
                "description": "d",
                "input_schema": {"type": "object"},
            }
        ]
        with patch("anthropic.Anthropic", return_value=client):
            li.call_llm_with_tools(
                system_prompt="s",
                messages=[{"role": "user", "content": "hi"}],
                tools=tools,
                tool_choice={"type": "none"},
                db=None,
            )
        kw = client.beta.messages.stream.call_args.kwargs
        assert kw["tool_choice"] == {"type": "none"}
        assert [t["name"] for t in kw["tools"]] == ["get_sales"]


class TestBackfill:
    LINES = [
        "2026-10-07T04:42:37.326800Z\t\x1b[1mprompt_size  \x1b[0m call_type=tool_use "
        "ctx_actual=2 ctx_cache_read=108666 ctx_cache_write=490 model=claude-opus-5-5 "
        "thread_id=t-1",
        "2026-10-06T20:00:09.169Z\tprompt_size_on_error call_type=tool_use "
        "ctx_total=23287 model=claude-opus-4-8 thread_id=t-1",
        "2026-10-07T04:42:20.972Z\tprompt_size call_type=extraction thread_id=t-1",
    ]

    def test_only_successful_agent_calls_are_read(self):
        import scripts.backfill_llm_usage as bf

        logs = bf.parse_log_lines(self.LINES)
        assert len(logs) == 1
        assert logs[0]["cache_read"] == 108_666 and logs[0]["input"] == 2

    def test_a_log_line_matches_its_call_by_thread_input_and_time(self):
        import scripts.backfill_llm_usage as bf

        logs = bf.parse_log_lines(self.LINES)
        at = logs[0]["at"]
        calls = [
            {
                "id": "right",
                "thread_id": "t-1",
                "created_at": at + dt.timedelta(seconds=1),
                "input_tokens": 2,
            },
            {
                "id": "other-thread",
                "thread_id": "t-2",
                "created_at": at,
                "input_tokens": 2,
            },
            {
                "id": "other-input",
                "thread_id": "t-1",
                "created_at": at,
                "input_tokens": 9,
            },
            {
                "id": "too-late",
                "thread_id": "t-1",
                "created_at": at + dt.timedelta(minutes=5),
                "input_tokens": 2,
            },
        ]
        assert set(bf.match(calls, logs)) == {"right"}
