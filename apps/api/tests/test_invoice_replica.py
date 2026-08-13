"""invoice_replica.build_replica — extraction → resolved working document.

All reference data injected (no network): the keyword overrides exist for
exactly this and for dojo batch caching. The LLM matcher is a stub.
"""

from app.services.supplier_identity import resolve_supplier
from app.services.invoice_replica import (
    _resolve_unit_record,
    build_replica,
)

CATALOGUE = [
    {
        "id": "item-salmon",
        "name": "SALMON FILLET",
        "globalSalesTaxSortOrder": 1,
        "defaultBrandId": None,
        "suppliers": [
            {
                "supplierId": "sup-akaroa",
                "stockCode": "PBO0.7",
                "unitId": "u-kilo",
                "unitCost": 44.4,
                "brandId": "brand-akaroa",
                "defaultForSupplier": True,
                "description": "Salmon Fillet Skin On",
            }
        ],
    },
    {
        "id": "item-freight",
        "name": "FREIGHT - FOOD",
        "globalSalesTaxSortOrder": 1,
        "suppliers": [
            {"supplierId": "sup-akaroa", "stockCode": "FGT001", "unitId": "u-each"}
        ],
    },
    {
        "id": "item-exempt",
        "name": "EXEMPT THING",
        "globalSalesTaxSortOrder": 0,
        "suppliers": [
            {"supplierId": "sup-akaroa", "stockCode": "EX1", "unitId": "u-each"}
        ],
    },
]
UNITS = [
    {"id": "u-kilo", "name": "Kilo", "ratio": 1, "stockUnitType": "Weight"},
    {"id": "u-each", "name": "Each", "ratio": 1, "stockUnitType": "Count"},
    {"id": "u-6x750", "name": "6x750mL", "ratio": 4.5, "stockUnitType": "Volume"},
]
SUPPLIERS = [
    {"id": "sup-akaroa", "name": "Akaroa Salmon"},
    {"id": "sup-other", "name": "Totally Different Ltd"},
]
TAX = {0: 0.0, 1: 0.15}

EXTRACTION = {
    "invoice_number": "F55755100",
    "supplier_name": "Akaroa Salmon NZ Ltd",
    "customer_purchase_order_number": None,
    "subtotal_ex_tax": 219.78,
    "tax_amount": 32.97,
    "total_incl_tax": 252.75,
    "lines": [
        {
            "code": "PBO0.7",
            "description": "Salmon Fillet Skin On",
            "quantity": 4.95,
            "unit": "Kilo",
            "unit_of_measure": "Kilo",
            "unit_price_ex_tax": 44.4,
            "line_total_ex_tax": 219.78,
        },
    ],
}


def _build(extraction=EXTRACTION, matcher=None, **over):
    kwargs = dict(
        lh=object(),  # never touched when all reference data is injected
        catalogue=CATALOGUE,
        units=UNITS,
        suppliers=SUPPLIERS,
        tax_rates=TAX,
        aliases_by_id={},
        item_matcher=matcher or (lambda *a, **k: {}),
    )
    kwargs.update(over)
    return build_replica(None, None, "v-1", extraction, **kwargs)


