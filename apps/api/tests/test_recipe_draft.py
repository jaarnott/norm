"""The recipe drafting path: the agent writes, Norm matches, the card saves.

Built from production thread aad94383 (2–4 Oct 2026), where half of the
agent's recipe saves failed on names and units it resolved itself. Each test
here names the failure it closes.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services import recipe_draft as rd
from app.services.recipe_draft import (
    draft_recipes,
    match_line,
    match_recipe,
    resolve_unit,
    summarise,
)

# ----------------------------------------------------------- fixtures ----

UNITS = [
    {"id": "u-kilo", "name": "Kilo", "ratio": 1.0, "stockUnitType": "Weight"},
    {"id": "u-gram", "name": "Gram", "ratio": 0.001, "stockUnitType": "Weight"},
    {"id": "u-litre", "name": "Litre", "ratio": 1.0, "stockUnitType": "Volume"},
    {"id": "u-ml", "name": "Millilitre", "ratio": 0.001, "stockUnitType": "Volume"},
    {"id": "u-each", "name": "Each", "ratio": 1.0, "stockUnitType": "Count"},
    {"id": "u-serve", "name": "Serving", "ratio": 1.0, "stockUnitType": "Count"},
    {
        "id": "u-dead",
        "name": "Cup",
        "ratio": 1.0,
        "stockUnitType": "Volume",
        "datestampDeleted": "2025-01-01",
    },
]

ITEMS = [
    {"id": "i-lemon", "name": "LEMONS", "unitType": 0, "countingUnitId": "u-kilo"},
    {
        "id": "i-worc",
        "name": "SAUCE WORCESTERSHIRE",
        "unitType": 1,
        "countingUnitId": "u-litre",
    },
    {"id": "i-cream", "name": "CREAM", "unitType": 0, "countingUnitId": "u-kilo"},
    {
        "id": "i-butter-u",
        "name": "BUTTER UNSALTED",
        "unitType": 0,
        "countingUnitId": "u-kilo",
    },
    {
        "id": "i-butter-s",
        "name": "BUTTER SALTED",
        "unitType": 0,
        "countingUnitId": "u-kilo",
    },
    {
        "id": "i-popcorn",
        "name": "POPCORN KERNELS",
        "unitType": 0,
        "countingUnitId": "u-kilo",
    },
    {
        "id": "i-polenta",
        "name": "POLENTA FINE GROUND",
        "unitType": 0,
        "countingUnitId": "u-kilo",
    },
    {"id": "i-egg", "name": "EGGS", "unitType": 2, "countingUnitId": "u-each"},
    {
        "id": "i-gone",
        "name": "LEMON JUICE",
        "unitType": 1,
        "countingUnitId": "u-litre",
        "datestampRemoved": "2024-01-01",
    },
]

RECIPES = [
    {
        "id": "r-polenta-a",
        "name": "COMPONENT - POLENTA",
        "currentVersion": {"yieldUnitId": "u-kilo"},
    },
    {
        "id": "r-polenta-b",
        "name": "COMPONENT - SOFT POLENTA 26",
        "currentVersion": {"yieldUnitId": "u-kilo"},
    },
    # an older, un-prefixed one — the thread's third "POLENTA"
    {
        "id": "r-polenta-c",
        "name": "POLENTA",
        "currentVersion": {"yieldUnitId": "u-kilo"},
    },
    {
        "id": "r-rub",
        "name": "COMPONENT - LAMB DRY RUB",
        "currentVersion": {"yieldUnitId": "u-kilo"},
    },
    {
        "id": "r-tara",
        "name": "COMPONENT - TARAMASALATA",
        "currentVersion": {"yieldUnitId": "u-kilo"},
    },
    {"id": "r-old", "name": "COMPONENT - OLD THING", "deletedAt": "2024-01-01"},
]

CAT = {"items": ITEMS, "recipes": RECIPES, "units": UNITS}


def line(name, qty=1, unit="g", **kw):
    return {"name": name, "quantity": qty, "unit": unit, **kw}


# -------------------------------------------------------------- units ----


class TestUnits:
    def test_abbreviations_find_the_venues_unit(self):
        """'g', 'ml', 'kg' failed as 'Unknown unit' in the thread."""
        assert resolve_unit("g", UNITS)[0]["id"] == "u-gram"
        assert resolve_unit("ml", UNITS)[0]["id"] == "u-ml"
        assert resolve_unit("KG", UNITS)[0]["id"] == "u-kilo"
        assert resolve_unit("each", UNITS)[0]["id"] == "u-each"
        assert resolve_unit("serve", UNITS)[0]["id"] == "u-serve"

    def test_a_unit_of_the_wrong_kind_is_offered_not_used(self):
        """'Millilitre' on CREAM (a weight item) was refused by Loaded."""
        unit, cands = resolve_unit("ml", UNITS, want_type="Weight")
        assert unit is None
        assert [c["id"] for c in cands] == ["u-ml"]

    def test_kitchen_measures_with_no_loaded_unit_stay_unresolved(self):
        assert resolve_unit("cup", UNITS) == (None, [])
        assert resolve_unit("tbsp", UNITS) == (None, [])

    def test_a_deleted_unit_is_never_returned(self):
        assert resolve_unit("cup", rd._live_units(UNITS)) == (None, [])


# -------------------------------------------------------------- lines ----


class TestMatchLine:
    def test_plural_and_case_match_confidently(self):
        out = match_line(line("lemon", 2, "kg"), CAT)
        assert out["match"]["status"] == "matched"
        assert (out["kind"], out["ref_id"], out["name"]) == (
            "item",
            "i-lemon",
            "LEMONS",
        )
        assert (out["quantity"], out["unit_id"], out["unit_name"]) == (
            2.0,
            "u-kilo",
            "Kilo",
        )
        assert out["stock_unit_name"] == "Kilo"

    def test_inverted_word_order_matches(self):
        """'worcestershire sauce' is 'SAUCE WORCESTERSHIRE' in Loaded."""
        out = match_line(line("Worcestershire sauce", 50, "ml"), CAT)
        assert out["match"]["status"] == "matched"
        assert out["ref_id"] == "i-worc"

    def test_two_equally_good_items_are_candidates_not_a_guess(self):
        out = match_line(line("butter", 100), CAT)
        assert out["match"]["status"] == "unresolved"
        assert out["ref_id"] is None
        assert {c["name"] for c in out["match"]["candidates"]} == {
            "BUTTER UNSALTED",
            "BUTTER SALTED",
        }
        assert "not sure" in out["match"]["reasons"][0]

    def test_a_sub_recipe_wins_over_a_stock_item_when_confident(self):
        out = match_line(line("lamb dry rub", 50), CAT)
        assert out["match"]["status"] == "matched"
        assert (out["kind"], out["ref_id"]) == ("recipe", "r-rub")

    def test_an_ambiguous_sub_recipe_name_is_flagged(self):
        """'POLENTA' matched 3 sub-recipes in the thread; the save failed."""
        out = match_line(line("polenta", 300), CAT)
        assert out["match"]["status"] == "unresolved"
        names = {c["name"] for c in out["match"]["candidates"]}
        assert {"COMPONENT - POLENTA", "POLENTA"} <= names
        assert "sub-recipes could be" in out["match"]["reasons"][0]

    def test_kind_item_restricts_to_stock_items(self):
        out = match_line(line("polenta", 300, kind="item"), CAT)
        assert out["match"]["status"] == "matched"
        assert out["ref_id"] == "i-polenta"

    def test_ref_id_is_honoured(self):
        out = match_line(line("whatever", 1, "kg", ref_id="r-polenta-b"), CAT)
        assert (out["kind"], out["ref_id"], out["name"]) == (
            "recipe",
            "r-polenta-b",
            "COMPONENT - SOFT POLENTA 26",
        )

    def test_a_removed_item_never_matches(self):
        out = match_line(line("lemon juice", 50, "ml"), CAT)
        assert out["ref_id"] != "i-gone"

    def test_unit_of_the_wrong_kind_for_the_item_is_flagged(self):
        out = match_line(line("cream", 500, "ml"), CAT)
        assert out["match"]["status"] == "unresolved"
        assert out["ref_id"] == "i-cream"  # the item is still matched
        assert out["unit_id"] is None
        assert "not a weight unit" in out["match"]["reasons"][0]

    def test_no_unit_written_uses_the_items_counting_unit(self):
        out = match_line({"name": "eggs", "quantity": 3}, CAT)
        assert out["match"]["status"] == "matched"
        assert out["unit_id"] == "u-each"

    def test_to_taste_needs_a_number(self):
        out = match_line(line("lemon", "to taste", "g"), CAT)
        assert out["match"]["status"] == "unresolved"
        assert out["ref_id"] == "i-lemon"
        assert "needs a number" in out["match"]["reasons"][0]

    def test_a_fraction_is_a_number(self):
        assert match_line(line("lemon", "1/2", "kg"), CAT)["quantity"] == 0.5

    def test_a_name_from_the_same_batch_says_save_it_first(self):
        out = match_line(
            line("burger sauce", 30, "g"), CAT, batch_names={"burger sauce"}
        )
        assert out["match"]["status"] == "unresolved"
        assert "save that one first" in out["match"]["reasons"][0]

    def test_nothing_matching_says_so(self):
        out = match_line(line("annatto seeds", 5), CAT)
        assert out["match"]["status"] == "unresolved"
        assert out["match"]["candidates"] == []
        assert "no stock item or sub-recipe matches" in out["match"]["reasons"][0]
        assert out["match"]["as_written"] == {
            "name": "annatto seeds",
            "quantity": 5,
            "unit": "g",
        }


# ------------------------------------------------------------ recipes ----


class TestMatchRecipe:
    def test_a_component_draft_is_counted_in_stocktake(self):
        d = match_recipe(
            {
                "name": "COMPONENT - LEMON BUTTER",
                "kind": "component",
                "yield_quantity": "1",
                "yield_unit": "kg",
                "method": "<ol><li>Mix</li></ol>",
                "lines": [line("lemon", 200), line("butter unsalted", 800)],
            },
            CAT,
            "venue-1",
        )
        assert d["recipe_id"] == "" and d["draft_id"]
        assert d["is_counted_in_stocktake"] is True
        assert (d["yield_quantity"], d["yield_unit_id"], d["yield_unit_name"]) == (
            1.0,
            "u-kilo",
            "Kilo",
        )
        assert d["notes"] == "<ol><li>Mix</li></ol>"
        assert d["unresolved_count"] == 0 and d["problems"] == []
        assert d["duplicate_of"] is None
        assert d["venue_id"] == "venue-1"

    def test_a_dish_is_not_counted(self):
        d = match_recipe(
            {
                "name": "DISH - X",
                "kind": "dish",
                "yield_quantity": 1,
                "yield_unit": "serve",
                "lines": [line("eggs", 2, "each")],
            },
            CAT,
            "v",
        )
        assert d["is_counted_in_stocktake"] is False
        assert d["yield_unit_id"] == "u-serve"

    def test_an_existing_recipe_with_the_name_is_flagged(self):
        """Two lamb rubs were created on top of two that already existed."""
        d = match_recipe(
            {
                "name": "Lamb Dry Rub",
                "yield_quantity": 1,
                "yield_unit": "kg",
                "lines": [line("lemon")],
            },
            CAT,
            "v",
        )
        assert d["duplicate_of"] == {"id": "r-rub", "name": "COMPONENT - LAMB DRY RUB"}

    def test_missing_yield_and_lines_are_problems(self):
        d = match_recipe({"name": "X", "yield_unit": "cup"}, CAT, "v")
        assert "needs a yield quantity" in d["problems"]
        assert "no Loaded unit called 'cup' for the yield" in d["problems"]
        assert "no ingredient lines" in d["problems"]

    def test_a_batch_knows_its_own_names_and_summarises(self):
        drafts = draft_recipes(
            [
                {
                    "name": "COMPONENT - BURGER SAUCE",
                    "yield_quantity": 1,
                    "yield_unit": "kg",
                    "lines": [line("lemon", 1, "kg")],
                },
                {
                    "name": "DISH - BURGER",
                    "kind": "dish",
                    "yield_quantity": 1,
                    "yield_unit": "each",
                    "lines": [line("burger sauce", 30, "g"), line("cream", 10, "ml")],
                },
            ],
            CAT,
            "v",
        )
        assert len(drafts) == 2 and drafts[0]["draft_id"] != drafts[1]["draft_id"]
        s = summarise(drafts)
        assert s[0] == {
            "name": "COMPONENT - BURGER SAUCE",
            "type": "component",
            "lines": 1,
            "matched": 1,
            "needs_the_chef": [],
            "problems": [],
            "already_exists": None,
        }
        assert s[1]["matched"] == 0
        whys = {f["line"]: f["why"] for f in s[1]["needs_the_chef"]}
        assert "save that one first" in whys["burger sauce"]
        assert "not a weight unit" in whys["cream"]


# ------------------------------------------------------------ handler ----


class TestDraftRecipeHandler:
    def _run(self, monkeypatch, params):
        from app.agents import internal_tools

        monkeypatch.setattr(rd, "load_catalogue", lambda venue_id, db, cfg: CAT)
        monkeypatch.setattr(
            "app.services.venue_service.resolve_venue_id",
            lambda name, db: "venue-9" if name else None,
        )
        handler = internal_tools.get_handler("loadedhub", "draft_recipe")
        return handler(params, None, None)

    def test_fans_out_one_draft_per_recipe_with_the_venue(self, monkeypatch):
        out = self._run(
            monkeypatch,
            {
                "venue": "La Zeppa",
                "recipes": [
                    {
                        "name": "A",
                        "yield_quantity": 1,
                        "yield_unit": "kg",
                        "lines": [line("lemon")],
                    },
                    {
                        "name": "B",
                        "yield_quantity": 1,
                        "yield_unit": "kg",
                        "lines": [line("eggs", 1, "each")],
                    },
                ],
            },
        )
        assert out["success"] is True
        data = out["data"]
        assert data["venue_id"] == "venue-9"  # tool_loop stamps tc.venue_id from this
        assert [r["name"] for r in data["recipes"]] == ["A", "B"]
        assert all(
            r["draft_id"] and r["venue_id"] == "venue-9" for r in data["recipes"]
        )
        assert [c["name"] for c in data["cards"]] == ["A", "B"]

    def test_json_text_is_read_and_a_single_recipe_is_a_list(self, monkeypatch):
        out = self._run(
            monkeypatch,
            {
                "venue_id": "venue-1",
                "recipes": '{"name": "A", "yield_quantity": 1, "yield_unit": "kg", "lines": []}',
            },
        )
        assert out["success"] is True and len(out["data"]["recipes"]) == 1

    def test_no_venue_is_an_error(self, monkeypatch):
        out = self._run(monkeypatch, {"recipes": [{"name": "A"}]})
        assert out["success"] is False and "venue" in out["error"]

    def test_bad_recipes_is_an_error(self, monkeypatch):
        out = self._run(monkeypatch, {"venue_id": "v", "recipes": "nonsense"})
        assert out["success"] is False and "recipes must be a list" in out["error"]


# ------------------------------------------------------- the save path ----


class TestSaveSendsTheChosenId:
    def test_ref_id_goes_with_the_name(self, monkeypatch):
        """A line pinned to one of three POLENTA sub-recipes must save as that one."""
        from app.services import recipe_save

        seen = {}
        fake_spec = type(
            "Spec",
            (),
            {"tools": [{"action": recipe_save.SAVE_ACTION, "required_fields": []}]},
        )()
        monkeypatch.setattr(
            recipe_save,
            "_cb_context",
            lambda *a, **k: (fake_spec, type("Cfg", (), {"config": {}})()),
        )
        monkeypatch.setattr(recipe_save, "resolve_cb_venue_id", lambda *a, **k: "cb-1")

        def fake_execute_spec(spec, op, fields, creds, db, venue_id=None, **k):
            seen["fields"] = fields
            return type(
                "R", (), {"success": True, "response_payload": {"data": {}}}
            )(), None

        monkeypatch.setattr(
            "app.connectors.spec_executor.execute_spec", fake_execute_spec
        )
        recipe_save.save_recipe(
            "v",
            {
                "name": "DISH - X",
                "lines": [
                    {
                        "kind": "recipe",
                        "ref_id": "r-polenta-b",
                        "name": "COMPONENT - SOFT POLENTA 26",
                        "unit_id": "u-gram",
                        "quantity": 300,
                    },
                    {
                        "kind": "item",
                        "name": "LEMONS",
                        "unit_id": "u-kilo",
                        "quantity": 1,
                    },
                ],
            },
            db=None,
            config_db=None,
        )
        ings = seen["fields"]["ingredients"]
        assert (
            ings[0]["ref_id"] == "r-polenta-b"
            and ings[0]["name"] == "COMPONENT - SOFT POLENTA 26"
        )
        assert "ref_id" not in ings[1]


# ------------------------------------------------- creating a stock item ----


class TestCreateStockItem:
    def test_payload_has_no_supplier_and_a_capital_name(self, monkeypatch):
        from app.services import stock_items

        sent = {}

        class FakeClient:
            def __init__(self, db, cfg, venue_id):
                pass

            def get(self, path):
                return UNITS

            def request(self, method, path, body):
                sent["method"], sent["path"], sent["body"] = method, path, body
                return {"id": "new-item"}

        monkeypatch.setattr(
            "app.services.received_invoice.LoadedInvoiceClient", FakeClient
        )
        out = stock_items.create_stock_item(
            "v", "annatto seeds", "g-spices", "u-gram", None, None
        )
        assert (sent["method"], sent["path"]) == ("POST", "/1.0/stock/internal/items")
        b = sent["body"]
        assert b["name"] == "ANNATTO SEEDS"
        assert (
            b["unitType"] == 0
            and b["countingUnitId"] == "u-kilo"
            and b["orderingUnitId"] == "u-gram"
        )
        assert b["suppliers"] == [] and "defaultSupplierId" not in b
        assert out == {
            "item_id": "new-item",
            "item_name": "ANNATTO SEEDS",
            "unit_id": "u-gram",
            "unit_name": "Gram",
            "unit_ratio": 0.001,
            "counting_unit_name": "Kilo",
            "counting_unit_ratio": 1.0,
        }

    def test_an_unknown_unit_is_refused_before_any_write(self, monkeypatch):
        from app.services import stock_items

        class FakeClient:
            def __init__(self, *a):
                pass

            def get(self, path):
                return UNITS

            def request(self, *a):
                raise AssertionError("must not write")

        monkeypatch.setattr(
            "app.services.received_invoice.LoadedInvoiceClient", FakeClient
        )
        with pytest.raises(ValueError, match="unit not found"):
            stock_items.create_stock_item("v", "X", "g", "u-nope", None, None)


# -------------------------------------------- record_recipe: an edit ----


class TestRecordRecipeEditWithoutVersion:
    CODE = (
        pathlib.Path(__file__).resolve().parent.parent
        / "config"
        / "consolidators"
        / "record_recipe.py"
    ).read_text()

    def _run(self, params, recipe_read):
        from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(self.CODE, ns)
        calls = []

        def call_api(connector, action, body):
            calls.append((action, body))
            if action == "kitchen_get_recipe":
                return recipe_read
            return {"saved": True}

        return ns["run"](params, call_api, lambda *_: None), calls

    def test_the_current_version_is_read_and_used(self):
        """'name and yield_unit_id are required to create a recipe' × 14."""
        read = {
            "data": {
                "recipe": {
                    "recipe": {"id": "abc", "name": "X"},
                    "version": {"version_id": "v-cur", "lines": []},
                }
            }
        }
        out, calls = self._run({"recipe_id": "abc", "notes": "<p>Method</p>"}, read)
        assert out == {"saved": True}
        assert [a for a, _ in calls] == ["kitchen_get_recipe", "kitchen_record_recipe"]
        assert calls[1][1]["version_id"] == "v-cur"

    def test_an_unreadable_recipe_is_refused_without_writing(self):
        out, calls = self._run({"recipe_id": "abc", "notes": "x"}, {"error": "nope"})
        assert "Couldn't read recipe abc" in out["error"]
        assert [a for a, _ in calls] == ["kitchen_get_recipe"]


# ------------------------------------------ a create's id, read back ----


ROWS = [
    {
        "id": "r-old",
        "name": "COMPONENT - LEMON BUTTER",
        "createdAt": "2025-01-01T00:00:00+00:00",
        "versions": [{"id": "v-old", "validTo": None}],
    },
    {
        "id": "r-new",
        "name": "COMPONENT - LEMON BUTTER",
        "createdAt": "2026-10-11T22:00:00+00:00",
        "versions": [
            {"id": "v-1", "validTo": "2026-10-11T22:00:01+00:00"},
            {"id": "v-2", "validTo": None},
        ],
    },
    {
        "id": "r-gone",
        "name": "COMPONENT - LEMON BUTTER",
        "createdAt": "2026-10-12T00:00:00+00:00",
        "deletedAt": "2026-10-12",
        "versions": [],
    },
    {
        "id": "r-other",
        "name": "COMPONENT - LIME BUTTER",
        "createdAt": "2026-10-12T00:00:00+00:00",
        "versions": [],
    },
]


class TestCreatedIdIsReadBack:
    """The CB App's create response carries null ids; the save finds the
    recipe it just made instead of handing the caller nothing."""

    def test_pick_created_takes_the_newest_live_match_and_its_current_version(self):
        from app.services.recipe_save import pick_created

        assert pick_created(ROWS, "component - lemon butter ") == ("r-new", "v-2")
        assert pick_created(ROWS, "nothing") == (None, None)
        assert pick_created(ROWS, "") == (None, None)

    def test_save_resolves_the_id_when_the_cb_app_returns_none(self, monkeypatch):
        from app.services import recipe_save

        fake_spec = type(
            "Spec",
            (),
            {"tools": [{"action": recipe_save.SAVE_ACTION, "required_fields": []}]},
        )()
        monkeypatch.setattr(
            recipe_save,
            "_cb_context",
            lambda *a, **k: (fake_spec, type("Cfg", (), {"config": {}})()),
        )
        monkeypatch.setattr(recipe_save, "resolve_cb_venue_id", lambda *a, **k: "cb-1")
        monkeypatch.setattr(
            "app.connectors.spec_executor.execute_spec",
            lambda *a, **k: (
                type(
                    "R",
                    (),
                    {
                        "success": True,
                        "response_payload": {
                            "data": {
                                "created": True,
                                "recipe_id": None,
                                "version_id": None,
                            }
                        },
                    },
                )(),
                None,
            ),
        )
        monkeypatch.setattr(
            "app.services.component_api.execute_component_action",
            lambda component, action, params, venue_id, db, cfg: {
                "data": ROWS,
                "status_code": 200,
            },
        )
        out = recipe_save.save_recipe(
            "v",
            {"name": "COMPONENT - LEMON BUTTER", "lines": []},
            db=None,
            config_db=None,
        )
        assert (out["created"], out["recipe_id"], out["version_id"]) == (
            True,
            "r-new",
            "v-2",
        )

    def test_record_recipe_fills_the_ids_in_after_a_create(self):
        CODE = (
            pathlib.Path(__file__).resolve().parent.parent
            / "config"
            / "consolidators"
            / "record_recipe.py"
        ).read_text()
        from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        calls = []

        def call_api(connector, action, body):
            calls.append((connector, action))
            if action == "kitchen_record_recipe":
                return {
                    "success": True,
                    "data": {"created": True, "recipe_id": None, "version_id": None},
                }
            if action == "get_all_recipes":
                return ROWS
            raise AssertionError(action)

        out = ns["run"](
            {
                "name": "COMPONENT - LEMON BUTTER",
                "yield_quantity": 1,
                "yield_unit": "Kilo",
                "ingredients": [
                    {"kind": "item", "name": "LEMONS", "quantity": 1, "unit": "Kilo"}
                ],
            },
            call_api,
            lambda *_: None,
        )
        assert [a for _, a in calls] == ["kitchen_record_recipe", "get_all_recipes"]
        assert (out["data"]["recipe_id"], out["data"]["version_id"]) == ("r-new", "v-2")
