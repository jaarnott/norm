"""The performer register (plan §11.1–11.2, D14, D15).

A performer is an identity with a payment **lane** — ``payroll_employee``
(PAYE, set by finance only), ``payroll_contractor`` (withholding tax through
payroll, the IR330C declaration), ``invoice`` (company, agency, band leader,
partnership — a bill in Xero) or ``exemption`` (certificate, no WT, no GST)
— and the compliance record the lane needs.

Who may do what: anyone with access to a venue adds performers, invites them
and edits identity; a finance person (``payroll:write``) sets the lane and
the payroll fields, accepts a submission, and is the only one who sees the
stored bank account, IRD number and date of birth — on the Register as
"account ending 34", in full only through ``sensitive``, which is logged.

**Payable** (the rule every pay line and bill checks): active, accepted, the
bank account not changed since acceptance, a lane set; a
``payroll_contractor`` holds a declaration (signed on the form or uploaded);
an ``exemption`` performer's certificate has not expired; a ``members_split``
band is payable only when every member is.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    ENT_FILE_KINDS,
    LANES,
    PERFORMER_TYPES,
    EntBandMember,
    EntFile,
    EntPerformer,
)
from app.db.models import _now
from app.services.entertainers import emails, tokens
from app.services.entertainers._shared import (
    check_version,
    day,
    iso,
    load_performer,
    parse_date,
    require_any_venue,
)
from app.services.finance_core import access_log
from app.services.finance_core.ctx import FinanceCtx, require_staff
from app.services.finance_core.errors import Conflict, Forbidden, Invalid, flush

logger = logging.getLogger(__name__)

#: Days ahead of ``exemption_expiry_date`` the Register flags it.
EXEMPTION_WARNING_DAYS = 30
IDENTITY_FIELDS = (
    "name",
    "legal_name",
    "trading_name",
    "email",
    "phone",
    "notes",
    "performer_type",
)
#: Set by finance only (payroll:write).
FINANCE_FIELDS = (
    "lane",
    "band_mode",
    "payroll_code",
    "tax_code",
    "kiwisaver_rate",
    "gst_registered",
    "gst_number",
    "company_name",
    "company_number",
    "exemption_certificate_number",
    "exemption_expiry_date",
    "needs_review",
    "review_reason",
    "wt_rate_type",
    "wt_rate_bp",
)
MAX_NAME = 200
MAX_TEXT = 2000
MAX_UPLOAD = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(value: str | None, *, required: bool = False) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        if required:
            raise Invalid("An email address is required.")
        return None
    if len(text) > 320 or not _EMAIL.match(text):
        raise Invalid("That email address doesn't look right.")
    return text


def clean_text(
    value, what: str, *, limit: int = MAX_NAME, required: bool = False
) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        if required:
            raise Invalid(f"{what} is required.")
        return None
    if len(text) > limit:
        raise Invalid(f"{what} is too long (keep it under {limit} characters).")
    return text


_IRD_PRIMARY = (3, 2, 7, 6, 5, 4, 3, 2)
_IRD_SECONDARY = (7, 4, 3, 2, 5, 2, 7, 6)


def normalise_ird(value: str | None) -> str:
    """An IRD (or GST) number as nine digits, or ``Invalid`` — Inland
    Revenue's published check: 8 or 9 digits, in range, and the ninth digit
    is the modulus-11 check of the first eight (a second weighting when the
    first gives 10)."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) not in (8, 9):
        raise Invalid("An IRD number has 8 or 9 digits.")
    digits = digits.zfill(9)
    if not 10_000_000 <= int(digits) <= 150_000_000:
        raise Invalid("That IRD number is out of range.")
    base = [int(d) for d in digits[:8]]
    check = int(digits[8])

    def _check(weights) -> int:
        remainder = sum(d * w for d, w in zip(base, weights)) % 11
        return 0 if remainder == 0 else 11 - remainder

    computed = _check(_IRD_PRIMARY)
    if computed == 10:
        computed = _check(_IRD_SECONDARY)
    if computed == 10 or computed != check:
        raise Invalid("That IRD number isn't valid — check the digits.")
    return digits


# ---------------------------------------------------------------------------
# NZ bank account numbers — Inland Revenue's validation (Payroll Calculations
# and Business Rules Specification 2026–27, §7 "Bank account number
# validation"). Mirrored in apps/web/.../entertainers/logic.ts: change both.
# ---------------------------------------------------------------------------

