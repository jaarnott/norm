"""Supplier Spec Dojo endpoints — sample invoices + regression runs.

Admin uploads sample invoice PDFs per supplier spec; each run re-extracts them
with the CURRENT prompts (main + spec) and diffs against the admin-accepted
baseline, so prompt edits get regression-tested from Settings → Supplier Specs
before they misread a real invoice. All admin-only; samples live in the shared
config DB (one suite, site-wide) — see services/spec_dojo.py for the runner.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.config_models import SupplierInvoiceSpec, SupplierSpecSample
from app.db.engine import get_config_db_rw, get_db
from app.db.models import User
from app.services import spec_dojo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/supplier-invoice-specs", tags=["supplier-spec-dojo"])

_MAX_PDF_BYTES = 5 * 1024 * 1024


def _sample_meta(s: SupplierSpecSample) -> dict:
    return {
        "id": s.id,
        "spec_id": s.spec_id,
        "label": s.label,
        "content_type": s.content_type,
        "has_expected": s.expected is not None,
        "last_status": s.last_status,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "diff_count": len((s.last_run or {}).get("diffs") or []),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "source_invoice_id": s.source_invoice_id,
        # The analysis agent's state: running | ready | not_green | failed |
        # applied | None — drives the panel's proposal UI + polling.
        "analysis_status": (s.analysis or {}).get("status"),
        "analysis_green": bool((s.analysis or {}).get("green")),
    }


def _spec_or_404(config_db: Session, spec_id: str) -> SupplierInvoiceSpec:
    spec = (
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.id == spec_id)
        .first()
    )
    if not spec:
        raise HTTPException(404, "spec not found")
    return spec


def _sample_or_404(config_db: Session, sample_id: str) -> SupplierSpecSample:
    sample = (
        config_db.query(SupplierSpecSample)
        .filter(SupplierSpecSample.id == sample_id)
        .first()
    )
    if not sample:
        raise HTTPException(404, "sample not found")
    return sample


@router.post("/{spec_id}/samples", status_code=201)
async def upload_sample(
    spec_id: str,
    file: UploadFile = File(...),
    label: str = Form(""),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    _spec_or_404(config_db, spec_id)
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty file")
    if len(content) > _MAX_PDF_BYTES:
        raise HTTPException(400, "file too large (max 5MB)")
    ctype = file.content_type or "application/pdf"
    if "pdf" not in ctype and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF invoices are supported")
    sample = SupplierSpecSample(
        spec_id=spec_id,
        label=(label or file.filename or "sample").strip(),
        content_type="application/pdf",
        pdf_bytes=content,
    )
    config_db.add(sample)
    config_db.commit()
    config_db.refresh(sample)
    return _sample_meta(sample)


@router.get("/{spec_id}/samples")
async def list_samples(
    spec_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    samples = (
        config_db.query(SupplierSpecSample)
        .filter(SupplierSpecSample.spec_id == spec_id)
        .order_by(SupplierSpecSample.created_at)
        .all()
    )
    return {"samples": [_sample_meta(s) for s in samples]}


@router.delete("/samples/{sample_id}")
async def delete_sample(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    config_db.delete(sample)
    config_db.commit()
    return {"deleted": True}


@router.get("/samples/{sample_id}/pdf")
async def sample_pdf(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    return Response(
        content=sample.pdf_bytes,
        media_type=sample.content_type or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{sample.label or "sample"}.pdf"',
            "Cache-Control": "no-store",
        },
    )


def _run_and_store(db: Session, config_db: Session, sample: SupplierSpecSample) -> dict:
    """Run one sample, persist last_run/status, return the run view."""
    spec = _spec_or_404(config_db, sample.spec_id)
    try:
        extraction = spec_dojo.run_extraction(
            db, config_db, spec, sample.pdf_bytes, sample.content_type
        )
        diffs = (
            spec_dojo.compare_extractions(sample.expected, extraction)
            if sample.expected is not None
            else []
        )
        status = "new" if sample.expected is None else ("pass" if not diffs else "fail")
        sample.last_run = {"extraction": extraction, "diffs": diffs}
    except Exception as exc:  # noqa: BLE001 — a broken run must record, not 500
        logger.warning("dojo run failed for sample %s: %s", sample.id, exc)
        extraction, diffs, status = None, [], "error"
        sample.last_run = {"error": str(exc), "diffs": []}
    sample.last_status = status
    sample.last_run_at = datetime.now(timezone.utc)
    config_db.commit()
    config_db.refresh(sample)
    out: dict = {
        "sample": _sample_meta(sample),
        "status": status,
        "diffs": diffs,
        # Raw extraction-shaped values for the DojoSampleView: the ADMIN/agent
        # authored baseline vs what the LLM actually pulled. Never sourced
        # from Loaded.
        "expected": sample.expected,
        "extraction": extraction,
    }
    if extraction is None:
        out["error"] = (sample.last_run or {}).get("error")
    return out


@router.post("/samples/{sample_id}/run")
async def run_sample(
    sample_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    return _run_and_store(db, config_db, sample)


@router.get("/samples/{sample_id}/last-run")
async def last_run(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """The stored latest run + the baseline — no re-extraction. A sample with
    no run yet still returns its expected values (editable in the view)."""
    sample = _sample_or_404(config_db, sample_id)
    run = sample.last_run or {}
    extraction = run.get("extraction")
    if not extraction and sample.expected is None:
        raise HTTPException(404, run.get("error") or "no run stored yet — run first")
    return {
        "sample": _sample_meta(sample),
        "status": sample.last_status,
        "diffs": run.get("diffs") or [],
        "expected": sample.expected,
        "extraction": extraction,
        "error": run.get("error"),
    }


class ExpectedValuesRequest(BaseModel):
    expected: dict


@router.put("/samples/{sample_id}/expected-values")
async def put_expected_values(
    sample_id: str,
    body: ExpectedValuesRequest,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Write the ADMIN-authored baseline for a sample (the DojoSampleView's
    editable Expected side). Values are what the LLM is EXPECTED to pull off
    the document — never sourced from Loaded. Diffs and status against the
    stored last run recompute immediately."""
    if not isinstance(body.expected.get("lines"), list):
        raise HTTPException(400, "expected must carry a lines array")
    sample = _sample_or_404(config_db, sample_id)
    sample.expected = body.expected
    run = sample.last_run or {}
    if run.get("extraction"):
        diffs = spec_dojo.compare_extractions(sample.expected, run["extraction"])
        sample.last_run = {"extraction": run["extraction"], "diffs": diffs}
        sample.last_status = "pass" if not diffs else "fail"
    config_db.commit()
    config_db.refresh(sample)
    run = sample.last_run or {}
    return {
        "sample": _sample_meta(sample),
        "status": sample.last_status,
        "diffs": run.get("diffs") or [],
        "expected": sample.expected,
        "extraction": run.get("extraction"),
    }


