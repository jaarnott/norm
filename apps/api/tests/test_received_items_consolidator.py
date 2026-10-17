"""Tests for the get_received_items_for_period consolidator function_code.

Same harness as test_reconcile_consolidator: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, so CI
validates the exact code production runs.

Fixtures mirror the live LoadedHub shape captured 19 Aug 2026 from
`get_received_invoices` (property=Received) against The Glass Goose: the spec's
response_transform renames `itemId` to `StockItemId` and `itemCategory.name` to
`Category`, drops `unitId` entirely, and keeps `unitName` / `unitRatio`.
"""

from tests import consolidator_harness as harness

FUNCTION_CODE = harness.source("received_items_for_period")

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
    defaults = {"venue": "Glass Goose", "period": "this month", **params}
    return harness.run(FUNCTION_CODE, api, **defaults)


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

    def test_a_line_with_no_ratio_is_excluded_and_declared(self):
        api = Api(
            [invoice("i1", "Bidfood", "2026-08-02", [line(MILK, "?", None, 5, 4.60)])]
        )
        out = run_consolidator(api)
        assert out["rows"][0]["quantity_base"] == 0.0
        assert any("no unit ratio" in w for w in out["warnings"])


class TestPriceMovementIsPerBaseUnit:
    """A pack-size change is not a price rise.

    These figures live on `item_supplier` rows now — the supplier comparison
    is the question they answer. The `item` row carries none of them: across
    118 production calls in 90 days they were used once (3 Oct 2026 review).
    """

    def test_same_price_in_a_bigger_box_is_not_a_price_change(self):
        """Folded in: a real rise is reported, and the price movers table's
        move — last receipt against the first in the window, per base unit —
        is 0 for the bigger box, 25% for the rise, and absent for a single
        receipt."""
        box = Api(
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
        row = run_consolidator(box, group_by="item_supplier")["rows"][0]
        assert (row["unit_cost_min"], row["unit_cost_max"]) == (4.6, 4.6)
        assert row["unit_cost_last"] == 4.6
        assert row["unit_cost_change_pct"] == 0.0

        # a real rise is reported
        rise = Api(
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
        row = run_consolidator(rise, group_by="item_supplier")["rows"][0]
        assert (row["unit_cost_min"], row["unit_cost_max"]) == (4.0, 5.0)
        assert row["unit_cost_last"] == 5.0
        # A percent: 25.0 is +25% (it was a fraction until Oct 2026, so an
        # agent read the chips' 25% rise as "up 0.3%").
        assert row["unit_cost_change_pct"] == 25.0
        assert row["last_price_change"] == {
            "date": "2026-08-09",
            "from": 4.0,
            "to": 5.0,
            "unit": "1 Litre",
            "pct": 25.0,
        }

        once = Api(
            [
                invoice(
                    "i1",
                    "Bidfood",
                    "2026-08-02",
                    [line(MILK, "1 Litre", 1.0, 10, 4.00)],
                )
            ]
        )
        row = run_consolidator(once, group_by="item_supplier")["rows"][0]
        assert row["unit_cost_change_pct"] is None
        assert row["last_price_change"] is None

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
        row = run_consolidator(api, group_by="item_supplier")["rows"][0]
        assert row["unit_cost_last"] == 5.0  # by date, not arrival order

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
        row = run_consolidator(api, group_by="item_supplier")["rows"][0]
        assert row["unit_cost_last"] == 6.0


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
            sum(
                1
                for c in api.calls
                if c[1] in ("get_stock_items", "get_stock_items_raw")
            )
            == 1
        )


class TestGrouping:
    # item_supplier keeping the split per supplier: TestModesReturnPurposeFitShapes::
    # test_item_supplier_is_the_price_comparison.

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
        out = run_consolidator(api, group_by="history", item_id=MILK)
        assert len(out["rows"]) == 2
        assert out["summary"]["distinct_items"] == 1

    def test_an_unknown_grouping_is_refused(self):
        api = Api([])
        assert (
            "group_by must be one of"
            in run_consolidator(api, group_by="banana")["error"]
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
        out = harness.run(FUNCTION_CODE, Api([]), venue="Glass Goose")
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
        row = run_consolidator(api, group_by="history", item_id=MILK)["rows"][0]
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
        out = run_consolidator(api)
        summary = out["summary"]
        assert summary["rows"] == 2
        assert summary["net_spend"] == 220.0
        assert summary["invoices"] == 2
        # rows lead with the biggest spend ($180 of gin before $40 of milk),
        # not with the first one received
        assert out["rows"][0]["item_name"] == "MALFY GIN ROSA"


class TestModesReturnPurposeFitShapes:
    """One mode per question people asked (3 Oct 2026 review: 211 calls, 11
    conversations, 90 days). No `detail` switch: the shape follows the mode,
    and across venues it follows the scope."""

    def _api(self):
        return Api(
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
                    "2026-08-09",
                    [line(MILK, "1 Litre", 1.0, 5, 4.20)],
                ),
            ]
        )

    def test_an_item_row_is_the_four_column_table_and_nothing_else(self):
        """Fails if the lists or price fields creep back onto `item`. Every
        reply built Item · Value · Qty · Unit; the rest was weight — 576
        chars a row, 55% of it field names."""
        row = run_consolidator(self._api())["rows"][0]
        assert set(row) == {
            "item_name",
            "item_code",
            "base_unit",
            "quantity_base",
            "spend",
            "invoice_count",
            "supplier_count",
            "credit_amount",
            "item_id",
        }
        assert row["supplier_count"] == 2 and row["invoice_count"] == 2

    def test_history_needs_a_filter_and_says_which(self):
        """Fails if a whole period of every line can be asked for again — the
        two largest responses in 90 days (450 kB, 266 kB) were exactly that."""
        api = self._api()
        out = run_consolidator(api, group_by="history")
        assert (
            "item_id" in out["error"]
            and "query" in out["error"]
            and "suppliers" in out["error"]
        )
        assert not any(a == "get_received_invoices" for _, a in api.calls), (
            "refused before fetching"
        )

    def test_a_history_row_is_a_purchase_line_without_the_ids(self):
        rows = run_consolidator(self._api(), group_by="history", query="milk")["rows"]
        assert [r["date"] for r in rows] == ["2026-08-02", "2026-08-09"]
        assert set(rows[0]) == {
            "date",
            "supplier_name",
            "invoice_number",
            "item_name",
            "item_code",
            "quantity",
            "unit_name",
            "unit_cost",
            "quantity_base",
            "base_unit",
            "cost_per_base_unit",
            "spend",
            "is_credit",
        }
        assert (
            rows[1]["supplier_name"] == "Service Foods" and rows[1]["unit_cost"] == 4.2
        )

    def test_line_is_still_accepted_as_the_old_name(self):
        out = run_consolidator(self._api(), group_by="line", query="milk")
        assert out["group_by"] == "history" and len(out["rows"]) == 2

    def test_item_supplier_is_the_price_comparison(self):
        """One item from two suppliers stays two rows — the split IS the
        comparison."""
        rows = run_consolidator(self._api(), group_by="item_supplier")["rows"]
        assert len(rows) == 2
        assert {r["supplier_name"] for r in rows} == {"Bidfood", "Service Foods"}
        assert {
            "unit_cost_avg",
            "unit_cost_min",
            "unit_cost_max",
            "unit_cost_last",
            "unit_cost_change_pct",
        } <= set(rows[0])

    def test_a_line_booked_in_the_wrong_unit_is_not_a_price_rise(self):
        """Seen on real receipts (Oct 2026): chicken booked as a kilo at a
        box's price. Compared carton against carton, the price held; the stray
        line can't make a 1,000% "rise"."""
        api = Api(
            [
                invoice(
                    "i1", "Bidfood", "2026-08-02", [line(MILK, "6x1L", 6.0, 2, 27.60)]
                ),
                invoice(
                    "i2",
                    "Bidfood",
                    "2026-08-05",
                    [line(MILK, "1 Litre", 1.0, 1, 27.60)],
                ),
                invoice(
                    "i3", "Bidfood", "2026-08-09", [line(MILK, "6x1L", 6.0, 2, 27.60)]
                ),
            ]
        )
        row = run_consolidator(api, group_by="item_supplier")["rows"][0]
        assert row["unit_cost_change_pct"] == 0.0
        assert row["last_price_change"] is None


