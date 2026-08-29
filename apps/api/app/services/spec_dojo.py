"""Supplier Spec Dojo — run the CURRENT extraction prompts against stored
sample invoices and diff the result against an admin-accepted baseline.

Faithful replay of the production extraction: the schema, the main prompt and
the instruction composer are the SHARED ones in
``app.services.invoice_extraction`` (the same module the live review path
uses), so a dojo run exercises exactly what production runs.
Deliberately NO DocumentExtraction cache — every dojo run is a fresh read.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.db.config_models import SupplierInvoiceSpec
from app.services.roster_health import regressions, roster_issues
from app.services.supplier_identity import alias_conflict, norm
from app.services.invoice_extraction import (
    BUILTIN_MAIN_PROMPT,
    MAIN_PROMPT_NAME,
    PDF_SCHEMA,
    compose_pdf_instructions,
    extraction_system_prompt,
    find_spec_for_supplier,
    main_prompt,
)

__all__ = [
    "MAIN_PROMPT_NAME",
    "find_spec_for_supplier",
    "loaded_supplier_aliases",
]  # re-exported for callers

logger = logging.getLogger(__name__)


# An analysis runs either in a background daemon thread (the cannot-receive intake) or
# inline in a request (the sensei). Both die with the process, leaving the
# stored analysis at "running" forever with no error — the panel then shows
# "sensei analysing…" for eternity and offers no way back (Lion Nathan
# 94793550, stuck 4 hours across a restart, 10 Aug 2026).
#
# Nothing can revive that thread, so a run older than this is reported as
# interrupted and can simply be started again. Deliberately evaluated at READ
# time rather than swept at startup: with more than one API replica, a boot
# sweep would mark another replica's genuinely-running analysis as dead. A
# real run takes 35-120s, so this threshold only ever catches corpses.
STALE_ANALYSIS_MINUTES = 15

# The founding spec written when a study finds a brand-new supplier already reads
# correctly under the main prompt. Every studied supplier ends with a spec, so the
# auto-spec trigger's "has a spec?" check is the only state it needs (no study
# history). The text must add NO reading rules — it is composed verbatim into the
# extraction prompt (compose_instructions) — while telling an admin the supplier
# was reviewed.
NO_RULES_SPEC = (
    "Standard layout — no supplier-specific extraction rules are required. "
    "(Reviewed by Norm's dojo.)"
)


def analysis_view(analysis: dict | None) -> dict | None:
    """The analysis as a client should see it — never an eternal lie.

    Pure; the stored row is left alone (the worker owns state transitions):

    - ``running`` with a stale heartbeat → annotated ``stale: True`` (the UI
      shows "restarting…"; the worker requeues it within a tick).
    - ``queued`` that nobody claimed for QUEUED_ABANDONED_SECONDS → reported
      ``failed`` with a message naming the real problem (no worker/job alive
      for its environment), instead of spinning forever.
    - Legacy ``running`` entries with no heartbeat at all fall back to the
      old 15-minute rule.
    """
    from app.services.sensei_runner import (
        HEARTBEAT_STALE_SECONDS,
        QUEUED_ABANDONED_SECONDS,
        _age_seconds,
    )

    if not isinstance(analysis, dict):
        return analysis
    status = analysis.get("status")
    if status == "queued":
        age = _age_seconds(analysis.get("queued_at") or analysis.get("at"))
        if age is not None and age > QUEUED_ABANDONED_SECONDS:
            env = analysis.get("queued_env") or "unknown"
            return {
                **analysis,
                "status": "failed",
                "error": (
                    f"queued for {int(age // 60)} minutes and nothing picked "
                    f"it up — is the sensei worker/job for '{env}' running?"
                ),
            }
        return analysis
    if status != "running":
        return analysis
    hb = analysis.get("heartbeat_at")
    if hb is not None:
        age = _age_seconds(hb)
        if age is not None and age > HEARTBEAT_STALE_SECONDS:
            # The executor died; the worker will requeue it. Say so.
            return {**analysis, "stale": True}
        return analysis
    # Legacy running entry (pre-heartbeat): the old staleness rule.
    age = _age_seconds(analysis.get("at"))
    if age is None or age < STALE_ANALYSIS_MINUTES * 60:
        return analysis
    return {
        **analysis,
        "status": "failed",
        "error": (
            "the sensei was interrupted before it finished (the server "
            "restarted, most likely) — run it again"
        ),
    }


def dojo_schema(config_db: Session) -> dict:
    """The extraction schema (shared with the live path)."""
    return PDF_SCHEMA


def compose_instructions(
    config_db: Session,
    spec: SupplierInvoiceSpec,
    override_instructions: str | None = None,
) -> str:
    """Main prompt (admin row, else the built-in) + this spec's notes, wrapped
    exactly like the live path (the shared composer).

    ``override_instructions`` substitutes CANDIDATE text without touching
    stored config: the spec's notes for a supplier row, the main prompt itself
    when ``spec`` IS the reserved Main prompt row — the dojo's
    test-before-commit primitive.
    """
    is_main = spec.name == MAIN_PROMPT_NAME
    if is_main and override_instructions is not None:
        main = override_instructions.strip() or BUILTIN_MAIN_PROMPT
    else:
        main = main_prompt(config_db)
    notes = (spec.instructions or "").strip()
    if not is_main and override_instructions is not None:
        notes = override_instructions.strip()
    if notes and not is_main:
        return compose_pdf_instructions(
            config_db, spec_notes=notes, spec_name=spec.name, main_override=main
        )
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

    parsed, _ = call_llm(
        system_prompt=extraction_system_prompt(),
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
    # The buyer's PO — the one field the replica resolves a purchase order
    # from. (The legacy catch-all `purchase_order_number` was retired from
    # the extraction schema 17 Aug 2026; `supplier_order_number` is a decoy
    # nothing consumes, so neither is regression-tested here.)
    "customer_purchase_order_number",
    "subtotal_ex_tax",
    "discount_amount",
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


# find_spec_for_supplier lives in invoice_extraction (the live path composes
# supplier notes with it too) and is re-exported here for existing callers.


def loaded_supplier_aliases(lh, detail: dict) -> list[str]:
    """This account's other spellings for the invoice's supplier, best-effort.

    Loaded holds them per supplier record and the venue maintains them, so
    they are the authority on 'is this the same business'. Identity hints
    only — never fatal, and never written back into a global spec.
    """
    sid = detail.get("linkedSupplierId") or detail.get("supplierId")
    if not sid:
        return []
    try:
        rows = lh.get(f"/1.0/stock/internal/suppliers/{sid}/aliases")
        return [str(a["name"]) for a in rows if isinstance(a, dict) and a.get("name")]
    except Exception:  # noqa: BLE001 — hints only
        return []


def find_or_create_spec_for_supplier(
    config_db: Session, supplier_name: str, *also_known_as: object
) -> tuple[SupplierInvoiceSpec, bool]:
    """Match, else create an empty spec row for the supplier (Add-to-Dojo on
    a supplier with no spec yet). Returns (spec, created).

    ``also_known_as`` carries the account's other spellings for this supplier
    (its Loaded aliases). Passing them is what stops a second spec being born
    for a business that already has one: a duplicate row created HERE is the
    real source of near-duplicate specs, because the sensei only gets to
    propose ``alias_of`` after the row already exists.
    """
    spec = find_spec_for_supplier(config_db, supplier_name, *also_known_as)
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


def stage_invoice_sample(
    db: Session,
    venue_id: str,
    invoice_id: str,
    *,
    draft: bool,
    supplier_name: str | None = None,
) -> dict:
    """File a Loaded invoice as a dojo sample — the shared engine behind
    Add-to-Dojo (permanent) and the Dojo page's triage staging (draft).

    Resolves/creates the supplier's spec, fetches the invoice copy, and
    creates the ``SupplierSpecSample`` with its source ids. Idempotent on
    ``(spec_id, source_invoice_id)``: an existing sample is reused, and an
    existing DRAFT is promoted when ``draft=False`` is requested. Opens its
    own RW config session (request config sessions are read-only).

    ``supplier_name`` overrides Loaded's — pass the name PRINTED on the copy
    when the two name different businesses."""
    from app.db.config_models import SupplierSpecSample
    from app.db.engine import _ConfigSessionLocal
    from app.services.received_invoice import LoadedInvoiceClient

    wcdb = _ConfigSessionLocal()
    try:
        lh = LoadedInvoiceClient(db, wcdb, venue_id)
        det = lh.invoice(invoice_id)
        if not det.get("fileId"):
            raise RuntimeError("no invoice copy attached — nothing to add")
        # Loaded's name unless the caller knows better. It knows better in one
        # case: the copy is printed by a business Loaded has filed under some
        # OTHER supplier record, and the printed name is the one the roster
        # should answer for. Loaded's aliases are then withheld deliberately —
        # they are that other business's spellings, and offering them would
        # file this sample under its spec, which is the Eurovintage fault
        # arriving by a new road.
        loaded_name = det.get("supplierName") or ""
        supplier = str(supplier_name or loaded_name or "")
        hints = (
            loaded_supplier_aliases(lh, det)
            if norm(supplier) == norm(loaded_name)
            else []
        )
        spec, created = find_or_create_spec_for_supplier(wcdb, supplier, *hints)
        existing = (
            wcdb.query(SupplierSpecSample)
            .filter(
                SupplierSpecSample.spec_id == spec.id,
                SupplierSpecSample.source_invoice_id == invoice_id,
            )
            .first()
        )
        was_draft = bool(existing.draft) if existing else False
        if existing:
            if was_draft and not draft:
                existing.draft = False
                wcdb.commit()
            sample_id = existing.id
        else:
            import base64

            b64, ctype = lh.file_base64(det["fileId"])
            sample = SupplierSpecSample(
                spec_id=spec.id,
                label=f"{det.get('referenceNumber') or invoice_id}.pdf",
                content_type=ctype or "application/pdf",
                pdf_bytes=base64.b64decode(b64),
                source_venue_id=venue_id,
                source_invoice_id=invoice_id,
                # The env-independent key: venue ids differ per environment,
                # the Loaded company doesn't — any env can resolve its own
                # venue for this sample (see resolve_sample_venue_id).
                source_company_id=_venue_company_id(db, venue_id),
                draft=draft,
            )
            wcdb.add(sample)
            wcdb.commit()
            wcdb.refresh(sample)
            sample_id = sample.id
        return {
            "sample_id": sample_id,
            "spec_id": spec.id,
            "spec_name": spec.name,
            "created_spec": created,
            "already_in_dojo": bool(existing) and not was_draft,
            "was_draft": was_draft,
        }
    finally:
        wcdb.close()


def autostudy_if_spec_less(
    db: Session,
    config_db: Session,
    venue_id: str,
    invoice_id: str,
    review: dict,
) -> None:
    """Start a background dojo study when a reviewed invoice's supplier has no
    working spec yet — the Receive Invoice screen's auto-spec trigger.

    The screen calls this on both opening and Re-analysing an invoice. It is
    fail-open and side-effect only: it never raises and never blocks the
    screen, and it mutates ``review`` only to set the ``sensei_studying`` flag
    the card surfaces. The study itself is unchanged — the invoice is staged as
    a dojo sample and QUEUED on the durable sensei queue
    (``sensei_runner.enqueue``); the background worker (Cloud Run job when
    deployed) does the ~2-minute analysis and writes the spec, or records "no
    spec needed". This is the same machinery the "Norm can't do this one"
    button uses; here it starts automatically where it never started before.

    Study when the supplier has no content-bearing spec AND this invoice has not
    already been staged. Two skips, both needed:
    - a spec WITH content already covers the supplier — nothing to do;
    - this invoice already has a (non-draft) dojo sample — it has been studied,
      so don't study it again. This second guard is what stops a loop when a
      study files its spec under a CANONICAL name (e.g. the footer entity
      'Atomic Coffee Roasters') that the Loaded feed name ('ATOMIC') can't match
      here: the content check misses the orphaned spec, but the sample is still
      there. Deleting the spec cascades its samples, so "delete to re-study"
      still works.
    ``sensei_runner.enqueue`` refuses to queue a run already queued/running, and
    ``stage_invoice_sample`` is idempotent per invoice.
    """
    try:
        supplier = str((review or {}).get("supplier_name") or "").strip()
        if not supplier:
            return
        from app.db.config_models import SupplierSpecSample
        from app.services import sensei_runner

        spec = find_spec_for_supplier(config_db, supplier)
        if spec is not None and (spec.instructions or "").strip():
            return  # a spec with content already exists — nothing to study

        if (
            config_db.query(SupplierSpecSample.id)
            .filter(
                SupplierSpecSample.source_invoice_id == invoice_id,
                SupplierSpecSample.draft.isnot(True),
            )
            .first()
            is not None
        ):
            return  # this invoice was already studied — don't loop

        staged = stage_invoice_sample(db, venue_id, invoice_id, draft=False)
        sensei_runner.enqueue(staged["sample_id"])
        review["sensei_studying"] = True
        logger.info(
            "autostudy: queued dojo study for spec-less supplier %r (invoice %s)",
            supplier,
            invoice_id,
        )
    except Exception as exc:  # noqa: BLE001 — must never break the review/screen
        logger.info("autostudy skipped for invoice %s: %s", invoice_id, exc)


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
    means EVERY sample in the dojo. Only samples WITH a baseline run: a
    no-baseline sample (including the Dojo page's hidden drafts) can neither
    pass nor fail, so re-extracting it spends a full extraction (~6k tokens)
    to report "new" — noise that read as regression coverage in the proposal
    card while verifying nothing. ``skip_sample_id`` lets the analysis agent
    exclude the sample it is grading against its own ground truth.
    """
    from app.db.config_models import SupplierSpecSample

    q = config_db.query(SupplierSpecSample).filter(
        SupplierSpecSample.expected.isnot(None)
    )
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
            diffs = (
                compare_extractions(s.expected, extraction)
                if s.expected is not None
                else []
            )
            status = _sample_status(s.expected, diffs, extraction)
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

    main = (main_text or "").strip()
    notes = (own_spec.instructions or "").strip()
    user = main + (
        "\n\nSupplier-specific notes for " + own_spec.name + ":\n" + notes
        if notes
        else ""
    )
    parsed, _ = call_llm(
        system_prompt=extraction_system_prompt(),
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
    "near-duplicate spec to maintain twice. Prefer alias_of whenever it is "
    "defensible: a spec row is created automatically from whatever the "
    "account happened to type, so a near-duplicate is the DEFAULT failure, "
    "not a rare one. Two rows for one business means every future invoice is "
    "a coin toss.\n"
    "- A spec is named for a BUSINESS, not for one account's spelling. Specs "
    "are shared by every Norm venue, while each Loaded account types the "
    "supplier its own way ('SERVICE FOODS LTD', 'SERVICE FOODS - AUCKLAND "
    "FOODSERVICE'). Local spellings belong in Loaded, where the venue "
    "maintains them; the spec keeps ONE canonical name. If the spec you are "
    "analysing is named after a branch or a local spelling, propose the "
    "business name in canonical_name.\n"
    "- An alias must name the SAME BUSINESS as the spec carrying it. If the "
    "roster shows an alias that names a different business (a food "
    "distributor listed under a wine importer), report it in wrong_aliases. "
    "One such alias silently routes every one of that supplier's invoices "
    "through the wrong prompt.\n"
    "- No needless specs: FIRST compare your corrected values with the "
    "CURRENT EXTRACTION RESULT in the context. If the current prompts "
    "already read this document correctly, return EMPTY "
    "proposed_instructions — spec notes exist to fix misreads, never to "
    "describe layouts that already parse correctly. The corrected values "
    "alone become the regression baseline.\n"
    "- The document is the ONLY truth: every ground-truth value must be read "
    "off the paper itself. A line the copy marks 'Not available' / quantity "
    "0 stays quantity 0 with line total 0.\n"
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
    # DELIBERATELY NO Loaded draft here. Loaded has no supplier integration —
    # its lines are Loaded's OWN text-recognition of the same paper, a
    # competing (worse) OCR, not independent evidence. Including it
    # contaminated a ground truth once (pink ling, 09 Aug 2026: Loaded read
    # qty 0.5 where the copy printed 'Not available'/0, and the agent
    # followed it). The document is the only truth the sensei may read.
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
    "canonical_name": (
        "string or null — the BUSINESS's proper name, when the spec this "
        "sample sits on is named after a branch or one account's spelling "
        "('Trents Wholesale Limited Trents Dunedin Branch' -> 'Trents "
        "Wholesale'). Renames that spec. null when the name is already the "
        "business name. Never a local/branch spelling."
    ),
    "wrong_aliases": (
        "array of objects {spec, alias} — aliases in the roster that name a "
        "DIFFERENT business from the spec carrying them, and so route that "
        "supplier's invoices through the wrong prompt. Empty array when the "
        "roster is clean. Only report ones you are confident about: name the "
        "spec and the alias exactly as they appear."
    ),
}


