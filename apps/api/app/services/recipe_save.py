"""Save a recipe to Loaded — the recipe editor's and the claude.ai recipe
card's writer.

Loaded's only recipe-write API is its older ``/wapi`` host, which Norm's OAuth
token can't reach. The writes go through the Loaded Legacy connector
(app/connectors/loaded_legacy.py), and the write itself is its
``record_recipe`` tool (config/consolidators/record_recipe.py): this module
runs that tool, so the editor, the claude.ai card and the agent share one
write path. Until 19 Oct 2026 this went through the Cook Brothers App's
``kitchen_record_recipe`` (docs/plans/loaded-legacy-takeover.md).

Quantities from the editor are DISPLAY units of each line's ``unit_id`` (the
RecipeEditor converts Loaded's base quantities on load, ``qty / unitRatio``).
The tool stores base units, so each line goes with its unit's ratio, read
here once from the venue's units — with the ids and ratios settled, the tool
needs no name matching.

Two front doors call ``save_recipe``: the web route (routers/recipe_editor)
and the MCP app tool (``norm__save_recipe``).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CONNECTOR = "loaded_legacy"
SAVE_TOOL = "record_recipe"


class RecipeSaveError(Exception):
    """Recoverable failure saving a recipe; message goes back to the caller."""


def _units(venue_id: str, db: Session, config_db: Session) -> dict[str, dict]:
    """The venue's units by id (the recipe editor's own read)."""
    from app.services.component_api import execute_component_action

    out = execute_component_action(
        "recipe_editor", "list_units", {}, venue_id, db, config_db
    )
    data = out.get("data") if isinstance(out, dict) else out
    return {
        str(u.get("id")): u
        for u in (data if isinstance(data, list) else [])
        if isinstance(u, dict) and u.get("id")
    }


def _unit(units: dict, unit_id: object, label: str) -> dict:
    u = units.get(str(unit_id or ""))
    try:
        ratio = float(u.get("ratio")) if u else 0.0
    except (TypeError, ValueError):
        ratio = 0.0
    if not ratio:
        raise RecipeSaveError(f"{label} has a unit that isn't one of this venue's.")
    return {"id": u["id"], "name": u.get("name"), "ratio": ratio}


def tool_params(recipe: dict, units: dict) -> dict:
    """The editor's recipe as ``record_recipe``'s input: a create when there is
    no ``recipe_id``, else an edit; ``lines`` replace the version's lines."""
    is_create = bool(recipe.get("create")) or not recipe.get("recipe_id")
    params: dict = {}
    keys = ("name", "notes", "is_counted_in_stocktake", "yield_quantity")
    if not is_create:
        keys = ("recipe_id", "version_id", *keys)
    for k in keys:
        if recipe.get(k) is not None:
            params[k] = recipe[k]
    if recipe.get("yield_unit_id"):
        yu = _unit(units, recipe["yield_unit_id"], "The yield")
        params["yield_unit"] = yu["id"]
        params["yield_unit_name"] = yu["name"]
        params["yield_unit_ratio"] = yu["ratio"]
    lines = recipe.get("lines")
    if lines is not None:
        params["mode"] = "replace"
        params["ingredients"] = []
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            label = f"'{(ln.get('name') or 'A line').strip()}'"
            if not ln.get("ref_id"):
                raise RecipeSaveError(f"{label} has no stock item or recipe.")
            u = _unit(units, ln.get("unit_id"), label)
            params["ingredients"].append(
                {
                    "kind": ln.get("kind") or "item",
                    "ref_id": ln["ref_id"],
                    "name": (ln.get("name") or "").strip(),
                    "unit": u["id"],
                    "unit_name": u["name"],
                    "unit_ratio": u["ratio"],
                    "quantity": ln.get("quantity"),
                }
            )
    return params


def save_recipe(venue_id: str, recipe: dict, db: Session, config_db: Session) -> dict:
    """Save a recipe to Loaded — update or create.

    ``recipe`` carries what the editor built: optional ``name``/``notes``/
    ``is_counted_in_stocktake``/``yield_quantity``/``yield_unit_id``, and
    ``lines`` (each ``{kind, ref_id, unit_id, quantity}`` in DISPLAY units — a
    full replacement of the version's lines).

    * **Update** — ``recipe_id`` + ``version_id`` present: writes that version.
    * **Create** — ``recipe_id`` omitted (or ``create: true``): makes the recipe
      and its first version and returns the new ``recipe_id``/``version_id``.
      A create needs a ``name``, a yield unit and its lines.
    """
    from app.connectors.tool_executor import execute_connector_tool

    if not isinstance(recipe, dict):
        raise RecipeSaveError("recipe must be an object.")
    is_create = bool(recipe.get("create")) or not recipe.get("recipe_id")
    if is_create:
        if not str(recipe.get("name") or "").strip():
            raise RecipeSaveError("A new recipe needs a name.")
    elif not recipe.get("version_id"):
        raise RecipeSaveError("An update must include a version_id (from the load).")
    lines = recipe.get("lines")
    if lines is not None and (not isinstance(lines, list) or len(lines) > 500):
        raise RecipeSaveError("lines must be a list of at most 500 entries.")

    units = (
        _units(venue_id, db, config_db)
        if (lines or recipe.get("yield_unit_id"))
        else {}
    )
    params = tool_params(recipe, units)
    logger.info(
        "recipe_save",
        extra={
            "venue_id": venue_id,
            "mode": "create" if is_create else "update",
            "recipe_id": recipe.get("recipe_id"),
            "lines": len(lines or []),
        },
    )
    result = execute_connector_tool(
        CONNECTOR,
        SAVE_TOOL,
        params,
        db,
        config_db,
        venue_id=venue_id,
        strict_venue=True,
    )
    data = result.payload if isinstance(result.payload, dict) else {}
    error = result.error or data.get("error")
    if not result.success or error:
        raise RecipeSaveError(str(error or "Loaded rejected the save."))
    return {
        "saved": True,
        "created": bool(data.get("created")),
        "recipe_id": data.get("recipe_id") or recipe.get("recipe_id"),
        "version_id": data.get("version_id") or recipe.get("version_id"),
        "detail": {
            "via": CONNECTOR,
            **({"note": data["note"]} if data.get("note") else {}),
        },
    }
