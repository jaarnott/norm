"""unit_resolver — the batched residue call: choose, never invent.

The model only ever picks from the venue's real units (or names a unit to
CREATE explicitly); its confidence is part of the answer, and category rules
(beverage ⇒ volume) trump it. Failure of any kind returns {} — the caller
parks the line as unit_missing exactly as if this module did not exist.
"""

from app.services.unit_resolver import resolve_units

UNITS = [
    {"id": "u-700", "name": "700 mL", "ratio": 1.0},
    {"id": "u-each", "name": "Each", "ratio": 1.0},
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


def _answer(**over):
    row = {
        "line_id": "rep-1",
        "unit_id": "u-700",
        "create_name": None,
        "confidence": "high",
        "why": "sibling 700ML at the same price",
    }
    row.update(over)
    return {"lines": [row]}


class TestTheCall:
    def test_the_model_sees_the_whole_invoice_and_clean_candidates(self):
        seen = {}

        def ask(payload):
            seen.update(payload)
            return _answer()

        out = resolve_units(LINES, ["rep-1"], UNITS, ask_llm=ask)
        # every line rides as context; only the sizeless one is to resolve
        assert [ln["id"] for ln in seen["invoice_lines"]] == ["rep-0", "rep-1"]
        assert seen["resolve"] == ["rep-1"]
        # packaging-word and deleted units are never candidates
        names = [u["name"] for u in seen["units"]]
        assert "PACK" not in names and "Old" not in names
        assert out["rep-1"]["unit"]["id"] == "u-700"
        assert out["rep-1"]["confidence"] == "high"

    def test_an_id_off_the_list_is_refused(self):
        out = resolve_units(
            LINES, ["rep-1"], UNITS, ask_llm=lambda p: _answer(unit_id="u-invented")
        )
        r = out["rep-1"]
        assert r["unit"] is None and r["confidence"] == "low"

    def test_create_name_is_the_sanctioned_escape_hatch(self):
        out = resolve_units(
            LINES,
            ["rep-1"],
            UNITS,
            ask_llm=lambda p: _answer(unit_id=None, create_name="750ml"),
        )
        assert out["rep-1"]["unit"] is None
        assert out["rep-1"]["create_name"] == "750ml"
        # ...but never a packaging word through the back door
        out = resolve_units(
            LINES,
            ["rep-1"],
            UNITS,
            ask_llm=lambda p: _answer(unit_id=None, create_name="carton"),
        )
        assert out["rep-1"]["create_name"] is None

    def test_category_rules_trump_the_model(self):
        # A beverage picked as a COUNT unit is invalid whatever the model's
        # confidence — beverages are always volumes.
        out = resolve_units(
            LINES,
            ["rep-1"],
            UNITS,
            category_by_line={"rep-1": "beverage"},
            ask_llm=lambda p: _answer(unit_id="u-each"),
        )
        r = out["rep-1"]
        assert r["unit"] is None and r["confidence"] == "low"
        # a volume pick for a beverage is fine
        out = resolve_units(
            LINES,
            ["rep-1"],
            UNITS,
            category_by_line={"rep-1": "beverage"},
            ask_llm=lambda p: _answer(unit_id="u-700"),
        )
        assert out["rep-1"]["unit"]["id"] == "u-700"

    def test_garbage_confidence_reads_as_low(self):
        out = resolve_units(
            LINES, ["rep-1"], UNITS, ask_llm=lambda p: _answer(confidence="sure!")
        )
        assert out["rep-1"]["confidence"] == "low"

    def test_any_failure_is_an_empty_answer(self):
        def boom(payload):
            raise RuntimeError("no")

        assert resolve_units(LINES, ["rep-1"], UNITS, ask_llm=boom) == {}
        assert resolve_units(LINES, ["rep-1"], UNITS, ask_llm=lambda p: "text") == {}
        assert resolve_units(LINES, [], UNITS, ask_llm=lambda p: _answer()) == {}
        # answers for lines nobody asked about are dropped
        out = resolve_units(
            LINES, ["rep-1"], UNITS, ask_llm=lambda p: _answer(line_id="rep-0")
        )
        assert out == {}
