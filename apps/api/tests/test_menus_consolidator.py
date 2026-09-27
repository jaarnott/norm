"""get_menus + manage_menu — the menu lookup and the menu write tool.

Exec'd under the REAL sandbox namespace (no `next`, no imports) against the
canonical files. Fixtures are shaped like Loaded's live MenuModel (production
list_menus payload, La Zeppa, 25 Aug 2026): a menu is {id, name, createdAt,
deletedAt, groups:[{id, name, lines:[{id, workingPrice, menuId, recipeId,
stockItemId, name, lineOrder, stockUnitRatio, salesTaxRateId}]}]}. The raw
list_menus reply was ~10,600 tokens for eight menus; every line carried a
menuId and two UUIDs the model never used.

Facts pinned:
- the slim list excludes deleted menus and carries no lines;
- query finds a dish across menus with its section and price;
- update merges deltas into the WHOLE MenuModel Loaded's PUT expects (a
  field not sent is a field deleted) and re-reads to prove added lines
  landed — a 200 is not proof;
- JSON-string arguments are read, not ignored.
"""

import copy
import json
import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
READ = (_DIR / "get_menus.py").read_text()
WRITE = (_DIR / "manage_menu.py").read_text()


def _line(i, name, price, recipe=True, order=None):
    return {
        "id": f"line-{i}",
        "workingPrice": price,
        "menuId": "m-cocktails",
        "recipeId": f"r-{i}" if recipe else None,
        "stockItemId": None if recipe else f"s-{i}",
        "name": name,
        "lineOrder": order or i,
        "stockUnitRatio": None,
        "salesTaxRateId": None,
    }


COCKTAILS = {
    "id": "m-cocktails",
    "name": "COCKTAIL LIST",
    "createdAt": "2024-08-15T10:35:41.7468735+00:00",
    "deletedAt": None,
    "groups": [
        {
            "id": "g-classics",
            "name": "CLASSICS",
            "lines": [
                _line(1, "COCKTAIL - ESPRESSO MARTINI (NEW)", 20.0, order=2),
                _line(2, "COCKTAIL - MARGARITA COUPE (NEW)", 20.0, order=1),
            ],
        },
        {
            "id": "g-spritz",
            "name": "SPRITZ",
            "lines": [
                _line(3, "APEROL SPRITZ", 16.0),
                _line(4, "LEMONADE", 5.0, recipe=False),
            ],
        },
    ],
}
DINNER = {
    "id": "m-dinner",
    "name": "DINNER MENU 2026",
    "createdAt": "2026-01-10T00:00:00+00:00",
    "deletedAt": None,
    "groups": [
        {
            "id": "g-mains",
            "name": "MAINS",
            "lines": [_line(9, "STEAK FRITES", 38.0), _line(10, "APEROL SPRITZ", 17.0)],
        }
    ],
}
OLD = {
    "id": "m-old",
    "name": "WINTER 2024",
    "createdAt": "2024-05-01T00:00:00+00:00",
    "deletedAt": "2025-01-01T00:00:00+00:00",
    "groups": [{"id": "g-x", "name": "X", "lines": [_line(20, "GONE DISH", 1.0)]}],
}


class Api:
    def __init__(self, fail_put=False):
        self.calls = []
        self.menus = {m["id"]: copy.deepcopy(m) for m in (COCKTAILS, DINNER, OLD)}
        self.put_body = None
        self.post_body = None
        self.deleted = None
        self.fail_put = fail_put
        self.drop_on_put = None  # a line name Loaded silently drops

    def call_api(self, connector, action, params=None):
        p = dict(params or {})
        self.calls.append((action, p))
        if action == "list_menus":
            return [copy.deepcopy(m) for m in self.menus.values()]
        if action == "get_menu":
            m = self.menus.get(p.get("menu_id"))
            return copy.deepcopy(m) if m else {"error": "404 not found"}
        if action == "update_menu":
            if self.fail_put:
                return {"error": "HTTP 400 validation"}
            self.put_body = copy.deepcopy(p["menu"])
            stored = copy.deepcopy(p["menu"])
            if self.drop_on_put:
                for g in stored["groups"]:
                    g["lines"] = [
                        ln for ln in g["lines"] if ln["name"] != self.drop_on_put
                    ]
            self.menus[p["menu_id"]] = stored
            return stored
        if action == "create_menu":
            self.post_body = copy.deepcopy(p["menu"])
            return {"id": "m-new", **p["menu"]}
        if action == "delete_menu":
            self.deleted = p["menu_id"]
            return {}
        raise AssertionError(f"unexpected action {action}")