# ── Oct 2026: the purchases-based cost-of-goods dashboard ──────────────────
#
# Month / week buckets split by Loaded's three categories, spend per supplier,
# freight, and the reconciliation the owner asked for: Food + Beverage + Other
# + freight must equal what the invoices charged. Checked on September 2026
# data at Applegum Cafe, Little West and Somewhere North (docs/plans/
# procurement-cogs-dashboard.md): equal to the cent.


def au_line(item_id, qty, cost, category, rate=0.0, ratio=1.0, unit="Kilo"):
    return {
        "StockItemId": item_id,
        "StockVariantCode": None,
        "Category": category,
        "unitName": unit,
        "unitRatio": ratio,
        "quantityReceived": qty,
        "unitCost": cost,
        "saleTaxRate": rate,
    }


def au_invoice(inv_id, supplier, date, lines, freight=0.0, credit=False):
    subtotal = round(sum(ln["quantityReceived"] * ln["unitCost"] for ln in lines), 2)
    tax = round(
        sum(ln["quantityReceived"] * ln["unitCost"] * ln["saleTaxRate"] for ln in lines)
        + freight * 0.1,
        2,
    )
    return {
        "id": inv_id,
        "type": "Invoice",
        "creditRequest": credit,
        "invoiceNumber": inv_id.upper(),
        "invoicedAt": date,
        "receivedAt": date,
        "supplierId": "sup-" + supplier.lower().replace(" ", ""),
        "supplierName": supplier,
        "freight": freight,
        "subtotal": round(subtotal + freight, 2),
        "total": round(subtotal + freight + tax, 2),
        "lines": lines,
    }


