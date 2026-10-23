"""Loaded Legacy — a TEMPORARY second connector to Loaded.

Norm's ``loadedhub`` connector (OAuth) reaches almost everything. A few calls
live only where the token Loaded's own login hands out is accepted and the
OAuth token isn't: Loaded's older API (``https://loadedhub.com/wapi``) —
recipes, stocktake lists, POS-item links — and the stock API's tenders.
Loaded won't register Norm as an app, so this connector logs in the way
Loaded's web app does.

Until 19 Oct 2026 Norm reached these through the Cook Brothers App (Orbit),
which holds one Loaded login for every venue. Loaded Legacy took them over
(owner: "replace the tools in the cook brothers app that we're using as
passthroughs"; docs/plans/loaded-legacy-takeover.md): the ``record_recipe``
tool, the recipe editor's saves, ``get_stocktakes``'s list, the supplier
tenders page and menu engineering's product → recipe links.

It is an ordinary connector (owner, 19 Oct 2026: "it should be the same as any
other connector"):

- its spec row (``SPEC``, written by ``scripts/sync_loaded_legacy_spec.py``)
  shows in Settings → Connector specs: the calls above as ``engine_only``
  endpoints, never shown to an LLM, and one tool, ``record_recipe``
  (config/consolidators/record_recipe.py);
- it is connected per venue (Settings → Venues, or Settings → Connections)
  with a Loaded username and password, the password stored encrypted like
  every typed secret (db/encrypted.py);
- one Loaded login reaches every company that person belongs to, and Loaded's
  web app picks one per request with the ``x-loaded-company-id`` header. So on
  connect Norm lists the login's companies (``/api/user-session``, the call
  Loaded's web app starts with) and an admin picks the venue's; it is kept in
  this connection's own config, like any connector's account id — nothing is
  taken from the ``loadedhub`` connection (owner, 19 Oct 2026: keep the two
  separate). Proved live 19 Oct: one login, three companies' headers, three
  different answers; a recipe read naming the wrong company answers 404;
- it authenticates with ``auth_type`` "password_login" (services/password_login):
  ``POST https://loadedhub.com/api/Token``, ``grant_type=password`` as
  ``client_id=mercury`` — the only client Norm can use (``loadedapi`` isn't
  registered, ``onboarding`` needs a secret; tested 19 Oct 2026). The token
  lasts 14 days in production (24 hours in Loaded's test environment); Norm
  logs in only when it needs to, because a new login replaces that person's
  own Loaded session (one refresh token per person per app — tested in
  Loaded's test environment).

This file holds the spec and :func:`call`, which the pages' own code (the
supplier tenders page, menu engineering) uses to make one of its calls.

REMOVAL, once Loaded's API covers these calls: each endpoint is replaced by
an OAuth one where it is used — ``record_recipe`` (config/consolidators),
``get_stocktakes`` (its list), ``routers/supplier_tenders.py``,
``services/menu_engineering.py``; ``services/recipe_save.py`` runs
``record_recipe``. Then ``sync_loaded_legacy_spec.py --remove`` (the spec row
and the App listing), delete this file, the sync scripts and
``tests/test_loaded_legacy.py``; the venues' connection rows (with their
encrypted passwords) go with
``DELETE FROM connector_configs WHERE connector_name = 'loaded_legacy'``.
Nothing goes back to the Cook Brothers App. ``password_login`` stays — it is a
general connector type.
"""

from __future__ import annotations

import copy
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CONNECTOR = "loaded_legacy"
DISPLAY_NAME = "Loaded Legacy"
#: The App and component that list this connection (Settings → Connections).
APP_SLUG = "recipes-and-costing"
COMPONENT = "recipe_editor"
#: The one tool (config/consolidators/record_recipe.py).
RECORD_RECIPE = "record_recipe"

