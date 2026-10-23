"""Norm Finance — the ``fin_*`` services (plan: docs/plans/finance-migration.md §6.3, §8).

Budgets (``budgets``, ``budget_import``), the Weekly Summary's budget side
(``summary``), the forecasting inputs (``holidays``, ``actuals``,
``drivers``), the engine and its scoring (``forecast``, ``scoring``), the
Loaded push ledger (``pushes``) and the nightly job (``jobs``). Each module
is imported by its full path; every call takes a ``FinanceCtx`` from
``finance_core.ctx`` and raises ``finance_core.errors``.

The one rule that shapes everything here: Norm never writes Loaded's budget
from a service. A push goes through ``page_writes`` (a page) or an approval
card (chat) into the ``loadedhub.set_budgets`` consolidators, which call the
engine-only built-in ``norm_finance.record_budget_push`` for the ledger.
"""

MODULE = "finance"
