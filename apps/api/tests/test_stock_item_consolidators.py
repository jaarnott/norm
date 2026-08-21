"""The stock-item consolidators: one lookup surface, delta-only updates.

Exec'd under the REAL sandbox namespace. The facts pinned: the LLM chooses
its data depth (query → slim matches; item_id → summary or full); an update
sends only deltas and the read-merge-write happens server-side — the model
never carries or echoes the complete Loaded object (the old flow fetched
get_stock_item_full and resent the WHOLE item around one edit).
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
READ_CODE = (_DIR / "get_stock_items.py").read_text()
UPDATE_CODE = (_DIR / "update_stock_item.py").read_text()

# Field names as the live endpoint returns them (OIL CANOLA probe,
# 22 Aug 2026): item-level unit/group NAMES ride inline; variants carry ids
# only, which the read consolidator resolves to names.
FULL_ITEM = {
    "id": "item-1",
    "name": "Jim Beam 700ml",
    "groupId": "g-spirits",
    "groupName": "Spirits",
    "countingUnitId": "u-bottle",
    "countingUnitName": "Bottle",
    "countingUnitRatio": 0.7,
    "orderingUnitId": "u-bottle",
    "orderingUnitName": "Bottle",
    "orderingUnitRatio": 0.7,
    "minimumStockOnHandQuantity": 4,
    "minimumStockOnHandUnit": "Bottle",
    "defaultSupplierId": "sup-1",
    "unitType": 1,
    "obscureLoadedField": "must survive updates untouched",
    "suppliers": [
        {
            "id": "var-1",
            "supplierId": "sup-1",
            "stockCode": "JB700",
            "unitId": "u-bottle",
            "unitCost": 44.0,
            "defaultForSupplier": True,
        },
        {
            "id": "var-2",
            "supplierId": "sup-2",
            "stockCode": "X-JB",
            "unitId": "u-bottle",
            "unitCost": 46.5,
            "defaultForSupplier": False,
        },
    ],
}


class Api:
    def __init__(self):
        self.calls = []
        self.put_body = None

    def call_api(self, connector, action, params=None):
        self.calls.append((action, dict(params or {})))
        if action == "get_stock_items_raw":
            return [
                {"id": "item-1", "name": "Jim Beam 700ml"},
                {"id": "item-2", "name": "Jim Beam 1L"},
                {"id": "item-3", "name": "Stella 330ml"},
            ]
        if action == "get_stock_item_full":
            import copy

            return copy.deepcopy(FULL_ITEM)
        if action == "update_stock_item_raw":
            self.put_body = params["item"]
            return {"ok": True}
        if action == "get_stock_units":
            return [{"id": "u-bottle", "name": "Bottle", "ratio": 0.7}]
        if action == "get_suppliers":
            return [
                {"id": "sup-1", "name": "Trents"},
                {"id": "sup-2", "name": "Hancocks"},
            ]
        raise AssertionError(f"unexpected action {action}")


def run(code, api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(code, ns)
    return ns["run"](
        {"venue": "The Glass Goose", **params}, api.call_api, lambda m: None
    )


class TestLookup:
    def test_query_returns_slim_matches_only(self):
        api = Api()
        out = run(READ_CODE, api, query="jim beam")
        assert out["total_matches"] == 2 and out["shown"] == 2
        assert out["matches"][0] == {"id": "item-1", "name": "Jim Beam 700ml"}
        assert "item" not in out  # ambiguous → no auto-detail fetch

    def test_unambiguous_query_includes_the_item_summary(self):
        api = Api()
        out = run(READ_CODE, api, query="stella")
        # one match → the consolidator fetches its detail to save a round trip
        assert out["total_matches"] == 1
        assert out["item"]["variants"][0]["stock_code"] == "JB700"
        assert "obscureLoadedField" not in out["item"]  # summary, not full

    def test_item_id_summary_slims_and_full_returns_everything(self):
        api = Api()
        out = run(READ_CODE, api, item_id="item-1")
        assert out["detail"] == "summary"
        assert out["item"]["minimum_stock_on_hand"] == 4
        assert out["item"]["minimum_stock_unit"] == "Bottle"
        assert "obscureLoadedField" not in out["item"]
        out = run(READ_CODE, Api(), item_id="item-1", detail="full")
        assert out["detail"] == "full"
        assert out["item"]["obscureLoadedField"]

    def test_summaries_carry_names_never_uuids(self):
        # Token doctrine: a unit id is ~10 tokens the model can't reason
        # about; "Bottle" is one it can. The only ids kept are the handles
        # later calls need — the item id and each variant_id.
        out = run(READ_CODE, Api(), item_id="item-1")
        item = out["item"]
        assert item["group"] == "Spirits"
        assert item["counting_unit"] == "Bottle"
        v = item["variants"][0]
        assert v["supplier"] == "Trents"
        assert v["unit"] == "Bottle"
        assert v["default"] is True
        assert v["variant_id"] == "var-1"  # the update handle survives
        assert "supplier_id" not in v and "unit_id" not in v
        assert "group_id" not in item and "counting_unit_id" not in item

    def test_bare_list_carries_the_steering_note(self):
        out = run(READ_CODE, Api())
        assert "query" in out["note"] and out["shown"] == 3


class TestUpdate:
    def test_deltas_merge_into_the_full_object_server_side(self):
        api = Api()
        out = run(
            UPDATE_CODE,
            api,
            item_id="item-1",
            changes={"minimumStockOnHandQuantity": 6},
            variant_changes=[{"variant_id": "var-2", "unitCost": 47.0}],
        )
        assert out["result"] == "updated"
        assert out["changed"] == {"minimumStockOnHandQuantity": {"from": 4, "to": 6}}
        assert out["variants_changed"] == 1
        # the PUT carried the COMPLETE object with only the deltas applied
        body = api.put_body
        assert body["obscureLoadedField"] == "must survive updates untouched"
        assert body["minimumStockOnHandQuantity"] == 6
        assert body["suppliers"][1]["unitCost"] == 47.0
        assert body["suppliers"][0]["unitCost"] == 44.0

    def test_variants_match_by_supplier_and_code_too(self):
        api = Api()
        out = run(
            UPDATE_CODE,
            api,
            item_id="item-1",
            variant_changes=[
                {"supplier_id": "sup-1", "stock_code": "JB700", "unitCost": 45.0}
            ],
        )
        assert out["variants_changed"] == 1
        assert api.put_body["suppliers"][0]["unitCost"] == 45.0

    def test_unknown_fields_are_refused_not_invented(self):
        api = Api()
        out = run(
            UPDATE_CODE,
            api,
            item_id="item-1",
            changes={"minimumStockOnHandQuantity": 6, "madeUpField": 1, "id": "evil"},
        )
        assert "madeUpField" in out["skipped"] and "id" in out["skipped"]
        assert "madeUpField" not in api.put_body

    def test_add_suppliers_appends_to_the_whole_item_put(self):
        api = Api()
        out = run(
            UPDATE_CODE,
            api,
            item_id="item-1",
            add_suppliers=[{"supplierId": "sup-3", "stockCode": "NEW1"}],
        )
        assert out["variants_changed"] == 1
        assert api.put_body["suppliers"][2]["stockCode"] == "NEW1"

    def test_no_effective_change_writes_nothing(self):
        api = Api()
        out = run(
            UPDATE_CODE,
            api,
            item_id="item-1",
            changes={"minimumStockOnHandQuantity": 4},
        )
        assert out["result"].startswith("no differences")
        assert api.put_body is None

    def test_missing_inputs_error_helpfully(self):
        assert "item_id" in run(UPDATE_CODE, Api())["error"]
        out = run(UPDATE_CODE, Api(), item_id="item-1")
        assert "nothing to change" in out["error"]