class TestDeterministicResolution:
    def test_full_happy_path(self):
        doc = _build()
        assert doc["replica"] is True
        assert doc["linked_supplier_id"] == "sup-akaroa"  # containment
        ln = doc["lines"][0]
        assert ln["linked_item_id"] == "item-salmon"
        assert ln["matched_by"] == "supplier_code"
        assert ln["linked_unit_id"] == "u-kilo"  # from the variant
        assert ln["unit_ratio"] == 1
        assert ln["linked_brand_id"] == "brand-akaroa"
        assert ln["sale_tax_rate"] == 0.15  # sortOrder 1 via the tax table
        assert ln["item_name"] == "SALMON FILLET"
        assert doc["reference_number"] == "F55755100"
        assert doc["total"] == 252.75
        assert any("supplier_code" in e for e in doc["resolution_log"])

    def test_exempt_item_gets_zero_rate(self):
        ext = dict(
            EXTRACTION,
            lines=[
                {
                    "code": "EX1",
                    "description": "Exempt Thing",
                    "quantity": 1,
                    "unit_price_ex_tax": 10.0,
                    "line_total_ex_tax": 10.0,
                }
            ],
        )
        doc = _build(ext)
        assert doc["lines"][0]["sale_tax_rate"] == 0.0
        assert doc["lines"][0]["tax_amount"] == 0.0

    def test_unmatched_line_takes_prevailing_then_copy_rate(self):
        ext = dict(
            EXTRACTION,
            lines=EXTRACTION["lines"]
            + [
                {
                    "code": None,
                    "description": "Mystery Product Nobody Stocks",
                    "quantity": 1,
                    "unit_price_ex_tax": 5.0,
                    "line_total_ex_tax": 5.0,
                }
            ],
        )
        doc = _build(ext)
        # Prevailing = the matched salmon line's 0.15.
        assert doc["lines"][1]["sale_tax_rate"] == 0.15
        # With NO matched lines at all → the copy-derived rate (32.97/219.78).
        ext2 = dict(EXTRACTION, lines=[ext["lines"][1]])
        doc2 = _build(ext2)
        assert abs(doc2["lines"][0]["sale_tax_rate"] - 0.15) < 0.001

    def test_llm_fallback_and_create_tail(self):
        def matcher(venue_id, lines, db, config_db, supplier_name=None):
            out = {}
            for ln in lines:
                if "MYSTERY" in ln["description"].upper():
                    out[ln["id"]] = {
                        "matched_item": None,
                        "suggested_name": "Mystery Product",
                        "suggested_group_id": "g-1",
                    }
                else:
                    out[ln["id"]] = {
                        "matched_item": {
                            "id": "item-freight",
                            "name": "FREIGHT - FOOD",
                        },
                    }
            return out

        ext = dict(
            EXTRACTION,
            lines=[
                {
                    "code": None,
                    "description": "Minimum Freight",
                    "quantity": 1,
                    "unit_price_ex_tax": 25.0,
                    "line_total_ex_tax": 25.0,
                },
                {
                    "code": None,
                    "description": "Mystery Product Nobody Stocks",
                    "quantity": 1,
                    "unit_price_ex_tax": 5.0,
                    "line_total_ex_tax": 5.0,
                },
            ],
        )
        doc = _build(ext, matcher=matcher)
        frt, mys = doc["lines"]
        assert frt["linked_item_id"] == "item-freight"
        assert frt["matched_by"] == "llm"
        assert frt["linked_unit_id"] == "u-each"  # variant via full catalogue row
        assert mys["linked_item_id"] is None
        assert mys["suggested_name"] == "Mystery Product"
        assert mys["suggested_group_id"] == "g-1"

    def test_unit_resolves_from_text_when_no_variant(self):
        assert _resolve_unit_record("kilo", UNITS)["id"] == "u-kilo"
        assert _resolve_unit_record("6x 750ml", UNITS)["id"] == "u-6x750"
        assert _resolve_unit_record("1 kg", UNITS)["id"] == "u-kilo"
        assert _resolve_unit_record("nonsense-unit", UNITS) is None

    def test_supplier_ambiguity_returns_none(self):
        sups = SUPPLIERS + [{"id": "sup-b", "name": "Akaroa Salmon South"}]
        s, by = resolve_supplier(["Akaroa"], sups)
        assert s is None  # two containment hits → ambiguous

    def test_invoice_date_carried_as_issued_at(self):
        # The extraction keeps dates as printed; the replica stores ISO.
        # Every format here was observed on a real supplier invoice.
        for printed in (
            "2026-08-07",
            "7 Aug 2026",
            "07 August 2026",
            "07.08.2026",
            "07 Aug 26",
            "Aug 7, 2026",
            "07/08/26",
        ):
            doc = _build(dict(EXTRACTION, invoice_date=printed))
            assert doc["issued_at"] == "2026-08-07", printed
        assert _build(EXTRACTION)["issued_at"] is None
        # Unparseable text stays verbatim — an honest diff, not a dropped date.
        assert (
            _build(dict(EXTRACTION, invoice_date="augustish"))["issued_at"]
            == "augustish"
        )

    def test_never_raises_with_empty_everything(self):
        doc = build_replica(
            None,
            None,
            "v-1",
            {},
            lh=object(),
            catalogue=[],
            units=[],
            suppliers=[],
            tax_rates={},
            aliases_by_id={},
            item_matcher=lambda *a, **k: {},
        )
        assert doc["replica"] is True
        assert doc["lines"] == []
        assert doc["resolution_log"]


