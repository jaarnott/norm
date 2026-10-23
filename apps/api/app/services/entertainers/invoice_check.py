"""The invoice check — plain code over what the reader printed (plan §11.5).

Six findings, each with its reason, each raised by its own case only:

1. ``total`` — the invoice total against the approved amount of the
   performances it covers (Σ ``actual_cents``, plus GST at the tenant's rate
   when the performer is GST registered); more than $1 apart is a query.
2. ``gst`` — GST charged by a performer the register has as not registered,
   or a printed GST number that differs from the registered one.
3. ``duplicate_number`` — an invoice number this performer has used before.
4. ``payee`` — a payee name matching none of the performer's legal, trading
   or company names (or their own name).
5. ``dates`` — printed dates that are not the selected performances' dates.
6. ``unreadable`` — a file the reader could not read.

Outcome: ``matched`` (the bill carries the invoice's own lines when they add
up to the approved total), ``query`` (the findings on Payments; finance
sends the invoice-query email or waives with a note), ``unreadable``.
Nothing is ever rescaled (Orbit's Gemini lines were scaled to the shift
amount, so a wrong invoice still looked right — 02 §9 item 13).

The job ``ent.invoice_checks`` runs every tick over files uploaded and not
yet checked, at most 100 per organisation per day; "check now" runs one by
hand.
"""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session

from app.db.entertainers_models import EntFile, EntPerformance, EntPerformer
from app.db.models import Organization, Venue, _now
from app.services.entertainers import emails
from app.services.entertainers import invoice_reader
from app.services.entertainers._shared import day, iso, load
from app.services.finance_core.ctx import FinanceCtx, require_staff
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush

#: Query when the total is more than this far from the approved figure.
TOLERANCE_CENTS = 100
#: GST rates by organisation currency (basis points) — the tenant's rate
#: as far as the check needs it; the bill itself takes the rate from Xero.
GST_RATE_BP = {"NZD": 1500, "AUD": 1000}
CHECKS_PER_ORG_PER_DAY = 100


def gst_rate_bp(db: Session, org_id: str) -> int:
    currency = (
        db.query(Organization.currency).filter(Organization.id == org_id).scalar()
        or "NZD"
    )
    return GST_RATE_BP.get(str(currency).upper(), 1500)


# ---------------------------------------------------------------------------
# Pure findings
# ---------------------------------------------------------------------------


def _cents(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(str(value).replace("$", "").replace(",", "")) * 100))
    except (TypeError, ValueError):
        return None


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _norm_name(value) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", str(value or "").lower())
    text = re.sub(r"\b(limited|ltd|the|t/a|trading as)\b", " ", text)
    return " ".join(text.split())


def _name_matches(payee: str, candidates: list[str | None]) -> bool:
    p = _norm_name(payee)
    if not p:
        return False
    for c in candidates:
        n = _norm_name(c)
        if not n:
            continue
        if p == n or p in n or n in p:
            return True
        # Every word of the shorter name in the longer one ("Sam Smith" vs
        # "Sam J Smith Entertainment").
        short, long_ = sorted((set(p.split()), set(n.split())), key=len)
        if short and short <= long_:
            return True
    return False


def _parse_day(value) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(
                text[:10] if fmt == "%Y-%m-%d" else text, fmt
            ).date()
        except ValueError:
            continue
    return None


