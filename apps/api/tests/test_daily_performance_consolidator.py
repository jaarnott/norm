"""get_daily_performance — the operations join: sales incl/ex GST, budget,
last year, orders and labour on one row per trading day per venue.

The canonical code from config/consolidators/ is exec'd under the REAL
sandbox namespace. The tools it composes are scripted in the shapes they
return today: get_sales daily rows (actual / budget / last_year; orders,
order_value) and get_labour attendance by day (a formatted `date` label).
"""

from tests import consolidator_harness as harness

CODE = harness.source("daily_performance")

WINDOW = {
    "start": "2026-10-12T07:00:00+13:00",
    "end": "2026-10-19T06:59:59+13:00",
    "trading_aligned": True,
    "description": "This week (Mon 12 – Sun 18 Oct 2026)",
    "day_start": "07:00",
}

DAYS = ["2026-10-12", "2026-10-13", "2026-10-14"]
LABELS = {
    "2026-10-12": "Mon 12 Oct",
    "2026-10-13": "Tue 13 Oct",
    "2026-10-14": "Wed 14 Oct",
}


def sales_rows(venue, tax="include"):
    factor = 1.0 if tax == "include" else 1 / 1.15
    out = []
    for i, d in enumerate(DAYS):
        actual = 1150.0 * (i + 1)
        row = {
            "venue": venue,
            "date": d,
            "day": LABELS[d][:3],
            "actual": round(actual * factor, 2),
        }
        if tax == "include":
            row["last_year"] = 1100.0 * (i + 1)
        out.append(row)
    return out


def budget_days():
    return [
        {"date": d, "day": LABELS[d][:3], "amount": 1000.0 * (i + 1)}
        for i, d in enumerate(DAYS)
    ]


def order_rows(venue):
    return [
        {
            "venue": venue,
            "date": d,
            "day": LABELS[d][:3],
            "orders": 10 * (i + 1),
            "order_value": 1150.0 * (i + 1),
            "average_order": 115.0,
        }
        for i, d in enumerate(DAYS)
    ]


def labour_days():
    return [
        {
            "date": LABELS[d],
            "rostered_hours": 20.0,
            "actual_hours": 22.0 + i,
            "rostered_cost": 500.0,
            "actual_cost": 550.0 + 25 * i,
            "variance": 2.0 + i,
            "unrostered": 0,
        }
        for i, d in enumerate(DAYS)
    ]


class Api:
    def __init__(self, venues=None, fail=None, labour_group=False):
        self.venues = venues
        self.fail = fail or {}
        self.calls = []
        self.labour_group = labour_group

    def call_api(self, connector, action, params=None):
        params = params or {}
        self.calls.append((action, params))
        if action in self.fail:
            return {"error": self.fail[action]}
        if action == "resolve_dates":
            return {"window": WINDOW}
        if action == "list_venues":
            names = self.venues or ["La Zeppa"]
            return {
                "venues": [
                    {
                        "id": "id-" + n.lower().replace(" ", "-"),
                        "name": n,
                        "connected": True,
                    }
                    for n in names
                ]
            }
        names = params.get("venues") or (
            [params["venue"]] if params.get("venue") else [None]
        )
        if action == "get_sales":
            rows = []
            for v in names:
                rows += (
                    order_rows(v)
                    if params.get("measure") == "orders"
                    else sales_rows(v, params.get("tax", "include"))
                )
            return {"rows": rows, "totals": {}}
        if action == "get_budgets":
            assert (
                params.get("venue")
                and params.get("from_date")
                and params.get("to_date")
            )
            return {"days": budget_days(), "total": 6000.0}
        if action == "get_labour":
            if len(names) > 1 or self.labour_group:
                return {
                    "rows": [
                        {
                            "venue": v,
                            "rostered_hours": 60.0,
                            "actual_hours": 69.0,
                            "days": labour_days(),
                        }
                        for v in names
                    ],
                    "totals": {},
                }
            return {"rows": labour_days(), "totals": {}}
        raise AssertionError(action)


def run(api, **params):
    merged = {"period": "this week", "venue_id": "id-la-zeppa", **params}
    merged = {k: v for k, v in merged.items() if v is not None}
    return harness.run(CODE, api, **merged)


