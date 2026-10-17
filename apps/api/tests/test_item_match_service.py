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

    def test_any_failure_degrades_to_empty(self, monkeypatch):
        # Catalogue unreachable → {} (never raises) so callers fall back to
        # plain create.
        def boom(venue_id, db, config_db):
            raise RuntimeError("catalogue down")

        monkeypatch.setattr(IM, "_fetch_raw_stock_items", boom)
        lines = [{"id": "L1", "description": "X", "code": "", "brand": "", "unit": ""}]
        assert IM.suggest_item_matches("v-1", lines, None, None, lh=object()) == {}


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

        def capture(lns, grps, cands, db, supplier_name=None):
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
