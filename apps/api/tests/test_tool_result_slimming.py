"""Per-tool result-size budget for the LLM context (`max_result_chars`).

Audit-style tools (invoice review/reconciliation) return reports the agent
must relay in full; a tool may raise its slim threshold via `max_result_chars`
on the tool definition, clamped to HARD_MAX_TOOL_RESULT_CHARS.
"""

import json

from app.agents.tool_loop import (
    HARD_MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_RESULT_CHARS,
    _slim_tool_result,
    _tool_max_result_chars,
    _without_card_bodies,
)


class TestToolMaxResultChars:
    def test_no_tool_def_uses_default(self):
        assert _tool_max_result_chars(None) == MAX_TOOL_RESULT_CHARS

    def test_absent_key_uses_default(self):
        assert _tool_max_result_chars({"action": "x"}) == MAX_TOOL_RESULT_CHARS

    def test_override_honoured(self):
        assert _tool_max_result_chars({"max_result_chars": 100_000}) == 100_000

    def test_clamped_to_hard_ceiling(self):
        assert (
            _tool_max_result_chars({"max_result_chars": 10_000_000})
            == HARD_MAX_TOOL_RESULT_CHARS
        )

    def test_never_below_default(self):
        assert _tool_max_result_chars({"max_result_chars": 5}) == MAX_TOOL_RESULT_CHARS

    def test_garbage_value_uses_default(self):
        assert (
            _tool_max_result_chars({"max_result_chars": "lots"})
            == MAX_TOOL_RESULT_CHARS
        )
        assert _tool_max_result_chars({"max_result_chars": None}) == (
            MAX_TOOL_RESULT_CHARS
        )


class TestSlimRespectsBudget:
    def test_result_within_raised_budget_passes_through_verbatim(self):
        # ~60k chars: over the 30k default, under a 100k override
        payload = {"data": [{"i": i, "pad": "x" * 50} for i in range(1000)]}
        assert len(json.dumps(payload)) > MAX_TOOL_RESULT_CHARS
        out = _slim_tool_result(payload, "tc-1", max_chars=100_000)
        assert json.loads(out) == payload

    def test_result_over_raised_budget_still_slims(self):
        payload = {"data": [{"i": i, "pad": "x" * 200} for i in range(1000)]}
        out = _slim_tool_result(payload, "tc-1", max_chars=100_000)
        assert '"_too_large": true' in out


class TestCardBodiesNeverReachTheModel:
    """The card carries an id; the model carries the summary.

    A fan-out tool's ``working_document.items_path`` holds one COMPLETE invoice
    document per card, and the card built from it is only
    ``{"working_document_id": id}`` — the browser fetches the body. So the
    bodies are pure weight in the model's copy, and leaving them there did real
    harm: on thread 8a270c60 (26 Aug 2026) `fix_invoices` was 62k of a 63k
    result, which tripped the size cap; the cap's array branch then kept the
    nested suggestions and discarded `reviewed`, `received` and `skipped` — the
    very numbers the playbook asks the model to report. It was handed one
    sample suggestion and told to search, made 13 search calls (~10 per run
    over a fortnight), and retyped as a table what the card was already showing
    with Accept buttons beside it.
    """

    TOOL_DEF = {"working_document": {"items_path": "fix_invoices"}}

    @staticmethod
    def _envelope(payload):
        """EXACTLY what `_execute_tool_call` hands the slim call site. The bare
        payload is what `tc.result_payload` holds and what builds the cards —
        they are NOT interchangeable, and testing only the bare shape is how
        this shipped as a no-op (thread 9e71aa33, 27 Aug 2026)."""
        return {
            "success": True,
            "data": payload,
            "reference": None,
            "error": None,
            "auth_failed": False,
        }

    def _result(self, cards=2):
        card = {
            "invoice_id": "inv-1",
            "lines": [{"description": "X" * 200} for _ in range(30)],
            "suggestions": [{"kind": "line_value", "proposed": 555.75}] * 5,
        }
        return {
            "venue": "The Glass Goose",
            "reviewed": 12,
            "summary": {"received": 1, "skipped": 11},
            "received": [{"reference": "INV1", "outcome": "awaiting your approval"}],
            "skipped": [{"reference": "INV2", "reason": "unit missing"}],
            "fix_invoices": [dict(card) for _ in range(cards)],
        }

    def test_the_bodies_are_gone_from_the_model_copy(self):
        out = _without_card_bodies(self._result(), self.TOOL_DEF)
        assert "fix_invoices" not in out

    def test_the_summary_survives_whole(self):
        """What the model narrates: the counts and the per-invoice reasons."""
        out = _without_card_bodies(self._result(), self.TOOL_DEF)
        assert out["reviewed"] == 12
        assert out["summary"] == {"received": 1, "skipped": 11}
        assert out["skipped"][0]["reason"] == "unit missing"

    def test_it_still_knows_how_many_cards_are_below(self):
        """Dropping them silently would leave the model unable to say "one card
        below" without going and looking — the exact round trip this removes."""
        out = _without_card_bodies(self._result(cards=3), self.TOOL_DEF)
        assert out["fix_invoices_count"] == 3

    def test_the_original_is_untouched_so_the_cards_still_build(self):
        """The fan-out reads tc.result_payload AFTER this. If this stripped in
        place, every Receive Invoice card would vanish."""
        result = self._result()
        _without_card_bodies(result, self.TOOL_DEF)
        assert len(result["fix_invoices"]) == 2
        assert result["fix_invoices"][0]["suggestions"]

    def test_it_now_fits_the_budget_instead_of_tripping_the_cap(self):
        """The regression in one assertion: the same result that produced a
        `_too_large` stub now slims to nothing at all."""
        result = self._result(cards=6)
        before = _slim_tool_result(result, "tc-1", max_chars=4000)
        after = _slim_tool_result(
            _without_card_bodies(result, self.TOOL_DEF), "tc-1", max_chars=4000
        )
        assert "_too_large" in before
        assert "_too_large" not in after
        assert json.loads(after)["reviewed"] == 12

    def test_a_tool_with_no_fan_out_is_left_alone(self):
        payload = {"items": [{"a": 1}], "total": 1}
        assert _without_card_bodies(payload, {"action": "get_sales"}) == payload
        assert _without_card_bodies(payload, None) == payload

    def test_a_missing_or_odd_items_path_is_left_alone(self):
        """Never guess: if the named key is absent or isn't a list, hand the
        result over exactly as it came."""
        assert _without_card_bodies({"a": 1}, self.TOOL_DEF) == {"a": 1}
        odd = {"fix_invoices": {"not": "a list"}}
        assert _without_card_bodies(odd, self.TOOL_DEF) == odd
        assert _without_card_bodies("not a dict", self.TOOL_DEF) == "not a dict"


