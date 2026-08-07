"""Supplier Spec Dojo — run the CURRENT extraction prompts against stored
sample invoices and diff the result against an admin-accepted baseline.

Faithful replay of the production Layer-6 extraction: the schema and the main
prompt come from the DEPLOYED review engine (the ``review_and_receive_invoices``
consolidator in the config DB, falling back to the repo copy), the supplier
spec's instructions are appended exactly the way the engine wraps them, and the
LLM call uses the same envelope as ``function_executor._extract_uncached``.
Deliberately NO DocumentExtraction cache — every dojo run is a fresh read.
"""

from __future__ import annotations

import ast
import json
import logging
import pathlib
import re
from typing import Any

from sqlalchemy.orm import Session

from app.db.config_models import ConnectorSpec, SupplierInvoiceSpec

logger = logging.getLogger(__name__)

MAIN_PROMPT_NAME = "Main prompt"

_REPO_CONSOLIDATOR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "config"
    / "consolidators"
    / "review_and_receive_invoices.py"
)


def _engine_source(config_db: Session) -> str:
    """The deployed engine's source — the consolidator function_code from the
    config DB (what production actually runs), else the repo copy."""
    spec = (
        config_db.query(ConnectorSpec)
        # The review consolidator rides on the LOADEDHUB connector spec (same
        # row run_review_and_merge reads) — querying the wrong connector here
        # silently falls back to the repo copy, defeating "test what is
        # deployed".
        .filter(ConnectorSpec.connector_name == "loadedhub")
        .first()
    )
    for tool in (spec.tools if spec else None) or []:
        if (
            isinstance(tool, dict)
            and tool.get("action") == "review_and_receive_invoices"
        ):
            code = ((tool.get("consolidator_config") or {}).get("function_code")) or ""
            if "PDF_SCHEMA" in code:
                return code
    try:
        return _REPO_CONSOLIDATOR.read_text()
    except OSError:
        return ""


def dojo_schema(config_db: Session) -> dict:
    """PDF_SCHEMA as the deployed engine defines it."""
    src = _engine_source(config_db)
    m = re.search(r"^PDF_SCHEMA = (\{.*?^\})", src, re.S | re.M)
    if not m:
        raise RuntimeError("PDF_SCHEMA not found in the review engine source")
    return ast.literal_eval(m.group(1))


def _builtin_main_prompt(src: str) -> str:
    m = re.search(r"_BUILTIN_MAIN_PROMPT = \(\n(.*?)\n\s*\)\n", src, re.S)
    if not m:
        return ""
    lit = "".join(
        line.strip() + "\n"
        for line in m.group(1).splitlines()
        if line.strip().startswith('"')
    )
    try:
        return ast.literal_eval("(" + lit + ")")
    except (ValueError, SyntaxError):
        return ""


def compose_instructions(config_db: Session, spec: SupplierInvoiceSpec) -> str:
    """Main prompt (admin row, else the engine's built-in) + this spec's notes,
    wrapped exactly like the engine's ``_pdf_instructions``."""
    main_row = (
        config_db.query(SupplierInvoiceSpec)
        .filter(
            SupplierInvoiceSpec.name == MAIN_PROMPT_NAME,
            SupplierInvoiceSpec.enabled.is_(True),
        )
        .first()
    )
    main = (main_row.instructions or "").strip() if main_row else ""
    if not main:
        main = _builtin_main_prompt(_engine_source(config_db))
    if not main:
        raise RuntimeError("no main extraction prompt available")
    notes = (spec.instructions or "").strip()
    if notes and spec.name != MAIN_PROMPT_NAME:
        return main + "\n\nSupplier-specific notes for " + spec.name + ":\n" + notes
    return main


def run_extraction(
    db: Session,
    config_db: Session,
    spec: SupplierInvoiceSpec,
    pdf_bytes: bytes,
    content_type: str = "application/pdf",
) -> dict:
    """One fresh extraction of ``pdf_bytes`` under the current prompts —
    the same envelope as function_executor._extract_uncached."""
    import base64

    from app.interpreter.llm_interpreter import call_llm

    schema_text = json.dumps(dojo_schema(config_db), indent=1)
    system_prompt = (
        "You extract structured data from a document exactly as printed. "
        "Return ONLY a JSON object matching this schema (no markdown, no "
        f"commentary):\n{schema_text}\n"
        "Rules: copy amounts, quantities and identifiers exactly as they "
        "appear in the document; use null for any field that is not "
        "present or not legible; never guess or compute values."
    )
    parsed, _ = call_llm(
        system_prompt=system_prompt,
        user_prompt=compose_instructions(config_db, spec),
        db=db,
        call_type="extraction",
        max_tokens=4096,
        documents=[
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": content_type or "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode(),
                },
            }
        ],
    )
    if not isinstance(parsed, dict):
        raise RuntimeError("extraction did not return an object")
    if parsed.get("error"):
        raise RuntimeError(str(parsed["error"]))
    return parsed


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

_HEADER_FIELDS = (
    "document_type",
    "invoice_number",
    "invoice_date",
    "purchase_order_number",
    "subtotal_ex_tax",
    "tax_amount",
    "total_incl_tax",
)
_LINE_FIELDS = (
    "quantity",
    "unit_of_measure",
    "unit_price_ex_tax",
    "line_total_ex_tax",
    "unit_unrecognisable",
)


