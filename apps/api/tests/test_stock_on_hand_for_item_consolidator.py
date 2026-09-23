"""get_stock_on_hand_for_item: item → group → category template → one row.

Exec'd under the REAL sandbox namespace against the canonical file. Before
23 Sep 2026 the tool read `superGroupName`, a field the groups transform never
emits, so it failed on every call it ever received. The facts pinned:

- the template is the one whose id IS the group's categoryId (Loaded's
  Beverage / Food / Other Stock categories share ids with their
  whole-category templates — confirmed on production payloads, 3 venues);
- a title that merely CONTAINS the category ("BEVERAGE - CELLAR") is never
  taken — it's a partial area the item may not be counted on;
- a slow stock-on-hand report is retried once, and a Loaded failure is
  reported as one, never as "not counted".
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE = (_DIR / "get_stock_on_hand_for_item.py").read_text()

BEV = "f73c08bd-0000-0000-0000-000000000001"
FOOD = "4e2de363-0000-0000-0000-000000000002"

# Shaped like the live transforms: groups carry categoryId / categoryName
# (no superGroupName); templates are {id, title}, area templates listed FIRST
# so a substring match would take them.
GROUPS = [
    {
        "id": "g-spirits",
        "name": "Spirits",
        "categoryId": BEV,
        "categoryName": "Beverage",
    },
    {"id": "g-veg", "name": "Vegetables", "categoryId": FOOD, "categoryName": "Food"},
]
TEMPLATES = [
    {"id": "t-cellar", "title": "BEVERAGE - CELLAR"},
    {"id": "t-bessie", "title": "BEVERAGE - BESSIE BAR"},
    {"id": "t-staff", "title": "Staff Food"},
    {"id": BEV, "title": "Beverage"},
    {"id": FOOD, "title": "Food"},
]
ITEM = {"id": "item-1", "name": "Jim Beam 700ml", "groupId": "g-spirits"}
ROW = {
    "stockItemID": "item-1",
    "itemName": "Jim Beam 700ml",
    "Category": "Beverage",
    "countingUnitName": "Bottle",
    "quantityOnHand": 7.5,
    "valueOnHand": 330.0,
}


class Api:
    def __init__(self, item=ITEM, groups=GROUPS, templates=TEMPLATES, soh=None):
        self.item, self.groups, self.templates = item, groups, templates
        # soh: list of responses, one per get_stock_on_hand call
        self.soh = list(soh) if soh is not None else [{"lines": [ROW]}]
        self.calls = []

    def call_api(self, connector, action, params=None):
        self.calls.append((action, dict(params or {})))
        if action == "get_stock_item_full":
            return dict(self.item)
        if action == "get_stock_item_groups":
            return list(self.groups)
        if action == "get_stocktake_templates":
            return list(self.templates)
        if action == "get_stock_on_hand":
            return self.soh.pop(0)
        raise AssertionError(f"unexpected action {action}")

    def call_api_parallel(self, calls):
        return [self.call_api(c, a, p) for c, a, p in calls]

    def soh_templates(self):
        return [p["template_id"] for a, p in self.calls if a == "get_stock_on_hand"]


def run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    return ns["run"](
        {
            "venue": "La Zeppa",
            "item_id": "item-1",
            "today_iso": "2026-09-23T00:00:00",
            **params,
        },
        api.call_api,
        lambda m: None,
        api.call_api_parallel,
    )


class TestTemplateResolution:
    def test_live_group_shape_resolves_and_returns_the_row(self):
        # The incident: groups carry categoryName, never superGroupName, and
        # the old code errored on this exact payload every time.
        api = Api()
        out = run(api)
        assert "error" not in out, out
        assert out["quantityOnHand"] == 7.5
        assert out["countingUnitName"] == "Bottle"
        assert api.soh_templates() == [BEV]

    def test_area_templates_containing_the_category_are_never_taken(self):
        api = Api()
        run(api)
        assert "t-cellar" not in api.soh_templates()
        assert "t-bessie" not in api.soh_templates()

    def test_food_is_not_staff_food(self):
        api = Api(item={"id": "item-1", "name": "Leeks", "groupId": "g-veg"})
        run(api)
        assert api.soh_templates() == [FOOD]

    def test_falls_back_to_an_exact_title_when_ids_differ(self):
        templates = [
            {"id": "t-cellar", "title": "BEVERAGE - CELLAR"},
            {"id": "t-whole", "title": "beverage"},
        ]
        api = Api(templates=templates)
        out = run(api)
        assert "error" not in out, out
        assert api.soh_templates() == ["t-whole"]

    def test_no_whole_category_template_is_an_error_not_a_guess(self):
        api = Api(templates=[{"id": "t-cellar", "title": "BEVERAGE - CELLAR"}])
        out = run(api)
        assert "whole-category stocktake template" in out["error"]
        assert api.soh_templates() == []


class TestStockOnHandFailures:
    def test_a_slow_report_is_retried_once(self):
        api = Api(soh=[{"error": "500 timeout"}, {"lines": [ROW]}])
        out = run(api)
        assert out["quantityOnHand"] == 7.5
        assert len(api.soh_templates()) == 2

    def test_a_loaded_failure_is_never_reported_as_not_counted(self):
        api = Api(soh=[{"error": "500 timeout"}, {"error": "500 timeout"}])
        out = run(api)
        assert "LoadedHub failure" in out["error"]
        assert "isn't counted" not in out["error"]

    def test_item_absent_from_the_snapshot_is_not_counted(self):
        api = Api(soh=[{"lines": [{**ROW, "stockItemID": "other"}]}])
        out = run(api)
        assert "isn't counted" in out["error"]
        assert len(api.soh_templates()) == 1
