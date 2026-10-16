"""Company settings: currency, default timezone, tax wording, functions_mode.

They live on ``organizations`` because money in Functions is stored as bare
integer cents — the currency is the company's, not the row's — and because
``functions_mode`` is the organisation-wide switch every write checks.
Changing them is an owner's act, and is allowed in every mode: it is how an
organisation goes live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.functions_models import FUNCTIONS_MODES
from app.db.models import Organization
from app.services.functions.ctx import FunctionsCtx, require_owner
from app.services.functions.errors import Invalid, NotFound, flush
from app.services.functions.timeutil import validate_timezone

_CURRENCY = re.compile(r"^[A-Z]{3}$")

#: Symbols for the currencies a hospitality group here plausibly trades in;
#: anything else is shown with its ISO code.
_SYMBOLS = {
    "NZD": "$",
    "AUD": "$",
    "USD": "$",
    "CAD": "$",
    "SGD": "$",
    "FJD": "$",
    "GBP": "£",
    "EUR": "€",
    "JPY": "¥",
}
_ZERO_DECIMAL = frozenset({"JPY"})


@dataclass(frozen=True)
class CompanySettings:
    currency: str
    default_timezone: str
    tax_label: str
    functions_mode: str

    def as_dict(self) -> dict:
        return {
            "currency": self.currency,
            "default_timezone": self.default_timezone,
            "tax_label": self.tax_label,
            "functions_mode": self.functions_mode,
        }


def _settings(org: Organization) -> CompanySettings:
    return CompanySettings(
        currency=org.currency or "NZD",
        default_timezone=org.default_timezone or "Pacific/Auckland",
        tax_label=org.tax_label or "incl. GST",
        functions_mode=org.functions_mode or "off",
    )


def get_company_settings(db: Session, org_id: str) -> CompanySettings:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise NotFound("Organisation not found.")
    return _settings(org)


def update_company_settings(
    db: Session,
    ctx: FunctionsCtx,
    *,
    currency: str | None = None,
    default_timezone: str | None = None,
    tax_label: str | None = None,
    functions_mode: str | None = None,
) -> CompanySettings:
    """Change any of the four. Going ``live`` is refused while a venue with
    Functions records has no valid timezone: every plan at that venue would
    otherwise convert to the wrong instant."""
    require_owner(ctx)
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    if org is None:
        raise NotFound("Organisation not found.")
    if currency is not None:
        code = currency.strip().upper()
        if not _CURRENCY.match(code):
            raise Invalid("Currency must be a three-letter ISO 4217 code, e.g. NZD.")
        org.currency = code
    if default_timezone is not None:
        org.default_timezone = validate_timezone(default_timezone)
    if tax_label is not None:
        label = tax_label.strip()
        if not label or len(label) > 40:
            raise Invalid("Tax wording must be 1–40 characters, e.g. 'incl. GST'.")
        org.tax_label = label
    if functions_mode is not None:
        if functions_mode not in FUNCTIONS_MODES:
            raise Invalid(
                f"functions_mode must be one of {', '.join(FUNCTIONS_MODES)}."
            )
        if functions_mode == "live":
            _require_venue_timezones(db, org.id)
        org.functions_mode = functions_mode
    flush(db)
    return _settings(org)


def _require_venue_timezones(db: Session, org_id: str) -> None:
    from app.db.functions_models import FnEnquiry, FnEvent, FnMailboxAccount
    from app.db.models import Venue

    used = set()
    for model in (FnEnquiry, FnEvent, FnMailboxAccount):
        used |= {
            r[0]
            for r in db.query(model.venue_id)
            .filter(model.organization_id == org_id, model.venue_id.is_not(None))
            .distinct()
            .all()
        }
    if not used:
        return
    bad = []
    for v in db.query(Venue).filter(Venue.id.in_(used)).all():
        try:
            validate_timezone(v.timezone or "")
        except Invalid:
            bad.append(v.name)
    if bad:
        raise Invalid(
            "Set a timezone on these venues before going live: "
            + ", ".join(sorted(bad))
        )


def format_money(cents: int | None, currency: str = "NZD") -> str:
    """``123456`` -> ``"$1,234.56"``; whole dollars drop the cents
    (``"$1,200"``) because that is how quotes and menus read."""
    if cents is None:
        return ""
    code = (currency or "NZD").upper()
    symbol = _SYMBOLS.get(code)
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    if code in _ZERO_DECIMAL:
        body = f"{value:,}"
    elif value % 100 == 0:
        body = f"{value // 100:,}"
    else:
        body = f"{value // 100:,}.{value % 100:02d}"
    return f"{sign}{symbol}{body}" if symbol else f"{sign}{code} {body}"


def money_for_org(db: Session, org_id: str, cents: int | None) -> str:
    """Format cents in the organisation's currency."""
    return format_money(cents, get_company_settings(db, org_id).currency)
