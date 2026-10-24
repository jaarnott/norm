"""The wage journal and the intercompany documents as Norm builds them —
``pay_journal_drafts`` (plan §10 "Journal", report 03 §8.3, xero-api.md §5).

Orbit's journal, line for line, with two things Orbit left to a person:

* **An "Other team" line.** Orbit's P&L side came from Loaded's role summary
  by cost group, so a role with no cost group fell out and the journal only
  balanced once someone plugged the gap with a manual line to Leave &
  Entitlements or Rounding (29 + 2 such lines). Here the debits are the
  snapshot's facts split by ``cost_group`` with ``other`` posted as its own
  line, so the journal balances from the data.
* **A "Reclass Gratuities" line** for the gratuity shares paid through
  payroll this week. The reconciliation counts them (they are in Ace gross),
  so without a debit the journal cannot balance in a gratuity week; Orbit
  only had the never-used tagged version. Its account is the accountant's
  choice (``xero_mapping.payroll.gratuities_reclass``, Payroll settings →
  Xero mapping).
  Until it is chosen a gratuity week's journal is REFUSED by name rather
  than built without the line: leaving the debit out would either fail the
  balance check with a less useful message or — for shares under the $1
  rounding limit — quietly post the gratuities to another line's account.
  A week with no gratuities never needs the key.

Everything is computed from the stored reconciliation (``reconciliation.compute``)
and the venue's mapping; the draft row carries ``basis_sha256`` over all of
it, so a push can refuse a draft whose inputs moved. Nothing here reads Xero:
the push consolidators do that, on the card.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlalchemy.orm import Session

from app.db.models import Venue
from app.db.payroll_models import PayEntity, PayJournalDraft, PayXeroPush
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid, NotFound
from app.services.payroll import reconciliation as recon_svc
from app.services.payroll import xero_mapping, xero_pushes

#: A residual this small or smaller is absorbed by the largest line when no
#: rounding account is mapped (Orbit's push corrected 1-cent drift the same way).
ABSORB_CENTS = 2
#: A residual this large or larger refuses the draft (Orbit's rule: under $1
#: a Rounding line, $1 or more a warning and no push).
REFUSE_CENTS = 100


def we_label(week_ending: dt.date) -> str:
    """Orbit's ``dd.mm.yyyy`` in narrations and references."""
    return f"{week_ending.day:02d}.{week_ending.month:02d}.{week_ending.year}"


def narration(venue_name: str, week_ending: dt.date) -> str:
    return f"Payroll Journal - {venue_name} - WE {we_label(week_ending)}"


def ico_reference(week_ending: dt.date) -> str:
    return f"ICO Wages WE {we_label(week_ending)}"


def _line(key, description, side, cents, code, names, source=None) -> dict:
    return {
        "key": key,
        "description": description,
        "account_code": code,
        "account_name": names.get(code) if code else None,
        "side": side,
        "amount_cents": int(cents),
        "source": source or {},
    }


