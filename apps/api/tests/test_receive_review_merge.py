"""Tests for the ONE review→draft merge path and its embedded surfaces.

``run_review_and_merge`` (routers/invoice_fixes.py) is the single code path
that runs the review engine and merges its artifact onto a draft — used by the
web ``/invoice-fixes/review`` endpoint AND the embedded builder
(app/mcp/receive_display.py). These tests script the engine and assert the
merge contract: per-line fields (including the item-match trio), the version
stamp the /draft gate compares against, and the embedded builder's guard +
model-facing summary.
"""

from types import SimpleNamespace

from app.mcp.receive_display import _attach_suggestions, _suggestion_summary
from app.routers import invoice_fixes as IF


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, result):
        self._result = result

    def query(self, *a, **k):
        return _FakeQuery(self._result)


def _spec_with_review_tool():
    return SimpleNamespace(
        tools=[
            {
                "action": "review_and_receive_invoices",
                "consolidator_config": {"function_code": "def run(...): ..."},
            }
        ]
    )


class TestRunReviewAndMerge:
    def _run(self, monkeypatch, data, fx):
        monkeypatch.setattr(
            "app.agents.internal_tools.execute_consolidator",
            lambda cfg, params, db, tid: {"data": {"fix_invoices": [fx] if fx else []}},
        )
        db = _FakeSession(SimpleNamespace(id="v-1", name="La Zeppa", timezone=None))
        config_db = _FakeSession(_spec_with_review_tool())
        IF.run_review_and_merge(data, "v-1", "inv-1", db, config_db)
        return data

    def test_merges_line_fields_including_item_match(self, monkeypatch):
        data = {
            "loaded_invoice_fingerprint": 7,
            "lines": [{"id": "l-1"}, {"id": "l-2"}],
        }
        fx = {
            "checks": "ppp",
            "check_reasons": ["r1"],
            "suggestions": [{"type": "link_po", "summary": "Link PO 1"}],
            "lines": [
                {
                    "id": "l-1",
                    "copy_quantity": 4,
                    "matched_item": {"id": "i-9", "name": "X"},
                    "suggested_name": None,
                    "suggested_group_id": None,
                }
            ],
        }
        self._run(monkeypatch, data, fx)
        ln = data["lines"][0]
        assert ln["copy_quantity"] == 4
        assert ln["matched_item"] == {"id": "i-9", "name": "X"}  # item-match rides in
        assert data["checks"] == "ppp"
        assert data["suggestions"][0]["type"] == "link_po"
        # the /draft version gate compares against this stamp
        assert data["reviewed_invoice_fingerprint"] == 7
        assert data["lines"][1].get("matched_item") is None  # unmatched line untouched

    def test_no_card_records_review_ran(self, monkeypatch):
        # Credit note / no PDF: the review still marks itself done (checks "")
        # and stamps the version so re-opens don't loop.
        data = {"loaded_invoice_fingerprint": 3, "lines": []}
        self._run(monkeypatch, data, None)
        assert data["checks"] == ""
        assert data["suggestions"] == []
        assert data["reviewed_invoice_fingerprint"] == 3


class TestEmbeddedAttachSuggestions:
    def test_cached_checks_skip_the_engine(self, monkeypatch):
        def never(*a, **k):
            raise AssertionError("engine must not run when checks are cached")

        monkeypatch.setattr(IF, "run_review_and_merge", never)
        data = {"checks": "ppp", "invoice_id": "inv-1"}
        _attach_suggestions(data, "v-1", None, None)  # no raise, no run

    def test_no_venue_skips(self, monkeypatch):
        def never(*a, **k):
            raise AssertionError("engine must not run without a venue")

        monkeypatch.setattr(IF, "run_review_and_merge", never)
        _attach_suggestions({"invoice_id": "inv-1"}, None, None, None)

    def test_failure_degrades_to_plain_mirror(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("engine down")

        monkeypatch.setattr(IF, "run_review_and_merge", boom)
        data = {"invoice_id": "inv-1"}
        _attach_suggestions(data, "v-1", None, None)  # swallowed
        assert "checks" not in data


class TestSuggestionSummary:
    def test_composes_checks_reasons_fixes_and_new_items(self):
        data = {
            "checks": "ppfs",
            "check_reasons": ["Line 'X': quantity 1 does not equal 2"],
            "suggestions": [{"type": "unit", "summary": "unit Kilo → 500g"}],
            "lines": [
                {"id": "a", "linked_item_id": "i-1"},  # linked → not mentioned
                {
                    "id": "b",
                    "description": "MYSTERY SAUCE",
                    "matched_item": {"name": "SAUCE MYSTERY", "group": "Sauces"},
                },
                {"id": "c", "description": "NEW THING", "suggested_name": "Thing New"},
                {"id": "d", "description": "BARE"},
            ],
        }
        s = _suggestion_summary(data)
        assert "2 passed, 1 failed, 1 suggested change(s)" in s
        assert "quantity 1 does not equal 2" in s
        assert "unit Kilo → 500g" in s
        assert "likely matches existing 'SAUCE MYSTERY' (Sauces)" in s
        assert "create as 'Thing New'" in s
        assert "'BARE': must be linked or created" in s

    def test_nothing_to_say_returns_none(self):
        assert (
            _suggestion_summary({"lines": [{"id": "a", "linked_item_id": "x"}]}) is None
        )


class TestNewSuggestionTypesSurviveMerge:
    """copy_missing (per-line remove affordance) and add_line suggestions must
    reach the editor through run_review_and_merge."""

    def _run(self, monkeypatch, data, fx):
        monkeypatch.setattr(
            "app.agents.internal_tools.execute_consolidator",
            lambda cfg, params, db, tid: {"data": {"fix_invoices": [fx] if fx else []}},
        )
        db = _FakeSession(SimpleNamespace(id="v-1", name="La Zeppa", timezone=None))
        config_db = _FakeSession(_spec_with_review_tool())
        IF.run_review_and_merge(data, "v-1", "inv-1", db, config_db)
        return data

    def test_copy_missing_and_add_line_merge(self, monkeypatch):
        data = {"loaded_invoice_fingerprint": 7, "lines": [{"id": "l-1"}]}
        fx = {
            "checks": "ppf",
            "check_reasons": ["Line 'X' not found on the attached invoice document"],
            "suggestions": [
                {
                    "type": "add_line",
                    "description": "Atlanta Bright IPA 4.6% 50L Keg",
                    "quantity": 1,
                    "unit_price_ex_tax": 340.0,
                    "line_total_ex_tax": 340.0,
                    "sale_tax_rate": 0.15,
                    "summary": "Add 'Atlanta Bright IPA 4.6% 50L Keg' ($340.00) from the invoice copy",
                }
            ],
            "lines": [
                {"id": "l-1", "copy_missing": True, "unit_needs_confirmation": True}
            ],
        }
        self._run(monkeypatch, data, fx)
        assert data["lines"][0]["copy_missing"] is True
        assert data["lines"][0]["unit_needs_confirmation"] is True
        adds = [s for s in data["suggestions"] if s["type"] == "add_line"]
        assert len(adds) == 1 and adds[0]["sale_tax_rate"] == 0.15
