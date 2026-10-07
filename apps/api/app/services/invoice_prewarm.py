"""Warm the invoice-copy extraction cache before a human opens the invoice.

Opening an invoice runs the whole review inside the user's wait: 4-7 serial LLM
calls (production telemetry: avg 5.9s, p90 10.9s each) plus ~15-25 LoadedHub
round trips — 20-60s on a first open. Every open after that is instant, because
the review is cached. Nothing ever filled that cache ahead of the user.

This fills the LARGEST single piece of it: the PDF extraction, which is cached
separately in ``DocumentExtraction`` keyed on (venue, file_id, instructions).
That key is content-addressed, so a warmed extraction CANNOT go stale when the
invoice's lines change in Loaded — which is why this needs no adoption logic,
no fingerprint guards and no change to any read path. The interactive review
picks it up for free.

Three things this deliberately does NOT do:

- **It never creates a working document.** ``mcp/app_tools._receive_invoice``
  uses "does an open received_invoice draft exist" as proof a model was shown
  the invoice, "so a model that fabricates an invoice id it was never shown
  cannot receive it". Pre-minting drafts for every outstanding invoice would
  make that check vacuous for the whole venue.
- **It never writes to Loaded** and never starts a sensei study. It calls
  ``extract_invoice_copy`` and nothing else.
- **It is not parallel.** The batch path fans extraction out 10-wide to shorten
  a human's wait; nobody is waiting on a tick, so that trade — memory and DB
  connections for latency — is pure downside here. Peak concurrency is one
  extraction fleet-wide, which is strictly less than one person opening one
  invoice today. That is the answer to the Aug 12-13 OOM aborts: this never
  holds more than the interactive path already does.
"""

from __future__ import annotations

import datetime as _dt
import logging
import time

logger = logging.getLogger(__name__)

#: Real extractions per tick, across all venues. Cache hits do not count —
#: they cost one cheap Loaded call and must not consume the budget.
PREWARM_MAX_PER_TICK = 6
#: Checked BETWEEN invoices, never mid-call. At ~10s an extraction a full tick
#: is ~60s against a */5 cadence, so ticks can never stack.
PREWARM_DEADLINE_SECONDS = 180
#: Older than this and nobody is about to open it. Also what stops the first
#: tick working through a year of stuck invoices.
PREWARM_MAX_AGE_DAYS = 21
#: Two tries, then terminal. The backoff is per-invoice, not global.
PREWARM_MAX_ATTEMPTS = 2
PREWARM_BACKOFF_SECONDS = (600, 3600)
#: One fixed key: this is a fleet-wide single-flight, not a per-venue one.
_LOCK_KEY = 0x6E70_7277  # "nprw"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _loadedhub_venue_ids(db) -> list[str]:
    """Venues with a live LoadedHub connection — the same rows
    ``LoadedInvoiceClient.__init__`` requires, so a selected venue can never
    fail to build a client."""
    from app.db.models import Connection

    rows = (
        db.query(Connection.venue_id)
        .filter(Connection.connector_name == "loadedhub")
        .distinct()
        .all()
    )
    return [r[0] for r in rows if r[0]]


def _skip_ids(db, venue_id: str) -> set[str]:
    """Invoices this venue should not be offered: already warmed, or failed
    and still inside their backoff (or terminal)."""
    from app.db.models import InvoicePrewarmAttempt

    now = _now()
    out: set[str] = set()
    for row in (
        db.query(InvoicePrewarmAttempt)
        .filter(InvoicePrewarmAttempt.venue_id == venue_id)
        .all()
    ):
        if row.warmed_at is not None:
            out.add(row.invoice_id)
        elif row.next_attempt_at is None and row.attempts:
            out.add(row.invoice_id)  # terminal
        elif row.next_attempt_at is not None and row.next_attempt_at > now:
            out.add(row.invoice_id)  # still backing off
    return out


