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


def _sub_day(interval: str) -> bool:
    head = str(interval or "").split(":")[0]
    return "." not in head and head.isdigit() and int(head) < 24


def sales_by_interval(interval: str, measure: str = "sales") -> dict:
    """Today's sales (or orders) — one row per venue, or one per clock-time
    bucket when the interval is under a day. get_sales absorbed
    get_pos_orders_for_period on 1 Oct 2026 (measure 'orders')."""
    params: dict = {"period": "today"}
    if _sub_day(interval):
        params.update({"breakdown": "daily", "interval": interval})
    if measure == "orders":
        params["measure"] = "orders"
    return _script("loadedhub", "get_sales", params, "rows")


def _from_pos_orders_tool(t, chart_type, s, sp):
    """A chart already on get_pos_orders_for_period → get_sales.

    A chart that plots `count` is an orders chart: get_sales measure 'orders'
    reads the same orders feed (orders when placed). One that plots only
    `amount` is titled as sales ("Sales Today", "Sales Trend") and moves to
    the money taken — the pos-orders amount was the order value as rung up,
    which is not sales."""
    keys = [sp.get("value_key")] + [x.get("key") for x in sp.get("series") or []]
    measure = "orders" if "count" in keys else "sales"
    fields = (
        {"count": "orders", "amount": "order_value"}
        if measure == "orders"
        else {"amount": "actual"}
    )
    if sp.get("value_key") in fields:
        sp["value_key"] = fields[sp["value_key"]]
    if not sp.get("group_by") and sp.get("series"):
        sp["series"] = [
            {**x, "key": fields.get(x.get("key"), x.get("key"))} for x in sp["series"]
        ]
    interval = (s.get("params") or {}).get("interval") or "1.00:00:00"
    return (
        t,
        chart_type,
        sales_by_interval(interval, measure),
        sp,
        "get_pos_orders_for_period folded into get_sales",
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
                "value_key": "actual",
                "group_by": "venue",
                "title": "Sales Today (30 min intervals)",
            }
        )
        return (
            "Sales Today (30 min intervals)",
            chart_type,
            sales_by_interval("00:30:00"),
            sp,
            "now the trading day so far",
        )
    if action == "get_sales_data" and t.startswith("Sales Trend"):
        sp.update(
            {
                "x_axis": {"key": "startTime", "label": "Time", "format": "time"},
                "series": [{"key": "actual", "label": "Sales ($)", "color": "#4f8a5e"}],
            }
        )
        return (
            t,
            chart_type,
            sales_by_interval("01:00:00"),
            sp,
            "plotted a field its call never returned",
        )
    if action == "get_sales_data" and t.startswith("Orders vs Items"):
        sp.update(
            {
                "x_axis": {"key": "startTime", "label": "Time", "format": "time"},
                "series": [{"key": "orders", "label": "Orders", "color": "#4f8a5e"}],
            }
        )
        return (
            "Orders by Hour",
            chart_type,
            sales_by_interval("01:00:00", "orders"),
            sp,
            "orders by when placed; the orders feed carries no item counts",
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
    if action == "get_pos_orders_for_period":
        return _from_pos_orders_tool(t, chart_type, s, sp)
    if action in ("get_pos_orders", "get_pos_sales") and t.startswith("Orders Today"):
        sp["value_key"] = "orders"
        sp.pop("prefix", None)
        sp["format"] = "number"
        return (
            t,
            chart_type,
            sales_by_interval("1.00:00:00", "orders"),
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