AU_ITEMS = [
    {
        "id": "i-flour",
        "name": "FLOUR PLAIN",
        "groupId": "g-dry",
        "groupName": "Dry Goods",
    },
    {
        "id": "i-milk",
        "name": "MILK FULL CREAM",
        "groupId": "g-dairy",
        "groupName": "Dairy",
    },
    {
        "id": "i-coffee",
        "name": "COFFEE BEANS",
        "groupId": "g-bev",
        "groupName": "Coffee",
    },
    {
        "id": "i-cups",
        "name": "TAKEAWAY CUPS",
        "groupId": "g-pack",
        "groupName": "Packaging",
    },
]
AU_UNITS = [
    {"id": "u-kilo", "name": "Kilo", "ratio": 1.0, "stockUnitType": "Weight"},
    {"id": "u-each", "name": "Each", "ratio": 1.0, "stockUnitType": "Count"},
]
AU_WINDOW = {
    "start": "2026-09-01T00:00:00+10:00",
    "end": "2026-09-30T23:59:59+10:00",
    "trading_aligned": True,
    "description": "September 2026",
}


def au_invoices():
    return [
        # GST-free food, with freight
        au_invoice(
            "i1",
            "Sonoma Baking",
            "2026-09-02",
            [au_line("i-flour", 10, 2.5, "Food"), au_line("i-milk", 20, 1.8, "Food")],
            freight=7.0,
        ),
        # beverage at 10% and packaging (Other Stock)
        au_invoice(
            "i2",
            "Made of Many",
            "2026-09-08",
            [
                au_line("i-coffee", 4, 38.0, "Beverage", rate=0.1),
                au_line("i-cups", 500, 0.12, "Other Stock", rate=0.1, unit="Each"),
            ],
            freight=20.0,
        ),
        # a credit: negative quantity, negative subtotal
        au_invoice(
            "cn1",
            "Sonoma Baking",
            "2026-09-10",
            [au_line("i-flour", -2, 2.5, "Food")],
            credit=True,
        ),
        # the next week and the next month
        au_invoice(
            "i3",
            "Sonoma Baking",
            "2026-09-16",
            [au_line("i-flour", 10, 2.6, "Food")],
            freight=7.0,
        ),
        au_invoice(
            "i4",
            "Made of Many",
            "2026-10-03",
            [au_line("i-coffee", 2, 38.0, "Beverage", rate=0.1)],
        ),
    ]


