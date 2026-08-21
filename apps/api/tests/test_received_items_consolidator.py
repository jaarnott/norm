"""Tests for the get_received_items_for_period consolidator function_code.

Same harness as test_reconcile_consolidator: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, so CI
validates the exact code production runs.

Fixtures mirror the live LoadedHub shape captured 19 Aug 2026 from
`get_received_invoices` (property=Received) against The Glass Goose: the spec's
response_transform renames `itemId` to `StockItemId` and `itemCategory.name` to
`Category`, drops `unitId` entirely, and keeps `unitName` / `unitRatio`.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "received_items_for_period.py"
).read_text(encoding="utf-8")

MILK = "item-milk"
GIN = "item-gin"

WINDOW = {
    "start": "2026-08-01T07:00:00+12:00",
    "end": "2026-08-19T06:59:59+12:00",
    "trading_aligned": True,
    "description": "1-19 August",
}


class Api:
    """Scriptable stand-in for the sandbox's call_api.

    Returns each action's payload FLAT, exactly as
    function_executor._do_api_call delivers it (`return
    handler_result.get("data")`). Returning a handler-shaped
    {"success", "data"} envelope here would model the wrong layer — that is
    what let a double-unwrap ship green in reconcile_received_invoices
    (19 Aug 2026).
    """

    def __init__(self, invoices, items=None, units=None, errors=None, window=WINDOW):
        self.invoices = invoices
        self.items = items if items is not None else DEFAULT_ITEMS
        self.units = units if units is not None else DEFAULT_UNITS
        self.errors = errors or {}
        self.window = window
        self.calls = []

    def call_api(self, connector, action, params=None):
        self.calls.append((connector, action))
        # Tests key errors/asserts on the original name; the consolidator now
        # calls the engine-only raw twin for the same list.
        alias = "get_stock_items" if action == "get_stock_items_raw" else action
        if alias in self.errors:
            return {"error": self.errors[alias]}
        if action == "resolve_dates":
            return {"window": self.window}
        if action == "get_received_invoices":
            return self.invoices
        if action in ("get_stock_items", "get_stock_items_raw"):
            # The consolidator moved to the engine-only raw list when
            # get_stock_items became a consolidator; same {id, name} shape.
            return self.items
        if action == "get_stock_units":
            return self.units
        if action == "get_stock_item_groups":
            # group → category map for group_by='super_group'; empty is a
            # valid degrade (rows fall back to the line's own category).
            return []
        raise AssertionError(f"unexpected action {connector}.{action}")


DEFAULT_ITEMS = [
    {"id": MILK, "name": "MILK BLUE"},
    {"id": GIN, "name": "MALFY GIN ROSA"},
]

DEFAULT_UNITS = [
    {"id": "u-1l", "name": "1 Litre", "ratio": 1.0, "stockUnitType": "Volume"},
    {"id": "u-6x1l", "name": "6x1L", "ratio": 6.0, "stockUnitType": "Volume"},
    {"id": "u-750", "name": "750 mL", "ratio": 0.75, "stockUnitType": "Volume"},
]


def line(item_id, unit_name, ratio, qty, cost, category="Beverage", code=None):
    return {
        "StockItemId": item_id,
        "StockVariantCode": code,
        "Category": category,
        "unitName": unit_name,
        "unitRatio": ratio,
        "quantityReceived": qty,
        "unitCost": cost,
        "saleTaxRate": 0.15,
    }


def invoice(inv_id, supplier, date, lines, credit=False, number=None):
    return {
        "id": inv_id,
        "type": "Invoice",
        "creditRequest": credit,
        "invoiceNumber": number or inv_id.upper(),
        "invoicedAt": date,
        "receivedAt": date,
        "supplierId": "sup-" + supplier.lower().replace(" ", ""),
        "supplierName": supplier,
        "lines": lines,
    }


def run_consolidator(api, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, namespace)
    defaults = {"venue": "Glass Goose", "period": "this month", **params}
    return namespace["run"](defaults, api.call_api, lambda m: None)


class TestQuantitiesAreMadeComparable:
    """Twelve 6x1L and twelve 1L are not twenty-four of anything."""

    def test_mixed_pack_sizes_sum_in_base_units(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 12, 4.60)],
                ),
                invoice(
                    "i2", "Bidfood", "2026-08-09", [line(MILK, "6x1L", 6.0, 12, 27.60)]
                ),
            ]
        )
        row = run_consolidator(api)["rows"][0]
        # 12 x 1L + 12 x 6L = 84 litres, NOT 24 of something.
        assert row["quantity_base"] == 84.0
        assert row["base_unit"] == "L"
        assert row["units_seen"] == ["1 Litre", "6x1L"]

    def test_a_line_with_no_ratio_is_excluded_and_declared(self):
        api = Api(
            [invoice("i1", "Bidfood", "2026-08-02", [line(MILK, "?", None, 5, 4.60)])]
        )
        out = run_consolidator(api)
        assert out["rows"][0]["quantity_base"] == 0.0
        assert any("no unit ratio" in w for w in out["warnings"])


class TestPriceMovementIsPerBaseUnit:
    """A pack-size change is not a price rise."""

    def test_same_price_in_a_bigger_box_is_not_a_price_change(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 12, 4.60)],
                ),
                invoice(
                    "i2", "Bidfood", "2026-08-09", [line(MILK, "6x1L", 6.0, 2, 27.60)]
                ),
            ]
        )
        row = run_consolidator(api)["rows"][0]
        assert row["unit_cost_first"] == 4.6
        assert row["unit_cost_last"] == 4.6
        assert row["price_change_pct"] == 0.0

    def test_a_real_rise_is_reported(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 10, 4.00)],
                ),
                invoice(
                    "i2",
                    "Bidfood",
                    "2026-08-09",
                    [line(MILK, "1 Litre", 1.0, 10, 5.00)],
                ),
            ]
        )
        row = run_consolidator(api)["rows"][0]
        assert row["price_change_pct"] == 25.0
        assert (row["unit_cost_min"], row["unit_cost_max"]) == (4.0, 5.0)

    def test_first_and_last_follow_date_not_arrival_order(self):
        api = Api(
            [
                invoice(
                    "i2",
                    "Bidfood",
                    "2026-08-09",
                    [line(MILK, "1 Litre", 1.0, 10, 5.00)],
                ),
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 10, 4.00)],
                ),
            ]
        )
        row = run_consolidator(api)["rows"][0]
        assert (row["unit_cost_first"], row["unit_cost_last"]) == (4.0, 5.0)

    def test_a_credit_does_not_set_the_last_price(self):
        """A credit carries the ORIGINAL price with a negative quantity —
        letting it land last would report a change that never happened."""
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 10, 4.00)],
                ),
                invoice(
                    "i2",
                    "Bidfood",
                    "2026-08-05",
                    [line(MILK, "1 Litre", 1.0, 10, 6.00)],
                ),
                invoice(
                    "c1",
                    "Bidfood",
                    "2026-08-09",
                    [line(MILK, "1 Litre", 1.0, -10, 4.00)],
                    credit=True,
                ),
            ]
        )
        row = run_consolidator(api)["rows"][0]
        assert row["unit_cost_last"] == 6.0
        assert row["price_change_pct"] == 50.0


class TestItemNamesAreResolved:
    """The feed carries no item name — only ids and the supplier's own text."""

    def test_one_item_under_two_supplier_descriptions_is_one_row(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(GIN, "750 mL", 0.75, 6, 30.00, code="MALFY-R")],
                ),
                invoice(
                    "i2",
                    "Service Foods",
                    "2026-08-09",
                    [line(GIN, "750 mL", 0.75, 6, 31.00, code="GIN99")],
                ),
            ]
        )
        rows = run_consolidator(api)["rows"]
        assert len(rows) == 1
        assert rows[0]["item_name"] == "MALFY GIN ROSA"
        assert rows[0]["supplier_count"] == 2

    def test_an_item_missing_from_the_catalogue_is_flagged_not_blanked(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line("item-gone", "1 Litre", 1.0, 5, 2.00)],
                )
            ],
            items=[],
        )
        out = run_consolidator(api)
        assert out["rows"][0]["item_name"].startswith("(unknown item")
        assert any("not in the stock catalogue" in w for w in out["warnings"])

    def test_a_catalogue_outage_degrades_but_keeps_the_numbers(self):
        api = Api(
            [
                invoice(
                    "i1", "Bidfood", "2026-08-02", [line(MILK, "1 Litre", 1.0, 5, 2.00)]
                )
            ],
            errors={"get_stock_items": "catalogue down"},
        )
        out = run_consolidator(api)
        assert out["rows"][0]["quantity_base"] == 5.0
        assert any("Item names unavailable" in w for w in out["warnings"])

    def test_names_cost_one_call_not_one_per_item(self):
        lines = [line("item-%d" % i, "1 Litre", 1.0, 1, 1.0) for i in range(40)]
        api = Api([invoice("i1", "Bidfood", "2026-08-02", lines)])
        run_consolidator(api)
        assert (
            sum(1 for c in api.calls if c[1] in ("get_stock_items", "get_stock_items_raw"))
            == 1
        )


