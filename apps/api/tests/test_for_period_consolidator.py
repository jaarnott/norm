"""Tests for the shared `for_period` consolidator function_code.

Same harness as the other consolidator tests: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, with a
scriptable fake for call_api.

What these tools exist to prevent: sales for "yesterday" were computed
midnight-to-midnight, so a late-night venue's post-midnight trade fell outside
the window and it read $0 for a Saturday — which looks like a POS outage rather
than a bad window. One implementation fronts every date-taking action, so the
behaviour is pinned once here rather than per tool.
"""

import pathlib

import pytest

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "for_period.py"
).read_text(encoding="utf-8")

TRADING = {
    "start": "2026-07-18T07:00:00+12:00",
    "end": "2026-07-19T06:59:59+12:00",
    "kind": "trading_day",
    "label": "Yesterday",
    "timezone": "Pacific/Auckland",
    "day_start": "07:00",
    "trading_aligned": True,
    "description": "Yesterday — Sat 18 Jul 07:00 → Sun 19 Jul 06:59 Pacific/Auckland",
}
CIVIL = dict(
    TRADING,
    start="2026-07-18T00:00:00+12:00",
    end="2026-07-19T00:00:00+12:00",
    kind="custom",
    label="Custom window",
    trading_aligned=False,
    description=(
        "Custom window — Sat 18 Jul 00:00 → Sun 19 Jul 00:00 Pacific/Auckland. "
        "Not a trading day: the venue's day starts at 07:00, so this splits a "
        "trading session."
    ),
)

SALES_OPTS = {
    "wraps": "get_sales_data",
    "start_param": "start_datetime",
    "end_param": "end_datetime",
}


class Api:
    def __init__(
        self, window=TRADING, payload=None, resolve_error=None, wrapped_error=None
    ):
        self.window = window
        self.payload = payload if payload is not None else {"total": 15945}
        self.resolve_error = resolve_error
        self.wrapped_error = wrapped_error
        self.calls = []

    def call_api(self, connector, action, params=None):
        self.calls.append((action, params or {}))
        if action == "resolve_dates":
            if self.resolve_error:
                return {"error": self.resolve_error}
            return {"success": True, "data": {"window": self.window}}
        if self.wrapped_error:
            return {"error": self.wrapped_error}
        return self.payload

    def actions(self):
        return [a for a, _ in self.calls]

    def params_for(self, action):
        return next(p for a, p in self.calls if a == action)


def run_consolidator(api, options=None, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, namespace)
    return namespace["run"](
        {"venue": "La Zeppa", **params},
        api.call_api,
        lambda m: None,
        None,
        {**SALES_OPTS, **(options or {})},
    )


class TestPeriodPath:
    def test_period_resolves_through_norms_calendar(self):
        api = Api()
        run_consolidator(api, period="yesterday", venue_id="v1")
        sent = api.params_for("resolve_dates")
        assert sent["query"] == "yesterday"
        assert sent["venue_id"] == "v1"  # so the venue's own day start applies

    def test_result_always_states_the_window_used(self):
        """The echo. A $0 venue is only diagnosable if you can see the window."""
        result = run_consolidator(Api(), period="yesterday")
        assert result["window"]["description"] == TRADING["description"]
        assert result["data"] == {"total": 15945}

    def test_missing_period_and_range_is_refused_without_fetching(self):
        api = Api()
        result = run_consolidator(api)
        assert "plain English" in result["error"]
        assert api.actions() == []


