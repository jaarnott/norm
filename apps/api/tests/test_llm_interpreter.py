"""Tests for the LLM helper (call_llm / _parse_response)."""

import json
import pytest

from app.interpreter.llm_interpreter import _parse_response


# -- Response parsing tests --


class TestResponseParsing:
    def test_parses_clean_json(self):
        raw = '{"domain": "procurement", "intent": "procurement.order"}'
        result = _parse_response(raw)
        assert result["domain"] == "procurement"

    def test_parses_markdown_fenced_json(self):
        raw = '```json\n{"domain": "hr"}\n```'
        result = _parse_response(raw)
        assert result["domain"] == "hr"

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_response("not json at all")

    def test_parses_json_after_prose_preamble(self):
        # Observed live (stock-item matcher, 08 Aug 2026): the model thinks
        # aloud before the JSON; the strict parse silently degraded a correct
        # Sailor Jerry match to "NEW item".
        raw = (
            "Looking at the invoice line, this matches index 203.\n\n"
            '{"matches": [{"line_id": "ln-1", "match_index": 203}]}'
        )
        assert _parse_response(raw)["matches"][0]["match_index"] == 203

    def test_leading_quoted_phrase_is_not_the_answer(self):
        # The nastier live shape: the preamble STARTS with a quoted product
        # name — a bare-string parse at position 0 must not win ("Extra
        # data: line 1 column 27").
        raw = (
            '"Sailor Jerry Spiced Rum" — same brand as catalogue index 203.\n\n'
            '{"matches": [{"line_id": "ln-1", "match_index": 203}]}'
        )
        assert _parse_response(raw)["matches"][0]["match_index"] == 203

    def test_prose_with_braces_before_the_object(self):
        # A brace inside prose that never closes must not stop the scan.
        raw = 'The shape { is JSON-like; answer: {"ok": true}'
        assert _parse_response(raw) == {"ok": True}

    def test_bare_array_after_prose(self):
        raw = 'Here are the rows:\n[{"id": 1}]'
        assert _parse_response(raw) == [{"id": 1}]

    def test_prose_with_no_json_still_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_response("I could not produce a result this time.")