def build_lines(recon: dict, payroll_map: dict, names: dict) -> dict:
    """The journal lines from a reconciliation and a mapping. Returns
    ``{lines, debit_cents, credit_cents, residual_cents, blockers, notes,
    needed}`` — ``needed`` is every mapping key a non-zero line wanted."""
    t = recon["terms"]
    ace = recon["ace"] or {
        "gross_cents": 0,
        "net_cents": 0,
        "esct_cents": 0,
        "kiwisaver_cents": 0,
    }
    taxes = (
        ace["gross_cents"]
        - ace["net_cents"]
        + ace["esct_cents"]
        + ace["kiwisaver_cents"]
    )
    unpaid = t["accrued_additions"]["cents"] - t["accrued_deductions"]["cents"]
    ico = t["intercompany"]["cents"]
    groups = t["timeclock"]["by_cost_group"]
    leave_ent = t["leave_paid"]["cents"] + ace["esct_cents"] + ace["kiwisaver_cents"]

    wanted: list[tuple] = [
        (
            "wages_payable",
            "Net Wages",
            "CR",
            ace["net_cents"],
            {"ace_id": (recon["ace"] or {}).get("id")},
        ),
        (
            "payroll_taxes_payable",
            "Payroll Taxes",
            "CR",
            taxes,
            {"ace_id": (recon["ace"] or {}).get("id")},
        ),
        (
            "unpaid_wages",
            "Unpaid Wages",
            "CR" if unpaid >= 0 else "DR",
            abs(unpaid),
            {"accrual_id": t["accrued_additions"]["row_id"]},
        ),
        (
            "security_owed",
            "Security Owed",
            "CR",
            t["security"]["cents"],
            {"fact_ids": t["security"]["fact_ids"]},
        ),
        (
            "intercompany_wages",
            "Intercompany — To Be Invoiced"
            if ico >= 0
            else "Intercompany — To Be Billed",
            "DR" if ico >= 0 else "CR",
            abs(ico),
            {
                "in_cents": t["intercompany"]["in_cents"],
                "out_cents": t["intercompany"]["out_cents"],
            },
        ),
        (
            "bar_floor",
            "Bar & Floor Team",
            "DR",
            groups.get("bar_floor", 0),
            {"cost_group": "bar_floor"},
        ),
        (
            "kitchen",
            "Kitchen Team",
            "DR",
            groups.get("kitchen", 0),
            {"cost_group": "kitchen"},
        ),
        (
            "other_team",
            "Other team",
            "DR",
            groups.get("other", 0),
            {"cost_group": "other"},
        ),
        (
            "leave_entitlements",
            "Leave & Entitlements",
            "DR",
            leave_ent,
            {
                "leave_paid_cents": t["leave_paid"]["cents"],
                "payout_ids": [r["id"] for r in t["leave_paid"]["rows"]],
            },
        ),
        (
            "entertainers",
            "Entertainers (via payroll)",
            "DR",
            t["entertainers"]["cents"],
            {
                "performance_ids": [
                    r["performance_id"] for r in t["entertainers"]["rows"]
                ]
            },
        ),
        (
            "gratuities_reclass",
            "Reclass Gratuities",
            "DR",
            t["gratuities"]["cents"],
            {"allocation_ids": [r["allocation_id"] for r in t["gratuities"]["rows"]]},
        ),
    ]
    lines: list[dict] = []
    needed: set[str] = set(xero_mapping.ALWAYS_NEEDED)
    blockers: list[str] = []
    notes: list[str] = []
    for key, description, side, cents, source in wanted:
        if cents == 0 and key not in xero_mapping.ALWAYS_NEEDED:
            continue
        needed.add(key)
        if cents == 0:
            continue
        lines.append(
            _line(key, description, side, cents, payroll_map.get(key), names, source)
        )
    for adj in t["other_adjustments"]["rows"]:
        amount = adj["amount_cents"]
        if amount == 0:
            continue
        if not adj["xero_account_code"]:
            continue  # the reconciliation already names it as a blocker
        lines.append(
            {
                "key": f"adjustment:{adj['id']}",
                "description": adj["label"] or "Adjustment",
                "account_code": adj["xero_account_code"],
                "account_name": adj["xero_account_name"]
                or names.get(adj["xero_account_code"]),
                "side": "DR" if amount > 0 else "CR",
                "amount_cents": abs(amount),
                "source": {
                    "adjustment_id": adj["id"],
                    "report_target": adj["report_target"],
                },
            }
        )

    debit = sum(ln["amount_cents"] for ln in lines if ln["side"] == "DR")
    credit = sum(ln["amount_cents"] for ln in lines if ln["side"] == "CR")
    residual = debit - credit
    if residual != 0:
        if abs(residual) >= REFUSE_CENTS:
            blockers.append(
                f"the journal does not balance: DR ${debit / 100:,.2f} vs CR ${credit / 100:,.2f} "
                f"(residual ${residual / 100:,.2f}) — review the ACE inputs and adjustments"
            )
        elif payroll_map.get("rounding"):
            needed.add("rounding")
            lines.append(
                _line(
                    "rounding",
                    "Rounding",
                    "CR" if residual > 0 else "DR",
                    abs(residual),
                    payroll_map["rounding"],
                    names,
                    {"residual_cents": residual},
                )
            )
            notes.append(f"a {abs(residual)}c residual posts to the Rounding account")
        elif abs(residual) <= ABSORB_CENTS:
            # The side that is short takes the cents on its largest line.
            short = "CR" if residual > 0 else "DR"
            biggest = max(
                (ln for ln in lines if ln["side"] == short),
                key=lambda ln: ln["amount_cents"],
                default=None,
            )
            if biggest is None:
                blockers.append("the journal has no line to absorb its residual")
            else:
                biggest["amount_cents"] += abs(residual)
                biggest["source"] = {
                    **biggest["source"],
                    "absorbed_residual_cents": residual,
                }
                notes.append(
                    f"a {abs(residual)}c residual was absorbed by '{biggest['description']}' "
                    "(no rounding account is mapped)"
                )
        else:
            needed.add("rounding")
            blockers.append(
                f"a {abs(residual)}c residual needs the Rounding account, which is not mapped"
            )
    missing = xero_mapping.missing_for_journal(payroll_map, needed)
    for k in missing:
        if k == "gratuities_reclass":
            blockers.append(
                f"Xero mapping missing: gratuities_reclass — this week pays "
                f"${t['gratuities']['cents'] / 100:,.2f} of gratuities through payroll and "
                "the account for the 'Reclass Gratuities' line isn't chosen yet "
                "(Payroll settings → Xero mapping)"
            )
        else:
            blockers.append(f"Xero mapping missing: {k}")
    debit = sum(ln["amount_cents"] for ln in lines if ln["side"] == "DR")
    credit = sum(ln["amount_cents"] for ln in lines if ln["side"] == "CR")
    return {
        "lines": lines,
        "debit_cents": debit,
        "credit_cents": credit,
        "residual_cents": residual,
        "blockers": blockers,
        "notes": notes,
        "needed": sorted(needed),
        "missing": missing,
    }


