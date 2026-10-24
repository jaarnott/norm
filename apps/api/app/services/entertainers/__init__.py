"""Entertainers — the performers, roster, approvals, portal, onboarding form,
invoice check, emails, closing manager, payments and Xero bills (plan
docs/plans/finance-migration.md §11; DESIGN-CONTRACT §4 row F5).

Every service takes a ``FinanceCtx`` (``finance_core.ctx``): the Register,
Roster and Emails work on venue access (D15); Payments, mark-paid, accept,
shares and the stored bank/IRD/DOB need ``payroll:write``; a performer on a
public link gets ``for_performer`` — no venues, no scopes, only their own
records. Nothing here reaches Loaded or Xero directly: the closing-manager
lookup goes through ``execute_connector_tool`` and bills through
``page_writes.confirm``.
"""
