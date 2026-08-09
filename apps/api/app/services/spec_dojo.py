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


def compose_instructions(
    config_db: Session,
    spec: SupplierInvoiceSpec,
    override_instructions: str | None = None,
) -> str:
    """Main prompt (admin row, else the engine's built-in) + this spec's notes,
    wrapped exactly like the engine's ``_pdf_instructions``.

    ``override_instructions`` substitutes CANDIDATE text without touching
    stored config: the spec's notes for a supplier row, the main prompt itself
    when ``spec`` IS the reserved Main prompt row — the dojo's
    test-before-commit primitive.
    """
    is_main = spec.name == MAIN_PROMPT_NAME
    main_row = (
        config_db.query(SupplierInvoiceSpec)
        .filter(
            SupplierInvoiceSpec.name == MAIN_PROMPT_NAME,
            SupplierInvoiceSpec.enabled.is_(True),
        )
        .first()
    )
    main = (main_row.instructions or "").strip() if main_row else ""
    if is_main and override_instructions is not None:
        main = override_instructions.strip()
    if not main:
        main = _builtin_main_prompt(_engine_source(config_db))
    if not main:
        raise RuntimeError("no main extraction prompt available")
    notes = (spec.instructions or "").strip()
    if not is_main and override_instructions is not None:
        notes = override_instructions.strip()
    if notes and not is_main:
        return main + "\n\nSupplier-specific notes for " + spec.name + ":\n" + notes
    return main


def run_extraction(
    db: Session,
    config_db: Session,
    spec: SupplierInvoiceSpec,
    pdf_bytes: bytes,
    content_type: str = "application/pdf",
    override_instructions: str | None = None,
) -> dict:
    """One fresh extraction of ``pdf_bytes`` under the current prompts —
    the same envelope as function_executor._extract_uncached. Candidate text
    via ``override_instructions`` (see compose_instructions)."""
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
        user_prompt=compose_instructions(config_db, spec, override_instructions),
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
    # Lowercase + whitespace-insensitive, dots KEPT ('1.9 KG' differs from
    # '19 KG'); '12pk' and '12 pack' are the same printed unit.
    s = "".join(str(v or "").lower().split())
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
# Supplier-name → spec matching + Add-to-Dojo intake
# ---------------------------------------------------------------------------


def find_spec_for_supplier(
    config_db: Session, supplier_name: str
) -> SupplierInvoiceSpec | None:
    """The spec row matching a supplier NAME — the server-side port of the
    engine's ``_supplier_notes`` rule: normalized name/alias equality or
    substring, candidates under 3 normalized chars skipped, the reserved Main
    prompt row excluded, first match wins."""

    def _n(v: object) -> str:
        return "".join(ch for ch in str(v or "").lower() if ch.isalnum())

    sname = _n(supplier_name)
    if not sname:
        return None
    for sp in (
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.enabled.is_(True))
        .order_by(SupplierInvoiceSpec.name)
        .all()
    ):
        if sp.name == MAIN_PROMPT_NAME:
            continue
        for candidate in [sp.name] + list(sp.aliases or []):
            c = _n(candidate)
            if len(c) >= 3 and (c == sname or c in sname):
                return sp
    return None


def find_or_create_spec_for_supplier(
    config_db: Session, supplier_name: str
) -> tuple[SupplierInvoiceSpec, bool]:
    """Match, else create an empty spec row for the supplier (Add-to-Dojo on
    a supplier with no spec yet). Returns (spec, created)."""
    spec = find_spec_for_supplier(config_db, supplier_name)
    if spec:
        return spec, False
    name = (supplier_name or "").strip()
    if not name:
        raise RuntimeError("invoice has no supplier name — cannot file a spec")
    spec = SupplierInvoiceSpec(name=name, aliases=[], instructions="")
    config_db.add(spec)
    config_db.commit()
    config_db.refresh(spec)
    return spec, True


# ---------------------------------------------------------------------------
# Candidate runs — test a PROPOSED prompt against the dojo without committing
# ---------------------------------------------------------------------------


