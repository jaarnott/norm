"""Payroll — the ``pay_*`` services (plan: docs/plans/finance-migration.md §9–§10).

F3 owns the week, snapshot, leave, gratuity, export and job modules; F4 owns
the wages reconciliation and the Xero side (``reconciliation``, ``intercompany``,
``journal``, ``xero_mapping``, ``xero_pushes``, ``xero_status``). This file is
deliberately empty: each module is imported by its full path.
"""
