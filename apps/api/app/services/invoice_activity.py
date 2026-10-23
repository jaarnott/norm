"""The invoice activity page: what Norm did with each invoice (owner, 19 Oct 2026).

It replaces the Dojo. The Dojo loaded every venue's outstanding invoices from
Loaded and let a person review them by hand; Norm now handles invoices itself,
so the page shows what happened instead, with the things that might be worth
checking highlighted. It reads only Norm's own records, never Loaded.

Where each invoice comes from:

- **Received or deleted** — ``invoice_autopilot_outcomes``, one row per
  invoice, written by ``record_receive_outcome`` for Norm and for people. Since
  19 Oct each row also carries ``detail.activity`` (what Norm did along the way,
  ``activity_from_doc``) and ``detail.level`` (the receiving level in force).
  Older rows carry neither; the page shows what they do have.
- **Stopped, waiting or failed** — ``invoice_activity_events``, written by the
  batch review (``record_stop``) for an invoice that neither went in nor was
  deleted. A later receive or delete supersedes it.
- **Units to look at** — the daily unit check's ``unit_check_proposals``.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections import Counter

logger = logging.getLogger(__name__)

#: Deletions that are routine: the same invoice sent twice, and statements.
ROUTINE_DELETES = frozenset({"duplicate", "statement"})

#: How a line was matched to a stock item, in words.
MATCHED_BY = {
    "supplier_code": "supplier code",
    "code": "code",
    "description_exact": "name",
    "description_substring": "part of the name",
    "llm": "Norm's model",
}

#: Where a line's unit came from, in words. ``unit_by`` (set when the review
#: finished the replica) wins over ``unit_source`` (set when it was built).
UNIT_FROM = {
    "copy": "the invoice",
    "printed": "the invoice",
    "catalogue": "Norm's catalogue",
    "estimate": "Norm's estimate",
    "resolver": "Norm's estimate",
}

#: Where the invoice was received, from the outcome row's ``mode``.
WHERE = {
    "autopilot": "norm",
    "approve_fixes": "norm",
    "interactive": "app",
    "mcp_card": "claude",
}

CREATE_KINDS = {
    "create_supplier": "suppliers",
    "create_item": "stock_items",
    "create_unit": "units",
    "create_brand": "brands",
}

TOTALS_MARK = "with every other suggestion applied"


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def level_of(settings: dict | None) -> dict | None:
    """The receiving level and the switches that were on."""
    if not isinstance(settings, dict) or not settings.get("mode"):
        return None
    from app.services import venue_autopilot as VA

    return {
        "mode": settings.get("mode"),
        "switches_on": sorted(g for g in VA.GATES if settings.get(g) is True),
    }


def level_for_user(session, user_id: str | None) -> dict | None:
    """The level a person's own receive ran under (their Preferences)."""
    if not user_id:
        return None
    try:
        from app.db.models import User
        from app.services.approvals import receiving_settings

        user = session.query(User).filter(User.id == user_id).first()
        return level_of(receiving_settings(user)) if user is not None else None
    except Exception as exc:  # noqa: BLE001 — a label, never an error
        logger.info("level for user %s unavailable: %s", user_id, exc)
        return None


