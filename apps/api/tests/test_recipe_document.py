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
    def test_converts_base_to_display_units_and_surfaces_deleted(self):
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
                        "stockUnitName": "Kilo",
                        "stockUnitRatio": 1,
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
                    # item deleted in Loaded, but the line stays on the recipe
                    {
                        "id": "l3",
                        "itemId": "x",
                        "itemName": "X",
                        "unitId": "kg",
                        "unitName": "Kilo",
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
        # All three lines are kept — the deleted-item line stays on the recipe.
        assert len(d["lines"]) == 3
        carrot = d["lines"][0]
        assert carrot["kind"] == "item"
        assert carrot["ref_id"] == "carrot"
        assert carrot["quantity"] == 500  # 0.5 / 0.001
        assert carrot["stock_unit_name"] == "Kilo"  # for the Stock Unit / Stock Cost columns
        assert carrot["stock_unit_ratio"] == 1
        assert carrot["item_deleted"] is False
        sub = d["lines"][1]
        assert sub["kind"] == "recipe"
        assert sub["ref_id"] == "sub"
        # The deleted-item line is kept as a normal line, just flagged.
        deleted = d["lines"][2]
        assert deleted["name"] == "X"
        assert deleted["quantity"] == 9  # 9 / 1
        assert deleted["item_deleted"] is True
        # With no explicit versions[], the current version is the only one, marked
        # current, and its lines mirror the editable draft (display units).
        assert len(d["versions"]) == 1
        assert d["versions"][0]["current"] is True
        assert d["versions"][0]["label"] == "Current"
        assert len(d["versions"][0]["lines"]) == 3

    def test_builds_read_only_version_history(self):
        payload = {
            "id": "R2",
            "name": "Sauce",
            "currentVersion": {
                "id": "V2",
                "yieldQuantity": 1,
                "yieldUnitRatio": 1,
                "yieldUnitName": "Litre",
                "lines": [
                    {"id": "c1", "itemId": "i", "itemName": "Cream", "unitId": "l", "unitName": "Litre", "unitRatio": 1, "quantity": 1}
                ],
            },
            "versions": [
                {
                    "id": "V1",
                    "validFrom": "2024-01-05T00:00:00+00:00",
                    "yieldQuantity": 1,
                    "yieldUnitRatio": 1,
                    "yieldUnitName": "Litre",
                    "lines": [
                        {"id": "b1", "itemId": "i", "itemName": "Cream", "unitId": "l", "unitName": "Litre", "unitRatio": 1, "quantity": 0.8}
                    ],
                },
                {
                    "id": "V2",
                    "yieldQuantity": 1,
                    "yieldUnitRatio": 1,
                    "yieldUnitName": "Litre",
                    "lines": [
                        {"id": "c1", "itemId": "i", "itemName": "Cream", "unitId": "l", "unitName": "Litre", "unitRatio": 1, "quantity": 1}
                    ],
                },
            ],
        }
        d = build_recipe_draft(payload)
        assert [v["id"] for v in d["versions"]] == ["V1", "V2"]
        v1, v2 = d["versions"]
        assert v1["current"] is False and v1["label"] == "From 2024-01-05"
        assert v2["current"] is True and v2["label"] == "Current"
        # Each version carries its own display-unit lines.
        assert v1["lines"][0]["quantity"] == 0.8
        assert v2["lines"][0]["quantity"] == 1


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