SAVE_RECIPE_VERSION = "save_recipe_version"
CREATE_RECIPE_VERSION = "create_recipe_version"
GET_RECIPE = "get_recipe"
FIND_RECIPES = "find_recipes"
GET_RECIPE_VERSION = "get_recipe_version"
LIST_STOCKTAKES = "list_stocktakes"
LIST_STOCK_GROUPS = "list_stock_groups"
LIST_POS_ITEM_LINKS = "list_pos_item_links"
LIST_TENDERS = "list_tenders"
GET_TENDER = "get_tender"
# The Catalogue page's writes (manage_stock_item / manage_supplier, 23 Oct
# 2026; docs/plans/stock-catalogue-page.md) — each proven on Loaded's test
# company first.
PATCH_STOCK_ITEM = "patch_stock_item"
FIND_PURCHASE_ORDER_LINES = "find_purchase_order_lines"
UPDATE_POS_ITEM_LINK = "update_pos_item_link"
FIND_STOCK_UNITS = "find_stock_units"
UPDATE_STOCK_UNIT = "update_stock_unit"
MERGE_SUPPLIERS = "merge_suppliers"
REVIEW_TENDER = "review_tender"
UPDATE_TENDER = "update_tender"

_WAPI = "//loadedhub.com/wapi/r"
_TENDERS = "//api.loadedhub.com/1.0/stock/internal/tenders"


def _endpoint(
    action: str,
    description: str,
    path: str,
    *,
    method: str = "GET",
    fields: dict | None = None,
    optional: tuple = (),
    body: str | None = None,
) -> dict:
    """One engine-only Loaded call, every one naming the venue's company."""
    fields = fields or {}
    row = {
        "action": action,
        "description": description,
        "method": method,
        "path_template": path,
        "headers": {
            "Accept": "application/json",
            "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
        },
        "required_fields": [k for k in fields if k not in optional],
        "optional_fields": list(optional),
        "field_mapping": {k: k for k in fields},
        "field_descriptions": fields,
        "success_status_codes": [200, 201, 204],
        "response_ref_path": "",
        "timeout_seconds": 60,
        "read_only": method == "GET",
        "engine_only": True,
    }
    if body:
        row["headers"]["Content-Type"] = "application/json"
        row["request_body_template"] = body
    return row