def _clean_rename(config_db: Session, spec, proposed: object) -> str | None:
    """A canonical-name proposal, or None when it must not be acted on.

    Dropped when it names the spec already, when it collides with another
    spec's identity, or when the row is the reserved Main prompt (the engine
    finds that one by name). Validated at PROPOSE time so the card never
    offers a rename that Apply would refuse.
    """
    name = str(proposed or "").strip()
    if not name or spec.name == MAIN_PROMPT_NAME:
        return None
    if norm(name) == norm(spec.name):
        return None
    rows = config_db.query(SupplierInvoiceSpec).all()
    if alias_conflict(rows, name, spec_id=spec.id):
        return None
    return name


def _clean_wrong_aliases(config_db: Session, proposed: object) -> list[dict]:
    """Misfiled aliases the sensei reported, kept only where they really exist.

    An alias naming a different business from the spec carrying it routes
    every one of that supplier's invoices through the wrong prompt — the
    Eurovintage/Service Foods fault. The sensei can now see and report it, but
    only exact, currently-present (spec, alias) pairs survive to the card: the
    model must not be able to invent a deletion.
    """
    out: list[dict] = []
    if not isinstance(proposed, list):
        return out
    rows = config_db.query(SupplierInvoiceSpec).all()
    for item in proposed:
        if not isinstance(item, dict):
            continue
        want_spec, want_alias = norm(item.get("spec")), norm(item.get("alias"))
        if not want_spec or not want_alias:
            continue
        for row in rows:
            if norm(row.name) != want_spec or row.name == MAIN_PROMPT_NAME:
                continue
            match = next(
                (a for a in (row.aliases or []) if norm(a) == want_alias), None
            )
            if match is not None and not any(
                d["spec_id"] == row.id and d["alias"] == match for d in out
            ):
                out.append({"spec_id": row.id, "spec": row.name, "alias": match})
    return out


