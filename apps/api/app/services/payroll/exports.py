"""The four Ace Payroll import files (plan §9 step 6, D7) — byte for byte
Orbit's formats (``PayrunReport.tsx``), generated from the week's CLOSE
snapshot and stored in ``pay_exports`` with their content and hash, so a
re-export diffs and the parity engine compares bytes with the owner's files.

What differs from Orbit, on purpose:

* **Nothing is skipped silently.** Orbit dropped a person without a pay
  code with a toast; here a missing pay code REFUSES the file and lists who
  (an unpaid person is a payroll error, not a warning).
* **The leave style comes from ``pay_leave_styles``**, not from a word in the
  type's name, and an unmapped type refuses the file (Orbit sent unpaid and
  every unrecognised type to #70, the annual-leave style — parity class
  ``leave_style_default``).
* The Ace pay-type code (``50001``), the security pay code and the gratuity
  allowance code are entity settings, not literals.

Numbers are written as JavaScript's ``toFixed`` writes them (``to_fixed``:
round half up on the exact binary value), so a figure Orbit wrote as
``12.13`` is written the same here.
"""

from __future__ import annotations

import csv
import datetime as dt
import difflib
import hashlib
import io
import logging
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.db.models import _now
from app.db.payroll_models import (
    EXPORT_KINDS,
    PayAccrual,
    PayAceInput,
    PayAdjustment,
    PayEntity,
    PayExport,
    PayLeaveStyle,
    PayWeek,
)
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush
from app.services.payroll import gratuities as gratuities_svc
from app.services.payroll import leave as leave_svc
from app.services.payroll import snapshot as snapshot_svc
from app.services.payroll import weeks as weeks_svc
from app.services.payroll import entities as entities_svc

logger = logging.getLogger(__name__)

CRLF = "\r\n"
FILENAMES = {
    "import": "IMPORT.TXT",
    "leave": "IMPORT_LEAVE.TXT",
    "gratuities": "GRATUITIES.TXT",
    "entertainers": "IMPORT_ENTERTAINERS.TXT",
}
#: Orbit's style numbers for the leave types it recognised.
DEFAULT_STYLES = {"annual": "70", "sick": "60", "bereavement": "63"}
#: The line shape per (style, remuneration) — Orbit's branches, named.
METHODS = (
    "days_daily_rate",  # #70 salaried: days + daily rate
    "days_hours_rate",  # #70 hourly: days + hours + hourly rate (amount blank — Ace rejects it)
    "days_only",  # #60/#63 salaried: entitlement reduction only
    "hours_rate_days",  # #60/#63 hourly: hours × rate, days taken
    "hours_rate",  # any other style
)


def default_methods(style: str | None) -> tuple[str | None, str | None]:
    """(salaried, hourly) methods Orbit used for a style."""
    if style == "70":
        return "days_daily_rate", "days_hours_rate"
    if style in ("60", "63"):
        return "days_only", "hours_rate_days"
    if style:
        return "hours_rate", "hours_rate"
    return None, None


# ================================================================ numbers ==


def to_fixed(value, places: int = 2) -> str:
    """``Number.prototype.toFixed``: round half up on the number's exact
    binary value — what Orbit's browser wrote."""
    exact = Decimal(float(value))
    quantum = Decimal(1).scaleb(-places)
    text = format(exact.quantize(quantum, rounding=ROUND_HALF_UP), "f")
    if text.startswith("-") and Decimal(text) == 0:
        text = text[1:]
    return text


def _name_key(name: str | None):
    """``localeCompare``'s order, near enough: case-blind, then as typed."""
    text = name or ""
    return (text.casefold(), text)


def _join(lines: list[str]) -> str:
    return CRLF.join(lines) + CRLF if lines else ""


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ============================================================ leave styles ==


