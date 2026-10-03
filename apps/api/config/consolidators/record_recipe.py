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
# Approval preview (Oct 2026, services/previews.py): before writing, the
# tool shows what changes — for an edit it reads the recipe as it is
# (`kitchen_get_recipe`) and lists each ingredient line before → after, so a
# 'replace' that would drop lines says which. The approved run reads it again
# and stops if the recipe moved in the meantime.
#
# Requires consolidator_config: {"max_api_calls": 2}  (read + write)

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
_STATUS_LABELS = {
    "contains": "Contains",
    "no": "No",
    "cmw": "Can be made without",
    "mc": "May contain",
}


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

    preview(lambda: _card(body, mode, call_api))
    log(
        f"record_recipe: {'edit ' + str(body['recipe_id']) if body.get('recipe_id') else 'create ' + repr(body.get('name'))}"
        f", {len(body.get('ingredients') or [])} ingredient line(s)"
        + (f", mode {mode}" if mode else "")
    )
    return call_api("cook_brothers_app", "kitchen_record_recipe", body)


# ------------------------------------------------------------- preview ----


def _plain_text(markup, limit=120):
    """Recipe notes are HTML; the card shows the words."""
    out, inside = [], False
    for ch in str(markup or ""):
        if ch == "<":
            inside = True
        elif ch == ">":
            inside = False
            out.append(" ")
        elif not inside:
            out.append(ch)
    text = " ".join("".join(out).replace("&amp;", "&").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _amount(quantity, unit):
    if quantity is None:
        return "—"
    if isinstance(quantity, float) and quantity == int(quantity):
        quantity = int(quantity)
    return f"{quantity} {unit or ''}".strip()


def _current(body, call_api):
    """(recipe head, version, allergens) as Orbit has it now, or None."""
    args = {"recipe_id": body["recipe_id"]}
    if body.get("version_id"):
        args["version_id"] = body["version_id"]
    out = call_api("cook_brothers_app", "kitchen_get_recipe", args)
    if not isinstance(out, dict) or out.get("error"):
        return None
    data = out.get("data") if isinstance(out.get("data"), dict) else out
    rec = data.get("recipe") if isinstance(data.get("recipe"), dict) else {}
    head = rec.get("recipe") if isinstance(rec.get("recipe"), dict) else {}
    if not head:
        return None
    return head, rec.get("version") or {}, rec.get("allergens") or []


def _match(line, existing):
    for old in existing:
        if line.get("line_id") and old.get("line_id") == line["line_id"]:
            return old
    for old in existing:
        if line.get("ref_id") and old.get("ref_id") == line["ref_id"]:
            return old
    name = str(line.get("name") or "").strip().lower()
    for old in existing:
        if name and str(old.get("name") or "").strip().lower() == name:
            return old
    return None


def _card(body, mode, call_api):
    """What this call changes, for the approval card — from the recipe as it
    is now when editing one."""
    changes, warnings = [], []
    head, version, allergens = {}, {}, []
    editing = bool(body.get("recipe_id"))
    if editing:
        now = _current(body, call_api)
        if now is None:
            warnings.append(
                "Couldn't read the recipe as it is now — showing the changes as sent."
            )
        else:
            head, version, allergens = now

    def change(field, before, after):
        row = {"field": field, "after": after}
        if editing:
            row["before"] = before
        changes.append(row)

    if body.get("name") and body["name"] != head.get("name"):
        change("Name", head.get("name"), body["name"])
    if "notes" in body and body["notes"] != head.get("notes"):
        change(
            "Method / notes", _plain_text(head.get("notes")), _plain_text(body["notes"])
        )
    if "yield_quantity" in body or "yield_unit" in body:
        before = _amount(version.get("yield_quantity"), version.get("yield_unit_name"))
        after = _amount(
            body.get("yield_quantity", version.get("yield_quantity")),
            body.get("yield_unit") or version.get("yield_unit_name"),
        )
        if before != after:
            change("Yield", before, after)
    if "is_counted_in_stocktake" in body:
        was = head.get("is_counted_in_stocktake")
        if was != body["is_counted_in_stocktake"]:
            change(
                "Counted in stocktake",
                None if was is None else ("Yes" if was else "No"),
                "Yes" if body["is_counted_in_stocktake"] else "No",
            )

    existing = [ln for ln in (version.get("lines") or []) if isinstance(ln, dict)]
    matched = []
    for line in body.get("ingredients") or []:
        old = _match(line, existing) if existing else None
        name = line.get("name") or (old or {}).get("name") or line.get("ref_id")
        before = _amount(old.get("quantity"), old.get("unit_name")) if old else None
        if old:
            matched.append(old)
        if line.get("remove"):
            change(f"Ingredient · {name}", before, "Removed")
            continue
        after = _amount(line.get("quantity"), line.get("unit"))
        if old is None:
            changes.append({"field": f"Ingredient · {name}", "after": after + " (new)"})
        elif before != after:
            change(f"Ingredient · {name}", before, after)
    if mode == "replace" and "ingredients" in body:
        dropped = [old for old in existing if not any(old is m for m in matched)]
        for old in dropped:
            change(
                f"Ingredient · {old.get('name')}",
                _amount(old.get("quantity"), old.get("unit_name")),
                "Removed",
            )
        if dropped:
            warnings.append(
                f"Replace: the recipe becomes exactly the {len(body['ingredients'])} "
                f"line(s) sent, so {len(dropped)} current line(s) are removed."
            )

    statuses = {
        str(a.get("code")): a.get("status") for a in allergens if isinstance(a, dict)
    }
    for a in body.get("allergens") or []:
        code = str(a.get("code"))
        if statuses.get(code) != a.get("status"):
            change(
                f"Allergen · {code.replace('_', ' ').title()}",
                _STATUS_LABELS.get(statuses.get(code), statuses.get(code)),
                _STATUS_LABELS.get(a.get("status"), a.get("status")),
            )

    return {
        "title": "Change a recipe" if editing else "Create a recipe",
        "target": body.get("name") or head.get("name") or body.get("recipe_id"),
        "changes": changes,
        "warnings": warnings,
    }