def _ground_truth_violations(gt: dict) -> list[str]:
    """The document's own arithmetic, enforced on the agent's ground truth.

    A wrong truth born from mis-reading (or from trusting anything but the
    paper) usually breaks the printed arithmetic — pink ling (09 Aug 2026):
    qty 0.5 × 21.75 = 10.88 against a printed line total of 0.00. Checks are
    skipped when an operand is missing; violations make a proposal not_green
    (and therefore never auto-applied)."""

    def _num(v: object) -> float | None:
        try:
            return float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    out: list[str] = []
    if not isinstance(gt, dict):
        return out
    line_sum = 0.0
    line_sum_complete = True
    for i, ln in enumerate(gt.get("lines") or []):
        if not isinstance(ln, dict):
            continue
        qty = _num(ln.get("quantity"))
        price = _num(ln.get("unit_price_ex_tax"))
        total = _num(ln.get("line_total_ex_tax"))
        if total is None:
            line_sum_complete = False
        else:
            line_sum += total
        if qty is not None and price is not None and total is not None:
            if abs(qty * price - total) > 0.011:
                out.append(
                    f"line {i + 1} '{ln.get('description')}': "
                    f"{qty} x {price} = {round(qty * price, 2)} but "
                    f"line_total_ex_tax is {total}"
                )
    subtotal = _num(gt.get("subtotal_ex_tax"))
    tax = _num(gt.get("tax_amount"))
    total_incl = _num(gt.get("total_incl_tax"))
    discount = _num(gt.get("discount_amount")) or 0.0
    if line_sum_complete and (gt.get("lines") or []) and subtotal is not None:
        if abs(line_sum - subtotal) > 0.02:
            out.append(
                f"line totals sum to {round(line_sum, 2)} but "
                f"subtotal_ex_tax is {subtotal}"
            )
    if subtotal is not None and tax is not None and total_incl is not None:
        # Mirror the receive-flow identity (invoice_replica): a document-level
        # discount is subtracted from the subtotal+tax to reach the total.
        expected_total = subtotal + tax - discount
        if abs(expected_total - total_incl) > 0.02:
            disc_note = f" - discount {discount}" if discount else ""
            out.append(
                f"subtotal {subtotal} + tax {tax}{disc_note} = "
                f"{round(expected_total, 2)} but total_incl_tax is {total_incl}"
            )
    return out