class TestItStripsTheShapeProductionActuallyPasses:
    """The regression that made the first attempt a no-op.

    `_execute_tool_call` returns an ENVELOPE — {"success", "data", "reference",
    "error", "auth_failed"} — and `data` is the payload holding `fix_invoices`.
    The working document and the cards are built from `tc.result_payload`,
    which is that bare `data`. Checking only the top level therefore matched
    nothing: 335,982 chars went to the model as a "too large, go and search"
    stub, while every test — written against the bare payload — passed.
    """

    TOOL_DEF = {"working_document": {"items_path": "fix_invoices"}}

    def _payload(self, cards=23):
        return {
            "venue": "Freeman & Grey",
            "reviewed": cards,
            "summary": {"received": 0, "skipped": cards},
            "skipped": [
                {"reference": f"INV{n}", "reason": "unit missing"} for n in range(14)
            ],
            "fix_invoices": [
                {"invoice_id": f"inv-{n}", "lines": [{"d": "X" * 400}] * 20}
                for n in range(cards)
            ],
        }

    def _envelope(self, payload):
        return {
            "success": True,
            "data": payload,
            "reference": None,
            "error": None,
            "auth_failed": False,
        }

    def test_the_bodies_are_stripped_from_inside_data(self):
        out = _without_card_bodies(self._envelope(self._payload()), self.TOOL_DEF)
        assert "fix_invoices" not in out["data"]
        assert out["data"]["fix_invoices_count"] == 23

    def test_the_envelope_itself_is_preserved(self):
        """The caller reads success/auth_failed off it."""
        out = _without_card_bodies(self._envelope(self._payload()), self.TOOL_DEF)
        assert out["success"] is True and out["auth_failed"] is False

    def test_the_summary_inside_data_survives(self):
        out = _without_card_bodies(self._envelope(self._payload()), self.TOOL_DEF)
        assert out["data"]["reviewed"] == 23
        assert len(out["data"]["skipped"]) == 14

    def test_the_original_payload_is_untouched(self):
        """tc.result_payload IS this dict — the cards are built from it after."""
        payload = self._payload()
        _without_card_bodies(self._envelope(payload), self.TOOL_DEF)
        assert len(payload["fix_invoices"]) == 23

    def test_the_real_size_no_longer_trips_the_cap(self):
        """Production's numbers: 335k in, well under budget out, no stub."""
        env = self._envelope(self._payload())
        assert len(json.dumps(env)) > 150_000
        before = _slim_tool_result(env, "tc", max_chars=60_000)
        after = _slim_tool_result(
            _without_card_bodies(env, self.TOOL_DEF), "tc", max_chars=60_000
        )
        assert "_too_large" in before
        assert "_too_large" not in after
        assert json.loads(after)["data"]["reviewed"] == 23

    def test_a_bare_payload_still_works(self):
        """Some callers pass the unwrapped payload; both shapes are handled."""
        out = _without_card_bodies(self._payload(), self.TOOL_DEF)
        assert "fix_invoices" not in out and out["fix_invoices_count"] == 23

    def test_an_envelope_for_a_non_fan_out_tool_is_left_alone(self):
        env = self._envelope({"items": [{"a": 1}]})
        assert _without_card_bodies(env, {"action": "get_sales"}) == env
