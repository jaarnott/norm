"""Charts on tools (Sep 2026: an LLM — and the charts it builds — only ever use
tools, never raw endpoints).

A tool returns a document (``{"rows": [...], "totals": …, "window": …}``, or a
named list such as ``jobs`` / ``days``), where the raw endpoints charts were
built on returned a bare list. Two things had to learn that:

- the chart tool (``render_chart``) looked for rows only under data / items /
  lines / results, so a chart built from any tool came out EMPTY — and saved no
  way for a refresh to find the list again;
- the refresh wrapped a non-list payload as one row, so a refreshed tool chart
  plotted the whole document as a single point.

And a dashboard's date filter must reach a tool's plain-English ``period``.
"""

from app.agents.internal_tools import _find_data_path
from app.routers.reports_crud import _apply_global_dates, _rows_from_payload

DOC = {
    "window": {"start": "2026-09-28T07:00:00+13:00"},
    "rows": [{"venue": "La Zeppa", "actual": 1794.0}],
    "totals": {"actual": 1794.0},
}


class TestFindingTheRows:
    def test_a_tool_document_names_its_rows(self):
        assert _find_data_path(DOC) == ("rows", DOC["rows"])

    def test_named_lists_are_found(self):
        jobs = {"view": "jobs", "jobs": [{"title": "Chef"}], "total": 1}
        assert _find_data_path(jobs) == ("jobs", jobs["jobs"])
        budgets = {
            "venue": "X",
            "days": [{"date": "2026-09-29", "amount": 5}],
            "total": 5,
        }
        assert _find_data_path(budgets)[0] == "days"

    def test_old_shapes_still_work(self):
        assert _find_data_path([{"a": 1}]) == (None, [{"a": 1}])
        assert _find_data_path({"data": {"items": [{"a": 1}]}}) == (
            "data.items",
            [{"a": 1}],
        )

    def test_no_list_means_no_rows(self):
        assert _find_data_path({"total": 5}) == (None, None)


class TestRefreshRows:
    def test_the_saved_path_picks_the_list(self):
        assert _rows_from_payload(DOC, {"rows": "rows"}) == DOC["rows"]

    def test_a_dotted_path(self):
        assert _rows_from_payload(
            {"data": {"items": [{"a": 1}]}}, {"rows": "data.items"}
        ) == [{"a": 1}]

    def test_a_missing_path_is_no_rows_not_a_crash(self):
        assert _rows_from_payload({"totals": {}}, {"rows": "rows"}) == []

    def test_without_a_path_the_old_behaviour(self):
        assert _rows_from_payload([{"a": 1}], {}) == [{"a": 1}]
        assert _rows_from_payload({"a": 1}, {}) == [{"a": 1}]


class TestDashboardDates:
    def test_a_picked_window_replaces_the_period_and_is_confirmed(self):
        out = _apply_global_dates(
            {"period": "today", "breakdown": "daily"},
            {"start": "2026-09-01T07:00:00+12:00", "end": "2026-09-08T06:59:59+12:00"},
        )
        assert "period" not in out
        assert out["start"] == "2026-09-01T07:00:00+12:00"
        assert out["confirmed_by_user"] is True
        assert out["breakdown"] == "daily"

    def test_no_filter_leaves_the_period(self):
        assert _apply_global_dates({"period": "today"}, {}) == {"period": "today"}

    def test_explicit_date_keys_are_overwritten_as_before(self):
        out = _apply_global_dates(
            {"start_datetime": "x", "end_datetime": "y"}, {"start": "S", "end": "E"}
        )
        assert out == {"start_datetime": "S", "end_datetime": "E"}


class TestChartsCallToolsOnly:
    """A saved chart may only call a tool — the agent's rule, applied to charts."""

    def _spec(self, db_session):
        from app.db.config_models import ConnectionSpec

        db_session.add(
            ConnectionSpec(
                connector_name="chart_acme",
                display_name="Acme",
                execution_mode="template",
                auth_type="none",
                auth_config={},
                tools=[
                    {
                        "action": "get_things",
                        "consolidator_config": {
                            "function_code": "def run(p, c, l): return {}"
                        },
                    }
                ],
                endpoints=[
                    {"action": "get_raw", "method": "GET", "path_template": "/r"}
                ],
            )
        )
        db_session.flush()

    def test_an_endpoint_script_is_refused(self, db_session):
        import pytest
        from fastapi import HTTPException

        from app.routers.reports_crud import _require_tool

        self._spec(db_session)
        with pytest.raises(HTTPException) as e:
            _require_tool({"connector": "chart_acme", "action": "get_raw"}, db_session)
        assert e.value.status_code == 400
        _require_tool({"connector": "chart_acme", "action": "get_things"}, db_session)
        _require_tool({}, db_session)  # a component chart has no call

    def test_the_chart_builder_lists_tools_only(self, db_session):
        from app.connectors.tool_executor import list_connector_tools

        self._spec(db_session)
        listed = [
            t["action"] for t in list_connector_tools("chart_acme", db_session)["tools"]
        ]
        assert listed == ["get_things"]


