"""The Finance tick: run every module's due jobs, each in its own session,
each wrapped so one failure never stops the next.

The coordinator wires ``run_due_jobs`` into ``POST /internal/run-due-tasks``
on its own thread (the ``app_schedules`` pattern), so it rides the existing
five-minute Cloud Scheduler job — no new scheduler job per environment. Each
module decides for itself what is due at ``now`` and claims each run through
``jobs.claim`` before working, so overlapping ticks and several API instances
never repeat a run.

The hooks are imported lazily: the modules land in their own waves, and a
module that does not exist yet is skipped with a debug line rather than
breaking the tick for the ones that do.
"""

from __future__ import annotations

import datetime as dt
import importlib
import logging

from app.db.models import _now

logger = logging.getLogger(__name__)

#: Each module's ``run_due(db, now) -> dict`` hook, in the order they run.
HOOKS = (
    "app.services.payroll.jobs",
    "app.services.entertainers.jobs",
    "app.services.finance.jobs",
    "app.services.finance_core.importer_jobs",
)


def _hook(path: str):
    """The module at ``path``, or None when it (or a package above it) does
    not exist yet. A missing import INSIDE an existing module is a bug and
    raises."""
    try:
        return importlib.import_module(path)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing and (path == missing or path.startswith(missing + ".")):
            logger.debug("finance tick: %s not built yet — skipped", path)
            return None
        raise


def run_due_jobs(now: dt.datetime | None = None, *, session_factory=None) -> dict:
    """Run every module's due jobs once. Returns what each hook reported
    (or its error), keyed by hook path."""
    now = now or _now()
    if session_factory is None:
        from app.db.engine import SessionLocal

        session_factory = SessionLocal
    ran: dict[str, dict] = {}
    for path in HOOKS:
        mod = _hook(path)
        if mod is None:
            continue
        run_due = getattr(mod, "run_due", None)
        if run_due is None:
            logger.warning("finance tick: %s has no run_due(db, now)", path)
            continue
        db = session_factory()
        try:
            out = run_due(db, now)
            db.commit()
            ran[path] = out if isinstance(out, dict) else {"result": out}
        except Exception as exc:  # noqa: BLE001 — one module's failure never stops the next
            logger.exception("finance tick: %s failed", path)
            db.rollback()
            ran[path] = {"error": str(exc)[:500]}
        finally:
            db.close()
    return {"ran": ran}
