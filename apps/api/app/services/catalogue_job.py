"""A Catalogue page merge, run as a background job (services/background_jobs).

An item merge moves every received invoice line, and a staple sits on
hundreds of them: Post Mix Lemonade at La Zeppa is on 104 invoices back to
2016. One run of the tool makes at most 180 Loaded calls and stops with
"in progress"; the page used to run it again and again from the browser, so
closing the page stopped a merge half way (safe — the old record is retired
last — but unfinished). Owner, 23 Oct 2026: run it in the background, like
the invoice batches, with a banner on the page.

The job runs the SAME tool call the person confirmed (manage_stock_item
merge / merge_units), pass after pass, until the tool says it is finished:

- the first pass carries the preview's fingerprint, so it still refuses if
  the records moved since the person looked; later passes don't (the tool
  re-reads what is left each time);
- each pass's moved counts are added up in the job's progress, which is also
  its heartbeat; a restarted worker carries on from the next pass;
- a cancel stops it between passes (everything moved so far stays moved; the
  old record isn't retired);
- when it ends, the person who asked gets an email with a link back to the
  Catalogue page.
"""

from __future__ import annotations

import logging

from app.services import background_jobs

logger = logging.getLogger(__name__)

JOB_KIND = "catalogue_merge"
#: The ops that can take many runs, so they go to a job.
LONG_OPS = {"merge", "merge_units"}
#: Passes before the job gives up (each moves up to ~90 invoices).
MAX_PASSES = 60
#: Extra passes after one that ended "partly merged". A save to Loaded can
#: fail once and work again (a dropped connection on the test company, 23 Oct
#: 2026); the next pass re-reads what still points at the old record.
RETRIES = 2


def _run_once(params: dict, fingerprint: str | None) -> dict:
    """One run of the tool, as /api/stock-catalogue/run makes it."""
    from app.agents.internal_tools import execute_consolidator
    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.services import caller_scope

    tool = params["tool"]
    cdb = _ConfigSessionLocal()
    try:
        spec = (
            cdb.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        tool_def = next(
            (
                r
                for r in (spec_rows.tools(spec) if spec else [])
                if r.get("action") == tool and r.get("consolidator_config")
            ),
            None,
        )
    finally:
        cdb.close()
    if tool_def is None:
        raise RuntimeError(f"loadedhub.{tool} isn't installed")
    cfg = {**tool_def["consolidator_config"], "action": tool}
    if fingerprint:
        cfg["expect_fingerprint"] = fingerprint
    db = SessionLocal()
    try:
        with caller_scope.use(caller_scope.for_venue(db, params["venue_id"])):
            result = execute_consolidator(cfg, dict(params["tool_params"]), db, None)
        db.commit()
    finally:
        db.close()
    if "changed" in result:
        return {"changed": True}
    data = result.get("data")
    if not result.get("success", True) and not isinstance(data, dict):
        return {"error": result.get("error") or "the tool failed"}
    return data if isinstance(data, dict) else {"error": "no answer from the tool"}


def _add(total: dict, moved: dict | None) -> dict:
    out = dict(total)
    for k, v in (moved or {}).items():
        if isinstance(v, (int, float)):
            out[k] = int(out.get(k) or 0) + int(v)
    return out


@background_jobs.register(JOB_KIND)
def run(ctx: background_jobs.JobContext) -> dict:
    p = ctx.params
    passes = int(ctx.progress.get("passes") or 0)
    moved = dict(ctx.progress.get("moved") or {})
    # The fingerprint guards the person's view of the records. A pass that
    # began and was cut off (a restart) may already have moved some of them,
    # so a resumed first pass runs without it.
    resumed = bool(ctx.progress.get("first_pass_begun"))
    retries = int(ctx.progress.get("retries") or 0)
    data: dict = {}
    while passes < MAX_PASSES:
        if ctx.cancelled():
            return {"result": "stopped", "moved": moved, "passes": passes}
        if passes == 0:
            ctx.update(first_pass_begun=True)
        fingerprint = p.get("fingerprint") if passes == 0 and not resumed else None
        data = _run_once(p, fingerprint)
        if data.get("changed"):
            raise RuntimeError(
                "These records changed since the change was checked, so nothing "
                "was moved — open the Catalogue and try it again."
            )
        passes += 1
        moved = _add(moved, data.get("moved"))
        ctx.update(
            done=passes,
            passes=passes,
            moved=moved,
            remaining=data.get("remaining") or {},
            last_result=data.get("result"),
        )
        if data.get("result") == "partly merged" and retries < RETRIES:
            retries += 1
            ctx.update(retries=retries, last_failed=data.get("failed") or [])
            continue
        if data.get("result") != "in progress":
            break
    else:
        raise RuntimeError(
            f"Still not finished after {MAX_PASSES} passes — open the Catalogue and "
            "run the merge again to carry on."
        )
    # "merged", or "partly merged" / refused with an error: what moved stays
    # moved, and the old record is retired only when everything did.
    return {**data, "moved": moved, "passes": passes}


def enqueue(db, *, user, venue, tool: str, tool_params: dict, fingerprint, title: str):
    """(job, created) — at most one catalogue merge per venue at a time."""
    return background_jobs.enqueue(
        db,
        JOB_KIND,
        title=title,
        params={
            "tool": tool,
            "tool_params": tool_params,
            "fingerprint": fingerprint,
            "venue_id": venue.id,
        },
        user_id=user.id,
        organization_id=venue.organization_id,
        venue_id=venue.id,
    )


def summary_text(job) -> str:
    """One sentence for the email: what moved, or why it stopped."""
    result = job.result or {}
    moved = result.get("moved") or (job.progress or {}).get("moved") or {}
    parts = []
    for key, one, many in (
        ("invoice_lines", "received invoice line", "received invoice lines"),
        ("variants", "supplier variant", "supplier variants"),
        ("items", "item", "items"),
        ("orders", "open order", "open orders"),
        ("recipe_versions", "recipe version", "recipe versions"),
        ("pos_links", "POS link", "POS links"),
    ):
        n = int(moved.get(key) or 0)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    what = ", ".join(parts) or "nothing"
    if job.status == "done" and result.get("result") == "merged":
        return f"{job.title} is done. It moved {what}."
    if job.status == "cancelled" or result.get("result") == "stopped":
        return f"{job.title} was stopped after moving {what}. The old record is still active."
    reason = job.error or result.get("error") or "it didn't finish"
    return f"{job.title} didn't finish: {reason} It had moved {what}."


@background_jobs.after_finish(JOB_KIND)
def email_summary(job_id: str) -> None:
    """Email the person who asked, once the job's end is committed."""
    from app.db.engine import SessionLocal
    from app.db.models import BackgroundJob, User
    from app.mcp.links import _base
    from app.services.email_service import send_system_email

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if job is None or not job.user_id:
            return
        user = db.query(User).filter(User.id == job.user_id).first()
        if user is None or not user.email:
            return
        try:
            send_system_email(
                "job_finished",
                [user.email],
                {
                    "user_name": (user.full_name or "").split(" ")[0] or "there",
                    "job_title": job.title,
                    "summary": summary_text(job),
                    "status": job.status,
                    "link": f"{_base()}/app?page=stock-catalogue",
                    "link_label": "Open the Catalogue",
                },
                db,
                organization_id=job.organization_id,
            )
            db.commit()
        except Exception:  # noqa: BLE001 — a failed email never undoes the job
            logger.exception("job %s: finish email failed", job_id)
            db.rollback()
    finally:
        db.close()