def findings(
    extracted: dict,
    *,
    performer: EntPerformer,
    performances: list[EntPerformance],
    gst_bp: int,
    previous_numbers: set[str],
) -> list[dict]:
    """The findings for one invoice against the performances it covers.
    Pure: no database, so every case is a plain unit test."""
    out: list[dict] = []
    if (
        not isinstance(extracted, dict)
        or extracted.get("error")
        or not any(
            extracted.get(k) not in (None, "", [])
            for k in ("total", "lines", "payee_name", "invoice_number")
        )
    ):
        return [
            {
                "code": "unreadable",
                "message": "Norm couldn't read this file — a person needs to look at it.",
            }
        ]

    approved = sum(p.actual_cents or 0 for p in performances)
    expected = approved
    gst_registered = bool(performer.gst_registered) and performer.lane != "exemption"
    if gst_registered:
        expected = approved + approved * gst_bp // 10_000
    total = _cents(extracted.get("total"))
    if total is None:
        total = _cents(extracted.get("subtotal"))
        gst_amount = _cents(extracted.get("gst")) or 0
        total = (total + gst_amount) if total is not None else None
    # An approved performance with no amount comes from Orbit (an approved
    # shift whose actual amount was never set); Norm's own approve always sets
    # one. Comparing it as $0 told finance "differs from the approved $0.00"
    # on a correct invoice (10-invoice trial, 23 Oct 2026) — say what is
    # actually wrong instead.
    unpriced = sorted(
        p.performance_date for p in performances if p.actual_cents is None
    )
    if unpriced:
        out.append(
            {
                "code": "total",
                "message": (
                    "No approved amount is recorded for "
                    + ", ".join(d.isoformat() for d in unpriced)
                    + ", so the invoice total can't be checked."
                ),
                "expected": None,
                "found": total,
            }
        )
    elif total is None:
        out.append(
            {
                "code": "total",
                "message": "No total is printed on the invoice.",
                "expected": expected,
                "found": None,
            }
        )
    elif abs(total - expected) > TOLERANCE_CENTS:
        out.append(
            {
                "code": "total",
                "message": (
                    f"The invoice total ${total / 100:,.2f} differs from the approved "
                    f"${expected / 100:,.2f} ({'including' if gst_registered else 'no'} GST)."
                ),
                "expected": expected,
                "found": total,
            }
        )

    gst_printed = _cents(extracted.get("gst"))
    printed_number = _digits(extracted.get("gst_number"))
    if not gst_registered and gst_printed:
        out.append(
            {
                "code": "gst",
                "message": "The invoice charges GST but the register has this performer as not GST registered.",
                "found": gst_printed,
            }
        )
    elif (
        gst_registered
        and printed_number
        and _digits(performer.gst_number)
        and printed_number.zfill(9) != _digits(performer.gst_number).zfill(9)
    ):
        out.append(
            {
                "code": "gst",
                "message": "The GST number on the invoice differs from the one on the register.",
                "found": printed_number,
            }
        )

    number = " ".join(str(extracted.get("invoice_number") or "").split()).upper()
    if number and number in {n.upper() for n in previous_numbers}:
        out.append(
            {
                "code": "duplicate_number",
                "message": f"Invoice number {number} has been used by this performer before.",
                "found": number,
            }
        )

    payee = extracted.get("payee_name")
    if payee and not _name_matches(
        payee,
        [
            performer.legal_name,
            performer.trading_name,
            performer.company_name,
            performer.name,
        ],
    ):
        out.append(
            {
                "code": "payee",
                "message": f"The payee '{payee}' matches none of the performer's names on the register.",
                "found": payee,
            }
        )

    wanted = {p.performance_date for p in performances}
    printed_days = set()
    for line in extracted.get("lines") or []:
        if isinstance(line, dict):
            d = _parse_day(line.get("date"))
            if d:
                printed_days.add(d)
    strays = sorted(d for d in printed_days if d not in wanted)
    if strays:
        out.append(
            {
                "code": "dates",
                "message": "The invoice names dates that are not the selected performances': "
                + ", ".join(d.isoformat() for d in strays)
                + ".",
                "found": [d.isoformat() for d in strays],
                "expected": sorted(d.isoformat() for d in wanted),
            }
        )
    return out


def lines_add_up(extracted: dict, approved_cents: int) -> list[dict] | None:
    """The invoice's own lines, when their amounts sum to the approved
    (GST-exclusive) total — for the bill. Else None: one line per
    performance instead. Never scaled."""
    lines = [ln for ln in (extracted or {}).get("lines") or [] if isinstance(ln, dict)]
    amounts = [_cents(ln.get("amount")) for ln in lines]
    if not lines or any(a is None for a in amounts):
        return None
    if sum(amounts) != approved_cents:
        return None
    return [
        {
            "description": " ".join(
                str(ln.get("description") or "Performance").split()
            )[:200],
            "amount_cents": a,
        }
        for ln, a in zip(lines, amounts)
    ]


