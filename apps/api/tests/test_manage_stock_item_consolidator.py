"""manage_stock_item — one stock write tool: create, update, set_variant_unit.

Exec'd under the REAL sandbox namespace. The facts pinned, each from
production:

- update merges deltas server-side into the whole object Loaded wants back;
  the model never carries or echoes the complete item.
- `changes` / `add_suppliers` / `item` given as JSON STRINGS are read, not
  ignored: on 27 Aug 2026 two updates to CHILLI POWDER MILD reported
  "no differences — nothing written" for exactly this reason.
- `minimumStockOnHand` — the name the old tool's own description taught the
  model — is Loaded's minimumStockOnHandQuantity; it is an alias, not a skip.
- an id that is not 36 characters is refused before the call: Loaded 400'd
  the one update_variant_unit failure ("5f392d87").
"""

import copy
import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE = (_DIR / "manage_stock_item.py").read_text()

VAR1 = "11111111-1111-4111-8111-111111111111"
VAR2 = "22222222-2222-4222-8222-222222222222"
UNIT = "33333333-3333-4333-8333-333333333333"

FULL_ITEM = {
    "id": "item-1",
    "name": "Jim Beam 700ml",
    "groupId": "g-spirits",
    "groupName": "Spirits",
    "countingUnitId": "u-bottle",
    "countingUnitName": "Bottle",
    "countingUnitRatio": 0.7,
    "orderingUnitId": "u-bottle",
    "orderingUnitRatio": 0.7,
    "minimumStockOnHandQuantity": 4,
    "minimumStockOnHandUnitId": "u-bottle",
    "defaultSupplierId": "sup-1",
    "unitType": 1,
    "obscureLoadedField": "must survive updates untouched",
    "suppliers": [
        {
            "id": VAR1,
            "supplierId": "sup-1",
            "stockCode": "JB700",
            "unitId": "u-bottle",
            "unitCost": 44.0,
            "defaultForSupplier": True,
        },
        {
            "id": VAR2,
            "supplierId": "sup-2",
            "stockCode": "X-JB",
            "unitId": "u-bottle",
            "unitCost": 46.5,
            "defaultForSupplier": False,
        },
    ],
}

NEW_ITEM = {
    "name": "MAPLE SYRUP 2L",
    "groupId": "g-dry",
    "unitType": 1,
    "countingUnitId": "u-l",
    "countingUnitRatio": 1,
    "orderingUnitId": "u-2l",
    "orderingUnitRatio": 2,
    "defaultSupplierId": "sup-1",
    "suppliers": [
        {
            "supplierId": "sup-1",
            "stockCode": "62404",
            "unitId": "u-2l",
            "unitCost": 18.5,
            "defaultForSupplier": True,
        }
    ],
}


class Api:
    def __init__(self):
        self.calls = []
        self.put_body = None
        self.post_body = None
        self.patch = None

    def call_api(self, connector, action, params=None):
        p = dict(params or {})
        self.calls.append((action, p))
        if action == "get_stock_item_full":
            return copy.deepcopy(FULL_ITEM)
        if action == "update_stock_item_raw":
            self.put_body = p["item"]
            return {"ok": True}
        if action == "create_stock_item_raw":
            self.post_body = p["item"]
            return {"id": "new-1", "name": p["item"].get("name")}
        if action == "update_variant_unit_raw":
            self.patch = p
            return {"ok": True}
        raise AssertionError(f"unexpected action {action}")


def run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    return ns["run"](
        {"venue": "The Glass Goose", **params}, api.call_api, lambda m: None
    )


class TestOps:
    def test_unknown_or_missing_op_is_refused_without_calling(self):
        api = Api()
        assert "op must be one of" in run(api)["error"]
        assert "op must be one of" in run(api, op="delete")["error"]
        assert api.calls == []


class TestCreate:
    def test_full_item_is_posted_verbatim_and_the_new_id_returned(self):
        api = Api()
        out = run(api, op="create", item=dict(NEW_ITEM))
        assert out == {
            "result": "created",
            "name": "MAPLE SYRUP 2L",
            "item_id": "new-1",
        }
        assert api.post_body["suppliers"][0]["stockCode"] == "62404"
        assert api.post_body["itemType"] == "Default"
        assert len(api.calls) == 1

    def test_a_json_string_item_is_read(self):
        import json

        api = Api()
        out = run(api, op="create", item=json.dumps(NEW_ITEM))
        assert out["result"] == "created"
        assert api.post_body["name"] == "MAPLE SYRUP 2L"

    def test_missing_required_fields_are_refused_before_the_call(self):
        api = Api()
        out = run(api, op="create", item={"name": "X", "groupId": "g"})
        assert "unitType" in out["error"] and "countingUnitId" in out["error"]
        assert api.calls == []

    def test_no_item_is_refused(self):
        assert "needs `item`" in run(Api(), op="create")["error"]
        assert "needs `item`" in run(Api(), op="create", item="not json")["error"]


