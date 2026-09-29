"""Charts on TOOLS — the one mapping from a chart built on a raw endpoint to
the tool that answers the same question (Sep 2026: only tools reach an LLM,
and charts follow the same rule; docs/tool-architecture-strategy.md).

Used by ``scripts/migrate_charts_to_tools.py`` (saved charts, and the shared
dashboard templates) and by template instantiation, so a new dashboard is
never built on an endpoint again. A tool returns a document, so every mapped
script names the list to plot in ``script["rows"]`` (see
routers/reports_crud._rows_from_payload).
"""

from __future__ import annotations

import copy

HIDE_LABOUR = [
    "date",
    "rostered_cost",
    "actual_cost",
    "leave_hours",
    "leave_cost",
    "unrostered",
    "variance",
]


def _script(connector: str, action: str, params: dict, rows: str) -> dict:
    return {"connector": connector, "action": action, "params": params, "rows": rows}


def sales_today() -> dict:
    return _script("loadedhub", "get_sales", {"period": "today"}, "rows")


def pos_orders(interval: str) -> dict:
    return _script(
        "loadedhub",
        "get_pos_orders_for_period",
        {"period": "today", "interval": interval},
        "data",
    )


def labour(period: str, group_by: str) -> dict:
    return _script(
        "loadedhub",
        "get_labour",
        {"period": period, "view": "attendance", "group_by": group_by},
        "rows",
    )


def purchase_orders(**params) -> dict:
    return _script("loadedhub", "get_purchase_orders", {"limit": 200, **params}, "rows")