def _sample_status(expected: dict | None, diffs: list, extraction: dict | None) -> str:
    """The verdict for a dojo sample run.

    A sample passes only if it BOTH matches its stored baseline AND its own
    printed arithmetic reconciles. A non-reconciling extraction is never a clean
    pass — even when it equals a baseline that was itself captured from bad
    numbers (which is how a broken invoice used to slip through as "PASS").
    """
    if expected is None:
        return "new"
    if diffs:
        return "fail"
    if _ground_truth_violations(extraction if isinstance(extraction, dict) else {}):
        return "fail"
    return "pass"


def _roster_snapshot(config_db: Session) -> tuple[list, list]:
    """Detached copies of every spec and sample.

    Plain objects, not ORM rows: the gate compares the roster before and after
    a mutation, and ORM rows change underneath you — a "before" snapshot made
    of live rows would silently become the "after" one.
    """
    from types import SimpleNamespace

    from app.db.config_models import SupplierSpecSample

    specs = [
        SimpleNamespace(
            id=s.id,
            name=s.name,
            aliases=list(s.aliases or []),
            instructions=s.instructions or "",
            enabled=bool(s.enabled),
        )
        for s in config_db.query(SupplierInvoiceSpec).all()
    ]
    samples = [
        SimpleNamespace(
            id=s.id,
            spec_id=s.spec_id,
            label=s.label,
            expected=s.expected,
            last_run=s.last_run,
            analysis=s.analysis,
        )
        for s in config_db.query(SupplierSpecSample).all()
    ]
    return specs, samples