#: The connector spec row (config DB ``connector_specs``). The tool's
#: function_code is config/consolidators/record_recipe.py, attached by the
#: sync script.
SPEC = {
    "connector_name": CONNECTOR,
    "display_name": DISPLAY_NAME,
    "category": "stock",
    "execution_mode": "template",
    "auth_type": "password_login",
    "auth_config": {
        "token_url": "https://loadedhub.com/api/Token",
        "client_id": "mercury",
        "username_field": "username",
        "password_field": "password",
        "extra_form": {"loginRememberMe": "false"},
        "accounts": {
            "url": "https://loadedhub.com/api/user-session",
            "list": "companies",
            "id": "id",
            "name": "name",
            "config_key": "x_loaded_company_id",
            "label": "Loaded company",
        },
    },
    "base_url_template": "https://",
    "credential_fields": [
        {"key": "username", "label": "Loaded username (email)", "secret": False},
        {"key": "password", "label": "Loaded password", "secret": True},
    ],
    "api_documentation": (
        "TEMPORARY. The Loaded calls Norm's OAuth token can't make: recipe "
        "writes and reads on Loaded's older API (/wapi), stocktake lists, "
        "POS-item → recipe links, and the stock API's tenders. Connected per "
        "venue with a Loaded login and the venue's Loaded company (Settings → "
        "Venues); Norm logs in as Loaded's web app only when it needs to, which "
        "can make that person log in to Loaded's website again once. Loaded "
        "refuses saves of recipes shared with other companies. Remove when "
        "Loaded's API covers these calls (app/connectors/loaded_legacy.py)."
    ),
    "endpoints": [
        _endpoint(
            SAVE_RECIPE_VERSION,
            "Save one recipe version (lines, yield) with its recipe (name, notes) "
            "— the whole version, as Loaded's web app sends it.",
            _WAPI + "/StockRecipeVersions/{{ version_id }}"
            "{% if valid_until %}?validUntil={{ valid_until | urlencode }}{% endif %}",
            method="PUT",
            fields={
                "version_id": "The recipe version's id",
                "valid_until": "The next version's validFrom, when this isn't the latest",
                "body": "The version in Loaded's web-API shape, with its recipe",
            },
            optional=("valid_until",),
            body="{{ body | tojson }}",
        ),
        _endpoint(
            CREATE_RECIPE_VERSION,
            "Create a recipe with its first version (Loaded's web app's create).",
            _WAPI + "/StockRecipeVersions",
            method="POST",
            fields={"body": "The first version, with its recipe, web-API shape"},
            body="{{ body | tojson }}",
        ),
        _endpoint(
            GET_RECIPE,
            "One recipe: name, notes, images, stocktake flag, its versions. "
            "404 when the venue's company doesn't have it.",
            _WAPI + "/StockRecipes/{{ recipe_id }}",
            fields={"recipe_id": "The recipe's id"},
        ),
        _endpoint(
            FIND_RECIPES,
            "Recipes matching an OData filter (id, name, datestampCreated) — a "
            "create answers 204 with no ids, so the new recipe is found by name.",
            _WAPI + "/StockRecipes?$filter={{ filter | urlencode }}",
            fields={
                "filter": "OData filter, e.g. Name eq 'X' and DatestampDeleted eq null"
            },
        ),
        _endpoint(
            GET_RECIPE_VERSION,
            "One recipe version with its lines (quantities in base units).",
            _WAPI + "/StockRecipeVersions/{{ version_id }}",
            fields={"version_id": "The version's id"},
        ),
        _endpoint(
            LIST_STOCKTAKES,
            "Stocktakes matching an OData filter, newest first (id, title, "
            "templateId, datestampCompleted, pending).",
            _WAPI + "/Stocktakes?$filter={{ filter | urlencode }}"
            "&$orderby=DateStampCreated%20desc{% if top %}&$top={{ top }}{% endif %}",
            fields={
                "filter": "OData filter, e.g. Pending eq true",
                "top": "At most this many (newest first)",
            },
            optional=("top",),
        ),
        _endpoint(
            LIST_STOCK_GROUPS,
            "The stock super-groups (Food, Beverage …) — the whole-category "
            "stocktake templates.",
            _WAPI + "/StockItemSuperGroups",
        ),
        _endpoint(
            LIST_POS_ITEM_LINKS,
            "Every POS item's link to a recipe or stock item (posIdentifier = the "
            "POS item's id).",
            _WAPI + "/StockPositemLinks",
        ),
        _endpoint(LIST_TENDERS, "The venue's supplier tenders.", _TENDERS),
        _endpoint(
            GET_TENDER,
            "One tender with its lines.",
            _TENDERS + "/{{ tender_id }}",
            fields={"tender_id": "The tender's id"},
        ),
        _endpoint(
            REVIEW_TENDER,
            "Loaded's tender review: what was bought against the tender's prices.",
            _TENDERS + "/{{ tender_id }}/review?startTime={{ start_time | urlencode }}"
            "&endTime={{ end_time | urlencode }}"
            "&excludeCreditNotes={{ 'true' if exclude_credit_notes else 'false' }}",
            fields={
                "tender_id": "The tender's id",
                "start_time": "Window start (ISO)",
                "end_time": "Window end (ISO)",
                "exclude_credit_notes": "Leave credit notes out",
            },
            optional=("exclude_credit_notes",),
        ),
        _endpoint(
            PATCH_STOCK_ITEM,
            "Change fields of one stock item (JSON Patch, Loaded's names: "
            "DatestampRemoved retires / restores it, CountingUnitId …) — what "
            "Loaded's own item screen sends. Nothing else on the item is touched.",
            _WAPI + "/StockItems/{{ item_id }}",
            method="PATCH",
            fields={
                "item_id": "The stock item's id",
                "patch": 'JSON Patch, e.g. [{"op": "replace", "path": '
                '"DatestampRemoved", "value": "<iso>"}]',
            },
            body="{{ patch | tojson }}",
        ),
        _endpoint(
            FIND_PURCHASE_ORDER_LINES,
            "Purchase-order lines matching an OData filter (StockItemId eq <id>), "
            "each with its order's id, number, supplier and received/deleted dates.",
            _WAPI + "/StockPurchaseOrderLines?$filter={{ filter | urlencode }}",
            fields={"filter": "OData filter, e.g. StockItemId eq <id>"},
        ),
        _endpoint(
            UPDATE_POS_ITEM_LINK,
            "Change a POS item's link in place (its stock item, unit, quantity) — "
            "the link as list_pos_item_links reads it, edited.",
            _WAPI + "/StockPositemLinks/{{ link_id }}",
            method="PUT",
            fields={"link_id": "The link's id", "link": "The whole link"},
            body="{{ link | tojson }}",
        ),
        _endpoint(
            FIND_STOCK_UNITS,
            "Units matching an OData filter (Id eq <id>) — the record a unit "
            "change sends back.",
            _WAPI + "/StockUnits?$filter={{ filter | urlencode }}",
            fields={"filter": "OData filter, e.g. Id eq <id>"},
        ),
        _endpoint(
            UPDATE_STOCK_UNIT,
            "Store a unit: name, unitType, ratio and datestampDeleted (set = "
            "deleted, null = restored). Loaded refuses its standard units.",
            _WAPI + "/StockUnits/{{ unit_id }}",
            method="PUT",
            fields={"unit_id": "The unit's id", "unit": "The whole unit record"},
            body="{{ unit | tojson }}",
        ),
        _endpoint(
            MERGE_SUPPLIERS,
            "Loaded's own supplier merge: moves the source's item variants, item "
            "defaults, orders, invoices and statements to the destination, then "
            "retires the source. Aliases and tenders stay behind.",
            "//loadedhub.com/api/stock/suppliers/Merge?sourceId={{ source_id }}"
            "&destId={{ dest_id }}",
            method="PUT",
            fields={
                "source_id": "The supplier merged away",
                "dest_id": "The supplier kept",
            },
        ),
        _endpoint(
            UPDATE_TENDER,
            "Replace a tender: the whole document, lines included (a line left "
            "out is removed).",
            _TENDERS + "/{{ tender_id }}",
            method="PUT",
            fields={"tender_id": "The tender's id", "tender": "The whole tender"},
            body="{{ tender | tojson }}",
        ),
    ],
    "tools": [
        {
            "action": RECORD_RECIPE,
            "method": "POST",
            "read_only": False,
            "effect": "write",
            "approval": {
                "default": "ask",
                "allow_auto": True,
                "label": "save recipes in Loaded",
            },
            "description": (
                "Create a NEW recipe in Loaded, or write a change to an existing "
                "one directly. To edit or create a recipe with the user, use "
                "edit_recipe or draft_recipe instead — they open the recipe card "
                "and the user saves. Ingredients may be given by name: Norm "
                "matches stock items, sub-recipes and units, and stops (writing "
                "nothing) on any line it isn't sure of, naming the candidates. "
                "When editing an existing recipe's ingredients you MUST pass "
                "mode: 'merge' changes or adds the lines you send and keeps the "
                "rest; 'replace' makes your list the whole recipe. Allergens are "
                "not stored in Loaded. This is a write — human-approved."
            ),
            "required_fields": [],
            "optional_fields": [
                "recipe_id",
                "version_id",
                "name",
                "notes",
                "yield_quantity",
                "yield_unit",
                "is_counted_in_stocktake",
                "mode",
                "ingredients",
            ],
            "field_descriptions": {
                "recipe_id": "Recipe to edit. Omit to create a new recipe.",
                "version_id": "Version to edit. Omit and the current one is used.",
                "name": "Recipe name (required when creating)",
                "notes": "Method / notes",
                "yield_quantity": "Yield in display units of yield_unit",
                "yield_unit": "Yield unit name or unit id (required when creating)",
                "is_counted_in_stocktake": "Whether the recipe is counted in stocktakes",
                "mode": (
                    "How to apply `ingredients`: 'merge' (change/add the lines you "
                    "send, keep the rest) or 'replace' (your list becomes the whole "
                    "recipe). Required when editing an existing recipe's ingredients."
                ),
                "ingredients": "Ingredient lines",
            },
            "field_schema": {
                "yield_quantity": {"type": "number"},
                "is_counted_in_stocktake": {"type": "boolean"},
                "mode": {"type": "string", "enum": ["replace", "merge"]},
                "ingredients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["item", "recipe"],
                                "description": "'item' for a stock item, 'recipe' for a sub-recipe",
                            },
                            "name": {
                                "type": "string",
                                "description": "Stock item or sub-recipe name (matched for you when ref_id is omitted)",
                            },
                            "ref_id": {
                                "type": "string",
                                "description": "Stock item id or sub-recipe id, if you already know it",
                            },
                            "line_id": {
                                "type": "string",
                                "description": "Existing line id, when editing a specific line",
                            },
                            "quantity": {
                                "type": "number",
                                "description": "Quantity in display units of `unit`",
                            },
                            "unit": {
                                "type": "string",
                                "description": "Unit name or unit id for the quantity",
                            },
                            "remove": {
                                "type": "boolean",
                                "description": "In merge mode, drop this line instead of keeping it",
                            },
                        },
                        "required": ["kind", "unit", "quantity"],
                    },
                },
            },
            "consolidator_config": {
                # max_api_calls: scripts/sync_loaded_legacy_spec.py TOOL_CONFIG
                "allowed_write_actions": [
                    f"{CONNECTOR}.{CREATE_RECIPE_VERSION}",
                    f"{CONNECTOR}.{SAVE_RECIPE_VERSION}",
                ],
            },
        }
    ],
}


