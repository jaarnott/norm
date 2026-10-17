"""Create the Xero bill for an invoice Norm has just received into Loaded.

One function, called from both receive paths after ``do_receive`` succeeded:
the card's Accept & Receive (``received_invoice.receive_from_draft``) and
autopilot (``invoice_review``). Best effort by design — Loaded already holds
the receive, so a Xero failure is recorded on the working document and
reported, never raised into the receive.

Off unless the venue's Xero mapping says ``push_bills`` (set on the Finance →
Bills page): posting a venue's bills is that venue's decision, and the
``review_and_receive_invoices`` consolidator is live config in every
environment the moment it is synced.

The bill itself is built by the ``xero.create_bill`` consolidator, run through
the shared executor the way dashboards and apps run tools: it resolves the
supplier to a Xero contact, the tax types from the organisation's own rates,
and each line's account from the venue's mapping. Its idempotency key is the
Loaded invoice id, so a re-run (a retried job, a second receive of a twin)
returns the same bill rather than a second one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Connection

logger = logging.getLogger(__name__)

CONNECTOR = "xero"


def mapping_for(db: Session, venue_id: str | None) -> dict | None:
    """The venue's Xero mapping when it is connected and pushes bills, else None."""
    if not venue_id:
        return None
    row = (
        db.query(Connection)
        .filter(Connection.connector_name == CONNECTOR, Connection.venue_id == venue_id)
        .first()
    )
    if not row or row.enabled != "true" or not row.access_token or row.needs_reconnect:
        return None
    mapping = (row.config or {}).get("xero_mapping") or {}
    if not mapping.get("push_bills"):
        return None
    return mapping


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bill_params_from_doc(venue_id: str, invoice_id: str, data: dict) -> dict:
    """The create_bill parameters for a received invoice's working values.

    Lines carry what the review settled on: description, quantity received,
    unit cost, the per-line GST amount (None = standard GST, 0 = none) and,
    when Loaded's draft had it, the item's category for the account mapping.
    Struck lines and empty lines are left out; a line with a total but no unit
    cost is sent as one unit at that total.
    """
    lines = []
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or ln.get("struck"):
            continue
        qty = _num(ln.get("quantity_received"))
        unit = _num(ln.get("unit_cost"))
        total = _num(ln.get("total_cost"))
        if unit is None and total is not None:
            qty, unit = 1.0, total
        if unit is None or (not qty and not total):
            continue
        tax = ln.get("tax_amount")
        line = {
            "description": str(
                ln.get("description") or ln.get("item_name") or ""
            ).strip()
            or "Line",
            "quantity": qty or 1.0,
            "unit_amount": round(unit, 4),
            "gst": "no" if (tax is not None and not _num(tax)) else "yes",
        }
        category = ln.get("category") or ln.get("item_category")
        if category:
            line["category"] = str(category)
        lines.append(line)
    date = str(data.get("issued_at") or data.get("received_at") or "")[:10] or (
        datetime.now(timezone.utc).date().isoformat()
    )
    params = {
        "venue_id": venue_id,
        "supplier": str(data.get("supplier_name") or "").strip(),
        "invoice_number": str(data.get("reference_number") or "").strip(),
        "date": date,
        "lines": lines,
        "line_amount_types": "Inclusive"
        if data.get("unit_cost_includes_tax")
        else "Exclusive",
        "create_contact": "true",
        "idempotency_key": f"norm:loaded:{invoice_id}"[:128],
    }
    due = str(data.get("due_at") or "")[:10]
    if due:
        params["due_date"] = due
    return params


def push_bill_after_receive(
    db: Session,
    config_db: Session,
    venue_id: str | None,
    invoice_id: str,
    data: dict,
    *,
    doc=None,
) -> dict | None:
    """Create the bill in Xero for a just-received invoice. Returns the outcome
    (also written to ``data["xero_bill"]`` and the document when given), or
    None when the venue does not push bills. Never raises."""
    try:
        mapping = mapping_for(db, venue_id)
        if mapping is None:
            return None
        existing = (
            data.get("xero_bill") if isinstance(data.get("xero_bill"), dict) else None
        )
        if existing and existing.get("created"):
            return existing
        params = bill_params_from_doc(str(venue_id), invoice_id, data)
        if not params["lines"] or not params["supplier"]:
            outcome = {
                "created": False,
                "error": "nothing to bill: no supplier or no priced lines on the receive",
                "at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            if mapping.get("bill_status"):
                params["status"] = str(mapping["bill_status"])
            from app.connectors.tool_executor import execute_connector_tool

            result = execute_connector_tool(
                connector_name=CONNECTOR,
                action="create_bill",
                params=params,
                db=db,
                config_db=config_db,
                venue_id=venue_id,
                strict_venue=True,
            )
            payload = result.payload if isinstance(result.payload, dict) else {}
            if result.success and payload.get("created"):
                outcome = {**payload, "at": datetime.now(timezone.utc).isoformat()}
            else:
                outcome = {
                    "created": False,
                    "error": payload.get("error")
                    or result.error
                    or "Xero did not create the bill",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
        data["xero_bill"] = outcome
        if doc is not None:
            doc.data = {**(doc.data or {}), "xero_bill": outcome}
            flag_modified(doc, "data")
        if outcome.get("created"):
            logger.info(
                "xero bill %s created for Loaded invoice %s (venue %s)",
                outcome.get("number"),
                invoice_id,
                venue_id,
            )
        else:
            logger.warning(
                "xero bill NOT created for Loaded invoice %s (venue %s): %s",
                invoice_id,
                venue_id,
                outcome.get("error"),
            )
        return outcome
    except Exception as exc:  # the receive already happened; never break it
        logger.exception("xero bill push failed for invoice %s: %s", invoice_id, exc)
        outcome = {
            "created": False,
            "error": str(exc)[:300],
            "at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            data["xero_bill"] = outcome
        except Exception:
            pass
        return outcome