def apply_analysis_proposal(
    config_db: Session,
    sample,
    *,
    apply_spec: bool = True,
    save_expected: bool = True,
    db: Session | None = None,
) -> dict:
    """Apply a stored analysis proposal: write the spec text, baseline the
    agent's ground truth, and record the candidate extraction as the sample's
    last run (no re-extraction spend). An ``alias_of`` proposal merges instead
    of duplicating. Shared by the admin Apply endpoint and the self-training
    auto-apply. Raises ``ValueError`` for the not-applicable cases.

    ``db`` (the venue-env session) lets apply also REBUILD the sample's
    rendered invoice view from the extraction it records; without it the old
    view is carried forward.

    Every roster write in the system funnels through here, so this is where the
    gate lives: the roster is measured before, mutated, measured again, and the
    whole change is rolled back if it INTRODUCED an error-severity incoherence.
    That check is what would have refused 'Service Foods' landing on the
    Eurovintage spec on 10 Aug 2026 — before the write, not months after.
    """
    import datetime as _dt

    from app.db.config_models import SupplierSpecSample

    before = roster_issues(
        *_roster_snapshot(config_db), main_prompt_name=MAIN_PROMPT_NAME
    )
    # Every mutation below runs inside a SAVEPOINT so a refused change can be
    # undone without touching anything else pending in the caller's session.
    # (A plain rollback here would throw away work this function never made.)
    savepoint = config_db.begin_nested()
    analysis = sample.analysis or {}
    if analysis.get("status") not in ("ready", "not_green"):
        raise ValueError("no analysis proposal to apply — run Analyse first")
    spec = (
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.id == sample.spec_id)
        .first()
    )
    if not spec:
        raise ValueError("spec not found")
    proposed = str(analysis.get("proposed_instructions") or "")
    alias_name = str(analysis.get("alias_of") or "").strip()
    target = None
    if alias_name and norm(alias_name) != norm(spec.name):
        target = next(
            (
                r
                for r in config_db.query(SupplierInvoiceSpec).all()
                if r.name.lower() == alias_name.lower()
                and r.id != spec.id
                and r.name != MAIN_PROMPT_NAME
            ),
            None,
        )
        if target is None:
            raise ValueError(f"alias target spec '{alias_name}' no longer exists")
    # An alias_of naming the spec this sample ALREADY sits on is not an error
    # — it is the state a successful apply leaves behind. Re-applying (a
    # double click, or a retry after the move) used to raise "alias target
    # spec 'X' no longer exists", which is both alarming and untrue.
    host = target or spec
    if apply_spec:
        # Clean the roster BEFORE writing to it, so a rename or an alias move
        # can't collide with a stale entry that is itself being removed.
        for bad in analysis.get("wrong_aliases") or []:
            row = (
                config_db.query(SupplierInvoiceSpec)
                .filter(SupplierInvoiceSpec.id == bad.get("spec_id"))
                .first()
            )
            if row is not None:
                row.aliases = [
                    a for a in (row.aliases or []) if norm(a) != norm(bad.get("alias"))
                ]
        if target is not None:
            # The merge that created the fault this guard exists for: it used
            # to copy the source spec's name AND all of its aliases onto the
            # target, unvalidated. Two rules now. Only the source spec's NAME
            # moves — its aliases are other accounts' local spellings, which
            # belong in Loaded, not multiplied across a global row. And an
            # alias already claimed by a THIRD spec is refused outright, so a
            # single bad adjudication can no longer make every future invoice
            # for that business a coin toss.
            merged = list(target.aliases or [])
            cand = spec.name
            if (
                norm(cand) != norm(target.name)
                and all(norm(cand) != norm(a) for a in merged)
                and not alias_conflict(
                    [
                        r
                        for r in config_db.query(SupplierInvoiceSpec).all()
                        if r.id != spec.id
                    ],
                    cand,
                    spec_id=target.id,
                )
            ):
                merged.append(cand)
            target.aliases = merged
        rename = str(analysis.get("canonical_name") or "").strip()
        if rename and host.name != MAIN_PROMPT_NAME:
            if not alias_conflict(
                config_db.query(SupplierInvoiceSpec).all(), rename, spec_id=host.id
            ):
                host.name = rename
        if proposed.strip():
            host.instructions = proposed
    if target is not None:
        for row in (
            config_db.query(SupplierSpecSample)
            .filter(SupplierSpecSample.spec_id == spec.id)
            .all()
        ):
            row.spec_id = target.id
        # The auto-created row is now redundant — but never delete one that
        # carries its own instructions (an admin wrote those).
        if not (spec.instructions or "").strip():
            config_db.delete(spec)
    # Baseline the agent's ground truth ONLY when no expected values are
    # stored yet — an admin-corrected baseline outranks the agent's, and the
    # candidate is re-diffed against the STORED baseline so the pass/fail
    # chip reflects the admin's values, never the agent's say-so.
    if (
        save_expected
        and isinstance(analysis.get("ground_truth"), dict)
        and not sample.expected
    ):
        sample.expected = analysis["ground_truth"]
    own = (analysis.get("candidate_results") or {}).get("own") or {}
    if isinstance(own.get("extraction"), dict) and sample.expected:
        diffs = compare_extractions(sample.expected, own["extraction"])
        sample.last_run = {
            **(sample.last_run or {}),
            "extraction": own["extraction"],
            "diffs": diffs,
        }
        sample.last_status = _sample_status(sample.expected, diffs, own["extraction"])
        sample.last_run_at = _dt.datetime.now(_dt.timezone.utc)
    sample.analysis = dict(analysis, status="applied")

    # The gate. Measured against the session's PENDING state (autoflush makes
    # the mutations above visible to these queries), so a refusal costs a
    # rollback and nothing reaches the database. Only error-severity findings
    # block: a repair that leaves a spec temporarily sample-less raises a
    # hygiene warning, and refusing that would trap the roster in its broken
    # state — the gate asks "does this make it worse", never "is it perfect".
    blocked = regressions(
        before,
        roster_issues(*_roster_snapshot(config_db), main_prompt_name=MAIN_PROMPT_NAME),
    )
    if blocked:
        savepoint.rollback()
        raise ValueError(
            "this change would break the spec roster — "
            + "; ".join(f"{i.where}: {i.problem}" for i in blocked)
        )
    savepoint.commit()
    # The rendered invoice view must show the extraction just recorded —
    # carrying the pre-apply replica forward left the dojo's invoice sheet
    # showing a PO string no values tab contained (Federal Merchants 396152,
    # 19 Aug 2026). After the gate, so a refused apply spends nothing. A
    # rebuild that isn't possible (no venue session, hand-uploaded sample,
    # sample filed in another environment) keeps the old view.
    if db is not None and isinstance(own.get("extraction"), dict) and sample.expected:
        try:
            replica, replica_diffs, replica_compare = replica_stage(
                db, config_db, sample, own["extraction"]
            )
            if isinstance(replica, dict) and not replica.get("error"):
                sample.last_run = {
                    **(sample.last_run or {}),
                    "replica": replica,
                    "replica_diffs": replica_diffs,
                    "replica_compare": replica_compare,
                }
        except Exception as exc:  # noqa: BLE001 — the view must never block an apply
            logger.warning("replica rebuild on apply failed for %s: %s", sample.id, exc)
    config_db.commit()
    config_db.refresh(sample)
    return {
        "spec_instructions": host.instructions,
        "alias_added_to": target.name if target is not None else None,
    }