def candidate_run(
    db: Session,
    config_db: Session,
    spec: SupplierInvoiceSpec,
    instructions: str,
    skip_sample_id: str | None = None,
) -> dict:
    """Re-extract every affected sample under CANDIDATE instruction text and
    diff each against its baseline. Stored config is never touched.

    For a supplier row that means the spec's own samples (its text only
    composes into that supplier's prompt); for the reserved Main prompt row it
    means EVERY sample in the dojo. Samples without a baseline report 'new'.
    ``skip_sample_id`` lets the analysis agent exclude the sample it is
    grading against its own ground truth.
    """
    from app.db.config_models import SupplierSpecSample

    q = config_db.query(SupplierSpecSample)
    if spec.name != MAIN_PROMPT_NAME:
        q = q.filter(SupplierSpecSample.spec_id == spec.id)
    samples = q.order_by(SupplierSpecSample.created_at).all()
    results = []
    for s in samples:
        if skip_sample_id and s.id == skip_sample_id:
            continue
        own_spec = (
            spec
            if s.spec_id == spec.id
            else config_db.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.id == s.spec_id)
            .first()
        )
        if own_spec is None:
            continue
        # A main-prompt candidate overrides the MAIN text for every sample
        # (their own supplier notes still append); a supplier candidate
        # overrides only its own spec's notes.
        try:
            if spec.name == MAIN_PROMPT_NAME:
                extraction = _extract_with_main_override(
                    db, config_db, own_spec, s, instructions
                )
            else:
                extraction = run_extraction(
                    db,
                    config_db,
                    own_spec,
                    s.pdf_bytes,
                    s.content_type,
                    override_instructions=instructions,
                )
            if s.expected is not None:
                diffs = compare_extractions(s.expected, extraction)
                status = "pass" if not diffs else "fail"
            else:
                diffs, status = [], "new"
            results.append(
                {
                    "id": s.id,
                    "label": s.label,
                    "status": status,
                    "diffs": diffs,
                    "extraction": extraction,
                }
            )
        except Exception as exc:  # noqa: BLE001 — one broken sample must not sink the run
            logger.warning("candidate run failed for sample %s: %s", s.id, exc)
            results.append(
                {
                    "id": s.id,
                    "label": s.label,
                    "status": "error",
                    "diffs": [],
                    "error": str(exc),
                }
            )
    statuses = [r["status"] for r in results]
    return {
        "samples": results,
        "passed": statuses.count("pass"),
        "failed": statuses.count("fail"),
        "errors": statuses.count("error"),
        "new": statuses.count("new"),
    }


def _extract_with_main_override(db, config_db, own_spec, sample, main_text):
    """Extraction for a MAIN-prompt candidate: the override replaces the main
    text while the sample's own supplier notes still append."""
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
    main = (main_text or "").strip()
    notes = (own_spec.instructions or "").strip()
    user = main + (
        "\n\nSupplier-specific notes for " + own_spec.name + ":\n" + notes
        if notes
        else ""
    )
    parsed, _ = call_llm(
        system_prompt=system_prompt,
        user_prompt=user,
        db=db,
        call_type="extraction",
        max_tokens=4096,
        documents=[
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": sample.content_type or "application/pdf",
                    "data": base64.b64encode(sample.pdf_bytes).decode(),
                },
            }
        ],
    )
    if not isinstance(parsed, dict) or parsed.get("error"):
        raise RuntimeError(
            str(parsed.get("error") if isinstance(parsed, dict) else "bad extraction")
        )
    return parsed


# ---------------------------------------------------------------------------
# The analysis agent — study a misread invoice, draft the spec update
# ---------------------------------------------------------------------------

