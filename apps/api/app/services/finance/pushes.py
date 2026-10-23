"""The Loaded budget ledger (``fin_budget_pushes``) and Loaded's budget as
it stood each night (``fin_loaded_budget_log``) — plan §7.1 item 7, §8.

Every day Norm writes into Loaded leaves a row: before, after, PUT or POST,
what asked for it (``source``), who pressed, and Loaded's answer. The
consolidators ``loadedhub.set_budgets`` / ``set_budgets_typed`` call the
engine-only built-in ``norm_finance.record_budget_push`` after their writes;
``record`` here is what that built-in writes. The ``source`` is never a
parameter a model could set: the built-in derives it from which row ran and
whether a thread is present, and the one page action that is neither — the
Finance router's "restore Loaded budgets" — marks itself with ``restoring``
(a context variable set at the door, as ``caller_scope`` is).

The log is appended only when a day's amount changed, so "the budget as it
stood 28 days before" — the human benchmark the scorer measures the forecast
against — is the latest row captured on or before that day.

Restore = the before values, handed back as the ``days`` a ``set_budgets``
push takes; a day Norm *created* cannot be restored (Loaded has no DELETE,
06 §2.2), so it is listed instead.
"""

from __future__ import annotations

import contextvars
import datetime as dt
from contextlib import contextmanager

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.finance_models import (
    BUDGET_PUSH_SOURCES,
    FinBudgetPush,
    FinLoadedBudgetLog,
)
from app.db.models import User
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid

#: Set by the Finance router around a "restore Loaded budgets" push, read by
#: the ledger built-in. A door-level fact, never a tool parameter.
_RESTORING: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "finance_restoring", default=False
)


@contextmanager
def restoring():
    token = _RESTORING.set(True)
    try:
        yield
    finally:
        _RESTORING.reset(token)


def is_restoring() -> bool:
    return _RESTORING.get()


def source_for(tool: str, *, has_thread: bool) -> str:
    """Which row ran, and from where, decides the ledger's ``source``
    (build-notes, 20 Oct): the typed twin → ``manual``; ``set_budgets`` from
    a conversation → ``chat``; from the page helper (no thread) → ``forecast``,
    or ``restore`` when the restore route is the caller."""
    if is_restoring():
        return "restore"
    if str(tool).rsplit(".", 1)[-1] == "set_budgets_typed":
        return "manual"
    return "chat" if has_thread else "forecast"


