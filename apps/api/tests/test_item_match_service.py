"""Tests for the item-match service (app/services/item_match.py): the matching
engine (classify → department-filtered match → index→id mapping, TestItemMatch,
moved here from test_invoice_fixes_handler.py on 17 Oct 2026), the public entry
point's never-raises degradation contract, and the consumable merge. (The
norm.match_stock_items engine tool that wrapped it was removed in Oct 2026 —
nothing called it once the review moved server-side.)
"""

import re

from app.services import item_match as IM


class TestSuggestItemMatches:
    def test_empty_lines_short_circuits(self):
        # No NEW lines → {} without touching the catalogue or any client.
        assert IM.suggest_item_matches("v-1", [], None, None) == {}

    LINES = [
        {"id": "L1", "description": "FRIES 13MM", "code": "", "brand": "", "unit": ""},
        {
            "id": "L2",
            "description": "PROMO CODE X",
            "code": "",
            "brand": "",
            "unit": "",
        },
    ]
    GROUPS = [{"id": "g-food", "name": "Frozen", "category": "Food"}]

    def _world(self, monkeypatch, replies, stock=None):
        """A venue whose catalogue and groups fetch fine, and a model that
        answers from ``replies`` in turn (each a {line_id: entry} dict)."""
        monkeypatch.setattr(IM, "_fetch_raw_stock_items", lambda *a: list(stock or []))
        monkeypatch.setattr(IM, "_fetch_stock_groups", lambda lh: self.GROUPS)
        monkeypatch.setattr(IM, "_classify_item_lines", lambda lines, db: {})
        asked = []

        def match(lns, grps, cands, db, supplier_name=None, note=None):
            asked.append(([ln["id"] for ln in lns], note))
            reply = replies[len(asked) - 1] if len(asked) <= len(replies) else {}
            return {k: v for k, v in reply.items() if k in {ln["id"] for ln in lns}}

        monkeypatch.setattr(IM, "_match_subset", match)
        return asked

    def test_a_failed_fetch_is_tried_again_then_every_line_is_marked(self, monkeypatch):
        # Catalogue unreachable twice: no guess, and every line says why
        # (owner, 19 Oct 2026: Norm's answer didn't come through, the next
        # review asks again). It never raises.
        calls = []

        def boom(venue_id, db, config_db):
            calls.append(1)
            raise RuntimeError("catalogue down")

        monkeypatch.setattr(IM, "_fetch_raw_stock_items", boom)
        out = IM.suggest_item_matches("v-1", self.LINES, None, None, lh=object())
        assert len(calls) == 2
        assert set(out) == {"L1", "L2"}
        assert all(v["failed"] for v in out.values())

    def test_an_error_reply_is_a_failed_fetch_not_an_empty_catalogue(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.component_api.execute_component_action",
            lambda *a, **k: {"data": "Unauthorized", "status_code": 401, "error": True},
        )
        with __import__("pytest").raises(RuntimeError):
            IM._fetch_raw_stock_items("v-1", None, None)

    def test_lines_without_an_answer_are_asked_once_more(self, monkeypatch):
        new = {"matched_item": None, "suggested_name": "Fries 13mm"}
        asked = self._world(
            monkeypatch,
            [
                # First try: L1 has no group, L2 is skipped.
                {"L1": {**new, "suggested_group_id": None}},
                {
                    "L1": {**new, "suggested_group_id": "g-food"},
                    "L2": {
                        "matched_item": None,
                        "suggested_name": "Promo",
                        "suggested_group_id": "g-food",
                    },
                },
            ],
        )
        out = IM.suggest_item_matches("v-1", self.LINES, None, None, lh=object())
        assert [ids for ids, _ in asked] == [["L1", "L2"], ["L1", "L2"]]
        assert asked[1][1] == IM._ASK_AGAIN
        assert out["L1"]["suggested_group_id"] == "g-food"
        assert out["L2"]["suggested_name"] == "Promo"

    def test_still_no_group_after_the_retry_is_marked_never_guessed(self, monkeypatch):
        no_group = {
            "matched_item": None,
            "suggested_name": "Promo",
            "suggested_group_id": None,
        }
        self._world(monkeypatch, [{"L2": no_group}, {"L2": no_group}])
        out = IM.suggest_item_matches("v-1", self.LINES[1:], None, None, lh=object())
        assert out == {"L2": {"failed": "no stock group was picked for the new item"}}

    def test_a_group_with_no_name_takes_the_invoices_words(self, monkeypatch):
        no_name = {
            "matched_item": None,
            "suggested_name": None,
            "suggested_group_id": "g-food",
        }
        self._world(monkeypatch, [{"L1": no_name}, {"L1": no_name}])
        out = IM.suggest_item_matches("v-1", self.LINES[:1], None, None, lh=object())
        assert out["L1"]["suggested_name"] == "FRIES 13MM"

    def test_a_venue_with_no_stock_items_is_still_asked(self, monkeypatch):
        # An empty catalogue used to skip the model, so a new venue's lines
        # got no name and no group at all.
        made = {
            "matched_item": None,
            "suggested_name": "Fries",
            "suggested_group_id": "g-food",
        }
        asked = self._world(monkeypatch, [{"L1": made}], stock=[])
        out = IM.suggest_item_matches("v-1", self.LINES[:1], None, None, lh=object())
        assert asked and out["L1"]["suggested_name"] == "Fries"

    def test_the_callers_catalogue_is_not_fetched_again(self, monkeypatch):
        made = {
            "matched_item": None,
            "suggested_name": "Fries",
            "suggested_group_id": "g-food",
        }
        self._world(monkeypatch, [{"L1": made}])
        monkeypatch.setattr(
            IM,
            "_fetch_raw_stock_items",
            lambda *a: __import__("pytest").fail("fetched"),
        )
        out = IM.suggest_item_matches(
            "v-1",
            self.LINES[:1],
            None,
            None,
            lh=object(),
            candidates=[{"id": "i-1", "name": "Chips"}],
        )
        assert out["L1"]["suggested_group_id"] == "g-food"


