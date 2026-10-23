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


from types import SimpleNamespace  # noqa: E402

import base64  # noqa: E402


def _tiny_jpeg() -> bytes:
    """A real 1x1 JPEG — build_content_block re-encodes images, so the bytes
    have to actually decode."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, format="JPEG")
    return buf.getvalue()


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
    def test_schema_keys_are_frozen(self):
        # PDF_SCHEMA is extraction-CACHE-KEY material (function_executor hashes
        # it with the instructions), and the dojo's candidate harness can only
        # override instruction TEXT — so a schema edit silently invalidates
        # every cached extraction with no way to score the blast radius first.
        # Teaching goes in BUILTIN_MAIN_PROMPT, which does have a harness.
        #
        # 17 Aug 2026: the legacy catch-all `purchase_order_number` was
        # DELIBERATELY retired (redundant with customer_…, consumed by
        # nothing, and its null read as phantom dojo failures). The one-time
        # cache invalidation was accepted: invoices re-extract lazily on next
        # touch, and readers keep a legacy fallback for pre-retirement data.
        #
        # 22 Oct 2026: `due_date` DELIBERATELY added, so a venue's "only when
        # the invoice has no due date" receiving rule can tell the two apart
        # (services/receiving_rules). One-time re-read accepted: ~39 open
        # invoices in production, about NZ$4; a read without the field leaves
        # such a rule untouched (`due_date_read`).
        assert set(ie.PDF_SCHEMA) == {
            "document_type",
            "invoice_number",
            "invoice_date",
            "due_date",
            "supplier_name",
            "supplier_differs",
            "customer_purchase_order_number",
            "supplier_order_number",
            "subtotal_ex_tax",
            "discount_amount",
            "tax_amount",
            "total_incl_tax",
            "lines",
        }
        # PO_EXTRACT_SCHEMA is retired: ONE extraction reads the buyer PO and
        # the supplier's own order number (both above) alongside the lines.
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

    def test_credit_notes_are_taught_in_the_prompt(self):
        # The schema has always ALLOWED 'credit_note'; nothing taught the
        # model to use it, or what to do with the signs. Credit notes now
        # auto-receive when clean, so the classification must be specified
        # behaviour rather than emergent.
        text = ie.BUILTIN_MAIN_PROMPT.lower()
        assert "credit_note" in text
        assert "credit note" in text
        assert "exactly as printed" in text  # never invent or drop a minus


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
        # ...unless the row is empty, which falls back to the built-in.
        row = SupplierInvoiceSpec(
            name=ie.MAIN_PROMPT_NAME, aliases=[], instructions="  "
        )
        db_session.add(row)
        db_session.flush()
        assert ie.main_prompt(db_session) == ie.BUILTIN_MAIN_PROMPT
        row.instructions = "ADMIN MAIN"
        db_session.flush()
        assert ie.main_prompt(db_session) == "ADMIN MAIN"

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


class TestTheCopyIsSentAsWhateverItActuallyIs:
    """Loaded attaches whatever the supplier sent, and plenty of it is a photo.

    SABATO 1110818 and RESOTECH 253769 are genuine 2.5MB JPEGs. Every copy used
    to be sent as an Anthropic `document` block, which accepts application/pdf
    ONLY — so those 400'd on every run, forever, and were reported as
    "unreadable copy" (12 of 39 such findings, 24-25 Aug 2026).
    """

    def _block_for(self, monkeypatch, data, content_type):
        import app.services.invoice_extraction as IE

        seen = {}

        def _fake_call_llm(**kw):
            seen["documents"] = kw.get("documents")
            return {"invoice_number": "IN1"}, None

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", _fake_call_llm)
        monkeypatch.setattr(IE, "_extraction_cache_put", lambda *a, **k: None)

        lh = SimpleNamespace(
            file_base64=lambda fid: (base64.b64encode(data).decode(), content_type)
        )
        IE._extract_uncached(None, lh, "f-1", "instructions", "key", None)
        return seen["documents"][0]

    def test_a_pdf_is_still_a_document(self, monkeypatch):
        block = self._block_for(monkeypatch, b"%PDF-1.4 body", "application/pdf")
        assert block["type"] == "document"
        assert block["source"]["media_type"] == "application/pdf"

    def test_a_jpeg_is_sent_as_an_image(self, monkeypatch):
        jpeg = _tiny_jpeg()
        block = self._block_for(monkeypatch, jpeg, "image/jpeg")
        assert block["type"] == "image"
        assert block["source"]["media_type"].startswith("image/")

    def test_the_bytes_beat_a_lying_header(self, monkeypatch):
        """A supplier's server calling a JPEG application/pdf must not put us
        back where we started."""
        block = self._block_for(monkeypatch, _tiny_jpeg(), "application/pdf")
        assert block["type"] == "image"

    def test_a_generic_header_is_resolved_by_the_bytes(self, monkeypatch):
        block = self._block_for(
            monkeypatch, b"%PDF-1.4 body", "application/octet-stream"
        )
        assert block["type"] == "document"
        assert block["source"]["media_type"] == "application/pdf"


class TestFailuresSayWhoseFaultTheyAre:
    def test_a_transient_failure_is_labelled(self):
        import anthropic
        import httpx

        from app.services.invoice_extraction import _error_result

        exc = anthropic.APIStatusError(
            message="overloaded_error",
            response=httpx.Response(529, request=httpx.Request("POST", "https://x")),
            body={"type": "error", "error": {"type": "overloaded_error"}},
        )
        assert _error_result(exc)["transient"] is True

    def test_a_bad_document_is_not_labelled_transient(self):
        """A 400 IS the document's fault and must keep reading as unreadable."""
        import anthropic
        import httpx

        from app.services.invoice_extraction import _error_result

        exc = anthropic.APIStatusError(
            message="invalid_request_error",
            response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
            body={"type": "error", "error": {"type": "invalid_request_error"}},
        )
        assert "transient" not in _error_result(exc)


