# ruff: noqa: F821 — sandbox-injected names (datetime); not imports.
#
# Canonical function_code for `loadedhub.get_stocktakes` — THE stocktake tool
# (installed by scripts/sync_last_raw_tools.py, Sep 2026). Two views:
#
#   list       completed (or pending) counts for the venue — id, template,
#              completed date — optionally for one template and a period.
#   variance   Loaded's stocktake report between two counts OF THE SAME
#              TEMPLATE, reduced to what a manager asks: the totals, the items
#              with the biggest shortfalls and surpluses by value, and the
#              items that could not be checked because they have no POS link.
#
# It replaces two tools on the agent menu. `stock_find_stocktakes` (Orbit's
# list — Orbit returns Loaded's own stocktake ids) and
# `generate_stocktake_report` (Loaded's report: 274–566 raw lines, which the
# agent paged through with 7+ search_tool_result calls to answer "biggest
# variances"). Both stay callable here.
#
# Pairing is done HERE, never by the caller: a variance only means something
# between two counts of the same template, and Loaded reports on a mixed pair
# without complaint — La Zeppa's 23/06 All Beverage -> 24/06 All Food "report"
# (Sep 2026) had 548 items, none counted at both ends, COGS at 116x sales and a
# -$45k "variance". Ad-hoc counts (template id all zeros) cover different items
# each time and are never paired.
#
# Variance, per item: closing − (opening + received − expected usage), times
# unit cost. Positive = more stock counted than expected, negative = a
# shortfall. Summed over every line it reproduces Loaded's own totalVariance
# exactly (287.7278 on La Zeppa All Food 11/08 -> 17/08). Items with no POS
# link have expected usage 0, so their "variance" is simply their usage — they
# are reported separately as usage, not ranked as variance.
#
# Requires consolidator_config: {"max_api_calls": 4}

_ZERO = "00000000-0000-0000-0000-000000000000"
_VIEWS = ("list", "variance")


def _lower(s):
    return str(s or "").strip().lower()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _ts(s):
    """Aware datetime for an ISO string (Orbit mixes +12:00 and +00:00)."""
    if not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def _err(result, key):
    if isinstance(result, dict) and result.get("error") and key not in result:
        return str(result["error"])
    return None


def _orbit_data(result):
    """Orbit's MCP reply is {"success", "data": {...}}; tolerate the bare dict."""
    if not isinstance(result, dict):
        return None, "Orbit returned nothing readable"
    if result.get("success") is False:
        return None, str(
            result.get("error") or result.get("message") or "Orbit call failed"
        )
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if not isinstance(data.get("stocktakes"), list):
        return None, _err(result, "stocktakes") or "Orbit returned no stocktake list"
    return data, None


def _template_names(data):
    """id -> title. Orbit lists the whole-category templates; titles of every
    other template are recovered from its counts ("11/07/2026 - DAILY
    STOCKTAKE - KITCHEN" -> "DAILY STOCKTAKE - KITCHEN")."""
    names = {}
    for t in data.get("templates") or []:
        if isinstance(t, dict) and t.get("id") and not t.get("datestampDeleted"):
            names[str(t["id"])] = t.get("title") or ""
    for s in data.get("stocktakes") or []:
        tid = str(s.get("templateId") or "")
        if tid and tid != _ZERO and tid not in names:
            title = str(s.get("title") or "")
            names[tid] = title.split(" - ", 1)[1] if " - " in title else title
    return names


def _match_template(word, names):
    """(template_id, None) or (None, message). Exact beats "All <word>" beats
    a unique partial — "Food" must never quietly become "Staff Food"."""
    w = _lower(word)
    if not w:
        return None, None
    if w in ("ad-hoc", "adhoc", "ad hoc"):
        return _ZERO, None
    for tid in names:
        if _lower(tid) == w:
            return tid, None
    exact = [tid for tid, t in names.items() if _lower(t) == w]
    if len(exact) == 1:
        return exact[0], None
    prefixed = [
        tid for tid, t in names.items() if _lower(t) in ("all " + w, "all " + w + "s")
    ]
    if len(prefixed) == 1:
        return prefixed[0], None
    partial = [tid for tid, t in names.items() if w in _lower(t)]
    if len(partial) == 1:
        return partial[0], None
    if partial:
        return None, (
            f"'{word}' matches more than one template: "
            + ", ".join(sorted(names[p] for p in partial))
        )
    return None, f"No stocktake template '{word}'. Templates: " + ", ".join(
        sorted(t for t in names.values() if t)
    )


