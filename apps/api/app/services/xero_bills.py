"""Create the Xero bill for an invoice Norm has just received into Loaded.

One function, called from both receive paths after ``do_receive`` succeeded:
the card's Accept & Receive (``received_invoice.receive_from_draft``) and
autopilot (``invoice_review``). Best effort by design — Loaded already holds
the receive, so a Xero failure is recorded on the working document and
reported, never raised into the receive.

Off unless the PERSON receiving has switched it on (Settings → Preferences →
Receive supplier invoices → "create a bill in Xero", venue_autopilot.
CREATE_XERO_BILL): the caller passes ``enabled``. A background run with no
person is off. The venue must also hold its own live Xero connection; its
accounts and bill status come from the Finance → Bills settings.

The bill itself is built by the ``xero.create_bill`` consolidator, run through
the shared executor the way dashboards and apps run tools: it resolves the
supplier to a Xero contact, the tax types from the organisation's own rates,
and each line's account from the venue's mapping. Its idempotency key is the
Loaded invoice id, so a re-run (a retried job, a second receive of a twin)
returns the same bill rather than a second one.

The supplier's PDF goes on the bill too: the receive's Loaded file is
downloaded and uploaded to ``PUT /Invoices/{id}/Attachments/{filename}`` (a
POST replaces one of the same name; Xero's published contract, raw binary
body). The upload is its own step with its own outcome
(``xero_bill["attachment"]``): a bill without a file is still a good bill, and
the next call for the same invoice retries only the file. Done here, not in
the consolidator sandbox, because the bytes come straight from Loaded.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Connection

logger = logging.getLogger(__name__)

CONNECTOR = "xero"
API_BASE = "https://api.xero.com/api.xro/2.0"
#: Xero's limit per attachment.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_EXT = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
}


def _row(db: Session, venue_id: str | None) -> Connection | None:
    """The venue's own live Xero connection row, else None."""
    if not venue_id:
        return None
    row = (
        db.query(Connection)
        .filter(Connection.connector_name == CONNECTOR, Connection.venue_id == venue_id)
        .first()
    )
    if not row or row.enabled != "true" or not row.access_token or row.needs_reconnect:
        return None
    return row


def mapping_for(db: Session, venue_id: str | None) -> dict | None:
    """The venue's Xero mapping (possibly empty) when it holds its own live
    Xero connection, else None."""
    row = _row(db, venue_id)
    if row is None:
        return None
    return (row.config or {}).get("xero_mapping") or {}


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


def attachment_name(
    invoice_number: str | None, fallback: str, content_type: str
) -> str:
    """A safe filename for Xero: the supplier's number (or the Loaded invoice id)
    plus the extension the file's type implies."""
    base = re.sub(
        r"[^A-Za-z0-9._-]+", "_", str(invoice_number or fallback or "invoice")
    )
    base = (base.strip("._") or "invoice")[:80]
    ext = _EXT.get(str(content_type or "").lower(), "pdf")
    return f"{base}.{ext}"