def ico_documents(recon: dict, ico_map: dict, uplift_bp: int) -> list[dict]:
    """One document per counterparty with ≥ 1 line: ``ico_invoice`` for the
    In groups (we invoice), ``ico_bill`` for the Out groups (they bill us).
    Groups with no mapped counterparty venue have nobody to address, so they
    are left out and stay a warning on the reconciliation."""
    docs = []
    for g in recon["terms"]["intercompany"]["counterparties"]:
        if not g.get("counterparty_venue_id") or not g.get("lines"):
            continue
        kind = "ico_invoice" if g["direction"] == "in" else "ico_bill"
        lines = [
            {
                "staff_member_id": ln["staff_member_id"],
                "staff_name": ln["staff_name"],
                "hours": ln["hours"],
                "amount_cents": ln["amount_cents"],
                "gratuity": bool(ln.get("gratuity")),
                "fact_ids": ln.get("fact_ids", []),
            }
            for ln in g["lines"]
            if ln["amount_cents"]
        ]
        if not lines:
            continue
        wages = sum(ln["amount_cents"] for ln in lines)
        uplift = int(round(wages * int(uplift_bp) / 10000))
        docs.append(
            {
                "kind": kind,
                "counterparty_venue_id": g["counterparty_venue_id"],
                "counterparty": g["counterparty"],
                "lines": lines
                + [
                    {
                        "key": "leave_uplift",
                        "bp": int(uplift_bp),
                        "amount_cents": uplift,
                    }
                ],
                "wages_cents": wages,
                "uplift_cents": uplift,
                "subtotal_cents": wages + uplift,
                "wages_account": ico_map.get("wages_account"),
                "leave_account": ico_map.get("leave_account")
                or ico_map.get("wages_account"),
            }
        )
    return docs


