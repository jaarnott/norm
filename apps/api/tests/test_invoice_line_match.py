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
        # The supplier's own earlier wording, inside a fuller one.
        item, by = self._idx().match_line(None, "SALMON FILLET SKIN ON 1KG", "sup-1")
        assert item["id"] == "item-salmon" and by == "description_substring"
        none_item, _ = self._idx().match_line(None, "WIDGET", "sup-2")
        assert none_item is None  # 6 chars, under the floor / ambiguous

    def test_a_name_inside_another_products_description_is_no_match(self):
        """Grange Gully Bistro, 23 Oct 2026: Two Providores' 'Pineapple
        Crushed Australian in Syrup 3kg A10' went on Select Fresh's fresh
        'Pineapple' because the name sat inside the description. Only the
        line's own supplier's wording counts here; the rest is the matcher's."""
        idx = M.CatalogueIndex.build(
            [
                {
                    "id": "item-pineapple",
                    "name": "Pineapple",
                    "suppliers": [
                        {
                            "supplierId": "sup-select",
                            "stockCode": "PINTE",
                            "description": "PINEAPPLE WITH TOP EACH",
                        }
                    ],
                }
            ]
        )
        crushed = "Pineapple Crushed Australian in Syrup 3kg A10"
        assert idx.match_line("E0543", crushed, "sup-two") == (None, None)
        assert idx.match_line(None, "SALMON FILLET SKIN ON 1KG", None) == (None, None)

    def test_supplier_variant_picker(self):
        v = M.supplier_variant(self.ITEMS[0], "sup-1", "PBO0.7")
        assert v["stockCode"] == "PBO0.7"
        v2 = M.supplier_variant(self.ITEMS[0], "sup-1", "UNKNOWN")
        assert v2["defaultForSupplier"] is True
        assert M.supplier_variant(self.ITEMS[0], "sup-9", None) is None