def to_tool_chart(title: str, chart_type: str, script: dict | None, spec: dict | None):
    """``(new_title, new_type, new_script, new_spec, note)``; None when the chart
    has no call (a component); ``"UNMAPPED"`` when nothing here knows it."""
    s = script or {}
    connector, action = s.get("connector"), s.get("action")
    if not action:
        return None  # a component (Hiring Board, Order Tracking) — no call
    old_params = s.get("params") or {}
    sp = copy.deepcopy(spec or {})
    t = title or ""

    if action == "get_sales_data" and t.startswith("Daily Sales"):
        new = _script(
            "loadedhub",
            "get_sales",
            {
                "start": old_params.get("start_datetime"),
                "end": old_params.get("end_datetime"),
                "confirmed_by_user": True,
                "breakdown": "daily",
            },
            "rows",
        )
        sp.update(
            {
                "x_axis": {"key": "date", "label": "Day"},
                "series": [{"key": "actual", "label": "Sales ($)", "color": "#4f8a5e"}],
            }
        )
        return t, chart_type, new, sp, "fixed week, one venue"
    if action == "get_sales_data" and t.startswith("Sales Today"):
        sp["value_key"] = "actual"
        return t, chart_type, sales_today(), sp, ""
    if action == "get_sales_data" and t.startswith("Venue Sales Breakdown"):
        sp["field_labels"] = {"actual": "Sales", "venue": "Venue"}
        sp["field_formats"] = {"actual": {"type": "currency", "align": "right"}}
        sp["hidden_fields"] = []
        return t, chart_type, sales_today(), sp, ""
    if action == "get_sales_data" and t.startswith("Sales Last 12 Hours"):
        sp.update(
            {
                "value_key": "amount",
                "group_by": "venue",
                "title": "Sales Today (30 min intervals)",
            }
        )
        return (
            "Sales Today (30 min intervals)",
            chart_type,
            pos_orders("00:30:00"),
            sp,
            "now the trading day so far",
        )
    if action == "get_sales_data" and t.startswith("Sales Trend"):
        sp.update(
            {
                "x_axis": {"key": "startTime", "label": "Time", "format": "time"},
                "series": [{"key": "amount", "label": "Sales ($)", "color": "#4f8a5e"}],
            }
        )
        return (
            t,
            chart_type,
            pos_orders("01:00:00"),
            sp,
            "plotted a field its call never returned",
        )
    if action == "get_sales_data" and t.startswith("Orders vs Items"):
        sp.update(
            {
                "x_axis": {"key": "startTime", "label": "Time", "format": "time"},
                "series": [{"key": "count", "label": "Orders", "color": "#4f8a5e"}],
            }
        )
        return (
            "Orders by Hour",
            chart_type,
            pos_orders("01:00:00"),
            sp,
            "items per hour has no source; orders only",
        )
    if action == "get_sales_data" and t.startswith("Sales by Venue"):
        sp.update(
            {
                "x_axis": {"key": "venue", "label": "Venue"},
                "series": [{"key": "actual", "label": "Sales ($)", "color": "#4f8a5e"}],
            }
        )
        return (
            t,
            chart_type,
            sales_today(),
            sp,
            "plotted a field its call never returned",
        )
    if action in ("get_pos_orders", "get_pos_sales") and t.startswith("Orders Today"):
        sp["value_key"] = "count"
        sp.pop("prefix", None)
        sp["format"] = "number"
        return (
            t,
            chart_type,
            pos_orders("1.00:00:00"),
            sp,
            "" if action == "get_pos_orders" else "called an action that doesn't exist",
        )
    if action == "get_pos_sales" and t.startswith("Items Sold"):
        sp["value_key"] = "quantity"
        return (
            t,
            chart_type,
            _script(
                "loadedhub",
                "get_sales",
                {"period": "today", "breakdown": "items"},
                "rows",
            ),
            sp,
            "called an action that doesn't exist",
        )
    if action == "get_budgets":
        sp["value_key"] = "amount"
        return (
            t,
            chart_type,
            _script("loadedhub", "get_budgets", {"period": "today"}, "days"),
            sp,
            "a tool already; now names its rows",
        )
    if action in ("get_roster_vs_actual", "get_timeclock_entries") and t.startswith(
        "Staff Hours by Venue"
    ):
        labels = dict(sp.get("field_labels") or {})
        labels.update(
            {
                "rostered_hours": "Rostered hours",
                "actual_hours": "Worked hours",
                "venue": "Venue",
            }
        )
        sp["field_labels"] = labels
        sp["hidden_fields"] = HIDE_LABOUR
        return t, chart_type, labour("today", "day"), sp, ""
    if action == "get_roster" and t.startswith("Staff On Duty"):
        sp.update(
            {"aggregate": "count", "value_key": "rostered_hours", "format": "number"}
        )
        return (
            "Staff Rostered Today",
            chart_type,
            labour("today", "staff"),
            sp,
            "counted a field its call never returned",
        )
    if action == "get_roster" and t.startswith("Hours This Week"):
        sp.update({"value_key": "rostered_hours", "format": "number"})
        return t, chart_type, labour("this week", "day"), sp, "now really this week"
    if action == "get_roster" and t.startswith("Hours by Team"):
        sp.update(
            {
                "x_axis": {"key": "role", "label": "Role"},
                "series": [
                    {"key": "rostered_hours", "label": "Hours", "color": "#4f8a5e"}
                ],
            }
        )
        return (
            "Hours by Role",
            chart_type,
            labour("today", "staff"),
            sp,
            "plotted fields its call never returned",
        )
    if action == "get_roster" and t.startswith("Roster Coverage"):
        sp.update(
            {
                "x_axis": {"key": "date", "label": "Day"},
                "series": [
                    {"key": "rostered_hours", "label": "Rostered", "color": "#4f8a5e"},
                    {"key": "actual_hours", "label": "Worked", "color": "#5b8abd"},
                ],
            }
        )
        return (
            "Rostered vs Worked (this week)",
            "bar",
            labour("this week", "day"),
            sp,
            "plotted fields its call never returned",
        )
    if action == "get_purchase_orders_summary" and t.startswith("Outstanding Orders"):
        sp.update({"aggregate": "count", "value_key": "total", "format": "number"})
        return t, chart_type, purchase_orders(), sp, "open (unreceived) purchase orders"
    if action == "get_purchase_orders_summary" and t.startswith("Pending Deliveries"):
        sp.update({"aggregate": "count", "value_key": "total", "format": "number"})
        return (
            t,
            chart_type,
            purchase_orders(status="Acknowledged"),
            sp,
            "open orders the supplier has acknowledged",
        )
    if action == "get_purchase_orders_summary" and t.startswith("Orders by Supplier"):
        sp.update(
            {
                "x_axis": {"key": "supplierName", "label": "Supplier"},
                "series": [{"key": "total", "label": "Amount", "color": "#b07d4f"}],
            }
        )
        return (
            t,
            chart_type,
            purchase_orders(),
            sp,
            "plotted fields its call never returned",
        )
    if connector == "bamboohr" and action == "get_jobs":
        sp.update({"aggregate": "count", "value_key": "title", "format": "number"})
        return (
            t,
            chart_type,
            _script("bamboohr", "get_hr", {"view": "jobs"}, "jobs"),
            sp,
            "",
        )
    if action == "get_monthly_spend" and t.startswith("Monthly Spend"):
        sp = {"value_key": "total", "format": "currency", "currency": "NZD"}
        return (
            "Supplier Spend This Month",
            chart_type,
            _script(
                "loadedhub",
                "get_invoices",
                {"kind": "received", "period": "this month", "limit": 1000},
                "rows",
            ),
            sp,
            "Xero connector never existed; now Loaded's received invoices",
        )
    if action == "get_monthly_spend" and t.startswith("Spend Trend"):
        sp.update(
            {
                "x_axis": {"key": "date", "label": "Day"},
                "series": [{"key": "total", "label": "Spend", "color": "#b07d4f"}],
            }
        )
        return (
            "Supplier Spend (last 3 months)",
            chart_type,
            _script(
                "loadedhub",
                "get_invoices",
                {"kind": "received", "period": "last 3 months", "limit": 1000},
                "rows",
            ),
            sp,
            "Xero connector never existed; now Loaded's received invoices",
        )
    return "UNMAPPED"