def basis_hash(recon: dict, payroll_map: dict, ico_map: dict, uplift_bp: int) -> str:
    """SHA-256 over everything the drafts were built from: the snapshot, the
    ACE inputs, adjustments, accruals, payouts, gratuities, entertainer rows,
    the intercompany picture, and the mapping codes used."""
    t = recon["terms"]
    basis = {
        "snapshot": recon["snapshot"]
        and {
            "id": recon["snapshot"]["id"],
            "content_sha256": recon["snapshot"]["content_sha256"],
        },
        "timeclock": {
            "cents": t["timeclock"]["cents"],
            "by_cost_group": t["timeclock"]["by_cost_group"],
            "facts": hashlib.sha256(
                ",".join(sorted(t["timeclock"]["fact_ids"])).encode()
            ).hexdigest(),
        },
        "ace": recon["ace"]
        and {
            k: recon["ace"][k]
            for k in ("gross_cents", "net_cents", "esct_cents", "kiwisaver_cents")
        },
        "adjustments": [
            [r["id"], r["amount_cents"], r["xero_account_code"]]
            for r in t["other_adjustments"]["rows"]
        ],
        "accrual": [t["accrued_additions"]["cents"], t["accrued_deductions"]["cents"]],
        "payouts": [[r["id"], r["paid_cents"]] for r in t["leave_paid"]["rows"]],
        "security": t["security"]["cents"],
        "gratuities": [
            [r["allocation_id"], r["amount_cents"], r["payroll_venue_id"]]
            for r in t["gratuities"]["rows"]
        ],
        "entertainers": [
            [r["performance_id"], r["amount_cents"]] for r in t["entertainers"]["rows"]
        ],
        "intercompany": [
            [g["direction"], g["counterparty_venue_id"], g["amount_cents"]]
            for g in t["intercompany"]["counterparties"]
        ],
        "mapping": {k: payroll_map.get(k) for k in xero_mapping.PAYROLL_KEYS},
        "ico_mapping": {
            "wages_account": ico_map.get("wages_account"),
            "leave_account": ico_map.get("leave_account"),
            "uplift_bp": int(uplift_bp),
        },
    }
    return hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def xero_journal(
    lines: list[dict],
    venue_name: str,
    week_ending: dt.date,
    tracking: dict | None = None,
) -> dict:
    """The Xero ManualJournal object (DRAFT, NoTax — D10)."""
    items = []
    for ln in lines:
        cents = int(ln["amount_cents"])
        if not cents:
            continue
        item = {
            "LineAmount": round(cents / 100 if ln["side"] == "DR" else -cents / 100, 2),
            "AccountCode": ln["account_code"],
            "Description": ln["description"],
        }
        if tracking and tracking.get("category_id") and tracking.get("option_id"):
            item["Tracking"] = [
                {
                    "TrackingCategoryID": tracking["category_id"],
                    "TrackingOptionID": tracking["option_id"],
                }
            ]
        items.append(item)
    return {
        "Narration": narration(venue_name, week_ending),
        "Date": week_ending.isoformat(),
        "Status": "DRAFT",
        "LineAmountTypes": "NoTax",
        "JournalLines": items,
    }


# ---------------------------------------------------------------------------
# Assemble, store, read
# ---------------------------------------------------------------------------


def assemble(db: Session, ctx: FinanceCtx, venue_id: str, week_start: dt.date) -> dict:
    """Everything a draft would hold, from the stored facts, without storing
    it: the reconciliation, the lines, the ICO documents, the basis hash and
    the blockers as they stand now."""
    ctx.require("payroll:read")
    require_venue(ctx, venue_id)
    monday, _ = recon_svc.week_bounds(week_start)
    payroll_map = xero_mapping.payroll_of(db, venue_id)
    names = xero_mapping.account_names_of(db, venue_id)
    ico_map = xero_mapping.intercompany_of(db, venue_id)
    recon = recon_svc.compute(db, ctx, venue_id, monday)
    built = build_lines(recon, payroll_map, names)
    uplift = recon["entity"]["ico_leave_uplift_bp"]
    docs = ico_documents(recon, ico_map, uplift)
    blockers = list(recon["blockers"]) + built["blockers"]
    if docs and not ico_map.get("wages_account"):
        blockers.append("Xero mapping missing: intercompany.wages_account")
    if not xero_mapping.connection(db, venue_id):
        blockers.append("this venue is not connected to Xero")
    return {
        "recon": recon,
        "lines": built["lines"],
        "debit_cents": built["debit_cents"],
        "credit_cents": built["credit_cents"],
        "residual_cents": built["residual_cents"],
        "notes": built["notes"],
        "needed": built["needed"],
        "ico_documents": docs,
        "basis_sha256": basis_hash(recon, payroll_map, ico_map, uplift),
        "blockers": blockers,
        "narration": narration(
            recon["venue"], dt.date.fromisoformat(recon["week_ending"])
        ),
    }