# The doctrine the human loop converged on — the agent must draft specs that
# obey it, not re-litigate it.
_DOCTRINE = (
    "House rules for extraction prompts:\n"
    "- unit_of_measure stays AS PRINTED on the document — never multiplied "
    "out or converted ('330ml 4x6' -> '4x6 pack', NOT '24 pack' unless the "
    "document literally prints 24; '5X3KG' -> '5x3kg'). Ratios are computed "
    "elsewhere when units are created.\n"
    "- A unit must be a weight, volume or count — never a bare packaging "
    "word (Case, CTN, pkt) or a length. When a unit column only prints a "
    "packaging word, the size comes from the description or pack columns.\n"
    "- When individual inner items were delivered (an Ea/inner column, a "
    "unit word like TUB), the unit is the INNER size; whole cases keep the "
    "pack notation.\n"
    "- quantity x unit_price_ex_tax must equal line_total_ex_tax.\n"
    "- Random weight billed per kg -> 'Kilo'.\n"
    "- If no confident unit can be derived, null; if size info is present "
    "but unreadable, null + unit_unrecognisable true. Never guess.\n"
    "- SUPPLIER specs carry THIS supplier's layout quirks (column meanings, "
    "number formats) — keep them short, concrete, example-driven. Generic "
    "rules belong in the main prompt and must NOT be duplicated into specs.\n"
    "- One layout, one spec: suppliers in the same brand family often print "
    "IDENTICAL documents under different Loaded names (e.g. 'Bidfood' vs "
    "'Bidvest Food Service'). If an existing spec already covers this same "
    "printed layout, the fix is an ALIAS on that spec (alias_of) — never a "
    "near-duplicate spec to maintain twice.\n"
)


def _analysis_context(
    db: Session, config_db: Session, spec: SupplierInvoiceSpec, sample
) -> str:
    """The reference pack the one-shot extractor never gets: current prompts,
    the current extraction + its diffs, the Loaded draft's own structured
    lines for the same paper, and any existing baseline."""
    parts: list[str] = []
    main_text = compose_instructions(config_db, spec)
    parts.append(
        "CURRENT COMPOSED EXTRACTION PROMPT (main + this supplier's spec):\n"
        + main_text
    )
    parts.append(
        "CURRENT SUPPLIER SPEC TEXT (empty means none yet):\n"
        + ((spec.instructions or "").strip() or "(empty)")
    )
    # The full spec roster: lets the agent recognise that this supplier's
    # document is an existing spec's layout under a different Loaded name —
    # the alias_of path — instead of writing a near-duplicate spec.
    others = [
        {
            "name": r.name,
            "aliases": r.aliases or [],
            "instructions": (r.instructions or "").strip(),
        }
        for r in config_db.query(SupplierInvoiceSpec).all()
        if r.id != spec.id and r.name != MAIN_PROMPT_NAME and r.enabled
    ]
    if others:
        parts.append(
            "EXISTING SUPPLIER SPECS (the full roster — check FIRST whether "
            "one already describes this same printed layout; same-brand "
            "suppliers appear under different Loaded names. If so, answer "
            "with alias_of that spec instead of a new spec text):\n"
            + json.dumps(others, indent=1)
        )
    run = sample.last_run or {}
    if run.get("extraction"):
        parts.append(
            "CURRENT EXTRACTION RESULT under the prompt above:\n"
            + json.dumps(run["extraction"], indent=1)
        )
    if sample.expected is not None:
        parts.append(
            "ADMIN-ACCEPTED BASELINE for this invoice:\n"
            + json.dumps(sample.expected, indent=1)
        )
        if run.get("diffs"):
            parts.append(
                "DIFFS of the current extraction vs that baseline:\n"
                + json.dumps(run["diffs"], indent=1)
            )
    # The Loaded draft: the supplier feed's own structured reading of the same
    # invoice — units/qty/costs as entered by the supplier's system.
    if sample.source_venue_id and sample.source_invoice_id:
        try:
            from app.services.received_invoice import LoadedInvoiceClient

            lh = LoadedInvoiceClient(db, config_db, sample.source_venue_id)
            det = lh.invoice(sample.source_invoice_id)
            slim = {
                "supplierName": det.get("supplierName"),
                "referenceNumber": det.get("referenceNumber"),
                "subtotal": det.get("subtotal"),
                "taxAmount": det.get("taxAmount"),
                "total": det.get("total"),
                "lines": [
                    {
                        "code": ln.get("code"),
                        "description": ln.get("description"),
                        "unit": ln.get("unit"),
                        "quantityReceived": ln.get("quantityReceived"),
                        "unitCostExclTax": ln.get(
                            "unitCostExclTax", ln.get("unitCost")
                        ),
                        "totalCostExclTax": ln.get("totalCostExclTax"),
                    }
                    for ln in det.get("lines") or []
                ],
            }
            parts.append(
                "LOADED DRAFT for the SAME invoice (the supplier feed's own "
                "structured reading — an independent reference; its units can "
                "themselves be wrong, but agreement is strong evidence):\n"
                + json.dumps(slim, indent=1)
            )
        except Exception as exc:  # noqa: BLE001 — reference data is optional
            logger.warning("analysis: Loaded draft fetch failed: %s", exc)
    return "\n\n".join(parts)


