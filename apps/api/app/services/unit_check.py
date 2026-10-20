"""The daily unit check (owner, 18 Oct 2026).

Each morning, every line Norm received since the last run is compared with the
invoice Norm read (the reading kept on the invoice's working document) and with
Norm's own catalogue answer for that supplier product. Two kinds of line are
recorded for a person in the Dojo:

- a correction: the unit booked disagrees with the invoice or the catalogue —
  apply it, or dismiss it;
- a line to confirm: the invoice shows no size and Norm has no settled answer,
  so the unit went in on an estimate Norm wasn't sure of (or, with no estimate
  at all, the venue's closest unit, which Norm picked). Confirm it, or change
  it; either way Norm's catalogue keeps the person's answer, so the product is
  never asked about again.

Nothing is changed in Loaded by the check itself, and the admins get an email
when a run finishes.

Judged against the invoice and Norm's knowledge, never against Loaded's own
settings: the owner's rule, after the review that leant on Loaded's stocktake
units and price history missed 73 basil bunches booked as Kilo — every line wrong
the same way, so nothing looked unusual.
"""

from __future__ import annotations

import difflib
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.services.invoice_units import (
    is_charge_line,
    is_multipack,
    is_packaging_word,
    parse_unit,
    units_equivalent,
)

logger = logging.getLogger(__name__)

TEMPLATE = "unit_check"
#: A first run (no earlier run to follow on from) checks this far back.
FIRST_WINDOW = timedelta(hours=24)


# ---------------------------------------------------------------------------
# One line
# ---------------------------------------------------------------------------


def _confident_size(printed: dict | None) -> str | None:
    """The size the invoice line shows, when Norm read one with confidence."""
    if not isinstance(printed, dict) or printed.get("unit_unrecognisable"):
        return None
    size = str(printed.get("unit_of_measure") or "").strip()
    if not size or is_packaging_word(size):
        return None
    if is_multipack(size) or parse_unit(size) is not None:
        return size
    return None