#: Step 1: the banks IR lists today and their algorithm. Every bank not named
#: here uses A, or B when the account base is 00990000 or more.
_BANK_ALGORITHM = {"08": "D", "25": "F", "31": "X"}
#: The branch numbers each listed bank uses. IR points at Payments NZ's Bank
#: Branch Register for these; the ranges are that register's, widened to the
#: blocks each bank holds — they covered all 3,295 branches in the register
#: downloaded on 23 Oct 2026. A bank that opens a branch outside them needs
#: the range here (and in logic.ts) widened.
_BANK_BRANCHES: dict[str, tuple[tuple[int, int], ...]] = {
    "01": ((1, 999), (1100, 1199), (1800, 1899), (6150, 6150)),
    "02": ((1, 999), (1200, 1299), (2025, 2055)),
    "03": (
        (1, 999),
        (1300, 1399),
        (1500, 1599),
        (1700, 1799),
        (1900, 1999),
        (5000, 5099),
        (7350, 7399),
    ),
    "04": ((2014, 2024),),
    "05": ((8884, 8889),),
    "06": ((1, 999), (1400, 1499)),
    "08": ((6500, 6599),),
    "10": ((5165, 5169),),
    "11": ((5000, 6499), (6600, 8999)),
    "12": ((3000, 3299), (3400, 3499), (3600, 3699)),
    "13": ((4900, 4999),),
    "14": ((4700, 4799),),
    "15": ((3900, 3999),),
    "16": ((4400, 4499),),
    "17": ((3300, 3399),),
    "18": ((3500, 3599),),
    "19": ((4600, 4649),),
    "20": ((4100, 4199),),
    "21": ((4800, 4899),),
    "22": ((4000, 4049),),
    "23": ((3700, 3799),),
    "24": ((4300, 4349),),
    "25": ((2500, 2599),),
    "27": ((3800, 3849),),
    "30": ((2900, 2949),),
    "31": ((2800, 2849),),
    "38": ((9000, 9499),),
    "88": ((8800, 8805),),
}
#: Step 2: weights over the 18 digits bank(2) branch(4) base(8) suffix(4),
#: and the modulus the weighted sum must divide by. C is in IR's table but
#: no bank uses it; X (bank 31) always passes.
_BANK_WEIGHTS: dict[str, tuple[tuple[int, ...], int]] = {
    "A": ((0, 0, 6, 3, 7, 9, 0, 0, 10, 5, 8, 4, 2, 1, 0, 0, 0, 0), 11),
    "B": ((0, 0, 0, 0, 0, 0, 0, 0, 10, 5, 8, 4, 2, 1, 0, 0, 0, 0), 11),
    "C": ((3, 7, 0, 0, 0, 0, 9, 1, 10, 5, 3, 4, 2, 1, 0, 0, 0, 0), 11),
    "D": ((0, 0, 0, 0, 0, 0, 0, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0, 0), 11),
    "E": ((0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 4, 3, 2, 0, 0, 0, 1), 11),
    "F": ((0, 0, 0, 0, 0, 0, 0, 1, 7, 3, 1, 7, 3, 1, 0, 0, 0, 0), 10),
    "G": ((0, 0, 0, 0, 0, 0, 0, 1, 3, 7, 1, 3, 7, 1, 0, 3, 7, 1), 10),
    "X": ((0,) * 18, 1),
}
#: E and G add a product's digits together, twice ("49 → 13 → 4").
_DIGIT_SUM = frozenset({"E", "G"})
#: How an unseparated run of digits can split — bank, branch, base, suffix —
#: in the order tried (a 7-digit base is the common one).
_BANK_LAYOUTS = {
    15: ((2, 4, 7, 2),),
    16: ((2, 4, 7, 3), (2, 4, 8, 2)),
    17: ((2, 4, 7, 4), (2, 4, 8, 3)),
    18: ((2, 4, 8, 4),),
}
BANK_SHAPE_MESSAGE = (
    "A bank account looks like 12-3456-7890123-00 (bank, branch, account, suffix)."
)
BANK_CHECK_MESSAGE = "That bank account number doesn't check out — please re-check it."


