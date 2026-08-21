# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `loadedhub.get_invoices` consolidator —
# THE supplier-invoice lookup (synced by scripts/sync_invoice_consolidators.py).
# Replaces five agent-facing tools: get_outstanding_invoices (deleted),
# list_stock_invoices, list_received_invoices, get_invoice_detail and the
# get_received_invoices_for_period wrapper (deleted), plus statements via
# list_supplier_statements — all now engine-only backends of this one surface.
#
# Token rules (hard):
# - List mode returns HEADERS ONLY — never invoice lines, however wide the
#   window. Lines exist in exactly two places: ONE invoice via invoice_id
#   (summarized lines), or aggregated via get_received_items_for_period
#   (group_by item/group/super_group) — the result note steers "how much
#   did we buy" questions there instead of paging invoices.
# - Names over ids: supplier names ride on the payloads; the only ids kept
#   are handles (invoice id, per-line item id for follow-up stock lookups).
#
# Requires consolidator_config: {"max_api_calls": 4}

_KINDS = ("outstanding", "received", "statements")

_AGG_NOTE = (
    "for 'how much of X did we buy' questions use "
    "get_received_items_for_period (group_by item, group or super_group) — "
    "never page invoices and add lines up yourself"
)


def run(params, call_api, log):
    invoice_id = params.get("invoice_id")
    kind = str(params.get("kind") or "outstanding").strip().lower()
    detail = str(params.get("detail") or "summary").strip().lower()
    query = str(params.get("query") or "").strip().lower()
    limit = int(params.get("limit") or 50)
    venue = params.get("venue")
    period = (params.get("period") or "").strip()

    def money(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    # ── ONE invoice, by id ────────────────────────────────────────────
    if invoice_id:
        inv = call_api(
            "loadedhub",
            "get_invoice_detail",
            {"venue": venue, "invoice_id": invoice_id},
        )
        if not isinstance(inv, dict) or inv.get("error"):
            return {
                "error": (inv or {}).get("error") or f"invoice {invoice_id} not found"
            }
        if detail == "full":
            return {"invoice": inv, "detail": "full"}
        lines = []
        for ln in inv.get("lines") or []:
            if not isinstance(ln, dict) or ln.get("deletedAt"):
                continue
            entry = {
                "description": ln.get("description"),
                "code": ln.get("code"),
                "quantity": ln.get("quantityReceived"),
                "unit": ln.get("unit"),
                "unit_cost": ln.get("unitCostExclTax"),
                "line_total": money(ln.get("totalCostExclTax")),
            }
            if ln.get("linkedItemId"):
                entry["item_id"] = ln["linkedItemId"]
            lines.append(entry)
        return {
            "invoice": {
                "id": inv.get("id"),
                "supplier": inv.get("supplierName"),
                "invoice_number": inv.get("referenceNumber"),
                "date": str(inv.get("issuedAt") or "")[:10],
                "po_number": inv.get("purchaseOrderNumber"),
                "subtotal": money(inv.get("subtotal")),
                "tax": money(inv.get("taxAmount")),
                "total": money(inv.get("total")),
                "received": inv.get("isReceived"),
                "reconciled": inv.get("reconciled"),
                "has_file": bool(inv.get("fileId")),
                "lines": lines,
                "line_count": len(lines),
            },
            "detail": "summary",
        }

    # ── Lists ─────────────────────────────────────────────────────────
    if kind not in _KINDS:
        return {"error": f"kind must be one of {', '.join(_KINDS)} — got {kind!r}"}

    window = None
    if kind == "outstanding" and not period and not params.get("from_date"):
        pass  # outstanding drafts: the whole backlog is the natural default
    else:
        resolve_args = (
            {"venue_id": params["venue_id"]} if params.get("venue_id") else {}
        )
        if period:
            resolve_args["query"] = period
        elif params.get("from_date") and params.get("to_date"):
            resolve_args["start"] = params["from_date"]
            resolve_args["end"] = params["to_date"]
        else:
            return {
                "error": (
                    f"kind='{kind}' needs a period in plain English "
                    "(e.g. 'last month') or from_date and to_date"
                )
            }
        resolved = call_api("norm", "resolve_dates", resolve_args)
        window = resolved.get("window") if isinstance(resolved, dict) else None
        if not isinstance(window, dict):
            data = resolved.get("data") if isinstance(resolved, dict) else None
            window = data.get("window") if isinstance(data, dict) else None
        if not isinstance(window, dict):
            return {"error": f"could not resolve '{period or 'that range'}' to dates"}

    if kind == "statements":
        rows = call_api(
            "loadedhub",
            "list_supplier_statements",
            {"venue": venue, "from_iso": window["start"], "to_iso": window["end"]},
        )
        if not isinstance(rows, list):
            return {"error": (rows or {}).get("error") or "statements unavailable"}
        out_rows = []
        for s in rows:
            if not isinstance(s, dict) or s.get("deletedAt"):
                continue
            if query and query not in str(s.get("supplierName") or "").lower():
                continue
            out_rows.append(
                {
                    "id": s.get("id"),
                    "supplier": s.get("supplierName"),
                    "statement_number": s.get("statementNumber"),
                    "start": str(s.get("startAt") or "")[:10],
                    "end": str(s.get("endAt") or "")[:10],
                    "statement_amount": money(s.get("statementAmount")),
                    "reconciled_amount": money(s.get("reconciledAmount")),
                    "invoices_reconciled": len(
                        s.get("reconciledStockReceivedItems") or []
                    ),
                }
            )
    elif kind == "received":
        rows = call_api(
            "loadedhub",
            "list_received_invoices",
            {
                "venue": venue,
                "from_date": window["start"][:10],
                "to_date": window["end"][:10],
            },
        )
        if not isinstance(rows, list):
            return {"error": (rows or {}).get("error") or "received list unavailable"}
        out_rows = []
        for r in rows:
            if not isinstance(r, dict) or r.get("deletedAt"):
                continue
            if query and query not in str(r.get("supplierName") or "").lower():
                continue
            out_rows.append(
                {
                    "id": r.get("id"),
                    "supplier": r.get("supplierName"),
                    "invoice_number": r.get("invoiceNumber"),
                    "date": str(r.get("invoicedAt") or r.get("receivedAt") or "")[:10],
                    "total": money(r.get("total")),
                    "po_number": r.get("purchaseOrderNumber"),
                    "reconciled": r.get("reconciled"),
                    "credit": bool(r.get("creditRequest")),
                }
            )
    else:  # outstanding
        req = {"venue": venue, "status": "NotReceived", "pageSize": 200}
        if window:
            req["from_date"] = window["start"][:10]
            req["to_date"] = window["end"][:10]
        rows = call_api("loadedhub", "list_stock_invoices", req)
        if not isinstance(rows, list):
            return {"error": (rows or {}).get("error") or "invoice list unavailable"}
        out_rows = []
        for r in rows:
            if not isinstance(r, dict) or r.get("deletedAt"):
                continue
            if query and query not in str(r.get("supplierName") or "").lower():
                continue
            out_rows.append(
                {
                    "id": r.get("id"),
                    "supplier": r.get("supplierName"),
                    "invoice_number": r.get("referenceNumber"),
                    "date": str(r.get("issuedAt") or "")[:10],
                    "total": money(r.get("total")),
                    "po_number": r.get("purchaseOrderNumber"),
                    "has_file": bool(r.get("fileId")),
                }
            )

    total = len(out_rows)
    out_rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    out = {
        "kind": kind,
        "rows": out_rows[:limit],
        "total_matches": total,
        "shown": min(total, limit),
    }
    if window:
        out["window"] = {"start": window.get("start"), "end": window.get("end")}
    if kind == "received":
        out["note"] = _AGG_NOTE
    return out
