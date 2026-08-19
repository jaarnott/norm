"""invoice_arbiter — validated LLM arbitration, or nothing.

The validators are the point: a verdict that doesn't reconcile (totals) or
isn't a proper matching (pairing) is rejected WHOLESALE, so the model can
never make anything worse than the standing blocker.
"""

from app.services.invoice_arbiter import arbitrate_pairing, diagnose_totals

LINES = [
    {
        "id": "rep-0",
        "description": "A",
        "quantity": 2,
        "unit_price": 10.0,
        "line_total": 20.0,
    },
    # misread: prints 90.00 but 2 x 10 = 20 — the single wrong figure
    {
        "id": "rep-1",
        "description": "B",
        "quantity": 2,
        "unit_price": 10.0,
        "line_total": 90.0,
    },
]
HEADER = {"subtotal": 40.0, "tax": 6.0, "discount": None, "total": 46.0}


def _verdict(**over):
    v = {
        "corrections": [
            {
                "scope": "line",
                "line_id": "rep-1",
                "field": "line_total",
                "current": 90.0,
                "proposed": 20.0,
            }
        ],
        "confidence": "high",
        "why": "2 x 10.00 = 20.00; the printed 90.00 fails the line check",
    }
    v.update(over)
    return v


class TestDiagnoseTotals:
    def test_a_reconciling_correction_is_accepted(self):
        out = diagnose_totals(
            LINES,
            HEADER,
            ["lines add to 110 but subtotal reads 40"],
            ask_llm=lambda p: _verdict(),
        )
        assert out["confidence"] == "high"
        c = out["corrections"][0]
        assert (c["line_id"], c["field"], c["proposed"]) == (
            "rep-1",
            "line_total",
            20.0,
        )

    def test_a_correction_that_does_not_reconcile_is_rejected_wholesale(self):
        out = diagnose_totals(
            LINES,
            HEADER,
            ["fail"],
            ask_llm=lambda p: _verdict(
                corrections=[
                    {
                        "scope": "line",
                        "line_id": "rep-1",
                        "field": "line_total",
                        "current": 90.0,
                        "proposed": 55.0,
                    }  # still 2 x 10 ≠ 55
                ]
            ),
        )
        assert out == {}

    def test_unknown_fields_lines_and_low_confidence_reject(self):
        assert (
            diagnose_totals(
                LINES,
                HEADER,
                ["fail"],
                ask_llm=lambda p: _verdict(
                    corrections=[
                        {
                            "scope": "line",
                            "line_id": "rep-9",
                            "field": "line_total",
                            "proposed": 20.0,
                        }
                    ]
                ),
            )
            == {}
        )
        assert (
            diagnose_totals(
                LINES,
                HEADER,
                ["fail"],
                ask_llm=lambda p: _verdict(
                    corrections=[
                        {
                            "scope": "line",
                            "line_id": "rep-1",
                            "field": "sneaky",
                            "proposed": 20.0,
                        }
                    ]
                ),
            )
            == {}
        )
        assert (
            diagnose_totals(
                LINES, HEADER, ["fail"], ask_llm=lambda p: _verdict(confidence="low")
            )
            == {}
        )

    def test_header_corrections_reconcile_too(self):
        # The lines are right; the printed subtotal was misread.
        lines = [
            {
                "id": "rep-0",
                "description": "A",
                "quantity": 2,
                "unit_price": 10.0,
                "line_total": 20.0,
            }
        ]
        header = {"subtotal": 90.0, "tax": 3.0, "discount": None, "total": 23.0}
        out = diagnose_totals(
            lines,
            header,
            ["subtotal 90 fails"],
            ask_llm=lambda p: _verdict(
                corrections=[
                    {
                        "scope": "header",
                        "line_id": None,
                        "field": "subtotal",
                        "current": 90.0,
                        "proposed": 20.0,
                    }
                ]
            ),
        )
        assert out["corrections"][0]["scope"] == "header"

    def test_any_failure_is_empty(self):
        def boom(p):
            raise RuntimeError("no")

        assert diagnose_totals(LINES, HEADER, ["x"], ask_llm=boom) == {}
        assert diagnose_totals([], HEADER, ["x"], ask_llm=lambda p: _verdict()) == {}


COPY = [
    {"id": "rep-0", "description": "COKE 330ML", "quantity": 2},
    {"id": "rep-1", "description": "COKE 330ML", "quantity": 5},
]
CANDS = [
    {"id": "ld-1", "description": "COKE 330ML", "quantity": 2},
    {"id": "ld-2", "description": "COKE 330ML", "quantity": 5},
]


class TestArbitratePairing:
    def test_a_decisive_proper_matching_is_adopted(self):
        out = arbitrate_pairing(
            COPY,
            CANDS,
            ask_llm=lambda p: {
                "pairs": {"rep-0": "ld-1", "rep-1": "ld-2"},
                "confidence": "high",
                "why": "quantities 2 and 5 disambiguate the identical lines",
            },
        )
        assert out["pairs"] == {"rep-0": "ld-1", "rep-1": "ld-2"}

    def test_medium_confidence_leaves_the_ambiguity_for_a_person(self):
        out = arbitrate_pairing(
            COPY,
            CANDS,
            ask_llm=lambda p: {
                "pairs": {"rep-0": "ld-1", "rep-1": "ld-2"},
                "confidence": "medium",
                "why": "probably",
            },
        )
        assert out == {}

    def test_double_use_foreign_ids_and_partial_matchings_reject(self):
        base = {"confidence": "high", "why": "x"}
        assert (
            arbitrate_pairing(
                COPY,
                CANDS,
                ask_llm=lambda p: {**base, "pairs": {"rep-0": "ld-1", "rep-1": "ld-1"}},
            )
            == {}
        )  # double use
        assert (
            arbitrate_pairing(
                COPY,
                CANDS,
                ask_llm=lambda p: {
                    **base,
                    "pairs": {"rep-0": "ld-1", "rep-1": "ld-99"},
                },
            )
            == {}
        )  # foreign id
        assert (
            arbitrate_pairing(
                COPY,
                CANDS,
                ask_llm=lambda p: {**base, "pairs": {"rep-0": "ld-1"}},
            )
            == {}
        )  # partial — half-right pairs the wrong cost to the wrong line
