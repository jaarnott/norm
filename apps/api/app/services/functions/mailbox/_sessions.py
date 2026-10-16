"""Short sessions of their own, for work that must not ride the caller's
transaction (sync bookkeeping, token refreshes, "sync now").

In production that is a fresh ``SessionLocal()``. Under the test harness the
caller's session is bound to a Connection whose outer transaction the test
owns; there the caller's session stands in, with commits releasing a
SAVEPOINT and rollbacks returning to it — so a refused write in one step
never rolls back the test's (or the request's) other work.
"""

from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


class _Savepoint:
    def __init__(self, db: Session):
        self._db = db
        self._sp = db.begin_nested()

    def __getattr__(self, name):
        return getattr(self._db, name)

    def commit(self) -> None:
        self._db.flush()
        if self._sp.is_active:
            self._sp.commit()
        self._sp = self._db.begin_nested()

    def rollback(self) -> None:
        if self._sp.is_active:
            self._sp.rollback()
        self._sp = self._db.begin_nested()

    def close(self) -> None:
        if self._sp.is_active:
            self._sp.commit()


def independent_session(db: Session | None):
    """A session for one short unit of work (see the module docstring)."""
    if db is not None and isinstance(db.get_bind(), Connection):
        return _Savepoint(db)
    from app.db.engine import SessionLocal

    return SessionLocal()
