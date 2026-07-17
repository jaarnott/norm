"""Tests for the generic consolidator/spec-executor infrastructure extensions:

- response_format: "binary" on spec tools (execute_http)
- consolidator write gate (allowed_write_actions, deny by default)
- configurable max_api_calls
- extract_document sandbox helper
"""

import base64
from types import SimpleNamespace

import httpx

from app.connectors.function_executor import execute_function
from app.connectors.spec_executor import RenderedRequest, execute_http


# ---------------------------------------------------------------------------
# Binary response format
# ---------------------------------------------------------------------------


class TestBinaryResponseFormat:
    def _respond(self, monkeypatch, content, content_type):
        def fake_request(**kwargs):
            return httpx.Response(
                200,
                content=content,
                headers={"content-type": content_type},
                request=httpx.Request("GET", kwargs.get("url", "https://x")),
            )

        monkeypatch.setattr(httpx, "request", lambda **kw: fake_request(**kw))

    def test_binary_tool_returns_base64(self, monkeypatch):
        pdf_bytes = b"%PDF-1.7 fake"
        self._respond(monkeypatch, pdf_bytes, "application/pdf")
        rendered = RenderedRequest(
            method="GET", url="https://api.example.com/f/1", headers={}, body=None
        )
        result = execute_http(rendered, {"response_format": "binary"})
        assert result.success
        payload = result.response_payload
        assert base64.b64decode(payload["content_base64"]) == pdf_bytes
        assert payload["content_type"] == "application/pdf"
        assert payload["size_bytes"] == len(pdf_bytes)

    def test_default_json_behavior_unchanged(self, monkeypatch):
        self._respond(monkeypatch, b'{"ok": true}', "application/json")
        rendered = RenderedRequest(
            method="GET", url="https://api.example.com/j", headers={}, body=None
        )
        result = execute_http(rendered, {})
        assert result.response_payload == {"ok": True}


# ---------------------------------------------------------------------------
# Consolidator sandbox: write gate, call cap, extract_document
# ---------------------------------------------------------------------------

FAKE_TOOLS = [
    {"action": "read_thing", "method": "GET", "path_template": "//x/read"},
    {"action": "write_thing", "method": "PUT", "path_template": "//x/write"},
    {
        "action": "download_thing",
        "method": "GET",
        "path_template": "//x/file",
        "response_format": "binary",
    },
]


def _wire_fake_connector(monkeypatch, payloads):
    """Point the executor's spec lookup + HTTP execution at fakes."""
    fake_spec = SimpleNamespace(
        connector_name="fake",
        tools=FAKE_TOOLS,
        base_url_template="https://",
        auth_type=None,
        auth_config=None,
        execution_mode="template",
    )

    class FakeQuery:
        def filter(self, *a, **k):
            return self

        def first(self):
            return fake_spec

    class FakeConfigSession:
        def query(self, *a, **k):
            return FakeQuery()

        def close(self):
            pass

    import app.db.engine as engine_mod
    import app.agents.tool_loop as tool_loop_mod
    import app.connectors.spec_executor as spec_mod

    monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", FakeConfigSession)
    monkeypatch.setattr(tool_loop_mod, "_resolve_venue_config", lambda *a, **k: None)

    calls = []

    def fake_execute_spec(
        spec, tool_def, params, credentials, db, thread_id, venue_id=None
    ):
        calls.append(tool_def["action"])
        payload = payloads.get(tool_def["action"], {"ok": True})
        return SimpleNamespace(
            success=True, response_payload=payload, error_message=None
        ), None

    monkeypatch.setattr(spec_mod, "execute_spec", fake_execute_spec)
    return calls


class TestWriteGate:
    def test_undeclared_write_action_is_denied(self, monkeypatch, db_session):
        _wire_fake_connector(monkeypatch, {})
        code = (
            "def run(params, call_api, log):\n"
            "    return call_api('fake', 'write_thing', {})\n"
        )
        result = execute_function(code, {}, db_session, None, options={})
        assert result["success"]
        assert "allowed_write_actions" in result["data"]["error"]

    def test_declared_write_action_executes(self, monkeypatch, db_session):
        calls = _wire_fake_connector(monkeypatch, {"write_thing": {"done": True}})
        code = (
            "def run(params, call_api, log):\n"
            "    return call_api('fake', 'write_thing', {})\n"
        )
        result = execute_function(
            code,
            {},
            db_session,
            None,
            options={"allowed_write_actions": ["write_thing"]},
        )
        assert result["data"] == {"done": True}
        assert calls == ["write_thing"]

    def test_reads_never_need_declaration(self, monkeypatch, db_session):
        _wire_fake_connector(monkeypatch, {"read_thing": {"v": 1}})
        code = (
            "def run(params, call_api, log):\n"
            "    return call_api('fake', 'read_thing', {})\n"
        )
        result = execute_function(code, {}, db_session, None, options={})
        assert result["data"] == {"v": 1}