class _PoLh:
    """resolve_po_id's world: an empty open list, one draft invoice claiming
    the PO, and the PO record; ``invoice()`` serves the sibling for the
    split-order validator."""

    def __init__(self, linked_invoice_id, sibling=None, po_supplier=None):
        self._linked = linked_invoice_id
        self._sibling = sibling
        self._po_supplier = po_supplier

    def get(self, path):
        if path.startswith("/1.0/stock/internal/purchase-orders?"):
            return []
        if path == "/1.0/stock/internal/purchase-orders/po-t":
            return {
                "id": "po-t",
                "orderNumber": "1521145",
                "linkedInvoiceId": self._linked,
                "supplierId": self._po_supplier,
            }
        if path == "/1.0/stock/internal/invoices":
            return [
                {
                    "id": self._linked,
                    "purchaseOrderNumber": "1521145",
                    "linkedPurchaseOrderId": "po-t",
                }
            ]
        if path.startswith("/1.0/stock/internal/stock-received"):
            return []
        raise AssertionError(f"unexpected GET {path}")

    def invoice(self, iid):
        assert iid == self._linked and self._sibling is not None
        return self._sibling


class TestPoSplitValidator:
    """The replica's PO stage runs the engine's split-order validator: a PO
    claimed by a sibling invoice is classified doubled-up (reference removed)
    vs genuine split (reference kept, never linked); a PO claimed by the very
    invoice being replicated links normally."""

    EXT = dict(EXTRACTION, customer_purchase_order_number="po#1521145")

    def test_po_claimed_by_own_draft_links(self):
        # The Tamar case: the PO is in neither the open list nor the received
        # feed (its invoice is still a draft) — the drafts pass finds it, and
        # the claiming invoice being OUR OWN means the link is ours.
        doc = _build(self.EXT, lh=_PoLh("inv-own"), own_invoice_id="inv-own")
        assert doc["linked_purchase_order_id"] == "po-t"
        assert doc["purchase_order_number"] == "1521145"

    def test_po_of_a_different_supplier_warns(self):
        # Gate L4's rule: the order must belong to this supplier. The PO is
        # under 'Totally Different Ltd' while the copy resolved to Akaroa.
        doc = _build(
            self.EXT,
            lh=_PoLh("inv-own", po_supplier="sup-other"),
            own_invoice_id="inv-own",
        )
        assert any("belongs to Totally Different Ltd" in w for w in doc["warnings"])

    def test_po_of_a_same_named_duplicate_record_does_not_warn(self):
        # Duplicate supplier records with matching names (the Ellesmere/Tamar
        # situation) must not false-flag — name-containment rescue.
        sups = SUPPLIERS + [{"id": "sup-akaroa2", "name": "Akaroa Salmon (NZ)"}]
        ext = dict(self.EXT, supplier_name="Akaroa Salmon")
        doc = _build(
            ext,
            suppliers=sups,
            lh=_PoLh("inv-own", po_supplier="sup-akaroa2"),
            own_invoice_id="inv-own",
        )
        assert doc["linked_supplier_id"] == "sup-akaroa"  # exact beats the twin
        assert not any("belongs to" in w for w in doc["warnings"])

    def test_split_delivery_keeps_reference_without_linking(self):
        # Sibling carries DIFFERENT goods → genuine split: reference only.
        sibling = {
            "referenceNumber": "INV-1111",
            "total": 999.99,
            "lines": [{"code": "ZZZ", "description": "OTHER GOODS"}],
        }
        doc = _build(self.EXT, lh=_PoLh("inv-sib", sibling), own_invoice_id="inv-own")
        assert doc["linked_purchase_order_id"] is None
        assert doc["purchase_order_number"] == "1521145"
        assert any("split across deliveries" in e for e in doc["resolution_log"])

    def test_doubled_up_removes_the_reference(self):
        # Sibling already carries the SAME lines and total → not a split:
        # the reference is bogus and the replica drops it.
        sibling = {
            "referenceNumber": "INV-1111",
            "total": EXTRACTION["total_incl_tax"],
            "lines": [
                {
                    "code": "PBO0.7",
                    "description": "Salmon Fillet Skin On",
                    "quantityReceived": 4.95,
                    "unitCost": 44.4,
                }
            ],
        }
        doc = _build(self.EXT, lh=_PoLh("inv-sib", sibling), own_invoice_id="inv-own")
        assert doc["linked_purchase_order_id"] is None
        assert doc["purchase_order_number"] is None
        assert any("doubled-up" in e for e in doc["resolution_log"])


