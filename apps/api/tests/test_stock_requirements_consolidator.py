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
# Post-transform shape of get_stock_item_minimums (see the spec action): one row
# per item with the par level and the ratios needed to convert it to counting
# units. Default: no minimums configured.
MINIMUMS = []

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
        minimums=MINIMUMS,
        retry_stock=None,
    ):
        self.stock_now = stock_now
        self.stock_4w = stock_4w
        self.received = received
        self.sales = sales
        self.budgets = budgets
        self.minimums = minimums
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
        if action == "get_stock_item_minimums":
            return self.minimums
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
        assert row["orderDriver"] == "usage"
        assert row["minimumStock"] == 0
        assert api.retries == 0

    def test_no_20_percent_buffer_is_applied(self):
        """We deliberately do NOT add LoadedHub's 20% forecast buffer."""
        out = run_fn(Api())
        # A buffered order would be 1.2 * 12 - 4 = 10.4; the bare forecast is 8.0.
        assert out[0]["orderQty"] == 8.0


class TestMinimumEnforcement:
    """Par levels: order up to the minimum, converting units, even with no usage."""

    def test_below_par_with_no_usage_is_still_ordered(self):
        # No usage this period (opening == closing), but on hand (4) is below the
        # par level of 10 -> order the 6-unit shortfall.
        api = Api(
            stock_now={
                "lines": [
                    {
                        "stockItemID": "i1",
                        "itemName": "Jim Beam 700ml",
                        "quantityOnHand": 4.0,
                        "countingUnitName": "bottle",
                        "Category": "Spirits",
                    }
                ]
            },
            stock_4w={"lines": [{"stockItemID": "i1", "quantityOnHand": 4.0}]},
            minimums=[
                {
                    "id": "i1",
                    "minQty": 10,
                    "minUnitRatio": 0.7,
                    "countingUnitRatio": 0.7,
                }
            ],
        )
        out = run_fn(api)
        assert len(out) == 1
        assert out[0]["orderQty"] == 6.0  # 10 par - 4 on hand
        assert out[0]["orderDriver"] == "minimum"
        assert out[0]["minimumStock"] == 10.0

    def test_minimum_is_converted_from_its_own_unit(self):
        """Min '1' in a 24-pack unit for an item counted in Each == 24 Each."""
        api = Api(
            stock_now={
                "lines": [
                    {
                        "stockItemID": "beer",
                        "itemName": "Stella 330ml",
                        "quantityOnHand": 5.0,
                        "countingUnitName": "Each",
                        "Category": "Beer",
                    }
                ]
            },
            stock_4w={"lines": [{"stockItemID": "beer", "quantityOnHand": 5.0}]},
            minimums=[
                {"id": "beer", "minQty": 1, "minUnitRatio": 24, "countingUnitRatio": 1}
            ],
        )
        out = run_fn(api)
        assert out[0]["minimumStock"] == 24.0
        assert out[0]["orderQty"] == 19.0  # 24 par - 5 on hand

    def test_at_or_above_par_with_no_usage_is_not_ordered(self):
        api = Api(
            stock_now={
                "lines": [
                    {
                        "stockItemID": "i1",
                        "itemName": "Jim Beam 700ml",
                        "quantityOnHand": 12.0,
                        "countingUnitName": "bottle",
                        "Category": "Spirits",
                    }
                ]
            },
            stock_4w={"lines": [{"stockItemID": "i1", "quantityOnHand": 12.0}]},
            minimums=[
                {
                    "id": "i1",
                    "minQty": 10,
                    "minUnitRatio": 0.7,
                    "countingUnitRatio": 0.7,
                }
            ],
        )
        assert run_fn(api) == []

    def test_usage_wins_when_it_exceeds_the_par_shortfall(self):
        # Default stock: usage order = 8. Par shortfall only 2 -> usage drives it.
        api = Api(
            minimums=[
                {"id": "i1", "minQty": 6, "minUnitRatio": 1, "countingUnitRatio": 1}
            ]
        )
        out = run_fn(api)
        assert out[0]["orderQty"] == 8.0
        assert out[0]["orderDriver"] == "usage"

    def test_minimums_call_failure_degrades_to_usage_only(self):
        api = Api(minimums=API_ERROR)
        out = run_fn(api)
        assert isinstance(out, list)
        assert out[0]["orderQty"] == 8.0  # usage forecast still works
        assert any("par levels will not be enforced" in m for m in api.logs)


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
