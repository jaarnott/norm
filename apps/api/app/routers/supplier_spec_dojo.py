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
from app.db.engine import SessionLocal, get_config_db_rw, get_db
from app.db.models import User
from app.services import spec_dojo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/supplier-invoice-specs", tags=["supplier-spec-dojo"])

_MAX_PDF_BYTES = 5 * 1024 * 1024


def _replica_ok(run: dict | None) -> bool:
    """A replica counts as scored only when it BUILT — the failure object
    (`{"replica": True, "error": …}`) is truthy and has zero diffs, so
    counting it would inflate both `scored` and `clean`."""
    rep = (run or {}).get("replica")
    return isinstance(rep, dict) and not rep.get("error")


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
        "source_venue_id": s.source_venue_id,
        "has_replica": _replica_ok(s.last_run),
        "replica_diff_count": len((s.last_run or {}).get("replica_diffs") or []),
        "replica_warning_count": len(
            (((s.last_run or {}).get("replica") or {}).get("warnings")) or []
        ),
        "has_expected_replica": getattr(s, "expected_replica", None) is not None,
        # Dojo-page triage staging — excluded from regression until promoted.
        "draft": bool(getattr(s, "draft", None)),
        # The analysis agent's state: running | ready | not_green | failed |
        # applied | None — drives the panel's proposal UI + polling. Read
        # through analysis_view so an interrupted run reports failed (and can
        # be re-run) instead of spinning forever.
        "analysis_status": (spec_dojo.analysis_view(s.analysis) or {}).get("status"),
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
        .filter(
            SupplierSpecSample.spec_id == spec_id,
            SupplierSpecSample.draft.isnot(True),
        )
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


def _run_and_store(
    db: Session,
    config_db: Session,
    sample: SupplierSpecSample,
    reference: dict | None = None,
) -> dict:
    """Run one sample, persist last_run/status, return the run view.
    ``reference`` = the sample venue's prefetched replica reference data
    (batch runs fetch it once per venue)."""
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
        # Replica: OUR extraction resolved into a full working document and
        # scored against what Loaded resolved for the same invoice (only for
        # add-to-dojo samples, which carry the source venue).
        replica, replica_diffs, replica_compare = spec_dojo.replica_stage(
            db, config_db, sample, extraction, reference
        )
        sample.last_run = {
            "extraction": extraction,
            "diffs": diffs,
            "replica": replica,
            "replica_diffs": replica_diffs,
            "replica_compare": replica_compare,
        }
    except Exception as exc:  # noqa: BLE001 — a broken run must record, not 500
        logger.warning("dojo run failed for sample %s: %s", sample.id, exc)
        extraction, diffs, status = None, [], "error"
        replica, replica_diffs, replica_compare = None, [], None
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
        "replica": replica,
        "replica_diffs": replica_diffs,
        "replica_compare": replica_compare,
        "expected_replica": getattr(sample, "expected_replica", None),
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
        "replica": run.get("replica"),
        "replica_diffs": run.get("replica_diffs") or [],
        "replica_compare": run.get("replica_compare"),
        "expected_replica": getattr(sample, "expected_replica", None),
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
        # {**run}: the replica keys must survive this rebuild.
        sample.last_run = {**run, "extraction": run["extraction"], "diffs": diffs}
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
    # {**run}: the replica keys must survive this rebuild.
    sample.last_run = {**(sample.last_run or {}), "extraction": extraction, "diffs": []}
    sample.last_status = "pass"
    config_db.commit()
    return {"sample": _sample_meta(sample)}