def _row(s, names):
    tid = str(s.get("templateId") or "")
    return {
        "id": s.get("id"),
        "title": s.get("title"),
        "template_id": tid,
        "template": "Ad-hoc count" if tid in ("", _ZERO) else names.get(tid) or tid,
        "completed": s.get("datestampCompleted"),
        "pending": bool(s.get("pending")),
        "_ts": _ts(s.get("datestampCompleted")),
    }


def _public(r):
    return {k: v for k, v in r.items() if not k.startswith("_") and k != "template_id"}


def _resolve_period(params, call_api):
    phrase = str(params.get("period") or "").strip()
    if not phrase:
        return None, None
    args = {"query": phrase}
    if params.get("venue_id"):
        args["venue_id"] = params["venue_id"]
    resolved = call_api("norm", "resolve_dates", args)
    window = resolved.get("window") if isinstance(resolved, dict) else None
    if not isinstance(window, dict) and isinstance(resolved, dict):
        data = resolved.get("data")
        window = data.get("window") if isinstance(data, dict) else None
    if not isinstance(window, dict) or not window.get("start"):
        return None, f"could not resolve '{phrase}' to dates"
    return window, None


# ------------------------------------------------------------- variance ----


def _unit_name(u):
    if isinstance(u, dict):
        return u.get("name")
    return u


