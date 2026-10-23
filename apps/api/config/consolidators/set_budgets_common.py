# ruff: noqa: F821 — sandbox-injected names (datetime, json, preview); not imports.
#
# The budget write, shared by `loadedhub.set_budgets` (the row a model sees;
# asks every time) and `loadedhub.set_budgets_typed` (engine-only; the day
# grid's type→sync, always-allowable per person). NOT a tool itself: the
# sandbox allows no imports, so scripts/sync_payroll_config.py prepends this
# file to each twin's function_code — the xero_common.py precedent. The twins
# differ only in the row name they hand to the ledger; `source` (manual /
# chat / forecast) is decided by that built-in from the row and the door,
# never by the model (plan docs/plans/finance-migration.md §7.1, §8, D6).
#
# Loaded facts this encodes (report 06 §2, verified live 18 Oct 2026):
#   - one Budget row per day: {id, date, amount, salesTax}; `amount` is
#     GST-inclusive; `salesTax` is the RATE (0.15);
#   - the row for trading day D is stored at (D+1) 00:00 venue-local, so a
#     read `from=F&to=T` is exclusive at T over those instants — covering
#     days F..T means asking for from=F, to=T+1;
#   - PUT /budgets/{id} applies only `amount` (204 — echo the row's own date
#     and salesTax); POST /budgets {date, amount} creates (200 with the new
#     row) and a second POST for the same day is a 500 — so a day that HAS a
#     row is only ever PUT; there is no DELETE, so a POSTed day stays;
#   - the POST date needs the venue's offset AT THAT MIDNIGHT: NZ is +13:00
#     half the year and +12:00 the other half (Orbit's +12:00 constant was
#     wrong half the year). Offsets come from the rows Loaded returned (each
#     carries its own) and, for days with no row, from norm.resolve_dates
#     probes of the exact instant — at most a handful, never one per day.
#
# What the card shows (every day: weekday, before, after, GST basis, update
# vs create) and the guards (a range over 92 days refused; $0 days only with
# allow_zero, listed; a move over 25% warned; refused unless Norm Finance is
# live; refused unless `venue` is exactly one connected Loaded venue — a
# venue-less call used to land on the organisation's first login).