class TestCallCap:
    def test_configured_cap_is_enforced(self, monkeypatch, db_session):
        _wire_fake_connector(monkeypatch, {})
        code = (
            "def run(params, call_api, log):\n"
            "    for _ in range(5):\n"
            "        call_api('fake', 'read_thing', {})\n"
            "    return {'ok': True}\n"
        )
        result = execute_function(
            code, {}, db_session, None, options={"max_api_calls": 3}
        )
        assert not result["success"]
        assert "max 3" in result["error"]

    def test_default_cap_still_twenty(self, monkeypatch, db_session):
        _wire_fake_connector(monkeypatch, {})
        code = (
            "def run(params, call_api, log):\n"
            "    for _ in range(21):\n"
            "        call_api('fake', 'read_thing', {})\n"
            "    return {'ok': True}\n"
        )
        result = execute_function(code, {}, db_session, None)
        assert not result["success"]
        assert "max 20" in result["error"]

    def test_hard_ceiling_bounds_config(self, monkeypatch, db_session):
        _wire_fake_connector(monkeypatch, {})
        code = "def run(params, call_api, log):\n    return {'ok': True}\n"
        # absurd config value is clamped — just verify execution still works
        result = execute_function(
            code, {}, db_session, None, options={"max_api_calls": 10_000}
        )
        assert result["success"]


class TestExtractDocument:
    def test_extracts_via_llm(self, monkeypatch, db_session):
        pdf_b64 = base64.b64encode(b"%PDF- fake").decode()
        _wire_fake_connector(
            monkeypatch,
            {
                "download_thing": {
                    "content_base64": pdf_b64,
                    "content_type": "application/pdf",
                }
            },
        )

        seen = {}

        def fake_call_llm(**kwargs):
            seen.update(kwargs)
            return {"total_incl_tax": 260.23}, None

        import app.interpreter.llm_interpreter as llm_mod

        monkeypatch.setattr(llm_mod, "call_llm", fake_call_llm)

        code = (
            "def run(params, call_api, log):\n"
            "    return extract_document('fake', 'download_thing', {}, schema={'total_incl_tax': 'number'})\n"
        )
        result = execute_function(code, {}, db_session, None)
        assert result["data"] == {"total_incl_tax": 260.23}
        assert seen["call_type"] == "extraction"
        doc = seen["documents"][0]
        assert doc["source"]["data"] == pdf_b64
        assert doc["source"]["media_type"] == "application/pdf"

    def test_non_binary_payload_is_an_error(self, monkeypatch, db_session):
        _wire_fake_connector(monkeypatch, {"read_thing": {"not": "binary"}})
        code = (
            "def run(params, call_api, log):\n"
            "    return extract_document('fake', 'read_thing', {}, schema={})\n"
        )
        result = execute_function(code, {}, db_session, None)
        assert "error" in result["data"]
        assert "binary" in result["data"]["error"]

    def test_counts_toward_call_cap(self, monkeypatch, db_session):
        pdf_b64 = base64.b64encode(b"x").decode()
        _wire_fake_connector(
            monkeypatch,
            {
                "download_thing": {
                    "content_base64": pdf_b64,
                    "content_type": "application/pdf",
                }
            },
        )
        import app.interpreter.llm_interpreter as llm_mod

        monkeypatch.setattr(llm_mod, "call_llm", lambda **kw: ({}, None))
        code = (
            "def run(params, call_api, log):\n"
            "    extract_document('fake', 'download_thing', {}, schema={})\n"
            "    extract_document('fake', 'download_thing', {}, schema={})\n"
            "    return {'ok': True}\n"
        )
        result = execute_function(
            code, {}, db_session, None, options={"max_api_calls": 1}
        )
        assert not result["success"]
        assert "max 1" in result["error"]
