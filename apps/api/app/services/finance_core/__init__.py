"""The core the three Finance modules share — Payroll (``payroll``),
Entertainers (``entertainers``) and Budget (``finance``), one App each.

What lives here is only what every module needs the same way:

* ``errors`` — the domain errors routers map to HTTP once.
* ``modes`` — each module's ``off | shadow | live`` switch on the organisation.
* ``ctx`` — who is asking, for which organisation, at which venues, holding
  which scopes, with the modes as they stood (``FinanceCtx``).
* ``jobs`` / ``tick`` — the one claim table for every scheduled job, and the
  tick that runs the modules' due jobs.
* ``parity`` — the shadow period's record of Norm vs Orbit differences.
* ``catalog`` — the two Apps as the marketplace seed declares them, and the
  mode rule entitlements apply.

Conventions (Functions' — ``app/services/functions``): every call takes a
``FinanceCtx``; every write calls ``ctx.require_live(module)``; services
``flush``, the caller commits; failures are ``FinanceError`` subclasses,
never HTTP exceptions.

Importing this package completes ``Base.metadata`` with the ``pay_*``,
``ent_*`` and ``fin_*`` tables.
"""

from app.db import entertainers_models, finance_models, payroll_models  # noqa: F401
from app.services.finance_core.errors import (
    Conflict,
    FinanceError,
    Forbidden,
    Invalid,
    NotFound,
    NotLive,
)

__all__ = [
    "Conflict",
    "FinanceError",
    "Forbidden",
    "Invalid",
    "NotFound",
    "NotLive",
]