def _same_unit(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    return a.get("id") == b.get("id") or units_equivalent(a.get("name"), b.get("name"))


def _charged_as(word: object, booked: dict | None, units: list[dict]) -> bool:
    """The invoice's own charge word ('Each', 'Kilogram') is the unit booked:
    the invoice decided it. A pack count with no size ('6 PK') never does."""
    from app.services.invoice_replica import _resolve_unit_record

    if not word or is_packaging_word(word):
        return False
    parsed = parse_unit(word)
    if parsed is None or (parsed[0] == "count" and parsed[1] != 1):
        return False
    return _same_unit(_resolve_unit_record(word, units), booked)


def judge_line(
    booked: dict | None,
    printed: dict | None,
    catalogue_unit: str | None,
    units: list[dict],
    *,
    has_copy: bool = True,
    estimate: dict | None = None,
) -> dict | None:
    """What a person should look at for one received line, or None when the
    unit booked agrees with the invoice and with Norm.

    The invoice decides when it shows a size. When it shows none, Norm's
    catalogue answer decides. A unit the venue doesn't have can't be proposed
    (the line is then left for the next receive to create it). With neither,
    the unit booked was Norm's unsure estimate (``estimate``) or a person's
    choice: a line to confirm — unless the invoice's own charge word decided
    it.
    ``has_copy`` False is an Ordermentum invoice: nothing printed to read, so
    ``printed`` (built from Loaded's figures) never confirms a unit."""
    from app.services.invoice_replica import _resolve_unit_record

    size = _confident_size(printed) if has_copy else None
    booked_name = (booked or {}).get("name") or "no unit"
    if size:
        rec = _resolve_unit_record(size, units)
        if rec is None or _same_unit(rec, booked):
            return None
        return {
            "kind": "correct",
            "proposed": rec,
            "invoice_says": size,
            "catalogue_says": catalogue_unit,
            "reason": (
                f"The invoice shows '{size}', but the line is booked as "
                f"'{booked_name}'."
            ),
        }
    if catalogue_unit:
        rec = _resolve_unit_record(catalogue_unit, units)
        if rec is None or _same_unit(rec, booked):
            return None
        return {
            "kind": "correct",
            "proposed": rec,
            "invoice_says": _no_size(printed, has_copy),
            "catalogue_says": catalogue_unit,
            "reason": (
                "The invoice shows no size. Norm's catalogue says this product is "
                f"'{catalogue_unit}', but the line is booked as '{booked_name}'."
            ),
        }
    if has_copy and printed is None:
        return None  # the line wasn't found on the invoice: nothing to go on
    if has_copy and _charged_as((printed or {}).get("unit"), booked, units):
        return None
    est = estimate if isinstance(estimate, dict) and estimate.get("unit") else None
    if est and _same_unit(_resolve_unit_record(est["unit"], units), booked):
        why = f" ({est['why']})" if est.get("why") else ""
        reason = (
            "The invoice shows no size, and Norm wasn't sure of this product: it "
            f"worked it out as '{booked_name}', with {est.get('confidence') or 'low'} "
            f"confidence{why}."
        )
    else:
        reason = (
            "The invoice shows no size and Norm had no estimate for this product: "
            f"it went in as '{booked_name}'."
        )
    return {
        "kind": "confirm",
        "proposed": booked,
        "invoice_says": _no_size(printed, has_copy),
        "catalogue_says": None,
        "reason": reason
        + " Confirm it or choose the right unit: Norm keeps your answer for this "
        "product.",
    }


def _no_size(printed: dict | None, has_copy: bool) -> str:
    if not has_copy:
        return "no size (an Ordermentum order: no invoice copy)"
    shown = (printed or {}).get("unit")
    return f"no size (charged per '{shown}')" if shown else "no size"


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def match_printed(line: dict, printed_lines: list[dict], used: set[int]) -> dict | None:
    """The reading's line for one Loaded line: by supplier code, else by
    wording with the same price (Loaded's description is the stock item's
    name once linked, so wording alone isn't enough)."""
    code = str(line.get("code") or "").strip()
    if code:
        for k, pl in enumerate(printed_lines):
            if k not in used and str(pl.get("code") or "").strip() == code:
                used.add(k)
                return pl
    cost = line.get("unitCostExclTax")
    best = None
    for k, pl in enumerate(printed_lines):
        if k in used:
            continue
        score = difflib.SequenceMatcher(
            None, _norm(pl.get("description")), _norm(line.get("description"))
        ).ratio()
        try:
            if (
                cost is not None
                and abs(float(pl.get("unit_price_ex_tax")) - float(cost)) < 0.011
            ):
                score += 0.5
        except (TypeError, ValueError):
            pass
        if best is None or score > best[0]:
            best = (score, k, pl)
    if best is not None and best[0] >= 0.8:
        used.add(best[1])
        return best[2]
    return None


# ---------------------------------------------------------------------------
# One venue
# ---------------------------------------------------------------------------


def _reading_lines(
    db, venue_id: str, invoice_id: str
) -> tuple[list[dict] | None, bool]:
    """The invoice as Norm read it — the reading the review kept on the
    invoice's working document — and whether there was a copy to read. An
    Ordermentum invoice has none: its "reading" is Loaded's own figures, good
    for finding the line, never for confirming a unit. (None, True) when Norm
    has no document for the invoice."""
    from app.db.models import WorkingDocument

    doc = (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == venue_id,
            WorkingDocument.external_ref["invoice_id"].as_string() == str(invoice_id),
        )
        .order_by(WorkingDocument.updated_at.desc())
        .first()
    )
    snap = ((doc.data or {}) if doc is not None else {}).get("extracted_snapshot") or {}
    if not isinstance(snap, dict) or not snap.get("lines"):
        return None, True
    lines = [ln for ln in snap.get("lines") or [] if isinstance(ln, dict)]
    return lines, bool((doc.data or {}).get("file_id"))