class TestCopyUnitPrecedence:
    """The unit-fix doctrine, replica-side: the copy's confidently-delivered
    unit overrides a variant default naming a DIFFERENT pack; equivalent
    names keep the variant's unit; unreadable units are never guessed."""

    def _line(self, **over):
        line = dict(EXTRACTION["lines"][0])
        line.update(over)
        return dict(EXTRACTION, lines=[line])

    def test_confident_copy_unit_overrides_variant_default(self):
        # Variant default is Kilo; the copy prints a 6x750mL pack.
        doc = _build(self._line(unit_of_measure="6x 750ml"))
        ln = doc["lines"][0]
        assert ln["linked_unit_id"] == "u-6x750"
        assert ln["unit_ratio"] == 4.5
        assert any("per the copy" in e for e in doc["resolution_log"])

    def test_equivalent_copy_unit_keeps_variant(self):
        # 'Kilo' ≡ '1 kg' — id-stable against Loaded.
        doc = _build(self._line(unit_of_measure="1 kg"))
        assert doc["lines"][0]["linked_unit_id"] == "u-kilo"
        assert not any("per the copy" in e for e in doc["resolution_log"])

    def test_unreadable_unit_uses_the_variant_unit_and_warns(self):
        # The variant's registered unit fills the gap, but an unreadable copy
        # unit still needs a human eye — the warning stays.
        doc = _build(self._line(unit_of_measure=None, unit_unrecognisable=True))
        assert doc["lines"][0]["linked_unit_id"] == "u-kilo"  # variant's unit
        assert any("confirm the unit" in w for w in doc["warnings"])

    def test_unreadable_unit_with_no_variant_warns(self):
        # Nothing authoritative to fall back on → the unit-confirm warning.
        doc = _build(
            self._line(
                code=None,
                description="Mystery Product Nobody Stocks",
                unit=None,
                unit_of_measure=None,
                unit_unrecognisable=True,
            )
        )
        assert doc["lines"][0]["linked_unit_id"] is None
        assert any("confirm the unit" in w for w in doc["warnings"])

    def test_unknown_copy_unit_keeps_variant_and_logs(self):
        # The copy names a pack the venue has no unit record for.
        doc = _build(self._line(unit_of_measure="9x 123ml"))
        assert doc["lines"][0]["linked_unit_id"] == "u-kilo"
        assert any("unit would need creating" in e for e in doc["resolution_log"])

    def test_vague_packaging_word_never_overrides(self):
        # 'ctn' is not a delivered unit (the engine's _delivered_unit rule).
        doc = _build(self._line(unit_of_measure="ctn"))
        assert doc["lines"][0]["linked_unit_id"] == "u-kilo"


class TestDocumentFlags:
    """The engine's credit-note/statement gates, mirrored as warnings."""

    def test_statement_flagged(self):
        doc = _build(dict(EXTRACTION, document_type="statement"))
        assert any("STATEMENT" in w for w in doc["warnings"])

    def test_letter_with_no_lines_flagged(self):
        doc = _build(dict(EXTRACTION, document_type="other", lines=[]))
        assert any("letter/notice" in w for w in doc["warnings"])

    def test_other_with_lines_not_flagged(self):
        doc = _build(dict(EXTRACTION, document_type="other"))
        assert not any("letter/notice" in w for w in doc["warnings"])

    def test_credit_note_flagged(self):
        # A credit note is RECEIVABLE (it reverses stock and cost) — flagged
        # loudly, never gated. See TestCreditNoteSigns for the sign contract.
        doc = _build(dict(EXTRACTION, total_incl_tax=-252.75))
        assert any("CREDIT NOTE" in w for w in doc["warnings"])
        assert doc["is_credit_note"] is True

    def test_inconsistent_line_logged(self):
        line = dict(EXTRACTION["lines"][0], quantity=3, line_total_ex_tax=219.78)
        doc = _build(dict(EXTRACTION, lines=[line]))
        assert any("not self-consistent" in e for e in doc["resolution_log"])