class TestUpdate:
    def test_deltas_merge_into_the_full_object_server_side(self):
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            changes={"minimumStockOnHandQuantity": 6},
            variant_changes=[{"variant_id": VAR2, "unitCost": 47.0}],
        )
        assert out["result"] == "updated"
        assert out["changed"] == {"minimumStockOnHandQuantity": {"from": 4, "to": 6}}
        assert out["variants_changed"] == 1
        body = api.put_body
        assert body["obscureLoadedField"] == "must survive updates untouched"
        assert body["minimumStockOnHandQuantity"] == 6
        assert body["suppliers"][1]["unitCost"] == 47.0
        assert body["suppliers"][0]["unitCost"] == 44.0
        assert [a for a, _ in api.calls] == [
            "get_stock_item_full",
            "update_stock_item_raw",
        ]

    def test_the_old_descriptions_field_name_is_an_alias(self):
        # update_stock_item told the model {'minimumStockOnHand': 6}; Loaded's
        # field is minimumStockOnHandQuantity and the edit was being skipped.
        api = Api()
        out = run(api, op="update", item_id="item-1", changes={"minimumStockOnHand": 6})
        assert out["changed"] == {"minimumStockOnHandQuantity": {"from": 4, "to": 6}}
        assert api.put_body["minimumStockOnHandQuantity"] == 6
        assert "minimumStockOnHand" not in api.put_body

    def test_json_strings_are_read_not_ignored(self):
        # The 27 Aug 2026 CHILLI POWDER MILD incident: both writes reported
        # "no differences — nothing written".
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            changes='{"defaultSupplierId": "sup-3"}',
            add_suppliers='[{"supplierId": "sup-3", "stockCode": "172884", "unitCost": 8.88, "defaultForSupplier": true}]',
        )
        assert out["result"] == "updated"
        assert out["changed"] == {"defaultSupplierId": {"from": "sup-1", "to": "sup-3"}}
        assert api.put_body["suppliers"][2]["stockCode"] == "172884"

    def test_unreadable_strings_are_an_error_not_a_silent_noop(self):
        api = Api()
        out = run(api, op="update", item_id="item-1", changes="set the min to 6")
        assert "could not be read as JSON" in out["error"]
        assert api.calls == []

    def test_variants_match_by_supplier_and_code_too(self):
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            variant_changes=[
                {"supplier_id": "sup-1", "stock_code": "JB700", "unitCost": 45.0}
            ],
        )
        assert out["variants_changed"] == 1
        assert api.put_body["suppliers"][0]["unitCost"] == 45.0

    def test_unknown_fields_are_refused_with_a_reason(self):
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            changes={"minimumStockOnHandQuantity": 6, "madeUpField": 1, "id": "evil"},
        )
        assert out["skipped"]["madeUpField"] == "Loaded has no such field on this item"
        assert "not editable" in out["skipped"]["id"]
        assert "madeUpField" not in api.put_body and api.put_body["id"] == "item-1"

    def test_add_suppliers_appends_to_the_whole_item_put(self):
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            add_suppliers=[{"supplierId": "sup-3", "stockCode": "NEW1"}],
        )
        assert out["variants_changed"] == 1
        assert api.put_body["suppliers"][2]["stockCode"] == "NEW1"

    def test_no_effective_change_writes_nothing(self):
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            changes={"minimumStockOnHandQuantity": 4},
        )
        assert out["result"].startswith("no differences")
        assert api.put_body is None

    def test_missing_inputs_error_helpfully(self):
        assert "item_id" in run(Api(), op="update")["error"]
        assert "nothing to change" in run(Api(), op="update", item_id="item-1")["error"]


class TestSetVariantUnit:
    def test_full_ids_reach_the_patch(self):
        api = Api()
        out = run(api, op="set_variant_unit", variant_id=VAR1, unit_id=UNIT)
        assert out["result"] == "updated"
        assert api.patch == {
            "venue": "The Glass Goose",
            "variant_id": VAR1,
            "unit_id": UNIT,
        }
        assert len(api.calls) == 1

    def test_short_ids_are_refused_before_the_call(self):
        api = Api()
        out = run(api, op="set_variant_unit", variant_id="5f392d87", unit_id="1f879aac")
        assert "36-character" in out["error"] and "variant_id" in out["error"]
        assert api.calls == []

    def test_missing_ids_are_refused(self):
        assert (
            "needs variant_id and unit_id"
            in run(Api(), op="set_variant_unit", variant_id=VAR1)["error"]
        )
