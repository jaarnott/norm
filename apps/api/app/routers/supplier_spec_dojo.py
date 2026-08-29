"""Supplier Spec Dojo endpoints — sample invoices + regression runs.

Admin uploads sample invoice PDFs per supplier spec; each run re-extracts them
with the CURRENT prompts (main + spec) and diffs against the admin-accepted
baseline, so prompt edits get regression-tested from Settings → Supplier Specs
before they misread a real invoice. All admin-only; samples live in the shared
config DB (one suite, site-wide) — see services/spec_dojo.py for the runner.

Every endpoint here is a plain ``def``, NOT ``async def`` — deliberately.
These handlers do sync-blocking work (SQLAlchemy sessions, LoadedHub HTTP
calls, extraction runs), and an ``async def`` runs that work ON the event
loop: one slow Loaded call in ``dojo_overview`` froze the entire local site
(even /health) while the page polled it during a sensei run (16 Aug 2026).
Plain ``def`` handlers run in FastAPI's threadpool, where blocking is
harmless. Only ``upload_sample`` stays async — it awaits the file body.
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
from app.services import sensei_runner, spec_dojo

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
        # Dojo-page triage staging — excluded from regression until promoted.
        "draft": bool(getattr(s, "draft", None)),
        # The analysis agent's state: queued | running | ready | not_green |
        # failed | applied | None — drives the panel's proposal UI + polling.
        # Read through analysis_view so a dead run reports stale/failed (and
        # can be re-run) instead of spinning forever.
        "analysis_status": (view := spec_dojo.analysis_view(s.analysis) or {}).get(
            "status"
        ),
        "analysis_phase": view.get("phase"),
        "analysis_stale": bool(view.get("stale")),
        "analysis_attempts": int(view.get("attempts") or 0),
        "analysis_error": view.get("error"),
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
def list_samples(
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
def delete_sample(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    config_db.delete(sample)
    config_db.commit()
    return {"deleted": True}


@router.get("/samples/{sample_id}/pdf")
def sample_pdf(
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
        # Replica: OUR extraction resolved into a full working document
        # against the venue's Loaded CATALOGUE (only for cannot-receive
        # samples, which carry the source venue). Not scored against Loaded's
        # own read of the invoice — that comparison was removed 16 Aug 2026.
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
    }
    if extraction is None:
        out["error"] = (sample.last_run or {}).get("error")
    return out


@router.post("/samples/{sample_id}/run")
def run_sample(
    sample_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    return _run_and_store(db, config_db, sample)


@router.get("/samples/{sample_id}/last-run")
def last_run(
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
        "error": run.get("error"),
    }


class ExpectedValuesRequest(BaseModel):
    expected: dict


@router.put("/samples/{sample_id}/expected-values")
def put_expected_values(
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
def save_expected(
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


# The /samples/{id}/replica-expected "bless" endpoint was removed 16 Aug 2026
# along with the replica-vs-Loaded scorecard it served: the replica is no
# longer scored against Loaded's own read of the invoice, so there is nothing
# to adjudicate. The expected_replica column keeps its historical data.


class DojoRunRequest(BaseModel):
    spec_ids: list[str] | None = None  # None = every spec with samples


@router.post("/dojo/run")
def run_dojo(
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
    # Venues are resolved per-ENVIRONMENT (a prod-filed sample maps to this
    # env's venue for the same Loaded company).
    reference_by_venue: dict[str, dict] = {}
    resolved_by_sample: dict[str, str] = {}
    _pref_db = SessionLocal()
    try:
        for s in samples:
            if s.source_venue_id and s.source_invoice_id:
                rv = spec_dojo.resolve_sample_venue_id(_pref_db, s)
                if rv:
                    resolved_by_sample[s.id] = rv
        for vid in set(resolved_by_sample.values()):
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
                wdb, wcdb, ws, reference_by_venue.get(resolved_by_sample.get(sid, ""))
            )
            # summary only — the per-sample view fetches full values (keep
            # replica_diffs for the accuracy rollup, drop the heavy docs)
            out.pop("expected", None)
            out.pop("extraction", None)
            out.pop("replica", None)
            out.pop("replica_compare", None)
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
def dojo_summary(
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

# Analysis states that still need a human: pending proposal, or queued/running/failed.
_PENDING_ANALYSIS = ("queued", "running", "ready", "not_green", "failed")


def _pending_review_rows(config_db: Session) -> list[dict]:
    """The in-dojo samples still awaiting a human: no baseline yet, or a
    queued/running/pending/failed analysis. Config-DB only — cheap."""
    spec_names = {sp.id: sp.name for sp in config_db.query(SupplierInvoiceSpec).all()}
    return [
        {**_sample_meta(s), "spec_name": spec_names.get(s.spec_id, "?")}
        for s in config_db.query(SupplierSpecSample)
        .filter(SupplierSpecSample.draft.isnot(True))
        .order_by(SupplierSpecSample.created_at.desc())
        .all()
        if s.expected is None or ((s.analysis or {}).get("status") in _PENDING_ANALYSIS)
    ]


@router.get("/dojo/pending")
def dojo_pending(
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Just the awaiting-review list — the part of the Dojo page people came
    for. Split from /dojo/overview (16 Aug 2026) because the overview's
    outstanding sweep calls LoadedHub per venue and takes seconds; the dojo
    list is a config-DB read and should render immediately."""
    return {"pending_review": _pending_review_rows(config_db)}


