"""Fields passed via `extra=` must survive into the rendered log.

Why this exists: `structlog.stdlib.ProcessorFormatter` drops `extra={...}` from
plain stdlib loggers unless `ExtraAdder` is in its processor chain. It wasn't.
Fields passed to a *structlog* logger as kwargs survived, which is exactly why
the loss went unnoticed — `request_complete` printed its fields happily while
`mcp_tool_call` printed nothing but its own name, in production, for as long as
it has existed.

That makes this a telemetry bug rather than a formatting one: the numbers the
MCP surface and the context budget exist to expose were never reaching Cloud
Logging.
"""

import json
import logging

import pytest

from app.logging_config import setup_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    setup_logging()


def _emit(capsys, fmt, event, fields):
    from app.config import settings

    original = settings.LOG_FORMAT
    try:
        settings.LOG_FORMAT = fmt
        setup_logging()
        logging.getLogger("test.extras").info(event, extra=fields)
    finally:
        settings.LOG_FORMAT = original
    return capsys.readouterr().out


def test_json_output_carries_every_extra_field(capsys):
    """This is the one that matters — JSON is what production ships."""
    out = _emit(
        capsys, "json", "mcp_tool_call",
        {"mcp_tool": "loadedhub__get_sales_for_period", "duration_ms": 412},
    )
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["event"] == "mcp_tool_call"
    assert payload["mcp_tool"] == "loadedhub__get_sales_for_period"
    assert payload["duration_ms"] == 412


def test_context_budget_fields_survive(capsys):
    """The prompt-size breakdown is the acceptance criterion for the context
    work. If it does not reach the logs, none of it can be measured."""
    out = _emit(
        capsys, "json", "prompt_size",
        {"ctx_total": 14122, "ctx_tools": 7354, "ctx_cache_read": 10242},
    )
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ctx_total"] == 14122
    assert payload["ctx_cache_read"] == 10242


def test_console_output_shows_them_too(capsys):
    out = _emit(capsys, "console", "prompt_size", {"ctx_total": 999})
    assert "ctx_total" in out and "999" in out