_MAX_DAYS = 92
_WARN_MOVE = 0.25
_MAX_CALLS = 100
_FALLBACK_TAX = 0.15
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _flag(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _money(value):
    return "$" + "{:,.2f}".format(float(value))


def _day_label(d):
    return _DOW[d.weekday()] + " " + str(d.day) + " " + _MONTHS[d.month - 1]


def _days_arg(raw):
    """[{date, amount}] however it arrived (a list, or JSON text of one)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if isinstance(raw, dict):
        raw = [{"date": k, "amount": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return None
    return [d for d in raw if isinstance(d, dict)]


def _offset_text(iso_text):
    """'+13:00' out of an ISO instant, or None."""
    s = str(iso_text or "").strip()
    if s.endswith("Z"):
        return "+00:00"
    tail = s[-6:]
    if len(tail) == 6 and tail[0] in "+-" and tail[3] == ":":
        return tail
    return None


def _offset_delta(text):
    sign = -1 if text[0] == "-" else 1
    return datetime.timedelta(
        hours=sign * int(text[1:3]), minutes=sign * int(text[4:6])
    )


def _window_from(resolved):
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _resolve_venue(params, call_api):
    """(name, id, error) — the ONE connected Loaded venue the call names."""
    listed = call_api("norm", "list_venues", {"connector": "loadedhub"})
    data = (
        listed.get("data") if isinstance(listed, dict) and "data" in listed else listed
    )
    rows = (data or {}).get("venues") if isinstance(data, dict) else None
    if isinstance(listed, dict) and listed.get("error"):
        return (
            None,
            None,
            "could not list this organisation's venues: " + str(listed["error"]),
        )
    rows = [r for r in rows or [] if isinstance(r, dict)]
    name = str(params.get("venue") or params.get("venue_name") or "").strip()
    wanted_id = str(params.get("venue_id") or "").strip()
    if not name and wanted_id:
        for r in rows:
            if str(r.get("id")) == wanted_id:
                name = str(r.get("name") or "")
    if not name:
        return None, None, "venue is required — budgets are set one venue at a time"
    matches = [
        r
        for r in rows
        if str(r.get("name") or "").strip().casefold() == name.casefold()
    ]
    if not matches:
        return (
            None,
            None,
            "'" + name + "' isn't a venue in this organisation — use its full name",
        )
    if len(matches) > 1:
        return None, None, "'" + name + "' names more than one venue — be exact"
    if not matches[0].get("connected"):
        return None, None, "'" + name + "' isn't connected to Loaded"
    return str(matches[0]["name"]), str(matches[0].get("id") or ""), None


def _probe_offset(call_api, venue_id, date, guess):
    """The venue's UTC offset at local midnight starting `date`, as text.

    norm.resolve_dates converts an explicit instant into the venue's zone,
    so probing the UTC instant that midnight would be under `guess` returns
    the true offset: a guess an hour out lands at 23:00 or 01:00, and both
    NZ and AU change clocks between 02:00 and 03:00, so the probe is always
    inside the same regime as the midnight it stands for.
    """
    midnight = datetime.datetime(date.year, date.month, date.day)
    instant = (midnight - _offset_delta(guess)).isoformat() + "+00:00"
    args = {"start": instant, "end": instant}
    if venue_id:
        args["venue_id"] = venue_id
    window = _window_from(call_api("norm", "resolve_dates", args))
    return _offset_text(window.get("start")) if window else None


def _settle_offsets(create_days, known, call_api, venue_id, budget):
    """{create day: offset text} for days with no Loaded row.

    `known` maps trading days that HAVE a row to the offset Loaded stamped
    on that row's midnight. A day bracketed by equal known offsets shares
    them (one clock change at most in 92 days). The rest are probed at both
    ends; when the ends differ the change is found by bisection — ≤ 9 calls
    in all. Returns (offsets, error, calls used).
    """
    out = {}
    unsettled = []
    known_days = sorted(known)
    for d in create_days:
        before = [k for k in known_days if k < d]
        after = [k for k in known_days if k > d]
        if before and after and known[before[-1]] == known[after[0]]:
            out[d] = known[before[-1]]
        else:
            unsettled.append(d)
    if not unsettled:
        return out, None, 0
    calls = 0

    def guess_for(d):
        nearest = None
        for k in known_days:
            if nearest is None or abs((k - d).days) < abs((nearest - d).days):
                nearest = k
        return known[nearest] if nearest is not None else None

    guess = guess_for(unsettled[0])
    if guess is None:
        if budget < 1:
            return out, "too many calls for this range — narrow it", calls
        window = _window_from(
            call_api(
                "norm",
                "resolve_dates",
                {
                    "query": unsettled[0].isoformat(),
                    **({"venue_id": venue_id} if venue_id else {}),
                },
            )
        )
        calls += 1
        guess = _offset_text(window.get("start")) if window else None
        if guess is None:
            return out, "could not learn the venue's timezone offset", calls
    if budget - calls < 2:
        return out, "too many calls for this range — narrow it", calls
    # Probe midnight AFTER each trading day — that is the instant Loaded files.
    first, last = unsettled[0], unsettled[-1]
    off_a = _probe_offset(call_api, venue_id, first + datetime.timedelta(days=1), guess)
    calls += 1
    off_b = off_a
    if last != first:
        off_b = _probe_offset(
            call_api, venue_id, last + datetime.timedelta(days=1), off_a or guess
        )
        calls += 1
    if off_a is None or off_b is None:
        return out, "could not learn the venue's timezone offset", calls
    if off_a == off_b:
        for d in unsettled:
            out[d] = off_a
        return out, None, calls
    # One change between first and last: bisect for the first day on the
    # new offset (≤ 7 probes for 92 days).
    lo, hi = first, last
    while (hi - lo).days > 1:
        if budget - calls < 1:
            return out, "too many calls for this range — narrow it", calls
        mid = lo + datetime.timedelta(days=(hi - lo).days // 2)
        off_mid = _probe_offset(
            call_api, venue_id, mid + datetime.timedelta(days=1), off_a
        )
        calls += 1
        if off_mid is None:
            return out, "could not learn the venue's timezone offset", calls
        if off_mid == off_a:
            lo = mid
        else:
            hi = mid
    for d in unsettled:
        out[d] = off_a if d <= lo else off_b
    return out, None, calls


def _push_budgets(params, call_api, log, row):
    """The whole write for one twin; `row` names the tool for the ledger."""
    # 1. Norm Finance must be live (shadow and off never write Loaded).
    mode = call_api("norm_finance", "get_mode", {})
    if not isinstance(mode, dict) or mode.get("error"):
        why = mode.get("error") if isinstance(mode, dict) else str(mode)
        return {
            "error": "Norm Finance isn't available for budget pushes (" + str(why) + ")"
        }
    if str(mode.get("mode") or "") != "live":
        return {
            "error": "Norm Finance is "
            + repr(mode.get("mode"))
            + " for this organisation — budgets are pushed to Loaded only when it is live"
        }
    calls = 1

    # 2. Exactly one connected venue.
    venue, venue_id, err = _resolve_venue(params, call_api)
    calls += 1
    if err:
        return {"error": err}

    # 3. The days.
    days = _days_arg(params.get("days"))
    if days is None or not days:
        return {
            "error": "days is required: [{date: YYYY-MM-DD, amount: <GST-inclusive>}]"
        }
    wanted = {}
    for d in days:
        try:
            date = datetime.date.fromisoformat(str(d.get("date") or "")[:10])
        except ValueError:
            return {
                "error": "each day needs a date in YYYY-MM-DD form, got "
                + repr(d.get("date"))
            }
        try:
            amount = round(float(d.get("amount")), 2)
        except (TypeError, ValueError):
            return {"error": "the amount for " + date.isoformat() + " is not a number"}
        if amount < 0:
            return {"error": "the amount for " + date.isoformat() + " is negative"}
        if date in wanted:
            return {"error": date.isoformat() + " is listed twice"}
        wanted[date] = amount
    ordered = sorted(wanted)
    first, last = ordered[0], ordered[-1]
    span = (last - first).days + 1
    if span > _MAX_DAYS or len(ordered) > _MAX_DAYS:
        return {
            "error": "that covers "
            + str(span)
            + " days — at most "
            + str(_MAX_DAYS)
            + " per push; split the range"
        }
    allow_zero = _flag(params.get("allow_zero"), False)

    # 4. What Loaded holds for those days (to T+1: the read is exclusive).
    existing = call_api(
        "loadedhub",
        "get_budgets_raw",
        {
            "venue": venue,
            "from_date": first.isoformat(),
            "to_date": (last + datetime.timedelta(days=1)).isoformat(),
        },
    )
    calls += 1
    if isinstance(existing, dict) and existing.get("error"):
        return {
            "error": "could not read the venue's budgets from Loaded: "
            + str(existing["error"])
        }
    if isinstance(existing, dict):
        existing = existing.get("data") or existing.get("items") or []
    by_day = {}
    known_offsets = {}
    for r in existing if isinstance(existing, list) else []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        try:
            stored = datetime.date.fromisoformat(str(r.get("date") or "")[:10])
        except ValueError:
            continue
        trading = stored - datetime.timedelta(days=1)
        by_day[trading] = r
        off = _offset_text(r.get("date"))
        if off:
            known_offsets[trading] = off

    # 5. The plan, day by day.
    plan = []
    warnings = []
    skipped_zero = []
    for date in ordered:
        after = wanted[date]
        row_ = by_day.get(date)
        before = float(row_.get("amount") or 0) if row_ else None
        entry = {
            "date": date.isoformat(),
            "weekday": _DOW[date.weekday()],
            "before": before,
            "after": after,
            "loaded_id": str(row_.get("id")) if row_ else None,
            "loaded_date": str(row_.get("date")) if row_ else None,
            "sales_tax": row_.get("salesTax") if row_ else None,
            "ok": None,
            "error": None,
        }
        if after == 0 and not allow_zero:
            entry["action"] = "skipped"
            skipped_zero.append(date.isoformat())
        elif row_ is None:
            entry["action"] = "create"
        elif before is not None and abs(before - after) < 0.005:
            entry["action"] = "unchanged"
        else:
            entry["action"] = "update"
            if not isinstance(entry["sales_tax"], (int, float)):
                entry["sales_tax"] = _FALLBACK_TAX
                warnings.append(
                    _day_label(date)
                    + ": Loaded's row carries no sales-tax rate; "
                    + str(_FALLBACK_TAX)
                    + " echoed (Loaded re-stamps it from the company setting)"
                )
        if entry["action"] in ("create", "update") and before:
            move = (after - before) / before
            if abs(move) > _WARN_MOVE:
                warnings.append(
                    _day_label(date)
                    + " moves "
                    + ("+" if move > 0 else "")
                    + str(int(round(move * 100)))
                    + "% (from "
                    + _money(before)
                    + " to "
                    + _money(after)
                    + ")"
                )
        plan.append((date, entry))
    if skipped_zero:
        warnings.append(
            str(len(skipped_zero))
            + " day(s) at $0 skipped — pass allow_zero to send them: "
            + ", ".join(skipped_zero)
        )

    # 6. Offsets for the days Loaded has no row for.
    creates = [date for date, e in plan if e["action"] == "create"]
    writes = len([1 for _, e in plan if e["action"] in ("create", "update")])
    budget = _MAX_CALLS - calls - writes - 1  # the ledger call at the end
    if budget < 0:
        return {"error": "too many days for one push — split the range"}
    offsets, oerr, used = _settle_offsets(
        creates, known_offsets, call_api, venue_id, budget
    )
    calls += used
    if oerr:
        return {"error": oerr}
    for date, entry in plan:
        if entry["action"] == "create":
            entry["loaded_date"] = (
                (date + datetime.timedelta(days=1)).isoformat()
                + "T00:00:00"
                + offsets[date]
            )

    counts = {"update": 0, "create": 0, "unchanged": 0, "skipped": 0}
    for _, e in plan:
        counts[e["action"]] += 1
    range_label = (
        _day_label(first) + " – " + _day_label(last) + " " + str(last.year)
        if first != last
        else _day_label(first) + " " + str(first.year)
    )

    def card():
        changes = []
        for date, e in plan:
            if e["action"] == "skipped":
                continue
            before = "— (no budget)" if e["before"] is None else _money(e["before"])
            after = _money(e["after"])
            if e["action"] == "create":
                after += " (new)"
            elif e["action"] == "unchanged":
                after += " (unchanged)"
            changes.append(
                {"field": _day_label(date), "before": before, "after": after}
            )
        return {
            "title": "Set daily budgets in Loaded",
            "target": venue + " — " + range_label,
            "venue": venue,
            "changes": changes,
            "warnings": warnings,
            "note": (
                "Amounts include GST, as Loaded stores budgets. "
                + str(counts["update"])
                + " to update, "
                + str(counts["create"])
                + " to create, "
                + str(counts["unchanged"])
                + " unchanged, "
                + str(counts["skipped"])
                + " skipped."
            ),
        }

    preview(
        card,
        basis={
            "venue": venue,
            "days": [
                {
                    "date": e["date"],
                    "action": e["action"],
                    "loaded_id": e["loaded_id"],
                    "before": e["before"],
                    "after": e["after"],
                }
                for _, e in plan
            ],
        },
    )

    # 7. The writes — PUT a day with a row, POST a day without, never both.
    failed = []
    for date, e in plan:
        if e["action"] == "update":
            result = call_api(
                "loadedhub",
                "update_budget_raw",
                {
                    "venue": venue,
                    "budget_id": e["loaded_id"],
                    "loaded_date": e["loaded_date"],
                    "amount": e["after"],
                    "sales_tax": e["sales_tax"],
                },
            )
        elif e["action"] == "create":
            result = call_api(
                "loadedhub",
                "create_budget_raw",
                {"venue": venue, "loaded_date": e["loaded_date"], "amount": e["after"]},
            )
        else:
            e["ok"] = True
            continue
        if isinstance(result, dict) and result.get("not_sent"):
            return {"preview_only": True}
        if isinstance(result, dict) and result.get("error"):
            e["ok"] = False
            e["error"] = str(result["error"])
            failed.append(e["date"] + ": " + e["error"])
            log(
                "budget " + e["action"] + " failed for " + e["date"] + ": " + e["error"]
            )
            continue
        e["ok"] = True
        if e["action"] == "create" and isinstance(result, dict) and result.get("id"):
            e["loaded_id"] = str(result["id"])

    # 8. The ledger (fin_budget_pushes) — an internal write, declared in
    # allowed_write_actions like any other; `source` is the built-in's call.
    ledger = call_api(
        "norm_finance",
        "record_budget_push",
        {
            "tool": row,
            "venue": venue,
            "venue_id": venue_id,
            "from": first.isoformat(),
            "to": last.isoformat(),
            "allow_zero": allow_zero,
            "vintage_id": params.get("vintage_id") or None,
            "days": [
                {
                    "date": e["date"],
                    "before": e["before"],
                    "after": e["after"],
                    "action": e["action"],
                    "loaded_id": e["loaded_id"],
                    "ok": e["ok"],
                    "error": e["error"],
                }
                for _, e in plan
            ],
        },
    )
    if isinstance(ledger, dict) and ledger.get("not_sent"):
        return {"preview_only": True}
    if isinstance(ledger, dict) and ledger.get("error"):
        ledger_state = "not recorded (" + str(ledger["error"]) + ")"
        log("budget push ledger not recorded: " + str(ledger["error"]))
    else:
        ledger_state = "recorded"

    sent = [e for _, e in plan if e["action"] in ("create", "update")]
    return {
        "venue": venue,
        "venue_id": venue_id,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "updated": len([e for e in sent if e["action"] == "update" and e["ok"]]),
        "created": len([e for e in sent if e["action"] == "create" and e["ok"]]),
        "unchanged": counts["unchanged"],
        "skipped_zero": skipped_zero,
        "failed": failed,
        "days": [e for _, e in plan],
        "ledger": ledger_state,
        "note": (
            "amounts include GST (Loaded's basis); a day with a Loaded row was "
            "updated in place, a day without one was created at the venue's "
            "midnight after it"
            + (
                "; " + str(len(failed)) + " day(s) failed — see failed"
                if failed
                else ""
            )
        ),
    }
