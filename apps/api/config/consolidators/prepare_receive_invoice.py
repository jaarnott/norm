# ruff: noqa: F821 — `json` is injected into the sandbox namespace by
# app/connectors/function_executor.py; it is not an import.
#
# Canonical function_code for the `receive_loadedhub_invoice` tool — prepares
# ONE outstanding supplier invoice as an editable "Receive Invoice" draft, so a
# user can receive a specific invoice from a conversation (the Invoices page
# uses the equivalent /invoice-fixes/draft endpoint).
#
# Synced into the config DB. It runs in the no-import sandbox, so the
# camelCase -> snake_case shaping is inlined here rather than importing
# app.services.received_invoice.build_received_invoice_data — KEEP THE TWO IN
# SYNC (that Python function is the source of truth; the web draft endpoint uses
# it, and tests pin its output).
#
# The tool carries a `working_document` config, so the tool loop materialises a
# `received_invoice` working document from this result and renders the
# receive_invoice_editor over it (web); the MCP path renders it through
# receive_display.py (Claude). On any error we RAISE (not return {"error": ...})
# so execute_function reports data=None and NO draft is created from a bad
# result.


def run(params, call_api, log):
    venue = params.get("venue")
    invoice_id = params.get("invoice_id")
    if not invoice_id:
        raise ValueError(
            "invoice_id is required — the id of the invoice to receive "
            "(from list_stock_invoices / the outstanding list)."
        )

    base = {"venue": venue} if venue else {}
    detail = call_api(
        "loadedhub", "get_invoice_detail", dict(base, invoice_id=invoice_id)
    )
    if isinstance(detail, dict) and detail.get("error"):
        raise ValueError(
            "Could not fetch invoice " + str(invoice_id) + ": " + str(detail["error"])
        )
    if not isinstance(detail, dict) or not detail.get("id"):
        raise ValueError("Invoice " + str(invoice_id) + " was not found.")
    if detail.get("isReceived"):
        raise ValueError(
            "Invoice "
            + str(detail.get("referenceNumber") or invoice_id)
            + " has already been received."
        )

    log(
        "Preparing receive draft for "
        + str(detail.get("referenceNumber") or invoice_id)
    )

    def shape_line(ln):
        linked_unit_id = ln.get("linkedUnitId")
        return {
            "id": ln.get("id"),
            "code": ln.get("code"),
            "description": ln.get("description"),
            "brand": ln.get("brand"),
            "unit": ln.get("unit"),
            "linked_unit_id": linked_unit_id,
            "original_unit_id": linked_unit_id,
            "unit_ratio": ln.get("linkedUnitRatio"),
            "quantity_ordered": ln.get("quantityOrdered"),
            "quantity_received": ln.get("quantityReceived"),
            "unit_cost": ln.get("unitCost"),
            "total_cost": ln.get("totalCost"),
            "tax_amount": ln.get("taxAmount"),
            "sale_tax_rate": ln.get("saleTaxRate"),
            "linked_item_id": ln.get("linkedItemId"),
            "linked_brand_id": ln.get("linkedBrandId"),
            "item_type": ln.get("itemType"),
        }

    lines = [
        shape_line(ln) for ln in (detail.get("lines") or []) if isinstance(ln, dict)
    ]

    def fingerprint(det):
        # Mirror of received_invoice.invoice_fingerprint (FNV-1a — the sandbox
        # has no hashlib). Change detection for the cached review: same content
        # → same fingerprint → a re-open skips the engine entirely.
        material = {
            "lines": [
                [
                    ln.get("id"),
                    ln.get("quantityReceived"),
                    ln.get("unitCost"),
                    ln.get("totalCost"),
                    ln.get("linkedItemId"),
                    ln.get("linkedUnitId"),
                    ln.get("unit"),
                    ln.get("code"),
                    bool(ln.get("deletedAt")),
                ]
                for ln in (det.get("lines") or [])
                if isinstance(ln, dict)
            ],
            "subtotal": det.get("subtotal"),
            "tax": det.get("taxAmount"),
            "total": det.get("total"),
            "po": det.get("linkedPurchaseOrderId"),
            "file": det.get("fileId"),
        }
        h = 0xCBF29CE484222325
        for b in json.dumps(material, sort_keys=True, default=str).encode():
            h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return format(h, "016x")

    return {
        "invoice_id": detail.get("id"),
        "reference_number": detail.get("referenceNumber"),
        "supplier_name": detail.get("supplierName"),
        "linked_supplier_id": detail.get("linkedSupplierId"),
        "purchase_order_number": detail.get("purchaseOrderNumber"),
        "linked_purchase_order_id": detail.get("linkedPurchaseOrderId"),
        "issued_at": detail.get("issuedAt"),
        "due_at": detail.get("dueAt"),
        "received_at": detail.get("receivedAt"),
        "subtotal": detail.get("subtotal"),
        "tax_amount": detail.get("taxAmount"),
        "discount_amount": detail.get("discountAmount"),
        "total": detail.get("total"),
        "unit_cost_includes_tax": bool(detail.get("unitCostIncludesTax")),
        "file_id": detail.get("fileId"),
        "is_received": bool(detail.get("isReceived")),
        "status": "draft",
        "notes": detail.get("notes") or "",
        "loaded_invoice_fingerprint": fingerprint(detail),
        "lines": lines,
    }