@router.get("/dojo/overview")
def dojo_overview(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """One fetch for the Dojo page: every venue's outstanding invoices with
    their dojo membership, plus the in-dojo samples still awaiting review
    (no baseline yet, or a pending/failed analysis proposal)."""
    from app.db.models import Connection, Venue
    from app.services.received_invoice import LoadedInvoiceClient

    connected = {
        v_id
        for (v_id,) in db.query(Connection.venue_id)
        .filter(
            Connection.connector_name == "loadedhub",
            Connection.enabled == "true",
            Connection.venue_id.isnot(None),
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

    return {
        "outstanding": outstanding,
        "pending_review": _pending_review_rows(config_db),
        "errors": errors,
    }


class StageRequest(BaseModel):
    venue_id: str
    invoice_id: str


@router.post("/dojo/stage")
def stage_draft(
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
def promote_sample(
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
def candidate_run_endpoint(
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
def analyse_sample_endpoint(
    sample_id: str,
    body: AnalyseRequest | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Queue a SENSEI run for the sample: strong model + full context +
    candidate verification. With ``feedback``, this is the admin replying to
    the proposal thread.

    This ONLY enqueues — it returns in milliseconds with the analysis marked
    ``queued``, and the worker (inline thread locally, Cloud Run job when
    ``SENSEI_JOB`` is set) executes it. It used to run inline without a job,
    a 1-4 minute blocking call that 504'd through request proxies and died
    with every dev-server reload (16 Aug 2026); the queue survives both.
    """
    sample = _sample_or_404(config_db, sample_id)
    feedback = body.feedback if body else None

    status = sensei_runner.enqueue(sample_id, feedback)
    config_db.refresh(sample)
    return {"analysis": _slim_analysis(sample.analysis), "dispatched": status}


class ApplyAnalysisRequest(BaseModel):
    # Apply the proposed spec text (when non-empty) and baseline the agent's
    # ground truth for this sample.
    apply_spec: bool = True
    save_expected: bool = True


@router.post("/samples/{sample_id}/apply-analysis")
def apply_analysis(
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
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"sample": _sample_meta(sample), **applied}


@router.post("/samples/{sample_id}/dismiss-analysis")
def dismiss_analysis(
    sample_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    sample = _sample_or_404(config_db, sample_id)
    sample.analysis = None
    config_db.commit()
    return {"sample": _sample_meta(sample)}


@router.get("/samples/{sample_id}/analysis")
def get_analysis(
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


@router.get("/autopilot-confidence")
def autopilot_confidence(
    days: int = 30,
    venue_id: str | None = None,
    supplier_name: str | None = None,
    actor: str = "user",
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Would autopilot have been right? The evidence, per supplier.

    Every human receive is a free experiment in the counterfactual "accept all
    suggestions, then receive". ``actor`` defaults to **user** on purpose:
    Norm's own receives are self-fulfilling (autopilot accepted everything a
    moment before receiving), so counting them would make the number say
    nothing. They are reported separately, as volume.
    """
    from datetime import timedelta

    from app.db.models import InvoiceAutopilotOutcome, Venue

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    q = db.query(InvoiceAutopilotOutcome).filter(
        InvoiceAutopilotOutcome.created_at >= since
    )
    # Scoped to the caller's own organisation. This read the WHOLE table for
    # any admin:system holder — every other tenant's suppliers, reference
    # numbers and receive history — which is a cross-tenant leak, not a
    # reporting nicety. Platform admins have no organisation, and keep the
    # cross-tenant view they need for support.
    if getattr(user, "organization_id", None):
        q = q.filter(InvoiceAutopilotOutcome.organization_id == user.organization_id)
    if venue_id:
        q = q.filter(InvoiceAutopilotOutcome.venue_id == venue_id)
    if supplier_name:
        q = q.filter(InvoiceAutopilotOutcome.supplier_name == supplier_name)
    rows = q.order_by(InvoiceAutopilotOutcome.created_at.desc()).all()

    human = [r for r in rows if r.actor == "user"]
    norm_rows = [r for r in rows if r.actor == "norm"]
    scope = human if actor == "user" else (norm_rows if actor == "norm" else rows)

    def _tally(rs: list) -> dict:
        t = {
            k: 0 for k in ("clean", "no_suggestions", "edited", "not_reviewed", "dojo")
        }
        for r in rs:
            if r.outcome in t:
                t[r.outcome] += 1
        t["attempts"] = len(rs)
        return t

    totals = _tally(scope)
    # not_reviewed is in no denominator: we cannot say what autopilot would
    # have done with an invoice nobody ever reviewed.
    rated = (
        totals["clean"] + totals["no_suggestions"] + totals["edited"] + totals["dojo"]
    )
    # suggestion_quality asks the narrower question — when Norm had something
    # to say, was it right? — so zero-suggestion invoices leave BOTH sides.
    with_sugg = [
        r for r in scope if r.suggestion_count > 0 and r.outcome != "not_reviewed"
    ]
    clean_with_sugg = sum(1 for r in with_sugg if r.outcome == "clean")

    def _rate(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    by_supplier: dict[str, dict] = {}
    for r in scope:
        key = r.supplier_name or "(no supplier)"
        s = by_supplier.setdefault(
            key,
            {
                "supplier_name": key,
                "attempts": 0,
                "clean": 0,
                "no_suggestions": 0,
                "edited": 0,
                "dojo": 0,
                "not_reviewed": 0,
                "suggestions": 0,
            },
        )
        s["attempts"] += 1
        if r.outcome in s:
            s[r.outcome] += 1
        s["suggestions"] += r.suggestion_count or 0
    for s in by_supplier.values():
        d = s["clean"] + s["no_suggestions"] + s["edited"] + s["dojo"]
        s["autopilot_ready"] = _rate(s["clean"] + s["no_suggestions"], d)
        s["avg_suggestions"] = (
            round(s["suggestions"] / s["attempts"], 2) if s["attempts"] else 0
        )

    # What Norm keeps missing — the training backlog, normalised so the same
    # field on different lines aggregates ('line:<uuid>.unit_cost').
    missed: dict[str, int] = {}
    for r in scope:
        for f in (r.detail or {}).get("manual_fields") or []:
            key = f.split(".", 1)[-1] if str(f).startswith("line:") else str(f)
            key = f"line.{key}" if str(f).startswith("line:") else key
            missed[key] = missed.get(key, 0) + 1

    # The end-state verdicts (detail.auto, recorded at receive): would
    # autopilot with every flag on have sent the identical receive? And the
    # per-flag sentence — for each toggle, how many receives it alone would
    # have unlocked, matched to the byte.
    from app.services.venue_autopilot import GATES

    auto_rows = [
        (r, (r.detail or {}).get("auto"))
        for r in scope
        if isinstance((r.detail or {}).get("auto"), dict)
    ]
    auto_tally = {k: 0 for k in ("matched", "differed", "never_auto", "unscored")}
    for _r, a in auto_rows:
        v = str(a.get("verdict") or "unscored")
        auto_tally[v if v in auto_tally else "unscored"] += 1
    flags = []
    for gate, label in GATES.items():
        sole = with_others = 0
        for _r, a in auto_rows:
            if a.get("verdict") != "matched":
                continue
            needed = a.get("gates_needed") or []
            if needed == [gate]:
                sole += 1
            elif gate in needed:
                with_others += 1
        if sole or with_others:
            flags.append(
                {
                    "gate": gate,
                    "label": label,
                    "sole_unlock": sole,
                    "with_others": with_others,
                }
            )
    flags.sort(key=lambda f: (-f["sole_unlock"], -f["with_others"]))
    # Receives already identical with NO flag needed: autopilot was simply
    # not switched on for them.
    auto_no_flags = sum(
        1
        for _r, a in auto_rows
        if a.get("verdict") == "matched" and not (a.get("gates_needed") or [])
    )

    venues = {v.id: v.name for v in db.query(Venue).all()}
    return {
        "window": {"days": days, "actor": actor, "since": since.isoformat()},
        "totals": totals,
        "auto": {**auto_tally, "no_flags_needed": auto_no_flags},
        "flags": flags,
        "rates": {
            "autopilot_ready": _rate(totals["clean"] + totals["no_suggestions"], rated),
            "suggestion_quality": _rate(clean_with_sugg, len(with_sugg)),
            "dojo": _rate(totals["dojo"], rated),
        },
        # Volume only — never mixed into the rates above.
        "autopilot": _tally(norm_rows),
        "suppliers": sorted(by_supplier.values(), key=lambda s: -s["attempts"]),
        "top_missed_fields": [
            {"field": k, "count": v}
            for k, v in sorted(missed.items(), key=lambda kv: -kv[1])[:12]
        ],
        "recent": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "venue_name": venues.get(r.venue_id),
                "supplier_name": r.supplier_name,
                "reference_number": r.reference_number,
                "outcome": r.outcome,
                "mode": r.mode,
                "actor": r.actor,
                "suggestion_count": r.suggestion_count,
                "accepted_count": r.accepted_count,
                "dismissed_count": r.dismissed_count,
                "pending_count": r.pending_count,
                "manual_edit_count": r.manual_edit_count,
                "manual_fields": (r.detail or {}).get("manual_fields") or [],
                "issues_waved_count": r.issues_waved_count,
                # The end-state verdict + diffs — the admin viewer's "what
                # was actually sent vs what autopilot would have sent".
                "auto": (r.detail or {}).get("auto"),
            }
            for r in scope[: max(1, min(limit, 200))]
        ],
    }