def activity_from_doc(data: dict | None) -> dict:
    """What Norm did with one invoice, from its review: the layout that read
    it, how its lines matched, where its units came from, whether the model
    worked out the totals, and what was created. Small, and never raises."""
    try:
        return _activity(data if isinstance(data, dict) else {})
    except Exception as exc:  # noqa: BLE001 — a summary, never an error
        logger.info("activity summary failed: %s", exc)
        return {}


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _activity(data: dict) -> dict:
    from app.services.replica_finish import TOTALS_ISSUE

    rep = data.get("replica") if isinstance(data.get("replica"), dict) else {}
    process = data.get("process") if isinstance(data.get("process"), dict) else {}
    lines = _dicts(rep.get("lines"))

    matched = Counter()
    new_items = 0
    for ln in lines:
        if ln.get("linked_item_id"):
            matched[MATCHED_BY.get(ln.get("matched_by"), "other")] += 1
        else:
            new_items += 1
    units = Counter()
    for ln in lines:
        src = ln.get("unit_by") or ln.get("unit_source")
        if src and (ln.get("linked_unit_id") or ln.get("unit")):
            units[UNIT_FROM.get(src, "other")] += 1

    suggestions = _dicts(data.get("suggestions"))
    entries: dict[str, dict] = {}
    for a in _dicts(data.get("suggestion_actions")):
        if a.get("suggestion_id"):
            entries[str(a["suggestion_id"])] = a
    made = data.get("autopilot_created")
    made = made if isinstance(made, dict) else {}
    created: dict[str, list[str]] = {}

    def _add(bucket: str, name: object) -> None:
        if name and str(name) not in created.get(bucket, []):
            created.setdefault(bucket, []).append(str(name))

    # What was made NEW in Loaded, each name once. Norm's own creates are in
    # autopilot_created, which leaves out a get-or-create that found the
    # record; a person's create says so on its accept (after.created).
    for s in suggestions:
        bucket = CREATE_KINDS.get(str(s.get("kind")))
        entry = entries.get(str(s.get("id"))) or {}
        if not bucket or entry.get("action") != "accepted" or not s.get("proposed"):
            continue
        if entry.get("by") == "norm" and made:
            continue
        after = entry.get("after") if isinstance(entry.get("after"), dict) else {}
        if after.get("created") is False:
            continue
        _add(bucket, s["proposed"])
    for bucket, names in made.items():
        for name in names if isinstance(names, list) else []:
            _add(bucket, name)

    issues = _dicts(data.get("issues"))
    # The model's work-out (invoice_review: work_out_totals) explains every
    # change it makes the same way.
    if any(
        str(s.get("explanation") or "").startswith(TOTALS_MARK) for s in suggestions
    ):
        totals = "worked out"
    elif any(i.get("code") == TOTALS_ISSUE and i.get("blocking") for i in issues):
        # The blocker stays on the card and clears once the working values
        # balance. Norm's own corrections often get them there: Loaded's draft
        # put 10% on every GST-free line, and 30 of Grange Gully Bistro's
        # invoices read "didn't add up" though each was received exactly as
        # its copy (23 Oct 2026).
        from app.services.invoice_review import totals_balance

        totals = "worked out" if totals_balance(data) else "didn't add up"
    else:
        totals = "added up"

    out: dict = {
        "total": data.get("total"),
        "layout": process.get("layout"),
        "reread": bool(process.get("reread")),
        "studied": bool(process.get("studied")),
        "lines": len(lines),
        "matched": dict(matched),
        "new_items": new_items,
        "units": dict(units),
        "totals": totals,
        "created": created,
        "feed": not data.get("file_id"),
    }
    # Kept small (it rides on every row): what is empty or off is left out.
    return {k: v for k, v in out.items() if v is not False and v not in (None, {}, [])}


def record_stop(
    *,
    venue_id: str,
    invoice_id: str,
    kind: str,
    data: dict | None,
    mode: str | None,
    reasons: list | None = None,
    gates_needed: list | None = None,
    level: dict | None = None,
    record: dict | None = None,
) -> None:
    """Note an invoice Norm handled that didn't go in or get deleted. Its own
    session, never raises: the batch must not fail on a note."""
    try:
        from app.db.engine import SessionLocal
        from app.db.models import InvoiceActivityEvent, Venue

        if not venue_id or not invoice_id:
            return
        data = data if isinstance(data, dict) else {}
        record = record if isinstance(record, dict) else {}
        detail = {
            "reasons": [str(r) for r in reasons or [] if r][:12],
            "gates_needed": list(gates_needed or []),
            "level": level,
            "activity": activity_from_doc(data) if data else {},
        }
        total = data.get("total") if data else record.get("total")
        session = SessionLocal()
        try:
            row = (
                session.query(InvoiceActivityEvent)
                .filter(
                    InvoiceActivityEvent.venue_id == venue_id,
                    InvoiceActivityEvent.invoice_id == invoice_id,
                    InvoiceActivityEvent.kind == kind,
                )
                .first()
            )
            if row is None:
                venue = session.query(Venue).filter(Venue.id == venue_id).first()
                row = InvoiceActivityEvent(
                    organization_id=getattr(venue, "organization_id", None),
                    venue_id=venue_id,
                    invoice_id=invoice_id,
                    kind=kind,
                )
                session.add(row)
            row.reference_number = data.get("reference_number") or record.get(
                "reference_number"
            )
            row.supplier_name = data.get("supplier_name") or record.get("supplier_name")
            row.total = total if isinstance(total, (int, float)) else None
            row.mode = mode
            # What earlier runs that stopped created is still made: kept on
            # the stop — whatever kind the invoice's earlier stops were —
            # until a receive or delete settles it and keeps it on its own
            # row (settle_events). A run that creates usually comes first;
            # the next nights only stop again.
            now = _dt.datetime.now(_dt.timezone.utc)
            for other in (
                session.query(InvoiceActivityEvent)
                .filter(
                    InvoiceActivityEvent.venue_id == venue_id,
                    InvoiceActivityEvent.invoice_id == invoice_id,
                )
                .all()
            ):
                od = other.detail if isinstance(other.detail, dict) else {}
                if not od.get("settled_at"):
                    detail["activity"]["created"] = merge_created(
                        detail["activity"].get("created"),
                        (od.get("activity") or {}).get("created"),
                    )
            if not detail["activity"].get("created"):
                detail["activity"].pop("created", None)
            old = row.detail if isinstance(row.detail, dict) else {}
            if old.get("settled_at"):
                # The invoice came back after it was settled: a new stop,
                # not the old one carrying on through the receive.
                row.created_at = now
            row.detail = detail
            row.updated_at = now
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            raise
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 — a note, never an error
        logger.info("activity note for %s failed: %s", invoice_id, exc)


