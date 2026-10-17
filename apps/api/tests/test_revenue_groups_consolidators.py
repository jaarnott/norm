"""set_revenue_group — the one tool that puts POS items in Food, Beverage, Other.

The read is get_sales' items breakdown (each row's group and category); this
is the write.

Exec'd under the REAL sandbox namespace against the canonical files. Items are
shaped like Loaded's GET /api/pos/items (read live for six NZ venues, 17 Oct
2026): {posIdentifier, name, price, priceEx, itemGroupName, itemCategoryName,
revenueGroup, whenDeleted}. Loaded's write, checked on its test environment:
PUT /api/pos/items/revenue-group {"posItemIds", "revenueGroupName"} — null
clears; an unknown id is a bare 500 with the ids before it already saved; an
item in a group reads its group AND category as the group's name.

Facts pinned:
- a name moves every live item with that name (the same dish at three prices);
- any name that matches nothing writes nothing and comes back with the closest;
- a category sweep leaves items already in a group alone by default;
- several groups are one call (one approval), written one PUT per group;
- ids go in chunks of 200, and the read-back reports what Loaded didn't keep;
- the approval preview shows every row and stops before writing; the approved
  run writes only if nobody's group moved since the person looked.
"""

import copy
import json
import pathlib
import sys

from app.connectors.function_executor import execute_function, fingerprint
from app.services import previews
from tests import consolidator_harness as harness
from tests.test_function_executor_extensions import _wire_fake_connector

WRITE = harness.source("set_revenue_group")
VENUE = "Applegum Cafe"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import sync_revenue_groups_config as installer  # noqa: E402


def _item(pid, name, price, category, group=None, deleted=None):
    shown = group or category  # Loaded reads an assigned item's category as its group
    return {
        "posIdentifier": pid,
        "name": name,
        "price": price,
        "priceEx": round(price / 1.1, 2),
        "itemGroupName": shown,
        "itemCategoryName": shown,
        "revenueGroup": group,
        "whenDeleted": deleted,
    }


ITEMS = [
    _item("p-1", "Flat White M", 5.5, "Uncategorised"),
    _item("p-2", "Latte M", 5.5, "Coffee", group="Beverage"),
    _item("p-3", "Smashed Avo", 24.0, "Uncategorised"),
    _item("p-4", "Smashed Avo", 26.0, "Uncategorised"),  # same dish, other menu
    _item("p-5", "Banana Bread", 9.0, "Cabinet Food"),
    _item("p-6", "Banana Bread", 9.0, "Cabinet Food", deleted="2026-03-01T00:00:00Z"),
    _item("p-7", "Gift Voucher $50", 50.0, "Vouchers"),
    _item("p-8", "Eggs Benedict", 27.0, "Food", group="Food"),
    _item("p-9", "Orange Juice", 7.0, "Uncategorised"),
]


class FakeLoaded:
    """GET returns the live list; the bulk PUT applies to it, as Loaded does."""

    def __init__(self, items=None, keep=None, fail=None):
        self.items = copy.deepcopy(items if items is not None else ITEMS)
        self.calls = []
        self.puts = []
        self.keep = keep  # posIdentifiers Loaded actually keeps (None = all)
        self.fail = fail  # an error to answer every PUT with

    def call_api(self, connector, action, params):
        self.calls.append(action)
        if action == "get_pos_items_raw":
            return copy.deepcopy(self.items)
        if action == "set_pos_items_revenue_group_raw":
            body = params["body"]
            self.puts.append(copy.deepcopy(body))
            if self.fail:
                return {"error": self.fail}
            for i in self.items:
                if i["posIdentifier"] in body["posItemIds"]:
                    if self.keep is not None and i["posIdentifier"] not in self.keep:
                        continue
                    g = body["revenueGroupName"]
                    i["revenueGroup"] = g
            return dict(body)
        raise AssertionError(f"unexpected call {action}")


def _set(api, **params):
    return harness.run(WRITE, api, venue=VENUE, **params)


# ------------------------------------------------------------ the write ----


