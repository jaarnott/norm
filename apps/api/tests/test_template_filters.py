"""Connector-spec Jinja filters.

shift_days is load-bearing for the LoadedHub budget fix: get_budgets templates
`from={{ from_date | shift_days(-1) }}` to work around LoadedHub dropping the
first day of a budget range, so the date arithmetic has to be exactly right.
"""

from app.connectors.spec_executor import _jinja_env
from app.connectors.template_filters import shift_days


class TestShiftDays:
    def test_shifts_a_bare_date_back_one(self):
        assert shift_days("2026-07-27", -1) == "2026-07-26"

    def test_shifts_forward(self):
        assert shift_days("2026-08-01", 1) == "2026-08-02"

    def test_ignores_time_and_offset(self):
        # resolve_dates hands out trading-day datetimes; only the date matters.
        assert shift_days("2026-07-27T07:00:00+12:00", -1) == "2026-07-26"

    def test_crosses_month_and_year_boundaries(self):
        assert shift_days("2026-03-01", -1) == "2026-02-28"
        assert shift_days("2026-01-01", -1) == "2025-12-31"

    def test_empty_is_empty(self):
        assert shift_days("", -1) == ""

    def test_days_may_arrive_as_a_string(self):
        # Jinja can pass filter args through as strings.
        assert shift_days("2026-07-27", "-1") == "2026-07-26"


class TestBudgetTemplateRenders:
    def test_get_budgets_path_shifts_only_the_from(self):
        tmpl = "//loadedhub.com/api/budgets?from={{ from_date | shift_days(-1) }}&to={{ to_date }}"
        out = _jinja_env.from_string(tmpl).render(
            from_date="2026-07-27", to_date="2026-08-02"
        )
        assert out == "//loadedhub.com/api/budgets?from=2026-07-26&to=2026-08-02"