@router.post("/samples/{sample_id}/expected")
async def save_expected(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Promote the latest run to the expected baseline (admin has reviewed it)."""
    sample = _sample_or_404(config_db, sample_id)
    extraction = (sample.last_run or {}).get("extraction")
    if not extraction:
        raise HTTPException(400, "no run to accept — run the sample first")
    sample.expected = extraction
    sample.last_run = {"extraction": extraction, "diffs": []}
    sample.last_status = "pass"
    config_db.commit()
    return {"sample": _sample_meta(sample)}


class DojoRunRequest(BaseModel):
    spec_ids: list[str] | None = None  # None = every spec with samples


@router.post("/dojo/run")
async def run_dojo(
    body: DojoRunRequest,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Run the full suite (or the given specs') samples and summarize.

    Each worker uses its OWN sessions (the extract_documents_parallel
    pattern) — the request's sessions are not thread-safe.
    """
    q = config_db.query(SupplierSpecSample)
    if body.spec_ids:
        q = q.filter(SupplierSpecSample.spec_id.in_(body.spec_ids))
    samples = q.order_by(SupplierSpecSample.created_at).all()
    if not samples:
        return {"suppliers": [], "passed": 0, "failed": 0, "errors": 0}
    sample_ids = [s.id for s in samples]

    def _worker(sid: str) -> dict:
        from app.db.engine import SessionLocal, _ConfigSessionLocal

        wdb, wcdb = SessionLocal(), _ConfigSessionLocal()
        try:
            ws = (
                wcdb.query(SupplierSpecSample)
                .filter(SupplierSpecSample.id == sid)
                .first()
            )
            if not ws:
                return {"sample": {"id": sid}, "status": "error", "diffs": []}
            out = _run_and_store(wdb, wcdb, ws)
            # summary only — the per-sample view fetches full values
            out.pop("expected", None)
            out.pop("extraction", None)
            return out
        finally:
            wdb.close()
            wcdb.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_worker, sample_ids))

    spec_names = {sp.id: sp.name for sp in config_db.query(SupplierInvoiceSpec).all()}
    by_spec: dict[str, dict] = {}
    for r in results:
        meta = r["sample"]
        spec_id = meta.get("spec_id") or ""
        group = by_spec.setdefault(
            spec_id,
            {"spec_id": spec_id, "name": spec_names.get(spec_id, "?"), "samples": []},
        )
        group["samples"].append(
            {
                "id": meta.get("id"),
                "label": meta.get("label"),
                "status": r["status"],
                "diff_count": len(r.get("diffs") or []),
            }
        )
    statuses = [r["status"] for r in results]
    return {
        "suppliers": list(by_spec.values()),
        "passed": statuses.count("pass"),
        "failed": statuses.count("fail"),
        "errors": statuses.count("error"),
        "new": statuses.count("new"),
    }