@router.post("/samples/{sample_id}/replica-expected")
async def bless_replica(
    sample_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Bless the latest replica as the adjudicated truth — used when Loaded's
    own resolution (the scorecard's default ground truth) is itself wrong.
    Later runs suppress diffs where the replica agrees with the blessing."""
    sample = _sample_or_404(config_db, sample_id)
    replica = (sample.last_run or {}).get("replica")
    if not replica:
        raise HTTPException(400, "no replica to bless — run the sample first")
    sample.expected_replica = replica
    run = sample.last_run or {}
    # Recompute against fresh ground truth rather than zeroing: blessing
    # suppresses fields the replica now agrees on, but a Loaded line the
    # replica lacks (line_missing) has nothing to bless and must stay
    # visible. Best-effort — an unreachable venue leaves the diffs empty
    # exactly as the old behavior did.
    diffs: list[dict] = []
    if not replica.get("error") and sample.source_venue_id:
        try:
            from app.services.received_invoice import (
                LoadedInvoiceClient,
                build_received_invoice_data,
            )

            lh = LoadedInvoiceClient(db, config_db, sample.source_venue_id)
            gt = build_received_invoice_data(lh.invoice(sample.source_invoice_id))
            diffs = spec_dojo.compare_replica(replica, gt, replica)
        except Exception as exc:  # noqa: BLE001
            logger.info("bless recompute failed: %s", exc)
    run = {**run, "replica_diffs": diffs}
    sample.last_run = run
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
    q = config_db.query(SupplierSpecSample).filter(SupplierSpecSample.draft.isnot(True))
    if body.spec_ids:
        q = q.filter(SupplierSpecSample.spec_id.in_(body.spec_ids))
    samples = q.order_by(SupplierSpecSample.created_at).all()
    if not samples:
        return {"suppliers": [], "passed": 0, "failed": 0, "errors": 0}
    sample_ids = [s.id for s in samples]

    # Replica reference data fetched ONCE per venue on the request thread —
    # without this every worker pays a full catalogue + units + suppliers +
    # tax + 400-day-feed fetch per sample. Read-only dicts, safe to share.
    reference_by_venue: dict[str, dict] = {}
    _pref_db = SessionLocal()
    try:
        for vid in {
            s.source_venue_id
            for s in samples
            if s.source_venue_id and s.source_invoice_id
        }:
            reference_by_venue[vid] = spec_dojo.prefetch_replica_reference(
                _pref_db, config_db, vid
            )
    finally:
        _pref_db.close()

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
            out = _run_and_store(
                wdb, wcdb, ws, reference_by_venue.get(ws.source_venue_id or "")
            )
            # summary only — the per-sample view fetches full values (keep
            # replica_diffs for the accuracy rollup, drop the heavy docs)
            out.pop("expected", None)
            out.pop("extraction", None)
            out.pop("replica", None)
            out.pop("replica_compare", None)
            out.pop("expected_replica", None)
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
                "has_replica": bool(meta.get("has_replica")),
                "replica_diff_count": meta.get("replica_diff_count") or 0,
            }
        )
    statuses = [r["status"] for r in results]
    # Replica accuracy rollup: how OUR resolution scored against Loaded's
    # across the corpus, with the per-field diff histogram (the measurement
    # loop's exit criterion for the future component flip).
    replica_field_diffs: dict[str, int] = {}
    replica_scored = replica_clean = 0
    for r in results:
        if not r["sample"].get("has_replica"):
            continue
        replica_scored += 1
        rd = r.get("replica_diffs") or []
        if not rd:
            replica_clean += 1
        for d in rd:
            f = str(d.get("field"))
            replica_field_diffs[f] = replica_field_diffs.get(f, 0) + 1
    return {
        "suppliers": list(by_spec.values()),
        "passed": statuses.count("pass"),
        "failed": statuses.count("fail"),
        "errors": statuses.count("error"),
        "new": statuses.count("new"),
        "replica": {
            "scored": replica_scored,
            "clean": replica_clean,
            "field_diffs": replica_field_diffs,
        },
    }


@router.get("/dojo/summary")
async def dojo_summary(
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Per-spec sample counts + stored statuses (no runs) for the list page."""
    out: dict[str, dict] = {}
    for s in (
        config_db.query(SupplierSpecSample)
        .filter(SupplierSpecSample.draft.isnot(True))
        .all()
    ):
        g = out.setdefault(
            s.spec_id,
            {
                "spec_id": s.spec_id,
                "total": 0,
                "pass": 0,
                "fail": 0,
                "error": 0,
                "new": 0,
                "replica_scored": 0,
                "replica_clean": 0,
            },
        )
        g["total"] += 1
        key = (
            s.last_status
            if s.last_status in ("pass", "fail", "error", "new")
            else "new"
        )
        g[key] += 1
        run = s.last_run or {}
        if _replica_ok(run):
            g["replica_scored"] += 1
            if not (run.get("replica_diffs") or []):
                g["replica_clean"] += 1
    return {"specs": list(out.values())}


# ---------------------------------------------------------------------------
# The Dojo page: triage ALL venues' outstanding invoices before they join
# the per-supplier regression suite. Staged invoices are DRAFT samples —
# the full toolkit works on them, regression ignores them until promoted.
# ---------------------------------------------------------------------------

# Analysis states that still need a human: pending proposal, or running/failed.
_PENDING_ANALYSIS = ("running", "ready", "not_green", "failed")


@router.get("/dojo/overview")
async def dojo_overview(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """One fetch for the Dojo page: every venue's outstanding invoices with
    their dojo membership, plus the in-dojo samples still awaiting review
    (no baseline yet, or a pending/failed analysis proposal)."""
    from app.db.models import ConnectorConfig, Venue
    from app.services.received_invoice import LoadedInvoiceClient

    connected = {
        v_id
        for (v_id,) in db.query(ConnectorConfig.venue_id)
        .filter(
            ConnectorConfig.connector_name == "loadedhub",
            ConnectorConfig.enabled == "true",
            ConnectorConfig.venue_id.isnot(None),
        )
        .all()
    }
    venues = [
        v for v in db.query(Venue).order_by(Venue.name).all() if v.id in connected
    ]

    # Dojo membership for badge-ing, keyed by source invoice id.
    by_invoice: dict[str, SupplierSpecSample] = {
        s.source_invoice_id: s
        for s in config_db.query(SupplierSpecSample)
        .filter(SupplierSpecSample.source_invoice_id.isnot(None))
        .all()
    }

    outstanding: list[dict] = []
    errors: list[dict] = []
    for v in venues:
        try:
            lh = LoadedInvoiceClient(db, config_db, v.id)
            rows = lh.get(
                "/1.0/stock/internal/invoices"
                "?from=1901-01-01&to=9999-12-31&status=NotReceived"
                "&page=0&pageSize=200"
            )
            rows = rows if isinstance(rows, list) else (rows or {}).get("data") or []
            for inv in rows:
                if not isinstance(inv, dict):
                    continue
                if inv.get("isReceived") or inv.get("deletedAt"):
                    continue
                s = by_invoice.get(inv.get("id"))
                outstanding.append(
                    {
                        "venue_id": v.id,
                        "venue_name": v.name,
                        "invoice_id": inv.get("id"),
                        "reference": inv.get("referenceNumber"),
                        "supplier_name": inv.get("supplierName"),
                        "issued_at": inv.get("issuedAt"),
                        "total": inv.get("total"),
                        "has_file": bool(inv.get("fileId")),
                        "sample_id": s.id if s else None,
                        "draft": bool(s.draft) if s else False,
                        "in_dojo": bool(s) and not bool(s.draft),
                    }
                )
        except Exception as exc:  # noqa: BLE001 — one venue must not sink the page
            logger.info("dojo overview: venue %s failed: %s", v.name, exc)
            errors.append({"venue_name": v.name, "error": str(exc)})

    spec_names = {sp.id: sp.name for sp in config_db.query(SupplierInvoiceSpec).all()}
    pending_review = [
        {**_sample_meta(s), "spec_name": spec_names.get(s.spec_id, "?")}
        for s in config_db.query(SupplierSpecSample)
        .filter(SupplierSpecSample.draft.isnot(True))
        .order_by(SupplierSpecSample.created_at.desc())
        .all()
        if s.expected is None or ((s.analysis or {}).get("status") in _PENDING_ANALYSIS)
    ]

    return {
        "outstanding": outstanding,
        "pending_review": pending_review,
        "errors": errors,
    }


class StageRequest(BaseModel):
    venue_id: str
    invoice_id: str


@router.post("/dojo/stage")
async def stage_draft(
    body: StageRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Stage an outstanding invoice as a DRAFT sample for triage — no
    background analysis (the toolkit's Analyse button is on-demand)."""
    try:
        staged = spec_dojo.stage_invoice_sample(
            db, body.venue_id, body.invoice_id, draft=True
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    sample = _sample_or_404(config_db, staged["sample_id"])
    return {**staged, "sample": _sample_meta(sample)}


@router.post("/samples/{sample_id}/promote")
async def promote_sample(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Add a triage draft to the dojo proper — it joins the per-spec sample
    list, Run Dojo and the summary from here on."""
    sample = _sample_or_404(config_db, sample_id)
    sample.draft = False
    config_db.commit()
    config_db.refresh(sample)
    return {"sample": _sample_meta(sample)}


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
    """Run the SENSEI on a sample synchronously (1–2 min): strong
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
    """Admin approval of a sensei proposal: write the spec text, baseline
    the agent's ground truth, and record the candidate extraction as the
    sample's last run (no re-spend).

    An ``alias_of`` proposal merges instead of duplicating: the supplier's
    name(s) become aliases on the TARGET spec, this spec's samples move
    there, and the redundant auto-created spec row is deleted — one layout,
    one spec. The logic lives in ``spec_dojo.apply_analysis_proposal`` (shared
    with the self-training auto-apply)."""
    sample = _sample_or_404(config_db, sample_id)
    try:
        applied = spec_dojo.apply_analysis_proposal(
            config_db,
            sample,
            apply_spec=body.apply_spec,
            save_expected=body.save_expected,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"sample": _sample_meta(sample), **applied}


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
    analysis = spec_dojo.analysis_view(analysis)
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