def seed_leave_styles(
    db: Session, ctx: FinanceCtx, venue_id: str, *, config_db=None
) -> dict:
    """One ``pay_leave_styles`` row per Loaded leave type at the venue
    (``get_leave_types``), prefilled as Orbit mapped them and left unsigned.
    Existing rows are kept."""
    ctx.require("payroll:write")
    venue = weeks_svc.venue_of(db, ctx, venue_id)
    payload = snapshot_svc.loaded(
        db, ctx, venue, "get_leave_types", {}, config_db=config_db
    )
    rows = payload if isinstance(payload, list) else None
    if rows is None:
        rows = next(
            (v for v in payload.values() if isinstance(v, list)),
            [],
        )
    existing = {
        s.loaded_leave_type_id: s
        for s in db.query(PayLeaveStyle).filter(
            PayLeaveStyle.organization_id == ctx.org_id,
            PayLeaveStyle.venue_id == venue.id,
        )
    }
    created = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("id") is None or r.get("deletedAt"):
            continue
        type_id = str(r["id"])
        if type_id in existing:
            continue
        name = str(r.get("name") or "").strip() or type_id
        style = None
        lowered = name.lower()
        for word, number in DEFAULT_STYLES.items():
            if word in lowered:
                style = number
                break
        salaried, hourly = default_methods(style)
        db.add(
            PayLeaveStyle(
                organization_id=ctx.org_id,
                venue_id=venue.id,
                loaded_leave_type_id=type_id,
                name=name,
                ace_style=style,
                salaried_method=salaried,
                hourly_method=hourly,
            )
        )
        created += 1
    flush(db)
    return {"created": created, "styles": list_leave_styles(db, ctx, venue.id)}


def list_leave_styles(
    db: Session, ctx: FinanceCtx, venue_id: str
) -> list[PayLeaveStyle]:
    require_venue(ctx, venue_id)
    return (
        db.query(PayLeaveStyle)
        .filter(
            PayLeaveStyle.organization_id == ctx.org_id,
            PayLeaveStyle.venue_id == venue_id,
        )
        .order_by(PayLeaveStyle.name)
        .all()
    )


def get_leave_style(db: Session, ctx: FinanceCtx, style_id: str) -> PayLeaveStyle:
    row = (
        db.query(PayLeaveStyle)
        .filter(
            PayLeaveStyle.organization_id == ctx.org_id, PayLeaveStyle.id == style_id
        )
        .first()
    )
    if row is None or not ctx.can_see_venue(row.venue_id):
        raise NotFound("Leave style not found.")
    return row


def update_leave_style(
    db: Session,
    ctx: FinanceCtx,
    style_id: str,
    *,
    version: int,
    ace_style=None,
    salaried_method=None,
    hourly_method=None,
    sign_off: bool = False,
) -> PayLeaveStyle:
    ctx.require("payroll:write")
    row = get_leave_style(db, ctx, style_id)
    if row.version != version:
        raise Conflict(
            "This leave style was changed by someone else — reload and try again."
        )
    if ace_style is not None:
        text = str(ace_style).strip().lstrip("#")
        if text and not text.isdigit():
            raise Invalid("ace_style is the Ace style number (70, 60, 63…) or blank.")
        changed = (text or None) != row.ace_style
        row.ace_style = text or None
        if changed or not row.salaried_method or not row.hourly_method:
            # The line shapes belong to the style: a new number takes its shapes
            # (a method given in the same call still wins, below).
            row.salaried_method, row.hourly_method = default_methods(row.ace_style)
    for name, value in (
        ("salaried_method", salaried_method),
        ("hourly_method", hourly_method),
    ):
        if value is not None:
            if value not in METHODS:
                raise Invalid(f"{name} is one of {', '.join(METHODS)}.")
            setattr(row, name, value)
    if sign_off:
        row.signed_off_by = ctx.user_id
        row.signed_off_at = _now()
    else:
        # Any change to the mapping needs a fresh sign-off.
        row.signed_off_by, row.signed_off_at = None, None
    row.version = version + 1
    flush(db)
    return row


def style_dict(row: PayLeaveStyle) -> dict:
    return {
        "id": row.id,
        "venue_id": row.venue_id,
        "loaded_leave_type_id": row.loaded_leave_type_id,
        "name": row.name,
        "ace_style": row.ace_style,
        "salaried_method": row.salaried_method,
        "hourly_method": row.hourly_method,
        "signed_off_by": row.signed_off_by,
        "signed_off_at": row.signed_off_at.isoformat() if row.signed_off_at else None,
        "version": row.version,
    }


