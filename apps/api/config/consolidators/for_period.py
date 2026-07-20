# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for every `*_for_period` consolidator on the
# `loadedhub` spec. ONE reviewed implementation serves them all: the tools
# differ only in which action they wrap and what that action calls its date
# parameters, so that lives in each tool's consolidator_config rather than in a
# copy of this file per tool.
#
# Runs inside the consolidator sandbox: no imports, `call_api` only.
# Required consolidator_config:
#   {
#     "max_api_calls": 6,
#     "allowed_write_actions": [],        # reads only, always
#     "wraps": "get_pos_orders",          # the action this fronts
#     "start_param": "start",             # what that action calls its window start
#     "end_param": "end",                 # ... and its end
#   }
#
# WHY THIS EXISTS
#
# The underlying tools take raw ISO timestamps, so the caller has to work out
# the window itself. Claude computed midnight-to-midnight for "yesterday", but a
# hospitality trading day runs from the venue's day_start_time (07:00) to one
# second before it the next day. A late-night venue read $0 for a Saturday —
# which looks like a POS outage rather than a bad window, so a wrong window
# produced a confident wrong diagnosis.
#
# These tools remove the chance to get it wrong: they take a period in plain
# English and resolve it through Norm's own venue-aware calendar. The rule stops
# being advice a client may ignore and becomes a property of the interface.
#
# The underlying actions also name their window seven different ways
# (start_datetime, start, start_time, from, from_iso, start_date...). Fronting
# them with one shape means a caller learns it once.

# Params this tool consumes; everything else is forwarded to the wrapped action
# so its own arguments (interval, posIdentifier, flags...) still work.
_CONSUMED = ("period", "start", "end", "confirmed_by_user", "venue_id", "mode")


def _window_from(resolved):
    """Pull the window dict out of a resolve_dates result, or None."""
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _rows_of(payload):
    """The array of records inside a payload, if there is one."""
    if isinstance(payload, list):
        return payload if payload and isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return None


def _summarise(payload):
    """Row count and column sums, so the answer is readable without the rows.

    Why: the model was being handed 120 product rows to answer "what were
    sales yesterday". Reading them costs tokens and doing arithmetic across
    them is an error source — a total computed here is exact, whereas one the
    model adds up by eye is a guess that looks like a fact.

    `column_sums` is every numeric column added up, and is deliberately named
    that rather than "totals": summing a rate or a unit price is meaningless,
    so the caller has to pick the columns that make sense. Nothing is invented
    — these are sums of values the API already returned.
    """
    rows = _rows_of(payload)
    if not rows:
        return None
    sums = {}
    for row in rows:
        for key, value in row.items():
            # bool is an int in Python; summing flags would be nonsense.
            if value is True or value is False:
                continue
            if isinstance(value, int) or isinstance(value, float):
                sums[key] = round(sums.get(key, 0) + value, 2)
    summary = {"row_count": len(rows)}
    if sums:
        summary["column_sums"] = sums
        summary["_note"] = (
            "column_sums adds up every numeric column. Sums of rates or unit "
            "prices are not meaningful — use only the columns that are."
        )
    return summary


def run(params, call_api, log, call_api_parallel=None, options=None):
    options = options or {}
    wraps = options.get("wraps")
    start_param = options.get("start_param")
    end_param = options.get("end_param")
    if not wraps or not start_param or not end_param:
        return {
            "error": (
                "Misconfigured: consolidator_config needs wraps, start_param and "
                "end_param. This is a Norm configuration problem, not something "
                "you can fix by changing your request."
            )
        }

    period = (params.get("period") or "").strip()
    start = params.get("start")
    end = params.get("end")
    confirmed = bool(params.get("confirmed_by_user"))

    if not period and not (start and end):
        return {
            "error": (
                "Give a period in plain English (e.g. 'yesterday', 'last week'). "
                "Only pass start and end if the user asked for specific clock times."
            )
        }

    # One resolver for both paths, so the venue's calendar is applied the same
    # way whichever the caller used. The sandbox allows no imports, so it is
    # reached as a tool rather than a function — which is correct anyway: the
    # calendar stays in Norm instead of being copied into config-DB code, and
    # config-DB code is shared by every organisation.
    resolve_args = {}
    if params.get("venue_id"):
        resolve_args["venue_id"] = params["venue_id"]
    if period:
        resolve_args["query"] = period
    else:
        resolve_args["start"] = start
        resolve_args["end"] = end

    resolved = call_api("norm", "resolve_dates", resolve_args)
    if isinstance(resolved, dict) and resolved.get("error"):
        return {"error": "Could not resolve the period: " + str(resolved["error"])}

    window = _window_from(resolved)
    if not window:
        return {
            "error": (
                "Could not resolve '" + (period or "that range") + "' to a date range. "
                "Try a simpler period such as 'yesterday' or 'last week'."
            )
        }

    # The deviation check. Ask a question the caller can answer from fact —
    # "did the user ask for these times?" — rather than "is this right?",
    # which invites agreement and would launder a mistake as confirmed.
    if not window.get("trading_aligned") and not confirmed:
        log("explicit window is not a trading day; asking before fetching")
        return {
            "needs_confirmation": True,
            "window": window,
            "question": (
                "These times are not this venue's trading day. "
                + str(window.get("description", ""))
                + " Did the user explicitly ask for these exact clock times? "
                "If yes, call again with confirmed_by_user=true. If they asked "
                "for a named period such as 'yesterday', call again with "
                "period set instead and no start/end."
            ),
        }

    forwarded = {k: v for k, v in params.items() if k not in _CONSUMED}
    forwarded[start_param] = window["start"]
    forwarded[end_param] = window["end"]

    # Fill the wrapped action's own defaults for anything the caller omitted.
    # These come from that action's spec template (e.g. get_sales_data renders
    # `interval | default('1.00:00:00')`), so nothing is invented here — the
    # value is the one the request would have used anyway.
    #
    # Why it matters: the executor validates the WRAPPED action's
    # required_fields, so an omitted `interval` is refused with "Missing
    # required fields: interval" AFTER the window resolves — even though the
    # template would have defaulted it. That also makes the tool robust to a
    # client calling with a stale copy of the schema, which is otherwise an
    # unfixable-from-here failure.
    for key, value in (options.get("defaults") or {}).items():
        if not forwarded.get(key):
            forwarded[key] = value

    data = call_api("loadedhub", wraps, forwarded)
    if isinstance(data, dict) and data.get("error"):
        return {"error": str(data["error"]), "window": window}

    # Always say which window produced these numbers. This is what turns a
    # silently wrong answer into a visible one — a $0 venue is only
    # diagnosable if you can see the window it was measured over.
    # The answer first, then the rows. A caller that only needs the headline
    # never has to read the data at all.
    result = {"window": window}
    summary = _summarise(data)
    if summary:
        result["summary"] = summary
    result["data"] = data
    return result
