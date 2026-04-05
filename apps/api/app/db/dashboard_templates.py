"""Pre-built dashboard templates — seed data for the config DB."""

DASHBOARD_TEMPLATES: list[dict] = [
    {
        "slug": "procurement-dashboard",
        "agent_slug": "procurement",
        "title": "Procurement Overview",
        "description": "Outstanding orders, monthly spend, supplier breakdown, and order tracking.",
        "charts": [
            {
                "title": "Outstanding Orders",
                "chart_type": "kpi",
                "chart_spec": {"value_key": "outstanding_count", "format": "number"},
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
                "chart_spec": {"value_key": "pending_count", "format": "number"},
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
                        {"key": "amount", "label": "Amount", "color": "#b07d4f"}
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
                    "series": [{"key": "spend", "label": "Spend", "color": "#b07d4f"}],
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
    {
        "slug": "reports-dashboard",
        "agent_slug": "reports",
        "title": "Operations Dashboard",
        "description": "Live overview across all venues",
        "charts": [
            {
                "title": "Orders Today",
                "chart_type": "kpi",
                "chart_spec": {
                    "chart_type": "kpi",
                    "title": "Orders Today",
                    "x_axis": {"key": "orders", "label": "Orders"},
                    "series": [{"key": "orders", "label": "Orders"}],
                    "prefix": "$",
                    "format": "number",
                    "value_key": "amount",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_pos_orders",
                    "params": {
                        "start": "today_start",
                        "end": "now",
                        "interval": "1.00:00:00",
                    },
                },
                "layout": {"col": 1, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Sales Today",
                "chart_type": "kpi",
                "chart_spec": {
                    "chart_type": "kpi",
                    "title": "Sales Today",
                    "x_axis": {"key": "sales", "label": "Sales"},
                    "series": [{"key": "sales", "label": "Sales"}],
                    "comparison_key": "",
                    "prefix": "$",
                    "format": "number",
                    "value_key": "amount",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_sales_data",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "1.00:00:00",
                    },
                },
                "layout": {"col": 9, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Budget Today",
                "chart_type": "kpi",
                "chart_spec": {
                    "chart_type": "kpi",
                    "title": "Budget Today",
                    "x_axis": {"key": "budget", "label": "Budget"},
                    "series": [{"key": "budget", "label": "Budget"}],
                    "value_key": "amount",
                    "format": "number",
                    "prefix": "$",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_budgets",
                    "params": {"from_date": "today_start", "to_date": "tomorrow_start"},
                },
                "layout": {"col": 17, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Venue Sales Breakdown",
                "chart_type": "table",
                "chart_spec": {
                    "chart_type": "table",
                    "title": "Venue Sales Breakdown",
                    "x_axis": {"key": "venue", "label": "Venue"},
                    "series": [
                        {"key": "orders", "label": "Orders"},
                        {"key": "sales", "label": "Sales ($)"},
                    ],
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_sales_data",
                    "params": {
                        "start_datetime": "today_start",
                        "end_datetime": "now",
                        "interval": "1.00:00:00",
                        "_all_venues": True,
                    },
                },
                "layout": {"col": 1, "row": 13, "colSpan": 12, "rowSpan": 8},
            },
            {
                "title": "Sales Last 12 Hours (30 min intervals)",
                "chart_type": "stacked_bar",
                "chart_spec": {
                    "chart_type": "stacked_bar",
                    "title": "Sales Last 12 Hours",
                    "x_axis": {"key": "startTime", "label": "Time", "format": "time"},
                    "series": [
                        {
                            "key": "Bessie & Engineers",
                            "label": "Bessie & Engineers",
                            "color": "#4f8a5e",
                        },
                        {
                            "key": "Freeman & Grey",
                            "label": "Freeman & Grey",
                            "color": "#5b8abd",
                        },
                        {
                            "key": "The Glass Goose",
                            "label": "The Glass Goose",
                            "color": "#c4a882",
                        },
                        {
                            "key": "Mr Murdochs",
                            "label": "Mr Murdochs",
                            "color": "#b07d4f",
                        },
                        {
                            "key": "Dunedin Social Club",
                            "label": "Dunedin Social Club",
                            "color": "#8b6caf",
                        },
                        {
                            "key": "Velvet Burger",
                            "label": "Velvet Burger",
                            "color": "#c75a5a",
                        },
                        {"key": "La Zeppa", "label": "La Zeppa", "color": "#3d9e8f"},
                    ],
                    "group_by": "venue",
                    "value_key": "amount",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_sales_data",
                    "params": {
                        "start_datetime": "12h_ago",
                        "end_datetime": "now",
                        "interval": "00:30:00",
                        "_round_30": True,
                    },
                },
                "layout": {"col": 1, "row": 5, "colSpan": 24, "rowSpan": 8},
            },
            {
                "title": "Staff Hours by Venue",
                "chart_type": "table",
                "chart_spec": {
                    "chart_type": "table",
                    "title": "Staff Hours by Venue",
                    "x_axis": {"key": "venue", "label": "Venue"},
                    "series": [
                        {"key": "rostered_hours", "label": "Rostered Hours"},
                        {"key": "actual_hours", "label": "Actual Hours"},
                        {"key": "rostered_cost", "label": "Rostered Cost ($)"},
                        {"key": "actual_cost", "label": "Actual Cost ($)"},
                    ],
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_timeclock_entries",
                    "params": {
                        "start_time": "today_start",
                        "end_time": "now",
                        "_all_venues": True,
                    },
                },
                "layout": {"col": 13, "row": 13, "colSpan": 12, "rowSpan": 8},
            },
        ],
    },
    {
        "slug": "hr-dashboard",
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
                    "params": {"start_datetime": "today_start", "end_datetime": "now"},
                },
                "layout": {"col": 1, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Open Jobs",
                "chart_type": "kpi",
                "chart_spec": {"value_key": "open_count", "format": "number"},
                "script": {"connector": "bamboohr", "action": "get_jobs", "params": {}},
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
                    "params": {"start_datetime": "today_start", "end_datetime": "now"},
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
                    "series": [{"key": "hours", "label": "Hours", "color": "#5b8abd"}],
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
                    "params": {"start_datetime": "today_start", "end_datetime": "now"},
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


def seed_templates(config_db) -> int:
    """Insert seed templates into config DB if they don't already exist.

    Returns the number of templates inserted.
    """
    from app.db.config_models import DashboardTemplate

    existing_slugs = {row.slug for row in config_db.query(DashboardTemplate.slug).all()}
    inserted = 0
    for t in DASHBOARD_TEMPLATES:
        if t["slug"] in existing_slugs:
            continue
        template = DashboardTemplate(
            slug=t["slug"],
            agent_slug=t["agent_slug"],
            title=t["title"],
            description=t.get("description", ""),
            charts=t["charts"],
            enabled=True,
        )
        config_db.add(template)
        inserted += 1
    if inserted:
        config_db.commit()
    return inserted