def check_venue(db, config_db, run, venue, invoice_ids: list[str], lh=None) -> dict:
    """Check one venue's receipts; adds UnitCheckProposal rows. Returns the
    venue's tally."""
    from app.db.models import UnitCheckProposal
    from app.services import supplier_catalog
    from app.services.received_invoice import LoadedInvoiceClient

    tally = {
        "venue_id": venue.id,
        "name": venue.name,
        "invoices": 0,
        "lines": 0,
        "proposals": 0,
        "corrections": 0,
        "to_confirm": 0,
        "errors": [],
    }
    lh = lh or LoadedInvoiceClient(db, config_db, venue.id)
    units = [
        u for u in (lh.get("/1.0/stock/internal/units") or []) if isinstance(u, dict)
    ]
    by_id = {u.get("id"): u for u in units}
    for invoice_id in invoice_ids:
        try:
            detail = lh.invoice(invoice_id)
        except Exception as exc:  # noqa: BLE001 — one invoice never stops a run
            tally["errors"].append(f"{invoice_id}: {exc}")
            continue
        if detail.get("deletedAt") or not detail.get("isReceived"):
            continue
        tally["invoices"] += 1
        printed_lines, has_copy = _reading_lines(db, venue.id, invoice_id)
        printed_lines = printed_lines or []
        supplier_key = None
        try:
            supplier_key = supplier_catalog.supplier_key_for(
                detail.get("supplierName"), config_db
            )
        except Exception:  # noqa: BLE001
            supplier_key = None
        used: set[int] = set()
        for line in detail.get("lines") or []:
            if line.get("deletedAt") or not line.get("linkedUnitId"):
                continue
            if is_charge_line(line.get("description")):
                continue
            tally["lines"] += 1
            printed = (
                match_printed(line, printed_lines, used) if printed_lines else None
            )
            answer, key, estimate = None, None, None
            if supplier_key:
                key = supplier_catalog.product_key(
                    line.get("code"),
                    (printed or {}).get("description") or line.get("description"),
                )
                answer = supplier_catalog.catalog_unit_for_line(
                    config_db, supplier_key, key
                )
                row = supplier_catalog.lookup(config_db, supplier_key, key)
                guess = ((row.evidence or {}) if row is not None else {}).get("guess")
                if isinstance(guess, dict) and not guess.get("settled"):
                    estimate = {k: guess.get(k) for k in ("unit", "confidence", "why")}
            booked = by_id.get(line.get("linkedUnitId")) or {
                "id": line.get("linkedUnitId"),
                "name": line.get("unit"),
            }
            verdict = judge_line(
                booked,
                printed,
                (answer or {}).get("unit_name"),
                units,
                has_copy=has_copy,
                estimate=estimate,
            )
            if verdict is None:
                continue
            if verdict["kind"] == "confirm" and not (supplier_key and key):
                # No supplier layout in Norm: nowhere to keep the answer, so the
                # product would come back every morning.
                continue
            rec = verdict["proposed"]
            db.add(
                UnitCheckProposal(
                    run_id=run.id,
                    venue_id=venue.id,
                    venue_name=venue.name,
                    invoice_id=str(invoice_id),
                    invoice_number=detail.get("referenceNumber"),
                    supplier_name=detail.get("supplierName"),
                    invoice_date=str(detail.get("issuedAt") or "")[:10] or None,
                    line_id=str(line.get("id")),
                    item_id=line.get("linkedItemId"),
                    item_name=line.get("description"),
                    code=line.get("code"),
                    printed=printed,
                    quantity=line.get("quantityReceived"),
                    unit_cost=line.get("unitCostExclTax"),
                    booked_unit_id=booked.get("id"),
                    booked_unit_name=booked.get("name"),
                    invoice_says=verdict["invoice_says"],
                    catalogue_says=verdict["catalogue_says"],
                    proposed_unit_id=rec.get("id"),
                    proposed_unit_name=rec.get("name"),
                    reason=verdict["reason"],
                    kind=verdict["kind"],
                    catalog_key=supplier_key,
                    product_key=key,
                    norm_estimate=estimate if verdict["kind"] == "confirm" else None,
                    status="open",
                )
            )
            tally["proposals"] += 1
            tally["corrections" if verdict["kind"] == "correct" else "to_confirm"] += 1
    return tally


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _receipts(db, start: datetime, end: datetime) -> dict[str, list[str]]:
    """{venue_id: [invoice_id]} — what Norm received in the window."""
    from app.db.models import InvoiceAutopilotOutcome as O

    rows = (
        db.query(O.venue_id, O.invoice_id)
        .filter(O.received.is_(True), O.created_at > start, O.created_at <= end)
        .all()
    )
    out: dict[str, list[str]] = {}
    for venue_id, invoice_id in rows:
        if venue_id and invoice_id and invoice_id not in out.setdefault(venue_id, []):
            out[venue_id].append(invoice_id)
    return out