def _candidates(db, config_db, venue_id: str) -> list[str]:
    """Outstanding invoice ids worth warming, newest first.

    One Loaded list call and one local query — no invoice detail is fetched
    here, so a venue with 200 outstanding invoices costs the same as one with
    two.
    """
    from app.services.received_invoice import (
        LoadedInvoiceClient,
        outstanding_invoice_rows,
    )

    try:
        lh = LoadedInvoiceClient(db, config_db, venue_id)
        rows = outstanding_invoice_rows(lh)
    except Exception as exc:  # noqa: BLE001 — one bad venue must not end the tick
        logger.info("prewarm: venue=%s list failed: %s", venue_id, exc)
        return []

    cutoff = _now() - _dt.timedelta(days=PREWARM_MAX_AGE_DAYS)
    skip = _skip_ids(db, venue_id)
    dated: list[tuple[str, str]] = []
    for r in rows:
        iid = str(r.get("id") or "")
        if not iid or iid in skip:
            continue
        stamp = str(r.get("createdAt") or r.get("issuedAt") or "")[:10]
        if stamp:
            try:
                when = _dt.datetime.fromisoformat(stamp).replace(
                    tzinfo=_dt.timezone.utc
                )
                if when < cutoff:
                    continue
            except ValueError:
                pass  # unparseable date is not a reason to skip a live invoice
        dated.append((stamp, iid))
    dated.sort(reverse=True)  # newest first: what the dashboard shows on top
    return [iid for _, iid in dated]


def _record_failure(db, venue_id: str, invoice_id: str, error: str) -> None:
    from app.db.models import InvoicePrewarmAttempt

    row = (
        db.query(InvoicePrewarmAttempt)
        .filter(
            InvoicePrewarmAttempt.venue_id == venue_id,
            InvoicePrewarmAttempt.invoice_id == invoice_id,
        )
        .first()
    )
    if row is None:
        row = InvoicePrewarmAttempt(venue_id=venue_id, invoice_id=invoice_id)
        db.add(row)
    row.attempts = (row.attempts or 0) + 1
    row.last_error = str(error)[:500]
    if row.attempts >= PREWARM_MAX_ATTEMPTS:
        row.next_attempt_at = None  # terminal — stop offering it forever
    else:
        wait = PREWARM_BACKOFF_SECONDS[
            min(row.attempts - 1, len(PREWARM_BACKOFF_SECONDS) - 1)
        ]
        row.next_attempt_at = _now() + _dt.timedelta(seconds=wait)
    db.commit()


def _record_warmed(db, venue_id: str, invoice_id: str) -> None:
    from app.db.models import InvoicePrewarmAttempt

    row = (
        db.query(InvoicePrewarmAttempt)
        .filter(
            InvoicePrewarmAttempt.venue_id == venue_id,
            InvoicePrewarmAttempt.invoice_id == invoice_id,
        )
        .first()
    )
    if row is None:
        row = InvoicePrewarmAttempt(venue_id=venue_id, invoice_id=invoice_id)
        db.add(row)
    row.warmed_at = _now()
    row.next_attempt_at = None
    row.last_error = None
    db.commit()


def warm_one(venue_id: str, invoice_id: str) -> str:
    """Warm one invoice's extraction. Returns "warmed" | "cached" | "failed".

    Runs as the venue's organisation (caller_scope), so the extraction's cost
    counts to it — a background job has no thread or user to say whose it is.
    """
    from app.db.engine import SessionLocal
    from app.services import caller_scope

    scope_db = SessionLocal()
    try:
        scope = caller_scope.for_venue(scope_db, venue_id)
    finally:
        scope_db.close()
    with caller_scope.use(scope):
        return _warm_one(venue_id, invoice_id)


