"""Pre-built dashboard templates — seed data for the config DB."""

DASHBOARD_TEMPLATES: list[dict] = [
    # The original procurement template, kept alongside the cost-of-goods one
    # (the owner wants both on the picker, 17 Oct 2026). Its raw-endpoint and
    # Xero charts are mapped onto tools when it is instantiated (to_tool_chart).
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
    # Purchases-based cost of goods (Oct 2026): a venue that receives its
    # invoices into Loaded but runs no stocktakes reads cost of goods as
    # purchases ÷ POS sales. Every tile is a tool over Loaded's data; the
    # dashboard's period control (this month / last month / last 12 months /
    # this week / last week / last 13 weeks) replaces each `period` with that
    # window on the venue's calendar, and `period_span` widens a trend chart
    # to that many buckets ending at the chosen period. The cards add venues
    # up (a ratio sums its two parts first); the lines pivot per venue.
    # Every card reads the same way (the previous period rides along), so the
    # reconciliation and freight cards describe the same invoices as the
    # headline: Loaded's received feed is by RECEIVED date, so a narrower
    # read left out invoices dated this month but received last month.
    # Checked against Loaded for September 2026 at Applegum Cafe: Food
    # 7,896.63 + Beverage 2,177.78 + Other 1,383.19 + freight 177.00 =
    # invoice subtotals 11,634.61; sales ex-GST 47,371.13; 24.6%.
    {
        "slug": "procurement-cogs",
        "agent_slug": "procurement",
        "title": "Cost of Goods",
        "description": (
            "Purchases against sales from Loaded — cost of goods %, the food / "
            "beverage split, the trend, suppliers and price moves. For venues "
            "that receive invoices but don't stocktake."
        ),
        "charts": [
            {
                "title": "Purchases ex-GST",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "purchases",
                    "format": "currency",
                    "prefix": "$",
                    "comparison_key": "previous.purchases",
                    "comparison_label": "vs previous period",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_cogs",
                    "params": {"period": "this month", "interval": "month"},
                    "rows": "summary",
                },
                "layout": {"col": 1, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Sales ex-GST",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "sales",
                    "format": "currency",
                    "prefix": "$",
                    "comparison_key": "previous.sales",
                    "comparison_label": "vs previous period",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_cogs",
                    "params": {"period": "this month", "interval": "month"},
                    "rows": "summary",
                },
                "layout": {"col": 9, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Cost of goods %",
                "chart_type": "kpi",
                "chart_spec": {
                    "numerator_key": "purchases",
                    "denominator_key": "sales",
                    "previous_numerator_key": "previous.purchases",
                    "previous_denominator_key": "previous.sales",
                    "format": "percent",
                    "comparison_label": "vs previous period",
                    "lower_is_better": True,
                    "note": "purchases ÷ sales, both ex-GST",
                    "threshold": {
                        "warning": 0.32,
                        "danger": 0.38,
                        "direction": "above",
                    },
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_cogs",
                    "params": {"period": "this month", "interval": "month"},
                    "rows": "summary",
                },
                "layout": {"col": 17, "row": 1, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Food cost of goods %",
                "chart_type": "kpi",
                "chart_spec": {
                    "numerator_key": "food",
                    "denominator_key": "food_sales",
                    "previous_numerator_key": "previous.food",
                    "previous_denominator_key": "previous.food_sales",
                    "format": "percent",
                    "comparison_label": "vs previous period",
                    "lower_is_better": True,
                    "empty_note": (
                        "No food sales: the POS items in Loaded have no category yet"
                    ),
                    "warning": {
                        "numerator_key": "uncategorised_sales",
                        "denominator_key": "sales",
                        "text": (
                            "of sales have no POS category in Loaded — the food / "
                            "beverage split misses them"
                        ),
                    },
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_cogs",
                    "params": {"period": "this month", "interval": "month"},
                    "rows": "summary",
                },
                "layout": {"col": 1, "row": 5, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Beverage cost of goods %",
                "chart_type": "kpi",
                "chart_spec": {
                    "numerator_key": "beverage",
                    "denominator_key": "beverage_sales",
                    "previous_numerator_key": "previous.beverage",
                    "previous_denominator_key": "previous.beverage_sales",
                    "format": "percent",
                    "comparison_label": "vs previous period",
                    "lower_is_better": True,
                    "empty_note": (
                        "No beverage sales: the POS items in Loaded have no "
                        "category yet"
                    ),
                    "warning": {
                        "numerator_key": "uncategorised_sales",
                        "denominator_key": "sales",
                        "text": (
                            "of sales have no POS category in Loaded — the food / "
                            "beverage split misses them"
                        ),
                    },
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_cogs",
                    "params": {"period": "this month", "interval": "month"},
                    "rows": "summary",
                },
                "layout": {"col": 9, "row": 5, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Freight",
                "chart_type": "kpi",
                "chart_spec": {
                    "value_key": "freight",
                    "format": "currency",
                    "prefix": "$",
                    "note": "included in purchases",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_cogs",
                    "params": {"period": "this month", "interval": "month"},
                    "rows": "summary",
                },
                "layout": {"col": 17, "row": 5, "colSpan": 8, "rowSpan": 4},
            },
            {
                "title": "Cost of goods % over time",
                "chart_type": "line",
                "chart_spec": {
                    "x_axis": {"key": "bucket", "label": "Period"},
                    "y_axis": {
                        "key": "cogs_pct",
                        "label": "Cost of goods",
                        "format": "percent",
                    },
                    "series": [
                        {
                            "key": "cogs_pct",
                            "label": "Cost of goods %",
                            "color": "#b07d4f",
                        }
                    ],
                    # Across venues: one line per venue, plus the group.
                    "group_by": "venue",
                    "numerator_key": "purchases",
                    "denominator_key": "sales",
                    "total_series": "All venues",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_purchase_cogs",
                    "params": {
                        "period": "last 12 months",
                        "interval": "month",
                        "compare_previous": False,
                        "period_span": 12,
                    },
                    "rows": "rows",
                },
                "layout": {"col": 1, "row": 9, "colSpan": 12, "rowSpan": 8},
            },
            {
                "title": "Purchases by category over time",
                "chart_type": "stacked_bar",
                "chart_spec": {
                    "x_axis": {"key": "bucket", "label": "Period"},
                    "y_axis": {"key": "spend", "label": "Spend", "format": "currency"},
                    "series": [
                        {"key": "food", "label": "Food", "color": "#4f8a5e"},
                        {"key": "beverage", "label": "Beverage", "color": "#5b8abd"},
                        {"key": "other", "label": "Other", "color": "#c4a882"},
                        {"key": "freight", "label": "Freight", "color": "#8b6caf"},
                    ],
                    # The stacks are the categories; venues add up.
                    "group_by": None,
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_received_items_for_period",
                    "params": {
                        "period": "last 12 months",
                        "group_by": "month",
                        "period_span": 12,
                    },
                    "rows": "rows",
                },
                "layout": {"col": 13, "row": 9, "colSpan": 12, "rowSpan": 8},
            },
            {
                "title": "Purchases by supplier",
                "chart_type": "bar",
                "chart_spec": {
                    "x_axis": {"key": "supplier_name", "label": "Supplier"},
                    "y_axis": {"key": "spend", "label": "Spend", "format": "currency"},
                    "series": [{"key": "spend", "label": "Spend", "color": "#b07d4f"}],
                    "orientation": "horizontal",
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_received_items_for_period",
                    "params": {
                        "period": "this month",
                        "group_by": "supplier",
                        "limit": 15,
                    },
                    "rows": "rows",
                },
                "layout": {"col": 1, "row": 17, "colSpan": 12, "rowSpan": 8},
            },
            {
                "title": "Purchases by stock group",
                "chart_type": "table",
                "chart_spec": {
                    "x_axis": {"key": "group", "label": "Stock group"},
                    "series": [
                        {"key": "super_group", "label": "Category"},
                        {"key": "spend", "label": "Spend"},
                        {"key": "share", "label": "Share"},
                        {"key": "invoice_count", "label": "Invoices"},
                    ],
                    "field_order": ["venue", "group", "super_group", "spend", "share"],
                    "hidden_fields": [
                        "credit_amount",
                        "distinct_items",
                        "top_items",
                        "venues",
                    ],
                    "field_formats": {
                        "spend": {"type": "currency"},
                        "credit_amount": {"type": "currency"},
                        "share": {"type": "percent"},
                    },
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_received_items_for_period",
                    "params": {
                        "period": "this month",
                        "group_by": "group",
                        "limit": 40,
                    },
                    "rows": "rows",
                },
                "layout": {"col": 13, "row": 17, "colSpan": 12, "rowSpan": 8},
            },
            {
                "title": "Price moves — biggest items, first to last receipt",
                "chart_type": "table",
                "chart_spec": {
                    "x_axis": {"key": "item_name", "label": "Item"},
                    "series": [
                        {"key": "supplier_name", "label": "Supplier"},
                        {"key": "spend", "label": "Spend"},
                        {"key": "unit_cost_last", "label": "Last unit cost"},
                        {"key": "unit_cost_change_pct", "label": "Change"},
                    ],
                    "field_order": [
                        "venue",
                        "item_name",
                        "supplier_name",
                        "base_unit",
                        "spend",
                        "unit_cost_last",
                        "unit_cost_change_pct",
                    ],
                    "hidden_fields": [
                        "item_id",
                        "item_code",
                        "supplier_id",
                        "quantity_base",
                        "invoice_count",
                        "supplier_count",
                        "credit_amount",
                        "unit_cost_avg",
                        "unit_cost_min",
                        "unit_cost_max",
                        "venues",
                    ],
                    "field_formats": {
                        "spend": {"type": "currency"},
                        "unit_cost_last": {"type": "currency", "decimals": 4},
                        "unit_cost_change_pct": {"type": "percent"},
                    },
                },
                "script": {
                    "connector": "loadedhub",
                    "action": "get_received_items_for_period",
                    "params": {
                        "period": "this month",
                        "group_by": "item_supplier",
                        "limit": 40,
                    },
                    "rows": "rows",
                },
                "layout": {"col": 1, "row": 25, "colSpan": 24, "rowSpan": 8},
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