#: Outcomes that end an invoice's stops: it went in, or it was deleted.
SETTLING_OUTCOMES = ("clean", "edited", "no_suggestions", "deleted", "not_reviewed")


def merge_created(into: dict | None, more: dict | None) -> dict:
    """Two "created in Loaded" lists ({bucket: [names]}) as one, each name
    once."""
    out = {b: list(v) for b, v in (into or {}).items() if isinstance(v, list)}
    for bucket, names in (more or {}).items():
        for name in names if isinstance(names, list) else []:
            if name and name not in out.setdefault(bucket, []):
                out[bucket].append(name)
    return {b: v for b, v in out.items() if v}


def settle_events(session, venue_id: str, invoice_id: str, at=None) -> dict:
    """The invoice went in or was deleted: its stops are over. Stamped on the
    stops themselves because the outcome row may not be new — there is one
    per verdict, so a delete after Loaded restored the invoice, or a second
    receive with the same verdict, records none. When the invoice stopped
    (``updated_at``) is kept as it was. Returns what the stops not settled
    before had created in Loaded, for the outcome to keep. In the caller's
    session; the caller commits."""
    from sqlalchemy import update

    from app.db.models import InvoiceActivityEvent

    stamp = (at or _dt.datetime.now(_dt.timezone.utc)).isoformat()
    made: dict = {}
    for eid, detail in (
        session.query(InvoiceActivityEvent.id, InvoiceActivityEvent.detail)
        .filter(
            InvoiceActivityEvent.venue_id == venue_id,
            InvoiceActivityEvent.invoice_id == invoice_id,
        )
        .all()
    ):
        d = dict(detail) if isinstance(detail, dict) else {}
        if not d.get("settled_at"):
            made = merge_created(made, (d.get("activity") or {}).get("created"))
        d["settled_at"] = stamp
        session.execute(
            update(InvoiceActivityEvent)
            .where(InvoiceActivityEvent.id == eid)
            .values(detail=d, updated_at=InvoiceActivityEvent.updated_at)
        )
    return made


def _invoice_docs(session, venue_id: str, invoice_id: str) -> list:
    """Every received_invoice document for one invoice, in any state: the one
    with a review first, then the newest."""
    import datetime as dt

    from app.db.models import WorkingDocument

    docs = (
        session.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == venue_id,
            WorkingDocument.external_ref["invoice_id"].as_string() == str(invoice_id),
        )
        .all()
    )
    oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    docs.sort(
        key=lambda d: (
            bool((d.data or {}).get("reviewed_at")),
            d.updated_at or d.created_at or oldest,
        ),
        reverse=True,
    )
    return docs