class LoadedLegacyError(Exception):
    """A failure whose message is safe to show a person."""


def connection(db: Session | None, venue_id: str | None):
    """This venue's Loaded Legacy connection (its own, else an
    organisation-wide one), when it has a login and a company — or None."""

    if db is None or not venue_id:
        return None

    from app.services import connection_scope

    # its own, else its organisation's organisation-wide one — never another
    # organisation's (services/connection_scope.py)
    row = connection_scope.venue_or_org_wide(db, CONNECTOR, venue_id)
    config = (row.config or {}) if row is not None else {}
    return row if config.get("username") and config.get("x_loaded_company_id") else None


def call(action: str, fields: dict, venue_id: str, db: Session, config_db: Session):
    """Make one Loaded Legacy call for this venue (the pages' own code: the
    supplier tenders page, menu engineering). Returns Loaded's answer; raises
    LoadedLegacyError with a message a person can act on."""
    from app.connectors import spec_rows
    from app.connectors.spec_executor import ConnectorAuthError, execute_spec
    from app.db.config_models import ConnectionSpec

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .first()
    )
    op = spec_rows.find(spec, action) if spec is not None else None
    if op is None:
        raise LoadedLegacyError(
            "The Loaded Legacy connector isn't set up (run "
            "scripts/sync_loaded_legacy_spec.py)."
        )
    cfg = connection(db, venue_id)
    if cfg is None:
        raise LoadedLegacyError(
            "This venue isn't connected to Loaded Legacy — connect it in Settings "
            "→ Venues."
        )
    try:
        result, _ = execute_spec(
            spec, op, copy.deepcopy(fields), cfg.config or {}, db, venue_id=venue_id
        )
    except ConnectorAuthError as exc:
        raise LoadedLegacyError(str(exc)) from exc
    if not result.success:
        raise LoadedLegacyError(result.error_message or "Loaded refused the call.")
    return result.response_payload