def _variance_report(report, opening, closing, params):
    lines = report.get("lines") if isinstance(report, dict) else None
    if not isinstance(lines, list):
        return {"error": "Loaded's stocktake report came back without lines"}
    group_f = _lower(params.get("group"))
    query = _lower(params.get("query"))
    top = int(params.get("top") or 15)
    sort_by = _lower(params.get("sort_by")) or "value"

    items = []
    counted_both = only_opening = only_closing = 0
    group = None
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        # A group's name rides ONLY on its header row (no item); the item rows
        # beneath it carry groupHeader null — all 264 in La Zeppa's Food report.
        if not ln.get("stockItemName"):
            group = ln.get("groupHeader") or group
            continue
        o = _num(ln.get("startQty"))
        rcv = _num(ln.get("qtyReceived"))
        c = _num(ln.get("endQty"))
        exp = _num(ln.get("expectedUsed"))
        cost = _num(ln.get("unitCost"))
        if o and c:
            counted_both += 1
        elif o:
            only_opening += 1
        elif c:
            only_closing += 1
        used = o + rcv - c
        var_qty = c - (o + rcv - exp)
        items.append(
            {
                "item": ln.get("stockItemName"),
                "group": ln.get("groupHeader") or group,
                "unit": _unit_name(ln.get("unit")),
                "linked": bool(ln.get("hasPosLinks")),
                "opening": round(o, 3),
                "received": round(rcv, 3),
                "closing": round(c, 3),
                "expected_use": round(exp, 3),
                "actual_use": round(used, 3),
                "variance_qty": round(var_qty, 3),
                "variance_value": round(var_qty * cost, 2),
                "usage_value": round(used * cost, 2),
                "_value": var_qty * cost,
            }
        )

    linked = [i for i in items if i["linked"]]
    unlinked = [i for i in items if not i["linked"] and abs(i["actual_use"]) > 0.0005]
    shortfall = sum(i["_value"] for i in linked if i["_value"] < 0)
    surplus = sum(i["_value"] for i in linked if i["_value"] > 0)

    ranked = [i for i in linked if abs(i["_value"]) >= 0.005]
    if group_f:
        ranked = [i for i in ranked if group_f in _lower(i["group"])]
        unlinked = [i for i in unlinked if group_f in _lower(i["group"])]
    if query:
        ranked = [i for i in ranked if query in _lower(i["item"])]
        unlinked = [i for i in unlinked if query in _lower(i["item"])]
    if sort_by == "qty":
        ranked.sort(key=lambda i: -abs(i["variance_qty"]))
    else:
        ranked.sort(key=lambda i: -abs(i["_value"]))
    shown = ranked[:top]
    rest = ranked[top:]
    unlinked.sort(key=lambda i: -abs(i["usage_value"]))

    var_keys = (
        "item",
        "group",
        "unit",
        "opening",
        "received",
        "closing",
        "expected_use",
        "actual_use",
        "variance_qty",
        "variance_value",
    )
    days = None
    if opening.get("_ts") and closing.get("_ts"):
        days = round((closing["_ts"] - opening["_ts"]).total_seconds() / 86400, 1)
    total_var = report.get("totalVariance")
    if total_var is None:
        total_var = sum(i["_value"] for i in items)

    out = {
        "view": "variance",
        "template": closing.get("template"),
        "opening": _public(opening),
        "closing": _public(closing),
        "days": days,
        "totals": {
            "sales": report.get("totalSales"),
            "sales_ex_tax": report.get("totalSalesEx"),
            "opening_value": round(_num(report.get("openingValue")), 2),
            "purchases": round(_num(report.get("totalPurchases")), 2),
            "closing_value": round(_num(report.get("closingValue")), 2),
            "cost_of_goods": round(_num(report.get("totalCostOfGoods")), 2),
            "cogs_pct": round(_num(report.get("totalCostOfGoodsPerc")) * 100, 1),
            "total_variance": round(_num(total_var), 2),
            "shortfall_value": round(shortfall, 2),
            "surplus_value": round(surplus, 2),
        },
        "coverage": {
            "items": len(items),
            "counted_both_ends": counted_both,
            "only_in_opening": only_opening,
            "only_in_closing": only_closing,
            "not_pos_linked": sum(1 for i in items if not i["linked"]),
        },
        "variances": [{k: i[k] for k in var_keys} for i in shown],
        "sign": "variance = closing − (opening + received − expected use); negative = stock short",
    }
    if rest:
        out["others"] = {
            "count": len(rest),
            "variance_value": round(sum(i["_value"] for i in rest), 2),
        }
    if unlinked:
        out["not_pos_linked"] = {
            "note": "no POS link, so no expected use — this is usage, not variance",
            "count": len(unlinked),
            "usage_value": round(sum(i["usage_value"] for i in unlinked), 2),
            "top": [
                {
                    k: i[k]
                    for k in ("item", "group", "unit", "actual_use", "usage_value")
                }
                for i in unlinked[:10]
            ],
        }
    counted = counted_both + only_opening + only_closing
    if counted and counted_both / counted < 0.3:
        out["warning"] = (
            f"Only {counted_both} of {counted} counted items were counted at both "
            "ends — these two counts may not cover the same stock."
        )
    return out


# ------------------------------------------------------------------ run ----


def _stub(sid):
    return {
        "id": sid,
        "title": None,
        "template": None,
        "template_id": None,
        "completed": None,
        "pending": False,
        "_ts": None,
    }


def _choose_pair(rows, tid, tname, window):
    same = sorted(
        [r for r in rows if r["template_id"] == tid and not r["pending"] and r["_ts"]],
        key=lambda r: r["_ts"],
    )
    if window:
        start = _ts(window.get("start"))
        end = _ts(window.get("end"))
        upto_end = [r for r in same if end is None or r["_ts"] <= end]
        before_start = [r for r in same if start is not None and r["_ts"] <= start]
        closing = upto_end[-1] if upto_end else None
        opening = before_start[-1] if before_start else None
        if opening is None and closing is not None:
            inside = [r for r in upto_end if r is not closing]
            opening = inside[0] if inside else None
        if opening is closing:
            opening = None
    else:
        closing = same[-1] if same else None
        opening = same[-2] if len(same) > 1 else None
    if opening and closing:
        return opening, closing, None
    found = (
        ", ".join(f"{r['title']} ({str(r['completed'])[:10]})" for r in same[-5:])
        or "none"
    )
    return (
        None,
        None,
        (
            f"Need two completed '{tname}' counts"
            + (" around that period" if window else "")
            + f" for a variance; found: {found}."
        ),
    )