def keep_review(*, venue_id: str, invoice_id: str, data: dict | None) -> None:
    """Keep the review the batch built for an invoice it received or deleted,
    as that invoice's document, so the activity page can open its card beside
    the copy without reviewing it again.

    Until 19 Oct 2026 the batch kept the review only for invoices it couldn't
    finish: of 3,093 Autopilot receives in 30 days, 133 had a document. The
    document has no thread, so it appears in no chat. Its own session; never
    raises."""
    try:
        import copy

        from sqlalchemy.orm.attributes import flag_modified

        from app.db.engine import SessionLocal
        from app.db.models import WorkingDocument

        if not (venue_id and invoice_id and isinstance(data, dict) and data):
            return
        session = SessionLocal()
        try:
            docs = _invoice_docs(session, venue_id, invoice_id)
            for doc in docs:
                # Every copy of it: a card bound to any of them shows the same.
                doc.data = copy.deepcopy(data)
                doc.version = (doc.version or 0) + 1
                flag_modified(doc, "data")
            if not docs:
                session.add(
                    WorkingDocument(
                        thread_id=None,
                        doc_type="received_invoice",
                        connector_name="loadedhub",
                        venue_id=venue_id,
                        sync_mode="submit",
                        data=copy.deepcopy(data),
                        external_ref={
                            "invoice_id": str(invoice_id),
                            "venue_id": venue_id,
                        },
                    )
                )
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            raise
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 — a record, never an error
        logger.info("review not kept for %s: %s", invoice_id, exc)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _iso(v) -> str | None:
    return v.isoformat() if v else None


def _outcome_row(o, venue_names: dict) -> dict:
    d = o.detail if isinstance(o.detail, dict) else {}
    where = WHERE.get(o.mode, o.mode)
    if o.outcome == "deleted":
        outcome = "deleted"
    elif o.outcome == "dojo":
        outcome = "reported"
    elif o.received:
        outcome = "received"
    else:
        outcome = o.outcome
    activity = d.get("activity") if isinstance(d.get("activity"), dict) else {}
    if not activity:
        # Rows from before 19 Oct record only what Norm SUGGESTED creating —
        # less the ones dismissed or left pending. Whether each was new in
        # Loaded (or found there) isn't recorded, so these are never counted
        # as created (no ghost data).
        kinds = dict(d.get("suggestion_kinds") or {})
        for sid in list(d.get("dismissed_ids") or []) + list(
            d.get("pending_ids") or []
        ):
            k = str(sid).split(":", 1)[0]
            if kinds.get(k):
                kinds[k] -= 1
        suggested = {
            CREATE_KINDS[k]: n
            for k, n in kinds.items()
            if k in CREATE_KINDS and n and n > 0 and o.received
        }
        activity = {"suggested_creates": suggested} if suggested else {}
    deleted = d.get("deleted") if isinstance(d.get("deleted"), dict) else {}
    # What Norm suggested changing, by kind (every row has these, old or new);
    # creates and the delete are shown on their own.
    changes = {
        k: n
        for k, n in (d.get("suggestion_kinds") or {}).items()
        if n and k not in CREATE_KINDS and k != "delete_invoice"
    }
    return {
        "key": f"o:{o.id}",
        "venue_id": o.venue_id,
        "venue_name": venue_names.get(o.venue_id),
        "invoice_id": o.invoice_id,
        "reference_number": o.reference_number,
        "supplier_name": o.supplier_name,
        "total": activity.get("total"),
        "at": _iso(o.created_at),
        "outcome": outcome,
        "by": "norm" if o.actor == "norm" else "person",
        "where": where,
        # Norm's own receives and deletes say which run handled them, which
        # is all a row from before 19 Oct has in place of the level.
        "run_mode": o.mode if o.actor == "norm" else None,
        # A person's delete was their own act: the receiving level in their
        # Preferences didn't govern it, so none is shown.
        "level": None
        if (o.actor != "norm" and outcome == "deleted")
        else d.get("level"),
        "person_changes": (o.manual_edit_count or 0) + (o.dismissed_count or 0)
        if o.actor != "norm"
        else 0,
        "verdict": o.outcome,
        "delete_reason": deleted.get("kind"),
        "delete_note": deleted.get("reason"),
        "activity": activity,
        "changes": changes,
        # The chat thread its card is in, when a person received it there.
        "thread_id": o.thread_id,
    }


def _event_row(e, venue_names: dict) -> dict:
    d = e.detail if isinstance(e.detail, dict) else {}
    return {
        "key": f"e:{e.id}",
        "venue_id": e.venue_id,
        "venue_name": venue_names.get(e.venue_id),
        "invoice_id": e.invoice_id,
        "reference_number": e.reference_number,
        "supplier_name": e.supplier_name,
        "total": float(e.total) if e.total is not None else None,
        "at": _iso(e.updated_at or e.created_at),
        "outcome": e.kind,
        "by": "norm",
        "where": "norm",
        "run_mode": e.mode,
        "level": d.get("level"),
        "reasons": d.get("reasons") or [],
        "gates_needed": d.get("gates_needed") or [],
        "activity": d.get("activity") or {},
    }