def _norm_text(v: Any) -> str:
    return " ".join(str(v or "").split()).lower()


def _norm_unit(v: Any) -> str:
    s = "".join(ch for ch in str(v or "").lower() if ch.isalnum())
    # '12pk' and '12 pack' are the same printed unit.
    return re.sub(r"pk$", "pack", s)


def _values_differ(field: str, exp: Any, cur: Any) -> bool:
    if field == "unit_of_measure":
        return _norm_unit(exp) != _norm_unit(cur)
    if field == "unit_unrecognisable":
        return bool(exp) != bool(cur)
    if isinstance(exp, (int, float)) or isinstance(cur, (int, float)):
        try:
            return abs(float(exp) - float(cur)) > 0.011
        except (TypeError, ValueError):
            return True
    return _norm_text(exp) != _norm_text(cur)


def compare_extractions(expected: dict, current: dict) -> list[dict]:
    """Structured diffs of ``current`` against the ``expected`` baseline.

    Each diff: {field, line (1-based or None for header), description,
    expected, actual}. Lines pair by index — same PDF, so a changed order or
    count is itself a finding (reported as missing/extra lines).
    """
    diffs: list[dict] = []
    for f in _HEADER_FIELDS:
        exp, cur = expected.get(f), current.get(f)
        if exp is None and cur is None:
            continue
        if _values_differ(f, exp, cur):
            diffs.append(
                {
                    "field": f,
                    "line": None,
                    "description": None,
                    "expected": exp,
                    "actual": cur,
                }
            )
    exp_lines = expected.get("lines") or []
    cur_lines = current.get("lines") or []
    for i, exp_ln in enumerate(exp_lines):
        desc = exp_ln.get("description") or exp_ln.get("code") or f"line {i + 1}"
        if i >= len(cur_lines):
            diffs.append(
                {
                    "field": "line_missing",
                    "line": i + 1,
                    "description": desc,
                    "expected": desc,
                    "actual": None,
                }
            )
            continue
        cur_ln = cur_lines[i]
        for f in _LINE_FIELDS:
            exp_v, cur_v = exp_ln.get(f), cur_ln.get(f)
            if exp_v is None and cur_v in (None, False):
                continue
            if _values_differ(f, exp_v, cur_v):
                diffs.append(
                    {
                        "field": f,
                        "line": i + 1,
                        "description": desc,
                        "expected": exp_v,
                        "actual": cur_v,
                    }
                )
    for i in range(len(exp_lines), len(cur_lines)):
        cur_ln = cur_lines[i]
        diffs.append(
            {
                "field": "line_extra",
                "line": i + 1,
                "description": cur_ln.get("description") or cur_ln.get("code"),
                "expected": None,
                "actual": cur_ln.get("description") or cur_ln.get("code"),
            }
        )
    return diffs


# ---------------------------------------------------------------------------
# ReceiveInvoiceEditor payload
# ---------------------------------------------------------------------------


def editor_payload(
    expected: dict | None, current: dict, diffs: list[dict], status: str
) -> dict:
    """Map a dojo run into the ReceiveInvoiceEditor's data shape.

    The BASELINE renders as the draft lines and the CURRENT run rides in as
    "the copy" (copy_* fields + mismatch flags), so differences light up with
    the editor's existing affordances. With no baseline yet, the current run
    renders plain — the admin reviews it and stores it as expected.
    """
    base = expected or current
    cur_lines = current.get("lines") or []
    lines = []
    for i, ln in enumerate(base.get("lines") or []):
        cur_ln = cur_lines[i] if i < len(cur_lines) else {}
        qty = ln.get("quantity")
        cost = ln.get("unit_price_ex_tax")
        row: dict[str, Any] = {
            "id": f"dojo-{i}",
            "code": ln.get("code"),
            "display_code": ln.get("code"),
            "description": ln.get("description"),
            "unit": ln.get("unit_of_measure"),
            "quantity_received": qty,
            "unit_cost": cost,
            "total_cost": ln.get("line_total_ex_tax"),
        }
        if expected is not None and cur_ln:
            if _values_differ("quantity", qty, cur_ln.get("quantity")):
                row["copy_quantity"] = cur_ln.get("quantity")
                row["copy_quantity_mismatch"] = True
            if _values_differ(
                "unit_price_ex_tax", cost, cur_ln.get("unit_price_ex_tax")
            ):
                row["copy_unit_price"] = cur_ln.get("unit_price_ex_tax")
                row["copy_unit_cost_mismatch"] = True
            if _values_differ(
                "unit_of_measure",
                ln.get("unit_of_measure"),
                cur_ln.get("unit_of_measure"),
            ):
                row["recommended_unit"] = cur_ln.get("unit_of_measure")
                row["copy_unit_mismatch"] = True
        lines.append(row)
    return {
        "reference_number": base.get("invoice_number"),
        "supplier_name": base.get("supplier_name"),
        "issued_at": base.get("invoice_date"),
        "total": base.get("total_incl_tax"),
        "lines": lines,
        "dojo_status": status,
        "dojo_diffs": diffs,
    }
