"""manage_stock_item — one stock write tool: create, update, set_variant_unit,
fix_received_unit.

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
- a create defaults to GST: without the tax field Loaded filed all four
  Aug 2026 creates at Mr Murdochs as Exempt (consolidator review, 1 Oct).
- a variant field Loaded doesn't have is skipped, not counted as a change;
  a unit needs its ratio; a supplier keeps exactly one default variant;
  an update is read back and anything Loaded didn't keep is reported.
- fix_received_unit (18 Oct 2026, thread bf840172): a Count item received six
  times in a Weight unit priced a bun at $11.25; the op re-PUTs each received
  invoice with ONLY the line's unit fields changed, reads it back, and moves
  the variants out of the old unit — unreceived and reconciled invoices are
  skipped with a reason, never silently.
"""

import copy

from tests import consolidator_harness as harness

CODE = harness.source("manage_stock_item")

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
            # Loaded keeps what was PUT — the read-back sees the write.
            return copy.deepcopy(self.put_body or FULL_ITEM)
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
    return harness.run(CODE, api, **{"venue": "The Glass Goose", **params})


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
            "sales_tax": "GST",
        }
        assert api.post_body["globalSalesTaxSortOrder"] == 1
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
            "get_stock_item_full",  # read back to confirm what Loaded kept
        ]
        assert "not_kept_by_loaded" not in out

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
            add_suppliers='[{"supplierId": "sup-3", "stockCode": "172884", "unitId": "u-bottle", "unitCost": 8.88, "defaultForSupplier": true}]',
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
            add_suppliers=[
                {"supplierId": "sup-3", "stockCode": "NEW1", "unitId": "u-bottle"}
            ],
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


class TestReviewFixes:
    """Consolidator review, 1 Oct 2026."""

    def test_an_explicit_exempt_item_stays_exempt(self):
        api = Api()
        out = run(api, op="create", item={**NEW_ITEM, "globalSalesTaxSortOrder": 0})
        assert api.post_body["globalSalesTaxSortOrder"] == 0
        assert out["sales_tax"] == "sort order 0"

    def test_create_refuses_a_variant_without_a_unit_or_with_two_defaults(self):
        api = Api()
        no_unit = {**NEW_ITEM, "suppliers": [{"supplierId": "sup-1", "stockCode": "1"}]}
        assert "needs unitId" in run(api, op="create", item=no_unit)["error"]
        two = dict(NEW_ITEM["suppliers"][0])
        twice = {**NEW_ITEM, "suppliers": [two, {**two, "stockCode": "62405"}]}
        assert "2 default variants" in run(api, op="create", item=twice)["error"]
        assert api.post_body is None

    def test_an_unknown_variant_field_is_skipped_not_counted(self):
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            variant_changes=[{"variant_id": VAR2, "colour": "red"}],
        )
        assert out["result"] == "no differences — nothing written"
        assert "no such field" in out["skipped"][f"variant {VAR2}.colour"]
        assert api.put_body is None

    def test_snake_case_variant_fields_are_aliases(self):
        api = Api()
        out = run(
            api,
            op="update",
            item_id="item-1",
            variant_changes=[{"variant_id": VAR2, "unit_cost": 47.0}],
        )
        assert out["result"] == "updated"
        assert api.put_body["suppliers"][1]["unitCost"] == 47.0
        assert "unit_cost" not in api.put_body["suppliers"][1]

    def test_a_unit_without_its_ratio_is_refused(self):
        api = Api()
        out = run(
            api, op="update", item_id="item-1", changes={"countingUnitId": "u-each"}
        )
        assert "countingUnitId needs countingUnitRatio" in out["error"]
        assert api.put_body is None
        ok = run(
            api,
            op="update",
            item_id="item-1",
            changes={"countingUnitId": "u-each", "countingUnitRatio": 1},
        )
        assert ok["result"] == "updated"

    def test_a_supplier_keeps_exactly_one_default_variant(self):
        api = Api()
        second = run(
            api,
            op="update",
            item_id="item-1",
            add_suppliers=[
                {
                    "supplierId": "sup-1",
                    "stockCode": "JB700B",
                    "unitId": "u-bottle",
                    "defaultForSupplier": True,
                }
            ],
        )
        assert "2 default variants" in second["error"]
        none_left = run(
            api,
            op="update",
            item_id="item-1",
            variant_changes=[{"variant_id": VAR1, "defaultForSupplier": False}],
        )
        assert "no default variant" in none_left["error"]
        assert api.put_body is None
        # moving the default within a supplier, in one call, is fine
        moved = run(
            api,
            op="update",
            item_id="item-1",
            add_suppliers=[
                {
                    "supplierId": "sup-1",
                    "stockCode": "JB700B",
                    "unitId": "u-bottle",
                    "defaultForSupplier": True,
                }
            ],
            variant_changes=[{"variant_id": VAR1, "defaultForSupplier": False}],
        )
        assert moved["result"] == "updated"

    def test_what_loaded_did_not_keep_is_reported(self):
        class Forgetful(Api):
            def call_api(self, connector, action, params=None):
                if action == "get_stock_item_full" and self.put_body is not None:
                    self.calls.append((action, dict(params or {})))
                    return copy.deepcopy(FULL_ITEM)  # the write didn't stick
                return super().call_api(connector, action, params)

        api = Forgetful()
        out = run(
            api,
            op="update",
            item_id="item-1",
            changes={"minimumStockOnHandQuantity": 9},
            add_suppliers=[
                {"supplierId": "sup-3", "stockCode": "NEW1", "unitId": "u-bottle"}
            ],
        )
        assert out["not_kept_by_loaded"] == [
            "minimumStockOnHandQuantity",
            "new variant sup-3/NEW1",
        ]
        assert out["result"].startswith("updated — but Loaded did not keep")
        assert [a for a, _ in api.calls] == [
            "get_stock_item_full",
            "update_stock_item_raw",
            "get_stock_item_full",
        ]


