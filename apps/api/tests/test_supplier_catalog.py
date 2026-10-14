"""supplier_catalog — global physical facts, provenance-honest.

The rules under test everywhere (28 Aug 2026; no person tier since 14 Oct):
- authority order enriched (the resolver verdict) > printed (a raw read); a
  receive ('practice') is evidence only and never wins, and there is no
  person-confirmed tier at all;
- a raw read is provisional — the catalogue does not hand it back as an
  answer; and conflict is a question, never a majority vote.
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
        out = sc.observe_extraction(db_session, _ext())
        assert out["observed"] == 1
        row = _row(db_session)
        assert row.supplier_key == "Trents"
        assert row.unit_name == "700ml"
        assert row.pack_type == "fixed"
        assert row.unit_type == "volume"
        assert row.provenance == "printed"

    def test_equivalent_spellings_never_manufacture_a_conflict(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
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
        )
        assert out["observed"] == 0
        for code in ("1", "2", "3", "4"):
            row = _row(db_session, code)
            assert row.unit_name is None and row.pack_type == "unknown"

    def test_conflicting_printed_sizes_are_a_question_not_a_vote(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
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
            )
        row = _row(db_session)
        assert row.unit_name is None
        assert row.pack_type == "unknown"
        assert len(row.evidence["printed"]) == 2  # both sightings kept

    def test_a_receive_never_overwrites_a_read(self, db_session):
        # A receive (practice) is evidence only — it can carry a user's
        # mistake, so it never changes the row's answer, and there is no
        # 'human' ranking tier (28 Aug 2026).
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
        # venue receives it as 1L — recorded as evidence, must change nothing
        sc.observe_practice(
            db_session,
            "Trents",
            "B",
            [{"code": "4183758", "description": "MALFY", "unit": "1L"}],
        )
        row = _row(db_session)
        assert row.unit_name == "700ml" and row.provenance == "printed"
        assert "practice" in (row.evidence or {})  # kept as evidence

    def test_a_later_read_never_overrules_norms_verdict(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
        row = _row(db_session)
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="1L",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="resolver says 1L",
        )
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {
                        "code": "4183758",
                        "description": "MALFY",
                        "unit_of_measure": "700ml",
                    }
                ],
            ),
        )
        row = _row(db_session)
        assert row.unit_name == "1L" and row.provenance == "enriched"

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
        )
        row = _row(db_session, "SAL1")
        assert row.pack_type == "random_weight"
        assert row.unit_name == "Kilo" and row.unit_type == "weight"

    def test_reobserving_the_same_invoice_accumulates_nothing(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
        sc.observe_extraction(db_session, _ext(invoice="A"))
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

    def test_a_resolver_verdict_overrules_a_read(self, db_session):
        # The analyser's verdict is the authority — it OUTRANKS a raw read
        # (28 Aug 2026). This is how a poisoned read heals.
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
        row = _row(db_session)
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="1L",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="resolver: the cross-supplier line is 1L",
        )
        assert row.unit_name == "1L" and row.provenance == "enriched"
        a = sc.catalog_unit_for_line(db_session, "Trents", "4183758")
        assert a and a["unit_name"] == "1L"

    def test_a_resolver_verdict_settles_a_conflicting_read(self, db_session):
        # Two different reads for one code are a conflict (no answer); the
        # resolver's verdict settles it — and now wins outright as the top
        # tier, not as a tie-breaker from below.
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {"code": "4183758", "description": "MALFY", "unit_of_measure": "1L"}
                ],
            ),
        )
        row = _row(db_session)
        assert row.unit_name is None  # the conflict
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="700ml",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="Malfy gins are 700ml in NZ",
        )
        assert row.unit_name == "700ml" and row.provenance == "enriched"

    def test_a_resolver_verdict_wins_even_over_conflicting_reads(self, db_session):
        # The resolver verdict is the top tier: it wins outright, even when it
        # agrees with NEITHER raw read below it (no longer a tie-breaker that
        # must match a sighting).
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
        sc.observe_extraction(
            db_session,
            _ext(
                invoice="B",
                lines=[
                    {"code": "4183758", "description": "MALFY", "unit_of_measure": "1L"}
                ],
            ),
        )
        row = _row(db_session)
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="2L",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="resolver decides 2L",
        )
        assert row.unit_name == "2L" and row.provenance == "enriched"


class TestPractice:
    def test_a_receive_only_row_has_no_answer_but_is_kept(self, db_session):
        # A receive is evidence, never the answer: a practice-only row carries
        # no unit_name and the catalogue stays silent (28 Aug 2026), but the
        # sighting IS kept for the divergence report.
        _spec(db_session)
        sc.observe_practice(
            db_session,
            "Trents",
            "A",
            [{"code": "P1", "description": "THING", "unit": "700 mL"}],
        )
        row = _row(db_session, "P1")
        assert row.unit_name is None  # a receive never becomes the answer
        assert "practice" in (row.evidence or {})  # but it is kept
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
        )
        out = sc.related_evidence(db_session, "SHOTT NATURAL SYRUP ELDERF")
        assert out and "BUTTERSCOTCH SHOTT" in out[0]
        assert "1L" in out[0]

    def test_the_line_itself_is_excluded(self, db_session):
        _spec(db_session)
        sc.observe_extraction(db_session, _ext(invoice="A"))
        out = sc.related_evidence(
            db_session,
            "MALFY GIN CON LIMONE 700ML",
            exclude=("Trents", "4183758"),
        )
        assert out == []


class TestAnswer:
    def test_a_raw_read_does_not_answer_a_verdict_does(self, db_session):
        # A raw extraction read is provisional — the catalogue does NOT hand it
        # back (the line is verified by the resolver instead). Only a resolver
        # verdict answers (28 Aug 2026).
        _spec(db_session)
        sc.observe_extraction(db_session, _ext())
        assert sc.catalog_unit_for_line(db_session, "Trents", "4183758") is None
        row = _row(db_session)
        sc.apply_enrichment(
            db_session,
            row,
            unit_name="700ml",
            pack_type="fixed",
            unit_type="volume",
            category="beverage",
            why="resolver confirms 700ml",
        )
        a = sc.catalog_unit_for_line(db_session, "Trents", "4183758")
        assert a == {
            "unit_name": "700ml",
            "pack_type": "fixed",
            "provenance": "enriched",
        }

    def test_questions_and_practice_answer_nothing(self, db_session):
        _spec(db_session)
        # conflict → question
        sc.observe_extraction(db_session, _ext(invoice="A"))
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
        )
        assert sc.catalog_unit_for_line(db_session, "Trents", "4183758") is None
        # practice-only entry → advisory, no answer
        sc.observe_practice(
            db_session,
            "Trents",
            "C",
            [{"code": "OTHER", "description": "X", "unit": "2L"}],
        )
        assert sc.lookup(db_session, "Trents", "OTHER").provenance == "practice"
        assert sc.catalog_unit_for_line(db_session, "Trents", "OTHER") is None

    def test_unknown_code_or_supplier_is_none(self, db_session):
        assert sc.catalog_unit_for_line(db_session, "Trents", "nope") is None
        assert sc.catalog_unit_for_line(db_session, None, "4183758") is None


class TestLearnFromResolver:
    """A HIGH-confidence estimate becomes Norm's verdict — the catalogue
    self-fills from invoices, and a verdict outranks a raw read so a poisoned
    row heals ('HIGHLAND PARK 15 YEAR OLD GIFT BOX (1X7', 4366904; analyser on
    top, 28 Aug 2026). Anything less is a GUESS: used on the invoice, asked
    again on the next one (14 Oct 2026 — medium was right 17 of 22 times in
    testing, below the ~85% a person manages; high 65 of 66)."""

    def _line(self, **over):
        ln = {
            "code": "99054",
            "description": "HIGHLAND PARK 15 YEAR OLD GIFT BOX (1X7",
            "unit_resolved": {
                "unit_id": "u-700",
                "unit_name": "700 mL",
                "create_name": None,
                "confidence": "high",
                "why": "a known branded bottle; '(1X7' reads 1X700ML",
            },
        }
        ln.update(over)
        return ln

    def test_decisive_verdict_answers_an_unknown_product(self, db_session):
        n = sc.learn_from_resolver(db_session, "Hancocks", [self._line()])
        db_session.commit()
        assert n == 1
        a = sc.catalog_unit_for_line(db_session, "Hancocks", "99054")
        assert a and a["unit_name"] == "700 mL" and a["provenance"] == "enriched"
        row = _row(db_session, "99054")
        assert "unit estimator" in (row.evidence or {}).get("enriched_note", "")

    def test_a_raw_read_is_overwritten_by_a_resolver_verdict(self, db_session):
        # The heal: a row that only carries a raw read is re-resolved and the
        # verdict wins (the read does not 'answer', so learn does not skip).
        _spec(db_session, name="Hancocks")
        sc.observe_extraction(
            db_session,
            _ext(
                supplier="Hancocks",
                lines=[
                    {
                        "code": "99054",
                        "description": "HIGHLAND PARK",
                        "unit_of_measure": "1L",
                    }
                ],
            ),
        )
        assert sc.learn_from_resolver(db_session, "Hancocks", [self._line()]) == 1
        a = sc.catalog_unit_for_line(db_session, "Hancocks", "99054")
        assert a["unit_name"] == "700 mL" and a["provenance"] == "enriched"

    def test_a_row_with_a_verdict_is_never_touched(self, db_session):
        # A row that already carries a resolver verdict is left alone.
        n1 = sc.learn_from_resolver(db_session, "Hancocks", [self._line()])
        assert n1 == 1
        n2 = sc.learn_from_resolver(
            db_session,
            "Hancocks",
            [
                self._line(
                    unit_resolved={
                        "unit_name": "1L",
                        "create_name": None,
                        "confidence": "high",
                        "why": "second opinion",
                    }
                )
            ],
        )
        assert n2 == 0  # not touched
        a = sc.catalog_unit_for_line(db_session, "Hancocks", "99054")
        assert a["unit_name"] == "700 mL"  # first verdict kept

    def test_medium_and_low_are_guesses_asked_again(self, db_session):
        for code, conf in (("med1", "medium"), ("low1", "low")):
            ln = self._line(
                code=code,
                unit_resolved={
                    "unit_name": "700 mL",
                    "create_name": None,
                    "confidence": conf,
                    "why": "likely a bottle",
                },
            )
            assert sc.learn_from_resolver(db_session, "Hancocks", [ln]) == 1
            assert sc.catalog_unit_for_line(db_session, "Hancocks", code) is None
            row = sc.lookup(db_session, "Hancocks", code)
            assert sc.unsettled_guess(row)
            assert row.evidence["guess"]["unit"] == "700 mL"
            assert row.evidence["guess"]["confidence"] == conf

    def test_a_line_asked_about_with_no_answer_is_a_guess_too(self, db_session):
        # The estimator failed and the chooser picked something: whatever the
        # receive saves for this product is not a verdict.
        ln = {"code": "x9", "description": "MYSTERY", "unit_asked": True}
        assert sc.learn_from_resolver(db_session, "Hancocks", [ln]) == 1
        assert sc.unsettled_guess(sc.lookup(db_session, "Hancocks", "x9"))

    def test_a_verdict_settles_the_guess(self, db_session):
        low = self._line(
            unit_resolved={"unit_name": "1L", "confidence": "low", "why": "unsure"}
        )
        sc.learn_from_resolver(db_session, "Hancocks", [low])
        assert sc.unsettled_guess(sc.lookup(db_session, "Hancocks", "99054"))
        sc.learn_from_resolver(db_session, "Hancocks", [self._line()])
        row = sc.lookup(db_session, "Hancocks", "99054")
        assert not sc.unsettled_guess(row)
        assert sc.guessed_before(row)  # so the verdict outranks the old guess
        a = sc.catalog_unit_for_line(db_session, "Hancocks", "99054")
        assert a["unit_name"] == "700 mL"

    def test_an_estimate_that_agreed_with_the_unit_in_use_still_counts(
        self, db_session
    ):
        ln = self._line(unit_agreed=self._line()["unit_resolved"])
        del ln["unit_resolved"]
        assert sc.learn_from_resolver(db_session, "Hancocks", [ln]) == 1
        assert sc.catalog_unit_for_line(db_session, "Hancocks", "99054")

    def test_a_high_each_is_a_verdict(self, db_session):
        # A printed 'EA' is only how a line is charged, so a READ can't answer
        # with it; the estimator's 'each' is a judgement (a box of straws is
        # counted as one), and refusing it would re-ask every invoice.
        ln = self._line(
            code="STRAW",
            unit_resolved={
                "unit_name": "Each",
                "create_name": None,
                "confidence": "high",
                "why": "a box of straws is counted as one",
            },
        )
        assert sc.learn_from_resolver(db_session, "Hancocks", [ln]) == 1
        assert sc.catalog_unit_for_line(db_session, "Hancocks", "STRAW")[
            "unit_name"
        ] == ("Each")

    def test_no_supplier_key_writes_nothing(self, db_session):
        assert sc.learn_from_resolver(db_session, None, [self._line()]) == 0

    def test_create_name_verdict_is_recorded_too(self, db_session):
        # The right unit isn't in the venue yet — the SIZE is still a fact
        # about the product, exactly what the catalogue stores.
        ln = self._line(
            unit_resolved={
                "unit_id": None,
                "unit_name": None,
                "create_name": "6x700ml",
                "confidence": "high",
                "why": "sibling lines print 6x700mL at the same price",
            }
        )
        assert sc.learn_from_resolver(db_session, "Hancocks", [ln]) == 1
        a = sc.catalog_unit_for_line(db_session, "Hancocks", "99054")
        assert a and a["unit_name"] == "6x700ml"


class TestThereIsNoPersonConfirmedTier:
    """Removed 14 Oct 2026, the user: "the human does not add any value in us
    getting to autopilot across 1000s of venues with 100s of invoices each
    per month … we need to completely remove this concept so it doesn't come
    back." Norm's verdict is the top of the ranking, and nothing can put a
    person's say-so above it. These fail if the concept is reintroduced."""

    def test_the_ranking_has_nothing_above_norms_verdict(self):
        assert sc._RANK == {"enriched": 1, "printed": 0}

    def test_a_read_cannot_be_recorded_as_anything_stronger(self):
        import inspect

        assert "provenance" not in inspect.signature(sc.observe_extraction).parameters

    def test_a_row_refuses_any_other_provenance(self, db_session):
        import pytest

        assert SupplierProduct.PROVENANCES == ("enriched", "printed", "practice")
        for value in ("human", "admin", "verified"):
            with pytest.raises(ValueError):
                SupplierProduct(supplier_key="Trents", code="X1", provenance=value)
        row = SupplierProduct(supplier_key="Trents", code="X1", provenance="printed")
        with pytest.raises(ValueError):
            row.provenance = "human"