def run_unit_check(
    now: datetime | None = None,
    *,
    db=None,
    config_db=None,
    force: bool = False,
    clients: dict | None = None,
) -> dict:
    """Run today's check once (the unique slot is the guard). ``force`` (the
    Dojo's "Run now") runs at any hour under its own slot. ``clients``
    ({venue_id: Loaded client}) is for tests."""
    from app.config import settings
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import UnitCheckRun, Venue

    now = now or datetime.now(timezone.utc)
    if not force and (
        not settings.UNIT_CHECK_ENABLED or now.hour < settings.UNIT_CHECK_HOUR_UTC
    ):
        return {"ran": False}
    own_db, own_cdb = db is None, config_db is None
    db = db or SessionLocal()
    config_db = config_db or _ConfigSessionLocal()
    try:
        slot = now.date().isoformat() + (
            f"+manual-{now.strftime('%H%M%S')}" if force else ""
        )
        last = (
            db.query(UnitCheckRun)
            .filter(UnitCheckRun.ok.is_(True))
            .order_by(UnitCheckRun.window_end.desc())
            .first()
        )
        start = (
            last.window_end
            if last is not None and last.window_end
            else now - FIRST_WINDOW
        )
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        run = UnitCheckRun(
            slot=slot, window_start=start, window_end=now, started_at=now
        )
        try:
            with db.begin_nested():
                db.add(run)
                db.flush()
        except IntegrityError:
            return {"ran": False, "claimed": True}
        db.commit()
        venues = []
        try:
            for venue_id, invoice_ids in sorted(_receipts(db, start, now).items()):
                venue = db.get(Venue, venue_id)
                if venue is None:
                    continue
                try:
                    with db.begin_nested():
                        venues.append(
                            check_venue(
                                db,
                                config_db,
                                run,
                                venue,
                                invoice_ids,
                                lh=(clients or {}).get(venue_id),
                            )
                        )
                except Exception as exc:  # noqa: BLE001 — a venue never stops a run
                    logger.exception("unit check failed for %s", venue_id)
                    venues.append(
                        {
                            "venue_id": venue_id,
                            "name": venue.name,
                            "invoices": 0,
                            "lines": 0,
                            "proposals": 0,
                            "corrections": 0,
                            "to_confirm": 0,
                            "errors": [str(exc)[:300]],
                        }
                    )
            run.ok = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("unit check run failed")
            run.ok, run.error = False, str(exc)[:500]
        run.finished_at = datetime.now(timezone.utc)
        run.summary = {
            "venues": venues,
            "invoices": sum(v["invoices"] for v in venues),
            "lines": sum(v["lines"] for v in venues),
            "proposals": sum(v["proposals"] for v in venues),
            "corrections": sum(v["corrections"] for v in venues),
            "to_confirm": sum(v["to_confirm"] for v in venues),
        }
        db.commit()
        _email(db, run)
        return {"ran": True, "run_id": run.id, "summary": run.summary}
    finally:
        if own_db:
            db.close()
        if own_cdb:
            config_db.close()


def _email(db, run) -> None:
    """Tell the admins the run finished, with a link to the Daily unit check
    page. Never raises."""
    from app.config import settings
    from app.db.models import User
    from app.services.email_service import send_system_email

    try:
        to = [
            u.email
            for u in db.query(User).filter(User.role == "admin").all()
            if u.email and not u.email.endswith((".test", ".local"))
        ]
        if not to:
            return
        s = run.summary or {}
        send_system_email(
            TEMPLATE,
            to,
            {
                "ok": bool(run.ok),
                "error": run.error,
                "date": (run.window_end or run.started_at).strftime("%-d %B %Y"),
                "invoices": s.get("invoices", 0),
                "lines": s.get("lines", 0),
                "proposals": s.get("proposals", 0),
                "corrections": s.get("corrections", 0),
                "to_confirm": s.get("to_confirm", 0),
                "venues": [v for v in s.get("venues") or [] if v.get("invoices")],
                # Settings → Invoice Dojo → Daily unit check.
                "link": f"{settings.app_url}/app?open=unit-check",
            },
            db,
        )
        run.emailed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:  # noqa: BLE001 — an email never fails the run
        logger.exception("unit check email failed for run %s", run.id)
        db.rollback()


# ---------------------------------------------------------------------------
# A person's decision
# ---------------------------------------------------------------------------


