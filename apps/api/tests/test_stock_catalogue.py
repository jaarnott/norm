"""The Catalogue page (Loaded Stock, Oct 2026; docs/plans/stock-catalogue-page.md).

A venue's stock items, units and suppliers, read and changed through the same
tools Norm uses in chat: get_stock (view 'catalogue'), manage_stock_item
(retire, restore, merge, and the unit ops) and manage_supplier (update, retire,
restore, merge). Each Loaded call was tried on Loaded's test company first;
these tests exec the real tool code under the real sandbox namespace against a
fake Loaded that keeps state, so a merge is judged by what it leaves behind.

Facts pinned:
- the catalogue lists live items with their variants and retired ones from
  Loaded's older list, marked; a unit Norm hides (not a measurement) is live;
- an item merge moves every received invoice line, the variants, open order
  lines, recipe lines (as NEW lines: Loaded never changes a line's item) and
  POS links, and retires the item LAST — a run that stops for its call budget
  retires nothing, and the same merge run again finishes it;
- merges refuse what would change what quantities mean (different unit
  types; units of different sizes); a unit still in use can't be deleted;
- a supplier merge is Loaded's own (one call), an email edit keeps the other
  emails (Loaded's are replace-all);
- the route runs only the page's ops, at a venue the person may open;
- a list request body (a JSON Patch) reaches the wire.
"""

from __future__ import annotations

import copy

import pytest

from tests import consolidator_harness as harness

STOCK = harness.source("get_stock")
ITEMS = harness.source("manage_stock_item")
SUPPLIERS = harness.source("manage_supplier")
VENUE = "La Zeppa"


