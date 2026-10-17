"""Choosing a unit Loaded already has — and never inventing one.

`receive_without_unit` lets a venue say "don't park an invoice just because a
line's unit is unreadable". The safety property is that this only ever CHOOSES
from what exists: a wrong match is one wrong line, where a wrong create is a
permanent catalogue entry every future invoice can match against.
"""

import pytest

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

    def test_an_exactly_printed_name_is_taken_at_its_word(self):
        """'6x1000mL' and '6 Pack' are equivalent by count, so equivalence
        alone cannot choose — the printed name can, either way round: '6 Pack'
        printed on the copy IS the '6 Pack' unit, even though '6x1000mL' is
        arithmetically equivalent to it."""
        unit, _ = guess_unit({"unit": "6x1000mL"}, UNITS)
        assert unit["id"] == "u-6x1l"
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


class TestTheLastPassAlwaysPicks:
    """A line with no unit stops autopilot outright. When neither the
    arithmetic nor the first ask settles it, the last pass (``insist``) must
    pick from the list anyway — INV-0446 (14 Oct 2026) parked on
    'Pistachio Jam-Can' and 'Strawberry Syrup-Case' with nothing chosen."""

    def test_a_declined_first_ask_is_followed_by_a_forced_pick(self):
        asked = []

        def _insist(line, cands):
            asked.append([c["id"] for c in cands])
            return "u-each"

        unit, why = guess_unit(
            {"description": "Pistachio Jam-Can"},
            UNITS,
            ask_llm=lambda line, cands: None,
            insist=_insist,
        )
        assert unit["id"] == "u-each"
        assert "best guess" in why and "check it" in why
        assert asked and "u-dead" not in asked[0]  # still only live units

    def test_a_failed_first_ask_is_followed_by_a_forced_pick(self):
        def _boom(line, cands):
            raise ValueError("no JSON object found in response")

        unit, _ = guess_unit(
            {"description": "Strawberry Syrup-Case"},
            UNITS,
            ask_llm=_boom,
            insist=lambda line, cands: "u-kilo",
        )
        assert unit["id"] == "u-kilo"

    def test_a_first_pick_is_not_second_guessed(self):
        unit, why = guess_unit(
            {"description": "SALT TABLE IODISED"},
            UNITS,
            ask_llm=lambda line, cands: "u-kilo",
            insist=lambda *a: pytest.fail("the last pass ran after a pick"),
        )
        assert unit["id"] == "u-kilo" and "closest existing unit" in why

    def test_a_forced_pick_still_never_invents(self):
        unit, why = guess_unit(
            {"description": "x"},
            UNITS,
            ask_llm=lambda line, cands: None,
            insist=lambda line, cands: "u-made-up",
        )
        assert unit is None and "no existing unit fits" in why

    def test_a_tie_between_equal_packs_is_settled_by_the_last_pass(self):
        offered = {}

        def _insist(line, cands):
            offered["ids"] = sorted(c["id"] for c in cands)
            return "u-6pk"

        unit, _ = guess_unit({"unit": "6 x 1L"}, UNITS, insist=_insist)
        assert unit["id"] == "u-6pk"
        assert offered["ids"] == ["u-6pk", "u-6x1l"]  # only the tied units

    def test_the_last_pass_alone_is_enough(self):
        unit, _ = guess_unit(
            {"description": "x"}, UNITS, insist=lambda line, cands: "u-each"
        )
        assert unit["id"] == "u-each"