class TestGrouping:
    def test_item_supplier_keeps_the_split(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 10, 4.00)],
                ),
                invoice(
                    "i2",
                    "Service Foods",
                    "2026-08-03",
                    [line(MILK, "1 Litre", 1.0, 10, 4.50)],
                ),
            ]
        )
        rows = run_consolidator(api, group_by="item_supplier")["rows"]
        assert len(rows) == 2
        assert {r["supplier_name"] for r in rows} == {"Bidfood", "Service Foods"}

    def test_line_grouping_does_not_aggregate(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [
                        line(MILK, "1 Litre", 1.0, 10, 4.00),
                        line(MILK, "1 Litre", 1.0, 3, 4.00),
                    ],
                ),
            ]
        )
        out = run_consolidator(api, group_by="line")
        assert len(out["rows"]) == 2
        assert out["summary"]["distinct_items"] == 1

    def test_an_unknown_grouping_is_refused(self):
        api = Api([])
        assert (
            "group_by must be one of"
            in run_consolidator(api, group_by="supplier")["error"]
        )


class TestCreditsNetAndStayVisible:
    def test_a_credit_subtracts_and_is_reported_separately(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 10, 4.00)],
                ),
                invoice(
                    "c1",
                    "Bidfood",
                    "2026-08-09",
                    [line(MILK, "1 Litre", 1.0, -2, 4.00)],
                    credit=True,
                ),
            ]
        )
        row = run_consolidator(api)["rows"][0]
        assert row["quantity_base"] == 8.0
        assert row["spend"] == 32.0
        assert row["credit_amount"] == -8.0