class TestDuplicateCheck:
    """The engine's Layer-0 duplicate gate, replica-side: the extracted
    invoice number + supplier already in the received feed → flagged with the
    same registry markers the production card carries."""

    def _feed_row(self, **over):
        row = {
            "id": "inv-old",
            "type": "Invoice",
            "invoiceNumber": "F55755100",
            "supplierName": "Akaroa Salmon",
            "receivedAt": "2026-07-30T00:00:00",
            "fileId": "file-9",
            "total": 252.75,
            "purchaseOrderNumber": "1520001",
        }
        row.update(over)
        return row

    def test_already_received_invoice_is_flagged(self):
        doc = _build(received_feed=[self._feed_row()])
        assert doc["duplicate_of_invoice_id"] == "inv-old"
        assert doc["duplicate_of_file_id"] == "file-9"
        assert doc["duplicate_of_purchase_order_id"] is None
        assert any("already received on 2026-07-30" in w for w in doc["warnings"])

    def test_receipted_against_the_order_is_flagged_as_po_kind(self):
        # Feed "PurchaseOrder" rows: goods receipted straight against the
        # ORDER — no invoice document exists; the row id is the PO's id.
        doc = _build(received_feed=[self._feed_row(type="PurchaseOrder", id="po-9")])
        assert doc["duplicate_of_invoice_id"] is None
        assert doc["duplicate_of_purchase_order_id"] == "po-9"
        assert any("against order 1520001" in w for w in doc["warnings"])

    def test_own_invoice_and_other_suppliers_do_not_flag(self):
        feed = [
            self._feed_row(id="inv-own"),  # the invoice being replicated
            self._feed_row(supplierName="Totally Different Ltd"),
            self._feed_row(invoiceNumber="OTHER-1"),
        ]
        doc = _build(received_feed=feed, own_invoice_id="inv-own")
        assert doc["duplicate_of_invoice_id"] is None
        assert doc["duplicate_of_purchase_order_id"] is None
        assert doc["warnings"] == []


