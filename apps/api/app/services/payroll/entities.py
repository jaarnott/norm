"""Payroll entities — one ``pay_entities`` row per venue that runs its own pay
run, created on first use with the plan's defaults (plan §6.1, §9).

The row holds the settings a client token cannot read from Loaded and every
literal Orbit's payroll report hard-coded (the Ace pay-type code, the
SECURITY pay code, the $150 uniform bond, the 12% leave uplift). The
statutory literals are NOT here: they are dated rows in ``pay_rules``
(``rules.py``). ``payroll_on`` is the per-entity switch (AU venues stay off
until asked, D12) and ``payroll_contact_email`` overrides the organisation's
(D25).

The Loaded company id is read from the venue's ``loadedhub`` connection
(``config["x_loaded_company_id"]``, written by the OAuth connect) and copied
onto the row so a snapshot can map Loaded's ``venueId`` on a shift to a Norm
venue without name matching.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Connection, Venue
from app.db.payroll_models import PayEntity
from app.services.business_calendar import day_start_for, timezone_name
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid, NotFound, flush

#: The plan's defaults (plan §6.1); the model's server defaults match.
DEFAULTS = {
    "pay_week_start_weekday": 1,
    "payroll_system": "ace",
    "ace_pay_type_code": "50001",
    "ace_gratuity_allowance_code": None,
    "security_pay_code": "SECURITY",
    "uniform_bond_cents": 15000,
    "ico_leave_uplift_bp": 1200,
    "sick_approver_name": None,
    "payroll_contact_email": None,
    "payroll_on": False,
}

#: What a payroll person may change on the settings page.
EDITABLE = (
    "pay_week_start_weekday",
    "payroll_system",
    "ace_pay_type_code",
    "ace_gratuity_allowance_code",
    "security_pay_code",
    "uniform_bond_cents",
    "ico_leave_uplift_bp",
    "sick_approver_name",
    "payroll_contact_email",
    "payroll_on",
)


def loaded_company_id(db: Session, venue_id: str) -> str | None:
    """The Loaded company this venue's connection serves, or None until it
    is connected."""
    row = (
        db.query(Connection.config)
        .filter(
            Connection.connector_name == "loadedhub",
            Connection.venue_id == venue_id,
            Connection.enabled == "true",
        )
        .first()
    )
    if row is None or not isinstance(row[0], dict):
        return None
    value = str(row[0].get("x_loaded_company_id") or "").strip()
    return value or None


def _venue(db: Session, ctx: FinanceCtx, venue_id: str) -> Venue:
    require_venue(ctx, venue_id)
    venue = (
        db.query(Venue)
        .filter(Venue.id == venue_id, Venue.organization_id == ctx.org_id)
        .first()
    )
    if venue is None:
        raise NotFound("Venue not found.")
    return venue


def get_or_create(db: Session, ctx: FinanceCtx, venue_id: str) -> PayEntity:
    """The entity row for a venue the caller can see, made with the defaults
    on first use. Refreshes ``loaded_company_id`` from the connection when
    the row has none yet (a venue connected after the row was made)."""
    venue = _venue(db, ctx, venue_id)
    entity = (
        db.query(PayEntity)
        .filter(PayEntity.organization_id == ctx.org_id, PayEntity.venue_id == venue.id)
        .first()
    )
    if entity is None:
        entity = PayEntity(
            organization_id=ctx.org_id,
            venue_id=venue.id,
            loaded_company_id=loaded_company_id(db, venue.id),
            **DEFAULTS,
        )
        db.add(entity)
        flush(db)
    elif not entity.loaded_company_id:
        found = loaded_company_id(db, venue.id)
        if found:
            entity.loaded_company_id = found
            flush(db)
    return entity


def list_entities(db: Session, ctx: FinanceCtx) -> list[PayEntity]:
    """One entity per venue the caller sees (rows made on first use), in
    venue-name order."""
    venues = (
        db.query(Venue)
        .filter(Venue.organization_id == ctx.org_id, ctx.venue_clause(Venue.id))
        .order_by(Venue.name)
        .all()
    )
    return [get_or_create(db, ctx, v.id) for v in venues]


def payroll_on(db: Session, ctx: FinanceCtx, venue_id: str) -> bool:
    return bool(get_or_create(db, ctx, venue_id).payroll_on)


def update_settings(
    db: Session, ctx: FinanceCtx, venue_id: str, *, version: int, **fields
) -> PayEntity:
    """Change an entity's settings (``payroll:write``). Norm's own settings,
    so shadow mode allows it — nothing here reaches Loaded or Xero."""
    ctx.require("payroll:write")
    entity = get_or_create(db, ctx, venue_id)
    if entity.version != version:
        from app.services.finance_core.errors import Conflict

        raise Conflict(
            "These settings were changed by someone else since you opened them — "
            "reload and try again."
        )
    unknown = sorted(set(fields) - set(EDITABLE))
    if unknown:
        raise Invalid(f"Not a payroll setting: {', '.join(unknown)}.")
    for name, value in fields.items():
        _validate(name, value)
        setattr(entity, name, value)
    entity.version = version + 1
    flush(db)
    return entity


def _validate(name: str, value) -> None:
    if name == "pay_week_start_weekday" and value != 1:
        raise Invalid("Pay weeks start on a Monday in this version.")
    if name == "payroll_system" and value != "ace":
        raise Invalid("Ace Payroll is the only payroll system in this version (D7).")
    if name in ("uniform_bond_cents", "ico_leave_uplift_bp"):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise Invalid(f"{name} must be a whole number of at least 0.")
    if name in ("ace_pay_type_code", "security_pay_code"):
        if not isinstance(value, str) or not value.strip():
            raise Invalid(f"{name} cannot be blank.")
    if name == "payroll_on" and not isinstance(value, bool):
        raise Invalid("payroll_on must be true or false.")


def contact_email(org, entity: PayEntity | None) -> str | None:
    """Where this entity's payroll emails go: the entity's override, else the
    organisation's ``payroll_contact_email`` (D25). None means no send."""
    override = (getattr(entity, "payroll_contact_email", None) or "").strip()
    if override:
        return override
    value = (getattr(org, "payroll_contact_email", None) or "").strip()
    return value or None


def as_dict(entity: PayEntity, venue: Venue | None = None) -> dict:
    out = {
        "id": entity.id,
        "venue_id": entity.venue_id,
        "venue_name": venue.name if venue is not None else None,
        "is_trading": bool(venue.is_trading) if venue is not None else None,
        "loaded_company_id": entity.loaded_company_id,
        "version": entity.version,
        "day_start": day_start_for(venue) if venue is not None else None,
        "timezone": timezone_name(venue) if venue is not None else None,
    }
    for name in EDITABLE:
        out[name] = getattr(entity, name)
    return out
