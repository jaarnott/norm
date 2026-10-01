# ruff: noqa: F821 — sandbox-injected names (json); not imports.
#
# Canonical function_code for `cook_brothers_app.record_recipe` — create a
# LoadedHub recipe, or write changes to one directly (installed by
# scripts/sync_orbit_recipe_wrapper.py, Sep 2026; review fixes applied by
# scripts/sync_write_tool_safety.py, 1 Oct 2026).
#
# The tool the agent sees. The work is done by Orbit's own MCP function
# `kitchen_record_recipe`, which resolves ingredients by name and writes the
# recipe — but under the Sep 2026 rule (docs/tool-architecture-strategy.md)
# an Orbit function is an API endpoint, and an LLM only ever sees tools. So
# this wrapper is the tool; the endpoint stays a building block that the
# recipe editor (app/services/recipe_save.py) also calls directly.
#
# The row's method is POST, so the tool loop asks for approval before it
# runs — the same gate the raw function had.
#
# Consolidator review (1 Oct 2026) — why it checks before it sends:
#
#   - Neither the tool nor the endpoint carried a schema, so the model saw
#     `ingredients` and `allergens` as strings and sent JSON text; Orbit's
#     schema (additionalProperties false, typed lines) rejects that. The row
#     now carries Orbit's schema, and text that is JSON is still read here.
#   - Orbit's `mode` defaults to REPLACE: an edit that sends only the changed
#     line deletes every other line. An edit with ingredients must now say
#     which it means.
#   - Lines are checked against Orbit's required fields first, so a bad line
#     is a clear refusal, not an Orbit validation dump after approval.
#
# Requires consolidator_config: {"max_api_calls": 1}

# No venue_id: call_api picks the venue's Orbit connection from this tool
# call's own venue, and strips venue keys before the request (the endpoint's
# venue_id — Orbit's own id — never reached Orbit from the agent either).
_FIELDS = (
    "recipe_id",
    "version_id",
    "name",
    "notes",
    "yield_quantity",
    "yield_unit",
    "is_counted_in_stocktake",
    "mode",
    "ingredients",
    "allergens",
)
_MODES = ("replace", "merge")
_KINDS = ("item", "recipe")
_ALLERGEN_STATUSES = ("contains", "no", "cmw", "mc")


def _parse(value):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return value
    return value


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _boolean(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    return None


def _check_ingredients(lines):
    """(clean lines, problems)."""
    if isinstance(lines, dict):
        lines = [lines]
    if not isinstance(lines, list):
        return None, ["ingredients must be a list of lines"]
    clean, problems = [], []
    for i, ln in enumerate(lines):
        label = f"ingredients[{i}]"
        if not isinstance(ln, dict):
            problems.append(f"{label} is not an object")
            continue
        ln = dict(ln)
        if ln.get("kind") not in _KINDS:
            problems.append(f"{label} needs kind 'item' (stock item) or 'recipe'")
        if not ln.get("unit"):
            problems.append(f"{label} needs a unit")
        quantity = _number(ln.get("quantity"))
        if quantity is None:
            problems.append(f"{label} needs a numeric quantity")
        else:
            ln["quantity"] = quantity
        if not (ln.get("name") or ln.get("ref_id") or ln.get("line_id")):
            problems.append(f"{label} needs a name (or ref_id / line_id)")
        if "remove" in ln:
            ln["remove"] = _boolean(ln["remove"])
        clean.append(ln)
    return clean, problems


def _check_allergens(entries):
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return None, ["allergens must be a list of {code, status}"]
    problems = []
    for i, a in enumerate(entries):
        if not isinstance(a, dict) or not a.get("code"):
            problems.append(f"allergens[{i}] needs a code")
        elif a.get("status") not in _ALLERGEN_STATUSES:
            problems.append(
                f"allergens[{i}] status must be one of {', '.join(_ALLERGEN_STATUSES)}"
            )
    return entries, problems


def run(params, call_api, log):
    body = {k: params[k] for k in _FIELDS if params.get(k) not in (None, "")}
    if not body.get("recipe_id") and not body.get("name"):
        return {"error": "A new recipe needs a name (or pass recipe_id to edit one)."}

    problems = []
    if "ingredients" in body:
        body["ingredients"], found = _check_ingredients(_parse(body["ingredients"]))
        problems += found
    if "allergens" in body:
        body["allergens"], found = _check_allergens(_parse(body["allergens"]))
        problems += found
    if "yield_quantity" in body:
        body["yield_quantity"] = _number(body["yield_quantity"])
        if body["yield_quantity"] is None:
            problems.append("yield_quantity must be a number")
    if "is_counted_in_stocktake" in body:
        body["is_counted_in_stocktake"] = _boolean(body["is_counted_in_stocktake"])
        if body["is_counted_in_stocktake"] is None:
            problems.append("is_counted_in_stocktake must be true or false")
    mode = str(body.get("mode") or "").strip().lower()
    if mode and mode not in _MODES:
        problems.append("mode must be 'merge' or 'replace'")
    elif mode:
        body["mode"] = mode
    if body.get("recipe_id") and "ingredients" in body and not mode:
        # Orbit's default is replace: sending only the changed line would
        # delete every other line of the recipe.
        problems.append(
            "editing a recipe's ingredients needs mode: 'merge' changes or adds "
            "the lines you send and keeps the rest; 'replace' makes your list "
            "the whole recipe"
        )
    if problems:
        return {"error": "nothing written — " + "; ".join(problems)}

    log(
        f"record_recipe: {'edit ' + str(body['recipe_id']) if body.get('recipe_id') else 'create ' + repr(body.get('name'))}"
        f", {len(body.get('ingredients') or [])} ingredient line(s)"
        + (f", mode {mode}" if mode else "")
    )
    return call_api("cook_brothers_app", "kitchen_record_recipe", body)
