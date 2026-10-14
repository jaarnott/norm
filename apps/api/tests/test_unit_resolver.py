"""unit_resolver — the estimator: the model sizes the product, code finds the unit.

Since 14 Oct 2026 one web-searching call per sizeless line estimates what ONE
delivered unit really is ('454 g'); code — never the model — turns that into
the venue's unit, by type and amount. A size the venue has no unit for comes
back as a name to create, never a link. Category rules trump the model, and
any failure leaves the line unanswered, exactly as if this module didn't
exist. Measured: 93% exact on 45 products whose size the invoice didn't print,
against ~85% for the venues' own Loaded set-ups (test in the commit message).
"""

from app.services.unit_resolver import match_unit, resolve_units, unit_name_for

UNITS = [
    {"id": "u-700", "name": "700 mL", "ratio": 0.7},
    {"id": "u-each", "name": "Each", "ratio": 1.0},
    {"id": "u-litre", "name": "Litre", "ratio": 1.0},
    {"id": "u-1l", "name": "1 L", "ratio": 1.0},
    {"id": "u-kilo", "name": "Kilo", "ratio": 1.0},
    {"id": "u-454", "name": "454 Grams", "ratio": 0.454},
    {"id": "u-6x750", "name": "6x750mL", "ratio": 4.5},
    {"id": "u-pack", "name": "PACK", "ratio": 1.0},
    {"id": "u-dead", "name": "Old", "ratio": 1.0, "datestampDeleted": "2026-01-01"},
]

LINES = [
    {
        "id": "rep-0",
        "code": "4183758",
        "description": "MALFY GIN CON LIMONE 700ML",
        "quantity": 1,
        "unit": "EA",
        "unit_of_measure": "700ml",
        "unit_price": 54.88,
    },
    {
        "id": "rep-1",
        "code": "4230513",
        "description": "MALFY GIN ROSA PINK GRAPEF",
        "quantity": 1,
        "unit": "EA",
        "unit_of_measure": None,
        "unit_price": 54.88,
    },
]


def _answer(size="700 mL", confidence="high", why="sibling 700ML at the same price"):
    return lambda prompt: {"size": size, "confidence": confidence, "why": why}