# ------------------------------------------------------- fix_received_unit ----

ITEM_ID = "1eeee634-b848-4b84-a733-62696afb1c0f"
EACH = "54db3be3-3274-4f6b-9ca0-eedae3b40a28"
GRAMS80 = "c54e5f20-471b-4595-8b92-fb5a455e1ca3"
KILO = "78b83be4-099a-4b0a-8ac2-e223073764a3"
BUN_VAR1 = "834915dc-a186-4461-98d9-08df2bb4bf94"
BUN_VAR2 = "88c9daf9-30d2-405b-98da-08df2bb4bf94"
INV_A = "38fa479b-53c2-41c2-6e00-08dee1dab56a"
INV_B = "4b4b4b4b-4b4b-4b4b-8b4b-4b4b4b4b4b4b"
INV_OPEN = "5c5c5c5c-5c5c-4c5c-8c5c-5c5c5c5c5c5c"
INV_RECON = "6d6d6d6d-6d6d-4d6d-8d6d-6d6d6d6d6d6d"
INV_EACH = "7e7e7e7e-7e7e-4e7e-8e7e-7e7e7e7e7e7e"

UNITS = [
    {"id": EACH, "name": "Each", "ratio": 1.0},
    {"id": GRAMS80, "name": "80 Grams", "ratio": 0.08},
    {"id": KILO, "name": "Kilo", "ratio": 1.0},
]

BUN_ITEM = {
    "id": ITEM_ID,
    "name": "80g Potato Bun",
    "unitType": 2,
    "countingUnitId": EACH,
    "suppliers": [
        {"id": BUN_VAR1, "supplierId": "sup-mhl", "unitId": GRAMS80, "unitCost": 0.9},
        {
            "id": BUN_VAR2,
            "supplierId": "sup-mhl",
            "stockCode": "BAK3007",
            "unitId": GRAMS80,
            "unitCost": 0.9,
            "defaultForSupplier": True,
        },
    ],
}


def _detail(inv_id, number, received=True, reconciled=False, bun_unit=GRAMS80):
    bun_name = {GRAMS80: "80 Grams", EACH: "Each"}[bun_unit]
    return {
        "id": inv_id,
        "referenceNumber": number,
        "isReceived": received,
        "reconciled": reconciled,
        "issuedAt": "2026-07-21T00:00:00",
        "subtotal": 118.0,
        "taxAmount": 11.8,
        "total": 129.8,
        "lines": [
            {
                "id": "l-bun",
                "description": "80g Potato Bun",
                "unit": bun_name,
                "quantityOrdered": 20,
                "quantityReceived": 20,
                "unitCostExclTax": 0.9,
                "totalCostExclTax": 18.0,
                "linkedUnitId": bun_unit,
                "linkedUnitRatio": 0.08 if bun_unit == GRAMS80 else 1.0,
                "linkedItemId": ITEM_ID,
            },
            {
                "id": "l-bacon",
                "description": "Bacon",
                "unit": "Kilo",
                "quantityReceived": 5,
                "unitCostExclTax": 20.0,
                "totalCostExclTax": 100.0,
                "linkedUnitId": KILO,
                "linkedUnitRatio": 1.0,
                "linkedItemId": "item-bacon",
            },
        ],
    }


