def run(params, call_api, log, call_api_parallel):
    """Forecast how much of a stocktake template's stock to order.

    Compares stock on hand now vs 4 weeks ago, adds what was received in between
    to get usage, then scales that usage by the budget for the forecast period.

    Two things to know about how this differs from LoadedHub's own Predicted
    Order page:

    * We deliberately do NOT add LoadedHub's 20% forecast buffer. Order
      quantities here are the bare usage-scaled forecast.
    * We DO enforce the per-item minimum stock-on-hand (par level), like
      LoadedHub does. An item that has fallen below its configured minimum is
      ordered up to that minimum even if it had no usage this period — so slow
      movers with a par level are no longer silently dropped. The minimum is
      stored in the item's own unit, which is often (but not always — ~1 in 5
      items) different from the counting unit stock-on-hand reports; we convert
      it via the unit ratios before comparing.

    Failure handling: `get_stock_on_hand` is slow — ~13s in LoadedHub's own UI —
    and LoadedHub aborts it with a 500 ("Something went wrong") at roughly 30s.
    When that happens `call_api_parallel` returns {"error": ...} for those calls.
    An earlier version read `.get("lines", [])` off that error dict, silently got
    an empty list, and reported "0 items need reordering out of 0 total" for a
    309-item template. So a failed stock call is now retried once (serially, off
    the parallel batch that contributed to the timeout) and, if it still fails,
    reported as the API error it is. The item-minimums call is best-effort: if it
    fails we warn and fall back to usage-only (par levels not enforced), rather
    than abort.
    """
    venue = params["venue"]
    template_id = params["template_id"]

    def _resolve(query):
        resolve_args = {"query": query}
        if params.get("venue_id"):
            resolve_args["venue_id"] = params["venue_id"]
        resolved = call_api("norm", "resolve_dates", resolve_args)
        window = resolved.get("window") if isinstance(resolved, dict) else None
        if not isinstance(window, dict):
            data = resolved.get("data") if isinstance(resolved, dict) else None
            window = data.get("window") if isinstance(data, dict) else None
        return window if isinstance(window, dict) else None

    # "Order until" accepts plain English ("next Friday") via Norm's venue
    # calendar; order_until_date (YYYY-MM-DD) remains the exact-date path.
    order_until = params.get("order_until_date")
    until_phrase = (params.get("order_until") or "").strip()
    if until_phrase and not order_until:
        window = _resolve(until_phrase)
        if not window:
            return {"error": f"could not resolve '{until_phrase}' to a date"}
        order_until = str(window["end"])[:10]
    if not order_until:
        order_until = params["today"]

    # The 4-week usage history window comes from the venue calendar too —
    # the executor-injected four_weeks_ago_iso/today_iso are CIVIL midnight
    # in a fixed timezone, which splits trading sessions. Falls back to the
    # injected values if the resolver is unavailable.
    hist = _resolve("last 4 weeks")
    hist_start = (hist or {}).get("start") or params["four_weeks_ago_iso"]
    hist_end = (hist or {}).get("end") or params["today_iso"]

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
                    "report_datetime": hist_start,
                },
            ),
            (
                "loadedhub",
                "get_received_invoices",
                {
                    "venue": venue,
                    "from": hist_start,
                    "to": hist_end,
                },
            ),
            (
                "loadedhub",
                "get_sales_data",
                {
                    "venue": venue,
                    "start_datetime": hist_start,
                    "end_datetime": hist_end,
                    "interval": "28.00:00:00",
                },
            ),
            (
                "loadedhub",
                # The budgets CONSOLIDATOR (nested read-only call): it owns
                # Loaded's off-by-one dating and the query-window quirk, so
                # this asks in plain true dates and reads the precomputed
                # total below.
                "get_budgets",
                {"venue": venue, "from_date": params["today"], "to_date": order_until},
            ),
            (
                "loadedhub",
                "get_stock_item_minimums",
                {"venue": venue},
            ),
            # Unit TYPES (Weight / Volume / Count), so a received quantity is
            # converted into counting units only when the two units share a
            # base — see the received block below.
            (
                "loadedhub",
                "get_stock_units",
                {"venue": venue},
            ),
        ]
    )
    (
        stock_now_raw,
        stock_4w_raw,
        received,
        sales,
        budgets,
        item_mins_raw,
        units_raw,
    ) = results

    # Retry the slow calls one at a time — the parallel batch is part of why they
    # time out, so a serial retry has a real chance of succeeding.
    if api_error(stock_now_raw):
        log("stock-on-hand (today) failed — retrying once, serially")
        stock_now_raw = stock_on_hand(params["today_iso"])
    if api_error(stock_4w_raw):
        log("stock-on-hand (4 weeks ago) failed — retrying once, serially")
        stock_4w_raw = stock_on_hand(hist_start)

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

    # Build the minimum-stock (par) lookup, keyed by item id, in COUNTING units.
    # Each item stores its minimum in its own unit; stock-on-hand reports
    # quantityOnHand in the counting unit. Convert via the two unit ratios:
    #   min_in_base     = minQty * minUnitRatio
    #   min_in_counting = min_in_base / countingUnitRatio
    # (~1 in 5 items has a min unit that differs from its counting unit, e.g. a
    # beer counted in "Each" with a minimum set in "24 Pack" — skipping this
    # conversion would understate those minimums 24-fold.)
    min_on_hand = {}
    if isinstance(item_mins_raw, list):
        for it in item_mins_raw:
            min_qty = float(it.get("minQty", 0) or 0)
            if min_qty <= 0:
                continue
            min_ratio = float(it.get("minUnitRatio", 0) or 0)
            count_ratio = float(it.get("countingUnitRatio", 0) or 0)
            if min_ratio > 0 and count_ratio > 0:
                min_on_hand[it.get("id", "")] = min_qty * min_ratio / count_ratio
            else:
                # No ratios to convert with — assume the min is already stated
                # in counting units rather than dropping the par level entirely.
                min_on_hand[it.get("id", "")] = min_qty
    else:
        log(
            f"WARNING: item minimums unavailable ({api_error(item_mins_raw)}) — "
            "par levels will not be enforced this run"
        )

    log(
        f"Current stock: {len(stock_now)} items, 4w ago: {len(stock_4w)} items, "
        f"Invoices: {len(received)}, Items with a par level: {len(min_on_hand)}"
    )

    if not stock_now:
        # Reachable only when LoadedHub genuinely returns an empty report.
        return {
            "error": (
                f"LoadedHub returned no stock lines for template {template_id}. "
                "The template exists but has no items counted against it."
            )
        }

    # Received quantity per stock item, IN COUNTING UNITS — the unit
    # quantityOnHand is reported in, so opening + received - closing is one
    # unit throughout.
    #
    # This read `lines[].quantity`, a field the get_received_invoices
    # transform never emits (it keeps `quantityReceived`). Every delivery
    # counted as zero, so usage was understated by exactly what arrived, any
    # item restocked in the window fell out of the usage forecast altogether
    # (usage <= 0), and the tool recommended ordering too little. Found 23 Sep
    # 2026 by reading the live transform; no test had ever fed a received line.
    #
    # Fixing only the field name would have traded that for a unit error:
    # quantityReceived * unitRatio is in BASE units (L / kg / each) while
    # stock on hand is in COUNTING units (a bottle, a 24-pack). Convert with the
    # item's countingUnitRatio, which the stock-on-hand transform keeps — the
    # same two-ratio conversion the par-level code below already does.
    #
    # Credits: the feed marks them either with creditRequest or with negative
    # quantities (received_items_for_period checks both). A credit request is
    # goods claimed back, so it subtracts; abs() keeps a credit already stored
    # negative from being negated twice.
    def _norm(text):
        return "".join(ch for ch in str(text or "").lower() if ch.isalnum())

    # A unit is identified by its (name, ratio) pair — the feeds drop unitId,
    # and one venue can hold two units both named '6.5 KG' (ratios 6.5, 1.0).
    unit_type = {}
    if isinstance(units_raw, list):
        for u in units_raw:
            if not isinstance(u, dict):
                continue
            r = float(u.get("ratio", 0) or 0)
            unit_type.setdefault(
                (_norm(u.get("name")), round(r, 6)), u.get("stockUnitType")
            )

    counting = {}  # item id -> (counting unit key, ratio, type)
    for item in stock_now:
        cr = float(item.get("countingUnitRatio", 0) or 0)
        if cr > 0:
            key = (_norm(item.get("countingUnitName")), round(cr, 6))
            counting[item.get("stockItemID", "")] = (key, cr, unit_type.get(key))
    received_qty = {}
    unconverted = 0
    incompatible = 0
    for invoice in received:
        is_credit = bool(invoice.get("creditRequest"))
        for line in invoice.get("lines", []):
            item_id = line.get("StockItemId", "")
            raw_qty = line.get("quantityReceived")
            if raw_qty is None:
                raw_qty = line.get("quantity", 0)
            qty = float(raw_qty or 0)
            if not qty:
                continue
            if is_credit:
                qty = -abs(qty)
            unit_ratio = float(line.get("unitRatio", 0) or 0)
            target = counting.get(item_id)
            if unit_ratio <= 0 or not target:
                # No way to make it comparable with on-hand. Leave it out and
                # say so, rather than guessing a ratio of 1 and ordering from
                # a number in the wrong unit.
                unconverted += 1
                continue
            count_key, count_ratio, count_type = target
            line_key = (_norm(line.get("unitName")), round(unit_ratio, 6))
            line_type = unit_type.get(line_key)
            # The two ratios share a base only within one unit TYPE. Real
            # production data (23 Sep 2026): a cos lettuce received in
            # '300 Grams' (Weight, 0.3 kg) and counted in 'Each' (Count, 1) —
            # dividing the ratios turned 3 heads into 0.9. Convert when the
            # unit is the same one, or the types are known and equal;
            # otherwise the quantity cannot be made comparable, so leave it
            # out and say so.
            if line_key != count_key and (
                not line_type or not count_type or line_type != count_type
            ):
                incompatible += 1
                continue
            received_qty[item_id] = received_qty.get(item_id, 0) + (
                qty * unit_ratio / count_ratio
            )
    if incompatible:
        log(
            f"WARNING: {incompatible} received line(s) were in a unit of a "
            "different type from the item's counting unit (e.g. grams vs each) "
            "and were left out of usage"
        )
    if unconverted:
        log(
            f"WARNING: {unconverted} received line(s) had no usable unit ratio "
            "and were left out of usage"
        )

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

    # Total budget for the forecast window — the consolidator precomputes it
    # (the list fallback covers the raw shape, defensively).
    total_budget = 0
    if isinstance(budgets, dict):
        total_budget = float(budgets.get("total") or 0)
    elif isinstance(budgets, list):
        for b in budgets:
            total_budget += float(b.get("amount", 0) or 0)
    log(f"Total budget (forecast period): ${total_budget:,.0f}")

    # Calculate per-item requirements. Each item's order is the greater of two
    # drivers:
    #   * usage forecast — usage scaled by the budget (no buffer), and
    #   * par level      — top up to the configured minimum stock on hand.
    # An item with no usage this period is still ordered if it has fallen below
    # its par level, which is why the old `usage <= 0: continue` short-circuit is
    # gone.
    results = []
    for item in stock_now:
        item_id = item.get("stockItemID", "")
        item_name = item.get("itemName", "Unknown")
        on_hand = float(item.get("quantityOnHand", 0) or 0)
        usage = usage_by_item.get(item_id, 0)

        forecast = 0.0
        usage_order = 0.0
        if usage > 0:
            usage_per_1k = usage / (total_sales / 1000)
            forecast = usage_per_1k * (total_budget / 1000)
            usage_order = max(0, forecast - on_hand)

        min_level = min_on_hand.get(item_id, 0)
        min_order = max(0, min_level - on_hand) if min_level > 0 else 0

        order_qty = max(usage_order, min_order)
        if order_qty <= 0:
            continue

        results.append(
            {
                "itemName": item_name,
                "currentStock": round(on_hand, 1),
                "usageLast4Weeks": round(usage, 1) if usage > 0 else 0,
                "forecastUsage": round(forecast, 1),
                "minimumStock": round(min_level, 1),
                "orderQty": round(order_qty, 1),
                # Which rule set the quantity, so the agent can explain the line.
                "orderDriver": "minimum" if min_order > usage_order else "usage",
                "unit": item.get("countingUnitName", ""),
                "category": item.get("Category", ""),
            }
        )

    results.sort(key=lambda x: x["orderQty"], reverse=True)
    log(f"RESULT: {len(results)} items need reordering out of {len(stock_now)} total")
    return results
