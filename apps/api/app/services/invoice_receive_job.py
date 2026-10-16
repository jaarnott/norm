"""Receiving a venue's invoices as a background job (services/background_jobs).

A chat asking for "all outstanding invoices" can mean 200 of them — far past
the 25 minutes a chat request is allowed (14 Oct 2026: two attempts, ~$15,
nothing saved). Past ``JOB_THRESHOLD`` invoices the review tool starts this job
instead and the chat answers straight away with a live card. The job:

- works through the invoices ``CHUNK`` at a time with the same batch review
  the chat uses (``invoice_review.review_invoices``);
- saves each invoice's draft the moment it is reviewed and records it in the
  job's progress — a restart carries on from the next one;
- stops between invoices when cancelled;
- posts its summary (and the drafts that need the person) into the thread it
  came from, and emails the person.
"""

from __future__ import annotations

import copy
import logging

from sqlalchemy.orm import Session

from app.services import background_jobs

logger = logging.getLogger(__name__)

JOB_KIND = "receive_invoices"
#: More invoices than this in one chat request → a background job.
JOB_THRESHOLD = 8
#: Invoices per pass (details, extraction, review); progress is per invoice.
CHUNK = 10
#: A chat-run batch stops starting new invoices after this long — well inside
#: the 25-minute request limit, so it answers instead of being cut off.
CHAT_BUDGET_SECONDS = 12 * 60
#: Drafts that need the person, shown as cards in the closing message.
MAX_CARDS = 20

_WD_CONFIG = {
    "doc_type": "received_invoice",
    "sync_mode": "submit",
    "items_path": "fix_invoices",
    "ref_fields": ["invoice_id"],
}

_RECEIVED = {"received", "deleted"}
_FAILED = {"review failed", "could not fetch from Loaded"}


def save_draft(
    bind_db: Session, thread_id: str | None, venue_id: str, data: dict
) -> str | None:
    """Save one invoice's reviewed draft NOW, on its own transaction — the
    same document the chat's end-of-turn fan-out writes (it updates this one
    rather than adding another). Returns the document id."""
    if not thread_id or not isinstance(data, dict) or not data.get("invoice_id"):
        return None
    from app.agents.tool_loop import _upsert_working_document

    own = Session(bind=bind_db.get_bind())
    try:
        doc = _upsert_working_document(
            own,
            thread_id,
            "loadedhub",
            _WD_CONFIG,
            copy.deepcopy(data),
            copy.deepcopy(data),
            venue_id=venue_id,
        )
        own.commit()
        return doc.id
    except Exception:  # noqa: BLE001 — the end-of-turn write still has it
        logger.exception("could not save the draft for %s", data.get("invoice_id"))
        own.rollback()
        return None
    finally:
        own.close()


def start(
    db: Session,
    *,
    venue_id: str,
    venue_name: str,
    thread_id: str,
    user_id: str | None,
    rows: list[dict],
    mode: str,
    require_valid_po: bool,
    from_date: str | None,
    to_date: str | None,
):
    """Enqueue the job (committed now, so a worker can claim it) and return
    ``(job, created)`` — an already-running job for the venue is returned
    rather than started twice."""
    from app.db.models import Venue

    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    receiving = mode in ("autopilot", "approve_fixes", "unset")
    title = f"{'Receiving' if receiving else 'Reviewing'} {venue_name} invoices"
    own = Session(bind=db.get_bind())
    try:
        job, created = background_jobs.enqueue(
            own,
            JOB_KIND,
            title=title,
            params={
                "venue_id": venue_id,
                "venue_name": venue_name,
                "thread_id": thread_id,
                "invoice_ids": [r["id"] for r in rows],
                "mode": mode,
                "require_valid_po": require_valid_po,
                "from_date": from_date,
                "to_date": to_date,
            },
            progress={"total": len(rows), "done": 0},
            user_id=user_id,
            organization_id=getattr(venue, "organization_id", None),
            venue_id=venue_id,
            thread_id=thread_id,
        )
        own.commit()
        own.refresh(job)
        own.expunge(job)
        return job, created
    finally:
        own.close()


def _outcome_bucket(outcome: str) -> str:
    if outcome in _RECEIVED:
        return "received"
    if outcome in _FAILED:
        return "failed"
    return "needs_you"