def zone(name: object):
    """The viewer's time zone (IANA name), else UTC. The page's dates are the
    viewer's calendar days — the venues are 11 to 13 hours ahead of UTC, so
    UTC midnights put the night's Autopilot run under the wrong day."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(str(name)) if name else _dt.timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return _dt.timezone.utc


def _day_bounds(start: _dt.date, end: _dt.date, tz=None):
    tz = tz or _dt.timezone.utc
    lo = _dt.datetime.combine(start, _dt.time.min, tzinfo=tz)
    hi = _dt.datetime.combine(end + _dt.timedelta(days=1), _dt.time.min, tzinfo=tz)
    return lo, hi


def venue_names(db) -> dict:
    from app.db.models import Venue

    return {v.id: v.name for v in db.query(Venue.id, Venue.name).all()}


def period_rows(
    db, start: _dt.date, end: _dt.date, venue_ids: list[str] | None, tz=None
) -> list[dict]:
    """Every invoice Norm handled in the period, newest first: received or
    deleted (outcome rows), and stopped, waiting or failed (events) unless the
    same invoice was received or deleted after the stop, by the end of the
    period. What Norm created in a run that stopped goes with the receive or
    delete that settled it, whichever period the stop was in."""
    from sqlalchemy import and_, or_

    from app.db.models import InvoiceActivityEvent, InvoiceAutopilotOutcome

    lo, hi = _day_bounds(start, end, tz)
    names = venue_names(db)
    q = db.query(InvoiceAutopilotOutcome).filter(
        InvoiceAutopilotOutcome.created_at >= lo,
        InvoiceAutopilotOutcome.created_at < hi,
    )
    if venue_ids:
        q = q.filter(InvoiceAutopilotOutcome.venue_id.in_(venue_ids))
    outcomes = q.all()
    rows = [_outcome_row(o, names) for o in outcomes]
    # The period's receives and deletes, per invoice, with when they happened.
    settling_rows: dict[tuple, list] = {}
    for o, r in zip(outcomes, rows):
        if r["outcome"] in ("received", "deleted"):
            settling_rows.setdefault((o.venue_id, o.invoice_id), []).append(
                (o.created_at, r)
            )
    # The period's stops, and — for what they created — the earlier stops of
    # invoices received or deleted in it.
    in_period = and_(
        InvoiceActivityEvent.updated_at >= lo, InvoiceActivityEvent.updated_at < hi
    )
    settled_here = {i for _v, i in settling_rows}
    eq = db.query(InvoiceActivityEvent).filter(
        or_(in_period, InvoiceActivityEvent.invoice_id.in_(settled_here))
        if settled_here
        else in_period
    )
    if venue_ids:
        eq = eq.filter(InvoiceActivityEvent.venue_id.in_(venue_ids))
    events = eq.all()
    settles: dict[tuple, list] = {}
    if events:
        # A stop that was received or deleted AFTER it, by the end of the
        # period, is no longer a stop. One from before the stop (a delete
        # Loaded restored, a receive before the invoice came back) doesn't
        # settle it, and neither does one after the period: on the stop's
        # own day it was a stop.
        for v, i, at in (
            db.query(
                InvoiceAutopilotOutcome.venue_id,
                InvoiceAutopilotOutcome.invoice_id,
                InvoiceAutopilotOutcome.created_at,
            )
            .filter(
                InvoiceAutopilotOutcome.invoice_id.in_({e.invoice_id for e in events}),
                InvoiceAutopilotOutcome.outcome.in_(SETTLING_OUTCOMES),
                InvoiceAutopilotOutcome.created_at < hi,
            )
            .all()
        ):
            settles.setdefault((v, i), []).append(at)

    def _aware(a):
        return a if a is None or a.tzinfo else a.replace(tzinfo=_dt.timezone.utc)

    def _since(e) -> object:
        # A stop backfilled from a job carries the job's finish as its
        # last review; when it first stopped is the better line.
        d = e.detail if isinstance(e.detail, dict) else {}
        return e.created_at if d.get("from_job") else (e.updated_at or e.created_at)

    def _after(a, b) -> bool:
        if a is None or b is None:
            return a is not None
        return _aware(a) >= _aware(b)

    def _stamped(e):
        # settle_events: a receive or delete whose outcome row wasn't new.
        d = e.detail if isinstance(e.detail, dict) else {}
        try:
            at = _dt.datetime.fromisoformat(str(d["settled_at"]))
        except (KeyError, TypeError, ValueError):
            return None
        return _aware(at) if _aware(at) < hi else None

    latest: dict[tuple, object] = {}
    for e in events:
        k = (e.venue_id, e.invoice_id)
        since = _since(e)
        shown = lo <= _aware(e.updated_at or e.created_at) < hi
        ends = [_aware(at) for at in settles.get(k, []) if _after(at, since)]
        if _after(_stamped(e), since):
            ends.append(_stamped(e))
        if ends:
            # What Norm created in the run that stopped still happened: it
            # goes with the receive or delete that settled the stop — the
            # first one after it, when that one is in the period.
            made = ((e.detail or {}).get("activity") or {}).get("created") or {}
            first = min(ends)
            target = next(
                (r for at, r in settling_rows.get(k, []) if _aware(at) == first),
                None,
            )
            if made and target is not None:
                # A copy: the row's activity is the outcome's stored detail.
                act = target["activity"] = dict(target.get("activity") or {})
                into = act["created"] = {
                    b: list(v) for b, v in (act.get("created") or {}).items()
                }
                for bucket, items in made.items():
                    for name in items if isinstance(items, list) else []:
                        if name and name not in into.setdefault(bucket, []):
                            into[bucket].append(name)
            continue
        if not shown:
            continue  # read only for what it created
        if k not in latest or (e.updated_at or e.created_at) > (
            latest[k].updated_at or latest[k].created_at
        ):
            latest[k] = e
    rows += [_event_row(e, names) for e in latest.values()]
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows


def matches_mode(row: dict, mode: str | None) -> bool:
    """``mode``: autopilot | approve_fixes | approve_all (the level in force,
    or the run that handled it) | app | claude (where a person received it)."""
    if not mode:
        return True
    if mode in ("app", "claude"):
        return row.get("where") == mode
    return ((row.get("level") or {}).get("mode") or row.get("run_mode")) == mode


#: The stream's "what happened" filter.
def _created_any(row: dict) -> bool:
    """Something was made new in Loaded for this invoice (recorded by name
    since 19 Oct 2026; older rows only say what was suggested)."""
    act = row.get("activity") or {}
    return bool(act.get("created"))


#: The stream's "what happened" filter; the page's cards set it.
OUTCOME_FILTERS = {
    "received_by_norm": lambda r: r["outcome"] == "received" and r["by"] == "norm",
    "received_by_person": lambda r: r["outcome"] == "received" and r["by"] == "person",
    "changed_by_person": lambda r: (
        r["outcome"] == "received" and bool(r.get("person_changes"))
    ),
    # Everything Norm couldn't finish.
    "not_finished": lambda r: r["outcome"] in ("stopped", "waiting", "failed"),
    # Deletions other than the routine duplicates and statements.
    "unusual_deletions": lambda r: (
        r["outcome"] == "deleted"
        and (r.get("delete_reason") or "") not in ROUTINE_DELETES
    ),
    "created": _created_any,
}


def filter_rows(
    rows: list[dict], *, mode=None, outcome=None, supplier=None, q=None
) -> list[dict]:
    keep = OUTCOME_FILTERS.get(outcome) if outcome else None
    if outcome and keep is None:
        keep = lambda r: r["outcome"] == outcome  # noqa: E731
    sup = (supplier or "").strip().lower()
    text = (q or "").strip().lower()
    out = []
    for r in rows:
        if not matches_mode(r, mode) or (keep and not keep(r)):
            continue
        if sup and sup not in str(r.get("supplier_name") or "").lower():
            continue
        said = f"{r.get('reference_number') or ''} {r.get('supplier_name') or ''}"
        if text and text not in said.lower():
            continue
        out.append(r)
    return out


def kpis(rows: list[dict]) -> dict:
    """The page's cards for the period: each count is one of the stream's
    filters (owner, 19 Oct 2026)."""
    received = [r for r in rows if r["outcome"] == "received"]
    by_norm = sum(1 for r in received if r["by"] == "norm")
    made: Counter = Counter()
    created = [r for r in rows if _created_any(r)]
    for r in created:
        act = r.get("activity") or {}
        for kind, names in (act.get("created") or {}).items():
            made[kind] += len(names)
    return {
        "invoices": len(rows),
        "received": len(received),
        "received_by_norm": by_norm,
        "received_by_person": len(received) - by_norm,
        "changed_by_person": sum(
            1 for r in rows if OUTCOME_FILTERS["changed_by_person"](r)
        ),
        "not_finished": sum(1 for r in rows if OUTCOME_FILTERS["not_finished"](r)),
        "deleted": sum(1 for r in rows if r["outcome"] == "deleted"),
        "unusual_deletions": sum(
            1 for r in rows if OUTCOME_FILTERS["unusual_deletions"](r)
        ),
        "created_invoices": len(created),
        "created": dict(made),
    }


def unit_check_query(
    db,
    start: _dt.date | None,
    end: _dt.date | None,
    venue_ids: list[str] | None,
    tz=None,
):
    """The daily unit check's lines for invoices received in the period (by the
    receive's own date; a line whose invoice has no receive record goes by when
    it was flagged)."""
    from sqlalchemy import and_, func

    from app.db.models import InvoiceAutopilotOutcome as O
    from app.db.models import UnitCheckProposal as P

    q = db.query(P)
    if venue_ids:
        q = q.filter(P.venue_id.in_(venue_ids))
    if start and end:
        lo, hi = _day_bounds(start, end, tz)
        received_at = (
            db.query(func.min(O.created_at))
            .filter(
                and_(
                    O.venue_id == P.venue_id,
                    O.invoice_id == P.invoice_id,
                    O.received.is_(True),
                )
            )
            .correlate(P)
            .scalar_subquery()
        )
        when = func.coalesce(received_at, P.created_at)
        q = q.filter(when >= lo, when < hi)
    return q


def unit_check_by_invoice(db, start, end, venue_ids, tz=None) -> dict:
    """{"venue:invoice": {flagged, open}}: the daily unit check's lines per
    invoice, for the stream's tags."""
    from app.db.models import UnitCheckProposal as P

    out: dict = {}
    q = unit_check_query(db, start, end, venue_ids, tz)
    for p in q.with_entities(P.status, P.venue_id, P.invoice_id).all():
        inv = out.setdefault(f"{p.venue_id}:{p.invoice_id}", {"flagged": 0, "open": 0})
        inv["flagged"] += 1
        if p.status in ("open", "failed"):
            inv["open"] += 1
    return out


