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

CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_sales.py"
)
CODE = CODE_PATH.read_text()

WINDOW = {
    "start": "2026-08-17T07:00:00+12:00",
    "end": "2026-08-24T06:59:59+12:00",
    "day_start": "07:00",
    "trading_aligned": True,
    "description": "This week",
}

# The REAL shape call_api hands back for internal norm.* tools: UNWRAPPED
# ({connector, venues}) — the live venues='all' path failed on first contact
# because the mock wrongly modelled a {data: ...} envelope. The consolidator
# tolerates both; the mock now matches production.
VENUES = {
    "connector": "loadedhub",
    "venues": [
        {"id": "v1", "name": "La Zeppa", "connected": True},
        {"id": "v2", "name": "Glass Goose", "connected": True},
        {"id": "v3", "name": "Mr Murdochs", "connected": False},
    ],
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
            # Loaded's real shape: one row per discount TYPE
            return [
                {
                    "discountTypeIdentifier": "d1",
                    "discountType": "20% Member Deal",
                    "discountsAmount": 100.0,
                    "discountsCount": 4,
                    "discountInvoices": 400.0,
                },
                {
                    "discountTypeIdentifier": "d2",
                    "discountType": "Staff Meal",
                    "discountsAmount": 50.0,
                    "discountsCount": 2,
                    "discountInvoices": 150.0,
                },
            ]
        if action == "get_sales_tax_rates":
            # GET /api/sales-tax, as Loaded returned it for La Zeppa
            return [
                {"sortOrder": 0, "label": "Exempt", "rate": 0.0},
                {"sortOrder": 1, "label": "GST", "rate": 0.15},
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

    def test_an_enveloped_list_venues_result_also_works(self):
        api = Api()

        def call(connector, action, p=None):
            if action == "list_venues":
                return {"success": True, "data": dict(VENUES)}
            return api._for(connector, action, p)

        from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {"venue": "La Zeppa", "period": "this week", "venues": "all"},
            call,
            api.log,
            None,
        )
        assert {r["venue"] for r in out["rows"]} == {"La Zeppa", "Glass Goose"}

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

    def test_items_time_windows_over_a_long_range_is_refused_not_faked(self):
        # 7 trading days x 4 windows = 28 day×window calls (> 20). The item feed
        # has no hour-of-day filter, so the old code fell back to ONE
        # month-spanning call per window (e.g. 17 Aug 12:00 → 23 Aug 15:00) —
        # a span of the whole range, not 12–3pm daily — so every window
        # returned ~the full-period total (an impossible "$194k at lunch").
        # It must refuse with guidance, never silently widen the answer.
        api = Api()
        out = run(
            api,
            breakdown="items",
            time_windows=[
                {"start_hour": 12, "end_hour": 15, "label": "lunch"},
                {"start_hour": 15, "end_hour": 18, "label": "arvo"},
                {"start_hour": 18, "end_hour": 21, "label": "dinner"},
                {"start_hour": 21, "end_hour": 23, "label": "late"},
            ],
        )
        assert out.get("error") and "max 20" in out["error"]
        # Refused BEFORE fetching — no garbage calls went to the feed.
        assert not [p for a, p in api.seen if a == "get_pos_item_sales"]

    def test_items_midnight_window_spans_into_the_next_day(self):
        # A window whose end hour is not past its start hour crosses midnight;
        # its end must land on the NEXT day. Same-day 23:00 → 03:00 is an empty
        # range that returned $0 for every after-midnight item.
        api = Api()
        out = run(
            api,
            breakdown="items",
            time_windows=[{"start_hour": 23, "end_hour": 3, "label": "late"}],
        )
        calls = [p for a, p in api.seen if a == "get_pos_item_sales"]
        assert len(calls) == 7  # 7 trading days x 1 window, per-day strategy
        assert calls[0]["start_time"].startswith("2026-08-17T23:00:00")
        assert calls[0]["end_time"].startswith("2026-08-18T03:00:00")
        assert out["rows"]  # a real, non-empty range now returns items


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
        # one row per discount type — this read a `label` field the feed
        # doesn't have, and merged every type into one "Unknown" row
        assert [r["discount"] for r in out["rows"]] == [
            "20% Member Deal",
            "Staff Meal",
        ]


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


# ── measure and tax (1 Oct 2026) ──────────────────────────────────────────
#
# get_pos_orders_for_period folded in as measure='orders', and a tax switch.
# The buckets below are REAL: La Zeppa, Mon 28 Sep 2026, two-hourly, as
# Loaded returned them. Invoices less invoicesTax is 1551.93 — exactly
# Loaded's own COGS "sales excluding tax" for that day.

LZ_28_SEP = [
    {
        "startTime": "2026-09-28T13:00:00+13:00",
        "invoices": 759.0,
        "invoicesTax": 110.52,
        "discounts": 305.0,
        "quantity": 58,
        "count": 15,
    },
    {
        "startTime": "2026-09-28T15:00:00+13:00",
        "invoices": 53.5,
        "invoicesTax": 6.98,
        "discounts": 0.0,
        "quantity": 4,
        "count": 3,
    },
    {
        "startTime": "2026-09-28T17:00:00+13:00",
        "invoices": 387.0,
        "invoicesTax": 48.55,
        "discounts": 0.0,
        "quantity": 22,
        "count": 13,
    },
    {
        "startTime": "2026-09-28T19:00:00+13:00",
        "invoices": 594.0,
        "invoicesTax": 75.52,
        "discounts": 209.0,
        "quantity": 39,
        "count": 15,
    },
]

DAY_WINDOW = {
    "start": "2026-09-28T07:00:00+13:00",
    "end": "2026-09-29T06:59:59+13:00",
    "day_start": "07:00",
    "trading_aligned": True,
    "description": "Monday",
}


# The same day from Loaded's ORDERS feed (/pos/orders, two-hourly, real):
# orders as they were PLACED, valued as rung up before discounts. 46 orders
# either way, but $2,307.50 ordered against $1,793.50 taken — and lunch
# orders land at 11:00 where their bills land at 13:00.
LZ_28_SEP_ORDERS = [
    {"startTime": "2026-09-28T11:00:00+13:00", "amount": 720.0, "count": 9},
    {"startTime": "2026-09-28T13:00:00+13:00", "amount": 344.0, "count": 6},
    {"startTime": "2026-09-28T15:00:00+13:00", "amount": 53.5, "count": 3},
    {"startTime": "2026-09-28T17:00:00+13:00", "amount": 1190.0, "count": 28},
]


class FeedApi(Api):
    """Api whose sales and orders feeds are fixed lists of buckets."""

    def __init__(
        self,
        buckets,
        window=DAY_WINDOW,
        last_year=None,
        orders=None,
        orders_last_year=None,
    ):
        super().__init__()
        self.buckets = buckets
        self.window = window
        self.last_year = last_year or []
        self.orders = LZ_28_SEP_ORDERS if orders is None else orders
        self.orders_last_year = orders_last_year or []
        self.budget_days = [
            {"date": "2026-09-28", "amount": 2300.0, "sales_tax_rate": 0.15}
        ]

    def _for(self, connector, action, params):
        p = dict(params or {})
        if action == "resolve_dates":
            self.seen.append((action, p))
            return {"window": dict(self.window)}
        if action == "get_sales_data":
            self.seen.append((action, p))
            if str(p.get("start_datetime", "")).startswith("2025"):
                return [dict(b) for b in self.last_year]
            return [dict(b) for b in self.buckets]
        if action == "get_pos_orders":
            self.seen.append((action, p))
            if str(p.get("start", "")).startswith("2025"):
                return [dict(b) for b in self.orders_last_year]
            return [dict(b) for b in self.orders]
        if action == "get_budgets":
            self.seen.append((action, p))
            days = [dict(d) for d in self.budget_days]
            return {"days": days, "total": sum(d["amount"] for d in days)}
        return super()._for(connector, action, params)


def run_day(api, **params):
    return run(api, period="monday", **params)


class TestTax:
    def test_tax_is_included_by_default_and_says_so(self):
        out = run_day(FeedApi(LZ_28_SEP))
        assert out["tax"] == "included"
        assert out["totals"]["actual"] == 1793.5

    def test_exclude_takes_off_the_tax_loaded_recorded(self):
        out = run_day(FeedApi(LZ_28_SEP), tax="exclude")
        assert out["tax"] == "excluded"
        assert out["rows"][0]["actual"] == 1551.93

    def test_an_unknown_tax_value_is_refused_not_guessed(self):
        out = run_day(FeedApi(LZ_28_SEP), tax="gst")
        assert "tax must be" in out["error"]

    def test_budgets_are_tax_inclusive_so_exclude_divides_by_the_rate(self):
        """Loaded's budget worksheet: budgetEx += amount / (1 + salesTax)."""
        api = FeedApi(LZ_28_SEP)
        incl = run_day(api, compare="budget")
        assert incl["rows"][0]["budget"] == 2300.0
        excl = run_day(api, compare="budget", tax="exclude")
        assert excl["rows"][0]["budget"] == 2000.0
        assert excl["rows"][0]["vs_budget"] == round(1551.93 - 2000.0, 2)

    def test_a_budget_without_a_rate_is_flagged_not_left_taxed(self):
        api = FeedApi(LZ_28_SEP)
        api.budget_days = [{"date": "2026-09-28", "amount": 2300.0}]
        out = run_day(api, compare="budget", tax="exclude")
        row = out["rows"][0]
        assert row["budget"] is None
        assert any("tax rate" in e for e in row["errors"])

    def test_daily_budget_ex_tax_too(self):
        api = FeedApi(LZ_28_SEP)
        out = run_day(api, breakdown="daily", compare="budget", tax="exclude")
        assert out["rows"][0]["budget"] == 2000.0
        assert out["totals"]["La Zeppa"]["budget"] == 2000.0

    def test_items_exclude_uses_the_gst_rate_as_loadeds_reports_do(self):
        """Not the effective rate inside the takings — exempt sales pull that
        down (12.8% over one real La Zeppa week)."""
        api = FeedApi(LZ_28_SEP)
        out = run_day(api, breakdown="items", tax="exclude")
        rows = {r["item"]: r for r in out["rows"]}
        assert rows["Pale Ale"]["sales"] == round(900.0 / 1.15, 2)
        assert out["tax"] == "excluded"
        assert "La Zeppa 15.0%" in out["note_tax"]

    def test_item_sales_are_marked_as_before_discounts(self):
        api = FeedApi(LZ_28_SEP)
        out = run_day(api, breakdown="items")
        assert out["note_amounts"] == "item sales are before discounts"
        assert out["tax"] == "included"
        # include costs no extra call for a rate
        assert not any(a == "get_sales_data" for a, _ in api.seen)

    def test_staff_and_discounts_exclude(self):
        api = FeedApi(LZ_28_SEP)
        staff = run_day(api, breakdown="staff", tax="exclude")
        assert staff["rows"][0]["sales"] == round(5000.0 / 1.15, 2)
        drill = run_day(api, breakdown="staff", staff_name="alice", tax="exclude")
        assert drill["totals"]["sales"] == round(500.0 / 1.15, 2)
        disc = run_day(api, breakdown="discounts", tax="exclude")
        assert disc["totals"]["discounts_amount"] == round(150.0 / 1.15, 2)
        assert disc["rows"][0]["discounted_invoices"] == round(400.0 / 1.15, 2)
        # counts are counts — no tax on them
        assert disc["totals"]["discounts_count"] == 6

    def test_a_venue_without_a_rate_keeps_its_tax_and_says_so(self):
        api = FeedApi(LZ_28_SEP)
        real = api._for

        def no_rate(connector, action, params):
            if action == "get_sales_tax_rates":
                return {"error": "403"}
            return real(connector, action, params)

        api._for = no_rate
        out = run_day(api, breakdown="items", tax="exclude")
        assert "NOT removed for La Zeppa" in out["note_tax"]
        assert {r["item"]: r for r in out["rows"]}["Pale Ale"]["sales"] == 900.0


class TestOrders:
    """Orders are what was PLACED (the orders feed), not what was paid."""

    def test_orders_are_placed_orders_from_the_orders_feed(self):
        api = FeedApi(LZ_28_SEP)
        out = run_day(api, measure="orders")
        assert out["measure"] == "orders"
        assert out["rows"] == [
            {
                "venue": "La Zeppa",
                "orders": 46,
                "order_value": 2307.5,
                "average_order": 50.16,
            }
        ]
        assert out["totals"]["orders"] == 46
        feeds = [a for a, _ in api.seen if a in ("get_pos_orders", "get_sales_data")]
        assert feeds == ["get_pos_orders"]
        fetch = next(p for a, p in api.seen if a == "get_pos_orders")
        assert fetch["start"] == DAY_WINDOW["start"]
        assert fetch["end"] == DAY_WINDOW["end"]

    def test_orders_and_sales_differ_by_design(self):
        """Orders are valued as rung up and counted whether or not they are
        paid; sales are what the bills took, after discounts."""
        api = FeedApi(LZ_28_SEP)
        sales = run_day(api)["totals"]["actual"]
        ordered = run_day(api, measure="orders")["totals"]["order_value"]
        assert (sales, ordered) == (1793.5, 2307.5)

    def test_orders_ex_tax_divides_the_value_by_the_gst_rate(self):
        out = run_day(FeedApi(LZ_28_SEP), measure="orders", tax="exclude")
        row = out["rows"][0]
        assert row["orders"] == 46
        assert row["order_value"] == round(2307.5 / 1.15, 2)
        assert out["tax"] == "excluded"
        assert "La Zeppa 15.0%" in out["note_tax"]

    def test_orders_against_budget_is_refused(self):
        out = run_day(FeedApi(LZ_28_SEP), measure="orders", compare="budget")
        assert "budgets are money" in out["error"]

    def test_orders_against_last_year_compares_counts(self):
        ly = [{"startTime": "2025-09-29T13:00:00+13:00", "amount": 900.0, "count": 40}]
        api = FeedApi(LZ_28_SEP, orders_last_year=ly)
        out = run_day(api, measure="orders", compare="last_year")
        row = out["rows"][0]
        assert row["last_year_orders"] == 40
        assert row["vs_last_year"] == 6
        assert out["totals"]["last_year_orders"] == 40
        ly_fetch = [
            p
            for a, p in api.seen
            if a == "get_pos_orders" and str(p["start"]).startswith("2025")
        ]
        assert len(ly_fetch) == 1

    def test_unknown_measure_is_refused(self):
        out = run_day(FeedApi(LZ_28_SEP), measure="covers")
        assert "measure must be" in out["error"]

    def test_orders_per_time_window_are_counted_when_placed(self):
        api = FeedApi(LZ_28_SEP)
        out = run_day(
            api,
            measure="orders",
            tax="exclude",
            time_windows=[{"start_hour": 11, "end_hour": 14, "label": "lunch"}],
        )
        # placed at 11:00 and 13:00 — the bills were paid from 13:00
        assert out["rows"][0]["lunch"] == 15
        assert isinstance(out["rows"][0]["lunch"], int)
        hourly = next(p for a, p in api.seen if a == "get_pos_orders")
        assert hourly["interval"] == "01:00:00"
        # counts carry no tax — no rate fetched
        assert not any(a == "get_sales_tax_rates" for a, _ in api.seen)


class TestClockTimeBuckets:
    """What the dashboards' 'Sales Today (30 min intervals)' and 'Orders by
    Hour' charts read — they used get_pos_orders_for_period until 1 Oct."""

    HALF_HOURS = [
        {"startTime": "2026-09-28T07:00:00+13:00", "invoices": 0, "count": 0},
        {"startTime": "2026-09-28T07:30:00+13:00", "invoices": 0, "count": 0},
        {
            "startTime": "2026-09-28T12:00:00+13:00",
            "invoices": 120.0,
            "invoicesTax": 15.65,
            "count": 3,
            "quantity": 6,
        },
        {"startTime": "2026-09-28T12:30:00+13:00", "invoices": 0, "count": 0},
        {
            "startTime": "2026-09-28T13:00:00+13:00",
            "invoices": 80.0,
            "invoicesTax": 10.43,
            "count": 2,
            "quantity": 3,
        },
        {
            "startTime": "2026-09-29T01:00:00+13:00",
            "invoices": 40.0,
            "invoicesTax": 5.22,
            "count": 1,
            "quantity": 2,
        },
        {"startTime": "2026-09-29T01:30:00+13:00", "invoices": 0, "count": 0},
        {"startTime": "2026-09-29T06:30:00+13:00", "invoices": 0, "count": 0},
    ]

    def test_buckets_carry_their_trading_day_and_clock_time(self):
        api = FeedApi(self.HALF_HOURS)
        out = run_day(api, breakdown="daily", interval="00:30:00")
        assert (
            next(p for a, p in api.seen if a == "get_sales_data")["interval"]
            == "00:30:00"
        )
        rows = out["rows"]
        assert [r["time"] for r in rows] == ["12:00", "12:30", "13:00", "01:00"]
        # 1am on the 29th is Monday's trade, not Tuesday's
        assert rows[-1]["date"] == "2026-09-28"
        assert rows[-1]["day"] == "Monday"
        assert rows[-1]["startTime"] == "2026-09-29T01:00:00+13:00"
        assert rows[0]["actual"] == 120.0
        # the empty slot BETWEEN trade stays, so the timeline is continuous
        assert rows[1]["actual"] == 0.0
        assert out["totals"]["La Zeppa"]["actual"] == 240.0

    def test_orders_by_hour_are_by_when_placed(self):
        placed = [
            {"startTime": "2026-09-28T07:00:00+13:00", "amount": 0, "count": 0},
            {"startTime": "2026-09-28T11:00:00+13:00", "amount": 720.0, "count": 9},
            {"startTime": "2026-09-28T12:00:00+13:00", "amount": 0, "count": 0},
            {"startTime": "2026-09-28T13:00:00+13:00", "amount": 344.0, "count": 6},
            {"startTime": "2026-09-29T00:00:00+13:00", "amount": 60.0, "count": 2},
            {"startTime": "2026-09-29T06:00:00+13:00", "amount": 0, "count": 0},
        ]
        api = FeedApi(self.HALF_HOURS, orders=placed)
        out = run_day(api, breakdown="daily", interval="01:00:00", measure="orders")
        assert next(p for a, p in api.seen if a == "get_pos_orders")["interval"] == (
            "01:00:00"
        )
        rows = out["rows"]
        assert [(r["time"], r["orders"]) for r in rows] == [
            ("11:00", 9),
            ("12:00", 0),
            ("13:00", 6),
            ("00:00", 2),
        ]
        assert rows[-1]["date"] == "2026-09-28"  # midnight is Monday's trade
        assert out["totals"]["La Zeppa"]["orders"] == 17
        assert out["totals"]["La Zeppa"]["order_value"] == 1124.0

    def test_budget_needs_a_daily_bucket(self):
        out = run_day(
            FeedApi(self.HALF_HOURS),
            breakdown="daily",
            interval="01:00:00",
            compare="budget",
        )
        assert "per day" in out["error"]

    def test_a_weekly_bucket_carries_the_whole_weeks_budget(self):
        """It used to carry only its first day's."""
        api = FeedApi(
            [{"startTime": "2026-08-17T07:00:00+12:00", "invoices": 20000.0}],
            window=WINDOW,
        )
        api.budget_days = [
            {"date": "2026-08-17", "amount": 7000.0, "sales_tax_rate": 0.15},
            {"date": "2026-08-22", "amount": 9000.0, "sales_tax_rate": 0.15},
        ]
        out = run(api, breakdown="daily", interval="7.00:00:00", compare="budget")
        assert out["rows"][0]["budget"] == 16000.0
        assert out["rows"][0]["vs_budget"] == 4000.0


class TestTheShape:
    """call_api shapes get_sales_data with config/consolidators/shapes.json.
    Until 1 Oct the shape dropped tax, orders and items, and rounded sales to
    whole dollars — neither switch could have worked."""

    def test_the_shape_keeps_what_get_sales_reads(self):
        import json

        from app.connectors.response_transform import apply_response_transform

        shapes = json.loads(
            (pathlib.Path(CODE_PATH).parent / "shapes.json").read_text()
        )
        shape = shapes["loadedhub.get_sales"]["loadedhub.get_sales_data"]
        raw = [
            {
                "startTime": "2026-09-28T00:00:00+00:00",
                "period": "00:00:00",
                "invoices": 759.0,
                "invoicesTax": 110.52,
                "discounts": 305.0,
                "surcharges": 0.0,
                "quantity": 58,
                "count": 15,
            }
        ]
        row = apply_response_transform(raw, shape, "Pacific/Auckland")[0]
        assert row == {
            "startTime": "2026-09-28T13:00:00+13:00",
            "invoices": 759.0,
            "invoicesTax": 110.52,
        }

    def test_the_orders_feed_is_shaped_to_venue_time(self):
        """Its startTime comes back in UTC; unshaped, every bucket would key
        by the wrong date and clock time."""
        import json

        from app.connectors.response_transform import apply_response_transform

        shapes = json.loads(
            (pathlib.Path(CODE_PATH).parent / "shapes.json").read_text()
        )
        shape = shapes["loadedhub.get_sales"]["loadedhub.get_pos_orders"]
        raw = [
            {
                "startTime": "2026-09-27T22:00:00+00:00",
                "period": "00:00:00",
                "amount": 720.0,
                "count": 9,
            }
        ]
        row = apply_response_transform(raw, shape, "Pacific/Auckland")[0]
        assert row == {
            "startTime": "2026-09-28T11:00:00+13:00",
            "amount": 720.0,
            "count": 9,
        }

    def test_the_installer_carries_code_and_shapes(self):
        import importlib.util

        path = pathlib.Path(CODE_PATH).parents[2] / "scripts" / "sync_sales_config.py"
        spec = importlib.util.spec_from_file_location("sync_sales_config", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tool = mod.build_tool()
        cfg = tool["consolidator_config"]
        assert cfg["function_code"] == CODE
        assert "loadedhub.get_sales_data" in cfg["shapes"]
        assert tool["field_schema"]["measure"]["enum"] == ["sales", "orders"]
        assert tool["field_schema"]["tax"]["enum"] == ["include", "exclude"]