class TestSupplierDetails:
    """The second, small read made only when Norm will CREATE a supplier: the
    business's own address and contacts, for the new Loaded record."""

    def _patch_llm(self, monkeypatch, result=None, fail=False):
        calls = []

        def fake_call_llm(**kwargs):
            calls.append(kwargs)
            if fail:
                raise RuntimeError("Anthropic 529")
            return result, None

        import app.interpreter.llm_interpreter as llm

        monkeypatch.setattr(llm, "call_llm", fake_call_llm)
        return calls

    def test_reads_once_then_serves_from_cache(self, db_session, monkeypatch):
        calls = self._patch_llm(
            monkeypatch, {"city": "Auckland", "email": "Orders@Tickety.co.nz"}
        )
        lh = _FakeLoaded()
        first = ie.read_supplier_details(
            db_session, lh, "file-1", supplier_name="Tickety-Boo Ltd"
        )
        assert first == {"city": "Auckland", "email": "orders@tickety.co.nz"}
        # The supplier is named, and the buyer's blocks are ruled out.
        assert "'Tickety-Boo Ltd'" in calls[0]["user_prompt"]
        assert "bill-to" in calls[0]["user_prompt"]
        second = ie.read_supplier_details(
            db_session, lh, "file-1", supplier_name="Tickety-Boo Ltd"
        )
        assert second == first
        assert len(calls) == 1 and lh.downloads == ["file-1"]

    def test_the_main_extraction_cache_is_untouched(self, db_session, monkeypatch):
        # Its own cache action, so reset-validation (which clears
        # download_invoice_file rows) and PDF_SCHEMA's key never see it.
        self._patch_llm(monkeypatch, {"city": "Auckland"})

        def count(action):
            return (
                db_session.query(DocumentExtraction)
                .filter(DocumentExtraction.action == action)
                .count()
            )

        main, own = count("download_invoice_file"), count("read_supplier_details")
        ie.read_supplier_details(
            db_session, _FakeLoaded(), "file-1", supplier_name="Tickety-Boo Ltd"
        )
        assert count("download_invoice_file") == main
        assert count("read_supplier_details") == own + 1

    def test_a_failure_is_an_empty_answer_and_not_cached(self, db_session, monkeypatch):
        self._patch_llm(monkeypatch, fail=True)
        out = ie.read_supplier_details(
            db_session, _FakeLoaded(), "file-1", supplier_name="Tickety-Boo Ltd"
        )
        assert out == {}
        assert (
            db_session.query(DocumentExtraction)
            .filter(DocumentExtraction.action == "read_supplier_details")
            .count()
            == 0
        )
        assert (
            ie.read_supplier_details(
                db_session, _FakeLoaded(fail=True), "file-1", supplier_name="X"
            )
            == {}
        )

    def test_nothing_is_read_without_a_file_or_a_name(self, db_session, monkeypatch):
        calls = self._patch_llm(monkeypatch, {"city": "Auckland"})
        assert ie.read_supplier_details(db_session, None, "", supplier_name="X") == {}
        assert (
            ie.read_supplier_details(db_session, None, "file-1", supplier_name="") == {}
        )
        assert calls == []

    def test_only_plausible_values_survive(self):
        assert ie.clean_supplier_details(
            {
                "address_lines": "  12   Wharf Road ",
                "city": None,
                "postcode": 1010,
                "phone": True,
                "email": "not an email",
                "contact_name": "x" * 200,
                "account_number": " CB-0042 ",
            }
        ) == {
            "address_lines": ["12 Wharf Road"],
            "postcode": "1010",
            "account_number": "CB-0042",
        }
        assert ie.clean_supplier_details(
            {"address_lines": ["a", "", "b", "c", "d"]}
        ) == {"address_lines": ["a", "b", "c"]}
        assert ie.clean_supplier_details(None) == {}