class TestSetRevenueGroup:
    def test_a_name_moves_every_live_item_with_it_and_is_read_back(self):
        api = FakeLoaded()
        out = _set(api, revenue_group="Food", items=["smashed  avo", "Banana Bread"])
        assert api.puts == [
            {"posItemIds": ["p-5", "p-3", "p-4"], "revenueGroupName": "Food"}
        ]
        assert out["changed"] == 3 and out["by_group"] == {"Food": 3}
        assert "not_kept" not in out and "error" not in out
        assert api.calls == [
            "get_pos_items_raw",
            "set_pos_items_revenue_group_raw",
            "get_pos_items_raw",
        ]

    def test_an_unknown_name_writes_nothing_and_offers_the_closest(self):
        api = FakeLoaded()
        out = _set(api, revenue_group="Beverage", items=["Flat White M", "Orange Juic"])
        assert api.puts == []
        assert "Nothing changed" in out["error"]
        assert out["not_found"] == [
            {"name": "Orange Juic", "closest": ["Orange Juice"]}
        ]
        out = _set(api, revenue_group="Beverage", items=["Juice Orange"])
        assert out["not_found"][0]["closest"][0] == "Orange Juice"

    def test_an_id_works_as_well_as_a_name(self):
        api = FakeLoaded()
        _set(api, revenue_group="Beverage", items=["P-9"])
        assert api.puts[0]["posItemIds"] == ["p-9"]

    def test_items_already_in_the_group_are_skipped_and_nothing_to_do_writes_nothing(
        self,
    ):
        api = FakeLoaded()
        out = _set(api, revenue_group="Beverage", items=["Latte M"])
        assert api.puts == [] and out["changed"] == 0 and out["already_in_group"] == 1

    def test_a_category_sweep_leaves_assigned_items_alone_by_default(self):
        items = ITEMS + [_item("p-10", "Mocha M", 6.0, "Beverage", group="Other")]
        api = FakeLoaded(items)
        _set(api, revenue_group="Beverage", pos_category="Uncategorised")
        assert api.puts[0]["posItemIds"] == ["p-1", "p-9", "p-3", "p-4"]
        # An assigned item's category reads as its group ("Mocha M" reads Other),
        # so moving it by category means naming that group and opting in.
        api = FakeLoaded(items)
        _set(api, revenue_group="Beverage", pos_category="Other")
        assert api.puts == []
        _set(api, revenue_group="Beverage", pos_category="Other", only_unassigned=False)
        assert api.puts[0]["posItemIds"] == ["p-10"]

    def test_several_groups_are_one_call_and_unassigned_clears(self):
        api = FakeLoaded()
        out = _set(
            api,
            assignments=json.dumps(
                [
                    {
                        "revenue_group": "Beverage",
                        "items": ["Flat White M", "Orange Juice"],
                    },
                    {"revenue_group": "Other", "items": ["Gift Voucher $50"]},
                    {"revenue_group": "Unassigned", "items": ["Eggs Benedict"]},
                ]
            ),
        )
        assert api.puts == [
            {"posItemIds": ["p-1", "p-9"], "revenueGroupName": "Beverage"},
            {"posItemIds": ["p-7"], "revenueGroupName": "Other"},
            {"posItemIds": ["p-8"], "revenueGroupName": None},
        ]
        assert out["by_group"] == {"Beverage": 2, "Other": 1, "Unassigned": 1}

    def test_ids_go_in_chunks_of_two_hundred(self):
        many = [_item(f"x-{n}", f"Item {n}", 1.0, "Uncategorised") for n in range(450)]
        api = FakeLoaded(many)
        out = _set(api, revenue_group="Food", pos_category="Uncategorised")
        assert [len(p["posItemIds"]) for p in api.puts] == [200, 200, 50]
        assert out["changed"] == 450

    def test_what_loaded_didnt_keep_is_reported(self):
        api = FakeLoaded(keep={"p-1"})
        out = _set(
            api, revenue_group="Beverage", items=["Flat White M", "Orange Juice"]
        )
        assert out["changed"] == 1
        assert out["not_kept"] == [
            {"item": "Orange Juice", "wanted": "Beverage", "now": "Unassigned"}
        ]

    def test_loaded_refusing_is_an_error_naming_the_group(self):
        api = FakeLoaded(fail="500: Something went wrong")
        out = _set(api, revenue_group="Food", items=["Smashed Avo"])
        assert "Food" in out["error"] and "Something went wrong" in out["error"]

    def test_bad_requests_are_refused_before_anything_is_read(self):
        api = FakeLoaded()
        assert "Drinks" in _set(api, revenue_group="Drinks", items=["Latte M"])["error"]
        assert "which items" in _set(api, revenue_group="Food")["error"]
        assert (
            "venue is required"
            in harness.run(WRITE, api, revenue_group="Food", items=["x"])["error"]
        )
        assert api.calls == []


# ------------------------------------------------------- the approval ----


