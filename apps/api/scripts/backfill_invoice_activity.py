"""Fill the invoice activity page's "stopped" history from background jobs.

Until 19 Oct 2026 an invoice the batch couldn't finish was kept only on the
background job that reviewed it (``progress.needs_you_list``: the invoice, its
supplier and number, the outcome and up to two reasons). The page reads
``invoice_activity_events``, which the batch writes from now on; this copies
the jobs' lists into it, so the page's Stopped highlight covers the same 30
days as the rest of it. Invoices received or deleted since are hidden by the
page itself.

An event that already exists is left as it is: the batch's own record is
newer and fuller. The level wasn't recorded then, so it stays empty.

Usage:
    .venv/bin/python scripts/backfill_invoice_activity.py --dry-run
    .venv/bin/python scripts/backfill_invoice_activity.py [--days 30]
"""

import argparse
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

#: A job record's outcome → the page's kind.
KINDS = {
    "needs review": "stopped",
    "ready to receive — awaiting approval": "waiting",
    "receive failed": "failed",
    "review failed": "failed",
    "could not fetch from Loaded": "failed",
}


def plan(db, days: int) -> list[dict]:
    """The events the jobs' lists describe, one per invoice and kind (the
    latest job's wins)."""
    from app.db.models import BackgroundJob

    since = datetime.now(timezone.utc) - timedelta(days=days)
    jobs = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.kind == "receive_invoices",
            BackgroundJob.created_at >= since,
        )
        .order_by(BackgroundJob.created_at)
        .all()
    )
    out: dict[tuple, dict] = {}
    for job in jobs:
        params = job.params or {}
        progress = job.progress or {}
        venue_id = job.venue_id or params.get("venue_id")
        at = job.finished_at or job.heartbeat_at or job.created_at
        for n in progress.get("needs_you_list") or []:
            kind = KINDS.get(str(n.get("outcome") or ""))
            if not (venue_id and n.get("invoice_id") and kind):
                continue
            out[(venue_id, str(n["invoice_id"]), kind)] = {
                "organization_id": job.organization_id,
                "venue_id": venue_id,
                "invoice_id": str(n["invoice_id"]),
                "kind": kind,
                "reference_number": n.get("reference"),
                "supplier_name": n.get("supplier"),
                "mode": progress.get("applied_mode") or params.get("mode"),
                "detail": {
                    "reasons": [str(r) for r in n.get("reasons") or [] if r],
                    "gates_needed": [],
                    "level": None,
                    "activity": {},
                    "from_job": job.id,
                },
                "created_at": job.created_at,
                "updated_at": at,
            }
    return list(out.values())


def main(dry_run: bool = False, days: int = 30) -> None:
    from app.db.engine import SessionLocal
    from app.db.models import InvoiceActivityEvent

    db = SessionLocal()
    try:
        events = plan(db, days)
        have = {
            (e.venue_id, e.invoice_id, e.kind)
            for e in db.query(
                InvoiceActivityEvent.venue_id,
                InvoiceActivityEvent.invoice_id,
                InvoiceActivityEvent.kind,
            ).all()
        }
        new = [
            e for e in events if (e["venue_id"], e["invoice_id"], e["kind"]) not in have
        ]
        for e in new:
            db.add(InvoiceActivityEvent(**e))
        if dry_run:
            db.rollback()
        else:
            db.commit()
        verb = "Would add" if dry_run else "Added"
        print(f"{verb} {len(new)} event(s); {len(events) - len(new)} already recorded.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    main(dry_run=args.dry_run, days=args.days)
