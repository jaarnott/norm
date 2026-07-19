# Consolidator function_code — canonical sources

Consolidator tools live in the shared config DB (`connector_specs.tools[].consolidator_config.function_code`),
which no test, type checker, or code review can see — the "config blind spot"
(docs/architecture.md §13). Files in this directory are the **reviewed,
version-controlled sources of truth** for that code:

- the unit tests exec these files under the real sandbox namespace
  (`_SAFE_BUILTINS`/`_SAFE_MODULES` from `app/connectors/function_executor.py`),
  so CI validates the exact code production runs;
- a sync script per workflow (see `scripts/`) copies the file verbatim into the
  config DB row.

**Edit here → run tests → run the sync script.** Never edit function_code
directly in Settings for consolidators tracked in this directory — the next
sync overwrites it.

| File | Config row | Sync script |
|---|---|---|
| `review_and_receive_invoices.py` | `loadedhub` spec → tool `review_and_receive_invoices` | `scripts/sync_invoice_receiving_config.py` |
| `reconcile_received_invoices.py` | `loadedhub` spec → tool `reconcile_received_invoices` | `scripts/sync_invoice_receiving_config.py` |
| `calculate_template_stock_requirements.py` | `loadedhub` spec → tool `calculate_template_stock_requirements` | `scripts/sync_stock_requirements_config.py` |
| `for_period.py` | `loadedhub` spec → the 13 `*_for_period` tools (one function_code, per-tool `wraps`/`start_param`/`end_param`) | `scripts/sync_for_period_config.py` |

## Interactive "Fix & Receive" card

`review_and_receive_invoices` also returns a `fixes` list (link a PO; correct a
line's unit + supplier variant). The tool declares `display_component:
invoice_fixes` + `suppress_display_early_exit` so the card renders **beneath**
the narrated report (the flag opts out of the tool loop's display-only
early-exit). Apply is NOT a config write — the `InvoiceFixesCard` POSTs to
`/api/invoice-fixes/apply` (`app/routers/invoice_fixes.py`), which orchestrates
the multi-step LoadedHub writes with the venue connector token. Contracts
(verified live in the test env, 18 Jul 2026):

- **link_po**: `GET /1.0/stock/internal/purchase-orders` (filter client-side on
  `orderNumber`, drop a leading `PO`), then `PUT /1.0/stock/internal/invoices/{id}`
  with `linkedPurchaseOrderId` + `purchaseOrderNumber` set. Does not re-match lines.
- **unit**: resolve the unit name via `GET /1.0/stock/internal/units`, `PUT` the
  invoice with the line's `unit`/`linkedUnitId`/`linkedUnitRatio`, then
  `PATCH /1.0/stock/internal/item-supplier-variant/{id}` `{unitId}` where the
  variant is `items/{linkedItemId}.suppliers[]` matched by `supplierId` + `stockCode`.
  If the proposed unit has no matching Loaded unit, the fix fails (create it first).

`calculate_template_stock_requirements` also depends on a plain connector
action, **`get_stock_item_minimums`** (loadedhub → `/1.0/stock/internal/items`),
which returns each item's par level plus the unit ratios to convert it into
counting units. That action likewise lives in the config DB; its reviewed source
and sync script is `scripts/sync_stock_item_minimums_action.py`. It enforces par
levels (order up to the minimum even with no usage) but deliberately does **not**
apply LoadedHub's 20% forecast buffer.

## Treat a failed API call as a failure, not as no data

`call_api` and `call_api_parallel` return `{"error": "..."}` when a call fails —
they do **not** raise. A consolidator that reaches straight for the data
(`result.get("lines", [])`) silently turns an upstream outage into an empty
result, and the agent then reports it as a data problem.

That is not hypothetical: LoadedHub's stock-on-hand report 500'd under load,
`calculate_template_stock_requirements` read `.get("lines", [])` off the error
dict, logged "0 items need reordering out of 0 total", and the agent told the
user the template "may be empty or unconfigured" — when it had 309 items.

Check every result before using it, and say which upstream call failed.

Sandbox constraints (enforced by `function_executor.py`): no imports —
`math`, `json`, `datetime`, `decimal` and the `extract_document()` helper are
injected; API access only via `call_api`/`call_api_parallel`, capped by
`consolidator_config.max_api_calls` (default 20, hard max 200); non-GET actions
must be declared in `consolidator_config.allowed_write_actions` (deny by default).
