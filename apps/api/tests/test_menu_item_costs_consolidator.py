"""get_menu_item_costs — the chef's cost of goods: Loaded's per-product
report (recipe cost × sold, revenue ex GST) against actual purchases.

The canonical code from config/consolidators/ is exec'd under the REAL
sandbox namespace; the two tools it composes are scripted in the shapes they
return today (get_cogs_detail_for_period: data rows product / group /
category / quantity / revenue / cost / discounts; get_received_items_for_period
month or week rows with food / beverage / other / freight).
"""

from tests import consolidator_harness as harness

CODE = harness.source("menu_item_costs")

WINDOW = {
    "start": "2026-09-01T07:00:00+12:00",
    "end": "2026-10-01T06:59:59+12:00",
    "trading_aligned": True,
    "description": "September 2026",
    "day_start": "07:00",
}

PRODUCTS = [
    {
        "product": "$85 Set Menu",
        "group": "Food",
        "category": "Food",
        "quantity": 78,
        "revenue": 6151.7,
        "cost": 0,
        "discounts": 0.0,
    },
    {
        "product": "Peroni (Standard)",
        "group": "Beverage",
        "category": "Beverage",
        "quantity": 306,
        "revenue": 3820.01,
        "cost": 991.44,
        "discounts": 390.0,
    },
    {
        "product": "Margarita",
        "group": "Beverage",
        "category": "Beverage",
        "quantity": 126,
        "revenue": 2824.11,
        "cost": 396.3,
        "discounts": 52.8,
    },
    {
        "product": "Fried Chicken & Hot Honey",
        "group": "Food",
        "category": "Food",
        "quantity": 74,
        "revenue": 1345.13,
        "cost": 1048.15,
        "discounts": 173.8,
    },
    {
        "product": "Glass Da Luca Prosecco",
        "group": "Beverage",
        "category": "Beverage",
        "quantity": 89,
        "revenue": 1174.93,
        "cost": 231.87,
        "discounts": 19.2,
    },
]


def purchases_rows(bucket="September 2026", start="2026-09-01"):
    return [
        {
            "bucket": bucket,
            "bucket_start": start,
            "spend": 4100.0,
            "food": 2800.0,
            "beverage": 1200.0,
            "other": 100.0,
            "freight": 50.0,
            "credit_amount": 0.0,
            "invoice_count": 30,
        }
    ]


class Api:
    def __init__(self, fail=None, products=None):
        self.fail = fail or {}
        self.calls = []
        self.products = PRODUCTS if products is None else products

    def call_api(self, connector, action, params=None):
        params = params or {}
        self.calls.append((action, params))
        if action in self.fail:
            return {"error": self.fail[action]}
        if action == "resolve_dates":
            return {"window": WINDOW}
        if action == "get_cogs_detail_for_period":
            # a week's report is a slice: scale by the window's days
            return {"window": {}, "summary": {}, "data": list(self.products)}
        if action == "get_received_items_for_period":
            if params.get("group_by") == "week":
                return {
                    "rows": [
                        {
                            **purchases_rows("2026-W36", "2026-08-31")[0],
                            "food": 700.0,
                            "beverage": 300.0,
                            "other": 25.0,
                            "freight": 10.0,
                        },
                        {
                            **purchases_rows("2026-W37", "2026-09-07")[0],
                            "food": 700.0,
                            "beverage": 300.0,
                            "other": 25.0,
                            "freight": 10.0,
                        },
                    ],
                    "summary": {},
                    "warnings": [],
                }
            return {"rows": purchases_rows(), "summary": {}, "warnings": []}
        raise AssertionError(action)


def run(api, **params):
    merged = {"period": "September 2026", "venue_id": "id-la-zeppa", **params}
    return harness.run(CODE, api, **{k: v for k, v in merged.items() if v is not None})


