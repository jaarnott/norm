"""invoice_extraction — the canonical schema, composer and cached extract.

This module replaced the consolidator-embedded schema/prompt (and the dojo's
regex scraping of them); these tests pin the composed instruction text (it is
part of the extraction cache key — accidental drift re-extracts every invoice)
and the DocumentExtraction cache behaviour the reset-validation flow depends
on.
"""

from app.db.config_models import SupplierInvoiceSpec
from app.db.models import DocumentExtraction
from app.services import invoice_extraction as ie


class _FakeLoaded:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.downloads: list[str] = []

    def file_base64(self, file_id: str):
        if self.fail:
            raise RuntimeError("file download → 500")
        self.downloads.append(file_id)
        return "QkFTRTY0", "application/pdf"


class TestSchema:
    def test_buyer_po_fields_folded_in(self):
        # PO_EXTRACT_SCHEMA is retired: ONE extraction reads the buyer PO and
        # the supplier's own order number alongside the lines.
        assert "customer_purchase_order_number" in ie.PDF_SCHEMA
        assert "supplier_order_number" in ie.PDF_SCHEMA
        assert "discount_amount" in ie.PDF_SCHEMA
        line = ie.PDF_SCHEMA["lines"][0]
        for key in (
            "code",
            "description",
            "quantity",
            "unit",
            "unit_of_measure",
            "unit_unrecognisable",
            "unit_price_ex_tax",
            "line_total_ex_tax",
        ):
            assert key in line


class TestComposer:
    def test_full_composition_text(self, db_session):
        out = ie.compose_pdf_instructions(
            db_session,
            loaded_supplier="Bidfood",
            loaded_aliases=["Bidfood Chch"],
            spec_notes="NOTES",
            spec_name="Bidfood Spec",
            main_override="MAIN",
        )
        assert out == (
            "MAIN"
            "\n\nLoaded records this invoice's supplier as 'Bidfood'"
            " (also known as: 'Bidfood Chch')"
            ". In supplier_name return the supplier printed on the "
            "document; set supplier_differs true ONLY when that is a "
            "DIFFERENT BUSINESS from ALL of those names (naming "
            "variations are the same business)."
            "\n\nSupplier-specific notes for Bidfood Spec:\nNOTES"
        )

    def test_no_supplier_no_notes_is_main_alone(self, db_session):
        out = ie.compose_pdf_instructions(db_session, main_override="MAIN")
        assert out == "MAIN"

    def test_main_prompt_row_overrides_builtin(self, db_session):
        db_session.add(
            SupplierInvoiceSpec(
                name=ie.MAIN_PROMPT_NAME, aliases=[], instructions="ADMIN MAIN"
            )
        )
        db_session.flush()
        assert ie.main_prompt(db_session) == "ADMIN MAIN"

    def test_empty_main_prompt_row_falls_back_to_builtin(self, db_session):
        db_session.add(
            SupplierInvoiceSpec(name=ie.MAIN_PROMPT_NAME, aliases=[], instructions="  ")
        )
        db_session.flush()
        assert ie.main_prompt(db_session) == ie.BUILTIN_MAIN_PROMPT

    def test_pdf_instructions_for_resolves_spec_notes(self, db_session):
        db_session.add(
            SupplierInvoiceSpec(
                name="Acme", aliases=["Acme Foods"], instructions="ACME NOTES"
            )
        )
        db_session.flush()
        out = ie.pdf_instructions_for(
            db_session, loaded_supplier="Acme Foods Ltd", loaded_aliases=[]
        )
        assert "Supplier-specific notes for Acme:\nACME NOTES" in out
        assert "Loaded records this invoice's supplier as 'Acme Foods Ltd'" in out

    def test_find_spec_skips_main_prompt_and_short_aliases(self, db_session):
        db_session.add_all(
            [
                SupplierInvoiceSpec(
                    name=ie.MAIN_PROMPT_NAME, aliases=[], instructions="M"
                ),
                SupplierInvoiceSpec(name="Ab", aliases=["Zz"], instructions="X"),
                SupplierInvoiceSpec(name="Harbour Fish", aliases=[], instructions="H"),
            ]
        )
        db_session.flush()
        assert ie.find_spec_for_supplier(db_session, "Harbour Fish Dunedin").name == (
            "Harbour Fish"
        )
        assert ie.find_spec_for_supplier(db_session, "Ab") is None
        assert ie.find_spec_for_supplier(db_session, ie.MAIN_PROMPT_NAME) is None


class TestCachedExtract:
    def _patch_llm(self, monkeypatch, result):
        calls = []

        def fake_call_llm(**kwargs):
            calls.append(kwargs)
            return result, None

        import app.interpreter.llm_interpreter as llm

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        return calls

    def test_extracts_then_serves_from_cache(self, db_session, monkeypatch):
        calls = self._patch_llm(monkeypatch, {"invoice_number": "INV-1"})
        lh = _FakeLoaded()
        first = ie.extract_invoice_copy(
            db_session, lh, "file-1", instructions="DO IT", venue_key="Bessie"
        )
        assert first == {"invoice_number": "INV-1"}
        assert len(calls) == 1 and lh.downloads == ["file-1"]
        # Cache row carries the connector/action reset-validation matches on.
        key = ie._cache_key("Bessie", "file-1", "DO IT")
        row = (
            db_session.query(DocumentExtraction)
            .filter(DocumentExtraction.cache_key == key)
            .one()
        )
        assert row.connector == "loadedhub"
        assert row.action == "download_invoice_file"
        second = ie.extract_invoice_copy(
            db_session, lh, "file-1", instructions="DO IT", venue_key="Bessie"
        )
        assert second == first
        assert len(calls) == 1 and lh.downloads == ["file-1"]  # cache hit

    def test_different_instructions_re_extract(self, db_session, monkeypatch):
        calls = self._patch_llm(monkeypatch, {"invoice_number": "INV-1"})
        lh = _FakeLoaded()
        ie.extract_invoice_copy(db_session, lh, "file-1", instructions="A")
        ie.extract_invoice_copy(db_session, lh, "file-1", instructions="B")
        assert len(calls) == 2  # instructions are part of the cache key

    def test_error_results_never_cached(self, db_session, monkeypatch):
        self._patch_llm(monkeypatch, {"error": "unreadable"})
        lh = _FakeLoaded()
        out = ie.extract_invoice_copy(db_session, lh, "file-1", instructions="X")
        assert out == {"error": "unreadable"}
        key = ie._cache_key(None, "file-1", "X")
        assert (
            db_session.query(DocumentExtraction)
            .filter(DocumentExtraction.cache_key == key)
            .count()
            == 0
        )

    def test_download_failure_returns_error_dict(self, db_session, monkeypatch):
        self._patch_llm(monkeypatch, {"invoice_number": "never reached"})
        out = ie.extract_invoice_copy(
            db_session, _FakeLoaded(fail=True), "file-1", instructions="X"
        )
        assert "error" in out
        key = ie._cache_key(None, "file-1", "X")
        assert (
            db_session.query(DocumentExtraction)
            .filter(DocumentExtraction.cache_key == key)
            .count()
            == 0
        )