def bank_algorithm(bank: str, base: str) -> str | None:
    """IR's step 1: the algorithm for a bank, or None for a bank IR doesn't list."""
    bank = bank.zfill(2)
    if bank not in _BANK_BRANCHES:
        return None
    if bank in _BANK_ALGORITHM:
        return _BANK_ALGORITHM[bank]
    return "A" if int(base) < 990000 else "B"


def bank_checksum_ok(
    algorithm: str, bank: str, branch: str, base: str, suffix: str
) -> bool:
    """IR's step 2 for one algorithm: the weighted sum of the 18 padded
    digits divides by the algorithm's modulus."""
    weights, modulus = _BANK_WEIGHTS[algorithm]
    digits = bank.zfill(2) + branch.zfill(4) + base.zfill(8) + suffix.zfill(4)
    total = 0
    for digit, weight in zip(digits, weights):
        product = int(digit) * weight
        if algorithm in _DIGIT_SUM:
            for _ in range(2):
                product = sum(int(d) for d in str(product))
        total += product
    return total % modulus == 0


def nz_bank_account_valid(bank: str, branch: str, base: str, suffix: str) -> bool:
    """A New Zealand bank account passes Inland Revenue's validation: a bank
    IR lists, a branch that bank uses, and the bank's check digits."""
    if not all(p.isdigit() for p in (bank, branch, base, suffix)):
        return False
    if len(bank) > 2 or len(branch) > 4 or len(base) > 8 or len(suffix) > 4:
        return False
    algorithm = bank_algorithm(bank, base)
    if algorithm is None:
        return False
    if not any(lo <= int(branch) <= hi for lo, hi in _BANK_BRANCHES[bank.zfill(2)]):
        return False
    return bank_checksum_ok(algorithm, bank, branch, base, suffix)


def _bank_candidates(text: str) -> list[tuple[str, str, str, str]]:
    """The ways the typed account can split into bank, branch, base and
    suffix: the groups as typed when there are four (the branch may be
    written with three digits, IR's own example ``01-902-0068389-00``),
    else the layouts an unseparated run of digits allows."""
    groups = re.findall(r"\d+", text)
    if len(groups) == 4:
        bank, branch, base, suffix = groups
        if (
            len(bank) == 2
            and 3 <= len(branch) <= 4
            and 6 <= len(base) <= 8
            and 2 <= len(suffix) <= 4
        ):
            return [(bank, branch, base, suffix)]
        return []
    digits = "".join(groups)
    out = []
    for layout in _BANK_LAYOUTS.get(len(digits), ()):
        parts, at = [], 0
        for size in layout:
            parts.append(digits[at : at + size])
            at += size
        out.append(tuple(parts))
    return out


def normalise_bank_account(value: str | None) -> str:
    """A New Zealand bank account as ``BB-bbbb-AAAAAAA-SS(S)`` — or
    ``Invalid`` unless it passes Inland Revenue's validation
    (``nz_bank_account_valid``). Spaces and dashes are optional; an
    unseparated run that could split two ways takes the split that checks
    out."""
    candidates = _bank_candidates(value or "")
    if not candidates:
        raise Invalid(BANK_SHAPE_MESSAGE)
    for bank, branch, base, suffix in candidates:
        if int(base) and nz_bank_account_valid(bank, branch, base, suffix):
            # IR's padded form (8-digit base, 4-digit suffix) back to the
            # one printed on a statement.
            if len(base) == 8 and base.startswith("0"):
                base = base[1:]
            if len(suffix) == 4 and suffix.startswith("0"):
                suffix = suffix[1:]
            return f"{bank}-{branch.zfill(4)}-{base.zfill(7)}-{suffix.zfill(2)}"
    raise Invalid(BANK_CHECK_MESSAGE)


def account_ending(value: str | None) -> str | None:
    """ "account ending 34" — the last two digits of the account part."""
    if not value:
        return None
    parts = value.split("-")
    body = parts[2] if len(parts) >= 3 else re.sub(r"\D", "", value)
    return body[-2:] if body else None


