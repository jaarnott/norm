"""Tests for the sales_for_period consolidator function_code.

Same harness as the other consolidator tests: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, with a
scriptable fake for call_api.

What this tool exists to prevent: sales for "yesterday" were computed
midnight-to-midnight, so a late-night venue's post-midnight trade fell outside
the window and it read $0 for a Saturday — which looks like a POS outage rather
than a bad window. These tests pin the behaviour that makes that impossible to
reach by accident, and possible to see when it is asked for deliberately.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "sales_for_period.py"
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
CIVIL = {
    "start": "2026-07-18T00:00:00+12:00",
    "end": "2026-07-19T00:00:00+12:00",
    "kind": "custom",
    "label": "Custom window",
    "timezone": "Pacific/Auckland",
    "day_start": "07:00",
    "trading_aligned": False,
    "description": (
        "Custom window — Sat 18 Jul 00:00 → Sun 19 Jul 00:00 Pacific/Auckland. "
        "Not a trading day: the venue's day starts at 07:00, so this splits a "
        "trading session."
    ),
}


class Api:
    def __init__(
        self, window=TRADING, sales=None, resolve_error=None, sales_error=None
    ):
        self.window = window
        self.sales = sales if sales is not None else {"total": 15945}
        self.resolve_error = resolve_error
        self.sales_error = sales_error
        self.calls = []  # (action, params)

    def call_api(self, connector, action, params=None):
        params = params or {}
        self.calls.append((action, params))
        if action == "resolve_dates":
            if self.resolve_error:
                return {"error": self.resolve_error}
            return {"success": True, "data": {"window": self.window, "periods": []}}
        if action == "get_sales_data":
            if self.sales_error:
                return {"error": self.sales_error}
            return self.sales
        raise AssertionError(f"unexpected action {action}")

    def actions(self):
        return [a for a, _ in self.calls]

    def params_for(self, action):
        return next(p for a, p in self.calls if a == action)


def run_consolidator(api, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, namespace)
    return namespace["run"](
        {"venue": "La Zeppa", **params}, api.call_api, lambda m: None
    )


class TestPeriodPath:
    def test_period_resolves_through_norms_calendar(self):
        api = Api()
        run_consolidator(api, period="yesterday", venue_id="v1")
        assert api.params_for("resolve_dates")["query"] == "yesterday"
        # The venue goes with it, so a venue's own day_start_time applies.
        assert api.params_for("resolve_dates")["venue_id"] == "v1"

    def test_sales_are_fetched_for_the_resolved_window(self):
        api = Api()
        run_consolidator(api, period="yesterday")
        sent = api.params_for("get_sales_data")
        assert sent["start_datetime"] == TRADING["start"]
        assert sent["end_datetime"] == TRADING["end"]

    def test_result_always_states_the_window_used(self):
        """The echo. A $0 venue is only diagnosable if you can see the window
        it was measured over."""
        result = run_consolidator(Api(), period="yesterday")
        assert result["window"]["description"] == TRADING["description"]
        assert result["sales"] == {"total": 15945}

    def test_missing_period_and_range_is_refused(self):
        api = Api()
        result = run_consolidator(api)
        assert "error" in result
        assert "plain English" in result["error"]
        assert api.actions() == []  # nothing fetched


class TestExplicitRangeDeviation:
    def test_deviating_range_asks_before_fetching(self):
        """The whole point: no data comes back for a window that isn't a
        trading day until someone confirms it was actually asked for."""
        api = Api(window=CIVIL)
        result = run_consolidator(
            api, start="2026-07-18T00:00:00+12:00", end="2026-07-19T00:00:00+12:00"
        )
        assert result["needs_confirmation"] is True
        assert "get_sales_data" not in api.actions()
        assert result["window"]["trading_aligned"] is False

    def test_the_question_is_answerable_from_fact(self):
        """Asking 'is this right?' invites agreement and would launder a
        mistake as confirmed. Ask what the user actually said instead."""
        result = run_consolidator(
            Api(window=CIVIL),
            start="2026-07-18T00:00:00+12:00",
            end="2026-07-19T00:00:00+12:00",
        )
        question = result["question"]
        assert "explicitly ask" in question
        assert "confirmed_by_user=true" in question  # how to proceed
        assert "period" in question  # how to correct

    def test_confirmed_deviating_range_is_honoured_verbatim(self):
        """Reconciling against a bank statement legitimately wants civil days.
        Never snapped."""
        api = Api(window=CIVIL)
        result = run_consolidator(
            api,
            start="2026-07-18T00:00:00+12:00",
            end="2026-07-19T00:00:00+12:00",
            confirmed_by_user=True,
        )
        sent = api.params_for("get_sales_data")
        assert sent["start_datetime"] == CIVIL["start"]
        assert sent["end_datetime"] == CIVIL["end"]
        # And it still says the window wasn't a trading day.
        assert result["window"]["trading_aligned"] is False

    def test_aligned_explicit_range_needs_no_confirmation(self):
        api = Api(window=TRADING)
        result = run_consolidator(api, start=TRADING["start"], end=TRADING["end"])
        assert "needs_confirmation" not in result
        assert "get_sales_data" in api.actions()


class TestFailures:
    def test_resolver_error_surfaces_and_fetches_nothing(self):
        api = Api(resolve_error="timezone lookup failed")
        result = run_consolidator(api, period="yesterday")
        assert "Could not resolve the period" in result["error"]
        assert "get_sales_data" not in api.actions()

    def test_unresolvable_phrase_suggests_a_simpler_one(self):
        api = Api(window=None)
        result = run_consolidator(api, period="the week before the long weekend")
        assert "Could not resolve" in result["error"]
        assert "yesterday" in result["error"]
        assert "get_sales_data" not in api.actions()

    def test_sales_error_still_reports_the_window(self):
        api = Api(sales_error="LoadedHub 500")
        result = run_consolidator(api, period="yesterday")
        assert result["error"] == "LoadedHub 500"
        assert result["window"]["description"] == TRADING["description"]


class TestSandboxSafety:
    def test_declares_no_writes(self):
        """This pattern wraps reads only. Consolidators are declared GET and so
        bypass the approval gate — wrapping a write here would route around it."""
        assert "allowed_write_actions" in FUNCTION_CODE
        assert '"allowed_write_actions": []' in FUNCTION_CODE

    def test_runs_without_imports(self):
        assert "\nimport " not in FUNCTION_CODE
        assert "\nfrom " not in FUNCTION_CODE
