"""supplier_catalog — global physical facts, provenance-honest.

The two rules under test everywhere: truth never comes from venue practice
(lower provenance can never overwrite higher), and conflict is a question,
never a majority vote.
"""

from app.db.config_models import SupplierInvoiceSpec, SupplierProduct
from app.services import supplier_catalog as sc


def _spec(db, name="Trents", aliases=None):
    row = SupplierInvoiceSpec(name=name, aliases=aliases or [], instructions="x")
    db.add(row)
    db.flush()
    return row


def _ext(supplier="Trents Wholesale Limited", invoice="INV-1", lines=None):
    return {
        "supplier_name": supplier,
        "invoice_number": invoice,
        "lines": lines
        if lines is not None
        else [
            {
                "code": "4183758",
                "description": "MALFY GIN CON LIMONE 700ML",
                "unit": "EA",
                "unit_of_measure": "700ml",
            }
        ],
    }


def _row(db, code="4183758"):
    return db.query(SupplierProduct).filter(SupplierProduct.code == code).one()


class TestObserve:
    def test_printed_size_becomes_the_answer(self, db_session):
        _spec(db_session)
        out = sc.observe_extraction(db_session, _ext(), provenance="printed")
        assert out["observed"] == 1
        row = _row(db_session)
        assert row.supplier_key == "Trents"
        assert row.unit_name == "700ml"
        assert row.pack_type == "fixed"
        assert row.unit_type == "volume"
        assert row.provenance == "printed"

    def test_equivalent_spellings_never_manufacture_a_conflict(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {
                        "code": "4183758",
                        "description": "MALFY GIN CON LIMONE 700ML",
                        "unit_of_measure": "0.7 L",
                    }
                ],
            ),
            provenance="printed",
        )
        row = _row(db_session)
        assert row.unit_name == "700ml"  # first-seen spelling kept
        bucket = row.evidence["printed"]
        assert len(bucket) == 1
        assert list(bucket.values())[0]["count"] == 2

    def test_charge_words_record_no_size(self, db_session):
        # The EA trap this catalogue exists to beat: how a line is CHARGED
        # (EA/PACK/each) must never become what the product IS.
        _spec(db_session)
        out = sc.observe_extraction(
            db_session,
            _ext(
                lines=[
                    {"code": "1", "description": "A", "unit_of_measure": "EA"},
                    {"code": "2", "description": "B", "unit_of_measure": "PACK"},
                    {"code": "3", "description": "C", "unit_of_measure": "each"},
                    {"code": "4", "description": "D", "unit_of_measure": None},
                ]
            ),
            provenance="printed",
        )
        assert out["observed"] == 0
        for code in ("1", "2", "3", "4"):
            row = _row(db_session, code)
            assert row.unit_name is None and row.pack_type == "unknown"

    def test_conflicting_printed_sizes_are_a_question_not_a_vote(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        for inv in ("B", "C"):  # 1L twice — a vote would pick it
            sc.observe_extraction(
                db_session,
                _ext(
                    invoice=inv,
                    lines=[
                        {
                            "code": "4183758",
                            "description": "MALFY GIN CON LIMONE",
                            "unit_of_measure": "1L",
                        }
                    ],
                ),
                provenance="printed",
            )
        row = _row(db_session)
        assert row.unit_name is None
        assert row.pack_type == "unknown"
        assert len(row.evidence["printed"]) == 2  # both sightings kept

    def test_practice_never_overwrites_printed_and_human_beats_both(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        # venue practice says 1L — advisory tier, must change nothing
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {
                        "code": "4183758",
                        "description": "MALFY",
                        "unit_of_measure": "1L",
                    }
                ],
            ),
            provenance="practice",
        )
        row = _row(db_session)
        assert row.unit_name == "700ml" and row.provenance == "printed"
        # a human verification of 1L outranks everything below it
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="C",
                lines=[
                    {
                        "code": "4183758",
                        "description": "MALFY",
                        "unit_of_measure": "1L",
                    }
                ],
            ),
            provenance="human",
        )
        row = _row(db_session)
        assert row.unit_name == "1L" and row.provenance == "human"

    def test_random_weight_is_kilo_not_a_pack(self, db_session):
        _spec(db_session, name="Harbour Fish")
        sc.observe_extraction(
            db_session,
            _ext(
                supplier="Harbour Fish Dunedin",
                lines=[
                    {
                        "code": "SAL1",
                        "description": "Salmon Fillet",
                        "unit_of_measure": "Kilo",
                    }
                ],
            ),
            provenance="printed",
        )
        row = _row(db_session, "SAL1")
        assert row.pack_type == "random_weight"
        assert row.unit_name == "Kilo" and row.unit_type == "weight"

    def test_reobserving_the_same_invoice_accumulates_nothing(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        bucket = _row(db_session).evidence["printed"]
        assert list(bucket.values())[0]["count"] == 1

    def test_no_supplier_spec_means_not_catalogued(self, db_session):
        out = sc.observe_extraction(
            db_session, _ext(supplier="Nobody Wrote A Spec Ltd")
        )
        assert out == {"observed": 0, "skipped": "no supplier spec"}
        assert db_session.query(SupplierProduct).count() == 0


class TestEnrichmentAndArbitration:
    def test_enrichment_answers_where_pages_are_silent(self, db_session):
        _spec(db_session)
        sc.observe_practice(
            db_session,
            "Trents",
            "A",
            [{"code": "SH1", "description": "SHOTT ELDERFLOWER", "unit": "Each"}],
        )
        row = _row(db_session, "SH1")
        assert row.unit_name is None  # practice count word answers nothing
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="750ml",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="SHOTT syrups sell in 750ml glass bottles",
        )
        assert row.unit_name == "750ml" and row.provenance == "enriched"
        assert row.category == "beverage"
        a = sc.catalog_unit_for_line(db_session, "Trents", "SH1")
        assert a and a["unit_name"] == "750ml" and a["provenance"] == "enriched"

    def test_enrichment_never_overrules_a_printed_page(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        row = _row(db_session)
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="1L",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="model opinion",
        )
        assert row.unit_name == "700ml" and row.provenance == "printed"

    def test_enrichment_breaks_a_printed_tie_by_agreeing_with_one_side(
        self, db_session
    ):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {
                        "code": "4183758",
                        "description": "MALFY",
                        "unit_of_measure": "1L",
                    }
                ],
            ),
            provenance="printed",
        )
        row = _row(db_session)
        assert row.unit_name is None  # the conflict
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="0.7 L",  # equivalent to one side, spelling differs
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="Malfy gins are 700ml in NZ",
        )
        assert row.unit_name == "700ml"  # the agreed printed sighting wins
        assert row.provenance == "enriched"  # the decider owns the answer

    def test_an_arbiter_agreeing_with_neither_decides_nothing(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {
                        "code": "4183758",
                        "description": "MALFY",
                        "unit_of_measure": "1L",
                    }
                ],
            ),
            provenance="printed",
        )
        row = _row(db_session)
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="2L",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="wrong",
        )
        assert row.unit_name is None  # still an open question