def _id(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


LEMON, LEMONS, JUICE = _id(1), _id(2), _id(3)
EACH, EA, DOZEN, KILO, BOX, MASTER = (
    _id(10),
    _id(11),
    _id(12),
    _id(13),
    _id(14),
    _id(15),
)
FRESH, GREEN, BLUE = _id(20), _id(21), _id(22)
EMPTY = "00000000-0000-0000-0000-000000000000"


class Stop(Exception):
    """A preview run stopping at the tool's preview(...)."""


def _previewing(card, basis=None):
    raise Stop(card() if callable(card) else card)


class FakeLoaded:
    """Just enough of Loaded's stock API and older web API, with state."""

    def __init__(self, invoices: int = 1):
        def item(iid, name, unit_type, variants, unit=EACH):
            return {
                "id": iid,
                "name": name,
                "unitType": unit_type,
                "groupId": "g",
                "groupName": "Fruit",
                "countingUnitId": unit,
                "countingUnitName": "Each",
                "countingUnitRatio": 1.0,
                "orderingUnitId": unit,
                "orderingUnitName": "Each",
                "orderingUnitRatio": 1.0,
                "minimumStockOnHandUnitId": None,
                "defaultSupplierId": FRESH,
                "globalSalesTaxSortOrder": 1,
                "systemItem": False,
                "datestampRemoved": None,
                "sharedItemId": EMPTY,
                "suppliers": variants,
            }

        self.items = {
            LEMON: {
                **item(
                    LEMON, "Lemon", 2, [self._v(_id(100), LEMON, FRESH, "LEM", EACH)]
                ),
                "sharedItemId": _id(99),  # shared with another Loaded company
            },
            LEMONS: item(
                LEMONS, "Lemons", 2, [self._v(_id(101), LEMONS, GREEN, "LMS", EACH)]
            ),
            JUICE: item(JUICE, "Lemon juice", 1, []),
        }
        self.retired_list = [  # Loaded's older list: retired items, no variants
            {
                "id": _id(9),
                "name": "Old lime",
                "groupName": "Fruit",
                "datestampRemoved": "2025-01-01T00:00:00+00:00",
            },
        ]
        self.units = {
            EACH: {
                "id": EACH,
                "name": "Each",
                "stockUnitType": "Count",
                "ratio": 1.0,
                "datestampDeleted": None,
            },
            EA: {
                "id": EA,
                "name": "EA",
                "stockUnitType": "Count",
                "ratio": 1.0,
                "datestampDeleted": None,
            },
            DOZEN: {
                "id": DOZEN,
                "name": "12 Pack",
                "stockUnitType": "Count",
                "ratio": 12.0,
                "datestampDeleted": None,
            },
            KILO: {
                "id": KILO,
                "name": "Kilo",
                "stockUnitType": "Weight",
                "ratio": 1.0,
                "datestampDeleted": None,
            },
            # Norm's mark for a unit it hides (invoice_units.HIDDEN_UNIT_MARK).
            BOX: {
                "id": BOX,
                "name": "Box",
                "stockUnitType": "Count",
                "ratio": 1.0,
                "datestampDeleted": "1970-01-01T00:00:00+00:00",
            },
            MASTER: {
                "id": MASTER,
                "name": "Litre",
                "stockUnitType": "Volume",
                "ratio": 1.0,
                "datestampDeleted": None,
                "masterId": _id(98),  # one of Loaded's standard units
            },
        }
        self.masters = {MASTER}
        self.suppliers = [
            {
                "id": FRESH,
                "name": "Fresh Co",
                "removedAt": None,
                "emails": [
                    {"id": "e1", "email": "a@fresh.co", "isPrimary": True},
                    {"id": "e2", "email": "b@fresh.co", "isPrimary": False},
                ],
            },
            {"id": GREEN, "name": "Green Grocer", "removedAt": None, "emails": []},
            {
                "id": BLUE,
                "name": "Blue Ltd",
                "removedAt": "2024-01-01T00:00:00Z",
                "emails": [],
            },
        ]
        self.invoices = {
            _id(500 + n): {
                "id": _id(500 + n),
                "referenceNumber": f"INV{n}",
                "isReceived": True,
                "receivedAt": "2026-10-01T00:00:00Z",
                "lines": [
                    {"id": _id(600 + n), "linkedItemId": LEMONS, "deletedAt": None}
                ],
            }
            for n in range(invoices)
        }
        self.orders = {
            _id(700): {
                "id": _id(700),
                "orderNumber": "PO7",
                "isReceived": False,
                "supplierId": GREEN,
                "lines": [
                    {
                        "id": _id(701),
                        "itemId": LEMONS,
                        "itemName": "Lemons",
                        "unitId": EACH,
                    }
                ],
            },
        }
        self.recipes = [
            {
                "id": _id(800),
                "name": "Lemon tart",
                "isShared": False,
                "deletedAt": None,
                "versions": [
                    {
                        "id": _id(801),
                        "yieldUnitId": EACH,
                        "lines": [
                            {
                                "id": _id(802),
                                "itemId": LEMONS,
                                "unitId": EACH,
                                "quantity": 2,
                                "deletedAt": None,
                            },
                            {
                                "id": _id(803),
                                "itemId": JUICE,
                                "unitId": KILO,
                                "quantity": 0.1,
                                "deletedAt": None,
                            },
                        ],
                    }
                ],
            },
        ]
        self.links = [
            {
                "id": _id(900),
                "posIdentifier": "pos-1",
                "stockItemId": LEMONS,
                "unitId": EACH,
            }
        ]
        self.writes: list[tuple] = []
        self._n = 1000

    @staticmethod
    def _v(vid, item_id, supplier, code, unit):
        return {
            "id": vid,
            "stockItemId": item_id,
            "supplierId": supplier,
            "stockCode": code,
            "unitId": unit,
            "unitCost": 1.0,
            "defaultForSupplier": True,
        }

    def call_api(self, connector, action, p):
        fn = getattr(self, f"{connector}__{action}", None)
        if fn is None:
            raise AssertionError(f"unexpected call {connector}.{action}")
        return fn(p)

    # ---------------------------------------------------- stock API reads --
    def loadedhub__get_stock_item_any_raw(self, p):
        it = self.items.get(p["item_id"])
        return copy.deepcopy(it) if it else {"error": "404 Not Found"}

    def loadedhub__get_stock_item_full(self, p):
        it = self.items.get(p["item_id"])
        return (
            copy.deepcopy(it) if it and not it["datestampRemoved"] else {"error": "404"}
        )

    def loadedhub__get_stock_items_detail_raw(self, p):
        return [
            copy.deepcopy(i) for i in self.items.values() if not i["datestampRemoved"]
        ]

    def loadedhub__get_stock_items_with_codes(self, p):
        return copy.deepcopy(self.retired_list) + [
            {**copy.deepcopy(i), "suppliers": None} for i in self.items.values()
        ]

    def loadedhub__get_stock_units(self, p):
        return [copy.deepcopy(u) for u in self.units.values()]

    def loadedhub__get_suppliers(self, p):
        return copy.deepcopy(self.suppliers)

    def loadedhub__get_stock_item_groups(self, p):
        return [{"id": "g", "name": "Fruit", "categoryName": "Food"}]

    def loadedhub__get_received_for_item_raw(self, p):
        return [
            {
                "id": d["id"],
                "invoiceNumber": d["referenceNumber"],
                "receivedAt": d["receivedAt"],
                "lines": [{"itemId": ln["linkedItemId"]} for ln in d["lines"]],
            }
            for d in self.invoices.values()
            if any(
                ln["linkedItemId"] == p["item_id"] and not ln["deletedAt"]
                for ln in d["lines"]
            )
        ]

    def loadedhub__get_invoice_detail(self, p):
        return copy.deepcopy(self.invoices[p["invoice_id"]])

    def loadedhub__get_purchase_order_detail(self, p):
        return copy.deepcopy(self.orders[p["order_id"]])

    def loadedhub__list_purchase_orders(self, p):
        return [
            {
                "id": o["id"],
                "orderNumber": o["orderNumber"],
                "isReceived": o["isReceived"],
                "supplierId": o["supplierId"],
            }
            for o in self.orders.values()
        ]

    def loadedhub__get_all_recipes(self, p):
        return copy.deepcopy(self.recipes)

    def loadedhub__get_invoiced_for_supplier_raw(self, p):
        return [{"id": "inv-x"}] if p["supplier_id"] == GREEN else []

    def loadedhub__get_supplier_aliases(self, p):
        return [{"id": "a1", "name": "GREEN GROCER LTD"}]

    # --------------------------------------------------- stock API writes --
    def loadedhub__update_invoice_raw(self, p):
        self.writes.append(("invoice", p["invoice_id"]))
        self.invoices[p["invoice_id"]] = copy.deepcopy(p["invoice"])
        return {}

    def loadedhub__move_variant_raw(self, p):
        self.writes.append(("variant", p["variant_id"], p["item_id"]))
        for it in self.items.values():
            for v in list(it["suppliers"]):
                if v["id"] == p["variant_id"]:
                    it["suppliers"].remove(v)
                    self.items[p["item_id"]]["suppliers"].append(
                        {**v, "stockItemId": p["item_id"]}
                    )
                    return {}
        return {"error": "404"}

    def loadedhub__update_variant_unit_raw(self, p):
        self.writes.append(("variant_unit", p["variant_id"], p["unit_id"]))
        for it in self.items.values():
            for v in it["suppliers"]:
                if v["id"] == p["variant_id"]:
                    v["unitId"] = p["unit_id"]
        return {}

    def loadedhub__update_purchase_order_raw(self, p):
        self.writes.append(("order", p["order_id"]))
        self.orders[p["order_id"]] = copy.deepcopy(p["order"])
        return {}

    def loadedhub__update_stock_item_raw(self, p):
        self.writes.append(("item_put", p["item_id"]))
        self.items[p["item_id"]] = copy.deepcopy(p["item"])
        return {}

    def loadedhub__update_supplier_raw(self, p):
        self.writes.append(("supplier", p["supplier_id"]))
        self.suppliers = [
            copy.deepcopy(p["supplier"]) if s["id"] == p["supplier_id"] else s
            for s in self.suppliers
        ]
        return {}

    # ------------------------------------------------- the older web API --
    def loaded_legacy__patch_stock_item(self, p):
        self.writes.append(
            (
                "patch_item",
                p["item_id"],
                tuple((op["path"], op["value"]) for op in p["patch"]),
            )
        )
        it = self.items[p["item_id"]]
        for op in p["patch"]:
            key = op["path"][0].lower() + op["path"][1:]
            it[key] = op["value"]
        return {}

    def loaded_legacy__find_purchase_order_lines(self, p):
        item = p["filter"].split(" eq ")[1]
        return [
            {
                "purchaseOrderId": o["id"],
                # A number, as Loaded sends it: a join over these failed
                # in production on 23 Oct 2026.
                "purchaseOrderOrderNumber": int(o["orderNumber"][2:]),
                "purchaseOrderDatestampReceived": None,
                "purchaseOrderDatestampDeleted": None,
            }
            for o in self.orders.values()
            for ln in o["lines"]
            if ln["itemId"] == item
        ]

    def loaded_legacy__list_pos_item_links(self, p):
        return copy.deepcopy(self.links)

    def loaded_legacy__update_pos_item_link(self, p):
        self.writes.append(("pos_link", p["link_id"]))
        self.links = [
            copy.deepcopy(p["link"]) if ln["id"] == p["link_id"] else ln
            for ln in self.links
        ]
        return {}

    def loaded_legacy__get_recipe(self, p):
        r = next(r for r in self.recipes if r["id"] == p["recipe_id"])
        return {k: v for k, v in r.items() if k != "versions"}

    def loaded_legacy__get_recipe_version(self, p):
        for r in self.recipes:
            for v in r["versions"]:
                if v["id"] == p["version_id"]:
                    return {
                        "id": v["id"],
                        "yieldUnitId": v["yieldUnitId"],
                        "lines": [
                            {
                                "id": ln["id"],
                                "stockItemId": ln["itemId"],
                                "unitId": ln["unitId"],
                                "quantity": ln["quantity"],
                                "currentPrice": 1.0,
                            }
                            for ln in v["lines"]
                        ],
                    }
        return {"error": "404"}

    def loaded_legacy__save_recipe_version(self, p):
        self.writes.append(("recipe", p["version_id"]))
        body = p["body"]
        assert body["recipe"]["name"]  # the recipe rides along, as the web app sends it
        for r in self.recipes:
            for v in r["versions"]:
                if v["id"] == p["version_id"]:
                    old = {ln["id"]: ln for ln in v["lines"]}
                    new_lines = []
                    for ln in body["lines"]:
                        if ln["id"] == EMPTY:
                            self._n += 1
                            new_lines.append(
                                {
                                    "id": _id(self._n),
                                    "itemId": ln["stockItemId"],
                                    "unitId": ln["unitId"],
                                    "quantity": ln["quantity"],
                                    "deletedAt": None,
                                }
                            )
                        else:  # Loaded keeps an existing line's item: only qty/unit move
                            kept = dict(old[ln["id"]])
                            kept.update(
                                {"unitId": ln["unitId"], "quantity": ln["quantity"]}
                            )
                            new_lines.append(kept)
                    v["lines"] = new_lines
                    v["yieldUnitId"] = body.get("yieldUnitId", v["yieldUnitId"])
        return {}

    def loaded_legacy__find_stock_units(self, p):
        uid = p["filter"].split(" eq ")[1]
        u = self.units.get(uid)
        if not u:
            return []
        gone = (
            u["datestampDeleted"]
            if u["datestampDeleted"] != "1970-01-01T00:00:00+00:00"
            else None
        )
        return [
            {
                "id": uid,
                "name": u["name"],
                "unitType": 3,
                "ratio": u["ratio"],
                "datestampDeleted": gone,
                "masterId": "m" if uid in self.masters else None,
            }
        ]

    def loaded_legacy__update_stock_unit(self, p):
        self.writes.append(
            (
                "unit",
                p["unit_id"],
                p["unit"].get("name"),
                p["unit"].get("ratio"),
                bool(p["unit"].get("datestampDeleted")),
            )
        )
        u = self.units[p["unit_id"]]
        u.update(
            {
                "name": p["unit"]["name"],
                "ratio": p["unit"]["ratio"],
                "datestampDeleted": p["unit"]["datestampDeleted"],
            }
        )
        return {}

    def loaded_legacy__list_tenders(self, p):
        return [{"id": "t1", "supplierId": GREEN, "datestampDeleted": None}]

    def loaded_legacy__merge_suppliers(self, p):
        self.writes.append(("merge_suppliers", p["source_id"], p["dest_id"]))
        for s in self.suppliers:
            if s["id"] == p["source_id"]:
                s["removedAt"] = "2026-10-23T00:00:00Z"
        return {}


def _run(code, api, **params):
    return harness.run(code, api, venue=VENUE, **params)


def _preview(code, api, **params):
    with pytest.raises(Stop) as stop:
        harness.call(
            code, {"venue": VENUE, **params}, api.call_api, preview=_previewing
        )
    return stop.value.args[0]


# ------------------------------------------------------------ the read ----


class TestCatalogueRead:
    def test_live_items_with_variants_retired_ones_marked_and_units_as_loaded_has_them(
        self,
    ):
        api = FakeLoaded()
        out = _run(STOCK, api, view="catalogue")
        names = {i["name"]: i for i in out["items"]}
        assert set(names) == {
            "Lemon",
            "Lemons",
            "Lemon juice",
        }  # no retired ones unless asked
        assert names["Lemon"]["variants"][0] | {} == {
            "variant_id": _id(100),
            "supplier_id": FRESH,
            "supplier": "Fresh Co",
            "stock_code": "LEM",
            "unit_id": EACH,
            "unit": "Each",
            "unit_cost": 1.0,
            "default": True,
        }
        units = {u["name"]: u for u in out["units"]}
        # Box is live in Loaded; Norm only hides it (not a measurement).
        assert (
            units["Box"]["deactivated"] is False
            and units["Box"]["hidden_by_norm"] is True
        )
        assert units["Each"]["items"] == 3
        assert units["Litre"]["standard"] is True and units["Each"]["standard"] is False
        assert names["Lemon"]["shared"] is True and names["Lemons"]["shared"] is False
        assert [s["name"] for s in out["suppliers"]] == ["Fresh Co", "Green Grocer"]
        assert out["counts"]["items_deactivated"] == 0

        out = _run(STOCK, api, view="catalogue", include_deleted=True)
        old = next(i for i in out["items"] if i["name"] == "Old lime")
        assert old["deactivated"] is True and old["variants"] == []
        assert (
            next(s for s in out["suppliers"] if s["name"] == "Blue Ltd")["deactivated"]
            is True
        )


# ----------------------------------------------------------- item ops ----


class TestItemMerge:
    def test_everything_moves_then_the_item_is_retired_last(self):
        api = FakeLoaded()
        out = _run(ITEMS, api, op="merge", item_id=LEMONS, into_item_id=LEMON)
        assert out["result"] == "merged", out
        assert out["moved"] == {
            "invoice_lines": 1,
            "invoices": 1,
            "variants": 1,
            "orders": 1,
            "recipe_versions": 1,
            "pos_links": 1,
        }
        assert api.invoices[_id(500)]["lines"][0]["linkedItemId"] == LEMON
        assert [v["stockCode"] for v in api.items[LEMON]["suppliers"]] == ["LEM", "LMS"]
        assert api.orders[_id(700)]["lines"][0]["itemId"] == LEMON
        lines = api.recipes[0]["versions"][0]["lines"]
        # A NEW line for Lemon in the old line's place; the juice line untouched.
        assert [(ln["itemId"], ln["quantity"]) for ln in lines] == [
            (LEMON, 2),
            (JUICE, 0.1),
        ]
        assert lines[0]["id"] != _id(802)
        assert api.links[0]["stockItemId"] == LEMON
        assert api.writes[-1][0] == "patch_item"  # retired last
        assert api.items[LEMONS]["datestampRemoved"]

    def test_a_merge_that_runs_out_of_calls_retires_nothing_and_finishes_when_run_again(
        self,
    ):
        api = FakeLoaded(invoices=120)  # two calls each: more than one run may make
        first = _run(ITEMS, api, op="merge", item_id=LEMONS, into_item_id=LEMON)
        assert first["result"] == "in progress" and first["continue"] is True
        assert 0 < first["remaining"]["invoices"] < 120
        assert not api.items[LEMONS]["datestampRemoved"]
        second = _run(ITEMS, api, op="merge", item_id=LEMONS, into_item_id=LEMON)
        assert second["result"] == "merged", second
        assert all(
            d["lines"][0]["linkedItemId"] == LEMON for d in api.invoices.values()
        )
        assert api.items[LEMONS]["datestampRemoved"]

    def test_a_shared_item_is_never_merged_away_but_may_be_merged_into(self):
        """Merging away retires, and retiring a shared item removes its copies
        in the other Loaded companies. The everything-moves test above merges
        INTO the shared Lemon."""
        api = FakeLoaded()
        out = _run(ITEMS, api, op="merge", item_id=LEMON, into_item_id=LEMONS)
        assert "shared with other companies" in out["error"]
        assert api.writes == []

    def test_items_counted_differently_are_refused_before_anything_is_written(self):
        api = FakeLoaded()
        out = _run(ITEMS, api, op="merge", item_id=JUICE, into_item_id=LEMON)
        assert "volume" in out["error"] and "count" in out["error"]
        assert api.writes == []

    def test_the_card_names_what_moves_and_writes_nothing(self):
        api = FakeLoaded()
        card = _preview(ITEMS, api, op="merge", item_id=LEMONS, into_item_id=LEMON)
        fields = [c["field"] for c in card["changes"]]
        assert "Received invoices" in fields and "Open order 7" in fields
        assert "Recipe · Lemon tart" in fields and fields[-1] == "Lemons"
        assert api.writes == []


class TestItemRetireRestoreUpdate:
    def test_retire_and_restore_patch_only_the_removed_date(self):
        api = FakeLoaded()
        out = _run(ITEMS, api, op="retire", item_id=LEMONS)
        assert out["result"] == "retired"
        assert (
            api.writes[0][0] == "patch_item"
            and api.writes[0][2][0][0] == "DatestampRemoved"
        )
        out = _run(ITEMS, api, op="restore", item_id=LEMONS)
        assert out["result"] == "restored"
        assert api.items[LEMONS]["datestampRemoved"] is None
        # A shared item is refused: its copies in other companies would go too.
        assert "shared" in _run(ITEMS, api, op="retire", item_id=LEMON)["error"]

    def test_retire_warns_about_what_still_uses_the_item(self):
        card = _preview(ITEMS, FakeLoaded(), op="retire", item_id=LEMONS)
        text = " ".join(card["warnings"])
        assert (
            "1 recipe(s) use it (Lemon tart)" in text
            and "merge it into another item" in text
        )
        assert "1 open order(s) include it: 7" in text

    def test_update_can_remove_a_variant(self):
        api = FakeLoaded()
        out = _run(ITEMS, api, op="update", item_id=LEMON, remove_variants=[_id(100)])
        assert out["result"] == "updated", out
        assert api.items[LEMON]["suppliers"] == []


# ----------------------------------------------------------- unit ops ----


class TestUnits:
    def test_a_unit_in_use_cant_be_deleted(self):
        api = FakeLoaded()
        out = _run(ITEMS, api, op="delete_unit", unit_id=EACH)
        assert "still in use" in out["error"] and "merge_units" in out["error"]
        assert api.writes == []

    def test_an_unused_unit_is_deleted_and_restored(self):
        api = FakeLoaded()
        assert _run(ITEMS, api, op="delete_unit", unit_id=DOZEN)["result"] == "deleted"
        assert (
            _run(ITEMS, api, op="restore_unit", unit_id=DOZEN)["result"] == "restored"
        )
        assert [w[4] for w in api.writes] == [True, False]

    def test_merge_needs_the_same_kind_and_size(self):
        api = FakeLoaded()
        out = _run(ITEMS, api, op="merge_units", unit_id=DOZEN, into_unit_id=EACH)
        assert "same size" in out["error"]
        assert api.writes == []

    def test_merge_moves_items_variants_orders_recipes_links_then_deletes(self):
        api = FakeLoaded()
        out = _run(ITEMS, api, op="merge_units", unit_id=EACH, into_unit_id=EA)
        assert out["result"] == "merged", out
        assert (
            api.items[LEMON]["countingUnitId"] == EA
            and api.items[LEMON]["orderingUnitId"] == EA
        )
        assert all(
            v["unitId"] == EA for i in api.items.values() for v in i["suppliers"]
        )
        assert api.orders[_id(700)]["lines"][0]["unitId"] == EA
        version = api.recipes[0]["versions"][0]
        assert version["lines"][0]["unitId"] == EA and version["lines"][0]["id"] == _id(
            802
        )
        assert version["yieldUnitId"] == EA
        assert api.links[0]["unitId"] == EA
        assert api.writes[-1][0] == "unit" and api.writes[-1][4] is True

    def test_a_standard_unit_is_never_changed(self):
        out = _run(ITEMS, FakeLoaded(), op="update_unit", unit_id=MASTER, name="Litres")
        assert "standard units" in out["error"]

    def test_a_rename_onto_another_units_name_is_refused(self):
        out = _run(ITEMS, FakeLoaded(), op="update_unit", unit_id=EA, name=" each ")
        assert "already called" in out["error"]


# ------------------------------------------------------- supplier ops ----


class TestSuppliers:
    def test_an_email_edit_keeps_the_other_emails(self):
        api = FakeLoaded()
        out = _run(
            SUPPLIERS,
            api,
            op="update",
            supplier="Fresh Co",
            changes={"email": "orders@fresh.co", "phone": "021"},
        )
        assert out["result"] == "updated", out
        fresh = api.suppliers[0]
        assert [e["email"] for e in fresh["emails"]] == [
            "orders@fresh.co",
            "b@fresh.co",
        ]
        assert fresh["phone1"] == "021"

    def test_renaming_onto_another_supplier_is_refused(self):
        out = _run(
            SUPPLIERS,
            FakeLoaded(),
            op="update",
            supplier="Fresh Co",
            changes={"name": "green grocer"},
        )
        assert "merge the two instead" in out["error"]

    def test_retire_says_which_items_buy_from_it_and_restore_finds_retired_ones(self):
        card = _preview(SUPPLIERS, FakeLoaded(), op="retire", supplier="Green Grocer")
        assert "1 item(s) buy from Green Grocer (Lemons)" in card["warnings"][0]
        api = FakeLoaded()
        assert (
            _run(SUPPLIERS, api, op="restore", supplier="Blue Ltd")["result"]
            == "restored"
        )
        assert api.suppliers[2]["removedAt"] is None

    def test_merge_is_loadeds_own_merge(self):
        api = FakeLoaded()
        card = _preview(
            SUPPLIERS,
            api,
            op="merge",
            supplier="Green Grocer",
            into_supplier="Fresh Co",
        )
        assert any("tender" in w for w in card["warnings"]) and any(
            "alias" in w for w in card["warnings"]
        )
        out = _run(
            SUPPLIERS,
            api,
            op="merge",
            supplier="Green Grocer",
            into_supplier="Fresh Co",
        )
        assert out["result"] == "merged", out
        assert api.writes == [("merge_suppliers", GREEN, FRESH)]
        assert _run(
            SUPPLIERS, api, op="merge", supplier="Fresh Co", into_supplier="Fresh Co"
        )["error"]


# ----------------------------------------------------------- the route ----


class TestRoute:
    def test_only_the_pages_ops_run(self, client, admin_headers, venue, monkeypatch):
        from app.services import previews

        monkeypatch.setattr(
            previews, "preview_call", lambda *a, **k: pytest.fail("ran")
        )
        r = client.post(
            "/api/stock-catalogue/preview",
            json={
                "venue_id": venue.id,
                "tool": "manage_stock_item",
                "params": {"op": "create"},
            },
            headers=admin_headers,
        )
        assert r.status_code == 400

    def test_a_venue_the_person_cant_open_is_refused(
        self, client, manager_headers, venue
    ):
        r = client.post(
            "/api/stock-catalogue/preview",
            json={
                "venue_id": venue.id,
                "tool": "manage_supplier",
                "params": {"op": "retire", "supplier": "x"},
            },
            headers=manager_headers,
        )
        assert r.status_code == 403

    def test_run_checks_the_fingerprint_and_is_recorded(
        self, client, admin_headers, venue, db_session, monkeypatch
    ):
        import app.routers.stock_catalogue as route
        from app.agents import internal_tools
        from app.db.models import Approval

        monkeypatch.setattr(
            route,
            "_tool_def",
            lambda db, action: {
                "action": action,
                "consolidator_config": {"function_code": "x"},
            },
        )
        seen = {}

        def fake_execute(cfg, params, db, thread_id):
            seen.update(cfg=cfg, params=params)
            return {"success": True, "data": {"result": "retired", "item": "Lemons"}}

        monkeypatch.setattr(internal_tools, "execute_consolidator", fake_execute)
        r = client.post(
            "/api/stock-catalogue/run",
            json={
                "venue_id": venue.id,
                "tool": "manage_stock_item",
                "params": {"op": "retire", "item_id": LEMONS, "venue": "elsewhere"},
                "fingerprint": "fp1",
            },
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert seen["cfg"]["expect_fingerprint"] == "fp1"
        assert seen["params"]["venue"] == venue.name  # the page's venue wins
        note = (
            db_session.query(Approval)
            .filter(Approval.action == "card_write")
            .one()
            .notes
        )
        assert "manage_stock_item retire" in note


# ------------------------------------------------------ the plumbing ----


def test_a_list_body_reaches_the_wire(monkeypatch):
    """A JSON Patch is a list; until Oct 2026 a list body was dropped and the
    request went out empty (Loaded answered 500 to the item retire)."""
    from app.connectors import http_pool, spec_executor

    sent = {}

    class Client:
        def request(self, **kw):
            sent.update(kw)

            class R:
                status_code = 204
                headers: dict = {}
                content = b""
                text = ""

                def json(self):
                    raise ValueError

            return R()

    monkeypatch.setattr(http_pool, "get_client", lambda: Client())
    body = [{"op": "replace", "path": "DatestampRemoved", "value": None}]
    rendered = spec_executor.RenderedRequest(
        method="PATCH", url="https://x/y", headers={}, body=body
    )
    spec_executor.execute_http(rendered, {"success_status_codes": [204]})
    assert sent["json"] == body


def test_loaded_legacy_carries_the_catalogue_endpoints():
    from app.connectors import loaded_legacy

    actions = {e["action"] for e in loaded_legacy.SPEC["endpoints"]}
    assert {
        "patch_stock_item",
        "find_purchase_order_lines",
        "update_pos_item_link",
        "find_stock_units",
        "update_stock_unit",
        "merge_suppliers",
    } <= actions


# ------------------------------------------------------ background merges ----


from tests.test_background_jobs import sessions  # noqa: E402,F401 — the fixture


@pytest.mark.usefixtures("sessions")
class TestBackgroundMerge:
    """A merge runs as a background job, pass after pass, so it finishes even
    if the page is closed (owner, 23 Oct 2026: like the invoice batches)."""

    def _passes(self, monkeypatch, results):
        from app.services import catalogue_job

        calls = []

        def fake_run_once(params, fingerprint):
            calls.append(fingerprint)
            return results[len(calls) - 1]

        monkeypatch.setattr(catalogue_job, "_run_once", fake_run_once)
        monkeypatch.setattr(catalogue_job, "email_summary", lambda job_id: None)
        return calls

    def _start(self, db_session, admin_user, venue):
        from app.services import catalogue_job

        job, created = catalogue_job.enqueue(
            db_session,
            user=admin_user,
            venue=venue,
            tool="manage_stock_item",
            tool_params={"op": "merge", "item_id": LEMONS, "into_item_id": LEMON},
            fingerprint="fp1",
            title="Merge Lemons into Lemon",
        )
        db_session.flush()
        assert created
        return job

    def test_it_runs_until_done_checking_the_fingerprint_once(
        self, db_session, monkeypatch, admin_user, venue
    ):
        from app.services import background_jobs as bj

        calls = self._passes(
            monkeypatch,
            [
                {
                    "result": "in progress",
                    "moved": {"invoice_lines": 90, "invoices": 90},
                    "remaining": {"invoices": 14},
                },
                {
                    "result": "merged",
                    "moved": {"invoice_lines": 14, "invoices": 14, "variants": 1},
                },
            ],
        )
        job = self._start(db_session, admin_user, venue)
        assert bj.worker_tick("w1")
        db_session.refresh(job)
        assert job.status == "done" and job.result["result"] == "merged"
        assert job.result["moved"] == {
            "invoice_lines": 104,
            "invoices": 104,
            "variants": 1,
        }
        assert calls == ["fp1", None]  # the person's view guards the first pass only

    def test_a_save_that_failed_once_is_tried_again(
        self, db_session, monkeypatch, admin_user, venue
    ):
        """A dropped connection on one invoice left a merge "partly merged"
        on the test company (23 Oct 2026); the next pass finished it."""
        from app.services import background_jobs as bj

        calls = self._passes(
            monkeypatch,
            [
                {
                    "result": "partly merged",
                    "moved": {"invoice_lines": 2},
                    "failed": [{"invoice": "1", "error": "Server disconnected"}],
                    "error": "1 change(s) failed",
                },
                {"result": "merged", "moved": {"invoice_lines": 1}},
            ],
        )
        job = self._start(db_session, admin_user, venue)
        bj.worker_tick("w1")
        db_session.refresh(job)
        assert job.status == "done" and job.result["result"] == "merged"
        assert job.result["moved"] == {"invoice_lines": 3} and len(calls) == 2

    def test_records_that_moved_since_the_check_stop_it_before_anything_moves(
        self, db_session, monkeypatch, admin_user, venue
    ):
        from app.services import background_jobs as bj

        self._passes(monkeypatch, [{"changed": True}])
        job = self._start(db_session, admin_user, venue)
        bj.worker_tick("w1")
        db_session.refresh(job)
        assert job.status == "failed" and "changed since" in job.error

    def test_a_stopped_merge_says_the_old_item_is_still_active(
        self, db_session, monkeypatch, admin_user, venue
    ):
        from app.services import background_jobs as bj
        from app.services import catalogue_job

        calls = self._passes(monkeypatch, [])
        job = self._start(db_session, admin_user, venue)
        job.cancel_requested = True
        job.status = "running"
        job.claimed_by = "w1"
        db_session.flush()
        ctx = bj.JobContext(job.id, "w1", dict(job.params), {})
        bj.run(ctx)
        db_session.refresh(job)
        assert job.status == "cancelled" and calls == []
        assert "still active" in catalogue_job.summary_text(job)

    def test_the_page_hands_a_merge_to_a_job_one_at_a_time(
        self, client, admin_headers, venue, db_session
    ):
        body = {
            "venue_id": venue.id,
            "tool": "manage_stock_item",
            "params": {"op": "merge", "item_id": LEMONS, "into_item_id": LEMON},
            "fingerprint": "fp1",
            "title": "Lemons into Lemon",
        }
        r = client.post("/api/stock-catalogue/run", json=body, headers=admin_headers)
        assert r.status_code == 200, r.text
        job = r.json()["job"]
        assert job["status"] == "queued" and job["title"] == "Merge Lemons into Lemon"
        again = client.post(
            "/api/stock-catalogue/run", json=body, headers=admin_headers
        )
        assert (
            again.status_code == 409
            and "Another merge is running" in again.json()["detail"]
        )
        listed = client.get(
            f"/api/stock-catalogue/jobs?venue_id={venue.id}", headers=admin_headers
        ).json()["jobs"]
        assert [j["id"] for j in listed] == [job["id"]]