def attach_file(
    db: Session,
    config_db: Session,
    venue_id: str,
    xero_invoice_id: str,
    loaded_file_id: str,
    *,
    invoice_number: str | None = None,
    loaded_invoice_id: str = "",
) -> dict:
    """Upload the invoice's Loaded file to the Xero bill. Never raises; returns
    ``{"uploaded": bool, "filename": ..., "error": ...}``."""
    import base64

    import httpx

    from app.db.config_models import ConnectionSpec
    from app.services.oauth_service import get_valid_access_token
    from app.services.received_invoice import LoadedInvoiceClient

    try:
        row = _row(db, venue_id)
        if row is None:
            return {"uploaded": False, "error": "Xero is not connected for this venue"}
        tenant = str((row.config or {}).get("xero_tenant_id") or "")
        spec = (
            config_db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not tenant or not spec:
            return {"uploaded": False, "error": "Xero connection is incomplete"}

        lh = LoadedInvoiceClient(db, config_db, venue_id)
        b64, ctype = lh.file_base64(loaded_file_id)
        content = base64.b64decode(b64)
        if not content:
            return {"uploaded": False, "error": "the invoice file is empty"}
        if len(content) > MAX_ATTACHMENT_BYTES:
            return {
                "uploaded": False,
                "error": "the invoice file is over Xero's 25 MB limit",
            }
        filename = attachment_name(invoice_number, loaded_invoice_id, ctype)

        token = get_valid_access_token(spec, db, venue_id=venue_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "xero-tenant-id": tenant,
            "Accept": "application/json",
            "Content-Type": ctype or "application/pdf",
        }
        url = f"{API_BASE}/Invoices/{xero_invoice_id}/Attachments/{filename}"
        resp = httpx.put(url, headers=headers, content=content, timeout=60.0)
        if resp.status_code >= 400:
            # A file of this name is already on the bill: replace it (POST).
            resp = httpx.post(url, headers=headers, content=content, timeout=60.0)
        if resp.status_code >= 400:
            return {
                "uploaded": False,
                "filename": filename,
                "error": f"Xero {resp.status_code}: {resp.text[:200]}",
            }
        return {"uploaded": True, "filename": filename, "bytes": len(content)}
    except Exception as exc:  # the bill exists; a missing file is retried later
        logger.warning(
            "xero attachment failed for invoice %s: %s", xero_invoice_id, exc
        )
        return {"uploaded": False, "error": str(exc)[:200]}


def push_bill_after_receive(
    db: Session,
    config_db: Session,
    venue_id: str | None,
    invoice_id: str,
    data: dict,
    *,
    doc=None,
    enabled: bool = False,
) -> dict | None:
    """Create the bill in Xero for a just-received invoice. Returns the outcome
    (also written to ``data["xero_bill"]`` and the document when given), or
    None when the person has not switched it on or the venue is not connected
    to Xero. Never raises."""
    try:
        if not enabled:
            return None
        mapping = mapping_for(db, venue_id)
        if mapping is None:
            return None
        existing = (
            data.get("xero_bill") if isinstance(data.get("xero_bill"), dict) else None
        )
        if existing and existing.get("created"):
            # The bill stands; only a missing file is worth another try.
            return _ensure_attachment(
                db, config_db, venue_id, invoice_id, data, existing, doc
            )
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
        if outcome.get("created"):
            outcome = _ensure_attachment(
                db, config_db, venue_id, invoice_id, data, outcome, doc
            )
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


def _ensure_attachment(
    db: Session,
    config_db: Session,
    venue_id: str | None,
    invoice_id: str,
    data: dict,
    outcome: dict,
    doc,
) -> dict:
    """Attach the invoice's file to an already-created bill unless it already is.
    Returns the outcome with ``attachment`` filled in; records it on the data
    and the document. Never raises."""
    try:
        done = outcome.get("attachment") or {}
        file_id = data.get("file_id")
        if done.get("uploaded") or not file_id or not outcome.get("invoice_id"):
            return outcome
        result = attach_file(
            db,
            config_db,
            str(venue_id),
            str(outcome["invoice_id"]),
            str(file_id),
            invoice_number=str(data.get("reference_number") or "") or None,
            loaded_invoice_id=invoice_id,
        )
        result["at"] = datetime.now(timezone.utc).isoformat()
        outcome = {**outcome, "attachment": result}
        data["xero_bill"] = outcome
        if doc is not None:
            doc.data = {**(doc.data or {}), "xero_bill": outcome}
            flag_modified(doc, "data")
        if not result.get("uploaded"):
            logger.warning(
                "xero attachment NOT uploaded for Loaded invoice %s: %s",
                invoice_id,
                result.get("error"),
            )
        return outcome
    except Exception as exc:
        logger.exception(
            "xero attachment step failed for invoice %s: %s", invoice_id, exc
        )
        return outcome
