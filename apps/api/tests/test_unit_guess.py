"""Choosing a unit Loaded already has — and never inventing one.

`receive_without_unit` lets a venue say "don't park an invoice just because a
line's unit is unreadable". The safety property is that this only ever CHOOSES
from what exists: a wrong match is one wrong line, where a wrong create is a
permanent catalogue entry every future invoice can match against.
"""

from app.services.invoice_units import is_packaging_word
from app.services.unit_guess import guess_unit

UNITS = [
    {"id": "u-each", "name": "Each", "ratio": 1.0},
    {"id": "u-kilo", "name": "Kilo", "ratio": 1.0},
    {"id": "u-6x1l", "name": "6x1000mL", "ratio": 6.0},
    {"id": "u-6pk", "name": "6 Pack", "ratio": 6.0},
    {"id": "u-dead", "name": "Old", "ratio": 1.0, "datestampDeleted": "2026-01-01"},
]


class TestItPrefersArithmeticToTheModel:
    def test_an_equivalent_pack_is_matched_without_asking(self):
        unit, why = guess_unit(
            {"unit": "0.7 L"},
            [{"id": "u-700", "name": "700 mL", "ratio": 1.0}],
            ask_llm=lambda *a: (_ for _ in ()).throw(
                AssertionError("should not have asked")
            ),
        )
        assert unit["id"] == "u-700"
        assert "same pack" in why

    def test_an_exact_name_wins_a_tie(self):
        """'6x1000mL' and '6 Pack' are equivalent by count, so equivalence
        alone cannot choose — the printed name can."""
        unit, _ = guess_unit({"unit": "6x1000mL"}, UNITS)
        assert unit["id"] == "u-6x1l"

    def test_an_exactly_printed_name_is_taken_at_its_word(self):
        """'6 Pack' printed on the copy IS the '6 Pack' unit, even though
        '6x1000mL' is arithmetically equivalent to it."""
        unit, why = guess_unit({"unit": "6 Pack"}, UNITS)
        assert unit["id"] == "u-6pk" and "exactly" in why

    def test_a_genuine_tie_refuses_rather_than_picking_one(self):
        """Two equivalent units and the printed name matches neither: there is
        no principled winner, so it parks instead of choosing."""
        unit, why = guess_unit({"unit": "6 x 1L"}, UNITS)
        assert unit is None
        assert "equally" in why


class TestPackagingWordsAreNotEvidence:
    """A bare bundling word must never pass for a delivered unit.

    Trents 5973784 (18 Aug 2026): two sizeless lines printed 'PACK', the venue
    carries a unit literally named PACK, and the replica linked it — a
    'successful' resolution into a meaningless unit that then read as a
    perfectly healthy line.
    """

    def test_the_word_list_draws_the_line_at_bundling(self):
        assert is_packaging_word("pack")
        assert is_packaging_word("PKT")
        assert is_packaging_word(" Carton ")
        assert not is_packaging_word("each")  # a real count of one (17 Aug 2026)
        assert not is_packaging_word("700 mL")
        assert not is_packaging_word("6x1000mL")
        assert not is_packaging_word("12 pack")  # a counted pack is information

    def test_bare_pack_falls_through_to_the_model_and_pack_is_never_offered(self):
        seen: dict = {}

        def _ask(line, cands):
            seen["names"] = [c["name"] for c in cands]
            return "u-700"

        unit, _ = guess_unit(
            {"description": "MALFY GIN ROSA PINK GRAPEF", "unit": "PACK"},
            [
                {"id": "u-pack", "name": "PACK", "ratio": 1.0},
                {"id": "u-700", "name": "700 mL", "ratio": 1.0},
            ],
            ask_llm=_ask,
        )
        assert unit["id"] == "u-700"
        assert "PACK" not in seen["names"]

    def test_each_is_still_taken_as_real_evidence(self):
        unit, _ = guess_unit(
            {"unit": "EA"},
            [{"id": "u-each", "name": "Each", "ratio": 1.0}],
            ask_llm=lambda *a: (_ for _ in ()).throw(
                AssertionError("should not have asked")
            ),
        )
        assert unit["id"] == "u-each"


class TestItOnlyEverChoosesFromWhatExists:
    def test_an_id_that_is_not_on_the_list_is_refused(self):
        """The failure that matters: accepting an invented id would create a
        unit through the back door, which is the one thing this must not do."""
        unit, why = guess_unit(
            {"description": "SALT TABLE IODISED"},
            UNITS,
            ask_llm=lambda line, cands: "u-made-up",
        )
        assert unit is None
        assert "no existing unit fits" in why

    def test_a_deleted_unit_is_never_offered_or_chosen(self):
        seen = {}

        def _ask(line, cands):
            seen["ids"] = [c["id"] for c in cands]
            return "u-dead"

        unit, _ = guess_unit({"description": "x"}, UNITS, ask_llm=_ask)
        assert "u-dead" not in seen["ids"]
        assert unit is None

    def test_a_model_failure_parks_the_line_rather_than_guessing(self):
        def _boom(line, cands):
            raise RuntimeError("no")

        unit, why = guess_unit({"description": "x"}, UNITS, ask_llm=_boom)
        assert unit is None and "could not be worked out" in why

    def test_no_units_at_all_is_answered_honestly(self):
        unit, why = guess_unit({"unit": "each"}, [])
        assert unit is None and "no units in Loaded" in why

    def test_with_no_model_it_says_so_rather_than_picking_the_first(self):
        unit, why = guess_unit({"description": "no unit printed"}, UNITS)
        assert unit is None and "no model available" in why
