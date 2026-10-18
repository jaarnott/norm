"""manage_supplier — review a venue's Loaded supplier aliases, remove wrong ones.

Loaded files a scanned invoice under a supplier by its aliases. St. Malo
Bakery at Somewhere North carried "St.", and 19 Sydney Waste Services bills
($12,170) went in under the bakery (17 Oct 2026).

Exec'd under the REAL sandbox namespace against the canonical file. Shapes as
Loaded returns them: GET /suppliers -> [{id, name, removedAt}], GET
/suppliers/{id}/aliases -> [{id, name}] (name may be null), DELETE
/suppliers/{id}/aliases/{aliasId} -> 204 (seen in Loaded's own app, 18 Oct 2026).

Facts pinned:
- the flags, and the names that must NOT be flagged: a business named only in
  generic words keeps them ('SERVICE FOODS LTD'), plurals and run-together
  words are the same name ('Aitken & Co' is Aitkens);
- an alias on two records, or unrelated to the name, is advice "check", never
  "remove" — a person decides which record keeps it;
- remove_alias takes names or ids, stops on one that isn't there, reads back;
- the approval card lists every alias and writes nothing; the approved run
  writes only if the list didn't change since.
"""

import copy
import pathlib
import sys

from app.connectors.function_executor import execute_function
from app.services import previews
from tests import consolidator_harness as harness
from tests.test_function_executor_extensions import _wire_fake_connector

TOOL = harness.source("manage_supplier")
VENUE = "Bessie & Engineers"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import sync_manage_supplier_config as installer  # noqa: E402

SUPPLIERS = [
    {"id": "s-malo", "name": "St. Malo Bakery"},
    {"id": "s-sf", "name": "Service Foods"},
    {"id": "s-ait", "name": "Aitkens"},
    {"id": "s-euro", "name": "Eurovintage"},
    {"id": "s-bid", "name": "Bidfood"},
    {"id": "s-bidf", "name": "Bidfoods fresh produce"},
    {"id": "s-cp", "name": "City Produce"},
    {"id": "s-pn", "name": "Premier Northpak"},
    {"id": "s-old", "name": "Gone Ltd", "removedAt": "2026-01-01T00:00:00Z"},
]

ALIASES = {
    "s-malo": [
        {"id": "a-st", "name": "St."},
        {"id": "a-blank", "name": None},
        {"id": "a-malo", "name": "St. Malo Bakery® artisan bakers"},
        {"id": "a-bessie", "name": "Bessie"},
    ],
    "s-sf": [
        {"id": "a-sfl", "name": "SERVICE FOODS LTD"},
        {"id": "a-sfl2", "name": "Service Foods Ltd."},
        {"id": "a-cp", "name": "CITY PRODUCE"},
    ],
    "s-ait": [{"id": "a-ait", "name": "Aitken & Co Limited"}],
    "s-euro": [{"id": "a-euro", "name": "Euro Vintage Ltd"}],
    "s-bid": [{"id": "a-bl1", "name": "Bidfood Limited"}],
    "s-bidf": [{"id": "a-bl2", "name": "Bidfood Limited"}],
    "s-cp": [],
    "s-pn": [{"id": "a-pnm", "name": "PNM SYDNEY PTY LTD"}],
}


class FakeLoaded:
    def __init__(self, aliases=None, fail=None):
        self.aliases = copy.deepcopy(aliases if aliases is not None else ALIASES)
        self.calls = []
        self.deletes = []
        self.fail = fail

    def call_api(self, connector, action, params):
        self.calls.append(action)
        if action == "get_suppliers":
            return copy.deepcopy(SUPPLIERS)
        if action == "get_supplier_aliases":
            return copy.deepcopy(self.aliases.get(params["supplier_id"], []))
        if action == "delete_supplier_alias_raw":
            self.deletes.append((params["supplier_id"], params["alias_id"]))
            if self.fail:
                return {"error": self.fail}
            rows = self.aliases[params["supplier_id"]]
            self.aliases[params["supplier_id"]] = [
                a for a in rows if a["id"] != params["alias_id"]
            ]
            return {"body": ""}
        raise AssertionError(f"unexpected call {action}")


