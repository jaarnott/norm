# ruff: noqa: F821 — `datetime` is injected into the sandbox namespace by
# app/connectors/function_executor.py; it is not an import.
#
# Canonical function_code for the `loadedhub.get_budgets` consolidator — the
# ONE budget surface. Synced into the config DB by
# scripts/sync_budget_dates.py; the raw HTTP endpoint behind it
# (`get_budgets_raw`) is [consolidator-only] and never bound to an agent.
#
# Why a consolidator at all: Loaded's /api/budgets dates every budget one day
# AFTER the day it belongs to (confirmed 21 Aug 2026 — the venue's Thursday
# $22k rode Friday's date, and the whole curve only aligns with the venue's
# sales once shifted back a day: the Saturday budget peak had been landing on
# Sunday). The serialization looks innocent (clean venue-local midnights),
# which is why it survived. The API's from/to filter compares the STORED
# instants against [from 00:00Z, to 00:00Z), so covering true days [F, T]
# means querying [F, T+1] — the old tool's `from | shift_days(-1)` hack
# compensated at the wrong end (it included yesterday and dropped the range's
# last day).
#
# Requires consolidator_config: {"max_api_calls": 2}


def run(params, call_api, log):
    def to_date(value, name):
        try:
            return datetime.date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}")

    frm = to_date(params.get("from_date"), "from_date")
    to_param = params.get("to_date")
    # A single date is a common ask ("budget for the week of the 23rd") —
    # default the window to that week.
    to = to_date(to_param, "to_date") if to_param else frm + datetime.timedelta(days=6)
    if to < frm:
        frm, to = to, frm
    if (to - frm).days > 400:
        return {"error": "date range too large — ask for 400 days or fewer"}

    rows = call_api(
        "loadedhub",
        "get_budgets_raw",
        {
            "venue": params.get("venue"),
            "from_date": frm.isoformat(),
            # +1: the filter is to-exclusive over the shifted instants (see
            # header) — this is what makes the range's LAST day come back.
            "to_date": (to + datetime.timedelta(days=1)).isoformat(),
        },
    )
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("items") or []

    day_names = (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday",
    )  # fmt: skip
    days = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            dated = datetime.date.fromisoformat(str(r.get("date") or "")[:10])
        except ValueError:
            log(f"unparseable budget date skipped: {r.get('date')!r}")
            continue
        # The correction: the day this budget is FOR is the day BEFORE
        # Loaded's date.
        true = dated - datetime.timedelta(days=1)
        if true < frm or true > to:
            continue
        days.append(
            {
                "date": true.isoformat(),
                "day": day_names[true.weekday()],
                "amount": float(r.get("amount") or 0),
                "sales_tax_rate": r.get("salesTax"),
            }
        )
    days.sort(key=lambda d: d["date"])

    # Weekly subtotals (Mon-Sun) and gaps — the two things every budget
    # report recomputes by hand, precomputed so the numbers can't drift.
    weeks = {}
    for d in days:
        dd = datetime.date.fromisoformat(d["date"])
        monday = dd - datetime.timedelta(days=dd.weekday())
        w = weeks.setdefault(
            monday.isoformat(),
            {
                "week_start": monday.isoformat(),
                "week_end": (monday + datetime.timedelta(days=6)).isoformat(),
                "total": 0.0,
            },
        )
        w["total"] = round(w["total"] + d["amount"], 2)
    have = {d["date"] for d in days}
    missing = []
    cursor = frm
    while cursor <= to:
        if cursor.isoformat() not in have:
            missing.append(cursor.isoformat())
        cursor += datetime.timedelta(days=1)

    return {
        "venue": params.get("venue"),
        "from": frm.isoformat(),
        "to": to.isoformat(),
        "days": days,
        "total": round(sum(d["amount"] for d in days), 2),
        "weeks": [weeks[k] for k in sorted(weeks)],
        "days_without_budget": missing,
        "note": (
            "dates are the day each budget is FOR (source dates corrected; "
            "weekday included)"
        ),
    }
