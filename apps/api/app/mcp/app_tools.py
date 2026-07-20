"""App-support tools: the callback surface for Norm's embedded MCP Apps.

An MCP App (SEP-1865) runs in a sandboxed iframe with no Norm session. The
only way it can reach Norm is ``tools/call`` back through the host — which is
exactly what these tools are for. They are code-defined (not config rows)
because each one is a security decision, not a capability an admin toggles:

- ``norm__get_working_document`` / ``norm__update_working_document`` — the
  purchase-order app reading and editing the draft it was opened on. Editing a
  draft is ACCESS_DRAFT, the same level the playbook that created it ran at.
- ``norm__component_api`` — read-only reference data (stock items, suppliers,
  units, live prices) through the same ComponentApiConfig path the web app
  uses. Allowlisted per (component, action); everything else is refused.
- ``norm__place_stock_order`` — the one write. It exists so the human can
  press "Place Order" in the embedded editor; the click is the approval. It
  requires ``mcp:orders:submit``, a scope with its own consent text, and it
  submits through the identical component-api action the web editor uses.

The model can technically call these directly — tools/list must include them
or hosts would refuse the app's calls — so every handler re-checks venue and
ownership server-side, and the descriptions say plainly that they exist for
the embedded apps.

Result shaping: apps parse their own results, so payloads are returned whole
(no ``_slimmed`` envelope, which apps can't unwrap into reference data). Large
list results are paged instead — the app-side shim fetches pages and
reassembles. ``MAX_APP_RESULT_CHARS`` is the per-response ceiling.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.mcp.principal import McpPrincipal
from app.mcp.scopes import ACCESS_DRAFT, ACCESS_READ, ACCESS_WRITE

logger = logging.getLogger(__name__)

# Ceiling for one app-tool response. Same order of magnitude as the UI payload
# budget in results.py — enough for a page of reference data, small enough
# that a host echoing results into model context isn't fed megabytes.
MAX_APP_RESULT_CHARS = 300_000

# Reference-data page size. 1092 stock items with supplier variants measure
# ~750 bytes each slimmed, so 300 items ≈ 225 KB — inside the ceiling.
PAGE_SIZE = 300

# (component_key, action_name) pairs the read bridge may execute. Everything
# else is refused — the bridge must not become "run any configured HTTP call".
COMPONENT_API_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("purchase_order_editor", "get_stock_items_detail"),
        ("purchase_order_editor", "get_suppliers"),
        ("purchase_order_editor", "get_units"),
        ("purchase_order_editor", "get_live_prices"),
    }
)

# The submit action, named separately: it is reachable only through
# norm__place_stock_order, never through the read bridge.
SUBMIT_COMPONENT = ("purchase_order_editor", "create_orders_batch")

# Keys kept when slimming stock items for transport. The purchase-order
# editor reads exactly these (see PurchaseOrderEditor.tsx reference-data load).
_STOCK_ITEM_KEYS = (
    "id",
    "name",
    "groupName",
    "defaultSupplierId",
    "globalSalesTaxSortOrder",
    "globalPrice",
    "orderingUnitId",
    "orderingUnitName",
    "orderingUnitRatio",
    "suppliers",
)
_SUPPLIER_VARIANT_KEYS = (
    "id",
    "supplierId",
    "unitId",
    "unitCost",
    "stockCode",
    "brandId",
    "defaultForSupplier",
)


class AppToolError(Exception):
    """Recoverable app-tool failure; message goes back as an error result."""


def app_tool_defs(mcp_ui_enabled: bool) -> list[dict]:
    """Static definitions for the app-support tools.

    Returned as plain dicts; projection turns them into McpTool. Empty when
    the UI extension is off — without embedded apps there is no caller.
    """
    if not mcp_ui_enabled:
        return []
    return [
        {
            "name": "norm__get_working_document",
            "method": "GET",
            "access": ACCESS_READ,
            "scopes": frozenset({"mcp:orders:draft"}),
            "description": (
                "Read a draft working document (e.g. a purchase order draft) "
                "by id. Used by Norm's embedded order editor to load the "
                "draft it renders; you rarely need to call it yourself."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "working_document_id": {
                        "type": "string",
                        "description": "The draft's id, from the workflow result.",
                    }
                },
                "required": ["working_document_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "norm__update_working_document",
            "method": "POST",
            "access": ACCESS_DRAFT,
            "scopes": frozenset({"mcp:orders:draft"}),
            "description": (
                "Apply edits (quantity change, add/remove line, notes) to a "
                "draft working document. This edits the DRAFT only — nothing "
                "is sent to a supplier. Used by Norm's embedded order editor "
                "when the user edits lines."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "working_document_id": {"type": "string"},
                    "ops": {
                        "type": "array",
                        "description": "Patch operations, e.g. "
                        '{"op":"update_line","index":0,"fields":{"quantity":2}}.',
                        "items": {"type": "object"},
                    },
                    "version": {
                        "type": "integer",
                        "description": "The draft version these ops were made "
                        "against (optimistic concurrency).",
                    },
                },
                "required": ["working_document_id", "ops", "version"],
                "additionalProperties": False,
            },
        },
        {
            "name": "norm__component_api",
            "method": "GET",
            "access": ACCESS_READ,
            "scopes": frozenset({"mcp:orders:read"}),
            "description": (
                "Read-only reference data for Norm's embedded apps (stock "
                "items, suppliers, units, live prices). Large results are "
                "paged: pass page (0-based) and read total_pages from the "
                "response. Used by the embedded order editor; you rarely "
                "need it yourself."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "venue_id": {
                        "type": "string",
                        "description": "Venue id the app was opened for.",
                    },
                    "component_key": {"type": "string"},
                    "action_name": {"type": "string"},
                    "params": {"type": "object"},
                    "page": {"type": "integer", "minimum": 0},
                },
                "required": ["venue_id", "component_key", "action_name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "norm__place_stock_order",
            "method": "POST",
            "access": ACCESS_WRITE,
            "scopes": frozenset({"mcp:orders:submit"}),
            "description": (
                "Submit a purchase order to the supplier. ONLY call this when "
                "the user has pressed Place Order in the embedded order "
                "editor — never on your own initiative. The order is sent "
                "exactly as passed, grouped per supplier."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "venue_id": {"type": "string"},
                    "orders": {
                        "type": "array",
                        "description": "Per-supplier order batches, exactly as "
                        "built by the order editor.",
                        "items": {"type": "object"},
                    },
                },
                "required": ["venue_id", "orders"],
                "additionalProperties": False,
            },
        },
    ]


# ── Authorization helpers ────────────────────────────────────────────────


def _authorize_venue_id(principal: McpPrincipal, venue_id: str, db: Session) -> None:
    """A venue_id from an app is input to be checked, never an assertion.

    Mirrors resolve_mcp_venue (execution.py) but keyed by id — apps carry the
    id they were opened with, not a display name. Fails closed on consent,
    live access, and org, in that order.
    """
    from app.db.models import UserVenueAccess, Venue

    if not venue_id or venue_id not in set(principal.venue_ids):
        raise AppToolError("No access to this venue.")

    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if venue is None:
        raise AppToolError("No access to this venue.")

    still = (
        db.query(UserVenueAccess)
        .filter(
            UserVenueAccess.user_id == principal.user_id,
            UserVenueAccess.venue_id == venue_id,
        )
        .first()
    )
    if not still:
        raise AppToolError("No access to this venue.")
    if venue.organization_id != principal.organization_id:
        raise AppToolError("No access to this venue.")


def _load_owned_doc(principal: McpPrincipal, doc_id: str, db: Session):
    """The working document, if it belongs to a thread this principal owns.

    Ownership is the thread's user - a draft is the artifact of one user's
    workflow run, and MCP must not become a way to read another user's
    drafts by guessing ids.
    """
    from app.db.models import Thread, WorkingDocument

    doc = db.query(WorkingDocument).filter(WorkingDocument.id == doc_id).first()
    if doc is None:
        raise AppToolError("Draft not found.")
    thread = db.query(Thread).filter(Thread.id == doc.thread_id).first()
    if thread is None or thread.user_id != principal.user_id:
        raise AppToolError("Draft not found.")
    return doc


def _doc_payload(doc) -> dict:
    return {
        "id": doc.id,
        "thread_id": doc.thread_id,
        "doc_type": doc.doc_type,
        "data": doc.data,
        "version": doc.version,
        "sync_status": doc.sync_status,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


# ── Handlers ─────────────────────────────────────────────────────────────


def execute_app_tool(
    name: str,
    params: dict,
    principal: McpPrincipal,
    db: Session,
    config_db: Session,
) -> dict:
    """Run one app-support tool. Raises AppToolError for refusals."""
    if name == "norm__get_working_document":
        return _get_working_document(params, principal, db)
    if name == "norm__update_working_document":
        return _update_working_document(params, principal, db)
    if name == "norm__component_api":
        return _component_api(params, principal, db, config_db)
    if name == "norm__place_stock_order":
        return _place_stock_order(params, principal, db, config_db)
    raise AppToolError(f"Unknown app tool: {name}")


def _get_working_document(params: dict, principal: McpPrincipal, db: Session) -> dict:
    doc = _load_owned_doc(principal, str(params.get("working_document_id") or ""), db)
    return _doc_payload(doc)


def _update_working_document(
    params: dict, principal: McpPrincipal, db: Session
) -> dict:
    # The patch mechanics live in the working-documents router; imported so an
    # MCP edit and a web edit are the same code path (ops semantics, pending
    # ops, memory signal, background sync).
    from app.routers.working_documents import _apply_op, _trigger_sync

    doc = _load_owned_doc(principal, str(params.get("working_document_id") or ""), db)

    ops = params.get("ops")
    if not isinstance(ops, list) or not ops:
        raise AppToolError("ops must be a non-empty list.")
    if len(ops) > 50:
        raise AppToolError("Too many ops in one call (max 50).")

    version = params.get("version")
    if doc.version != version:
        # Recoverable: the app refetches and replays. Same contract as the
        # HTTP 409.
        return {
            "conflict": True,
            "expected_version": doc.version,
            "message": f"Version conflict: expected {doc.version}, got {version}",
        }

    from datetime import datetime, timezone

    from sqlalchemy.orm.attributes import flag_modified

    data = doc.data
    for op in ops:
        data = _apply_op(data, op)
    doc.data = data
    flag_modified(doc, "data")
    doc.version += 1
    doc.updated_at = datetime.now(timezone.utc)

    syncable_ops = [o for o in ops if o.get("op") != "set_status"]
    if syncable_ops:
        from app.services.memory_signals import record_draft_edit

        record_draft_edit(
            db,
            organization_id=principal.organization_id,
            user_id=principal.user_id,
            thread_id=doc.thread_id,
            document_kind=doc.doc_type,
            ops=syncable_ops,
        )
        pending = doc.pending_ops or []
        pending.extend(syncable_ops)
        doc.pending_ops = pending
        flag_modified(doc, "pending_ops")
        if doc.sync_mode == "auto":
            doc.sync_status = "dirty"
        elif doc.sync_mode == "submit":
            doc.sync_status = "pending_submit"

    db.commit()
    db.refresh(doc)
    if doc.sync_mode == "auto":
        _trigger_sync(doc.id)
    return _doc_payload(doc)


def _slim_stock_items(data):
    """Keep only the fields the order editor reads. ~40% smaller on the wire."""
    if not isinstance(data, list):
        return data
    out = []
    for item in data:
        if not isinstance(item, dict):
            out.append(item)
            continue
        slim = {k: item.get(k) for k in _STOCK_ITEM_KEYS if k in item}
        suppliers = slim.get("suppliers")
        if isinstance(suppliers, list):
            slim["suppliers"] = [
                {k: s.get(k) for k in _SUPPLIER_VARIANT_KEYS if k in s}
                if isinstance(s, dict)
                else s
                for s in suppliers
            ]
        out.append(slim)
    return out


def _component_api(
    params: dict, principal: McpPrincipal, db: Session, config_db: Session
) -> dict:
    from app.services.component_api import ComponentApiError, execute_component_action

    component_key = str(params.get("component_key") or "")
    action_name = str(params.get("action_name") or "")
    if (component_key, action_name) not in COMPONENT_API_ALLOWLIST:
        raise AppToolError(
            f"{component_key}/{action_name} is not available on this surface."
        )

    venue_id = str(params.get("venue_id") or "")
    _authorize_venue_id(principal, venue_id, db)

    call_params = params.get("params") if isinstance(params.get("params"), dict) else {}
    try:
        result = execute_component_action(
            component_key, action_name, call_params, venue_id, db, config_db
        )
    except ComponentApiError as e:
        raise AppToolError(str(e)) from e

    data = result.get("data")
    if action_name == "get_stock_items_detail":
        data = _slim_stock_items(data)

    # Page large list results; the app-side shim reassembles.
    if isinstance(data, list):
        page = params.get("page")
        page = int(page) if isinstance(page, (int, float)) and page >= 0 else 0
        total_pages = max(1, -(-len(data) // PAGE_SIZE))
        page = min(page, total_pages - 1)
        return {
            "data": data[page * PAGE_SIZE : (page + 1) * PAGE_SIZE],
            "page": page,
            "total_pages": total_pages,
            "total_items": len(data),
            "status_code": result.get("status_code"),
        }
    return {"data": data, "status_code": result.get("status_code")}


def _place_stock_order(
    params: dict, principal: McpPrincipal, db: Session, config_db: Session
) -> dict:
    from app.services.component_api import ComponentApiError, execute_component_action

    venue_id = str(params.get("venue_id") or "")
    _authorize_venue_id(principal, venue_id, db)

    orders = params.get("orders")
    if not isinstance(orders, list) or not orders:
        raise AppToolError("orders must be a non-empty list of supplier batches.")
    if len(orders) > 20:
        raise AppToolError("Too many supplier batches in one order (max 20).")
    total_lines = 0
    for batch in orders:
        if not isinstance(batch, dict):
            raise AppToolError("Each order batch must be an object.")
        lines = batch.get("lines")
        if not isinstance(lines, list) or not lines:
            raise AppToolError("Each order batch must have at least one line.")
        total_lines += len(lines)
    if total_lines > 200:
        raise AppToolError("Too many order lines in one submission (max 200).")

    logger.info(
        "mcp_place_stock_order",
        extra={
            "venue_id": venue_id,
            "batches": len(orders),
            "lines": total_lines,
            "user_id": principal.user_id,
        },
    )

    component_key, action_name = SUBMIT_COMPONENT
    try:
        result = execute_component_action(
            component_key, action_name, orders, venue_id, db, config_db
        )
    except ComponentApiError as e:
        raise AppToolError(str(e)) from e

    if result.get("error"):
        # Upstream refused — pass the reason through, but as data, not as a
        # tool error: the app shows it inline next to the button.
        return {
            "submitted": False,
            "status_code": result.get("status_code"),
            "detail": result.get("data"),
        }
    return {
        "submitted": True,
        "status_code": result.get("status_code"),
        "detail": result.get("data"),
    }