def run(code, api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(code, ns)
    return ns["run"]({"venue": "La Zeppa", **params}, api.call_api, lambda m: None)


class TestList:
    def test_slim_rows_exclude_deleted_and_carry_no_lines(self):
        api = Api()
        out = run(READ, api)
        assert out["total"] == 2 and out["deleted_excluded"] == 1
        assert [m["name"] for m in out["menus"]] == [
            "COCKTAIL LIST",
            "DINNER MENU 2026",
        ]
        assert out["menus"][0] == {
            "id": "m-cocktails",
            "name": "COCKTAIL LIST",
            "sections": 2,
            "lines": 4,
            "created": "2024-08-15",
        }
        assert "groups" not in json.dumps(out)
        assert len(api.calls) == 1

    def test_query_finds_a_dish_across_menus_with_section_and_price(self):
        out = run(READ, Api(), query="aperol")
        assert out["total_dishes_matching"] == 2
        assert [
            (h["menu"], h["section"], h["price"]) for h in out["dishes_matching"]
        ] == [("COCKTAIL LIST", "SPRITZ", 16.0), ("DINNER MENU 2026", "MAINS", 17.0)]
        assert out["menus_matching"] == []
        assert out["menus_searched"] == 2  # deleted menu not searched

    def test_query_matches_menu_names_too(self):
        out = run(READ, Api(), query="dinner")
        assert out["menus_matching"] == [{"id": "m-dinner", "name": "DINNER MENU 2026"}]


class TestOneMenu:
    def test_summary_orders_lines_and_names_the_kind(self):
        out = run(READ, Api(), menu_id="m-cocktails")
        assert out["detail"] == "summary" and out["lines"] == 4
        classics = out["sections"][0]
        assert classics["name"] == "CLASSICS"
        assert [ln["name"] for ln in classics["lines"]] == [
            "COCKTAIL - MARGARITA COUPE (NEW)",
            "COCKTAIL - ESPRESSO MARTINI (NEW)",
        ]
        assert out["sections"][1]["lines"][1] == {
            "id": "line-4",
            "name": "LEMONADE",
            "price": 5.0,
            "kind": "stock item",
        }
        assert "recipeId" not in json.dumps(out) and "menuId" not in json.dumps(out)

    def test_full_returns_the_raw_model_tagged_for_the_editor(self):
        out = run(READ, Api(), menu_id="m-cocktails", detail="full")
        assert (
            out["detail"] == "full"
            and out["groups"][0]["lines"][0]["recipeId"] == "r-1"
        )

    def test_missing_menu_errors(self):
        assert "not found" in run(READ, Api(), menu_id="m-nope")["error"]


class TestManageMenuUpdate:
    def test_price_change_merges_into_the_whole_model(self):
        api = Api()
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            line_changes=[{"name": "APEROL SPRITZ", "price": 18}],
        )
        assert out["result"] == "updated"
        assert out["changed"] == ["'APEROL SPRITZ' price 16.0 -> 18.0"]
        body = api.put_body
        assert body["name"] == "COCKTAIL LIST" and len(body["groups"]) == 2
        assert body["groups"][1]["lines"][0]["workingPrice"] == 18.0
        assert (
            body["groups"][0]["lines"][0]["recipeId"] == "r-1"
        )  # untouched lines survive whole
        assert [a for a, _ in api.calls] == [
            "get_menu",
            "update_menu",
        ]  # no re-read: nothing added

    def test_json_string_arguments_are_read(self):
        api = Api()
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            changes='{"name": "COCKTAILS 2026"}',
            remove_lines='["LEMONADE"]',
        )
        assert out["result"] == "updated"
        assert api.put_body["name"] == "COCKTAILS 2026"
        assert [ln["name"] for ln in api.put_body["groups"][1]["lines"]] == [
            "APEROL SPRITZ"
        ]

    def test_added_lines_are_verified_by_a_re_read(self):
        api = Api()
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            add_lines=[
                {
                    "section": "SPRITZ",
                    "name": "HUGO SPRITZ",
                    "price": 17,
                    "recipe_id": "r-hugo",
                }
            ],
        )
        assert out["result"] == "updated" and out["added_lines_confirmed"] == [
            "HUGO SPRITZ"
        ]
        assert [a for a, _ in api.calls] == ["get_menu", "update_menu", "get_menu"]
        new = api.put_body["groups"][1]["lines"][2]
        assert new["recipeId"] == "r-hugo" and new["lineOrder"] == 5 and "id" not in new

    def test_a_line_loaded_drops_is_reported_not_hidden(self):
        api = Api()
        api.drop_on_put = "HUGO SPRITZ"
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            add_lines=[{"section": "SPRITZ", "name": "HUGO SPRITZ", "price": 17}],
        )
        assert out["added_lines_MISSING_after_write"] == ["HUGO SPRITZ"]
        assert "dropped" in out["result"]

    def test_a_new_section_is_created_for_an_added_line(self):
        api = Api()
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            add_lines=[{"section": "MOCKTAILS", "name": "VIRGIN MOJITO", "price": 12}],
        )
        assert "added section 'MOCKTAILS'" in out["changed"]
        assert api.put_body["groups"][2] == {
            "name": "MOCKTAILS",
            "lines": [
                {
                    "name": "VIRGIN MOJITO",
                    "workingPrice": 12.0,
                    "recipeId": None,
                    "stockItemId": None,
                    "lineOrder": 1,
                    "salesTaxRateId": None,
                    "menuId": "m-cocktails",
                }
            ],
        }

    def test_ambiguous_and_missing_lines_are_skipped_with_a_reason(self):
        api = Api()
        api.menus["m-cocktails"]["groups"][0]["lines"].append(
            _line(5, "APEROL SPRITZ", 15.0)
        )
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            line_changes=[
                {"name": "APEROL SPRITZ", "price": 18},
                {"name": "NOPE", "price": 1},
            ],
        )
        assert out["result"].startswith("no differences")
        assert any("2 sections" in s for s in out["skipped"]) and any(
            "no line 'NOPE'" in s for s in out["skipped"]
        )
        assert api.put_body is None

    def test_section_disambiguates_by_name(self):
        api = Api()
        api.menus["m-cocktails"]["groups"][0]["lines"].append(
            _line(5, "APEROL SPRITZ", 15.0)
        )
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            remove_lines=[{"name": "APEROL SPRITZ", "section": "CLASSICS"}],
        )
        assert out["changed"] == ["removed 'APEROL SPRITZ' from 'CLASSICS'"]
        assert len(api.put_body["groups"][1]["lines"]) == 2

    def test_move_and_remove_section(self):
        api = Api()
        out = run(
            WRITE,
            api,
            op="update",
            menu_id="m-cocktails",
            line_changes=[{"line_id": "line-3", "move_to": "CLASSICS"}],
            remove_sections=["SPRITZ"],
        )
        assert "moved 'APEROL SPRITZ' to 'CLASSICS'" in out["changed"]
        assert [g["name"] for g in api.put_body["groups"]] == ["CLASSICS"]
        assert len(api.put_body["groups"][0]["lines"]) == 3

    def test_a_failed_put_is_an_error_with_what_was_attempted(self):
        out = run(
            WRITE,
            Api(fail_put=True),
            op="update",
            menu_id="m-cocktails",
            changes={"name": "X"},
        )
        assert out["error"] == "HTTP 400 validation" and out["attempted"]

    def test_nothing_to_change_and_unreadable_json_error_before_any_call(self):
        api = Api()
        assert (
            "nothing to change"
            in run(WRITE, api, op="update", menu_id="m-cocktails")["error"]
        )
        assert (
            "could not be read as JSON"
            in run(WRITE, api, op="update", menu_id="m-cocktails", changes="rename it")[
                "error"
            ]
        )
        assert api.calls == []


