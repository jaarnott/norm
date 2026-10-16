"""get_purchase_cogs — purchases against sales for a venue with no stocktakes.

The canonical code from config/consolidators/ is exec'd under the REAL
sandbox namespace (the pattern of the other consolidator tests). The two
tools it joins are scripted: their row shapes are the ones
get_received_items_for_period (group_by month/week) and get_sales (daily;
items) return today.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "purchase_cogs.py"
).read_text(encoding="utf-8")

WINDOW = {
    "start": "2026-09-01T00:00:00+10:00",
    "end": "2026-09-30T23:59:59+10:00",
    "trading_aligned": True,
    "description": "September 2026",
}


def purchases_rows(venue=None):
    def row(bucket, start, food, bev, other, freight, credit=0.0, invoices=4):
        r = {
            "bucket": bucket,
            "bucket_start": start,
            "spend": round(food + bev + other, 2),
            "food": food,
            "beverage": bev,
            "other": other,
            "freight": freight,
            "credit_amount": credit,
            "invoice_subtotals": round(food + bev + other + freight, 2),
            "invoice_count": invoices,
        }
        if venue:
            r["venue"] = venue
        return r

    return [
        row("August 2026", "2026-08-01", 7000.0, 2000.0, 1000.0, 150.0),
        row("September 2026", "2026-09-01", 7896.63, 2177.78, 1383.19, 177.0, -92.19),
    ]


def sales_rows(venue=None):
    rows = []
    # August: 40,000 ex-GST over the month; September: 47,371.13
    for day, amount in (("2026-08-10", 20000.0), ("2026-08-20", 20000.0)):
        rows.append({"venue": venue, "date": day, "day": "Mon", "actual": amount})
    for day, amount in (("2026-09-05", 20000.0), ("2026-09-25", 27371.13)):
        rows.append({"venue": venue, "date": day, "day": "Fri", "actual": amount})
    return rows


ITEM_ROWS = [
    {"item": "Bacon & egg wrap", "group": "Food", "category": "Food", "sales": 6000.0},
    {"item": "Latte", "group": "Coffee", "category": "Beverage", "sales": 3000.0},
    {"item": "Mystery", "group": "", "category": "Uncategorised", "sales": 1000.0},
]


class Api:
    def __init__(self, venues=None, items=None, fail=None):
        self.venues = venues
        self.items = ITEM_ROWS if items is None else items
        self.fail = fail or {}
        self.calls = []

    def call_api(self, connector, action, params=None):
        self.calls.append((action, params or {}))
        if action in self.fail:
            return {"error": self.fail[action]}
        if action == "resolve_dates":
            return {"window": WINDOW}
        if action == "list_venues":
            return {
                "venues": [
                    {
                        "id": "id-" + v.lower().replace(" ", "-"),
                        "name": v,
                        "connected": True,
                    }
                    for v in (self.venues or ["Applegum Cafe"])
                ]
            }
        if action == "get_received_items_for_period":
            names = params.get("venues")
            if names and len(names) > 1:
                rows = []
                for v in names:
                    rows += purchases_rows(v)
            else:
                rows = purchases_rows()
            return {
                "rows": rows,
                "summary": {
                    "reconciliation": {"ok": True, "difference": 0.0, "invoices": 65}
                },
                "warnings": [],
            }
        if action == "get_sales":
            if params.get("breakdown") == "items":
                return {"rows": self.items}
            names = params.get("venues")
            if names and len(names) > 1:
                rows = []
                for v in names:
                    rows += sales_rows(v)
            else:
                rows = sales_rows()
            return {"rows": rows, "totals": {}}
        raise AssertionError(action)


def run(api, **params):
    """A dashboard-style call: the executor hands the tool this venue's id
    (tool_executor sets venue_id); pass venue_id=None to leave it out."""
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    merged = {"period": "September 2026", "venue_id": "id-applegum-cafe", **params}
    merged = {k: v for k, v in merged.items() if v is not None}
    return ns["run"](merged, api.call_api, lambda m: None)


class TestTheJoin:
    def test_purchases_over_sales_per_month_with_the_category_split(self):
        api = Api()
        out = run(api)
        assert out["interval"] == "month"
        assert [r["bucket"] for r in out["rows"]] == ["September 2026"]
        sep = out["rows"][0]
        # freight is in purchases by default
        assert sep["purchases"] == round(7896.63 + 2177.78 + 1383.19 + 177.0, 2)
        assert sep["sales"] == 47371.13
        assert sep["cogs_pct"] == round(sep["purchases"] / 47371.13, 4)
        # the period's POS category shares: 60% food, 30% beverage, 10% uncategorised
        assert sep["food_sales"] == round(47371.13 * 0.6, 2)
        assert sep["beverage_sales"] == round(47371.13 * 0.3, 2)
        assert sep["uncategorised_sales"] == round(47371.13 * 0.1, 2)
        assert sep["food_cogs_pct"] == round(7896.63 / (47371.13 * 0.6), 4)
        assert sep["beverage_cogs_pct"] == round(2177.78 / (47371.13 * 0.3), 4)
        assert "venue" not in sep

    def test_the_summary_carries_the_previous_period_and_the_reconciliation(self):
        out = run(Api())
        s = out["summary"]
        assert s["purchases"] == out["rows"][0]["purchases"]
        assert s["sales"] == 47371.13
        assert s["previous"]["purchases"] == 10150.0  # August incl. freight
        assert s["previous"]["sales"] == 40000.0
        assert s["previous"]["cogs_pct"] == round(10150.0 / 40000.0, 4)
        assert s["cogs_pct_change"] == round(
            s["cogs_pct"] - s["previous"]["cogs_pct"], 4
        )
        assert s["reconciliation"]["ok"] is True
        assert s["uncategorised_sales_pct"] == 0.1
        assert any("10.0% of sales" in w for w in out["warnings"])

    def test_the_reads_cover_the_previous_period_in_one_go(self):
        api = Api()
        run(api)
        got = {a: p for a, p in api.calls}
        assert (
            got["get_received_items_for_period"]["start"] == "2026-08-02T00:00:00+10:00"
        )
        assert got["get_received_items_for_period"]["end"] == WINDOW["end"]
        assert got["get_received_items_for_period"]["confirmed_by_user"] is True
        assert got["get_received_items_for_period"]["group_by"] == "month"
        daily = [
            p
            for a, p in api.calls
            if a == "get_sales" and p.get("breakdown") == "daily"
        ]
        assert (
            daily
            and daily[0]["tax"] == "exclude"
            and daily[0]["interval"] == "1.00:00:00"
        )
        items = [
            p
            for a, p in api.calls
            if a == "get_sales" and p.get("breakdown") == "items"
        ]
        assert (
            items and items[0]["period"] == "September 2026" and items[0]["top"] == 5000
        )

    def test_freight_can_be_left_out(self):
        out = run(Api(), include_freight=False)
        sep = out["rows"][0]
        assert sep["purchases"] == round(7896.63 + 2177.78 + 1383.19, 2)
        assert sep["freight"] == 177.0  # still reported
        assert out["summary"]["include_freight"] is False

    def test_no_previous_period_when_not_asked(self):
        api = Api()
        out = run(api, compare_previous=False)
        assert "previous" not in out["summary"]
        got = {a: p for a, p in api.calls}
        assert got["get_received_items_for_period"]["start"] == WINDOW["start"]


class TestEdges:
    def test_no_sales_gives_a_null_percentage_not_a_crash(self):
        class NoSales(Api):
            def call_api(self, connector, action, params=None):
                if action == "get_sales" and (params or {}).get("breakdown") == "daily":
                    self.calls.append((action, params))
                    return {"rows": [], "totals": {}}
                return super().call_api(connector, action, params)

        out = run(NoSales())
        sep = out["rows"][0]
        assert sep["sales"] == 0.0 and sep["cogs_pct"] is None
        assert sep["food_cogs_pct"] is None

    def test_uncategorised_sales_leave_the_split_null(self):
        items = [
            {"item": "x", "group": "", "category": "Uncategorised", "sales": 100.0}
        ]
        out = run(Api(items=items))
        sep = out["rows"][0]
        assert sep["food_sales"] == 0.0 and sep["food_cogs_pct"] is None
        assert out["summary"]["uncategorised_sales_pct"] == 1.0

    def test_a_failed_category_read_is_a_warning_not_a_failure(self):
        class NoItems(Api):
            def call_api(self, connector, action, params=None):
                if action == "get_sales" and (params or {}).get("breakdown") == "items":
                    return {"error": "boom"}
                return super().call_api(connector, action, params)

        out = run(NoItems())
        assert out["rows"][0]["cogs_pct"] is not None
        assert out["rows"][0]["food_cogs_pct"] is None
        assert any("sales by category unavailable" in w for w in out["warnings"])

    def test_a_failed_purchases_read_is_the_error(self):
        out = run(Api(fail={"get_received_items_for_period": "Loaded down"}))
        assert out["error"].startswith("purchases: Loaded down")

    def test_weeks_bucket_sales_on_iso_weeks(self):
        class Weekly(Api):
            def call_api(self, connector, action, params=None):
                if action == "get_received_items_for_period":
                    self.calls.append((action, params))
                    assert params["group_by"] == "week"
                    return {
                        "rows": [
                            {
                                "bucket": "2026-W36",
                                "bucket_start": "2026-08-31",
                                "food": 100.0,
                                "beverage": 0.0,
                                "other": 0.0,
                                "freight": 0.0,
                                "credit_amount": 0.0,
                                "invoice_count": 1,
                            },
                            {
                                "bucket": "2026-W39",
                                "bucket_start": "2026-09-21",
                                "food": 200.0,
                                "beverage": 0.0,
                                "other": 0.0,
                                "freight": 0.0,
                                "credit_amount": 0.0,
                                "invoice_count": 1,
                            },
                        ],
                        "summary": {},
                        "warnings": [],
                    }
                return super().call_api(connector, action, params)

        out = run(Weekly(), interval="week", compare_previous=False)
        by = {r["bucket"]: r for r in out["rows"]}
        # 5 Sep is in W36 (Mon 31 Aug), 25 Sep in W39 (Mon 21 Sep)
        assert (
            by["2026-W36"]["sales"] == 20000.0 and by["2026-W36"]["purchases"] == 100.0
        )
        assert (
            by["2026-W39"]["sales"] == 27371.13 and by["2026-W39"]["purchases"] == 200.0
        )

    def test_across_venues_rows_carry_the_venue(self):
        api = Api(venues=["Applegum Cafe", "Little West"])
        out = run(api, venues="all")
        assert {r["venue"] for r in out["rows"]} == {"Applegum Cafe", "Little West"}
        assert out["summary"]["venues"] == ["Applegum Cafe", "Little West"]
        assert out["summary"]["sales"] == 2 * 47371.13
        items = [
            p
            for a, p in api.calls
            if a == "get_sales" and p.get("breakdown") == "items"
        ]
        assert [p["venues"] for p in items] == [["Applegum Cafe"], ["Little West"]]

    def test_a_dashboard_call_names_the_venue_for_the_sales_reads(self):
        """A chart, an app or MCP hands the tool a venue_id; get_sales takes a
        venue by name — the Cost of Goods tiles were empty until the id was
        turned into the name (16 Oct 2026)."""
        api = Api()
        out = run(api, venue_id="id-applegum-cafe")
        assert "error" not in out and out["rows"][0]["sales"] == 47371.13
        sales_calls = [p for a, p in api.calls if a == "get_sales"]
        assert sales_calls and all(
            p.get("venue") == "Applegum Cafe" for p in sales_calls
        )
        assert all("venues" not in p for p in sales_calls)
        assert not any(a == "list_venues" for a, _ in api.calls[:1])  # dates first

    def test_no_venue_at_all_is_a_clear_error(self):
        out = run(Api(), venue_id=None)
        assert out["error"].startswith("no venue")

    def test_a_named_venue_is_used_as_given(self):
        api = Api()
        out = run(api, venue_id=None, venue="Little West")
        assert "error" not in out
        assert all(
            p.get("venue") == "Little West" for a, p in api.calls if a == "get_sales"
        )

    def test_a_bad_interval_is_refused(self):
        assert "interval must be" in run(Api(), interval="day")["error"]