def run_au(group_by, **params):
    api = Api(au_invoices(), items=AU_ITEMS, units=AU_UNITS, window=AU_WINDOW)
    return run_consolidator(api, group_by=group_by, **params), api


class TestCategoriesReconcileToTheInvoices:
    def test_food_plus_beverage_plus_other_plus_freight_equals_the_subtotals(self):
        out, _ = run_au("month")
        s = out["summary"]
        # lines: 25 + 36 + 152 + 60 − 5 + 26 + 76 = 370.00; freight 34.00
        assert s["by_category"] == {
            "Food": 82.0,
            "Beverage": 228.0,
            "Other Stock": 60.0,
        }
        assert s["freight"] == 34.0
        rec = s["reconciliation"]
        assert rec["categories"] == 370.0
        assert rec["invoice_subtotals"] == 404.0
        assert rec["difference"] == 0.0
        assert rec["ok"] is True
        assert rec["invoices"] == 5

    def test_a_gap_is_reported_not_hidden(self):
        invoices = au_invoices()
        invoices[0]["subtotal"] += 5.0  # a header that doesn't match its lines
        api = Api(invoices, items=AU_ITEMS, units=AU_UNITS, window=AU_WINDOW)
        out = run_consolidator(api, group_by="item")
        rec = out["summary"]["reconciliation"]
        assert rec["ok"] is False and rec["difference"] == -5.0

    def test_a_narrowed_read_carries_no_reconciliation(self):
        out, _ = run_au("item", query="flour")
        assert "reconciliation" not in out["summary"]
        assert "freight" not in out["summary"]
        assert out["summary"]["by_category"] == {"Food": 46.0}

    def test_every_mode_carries_the_split(self):
        for mode in ("item", "group", "super_group", "supplier", "week"):
            out, _ = run_au(mode)
            assert out["summary"]["by_category"]["Food"] == 82.0, mode


class TestTimeBuckets:
    def test_months_split_by_category_with_freight_and_credits(self):
        out, api = run_au("month")
        assert ("loadedhub", "get_stock_item_groups") in api.calls
        rows = out["rows"]
        assert [r["bucket"] for r in rows] == ["September 2026", "October 2026"]
        sep, octo = rows
        assert sep["bucket_start"] == "2026-09-01"
        assert (sep["food"], sep["beverage"], sep["other"]) == (82.0, 152.0, 60.0)
        assert sep["spend"] == 294.0
        assert sep["freight"] == 34.0
        assert sep["credit_amount"] == -5.0
        assert sep["invoice_subtotals"] == 328.0
        assert sep["invoice_count"] == 4
        assert octo["beverage"] == 76.0 and octo["freight"] == 0.0
        assert "venue" not in sep  # one venue: no column

    def test_weeks_are_iso_weeks_from_monday(self):
        out, _ = run_au("week")
        rows = out["rows"]
        assert [r["bucket"] for r in rows] == [
            "2026-W36",
            "2026-W37",
            "2026-W38",
            "2026-W40",
        ]
        assert rows[0]["bucket_start"] == "2026-08-31"  # the Monday of W36
        assert rows[1]["food"] == -5.0 and rows[1]["beverage"] == 152.0

    def test_across_venues_each_bucket_is_per_venue(self):
        api = Api(au_invoices(), items=AU_ITEMS, units=AU_UNITS, window=AU_WINDOW)
        out = run_consolidator(
            api, group_by="month", venues=["Applegum", "Little West"]
        )
        rows = out["rows"]
        assert {r["venue"] for r in rows} == {"Applegum", "Little West"}
        assert [(r["bucket"], r["venue"]) for r in rows][:2] == [
            ("September 2026", "Applegum"),
            ("September 2026", "Little West"),
        ]
        assert out["summary"]["reconciliation"]["invoices"] == 10


class TestSupplierMode:
    def test_spend_per_supplier_with_share_and_top_items(self):
        out, _ = run_au("supplier")
        rows = out["rows"]
        assert [r["supplier_name"] for r in rows] == ["Made of Many", "Sonoma Baking"]
        mom, sonoma = rows
        assert mom["spend"] == 288.0 and mom["invoice_count"] == 2
        assert mom["share"] == round(288.0 / 370.0, 4)
        assert mom["top_items"][0] == {"item": "COFFEE BEANS", "spend": 228.0}
        assert sonoma["credit_amount"] == -5.0


