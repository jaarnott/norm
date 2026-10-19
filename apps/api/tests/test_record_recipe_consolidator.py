"""record_recipe — the one recipe write, on Loaded Legacy (19 Oct 2026).

Until then it wrapped the Cook Brothers App's ``kitchen_record_recipe``; now it
writes through Loaded's older API itself (docs/plans/loaded-legacy-takeover.md)
and the recipe editor saves through it too. Exec'd under the REAL sandbox
namespace against the canonical file; the fake below answers like Loaded's
/wapi did when read on 19 Oct 2026 (recipe: versions without lines, images as
JSON text; version: lines with stockItemId/recipeId, quantities in base units)
and like ``draft_recipe`` (Norm's matcher) answers.

The bodies these build were sent to Loaded's test environment on 19 Oct 2026
(an edit merged, restored by replace, a create read back and removed).
"""

import copy
import json

from tests import consolidator_harness as harness

CODE = harness.source("record_recipe")

G = {"id": "u-g", "name": "Gram", "ratio": 0.001}
KG = {"id": "u-kg", "name": "Kilo", "ratio": 1.0}
EACH = {"id": "u-each", "name": "Each", "ratio": 1.0}


def _line(lid, ref, name, unit, qty_display, recipe=False):
    return {
        "id": lid,
        "versionId": "v-1",
        "stockItemId": None if recipe else ref,
        "stockItemName": None if recipe else name,
        "recipeId": ref if recipe else None,
        "recipeName": name if recipe else None,
        "unitId": unit["id"],
        "unitName": unit["name"],
        "unitRatio": unit["ratio"],
        "quantity": qty_display * unit["ratio"],
        "currentPrice": 1.23,
    }


RECIPE = {
    "id": "r-1",
    "name": "COMPONENT - AIOLI",
    "notes": '<p>Whisk</p><img alt="x" src="data:image/png;base64,AAAA">',
    "imagesJson": json.dumps([{"name": "aioli.jpg", "src": "data:..."}]),
    "mainImageJson": None,
    "stocktake": True,
    "isShared": False,
    "currentVersion": {"id": "v-1", "versionNo": 1},
    "versions": [
        {"id": "v-2", "versionNo": 2, "validFrom": "2027-01-01T00:00:00Z"},
        {"id": "v-1", "versionNo": 1, "validFrom": None},
    ],
}
VERSION = {
    "id": "v-1",
    "recipeId": "r-1",
    "validFrom": None,
    "validUntil": None,
    "yeildQuantity": 2.0,
    "yeildUnitId": "u-kg",
    "yeildUnitName": "Kilo",
    "yeildUnitRatio": 1.0,
    "lines": [
        _line("l-egg", "it-egg", "EGG YOLK", EACH, 12),
        _line("l-oil", "it-oil", "OIL CANOLA", G, 900),
        _line("l-salt", "it-salt", "SALT", G, 10),
    ],
}


class Loaded:
    """Loaded Legacy's endpoints and draft_recipe, scripted."""

    def __init__(self, recipe=RECIPE, version=VERSION, matches=None, keep=True):
        self.recipe = copy.deepcopy(recipe)
        self.version = copy.deepcopy(version)
        self.matches = matches or {}
        self.keep = keep
        self.calls, self.logs = [], []
        self.duplicate = None
        self.created = []

    def log(self, msg):
        self.logs.append(msg)

    def call_api(self, connector, action, params=None):
        p = copy.deepcopy(params or {})
        self.calls.append((connector, action, p))
        if action == "draft_recipe":
            assert connector == "loadedhub"
            [r] = p["recipes"]
            lines = []
            for ln in r["lines"]:
                m = self.matches.get(ln.get("name"))
                if m is None:
                    lines.append(
                        {
                            "match": {
                                "status": "unresolved",
                                "reasons": ["matches 2 sub-recipes"],
                                "candidates": [
                                    {"name": "POLENTA A"},
                                    {"name": "POLENTA B"},
                                ],
                            }
                        }
                    )
                else:
                    lines.append({**m, "match": {"status": "matched", "reasons": []}})
            unit = {"kg": KG, "each": EACH}.get(str(r.get("yield_unit")).lower())
            return {
                "recipes": [
                    {
                        "lines": lines,
                        "yield_unit_id": unit and unit["id"],
                        "yield_unit_name": unit and unit["name"],
                        "yield_unit_ratio": unit and unit["ratio"],
                        "duplicate_of": self.duplicate,
                    }
                ]
            }
        assert connector == "loaded_legacy", (connector, action)
        if action == "get_recipe":
            if p["recipe_id"] == "r-1":
                return copy.deepcopy(self.recipe)
            if p["recipe_id"] == "r-new":
                return {"id": "r-new", "currentVersion": {"id": "v-new"}}
            return {"error": "API error 404"}
        if action == "get_recipe_version":
            return copy.deepcopy(self.version)
        if action == "save_recipe_version":
            if self.keep:
                self.version["lines"] = copy.deepcopy(p["body"]["lines"])
            return None
        if action == "create_recipe_version":
            self.created.append(p["body"])
            return None  # Loaded answers 204, no ids
        if action == "find_recipes":
            return [
                {
                    "id": "r-old",
                    "name": "x",
                    "datestampCreated": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "r-new",
                    "name": "x",
                    "datestampCreated": "2026-10-19T10:00:00Z",
                },
            ]
        raise AssertionError(f"unexpected {connector}.{action}")

    def sent(self, action):
        return [p for _, a, p in self.calls if a == action]

    def actions(self):
        return [a for _, a, _ in self.calls]


