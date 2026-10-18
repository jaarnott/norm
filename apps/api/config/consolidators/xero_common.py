# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# NOT a consolidator. Shared helpers for the xero_* consolidators, PREPENDED
# verbatim to each one's function_code by scripts/sync_xero_connector.py (the
# sandbox allows no imports, so sharing means concatenation). Keep it small
# and free of anything that depends on a single tool's params.
#
# Xero facts these lean on (verified live 17 Oct 2026):
# - every endpoint returns an envelope {"Id", "Status", "ProviderName",
#   "DateTimeUTC", "<Collection>": [...]} — unwrap by collection name;
# - call_api returns {"error": "..."} on failure and never raises;
# - dates come twice: "Date": "/Date(1757...)/" (.NET) and "DateString":
#   "2026-09-10T00:00:00" — always read the *String form;
# - deep links need the organisation's ShortCode (GET /Organisation), which a
#   consolidator cannot read from the venue's creds, so it is one extra call.


def xero_rows(payload, collection):
    """The list under ``collection`` in a Xero envelope, or an error string."""
    if not isinstance(payload, dict):
        return None, "Xero returned no data"
    if payload.get("error"):
        return None, str(payload.get("error"))
    rows = payload.get(collection)
    if not isinstance(rows, list):
        return None, f"Xero returned no {collection}"
    return [r for r in rows if isinstance(r, dict)], None


def xero_money(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def xero_date(v):
    """'2026-09-10T00:00:00' -> '2026-09-10'; anything else -> None."""
    s = str(v or "")
    return s[:10] if len(s) >= 10 and s[4] == "-" else None


def xero_venue_params(params):
    """The venue identity to forward on every call_api (name and/or id)."""
    out = {}
    for k in ("venue", "venue_id"):
        if params.get(k):
            out[k] = params[k]
    return out


def xero_settings(call_api, params):
    """THIS venue's Xero settings (organisation, country, currency, short code,
    account mapping), or an error when the venue is not connected.

    The guard every xero tool runs first. When a venue has no connection of
    its own, the executor resolves credentials by falling back to another
    venue's row in the same organisation — fine for a platform connector,
    wrong for Xero, where it would answer with another company's books under
    this venue's name. norm.get_connection_settings is strict: it reads the
    named venue's row only, and never returns a secret.
    """
    vp = xero_venue_params(params)
    if not vp:
        return None, "name the venue"
    res = call_api("norm", "get_connection_settings", {"connector": "xero", **vp})
    if not isinstance(res, dict) or res.get("error"):
        return None, (res or {}).get(
            "error"
        ) or "could not read the venue's Xero settings"
    if not res.get("connected"):
        who = vp.get("venue") or "this venue"
        why = " (reconnect it)" if res.get("needs_reconnect") else ""
        return None, (
            f"{who} is not connected to Xero{why} — connect it under "
            "Settings → Connections, then ask again"
        )
    return dict(res.get("settings") or {}), None


def xero_organisation(call_api, venue_params):
    """Name, country, currency, short code — or {} with an error logged."""
    payload = call_api("xero", "get_organisation", dict(venue_params))
    rows, err = xero_rows(payload, "Organisations")
    if err or not rows:
        return {}, err or "Xero returned no organisation"
    org = rows[0]
    return {
        "name": org.get("Name"),
        "country": org.get("CountryCode"),
        "currency": org.get("BaseCurrency"),
        "short_code": org.get("ShortCode"),
        "timezone": org.get("Timezone"),
    }, None


def xero_bill_url(short_code, invoice_id):
    """Deep link into Xero's bill screen (Xero's own MCP server uses this form)."""
    if not short_code or not invoice_id:
        return None
    return (
        "https://go.xero.com/organisationlogin/default.aspx?shortcode="
        + str(short_code)
        + "&redirecturl=/AccountsPayable/Edit.aspx?InvoiceID="
        + str(invoice_id)
    )


def xero_invoice_url(short_code, invoice_id):
    if not short_code or not invoice_id:
        return None
    return (
        "https://go.xero.com/app/"
        + str(short_code)
        + "/invoicing/view/"
        + str(invoice_id)
    )


def xero_end_date(end):
    """The last CALENDAR day a window covers, for Xero's inclusive toDate /
    Date<=. A plain YYYY-MM-DD is already inclusive. A timestamp is the
    window's boundary — Norm's windows end at the START of the next business
    day (06:59 the next morning, or midnight) — so one before midday is the
    previous calendar day."""
    end = str(end or "")
    day = end[:10]
    if len(end) < 13 or end[10] not in ("T", " "):
        return day
    try:
        hour = int(end[11:13])
        y, m, d = (int(x) for x in day.split("-"))
    except ValueError:
        return day
    if hour < 12:
        return (datetime.date(y, m, d) - datetime.timedelta(days=1)).isoformat()
    return day


def xero_window(call_api, params, venue_params):
    """(from_date, to_date, description, error) from from_date/to_date or a
    plain-English period resolved by Norm's calendar. ("", "", None, None)
    when neither was given.

    Facts verified live (18 Oct 2026):
    - call_api returns the handler's data ALREADY unwrapped, so the resolver
      answers {"window": {...}}; a wrapped {"success", "data": {"window"}} is
      accepted too (tests, MCP callers);
    - a dashboard's period control sends from_date/to_date as full timestamps
      on the venue's trading calendar, ending at 06:59 on the first day of
      the next period — see xero_end_date.
    """
    from_date = str(params.get("from_date") or "")[:10]
    to_raw = str(params.get("to_date") or "")
    if from_date or to_raw:
        return from_date, xero_end_date(to_raw), None, None
    period = str(params.get("period") or "").strip()
    if not period:
        return "", "", None, None
    q = {"query": period}
    if venue_params.get("venue_id"):
        q["venue_id"] = venue_params["venue_id"]
    elif venue_params.get("venue"):
        q["venue"] = venue_params["venue"]
    res = call_api("norm", "resolve_dates", q)
    if not isinstance(res, dict) or res.get("error"):
        return "", "", None, (res or {}).get("error") or f"could not resolve '{period}'"
    inner = res.get("data") if isinstance(res.get("data"), dict) else res
    window = inner.get("window") if isinstance(inner, dict) else None
    if not isinstance(window, dict) or not window.get("start") or not window.get("end"):
        return "", "", None, f"could not resolve '{period}'"
    return (
        str(window["start"])[:10],
        xero_end_date(window["end"]),
        window.get("description"),
        None,
    )