# ---------------------------------------------------------------------------
# Running a check
# ---------------------------------------------------------------------------


def performances_of(db: Session, file: EntFile) -> list[EntPerformance]:
    return (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == file.organization_id,
            EntPerformance.invoice_file_id == file.id,
        )
        .order_by(EntPerformance.performance_date)
        .all()
    )


def previous_numbers(db: Session, file: EntFile, performer_id: str) -> set[str]:
    """Invoice numbers on this performer's OTHER invoice files."""
    rows = (
        db.query(EntFile.invoice_number, EntFile.extracted)
        .join(EntPerformance, EntPerformance.invoice_file_id == EntFile.id)
        .filter(
            EntFile.organization_id == file.organization_id,
            EntFile.id != file.id,
            EntPerformance.performer_id == performer_id,
        )
        .all()
    )
    out = set()
    for number, extracted in rows:
        if number:
            out.add(" ".join(str(number).split()))
        if isinstance(extracted, dict) and extracted.get("invoice_number"):
            out.add(" ".join(str(extracted["invoice_number"]).split()))
    return out


def run_check(
    db: Session, file: EntFile, *, now: dt.datetime | None = None, reader=None
) -> EntFile:
    """Read (once) and check one invoice file; writes the outcome on the
    row. ``reader`` defaults to ``invoice_reader.read_file`` — tests pass a
    fake."""
    now = now or _now()
    reader = reader or invoice_reader.read_file
    rows = performances_of(db, file)
    if not rows:
        file.check_status = "unreadable"
        file.check_findings = [
            {"code": "unreadable", "message": "This invoice covers no performance."}
        ]
        file.checked_at = now
        flush(db)
        return file
    performer = (
        db.query(EntPerformer).filter(EntPerformer.id == rows[0].performer_id).first()
    )
    extracted = reader(db, file)
    if (
        isinstance(extracted, dict)
        and not extracted.get("error")
        and file.extracted != extracted
    ):
        # The cache lives on the content-addressed row, whichever reader
        # produced the reading (the model, or a test's fake).
        file.extracted = extracted
    if isinstance(extracted, dict) and extracted.get("transient"):
        # Anthropic unavailable: leave it pending for the next tick, not
        # "unreadable" (the supplier-invoice lesson of 24 Aug 2026).
        file.check_findings = [
            {
                "code": "pending",
                "message": "The reader was unavailable — Norm will try again.",
            }
        ]
        flush(db)
        return file
    found = findings(
        extracted,
        performer=performer,
        performances=rows,
        gst_bp=gst_rate_bp(db, file.organization_id),
        previous_numbers=previous_numbers(db, file, performer.id),
    )
    if any(f["code"] == "unreadable" for f in found):
        file.check_status = "unreadable"
    elif found:
        file.check_status = "query"
    else:
        file.check_status = "matched"
    file.check_findings = found
    file.checked_at = now
    if isinstance(extracted, dict) and not extracted.get("error"):
        number = extracted.get("invoice_number")
        if number and not file.invoice_number:
            file.invoice_number = " ".join(str(number).split())[:100]
    flush(db)
    return file


