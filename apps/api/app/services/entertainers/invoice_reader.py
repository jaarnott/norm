"""Reading a performer's invoice with the document reader (plan §11.5,
§5.2 item 16, D35).

A function beside ``invoice_extraction.extract_invoice_copy`` with its own
schema: payee name, GST number, invoice number and date, each line
(description, date if printed, amount), subtotal, GST, total — what is
PRINTED. The model reads; it decides nothing (``invoice_check`` does, in
plain code). The same ``call_llm(..., call_type="extraction",
documents=[block])`` and ``attachments.build_content_block`` the supplier
invoices use, so a PDF or a photo both work.

**Cost is Norm's** (D35): the call runs under an empty caller scope and no
thread, so ``LlmCall.organization_id`` is None — a performer's upload never
spends the venue's allowance. **Cached by the file's SHA-256**: the row is
content-addressed and keeps ``extracted``, so a second check of the same
bytes never calls the model. **Never called in tests** — ``read_file`` is
the seam every test replaces.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.db.entertainers_models import EntFile

logger = logging.getLogger(__name__)

SCHEMA = {
    "payee_name": "string|null — the name of the business or person issuing the invoice, as printed",
    "gst_number": "string|null — the GST or IRD number printed on the invoice, digits as printed",
    "invoice_number": "string|null",
    "invoice_date": "string|null — YYYY-MM-DD when a date is printed",
    "lines": [
        {
            "description": "string",
            "date": "string|null — YYYY-MM-DD when the line names a date",
            "amount": "number|null — the line's amount as printed",
        }
    ],
    "subtotal": "number|null — the total before GST, as printed",
    "gst": "number|null — the GST amount, as printed (null when none is printed)",
    "total": "number|null — the amount payable, as printed",
    "currency": "string|null",
}
INSTRUCTIONS = (
    "Read the attached invoice and copy what is printed into the schema: the "
    "payee, their GST number, the invoice number and date, every line with its "
    "description, date (if one is printed on the line) and amount, and the "
    "subtotal, GST and total. Amounts exactly as printed; null for anything "
    "not printed or not legible. Do not compute or infer a value."
)


def document_block(file: EntFile) -> dict:
    from app.services.attachments import build_content_block

    return build_content_block(bytes(file.data), file.content_type, file.filename)


def _call(db: Session, block: dict) -> dict:
    """One extraction call, attributed to nobody (Norm's cost)."""
    from app.interpreter.llm_interpreter import call_llm
    from app.services import caller_scope
    from app.services.invoice_extraction import extraction_system_prompt

    with caller_scope.use(caller_scope.Scope(org_id=None)):
        parsed, _call_id = call_llm(
            system_prompt=extraction_system_prompt(SCHEMA),
            user_prompt=INSTRUCTIONS,
            db=db,
            thread_id=None,
            call_type="extraction",
            max_tokens=4096,
            documents=[block],
        )
    return (
        parsed
        if isinstance(parsed, dict)
        else {"error": "extraction did not return an object"}
    )


def read_file(db: Session, file: EntFile) -> dict:
    """What the invoice says, from the cached ``extracted`` when the file
    has been read before, else from one model call. An error dict
    (``{"error": …}``) is returned, never raised, and never cached."""
    if (
        isinstance(file.extracted, dict)
        and file.extracted
        and "error" not in file.extracted
    ):
        return file.extracted
    try:
        block = document_block(file)
    except Exception as exc:  # noqa: BLE001 — a type the reader can't send
        return {"error": f"cannot read this file type: {exc}"}
    try:
        parsed = _call(db, block)
    except Exception as exc:  # noqa: BLE001 — reported as unreadable/transient
        logger.warning("invoice reader failed for %s: %s", file.id, exc)
        return {"error": str(exc)[:300], "transient": True}
    if "error" not in parsed:
        file.extracted = json.loads(json.dumps(parsed))
        db.flush()
    return parsed