def _feed_row(inv_id, item_id=ITEM_ID, unit_name="80 Grams"):
    return {
        "id": inv_id,
        "lines": [{"itemId": item_id, "unitName": unit_name, "quantityReceived": 20}],
    }


class InvoiceApi(Api):
    """Loaded keeps what was PUT; the read-back sees the write."""

    def __init__(self, details=None, feed=None):
        super().__init__()
        self.details = details or {
            INV_A: _detail(INV_A, "OMO1060033"),
            INV_B: _detail(INV_B, "OMO1044189"),
            INV_OPEN: _detail(INV_OPEN, "OMO1070000", received=False),
            INV_RECON: _detail(INV_RECON, "OMO1035255", reconciled=True),
            INV_EACH: _detail(INV_EACH, "OMO1017922", bun_unit=EACH),
        }
        self.feed = (
            feed
            if feed is not None
            else [
                _feed_row(INV_A),
                _feed_row(INV_B),
                _feed_row(INV_EACH, unit_name="Each"),  # already right
                _feed_row("other", item_id="item-bacon", unit_name="Kilo"),
            ]
        )
        self.puts = []
        self.patches = []
        self.window = None

    def call_api(self, connector, action, params=None):
        p = dict(params or {})
        if connector == "norm" and action == "resolve_dates":
            self.calls.append((action, p))
            return {
                "window": {
                    "start": "2025-10-18T07:00:00+11:00",
                    "end": "2026-10-18T06:59:59+11:00",
                }
            }
        if action == "get_stock_units":
            self.calls.append((action, p))
            return copy.deepcopy(UNITS)
        if action == "get_suppliers":
            self.calls.append((action, p))
            return [{"id": "sup-mhl", "name": "M H Luxe"}]
        if action == "get_stock_item_full":
            self.calls.append((action, p))
            return copy.deepcopy(BUN_ITEM)
        if action == "get_received_invoices":
            self.calls.append((action, p))
            self.window = (p.get("from"), p.get("to"))
            return copy.deepcopy(self.feed)
        if action == "get_invoice_detail":
            self.calls.append((action, p))
            d = self.details.get(p.get("invoice_id"))
            return copy.deepcopy(d) if d else {"error": "404 not found"}
        if action == "update_invoice_raw":
            self.calls.append((action, p))
            self.puts.append(p)
            self.details[p["invoice_id"]] = copy.deepcopy(p["invoice"])
            return {"ok": True}
        if action == "update_variant_unit_raw":
            self.calls.append((action, p))
            self.patches.append(p)
            return {"ok": True}
        return super().call_api(connector, action, params)


def fix(api, **params):
    return run(api, op="fix_received_unit", item_id=ITEM_ID, unit_id=EACH, **params)