class TestFailuresAreNotEmptyPeriods:
    def test_a_failed_feed_read_is_an_error_not_zero_rows(self):
        api = Api([], errors={"get_received_invoices": "LoadedHub 502"})
        out = run_consolidator(api)
        assert "502" in out["error"]
        assert "rows" not in out

    def test_an_unresolvable_period_is_refused(self):
        api = Api([], window=None)
        assert "date range" in run_consolidator(api)["error"]

    def test_no_period_at_all_is_refused(self):
        api = Api([])
        namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, namespace)
        out = namespace["run"]({"venue": "Glass Goose"}, api.call_api, lambda m: None)
        assert "plain English" in out["error"]


class TestWindowHonesty:
    def test_a_non_trading_window_asks_before_fetching(self):
        api = Api([], window={**WINDOW, "trading_aligned": False})
        out = run_consolidator(
            api,
            period="",
            start="2026-08-01T00:00:00+12:00",
            end="2026-08-19T00:00:00+12:00",
        )
        assert out["needs_confirmation"] is True
        assert not any(c[1] == "get_received_invoices" for c in api.calls)

    def test_confirmed_by_user_proceeds(self):
        api = Api(
            [
                invoice(
                    "i1", "Bidfood", "2026-08-02", [line(MILK, "1 Litre", 1.0, 5, 2.00)]
                )
            ],
            window={**WINDOW, "trading_aligned": False},
        )
        out = run_consolidator(
            api,
            period="",
            start="2026-08-01T00:00:00+12:00",
            end="2026-08-19T00:00:00+12:00",
            confirmed_by_user=True,
        )
        assert out["rows"][0]["quantity_base"] == 5.0

    def test_the_window_is_always_reported(self):
        api = Api(
            [
                invoice(
                    "i1", "Bidfood", "2026-08-02", [line(MILK, "1 Litre", 1.0, 5, 2.00)]
                )
            ]
        )
        assert run_consolidator(api)["window"] == WINDOW


class TestArithmeticIsExact:
    """Rounding each line before summing drifts. Live check, 19 Aug 2026: over
    552 lines it put a fortnight's spend 4c above the same figure computed by
    hand from the feed, which is exactly the kind of small wrongness a report
    is trusted on."""

    def test_spend_is_summed_at_full_precision(self):
        # Three lines whose exact spends each need more than 2dp.
        lines = [line(MILK, "1 Litre", 1.0, 3, 4.005) for _ in range(3)]
        api = Api([invoice("i1", "Bidfood", "2026-08-02", lines)])
        out = run_consolidator(api)
        assert out["summary"]["net_spend"] == round(3 * 3 * 4.005, 2)
        assert out["rows"][0]["spend"] == round(3 * 3 * 4.005, 2)

    def test_the_headline_is_not_a_sum_of_rounded_rows(self):
        """Many items, each needing rounding: adding the rounded per-item
        subtotals drifts the total away from the source."""
        invs = [
            invoice(
                "i%d" % i,
                "Bidfood",
                "2026-08-02",
                [line("item-%d" % i, "1 Litre", 1.0, 3, 4.005)],
            )
            for i in range(40)
        ]
        api = Api(invs, items=[])
        out = run_consolidator(api)
        assert out["summary"]["net_spend"] == round(40 * 3 * 4.005, 2)

    def test_line_rows_still_round_for_display(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 3, 4.005)],
                )
            ]
        )
        row = run_consolidator(api, group_by="line")["rows"][0]
        assert row["spend"] == 12.02


class TestSummary:
    def test_the_headline_answers_without_reading_rows(self):
        api = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 10, 4.00)],
                ),
                invoice(
                    "i2", "Bidfood", "2026-08-09", [line(GIN, "750 mL", 0.75, 6, 30.00)]
                ),
            ]
        )
        summary = run_consolidator(api)["summary"]
        assert summary["rows"] == 2
        assert summary["net_spend"] == 220.0
        assert summary["invoices"] == 2

    def test_rows_lead_with_the_biggest_spend(self):
        api = Api(
            [
                invoice(
                    "i1", "Bidfood", "2026-08-02", [line(MILK, "1 Litre", 1.0, 1, 4.00)]
                ),
                invoice(
                    "i2", "Bidfood", "2026-08-09", [line(GIN, "750 mL", 0.75, 6, 30.00)]
                ),
            ]
        )
        assert run_consolidator(api)["rows"][0]["item_name"] == "MALFY GIN ROSA"
