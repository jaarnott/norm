# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `xero.get_accounts` consolidator — the chart
# of accounts and tax rates of the venue's Xero organisation. The xero_common
# helpers are prepended by scripts/sync_xero_connector.py.
#
# Requires consolidator_config: {"max_api_calls": 3, "allowed_write_actions": []}


def run(params, call_api, log):
    kind = str(params.get("kind") or "expense").strip().lower()
    query = str(params.get("query") or "").strip().lower()
    limit = int(params.get("limit") or 200)
    vp = xero_venue_params(params)
    settings, err = xero_settings(call_api, params)
    if err:
        return {"error": err}

    payload = call_api("xero", "list_accounts", {**vp, "where": 'Status=="ACTIVE"'})
    rows, err = xero_rows(payload, "Accounts")
    if err:
        return {"error": f"Xero accounts unavailable: {err}"}

    # kind: expense (DIRECTCOSTS, EXPENSE, OVERHEADS — where a supplier bill's
    # lines go), revenue (SALES, OTHERINCOME), bank, all.
    kinds = {
        "expense": {"DIRECTCOSTS", "EXPENSE", "OVERHEADS"},
        "revenue": {"SALES", "OTHERINCOME"},
        "bank": {"BANK"},
    }
    wanted = kinds.get(kind)
    accounts = []
    for a in rows:
        if wanted and str(a.get("Type") or "") not in wanted:
            continue
        hay = (str(a.get("Code") or "") + " " + str(a.get("Name") or "")).lower()
        if query and query not in hay:
            continue
        accounts.append(
            {
                "code": a.get("Code"),
                "name": a.get("Name"),
                "type": a.get("Type"),
                "class": a.get("Class"),
                "tax_type": a.get("TaxType"),
                "account_id": a.get("AccountID"),
            }
        )
    accounts.sort(key=lambda a: (str(a.get("code") or "~"), str(a.get("name") or "")))

    tax_payload = call_api("xero", "list_tax_rates", dict(vp))
    tax_rows, tax_err = xero_rows(tax_payload, "TaxRates")
    tax_rates = []
    if tax_err:
        log(f"tax rates unavailable: {tax_err}")
    else:
        for t in tax_rows:
            if str(t.get("Status") or "ACTIVE") != "ACTIVE":
                continue
            tax_rates.append(
                {
                    "tax_type": t.get("TaxType"),
                    "name": t.get("Name"),
                    "rate": t.get("EffectiveRate"),
                    "for_expenses": bool(t.get("CanApplyToExpenses")),
                    "for_revenue": bool(t.get("CanApplyToRevenue")),
                }
            )

    mapping = settings.get("xero_mapping") or {}
    return {
        "organisation": settings.get("organisation"),
        "country": settings.get("xero_country"),
        "currency": settings.get("xero_currency"),
        "mapping": mapping,
        "kind": kind,
        "accounts": accounts[:limit],
        "account_count": len(accounts),
        "tax_rates": tax_rates,
    }