def pending_files(db: Session, org_id: str, *, limit: int = 100) -> list[EntFile]:
    return (
        db.query(EntFile)
        .filter(
            EntFile.organization_id == org_id,
            EntFile.kind == "invoice",
            EntFile.check_status == "pending",
        )
        .order_by(EntFile.created_at)
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------


def file_dict(db: Session, ctx: FinanceCtx, file: EntFile) -> dict:
    ids = [p.id for p in performances_of(db, file)]
    out = {
        "id": file.id,
        "kind": file.kind,
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "sha256": file.sha256,
        "invoice_number": file.invoice_number,
        "invoice_cents": file.invoice_cents,
        "check_status": file.check_status,
        "checked_at": iso(file.checked_at),
        "waived_by": file.waived_by,
        "waived_at": iso(file.waived_at),
        "findings": file.check_findings or [],
        "performance_ids": ids,
        "created_at": iso(file.created_at),
    }
    if ctx.holds("payroll:write"):
        out["extracted"] = file.extracted
    return out


def _file(db: Session, ctx: FinanceCtx, file_id: str) -> EntFile:
    require_staff(ctx)
    ctx.require("payroll:write")
    file = load(db, ctx, EntFile, file_id, label="Invoice")
    if file.kind != "invoice":
        raise NotFound("Invoice not found.")
    return file


def get_file(db: Session, ctx: FinanceCtx, file_id: str) -> EntFile:
    return _file(db, ctx, file_id)


def check_now(db: Session, ctx: FinanceCtx, file_id: str, *, reader=None) -> EntFile:
    file = _file(db, ctx, file_id)
    if file.check_status == "waived":
        raise Conflict("This invoice was waived — nothing to check.")
    return run_check(db, file, reader=reader)


def waive(db: Session, ctx: FinanceCtx, file_id: str, *, note: str | None) -> EntFile:
    file = _file(db, ctx, file_id)
    text = " ".join(str(note or "").split())
    if not text:
        raise Invalid("Say why the query is waived.")
    file.check_status = "waived"
    file.waived_by = ctx.user_id
    file.waived_at = _now()
    file.check_findings = [
        *(file.check_findings or []),
        {"code": "waived", "message": text[:1000]},
    ]
    flush(db)
    return file


def send_query(db: Session, ctx: FinanceCtx, file_id: str) -> dict:
    """The invoice-query email to the performer's registered address —
    figures by email, never on the link."""
    file = _file(db, ctx, file_id)
    if file.check_status not in ("query", "unreadable"):
        raise Conflict("Only an invoice with a query can be sent back.")
    rows = performances_of(db, file)
    if not rows:
        raise Conflict("This invoice covers no performance.")
    performer = (
        db.query(EntPerformer).filter(EntPerformer.id == rows[0].performer_id).first()
    )
    if performer is None or not performer.email:
        raise Invalid("The performer has no email address on the register.")
    venues = {
        v.id: v.name
        for v in db.query(Venue).filter(Venue.id.in_({r.venue_id for r in rows}))
    }
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    approved = sum(p.actual_cents or 0 for p in rows)
    context = {
        "first_name": emails.first_name(performer.name),
        "invoice_number": file.invoice_number or "(no number)",
        "performances": [
            {
                "venue": venues.get(p.venue_id),
                "date_text": emails.date_text(p.performance_date),
                "amount_text": emails.money_text(p.actual_cents),
            }
            for p in rows
        ],
        "expected_text": emails.money_text(approved),
        "findings": [
            f["message"]
            for f in (file.check_findings or [])
            if f.get("code") != "waived"
        ],
        "contact_email": getattr(org, "payroll_contact_email", None) or "",
    }
    org_id, performer_id, address, file_id = (
        ctx.org_id,
        performer.id,
        performer.email,
        file.id,
    )
    ids = [p.id for p in rows]
    venue_id = rows[0].venue_id

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind="ent_invoice_query",
            to=address,
            context=context,
            performer_id=performer_id,
            venue_id=venue_id,
            performance_ids=ids,
            target_id=f"invoice:{file_id}",
        )

    emails.later(db, _send)
    return {"sent": True}


def invoice_bill_lines(
    db: Session, file: EntFile | None, perf: EntPerformance
) -> list[dict] | None:
    """The matched invoice's own lines for this performance's bill, when
    the invoice covers only this performance and its lines add up."""
    if (
        file is None
        or file.check_status != "matched"
        or not isinstance(file.extracted, dict)
    ):
        return None
    covered = performances_of(db, file)
    if [p.id for p in covered] != [perf.id]:
        return None
    return lines_add_up(file.extracted, perf.actual_cents or 0)


def performance_day(perf: EntPerformance) -> str:
    return day(perf.performance_date)
