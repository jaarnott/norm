"""The statutory rules payroll encodes, as dated rows in ``pay_rules``
(plan §9.1): the Holidays Act replacement is a new row and a test, not a
code change.

A rule is resolved for an organisation on a date as: the organisation's own
latest row with ``effective_from <= date``, else the platform default's
(``organization_id`` NULL). The platform defaults are seeded idempotently on
first use (``seed_defaults``); the unique index on (org, rule, effective_from)
is NULLS NOT DISTINCT, so a second seed of the same default is refused by the
database as well as skipped here.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.payroll_models import PayRule
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import Invalid, flush

#: Holidays Act 2003 commenced on 1 April 2004.
_HOLIDAYS_ACT_FROM = dt.date(2004, 4, 1)

#: Sick-leave eligibility: six months' current continuous employment, an
#: average of at least 10 hours a week, and at least 1 hour every week or 40
#: hours every month — over the look-back the test runs on.
SICK_ELIGIBILITY = {
    "months_employed": 6,
    "avg_hours_per_week": 10,
    "min_hours_per_week": 1,
    "min_hours_per_month": 40,
    "lookback_weeks": 26,
}
#: A sick "day" is the hours the employee would otherwise have worked that
#: day; this is the default when their usual day cannot be worked out.
SICK_DAY_HOURS = {"hours": 8}

PLATFORM_DEFAULTS = (
    {
        "rule": "sick_eligibility",
        "effective_from": _HOLIDAYS_ACT_FROM,
        "params": SICK_ELIGIBILITY,
        "source": "Holidays Act 2003 ss 63–65",
    },
    {
        "rule": "sick_day_hours",
        "effective_from": _HOLIDAYS_ACT_FROM,
        "params": SICK_DAY_HOURS,
        "source": "Holidays Act 2003 s 63 (the hours the employee would otherwise have worked)",
    },
)
RULES = tuple(d["rule"] for d in PLATFORM_DEFAULTS)


def seed_defaults(db: Session) -> int:
    """Insert the platform defaults that are missing. Returns how many were
    added. Flushes; the caller commits."""
    added = 0
    for default in PLATFORM_DEFAULTS:
        exists = (
            db.query(PayRule.id)
            .filter(
                PayRule.organization_id.is_(None),
                PayRule.rule == default["rule"],
                PayRule.effective_from == default["effective_from"],
            )
            .first()
        )
        if exists:
            continue
        db.add(PayRule(organization_id=None, **default))
        added += 1
    if added:
        flush(db)
    return added


def rule_for(
    db: Session, org_id: str | None, rule: str, on_date: dt.date
) -> PayRule | None:
    """The row that applies to ``org_id`` on ``on_date``: the organisation's
    latest effective row, else the platform's. Seeds the defaults first so a
    fresh database answers."""
    seed_defaults(db)
    if org_id:
        own = (
            db.query(PayRule)
            .filter(
                PayRule.organization_id == org_id,
                PayRule.rule == rule,
                PayRule.effective_from <= on_date,
            )
            .order_by(PayRule.effective_from.desc())
            .first()
        )
        if own is not None:
            return own
    return (
        db.query(PayRule)
        .filter(
            PayRule.organization_id.is_(None),
            PayRule.rule == rule,
            PayRule.effective_from <= on_date,
        )
        .order_by(PayRule.effective_from.desc())
        .first()
    )


def params_for(db: Session, org_id: str | None, rule: str, on_date: dt.date) -> dict:
    """The rule's parameters on a date — the platform default's when nothing
    is stored for the date (a date before the Act is answered with the
    current rule rather than nothing)."""
    row = rule_for(db, org_id, rule, on_date)
    if row is not None:
        return dict(row.params or {})
    for default in PLATFORM_DEFAULTS:
        if default["rule"] == rule:
            return dict(default["params"])
    raise Invalid(f"Unknown payroll rule {rule!r}.")


def list_rules(db: Session, ctx: FinanceCtx) -> list[PayRule]:
    """Every platform default plus this organisation's overrides — the
    statutory rules only (the importer keeps its ``post_freeze_watch``
    window in this table too; that is not a rule a payroll person edits)."""
    seed_defaults(db)
    from sqlalchemy import or_

    return (
        db.query(PayRule)
        .filter(
            or_(
                PayRule.organization_id.is_(None), PayRule.organization_id == ctx.org_id
            ),
            PayRule.rule.in_(RULES),
        )
        .order_by(PayRule.rule, PayRule.effective_from, PayRule.organization_id)
        .all()
    )


def set_rule(
    db: Session,
    ctx: FinanceCtx,
    *,
    rule: str,
    effective_from: dt.date,
    params: dict,
    source: str | None = None,
) -> PayRule:
    """An organisation's own row for a rule from a date (``payroll:write``).
    The same rule and date replaces its params."""
    ctx.require("payroll:write")
    if rule not in RULES:
        raise Invalid(f"Unknown payroll rule {rule!r} — one of {', '.join(RULES)}.")
    if not isinstance(params, dict) or not params:
        raise Invalid("A rule needs its parameters.")
    row = (
        db.query(PayRule)
        .filter(
            PayRule.organization_id == ctx.org_id,
            PayRule.rule == rule,
            PayRule.effective_from == effective_from,
        )
        .first()
    )
    if row is None:
        row = PayRule(
            organization_id=ctx.org_id, rule=rule, effective_from=effective_from
        )
        db.add(row)
    row.params = dict(params)
    row.source = source
    flush(db)
    return row


def as_dict(row: PayRule) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "rule": row.rule,
        "effective_from": row.effective_from.isoformat(),
        "params": row.params,
        "source": row.source,
        "is_platform_default": row.organization_id is None,
    }
