"""The shadow period's record: Norm's figures beside Orbit's, every
difference named (plan §14).

A run compares one module's figures for one period — weekly from the tick
after the nightly importer, or on demand from the Shadow tab — and writes
one item per difference. An item is ``expected`` when it belongs to a named
class of known difference (``EXPECTED_CLASSES``: things Orbit got wrong or
did differently, which Norm will not reproduce) or ``unexplained``. The gate
before each cut-over step counts unexplained items; the payroll person signs
items off as they are understood.

The classes are a table here, keyed by a short code, so a parity job names
the class rather than retyping its wording, and the Shadow tab can list what
each means.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.db.finance_models import (
    PARITY_CLASSES,
    PARITY_MODULES,
    PARITY_TRIGGERS,
    FinParityItem,
    FinParityRun,
)
from app.db.models import _now
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import Invalid, NotFound

#: Expected-difference classes, seeded on day one from plan §14.
EXPECTED_CLASSES: dict[str, str] = {
    "loaded_attribution": (
        "Loaded-side payroll attribution vs Orbit's client-side timeline (staff "
        "Orbit dropped for lack of a timeline row)."
    ),
    "dropped_clockins": (
        "Unfinished and deleted clock-ins, which Orbit mapped as zero rows and "
        "Norm drops at snapshot and counts."
    ),
    "other_team_plug": "The 'Other team' line vs Orbit's manual plugs.",
    "budget_day_shift": (
        "The one-day budget shift: Loaded dates each budget a day late and Norm "
        "corrects it; Orbit did not."
    ),
    "summed_percentages": (
        "Summed percentages — the Financial Report's totals row added the "
        "per-venue percentages."
    ),
    "budget_as_actual": (
        "Budget used as actual, and the 12% leave guess, in Orbit's Financial "
        "Summary (D33)."
    ),
    "gratuity_residual": (
        "Gratuity cent residuals: Orbit rounded each share with no residual "
        "correction; Norm assigns the leftover cents by largest remainder."
    ),
    "leave_style_default": (
        "Orbit defaulted an unmapped leave type to #70, the annual-leave style."
    ),
    "loaded_changed": (
        "Loaded changed between Norm's snapshot and Orbit's export (diffed by "
        "source_shift_id)."
    ),
}

#: The scope a module's parity record needs to read, and to sign off.
_READ_SCOPE = {"summary": "finance:read"}
_SIGN_SCOPE = {"summary": "budgets:write"}
_DEFAULT_READ, _DEFAULT_SIGN = "payroll:read", "payroll:write"


def _text(value) -> str | None:
    return None if value is None else str(value)


def record_run(
    db: Session,
    ctx: FinanceCtx,
    *,
    module: str,
    period: str,
    trigger: str,
    items: Iterable[dict],
    totals: dict | None = None,
) -> FinParityRun:
    """Write a run and its items. Each item is a dict with ``entity`` (a
    venue id or a label), ``key``, ``norm_value``, ``orbit_value`` and either
    ``code`` (an ``EXPECTED_CLASSES`` key — the item is ``expected`` and the
    class wording is its explanation unless one is given) or ``class``
    (default ``unexplained``) with an optional ``explanation``. Flushes; the
    caller commits."""
    if module not in PARITY_MODULES:
        raise Invalid(f"Unknown parity module {module!r}.")
    if trigger not in PARITY_TRIGGERS:
        raise Invalid(f"Unknown parity trigger {trigger!r}.")
    ctx.require(_READ_SCOPE.get(module, _DEFAULT_READ))
    run = FinParityRun(
        organization_id=ctx.org_id,
        module=module,
        period=str(period),
        trigger=trigger,
        totals=totals,
        ran_by=ctx.user_id,
    )
    db.add(run)
    db.flush()
    rows = []
    for item in items:
        code = item.get("code")
        if code is not None:
            if code not in EXPECTED_CLASSES:
                raise Invalid(f"Unknown expected-difference class {code!r}.")
            klass = "expected"
            explanation = item.get("explanation") or EXPECTED_CLASSES[code]
        else:
            klass = item.get("class", "unexplained")
            if klass not in PARITY_CLASSES:
                raise Invalid(
                    f"A parity item's class is expected or unexplained, not {klass!r}."
                )
            if klass == "expected" and not item.get("explanation"):
                raise Invalid(
                    "An expected difference names its class (code) or explains itself."
                )
            explanation = item.get("explanation")
        if not item.get("entity") or not item.get("key"):
            raise Invalid("A parity item needs an entity and a key.")
        rows.append(
            FinParityItem(
                organization_id=ctx.org_id,
                run_id=run.id,
                entity=str(item["entity"]),
                key=str(item["key"]),
                norm_value=_text(item.get("norm_value")),
                orbit_value=_text(item.get("orbit_value")),
                class_=klass,
                explanation=explanation,
            )
        )
    db.add_all(rows)
    db.flush()
    return run


def get_run(db: Session, ctx: FinanceCtx, run_id: str) -> FinParityRun:
    run = (
        db.query(FinParityRun)
        .filter(FinParityRun.organization_id == ctx.org_id, FinParityRun.id == run_id)
        .first()
    )
    if run is None:
        raise NotFound("Parity run not found.")
    ctx.require(_READ_SCOPE.get(run.module, _DEFAULT_READ))
    return run


def list_runs(
    db: Session,
    ctx: FinanceCtx,
    *,
    module: str | None = None,
    limit: int = 20,
) -> list[FinParityRun]:
    """Recent runs, newest first — only the modules the caller may read."""
    q = db.query(FinParityRun).filter(FinParityRun.organization_id == ctx.org_id)
    if module is not None:
        if module not in PARITY_MODULES:
            raise Invalid(f"Unknown parity module {module!r}.")
        q = q.filter(FinParityRun.module == module)
    readable = [
        m for m in PARITY_MODULES if ctx.holds(_READ_SCOPE.get(m, _DEFAULT_READ))
    ]
    q = q.filter(FinParityRun.module.in_(readable))
    return q.order_by(FinParityRun.ran_at.desc()).limit(limit).all()


def items_of(db: Session, ctx: FinanceCtx, run_id: str) -> list[FinParityItem]:
    run = get_run(db, ctx, run_id)
    return (
        db.query(FinParityItem)
        .filter(
            FinParityItem.organization_id == ctx.org_id, FinParityItem.run_id == run.id
        )
        .order_by(FinParityItem.entity, FinParityItem.key)
        .all()
    )


def unexplained_count(db: Session, ctx: FinanceCtx, run_id: str) -> int:
    """The gate's number: unexplained items not yet signed off."""
    run = get_run(db, ctx, run_id)
    return (
        db.query(FinParityItem)
        .filter(
            FinParityItem.organization_id == ctx.org_id,
            FinParityItem.run_id == run.id,
            FinParityItem.class_ == "unexplained",
            FinParityItem.signed_off_at.is_(None),
        )
        .count()
    )


def sign_off(
    db: Session, ctx: FinanceCtx, item_id: str, *, explanation: str | None = None
) -> FinParityItem:
    """The payroll (or budgets) person has understood the difference. An
    explanation given here replaces the item's."""
    item = (
        db.query(FinParityItem)
        .filter(
            FinParityItem.organization_id == ctx.org_id, FinParityItem.id == item_id
        )
        .first()
    )
    if item is None:
        raise NotFound("Parity item not found.")
    run = get_run(db, ctx, item.run_id)
    ctx.require(_SIGN_SCOPE.get(run.module, _DEFAULT_SIGN))
    if ctx.user_id is None:
        raise Invalid("A person signs a parity item off, not a job.")
    item.signed_off_by = ctx.user_id
    item.signed_off_at = _now()
    if explanation:
        item.explanation = explanation
    db.flush()
    return item
