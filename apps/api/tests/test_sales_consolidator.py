"""get_sales — THE sales domain tool: breakdowns, fan-out, engine-side joins.

Exec'd under the REAL sandbox namespace. The facts pinned: venues='all'
fans out over norm.list_venues' CONNECTED venues; the budget/last-year
joins are computed here with last year fixed at exactly 364 days back (one
baseline per call — prod thread b9bda2c1 quoted two); an erroring venue
becomes a flagged row excluded from totals, never a stall or a silent gap;
the time-window cut attributes pre-day-start hours to the PREVIOUS trading
day (civil-midnight bucketing under-reported a Saturday by $4.5k); items
and staff return top rows + an '(others)' rollup whose totals stay honest.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_sales.py"
).read_text()

WINDOW = {
    "start": "2026-08-17T07:00:00+12:00",
    "end": "2026-08-24T06:59:59+12:00",
    "day_start": "07:00",
    "trading_aligned": True,
    "description": "This week",
}

VENUES = {
    "data": {
        "venues": [
            {"id": "v1", "name": "La Zeppa", "connected": True},
            {"id": "v2", "name": "Glass Goose", "connected": True},
            {"id": "v3", "name": "Mr Murdochs", "connected": False},
        ]
    }
}


class Api:
    def __init__(self):
        self.seen = []
        self.sales = {"La Zeppa": 57078.0, "Glass Goose": 66498.0}
        self.ly = {"La Zeppa": 16728.0, "Glass Goose": 0.0}
        self.budgets = {"La Zeppa": 53775.0, "Glass Goose": 61213.0}
        self.fail_venue = None

    def _for(self, connector, action, params):
        p = dict(params or {})
        self.seen.append((action, p))
        if action == "resolve_dates":
            return {"window": dict(WINDOW)}
        if action == "list_venues":
            return {
                k: (dict(v) if isinstance(v, dict) else v) for k, v in VENUES.items()
            }
        v = p.get("venue")
        if action == "get_sales_data":
            if v == self.fail_venue:
                return {"error": "Loaded timed out"}
            table = (
                self.ly
                if str(p.get("start_datetime", "")).startswith("2025")
                else self.sales
            )
            return [
                {"startTime": p.get("start_datetime"), "invoices": table.get(v, 0.0)}
            ]
        if action == "get_budgets":
            return {
                "total": self.budgets.get(v),
                "days": [
                    {"date": "2026-08-17", "day": "Monday", "amount": 7000.0},
                    {"date": "2026-08-22", "day": "Saturday", "amount": 9000.0},
                ],
            }
        if action == "get_pos_item_sales":
            return [
                {
                    "itemName": "Pale Ale",
                    "itemGroupName": "Beverage",
                    "itemCategoryName": "Beer",
                    "amount": 900.0,
                    "quantity": 90,
                },
                {
                    "itemName": "Burger",
                    "itemGroupName": "Food",
                    "itemCategoryName": "Mains",
                    "amount": 700.0,
                    "quantity": 35,
                },
                {
                    "itemName": "Fries",
                    "itemGroupName": "Food",
                    "itemCategoryName": "Sides",
                    "amount": 200.0,
                    "quantity": 40,
                },
            ]
        if action == "get_staff_orders":
            if p.get("staff_id"):
                return [
                    {"startTime": "2026-08-17T18:00:00+12:00", "amount": 120.0},
                    {"startTime": "2026-08-18T18:00:00+12:00", "amount": 80.0},
                ]
            return [
                {"label": "Alice A", "id": "s1", "amount": 5000.0, "quantity": 80},
                {"label": "Bob B", "id": "s2", "amount": 3000.0, "quantity": 50},
                {"label": "No Sales", "id": "s3", "amount": 0.0, "quantity": 0},
            ]
        if action == "get_staff_item_orders":
            return [
                {"itemName": "Pale Ale", "amount": 300.0, "quantity": 30},
                {"itemName": "Burger", "amount": 200.0, "quantity": 10},
            ]
        if action == "get_pos_discounts":
            return [
                {
                    "label": "Alice A",
                    "discountsAmount": 100.0,
                    "discountsCount": 4,
                    "discountInvoices": 400.0,
                },
                {
                    "label": "Bob B",
                    "discountsAmount": 50.0,
                    "discountsCount": 2,
                    "discountInvoices": 150.0,
                },
            ]
        raise AssertionError(f"unexpected action {action}")

    def call_api(self, connector, action, params=None):
        return self._for(connector, action, params)

    def call_api_parallel(self, calls):
        return [self._for(c, a, p) for (c, a, p) in calls]

    def log(self, m):
        pass


def run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    return ns["run"](
        {"venue": "La Zeppa", "period": "this week", **params},
        api.call_api,
        api.log,
        api.call_api_parallel,
    )


class TestTotals:
    def test_single_venue_default_is_one_totals_row(self):
        api = Api()
        out = run(api)
        assert out["window"] == WINDOW
        assert out["rows"] == [{"venue": "La Zeppa", "actual": 57078.0}]
        assert out["totals"]["actual"] == 57078.0
        # Whole window in ONE bucket — a trading week ends at 06:59 the
        # next Monday, so it spans 8 civil dates.
        fetch = next(p for a, p in api.seen if a == "get_sales_data")
        assert fetch["interval"] == "8.00:00:00"

    def test_no_period_is_refused(self):
        api = Api()
        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {"venue": "La Zeppa"}, api.call_api, api.log, api.call_api_parallel
        )
        assert "period" in out["error"]

    def test_all_venues_budget_and_last_year_in_one_call(self):
        api = Api()
        out = run(api, venues="all", compare=["budget", "last_year"])
        rows = {r["venue"]: r for r in out["rows"]}
        # Connected venues only — the disconnected one is not queried.
        assert set(rows) == {"La Zeppa", "Glass Goose"}
        lz = rows["La Zeppa"]
        assert lz["actual"] == 57078.0
        assert lz["budget"] == 53775.0
        assert lz["vs_budget"] == 3303.0
        assert lz["last_year"] == 16728.0
        assert lz["vs_last_year"] == 40350.0
        assert out["totals"]["actual"] == 123576.0
        assert out["totals"]["budget"] == 114988.0
        # One deterministic LY baseline, exactly 364 days back.
        assert out["last_year_window"]["start"].startswith("2025-08-18T07:00:00")
        ly_fetches = [
            p
            for a, p in api.seen
            if a == "get_sales_data"
            and str(p.get("start_datetime", "")).startswith("2025")
        ]
        assert {p["start_datetime"] for p in ly_fetches} == {
            "2025-08-18T07:00:00+12:00"
        }

    def test_a_failing_venue_is_a_flagged_row_not_a_silent_gap(self):
        api = Api()
        api.fail_venue = "Glass Goose"
        out = run(api, venues="all", compare=["budget"])
        gg = next(r for r in out["rows"] if r["venue"] == "Glass Goose")
        assert any("timed out" in e for e in gg["errors"])
        assert "Glass Goose" in out["note"]
        # Totals exclude the failed venue's actual but keep the good one.
        assert out["totals"]["actual"] == 57078.0

    def test_explicit_venue_list_is_used_verbatim(self):
        api = Api()
        out = run(api, venues=["La Zeppa"], compare="budget")
        assert [r["venue"] for r in out["rows"]] == ["La Zeppa"]
        assert not [a for a, _ in api.seen if a == "list_venues"]

    def test_budget_window_stops_at_the_trading_weeks_last_day(self):
        api = Api()
        run(api, venues=["La Zeppa"], compare="budget")
        b = next(p for a, p in api.seen if a == "get_budgets")
        assert b["from_date"] == "2026-08-17"
        assert b["to_date"] == "2026-08-23"  # Mon..Sun, not the 07:00 Monday

    def test_incomplete_window_rides_into_the_result(self):
        api = Api()
        incomplete = dict(WINDOW)
        incomplete["incomplete"] = True

        def call_api(connector, action, params=None):
            if action == "resolve_dates":
                return {"window": dict(incomplete)}
            return api._for(connector, action, params)

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {"venue": "La Zeppa", "period": "this week", "venues": "all"},
            call_api,
            api.log,
            None,
        )
        assert out["window"]["incomplete"] is True


class TestDaily:
    def test_daily_budget_join_is_by_civil_date(self):
        api = Api()

        def sales(connector, action, params=None):
            p = dict(params or {})
            api.seen.append((action, p))
            if action == "resolve_dates":
                return {"window": dict(WINDOW)}
            if action == "get_budgets":
                return api._for(connector, action, params)
            return [
                {"startTime": "2026-08-17T07:00:00+12:00", "invoices": 8000.0},
                {"startTime": "2026-08-22T07:00:00+12:00", "invoices": 9500.0},
            ]

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {
                "venue": "La Zeppa",
                "period": "this week",
                "breakdown": "daily",
                "compare": "budget",
            },
            sales,
            api.log,
            None,
        )
        rows = {r["date"]: r for r in out["rows"]}
        assert rows["2026-08-17"]["budget"] == 7000.0
        assert rows["2026-08-17"]["vs_budget"] == 1000.0
        assert rows["2026-08-22"]["vs_budget"] == 500.0
        assert rows["2026-08-17"]["day"] == "Monday"
        assert out["totals"]["La Zeppa"]["actual"] == 17500.0

    def test_daily_last_year_aligns_by_weekday_not_date(self):
        api = Api()

        def sales(connector, action, params=None):
            p = dict(params or {})
            api.seen.append((action, p))
            if action == "resolve_dates":
                return {"window": dict(WINDOW)}
            if str(p.get("start_datetime", "")).startswith("2025"):
                # Last year's Monday: 2025-08-18 (364 days before 2026-08-17)
                return [{"startTime": "2025-08-18T07:00:00+12:00", "invoices": 5000.0}]
            return [{"startTime": "2026-08-17T07:00:00+12:00", "invoices": 8000.0}]

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {
                "venue": "La Zeppa",
                "period": "this week",
                "breakdown": "daily",
                "compare": "last_year",
            },
            sales,
            api.log,
            None,
        )
        row = out["rows"][0]
        assert row["date"] == "2026-08-17"
        assert row["last_year"] == 5000.0
        assert row["vs_last_year"] == 3000.0


class TestTimeWindows:
    def test_pre_day_start_hours_belong_to_the_previous_trading_day(self):
        """The b9bda2c1 pin: a Saturday's 1am trade is Saturday's."""
        api = Api()

        def sales(connector, action, params=None):
            p = dict(params or {})
            api.seen.append((action, p))
            if action == "resolve_dates":
                return {"window": dict(WINDOW)}
            assert p.get("interval") == "01:00:00"
            # Saturday 22 Aug 20:00 + Sunday 23 Aug 01:00 (pre-day-start).
            return [
                {"startTime": "2026-08-22T20:00:00+12:00", "invoices": 1000.0},
                {"startTime": "2026-08-23T01:00:00+12:00", "invoices": 500.0},
            ]

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {
                "venue": "La Zeppa",
                "period": "this week",
                "time_windows": [{"start_hour": 20, "end_hour": 3, "label": "late"}],
                "group_by": "each",
            },
            sales,
            api.log,
            None,
        )
        assert len(out["rows"]) == 1
        row = out["rows"][0]
        # Both hours land on SATURDAY 22 Aug: the 1am hour crosses back.
        assert row["period"].startswith("Saturday 22")
        assert row["late"] == 1500.0
        assert out["totals"]["La Zeppa"]["late"] == 1500.0
        # And the fetch runs day-start to day-start, not civil midnight.
        fetch = next(p for a, p in api.seen if a == "get_sales_data")
        assert "T07:00:00" in fetch["start_datetime"]

    def test_compare_does_not_combine_with_time_windows(self):
        api = Api()
        out = run(
            api,
            compare="budget",
            time_windows=[{"start_hour": 17, "end_hour": 22, "label": "dinner"}],
        )
        assert "compare" in out["error"]


class TestItems:
    def test_items_are_merged_ranked_and_rolled_up(self):
        api = Api()
        out = run(api, breakdown="items", top=2)
        names = [r["item"] for r in out["rows"]]
        assert names == ["Pale Ale", "Burger", "(others)"]
        others = out["rows"][-1]
        assert others["sales"] == 200.0  # Fries rolled up, total stays honest
        assert out["totals"]["sales"] == 1800.0
        assert out["totals"]["row_count"] == 3
        assert "top 2 of 3" in out["note"]

    def test_items_category_filter(self):
        api = Api()
        out = run(api, breakdown="items", category="Beer")
        assert [r["item"] for r in out["rows"]] == ["Pale Ale"]

    def test_items_group_by_month_keeps_a_trend_row_per_month(self):
        api = Api()
        window = dict(WINDOW)
        window["start"] = "2026-07-01T07:00:00+12:00"
        window["end"] = "2026-08-24T06:59:59+12:00"

        def call(connector, action, p=None):
            if action == "resolve_dates":
                return {"window": window}
            return api._for(connector, action, p)

        from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {
                "venue": "La Zeppa",
                "period": "last 2 months",
                "breakdown": "items",
                "group_by": "month",
            },
            call,
            api.log,
            api.call_api_parallel,
        )
        ales = [r for r in out["rows"] if r["item"] == "Pale Ale"]
        assert {r["period"] for r in ales} == {"July 2026", "August 2026"}

    def test_items_time_windows_get_per_window_columns(self):
        api = Api()
        out = run(
            api,
            breakdown="items",
            time_windows=[{"start_hour": 17, "end_hour": 22, "label": "dinner"}],
        )
        row = out["rows"][0]
        assert "dinner sales" in row and "dinner qty" in row
        # 7 trading days x 1 window = 7 calls, the per-day strategy.
        calls = [p for a, p in api.seen if a == "get_pos_item_sales"]
        assert len(calls) == 7
        assert calls[0]["start_time"].endswith("T17:00:00+12:00")


class TestStaff:
    def test_staff_ranked_with_honest_totals(self):
        api = Api()
        out = run(api, breakdown="staff")
        assert [r["staff"] for r in out["rows"]] == ["Alice A", "Bob B"]
        assert out["totals"]["sales"] == 8000.0
        assert out["totals"]["staff_count"] == 2  # zero-sales staff dropped

    def test_staff_name_drills_into_one_persons_items(self):
        api = Api()
        out = run(api, breakdown="staff", staff_name="alice")
        assert out["staff"] == "Alice A"
        assert [r["item"] for r in out["rows"]] == ["Pale Ale", "Burger"]
        assert out["totals"]["sales"] == 500.0
        drill = next(p for a, p in api.seen if a == "get_staff_item_orders")
        assert drill["staff_id"] == "s1"

    def test_unknown_staff_name_lists_who_did_sell(self):
        api = Api()
        out = run(api, breakdown="staff", staff_name="zorro")
        assert "no staff member" in out["error"]
        assert "Alice A" in out["staff_with_sales"]


class TestDiscounts:
    def test_discounts_totals(self):
        api = Api()
        out = run(api, breakdown="discounts")
        assert out["totals"]["discounts_amount"] == 150.0
        assert out["totals"]["discounts_count"] == 6
        assert out["rows"][0]["staff"] == "Alice A"


class TestRecurringPeriods:
    """'every Friday for the last 3 weeks' resolves to a periods LIST; the
    envelope becomes the window and the day filter fills in automatically
    (ported from the periodic engines, which owned this before)."""

    PERIODS = [
        {"start": "2026-08-07T07:00:00+12:00", "end": "2026-08-08T06:59:59+12:00"},
        {"start": "2026-08-14T07:00:00+12:00", "end": "2026-08-15T06:59:59+12:00"},
        {"start": "2026-08-21T07:00:00+12:00", "end": "2026-08-22T06:59:59+12:00"},
    ]

    def _run(self, **params):
        api = Api()

        def call(connector, action, p=None):
            p = dict(p or {})
            api.seen.append((action, p))
            if action == "resolve_dates":
                return {"periods": [dict(x) for x in self.PERIODS]}
            if action == "get_sales_data":
                # One Friday and one Monday bucket: only Fridays may count.
                return [
                    {"startTime": "2026-08-07T07:00:00+12:00", "invoices": 900.0},
                    {"startTime": "2026-08-10T07:00:00+12:00", "invoices": 111.0},
                    {"startTime": "2026-08-14T07:00:00+12:00", "invoices": 800.0},
                ]
            return api._for(connector, action, p)

        from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        return api, ns["run"](
            {"venue": "La Zeppa", "period": "every friday for 3 weeks", **params},
            call,
            api.log,
            None,
        )

    def test_daily_keeps_only_the_matching_weekday(self):
        api, out = self._run(breakdown="daily")
        assert out["day_of_week"] == "friday"
        dates = [r["date"] for r in out["rows"]]
        assert dates == ["2026-08-07", "2026-08-14"]  # the Monday is dropped
        assert out["totals"]["La Zeppa"]["actual"] == 1700.0
        assert out["window"]["recurring"] is True

    def test_total_collapses_the_filtered_days(self):
        api, out = self._run()
        assert out["rows"] == [{"venue": "La Zeppa", "actual": 1700.0}]
        assert out["day_of_week"] == "friday"

    def test_items_without_time_windows_is_refused(self):
        api, out = self._run(breakdown="items")
        assert "day of week" in out["error"]

    def test_unresolvable_phrase_is_an_error_not_a_guess(self):
        from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

        api = Api()

        def call(connector, action, p=None):
            if action == "resolve_dates":
                return {}
            raise AssertionError("must not fetch without a window")

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {"venue": "La Zeppa", "period": "the vibes era"}, call, api.log, None
        )
        assert "Could not resolve" in out["error"]


class TestVenueAllRefusal:
    """'all' is not a venue: the resolver used to fall through to an
    arbitrary credential row and answer a group question with ONE venue's
    data (thread b9bda2c1 — 'all' returned only La Zeppa)."""

    def test_all_is_refused_with_guidance(self):
        import pytest

        from app.agents.tool_loop import _resolve_venue_config

        for value in ("all", "All Venues", "*", "group"):
            with pytest.raises(ValueError, match="get_sales"):
                _resolve_venue_config("loadedhub", {"venue": value}, None)
