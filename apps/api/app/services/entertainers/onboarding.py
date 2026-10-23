"""The onboarding form's server side (plan §11.2–11.3, D14, D36) — for a
performer's link, a band member's link and the declaration-only link.

The form keeps Orbit's six steps; the server keeps the rules Orbit lacked:

* **No link, no form.** Every route resolves a live ``ent_public_tokens``
  row first (the router answers 404 otherwise, and writes nothing).
* **The code before the bank and tax step.** Step 5 and ``submit`` refuse
  unless a six-digit code sent to the invited address was verified on this
  link in the last hour (``tokens.code_verified``). A forwarded link can
  fill in a name; it cannot redirect pay.
* **A link submits once.** ``submitted_at`` on the token; the next submit
  is refused and the page shows "received — finance will be in touch".
* **Nothing stored is shown back.** The form contract carries only the
  invite's name, email and phone — never a bank account, IRD number or
  date of birth, not even as a prefill.
* **The lane comes from the "how you're paid" answer**, GST from the GST
  answer and the GST number — never from the payment method (Orbit's
  defect, 02 §5.1). ``payroll_employee`` is never self-chosen.
* **Submitted is not payable.** The form sets ``onboarding_status =
  submitted``; finance accepts (``performers.accept``). A performer already
  accepted who submits a changed bank account is held
  (``bank_changed_at``) and the previous address is told.
* **The IR330C is signed on the form**: the wording (version
  ``2026-07-13-v1``, carried from Orbit's wizard), a typed signature, the
  time, a keyed hash of the IP and the user agent go into
  ``declaration_snapshot`` — the record kept with the pay records.
"""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    EntBandMember,
    EntFile,
    EntPerformer,
    EntPublicToken,
)
from app.db.models import Organization, _now
from app.services.entertainers import band as band_svc
from app.services.entertainers import emails, tokens
from app.services.entertainers._shared import ip_hash, parse_date
from app.services.entertainers.performers import (
    clean_email,
    clean_text,
    normalise_bank_account,
    normalise_ird,
    store_file,
)
from app.services.finance_core.errors import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    flush,
)

DECLARATION_WORDING_VERSION = "2026-07-13-v1"
#: The IR330C declaration as Orbit's wizard showed it (re-read by the
#: accountant, D28). Shown on the form and snapshotted with the signature.
DECLARATION_PARAGRAPHS = (
    "I declare that the information I have provided is true and correct.",
    "I confirm that I have selected the withholding tax rate shown above and "
    "authorise {org_name} to deduct withholding tax from payments made to me at "
    "that rate.",
)
DECLARATION_UNDERSTAND = (
    "my tax code for these payments will be WT;",
    "withholding tax will be deducted from my GST-exclusive entertainer payments;",
    "withholding tax may not represent my final income-tax liability;",
    "I am responsible for choosing an appropriate withholding tax rate and "
    "obtaining tax advice where required;",
    "I must tell {org_name} if my IRD details, tax rate, residency, GST status "
    "or exemption status changes; and",
    "{org_name} may apply a different rate if required by Inland Revenue or by law.",
)
DECLARATION_CONFIRM = (
    "I confirm that I have read this declaration and that the information I "
    "have provided is correct."
)
STANDARD_WT_BP = 2000

#: The six contractor confirmations (Orbit's keys and wording).
CONFIRMATIONS = (
    (
        "independent",
        "I am providing entertainment services as an independent contractor or authorised supplier.",
    ),
    ("accept_decline", "I can accept or decline future gigs."),
    ("other_clients", "I can perform for other venues or clients."),
    (
        "own_equipment",
        "I provide my own skills, act, music, equipment or performance service.",
    ),
    (
        "own_tax",
        "I understand I am responsible for my own tax return, ACC and business expenses, except for any withholding tax {org_name} is required to deduct.",
    ),
    (
        "advice",
        "I have had a reasonable opportunity to get advice before agreeing to this arrangement.",
    ),
)
CONFIRMATION_KEYS = tuple(k for k, _ in CONFIRMATIONS)

