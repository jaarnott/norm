"""The norm_reports periodic consolidators' period resolution.

Exec'd under the REAL sandbox namespace. These tools required the model to
supply exact period_start/period_end dates it computed itself — the
documented incident class (test_mcp_execution.py:279: a Saturday reported
midnight-to-midnight after Claude routed around the date-safe tools). The
facts pinned here: `period` in plain English resolves through
norm.resolve_dates; a recurring phrase becomes the envelope of its resolved
days (with the weekday filter inferred when every day lands on the same
weekday); explicit dates remain the fallback and never call the resolver;
the staff tool (no weekday filter) refuses recurring phrases instead of
silently summing the whole envelope.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
SALES = (_DIR / "get_periodic_sales.py").read_text()
PRODUCT = (_DIR / "get_periodic_product_sales.py").read_text()
STAFF = (_DIR / "get_periodic_staff_sales.py").read_text()

WEEK_WINDOW = {
    "window": {
        "start": "2026-08-10T07:00:00+12:00",
        "end": "2026-08-17T06:59:59+12:00",
        "trading_aligned": True,
    }
}

FRIDAYS = {
    "data": {
        "periods": [
            {"label": "Fri 31 Jul", "start": "2026-07-31", "end": "2026-07-31"},
            {"label": "Fri 7 Aug", "start": "2026-08-07", "end": "2026-08-07"},
            {"label": "Fri 14 Aug", "start": "2026-08-14", "end": "2026-08-14"},
        ]
    }
}


class Api:
    """Records every call; loadedhub reads return empty datasets."""

    def __init__(self, resolved=None):
        self.resolved = resolved
        self.seen = []
        self.logs = []

    def _for(self, connector, action, params):
        self.seen.append((action, dict(params or {})))
        if action == "resolve_dates":
            return dict(self.resolved) if self.resolved else {"error": "offline"}
        return []

    def call_api(self, connector, action, params=None):
        return self._for(connector, action, params)

    def call_api_parallel(self, calls):
        return [self._for(c, a, p) for (c, a, p) in calls]

    def log(self, m):
        self.logs.append(str(m))


def run(code, api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(code, ns)
    return ns["run"](
        {"venue": "La Zeppa", **params}, api.call_api, api.log, api.call_api_parallel
    )


WINDOWS = [{"label": "Dinner", "start": "18:00", "end": "21:00"}]


class TestPeriodResolution:
    def test_simple_phrase_resolves_to_the_trading_week_dates(self):
        api = Api(resolved=WEEK_WINDOW)
        run(SALES, api, period="last week", time_windows=WINDOWS)
        fetch = next(p for a, p in api.seen if a == "get_sales_data")
        assert fetch["start_datetime"].startswith("2026-08-10T00:00:00")
        # The trading week's end lands Mon 06:59 — Monday is NOT one of the
        # asked-for days, so the fetch stops after Sunday the 16th.
        assert fetch["end_datetime"].startswith("2026-08-17T00:00:00")

    def test_recurring_phrase_becomes_envelope_and_infers_the_weekday(self):
        api = Api(resolved=FRIDAYS)
        run(
            SALES, api, period="every friday for the last 3 weeks", time_windows=WINDOWS
        )
        fetch = next(p for a, p in api.seen if a == "get_sales_data")
        assert fetch["start_datetime"].startswith("2026-07-31")
        assert any("filtering to friday" in m for m in api.logs)

    def test_product_tool_resolves_too(self):
        api = Api(resolved=WEEK_WINDOW)
        run(PRODUCT, api, period="last week")
        fetch = next(p for a, p in api.seen if a == "get_pos_item_sales")
        assert fetch["start_time"].startswith("2026-08-10")

    def test_staff_tool_refuses_recurring_phrases(self):
        api = Api(resolved=FRIDAYS)
        out = run(STAFF, api, period="every friday for the last 3 weeks")
        assert "continuous range" in out["error"]
        assert not [x for x in api.seen if x[0] == "get_staff_orders"]

    def test_explicit_dates_never_call_the_resolver(self):
        api = Api()
        run(
            SALES,
            api,
            period_start="2026-08-01",
            period_end="2026-08-07",
            time_windows=WINDOWS,
        )
        assert not [x for x in api.seen if x[0] == "resolve_dates"]
        fetch = next(p for a, p in api.seen if a == "get_sales_data")
        assert fetch["start_datetime"].startswith("2026-08-01")

    def test_unresolvable_phrase_is_an_error_not_a_guess(self):
        api = Api()  # resolver offline
        out = run(SALES, api, period="the vibe era", time_windows=WINDOWS)
        assert "could not resolve" in out["error"]
        assert not [x for x in api.seen if x[0] == "get_sales_data"]