ENDPOINTS = [
    {"action": "get_pos_items_raw", "method": "GET", "path_template": "//i"},
    {
        "action": "set_pos_items_revenue_group_raw",
        "method": "PUT",
        "path_template": "//g",
    },
]
CONFIG = {
    "max_api_calls": 20,
    "allowed_write_actions": ["set_pos_items_revenue_group_raw"],
}
CALL = {
    "venue": VENUE,
    "revenue_group": "Beverage",
    "items": ["Flat White M", "Orange Juice"],
}


def _preview(monkeypatch, db, items=ITEMS):
    _wire_fake_connector(
        monkeypatch, {"get_pos_items_raw": copy.deepcopy(items)}, tools=ENDPOINTS
    )
    return execute_function(
        WRITE,
        dict(CALL),
        db,
        None,
        options={**CONFIG, "preview": True, "_preview_memo": previews.new_memo()},
    )


class TestTheApprovalCard:
    def test_the_preview_shows_every_item_and_writes_nothing(
        self, monkeypatch, db_session
    ):
        out = _preview(monkeypatch, db_session)
        card = out["preview"]["card"]
        assert card["title"] == "Set revenue groups" and card["venue"] == VENUE
        assert card["changes"] == [
            {
                "field": "Flat White M ($5.50)",
                "before": "Unassigned",
                "after": "Beverage",
            },
            {
                "field": "Orange Juice ($7.00)",
                "before": "Unassigned",
                "after": "Beverage",
            },
        ]
        assert "reports these items under their revenue group" in card["warnings"][0]
        assert out.get("preview_writes") in (None, [])

    def test_the_approved_run_writes_when_nothing_moved(self, monkeypatch, db_session):
        fp = _preview(monkeypatch, db_session)["preview"]["fingerprint"]
        calls = _wire_fake_connector(
            monkeypatch, {"get_pos_items_raw": copy.deepcopy(ITEMS)}, tools=ENDPOINTS
        )
        execute_function(
            WRITE,
            dict(CALL),
            db_session,
            None,
            options={**CONFIG, "expect_fingerprint": fp},
        )
        assert "set_pos_items_revenue_group_raw" in calls

    def test_an_item_that_moved_since_stops_the_write(self, monkeypatch, db_session):
        fp = _preview(monkeypatch, db_session)["preview"]["fingerprint"]
        moved = copy.deepcopy(ITEMS)
        moved[0]["revenueGroup"] = (
            "Other"  # someone set Flat White M in Loaded meanwhile
        )
        calls = _wire_fake_connector(
            monkeypatch, {"get_pos_items_raw": moved}, tools=ENDPOINTS
        )
        out = execute_function(
            WRITE,
            dict(CALL),
            db_session,
            None,
            options={**CONFIG, "expect_fingerprint": fp},
        )
        assert "set_pos_items_revenue_group_raw" not in calls
        assert (
            out["success"] is False
            and out["changed"]["changes"][0]["before"] == "Other"
        )
        assert fp != fingerprint(None)


# ------------------------------------------------------- the installer ----


class _Spec:
    connector_name = "loadedhub"

    def __init__(self, tools, endpoints):
        self.tools, self.endpoints = tools, endpoints


class TestInstaller:
    def test_installs_two_endpoints_and_one_tool_then_is_idempotent(self):
        spec = _Spec(
            [{"action": "get_sales", "method": "GET"}],
            [{"action": "get_stock_items_raw"}],
        )
        tools, endpoints, changes = installer.plan(spec)
        assert sorted(changes) == [
            "added endpoint get_pos_items_raw",
            "added endpoint set_pos_items_revenue_group_raw",
            "added tool set_revenue_group",
        ]
        assert [t["action"] for t in tools] == ["get_sales", "set_revenue_group"]
        assert [e["action"] for e in endpoints] == [
            "get_stock_items_raw",
            "get_pos_items_raw",
            "set_pos_items_revenue_group_raw",
        ]
        _, _, again = installer.plan(_Spec(tools, endpoints))
        assert again == []

    def test_the_tool_asks_first_and_may_be_always_allowed(self):
        tools, _, _ = installer.plan(_Spec([], []))
        (write,) = tools
        assert write["action"] == "set_revenue_group" and write["effect"] == "write"
        assert write["approval"] == {
            "default": "ask",
            "allow_auto": True,
            "label": "set revenue groups on POS items",
        }
        cc = write["consolidator_config"]
        assert cc["allowed_write_actions"] == ["set_pos_items_revenue_group_raw"]
        assert cc["function_code"] == WRITE
        # The model finds the items with get_sales, and never asks in chat:
        # the approval decides.
        assert "get_sales" in write["description"]
        assert "Don't ask" in write["description"]