class TestReplicaIssues:
    """Structured confidence issues — {id, code, blocking, line_id, message}.

    The replica is the single engine behind the live review's suggestions and
    confidence gating; a blocking issue means "we cannot be confident in this
    invoice — a human must look".
    """

    @staticmethod
    def _codes(doc):
        return {i["code"] for i in doc["issues"]}

    def test_clean_invoice_has_no_issues(self):
        doc = _build(received_feed=[])
        assert doc["issues"] == []

    def test_unmatched_item_and_unit_are_blocking(self):
        ext = dict(
            EXTRACTION,
            lines=[
                {
                    "code": "ZZZ9",
                    "description": "Mystery Product Nobody Stocks",
                    "quantity": 1,
                    "unit": None,
                    "unit_of_measure": None,
                    "unit_price_ex_tax": 219.78,
                    "line_total_ex_tax": 219.78,
                }
            ],
        )
        doc = _build(ext, received_feed=[])
        by_code = {i["code"]: i for i in doc["issues"]}
        assert by_code["item_unmatched"]["line_id"] == "rep-0"
        assert by_code["item_unmatched"]["blocking"] is True
        # The user's confidence rule: nothing recognisable on the copy AND no
        # variant unit → we cannot be confident; the invoice needs checking.
        assert by_code["unit_missing"]["line_id"] == "rep-0"
        assert by_code["unit_missing"]["blocking"] is True
        assert by_code["item_unmatched"]["id"] == "item_unmatched:rep-0"

    def test_variant_unit_prevents_unit_missing(self):
        doc = _build(received_feed=[])
        assert "unit_missing" not in self._codes(doc)

    def test_unreadable_unit_asks_for_confirmation(self):
        line = dict(
            EXTRACTION["lines"][0], unit_of_measure=None, unit_unrecognisable=True
        )
        doc = _build(dict(EXTRACTION, lines=[line]), received_feed=[])
        assert "unit_unconfirmed" in self._codes(doc)
        # The variant still supplied a unit, so unit_missing must NOT fire.
        assert "unit_missing" not in self._codes(doc)

    def test_supplier_unresolved_is_blocking(self):
        doc = _build(
            dict(EXTRACTION, supplier_name="Totally Unknown Vendor"),
            received_feed=[],
        )
        issue = next(i for i in doc["issues"] if i["code"] == "supplier_unresolved")
        assert issue["blocking"] is True
        assert "Totally Unknown Vendor" in issue["message"]

    def test_statement_blocks_but_a_credit_note_does_not(self):
        # A statement is not a receivable document; a credit note IS one.
        doc = _build(dict(EXTRACTION, document_type="statement"), received_feed=[])
        assert "not_an_invoice" in self._codes(doc)
        doc = _build(dict(EXTRACTION, total_incl_tax=-252.75), received_feed=[])
        assert "not_an_invoice" not in self._codes(doc)
        issue = next(i for i in doc["issues"] if i["code"] == "credit_note")
        assert issue["blocking"] is False  # informational — never a gate
        assert issue["data"]["document_type"] == "credit_note"

    def test_totals_inconsistent_flags_bad_arithmetic(self):
        # The pink-ling shape: printed totals that don't follow from the lines.
        ext = dict(EXTRACTION, subtotal_ex_tax=419.78, total_incl_tax=452.75)
        doc = _build(ext, received_feed=[])
        issue = next(i for i in doc["issues"] if i["code"] == "totals_inconsistent")
        assert issue["blocking"] is True
        assert "don't reconcile" in issue["message"]

    def test_loaded_rounding_band_absorbs_small_drift(self):
        # lines 219.78 + tax 32.97 = 252.75; a 5c total drift sits inside
        # Loaded's own ±10c entry-validation band — consistent, no issue.
        ext = dict(EXTRACTION, subtotal_ex_tax=219.80, total_incl_tax=252.80)
        doc = _build(ext, received_feed=[])
        assert "totals_inconsistent" not in self._codes(doc)

    def test_duplicate_issue_carries_registry_ids(self):
        feed = [
            {
                "id": "inv-old",
                "type": "Invoice",
                "invoiceNumber": "F55755100",
                "supplierName": "Akaroa Salmon",
                "receivedAt": "2026-07-30T00:00:00",
                "fileId": "file-9",
            }
        ]
        doc = _build(received_feed=feed)
        issue = next(i for i in doc["issues"] if i["code"] == "duplicate_invoice")
        assert issue["data"]["duplicate_of_invoice_id"] == "inv-old"
        assert issue["data"]["duplicate_of_file_id"] == "file-9"

    def test_unresolvable_po_reference_is_flagged(self):
        # lh=object() cannot serve the PO lookup → the reference cannot be
        # checked → po_unresolved (never a silent log line).
        ext = dict(EXTRACTION, customer_purchase_order_number="PO12345")
        doc = _build(ext, received_feed=[])
        issue = next(i for i in doc["issues"] if i["code"] == "po_unresolved")
        assert "PO12345" in issue["message"]

    def test_discount_amount_carried(self):
        ext = dict(EXTRACTION, discount_amount=5.5)
        doc = _build(ext, received_feed=[])
        assert doc["discount_amount"] == 5.5

    def test_copy_unit_not_in_loaded_marks_line_for_create(self):
        # The copy confidently names a pack the venue has no unit for: the
        # variant default is kept (receivable), and the replica line carries the
        # copy's unit name so the review layer can raise a create_unit SUGGESTION
        # (no longer a non-blocking "note" issue).
        line = dict(EXTRACTION["lines"][0], unit_of_measure="9x123ml")
        doc = _build(dict(EXTRACTION, lines=[line]), received_feed=[])
        assert doc["lines"][0]["unit_create_name"] == "9x123ml"
        assert doc["lines"][0]["linked_unit_id"] == "u-kilo"  # variant kept
        assert not any(i["code"] == "unit_not_in_loaded" for i in doc["issues"])

    def test_unit_missing_carries_confident_copy_name(self):
        ext = dict(
            EXTRACTION,
            lines=[
                {
                    "code": "ZZZ9",
                    "description": "Mystery Product",
                    "quantity": 1,
                    "unit_of_measure": "9x123ml",
                    "unit_price_ex_tax": 219.78,
                    "line_total_ex_tax": 219.78,
                }
            ],
        )
        doc = _build(ext, received_feed=[])
        issue = next(i for i in doc["issues"] if i["code"] == "unit_missing")
        assert issue["data"]["unit_name"] == "9x123ml"

    def test_unit_missing_has_no_name_when_nothing_confident(self):
        ext = dict(
            EXTRACTION,
            lines=[
                {
                    "code": "ZZZ9",
                    "description": "Mystery Product",
                    "quantity": 1,
                    "unit": None,
                    "unit_of_measure": None,
                    "unit_price_ex_tax": 219.78,
                    "line_total_ex_tax": 219.78,
                }
            ],
        )
        doc = _build(ext, received_feed=[])
        issue = next(i for i in doc["issues"] if i["code"] == "unit_missing")
        assert "data" not in issue  # no create offer from nothing


