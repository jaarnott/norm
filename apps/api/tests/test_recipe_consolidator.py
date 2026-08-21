"""The get_recipes consolidator: one recipe read surface.

Exec'd under the REAL sandbox namespace. The facts pinned: the LLM chooses
its data depth (query → slim {id, name} matches; recipe_id → summary or the
raw payload); summaries carry names and display units, never raw ids (except
the handles later calls need: recipe id, current version_id, sub-recipe
line refs); notes HTML is stripped to text; cost rides in from Loaded's own
costs endpoint at priceType=Live and its failure never breaks the read.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE = (_DIR / "get_recipes.py").read_text()

# Field names as the live endpoint returns them (WINTER SOUR probe, 21 Aug
# 2026): quantities are stored in STOCK units (0.03 at unitRatio 0.001 is
# 30 mL display); lines reference an item OR a sub-recipe; notes carry
# pasted-web-page HTML.
RAW_RECIPE = {
    "id": "r-1",
    "name": "COCKTAIL - WINTER SOUR",
    "prepRecipe": False,
    "isCountedInStocktake": True,
    "deletedAt": None,
    "notes": '<p><br></p><!--StartFragment--><ol style="font-size: 18px;"><li>Add'
    " bourbon &amp; lemon</li><li>Shake&nbsp;hard</li></ol>",
    "currentVersion": {
        "id": "v-cur",
        "recipeId": "r-1",
        "validFrom": "2026-07-07T00:00:00+12:00",
        "yieldQuantity": 1.0,
        "yieldUnitId": "u-serving",
        "yieldUnitName": "Serving",
        "yieldUnitRatio": 1.0,
        "lines": [
            {
                "id": "l-2",
                "lineOrder": 1,
                "itemId": None,
                "itemName": None,
                "recipeId": "r-sub",
                "recipeName": "COMPONENT - CLOVES HONEY",
                "unitId": "u-ml",
                "unitName": "mL",
                "unitRatio": 0.001,
                "stockUnitName": "3.5 KG",
                "stockUnitRatio": 3.5,
                "deletedAt": None,
                "quantity": 0.045,
            },
            {
                "id": "l-1",
                "lineOrder": 0,
                "itemId": "i-campari",
                "itemName": "CAMPARI",
                "recipeId": None,
                "recipeName": None,
                "unitId": "u-ml",
                "unitName": "mL",
                "unitRatio": 0.001,
                "stockUnitName": "700 mL",
                "stockUnitRatio": 0.7,
                "deletedAt": None,
                "quantity": 0.03,
            },
            {
                "id": "l-gone",
                "lineOrder": 2,
                "itemId": "i-old",
                "itemName": "RETIRED SYRUP",
                "recipeId": None,
                "unitName": "mL",
                "unitRatio": 0.001,
                "deletedAt": "2026-05-01T00:00:00+12:00",
                "quantity": 0.01,
            },
        ],
    },
    "versions": [{"id": "v-old"}, {"id": "v-cur"}],
}

LIST_ROWS = [
    {"id": "r-1", "name": "COCKTAIL - WINTER SOUR", "deletedAt": None},
    {"id": "r-2", "name": "COCKTAIL - NEGRONI", "deletedAt": None},
    {"id": "r-3", "name": "DISH - BURRATA", "deletedAt": None},
    {"id": "r-dead", "name": "COCKTAIL - RETIRED", "deletedAt": "2026-01-01"},
]


class Api:
    def __init__(self, cost_error=False):
        self.calls = []
        self.cost_error = cost_error

    def call_api(self, connector, action, params=None):
        self.calls.append((action, dict(params or {})))
        if action == "get_all_recipes":
            import copy

            return copy.deepcopy(LIST_ROWS)
        if action == "get_recipe_details":
            if params.get("recipe_id") != "r-1":
                return {"error": "not found"}
            import copy

            return copy.deepcopy(RAW_RECIPE)
        if action == "get_recipe_costs_raw":
            if self.cost_error:
                return {"error": "boom"}
            q = params["q"]
            assert q.endswith("&priceType=Live")
            costs = {}
            for part in q.split("&"):
                if part.startswith("recipeIdTimeStrings="):
                    rid = part.split("=", 1)[1].split(",", 1)[0]
                    costs[rid] = [
                        {"cost": 1.0879852857, "unitName": "Serving", "unitRatio": 1.0}
                    ]
            return {"recipeCosts": costs}
        raise AssertionError(f"unexpected action {action}")


def run(code, api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(code, ns)
    return ns["run"]({"venue": "La Zeppa", **params}, api.call_api, lambda m: None)


class TestSearch:
    def test_query_returns_slim_matches(self):
        api = Api()
        out = run(CODE, api, query="cocktail", limit=10)
        assert out["matches"] == [
            {"id": "r-1", "name": "COCKTAIL - WINTER SOUR"},
            {"id": "r-2", "name": "COCKTAIL - NEGRONI"},
        ]
        assert out["total_matches"] == 2
        # Two matches: no auto-fetch, no detail call.
        assert [a for a, _ in api.calls] == ["get_all_recipes"]

    def test_deleted_recipes_filtered(self):
        out = run(CODE, Api(), query="retired")
        assert out["total_matches"] == 0

    def test_limit_caps_query_matches(self):
        out = run(CODE, Api(), query="cocktail", limit=1)
        assert out["shown"] == 1
        assert out["total_matches"] == 2

    def test_no_query_lists_all_with_note(self):
        out = run(CODE, Api())
        assert out["shown"] == 3  # the deleted one is gone, no 25 cap
        assert "query" in out["note"]

    def test_unambiguous_query_autofetches_summary_and_cost(self):
        api = Api()
        out = run(CODE, api, query="winter sour")
        assert out["total_matches"] == 1
        assert out["recipe"]["name"] == "COCKTAIL - WINTER SOUR"
        assert out["recipe"]["version_id"] == "v-cur"
        assert out["cost"]["cost"] == 1.088
        assert [a for a, _ in api.calls] == [
            "get_all_recipes",
            "get_recipe_details",
            "get_recipe_costs_raw",
        ]

    def test_include_cost_decorates_matches_in_one_call(self):
        api = Api()
        out = run(CODE, api, query="cocktail", include_cost=True)
        m = {r["id"]: r for r in out["matches"]}
        assert m["r-1"]["cost"] == 1.088
        assert m["r-1"]["cost_unit"] == "Serving"
        cost_calls = [(a, p) for a, p in api.calls if a == "get_recipe_costs_raw"]
        assert len(cost_calls) == 1
        assert "recipeIdTimeStrings=r-1," in cost_calls[0][1]["q"]
        assert "recipeIdTimeStrings=r-2," in cost_calls[0][1]["q"]


class TestOneRecipe:
    def test_summary_shape(self):
        out = run(CODE, Api(), recipe_id="r-1")
        r = out["recipe"]
        assert out["detail"] == "summary"
        assert r["id"] == "r-1"
        assert r["version_id"] == "v-cur"
        assert r["yield"] == {"quantity": 1.0, "unit": "Serving"}
        assert r["versions_count"] == 2
        # Lines: display units (0.03 / 0.001 = 30), lineOrder respected,
        # deleted line dropped, sub-recipe ref kept, item ids dropped.
        assert r["lines"] == [
            {"name": "CAMPARI", "kind": "item", "quantity": 30, "unit": "mL"},
            {
                "name": "COMPONENT - CLOVES HONEY",
                "kind": "recipe",
                "quantity": 45,
                "unit": "mL",
                "recipe_id": "r-sub",
            },
        ]
        assert out["cost"] == {"cost": 1.088, "unit": "Serving", "price_type": "Live"}

    def test_notes_html_stripped(self):
        out = run(CODE, Api(), recipe_id="r-1")
        notes = out["recipe"]["notes"]
        assert "<" not in notes and "&amp;" not in notes
        assert "Add bourbon & lemon" in notes
        assert "Shake hard" in notes

    def test_long_notes_truncated(self):
        api = Api()
        big = dict(RAW_RECIPE)
        big["notes"] = "word " * 1000

        def call_api(connector, action, params=None):
            if action == "get_recipe_details":
                return big
            return api.call_api(connector, action, params)

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"]({"venue": "x", "recipe_id": "r-1"}, call_api, lambda m: None)
        assert len(out["recipe"]["notes"]) < 1600
        assert "truncated" in out["recipe"]["notes"]

    def test_detail_full_is_raw_passthrough(self):
        out = run(CODE, Api(), recipe_id="r-1", detail="full")
        assert out["detail"] == "full"
        assert out["recipe"]["currentVersion"]["lines"][0]["quantity"] == 0.045
        assert out["recipe"]["notes"].startswith("<p>")

    def test_unknown_recipe_errors(self):
        out = run(CODE, Api(), recipe_id="r-nope")
        assert out == {"error": "not found"}

    def test_cost_failure_never_breaks_the_read(self):
        out = run(CODE, Api(cost_error=True), recipe_id="r-1")
        assert out["recipe"]["name"] == "COCKTAIL - WINTER SOUR"
        assert "cost" not in out