class TestManageMenuCreateDelete:
    def test_create_builds_loadeds_model_from_sections_and_prices(self):
        api = Api()
        out = run(
            WRITE,
            api,
            op="create",
            menu={
                "name": "BRUNCH",
                "sections": [
                    {
                        "name": "EGGS",
                        "lines": [
                            {"name": "BENEDICT", "price": 24, "recipe_id": "r-ben"},
                            {"name": "JUICE", "price": 6, "stock_item_id": "s-j"},
                        ],
                    }
                ],
            },
        )
        assert out == {
            "result": "created",
            "name": "BRUNCH",
            "menu_id": "m-new",
            "sections": 1,
            "lines": 2,
        }
        lines = api.post_body["groups"][0]["lines"]
        assert lines[0] == {
            "name": "BENEDICT",
            "workingPrice": 24.0,
            "recipeId": "r-ben",
            "stockItemId": None,
            "lineOrder": 1,
            "salesTaxRateId": None,
        }
        assert lines[1]["stockItemId"] == "s-j" and lines[1]["lineOrder"] == 2

    def test_create_needs_a_name(self):
        assert (
            "needs a name"
            in run(WRITE, Api(), op="create", menu={"sections": []})["error"]
        )
        assert "needs `menu`" in run(WRITE, Api(), op="create")["error"]

    def test_delete(self):
        api = Api()
        assert run(WRITE, api, op="delete", menu_id="m-old") == {
            "result": "deleted",
            "menu_id": "m-old",
        }
        assert api.deleted == "m-old"

    def test_unknown_op_refused_without_calling(self):
        api = Api()
        assert "op must be one of" in run(WRITE, api, op="publish")["error"]
        assert api.calls == []