class TestCreditNoteSigns:
    """A credit note reverses stock and cost, so it must land in Loaded's own
    sign space: quantities and totals NEGATIVE, unit costs POSITIVE.

    That shape was read off 18 live credit notes across the three venues —
    every one stores quantityReceived negative, unitCostExclTax positive,
    totalCostExclTax negative, total negative. (Loaded's own header
    subtotal/tax are inconsistent in its records — null on 8, positive on 7
    while the total is negative — so we always produce the coherent form.)

    The extraction stays AS PRINTED; the replica does the negating.
    """

    # A credit note normally prints POSITIVE numbers under a "CREDIT NOTE"
    # heading — this is the common shape, and the one Loaded's OCR mangles.
    PRINTED = dict(EXTRACTION, document_type="credit_note")

    def _line(self, doc):
        return doc["lines"][0]

    def test_all_positive_print_is_negated_end_to_end(self):
        doc = _build(self.PRINTED)
        ln = self._line(doc)
        assert doc["is_credit_note"] is True
        assert doc["document_type"] == "credit_note"
        assert ln["quantity_received"] == -4.95
        assert ln["unit_cost"] == 44.4  # a price is never negative
        assert ln["total_cost"] == -219.78
        assert doc["subtotal"] == -219.78
        assert doc["tax_amount"] == -32.97
        assert doc["total"] == -252.75

    def test_recognised_by_printed_negative_total_alone(self):
        # document_type still says "invoice" — the printed total decides.
        doc = _build(dict(EXTRACTION, total_incl_tax=-252.75))
        assert doc["is_credit_note"] is True
        assert self._line(doc)["quantity_received"] == -4.95

    def test_recognised_by_loaded_total_alone(self):
        # Neither the classification nor the print says credit — but Loaded
        # read it as negative, which is Loaded's own definition.
        doc = _build(EXTRACTION, loaded_total=-252.75)
        assert doc["is_credit_note"] is True
        assert doc["total"] == -252.75

    def test_a_plain_invoice_is_untouched(self):
        doc = _build(EXTRACTION)
        assert doc["is_credit_note"] is False
        assert self._line(doc)["quantity_received"] == 4.95
        assert doc["total"] == 252.75
        assert not any(i["code"] == "credit_note" for i in doc["issues"])

    def test_already_signed_print_passes_through(self):
        # A credit note that prints its own negatives must not be flipped back.
        ext = dict(
            EXTRACTION,
            document_type="credit_note",
            subtotal_ex_tax=-219.78,
            tax_amount=-32.97,
            total_incl_tax=-252.75,
            lines=[
                dict(EXTRACTION["lines"][0], quantity=-4.95, line_total_ex_tax=-219.78)
            ],
        )
        doc = _build(ext)
        ln = self._line(doc)
        assert ln["quantity_received"] == -4.95
        assert ln["unit_cost"] == 44.4
        assert ln["total_cost"] == -219.78
        assert doc["total"] == -252.75

    def test_normalising_is_idempotent(self):
        from app.services.invoice_replica import _credit_normalise

        once = _credit_normalise(self.PRINTED)
        twice = _credit_normalise(once)
        assert once == twice

    def test_header_and_lines_are_scoped_independently(self):
        # Lines already signed, header printed positive: force the header only.
        ext = dict(
            EXTRACTION,
            document_type="credit_note",
            lines=[
                dict(EXTRACTION["lines"][0], quantity=-4.95, line_total_ex_tax=-219.78)
            ],
        )
        doc = _build(ext)
        assert self._line(doc)["total_cost"] == -219.78  # untouched
        assert doc["total"] == -252.75  # forced

    def test_a_signed_mixed_line_keeps_its_own_sign(self):
        # A restocking charge among the credits: the print is signed, so each
        # line keeps the sign it was given.
        ext = dict(
            EXTRACTION,
            document_type="credit_note",
            total_incl_tax=-252.75,
            lines=[
                dict(EXTRACTION["lines"][0], quantity=-4.95, line_total_ex_tax=-219.78),
                dict(
                    EXTRACTION["lines"][0],
                    code="EX1",
                    description="Restocking fee",
                    quantity=1,
                    unit_price_ex_tax=10.0,
                    line_total_ex_tax=10.0,
                ),
            ],
        )
        doc = _build(ext)
        assert doc["lines"][0]["quantity_received"] == -4.95
        assert doc["lines"][1]["quantity_received"] == 1

    def test_the_credit_issue_is_informational(self):
        doc = _build(self.PRINTED, received_feed=[])
        issue = next(i for i in doc["issues"] if i["code"] == "credit_note")
        assert issue["blocking"] is False
        assert "REVERSES" in issue["message"]
        assert "document_type" in issue["data"]["signals"]

    def test_totals_still_reconcile_after_negation(self):
        doc = _build(self.PRINTED, received_feed=[])
        assert not any(i["code"] == "totals_inconsistent" for i in doc["issues"])

    def test_copy_tax_rate_survives_a_negative_subtotal(self):
        # The guard used to be `subtotal > 0`, which silently dropped the
        # copy-derived rate for every credit note.
        ext = dict(self.PRINTED, lines=[dict(EXTRACTION["lines"][0], code="NOPE")])
        doc = _build(ext, received_feed=[])
        assert self._line(doc)["sale_tax_rate"] == 0.15

    def test_purchase_order_is_never_linked(self):
        # The PO a credit prints belongs to the invoice being credited;
        # resolving it would steal Loaded's 1:1 link and stamp bogus
        # split-order notes onto that PO and that invoice at receive.
        ext = dict(self.PRINTED, customer_purchase_order_number="po#1521145")
        doc = _build(ext, lh=_PoLh("inv-own"), own_invoice_id="inv-own")
        assert doc["linked_purchase_order_id"] is None
        # Dropped, not just unlinked: the field means "THIS document's order",
        # and Loaded stores none for a credit either (verified on 24 live
        # credits) — carrying it would diff against Loaded forever.
        assert doc["purchase_order_number"] is None
        assert any("dropped" in e for e in doc["resolution_log"])

    def test_zero_quantity_value_credit_blocks(self):
        # Receive recomputes every line as quantity x cost, so a lump-sum
        # value credit with no quantity would silently come through as zero.
        ext = dict(
            self.PRINTED,
            lines=[dict(EXTRACTION["lines"][0], quantity=0, line_total_ex_tax=25.0)],
        )
        doc = _build(ext, received_feed=[])
        issue = next(i for i in doc["issues"] if i["code"] == "credit_zero_quantity")
        assert issue["blocking"] is True

    def test_credit_is_not_a_duplicate_of_the_invoice_it_credits(self):
        # A credit note commonly reprints the original's invoice number.
        feed = [
            {
                "id": "other",
                "invoiceNumber": "F55755100",
                "supplierName": "Akaroa Salmon",
                "type": "Invoice",
                "total": 252.75,  # the ORIGINAL, positive
                "receivedAt": "2026-08-01",
            }
        ]
        doc = _build(self.PRINTED, received_feed=feed)
        assert not any(i["code"] == "duplicate_invoice" for i in doc["issues"])

    def test_the_same_credit_received_twice_is_still_a_duplicate(self):
        # Double-reversing stock is the nastiest failure here — the sign rule
        # must not switch duplicate detection off for credits entirely.
        feed = [
            {
                "id": "other",
                "invoiceNumber": "F55755100",
                "supplierName": "Akaroa Salmon",
                "type": "Invoice",
                "total": -252.75,
                "receivedAt": "2026-08-01",
            }
        ]
        doc = _build(self.PRINTED, received_feed=feed)
        assert any(i["code"] == "duplicate_invoice" for i in doc["issues"])