def _warm_one(venue_id: str, invoice_id: str) -> str:
    """Warm one invoice's extraction. Returns "warmed" | "cached" | "failed".

    Owns its sessions and closes them: never hold a session across a tick.
    The instructions are composed exactly as ``review_invoice`` composes them,
    because a cache key that differs by one character warms nothing.
    """
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.services.invoice_extraction import extract_invoice_copy
    from app.services.invoice_review import (
        account_suppliers,
        extraction_instructions,
        supplier_aliases,
    )
    from app.services.received_invoice import LoadedInvoiceClient

    db = SessionLocal()
    config_db = _ConfigSessionLocal()
    try:
        lh = LoadedInvoiceClient(db, config_db, venue_id)
        detail = lh.invoice(invoice_id)
        if not isinstance(detail, dict):
            _record_failure(db, venue_id, invoice_id, "no invoice detail")
            return "failed"
        file_id = detail.get("fileId")
        if not file_id:
            # No copy attached is a real, permanent state — not a transient
            # failure. Recording it stops the invoice being offered forever.
            _record_failure(db, venue_id, invoice_id, "no file attached")
            return "failed"

        suppliers = account_suppliers(lh)
        aliases = supplier_aliases(lh, detail, suppliers)
        instructions = extraction_instructions(config_db, lh, detail, aliases)

        from app.services.invoice_extraction import _cache_key, _extraction_cache_get

        if _extraction_cache_get(db, _cache_key(venue_id, file_id, instructions)):
            _record_warmed(db, venue_id, invoice_id)
            return "cached"

        result = extract_invoice_copy(
            db, lh, file_id, instructions=instructions, venue_key=venue_id
        )
        # extract_invoice_copy never raises: an unreadable document comes back
        # as {"error": ...} and must never be recorded as a success.
        if not isinstance(result, dict) or result.get("error"):
            err = result.get("error") if isinstance(result, dict) else "no extraction"
            _record_failure(db, venue_id, invoice_id, str(err))
            return "failed"
        _record_warmed(db, venue_id, invoice_id)
        return "warmed"
    except Exception as exc:  # noqa: BLE001 — one invoice must not end the tick
        try:
            _record_failure(db, venue_id, invoice_id, str(exc))
        except Exception:  # noqa: BLE001
            pass
        return "failed"
    finally:
        db.close()
        config_db.close()


def prewarm_extractions(*, venue_id: str | None = None) -> dict:
    """One bounded pass. Safe to call concurrently — only one runs.

    Single-flight is a Postgres session-level advisory lock rather than a
    table: four gunicorn workers times up to three instances would otherwise
    each start a tick, and each extraction is a paid LLM call.
    """
    from sqlalchemy import text

    from app.db.engine import SessionLocal, _ConfigSessionLocal

    started = time.monotonic()
    lock_db = SessionLocal()
    try:
        got = lock_db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
        ).scalar()
        if not got:
            logger.info("prewarm: another tick holds the lock — skipping")
            return {"locked": True, "warmed": 0, "failed": 0}

        db = SessionLocal()
        config_db = _ConfigSessionLocal()
        try:
            venues = [venue_id] if venue_id else _loadedhub_venue_ids(db)
            per_venue = {v: _candidates(db, config_db, v) for v in venues}
            candidates = sum(len(c) for c in per_venue.values())
        finally:
            db.close()
            config_db.close()

        # Round-robin so one venue's backlog cannot starve another.
        queue: list[tuple[str, str]] = []
        for i in range(max((len(c) for c in per_venue.values()), default=0)):
            for v, ids in per_venue.items():
                if i < len(ids):
                    queue.append((v, ids[i]))

        warmed = failed = cached = 0
        for v, iid in queue:
            if warmed >= PREWARM_MAX_PER_TICK:
                break
            if time.monotonic() - started >= PREWARM_DEADLINE_SECONDS:
                logger.info("prewarm: deadline reached")
                break
            outcome = warm_one(v, iid)
            if outcome == "warmed":
                warmed += 1
            elif outcome == "cached":
                cached += 1
            else:
                failed += 1

        out = {
            "locked": False,
            "venues": len(venues),
            "candidates": candidates,
            "warmed": warmed,
            "cached": cached,
            "failed": failed,
            "seconds": round(time.monotonic() - started, 1),
        }
        logger.info(
            "prewarm: venues=%s candidates=%s warmed=%s cached=%s failed=%s in %ss",
            out["venues"],
            out["candidates"],
            warmed,
            cached,
            failed,
            out["seconds"],
        )
        return out
    finally:
        try:
            lock_db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            lock_db.commit()
        except Exception:  # noqa: BLE001 — the lock dies with the connection
            pass
        lock_db.close()
