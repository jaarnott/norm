# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `loadedhub.get_purchase_orders`
# consolidator — THE purchase-order lookup (synced by
# scripts/sync_invoice_consolidators.py). Replaces three agent-facing
# tools: get_purchase_orders_summary, get_purchase_order_detail and the
# orphaned list_purchase_orders (get_stock_purchase_order, a literal
# duplicate of the detail tool, is deleted outright).
#
# Token rules: list rows are the summary transform's slim fields renamed
# to snake_case; PO LINES exist only per single order_id, shaped with
# names. detail='full' hands over the raw Loaded payload.
#
# Requires consolidator_config: {"max_api_calls": 3}


def run(params, call_api, log):
    order_id = params.get("order_id")
    detail = str(params.get("detail") or "summary").strip().lower()
    query = str(params.get("query") or "").strip().lower()
    status = str(params.get("status") or "").strip().lower()
    limit = int(params.get("limit") or 50)
    venue = params.get("venue")

    def money(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    if order_id:
        po = call_api(
            "loadedhub",
            "get_purchase_order_detail",
            {"venue": venue, "order_id": order_id},
        )
        if not isinstance(po, dict) or po.get("error"):
            return {
                "error": (po or {}).get("error")
                or f"purchase order {order_id} not found"
            }
        if detail == "full":
            return {"purchase_order": po, "detail": "full"}
        lines = []
        for ln in po.get("lines") or []:
            if not isinstance(ln, dict) or ln.get("deletedAt"):
                continue
            qty = (
                ln.get("quantityOrdered")
                if ln.get("quantityOrdered") is not None
                else ln.get("quantity")
            )
            cost = (
                ln.get("unitCost")
                if ln.get("unitCost") is not None
                else ln.get("unitCostExclTax")
            )
            # Loaded's PO lines carry no line total (verified live 21 Aug
            # 2026) — it is quantity × unit cost.
            total = None
            if isinstance(qty, (int, float)) and isinstance(cost, (int, float)):
                total = money(qty * cost)
            lines.append(
                {
                    "item": ln.get("itemName")
                    or ln.get("description")
                    or ln.get("name"),
                    "quantity": qty,
                    "unit": ln.get("unitName") or ln.get("unit"),
                    "unit_cost": cost,
                    "line_total": total,
                }
            )
        return {
            "purchase_order": {
                "id": po.get("id"),
                "order_number": po.get("orderNumber"),
                "supplier": po.get("supplierName"),
                "status": po.get("status"),
                "created": str(po.get("createdAt") or "")[:10],
                "ordered_by": po.get("orderedBy"),
                "subtotal": money(po.get("subtotal")),
                "tax": money(po.get("tax")),
                "total": money(po.get("total")),
                "received": po.get("isReceived"),
                "linked_invoice_number": po.get("invoiceNumber"),
                "lines": lines,
                "line_count": len(lines),
            },
            "detail": "summary",
        }

    rows = call_api("loadedhub", "get_purchase_orders_summary", {"venue": venue})
    if not isinstance(rows, list):
        return {"error": (rows or {}).get("error") or "purchase orders unavailable"}
    # List rows pass through the summary transform's slim fields UNCHANGED
    # (id, orderNumber, supplierName, status, createdAt, subtotal, tax, total,
    # isReceived) — the orders dashboard component parses exactly this shape,
    # and show_orders replays either this result or the raw summary's.
    out_rows = [
        r
        for r in rows
        if isinstance(r, dict)
        and (not query or query in str(r.get("supplierName") or "").lower())
        and (not status or status == str(r.get("status") or "").lower())
    ]
    total = len(out_rows)
    out_rows.sort(key=lambda r: str(r.get("createdAt") or ""), reverse=True)
    return {
        "rows": out_rows[:limit],
        "total_matches": total,
        "shown": min(total, limit),
    }
