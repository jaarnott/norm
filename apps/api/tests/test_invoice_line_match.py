"""invoice_line_match — THE line↔catalogue matcher.

``plain_match`` began as a verbatim port of the sandboxed engine's
``_plain_match``; since the replica-primary refactor the engine's copy is gone
and this is the single implementation, used by the replica and review-time
pairing. ``CatalogueIndex`` is the replica's catalogue lookup.
"""

from app.services import invoice_line_match as M


class TestPlainMatch:
    def test_code_tier_first(self):
        pool = [
            {"code": "A1", "description": "Zed"},
            {"code": None, "description": "Widget Thing"},
        ]
        got = M.plain_match({"code": "a1", "description": "Widget Thing"}, pool)
        assert got is pool[0]

    def test_description_substring_both_ways(self):
        pool = [{"code": None, "description": "CHICKEN BREAST"}]
        got = M.plain_match(
            {"code": None, "description": "chicken breast skin off"}, pool
        )
        assert got is pool[0]
        assert M.plain_match({"code": None, "description": "LAMB RUMP"}, pool) is None


class TestCatalogueIndex:
    ITEMS = [
        {
            "id": "item-salmon",
            "name": "SALMON FILLET",
            "suppliers": [
                {
                    "supplierId": "sup-1",
                    "stockCode": "PBO0.7",
                    "unitId": "u1",
                    "description": "Salmon Fillet Skin On",
                    "defaultForSupplier": True,
                }
            ],
        },
        {
            "id": "item-freight",
            "name": "FREIGHT - FOOD",
            "suppliers": [
                {
                    "supplierId": "sup-1",
                    "stockCode": "FGT001",
                    "description": "Courier Freight",
                }
            ],
        },
        {
            # Two items share a code across DIFFERENT suppliers — the
            # unscoped code tier must refuse.
            "id": "item-a",
            "name": "AAA WIDGET",
            "suppliers": [{"supplierId": "sup-2", "stockCode": "SHARED"}],
        },
        {
            "id": "item-b",
            "name": "BBB WIDGET",
            "suppliers": [{"supplierId": "sup-3", "stockCode": "SHARED"}],
        },
    ]

    def _idx(self):
        return M.CatalogueIndex.build(self.ITEMS)

    def test_supplier_code_tier_wins(self):
        item, by = self._idx().match_line("pbo0.7", "anything", "sup-1")
        assert item["id"] == "item-salmon" and by == "supplier_code"

    def test_unscoped_code_unique_only(self):
        item, by = self._idx().match_line("FGT001", None, "sup-9")
        assert item["id"] == "item-freight" and by == "code"
        item2, by2 = self._idx().match_line("SHARED", None, "sup-9")
        assert item2 is None and by2 is None

    def test_description_exact(self):
        item, by = self._idx().match_line(None, "Courier Freight", None)
        assert item["id"] == "item-freight" and by == "description_exact"

    def test_description_substring_floor_and_uniqueness(self):
        item, by = self._idx().match_line(None, "SALMON FILLET SKIN ON 1KG", None)
        assert item["id"] == "item-salmon" and by in (
            "description_substring",
            "description_exact",
        )
        none_item, _ = self._idx().match_line(None, "WIDGET", None)
        assert none_item is None  # 6 chars, under the floor / ambiguous

    def test_supplier_variant_picker(self):
        v = M.supplier_variant(self.ITEMS[0], "sup-1", "PBO0.7")
        assert v["stockCode"] == "PBO0.7"
        v2 = M.supplier_variant(self.ITEMS[0], "sup-1", "UNKNOWN")
        assert v2["defaultForSupplier"] is True
        assert M.supplier_variant(self.ITEMS[0], "sup-9", None) is None