class TestTheJoin:
    def test_one_row_per_trading_day_with_every_figure(self):
        api = Api()
        out = run(api)
        assert out["interval"] == "day"
        assert [r["bucket_start"] for r in out["rows"]] == DAYS
        mon = out["rows"][0]
        assert mon["bucket"] == "Mon 12 Oct"
        assert (
            mon["sales"] == 1150.0
            and mon["budget"] == 1000.0
            and mon["last_year"] == 1100.0
        )
        assert mon["sales_ex_gst"] == round(1150.0 / 1.15, 2)
        assert mon["budget_pct"] == 1.15
        assert mon["vs_last_year_pct"] == round(50 / 1100, 4)
        assert mon["orders"] == 10 and mon["average_order"] == 115.0
        assert mon["rostered_hours"] == 20.0 and mon["actual_hours"] == 22.0
        assert mon["hours_variance"] == 2.0
        assert mon["wage_pct"] == round(550.0 / (1150.0 / 1.15), 4)
        assert "venue" not in mon
        # the by-venue row exists for one venue too (a chart draws it)
        assert out["by_venue"][0]["venue"] == "La Zeppa"
        assert out["by_venue"][0]["sales"] == 1150.0 * 6

    def test_the_summary_adds_the_days_and_recomputes_the_ratios(self):
        s = run(Api())["summary"]
        assert s["sales"] == 1150.0 * 6 and s["budget"] == 6000.0
        assert s["budget_pct"] == 1.15
        assert s["orders"] == 60 and s["average_order"] == 115.0
        assert s["actual_hours"] == 69.0 and s["hours_variance"] == 9.0
        assert s["wage_pct"] == round((550 + 575 + 600) / (1150.0 * 6 / 1.15), 4)
        assert s["buckets"] == 3

    def test_the_venue_id_a_dashboard_sends_becomes_the_name_the_tools_need(self):
        api = Api()
        run(api)
        for action, p in api.calls:
            if action in ("get_sales", "get_labour"):
                assert p.get("venue") == "La Zeppa" and "venues" not in p
                assert p["confirmed_by_user"] is True and p["start"] == WINDOW["start"]
        sales = [p for a, p in api.calls if a == "get_sales"]
        # budgets come from get_budgets directly: compare='budget' would nest
        # the budgets consolidator two deep, which the sandbox refuses
        assert any(p.get("compare") == ["last_year"] for p in sales)
        assert not any("budget" in (p.get("compare") or []) for p in sales)
        budget_calls = [p for a, p in api.calls if a == "get_budgets"]
        assert budget_calls and budget_calls[0]["from_date"] == "2026-10-12"
        assert budget_calls[0]["to_date"] == "2026-10-18"
        assert any(p.get("tax") == "exclude" for p in sales)
        assert any(p.get("measure") == "orders" for p in sales)

    def test_weeks_add_the_days_up(self):
        out = run(Api(), interval="week")
        assert len(out["rows"]) == 1
        wk = out["rows"][0]
        assert wk["bucket"] == "2026-W42" and wk["bucket_start"] == "2026-10-12"
        assert wk["sales"] == 1150.0 * 6 and wk["orders"] == 60

    def test_across_venues_rows_carry_the_venue_and_by_venue_sums_each(self):
        api = Api(venues=["La Zeppa", "Mr Murdochs"])
        out = run(api, venues="all")
        assert {r["venue"] for r in out["rows"]} == {"La Zeppa", "Mr Murdochs"}
        assert len(out["rows"]) == 6
        by = {b["venue"]: b for b in out["by_venue"]}
        assert (
            by["La Zeppa"]["sales"] == 1150.0 * 6
            and by["La Zeppa"]["actual_hours"] == 69.0
        )
        assert out["summary"]["sales"] == 2 * 1150.0 * 6
        assert out["summary"]["venues"] == ["La Zeppa", "Mr Murdochs"]


class TestEdges:
    def test_a_failed_sales_read_is_the_error(self):
        out = run(Api(fail={"get_sales": "Loaded down"}))
        assert out["error"].startswith("sales: Loaded down")

    def test_a_failed_labour_read_leaves_labour_blank_with_a_warning(self):
        out = run(Api(fail={"get_labour": "roster unavailable"}))
        mon = out["rows"][0]
        assert (
            mon["sales"] == 1150.0
            and mon["actual_hours"] is None
            and mon["wage_pct"] is None
        )
        assert any("labour: roster unavailable" in w for w in out["warnings"])

    def test_no_venue_at_all_is_a_clear_error(self):
        out = run(Api(), venue_id=None)
        assert out["error"].startswith("no venue")

    def test_a_failed_budget_read_leaves_budget_blank_with_a_warning(self):
        out = run(Api(fail={"get_budgets": "no budgets"}))
        assert out["rows"][0]["budget"] is None and out["rows"][0]["budget_pct"] is None
        assert any("budget: no budgets" in w for w in out["warnings"])

    def test_a_bad_interval_is_refused(self):
        assert "interval must be" in run(Api(), interval="hour")["error"]

    def test_the_labour_labels_resolve_against_the_windows_year(self):
        out = run(Api(labour_group=True))
        assert [r["actual_hours"] for r in out["rows"]] == [22.0, 23.0, 24.0]
