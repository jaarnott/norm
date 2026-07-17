def run(params, call_api, log, call_api_parallel):
    """Forecast how much of a stocktake template's stock to order.

    Mirrors what LoadedHub's own Stock → Ordering → Predicted Order page does:
    compare stock on hand now vs 4 weeks ago, add what was received in between to
    get usage, then scale that usage by the budget for the forecast period.

    Failure handling is the point of this version. `get_stock_on_hand` is slow —
    ~13s in LoadedHub's own UI, and it degrades under concurrency — and LoadedHub
    aborts it with a 500 ("Something went wrong") at roughly 30s. When that
    happened, `call_api_parallel` returned {"error": ...} for those two calls;
    this function read `.get("lines", [])` off that error dict, silently got an
    empty list, and reported "0 items need reordering out of 0 total". The agent
    then told the user the template "may be empty or unconfigured" — while the
    template actually had 309 items. A LoadedHub outage was reported as a data
    problem, which sent everyone looking in the wrong place.

    So: a failed call is now retried once (serially, off the parallel batch that
    contributed to the timeout) and, if it still fails, reported as the API error
    it is. Never silently treated as "no stock".
    """
    venue = params["venue"]
    template_id = params["template_id"]
    order_until = params.get("order_until_date", params["today"])

    def api_error(result):
        """call_api / call_api_parallel hand back {"error": ...} on failure.

        A successful stock-on-hand payload carries "lines", so require its
        absence before treating the response as an error.
        """
        if isinstance(result, dict) and "error" in result and "lines" not in result:
            return str(result["error"])
        return None

    def stock_on_hand(when_iso):
        return call_api(
            "loadedhub",
            "get_stock_on_hand",
            {"venue": venue, "template_id": template_id, "report_datetime": when_iso},
        )

    log("Fetching all data in parallel...")
    results = call_api_parallel(
        [
            (
                "loadedhub",
                "get_stock_on_hand",
                {
                    "venue": venue,
                    "template_id": template_id,
                    "report_datetime": params["today_iso"],
                },
            ),
            (
                "loadedhub",
                "get_stock_on_hand",
                {
                    "venue": venue,
                    "template_id": template_id,
                    "report_datetime": params["four_weeks_ago_iso"],
                },
            ),
            (
                "loadedhub",
                "get_received_invoices",
                {
                    "venue": venue,
                    "from": params["four_weeks_ago_iso"],
                    "to": params["today_iso"],
                },
            ),
            (
                "loadedhub",
                "get_sales_data",
                {
                    "venue": venue,
                    "start_datetime": params["four_weeks_ago_iso"],
                    "end_datetime": params["today_iso"],
                    "interval": "28.00:00:00",
                },
            ),
            (
                "loadedhub",
                "get_budgets",
                {"venue": venue, "from_date": params["today"], "to_date": order_until},
            ),
        ]
    )
    stock_now_raw, stock_4w_raw, received, sales, budgets = results

    # Retry the slow calls one at a time — the parallel batch is part of why they
    # time out, so a serial retry has a real chance of succeeding.
    if api_error(stock_now_raw):
        log("stock-on-hand (today) failed — retrying once, serially")
        stock_now_raw = stock_on_hand(params["today_iso"])
    if api_error(stock_4w_raw):
        log("stock-on-hand (4 weeks ago) failed — retrying once, serially")
        stock_4w_raw = stock_on_hand(params["four_weeks_ago_iso"])

    failure = api_error(stock_now_raw) or api_error(stock_4w_raw)
    if failure:
        log(f"ABORT: stock-on-hand unavailable: {failure}")
        return {
            "error": (
                "LoadedHub could not return stock on hand for this template, so "
                "the forecast can't be calculated. This is a LoadedHub API "
                "failure, not an empty template — the stock-on-hand report is "
                "slow and times out under load. Try again, or try one template "
                f"at a time. LoadedHub said: {failure}"
            )
        }

    stock_now = (
        stock_now_raw.get("lines", [])
        if isinstance(stock_now_raw, dict)
        else (stock_now_raw if isinstance(stock_now_raw, list) else [])
    )
    stock_4w = (
        stock_4w_raw.get("lines", [])
        if isinstance(stock_4w_raw, dict)
        else (stock_4w_raw if isinstance(stock_4w_raw, list) else [])
    )
    if not isinstance(received, list):
        if api_error(received):
            log(
                f"WARNING: received invoices unavailable ({api_error(received)}) — usage will be understated"
            )
        received = []

    log(
        f"Current stock: {len(stock_now)} items, 4w ago: {len(stock_4w)} items, Invoices: {len(received)}"
    )

    if not stock_now:
        # Reachable only when LoadedHub genuinely returns an empty report.
        return {
            "error": (
                f"LoadedHub returned no stock lines for template {template_id}. "
                "The template exists but has no items counted against it."
            )
        }

    # Build received quantity lookup by stock item ID
    received_qty = {}
    for invoice in received:
        for line in invoice.get("lines", []):
            item_id = line.get("StockItemId", "")
            qty = float(line.get("quantity", 0) or 0)
            ratio = float(line.get("unitRatio", 1) or 1)
            received_qty[item_id] = received_qty.get(item_id, 0) + (qty * ratio)

    # Build 4-week-ago stock lookup
    stock_4w_lookup = {}
    for item in stock_4w:
        item_id = item.get("stockItemID", "")
        stock_4w_lookup[item_id] = float(item.get("quantityOnHand", 0) or 0)

    # Calculate usage: opening + received - closing = used
    usage_by_item = {}
    for item in stock_now:
        item_id = item.get("stockItemID", "")
        closing = float(item.get("quantityOnHand", 0) or 0)
        opening = stock_4w_lookup.get(item_id, 0)
        recv = received_qty.get(item_id, 0)
        usage = opening + recv - closing
        if usage > 0:
            usage_by_item[item_id] = usage

    log(f"Items with positive usage: {len(usage_by_item)}")

    # Calculate total sales
    total_sales = 0
    if isinstance(sales, list):
        for s in sales:
            total_sales += float(s.get("amount", 0) or 0)
    log(f"Total sales (4 weeks): ${total_sales:,.0f}")

    if total_sales <= 0:
        log("ERROR: No sales data - cannot calculate usage rates")
        return {"error": "No sales data available", "items_checked": len(stock_now)}

    # Calculate total budget
    total_budget = 0
    if isinstance(budgets, list):
        for b in budgets:
            total_budget += float(b.get("amount", 0) or 0)
    log(f"Total budget (forecast period): ${total_budget:,.0f}")

    # Calculate per-item requirements
    results = []
    for item in stock_now:
        item_id = item.get("stockItemID", "")
        item_name = item.get("itemName", "Unknown")
        on_hand = float(item.get("quantityOnHand", 0) or 0)
        usage = usage_by_item.get(item_id, 0)

        if usage <= 0:
            continue

        usage_per_1k = usage / (total_sales / 1000)
        forecast = usage_per_1k * (total_budget / 1000)
        order_qty = max(0, forecast - on_hand)

        if order_qty > 0:
            results.append(
                {
                    "itemName": item_name,
                    "currentStock": round(on_hand, 1),
                    "usageLast4Weeks": round(usage, 1),
                    "forecastUsage": round(forecast, 1),
                    "orderQty": round(order_qty, 1),
                    "unit": item.get("countingUnitName", ""),
                    "category": item.get("Category", ""),
                }
            )

    results.sort(key=lambda x: x["orderQty"], reverse=True)
    log(f"RESULT: {len(results)} items need reordering out of {len(stock_now)} total")
    return results