def _type_key(name: str | None) -> str:
    text = (name or "").strip().casefold()
    for prefix in ("new zealand ", "nz "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def style_for(
    styles: list[PayLeaveStyle], leave_type_name: str | None
) -> PayLeaveStyle | None:
    """The style row for a leave fact's type name (exact, then with the
    'NZ' / 'New Zealand' prefix ignored)."""
    wanted = (leave_type_name or "").strip().casefold()
    for s in styles:
        if s.name.strip().casefold() == wanted:
            return s
    loose = _type_key(leave_type_name)
    for s in styles:
        if _type_key(s.name) == loose:
            return s
    return None


# ================================================================ writers ==


def import_lines(entity: PayEntity, facts) -> tuple[list[str], dict, list[str]]:
    """``IMPORT.TXT``: per pay code, ``#52,<code>,<pay type>,<hours>,<rate>``,
    rate = cost ÷ hours (the public-holiday premium stays inside cost, as
    Orbit wrote it); leave and security excluded. Returns (lines, totals,
    names without a pay code)."""
    security = entity.security_pay_code or "SECURITY"
    by_code: "OrderedDict[str, dict]" = OrderedDict()
    missing: list[str] = []
    for f in facts:
        if f.shift_type == "Leave" or (f.pay_code or "") == security:
            continue
        if not f.pay_code:
            if f.staff_name not in missing:
                missing.append(f.staff_name or "?")
            continue
        entry = by_code.setdefault(
            f.pay_code, {"hours": Decimal("0"), "cost_cents": 0, "name": f.staff_name}
        )
        entry["hours"] += f.hours or 0
        entry["cost_cents"] += f.cost_cents
    lines = []
    for code, e in sorted(by_code.items(), key=lambda kv: _name_key(kv[1]["name"])):
        hours = float(e["hours"])
        rate = (e["cost_cents"] / 100.0) / hours if hours > 0 else 0.0
        lines.append(
            f"#52,{code},{entity.ace_pay_type_code},{to_fixed(hours)},{to_fixed(rate)}"
        )
    totals = {
        "lines": len(lines),
        "pay_codes": len(by_code),
        "hours": float(sum((e["hours"] for e in by_code.values()), Decimal("0"))),
        "cost_cents": sum(e["cost_cents"] for e in by_code.values()),
    }
    return lines, totals, missing


def leave_lines(
    entity: PayEntity, facts, styles: list[PayLeaveStyle], venue
) -> tuple[list[str], dict, list[str], list[str]]:
    """``IMPORT_LEAVE.TXT``: one line per (pay code, style), salaried and
    hourly variants, ``daysTaken`` = distinct local dates (3 dp). Returns
    (lines, totals, names without a pay code, unmapped type names)."""
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    missing: list[str] = []
    unmapped: list[str] = []
    skipped_zero: list[str] = []
    for f in facts:
        if f.shift_type != "Leave":
            continue
        if not f.pay_code:
            if f.staff_name not in missing:
                missing.append(f.staff_name or "?")
            continue
        if (f.hours or 0) <= 0:
            if f.staff_name not in skipped_zero:
                skipped_zero.append(f.staff_name or "?")
            continue
        style = style_for(styles, f.leave_type_name)
        if style is None or not style.ace_style:
            label = f.leave_type_name or "(no type)"
            if label not in unmapped:
                unmapped.append(label)
            continue
        key = (f.pay_code, style.ace_style)
        g = groups.get(key)
        day = leave_svc.local_date(f.clockin_at, venue) or (
            f.trading_date.isoformat() if f.trading_date else None
        )
        if g is None:
            g = groups[key] = {
                "code": f.pay_code,
                "name": f.staff_name,
                "remuneration_type": f.remuneration_type,
                "style": style,
                "dates": set(),
                "hours": Decimal("0"),
                "cost_cents": 0,
                "types": [],
            }
        if day:
            g["dates"].add(day)
        g["hours"] += f.hours or 0
        g["cost_cents"] += f.cost_cents
        if f.leave_type_name and f.leave_type_name not in g["types"]:
            g["types"].append(f.leave_type_name)
    lines = []
    by_style: dict[str, int] = {}
    unsigned: list[str] = []
    for g in sorted(
        groups.values(), key=lambda g: (_name_key(g["name"]), int(g["style"].ace_style))
    ):
        style: PayLeaveStyle = g["style"]
        number = style.ace_style
        code = g["code"]
        days_n = len(g["dates"])
        days = to_fixed(days_n, 3)
        hours = float(g["hours"])
        cost = g["cost_cents"] / 100.0
        salaried = (g["remuneration_type"] or "").strip().lower() == "salary"
        method = (style.salaried_method if salaried else style.hourly_method) or (
            default_methods(number)[0 if salaried else 1]
        )
        hourly_rate = to_fixed(cost / hours) if hours > 0 else ""
        if method == "days_daily_rate":
            daily = to_fixed(cost / days_n) if days_n > 0 else ""
            lines.append(f"#{number},{code},0,{days},{daily},,,,")
        elif method == "days_hours_rate":
            lines.append(
                f"#{number},{code},0,{days},,,,{to_fixed(hours)},{hourly_rate}"
            )
        elif method == "days_only":
            lines.append(f"#{number},{code},0,,,,{days},")
        elif method == "hours_rate_days":
            lines.append(f"#{number},{code},0,{to_fixed(hours)},{hourly_rate},,{days},")
        else:
            rate = cost / hours if hours > 0 else 0.0
            lines.append(f"#{number},{code},0,{to_fixed(hours)},{to_fixed(rate)}")
        by_style[number] = by_style.get(number, 0) + 1
        if style.signed_off_at is None and style.name not in unsigned:
            unsigned.append(style.name)
    totals = {
        "lines": len(lines),
        "by_style": by_style,
        "skipped_zero_hours": skipped_zero,
        "unsigned_styles": unsigned,
        "hours": float(sum((g["hours"] for g in groups.values()), Decimal("0"))),
        "cost_cents": sum(g["cost_cents"] for g in groups.values()),
    }
    return lines, totals, missing, sorted(unmapped, key=str.casefold)


def gratuity_lines(entity: PayEntity, shares) -> tuple[list[str], dict, list[str]]:
    """``GRATUITIES.TXT``: ``#51,<code>,<allowance code>,<amount>`` per pay
    code — the shares this entity's payroll pays this week."""
    by_code: "OrderedDict[str, dict]" = OrderedDict()
    missing: list[str] = []
    for _batch, a in shares:
        if not a.pay_code:
            if a.staff_name not in missing:
                missing.append(a.staff_name or "?")
            continue
        e = by_code.setdefault(a.pay_code, {"amount_cents": 0, "name": a.staff_name})
        e["amount_cents"] += a.amount_cents
    lines = [
        f"#51,{code},{entity.ace_gratuity_allowance_code},{to_fixed(e['amount_cents'] / 100.0)}"
        for code, e in sorted(by_code.items(), key=lambda kv: _name_key(kv[1]["name"]))
    ]
    totals = {
        "lines": len(lines),
        "amount_cents": sum(e["amount_cents"] for e in by_code.values()),
        "shares": len(shares),
    }
    return lines, totals, missing


def entertainer_lines(entity: PayEntity, items) -> tuple[list[str], dict, list[str]]:
    """``IMPORT_ENTERTAINERS.TXT``: ``#52,<code>,<pay type>,<hours>,<rate>``
    per payroll code from the entertainers module's lines (hours from the
    actual times; rate = amount ÷ hours)."""
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    missing: list[str] = []
    for line in items:
        code = _attr(line, "pay_code")
        hours = float(_attr(line, "hours") or 0)
        amount = _attr(line, "amount_cents")
        if amount is None:
            amount = int(round(float(_attr(line, "rate_cents") or 0) * hours))
        if not code:
            label = (
                _attr(line, "performer_name") or _attr(line, "performance_id") or "?"
            )
            if label not in missing:
                missing.append(str(label))
            continue
        g = grouped.setdefault(
            str(code), {"hours": 0.0, "amount_cents": 0, "performances": []}
        )
        g["hours"] += hours
        g["amount_cents"] += int(amount)
        g["performances"].append(_attr(line, "performance_id"))
    lines = []
    for code, g in grouped.items():
        rate = (g["amount_cents"] / 100.0) / g["hours"] if g["hours"] > 0 else 0.0
        lines.append(
            f"#52,{code},{entity.ace_pay_type_code},{to_fixed(g['hours'])},{to_fixed(rate)}"
        )
    totals = {
        "lines": len(lines),
        "hours": round(sum(g["hours"] for g in grouped.values()), 2),
        "amount_cents": sum(g["amount_cents"] for g in grouped.values()),
        "performance_ids": [p for g in grouped.values() for p in g["performances"]],
    }
    return lines, totals, missing


def _attr(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def entertainer_items(
    db: Session, ctx: FinanceCtx, venue_id: str, week_start
) -> tuple[list, bool]:
    """F5's ``entertainers.payments.payroll_lines`` — imported lazily; an
    empty list (and ``available=False``) while the module is not built."""
    try:
        from app.services.entertainers import payments  # type: ignore
    except ImportError:
        return [], False
    fn = getattr(payments, "payroll_lines", None)
    if fn is None:
        return [], False
    return list(fn(db, ctx, venue_id, week_start) or []), True


# ================================================================ building ==


def build(
    db: Session, ctx: FinanceCtx, week: PayWeek, kind: str, *, snapshot=None
) -> dict:
    """One file's content from the close snapshot (or ``snapshot``), with its
    totals and refusals — nothing stored."""
    if kind not in EXPORT_KINDS:
        raise Invalid(f"kind is one of {', '.join(EXPORT_KINDS)}.")
    venue = weeks_svc.venue_of(db, ctx, week.venue_id)
    entity = entities_svc.get_or_create(db, ctx, venue.id)
    snapshot = snapshot or weeks_svc.close_snapshot(db, week)
    if snapshot is None:
        raise Invalid(
            "No close snapshot for this week — press Start close (or wait for "
            "Tuesday's close snapshot) before exporting."
        )
    facts = snapshot_svc.facts_of(db, snapshot)
    refusals: list[str] = []
    if kind == "import":
        lines, totals, missing = import_lines(entity, facts)
        if missing:
            refusals.append("Staff without a pay code in Loaded: " + ", ".join(missing))
    elif kind == "leave":
        styles = list_leave_styles(db, ctx, venue.id)
        lines, totals, missing, unmapped = leave_lines(entity, facts, styles, venue)
        if missing:
            refusals.append(
                "Leave for staff without a pay code in Loaded: " + ", ".join(missing)
            )
        if unmapped:
            refusals.append(
                "Leave types with no Ace style on the Payroll settings page: "
                + ", ".join(unmapped)
            )
    elif kind == "gratuities":
        shares = gratuities_svc.payable_by(db, ctx, venue.id, week.week_start)
        if shares and not entity.ace_gratuity_allowance_code:
            refusals.append(
                "No gratuity allowance code for this entity — set it on the Payroll settings page."
            )
        lines, totals, missing = gratuity_lines(entity, shares)
        if missing:
            refusals.append(
                "Gratuity shares for staff without a pay code: " + ", ".join(missing)
            )
    else:
        items, available = entertainer_items(db, ctx, venue.id, week.week_start)
        lines, totals, missing = entertainer_lines(entity, items)
        totals["available"] = available
        if missing:
            refusals.append(
                "Entertainers without a payroll code: " + ", ".join(missing)
            )
    content = _join(lines)
    return {
        "kind": kind,
        "filename": FILENAMES[kind],
        "content": content,
        "content_sha256": _sha(content),
        "line_count": len(lines),
        "totals": totals,
        "refusals": refusals,
        "snapshot_id": snapshot.id,
    }


def generate(db: Session, ctx: FinanceCtx, week: PayWeek, kinds=EXPORT_KINDS) -> dict:
    """Build and store the files (``payroll:write``). Every refusal is
    collected first; with any, nothing is stored. Allowed in shadow — an
    export never leaves Norm, and the shadow period compares it."""
    ctx.require("payroll:write")
    kinds = [k for k in (kinds or EXPORT_KINDS)]
    built = [build(db, ctx, week, k) for k in kinds]
    refusals = [r for b in built for r in b["refusals"]]
    if refusals:
        raise Invalid("Export refused — " + " · ".join(refusals))
    previous = {
        e.kind: e for e in weeks_svc.exports_of(db, week)
    }  # newest first → first wins
    stored, diffs = [], {}
    for b in built:
        row = PayExport(
            organization_id=ctx.org_id,
            week_id=week.id,
            snapshot_id=b["snapshot_id"],
            kind=b["kind"],
            filename=b["filename"],
            content=b["content"],
            content_sha256=b["content_sha256"],
            line_count=b["line_count"],
            totals=b["totals"],
            generated_by=ctx.user_id,
        )
        db.add(row)
        stored.append(row)
        prior = previous.get(b["kind"])
        diffs[b["kind"]] = (
            diff_text(prior.content, b["content"]) if prior is not None else None
        )
    flush(db)
    return {"exports": stored, "diffs": diffs}


def diff_text(before: str, after: str) -> dict:
    """Line-level differences between two files."""
    a = before.split(CRLF) if before else []
    b = after.split(CRLF) if after else []
    a = [x for x in a if x != ""]
    b = [x for x in b if x != ""]
    added = [x for x in b if x not in a]
    removed = [x for x in a if x not in b]
    unified = list(difflib.unified_diff(a, b, lineterm="", n=0))
    return {
        "same": a == b,
        "added": added,
        "removed": removed,
        "unified": unified[2:] if len(unified) > 2 else [],
    }


def get_export(db: Session, ctx: FinanceCtx, export_id: str) -> PayExport:
    row = (
        db.query(PayExport)
        .filter(PayExport.organization_id == ctx.org_id, PayExport.id == export_id)
        .first()
    )
    if row is None:
        raise NotFound("Export not found.")
    weeks_svc.get(db, ctx, row.week_id)
    return row


def export_dict(row: PayExport, *, content: bool = False) -> dict:
    out = {
        "id": row.id,
        "week_id": row.week_id,
        "snapshot_id": row.snapshot_id,
        "kind": row.kind,
        "filename": row.filename,
        "content_sha256": row.content_sha256,
        "line_count": row.line_count,
        "totals": row.totals,
        "generated_by": row.generated_by,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }
    if content:
        out["content"] = row.content
    return out


# =========================================================== fallback pack ==


def rekey_rows(db: Session, ctx: FinanceCtx, week: PayWeek) -> list[dict]:
    """Every Norm-authored input for the week, flat, for re-keying into Orbit
    (plan §15 rollback): payouts, gratuity shares, ACE inputs, adjustments,
    accruals. Rehearsal rows are left out — they were keyed in Orbit too."""
    rows: list[dict] = []
    for p in leave_svc.list_payouts(
        db, ctx, venue_id=week.venue_id, week_start=week.week_start
    ):
        if p.source != "norm" or p.rehearsal:
            continue
        rows.append(
            {
                "kind": "leave_payout",
                "id": p.id,
                "staff_member_id": p.staff_member_id,
                "staff_name": p.staff_name,
                "reference": p.loaded_request_id,
                "label": p.leave_type,
                "requested_cents": p.requested_cents,
                "paid_cents": p.paid_cents,
                "detail": p.workflow_kind,
                "notes": p.notes,
            }
        )
    for batch, a in gratuities_svc.payable_by(db, ctx, week.venue_id, week.week_start):
        if batch.source != "norm":
            continue
        rows.append(
            {
                "kind": "gratuity_share",
                "id": a.id,
                "staff_member_id": a.staff_member_id,
                "staff_name": a.staff_name,
                "reference": batch.id,
                "label": f"{batch.period} pool at venue {batch.venue_id}",
                "requested_cents": batch.total_cents,
                "paid_cents": a.amount_cents,
                "detail": f"{float(a.hours or 0)} h; pay code {a.pay_code or ''}",
                "notes": batch.notes,
            }
        )
    ace = (
        db.query(PayAceInput)
        .filter(
            PayAceInput.organization_id == ctx.org_id, PayAceInput.week_id == week.id
        )
        .first()
    )
    if ace is not None and ace.source == "norm" and not ace.rehearsal:
        rows.append(
            {
                "kind": "ace_inputs",
                "id": ace.id,
                "staff_member_id": None,
                "staff_name": None,
                "reference": week.week_start.isoformat(),
                "label": "gross / net / ESCT / KiwiSaver",
                "requested_cents": ace.gross_cents,
                "paid_cents": ace.net_cents,
                "detail": f"esct {ace.esct_cents}; kiwisaver {ace.kiwisaver_cents}",
                "notes": None,
            }
        )
    for adj in (
        db.query(PayAdjustment)
        .filter(
            PayAdjustment.organization_id == ctx.org_id,
            PayAdjustment.week_id == week.id,
        )
        .order_by(PayAdjustment.sort_order)
        .all()
    ):
        if adj.source != "norm" or adj.rehearsal:
            continue
        rows.append(
            {
                "kind": "adjustment",
                "id": adj.id,
                "staff_member_id": None,
                "staff_name": None,
                "reference": adj.xero_account_code,
                "label": adj.label,
                "requested_cents": None,
                "paid_cents": adj.amount_cents,
                "detail": adj.report_target,
                "notes": adj.notes,
            }
        )
    acc = (
        db.query(PayAccrual)
        .filter(PayAccrual.organization_id == ctx.org_id, PayAccrual.week_id == week.id)
        .first()
    )
    if acc is not None and acc.source == "norm":
        rows.append(
            {
                "kind": "accrual",
                "id": acc.id,
                "staff_member_id": None,
                "staff_name": None,
                "reference": week.week_start.isoformat(),
                "label": "accrued wages",
                "requested_cents": acc.accrued_cents,
                "paid_cents": acc.paid_cents,
                "detail": f"opening {acc.opening_cents}",
                "notes": None,
            }
        )
    return rows


def _csv_cell(value):
    """A text cell a spreadsheet would run as a formula — one starting with
    ``=``, ``+``, ``-``, ``@``, a tab or a carriage return — is prefixed with
    an apostrophe, which Excel and Sheets read as "text". Notes, labels and
    staff names come from people and from Loaded; the sheet is opened in
    Excel (security review, 20 Oct 2026). Numbers are left as numbers."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def fallback_pack(db: Session, ctx: FinanceCtx, week: PayWeek) -> dict:
    """The week's four files (the latest stored of each kind, else built now
    without storing) plus ``REKEY.csv`` of every Norm-authored input."""
    ctx.require("payroll:read")
    latest = {}
    for e in weeks_svc.exports_of(db, week):
        latest.setdefault(e.kind, e)
    files: dict[str, str] = {}
    notes: list[str] = []
    for kind in EXPORT_KINDS:
        if kind in latest:
            files[FILENAMES[kind]] = latest[kind].content
            continue
        try:
            built = build(db, ctx, week, kind)
        except Invalid as exc:
            files[FILENAMES[kind]] = ""
            notes.append(f"{FILENAMES[kind]}: {exc.message}")
            continue
        files[FILENAMES[kind]] = built["content"]
        if built["refusals"]:
            notes.append(f"{FILENAMES[kind]}: " + "; ".join(built["refusals"]))
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator=CRLF)
    columns = [
        "kind",
        "id",
        "staff_member_id",
        "staff_name",
        "reference",
        "label",
        "requested_cents",
        "paid_cents",
        "detail",
        "notes",
    ]
    writer.writerow(columns)
    for r in rekey_rows(db, ctx, week):
        writer.writerow(
            ["" if r.get(c) is None else _csv_cell(r.get(c)) for c in columns]
        )
    files["REKEY.csv"] = buf.getvalue()
    return {
        "week_id": week.id,
        "week_start": week.week_start.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "files": files,
        "notes": notes,
    }
