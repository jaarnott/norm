"""Clear scheduled runs that have been waiting for an approval nobody gave.

Approvals stage 5 (Oct 2026). Before it, a scheduled run that reached a write
which asks suspended silently and was recorded as "success": in production,
20 such runs sat waiting for weeks (2 Oct 2026), their cards copied into task
conversations nobody opened. New runs now say they are waiting, email their
owner, and replace any earlier waiting run — but the old ones predate that and
are recorded as "success", so nothing will ever replace them.

For each scheduled-run thread still ``awaiting_tool_approval`` and older than
``--older-than-days``: its pending changes become "superseded" (nothing is
written anywhere), its card — and the copy in the task conversation — says
"Not done — it waited too long", the thread is closed, and its run is marked
``superseded``. Chat threads waiting on their owner are left alone: replying
in them already does this.

Read-only unless ``--apply``. Point DATABASE_URL at the database to clean;
production needs the write login (DATABASE_URL_DIRECT) and the user's
go-ahead.

Usage (from apps/api):
    uv run python scripts/settle_stuck_task_runs.py               # list only
    uv run python scripts/settle_stuck_task_runs.py --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

NOTE = (
    "_Not done — this run waited for an approval that never came, so Norm "
    "cleared it and changed nothing._"
)


def stuck(db, older_than_days: int):
    from app.db.models import AutomatedTask, AutomatedTaskRun, Thread
    from app.services import approvals

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)
    threads = (
        db.query(Thread)
        .filter(
            Thread.status == approvals.AWAITING,
            Thread.intent.like("%.automated_task"),
            Thread.created_at < cutoff,
        )
        .order_by(Thread.created_at)
        .all()
    )
    out = []
    for thread in threads:
        run = (
            db.query(AutomatedTaskRun)
            .filter(AutomatedTaskRun.thread_id == thread.id)
            .first()
        )
        task = (
            db.query(AutomatedTask)
            .filter(AutomatedTask.id == run.automated_task_id)
            .first()
            if run
            else None
        )
        out.append((thread, run, task))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--older-than-days", type=int, default=2)
    args = parser.parse_args()

    from app.db.engine import SessionLocal
    from app.db.models import Thread
    from app.services import approvals

    db = SessionLocal()
    try:
        rows = stuck(db, args.older_than_days)
        for thread, run, task in rows:
            print(
                f"{thread.created_at:%Y-%m-%d}  "
                f"{(task.title if task else '(no task)')[:50]:50}  "
                f"run={run.status if run else '-':8}  "
                f"pending={len(thread.pending_tool_call_ids or [])}  "
                f"thread={thread.id[:8]}"
            )
        chats = (
            db.query(Thread)
            .filter(
                Thread.status == approvals.AWAITING,
                ~Thread.intent.like("%.automated_task"),
            )
            .count()
        )
        print(
            f"\n{len(rows)} scheduled run(s) to clear; {chats} chat thread(s) "
            "waiting on their owner (left alone)."
        )
        if not args.apply:
            print("Nothing changed — pass --apply to clear them.")
            return
        for thread, run, _task in rows:
            approvals.supersede(
                db,
                thread,
                None,
                note=NOTE,
                card_note="it waited too long",
                status_after="completed",
            )
            if run is not None:
                run.status = "superseded"
        db.commit()
        print(f"Cleared {len(rows)}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