def ird_ending(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits[-3:] if digits else None


def clean_filename(value: str | None) -> str | None:
    """The name a file is stored and served under: no path, no control
    characters, no quotes (it goes into a ``Content-Disposition`` header),
    200 characters at most; None when nothing is left."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if " " <= ch != "\x7f" and ch != '"')
    return name.strip()[:200] or None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def has_declaration(row: EntPerformer) -> bool:
    return bool(row.declaration_snapshot) or bool(row.declaration_file_id)


def declaration_method(row: EntPerformer) -> str | None:
    if row.declaration_snapshot:
        return (row.declaration_snapshot or {}).get("method") or "form"
    if row.declaration_file_id:
        return "upload"
    return None


def exemption_state(
    row: EntPerformer, today: dt.date | None = None
) -> tuple[bool, bool]:
    """``(expiring_within_30_days, expired)``."""
    if row.exemption_expiry_date is None:
        return False, False
    today = today or _now().date()
    expired = row.exemption_expiry_date < today
    expiring = not expired and row.exemption_expiry_date <= today + dt.timedelta(
        days=EXEMPTION_WARNING_DAYS
    )
    return expiring, expired


def members_of(db: Session, band: EntPerformer) -> list[EntBandMember]:
    return (
        db.query(EntBandMember)
        .filter(
            EntBandMember.organization_id == band.organization_id,
            EntBandMember.band_performer_id == band.id,
            EntBandMember.removed_at.is_(None),
        )
        .order_by(EntBandMember.sort_order, EntBandMember.created_at)
        .all()
    )


def payable(
    db: Session, row: EntPerformer, *, today: dt.date | None = None
) -> tuple[bool, str | None]:
    """Whether a performance of this performer may produce a pay line or a
    bill, and the plain reason when not."""
    if row.anonymised_at is not None:
        return False, "record anonymised"
    if not row.is_active:
        return False, "performer is inactive"
    if row.onboarding_status != "accepted":
        return False, (
            "submitted — finance has not accepted it yet"
            if row.onboarding_status == "submitted"
            else "not onboarded yet"
        )
    if row.bank_changed_at is not None and (
        row.accepted_at is None or row.bank_changed_at > row.accepted_at
    ):
        return False, "bank account changed — accept again"
    if row.band_mode == "members_split":
        members = members_of(db, row)
        if not members:
            return False, "band has no members"
        for member in members:
            if not member.member_performer_id:
                return False, f"band member {member.name} has not submitted their form"
            member_row = (
                db.query(EntPerformer)
                .filter(
                    EntPerformer.organization_id == row.organization_id,
                    EntPerformer.id == member.member_performer_id,
                )
                .first()
            )
            ok, why = (
                payable(db, member_row, today=today)
                if member_row
                else (False, "missing")
            )
            if not ok:
                return False, f"band member {member.name}: {why}"
        return True, None
    if not row.lane:
        return False, "no payment lane set"
    if row.lane == "payroll_contractor" and not has_declaration(row):
        return False, "no IR330C declaration on record"
    if row.lane == "exemption":
        _expiring, expired = exemption_state(row, today)
        if expired:
            return False, "exemption certificate has expired"
    return True, None


def performer_dict(
    db: Session, ctx: FinanceCtx, row: EntPerformer, *, today: dt.date | None = None
) -> dict:
    """The Register's view: identity for everyone with venue access; the
    sensitive endings and payroll fields only for ``payroll:write``."""
    expiring, expired = exemption_state(row, today)
    ok, why = payable(db, row, today=today)
    snapshot = row.declaration_snapshot or {}
    out = {
        "id": row.id,
        "name": row.name,
        "legal_name": row.legal_name,
        "trading_name": row.trading_name,
        "email": row.email,
        "phone": row.phone,
        "notes": row.notes,
        "is_active": row.is_active,
        "lane": row.lane,
        "performer_type": row.performer_type,
        "band_mode": row.band_mode,
        "onboarding_status": row.onboarding_status,
        "submitted_at": iso(row.submitted_at),
        "accepted_at": iso(row.accepted_at),
        "accepted_by": row.accepted_by,
        "bank_changed_at": iso(row.bank_changed_at),
        "needs_review": row.needs_review,
        "review_reason": row.review_reason,
        "gst_registered": row.gst_registered,
        "gst_number": row.gst_number,
        "company_name": row.company_name,
        "company_number": row.company_number,
        "exemption_certificate_number": row.exemption_certificate_number,
        "exemption_expiry_date": day(row.exemption_expiry_date),
        "exemption_expiring": expiring,
        "exemption_expired": expired,
        "has_exemption_file": bool(row.exemption_file_id),
        "has_company_document": bool(row.company_document_file_id),
        "has_declaration": has_declaration(row),
        "declaration_method": declaration_method(row),
        "declaration_signed_at": snapshot.get("signed_at"),
        "declaration_wording_version": snapshot.get("wording_version"),
        "band_leader_declaration_at": iso(row.band_leader_declaration_at),
        "contractor_confirmations": row.contractor_confirmations,
        "invite_sent_at": iso(row.invite_sent_at),
        "invite_reminders_sent": row.invite_reminders_sent,
        "last_reminder_at": iso(row.last_reminder_at),
        "anonymised_at": iso(row.anonymised_at),
        "payable": ok,
        "payable_reason": why,
        "created_at": iso(row.created_at),
        "version": row.version,
    }
    if ctx.holds("payroll:write"):
        # The endings are read from the stored values: an access row each
        # (plan §6.5 item 3), only when there is a value to read.
        for field, value in (
            ("bank_account", row.bank_account),
            ("ird_number", row.ird_number),
        ):
            if value:
                access_log.record(
                    db,
                    ctx,
                    subject_kind="performer",
                    subject_id=row.id,
                    field=f"{field}_ending",
                    route="entertainers.register",
                )
        out.update(
            {
                "bank_account_ending": account_ending(row.bank_account),
                "ird_ending": ird_ending(row.ird_number),
                "date_of_birth": row.date_of_birth,
                "tax_code": row.tax_code,
                "kiwisaver_rate": float(row.kiwisaver_rate)
                if row.kiwisaver_rate is not None
                else None,
                "payroll_code": row.payroll_code,
                "wt_rate_type": row.wt_rate_type,
                "wt_rate_bp": row.wt_rate_bp,
                "nz_tax_resident": row.nz_tax_resident,
            }
        )
    return out


def list_performers(
    db: Session,
    ctx: FinanceCtx,
    *,
    q: str | None = None,
    include_inactive: bool = False,
    needs_review: bool | None = None,
    lane: str | None = None,
) -> list[dict]:
    require_staff(ctx)
    query = db.query(EntPerformer).filter(EntPerformer.organization_id == ctx.org_id)
    if not include_inactive:
        query = query.filter(EntPerformer.is_active.is_(True))
    if needs_review is not None:
        query = query.filter(EntPerformer.needs_review.is_(needs_review))
    if lane:
        if lane not in LANES:
            raise Invalid(f"Unknown lane {lane!r}.")
        query = query.filter(EntPerformer.lane == lane)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                EntPerformer.name.ilike(like),
                EntPerformer.legal_name.ilike(like),
                EntPerformer.trading_name.ilike(like),
                EntPerformer.company_name.ilike(like),
                EntPerformer.email.ilike(like),
            )
        )
    today = _now().date()
    rows = query.order_by(func.lower(EntPerformer.name)).all()
    return [performer_dict(db, ctx, r, today=today) for r in rows]


def get_performer(db: Session, ctx: FinanceCtx, performer_id: str) -> EntPerformer:
    require_staff(ctx)
    return load_performer(db, ctx, performer_id)


def missing_declarations(db: Session, ctx: FinanceCtx) -> list[dict]:
    """Active withholding-tax performers with no declaration on record —
    the list the Tue 12 Jan gate works down (plan §15, D36)."""
    require_staff(ctx)
    # Filtered in Python: a JSON column holds Python's None as JSON null, not
    # SQL NULL, so ``IS NULL`` would miss every row written with no snapshot.
    rows = (
        db.query(EntPerformer)
        .filter(
            EntPerformer.organization_id == ctx.org_id,
            EntPerformer.is_active.is_(True),
            EntPerformer.lane == "payroll_contractor",
        )
        .order_by(func.lower(EntPerformer.name))
        .all()
    )
    return [performer_dict(db, ctx, r) for r in rows if not has_declaration(r)]


def sensitive(db: Session, ctx: FinanceCtx, performer_id: str) -> dict:
    """The stored bank account, IRD number and date of birth — the one place
    they leave the database, for keying into the payroll system. Every read
    is logged with who and which performer (plan §6.5 item 3)."""
    require_staff(ctx)
    ctx.require("payroll:write")
    row = load_performer(db, ctx, performer_id)
    logger.info(
        "sensitive read: user=%s org=%s performer=%s fields=bank_account,ird_number,date_of_birth",
        ctx.user_id,
        ctx.org_id,
        row.id,
    )
    for field in ("bank_account", "ird_number", "date_of_birth"):
        access_log.record(
            db,
            ctx,
            subject_kind="performer",
            subject_id=row.id,
            field=field,
            route="entertainers.performers.sensitive",
        )
    return {
        "bank_account": row.bank_account,
        "ird_number": row.ird_number,
        "date_of_birth": row.date_of_birth,
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def create_performer(
    db: Session,
    ctx: FinanceCtx,
    *,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    notes: str | None = None,
    performer_type: str | None = None,
    send_invite: bool = True,
) -> tuple[EntPerformer, dict | None]:
    """Add a performer and, by default, email them their personal onboarding
    link. Anyone with venue access may (D15)."""
    require_any_venue(ctx)
    if performer_type is not None and performer_type not in PERFORMER_TYPES:
        raise Invalid(f"performer_type must be one of {', '.join(PERFORMER_TYPES)}.")
    row = EntPerformer(
        organization_id=ctx.org_id,
        name=clean_text(name, "Name", required=True),
        email=clean_email(email, required=send_invite),
        phone=clean_text(phone, "Phone", limit=50),
        notes=clean_text(notes, "Notes", limit=MAX_TEXT),
        performer_type=performer_type,
        onboarding_status="invited",
        created_by=ctx.user_id,
    )
    db.add(row)
    flush(db)
    invite = send_invite_link(db, ctx, row) if send_invite else None
    return row, invite


def update_performer(
    db: Session,
    ctx: FinanceCtx,
    performer_id: str,
    fields: dict,
    *,
    version: int | None,
) -> EntPerformer:
    require_any_venue(ctx)
    row = load_performer(db, ctx, performer_id, for_update=True)
    check_version(row, version)
    unknown = set(fields) - set(IDENTITY_FIELDS) - set(FINANCE_FIELDS) - {"is_active"}
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    if set(fields) & set(FINANCE_FIELDS):
        ctx.require("payroll:write")
    if (
        "email" in fields
        and row.onboarding_status != "invited"
        and clean_email(fields["email"]) != row.email
        and not ctx.holds("payroll:write")
    ):
        # Once a performer has submitted, their address is where the one-time
        # code and the "your details were changed" notice go. Moving it from
        # the Register would move both (security review, 20 Oct 2026): a
        # payroll person may, with the performer on the phone; a booker
        # asks them.
        raise Forbidden(
            "Only a payroll administrator can change the email address of a "
            "performer who has already submitted their details."
        )
    for key, value in fields.items():
        if key == "name":
            value = clean_text(value, "Name", required=True)
        elif key in ("legal_name", "trading_name", "company_name"):
            value = clean_text(value, key.replace("_", " ").capitalize())
        elif key == "email":
            value = clean_email(value)
        elif key in ("phone", "company_number", "payroll_code", "tax_code"):
            value = clean_text(value, key.replace("_", " ").capitalize(), limit=50)
        elif key in ("notes", "review_reason"):
            value = clean_text(value, key.capitalize(), limit=MAX_TEXT)
        elif (
            key == "performer_type"
            and value is not None
            and value not in PERFORMER_TYPES
        ):
            raise Invalid(
                f"performer_type must be one of {', '.join(PERFORMER_TYPES)}."
            )
        elif key == "lane" and value is not None and value not in LANES:
            raise Invalid(f"lane must be one of {', '.join(LANES)}.")
        elif (
            key == "band_mode"
            and value is not None
            and value not in ("leader", "members_split")
        ):
            raise Invalid("band_mode must be leader or members_split.")
        elif key == "gst_number" and value:
            value = normalise_ird(value)
        elif key == "exemption_expiry_date" and value is not None:
            value = parse_date(value, "Exemption expiry")
        elif key == "exemption_certificate_number":
            value = clean_text(value, "Certificate number", limit=100)
        elif key == "wt_rate_bp" and value is not None:
            value = int(value)
            if not 0 <= value <= 10000:
                raise Invalid("The withholding rate must be between 0% and 100%.")
        elif (
            key == "wt_rate_type"
            and value is not None
            and value
            not in (
                "standard",
                "self_selected",
                "tailored",
            )
        ):
            raise Invalid("wt_rate_type must be standard, self_selected or tailored.")
        elif key == "kiwisaver_rate" and value is not None:
            value = float(value)
        if key == "is_active":
            if value:
                reactivate(db, ctx, row.id)
            else:
                deactivate(db, ctx, row.id)
            continue
        setattr(row, key, value)
    flush(db)
    return row


def deactivate(db: Session, ctx: FinanceCtx, performer_id: str) -> EntPerformer:
    """Retire a performer: no more bookings, every live link revoked."""
    require_any_venue(ctx)
    row = load_performer(db, ctx, performer_id, for_update=True)
    row.is_active = False
    tokens.revoke_for_performer(db, ctx.org_id, row.id)
    flush(db)
    return row


def reactivate(db: Session, ctx: FinanceCtx, performer_id: str) -> EntPerformer:
    require_any_venue(ctx)
    row = load_performer(db, ctx, performer_id, for_update=True)
    row.is_active = True
    flush(db)
    return row


def accept(db: Session, ctx: FinanceCtx, performer_id: str) -> EntPerformer:
    """Finance accepts a submission — the step that makes a performer
    payable (D14c). ``payroll:write`` only; a ``team_member`` cannot."""
    require_staff(ctx)
    ctx.require("payroll:write")
    row = load_performer(db, ctx, performer_id, for_update=True)
    if row.onboarding_status != "submitted" and row.bank_changed_at is None:
        raise Conflict(
            "There is nothing to accept — the performer has not submitted the form."
            if row.onboarding_status == "invited"
            else "This performer is already accepted."
        )
    if row.band_mode != "members_split" and not row.lane:
        raise Conflict("Pick the performer's payment lane before accepting.")
    now = _now()
    row.onboarding_status = "accepted"
    row.accepted_at = now
    row.accepted_by = ctx.user_id
    row.bank_changed_at = None
    if row.review_reason and "Not sure" in row.review_reason and row.lane:
        row.needs_review = False
        row.review_reason = None
    flush(db)
    return row


# ---------------------------------------------------------------------------
# Links and the emails that carry them
# ---------------------------------------------------------------------------


def send_invite_link(
    db: Session, ctx: FinanceCtx, row: EntPerformer, *, update: bool = False
) -> dict:
    """Mint a fresh onboarding link (revoking the old) and email it."""
    if not row.email:
        raise Invalid("Add the performer's email address first.")
    if not row.is_active:
        raise Conflict("This performer is inactive — reactivate them first.")
    token_row, _token, url = tokens.mint(
        db,
        org_id=ctx.org_id,
        purpose="onboarding",
        performer_id=row.id,
        created_by=ctx.user_id,
    )
    now = _now()
    row.invite_sent_at = now
    flush(db)
    performer_id, address, name = row.id, row.email, row.name
    org_id = ctx.org_id
    expires = token_row.expires_at

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind="ent_invite",
            to=address,
            context={
                "name": name,
                "first_name": emails.first_name(name),
                "url": url,
                "expires_days": tokens.TTL.days,
                "update": update,
            },
            performer_id=performer_id,
            target_id=performer_id,
        )

    emails.later(db, _send)
    return {"sent": bool(address), "url": url, "expires_at": expires.isoformat()}


def send_invite(db: Session, ctx: FinanceCtx, performer_id: str) -> dict:
    """ "Resend invite" on the Register: a new link, the old one revoked."""
    require_any_venue(ctx)
    row = load_performer(db, ctx, performer_id, for_update=True)
    if row.onboarding_status == "accepted":
        raise Conflict(
            "This performer is already accepted — use Request updated details instead."
        )
    return send_invite_link(db, ctx, row)


def request_details(db: Session, ctx: FinanceCtx, performer_id: str) -> dict:
    """ "Request updated details": the same form on a fresh link, the code
    again; a changed bank account is held until accepted again and the
    previous address is told (plan §11.2)."""
    require_any_venue(ctx)
    row = load_performer(db, ctx, performer_id, for_update=True)
    return send_invite_link(db, ctx, row, update=True)


def send_declaration_link(db: Session, ctx: FinanceCtx, performer_id: str) -> dict:
    """The declaration-only link (D36): code, IR330C, signature — nothing
    else. Sending it to a real performer during shadow is the owner's call
    on the day; in shadow the email goes to the payroll contact."""
    require_staff(ctx)
    ctx.require("payroll:write")
    row = load_performer(db, ctx, performer_id, for_update=True)
    if not row.email:
        raise Invalid("Add the performer's email address first.")
    _token_row, _token, url = tokens.mint(
        db,
        org_id=ctx.org_id,
        purpose="declaration",
        performer_id=row.id,
        created_by=ctx.user_id,
    )
    performer_id, address, name, org_id = row.id, row.email, row.name, ctx.org_id

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind="ent_declaration_request",
            to=address,
            context={"first_name": emails.first_name(name), "url": url},
            performer_id=performer_id,
            target_id=performer_id,
        )

    emails.later(db, _send)
    return {"sent": True, "url": url}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def store_file(
    db: Session,
    org_id: str,
    *,
    kind: str,
    data: bytes,
    filename: str | None,
    content_type: str | None,
    uploaded_by: str | None = None,
) -> EntFile:
    """Check the bytes (PDF through ``files/pdf_check``, images through
    ``functions/uploads.sniff_image``) and store them content-addressed.
    The identical file uploaded twice is one row."""
    from app.services.files.pdf_check import check_pdf
    from app.services.functions.uploads import sniff_image

    if kind not in ENT_FILE_KINDS:
        raise Invalid(f"Unknown document kind {kind!r}.")
    if not data:
        raise Invalid("The file is empty.")
    if len(data) > MAX_UPLOAD:
        raise Invalid("The file is larger than 10 MB.")
    if data[:5] == b"%PDF-":
        check_pdf(data)
        stored_type = "application/pdf"
    else:
        try:
            stored_type, _w, _h = sniff_image(data)
        except Exception as exc:  # noqa: BLE001 — Functions' Invalid is not ours
            raise Invalid("Upload a PDF or a JPEG, PNG, GIF or WebP image.") from exc
    sha = hashlib.sha256(data).hexdigest()
    existing = (
        db.query(EntFile)
        .filter(EntFile.organization_id == org_id, EntFile.sha256 == sha)
        .first()
    )
    if existing is not None:
        return existing
    name = clean_filename(filename)
    row = EntFile(
        organization_id=org_id,
        sha256=sha,
        kind=kind,
        filename=name,
        content_type=stored_type,
        size_bytes=len(data),
        data=data,
        uploaded_by_user_id=uploaded_by,
        check_status="pending" if kind == "invoice" else "waived",
    )
    db.add(row)
    flush(db)
    return row


def attach_document(
    db: Session,
    ctx: FinanceCtx,
    performer_id: str,
    *,
    kind: str,
    data: bytes,
    filename: str | None,
    content_type: str | None,
) -> EntFile:
    """Staff upload a certificate, a company document or a signed paper
    IR330C for a performer. An IR330C upload records an upload-method
    declaration snapshot (who, when, which file) — the fallback for
    performers set up before Norm (plan §11.1)."""
    require_any_venue(ctx)
    if kind == "invoice":
        raise Invalid("Invoices are uploaded by the performer on their link.")
    row = load_performer(db, ctx, performer_id, for_update=True)
    if kind == "ir330c":
        ctx.require("payroll:write")
    file = store_file(
        db,
        ctx.org_id,
        kind=kind,
        data=data,
        filename=filename,
        content_type=content_type,
        uploaded_by=ctx.user_id,
    )
    if kind == "exemption_certificate":
        row.exemption_file_id = file.id
    elif kind == "company_document":
        row.company_document_file_id = file.id
    elif kind == "ir330c":
        row.declaration_file_id = file.id
        row.declaration_snapshot = {
            "method": "upload",
            "file_id": file.id,
            "recorded_by": ctx.user_id,
            "signed_at": _now().isoformat(),
            "wording_version": None,
        }
    flush(db)
    return file


def get_document(
    db: Session, ctx: FinanceCtx, performer_id: str, file_id: str
) -> EntFile:
    """A performer's document, through the row that references it (the
    access check): certificates and company documents on venue access, the
    IR330C on ``payroll:write``."""
    require_staff(ctx)
    row = load_performer(db, ctx, performer_id)
    if file_id == row.declaration_file_id:
        ctx.require("payroll:write")
    elif file_id not in (row.exemption_file_id, row.company_document_file_id):
        raise Forbidden("That file does not belong to this performer.")
    file = (
        db.query(EntFile)
        .filter(EntFile.organization_id == ctx.org_id, EntFile.id == file_id)
        .first()
    )
    if file is None:
        raise Forbidden("That file does not belong to this performer.")
    return file


def file_dict(row: EntFile) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "created_at": iso(row.created_at),
    }