class TestProductsWithNoCode:
    """Some suppliers print no codes — Factory Group, 0 of 27 lines on
    INV-0446 (14 Oct 2026). Keyed on codes alone the catalogue learned nothing
    about them, so every invoice asked again. Their products are filed under
    their wording."""

    def test_the_key_is_the_code_else_the_wording(self):
        assert sc.product_key("4183758", "MALFY") == "4183758"
        assert sc.product_key(None, "Strawberry Syrup-Case") == (
            "desc:strawberry syrup case"
        )
        assert sc.product_key("", "  STRAWBERRY  syrup—case ") == (
            "desc:strawberry syrup case"
        )
        assert sc.product_key(None, "") is None

    def test_a_codeless_estimate_is_learned_and_answers(self, db_session):
        ln = {
            "code": None,
            "description": "Coconut Flakes-Bag",
            "unit_resolved": {
                "unit_name": "Kilo",
                "confidence": "high",
                "why": "a 1 kg foodservice bag",
            },
        }
        assert sc.learn_from_resolver(db_session, "Factory Group", [ln]) == 1
        key = sc.product_key(None, "Coconut Flakes-Bag")
        assert sc.catalog_unit_for_line(db_session, "Factory Group", key)[
            "unit_name"
        ] == ("Kilo")

    def test_a_codeless_printed_size_is_observed(self, db_session):
        _spec(db_session, name="Factory Group")
        out = sc.observe_extraction(
            db_session,
            _ext(
                supplier="Factory Group",
                lines=[
                    {
                        "code": None,
                        "description": "Coconut Milk 1L",
                        "unit_of_measure": "1L",
                    }
                ],
            ),
        )
        assert out["observed"] == 1
        row = sc.lookup(db_session, "Factory Group", "desc:coconut milk 1l")
        assert row is not None and row.unit_name == "1L"
