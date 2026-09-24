"""get_stock — THE stock read tool: four views, each inlined, never nested.

Exec'd under the REAL sandbox namespace (no `next`, no imports) against the
canonical file. The facts pinned, each from a production incident or a
verified Loaded fact:

- items is get_stock_items verbatim, minus its one leak: the summary's
  minimum_stock_unit was Loaded's WHOLE unit object; it is the unit's name.
- on_hand by item finds the whole-category template by ID (categoryId ==
  template id on five venues); an area template whose title merely contains
  the category ("BEVERAGE - CELLAR") is never taken — the pre-0c tool read a
  field that didn't exist and failed every call.
- a slow stock-on-hand report is retried once, serially, and a Loaded
  failure is reported as one — never as "not counted" or an empty snapshot.
- a whole-template snapshot shows top rows plus an "(others)" rollup whose
  totals stay honest; deleted items are excluded and counted.
- reference rows are slim and filterable: the raw units list (456 rows at
  La Zeppa) trips the MCP size gate as-is.
- minimums are converted into counting units (a 24 Pack minimum on a beer
  counted in Each is 24x understated without it).
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE = (_DIR / "get_stock.py").read_text()

BEV = "a134765a-0000-4000-8000-000000000001"
FOOD = "665a5818-0000-4000-8000-000000000002"
OTHER = "bd216723-0000-4000-8000-000000000003"

ITEMS_RAW = [
    {
        "id": "i-jbb",
        "groupId": "g-spirits",
        "groupName": "Spirits",
        "name": "JIM BEAM BLACK",
    },
    {
        "id": "i-jbw",
        "groupId": "g-spirits",
        "groupName": "Spirits",
        "name": "JIM BEAM WHITE 37%",
    },
    {
        "id": "i-asahi",
        "groupId": "g-bottled",
        "groupName": "Bottled",
        "name": "ASAHI SUPER DRY",
    },
    {
        "id": "i-cumin",
        "groupId": "g-herbs",
        "groupName": "Herbs and Spices",
        "name": "CUMIN GROUND",
    },
    {
        "id": "i-nap",
        "groupId": "g-pack",
        "groupName": "Packaging and Consumables",
        "name": "Cocktail Napkins",
    },
]

# Shaped like the live /internal/items/{id} payload (JIM BEAM BLACK and ASAHI
# SUPER DRY at La Zeppa, 24 Sep 2026): item-level unit/group NAMES ride
# inline, the minimum's unit is a WHOLE unit object, variants carry ids only.
FULL = {
    "i-jbb": {
        "id": "i-jbb",
        "name": "JIM BEAM BLACK",
        "groupId": "g-spirits",
        "groupName": "Spirits",
        "countingUnitId": "u-l",
        "countingUnitName": "Litre",
        "countingUnitRatio": 1,
        "orderingUnitId": "u-l",
        "orderingUnitName": "Litre",
        "orderingUnitRatio": 1,
        "minimumStockOnHandQuantity": 1.2,
        "minimumStockOnHandUnitId": "u-l",
        "minimumStockOnHandUnit": {
            "id": "u-l",
            "stockUnitType": "Unknown",
            "ratio": 1,
            "name": "Litre",
            "datestampDeleted": None,
            "masterId": "m-1",
        },
        "suppliers": [],
        "unitType": 0,
        "itemType": "Default",
    },
    "i-asahi": {
        "id": "i-asahi",
        "name": "ASAHI SUPER DRY",
        "groupId": "g-bottled",
        "groupName": "Bottled",
        "countingUnitId": "u-each",
        "countingUnitName": "Each",
        "countingUnitRatio": 1,
        "orderingUnitId": "u-12",
        "orderingUnitName": "12 Pack",
        "orderingUnitRatio": 12,
        "minimumStockOnHandQuantity": 27,
        "minimumStockOnHandUnit": {"id": "u-each", "name": "Each", "ratio": 1},
        "suppliers": [
            {
                "id": "var-asahi",
                "supplierId": "s-asahi",
                "stockCode": "DPASAHI12AUN",
                "unitId": "u-12",
                "unitCost": 0,
                "defaultForSupplier": True,
            }
        ],
    },
    "i-cumin": {
        "id": "i-cumin",
        "name": "CUMIN GROUND",
        "groupId": "g-herbs",
        "groupName": "Herbs and Spices",
        "countingUnitName": "KG",
        "countingUnitRatio": 1,
        "orderingUnitName": "KG",
        "orderingUnitRatio": 1,
        "minimumStockOnHandQuantity": None,
        "minimumStockOnHandUnit": None,
        "suppliers": [],
    },
    "i-nap": {
        "id": "i-nap",
        "name": "Cocktail Napkins",
        "groupId": "g-pack",
        "groupName": "Packaging and Consumables",
        "countingUnitName": "CARTON",
        "suppliers": [],
    },
}

UNITS = [
    {
        "id": "u-each",
        "name": "Each",
        "stockUnitType": "Count",
        "ratio": 1,
        "datestampDeleted": None,
    },
    {
        "id": "u-12",
        "name": "12 Pack",
        "stockUnitType": "Count",
        "ratio": 12,
        "datestampDeleted": None,
    },
    {
        "id": "u-l",
        "name": "Litre",
        "stockUnitType": "Volume",
        "ratio": 1,
        "datestampDeleted": None,
    },
    {
        "id": "u-kg",
        "name": "KG",
        "stockUnitType": "Weight",
        "ratio": 1,
        "datestampDeleted": None,
    },
    {
        "id": "u-old",
        "name": "Old Unit",
        "stockUnitType": "Count",
        "ratio": 1,
        "datestampDeleted": "2025-01-01",
    },
]

SUPPLIERS = [
    {
        "id": "s-asahi",
        "name": "Asahi",
        "contactName": "Kirsten",
        "emails": [{"email": "orders@asahi.nz", "isPrimary": True}],
        "customerNumber": "C1",
        "lastOrderAt": "2026-09-01T00:00:00+00:00",
        "removedAt": None,
    },
    {"id": "s-sf", "name": "SERVICE FOODS AUCKLAND", "emails": [], "removedAt": None},
    {"id": "s-gone", "name": "Old Supplier", "emails": [], "removedAt": "2025-06-01"},
]

GROUPS = [
    {
        "id": "g-spirits",
        "name": "Spirits",
        "categoryId": BEV,
        "categoryName": "Beverage",
    },
    {
        "id": "g-bottled",
        "name": "Bottled",
        "categoryId": BEV,
        "categoryName": "Beverage",
    },
    {
        "id": "g-herbs",
        "name": "Herbs and Spices",
        "categoryId": FOOD,
        "categoryName": "Food",
    },
    {
        "id": "g-pack",
        "name": "Packaging and Consumables",
        "categoryId": OTHER,
        "categoryName": "Other Stock",
    },
]

# Area templates first, so a substring match would take them.
TEMPLATES = [
    {"id": "t-cellar", "title": "BEVERAGE - CELLAR"},
    {"id": "t-staff", "title": "Staff Food"},
    {"id": "t-front", "title": "Front Bar Fridge"},
    {"id": BEV, "title": "Beverage"},
    {"id": FOOD, "title": "Food"},
    {"id": OTHER, "title": "Other Stock"},
]

# Stock-on-hand lines as the live transform emits them.
SOH = {
    BEV: [
        {
            "Category": "Spirits",
            "stockItemID": "i-jbb",
            "itemName": "JIM BEAM BLACK",
            "isItemDeleted": False,
            "countingUnitName": "Litre",
            "countingUnitRatio": 1,
            "quantityOnHand": 2.8,
            "valueOnHand": 117.712,
        },
        {
            "Category": "Spirits",
            "stockItemID": "i-jbw",
            "itemName": "JIM BEAM WHITE 37%",
            "isItemDeleted": False,
            "countingUnitName": "Litre",
            "countingUnitRatio": 1,
            "quantityOnHand": 0,
            "valueOnHand": 0,
        },
        {
            "Category": "Bottled",
            "stockItemID": "i-asahi",
            "itemName": "ASAHI SUPER DRY",
            "isItemDeleted": False,
            "countingUnitName": "Each",
            "countingUnitRatio": 1,
            "quantityOnHand": 36,
            "valueOnHand": 90.0,
        },
        {
            "Category": "Bottled",
            "stockItemID": "i-dead",
            "itemName": "DELETED LAGER",
            "isItemDeleted": True,
            "countingUnitName": "Each",
            "countingUnitRatio": 1,
            "quantityOnHand": 5,
            "valueOnHand": 12.5,
        },
    ],
    FOOD: [
        {
            "Category": "Herbs and Spices",
            "stockItemID": "i-cumin",
            "itemName": "CUMIN GROUND",
            "isItemDeleted": False,
            "countingUnitName": "KG",
            "countingUnitRatio": 1,
            "quantityOnHand": 0.42,
            "valueOnHand": 10.52,
        },
    ],
    OTHER: [
        {
            "Category": "Packaging and Consumables",
            "stockItemID": "i-nap",
            "itemName": "Cocktail Napkins",
            "isItemDeleted": False,
            "countingUnitName": "CARTON",
            "countingUnitRatio": 1,
            "quantityOnHand": 0,
            "valueOnHand": 0,
        },
    ],
    "t-front": [
        {
            "Category": "Bottled",
            "stockItemID": "i-asahi",
            "itemName": "ASAHI SUPER DRY",
            "isItemDeleted": False,
            "countingUnitName": "Each",
            "countingUnitRatio": 1,
            "quantityOnHand": 12,
            "valueOnHand": 30.0,
        },
    ],
}

MINIMUMS = [
    {
        "id": "i-asahi",
        "itemName": "ASAHI SUPER DRY",
        "countingUnitRatio": 1,
        "minQty": 27,
        "minUnitRatio": 1,
    },
    {
        "id": "i-stella",
        "itemName": "STELLA 330ML",
        "countingUnitRatio": 1,
        "minQty": 2,
        "minUnitRatio": 24,
    },
    {
        "id": "i-jbb",
        "itemName": "JIM BEAM BLACK",
        "countingUnitRatio": 1,
        "minQty": 0,
        "minUnitRatio": 0,
    },
]


class Api:
    def __init__(self, soh_failures=0):
        self.calls = []
        self.soh_failures = soh_failures

    def call_api(self, connector, action, params=None):
        p = dict(params or {})
        self.calls.append((action, p))
        if action == "get_stock_items_raw":
            return [dict(r) for r in ITEMS_RAW]
        if action == "get_stock_item_full":
            item = FULL.get(p.get("item_id"))
            return dict(item) if item else {"error": "404 not found"}
        if action == "get_stock_units":
            return [dict(u) for u in UNITS]
        if action == "get_suppliers":
            return [dict(s) for s in SUPPLIERS]
        if action == "get_stock_item_groups":
            return [dict(g) for g in GROUPS]
        if action == "get_stocktake_templates":
            return [dict(t) for t in TEMPLATES]
        if action == "get_stock_on_hand":
            if self.soh_failures > 0:
                self.soh_failures -= 1
                return {"error": "HTTP 500 timeout"}
            return {"lines": [dict(r) for r in SOH.get(p.get("template_id"), [])]}
        if action == "get_stock_item_minimums":
            return [dict(m) for m in MINIMUMS]
        raise AssertionError(f"unexpected action {action}")

    def call_api_parallel(self, calls):
        return [self.call_api(c, a, p) for c, a, p in calls]

    def actions(self):
        return [a for a, _ in self.calls]

    def soh_calls(self):
        return [p for a, p in self.calls if a == "get_stock_on_hand"]


def run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    return ns["run"](
        {
            "venue": "La Zeppa",
            "today": "2026-09-24",
            "today_iso": "2026-09-24T00:00:00%2B12:00",
            **params,
        },
        api.call_api,
        lambda m: None,
        api.call_api_parallel,
    )


class TestViews:
    def test_default_view_is_items(self):
        out = run(Api(), query="jim beam")
        assert out["total_matches"] == 2

    def test_unknown_view_is_refused_without_fetching(self):
        api = Api()
        out = run(api, view="stocktakes")
        assert "Unknown view" in out["error"] and "on_hand" in out["error"]
        assert api.calls == []


class TestItems:
    def test_query_returns_slim_matches_only(self):
        out = run(Api(), query="jim beam")
        assert out["total_matches"] == 2 and out["shown"] == 2
        assert [m["name"] for m in out["matches"]] == [
            "JIM BEAM BLACK",
            "JIM BEAM WHITE 37%",
        ]
        assert "item" not in out

    def test_unambiguous_query_carries_the_summary_in_four_calls(self):
        api = Api()
        out = run(api, query="asahi")
        assert out["total_matches"] == 1
        item = out["item"]
        assert item["ordering_unit"] == "12 Pack" and item["ordering_unit_ratio"] == 12
        assert item["variants"][0]["supplier"] == "Asahi"
        assert item["variants"][0]["unit"] == "12 Pack"
        assert item["variants"][0]["stock_code"] == "DPASAHI12AUN"
        # Bug A: the row shipped with a budget of 3 for this 4-call path.
        assert len(api.calls) == 4

    def test_minimum_stock_unit_is_a_name_not_loadeds_unit_object(self):
        # get_stock_items leaked the whole {id, stockUnitType, ratio, name,
        # datestampDeleted, masterId} object here — seen live 24 Sep 2026.
        out = run(Api(), item_id="i-jbb")
        assert out["item"]["minimum_stock_unit"] == "Litre"
        assert out["item"]["minimum_stock_on_hand"] == 1.2

    def test_item_id_summary_carries_names_never_uuids(self):
        out = run(Api(), item_id="i-asahi")
        item = out["item"]
        assert out["detail"] == "summary"
        assert item["counting_unit"] == "Each" and item["group"] == "Bottled"
        flat = {k: v for k, v in item.items() if k != "variants"}
        assert not any(
            str(v).startswith("u-") or str(v).startswith("s-") for v in flat.values()
        )
        assert set(item["variants"][0]) == {
            "variant_id",
            "supplier",
            "stock_code",
            "unit",
            "unit_cost",
            "default",
        }

    def test_full_returns_everything(self):
        out = run(Api(), item_id="i-asahi", detail="full")
        assert out["detail"] == "full"
        assert out["item"]["suppliers"][0]["stockCode"] == "DPASAHI12AUN"

    def test_missing_item_errors(self):
        out = run(Api(), item_id="i-nope")
        assert "error" in out

    def test_bare_list_carries_the_steering_note_and_limit(self):
        out = run(Api(), limit=2)
        assert out["shown"] == 2 and out["total_matches"] == 5
        assert "query" in out["note"]


class TestOnHandByItem:
    def test_live_group_shape_resolves_the_category_template_by_id(self):
        api = Api()
        out = run(api, view="on_hand", item_id="i-jbb")
        assert "error" not in out, out
        assert out["quantity_on_hand"] == 2.8 and out["counting_unit"] == "Litre"
        assert out["value_on_hand"] == 117.712
        assert out["template"] == "Beverage" and out["as_of"] == "2026-09-24"
        assert [p["template_id"] for p in api.soh_calls()] == [BEV]

    def test_area_templates_are_never_taken(self):
        api = Api()
        run(api, view="on_hand", item_id="i-jbb")
        assert "t-cellar" not in [p["template_id"] for p in api.soh_calls()]

    def test_food_is_not_staff_food(self):
        api = Api()
        out = run(api, view="on_hand", item_id="i-cumin")
        assert out["quantity_on_hand"] == 0.42
        assert [p["template_id"] for p in api.soh_calls()] == [FOOD]

    def test_other_stock_resolves_too(self):
        out = run(Api(), view="on_hand", item_id="i-nap")
        assert out["quantity_on_hand"] == 0 and out["template"] == "Other Stock"

    def test_default_as_of_is_the_engines_today_iso(self):
        api = Api()
        run(api, view="on_hand", item_id="i-jbb")
        assert api.soh_calls()[0]["report_datetime"] == "2026-09-24T00:00:00%2B12:00"

    def test_a_bare_as_of_date_keeps_the_venues_offset(self):
        api = Api()
        out = run(api, view="on_hand", item_id="i-jbb", as_of="2026-09-01")
        assert api.soh_calls()[0]["report_datetime"] == "2026-09-01T00:00:00%2B12:00"
        assert out["as_of"] == "2026-09-01"

    def test_a_slow_report_is_retried_once(self):
        api = Api(soh_failures=1)
        out = run(api, view="on_hand", item_id="i-jbb")
        assert out["quantity_on_hand"] == 2.8
        assert len(api.soh_calls()) == 2

    def test_a_loaded_failure_is_never_reported_as_not_counted(self):
        api = Api(soh_failures=2)
        out = run(api, view="on_hand", item_id="i-jbb")
        assert "LoadedHub failure" in out["error"]
        assert "isn't counted" not in out["error"]
        assert len(api.soh_calls()) == 2

    def test_item_absent_from_the_snapshot_is_not_counted(self):
        api = Api()
        # Napkins live on Other Stock; pretend the snapshot lacks them.
        SOH[OTHER].append({"stockItemID": "x", "itemName": "X", "quantityOnHand": 1})
        removed = SOH[OTHER].pop(0)
        try:
            out = run(api, view="on_hand", item_id="i-nap")
        finally:
            SOH[OTHER].pop()
            SOH[OTHER].insert(0, removed)
        assert "isn't counted" in out["error"]

    def test_heaviest_path_fits_the_budget(self):
        api = Api(soh_failures=1)
        run(api, view="on_hand", item_id="i-jbb")
        assert len(api.calls) == 5  # item + groups + templates + report + retry


class TestOnHandByTemplate:
    def test_exact_title_case_insensitive(self):
        api = Api()
        out = run(api, view="on_hand", template="front bar fridge")
        assert out["template"] == "Front Bar Fridge" and out["lines"] == 1
        assert [p["template_id"] for p in api.soh_calls()] == ["t-front"]

    def test_a_category_word_means_the_whole_category_template(self):
        api = Api()
        out = run(api, view="on_hand", template="food")
        assert out["template"] == "Food"
        assert [p["template_id"] for p in api.soh_calls()] == [FOOD]
        api = Api()
        run(api, view="on_hand", template="Beverage")
        assert [p["template_id"] for p in api.soh_calls()] == [BEV]

    def test_template_id_is_honoured(self):
        api = Api()
        out = run(api, view="on_hand", template_id="t-front")
        assert out["template_id"] == "t-front"

    def test_snapshot_rollup_totals_stay_honest(self):
        out = run(Api(), view="on_hand", template="Beverage", top=1)
        assert out["lines"] == 3 and out["shown"] == 1
        assert out["rows"][0]["name"] == "JIM BEAM BLACK"  # top by value
        assert out["others"] == {"count": 2, "value_on_hand": 90.0}
        assert out["total_value_on_hand"] == 207.71
        assert out["zero_on_hand"] == 1
        assert out["deleted_items_excluded"] == 1

    def test_query_and_sort_filter_the_snapshot(self):
        out = run(
            Api(), view="on_hand", template="Beverage", query="jim", sort_by="name"
        )
        assert [r["name"] for r in out["rows"]] == [
            "JIM BEAM BLACK",
            "JIM BEAM WHITE 37%",
        ]

    def test_unknown_template_names_the_venues_templates(self):
        out = run(Api(), view="on_hand", template="Cellar")
        assert "No stocktake template titled 'Cellar'" in out["error"]
        assert "Front Bar Fridge" in out["error"]

    def test_neither_item_nor_template_is_refused_without_fetching(self):
        api = Api()
        out = run(api, view="on_hand")
        assert "Pass item_id" in out["error"]
        assert api.calls == []

    def test_a_loaded_failure_is_never_an_empty_snapshot(self):
        out = run(Api(soh_failures=2), view="on_hand", template="Beverage")
        assert "LoadedHub failure" in out["error"] and "rows" not in out


class TestReference:
    def test_units_are_slim_sorted_and_deleted_dropped(self):
        out = run(Api(), view="reference", kind="units")
        assert out["total"] == 4 and out["shown"] == 4
        assert set(out["rows"][0]) == {"id", "name", "type", "ratio"}
        assert "Old Unit" not in [r["name"] for r in out["rows"]]
        assert [r["name"] for r in out["rows"]] == ["Each", "12 Pack", "Litre", "KG"]

    def test_units_include_deleted_query_and_limit(self):
        out = run(Api(), view="reference", kind="units", include_deleted=True)
        assert out["total"] == 5
        out = run(Api(), view="reference", kind="units", query="pack")
        assert [r["name"] for r in out["rows"]] == ["12 Pack"]
        out = run(Api(), view="reference", kind="units", limit=2)
        assert out["shown"] == 2 and out["total"] == 4 and "more" in out["note"]

    def test_suppliers_are_slim_with_the_primary_email(self):
        out = run(Api(), view="reference", kind="suppliers")
        assert out["total"] == 2
        asahi = out["rows"][0]
        assert asahi["name"] == "Asahi" and asahi["email"] == "orders@asahi.nz"
        assert "Old Supplier" not in [r["name"] for r in out["rows"]]
        assert "address1" not in asahi

    def test_groups_carry_their_category_and_the_category_ids(self):
        out = run(Api(), view="reference", kind="groups")
        assert out["total"] == 4
        assert out["categories"] == {
            "Beverage": BEV,
            "Food": FOOD,
            "Other Stock": OTHER,
        }
        assert out["rows"][0] == {
            "id": "g-bottled",
            "name": "Bottled",
            "category": "Beverage",
            "category_id": BEV,
        }

    def test_templates_flag_the_whole_category_ones(self):
        out = run(Api(), view="reference", kind="templates")
        assert out["total"] == 6
        by_title = {r["title"]: r for r in out["rows"]}
        assert by_title["Beverage"]["category"] == "Beverage"
        assert by_title["Food"]["category"] == "Food"
        assert "category" not in by_title["BEVERAGE - CELLAR"]
        assert "category" not in by_title["Staff Food"]
        assert [r["title"] for r in out["rows"][:3]] == [
            "Beverage",
            "Food",
            "Other Stock",
        ]

    def test_unknown_kind_is_refused_without_fetching(self):
        api = Api()
        out = run(api, view="reference", kind="recipes")
        assert "kind must be one of" in out["error"]
        assert api.calls == []


class TestMinimums:
    def test_minimums_convert_into_counting_units(self):
        out = run(Api(), view="minimums")
        by_name = {r["name"]: r for r in out["rows"]}
        assert by_name["STELLA 330ML"]["minimum_in_counting_units"] == 48  # 2 x 24 Pack
        assert by_name["ASAHI SUPER DRY"]["minimum_in_counting_units"] == 27
        assert "JIM BEAM BLACK" not in by_name  # no par level set

    def test_item_id_returns_the_row_even_at_zero(self):
        out = run(Api(), view="minimums", item_id="i-jbb")
        assert out["total"] == 1 and out["rows"][0]["minimum_as_set"] == 0

    def test_query_filters(self):
        out = run(Api(), view="minimums", query="stella")
        assert [r["name"] for r in out["rows"]] == ["STELLA 330ML"]
