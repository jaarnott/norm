# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `cook_brothers_app.record_recipe` — create or
# edit a LoadedHub recipe (installed by scripts/sync_orbit_recipe_wrapper.py,
# Sep 2026).
#
# The tool the agent sees. The work is done by Orbit's own MCP function
# `kitchen_record_recipe`, which resolves ingredients by name and writes the
# recipe — but under the Sep 2026 rule (docs/tool-architecture-strategy.md)
# an Orbit function is an API endpoint, and an LLM only ever sees tools. So
# this wrapper is the tool; the endpoint stays a building block that the
# recipe editor (app/services/recipe_save.py) also calls directly.
#
# It passes the recipe through unchanged and returns Orbit's answer. The row's
# method is POST, so the tool loop asks for approval before it runs — the same
# gate the raw function had.
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


def run(params, call_api, log):
    body = {k: params[k] for k in _FIELDS if params.get(k) not in (None, "")}
    if not body.get("recipe_id") and not body.get("name"):
        return {"error": "A new recipe needs a name (or pass recipe_id to edit one)."}
    log(
        f"record_recipe: {'edit ' + str(body['recipe_id']) if body.get('recipe_id') else 'create ' + repr(body.get('name'))}"
        f", {len(body.get('ingredients') or [])} ingredient line(s)"
    )
    return call_api("cook_brothers_app", "kitchen_record_recipe", body)
