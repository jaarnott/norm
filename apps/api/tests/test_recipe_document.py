"""Tests for the recipe working-document machinery.

Covers the pure logic behind the recipe editor's agent-editable draft:
- the recipe line ops in ``working_documents._apply_op``,
- ``recipe_document.build_recipe_draft`` (Loaded payload -> display-unit draft),
- ``internal_tools._apply_recipe_changes`` (the edit_recipe merge).
"""

from app.routers.working_documents import _apply_op
from app.services.recipe_document import build_recipe_draft
from app.agents.internal_tools import _apply_recipe_changes


class TestApplyOpRecipe:
    def _doc(self):
        return {
            "recipe_id": "R1",
            "name": "Aioli",
            "notes": "old",
            "lines": [
                {
                    "id": "a",
                    "kind": "item",
                    "ref_id": "salt",
                    "name": "SALT",
                    "quantity": 10,
                },
                {
                    "id": "b",
                    "kind": "item",
                    "ref_id": "oil",
                    "name": "OIL",
                    "quantity": 100,
                },
            ],
        }

    def test_update_recipe_line_by_id(self):
        d = _apply_op(
            self._doc(),
            {"op": "update_recipe_line", "line_id": "a", "fields": {"quantity": 5}},
        )
        assert d["lines"][0]["quantity"] == 5
        assert d["lines"][1]["quantity"] == 100  # untouched

    def test_add_recipe_line_keeps_given_id(self):
        d = _apply_op(
            self._doc(),
            {
                "op": "add_recipe_line",
                "line": {"id": "c", "name": "PEPPER", "quantity": 2},
            },
        )
        assert [ln["name"] for ln in d["lines"]] == ["SALT", "OIL", "PEPPER"]

    def test_add_recipe_line_generates_id_when_missing(self):
        d = _apply_op(
            {"lines": []},
            {"op": "add_recipe_line", "line": {"name": "X", "quantity": 1}},
        )
        assert d["lines"][0].get("id")

    def test_remove_recipe_line(self):
        d = _apply_op(self._doc(), {"op": "remove_recipe_line", "line_id": "b"})
        assert [ln["name"] for ln in d["lines"]] == ["SALT"]

    def test_header_and_notes(self):
        d = _apply_op(
            self._doc(),
            {
                "op": "update_header",
                "fields": {"name": "Garlic Aioli", "yield_quantity": 4},
            },
        )
        d = _apply_op(d, {"op": "update_notes", "value": "Blend well."})
        assert d["name"] == "Garlic Aioli"
        assert d["yield_quantity"] == 4
        assert d["notes"] == "Blend well."


class TestBuildRecipeDraft:
    def test_converts_base_to_display_units_and_drops_deleted(self):
        payload = {
            "id": "R1",
            "name": " Roasted Carrots ",
            "isCountedInStocktake": True,
            "currentVersion": {
                "id": "V1",
                "notes": "roast",
                "yieldQuantity": 4,
                "yieldUnitRatio": 1,
                "yieldUnitId": "each",
                "yieldUnitName": "Portion",
                "lines": [
                    # 500 g stored as base 0.5 (ratio 0.001) -> display 500
                    {
                        "id": "l1",
                        "itemId": "carrot",
                        "itemName": "Carrots",
                        "unitId": "g",
                        "unitName": "Gram",
                        "unitRatio": 0.001,
                        "quantity": 0.5,
                    },
                    # a sub-recipe line
                    {
                        "id": "l2",
                        "recipeId": "sub",
                        "recipeName": "Dukkah",
                        "unitId": "kg",
                        "unitName": "Kilo",
                        "unitRatio": 1,
                        "quantity": 0.02,
                    },
                    # deleted -> dropped
                    {
                        "id": "l3",
                        "itemId": "x",
                        "itemName": "X",
                        "unitRatio": 1,
                        "quantity": 9,
                        "deletedAt": "2021-01-01",
                    },
                ],
            },
        }
        d = build_recipe_draft(payload)
        assert d["recipe_id"] == "R1"
        assert d["name"] == "Roasted Carrots"  # trimmed
        assert d["is_counted_in_stocktake"] is True
        assert d["yield_quantity"] == 4
        assert len(d["lines"]) == 2  # deleted dropped
        carrot = d["lines"][0]
        assert carrot["kind"] == "item"
        assert carrot["ref_id"] == "carrot"
        assert carrot["quantity"] == 500  # 0.5 / 0.001
        sub = d["lines"][1]
        assert sub["kind"] == "recipe"
        assert sub["ref_id"] == "sub"


class TestApplyRecipeChanges:
    def _draft(self):
        return {
            "name": "Aioli",
            "notes": "",
            "yield_quantity": 20,
            "lines": [
                {
                    "id": "a",
                    "kind": "item",
                    "ref_id": "salt",
                    "name": "SALT SEA FLAKEY",
                    "quantity": 60,
                },
                {
                    "id": "b",
                    "kind": "item",
                    "ref_id": "oil",
                    "name": "OIL CANOLA",
                    "quantity": 9,
                },
            ],
        }

    def test_update_line_by_match(self):
        d = self._draft()
        _apply_recipe_changes(
            d, {"lines": {"update": [{"match": "salt", "set": {"quantity": 5}}]}}
        )
        assert d["lines"][0]["quantity"] == 5
        assert d["lines"][1]["quantity"] == 9

    def test_update_line_by_id(self):
        d = self._draft()
        _apply_recipe_changes(
            d, {"lines": {"update": [{"id": "b", "set": {"quantity": 3}}]}}
        )
        assert d["lines"][1]["quantity"] == 3

    def test_scalar_fields(self):
        d = self._draft()
        _apply_recipe_changes(
            d, {"name": "Garlic Aioli", "yield_quantity": 25, "notes": "Blend."}
        )
        assert d["name"] == "Garlic Aioli"
        assert d["yield_quantity"] == 25
        assert d["notes"] == "Blend."

    def test_remove_by_match(self):
        d = self._draft()
        _apply_recipe_changes(d, {"lines": {"remove": [{"match": "oil"}]}})
        assert [ln["name"] for ln in d["lines"]] == ["SALT SEA FLAKEY"]

    def test_add_line_gets_id(self):
        d = self._draft()
        _apply_recipe_changes(d, {"lines": {"add": [{"name": "Cumin", "quantity": 2}]}})
        assert d["lines"][-1]["name"] == "Cumin"
        assert d["lines"][-1].get("id")

    def test_no_match_is_a_noop(self):
        d = self._draft()
        _apply_recipe_changes(
            d, {"lines": {"update": [{"match": "nope", "set": {"quantity": 1}}]}}
        )
        assert [ln["quantity"] for ln in d["lines"]] == [60, 9]