class TestTheMapping:
    def test_every_production_chart_style_maps_to_a_tool(self):
        from app.services.chart_tools import to_tool_chart

        cases = [
            (
                "Sales Today",
                "kpi",
                {"connector": "loadedhub", "action": "get_sales_data", "params": {}},
            ),
            (
                "Staff Hours by Venue",
                "table",
                {
                    "connector": "loadedhub",
                    "action": "get_timeclock_entries",
                    "params": {},
                },
            ),
            (
                "Roster Coverage",
                "stacked_bar",
                {"connector": "loadedhub", "action": "get_roster", "params": {}},
            ),
            (
                "Open Jobs",
                "kpi",
                {"connector": "bamboohr", "action": "get_jobs", "params": {}},
            ),
            (
                "Monthly Spend",
                "kpi",
                {"connector": "xero", "action": "get_monthly_spend", "params": {}},
            ),
        ]
        tools = {
            "get_sales",
            "get_labour",
            "get_hr",
            "get_invoices",
            "get_purchase_orders",
            "get_budgets",
        }
        for title, ctype, script in cases:
            out = to_tool_chart(title, ctype, script, {})
            assert isinstance(out, tuple), title
            new_script = out[2]
            assert new_script["action"] in tools and new_script.get("rows"), title

    def test_pos_orders_charts_move_onto_get_sales(self):
        """get_pos_orders_for_period folded into get_sales (measure 'orders')
        on 1 Oct 2026. The four production chart styles, as stored."""
        from app.services.chart_tools import to_tool_chart

        def pos(interval):
            return {
                "connector": "loadedhub",
                "action": "get_pos_orders_for_period",
                "params": {"period": "today", "interval": interval},
                "rows": "data",
            }

        _, _, script, spec, _ = to_tool_chart(
            "Orders Today",
            "kpi",
            pos("1.00:00:00"),
            {"value_key": "count", "format": "number"},
        )
        assert script == {
            "connector": "loadedhub",
            "action": "get_sales",
            "params": {"period": "today", "measure": "orders"},
            "rows": "rows",
        }
        assert spec["value_key"] == "orders"

        venues = [{"key": "La Zeppa", "label": "La Zeppa"}]
        _, _, script, spec, _ = to_tool_chart(
            "Sales Today (30 min intervals)",
            "stacked_bar",
            pos("00:30:00"),
            {
                "x_axis": {"key": "startTime", "format": "time"},
                "series": venues,
                "group_by": "venue",
                "value_key": "amount",
            },
        )
        assert script["params"] == {
            "period": "today",
            "breakdown": "daily",
            "interval": "00:30:00",
        }
        assert spec["value_key"] == "actual"
        assert spec["series"] == venues  # venue series keys are names

        _, _, script, spec, _ = to_tool_chart(
            "Sales Trend",
            "line",
            pos("01:00:00"),
            {"x_axis": {"key": "startTime"}, "series": [{"key": "amount"}]},
        )
        assert "measure" not in script["params"]
        assert spec["series"] == [{"key": "actual"}]

        _, _, script, spec, _ = to_tool_chart(
            "Orders by Hour",
            "bar",
            pos("01:00:00"),
            {"x_axis": {"key": "startTime"}, "series": [{"key": "count"}]},
        )
        assert script["params"]["measure"] == "orders"
        assert script["params"]["interval"] == "01:00:00"
        assert spec["series"] == [{"key": "orders"}]

    def test_a_component_is_left_alone(self):
        from app.services.chart_tools import to_tool_chart

        assert to_tool_chart("Hiring Board", "component", None, {}) is None


class TestDatePlaceholders:
    def test_a_placeholder_inside_a_word_is_left_alone(self):
        """'Acknowledged' contains 'now'; a substring replace turned it into
        'Ack2026-…ledged' and the Pending Deliveries chart matched nothing."""
        from app.routers.reports_crud import _resolve_date_placeholders

        out = _resolve_date_placeholders(
            {"status": "Acknowledged", "end": "now", "start": "today_start"}
        )
        assert out["status"] == "Acknowledged"
        assert out["end"][:2] == "20" and out["start"][:2] == "20"
