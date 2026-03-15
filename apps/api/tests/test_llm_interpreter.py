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