def run(api, **params):
    return harness.run(CODE, api, venue="La Zeppa", **params)


def settled(ref, name, unit, qty, kind="item"):
    """A line as the recipe editor sends it: ids and the unit's ratio."""
    return {
        "kind": kind,
        "ref_id": ref,
        "name": name,
        "unit": unit["id"],
        "unit_name": unit["name"],
        "unit_ratio": unit["ratio"],
        "quantity": qty,
    }


class TestEdits:
    def test_an_edit_by_name_is_matched_by_norm_and_merged_into_the_version(self):
        api = Loaded(
            matches={
                "Garlic": {
                    "kind": "item",
                    "ref_id": "it-garlic",
                    "name": "GARLIC PEELED",
                    "unit_id": "u-g",
                    "unit_name": "Gram",
                    "unit_ratio": 0.001,
                    "quantity": 30,
                }
            }
        )
        out = run(
            api,
            recipe_id="r-1",
            mode="merge",
            ingredients=[
                {"kind": "item", "name": "Garlic", "quantity": 30, "unit": "g"}
            ],
        )
        assert out["saved"] and out["recipe_id"] == "r-1" and out["lines"] == 4
        assert api.actions() == [
            "draft_recipe",
            "get_recipe",
            "get_recipe_version",
            "save_recipe_version",
            "get_recipe_version",  # Loaded kept it?
        ]
        [save] = api.sent("save_recipe_version")
        # the next version's start bounds this one, as Loaded's web app sends it
        assert (
            save["version_id"] == "v-1"
            and save["valid_until"] == "2027-01-01T00:00:00Z"
        )
        body = save["body"]
        egg, oil, salt, garlic = body["lines"]
        assert (egg["id"], oil["id"], salt["id"]) == ("l-egg", "l-oil", "l-salt")
        assert (
            "id" not in garlic
            and garlic["stockItemId"] == garlic["itemId"] == "it-garlic"
        )
        assert garlic["quantity"] == 0.03 and garlic["unitRatio"] == 0.001  # base units
        assert [ln["lineOrder"] for ln in body["lines"]] == [0, 1, 2, 3]
        # the recipe rides along as Loaded's web app's save() sends it
        rec = body["recipe"]
        assert "versions" not in rec and "currentVersion" not in rec
        assert json.loads(rec["imagesJson"]) == [{"name": "aioli.jpg"}]
        assert rec["notes"] == '<p>Whisk</p><img alt="x">'

    def test_merge_changes_and_removes_only_what_is_sent(self):
        api = Loaded()
        run(
            api,
            recipe_id="r-1",
            mode="merge",
            ingredients=[
                settled("it-oil", "OIL CANOLA", KG, 1.2),
                {"name": "SALT", "remove": True},
            ],
        )
        lines = api.sent("save_recipe_version")[0]["body"]["lines"]
        assert [(ln["id"], ln["quantity"]) for ln in lines] == [
            ("l-egg", 12.0),
            ("l-oil", 1.2),  # kept its Loaded id, now 1.2 kg
        ]
        assert "draft_recipe" not in api.actions()  # the ids were given

    def test_replace_makes_the_list_the_whole_recipe(self):
        api = Loaded()
        run(
            api,
            recipe_id="r-1",
            mode="replace",
            ingredients=[settled("it-egg", "EGG YOLK", EACH, 6)],
        )
        [line] = api.sent("save_recipe_version")[0]["body"]["lines"]
        assert line["id"] == "l-egg" and line["quantity"] == 6.0

    def test_a_line_norm_isnt_sure_of_writes_nothing_and_names_the_candidates(self):
        api = Loaded()
        out = run(
            api,
            recipe_id="r-1",
            mode="merge",
            ingredients=[
                {"kind": "recipe", "name": "POLENTA", "quantity": 1, "unit": "kg"}
            ],
        )
        assert out["error"].startswith("nothing written")
        assert "POLENTA A" in out["error"] and "POLENTA B" in out["error"]
        assert api.actions() == ["draft_recipe"]

    def test_a_recipe_the_venues_company_cant_see_is_explained(self):
        """A read naming the wrong company answers 404 (verified live)."""
        api = Loaded()
        out = run(api, recipe_id="r-elsewhere", name="X")
        assert "Settings → Venues" in out["error"]
        assert "save_recipe_version" not in api.actions()

    def test_a_change_loaded_didnt_keep_is_an_error(self):
        api = Loaded(keep=False)
        out = run(
            api,
            recipe_id="r-1",
            mode="merge",
            ingredients=[settled("it-oil", "OIL CANOLA", KG, 1.2)],
        )
        assert "doesn't show the change" in out["error"]

    def test_yield_name_and_notes_change_without_touching_the_lines(self):
        api = Loaded()
        out = run(
            api,
            recipe_id="r-1",
            name="COMPONENT - GARLIC AIOLI",
            yield_quantity=3,
            yield_unit="kg",
            is_counted_in_stocktake="false",
        )
        body = api.sent("save_recipe_version")[0]["body"]
        assert body["lines"] == VERSION["lines"]
        assert body["yeildQuantity"] == body["yieldQuantity"] == 3.0
        assert body["recipe"]["name"] == "COMPONENT - GARLIC AIOLI"
        assert body["recipe"]["stocktake"] is False
        assert out["saved"] and "get_recipe_version" in api.actions()


