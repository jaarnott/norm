"""Intercompany attribution: who paid whom for work at whose venue (plan §10,
report 03 §9).

A fact is intercompany iff Loaded flagged it ``is_from_other_venue`` — Loaded
sets that when the shift's venue is not the staff member's payroll venue and
the member is not ``isPaidByEachVenue``, so the flag already carries the rule
Orbit re-derived client-side from its remuneration mirror. From one entity's
view:

* **In** — we pay the person (the fact is in OUR close snapshot) and they
  worked at the counterparty → we invoice the counterparty (ACCREC).
* **Out** — the counterparty pays the person (the fact is in THEIR close
  snapshot, ``worked_venue_id`` = us) → they bill us (ACCPAY).
* ``net = In − Out``: what the reconciliation adds to our timeclock total,
  and the one intercompany line on the journal.

A gratuity share earned here but paid through another entity's payroll is a
**synthetic recharge** (0 h, cost = the share): Out for the venue it was
earned at, In for the entity that paid it — exactly as Orbit appended them.

Other entities' snapshots are read organisation-wide, whatever venues the
caller holds: their staff's shifts here are this venue's cost, and the page
shows only the counterparty's name and the lines.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import Venue
from app.db.payroll_models import (
    PayEntity,
    PayGratuityAllocation,
    PayGratuityBatch,
    PayShiftFact,
    PayWeek,
)
from app.services.finance_core.ctx import FinanceCtx


def _leave(fact: PayShiftFact) -> bool:
    return bool(fact.leave_request_id) or str(fact.shift_type or "") == "Leave"


def _venue_names(db: Session, org_id: str) -> dict[str, str]:
    return dict(
        db.query(Venue.id, Venue.name).filter(Venue.organization_id == org_id).all()
    )


def current_close_facts(
    db: Session, org_id: str, week_start: dt.date
) -> dict[str, list[PayShiftFact]]:
    """Every entity's close-snapshot facts for the week (the latest close
    snapshot, else the current one — ``reconciliation.basis_snapshot``'s
    rule), keyed by the payroll venue id."""
    from app.services.payroll.reconciliation import basis_snapshot

    weeks = (
        db.query(PayWeek.venue_id, PayWeek.id)
        .filter(
            PayWeek.organization_id == org_id,
            PayWeek.week_start == week_start,
        )
        .all()
    )
    out: dict[str, list[PayShiftFact]] = {}
    for venue_id, week_id in weeks:
        snapshot = basis_snapshot(db, org_id, week_id)
        if snapshot is None:
            continue
        snapshot_id = snapshot.id
        out[venue_id] = (
            db.query(PayShiftFact)
            .filter(
                PayShiftFact.organization_id == org_id,
                PayShiftFact.snapshot_id == snapshot_id,
            )
            .all()
        )
    return out


def _group(
    facts: list[PayShiftFact],
    key_of,
    names: dict[str, str],
) -> list[dict]:
    """Counterparty groups with one line per person (Orbit's collapse)."""
    groups: dict[str, dict] = {}
    for f in facts:
        cp_id, cp_label = key_of(f)
        g = groups.setdefault(
            cp_id or cp_label,
            {
                "counterparty_venue_id": cp_id,
                "counterparty": names.get(cp_id, cp_label) if cp_id else cp_label,
                "people": {},
            },
        )
        person = str(f.staff_member_id or f.staff_name or f.id)
        line = g["people"].setdefault(
            person,
            {
                "staff_member_id": f.staff_member_id,
                "staff_name": f.staff_name,
                "hours": 0.0,
                "amount_cents": 0,
                "fact_ids": [],
                "gratuity": False,
            },
        )
        line["hours"] = round(line["hours"] + float(f.hours or 0), 2)
        line["amount_cents"] += int(f.cost_cents or 0)
        line["fact_ids"].append(f.id)
    out = []
    for g in groups.values():
        lines = sorted(
            g.pop("people").values(), key=lambda x: str(x["staff_name"] or "")
        )
        g["lines"] = lines
        g["hours"] = round(sum(x["hours"] for x in lines), 2)
        g["amount_cents"] = sum(x["amount_cents"] for x in lines)
        out.append(g)
    return sorted(out, key=lambda x: str(x["counterparty"] or ""))


def _add_gratuity_line(groups: list[dict], cp_id, cp_name, allocation) -> None:
    g = None
    for row in groups:
        if row["counterparty_venue_id"] == cp_id:
            g = row
            break
    if g is None:
        g = {
            "counterparty_venue_id": cp_id,
            "counterparty": cp_name,
            "lines": [],
            "hours": 0.0,
            "amount_cents": 0,
        }
        groups.append(g)
        groups.sort(key=lambda x: str(x["counterparty"] or ""))
    g["lines"].append(
        {
            "staff_member_id": allocation.staff_member_id,
            "staff_name": f"{allocation.staff_name or allocation.staff_member_id} — Gratuities",
            "hours": 0.0,
            "amount_cents": int(allocation.amount_cents or 0),
            "fact_ids": [],
            "allocation_id": allocation.id,
            "gratuity": True,
        }
    )
    g["amount_cents"] += int(allocation.amount_cents or 0)


def _gratuity_recharges(
    db: Session, org_id: str, week_start: dt.date
) -> list[tuple[str, str, PayGratuityAllocation]]:
    """(earned-at venue, paid-by venue, share) for every active payable share
    paid by an entity other than the one it was earned at."""
    rows = (
        db.query(PayGratuityBatch, PayGratuityAllocation)
        .join(
            PayGratuityAllocation,
            (PayGratuityAllocation.organization_id == PayGratuityBatch.organization_id)
            & (PayGratuityAllocation.batch_id == PayGratuityBatch.id),
        )
        .filter(
            PayGratuityBatch.organization_id == org_id,
            PayGratuityBatch.week_start == week_start,
            PayGratuityBatch.rehearsal.is_(False),
            PayGratuityAllocation.excluded_at.is_(None),
            PayGratuityAllocation.is_payable.is_(True),
            PayGratuityAllocation.payroll_venue_id.is_not(None),
        )
        .all()
    )
    return [
        (b.venue_id, a.payroll_venue_id, a)
        for b, a in rows
        if a.payroll_venue_id != b.venue_id
    ]


def for_venue(
    db: Session,
    org_id: str,
    venue_id: str,
    week_start: dt.date,
    *,
    own_facts: list[PayShiftFact] | None = None,
    entity: PayEntity | None = None,
    all_facts: dict[str, list[PayShiftFact]] | None = None,
) -> dict:
    """One entity's intercompany picture for the week (xero-api.md §4), plus
    ``out_facts`` — the other entities' facts worked here, which the
    reconciliation adds to its timeclock total."""
    names = _venue_names(db, org_id)
    if all_facts is None:
        all_facts = current_close_facts(db, org_id, week_start)
    own = own_facts if own_facts is not None else all_facts.get(venue_id, [])
    warnings: list[str] = []

    in_facts = [f for f in own if f.is_from_other_venue and not _leave(f)]
    unknown = [f for f in in_facts if not f.worked_venue_id]
    if unknown:
        where = sorted({str(f.worked_venue_loaded_id or "?") for f in unknown})
        warnings.append(
            f"{len(unknown)} shift{'s' if len(unknown) != 1 else ''} worked at a "
            f"Loaded venue Norm doesn't know ({', '.join(where)}) — counted as "
            "intercompany with no counterparty mapping"
        )
    out_facts = [
        f
        for other_id, facts in all_facts.items()
        if other_id != venue_id
        for f in facts
        if f.is_from_other_venue and f.worked_venue_id == venue_id and not _leave(f)
    ]
    # The other entity is the counterparty of an Out row: find it by the
    # snapshot the fact came from.
    payroll_venue_of = {
        f.id: other_id
        for other_id, facts in all_facts.items()
        if other_id != venue_id
        for f in facts
    }
    in_groups = _group(
        in_facts,
        lambda f: (f.worked_venue_id, str(f.worked_venue_loaded_id or "unknown venue")),
        names,
    )
    out_groups = _group(
        out_facts,
        lambda f: (payroll_venue_of.get(f.id), "unknown entity"),
        names,
    )
    for earned_at, paid_by, share in _gratuity_recharges(db, org_id, week_start):
        if paid_by == venue_id:
            _add_gratuity_line(
                in_groups, earned_at, names.get(earned_at, earned_at), share
            )
        elif earned_at == venue_id:
            _add_gratuity_line(out_groups, paid_by, names.get(paid_by, paid_by), share)
    in_cents = sum(g["amount_cents"] for g in in_groups)
    out_cents = sum(g["amount_cents"] for g in out_groups)
    for g in in_groups:
        g["direction"] = "in"
    for g in out_groups:
        g["direction"] = "out"
    return {
        "venue_id": venue_id,
        "venue": names.get(venue_id, venue_id),
        "in": in_groups,
        "out": out_groups,
        "in_cents": in_cents,
        "out_cents": out_cents,
        "net_cents": in_cents - out_cents,
        "out_facts": out_facts,
        "warnings": warnings,
    }


def picture(
    db: Session, ctx: FinanceCtx, week_start: dt.date, venue_id: str | None = None
) -> dict:
    """The Intercompany page: one entity's view, or every entity the caller
    may see. Contacts from the venue's Xero mapping ride along so the page
    can say which counterparty has no Xero contact yet."""
    from app.services.finance_core.ctx import require_venue
    from app.services.payroll import xero_mapping
    from app.services.payroll.reconciliation import week_bounds

    ctx.require("payroll:read")
    monday, sunday = week_bounds(week_start)
    if venue_id:
        require_venue(ctx, venue_id)
        venue_ids = [venue_id]
    else:
        venue_ids = [
            v.id
            for v in db.query(Venue)
            .filter(Venue.organization_id == ctx.org_id, ctx.venue_clause(Venue.id))
            .order_by(Venue.name)
            .all()
        ]
    all_facts = current_close_facts(db, ctx.org_id, monday)
    venues = []
    for vid in venue_ids:
        view = for_venue(db, ctx.org_id, vid, monday, all_facts=all_facts)
        view.pop("out_facts")
        contacts = xero_mapping.contacts_of(db, vid)
        for g in view["in"] + view["out"]:
            g["contact"] = contacts.get(g["counterparty_venue_id"] or "")
        if view["in"] or view["out"] or vid == venue_id:
            venues.append(view)
    return {
        "week_start": monday.isoformat(),
        "week_ending": sunday.isoformat(),
        "venues": venues,
    }


def by_counterparty_lines(view: dict) -> dict[tuple[str, str], dict]:
    """``{(kind, counterparty venue id): group}`` for the ICO draft builder —
    ``ico_invoice`` for In groups, ``ico_bill`` for Out; groups with no
    mapped counterparty venue are left out (nothing to address them to)."""
    out: dict[tuple[str, str], dict] = {}
    for g in view["in"]:
        if g["counterparty_venue_id"] and g["lines"]:
            out[("ico_invoice", g["counterparty_venue_id"])] = g
    for g in view["out"]:
        if g["counterparty_venue_id"] and g["lines"]:
            out[("ico_bill", g["counterparty_venue_id"])] = g
    return out


__all__ = [
    "by_counterparty_lines",
    "current_close_facts",
    "for_venue",
    "picture",
]