class TestPractice:
    def test_practice_measures_answer_nothing_but_are_kept(self, db_session):
        _spec(db_session)
        sc.observe_practice(
            db_session,
            "Trents",
            "A",
            [{"code": "P1", "description": "THING", "unit": "700 mL"}],
        )
        row = _row(db_session, "P1")
        assert row.provenance == "practice"
        assert row.unit_name == "700 mL"  # best-known, honestly labelled
        assert sc.catalog_unit_for_line(db_session, "Trents", "P1") is None

    def test_count_words_are_recorded_as_divergence_evidence_only(self, db_session):
        _spec(db_session)
        sc.observe_practice(
            db_session,
            "Trents",
            "A",
            [{"code": "SH1", "description": "SHOTT SYRUP", "unit": "Each"}],
        )
        row = _row(db_session, "SH1")
        entry = list(row.evidence["practice"].values())[0]
        assert entry["count_word"] is True
        assert row.unit_name is None


class TestRelatedEvidence:
    def test_cross_supplier_hit_ranks_by_word_overlap(self, db_session):
        _spec(db_session, name="Bidfood")
        sc.observe_extraction(
            db_session,
            _ext(
                supplier="Bidfood Limited",
                invoice="X",
                lines=[
                    {
                        "code": "70951",
                        "description": "SYRUP BUTTERSCOTCH SHOTT",
                        "unit_of_measure": "1L",
                    },
                    {
                        "code": "111",
                        "description": "SYRUP RASPBERRY GENERIC",
                        "unit_of_measure": "750ml",
                    },
                ],
            ),
            provenance="printed",
        )
        out = sc.related_evidence(db_session, "SHOTT NATURAL SYRUP ELDERF")
        assert out and "BUTTERSCOTCH SHOTT" in out[0]
        assert "1L" in out[0]

    def test_the_line_itself_is_excluded(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        out = sc.related_evidence(
            db_session,
            "MALFY GIN CON LIMONE 700ML",
            exclude=("Trents", "4183758"),
        )
        assert out == []


class TestAnswer:
    def test_fixed_printed_answers(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(), provenance="printed")
        a = sc.catalog_unit_for_line(db_session, "Trents", "4183758")
        assert a == {
            "unit_name": "700ml",
            "pack_type": "fixed",
            "provenance": "printed",
        }

    def test_questions_and_practice_answer_nothing(self, db_session):
        _spec(db_session)
        # conflict → question
        sc.observe_extraction(db_session, _ext(invoice="A"), provenance="printed")
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {
                        "code": "4183758",
                        "description": "MALFY",
                        "unit_of_measure": "1L",
                    }
                ],
            ),
            provenance="printed",
        )
        assert sc.catalog_unit_for_line(db_session, "Trents", "4183758") is None
        # practice-only entry → advisory, no answer
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="C",
                lines=[
                    {
                        "code": "OTHER",
                        "description": "X",
                        "unit_of_measure": "2L",
                    }
                ],
            ),
            provenance="practice",
        )
        assert sc.catalog_unit_for_line(db_session, "Trents", "OTHER") is None

    def test_unknown_code_or_supplier_is_none(self, db_session):
        assert sc.catalog_unit_for_line(db_session, "Trents", "nope") is None
        assert sc.catalog_unit_for_line(db_session, None, "4183758") is None