#: The "how you're paid" answers → lane. ``band_leader`` and ``partnership``
#: carry review notes; ``not_sure`` is submitted with no lane and a flag.
PAID_AS = {
    "individual": (
        "payroll_contractor",
        "An individual (not a business)",
        "You'll be paid weekly at your agreed hourly rate. Withholding tax (WT) is deducted before payment and you don't need to send an invoice.",
    ),
    "sole_trader": (
        "payroll_contractor",
        "A sole trader",
        "You'll be paid weekly at your agreed hourly rate with withholding tax deducted as IRD requires. If you're GST registered, send a GST invoice after you've performed and the GST is paid separately.",
    ),
    "company": (
        "invoice",
        "A registered NZ company",
        "You'll be paid after you upload an invoice for the times you performed. No withholding tax is deducted — your company is responsible for its own tax.",
    ),
    "agency": (
        "invoice",
        "A booking agent or agency",
        "{org_name} contracts with and pays your agency, after an invoice for each performance.",
    ),
    "band_leader": (
        "invoice",
        "A band leader paid for the whole band",
        "Payment to you is payment for the full performance fee; you are responsible for paying the other performers.",
    ),
    "partnership": (
        "invoice",
        "An IRD-registered partnership",
        "Our finance team will check the partnership details before approving payment.",
    ),
    "exemption": (
        "exemption",
        "Holder of an IRD certificate of exemption",
        "You'll be paid after you upload an invoice for the times you performed. No withholding tax is deducted while your certificate covers these payments.",
    ),
    "not_sure": (
        None,
        "Not sure",
        "Submit what you know and our finance team will set the rest up with you.",
    ),
}
STEPS_FULL = ("about", "performing", "paid", "band", "bank_tax", "review")
STEPS_DECLARATION = ("code", "declaration", "review")
CODE_BEFORE = "bank_tax"


# ---------------------------------------------------------------------------
# Resolving a link to the records behind it
# ---------------------------------------------------------------------------


def _org(db: Session, org_id: str) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise NotFound("This link isn't valid.")
    return org


def _performer(db: Session, row: EntPublicToken) -> EntPerformer:
    performer = (
        db.query(EntPerformer)
        .filter(
            EntPerformer.organization_id == row.organization_id,
            EntPerformer.id == row.performer_id,
        )
        .first()
    )
    if performer is None or not performer.is_active:
        raise NotFound("This link isn't valid.")
    return performer


def _member(db: Session, row: EntPublicToken) -> EntBandMember:
    member = (
        db.query(EntBandMember)
        .filter(
            EntBandMember.organization_id == row.organization_id,
            EntBandMember.id == row.band_member_id,
            EntBandMember.removed_at.is_(None),
        )
        .first()
    )
    if member is None:
        raise NotFound("This link isn't valid.")
    return member


def resolve(
    db: Session, token: str | None, *, now: dt.datetime | None = None
) -> EntPublicToken:
    """The live join link, or ``NotFound``. The module must be on (shadow
    or live): the owner tests the form at their own address in shadow."""
    row = tokens.resolve(db, token, purposes=tokens.JOIN_PURPOSES, now=now)
    if row is None:
        raise NotFound("This link isn't valid.")
    org = _org(db, row.organization_id)
    if getattr(org, "entertainers_mode", "off") == "off":
        raise NotFound("This link isn't valid.")
    return row