def run(params, call_api, log):
    venue = params.get("venue")
    opening_id = params.get("opening_id")
    closing_id = params.get("closing_id")
    template_word = params.get("template") or params.get("template_id")
    view = _lower(params.get("view")) or (
        "variance" if (opening_id or closing_id or template_word) else "list"
    )
    if view not in _VIEWS:
        return {"error": f"view must be one of {', '.join(_VIEWS)}"}
    if view == "variance" and bool(opening_id) != bool(closing_id):
        return {
            "error": "pass both opening_id and closing_id, or neither (then give a template)"
        }
    if view == "variance" and not (opening_id or template_word):
        return {
            "error": (
                "variance needs a template ('Food', 'Beverage', a template name) or "
                "two stocktake ids — call view 'list' to see what exists"
            )
        }

    window, werr = _resolve_period(params, call_api)
    if werr:
        return {"error": werr}

    status = "completed"
    if view == "list":
        status = _lower(params.get("status")) or "completed"
    find = {"venue": venue, "status": status, "include_templates": True, "limit": 200}
    if window:
        start = _ts(window.get("start"))
        end = _ts(window.get("end"))
        # A variance over a period opens on the last count BEFORE the period —
        # reach back far enough to find it.
        back = 120 if view == "variance" else 0
        if start:
            find["from"] = (start - datetime.timedelta(days=back)).date().isoformat()
        if end:
            find["to"] = end.date().isoformat()
    listing = call_api("cook_brothers_app", "stock_find_stocktakes", find)
    data, derr = _orbit_data(listing)
    if derr:
        return {"error": f"could not list stocktakes: {derr}"}
    names = _template_names(data)
    rows = [
        _row(s, names)
        for s in data["stocktakes"]
        if isinstance(s, dict) and s.get("id")
    ]

    tid = None
    if template_word:
        tid, terr = _match_template(template_word, names)
        if terr:
            return {"error": terr}

    if view == "list":
        shown = [r for r in rows if tid is None or r["template_id"] == tid]
        shown.sort(
            key=lambda r: (not r["pending"], -(r["_ts"].timestamp() if r["_ts"] else 0))
        )
        limit = int(params.get("limit") or 20)
        by_template = {}
        for r in shown:
            if not r["pending"]:
                by_template[r["template"]] = by_template.get(r["template"], 0) + 1
        return {
            "view": "list",
            "stocktakes": [_public(r) for r in shown[:limit]],
            "total": len(shown),
            "completed_by_template": by_template,
            "note": (
                "for a variance, call view 'variance' with a template — ad-hoc "
                "counts are never paired"
            ),
        }

    adhoc = (
        "Ad-hoc counts cover different items each time and can't be paired for a "
        "variance."
    )
    if opening_id:
        by_id = {r["id"]: r for r in rows}
        opening = by_id.get(opening_id) or _stub(opening_id)
        closing = by_id.get(closing_id) or _stub(closing_id)
        if opening["template_id"] and closing["template_id"]:
            if opening["template_id"] != closing["template_id"]:
                return {
                    "error": (
                        f"These are counts of different templates ('{opening['template']}' "
                        f"and '{closing['template']}') — a variance between them is "
                        "meaningless. Pick two counts of the same template."
                    )
                }
            if opening["template_id"] == _ZERO:
                return {"error": adhoc}
        if opening["_ts"] and closing["_ts"] and closing["_ts"] < opening["_ts"]:
            opening, closing = closing, opening
    else:
        if tid == _ZERO:
            return {"error": adhoc}
        opening, closing, perr = _choose_pair(
            rows, tid, names.get(tid) or template_word, window
        )
        if perr:
            return {"error": perr}

    log(f"variance {opening.get('title')} -> {closing.get('title')}")
    args = {
        "venue": venue,
        "start_stocktake_id": opening["id"],
        "end_stocktake_id": closing["id"],
    }
    report = call_api("loadedhub", "generate_stocktake_report", args)
    if _err(report, "lines"):
        log(f"report failed ({_err(report, 'lines')}); retrying once")
        report = call_api("loadedhub", "generate_stocktake_report", args)
    if _err(report, "lines"):
        return {
            "error": (
                "LoadedHub could not produce the stocktake report — this is a "
                "LoadedHub failure, not missing counts. LoadedHub said: "
                f"{_err(report, 'lines')}"
            )
        }
    return _variance_report(report, opening, closing, params)