def _run(api, **params):
    return harness.run(TOOL, api, venue=VENUE, **params)


def _flags(out):
    return {
        (f["supplier"], f["alias"]): (f["issue"], f["advice"]) for f in out["flagged"]
    }


class TestReviewAliases:
    def test_wrong_aliases_are_flagged_and_real_names_are_not(self):
        out = _run(FakeLoaded(), op="review_aliases")
        assert _flags(out) == {
            ("St. Malo Bakery", "St."): ("too_short", "remove"),
            ("St. Malo Bakery", "(blank)"): ("blank", "remove"),
            ("St. Malo Bakery", "Bessie"): ("venue_name", "remove"),
            ("Service Foods", "Service Foods Ltd."): ("duplicate", "remove"),
            ("Service Foods", "CITY PRODUCE"): ("another_supplier", "remove"),
            ("Bidfood", "Bidfood Limited"): ("on_two_suppliers", "check"),
            ("Bidfoods fresh produce", "Bidfood Limited"): (
                "on_two_suppliers",
                "check",
            ),
            ("Premier Northpak", "PNM SYDNEY PTY LTD"): ("unrelated", "check"),
        }
        # Not flagged: 'SERVICE FOODS LTD' (the name is generic words),
        # 'Aitken & Co Limited' (Aitkens), 'Euro Vintage Ltd' (Eurovintage),
        # 'St. Malo Bakery® artisan bakers'. A removed record isn't read.
        assert out["suppliers_checked"] == 8 and out["aliases"] == 12
        assert (out["to_remove"], out["to_check"]) == (5, 3)
        assert out["flagged"][0]["advice"] == "remove"  # removals first
        assert "removed" not in out  # a review never writes

    def test_one_supplier_lists_its_aliases_with_their_ids(self):
        api = FakeLoaded()
        out = _run(api, op="review_aliases", supplier="st malo bakery")
        assert out["supplier"]["aliases"][0] == {"alias": "St.", "alias_id": "a-st"}
        assert api.calls == ["get_suppliers", "get_supplier_aliases"]

    def test_an_unknown_supplier_comes_back_with_the_closest(self):
        out = _run(FakeLoaded(), op="review_aliases", supplier="Malo Bakeries")
        assert "Nothing changed" in out["error"]
        assert out["closest"] == ["St. Malo Bakery"]


class TestRemoveAlias:
    def test_by_name_and_id_then_read_back(self):
        api = FakeLoaded()
        out = _run(
            api,
            op="remove_alias",
            supplier="St. Malo Bakery",
            aliases=["st.", "a-blank"],
        )
        assert api.deletes == [("s-malo", "a-st"), ("s-malo", "a-blank")]
        assert out["removed"] == ["St.", "(blank)"]
        assert out["aliases_now"] == ["St. Malo Bakery® artisan bakers", "Bessie"]
        assert "error" not in out and "not_removed" not in out

    def test_an_alias_that_isnt_there_removes_nothing(self):
        api = FakeLoaded()
        out = _run(
            api,
            op="remove_alias",
            supplier="St. Malo Bakery",
            aliases=["St.", "Sydney"],
        )
        assert api.deletes == []
        assert out["not_found"] == ["Sydney"] and "Nothing changed" in out["error"]

    def test_a_refusal_is_reported_and_the_alias_is_still_there(self):
        api = FakeLoaded(fail="403: Forbidden")
        out = _run(api, op="remove_alias", supplier="s-malo", aliases=["St."])
        assert out["not_removed"] == ["St."] and "refused" in out["error"]

    def test_bad_requests_are_refused_before_anything_is_written(self):
        api = FakeLoaded()
        assert (
            "which aliases" in _run(api, op="remove_alias", supplier="Aitkens")["error"]
        )
        assert "op must be" in _run(api, op="delete")["error"]
        assert (
            "venue is required" in harness.run(TOOL, api, op="review_aliases")["error"]
        )
        assert api.deletes == []


# ------------------------------------------------------- the approval ----