@router.get("/dojo/summary")
async def dojo_summary(
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Per-spec sample counts + stored statuses (no runs) for the list page."""
    out: dict[str, dict] = {}
    for s in config_db.query(SupplierSpecSample).all():
        g = out.setdefault(
            s.spec_id,
            {
                "spec_id": s.spec_id,
                "total": 0,
                "pass": 0,
                "fail": 0,
                "error": 0,
                "new": 0,
            },
        )
        g["total"] += 1
        key = (
            s.last_status
            if s.last_status in ("pass", "fail", "error", "new")
            else "new"
        )
        g[key] += 1
    return {"specs": list(out.values())}


class CandidateRunRequest(BaseModel):
    instructions: str


@router.post("/{spec_id}/candidate-run")
async def candidate_run_endpoint(
    spec_id: str,
    body: CandidateRunRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Test CANDIDATE instruction text against the dojo without committing it.

    Supplier row: runs that supplier's samples with the text as its spec.
    Main prompt row: runs EVERY sample with the text as the main prompt.
    Stored config is never modified.
    """
    spec = _spec_or_404(config_db, spec_id)
    out = spec_dojo.candidate_run(db, config_db, spec, body.instructions)
    # extractions are large — the tester only needs statuses + diffs
    for r in out["samples"]:
        r.pop("extraction", None)
    return out


class AnalyseRequest(BaseModel):
    # An admin's reply to the proposal thread — e.g. "line 4's unit must stay
    # '2x12 pack', never flattened". Sent to the agent as authoritative; the
    # full loop (re-analysis + candidate verification) runs again.
    feedback: str | None = None


@router.post("/samples/{sample_id}/analyse")
async def analyse_sample_endpoint(
    sample_id: str,
    body: AnalyseRequest | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Run the analysis agent on a sample synchronously (1–2 min): strong
    model + full context + candidate verification. Stores sample.analysis.
    With ``feedback``, this is the admin replying to the proposal thread."""
    _sample_or_404(config_db, sample_id)
    analysis = spec_dojo.analyse_sample(
        db, config_db, sample_id, feedback=(body.feedback if body else None)
    )
    return {"analysis": _slim_analysis(analysis)}


class ApplyAnalysisRequest(BaseModel):
    # Apply the proposed spec text (when non-empty) and baseline the agent's
    # ground truth for this sample.
    apply_spec: bool = True
    save_expected: bool = True


@router.post("/samples/{sample_id}/apply-analysis")
async def apply_analysis(
    sample_id: str,
    body: ApplyAnalysisRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Admin approval of an analysis proposal: write the spec text, baseline
    the agent's ground truth, and record the candidate extraction as the
    sample's last run (no re-spend).

    An ``alias_of`` proposal merges instead of duplicating: the supplier's
    name(s) become aliases on the TARGET spec, this spec's samples move
    there, and the redundant auto-created spec row is deleted — one layout,
    one spec."""
    sample = _sample_or_404(config_db, sample_id)
    analysis = sample.analysis or {}
    if analysis.get("status") not in ("ready", "not_green"):
        raise HTTPException(400, "no analysis proposal to apply — run Analyse first")
    spec = _spec_or_404(config_db, sample.spec_id)
    proposed = str(analysis.get("proposed_instructions") or "")
    alias_name = str(analysis.get("alias_of") or "").strip()
    target = None
    if alias_name:
        target = next(
            (
                r
                for r in config_db.query(SupplierInvoiceSpec).all()
                if r.name.lower() == alias_name.lower()
                and r.id != spec.id
                and r.name != spec_dojo.MAIN_PROMPT_NAME
            ),
            None,
        )
        if target is None:
            raise HTTPException(
                400, f"alias target spec '{alias_name}' no longer exists"
            )
    host = target or spec
    if body.apply_spec:
        if target is not None:
            merged = list(target.aliases or [])
            for cand in [spec.name, *(spec.aliases or [])]:
                if (
                    cand
                    and cand.lower() != target.name.lower()
                    and all(cand.lower() != a.lower() for a in merged)
                ):
                    merged.append(cand)
            target.aliases = merged
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
        body.save_expected
        and isinstance(analysis.get("ground_truth"), dict)
        and not sample.expected
    ):
        sample.expected = analysis["ground_truth"]
    own = (analysis.get("candidate_results") or {}).get("own") or {}
    if isinstance(own.get("extraction"), dict) and sample.expected:
        diffs = spec_dojo.compare_extractions(sample.expected, own["extraction"])
        sample.last_run = {"extraction": own["extraction"], "diffs": diffs}
        sample.last_status = "pass" if not diffs else "fail"
        sample.last_run_at = datetime.now(timezone.utc)
    applied = dict(analysis, status="applied")
    sample.analysis = applied
    config_db.commit()
    config_db.refresh(sample)
    return {
        "sample": _sample_meta(sample),
        "spec_instructions": host.instructions,
        "alias_added_to": target.name if target is not None else None,
    }


@router.post("/samples/{sample_id}/dismiss-analysis")
async def dismiss_analysis(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    sample.analysis = None
    config_db.commit()
    return {"sample": _sample_meta(sample)}


@router.get("/samples/{sample_id}/analysis")
async def get_analysis(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    return {"analysis": _slim_analysis(sample.analysis)}


def _slim_analysis(analysis: dict | None) -> dict | None:
    """The analysis payload for the panel. The candidate's own EXTRACTION is
    kept deliberately: "the agent says it passed" is not evidence — the admin
    must be able to see the values the proposed prompt actually pulled, next
    to the agent's corrected values, and check both against the PDF."""
    if not analysis:
        return None
    out = dict(analysis)
    results = out.get("candidate_results")
    if isinstance(results, dict):
        slim: dict = {}
        own = results.get("own")
        if isinstance(own, dict):
            slim["own"] = {k: own.get(k) for k in ("status", "diffs", "extraction")}
        sib = results.get("siblings")
        if isinstance(sib, dict):
            slim["siblings"] = sib
        out["candidate_results"] = slim
    return out
