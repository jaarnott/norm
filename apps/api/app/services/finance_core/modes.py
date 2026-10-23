"""Each Finance module's switch on the organisation: ``off | shadow | live``.

Three columns on ``organizations`` — ``payroll_mode``, ``entertainers_mode``,
``finance_mode`` (F0's migration) — beside Functions' ``functions_mode``. The
shadow rule (plan §6): until a module is live, Norm only computes beside
Orbit — snapshots, drafts, parity — and every Loaded, Xero and customer-facing
write refuses (``FinanceCtx.require_live``). ``live`` allows writes and
refuses the importer. Entertainers may go live before payroll (the columns
allow it; the plan does not).

Fail closed: an organisation that does not exist, a column the migration has
not added yet, or a value outside the vocabulary all read as ``off``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.finance_core.errors import Invalid, NotFound

#: The three modules, in the order the tick runs them.
MODULES = ("payroll", "entertainers", "finance")
MODES = ("off", "shadow", "live")


def column(module: str) -> str:
    """The ``organizations`` column holding a module's mode."""
    if module not in MODULES:
        raise Invalid(f"Unknown finance module {module!r}.")
    return f"{module}_mode"


def modes_of(org) -> dict[str, str]:
    """Every module's mode from a loaded ``Organization`` row (``off`` for a
    column the row does not have yet)."""
    out = {}
    for module in MODULES:
        value = getattr(org, column(module), None) if org is not None else None
        out[module] = value if value in MODES else "off"
    return out


def all_modes(db: Session, org_id: str | None) -> dict[str, str]:
    """Every module's mode for an organisation, read fresh."""
    if not org_id:
        return {m: "off" for m in MODULES}
    from app.db.models import Organization

    org = db.query(Organization).filter(Organization.id == org_id).first()
    return modes_of(org)


def mode(db: Session, org_id: str | None, module: str) -> str:
    """One module's mode (``off`` when anything is missing)."""
    column(module)  # validates the name
    return all_modes(db, org_id)[module]


def set_mode(db: Session, org_id: str, module: str, value: str) -> None:
    """Switch a module — for the admin screen and scripts, never for a
    request a page makes. Flushes; the caller commits."""
    col = column(module)
    if value not in MODES:
        raise Invalid(f"A module's mode is one of {', '.join(MODES)} — not {value!r}.")
    from app.db.models import Organization

    if not hasattr(Organization, col):
        # Setting a plain attribute on the row would look like it worked and
        # persist nothing.
        raise RuntimeError(
            f"organizations.{col} is not in the model yet — the finance scopes "
            "and modes migration has not landed."
        )
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise NotFound("Organisation not found.")
    setattr(org, col, value)
    db.flush()