class TestCreates:
    def test_a_create_sends_the_first_version_and_finds_its_ids(self):
        api = Loaded()
        out = run(
            api,
            name="COMPONENT - CHEF'S DUKKAH",
            yield_quantity=500,
            yield_unit="u-g",
            yield_unit_ratio=0.001,
            yield_unit_name="Gram",
            notes="<p>Toast</p>",
            ingredients=[settled("it-salt", "SALT", G, 20)],
        )
        assert out["created"] and (out["recipe_id"], out["version_id"]) == (
            "r-new",
            "v-new",
        )
        [body] = api.created
        assert body["versionNo"] == 1 and body["yeildQuantity"] == 0.5
        assert body["recipe"] == {
            "imagesJson": "[]",
            "name": "COMPONENT - CHEF'S DUKKAH",
            "notes": "<p>Toast</p>",
            "stocktake": False,
        }
        assert (
            body["lines"][0]["quantity"] == 0.02 and "versionId" not in body["lines"][0]
        )
        # Loaded answers a create with 204 and no ids: found by name, quote doubled
        [find] = api.sent("find_recipes")
        assert find["filter"] == (
            "Name eq 'COMPONENT - CHEF''S DUKKAH' and DatestampDeleted eq null"
        )

    def test_a_create_named_like_an_existing_recipe_is_refused(self):
        """2–3 Oct 2026: the Cook Brothers App made several recipes twice."""
        api = Loaded(
            matches={
                "Salt": {
                    "kind": "item",
                    "ref_id": "it-salt",
                    "unit_id": "u-g",
                    "unit_ratio": 0.001,
                    "quantity": 5,
                }
            }
        )
        api.duplicate = {"id": "r-1", "name": "component - aioli"}
        out = run(
            api,
            name="COMPONENT - AIOLI",
            yield_quantity=1,
            yield_unit="kg",
            ingredients=[{"kind": "item", "name": "Salt", "quantity": 5, "unit": "g"}],
        )
        assert "already exists (recipe_id r-1)" in out["error"]
        assert api.created == []


class TestRefusedBeforeAnyCall:
    def test_a_create_without_a_name_or_yield_or_lines(self):
        api = Loaded()
        assert "needs a name" in run(api, yield_unit="kg")["error"]
        out = run(api, name="New")
        assert "yield_unit" in out["error"] and "ingredient lines" in out["error"]
        assert api.calls == []

    def test_an_ingredient_edit_must_say_merge_or_replace(self):
        """Sending one changed line must never delete the rest."""
        api = Loaded()
        line = {"kind": "item", "name": "Salt", "quantity": 5, "unit": "g"}
        out = run(api, recipe_id="r-1", ingredients=[line])
        assert "needs mode" in out["error"] and api.calls == []

    def test_bad_lines_and_json_text(self):
        """With no schema the model sent arrays as JSON text."""
        api = Loaded()
        out = run(
            api,
            name="Broken",
            yield_unit="kg",
            ingredients='[{"name": "Salt", "quantity": "a pinch"}]',
        )
        for why in (
            "ingredients[0] needs kind",
            "ingredients[0] needs a unit",
            "ingredients[0] needs a numeric quantity",
        ):
            assert why in out["error"]
        assert api.calls == []


def test_allergens_are_not_written_and_the_result_says_so():
    api = Loaded()
    out = run(
        api,
        recipe_id="r-1",
        notes="<p>Whisk well</p>",
        allergens=[{"code": "egg", "status": "contains"}],
    )
    assert out["saved"] and "Allergens weren't saved" in out["note"]
    assert "allergens" not in json.dumps(api.sent("save_recipe_version"))


def test_a_shared_recipe_refusal_says_why():
    api = Loaded(recipe={**RECIPE, "isShared": True})

    def refuse(connector, action, params=None):
        if action == "save_recipe_version":
            return {"error": "API error 500"}
        return Loaded.call_api(api, connector, action, params)

    api.call_api = refuse
    out = run(api, recipe_id="r-1", notes="<p>x</p>")
    assert "shared with other companies" in out["error"]