def _drafts_of(db: Session, org_id: str, week_id: str) -> list[PayJournalDraft]:
    return (
        db.query(PayJournalDraft)
        .filter(
            PayJournalDraft.organization_id == org_id,
            PayJournalDraft.week_id == week_id,
        )
        .order_by(PayJournalDraft.built_at.desc(), PayJournalDraft.id)
        .all()
    )


def _pushed_draft_ids(db: Session, org_id: str, week_id: str) -> set[str]:
    return {
        r[0]
        for r in db.query(PayXeroPush.draft_id)
        .filter(
            PayXeroPush.organization_id == org_id,
            PayXeroPush.week_id == week_id,
            PayXeroPush.draft_id.is_not(None),
        )
        .all()
    }


def build(db: Session, ctx: FinanceCtx, venue_id: str, week_start: dt.date) -> dict:
    """Build and store the week's journal draft and ICO drafts, replacing
    earlier unpushed drafts. Refuses, naming every blocker, while one stands.
    Flushes; the route commits."""
    ctx.require("payroll:write")
    out = assemble(db, ctx, venue_id, week_start)
    if out["blockers"]:
        raise Invalid("The journal can't be built yet: " + "; ".join(out["blockers"]))
    recon = out["recon"]
    week_id = recon["week_id"]
    snapshot_id = recon["snapshot"]["id"]
    keep = _pushed_draft_ids(db, ctx.org_id, week_id)
    for old in _drafts_of(db, ctx.org_id, week_id):
        if old.id not in keep:
            db.delete(old)
    db.flush()
    journal = PayJournalDraft(
        organization_id=ctx.org_id,
        week_id=week_id,
        snapshot_id=snapshot_id,
        kind="journal",
        lines=out["lines"],
        debit_cents=out["debit_cents"],
        credit_cents=out["credit_cents"],
        residual_cents=out["residual_cents"],
        basis_sha256=out["basis_sha256"],
        built_by=ctx.user_id,
    )
    db.add(journal)
    ico_rows = []
    for doc in out["ico_documents"]:
        row = PayJournalDraft(
            organization_id=ctx.org_id,
            week_id=week_id,
            snapshot_id=snapshot_id,
            kind=doc["kind"],
            counterparty_venue_id=doc["counterparty_venue_id"],
            lines=doc["lines"],
            debit_cents=doc["subtotal_cents"] if doc["kind"] == "ico_invoice" else 0,
            credit_cents=doc["subtotal_cents"] if doc["kind"] == "ico_bill" else 0,
            residual_cents=0,
            basis_sha256=out["basis_sha256"],
            built_by=ctx.user_id,
        )
        db.add(row)
        ico_rows.append(row)
    db.flush()
    return {**current(db, ctx, venue_id, week_start, assembled=out), "built": True}


def draft_dict(row: PayJournalDraft) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "week_id": row.week_id,
        "snapshot_id": row.snapshot_id,
        "counterparty_venue_id": row.counterparty_venue_id,
        "lines": row.lines or [],
        "debit_cents": row.debit_cents,
        "credit_cents": row.credit_cents,
        "residual_cents": row.residual_cents,
        "basis_sha256": row.basis_sha256,
        "built_at": row.built_at.isoformat() if row.built_at else None,
        "built_by": row.built_by,
    }


