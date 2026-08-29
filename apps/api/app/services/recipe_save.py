"""Save a recipe to LoadedHub — the one place recipe writes happen.

Loaded's only recipe-write API is its legacy ``/wapi`` host, which Norm's own
OAuth token can't authenticate against. So recipe writes are routed through the
Cook Brothers App MCP connector's ``kitchen_record_recipe`` tool (its
stored per-org token DOES reach the legacy host). Recipe *reads* stay direct on
the loadedhub connector — see ``sync_recipe_component_apis.py``.

The write tool wants the CB App's OWN venue id (not Norm's), so we resolve it
from ``list_venues`` on the same connection. Quantities on the wire are DISPLAY
units for each line's ``unit_id`` — the RecipeEditor already converts Loaded's
raw base quantities to display on load (``qty / unitRatio``), so the lines it
sends here pass straight through.

One writer, two front doors: the web route (routers/recipe_editor) and the MCP
app tool (``norm__save_recipe``) both call ``save_recipe``.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CONNECTOR = "cook_brothers_app"
SAVE_ACTION = "kitchen_record_recipe"
LIST_VENUES_ACTION = "list_venues"


class RecipeSaveError(Exception):
    """Recoverable failure saving a recipe; message goes back to the caller."""


def _op(spec, action: str) -> dict:
    for t in spec.tools or []:
        if t.get("action") == action:
            return t
    raise RecipeSaveError(
        f"The Cook Brothers App connector has no '{action}' tool. "
        "Run sync-mcp-tools to discover it."
    )


def _cb_context(venue_id: str, db: Session, config_db: Session):
    """The CB App spec + this venue's credentials, or a clear error."""
    from app.db.config_models import ConnectionSpec
    from app.db.models import Connection

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .first()
    )
    if not spec:
        raise RecipeSaveError("The Cook Brothers App connector is not configured.")

    cfg = (
        db.query(Connection)
        .filter(
            Connection.connector_name == CONNECTOR,
            Connection.enabled == "true",
            Connection.venue_id == venue_id,
        )
        .first()
    )
    if not cfg:
        raise RecipeSaveError(
            "This venue isn't connected to the Cook Brothers App. Connect it in "
            "Settings to save recipes."
        )
    return spec, cfg


def resolve_cb_venue_id(venue_id: str, db: Session, config_db: Session) -> str:
    """The CB App's own venue id for this Norm venue.

    The connection's token grants a set of CB venues; pick the one that matches
    this Norm venue by name, or the only one if it grants exactly one.
    """
    from app.connectors.spec_executor import execute_spec
    from app.db.models import Venue

    spec, cfg = _cb_context(venue_id, db, config_db)
    result, _ = execute_spec(
        spec, _op(spec, LIST_VENUES_ACTION), {}, cfg.config, db, venue_id=venue_id
    )
    if not result.success:
        raise RecipeSaveError(
            f"Couldn't list Cook Brothers App venues: {result.error_message}"
        )
    payload = result.response_payload or {}
    data = payload.get("data") if isinstance(payload, dict) else payload
    venues = data.get("venues") if isinstance(data, dict) else data
    venues = venues if isinstance(venues, list) else []
    if not venues:
        raise RecipeSaveError("The Cook Brothers App connection grants no venues.")
    if len(venues) == 1:
        return venues[0].get("id")

    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    name = (venue.name if venue else "").strip().lower()
    for v in venues:
        vn = str(v.get("name") or "").strip().lower()
        if vn and (vn == name or vn in name or name in vn):
            return v.get("id")
    raise RecipeSaveError(
        "Couldn't match this venue to a Cook Brothers App venue. Reconnect the "
        "Cook Brothers App for the correct venue."
    )


def save_recipe(venue_id: str, recipe: dict, db: Session, config_db: Session) -> dict:
    """Save a recipe to Loaded via the Cook Brothers App — update or create.

    ``recipe`` carries what the editor built: optional ``name``/``notes``/
    ``is_counted_in_stocktake``/``yield_quantity``/``yield_unit_id``, and
    ``lines`` (each ``{kind, ref_id, unit_id, quantity}`` in DISPLAY units — a
    full replacement of the version's lines).

    Two modes:

    * **Update** — ``recipe_id`` + ``version_id`` present: writes the given
      version.
    * **Create** — ``recipe_id`` omitted (or ``create: true``): the CB tool makes
      the recipe and its first version, then writes the lines/yield in the same
      call, and returns the new ``recipe_id``/``version_id``. A create needs a
      ``name``.
    """
    from app.connectors.spec_executor import ConnectorAuthError, execute_spec

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

    spec, cfg = _cb_context(venue_id, db, config_db)
    cb_venue_id = resolve_cb_venue_id(venue_id, db, config_db)

    # The consolidated kitchen_record_recipe (29 Aug 2026, replacing
    # kitchen_loadedhub_update_recipe) has a strict schema, verified live:
    # create = omit recipe_id/version_id (no create flag exists); update needs
    # BOTH ids (the "version_id resolved from recipe_id" in its description is
    # not the actual behaviour); ingredients are {kind, name, quantity, unit}
    # where unit takes a unit NAME or unit id (GUID ok) and name is required;
    # yield_unit likewise takes name-or-id.
    fields = {"venue_id": cb_venue_id}
    passthrough = ("name", "notes", "is_counted_in_stocktake", "yield_quantity")
    if not is_create:
        passthrough = ("recipe_id", "version_id", *passthrough)
    for k in passthrough:
        if recipe.get(k) is not None:
            fields[k] = recipe[k]
    if recipe.get("yield_unit_id") is not None:
        fields["yield_unit"] = recipe["yield_unit_id"]
    if lines is not None:
        fields["ingredients"] = [
            {
                "kind": ln.get("kind") or "item",
                "name": (ln.get("name") or "").strip(),
                "quantity": ln.get("quantity"),
                "unit": ln.get("unit_id") or ln.get("unit_name") or "",
            }
            for ln in lines
            if isinstance(ln, dict)
        ]

    logger.info(
        "recipe_save",
        extra={
            "venue_id": venue_id,
            "mode": "create" if is_create else "update",
            "recipe_id": recipe.get("recipe_id"),
            "lines": len(lines or []),
        },
    )
    # execute_spec enforces the operation's required_fields (recipe_id/version_id
    # in the discovered CB schema) — but a create legitimately omits them. We own
    # validation above, so hand execute_spec an operation with no required gate.
    op = {**_op(spec, SAVE_ACTION), "required_fields": []}
    try:
        result, _ = execute_spec(spec, op, fields, cfg.config, db, venue_id=venue_id)
    except ConnectorAuthError as exc:
        raise RecipeSaveError(str(exc)) from exc
    if not result.success:
        raise RecipeSaveError(result.error_message or "Loaded rejected the save.")

    # A create returns the new ids; surface them so the caller can open the saved
    # recipe. (The CB response otherwise echoes a placeholder, so for an update
    # callers should still re-read rather than trust the body.)
    payload = result.response_payload or {}
    src = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(src, dict):
        src = payload if isinstance(payload, dict) else {}
    new_recipe_id = (
        src.get("recipe_id") or src.get("recipeId") or recipe.get("recipe_id")
    )
    new_version_id = (
        src.get("version_id") or src.get("versionId") or recipe.get("version_id")
    )
    return {
        "saved": True,
        "created": is_create,
        "recipe_id": new_recipe_id,
        "version_id": new_version_id,
        "detail": result.response_payload,
    }