def _cents(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def record(
    db: Session,
    org_id: str,
    *,
    venue_id: str,
    source: str,
    pushed_by: str | None,
    days: list[dict],
    tool: str | None = None,
    vintage_id: str | None = None,
) -> list[FinBudgetPush]:
    """One ledger row per day actually sent (``update`` → PUT, ``create`` →
    POST); unchanged and skipped days leave no row. Amounts arrive as
    dollars (the consolidator's basis) and are stored as cents."""
    if source not in BUDGET_PUSH_SOURCES:
        raise Invalid(f"source is one of {', '.join(BUDGET_PUSH_SOURCES)}.")
    rows = []
    for d in days or []:
        if not isinstance(d, dict):
            continue
        action = str(d.get("action") or "")
        if action not in ("update", "create"):
            continue
        try:
            date = dt.date.fromisoformat(str(d.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        after = _cents(d.get("after"))
        if after is None:
            continue
        row = FinBudgetPush(
            organization_id=org_id,
            venue_id=venue_id,
            date=date,
            before_cents=_cents(d.get("before")),
            after_cents=after,
            loaded_id=str(d["loaded_id"]) if d.get("loaded_id") else None,
            method="PUT" if action == "update" else "POST",
            source=source,
            pushed_by=pushed_by,
            ok=bool(d.get("ok", True)),
            result={
                "tool": tool,
                "error": d.get("error"),
                "vintage_id": vintage_id,
            },
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def ledger(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str | None = None,
    first: dt.date | None = None,
    last: dt.date | None = None,
    limit: int = 200,
) -> list[dict]:
    ctx.require("finance:read")
    q = db.query(FinBudgetPush).filter(FinBudgetPush.organization_id == ctx.org_id)
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(FinBudgetPush.venue_id == venue_id)
    else:
        q = q.filter(ctx.venue_clause(FinBudgetPush.venue_id))
    if first:
        q = q.filter(FinBudgetPush.date >= first)
    if last:
        q = q.filter(FinBudgetPush.date <= last)
    rows = (
        q.order_by(FinBudgetPush.pushed_at.desc())
        .limit(max(1, min(int(limit), 2000)))
        .all()
    )
    names = _names(db, {r.pushed_by for r in rows if r.pushed_by})
    return [
        {
            "id": r.id,
            "venue_id": r.venue_id,
            "date": r.date.isoformat(),
            "before_cents": r.before_cents,
            "after_cents": r.after_cents,
            "method": r.method,
            "source": r.source,
            "pushed_by": r.pushed_by,
            "pushed_by_name": names.get(r.pushed_by),
            "pushed_at": r.pushed_at.isoformat() if r.pushed_at else None,
            "ok": r.ok,
            "loaded_id": r.loaded_id,
            "result": r.result,
        }
        for r in rows
    ]


def _names(db: Session, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {
        u.id: (u.full_name or u.email)
        for u in db.query(User).filter(User.id.in_(list(ids)))
    }


def restore_plan(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    first: dt.date,
    last: dt.date,
    *,
    scope: str = "latest",
) -> dict:
    """The ``days`` a ``set_budgets`` push takes to put Loaded back: per day
    the before value of the latest push (``latest`` — undo the last change)
    or of the first one (``first`` — back to before Norm ever touched it).
    A day whose before is empty was created by Norm and cannot be restored."""
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    if scope not in ("latest", "first"):
        raise Invalid("scope is 'latest' or 'first'.")
    rows = (
        db.query(FinBudgetPush)
        .filter(
            FinBudgetPush.organization_id == ctx.org_id,
            FinBudgetPush.venue_id == venue_id,
            FinBudgetPush.date >= first,
            FinBudgetPush.date <= last,
            FinBudgetPush.ok.is_(True),
        )
        .order_by(FinBudgetPush.date, FinBudgetPush.pushed_at)
        .all()
    )
    chosen: dict[dt.date, FinBudgetPush] = {}
    for r in rows:
        if scope == "latest" or r.date not in chosen:
            chosen[r.date] = r
    days, cannot = [], []
    for date in sorted(chosen):
        r = chosen[date]
        if r.before_cents is None:
            cannot.append(
                f"{date.isoformat()} (created by Norm — Loaded has no delete)"
            )
            continue
        days.append(
            {
                "date": date.isoformat(),
                "amount_cents": r.before_cents,
                "amount": round(r.before_cents / 100, 2),
                "current_cents": r.after_cents,
            }
        )
    return {
        "venue_id": venue_id,
        "scope": scope,
        "days": days,
        "cannot_restore": cannot,
    }


# ---------------------------------------------------------------------------
# Loaded's budget, as it stood
# ---------------------------------------------------------------------------


def append_loaded_log(
    db: Session,
    org_id: str,
    venue_id: str,
    captured_on: dt.date,
    days: dict[dt.date, tuple[int, str | None]],
) -> int:
    """Append ``{date: (amount_cents, loaded_id)}`` captured today, only for
    days whose amount differs from the latest earlier capture. Returns the
    rows added."""
    if not days:
        return 0
    first, last = min(days), max(days)
    latest: dict[dt.date, FinLoadedBudgetLog] = {}
    for r in (
        db.query(FinLoadedBudgetLog)
        .filter(
            FinLoadedBudgetLog.organization_id == org_id,
            FinLoadedBudgetLog.venue_id == venue_id,
            FinLoadedBudgetLog.date >= first,
            FinLoadedBudgetLog.date <= last,
            FinLoadedBudgetLog.captured_on <= captured_on,
        )
        .order_by(FinLoadedBudgetLog.date, FinLoadedBudgetLog.captured_on)
    ):
        latest[r.date] = r
    added = 0
    for date, (cents, loaded_id) in sorted(days.items()):
        prev = latest.get(date)
        if prev is not None and prev.amount_cents == int(cents):
            continue
        if prev is not None and prev.captured_on == captured_on:
            prev.amount_cents = int(cents)  # the same day's capture, corrected
            prev.loaded_id = loaded_id
            continue
        db.add(
            FinLoadedBudgetLog(
                organization_id=org_id,
                venue_id=venue_id,
                date=date,
                amount_cents=int(cents),
                loaded_id=loaded_id,
                captured_on=captured_on,
            )
        )
        added += 1
    db.flush()
    return added


def loaded_as_at(
    db: Session,
    org_id: str,
    venue_id: str,
    first: dt.date,
    last: dt.date,
    *,
    as_of: dt.date | None = None,
) -> dict[dt.date, int]:
    """``{date: amount_cents}`` — Loaded's budget for each day as it stood on
    ``as_of`` (the latest capture on or before it), or the latest known."""
    q = db.query(FinLoadedBudgetLog).filter(
        FinLoadedBudgetLog.organization_id == org_id,
        FinLoadedBudgetLog.venue_id == venue_id,
        FinLoadedBudgetLog.date >= first,
        FinLoadedBudgetLog.date <= last,
    )
    if as_of is not None:
        q = q.filter(FinLoadedBudgetLog.captured_on <= as_of)
    out: dict[dt.date, int] = {}
    for r in q.order_by(FinLoadedBudgetLog.date, FinLoadedBudgetLog.captured_on):
        out[r.date] = int(r.amount_cents)
    return out


def loaded_as_at_offset(
    db: Session,
    org_id: str,
    venue_id: str,
    first: dt.date,
    last: dt.date,
    *,
    days_before: int,
) -> dict[dt.date, int]:
    """Per day, the budget as it stood ``days_before`` days earlier — the
    scorer's benchmark (28). One query, then the pick per day."""
    rows = (
        db.query(FinLoadedBudgetLog)
        .filter(
            FinLoadedBudgetLog.organization_id == org_id,
            FinLoadedBudgetLog.venue_id == venue_id,
            FinLoadedBudgetLog.date >= first,
            FinLoadedBudgetLog.date <= last,
        )
        .order_by(FinLoadedBudgetLog.date, FinLoadedBudgetLog.captured_on)
        .all()
    )
    out: dict[dt.date, int] = {}
    for r in rows:
        if r.captured_on <= r.date - dt.timedelta(days=days_before):
            out[r.date] = int(r.amount_cents)
    return out


def log_coverage(db: Session, org_id: str, venue_id: str) -> dict:
    first, last, n = (
        db.query(
            func.min(FinLoadedBudgetLog.captured_on),
            func.max(FinLoadedBudgetLog.captured_on),
            func.count(FinLoadedBudgetLog.id),
        )
        .filter(
            FinLoadedBudgetLog.organization_id == org_id,
            FinLoadedBudgetLog.venue_id == venue_id,
        )
        .one()
    )
    return {
        "first_capture": first.isoformat() if first else None,
        "last_capture": last.isoformat() if last else None,
        "rows": int(n or 0),
    }