class TestFeedInvoices:
    """Ordermentum invoices arrive as the supplier's own data with no PDF."""

    DETAIL = {
        "referenceNumber": "OMI1107031",
        "issuedAt": "2026-10-14",
        "subtotal": 120.95,
        "discountAmount": 0.0,
        "taxAmount": 12.09,
        "total": 133.04,
        "supplierName": "Sonoma Baking Company",
        "purchaseOrderNumber": None,
        "fileId": None,
        "source": "Ordermentum",
        "lines": [
            {
                "code": "30132",
                "description": "Croissant Plain 6 PCK",
                "unit": "unit",
                "quantityReceived": 1.0,
                "unitCostExclTax": 14.96,
                "totalCostExclTax": 14.96,
                "saleTaxRate": 0.1,
            },
            {"code": "X", "description": "gone", "deletedAt": "2026-10-14"},
        ],
    }

    def test_recognised_only_with_no_copy_and_the_feed_source(self):
        assert ie.is_feed_invoice(self.DETAIL)
        assert not ie.is_feed_invoice({**self.DETAIL, "fileId": "f-1"})
        for source in ("Email", "Upload", "Po", "Manual", None):
            assert not ie.is_feed_invoice({**self.DETAIL, "source": source})

    def test_loadeds_data_fills_the_same_slots_as_a_read(self):
        out = ie.extraction_from_loaded(self.DETAIL)
        assert set(out) == set(ie.PDF_SCHEMA)
        # The same slots a read fills, plus the marks that the unit and the
        # brand are Loaded's own — never taken as the invoice's charge word
        # (owner, 18 Oct 2026), never a reason to change a brand (19 Oct).
        assert set(out["lines"][0]) - {"unit_from_loaded", "brand_from_loaded"} == set(
            ie.PDF_SCHEMA["lines"][0]
        )
        assert out["lines"][0]["unit_from_loaded"] is True
        assert out["lines"][0]["brand_from_loaded"] is True
        assert out["supplier_name"] == "Sonoma Baking Company"
        assert out["invoice_number"] == "OMI1107031"
        assert (out["subtotal_ex_tax"], out["tax_amount"], out["total_incl_tax"]) == (
            120.95,
            12.09,
            133.04,
        )
        assert out["discount_amount"] is None
        assert out["document_type"] == "invoice"
        [line] = out["lines"]  # the deleted line is left out
        assert line["code"] == "30132"
        assert line["description"] == "Croissant Plain 6 PCK"
        assert (
            line["quantity"],
            line["unit_price_ex_tax"],
            line["line_total_ex_tax"],
        ) == (
            1.0,
            14.96,
            14.96,
        )


class TestOrdermentumOrderNumbers:
    """The order number a supplier's copy of an Ordermentum order carries —
    which copy is its other half is decided in services/ordermentum_pairs
    (tests/test_ordermentum_pairs.py)."""

    def test_the_order_number_is_found_wherever_the_supplier_prints_it(self):
        assert ie.ordermentum_order_number(None, "OMO1073025") == "OMO1073025"
        assert ie.ordermentum_order_number("PO: omo 29044", "133714") == "OMO29044"
        assert ie.ordermentum_order_number("1520001", "3693436") is None
        assert ie.ordermentum_order_number("OMI1072575") is None