def current(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    week_start: dt.date,
    *,
    assembled: dict | None = None,
) -> dict:
    """The stored drafts (the latest journal, the latest ICO draft per
    counterparty), the live pushes, and the blockers as they stand now —
    with ``stale`` when the inputs have moved since the draft was built."""
    ctx.require("payroll:read")
    out = assembled or assemble(db, ctx, venue_id, week_start)
    recon = out["recon"]
    journal = None
    ico: dict[tuple[str, str], PayJournalDraft] = {}
    if recon["week_id"]:
        for row in _drafts_of(db, ctx.org_id, recon["week_id"]):
            if row.kind == "journal":
                journal = journal or row
            elif (row.kind, row.counterparty_venue_id) not in ico:
                ico[(row.kind, row.counterparty_venue_id)] = row
    names = dict(
        db.query(Venue.id, Venue.name).filter(Venue.organization_id == ctx.org_id).all()
    )
    leave_taxes = leave_tax_rules(db, ctx.org_id)
    contacts = xero_mapping.contacts_of(db, venue_id)
    monday = dt.date.fromisoformat(recon["week_start"])
    live = {
        (p.kind, p.counterparty_venue_id): p
        for p in xero_pushes.live_pushes_for_week(db, ctx.org_id, venue_id, monday)
    }
    tracking = xero_mapping.mapping_of(db, venue_id).get("tracking") or {}
    week_ending = dt.date.fromisoformat(recon["week_ending"])
    journal_out = None
    if journal is not None:
        journal_out = {
            **draft_dict(journal),
            "stale": journal.basis_sha256 != out["basis_sha256"],
            "journal": xero_journal(
                journal.lines or [], recon["venue"], week_ending, tracking
            ),
            "narration": narration(recon["venue"], week_ending),
            "live_push": xero_pushes.brief(live[("journal", None)])
            if ("journal", None) in live
            else None,
        }
    ico_out = []
    for (kind, cp), row in sorted(
        ico.items(),
        key=lambda kv: (names.get(kv[0][1], ""), 0 if kv[0][0] == "ico_invoice" else 1),
    ):
        lines = row.lines or []
        uplift = next((ln for ln in lines if ln.get("key") == "leave_uplift"), {})
        people = [ln for ln in lines if ln.get("key") != "leave_uplift"]
        wages = sum(int(ln.get("amount_cents") or 0) for ln in people)
        ico_out.append(
            {
                **draft_dict(row),
                "counterparty": names.get(cp, cp),
                "contact": contacts.get(cp),
                "wages_cents": wages,
                "uplift_bp": uplift.get("bp"),
                "uplift_cents": int(uplift.get("amount_cents") or 0),
                "subtotal_cents": wages + int(uplift.get("amount_cents") or 0),
                # The other side of this recharge is built from ITS entity's
                # rule: the push card says when the two would disagree.
                "counterparty_leave_tax": leave_taxes.get(cp, "account_default"),
                "stale": row.basis_sha256 != out["basis_sha256"],
                "live_push": xero_pushes.brief(live[(kind, cp)])
                if (kind, cp) in live
                else None,
            }
        )
    return {
        "venue_id": venue_id,
        "venue": recon["venue"],
        "organization_id": ctx.org_id,
        "week_id": recon["week_id"],
        "week_start": recon["week_start"],
        "week_ending": recon["week_ending"],
        "journal": journal_out,
        "ico": ico_out,
        "proposed": {
            "lines": out["lines"],
            "debit_cents": out["debit_cents"],
            "credit_cents": out["credit_cents"],
            "residual_cents": out["residual_cents"],
            "notes": out["notes"],
            "ico_documents": out["ico_documents"],
            "basis_sha256": out["basis_sha256"],
        },
        "blockers": out["blockers"],
        "warnings": recon["warnings"],
        "intercompany": {
            "wages_account": xero_mapping.intercompany_of(db, venue_id).get(
                "wages_account"
            ),
            "leave_account": xero_mapping.intercompany_of(db, venue_id).get(
                "leave_account"
            ),
            "uplift_bp": recon["entity"]["ico_leave_uplift_bp"],
            "leave_tax": recon["entity"]["ico_leave_tax"],
        },
        "live_pushes": [xero_pushes.brief(p) for p in live.values()],
    }


def leave_tax_rules(db: Session, org_id: str) -> dict[str, str]:
    """Every entity's tax rule for the intercompany leave line, by venue —
    read organisation-wide like the intercompany picture: a counterparty's
    rule is needed whether or not the caller can see its venue."""
    return dict(
        db.query(PayEntity.venue_id, PayEntity.ico_leave_tax)
        .filter(PayEntity.organization_id == org_id)
        .all()
    )


def get_draft(db: Session, ctx: FinanceCtx, draft_id: str) -> PayJournalDraft:
    ctx.require("payroll:read")
    row = (
        db.query(PayJournalDraft)
        .filter(
            PayJournalDraft.organization_id == ctx.org_id,
            PayJournalDraft.id == draft_id,
        )
        .first()
    )
    if row is None:
        raise NotFound("Journal draft not found.")
    return row