_ANALYSIS_SCHEMA = {
    "rationale": (
        "string — what the current extraction gets wrong on THIS document and "
        "why (name the layout facts: which columns mean what)"
    ),
    "layout_facts": "array of short strings — durable facts about this supplier's invoice layout",
    "ground_truth": (
        "object — the CORRECT full extraction for this document, in exactly "
        "the extraction schema shape (document_type, invoice_number, lines "
        "with quantity/unit_of_measure/unit_price_ex_tax/line_total_ex_tax, "
        "totals). This becomes the regression baseline."
    ),
    "proposed_instructions": (
        "string — the COMPLETE replacement supplier-spec text (not a diff). "
        "Short, concrete, example-driven; layout quirks only — never repeat "
        "generic rules from the main prompt. Empty string if the current "
        "spec already suffices and only the baseline was wrong. When "
        "alias_of is set this text applies to THAT spec (empty = keep its "
        "text unchanged)."
    ),
    "alias_of": (
        "string or null — if one of the EXISTING SUPPLIER SPECS already "
        "covers this exact printed layout (same brand family / same document "
        "template), give ITS exact name: the durable fix is an alias on that "
        "spec, not a duplicate spec. null when this supplier's layout is "
        "genuinely its own."
    ),
}


def analyse_sample(
    db: Session,
    config_db: Session,
    sample_id: str,
    feedback: str | None = None,
) -> dict:
    """The capable-agent loop: context → analysis → candidate verification →
    one refinement → stored proposal. Mutates sample.analysis; returns it.

    Green (this sample's candidate extraction matches the agent's ground
    truth AND every sibling baseline still passes) ⇒ status 'ready' — a
    proposal awaiting admin approval. Anything else ⇒ 'not_green' with the
    failures attached. Config is never written here.

    ``feedback`` is the admin replying to the proposal thread: their
    correction is appended to the running ``thread``, shown to the agent as
    AUTHORITATIVE alongside its previous proposal, and the whole loop
    (re-analysis → candidate verification) runs again — so a wrong unit in a
    proposal is corrected conversationally, and the correction is re-tested,
    never just taken on trust.
    """
    import datetime as _dt

    from app.config import settings
    from app.db.config_models import SupplierSpecSample
    from app.interpreter.llm_interpreter import call_llm

    sample = (
        config_db.query(SupplierSpecSample)
        .filter(SupplierSpecSample.id == sample_id)
        .first()
    )
    if not sample:
        raise RuntimeError("sample not found")
    spec = (
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.id == sample.spec_id)
        .first()
    )
    if not spec:
        raise RuntimeError("spec not found")

    def _store(analysis: dict) -> dict:
        sample.analysis = analysis
        config_db.commit()
        return analysis

    # The conversation so far: prior admin corrections + the proposal they
    # were reviewing. A new feedback message joins the thread; the whole
    # thread rides on every subsequent analysis (and re-analysis).
    prev = dict(sample.analysis or {})
    thread = list(prev.get("thread") or [])
    if str(feedback or "").strip():
        thread.append(
            {
                "role": "admin",
                "text": str(feedback).strip(),
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
        )

    _store(
        {
            "status": "running",
            "thread": thread,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
    )

    # Ensure there is a current-prompt extraction to critique.
    run = sample.last_run or {}
    if not run.get("extraction"):
        extraction = run_extraction(
            db, config_db, spec, sample.pdf_bytes, sample.content_type
        )
        diffs = (
            compare_extractions(sample.expected, extraction)
            if sample.expected is not None
            else []
        )
        sample.last_run = {"extraction": extraction, "diffs": diffs}
        sample.last_status = (
            "new" if sample.expected is None else ("pass" if not diffs else "fail")
        )
        sample.last_run_at = _dt.datetime.now(_dt.timezone.utc)
        config_db.commit()

    import base64

    documents = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": sample.content_type or "application/pdf",
                "data": base64.b64encode(sample.pdf_bytes).decode(),
            },
        }
    ]
    system = (
        "You are the invoice-extraction ANALYST for a hospitality stock "
        "system. A cheaper single-pass extractor reads supplier invoice PDFs "
        "under a composed prompt (main prompt + per-supplier spec). Your job: "
        "study THIS document against all the reference data, work out what "
        "the extractor gets wrong and why, and draft the supplier-spec text "
        "that fixes it durably.\n\n"
        + _DOCTRINE
        + "\nReturn ONLY a JSON object matching this schema:\n"
        + json.dumps(_ANALYSIS_SCHEMA, indent=1)
    )
    context = _analysis_context(db, config_db, spec, sample)
    # Admin corrections outrank the agent's own reading: show the previous
    # proposal being corrected + every admin message, on every ask.
    if thread:
        blocks: list[str] = []
        if prev.get("ground_truth") or prev.get("proposed_instructions"):
            blocks.append(
                "YOUR PREVIOUS PROPOSAL (the admin reviewed this):\n"
                + json.dumps(
                    {
                        k: prev.get(k)
                        for k in (
                            "rationale",
                            "layout_facts",
                            "proposed_instructions",
                            "alias_of",
                            "ground_truth",
                        )
                    },
                    indent=1,
                )
            )
        blocks.append(
            "ADMIN CORRECTIONS (authoritative — a human read this document; "
            "their corrections OVERRULE your own reading. Fold every one "
            "into the ground truth AND the spec text):\n"
            + "\n".join(
                "- " + str(m.get("text") or "")
                for m in thread
                if m.get("role") == "admin"
            )
        )
        context = context + "\n\n" + "\n\n".join(blocks)

    def _ask(extra: str = "") -> dict:
        parsed, _ = call_llm(
            system_prompt=system,
            user_prompt=context + (("\n\n" + extra) if extra else ""),
            model=settings.DOJO_ANALYSIS_MODEL,
            db=db,
            call_type="dojo_analysis",
            max_tokens=8192,
            documents=documents,
        )
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("ground_truth"), dict
        ):
            raise RuntimeError("analysis model returned an unusable answer")
        return parsed

    def _resolve_alias_target(name: object) -> SupplierInvoiceSpec | None:
        """The existing spec an alias_of proposal points at — exact name,
        case-insensitive; never the sample's own spec or the Main prompt."""
        t = str(name or "").strip().lower()
        if not t:
            return None
        for row in config_db.query(SupplierInvoiceSpec).all():
            if (
                row.name.lower() == t
                and row.id != spec.id
                and row.name != MAIN_PROMPT_NAME
            ):
                return row
        return None

    def _verify(proposal: dict) -> tuple[list[dict], dict]:
        """This sample's candidate extraction vs the agent's ground truth,
        plus every sibling vs its baseline. With alias_of, the candidate runs
        under the TARGET spec (its text unless new text is proposed) and that
        spec's samples are the siblings that must keep passing."""
        alias_name = str(proposal.get("alias_of") or "").strip()
        target = _resolve_alias_target(alias_name) if alias_name else None
        if alias_name and target is None:
            bad = [
                {
                    "field": "alias_of",
                    "line": None,
                    "description": None,
                    "expected": "the exact name of an EXISTING supplier spec",
                    "actual": alias_name,
                }
            ]
            return bad, {
                "own": {"status": "fail", "diffs": bad, "extraction": None},
                "siblings": {
                    "samples": [],
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "new": 0,
                },
            }
        host = target or spec
        text = str(proposal.get("proposed_instructions") or "")
        candidate_text = text if text.strip() else (host.instructions or "")
        own = run_extraction(
            db,
            config_db,
            host,
            sample.pdf_bytes,
            sample.content_type,
            override_instructions=candidate_text,
        )
        own_diffs = compare_extractions(proposal["ground_truth"], own)
        siblings = candidate_run(
            db, config_db, host, candidate_text, skip_sample_id=sample.id
        )
        if target is not None:
            # This spec's other samples move with the alias — they must hold
            # under the target's composed prompt too.
            extra = candidate_run(
                db, config_db, spec, candidate_text, skip_sample_id=sample.id
            )
            siblings = {
                "samples": siblings["samples"] + extra["samples"],
                "passed": siblings["passed"] + extra["passed"],
                "failed": siblings["failed"] + extra["failed"],
                "errors": siblings["errors"] + extra["errors"],
                "new": siblings["new"] + extra["new"],
            }
        return own_diffs, {
            "own": {
                "status": "pass" if not own_diffs else "fail",
                "diffs": own_diffs,
                "extraction": own,
            },
            "siblings": {
                "samples": [
                    {k: r.get(k) for k in ("id", "label", "status", "diffs")}
                    for r in siblings["samples"]
                ],
                "passed": siblings["passed"],
                "failed": siblings["failed"],
                "errors": siblings["errors"],
                "new": siblings["new"],
            },
        }

    try:
        proposal = _ask()
        own_diffs, results = _verify(proposal)
        if own_diffs or results["siblings"]["failed"] or results["siblings"]["errors"]:
            # ONE refinement round with the concrete failures as feedback —
            # the iteration the single-shot extractor never gets.
            feedback = (
                "YOUR PREVIOUS PROPOSAL WAS TESTED AND FAILED.\n"
                "Previous proposed_instructions:\n"
                + str(proposal.get("proposed_instructions") or "(unchanged)")
                + "\nExtraction under your proposal vs YOUR ground truth "
                "diffs:\n"
                + json.dumps(own_diffs, indent=1)
                + "\nSibling baseline results:\n"
                + json.dumps(results["siblings"], indent=1)
                + "\nRevise: fix the proposal (or your ground truth if IT was "
                "wrong) and return the full JSON again."
            )
            proposal = _ask(feedback)
            own_diffs, results = _verify(proposal)
        green = (
            not own_diffs
            and not results["siblings"]["failed"]
            and not results["siblings"]["errors"]
        )
        # A green proposal's ground truth becomes the sample's stored expected
        # values when the baseline is still AGENT-OWNED: empty, or exactly the
        # previous proposal's ground truth (i.e. auto-populated and untouched)
        # — a corrected re-analysis must be able to fix its own earlier wrong
        # values. A baseline an admin has edited is never overwritten.
        agent_owned = sample.expected is None or (
            prev.get("ground_truth") is not None
            and sample.expected == prev.get("ground_truth")
        )
        if green and agent_owned:
            sample.expected = proposal["ground_truth"]
            run_now = sample.last_run or {}
            if run_now.get("extraction"):
                diffs_now = compare_extractions(sample.expected, run_now["extraction"])
                sample.last_run = {**run_now, "diffs": diffs_now}
                sample.last_status = "pass" if not diffs_now else "fail"
        alias_target = _resolve_alias_target(proposal.get("alias_of"))
        return _store(
            {
                "status": "ready" if green else "not_green",
                "green": green,
                "rationale": proposal.get("rationale"),
                "layout_facts": proposal.get("layout_facts") or [],
                "proposed_instructions": str(
                    proposal.get("proposed_instructions") or ""
                ),
                # Canonical target name — Apply adds the alias there instead
                # of keeping a duplicate spec.
                "alias_of": alias_target.name if alias_target else None,
                "ground_truth": proposal["ground_truth"],
                "candidate_results": results,
                "thread": thread,
                "model": settings.DOJO_ANALYSIS_MODEL,
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001 — a failed analysis must record, not crash
        logger.warning("dojo analysis failed for sample %s: %s", sample_id, exc)
        return _store(
            {
                "status": "failed",
                "error": str(exc),
                "thread": thread,
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
        )