class TestProducts:
    def test_rows_by_revenue_with_margin_and_the_recipe_flag(self):
        out = run(Api())
        rows = out["rows"]
        assert [r["product"] for r in rows][:3] == [
            "$85 Set Menu",
            "Peroni (Standard)",
            "Margarita",
        ]
        peroni = rows[1]
        assert peroni["cogs_pct"] == round(991.44 / 3820.01, 4)
        assert peroni["margin"] == round(3820.01 - 991.44, 2)
        assert peroni["has_recipe"] is True
        assert rows[0]["has_recipe"] is False and rows[0]["cogs_pct"] is None
        total = sum(p["revenue"] for p in PRODUCTS)
        assert rows[0]["revenue_share"] == round(6151.7 / total, 4)

    def test_missing_recipes_and_the_dearest_dishes_are_listed(self):
        out = run(Api())
        assert [r["product"] for r in out["missing"]] == ["$85 Set Menu"]
        assert out["dear"][0]["product"] == "Fried Chicken & Hot Honey"
        assert out["dear"][0]["cogs_pct"] == round(1048.15 / 1345.13, 4)

    def test_the_summary_sets_theoretical_against_actual(self):
        s = run(Api())["summary"]
        rev = round(sum(p["revenue"] for p in PRODUCTS), 2)
        cost = round(sum(p["cost"] for p in PRODUCTS), 2)
        assert s["revenue"] == rev and s["cost"] == cost
        assert s["theoretical_cogs_pct"] == round(cost / rev, 4)
        assert s["purchases"] == 4150.0  # incl. freight
        assert s["actual_cogs_pct"] == round(4150.0 / rev, 4)
        assert s["gap_pts"] == round(
            (s["actual_cogs_pct"] - s["theoretical_cogs_pct"]) * 100, 1
        )
        assert s["products_without_recipe"] == 1
        assert s["revenue_without_recipe"] == 6151.7
        assert s["revenue_without_recipe_pct"] == round(6151.7 / rev, 4)

    def test_by_category_pairs_recipe_cost_with_invoices(self):
        by = {c["category"]: c for c in run(Api())["by_category"]}
        food, bev = by["Food"], by["Beverage"]
        assert food["revenue"] == round(6151.7 + 1345.13, 2) and food["cost"] == 1048.15
        assert food["purchases"] == 2800.0
        assert bev["purchases"] == 1200.0
        assert bev["theoretical_cogs_pct"] == round(
            (991.44 + 396.3 + 231.87) / (3820.01 + 2824.11 + 1174.93), 4
        )
        assert by["Other"]["purchases"] == 150.0  # other stock + freight

    def test_freight_can_be_left_out(self):
        s = run(Api(), include_freight=False)["summary"]
        assert s["purchases"] == 4100.0

    def test_top_caps_the_rows_with_an_others_row(self):
        rows = run(Api(), top=2)["rows"]
        assert len(rows) == 3
        others = rows[-1]
        assert others["product"] == "(others: 3 products)"
        assert others["revenue"] == round(2824.11 + 1345.13 + 1174.93, 2)
        assert others["cost"] == round(396.3 + 1048.15 + 231.87, 2)


class TestTrendAndEdges:
    def test_a_weekly_trend_reads_one_report_per_week(self):
        two_weeks = {
            "start": "2026-08-31T07:00:00+12:00",
            "end": "2026-09-14T06:59:59+12:00",
            "trading_aligned": True,
            "description": "two weeks",
            "day_start": "07:00",
        }

        class TwoWeeks(Api):
            def call_api(self, connector, action, params=None):
                if action == "resolve_dates":
                    self.calls.append((action, params))
                    return {"window": two_weeks}
                return super().call_api(connector, action, params)

        api = TwoWeeks()
        out = run(api, interval="week")
        detail = [p for a, p in api.calls if a == "get_cogs_detail_for_period"]
        assert len(detail) == 2
        assert detail[0]["start"] == "2026-08-31T07:00:00+12:00"
        assert detail[0]["end"] == "2026-09-07T06:59:59+12:00"
        assert detail[1]["start"] == "2026-09-07T07:00:00+12:00"
        assert detail[1]["end"] == "2026-09-14T06:59:59+12:00"
        assert [t["bucket"] for t in out["trend"]] == ["2026-W36", "2026-W37"]
        assert out["trend"][0]["purchases"] == 1035.0
        assert out["trend"][0]["actual_cogs_pct"] is not None
        received = [p for a, p in api.calls if a == "get_received_items_for_period"]
        assert received[0]["group_by"] == "week"

    def test_failed_purchases_are_a_warning_not_a_failure(self):
        out = run(Api(fail={"get_received_items_for_period": "feed down"}))
        assert (
            out["summary"]["purchases"] is None
            and out["summary"]["actual_cogs_pct"] is None
        )
        assert out["summary"]["theoretical_cogs_pct"] is not None
        assert any("purchases unavailable" in w for w in out["warnings"])

    def test_a_failed_product_report_is_the_error(self):
        out = run(Api(fail={"get_cogs_detail_for_period": "Loaded down"}))
        assert out["error"].startswith("product costs: Loaded down")

    def test_a_bad_interval_is_refused(self):
        assert "interval must be" in run(Api(), interval="day")["error"]
