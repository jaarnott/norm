# ruff: noqa: F821 — `_push_budgets` comes from set_budgets_common.py, which
# scripts/sync_payroll_config.py prepends to this file (the sandbox allows no
# imports); not an import.
#
# Canonical function_code for `loadedhub.set_budgets_typed` — the ENGINE-ONLY
# twin of set_budgets, never in a model's menu. Only the page-side approval
# helper runs it, from the Budgets day grid's type→sync and "Sync month to
# Loaded": a person typed the number, so it may be always-allowed per person
# (approval allow_auto true) — the one forecasting habit the business keeps
# (268 edits in three months), which a card on every keystroke would break.
# The ledger records every push from this row as source "manual".
#
# Same behaviour as set_budgets, by construction: the shared body is
# prepended to both files; this one only names its row.
#
# Requires consolidator_config: {"max_api_calls": 100,
#   "allowed_write_actions": ["update_budget_raw", "create_budget_raw",
#                             "norm_finance.record_budget_push"]}


def run(params, call_api, log):
    return _push_budgets(params, call_api, log, "set_budgets_typed")
