# ruff: noqa: F821 — `_push_budgets` comes from set_budgets_common.py, which
# scripts/sync_payroll_config.py prepends to this file (the sandbox allows no
# imports); not an import.
#
# Canonical function_code for `loadedhub.set_budgets` — the Loaded budget
# write a model may call. It asks EVERY time (approval default "ask", no
# always-allow): a model can fill the numbers from a forecast, and a
# per-person always-allow would carry them past the card. The page helper
# runs this same row for the Forecasting grid's "Set Loaded budget". The
# ledger built-in records the source from the row and the door — chat when
# a thread is present, forecast when the page helper ran it.
#
# Requires consolidator_config: {"max_api_calls": 100,
#   "allowed_write_actions": ["update_budget_raw", "create_budget_raw",
#                             "norm_finance.record_budget_push"]}


def run(params, call_api, log):
    return _push_budgets(params, call_api, log, "set_budgets")