class TestTheEstimate:
    def test_the_model_sees_the_invoice_and_the_line(self):
        seen = []

        def ask(prompt):
            seen.append(prompt)
            return {"size": "700 mL", "confidence": "high", "why": "sibling"}

        out = resolve_units(
            LINES, ["rep-1"], UNITS, supplier_name="Trents", ask_llm=ask
        )
        assert len(seen) == 1  # one call per line asked about
        p = seen[0]
        assert "Supplier: Trents" in p
        # the rest of the invoice is the price scale, sizes and all
        assert "MALFY GIN CON LIMONE 700ML @ $54.88 (printed size 700ml)" in p
        assert '"description": "MALFY GIN ROSA PINK GRAPEF"' in p
        assert '"charge_word": "EA"' in p
        assert out["rep-1"]["unit"]["id"] == "u-700"
        assert out["rep-1"]["size"] == "700 mL"
        assert out["rep-1"]["confidence"] == "high"

    def test_each_line_is_asked_on_its_own(self):
        asked = []
        resolve_units(
            LINES,
            ["rep-0", "rep-1"],
            UNITS,
            ask_llm=lambda p: asked.append(p) or {"size": "1 kg", "confidence": "high"},
        )
        assert len(asked) == 2

    def test_a_size_the_venue_lacks_is_a_unit_to_create(self):
        out = resolve_units(LINES, ["rep-1"], UNITS, ask_llm=_answer(size="2.5 kg"))
        assert out["rep-1"]["unit"] is None
        assert out["rep-1"]["create_name"] == "2.5 KG"

    def test_no_answer_unless_it_is_a_size(self):
        for size in ("carton", "PACK", "", None, "a big one"):
            assert (
                resolve_units(LINES, ["rep-1"], UNITS, ask_llm=_answer(size=size)) == {}
            )

    def test_category_rules_trump_the_model(self):
        # Packaging is a count, so a VOLUME answer for it is invalid whatever
        # the model's confidence.
        out = resolve_units(
            LINES,
            ["rep-1"],
            UNITS,
            category_by_line={"rep-1": "packaging"},
            ask_llm=_answer(size="700 mL"),
        )
        r = out["rep-1"]
        assert r["unit"] is None and r["create_name"] is None
        assert r["confidence"] == "low"

    def test_a_beverage_may_be_a_volume_or_a_count(self):
        # Poured drinks are volumes, but a drink sold as an each is a count —
        # the category bans neither (27 Aug 2026).
        for size, unit in (("700 mL", "u-700"), ("each", "u-each")):
            out = resolve_units(
                LINES,
                ["rep-1"],
                UNITS,
                category_by_line={"rep-1": "beverage"},
                ask_llm=_answer(size=size),
            )
            assert out["rep-1"]["unit"]["id"] == unit

    def test_garbage_confidence_reads_as_low(self):
        out = resolve_units(
            LINES, ["rep-1"], UNITS, ask_llm=_answer(confidence="sure!")
        )
        assert out["rep-1"]["confidence"] == "low"

    def test_any_failure_is_no_answer_for_that_line(self):
        def boom(prompt):
            raise RuntimeError("no")

        assert resolve_units(LINES, ["rep-1"], UNITS, ask_llm=boom) == {}
        assert resolve_units(LINES, ["rep-1"], UNITS, ask_llm=lambda p: "text") == {}
        assert resolve_units(LINES, [], UNITS, ask_llm=_answer()) == {}

    def test_an_answer_is_asked_for_once_per_invoice_line(self, db_session):
        # Re-opening a card re-reviews the invoice; the estimate is cached on
        # the line as printed, so that costs nothing the second time.
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return {"size": "700 mL", "confidence": "high", "why": "x"}

        for _ in range(2):
            out = resolve_units(LINES, ["rep-1"], UNITS, ask_llm=ask, db=db_session)
            assert out["rep-1"]["unit"]["id"] == "u-700"
        assert len(asked) == 1


class TestCodeFindsTheUnit:
    """The model's size → the venue's unit, by type and amount only."""

    def test_same_amount_same_type(self):
        assert match_unit("0.7 L", UNITS)["id"] == "u-700"
        assert match_unit("454 g", UNITS)["id"] == "u-454"
        assert match_unit("1 kg", UNITS)["id"] == "u-kilo"
        assert match_unit("4.5 L", UNITS)["id"] == "u-6x750"  # a multipack's total
        assert match_unit("6x750mL", UNITS)["id"] == "u-6x750"

    def test_one_litre_is_never_each(self):
        # The loose 'equivalent' rule reads a single bottle as Each; picking a
        # unit must not (Coconut Milk 1L landed on Each in testing).
        assert match_unit("1 L", UNITS)["id"] in ("u-1l", "u-litre")
        assert match_unit("each", UNITS)["id"] == "u-each"

    def test_the_size_as_written_then_a_base_unit(self):
        assert match_unit("1 L", UNITS)["id"] == "u-1l"
        assert match_unit("1 litre", UNITS)["id"] == "u-litre"

    def test_nothing_that_merely_resembles(self):
        assert match_unit("750 mL", UNITS) is None
        assert match_unit("a bag", UNITS) is None

    def test_new_unit_names_follow_the_venues_style(self):
        assert unit_name_for("454 g") == "454 Grams"
        assert unit_name_for("2.5kg") == "2.5 KG"
        assert unit_name_for("750ml") == "750 mL"
        assert unit_name_for("4.5 L") == "4.5 L"
        assert unit_name_for("24 each") == "24 Pack"
        assert unit_name_for("each") == "Each"
        assert unit_name_for("6x750mL") == "6x750mL"
        assert unit_name_for("a bag") is None