class TestFixReceivedUnit:
    def test_the_period_path_rewrites_only_the_unit_fields(self):
        api = InvoiceApi()
        out = fix(api, period="last 12 months")
        assert out["result"] == "updated"
        # The feed decides which invoices: the two in "80 Grams" — not the one
        # already in Each, not the bacon-only invoice.
        assert api.window == ("2025-10-18T07:00:00+11:00", "2026-10-18T06:59:59+11:00")
        assert [p["invoice_id"] for p in api.puts] == [INV_A, INV_B]
        body = api.puts[0]["invoice"]
        bun, bacon = body["lines"]
        assert bun["unit"] == "Each"
        assert bun["linkedUnitId"] == EACH
        assert bun["linkedUnitRatio"] == 1.0
        # the money does not move — the price per base unit does
        assert (bun["quantityReceived"], bun["unitCostExclTax"]) == (20, 0.9)
        assert bun["totalCostExclTax"] == 18.0
        assert bacon == _detail(INV_A, "OMO1060033")["lines"][1]
        assert (body["subtotal"], body["taxAmount"], body["total"]) == (
            118.0,
            11.8,
            129.8,
        )
        assert body["isReceived"] is True
        # read back: still received, totals add up, the line is in the unit
        assert out["invoices"] == [
            {
                "invoice": "OMO1060033",
                "date": "2026-07-21",
                "lines_changed": 1,
                "still_received": True,
                "adds_up": True,
                "lines_now_in_unit": 1,
            },
            {
                "invoice": "OMO1044189",
                "date": "2026-07-21",
                "lines_changed": 1,
                "still_received": True,
                "adds_up": True,
                "lines_now_in_unit": 1,
            },
        ]
        # both variants in the old unit follow, so the next receive is right
        assert [(p["variant_id"], p["unit_id"]) for p in api.patches] == [
            (BUN_VAR1, EACH),
            (BUN_VAR2, EACH),
        ]
        assert (
            out["variants"][1]["supplier"] == "Supplier BAK3007"
        )  # no names: not previewing
        assert out["skipped"] == []
        actions = [a for a, _ in api.calls]
        assert actions[:4] == [
            "get_stock_units",
            "get_stock_item_full",
            "resolve_dates",
            "get_received_invoices",
        ]
        assert "get_suppliers" not in actions  # names only while previewing

    def test_explicit_ids_skip_open_and_reconciled_invoices_with_a_reason(self):
        api = InvoiceApi()
        out = fix(api, invoice_ids=[INV_A, INV_OPEN, INV_RECON, INV_EACH, "nope"])
        assert [p["invoice_id"] for p in api.puts] == [INV_A]
        assert out["skipped"] == [
            {
                "invoice": "OMO1070000",
                "why": "not received — open it with the receive tool instead",
            },
            {
                "invoice": "OMO1035255",
                "why": "reconciled against a statement — pass include_reconciled to change it",
            },
            {"invoice": "OMO1017922", "why": "no line of this item in another unit"},
            {"invoice_id": "nope", "why": "404 not found"},
        ]
        assert "resolve_dates" not in [a for a, _ in api.calls]

    def test_include_reconciled_writes_the_reconciled_one_too(self):
        api = InvoiceApi()
        out = fix(api, invoice_ids=f"{INV_A},{INV_RECON}", include_reconciled=True)
        assert [p["invoice_id"] for p in api.puts] == [INV_A, INV_RECON]
        assert out["skipped"] == []

    def test_from_unit_by_name_limits_lines_and_variants(self):
        api = InvoiceApi()
        out = fix(api, invoice_ids=[INV_A], from_unit="Kilo")
        # the bun lines are in 80 Grams, the variants too: nothing in Kilo
        assert out["result"] == "nothing to fix"
        assert api.puts == [] and api.patches == []
        assert "no line of this item" in out["skipped"][0]["why"]
        api = InvoiceApi()
        out = fix(api, invoice_ids=[INV_A], from_unit="80 grams")
        assert [p["invoice_id"] for p in api.puts] == [INV_A]
        assert len(api.patches) == 2
        bad = fix(InvoiceApi(), from_unit="Pallet")
        assert "not one of this venue's units" in bad["error"]

    def test_nothing_in_the_period_writes_nothing(self):
        api = InvoiceApi(feed=[_feed_row(INV_EACH, unit_name="Each")])
        out = fix(api, period="last month")
        assert out["result"] == "nothing to fix"
        assert "no received invoice in the period" in out["note"]
        assert api.puts == [] and api.patches == []

    def test_also_variants_false_leaves_the_variants(self):
        api = InvoiceApi()
        out = fix(api, invoice_ids=[INV_A], also_variants=False)
        assert out["result"] == "updated"
        assert api.patches == [] and out["variants"] == []

    def test_short_or_missing_ids_are_refused_before_any_call(self):
        api = InvoiceApi()
        assert (
            "36-character"
            in run(api, op="fix_received_unit", item_id="1eeee634")["error"]
        )
        assert (
            "36-character" in run(api, op="fix_received_unit", item_id=ITEM_ID)["error"]
        )
        assert api.calls == []
        unknown = run(api, op="fix_received_unit", item_id=ITEM_ID, unit_id=INV_A)
        assert "not one of this venue's units" in unknown["error"]

    def test_a_read_back_that_lost_the_change_is_flagged(self):
        class Forgetful(InvoiceApi):
            def call_api(self, connector, action, params=None):
                if action == "update_invoice_raw":
                    self.calls.append((action, dict(params or {})))
                    self.puts.append(dict(params or {}))
                    return {"ok": True}  # but Loaded kept the old detail
                return super().call_api(connector, action, params)

        api = Forgetful()
        out = fix(api, invoice_ids=[INV_A])
        entry = out["invoices"][0]
        assert entry["lines_now_in_unit"] == 0
        assert "check it" in entry["warning"]

    def test_a_failed_put_is_reported_per_invoice_and_the_rest_continue(self):
        class Flaky(InvoiceApi):
            def call_api(self, connector, action, params=None):
                if action == "update_invoice_raw" and params["invoice_id"] == INV_A:
                    self.calls.append((action, dict(params or {})))
                    return {"error": "500 from Loaded"}
                return super().call_api(connector, action, params)

        api = Flaky()
        out = fix(api, invoice_ids=[INV_A, INV_B])
        assert out["invoices"][0] == {
            "invoice": "OMO1060033",
            "date": "2026-07-21",
            "lines_changed": 1,
            "error": "500 from Loaded",
        }
        assert out["invoices"][1]["still_received"] is True
        assert [p["invoice_id"] for p in api.puts] == [INV_B]
