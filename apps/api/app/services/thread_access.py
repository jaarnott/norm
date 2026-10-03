"""Who may open — and act in — a conversation thread.

A thread belongs to the person who started it. Until Oct 2026 the thread
routes checked only that a caller was signed in, so anyone holding a thread id
could read another person's conversation — another organisation's included —
continue it, or approve its pending writes.

The rule:

- **The owner** may open and act in their thread.
- **A platform admin** may open and act in any thread (the admin panel and
  support rely on this).
- **A scheduled task's threads** — its conversation, and each run's own thread —
  are shared with the task creator's organisation, because the task board shows
  the task to the whole team.

Anyone else gets "not found", never "forbidden", so a thread id can't be probed.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Thread, User

_TASK_SUFFIXES = (".automated_conversation", ".automated_task")


def _org_of(db: Session, user_id: str | None) -> str | None:
    if not user_id:
        return None
    from app.db.models import OrganizationMembership

    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user_id)
        .first()
    )
    return membership.organization_id if membership else None


def _task_creator(db: Session, thread: Thread) -> str | None:
    """The creator of the scheduled task this thread belongs to, if any."""
    if not (thread.intent or "").endswith(_TASK_SUFFIXES):
        return None
    from app.db.models import AutomatedTask, AutomatedTaskRun

    task = (
        db.query(AutomatedTask)
        .filter(AutomatedTask.conversation_thread_id == thread.id)
        .first()
    )
    if task is None:
        run = (
            db.query(AutomatedTaskRun)
            .filter(AutomatedTaskRun.thread_id == thread.id)
            .first()
        )
        if run is not None:
            task = (
                db.query(AutomatedTask)
                .filter(AutomatedTask.id == run.automated_task_id)
                .first()
            )
    return task.created_by if task else None


def can_access(db: Session, thread: Thread, user: User) -> bool:
    if getattr(user, "role", None) == "admin":
        return True
    if thread.user_id and thread.user_id == user.id:
        return True
    creator = _task_creator(db, thread)
    if creator:
        if creator == user.id:
            return True
        mine = _org_of(db, user.id)
        return mine is not None and mine == _org_of(db, creator)
    return False


def can_manage_task(db: Session, task, user: User | None) -> bool:
    """A scheduled task is its creator's organisation's: the same people who
    may open its threads may change it."""
    if user is None:
        return False
    if getattr(user, "role", None) == "admin" or task.created_by == user.id:
        return True
    mine = _org_of(db, user.id)
    return mine is not None and mine == _org_of(db, task.created_by)


def thread_for(db: Session, thread_id: str | None, user: User) -> Thread | None:
    """The thread if ``user`` may open it, else None (missing or not theirs)."""
    if not thread_id:
        return None
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if thread is None or not can_access(db, thread, user):
        return None
    return thread