ENDPOINTS = [
    {"action": "get_suppliers", "method": "GET", "path_template": "//s"},
    {"action": "get_supplier_aliases", "method": "GET", "path_template": "//a"},
    {"action": "delete_supplier_alias_raw", "method": "DELETE", "path_template": "//d"},
]
CONFIG = {"max_api_calls": 200, "allowed_write_actions": ["delete_supplier_alias_raw"]}
CALL = {
    "venue": VENUE,
    "op": "remove_alias",
    "supplier": "St. Malo Bakery",
    "aliases": ["St."],
}


def _wire(monkeypatch, aliases):
    return _wire_fake_connector(
        monkeypatch,
        {"get_suppliers": copy.deepcopy(SUPPLIERS), "get_supplier_aliases": aliases},
        tools=ENDPOINTS,
    )


def _preview(monkeypatch, db):
    _wire(monkeypatch, copy.deepcopy(ALIASES["s-malo"]))
    return execute_function(
        TOOL,
        dict(CALL),
        db,
        None,
        options={**CONFIG, "preview": True, "_preview_memo": previews.new_memo()},
    )


class TestTheApprovalCard:
    def test_the_card_lists_the_aliases_and_writes_nothing(
        self, monkeypatch, db_session
    ):
        out = _preview(monkeypatch, db_session)
        card = out["preview"]["card"]
        assert card["title"] == "Remove supplier aliases" and card["venue"] == VENUE
        assert card["changes"] == [
            {"field": "alias", "before": "St.", "after": "removed"}
        ]
        assert "stops filing documents" in card["warnings"][0]
        assert out.get("preview_writes") in (None, [])

    def test_a_review_needs_no_approval(self, monkeypatch, db_session):
        _wire(monkeypatch, [])
        out = execute_function(
            TOOL,
            {"venue": VENUE, "op": "review_aliases", "supplier": "Aitkens"},
            db_session,
            None,
            options={**CONFIG, "preview": True, "_preview_memo": previews.new_memo()},
        )
        assert "preview" not in out and out.get("preview_writes") in (None, [])

    def test_the_approved_run_writes_when_the_list_is_unchanged(
        self, monkeypatch, db_session
    ):
        fp = _preview(monkeypatch, db_session)["preview"]["fingerprint"]
        calls = _wire(monkeypatch, copy.deepcopy(ALIASES["s-malo"]))
        execute_function(
            TOOL,
            dict(CALL),
            db_session,
            None,
            options={**CONFIG, "expect_fingerprint": fp},
        )
        assert "delete_supplier_alias_raw" in calls

    def test_a_list_that_changed_since_stops_the_write(self, monkeypatch, db_session):
        fp = _preview(monkeypatch, db_session)["preview"]["fingerprint"]
        changed = [{"id": "a-st-new", "name": "St."}]  # re-added under a new id
        calls = _wire(monkeypatch, changed)
        out = execute_function(
            TOOL,
            dict(CALL),
            db_session,
            None,
            options={**CONFIG, "expect_fingerprint": fp},
        )
        assert "delete_supplier_alias_raw" not in calls and out["success"] is False


# ------------------------------------------------------- the installer ----


class _Spec:
    connector_name = "loadedhub"

    def __init__(self, tools, endpoints):
        self.tools, self.endpoints = tools, endpoints


class TestInstaller:
    def test_installs_one_endpoint_and_one_tool_then_is_idempotent(self):
        spec = _Spec([{"action": "get_invoices"}], [{"action": "get_supplier_aliases"}])
        tools, endpoints, changes = installer.plan(spec)
        assert sorted(changes) == [
            "added endpoint delete_supplier_alias_raw",
            "added tool manage_supplier",
        ]
        assert [e["action"] for e in endpoints] == [
            "get_supplier_aliases",
            "delete_supplier_alias_raw",
        ]
        assert installer.plan(_Spec(tools, endpoints))[2] == []

    def test_the_tool_asks_before_removing_and_may_be_always_allowed(self):
        (tool,) = installer.plan(_Spec([], []))[0]
        assert tool["effect"] == "write"
        assert tool["approval"] == {
            "default": "ask",
            "allow_auto": True,
            "label": "remove supplier aliases in Loaded",
        }
        cc = tool["consolidator_config"]
        assert cc["allowed_write_actions"] == ["delete_supplier_alias_raw"]
        assert cc["function_code"] == TOOL
        assert "Don't ask" in tool["description"]