class TestConsumableMerge:
    """A consumable line (food OR beverage) searches ALL consumables — food,
    beverage and uncategorised — so a food product the venue filed under
    BEVERAGE (limes for the bar, Service Foods VEGF390) is still offered to a
    food line, and a beverage line sees the food-filed items too, while
    cleaning/packaging stays out."""

    def test_food_line_sees_beverage_filed_candidate(self, monkeypatch):
        lines = [
            {
                "id": "L1",
                "description": "LIMES GREEN IMPORTED",
                "code": "VEGF390",
                "brand": "",
                "unit": "",
            },
            {
                "id": "L2",
                "description": "Coca Cola",
                "code": "",
                "brand": "",
                "unit": "",
            },
        ]
        groups = [
            {"id": "g-bev", "category": "Beverage"},
            {"id": "g-food", "category": "Food"},
            {"id": "g-clean", "category": "Cleaning"},
        ]
        candidates = [
            {
                "id": "i-lime",
                "name": "Lime",
                "groupId": "g-bev",
            },  # food item filed as bev
            {"id": "i-flour", "name": "Flour", "groupId": "g-food"},
            {
                "id": "i-bleach",
                "name": "Bleach",
                "groupId": "g-clean",
            },  # non-consumable
        ]
        monkeypatch.setattr(
            IM,
            "_classify_item_lines",
            lambda lines, db: {"L1": "food", "L2": "beverage"},
        )
        seen: dict = {}

        def capture(lns, grps, cands, db, supplier_name=None, note=None):
            for ln in lns:
                seen[ln["id"]] = {c["id"] for c in cands}
            return {}

        monkeypatch.setattr(IM, "_match_subset", capture)
        IM._match_stock_items(lines, groups, candidates, None)
        for line_id in ("L1", "L2"):  # the food line and the beverage line
            sent = seen[line_id]
            assert "i-lime" in sent  # the beverage-filed lime IS offered
            assert "i-flour" in sent  # ordinary food item still included
            assert "i-bleach" not in sent  # a lime never matches a mop


def _ids_in(prompt: str) -> list[str]:
    """The line_ids the LLM was shown, in order (for scripted-mock replies)."""
    return re.findall(r"line_id=(\S+?)\]", prompt)