def _heal_source_review(db: Session, config_db: Session, sample) -> None:
    """Follow-through on a self-training auto-apply: re-run the SOURCE
    invoice's review so the card that raised the sample reflects the fix.

    The sensei taught itself Federal Merchants one minute after the review
    blocked (396152, 19 Aug 2026) — and the card stayed blocked until a human
    pressed Re-analyse, because nothing re-reviewed a draft after its prompt
    was fixed. Best-effort: a failure leaves the card exactly as it was, and
    the Re-analyse button still works.
    """
    if not (sample.source_venue_id and sample.source_invoice_id):
        return
    try:
        from app.routers.invoice_fixes import heal_review

        venue_id = resolve_sample_venue_id(db, sample)
        if venue_id is None:
            return
        heal_review(db, config_db, venue_id, str(sample.source_invoice_id))
    except Exception as exc:  # noqa: BLE001 — healing must never fail the analysis
        logger.warning(
            "post-apply review heal failed for sample %s: %s", sample.id, exc
        )


def _clear_studying_flag(db: Session, sample) -> None:
    """Take the source invoice's card OUT of the autostudy 'studying' state when
    the study is done but applied no spec (an existing spec, a not-green
    proposal, a failure) — without a full re-review. The card keeps its current
    review; the flag just turns off so it stops showing 'studying'. Auto-apply
    (or an existing spec) uses ``_heal_source_review`` instead. Best-effort."""
    if not (sample.source_venue_id and sample.source_invoice_id):
        return
    try:
        from app.routers.invoice_fixes import clear_studying_on_drafts

        venue_id = resolve_sample_venue_id(db, sample)
        if venue_id is None:
            return
        clear_studying_on_drafts(db, venue_id, str(sample.source_invoice_id))
    except Exception as exc:  # noqa: BLE001 — must never fail the analysis
        logger.warning("clear studying flag failed for sample %s: %s", sample.id, exc)


