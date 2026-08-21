# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `loadedhub.get_recipes` consolidator —
# THE recipe lookup (synced by scripts/sync_recipe_consolidator.py).
# Replaces two raw tools on the agent surface: the all-recipes list
# (get_all_recipes — 434 recipes, ~2.5MB, notes carry entire pasted web
# pages of HTML) and the per-recipe detail (get_recipe_details). Both stay
# as engine-only backends this code calls.
#
# The LLM chooses what data it wants: a name query returns slim {id, name}
# matches; a recipe_id returns ONE recipe at `detail` "summary" (the working
# fields, display units, cost) or "full" (the raw Loaded payload).
#
# Token doctrine: summaries carry NAMES, not UUIDs. The only ids kept are
# the handles later calls need: the recipe's id, the CURRENT version_id
# (what a version-scoped write needs), and recipe_id on sub-recipe lines so
# a follow-up fetch is one hop. Quantities are converted to DISPLAY units
# (Loaded stores them in stock units: quantity 0.03 at unitRatio 0.001 is
# 30 mL) — same conversion the recipe editor does.
#
# Cost comes from Loaded's own costs endpoint (get_recipe_costs_raw, an
# engine-only tool this code builds the query for) with priceType=Live —
# real invoice-derived costs; Forecast returns zeros (verified live 21 Aug
# 2026). Loaded computes the recipe cost server-side, so no line math here.
#
# Requires consolidator_config: {"max_api_calls": 6}

_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
}

_NOTES_LIMIT = 1500


def _strip_html(text):
    # Hand-rolled tag/comment scrubber — the sandbox has no imports. Good
    # enough for Loaded's pasted-web-page notes: drop comments and tags,
    # decode the common entities, collapse whitespace.
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "<":
            close = text.find("-->", i) + 3 if text.startswith("<!--", i) else 0
            if close < 3:
                close = text.find(">", i) + 1
            if close < 1:
                break  # unterminated tag — drop the tail
            # Block-ish boundaries become spaces so words don't fuse.
            out.append(" ")
            i = close
        elif ch == "&":
            for ent, rep in _ENTITIES.items():
                if text.startswith(ent, i):
                    out.append(rep)
                    i += len(ent)
                    break
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    cleaned = " ".join("".join(out).split())
    if len(cleaned) > _NOTES_LIMIT:
        cleaned = cleaned[:_NOTES_LIMIT] + " … (notes truncated; detail='full' for raw)"
    return cleaned


def run(params, call_api, log):
    recipe_id = params.get("recipe_id")
    query = str(params.get("query") or "").strip().lower()
    detail = str(params.get("detail") or "summary").strip().lower()
    limit_param = params.get("limit")
    include_cost = bool(params.get("include_cost"))
    venue = params.get("venue")

    def now_string():
        # isoformat, not strftime — strftime lazily imports `time`, which the
        # sandbox's builtins can't satisfy (same lesson as strptime).
        return datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        )

    def fetch_costs(recipe_ids):
        # One batched call: repeated recipeIdTimeStrings=<id>,<now> params,
        # priceType=Live. Returns {recipe_id: {"cost": N, "unit": name}}.
        if not recipe_ids:
            return {}
        ts = now_string()
        q = "&".join(f"recipeIdTimeStrings={rid},{ts}" for rid in recipe_ids)
        q += "&priceType=Live"
        resp = call_api("loadedhub", "get_recipe_costs_raw", {"venue": venue, "q": q})
        if not isinstance(resp, dict) or resp.get("error"):
            log(f"cost lookup failed: {(resp or {}).get('error')}")
            return {}
        costs = {}
        for rid, entries in (resp.get("recipeCosts") or {}).items():
            if isinstance(entries, list) and entries:
                e = entries[0]
                cost = e.get("cost")
                costs[rid] = {
                    "cost": round(cost, 4) if isinstance(cost, (int, float)) else cost,
                    "unit": e.get("unitName"),
                    "price_type": "Live",
                }
        return costs

    def summarize(r):
        cv = r.get("currentVersion") or {}
        lines = []
        raw_lines = [ln for ln in cv.get("lines") or [] if not ln.get("deletedAt")]
        raw_lines.sort(key=lambda ln: ln.get("lineOrder") or 0)
        for ln in raw_lines:
            ratio = ln.get("unitRatio") or 1
            qty = ln.get("quantity")
            if isinstance(qty, (int, float)) and ratio:
                qty = round(qty / ratio, 3)
            entry = {
                "name": ln.get("itemName") or ln.get("recipeName"),
                "kind": "recipe" if ln.get("recipeId") else "item",
                "quantity": qty,
                "unit": ln.get("unitName"),
            }
            if ln.get("recipeId"):
                entry["recipe_id"] = ln["recipeId"]
            lines.append(entry)
        out = {
            "id": r.get("id"),
            "name": r.get("name"),
            "prep_recipe": r.get("prepRecipe"),
            "counted_in_stocktake": r.get("isCountedInStocktake"),
            "version_id": cv.get("id"),
            "yield": {"quantity": cv.get("yieldQuantity"), "unit": cv.get("yieldUnitName")},
            "lines": lines,
            "versions_count": len(r.get("versions") or []),
        }
        notes = _strip_html(r.get("notes") or "")
        if notes:
            out["notes"] = notes
        return out

    def one_recipe(rid):
        r = call_api("loadedhub", "get_recipe_details", {"venue": venue, "recipe_id": rid})
        if not isinstance(r, dict) or r.get("error"):
            return None, {"error": (r or {}).get("error") or f"recipe {rid} not found"}
        return r, None

    if recipe_id:
        r, err = one_recipe(recipe_id)
        if err:
            return err
        costs = fetch_costs([recipe_id])
        if detail == "full":
            out = {"recipe": r, "detail": "full"}
        else:
            out = {"recipe": summarize(r), "detail": "summary"}
        if costs.get(recipe_id):
            out["cost"] = costs[recipe_id]
        return out

    rows = call_api("loadedhub", "get_all_recipes", {"venue": venue})
    if not isinstance(rows, list):
        return {"error": (rows or {}).get("error") or "recipe list unavailable"}
    rows = [r for r in rows if not r.get("deletedAt")]
    if query:
        rows = [r for r in rows if query in str(r.get("name") or "").lower()]
    total = len(rows)
    # A query defaults to the top 25; no query is the list-them-all path
    # (slim rows are cheap) unless the caller caps it explicitly.
    limit = int(limit_param) if limit_param else (25 if query else total)
    rows = rows[:limit]
    matches = [{"id": r.get("id"), "name": r.get("name")} for r in rows]
    out = {"matches": matches, "total_matches": total, "shown": len(matches)}

    if include_cost and matches:
        capped = matches[:50]
        if len(matches) > 50:
            out["note"] = "cost decoration capped at the first 50 matches"
        costs = fetch_costs([m["id"] for m in capped])
        for m in capped:
            c = costs.get(m["id"])
            if c:
                m["cost"] = c["cost"]
                m["cost_unit"] = c["unit"]

    if len(rows) == 1 and query:
        # An unambiguous name hit: save the model a round trip.
        r, err = one_recipe(rows[0].get("id"))
        if not err:
            out["recipe"] = r if detail == "full" else summarize(r)
            out["detail"] = "full" if detail == "full" else "summary"
            costs = fetch_costs([rows[0].get("id")])
            if costs.get(rows[0].get("id")):
                out["cost"] = costs[rows[0].get("id")]
    elif not query:
        out["note"] = (
            "this is the full recipe list — pass query (name substring) or "
            "recipe_id instead of scanning it"
        )
    return out
