"""Tests for the norm.match_stock_items LLM function (service + handler).

The matching ENGINE (classify → department-filtered match → index→id mapping)
is covered by tests/test_invoice_fixes_handler.py::TestItemMatch, which execs
the same code through the router's re-import shim. Here we cover what's new:
the public service entry points and the internal-tool handler the review
engine calls via call_api — registration, venue resolution, caching, and the
never-raises degradation contract.
"""

from app.agents.internal_tools import get_handler
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

    def test_for_invoice_degrades_to_empty(self):
        # A client that cannot even be built → {} (never raises).
        assert IM.suggest_item_matches_for_invoice("v-1", "inv-1", None, None) == {}


class TestMatchStockItemsHandler:
    """The @register("norm", "match_stock_items") internal tool — what the
    review engine reaches through call_api (the resolve_dates pattern)."""

    def _handler(self):
        h = get_handler("norm", "match_stock_items")
        assert h is not None, "norm.match_stock_items handler not registered"
        return h

    def test_registered(self):
        self._handler()

    def test_no_lines_is_success_empty(self):
        out = self._handler()({"venue_id": "v-1", "lines": []}, None, None)
        assert out == {"success": True, "data": {"suggestions": {}}}

    def test_unresolvable_venue_errors(self, monkeypatch):
        # No venue_id and no venue name → error (never guesses a venue).
        out = self._handler()(
            {"lines": [{"id": "L1", "description": "X", "code": "", "brand": "", "unit": ""}]},
            None,
            None,
        )
        assert out["success"] is False and "venue" in out["error"]

    def test_delegates_and_wraps(self, monkeypatch):
        # venue_id given → skips the name lookup, calls the service, wraps the
        # result. Cache read (db=None) fails soft; cache write is best-effort.
        seen = {}

        def fake_suggest(venue_id, lines, db, config_db):
            seen["venue_id"] = venue_id
            seen["lines"] = lines
            return {"L1": {"matched_item": {"id": "i-1", "name": "X"}, "suggested_name": None, "suggested_group_id": None}}

        monkeypatch.setattr("app.services.item_match.suggest_item_matches", fake_suggest)
        lines = [{"id": "L1", "description": "X", "code": "", "brand": "", "unit": ""}]
        out = self._handler()({"venue_id": "v-9", "lines": lines}, None, None)
        assert out["success"] is True
        assert out["data"]["suggestions"]["L1"]["matched_item"]["id"] == "i-1"
        assert seen == {"venue_id": "v-9", "lines": lines}

    def test_cache_hit_skips_the_llm(self, monkeypatch):
        # A cached result is returned without invoking the service at all.
        cached = {"L1": {"matched_item": None, "suggested_name": "X", "suggested_group_id": None}}
        monkeypatch.setattr(
            "app.connectors.function_executor._extraction_cache_get",
            lambda db, key: cached,
        )

        def never(*a, **k):
            raise AssertionError("service must not run on a cache hit")

        monkeypatch.setattr("app.services.item_match.suggest_item_matches", never)
        lines = [{"id": "L1", "description": "X", "code": "", "brand": "", "unit": ""}]
        out = self._handler()({"venue_id": "v-9", "lines": lines}, None, None)
        assert out == {"success": True, "data": {"suggestions": cached}}
