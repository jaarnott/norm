"""Tests for the item-match service (app/services/item_match.py).

The matching ENGINE (classify → department-filtered match → index→id mapping)
is covered by tests/test_invoice_fixes_handler.py::TestItemMatch. Here: the
public entry point's never-raises degradation contract, and the consumable
merge. (The norm.match_stock_items engine tool that wrapped it was removed in
Oct 2026 — nothing called it once the review moved server-side.)
"""

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
    """A food-classified line searches ALL consumables — food, beverage and
    uncategorised — so a food product the venue filed under BEVERAGE (limes for
    the bar) is still offered to it, while cleaning/packaging stays out."""

    def test_food_line_sees_beverage_filed_candidate(self, monkeypatch):
        lines = [
            {
                "id": "L1",
                "description": "LIMES GREEN IMPORTED",
                "code": "VEGF390",
                "brand": "",
                "unit": "",
            }
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
            IM, "_classify_item_lines", lambda lines, db: {"L1": "food"}
        )
        seen: dict = {}

        def capture(lns, grps, cands, db, supplier_name=None):
            seen["cands"] = cands
            return {}

        monkeypatch.setattr(IM, "_match_subset", capture)
        IM._match_stock_items(lines, groups, candidates, None)
        sent = {c["id"] for c in seen["cands"]}
        assert "i-lime" in sent  # the beverage-filed lime IS offered to the food line
        assert "i-flour" in sent  # ordinary food item still included
        assert "i-bleach" not in sent  # cleaning stays out — a lime never matches a mop