class TestNoSizeFromNowhere:
    """A unit that carries a size, picked for a line that states none, books
    stock that never arrived: 'Strawberry Syrup-Case' at $96 drew '1500 Pack'
    on the first ask and '12.5 L' on the last pass (14 Oct 2026). The prompt
    forbids it; this holds it when the model doesn't."""

    def test_a_forced_size_on_a_sizeless_line_becomes_each(self):
        unit, why = guess_unit(
            {"description": "Strawberry Syrup-Case"},
            UNITS,
            ask_llm=lambda line, cands: None,
            insist=lambda line, cands: "u-6pk",
        )
        assert unit["id"] == "u-each"
        assert "'Each' is Norm's best guess" in why

    def test_a_first_pick_with_an_unstated_size_goes_to_the_last_pass(self):
        asked = []
        unit, _ = guess_unit(
            {"description": "Strawberry Syrup-Case"},
            UNITS,
            ask_llm=lambda line, cands: "u-6x1l",
            insist=lambda line, cands: asked.append(1) or "u-each",
        )
        assert asked and unit["id"] == "u-each"

    def test_a_size_the_line_states_is_kept(self):
        units = UNITS + [{"id": "u-700", "name": "700 mL", "ratio": 0.7}]
        unit, _ = guess_unit(
            {"description": "Monin Vanilla Syrup 700ml"},
            units,
            ask_llm=lambda line, cands: "u-700",
        )
        assert unit["id"] == "u-700"

    def test_a_base_unit_needs_no_number(self):
        # Loose produce is weighed: 'SHALLOTS (PER KG)' is rightly a Kilo.
        unit, _ = guess_unit(
            {"description": "SHALLOTS (PER KG)"},
            UNITS,
            ask_llm=lambda line, cands: "u-kilo",
        )
        assert unit["id"] == "u-kilo"

    def test_with_no_each_an_invented_size_still_parks(self):
        units = [u for u in UNITS if u["id"] != "u-each"]
        unit, _ = guess_unit(
            {"description": "Strawberry Syrup-Case"},
            units,
            insist=lambda line, cands: "u-6pk",
        )
        assert unit is None


class TestTheChooserGetsItsAnswerOut:
    """The chooser used to call the model directly with 64 output tokens; the
    router model spent them reasoning aloud and the JSON never arrived, so the
    parse failed and every hard line parked (both INV-0446 replies, 14 Oct
    2026, began 'Looking at the product …'). It now goes through call_llm."""

    def _patch(self, monkeypatch, reply):
        import app.interpreter.llm_interpreter as llm

        seen = []

        def fake_call_llm(**kw):
            seen.append(kw)
            return llm._parse_response(reply), None

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        monkeypatch.setattr(
            "app.services.models.router_model", lambda db=None, override=None: "m"
        )
        return seen

    def test_an_answer_after_some_reasoning_is_still_read(self, monkeypatch):
        from app.services.unit_guess import llm_chooser

        seen = self._patch(
            monkeypatch,
            "Looking at the product 'Pistachio Jam-Can', a can of jam is a single "
            'item.\n{"unit": 2}',
        )
        cands = [
            {"id": "u-kilo", "name": "Kilo", "ratio": 1.0, "type": "Weight"},
            {"id": "u-each", "name": "Each", "ratio": 1.0, "type": "Count"},
        ]
        ask = llm_chooser(None)
        assert ask({"description": "Pistachio Jam-Can"}, cands) == "u-each"
        assert seen[0]["max_tokens"] >= 400
        assert seen[0]["call_type"] == "unit_guess"
        assert "null" in seen[0]["system_prompt"]  # the first ask may decline
        # A short numbered list with each unit's type — not 36-character ids.
        assert "2. Each (Count, ratio 1.0)" in seen[0]["user_prompt"]
        assert "u-each" not in seen[0]["user_prompt"]

    def test_the_last_pass_is_told_it_may_not_decline(self, monkeypatch):
        from app.services.unit_guess import llm_chooser

        seen = self._patch(monkeypatch, '{"unit": 1}')
        llm_chooser(None, insist=True)({"description": "x"}, [{"id": "u-each"}])
        assert "MUST pick" in seen[0]["system_prompt"]
        assert "Never null" in seen[0]["system_prompt"]

    def test_a_number_off_the_list_is_no_answer(self, monkeypatch):
        from app.services.unit_guess import llm_chooser

        cands = [{"id": "u-each", "name": "Each"}]
        for reply in ('{"unit": 0}', '{"unit": 2}', '{"unit": null}', '{"unit": "x"}'):
            self._patch(monkeypatch, reply)
            assert llm_chooser(None)({"description": "x"}, cands) is None

    def test_both_passes_refuse_an_unstated_size(self):
        # 'Strawberry Syrup-Case' at $96 drew '1500 Pack' before this rule.
        from app.services.unit_guess import _CHOOSE_SYSTEM, _INSIST_SYSTEM

        for prompt in (_CHOOSE_SYSTEM, _INSIST_SYSTEM):
            assert "only when the line's description or unit states" in prompt
            assert "'Each'" in prompt