def analyse_sample(
    db: Session,
    config_db: Session,
    sample_id: str,
    feedback: str | None = None,
) -> dict:
    """The SENSEI loop: context → analysis → candidate verification →
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

    def _beat(phase: str) -> None:
        """Heartbeat: prove the executor is alive and say what it's doing.

        The worker requeues a running analysis whose heartbeat goes stale
        (HEARTBEAT_STALE_SECONDS), so every stretch of work in this function
        must be preceded by a beat — a phase is at most one model call."""
        cur = dict(sample.analysis or {})
        if cur.get("status") == "running":
            cur["heartbeat_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            cur["phase"] = phase
            _store(cur)

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
            "heartbeat_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "phase": "starting",
            # Queue bookkeeping survives into the running record so a death
            # here is requeued with its history intact (worker requeue logic
            # reads attempts; the view shows the claimant).
            "attempts": int(prev.get("attempts") or 0),
            "queued_env": prev.get("queued_env"),
            "claimed_by": prev.get("claimed_by"),
        }
    )

    # Ensure there is a current-prompt extraction to critique. This is often
    # the sample's FIRST stored run (cannot-receive kicks analyse in the
    # background), so it must carry the replica keys like every other run —
    # a run without them renders as "no invoice view" in the panel.
    _beat("reading the invoice")
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
        replica, replica_diffs, replica_compare = replica_stage(
            db, config_db, sample, extraction
        )
        sample.last_run = {
            "extraction": extraction,
            "diffs": diffs,
            "replica": replica,
            "replica_diffs": replica_diffs,
            "replica_compare": replica_compare,
            "reconcile_violations": _ground_truth_violations(extraction),
        }
        sample.last_status = _sample_status(sample.expected, diffs, extraction)
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
                # Keep the candidate extraction per sibling: it is the answer
                # to "WHY did this sibling fail" in the proposal card (each
                # row expands into baseline-vs-candidate). It was stripped
                # here until Aug 2026, which left the card showing a FAIL
                # verdict with no way to inspect the failing values.
                "samples": [
                    {
                        k: r.get(k)
                        for k in ("id", "label", "status", "diffs", "extraction")
                    }
                    for r in siblings["samples"]
                ],
                "passed": siblings["passed"],
                "failed": siblings["failed"],
                "errors": siblings["errors"],
                "new": siblings["new"],
            },
        }

    try:
        _beat("asking the model")
        proposal = _ask()
        _beat("verifying against the baselines")
        own_diffs, results = _verify(proposal)
        gt_violations = _ground_truth_violations(proposal["ground_truth"])
        if (
            own_diffs
            or results["siblings"]["failed"]
            or results["siblings"]["errors"]
            or gt_violations
        ):
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
                + (
                    "\nYOUR GROUND TRUTH VIOLATES THE DOCUMENT'S OWN "
                    "ARITHMETIC (re-read those lines off the paper — a "
                    "'Not available'/0-quantity line has line total 0):\n"
                    + "\n".join("- " + v for v in gt_violations)
                    if gt_violations
                    else ""
                )
                + "\nRevise: fix the proposal (or your ground truth if IT was "
                "wrong) and return the full JSON again."
            )
            _beat("re-asking after verification failed")
            proposal = _ask(feedback)
            _beat("re-verifying the revised proposal")
            own_diffs, results = _verify(proposal)
            gt_violations = _ground_truth_violations(proposal["ground_truth"])
        green = (
            not own_diffs
            and not results["siblings"]["failed"]
            and not results["siblings"]["errors"]
            # An arithmetically impossible truth can never be green — and
            # therefore can never baseline itself or auto-apply.
            and not gt_violations
        )
        # No needless specs (deterministic belt for the doctrine rule): when
        # the CURRENT prompts already read this document correctly, the
        # proposal is values-only — spec text would be churn to maintain.
        cur_run = sample.last_run or {}
        current_ok = bool(cur_run.get("extraction")) and not compare_extractions(
            proposal["ground_truth"], cur_run["extraction"]
        )
        spec_not_needed = bool(
            current_ok and not str(proposal.get("alias_of") or "").strip()
        )
        if spec_not_needed:
            # Leave every supplier with a spec so they all go through one path.
            # A brand-new (empty) holder gets a standard "no rules needed" note
            # that auto-applies below like any spec (inert to the extractor,
            # visible as reviewed to an admin); an existing real spec is left
            # untouched — no note churned over its rules.
            proposal["proposed_instructions"] = (
                NO_RULES_SPEC if not (spec.instructions or "").strip() else ""
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
                sample.last_status = _sample_status(
                    sample.expected, diffs_now, run_now["extraction"]
                )
        alias_target = _resolve_alias_target(proposal.get("alias_of"))
        proposed_text = str(proposal.get("proposed_instructions") or "")
        stored = _store(
            {
                "status": "ready" if green else "not_green",
                "green": green,
                "rationale": proposal.get("rationale"),
                "layout_facts": proposal.get("layout_facts") or [],
                "proposed_instructions": proposed_text,
                # Canonical target name — Apply adds the alias there instead
                # of keeping a duplicate spec.
                "alias_of": alias_target.name if alias_target else None,
                "canonical_name": _clean_rename(
                    config_db, spec, proposal.get("canonical_name")
                ),
                "wrong_aliases": _clean_wrong_aliases(
                    config_db, proposal.get("wrong_aliases")
                ),
                "spec_not_needed": spec_not_needed,
                "ground_truth_violations": gt_violations,
                "ground_truth": proposal["ground_truth"],
                "candidate_results": results,
                "thread": thread,
                "model": settings.DOJO_ANALYSIS_MODEL,
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
        )
        # Self-training v1 — auto-apply ONLY the provably-safe case: green,
        # no alias (never touch another spec's prompt), real text to write,
        # and the target spec has NO existing prompt (a brand-new supplier,
        # so there is nothing the change could break).
        if (
            green
            and alias_target is None
            and proposed_text.strip()
            and not (spec.instructions or "").strip()
        ):
            try:
                apply_analysis_proposal(
                    config_db, sample, apply_spec=True, save_expected=True, db=db
                )
                stored = _store(
                    dict(
                        sample.analysis or stored,
                        auto_applied=True,
                        applied_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — proposal stays reviewable
                logger.warning("auto-apply failed for sample %s: %s", sample.id, exc)
        # The study is done: take the source card out of the autostudy 'studying'
        # state whatever the outcome. If the supplier now has a spec (applied
        # just now, or one that already existed), re-review the invoice under it
        # so the card heals to the spec-based read; otherwise just drop the flag
        # and the card keeps its main-prompt review. A study that never
        # auto-applied (existing spec, not-green, failure) used to leave the card
        # stuck on 'studying'.
        try:
            if (spec.instructions or "").strip():
                _heal_source_review(db, config_db, sample)
            else:
                _clear_studying_flag(db, sample)
        except Exception as exc:  # noqa: BLE001 — card update must never fail analysis
            logger.warning(
                "post-analysis card update failed for %s: %s", sample.id, exc
            )
        return stored
    except Exception as exc:  # noqa: BLE001 — a failed analysis must record, not crash
        logger.warning("dojo analysis failed for sample %s: %s", sample_id, exc)
        try:
            _clear_studying_flag(db, sample)  # a failed study must still un-stick the card
        except Exception:  # noqa: BLE001 — sample may be unbound; ignore
            pass
        return _store(
            {
                "status": "failed",
                "error": str(exc),
                "thread": thread,
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
        )


# ---------------------------------------------------------------------------
# Replica: OUR extraction resolved into a full working document, scored
# against what Loaded actually resolved for the same invoice.
# ---------------------------------------------------------------------------

_REPLICA_HEADER_FIELDS = (
    "linked_supplier_id",
    "linked_purchase_order_id",
    "issued_at",
    "subtotal",
    "tax_amount",
    "total",
)


# Header fields compared by calendar day: Loaded returns '2026-08-07' on some
# venues and a full ISO datetime on others, the extraction always a bare date.
def prefetch_replica_reference(db: Session, config_db: Session, venue_id: str) -> dict:
    """One venue's replica reference data, fetched ONCE for a batch run —
    exactly the kwargs ``build_replica`` would otherwise fetch per sample
    (catalogue + units + suppliers + tax + the 400-day received feed).
    Best-effort: anything that fails is simply omitted and the builder
    self-fetches it per sample as before."""
    out: dict = {}
    try:
        from app.services.invoice_replica import _received_feed, sales_tax_rates
        from app.services.item_match import _fetch_raw_stock_items
        from app.services.received_invoice import LoadedInvoiceClient

        lh = LoadedInvoiceClient(db, config_db, venue_id)
        units = lh.get("/1.0/stock/internal/units")
        suppliers = lh.get("/1.0/stock/internal/suppliers")
        out = {
            "catalogue": _fetch_raw_stock_items(venue_id, db, config_db),
            "units": units if isinstance(units, list) else [],
            "suppliers": suppliers if isinstance(suppliers, list) else [],
            "tax_rates": sales_tax_rates(lh),
            "received_feed": _received_feed(lh),
        }
    except Exception as exc:  # noqa: BLE001 — prefetch is an optimization only
        logger.info("replica reference prefetch failed for %s: %s", venue_id, exc)
    return {k: v for k, v in out.items() if v}


def _config_company_id(cfg) -> str | None:
    """A connector row's Loaded company id — the configured
    ``x_loaded_company_id`` when present, else the company the token was
    actually minted for (``oauth_metadata.venue_id``, Loaded's name for the
    company in its token response). Rows connected before the company-id
    config existed carry only the latter."""
    if cfg is None:
        return None
    configured = str((cfg.config or {}).get("x_loaded_company_id") or "").strip()
    if configured:
        return configured
    minted = str((cfg.oauth_metadata or {}).get("venue_id") or "").strip()
    return minted or None


def _venue_company_id(db: Session, venue_id: str) -> str | None:
    """The Loaded company id a venue's connection is bound to (main DB)."""
    from app.db.models import ConnectorConfig

    cfg = (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == "loadedhub",
            ConnectorConfig.venue_id == venue_id,
        )
        .first()
    )
    return _config_company_id(cfg)


