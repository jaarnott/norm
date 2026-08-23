"""The dojo verdict must fold in the document's own arithmetic.

A sample that matches its stored baseline but whose totals don't add up is not a
clean pass — that is how a broken invoice (Lion 94793550: lines 1217.29 vs a
subtotal of 1212.35) used to slip through as "PASS". The reconciliation is
discount-aware, mirroring the receive-flow identity subtotal + tax - discount.
"""

from app.services.spec_dojo import _ground_truth_violations, _sample_status


def _doc(subtotal, tax, total, discount=None, lines=None):
    d = {"subtotal_ex_tax": subtotal, "tax_amount": tax, "total_incl_tax": total}
    if discount is not None:
        d["discount_amount"] = discount
    d["lines"] = lines or []
    return d


class TestGroundTruthViolations:
    def test_reconciling_totals_have_no_violation(self):
        assert _ground_truth_violations(_doc(1000.0, 150.0, 1150.0)) == []

    def test_discount_is_subtracted_from_the_total_identity(self):
        # subtotal 1000 + tax 150 - discount 100 == total 1050 → reconciles.
        # (Without discount-awareness this would falsely flag 1150 != 1050.)
        assert (
            _ground_truth_violations(_doc(1000.0, 150.0, 1050.0, discount=100.0)) == []
        )

    def test_non_reconciling_total_is_flagged(self):
        out = _ground_truth_violations(_doc(1212.35, 182.59, 1399.88, discount=156.38))
        assert any("total_incl_tax" in v for v in out)

    def test_lines_not_summing_to_subtotal_is_flagged(self):
        doc = _doc(
            1212.35,
            182.59,
            1399.88,
            lines=[{"line_total_ex_tax": 1217.29}],
        )
        out = _ground_truth_violations(doc)
        assert any("subtotal_ex_tax" in v for v in out)


class TestSampleStatus:
    def test_no_baseline_is_new(self):
        assert _sample_status(None, [], _doc(1000.0, 150.0, 1150.0)) == "new"

    def test_baseline_mismatch_fails(self):
        assert (
            _sample_status({}, [{"field": "x"}], _doc(1000.0, 150.0, 1150.0)) == "fail"
        )

    def test_clean_and_reconciling_passes(self):
        assert _sample_status({}, [], _doc(1000.0, 150.0, 1150.0)) == "pass"

    def test_clean_but_not_reconciling_fails(self):
        # Matches the baseline yet the arithmetic is broken → not a pass.
        assert _sample_status({}, [], _doc(1212.35, 182.59, 1399.88)) == "fail"