class TestItemMatch:
    """The LLM match-before-create step: classify → department-filtered match →
    suggest a link (existing item) or a normalized name+group (create)."""

    def test_the_matcher_is_told_a_foods_form_is_part_of_it(self):
        # Hawea, 21 Oct 2026: tinned whole-peeled and crushed tomatoes went on
        # the fresh 'Tomatoes' item — the food rule said to ignore packaging,
        # and a tin was taken for packaging.
        text = IM._MATCH_SYSTEM_PROMPT
        assert "never the product's FORM" in text
        assert "are not the fresh 'Tomatoes'" in text
        # New items are named as people say them (the human lists' style).
        assert "'Tomato Sauce'" in text and "Flour All Purpose" not in text

    def test_classify_labels_lines(self, monkeypatch):
        def fake(**kw):
            assert "Classify each supplier invoice line" in kw["system_prompt"]
            ids = _ids_in(kw["user_prompt"])
            label = {"L1": "food", "L2": "beverage", "L3": "cleaning"}
            return ({"classes": [{"line_id": i, "class": label[i]} for i in ids]}, None)

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake)
        lines = [
            {"id": "L1", "description": "Chicken", "brand": ""},
            {"id": "L2", "description": "Coke", "brand": ""},
            {"id": "L3", "description": "Bleach", "brand": ""},
        ]
        out = IM._classify_item_lines(lines, db=None)
        # unknown labels collapse to "other"
        assert out == {"L1": "food", "L2": "beverage", "L3": "other"}

    def test_classify_empty_on_error(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("breaker open")

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", boom)
        assert (
            IM._classify_item_lines(
                [{"id": "L1", "description": "x", "brand": ""}], None
            )
            == {}
        )

    def test_match_subset_maps_index_and_suggestion(self, monkeypatch):
        cands = [
            {
                "id": "i0",
                "name": "SPIANATA PICCANTE",
                "groupName": "Meats",
                "orderingUnitId": "u-kilo",
                "suppliers": [
                    {
                        "supplierId": "s",
                        "unitId": "u-kilo",
                        "unitCost": 10,
                        "defaultForSupplier": True,
                    }
                ],
            },
            {"id": "i1", "name": "OIL CANOLA", "groupName": "Oils"},
        ]
        groups = [
            {"id": "g-meat", "name": "Meats", "category": "Food"},
            {"id": "g-oil", "name": "Oils", "category": "Food"},
        ]
        lines = [
            {
                "id": "L1",
                "description": "Spianata Piccante 2kg C6",
                "code": "CHS.009",
                "brand": "",
                "unit": "KGM",
            },
            {
                "id": "L2",
                "description": "Mystery Product",
                "code": "X",
                "brand": "",
                "unit": "Each",
            },
        ]

        def fake(**kw):
            return (
                {
                    "matches": [
                        {
                            "line_id": "L1",
                            "match_index": 0,
                            "suggested_name": None,
                            "suggested_group_index": None,
                        },
                        {
                            "line_id": "L2",
                            "match_index": None,
                            "suggested_name": "Mystery Product",
                            "suggested_group_index": 1,
                        },
                    ]
                },
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake)
        out = IM._match_subset(lines, groups, cands, db=None)
        assert out["L1"]["matched_item"]["id"] == "i0"
        assert out["L1"]["matched_item"]["unit_id"] == "u-kilo"
        assert out["L1"]["matched_item"]["unit_cost"] == 10
        assert out["L2"]["matched_item"] is None
        assert out["L2"]["suggested_name"] == "Mystery Product"
        assert out["L2"]["suggested_group_id"] == "g-oil"  # index 1

    def test_match_subset_ignores_out_of_range_index(self, monkeypatch):
        cands = [{"id": "i0", "name": "A", "groupName": "G"}]
        groups = [{"id": "g", "name": "G", "category": "Food"}]
        lines = [{"id": "L1", "description": "A", "code": "", "brand": "", "unit": ""}]

        def fake(**kw):
            return (
                {
                    "matches": [
                        {
                            "line_id": "L1",
                            "match_index": 9,
                            "suggested_name": "A",
                            "suggested_group_index": 9,
                        }
                    ]
                },
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake)
        out = IM._match_subset(lines, groups, cands, db=None)
        assert out["L1"]["matched_item"] is None
        assert out["L1"]["suggested_group_id"] is None

    def test_match_empty_on_error(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("down")

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", boom)
        cands = [{"id": "i0", "name": "A", "groupId": "g", "groupName": "G"}]
        groups = [{"id": "g", "name": "G", "category": "Food"}]
        lines = [{"id": "L1", "description": "A", "code": "", "brand": "", "unit": ""}]
        # classify fails → all "other" → match fails → {}
        assert IM._match_stock_items(lines, groups, cands, db=None) == {}
