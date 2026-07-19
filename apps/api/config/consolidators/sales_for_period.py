# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for the `loadedhub.get_sales_for_period` consolidator.
# This file is the reviewed, version-controlled source of truth; its contents
# are synced verbatim into the ConnectorSpec tool's
# consolidator_config.function_code in the config DB (see README.md).
#
# Runs inside the consolidator sandbox: no imports, `call_api` only.
# Requires consolidator_config:
#   {"max_api_calls": 6, "allowed_write_actions": []}   # reads only
#
# WHY THIS EXISTS
#
# get_sales_data takes raw ISO timestamps, so a caller has to work out the
# window itself. Claude computed midnight-to-midnight for "yesterday", but a
# hospitality trading day runs from the venue's day_start_time (07:00) to one
# second before it the next day. The numbers were wrong, and a late-night venue
# read $0 for a Saturday — which looks like a POS outage rather than a bad
# window, so the wrong window produced a confident wrong diagnosis.
#
# This tool removes the chance to get it wrong: it accepts a period in plain
# English and resolves it through Norm's own calendar (norm.resolve_dates,
# which is venue-aware), then calls get_sales_data with the resolved window.
# The rule stops being advice and becomes a property of the interface.
#
# An explicit start/end is still honoured verbatim — never snapped — because
# reconciling against a bank statement legitimately wants civil days. But when
# an explicit range does not line up with the venue's trading day, Norm cannot
# tell an informed override from a caller that simply didn't know. Only the
# caller has that context, so this returns the analysis and asks, WITHOUT the
# data: handing back wrong-window numbers with a footnote invites exactly the
# confident-but-wrong answer this exists to prevent.


def _window_from(resolved):
    """Pull the window dict out of a resolve_dates result, or None."""
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def run(params, call_api, log, call_api_parallel=None):
    venue = params.get("venue")
    venue_id = params.get("venue_id")
    period = (params.get("period") or "").strip()
    start = params.get("start")
    end = params.get("end")
    interval = params.get("interval") or "1.00:00:00"
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
    # calendar stays in Norm instead of being copied into config-DB code.
    resolve_args = {"venue_id": venue_id} if venue_id else {}
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

    sales = call_api(
        "loadedhub",
        "get_sales_data",
        {
            "venue": venue,
            "start_datetime": window["start"],
            "end_datetime": window["end"],
            "interval": interval,
        },
    )
    if isinstance(sales, dict) and sales.get("error"):
        return {"error": str(sales["error"]), "window": window}

    # Always say which window produced these numbers. This is what turns a
    # silently wrong answer into a visible one — the $0 venue is only
    # diagnosable if you can see the window it was measured over.
    return {"window": window, "sales": sales}