class TestCopyUnitBeatsAVagueVariant:
    """Hancocks 4362108, 13 Aug 2026.

    The copy printed 'CITY OF LONDON DRY GIN (6X1000ML)' and the extraction
    read '6x1000ml' correctly. Loaded's supplier variant said '6 Pack'.
    units_equivalent calls those the same pack — rightly, since a copy that
    prints only a count must not displace a variant that knows the size — so
    the variant was kept. The working value then MATCHED the replica exactly,
    which meant no suggestion was raised: the right answer was on the page and
    the user had no way to reach it.
    """

    def test_a_sized_copy_unit_overrides_a_bare_count_variant(self):
        from app.services.invoice_units import copy_is_more_specific

        assert copy_is_more_specific("6x1000ml", "6 Pack") is True

    def test_a_bare_count_copy_never_displaces_a_sized_variant(self):
        """The asymmetry is the whole point — reversing it would let a vague
        copy throw away the size Loaded already knows."""
        from app.services.invoice_units import copy_is_more_specific

        assert copy_is_more_specific("6 Pack", "6x750ml") is False

    def test_equivalent_and_equally_specific_units_keep_the_variant(self):
        """Unchanged behaviour: same pack, same information, so the variant's
        unit id wins for stability against Loaded."""
        from app.services.invoice_units import copy_is_more_specific, units_equivalent

        assert units_equivalent("700ml", "700 mL") is True
        assert copy_is_more_specific("700ml", "700 mL") is False