def resolve_sample_venue_id(db: Session, sample) -> str | None:
    """The venue to use for this sample's Loaded calls IN THIS ENVIRONMENT.

    Venue ids are per-environment (the shared config DB stores samples, but
    venues live in each env's main DB), so a sample filed in production
    carries a venue id local dev has never heard of. The Loaded COMPANY id
    is the env-independent key: prefer the sample's own venue id when this
    environment knows it, else find whichever of this environment's venues
    is connected to the same Loaded company. None = this environment has no
    venue talking to that company.
    """
    from app.db.models import ConnectorConfig, Venue

    vid = sample.source_venue_id
    if vid and db.query(Venue.id).filter(Venue.id == vid).first() is not None:
        return vid
    company = str(getattr(sample, "source_company_id", None) or "").strip()
    if not company:
        return None
    for cfg in (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == "loadedhub",
            ConnectorConfig.venue_id.isnot(None),
            ConnectorConfig.enabled == "true",
        )
        .all()
    ):
        if _config_company_id(cfg) == company:
            return cfg.venue_id
    return None


def replica_stage(
    db: Session,
    config_db: Session,
    sample,
    extraction: dict,
    reference: dict | None = None,
) -> tuple[dict | None, list[dict], dict | None]:
    """Build the replica for one dojo sample — returns
    (replica, [], display-ready view rows).

    The replica is our extraction resolved against the venue's Loaded
    CATALOGUE (items, units, suppliers, tax, PO references). It is no longer
    scored against Loaded's own read of the same invoice (removed 16 Aug
    2026: that read is competing OCR of the same paper, not a reference), so
    the middle element of the tuple — the old vs-Loaded scorecard — is
    always empty; kept in the shape so stored runs from the compare era and
    their consumers keep working.

    Requires the sample's source venue (cannot-receive intake); hand-uploaded
    samples return (None, [], None) — the panel explains why. Best-effort: an
    exception records an error replica rather than failing the run.
    ``reference`` is a venue's prefetched build_replica kwargs (batch runs).
    """
    if not (sample.source_venue_id and sample.source_invoice_id):
        return None, [], None
    if not isinstance(extraction, dict):
        return None, [], None
    venue_id = resolve_sample_venue_id(db, sample)
    if venue_id is None:
        return (
            {
                "replica": True,
                "error": (
                    "this sample was filed in another environment and no "
                    "venue here is connected to the same Loaded company — "
                    "the invoice view needs a live connection to resolve "
                    "against the catalogue"
                ),
                "lines": [],
            },
            [],
            None,
        )
    try:
        from app.services.invoice_replica import build_replica
        from app.services.received_invoice import LoadedInvoiceClient

        lh = LoadedInvoiceClient(db, config_db, venue_id)
        replica = build_replica(
            db,
            config_db,
            venue_id,
            extraction,
            lh=lh,
            own_invoice_id=sample.source_invoice_id,
            **(reference or {}),
        )
        return replica, [], replica_view_rows(replica)
    except Exception as exc:  # noqa: BLE001 — the replica must never break a run
        logger.warning("replica stage failed for sample %s: %s", sample.id, exc)
        return {"replica": True, "error": str(exc), "lines": []}, [], None


_COMPARE_LINE_KEYS = (
    "code",
    "description",
    "item_name",
    "linked_item_id",
    "unit",
    "linked_unit_id",
    "quantity_received",
    "unit_cost",
    "total_cost",
    "sale_tax_rate",
    "matched_by",
)


def replica_view_rows(replica: dict) -> dict:
    """Display rows for the dojo's invoice view, from the replica ALONE.

    Same shape ``replica_compare_rows`` produced back when Loaded's own read
    of the invoice was fetched for comparison (removed 16 Aug 2026: Loaded's
    read is competing OCR of the same paper, not a reference — the replica is
    primary). The ``loaded`` side is None and nothing differs, so the
    extracted-only invoice sheet renders unchanged and stored runs from the
    compare era keep rendering too.
    """

    def _slim(ln: dict | None) -> dict | None:
        if not isinstance(ln, dict):
            return None
        return {k: ln.get(k) for k in _COMPARE_LINE_KEYS}

    def _row(field: str) -> dict:
        return {
            "field": field,
            "replica": replica.get(field),
            "loaded": None,
            "differs": False,
        }

    header = [_row("supplier_name"), _row("reference_number")]
    for f in _REPLICA_HEADER_FIELDS:
        header.append(_row(f))
        if f == "linked_purchase_order_id":
            header.append(_row("purchase_order_number"))
    lines = [
        {"replica": _slim(rl), "loaded": None, "diff_fields": []}
        for rl in replica.get("lines") or []
        if isinstance(rl, dict)
    ]
    return {"header": header, "lines": lines}