def apply_proposal(db, config_db, proposal, user_email: str, *, lh=None) -> None:
    """Change the line's unit in Loaded as proposed (only the unit), and
    record who did it. A failure is recorded on the proposal, never raised."""
    from app.services.received_invoice import (
        LoadedInvoiceClient,
        ReceivedLineUnitError,
        change_received_line_unit,
    )

    if proposal.status not in ("open", "failed") or not proposal.proposed_unit_id:
        return
    try:
        lh = lh or LoadedInvoiceClient(db, config_db, proposal.venue_id)
        units = {u.get("id"): u for u in lh.get("/1.0/stock/internal/units") or []}
        unit = units.get(proposal.proposed_unit_id)
        if unit is None or unit.get("datestampDeleted"):
            raise ReceivedLineUnitError(
                f"'{proposal.proposed_unit_name}' is no longer a unit at this venue"
            )
        change_received_line_unit(
            lh,
            proposal.invoice_id,
            proposal.line_id,
            unit,
            from_unit_id=proposal.booked_unit_id,
        )
        proposal.status, proposal.error = "applied", None
    except Exception as exc:  # noqa: BLE001 — shown on the proposal
        proposal.status, proposal.error = "failed", str(exc)[:500]
    proposal.decided_by = user_email
    proposal.decided_at = datetime.now(timezone.utc)
    db.commit()


def _same_product(db, proposal) -> list:
    """The open "confirm" lines for the same product booked in the same unit
    name, at every venue — one person's answer settles them all, as it settles
    the product in Norm's catalogue."""
    from app.db.models import UnitCheckProposal as P

    if not (proposal.catalog_key and proposal.product_key):
        return [proposal]
    rows = (
        db.query(P)
        .filter(
            P.kind == "confirm",
            P.status.in_(("open", "failed")),
            P.catalog_key == proposal.catalog_key,
            P.product_key == proposal.product_key,
        )
        .all()
    )
    same = [
        r
        for r in rows
        if r.id == proposal.id
        or units_equivalent(r.booked_unit_name, proposal.booked_unit_name)
        or r.booked_unit_name == proposal.booked_unit_name
    ]
    return same if proposal in same else [proposal, *same]


def _keep_answer(config_db, proposal, unit_name: str, user_email: str) -> None:
    """Norm's catalogue keeps a person's answer for the product (shared by
    every venue), so it is never asked about again."""
    from app.services import supplier_catalog

    if not (proposal.catalog_key and proposal.product_key):
        return
    description = (proposal.printed or {}).get("description") or proposal.item_name
    supplier_catalog.record_answer(
        config_db,
        proposal.catalog_key,
        proposal.product_key,
        unit_name,
        description=description,
        why=f"{user_email} confirmed it in the Dojo's daily unit check",
    )
    config_db.commit()


def confirm_proposal(db, config_db, proposal, user_email: str) -> None:
    """The unit Norm booked is right: Norm's catalogue keeps it, and every open
    line of the same product booked the same way is settled. Nothing changes
    in Loaded. A failure is recorded on the proposal, never raised."""
    if proposal.kind != "confirm" or proposal.status not in ("open", "failed"):
        return
    now = datetime.now(timezone.utc)
    try:
        _keep_answer(config_db, proposal, proposal.booked_unit_name, user_email)
    except Exception as exc:  # noqa: BLE001 — shown on the proposal
        config_db.rollback()
        proposal.status, proposal.error = "failed", str(exc)[:500]
        proposal.decided_by, proposal.decided_at = user_email, now
        db.commit()
        return
    for p in _same_product(db, proposal):
        p.status, p.error = "confirmed", None
        p.decided_by, p.decided_at = user_email, now
    db.commit()