@background_jobs.register(JOB_KIND)
def run(ctx: background_jobs.JobContext) -> dict:
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.services import caller_scope
    from app.services.approvals import receiving_settings_for_thread
    from app.services.invoice_review import review_invoices

    p = ctx.params
    venue_id = p["venue_id"]
    thread_id = p.get("thread_id")
    done_ids = set(ctx.progress.get("done_ids") or [])
    todo = [i for i in p.get("invoice_ids") or [] if i not in done_ids]

    def on_invoice(db: Session):
        def _record(iid: str, record: dict, data: dict | None) -> None:
            outcome = str(record.get("outcome") or "needs review")
            bucket = _outcome_bucket(outcome)
            doc_id = None
            if data is not None and bucket == "needs_you":
                doc_id = save_draft(db, thread_id, venue_id, data)
            prog = ctx.progress
            needs = list(prog.get("needs_you_list") or [])
            if bucket == "needs_you" and len(needs) < 200:
                needs.append(
                    {
                        "invoice_id": iid,
                        "reference": record.get("reference_number"),
                        "supplier": record.get("supplier_name"),
                        "outcome": outcome,
                        "reasons": (record.get("reasons") or [])[:2],
                        "doc_id": doc_id,
                    }
                )
            label = " ".join(
                str(x)
                for x in (record.get("supplier_name"), record.get("reference_number"))
                if x
            )
            ctx.update(
                done=int(prog.get("done") or 0) + 1,
                done_ids=list(prog.get("done_ids") or []) + [iid],
                **{bucket: int(prog.get(bucket) or 0) + 1},
                needs_you_list=needs,
                current=label or iid,
                last_outcome=outcome,
            )

        return _record

    for start_at in range(0, len(todo), CHUNK):
        if ctx.cancelled():
            break
        chunk = todo[start_at : start_at + CHUNK]
        db = SessionLocal()
        cdb = _ConfigSessionLocal()
        try:
            scope = caller_scope.for_thread(db, thread_id) or caller_scope.for_venue(
                db, venue_id
            )
            with caller_scope.use(scope):
                out = review_invoices(
                    db,
                    cdb,
                    venue_id,
                    chunk,
                    mode=str(p.get("mode") or "approve_all"),
                    require_valid_po=p.get("require_valid_po") is not False,
                    settings=receiving_settings_for_thread(db, thread_id),
                    on_invoice=on_invoice(db),
                    should_stop=ctx.cancelled,
                )
            db.commit()
        finally:
            db.close()
            cdb.close()
        ctx.update(applied_mode=out.get("mode"))
    return {
        k: ctx.progress.get(k)
        for k in ("total", "done", "received", "needs_you", "failed")
    }


def summary_text(job, cards: int = 0) -> str:
    """Norm's closing line. ``cards``: how many of the drafts that need the
    person are shown with it (none in the email)."""
    p = job.progress or {}
    total, done = int(p.get("total") or 0), int(p.get("done") or 0)
    parts = [
        f"{int(p.get(k) or 0)} {label}"
        for k, label in (
            ("received", "received"),
            ("needs_you", "need you"),
            ("failed", "couldn't be reviewed"),
        )
        if p.get(k)
    ]
    tally = f"{done} of {total} reviewed" + (f" — {', '.join(parts)}" if parts else "")
    if job.status == "done":
        head = f"Finished: {job.title}. {tally}."
    elif job.status == "cancelled":
        head = f"Stopped {job.title} as you asked. {tally}; everything done so far is saved."
    else:
        head = (
            f"{job.title} stopped before finishing: {job.error or 'it kept failing'}. "
            f"{tally}; everything done so far is saved, so asking again carries on "
            "from where it stopped."
        )
    needs = int(p.get("needs_you") or 0)
    if needs and cards >= needs:
        head += " The ones that need you are below, and on the Invoices page."
    elif needs and cards:
        head += (
            f" The first {cards} that need you are below; all {needs} are on the "
            "Invoices page."
        )
    elif needs:
        head += " The ones that need you are on the Invoices page."
    return head


@background_jobs.on_finish(JOB_KIND)
def post_summary(db: Session, job) -> None:
    """Norm's summary in the thread the job came from, with the drafts that
    need the person — committed with the job's end state."""
    from app.db.models import Message

    if not job.thread_id:
        return
    p = job.progress or {}
    blocks = [
        {
            "component": "receive_invoice_editor",
            "data": {"working_document_id": n["doc_id"], "venue_id": job.venue_id},
            "props": {"compact": True, "activeVenueId": job.venue_id},
        }
        for n in (p.get("needs_you_list") or [])
        if n.get("doc_id")
    ][:MAX_CARDS]
    db.add(
        Message(
            thread_id=job.thread_id,
            role="assistant",
            content=summary_text(job, cards=len(blocks)),
            display_blocks=blocks or None,
        )
    )


@background_jobs.after_finish(JOB_KIND)
def email_summary(job_id: str) -> None:
    """Email the person who asked, once the job's end is committed."""
    from app.db.engine import SessionLocal
    from app.db.models import BackgroundJob, User
    from app.mcp.links import thread_link
    from app.services.email_service import send_system_email

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if job is None or not job.thread_id or not job.user_id:
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
                    "link": thread_link(job.thread_id),
                },
                db,
                organization_id=job.organization_id,
                thread_id=job.thread_id,
            )
            db.commit()
        except Exception:  # noqa: BLE001 — a failed email never undoes the job
            logger.exception("job %s: finish email failed", job_id)
            db.rollback()
    finally:
        db.close()