def timeline(db, venue_id: str, invoice_id: str) -> dict:
    """Everything recorded about one invoice, oldest first."""
    from app.db.models import (
        InvoiceActivityEvent,
        InvoiceAutopilotOutcome,
        UnitCheckProposal,
    )
    from app.services import unit_check as UC

    names = venue_names(db)
    items = []
    for o in (
        db.query(InvoiceAutopilotOutcome)
        .filter(
            InvoiceAutopilotOutcome.venue_id == venue_id,
            InvoiceAutopilotOutcome.invoice_id == invoice_id,
        )
        .all()
    ):
        row = {"type": "outcome", **_outcome_row(o, names), "detail": o.detail}
        items.append(row)
    for e in (
        db.query(InvoiceActivityEvent)
        .filter(
            InvoiceActivityEvent.venue_id == venue_id,
            InvoiceActivityEvent.invoice_id == invoice_id,
        )
        .all()
    ):
        row = _event_row(e, names)
        row["first_at"] = _iso(e.created_at)
        items.append({"type": "event", **row})
    unit_lines = UC.views(
        db,
        db.query(UnitCheckProposal)
        .filter(
            UnitCheckProposal.venue_id == venue_id,
            UnitCheckProposal.invoice_id == invoice_id,
        )
        .order_by(UnitCheckProposal.created_at)
        .all(),
    )
    # By when each last happened: a stop refreshed after a receive (the
    # invoice came back) is the latest thing, not the first.
    items.sort(key=lambda x: x.get("at") or "")
    # The document the card opens on: Norm's review as it was kept.
    docs = _invoice_docs(db, venue_id, invoice_id)
    doc = docs[0] if docs else None
    data = (doc.data or {}) if doc is not None else {}
    return {
        "venue_id": venue_id,
        "venue_name": names.get(venue_id),
        "invoice_id": invoice_id,
        "items": items,
        "unit_check": unit_lines,
        "document": {
            "id": doc.id,
            "file_id": data.get("file_id"),
            "reviewed": bool(data.get("reviewed_at")),
            "received": bool(data.get("is_received")),
            "deleted": bool(data.get("is_deleted")),
        }
        if doc is not None
        else None,
    }