def _hint(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    local, domain = address.split("@", 1)
    return f"{local[:1]}***@{domain}"


def _fill(text: str, org_name: str) -> str:
    return text.replace("{org_name}", org_name)


def form(db: Session, row: EntPublicToken, *, now: dt.datetime | None = None) -> dict:
    """The page contract. Never a stored bank account, IRD number or date of
    birth — not even for a performer re-submitting their details."""
    now = now or _now()
    org = _org(db, row.organization_id)
    performer = _performer(db, row)
    member = _member(db, row) if row.purpose == "band_member" else None
    who = member or performer
    if row.purpose == "declaration":
        steps: tuple = STEPS_DECLARATION
    elif row.purpose == "band_member":
        steps = tuple(s for s in STEPS_FULL if s != "band")
    else:
        steps = STEPS_FULL
    address = who.email
    return {
        "purpose": row.purpose,
        "state": "submitted" if row.submitted_at is not None else "open",
        "organisation": org.name,
        "band_name": performer.name if member is not None else None,
        "prefill": {"name": who.name, "email": who.email, "phone": who.phone},
        "update": performer.onboarding_status == "accepted"
        and row.purpose == "onboarding",
        "steps": list(steps),
        "code": {
            "required_before": "declaration"
            if row.purpose == "declaration"
            else CODE_BEFORE,
            "verified": tokens.code_verified(row, now=now),
            "sent_to_hint": _hint(address),
        },
        "lanes": [
            {"key": key, "lane": lane, "label": label, "blurb": _fill(blurb, org.name)}
            for key, (lane, label, blurb) in PAID_AS.items()
        ],
        "performer_types": ["solo", "band", "agent"],
        "confirmations": [
            {"key": key, "label": _fill(label, org.name)}
            for key, label in CONFIRMATIONS
        ],
        "declaration": {
            "wording_version": DECLARATION_WORDING_VERSION,
            "paragraphs": [_fill(p, org.name) for p in DECLARATION_PARAGRAPHS],
            "understand": [_fill(p, org.name) for p in DECLARATION_UNDERSTAND],
            "confirm_label": DECLARATION_CONFIRM,
            "standard_rate_percent": STANDARD_WT_BP / 100,
        },
        "expires_at": row.expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# The code
# ---------------------------------------------------------------------------


def request_code(
    db: Session, row: EntPublicToken, *, now: dt.datetime | None = None
) -> None:
    """Issue a code and queue its email to the INVITED address — never one
    the visitor typed. Silent when the link has already submitted or the
    send window is spent."""
    if row.submitted_at is not None:
        return
    performer = _performer(db, row)
    who = _member(db, row) if row.purpose == "band_member" else performer
    if not who.email:
        return
    code = tokens.request_code(db, row, now=now)
    if code is None:
        return
    org_id, address, name = row.organization_id, who.email, who.name
    performer_id = performer.id
    member_id = row.band_member_id
    target = member_id or performer_id

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind="ent_code",
            to=address,
            context={
                "first_name": emails.first_name(name),
                "code": code,
                "minutes": 10,
            },
            performer_id=performer_id,
            band_member_id=member_id,
            target_id=target,
        )

    emails.later(db, _send)


def verify_code(db: Session, row: EntPublicToken, code: str, *, now=None) -> bool:
    if row.submitted_at is not None:
        return False
    return tokens.verify_code(db, row, code, now=now)


def require_code(row: EntPublicToken, *, now: dt.datetime | None = None) -> None:
    if not tokens.code_verified(row, now=now):
        raise Forbidden("Enter the code we emailed you before this step.")


# ---------------------------------------------------------------------------
# Validation per step
# ---------------------------------------------------------------------------


class FieldErrors(Invalid):
    """A 400 with a message per field, so the form can mark each one."""

    def __init__(self, errors: dict):
        super().__init__("Some answers need another look.")
        self.errors = errors


def _collect(fn, errors: dict, field: str):
    try:
        return fn()
    except Invalid as exc:
        errors[field] = exc.message
        return None


def validate_about(data: dict) -> tuple[dict, dict]:
    errors: dict = {}
    out = {
        "name": _collect(
            lambda: clean_text(data.get("name"), "Name", required=True), errors, "name"
        ),
        "email": _collect(
            lambda: clean_email(data.get("email"), required=True), errors, "email"
        ),
        "phone": _collect(
            lambda: clean_text(data.get("phone"), "Phone", limit=50, required=True),
            errors,
            "phone",
        ),
    }
    return out, errors


def validate_performing(data: dict) -> tuple[dict, dict]:
    errors: dict = {}
    kind = str(data.get("performer_type") or "").strip()
    if kind not in ("solo", "band", "agent"):
        errors["performer_type"] = "Tell us who is performing: solo, band or agent."
    return {"performer_type": kind or None}, errors


def validate_paid(data: dict, performer_type: str | None) -> tuple[dict, dict]:
    errors: dict = {}
    paid_as = str(data.get("paid_as") or "").strip()
    if paid_as not in PAID_AS:
        errors["paid_as"] = "Choose how you're paid."
        return {"paid_as": None}, errors
    lane = PAID_AS[paid_as][0]
    out: dict = {"paid_as": paid_as, "lane": lane, "legal_name": None}
    out["legal_name"] = _collect(
        lambda: clean_text(
            data.get("legal_name"), "Legal name", required=lane is not None
        ),
        errors,
        "legal_name",
    )
    out["trading_name"] = _collect(
        lambda: clean_text(data.get("trading_name"), "Trading name"),
        errors,
        "trading_name",
    )
    gst = data.get("gst_registered")
    if gst not in (True, False, None):
        gst = str(gst).lower() in ("true", "1", "yes")
    out["gst_registered"] = bool(gst) if gst is not None else None
    gst_number = str(data.get("gst_number") or "").strip()
    if out["gst_registered"]:
        if not gst_number:
            errors["gst_number"] = "Enter your GST number (it is your IRD number)."
        else:
            out["gst_number"] = _collect(
                lambda: normalise_ird(gst_number), errors, "gst_number"
            )
    else:
        out["gst_number"] = None
    if paid_as in ("company", "agency", "partnership", "band_leader"):
        out["company_name"] = _collect(
            lambda: clean_text(
                data.get("company_name"),
                "Company name",
                required=paid_as in ("company", "agency"),
            ),
            errors,
            "company_name",
        )
        out["company_number"] = _collect(
            lambda: clean_text(data.get("company_number"), "Company number", limit=50),
            errors,
            "company_number",
        )
    if paid_as == "exemption":
        out["exemption_certificate_number"] = _collect(
            lambda: clean_text(
                data.get("exemption_certificate_number"),
                "Certificate number",
                limit=100,
                required=True,
            ),
            errors,
            "exemption_certificate_number",
        )
        expiry = _collect(
            lambda: parse_date(data.get("exemption_expiry_date"), "Certificate expiry"),
            errors,
            "exemption_expiry_date",
        )
        if expiry is not None and expiry < _now().date():
            errors["exemption_expiry_date"] = "That certificate has already expired."
        out["exemption_expiry_date"] = expiry
    if performer_type == "band" and paid_as in ("individual", "sole_trader"):
        # A band paid to one person is a band leader; an individual answer is
        # still allowed (a solo act who ticked band by mistake) — reviewed.
        out["review"] = "Band answered as an individual — check the lane"
    return out, errors


def validate_band(data: dict, performer_type: str | None) -> tuple[dict, dict]:
    errors: dict = {}
    if performer_type != "band":
        return {"band_mode": None}, errors
    mode = str(data.get("band_mode") or "").strip()
    if mode not in ("leader", "members_split"):
        errors["band_mode"] = (
            "Is one band leader paid for everyone, or each member separately?"
        )
        return {"band_mode": None}, errors
    out: dict = {"band_mode": mode}
    if mode == "leader":
        if not data.get("band_leader_declaration"):
            errors["band_leader_declaration"] = "Accept the band leader declaration."
    else:
        members = data.get("members") or []
        if not isinstance(members, list) or len(members) < 2:
            errors["members"] = "List at least two band members."
        elif len(members) > band_svc.MAX_MEMBERS:
            errors["members"] = f"List at most {band_svc.MAX_MEMBERS} members."
        else:
            cleaned = []
            for i, m in enumerate(members):
                if not isinstance(m, dict):
                    errors["members"] = "Each member needs a name."
                    break
                try:
                    cleaned.append(
                        {
                            "name": clean_text(
                                m.get("name"), "Member name", required=True
                            ),
                            "email": clean_email(m.get("email")),
                            "phone": clean_text(m.get("phone"), "Phone", limit=50),
                        }
                    )
                except Invalid as exc:
                    errors[f"members.{i}"] = exc.message
            out["members"] = cleaned
    return out, errors


def validate_bank_tax(
    data: dict, lane: str | None, *, band_mode: str | None
) -> tuple[dict, dict]:
    """Bank, IRD, date of birth where the lane needs it, the six
    confirmations, and the IR330C declaration for the WT lane."""
    errors: dict = {}
    out: dict = {}
    if band_mode == "members_split":
        return out, errors  # the members give their own
    out["bank_account"] = _collect(
        lambda: normalise_bank_account(data.get("bank_account")), errors, "bank_account"
    )
    if lane in ("payroll_contractor", "exemption", "invoice") or lane is None:
        ird = str(data.get("ird_number") or "").strip()
        if ird:
            out["ird_number"] = _collect(
                lambda: normalise_ird(ird), errors, "ird_number"
            )
        elif lane == "payroll_contractor":
            errors["ird_number"] = "Your IRD number is required for withholding tax."
        else:
            out["ird_number"] = None
    dob = data.get("date_of_birth")
    if dob:
        parsed = _collect(
            lambda: parse_date(dob, "Date of birth"), errors, "date_of_birth"
        )
        if parsed is not None and parsed > _now().date() - dt.timedelta(days=365 * 14):
            errors["date_of_birth"] = "Check the date of birth."
        out["date_of_birth"] = parsed.isoformat() if parsed else None
    else:
        out["date_of_birth"] = None
    confirmations = data.get("contractor_confirmations") or {}
    if lane is not None:
        missing = [k for k in CONFIRMATION_KEYS if not confirmations.get(k)]
        if missing:
            errors["contractor_confirmations"] = "Tick every confirmation to continue."
    out["contractor_confirmations"] = {
        k: bool(confirmations.get(k)) for k in CONFIRMATION_KEYS
    }
    if lane == "payroll_contractor":
        decl, decl_errors = validate_declaration(
            data.get("declaration") or {}, legal_name=data.get("legal_name")
        )
        out["declaration"] = decl
        errors.update({f"declaration.{k}": v for k, v in decl_errors.items()})
    return out, errors


def validate_declaration(data: dict, *, legal_name: str | None) -> tuple[dict, dict]:
    errors: dict = {}
    out: dict = {}
    resident = data.get("nz_tax_resident")
    if resident in ("true", "1", "yes", True):
        out["nz_tax_resident"] = True
    else:
        errors["nz_tax_resident"] = (
            "This form is for New Zealand tax residents. If you're not one, "
            "contact the finance team to be set up another way."
        )
    rate_type = str(data.get("wt_rate_type") or "standard").strip()
    if rate_type not in ("standard", "self_selected", "tailored"):
        errors["wt_rate_type"] = (
            "Choose the standard rate, your own rate or a tailored rate."
        )
        rate_type = "standard"
    out["wt_rate_type"] = rate_type
    if rate_type == "standard":
        out["wt_rate_bp"] = STANDARD_WT_BP
    else:
        try:
            percent = float(data.get("wt_rate_percent"))
        except (TypeError, ValueError):
            percent = None
        if (
            percent is None
            or (rate_type == "self_selected" and not 10 <= percent <= 100)
            or (rate_type == "tailored" and not 0 < percent <= 100)
        ):
            errors["wt_rate_percent"] = (
                "Enter a rate between 10% and 100%."
                if rate_type == "self_selected"
                else "Enter your tailored rate (up to 100%)."
            )
        else:
            out["wt_rate_bp"] = int(round(percent * 100))
        if rate_type == "tailored":
            out["tailored_certificate_number"] = _collect(
                lambda: clean_text(
                    data.get("tailored_certificate_number"),
                    "Certificate reference",
                    limit=100,
                    required=True,
                ),
                errors,
                "tailored_certificate_number",
            )
    signature = " ".join(str(data.get("signature") or "").split())
    if not signature:
        errors["signature"] = "Type your full legal name to sign."
    elif (
        legal_name
        and signature.casefold() != " ".join(str(legal_name).split()).casefold()
    ):
        errors["signature"] = "Sign with the legal name you gave above."
    out["signature"] = signature
    if not data.get("confirmed"):
        errors["confirmed"] = "Confirm that you have read the declaration."
    return out, errors


def validate_step(
    db: Session, row: EntPublicToken, step: str, data: dict, *, now=None
) -> dict:
    """Validate one step and save the non-sensitive answers (steps 1–4) on
    the record the link points at. Step 5 is validated only (and needs the
    code); nothing sensitive is stored until ``submit``."""
    if row.submitted_at is not None:
        raise Conflict("This form has already been submitted.")
    performer = _performer(db, row)
    member = _member(db, row) if row.purpose == "band_member" else None
    who = member or performer
    if row.purpose == "declaration":
        if step != "declaration":
            raise Invalid("This link only takes the tax declaration.")
        require_code(row, now=now)
        _out, errors = validate_declaration(
            data, legal_name=data.get("legal_name") or performer.legal_name
        )
        if errors:
            raise FieldErrors(errors)
        return {"ok": True}
    if step == "about":
        out, errors = validate_about(data)
        if (
            not errors
            and out["email"] != (who.email or "").strip().lower()
            and not tokens.code_verified(row, now=now)
        ):
            # The code goes to the address the link was sent to. Letting
            # the form move that address first would hand a forwarded link
            # the code (security review, 20 Oct 2026): a verified code
            # proves the invited mailbox, and only then may it be corrected.
            errors["email"] = (
                "The code for the bank step goes to the address this link was "
                "sent to. Enter that code first to change your email address, "
                "or ask the venue to resend the invite to the right address."
            )
        if errors:
            raise FieldErrors(errors)
        who.name, who.email, who.phone = out["name"], out["email"], out["phone"]
        flush(db)
    elif step == "performing":
        out, errors = validate_performing(data)
        if errors:
            raise FieldErrors(errors)
        if member is None:
            performer.performer_type = out["performer_type"]
            flush(db)
    elif step == "paid":
        out, errors = validate_paid(
            data, data.get("performer_type") or performer.performer_type
        )
        if errors:
            raise FieldErrors(errors)
    elif step == "band":
        if member is not None:
            raise Invalid("A band member's link has no band step.")
        out, errors = validate_band(
            data, data.get("performer_type") or performer.performer_type
        )
        if errors:
            raise FieldErrors(errors)
    elif step == "bank_tax":
        require_code(row, now=now)
        paid, perrors = validate_paid(
            data, data.get("performer_type") or performer.performer_type
        )
        if perrors:
            raise FieldErrors(perrors)
        _out, errors = validate_bank_tax(
            {**data, "legal_name": paid.get("legal_name")},
            paid.get("lane"),
            band_mode=data.get("band_mode") if member is None else None,
        )
        if errors:
            raise FieldErrors(errors)
    elif step == "review":
        pass
    else:
        raise Invalid(f"Unknown step {step!r}.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


def _review_reasons(paid: dict, band: dict, performer_type: str | None) -> list[str]:
    reasons = []
    if paid.get("paid_as") == "not_sure" or performer_type is None:
        reasons.append("Entertainer answered 'Not sure'")
    if paid.get("paid_as") == "partnership":
        reasons.append("IRD-registered partnership")
    if band.get("band_mode") == "members_split":
        reasons.append(band_svc.MEMBERS_PENDING)
    if paid.get("paid_as") == "company":
        name = paid.get("company_name") or ""
        if not re.search(r"\b(limited|ltd)\b", name, re.IGNORECASE):
            reasons.append("Company name missing 'Limited' / 'Ltd'")
    if paid.get("gst_registered") and not paid.get("gst_number"):
        reasons.append("GST registered but GST number missing")
    if paid.get("review"):
        reasons.append(paid["review"])
    return reasons


def _snapshot(
    decl: dict,
    *,
    org_name: str,
    legal_name: str | None,
    ird: str | None,
    gst: bool | None,
    ip: str | None,
    user_agent: str | None,
    now: dt.datetime,
) -> dict:
    return {
        "method": "form",
        "wording_version": DECLARATION_WORDING_VERSION,
        "paragraphs": [_fill(p, org_name) for p in DECLARATION_PARAGRAPHS],
        "understand": [_fill(p, org_name) for p in DECLARATION_UNDERSTAND],
        "confirm_label": DECLARATION_CONFIRM,
        "legal_name": legal_name,
        "ird_ending": (ird or "")[-3:] or None,
        "nz_tax_resident": True,
        "wt_rate_type": decl.get("wt_rate_type"),
        "wt_rate_bp": decl.get("wt_rate_bp"),
        "tailored_certificate_number": decl.get("tailored_certificate_number"),
        "gst_registered": gst,
        "signature": decl.get("signature"),
        "signed_at": now.isoformat(),
        "ip_hash": ip_hash(ip),
        "user_agent": (user_agent or "")[:300] or None,
    }


def submit(
    db: Session,
    row: EntPublicToken,
    data: dict,
    *,
    ip: str | None,
    user_agent: str | None,
    now: dt.datetime | None = None,
) -> dict:
    """Validate every step at once and write the submission. Returns
    ``{"state": "submitted"}`` — and nothing stored."""
    now = now or _now()
    if row.submitted_at is not None:
        raise Conflict("This form has already been submitted.")
    org = _org(db, row.organization_id)
    performer = _performer(db, row)
    if row.purpose == "declaration":
        return _submit_declaration(
            db, row, performer, org, data, ip=ip, user_agent=user_agent, now=now
        )
    member = _member(db, row) if row.purpose == "band_member" else None

    errors: dict = {}
    about, e = validate_about(data)
    errors.update(e)
    performing, e = validate_performing(data)
    errors.update(e)
    paid, e = validate_paid(data, performing.get("performer_type"))
    errors.update(e)
    band = {"band_mode": None}
    if member is None:
        band, e = validate_band(data, performing.get("performer_type"))
        errors.update(e)
    lane = paid.get("lane")
    needs_bank = not (member is None and band.get("band_mode") == "members_split")
    bank: dict = {}
    if needs_bank:
        require_code(row, now=now)
        bank, e = validate_bank_tax(
            {**data, "legal_name": paid.get("legal_name")},
            lane,
            band_mode=band.get("band_mode"),
        )
        errors.update(e)
    if errors:
        raise FieldErrors(errors)

    if member is not None:
        target = EntPerformer(
            organization_id=row.organization_id,
            name=about["name"],
            email=about["email"],
            phone=about["phone"],
            onboarding_status="invited",
            performer_type=performing["performer_type"],
            created_by=None,
        )
        db.add(target)
        flush(db)
    else:
        target = performer
    previous_email = target.email
    previous_bank = target.bank_account
    was_accepted = target.onboarding_status == "accepted"

    target.name, target.email, target.phone = (
        about["name"],
        about["email"],
        about["phone"],
    )
    target.performer_type = performing["performer_type"]
    target.legal_name = paid.get("legal_name")
    target.trading_name = paid.get("trading_name")
    target.gst_registered = paid.get("gst_registered")
    target.gst_number = paid.get("gst_number")
    target.company_name = paid.get("company_name")
    target.company_number = paid.get("company_number")
    target.exemption_certificate_number = paid.get("exemption_certificate_number")
    target.exemption_expiry_date = paid.get("exemption_expiry_date")
    if lane is not None or not was_accepted:
        target.lane = lane  # finance's lane is kept when a "not sure" update comes in
    if member is None:
        target.band_mode = band.get("band_mode")
        if band.get("band_mode") == "leader":
            target.band_leader_declaration_at = now
    if needs_bank:
        target.bank_account = bank.get("bank_account")
        if "ird_number" in bank:
            target.ird_number = bank.get("ird_number")
        target.date_of_birth = bank.get("date_of_birth")
        target.contractor_confirmations = bank.get("contractor_confirmations")
        if lane == "payroll_contractor":
            decl = bank["declaration"]
            target.nz_tax_resident = True
            target.wt_rate_type = decl["wt_rate_type"]
            target.wt_rate_bp = decl["wt_rate_bp"]
            target.tax_code = "WT"
            target.declaration_snapshot = _snapshot(
                decl,
                org_name=org.name,
                legal_name=paid.get("legal_name"),
                ird=bank.get("ird_number"),
                gst=paid.get("gst_registered"),
                ip=ip,
                user_agent=user_agent,
                now=now,
            )
    reasons = _review_reasons(paid, band, performing.get("performer_type"))
    target.needs_review = bool(reasons)
    target.review_reason = "; ".join(reasons) if reasons else None
    target.onboarding_status = "submitted"
    target.submitted_at = now
    bank_changed = (
        was_accepted
        and needs_bank
        and previous_bank
        and previous_bank != target.bank_account
    )
    if bank_changed:
        target.bank_changed_at = now
    for kind, field in (
        ("exemption_certificate", "exemption_file_id"),
        ("company_document", "company_document_file_id"),
    ):
        file_id = data.get(f"{kind}_file_id")
        if file_id:
            if _owned_upload(db, row, file_id, kind) is None:
                raise FieldErrors({f"{kind}_file_id": "Upload the file again."})
            setattr(target, field, file_id)
    flush(db)

    if member is None and band.get("band_mode") == "members_split":
        band_svc.set_members(db, target, band["members"], invite=True)
    if member is not None:
        band_svc.member_submitted(db, performer, member, target)

    row.submitted_at = now
    row.otp_verified_at = None
    flush(db)

    if bank_changed and previous_email:
        org_id, target_id, name, contact = (
            row.organization_id,
            target.id,
            target.name,
            getattr(org, "payroll_contact_email", None),
        )
        changed_at = now

        def _send(session: Session) -> None:
            emails.deliver(
                session,
                org_id=org_id,
                kind="ent_details_changed",
                to=previous_email,
                context={
                    "first_name": emails.first_name(name),
                    "changed_at": changed_at.strftime("%-d %B %Y at %H:%M UTC"),
                    "contact_email": contact or "",
                },
                performer_id=target_id,
                target_id=f"{target_id}:bank-changed:{changed_at.date().isoformat()}",
            )

        emails.later(db, _send)
    return {"state": "submitted"}


def _submit_declaration(db, row, performer, org, data, *, ip, user_agent, now) -> dict:
    require_code(row, now=now)
    legal_name = (
        clean_text(data.get("legal_name"), "Legal name") or performer.legal_name
    )
    decl, errors = validate_declaration(
        data.get("declaration") or data, legal_name=legal_name
    )
    ird = str(data.get("ird_number") or "").strip()
    ird_value = None
    if ird:
        try:
            ird_value = normalise_ird(ird)
        except Invalid as exc:
            errors["ird_number"] = exc.message
    elif not performer.ird_number:
        errors["ird_number"] = "Your IRD number is required for the declaration."
    if not legal_name:
        errors["legal_name"] = "Your full legal name is required."
    if errors:
        raise FieldErrors(errors)
    if ird_value:
        performer.ird_number = ird_value
    performer.legal_name = legal_name
    performer.nz_tax_resident = True
    performer.wt_rate_type = decl["wt_rate_type"]
    performer.wt_rate_bp = decl["wt_rate_bp"]
    performer.tax_code = performer.tax_code or "WT"
    performer.declaration_snapshot = _snapshot(
        decl,
        org_name=org.name,
        legal_name=legal_name,
        ird=performer.ird_number,
        gst=performer.gst_registered,
        ip=ip,
        user_agent=user_agent,
        now=now,
    )
    row.submitted_at = now
    row.otp_verified_at = None
    flush(db)
    return {"state": "submitted"}


# ---------------------------------------------------------------------------
# Uploads from the form
# ---------------------------------------------------------------------------


def upload_document(
    db: Session,
    row: EntPublicToken,
    *,
    kind: str,
    data: bytes,
    filename: str | None,
    content_type: str | None,
    now=None,
) -> EntFile:
    """A certificate or company document uploaded on the form, attached to
    the performer at submit. Needs the code (the upload is part of the
    sensitive step)."""
    if row.submitted_at is not None:
        raise Conflict("This form has already been submitted.")
    if kind not in ("exemption_certificate", "company_document", "ir330c"):
        raise Invalid(
            "Upload an exemption certificate, a company document or a signed IR330C."
        )
    require_code(row, now=now)
    file = store_file(
        db,
        row.organization_id,
        kind=kind,
        data=data,
        filename=filename,
        content_type=content_type,
    )
    file.source_ref = f"join:{row.id}"
    flush(db)
    return file


def _owned_upload(
    db: Session, row: EntPublicToken, file_id: str, kind: str
) -> EntFile | None:
    """A file this link uploaded — never one some other link or staff member
    stored, whatever id the form sends."""
    return (
        db.query(EntFile)
        .filter(
            EntFile.organization_id == row.organization_id,
            EntFile.id == file_id,
            EntFile.kind == kind,
            EntFile.source_ref == f"join:{row.id}",
        )
        .first()
    )