# ── group rollups (moved from test_invoice_consolidators.py, 17 Oct 2026) ──

RECEIVED_FEED = [
    {
        "id": "ri-1",
        "supplierName": "Bidfood",
        "invoiceNumber": "109700",
        "invoicedAt": "2026-08-03T00:00:00+12:00",
        "creditRequest": False,
        "lines": [
            {
                "StockItemId": "i-flour",
                "quantityReceived": 2,
                "unitCost": 20.0,
                "unitRatio": 10.0,
                "unitName": "10 KG",
            },
            {
                "StockItemId": "i-oil",
                "quantityReceived": 1,
                "unitCost": 30.0,
                "unitRatio": 5.0,
                "unitName": "5 L",
            },
            {
                "StockItemId": "i-gin",
                "quantityReceived": 6,
                "unitCost": 45.0,
                "unitRatio": 0.7,
                "unitName": "700 mL",
            },
        ],
    }
]

CATALOGUE = [
    {
        "id": "i-flour",
        "name": "FLOUR HIGH GRADE",
        "groupId": "g-dry",
        "groupName": "Dry Goods",
    },
    {"id": "i-oil", "name": "OIL CANOLA", "groupId": "g-dry", "groupName": "Dry Goods"},
    {
        "id": "i-gin",
        "name": "GIN LONDON DRY",
        "groupId": "g-spirits",
        "groupName": "Spirits",
    },
]

SUBCATS = [
    {
        "id": "g-dry",
        "categoryId": "c-food",
        "categoryName": "Food",
        "name": "Dry Goods",
    },
    {
        "id": "g-spirits",
        "categoryId": "c-bev",
        "categoryName": "Beverage",
        "name": "Spirits",
    },
]


class ItemsApi:
    def call_api(self, connector, action, params=None):
        if action == "resolve_dates":
            return {"window": dict(WINDOW)}
        if action == "get_received_invoices":
            import copy

            return copy.deepcopy(RECEIVED_FEED)
        if action == "get_stock_items_raw":
            import copy

            return copy.deepcopy(CATALOGUE)
        if action == "get_stock_units":
            return []
        if action == "get_stock_item_groups":
            import copy

            return copy.deepcopy(SUBCATS)
        raise AssertionError(f"unexpected action {action}")


def run_items(**params):
    return harness.run(
        FUNCTION_CODE, ItemsApi(), venue="La Zeppa", period="last week", **params
    )


class TestReceivedItemsRollups:
    def test_group_rollup(self):
        out = run_items(group_by="group")
        rows = {r["group"]: r for r in out["rows"]}
        assert rows["Dry Goods"]["spend"] == 70.0  # 2*20 + 1*30
        assert rows["Dry Goods"]["distinct_items"] == 2
        assert rows["Spirits"]["spend"] == 270.0  # 6*45
        assert rows["Spirits"]["top_items"] == [
            {"item": "GIN LONDON DRY", "spend": 270.0}
        ]
        assert out["summary"]["net_spend"] == 340.0

    def test_super_group_rollup(self):
        out = run_items(group_by="super_group")
        rows = {r["super_group"]: r for r in out["rows"]}
        assert rows["Food"]["spend"] == 70.0
        assert rows["Beverage"]["spend"] == 270.0

    def test_item_query_filter(self):
        out = run_items(query="oil")
        assert len(out["rows"]) == 1
        assert out["rows"][0]["item_name"] == "OIL CANOLA"
        assert out["rows"][0]["quantity_base"] == 5.0

    def test_group_filter(self):
        out = run_items(group="dry")
        assert {r["item_name"] for r in out["rows"]} == {
            "FLOUR HIGH GRADE",
            "OIL CANOLA",
        }

    def test_limit_rolls_up_others(self):
        out = run_items(limit=1)
        assert len(out["rows"]) == 2  # top row + (others)
        assert out["rows"][0]["item_name"] == "GIN LONDON DRY"
        others = out["rows"][1]
        assert others["item_name"] == "(others)"
        assert others["rows_rolled_up"] == 2
        assert others["spend"] == 70.0
