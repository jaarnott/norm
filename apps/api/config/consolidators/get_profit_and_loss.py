# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `xero.get_profit_and_loss` consolidator —
# Xero's Profit & Loss report for a period, parsed from its row/cell report
# format into sections, headline totals and one row per period column (what a
# dashboard plots). The xero_common helpers are prepended by
# scripts/sync_xero_connector.py.
#
# Report format (verified live 17 Oct 2026): Reports[0].Rows is a Header row
# (cells = ['', '30 Sep 26', '30 Aug 26', ...]) followed by Sections; each
# Section has a Title ('Income', 'Cost of Goods', 'Less Operating Expenses'…)
# and Rows of RowType Row / SummaryRow with Cells[].Value; the headline lines
# ('Total Income', 'Total Cost of Sales', 'Gross Profit', 'Total Operating
# Expenses', 'Net Profit') sit in untitled sections or as SummaryRows.
#
# Requires consolidator_config: {"max_api_calls": 3,
#   "allowed_write_actions": ["resolve_dates"]}

HEADLINES = {
    "total income": "income",
    "total cost of sales": "cost_of_sales",
    "gross profit": "gross_profit",
    "total operating expenses": "operating_expenses",
    "net profit": "net_profit",
}


def run(params, call_api, log):
    vp = xero_venue_params(params)
    settings, err = xero_settings(call_api, params)
    if err:
        return {"error": err}
    from_date, to_date, window_desc, win_err = xero_window(call_api, params, vp)
    if win_err:
        return {"error": win_err}
    if not from_date or not to_date:
        return {"error": "give a period ('last month') or from_date and to_date"}

    q = {**vp, "from_date": from_date, "to_date": to_date}
    periods = int(params.get("periods") or 0)
    if periods:
        q["periods"] = str(min(max(periods, 1), 11))
        q["timeframe"] = str(params.get("timeframe") or "MONTH").upper()
    if params.get("tracking_category_id"):
        q["tracking_category_id"] = params["tracking_category_id"]

    payload = call_api("xero", "profit_and_loss", q)
    reports, err = xero_rows(payload, "Reports")
    if err or not reports:
        return {"error": f"Xero profit and loss unavailable: {err or 'empty report'}"}
    report = reports[0]

    def cells(row):
        return [c.get("Value") for c in (row.get("Cells") or []) if isinstance(c, dict)]

    def nums(values):
        return [xero_money(v) for v in values[1:]]

    columns = []
    sections = []
    totals = {}
    for row in report.get("Rows") or []:
        rtype = row.get("RowType")
        if rtype == "Header":
            columns = [str(v) for v in cells(row)[1:]]
            continue
        if rtype != "Section":
            continue
        title = str(row.get("Title") or "").strip()
        lines = []
        section_total = None
        for sub in row.get("Rows") or []:
            vals = cells(sub)
            if not vals:
                continue
            label = str(vals[0] or "").strip()
            key = HEADLINES.get(label.lower())
            if key:
                totals[key] = nums(vals)
                if sub.get("RowType") == "SummaryRow":
                    section_total = nums(vals)
                continue
            if sub.get("RowType") == "SummaryRow":
                section_total = nums(vals)
                continue
            lines.append({"name": label, "values": nums(vals)})
        # Grouping headers such as 'Less Cost of Sales' carry no rows of their
        # own, and the headline totals live in untitled sections (kept in
        # ``totals``), so only sections with content are listed.
        if lines or (title and section_total is not None):
            sections.append(
                {"title": title or None, "rows": lines, "total": section_total}
            )

    months = []
    for i, col in enumerate(columns):
        months.append(
            {
                "column": col,
                **{k: (v[i] if i < len(v) else None) for k, v in totals.items()},
            }
        )
    # Oldest first for charts; Xero lists the latest period first.
    months.reverse()

    return {
        "organisation": settings.get("organisation"),
        "currency": settings.get("xero_currency"),
        "titles": report.get("ReportTitles"),
        "from": from_date,
        "to": to_date,
        "window": window_desc,
        "columns": columns,
        "totals": totals,
        "sections": sections,
        "months": months,
    }
