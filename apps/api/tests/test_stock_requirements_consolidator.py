"""Tests for the calculate_template_stock_requirements consolidator.

Same harness as the other consolidator tests: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, so CI runs the
exact code production runs.

The regression these lock down: LoadedHub's stock-on-hand report is slow (~13s in
LoadedHub's own Predicted Order page) and it 500s past their ~30s server limit
under load. When that happened, call_api_parallel returned {"error": ...} for
those calls, the function read .get("lines", []) off the error dict, and reported
"0 items need reordering out of 0 total" — so the agent told the user the
template "may be empty or unconfigured". The Beverage template actually had 309
items. An upstream outage was reported as a data problem.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "calculate_template_stock_requirements.py"
).read_text(encoding="utf-8")

PARAMS = {
    "venue": "The Glass Goose",
    "template_id": "f2d69809-2235-4d99-825d-de5728c902a5",
    "today": "2026-07-17",
    "today_iso": "2026-07-17T00:00:00%2B12:00",
    "four_weeks_ago_iso": "2026-06-19T00:00:00%2B12:00",
    "order_until_date": "2026-08-03",
}

# Shapes captured from the live API (17 Jul 2026): stock-on-hand returns
# {"generatedAt", "reportAt", "lines"[...]}, post-transform ids are stockItemID.
STOCK_NOW = {
    "lines": [
        {
            "stockItemID": "i1",
            "itemName": "Jim Beam 700ml",
            "quantityOnHand": 4.0,
            "countingUnitName": "bottle",
            "Category": "Spirits",
        },
    ]
}
STOCK_4W = {"lines": [{"stockItemID": "i1", "quantityOnHand": 10.0}]}
RECEIVED = []
SALES = [{"amount": 100000}]
BUDGETS = [{"amount": 200000}]

API_ERROR = {
    "error": 'API error 500: {"code":500,"description":"Something went wrong"}'
}


class Api:
    """Scriptable call_api / call_api_parallel."""

    def __init__(
        self,
        stock_now=STOCK_NOW,
        stock_4w=STOCK_4W,
        received=RECEIVED,
        sales=SALES,
        budgets=BUDGETS,
        retry_stock=None,
    ):
        self.stock_now = stock_now
        self.stock_4w = stock_4w
        self.received = received
        self.sales = sales
        self.budgets = budgets
        self.retry_stock = retry_stock  # what a serial retry returns, if set
        self.retries = 0
        self.logs = []

    def _for(self, action, params):
        if action == "get_stock_on_hand":
            today = params.get("report_datetime") == PARAMS["today_iso"]
            return self.stock_now if today else self.stock_4w
        if action == "get_received_invoices":
            return self.received
        if action == "get_sales_data":
            return self.sales
        if action == "get_budgets":
            return self.budgets
        raise AssertionError(f"unexpected action {action}")

    def call_api(self, connector, action, params=None):
        # Only the retry path uses single calls in this consolidator.
        if action == "get_stock_on_hand":
            self.retries += 1
            if self.retry_stock is not None:
                return self.retry_stock
        return self._for(action, params or {})

    def call_api_parallel(self, calls):
        return [self._for(a, p or {}) for (_c, a, p) in calls]

    def log(self, m):
        self.logs.append(str(m))


def run_fn(api):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, ns)
    return ns["run"](PARAMS, api.call_api, api.log, api.call_api_parallel)


class TestHappyPath:
    def test_calculates_order_quantities(self):
        api = Api()
        out = run_fn(api)
        assert isinstance(out, list) and len(out) == 1
        row = out[0]
        assert row["itemName"] == "Jim Beam 700ml"
        # used 6 (10 opening - 4 closing); 6 per $100k sales, budget $200k -> 12
        assert row["usageLast4Weeks"] == 6.0
        assert row["forecastUsage"] == 12.0
        assert row["orderQty"] == 8.0  # 12 forecast - 4 on hand
        assert api.retries == 0


class TestStockOnHandFailure:
    """The regression: an API failure must never read as 'no items'."""

    def test_failure_is_reported_not_silently_empty(self):
        api = Api(stock_now=API_ERROR, retry_stock=API_ERROR)
        out = run_fn(api)

        assert isinstance(out, dict), "must return an error, not an empty list"
        assert "error" in out
        # Names the real cause and rules out the wrong one the agent guessed.
        assert "not an empty template" in out["error"]
        assert "500" in out["error"]
        assert "0 items need reordering" not in " ".join(api.logs)

    def test_failure_is_retried_once_serially_then_succeeds(self):
        """The parallel batch contributes to the timeout, so retry off it."""
        api = Api(stock_now=API_ERROR, retry_stock=STOCK_NOW)
        out = run_fn(api)

        assert api.retries == 1, "should retry exactly once"
        assert isinstance(out, list), "a successful retry should produce results"
        assert out[0]["orderQty"] == 8.0

    def test_both_stock_calls_failing_retries_each(self):
        api = Api(stock_now=API_ERROR, stock_4w=API_ERROR, retry_stock=API_ERROR)
        out = run_fn(api)
        assert api.retries == 2
        assert "error" in out

    def test_genuinely_empty_template_says_so_distinctly(self):
        """An empty report is a different message from an API failure."""
        api = Api(stock_now={"lines": []})
        out = run_fn(api)
        assert isinstance(out, dict)
        assert "no stock lines" in out["error"]
        assert "not an empty template" not in out["error"]


class TestDegradedInputs:
    def test_received_invoice_failure_warns_but_continues(self):
        """Usage is understated without invoices, but a forecast is still useful."""
        api = Api(received=API_ERROR)
        out = run_fn(api)
        assert isinstance(out, list)
        assert any("received invoices unavailable" in m for m in api.logs)

    def test_no_sales_data_still_reports_clearly(self):
        api = Api(sales=[])
        out = run_fn(api)
        assert out["error"] == "No sales data available"


class TestSandboxCompatibility:
    def test_code_runs_under_the_real_sandbox_namespace(self):
        """No imports / strftime — the sandbox blocks __import__."""
        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, ns)
        assert callable(ns["run"])
