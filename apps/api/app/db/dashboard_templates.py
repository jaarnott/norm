"""Pre-built dashboard templates that users can instantiate with one click."""

DASHBOARD_TEMPLATES: list[dict] = [
    # ------------------------------------------------------------------
    # Reports / Sales dashboard
    # ------------------------------------------------------------------
    {
        "slug": "sales-overview",
        "agent_slug": "reports",
        "title": "Sales Overview",
        "description": "Revenue KPIs, sales trends, and category breakdowns across your venues.",
        "charts": [
            {
                "title": "Sales Today",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "invoices",
                    "format": "currency",
                    "prefix": "$",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_pos_sales",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "1.00:00:00",
                    },
                },
                "layout": {"col": 1, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Orders Today",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "count",
                    "format": "number",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_pos_sales",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "1.00:00:00",
                    },
                },
                "layout": {"col": 9, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Items Sold",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "quantity",
                    "format": "number",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_pos_sales",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "1.00:00:00",
                    },
                },
                "layout": {"col": 17, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Sales Trend",
                "chart_type": "line",
                "chart_spec": {
                    "x_axis": {"key": "startTime", "label": "Date"},
                    "series": [
                        {"key": "invoices", "label": "Sales ($)", "color": "#4f8a5e"},
                    ],
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_pos_sales",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "01:00:00",
                        "_all_venues": True,
                    },
                },
                "layout": {"col": 1, "row": 5, "colSpan": 16, "rowSpan": 8},
            },
            {
                "title": "Orders vs Items",
                "chart_type": "bar",
                "chart_spec": {
                    "x_axis": {"key": "startTime", "label": "Time"},
                    "series": [
                        {"key": "count", "label": "Orders", "color": "#4f8a5e"},
                        {"key": "quantity", "label": "Items", "color": "#5b8abd"},
                    ],
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_pos_sales",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "01:00:00",
                    },
                },
                "layout": {"col": 17, "row": 5, "colSpan": 8, "rowSpan": 8},
            },
            {
                "title": "Sales by Venue",
                "chart_type": "bar",
                "chart_spec": {
                    "x_axis": {"key": "venue", "label": "Venue"},
                    "series": [
                        {"key": "invoices", "label": "Sales ($)", "color": "#4f8a5e"},
                    ],
                    "orientation": "horizontal",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_pos_sales",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "1.00:00:00",
                        "_all_venues": True,
                    },
                },
                "layout": {"col": 1, "row": 13, "colSpan": 24, "rowSpan": 8},
            },
        ],
    },
    # ------------------------------------------------------------------
    # HR dashboard
    # ------------------------------------------------------------------
    {
        "slug": "hr-overview",
        "agent_slug": "hr",
        "title": "HR Overview",
        "description": "Staff on duty, hiring pipeline, roster coverage, and hours summary.",
        "charts": [
            {
                "title": "Staff On Duty",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "on_duty_count",
                    "format": "number",
                    "label": "right now",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_roster",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                    },
                },
                "layout": {"col": 1, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Open Jobs",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "open_count",
                    "format": "number",
                },
                "script": {
                    "connector": "bamboohr",
                    "action": "get_jobs",
                    "params": {},
                },
                "layout": {"col": 9, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Hours This Week",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "total_hours",
                    "format": "number",
                    "delta_key": "delta_pct",
                    "delta_label": "vs last week",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_roster",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                    },
                },
                "layout": {"col": 17, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Hiring Board",
                "chart_type": "component",
                "chart_spec": {
                    "component_key": "hiring_board",
                    "title": "Hiring Pipeline",
                    "component_props": {"connector_name": "bamboohr"},
                },
                "script": {},
                "layout": {"col": 1, "row": 5, "colSpan": 24, "rowSpan": 10},
            },
            {
                "title": "Hours by Team",
                "chart_type": "bar",
                "chart_spec": {
                    "x_axis": {"key": "team", "label": "Team"},
                    "series": [
                        {"key": "hours", "label": "Hours", "color": "#5b8abd"},
                    ],
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_roster",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "_all_venues": True,
                    },
                },
                "layout": {"col": 1, "row": 15, "colSpan": 12, "rowSpan": 8},
            },
            {
                "title": "Roster Coverage",
                "chart_type": "stacked_bar",
                "chart_spec": {
                    "x_axis": {"key": "day", "label": "Day"},
                    "series": [
                        {"key": "filled", "label": "Filled", "color": "#4f8a5e"},
                        {"key": "open", "label": "Open", "color": "#dc3545"},
                    ],
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_roster",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                    },
                },
                "layout": {"col": 13, "row": 15, "colSpan": 12, "rowSpan": 8},
            },
        ],
    },
    # ------------------------------------------------------------------
    # Procurement dashboard
    # ------------------------------------------------------------------
    {
        "slug": "procurement-overview",
        "agent_slug": "procurement",
        "title": "Procurement Overview",
        "description": "Outstanding orders, monthly spend, supplier breakdown, and order tracking.",
        "charts": [
            {
                "title": "Outstanding Orders",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "outstanding_count",
                    "format": "number",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_orders_summary",
                    "params": {},
                },
                "layout": {"col": 1, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Monthly Spend",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "monthly_spend",
                    "format": "currency",
                    "currency": "NZD",
                    "delta_key": "delta_pct",
                    "delta_label": "vs last month",
                },
                "script": {
                    "connector": "xero",
                    "action": "get_monthly_spend",
                    "params": {},
                },
                "layout": {"col": 9, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Pending Deliveries",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "pending_count",
                    "format": "number",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_orders_summary",
                    "params": {},
                },
                "layout": {"col": 17, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Recent Orders",
                "chart_type": "component",
                "chart_spec": {
                    "component_key": "orders_dashboard",
                    "title": "Order Tracking",
                },
                "script": {},
                "layout": {"col": 1, "row": 5, "colSpan": 24, "rowSpan": 10},
            },
            {
                "title": "Orders by Supplier",
                "chart_type": "bar",
                "chart_spec": {
                    "x_axis": {"key": "supplier", "label": "Supplier"},
                    "series": [
                        {"key": "amount", "label": "Amount", "color": "#b07d4f"},
                    ],
                    "orientation": "horizontal",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_orders_summary",
                    "params": {"_all_venues": True},
                },
                "layout": {"col": 1, "row": 15, "colSpan": 12, "rowSpan": 8},
            },
            {
                "title": "Spend Trend",
                "chart_type": "line",
                "chart_spec": {
                    "x_axis": {"key": "month", "label": "Month"},
                    "series": [
                        {"key": "spend", "label": "Spend", "color": "#b07d4f"},
                    ],
                },
                "script": {
                    "connector": "xero",
                    "action": "get_monthly_spend",
                    "params": {"period": "last_6_months"},
                },
                "layout": {"col": 13, "row": 15, "colSpan": 12, "rowSpan": 8},
            },
        ],
    },
]


def get_template(slug: str) -> dict | None:
    """Return a template by slug, or None."""
    for t in DASHBOARD_TEMPLATES:
        if t["slug"] == slug:
            return t
    return None
