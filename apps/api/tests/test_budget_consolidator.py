"""config/consolidators/get_budgets.py — THE one budget surface.

Exec'd under the REAL sandbox namespace (no imports, injected datetime), so a
construct the sandbox forbids fails here rather than in production.

The facts pinned: Loaded dates each budget one day AFTER the day it belongs
to, and its from/to filter is to-exclusive over those shifted instants —
so the consolidator queries [F, T+1] and maps every date back a day
(21 Aug 2026: the venue's Thursday $22k rode Friday's date, and the daily
report's budget peak landed on Sunday while sales peaked Saturday).
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_budgets.py"
).read_text()


class Api:
    """Fake Loaded: rows dated one day late, filtered like the real API —
    to-exclusive over the shifted dates."""

    def __init__(self, rows):
        self.rows = rows  # {dated: (amount, tax)}
        self.calls = []
        self.resolved = None

    def call_api(self, connector, action, params=None):
        self.calls.append((connector, action, dict(params or {})))
        if action == "resolve_dates":
            return dict(self.resolved) if self.resolved else {"error": "offline"}
        assert action == "get_budgets_raw", action
        frm, to = params["from_date"], params["to_date"]
        return [
            {"id": f"b-{d}", "date": f"{d}T00:00:00+13:00", "amount": a, "salesTax": t}
            for d, (a, t) in sorted(self.rows.items())
            # the real filter: stored instant (local midnight of `dated`,
            # i.e. UTC of dated-1) in [from 00:00Z, to 00:00Z)
            if frm < d <= to
        ]


def run(api, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, namespace)
    return namespace["run"](
        {"venue": "The Glass Goose", **params}, api.call_api, lambda m: None
    )


# The live November week (the user's case): Loaded's dates, one day late.
NOV = {
    "2026-11-24": (5000.0, 0.15),
    "2026-11-25": (7000.0, 0.15),
    "2026-11-26": (10000.0, 0.15),
    "2026-11-27": (22000.0, 0.15),
    "2026-11-28": (30000.0, 0.15),
    "2026-11-29": (25000.0, 0.15),
    "2026-11-30": (6000.0, 0.15),
    # neighbours that must NOT leak into the week
    "2026-11-23": (4000.0, 0.15),
    "2026-12-01": (9999.0, 0.15),
}


class TestBudgetDates:
    def test_thursday_the_26th_is_22k(self):
        api = Api(NOV)
        out = run(api, from_date="2026-11-23", to_date="2026-11-29")
        by_date = {d["date"]: d for d in out["days"]}
        thu = by_date["2026-11-26"]
        assert thu["amount"] == 22000.0
        assert thu["day"] == "Thursday"
        # the full corrected week, nothing leaked from the neighbours
        assert [d["amount"] for d in out["days"]] == [
            5000.0, 7000.0, 10000.0, 22000.0, 30000.0, 25000.0, 6000.0,
        ]  # fmt: skip
        assert out["total"] == 105000.0
        assert out["days_without_budget"] == []

    def test_the_query_window_covers_the_ranges_last_day(self):
        api = Api(NOV)
        run(api, from_date="2026-11-23", to_date="2026-11-29")
        _, _, p = api.calls[0]
        assert p["from_date"] == "2026-11-23"
        assert p["to_date"] == "2026-11-30"  # T+1: the to-exclusive filter

    def test_single_date_defaults_to_a_week(self):
        api = Api(NOV)
        out = run(api, from_date="2026-11-23")
        assert out["from"] == "2026-11-23" and out["to"] == "2026-11-29"
        assert len(out["days"]) == 7

    def test_weekly_subtotals_and_gaps(self):
        rows = {
            "2026-11-24": (5000.0, 0.15),  # true Mon 23
            "2026-11-27": (22000.0, 0.15),  # true Thu 26
            "2026-12-01": (8000.0, 0.15),  # true Mon 30
        }
        api = Api(rows)
        out = run(api, from_date="2026-11-23", to_date="2026-11-30")
        assert [w["total"] for w in out["weeks"]] == [27000.0, 8000.0]
        assert out["weeks"][0]["week_start"] == "2026-11-23"
        assert out["weeks"][1]["week_start"] == "2026-11-30"
        assert "2026-11-24" in out["days_without_budget"]
        assert len(out["days_without_budget"]) == 5

    def test_reversed_and_bad_inputs(self):
        api = Api(NOV)
        out = run(api, from_date="2026-11-29", to_date="2026-11-23")
        assert out["from"] == "2026-11-23" and len(out["days"]) == 7
        try:
            run(api, from_date="next Tuesday")
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "from_date" in str(exc)

    def test_huge_range_is_refused(self):
        api = Api(NOV)
        out = run(api, from_date="2025-01-01", to_date="2026-12-31")
        assert "error" in out


class TestPeriodResolution:
    def test_period_resolves_to_calendar_dates(self):
        # "That week in November" as a trading week: Mon 07:00 - next Mon
        # 06:59. Budget days are calendar days, so the window becomes
        # Mon 23rd .. Sun 29th — the same seven true days as before, and the
        # off-by-one shift still puts Thursday's $22k on Thursday.
        api = Api(NOV)
        api.resolved = {
            "window": {
                "start": "2026-11-23T07:00:00+13:00",
                "end": "2026-11-30T06:59:59+13:00",
                "trading_aligned": True,
            }
        }
        out = run(api, period="that week in November")
        thursday = next(d for d in out["days"] if d["date"] == "2026-11-26")
        assert thursday["amount"] == 22000.0
        assert thursday["day"] == "Thursday"
        raw = next(p for (_c, a, p) in api.calls if a == "get_budgets_raw")
        # [F, T+1]: the to-exclusive query window over the shifted instants
        assert raw["from_date"] == "2026-11-23"
        assert raw["to_date"] == "2026-11-30"

    def test_unresolvable_period_is_an_error_not_a_guess(self):
        api = Api(NOV)
        out = run(api, period="the vibes of spring")
        assert "could not resolve" in out["error"]
        assert not [c for c in api.calls if c[1] == "get_budgets_raw"]

    def test_no_period_and_no_from_date_is_refused(self):
        out = run(Api(NOV))
        assert "period" in out["error"]