class TestParamMapping:
    """The wrapped actions name their window seven different ways. One config
    entry per tool maps it, so a caller learns one shape."""

    @pytest.mark.parametrize(
        "wraps,sp,ep",
        [
            ("get_sales_data", "start_datetime", "end_datetime"),
            ("get_pos_orders", "start", "end"),
            ("get_pos_item_sales", "start_time", "end_time"),
            ("get_received_invoices", "from", "to"),
            ("list_supplier_statements", "from_iso", "to_iso"),
            ("get_completed_stocktakes", "start_date", "end_date"),
        ],
    )
    def test_window_lands_on_the_wrapped_actions_param_names(self, wraps, sp, ep):
        api = Api()
        run_consolidator(
            api,
            options={"wraps": wraps, "start_param": sp, "end_param": ep},
            period="yesterday",
        )
        assert wraps in api.actions()
        sent = api.params_for(wraps)
        assert sent[sp] == TRADING["start"]
        assert sent[ep] == TRADING["end"]

    def test_other_arguments_are_forwarded(self):
        """Wrapped actions have their own required params (interval,
        posIdentifier, flags) — fronting must not swallow them."""
        api = Api()
        run_consolidator(
            api, period="yesterday", interval="1.00:00:00", posIdentifier="pos-7"
        )
        sent = api.params_for("get_sales_data")
        assert sent["interval"] == "1.00:00:00"
        assert sent["posIdentifier"] == "pos-7"
        assert sent["venue"] == "La Zeppa"

    def test_consumed_params_are_not_forwarded(self):
        api = Api()
        run_consolidator(api, period="yesterday", venue_id="v1")
        sent = api.params_for("get_sales_data")
        for key in ("period", "venue_id", "confirmed_by_user"):
            assert key not in sent

    def test_misconfiguration_is_reported_not_guessed(self):
        api = Api()
        result = run_consolidator(api, options={"wraps": None}, period="yesterday")
        assert "Misconfigured" in result["error"]
        assert api.actions() == []


class TestDeviation:
    def test_deviating_range_asks_before_fetching(self):
        api = Api(window=CIVIL)
        result = run_consolidator(
            api, start="2026-07-18T00:00:00+12:00", end="2026-07-19T00:00:00+12:00"
        )
        assert result["needs_confirmation"] is True
        assert "get_sales_data" not in api.actions()

    def test_the_question_is_answerable_from_fact(self):
        """Asking 'is this right?' invites agreement and would launder a mistake
        as confirmed. Ask what the user actually said instead."""
        result = run_consolidator(
            Api(window=CIVIL),
            start="2026-07-18T00:00:00+12:00",
            end="2026-07-19T00:00:00+12:00",
        )
        assert "explicitly ask" in result["question"]
        assert "confirmed_by_user=true" in result["question"]

    def test_confirmed_deviating_range_is_honoured_verbatim(self):
        """Reconciling against a bank statement legitimately wants civil days."""
        api = Api(window=CIVIL)
        run_consolidator(
            api,
            start="2026-07-18T00:00:00+12:00",
            end="2026-07-19T00:00:00+12:00",
            confirmed_by_user=True,
        )
        sent = api.params_for("get_sales_data")
        assert sent["start_datetime"] == CIVIL["start"]

    def test_aligned_explicit_range_needs_no_confirmation(self):
        api = Api(window=TRADING)
        result = run_consolidator(api, start=TRADING["start"], end=TRADING["end"])
        assert "needs_confirmation" not in result
        assert "get_sales_data" in api.actions()


class TestFailures:
    def test_resolver_error_fetches_nothing(self):
        api = Api(resolve_error="timezone lookup failed")
        result = run_consolidator(api, period="yesterday")
        assert "Could not resolve the period" in result["error"]
        assert "get_sales_data" not in api.actions()

    def test_unresolvable_phrase_suggests_a_simpler_one(self):
        api = Api(window=None)
        result = run_consolidator(api, period="the week before the long weekend")
        assert "yesterday" in result["error"]
        assert "get_sales_data" not in api.actions()

    def test_wrapped_error_still_reports_the_window(self):
        api = Api(wrapped_error="LoadedHub 500")
        result = run_consolidator(api, period="yesterday")
        assert result["error"] == "LoadedHub 500"
        assert result["window"]["description"] == TRADING["description"]


class TestSandboxSafety:
    def test_runs_without_imports(self):
        assert "\nimport " not in FUNCTION_CODE
        assert "\nfrom " not in FUNCTION_CODE

    def test_every_registered_tool_declares_no_writes(self):
        """These are declared GET and so bypass the approval gate. Wrapping a
        write with this pattern would route around it."""
        import importlib.util

        spec_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "scripts"
            / "sync_for_period_config.py"
        )
        spec = importlib.util.spec_from_file_location("sync_for_period", spec_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for action, wraps, sp, ep, returns in module.WRAPPED:
            tool = module.tool_for(action, wraps, sp, ep, returns, "")
            assert tool["method"] == "GET"
            assert tool["consolidator_config"]["allowed_write_actions"] == []