def change_proposal(
    db, config_db, proposal, unit_id: str, user_email: str, *, clients=None
) -> None:
    """The person's unit is the right one: Norm's catalogue keeps it, and the
    line — with the same product's other open lines booked the same way, at
    every venue — is changed in Loaded (only the unit). ``clients``
    ({venue_id: Loaded client}) is for tests. Failures are recorded on the
    proposals, never raised."""
    from app.services.invoice_replica import _resolve_unit_record
    from app.services.invoice_units import is_measurement
    from app.services.received_invoice import (
        LoadedInvoiceClient,
        ReceivedLineUnitError,
        change_received_line_unit,
    )

    if proposal.status not in ("open", "failed"):
        return
    now = datetime.now(timezone.utc)
    group = _same_product(db, proposal) if proposal.kind == "confirm" else [proposal]
    by_venue: dict[str, list] = {}
    for p in group:
        by_venue.setdefault(p.venue_id, []).append(p)
    chosen = None
    for venue_id, rows in sorted(
        by_venue.items(), key=lambda kv: kv[0] != proposal.venue_id
    ):
        try:
            lh = (clients or {}).get(venue_id) or LoadedInvoiceClient(
                db, config_db, venue_id
            )
            units = [
                u
                for u in lh.get("/1.0/stock/internal/units") or []
                if isinstance(u, dict) and not u.get("datestampDeleted")
            ]
            if venue_id == proposal.venue_id:
                unit = next((u for u in units if u.get("id") == unit_id), None)
                if unit is None or not is_measurement(unit):
                    raise ReceivedLineUnitError(
                        "that unit isn't a measurement at this venue"
                    )
                chosen = unit
                try:
                    _keep_answer(config_db, proposal, unit.get("name"), user_email)
                except Exception:
                    config_db.rollback()
                    raise
            else:
                # Another venue's own unit of the same size.
                unit = _resolve_unit_record(
                    (chosen or {}).get("name"), [u for u in units if is_measurement(u)]
                )
                if unit is None:
                    continue  # left open there: that venue lacks the unit
        except Exception as exc:  # noqa: BLE001
            for p in rows:
                p.status, p.error = "failed", str(exc)[:500]
                p.decided_by, p.decided_at = user_email, now
            if venue_id == proposal.venue_id:
                break
            continue
        for p in rows:
            try:
                change_received_line_unit(
                    lh, p.invoice_id, p.line_id, unit, from_unit_id=p.booked_unit_id
                )
                p.status, p.error = "applied", None
                p.proposed_unit_id, p.proposed_unit_name = (
                    unit.get("id"),
                    unit.get("name"),
                )
            except Exception as exc:  # noqa: BLE001
                p.status, p.error = "failed", str(exc)[:500]
            p.decided_by, p.decided_at = user_email, now
    db.commit()


def venue_units(db, config_db, proposal, *, lh=None) -> list[dict]:
    """The units a person can choose for the line: the venue's measurements."""
    from app.services.invoice_units import is_measurement
    from app.services.received_invoice import LoadedInvoiceClient

    lh = lh or LoadedInvoiceClient(db, config_db, proposal.venue_id)
    out = [
        {"id": u.get("id"), "name": u.get("name"), "kind": u.get("stockUnitType")}
        for u in lh.get("/1.0/stock/internal/units") or []
        if isinstance(u, dict) and not u.get("datestampDeleted") and is_measurement(u)
    ]
    return sorted(
        out, key=lambda u: (str(u["kind"] or ""), str(u["name"] or "").lower())
    )


def dismiss_proposal(db, proposal, user_email: str, note: str | None) -> None:
    if proposal.status not in ("open", "failed"):
        return
    proposal.status = "dismissed"
    proposal.note = (note or "").strip() or None
    proposal.decided_by = user_email
    proposal.decided_at = datetime.now(timezone.utc)
    db.commit()


def proposal_view(p) -> dict:
    def _num(v):
        return float(v) if v is not None else None

    return {
        "id": p.id,
        "run_id": p.run_id,
        "venue_id": p.venue_id,
        "venue_name": p.venue_name,
        "invoice_id": p.invoice_id,
        "invoice_number": p.invoice_number,
        "supplier_name": p.supplier_name,
        "invoice_date": p.invoice_date,
        "item_name": p.item_name,
        "code": p.code,
        "printed": p.printed,
        "quantity": _num(p.quantity),
        "unit_cost": _num(p.unit_cost),
        "booked_unit_name": p.booked_unit_name,
        "invoice_says": p.invoice_says,
        "catalogue_says": p.catalogue_says,
        "proposed_unit_name": p.proposed_unit_name,
        "reason": p.reason,
        "kind": p.kind or "correct",
        "norm_estimate": p.norm_estimate,
        "status": p.status,
        "decided_by": p.decided_by,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "note": p.note,
        "error": p.error,
    }


def run_view(run, counts: dict | None = None) -> dict:
    return {
        "id": run.id,
        "slot": run.slot,
        "window_start": run.window_start.isoformat() if run.window_start else None,
        "window_end": run.window_end.isoformat() if run.window_end else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "ok": run.ok,
        "error": run.error,
        "summary": run.summary or {},
        "status_counts": counts or {},
    }
