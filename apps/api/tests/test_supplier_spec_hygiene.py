"""One name, one spec — the guards on every path that writes a spec alias.

A supplier spec is a GLOBAL row shared by every Norm venue, so an alias on it
is an identity claim about a business. On 10 Aug 2026 the sensei's alias merge
copied 'Service Foods' and 'Service Foods Auckland' onto the EUROVINTAGE spec
(a wine wholesaler) with no validation at all. From then on every Service
Foods invoice matched a wine prompt, which is how IN11413982 was extracted
with the wrong rules, filed in the dojo under the wrong supplier, and left
unreceivable — and the fault was self-reinforcing, because the sensei then
saw the wine spec as "the current spec" for the next Service Foods sample.
"""

import pytest

from app.db.config_models import (
    SupplierInvoiceSpec,
    SupplierProduct,
    SupplierSpecSample,
)
from app.services import spec_dojo

from .supplier_fakes import bind_sessions, install_fake_loaded


@pytest.fixture
def roster(db_session):
    """The two specs at the centre of the incident, clean."""
    sf = SupplierInvoiceSpec(
        name="Service Foods", aliases=["Service Foods Auckland"], instructions="SF text"
    )
    ev = SupplierInvoiceSpec(
        name="Eurovintage", aliases=["EuroVintage Ltd"], instructions="wine text"
    )
    db_session.add_all([sf, ev])
    db_session.commit()
    return sf, ev


class TestAliasWritesAreGuarded:
    def test_the_router_refuses_another_specs_name_or_alias(
        self, client, admin_headers, roster
    ):
        _sf, ev = roster
        res = client.put(
            f"/api/supplier-invoice-specs/{ev.id}",
            headers=admin_headers,
            json={"aliases": ["EuroVintage Ltd", "Service Foods"]},
        )
        assert res.status_code == 409, res.text
        assert "Service Foods" in res.json()["detail"]
        # Another spec's ALIAS is just as much its identity.
        res = client.put(
            f"/api/supplier-invoice-specs/{ev.id}",
            headers=admin_headers,
            json={"aliases": ["Service Foods Auckland"]},
        )
        assert res.status_code == 409

    def test_a_spec_may_keep_its_own_aliases(self, client, admin_headers, roster):
        sf, _ev = roster
        res = client.put(
            f"/api/supplier-invoice-specs/{sf.id}",
            headers=admin_headers,
            json={"aliases": ["Service Foods Auckland", "Service Foods NZ"]},
        )
        assert res.status_code == 200, res.text

    def test_creating_a_spec_that_steals_an_alias_is_refused(
        self, client, admin_headers, roster
    ):
        res = client.post(
            "/api/supplier-invoice-specs",
            headers=admin_headers,
            json={"name": "Wine Co", "aliases": ["Service Foods Auckland"]},
        )
        assert res.status_code == 409

    def test_a_name_or_alias_that_names_no_business_is_refused(
        self, client, admin_headers, roster
    ):
        """'PTY LTD' named a layout, and every 'Pty Ltd' supplier fitted it."""
        sf, _ev = roster
        res = client.post(
            "/api/supplier-invoice-specs",
            headers=admin_headers,
            json={"name": "PTY LTD"},
        )
        assert res.status_code == 422 and "names no business" in res.text
        for body in (
            {"name": "LTD"},
            {"aliases": ["Service Foods Auckland", "Pty Ltd"]},
        ):
            res = client.put(
                f"/api/supplier-invoice-specs/{sf.id}", headers=admin_headers, json=body
            )
            assert res.status_code == 422, body

    def test_a_rename_takes_the_suppliers_products_with_it(
        self, client, admin_headers, roster, db_session
    ):
        """The catalogue files products under the layout's name: 'ATOMIC' and
        'KEMP WINE MERCHANTS LIMITED' kept 6 products no layout could reach
        after renames (22 Oct 2026)."""
        sf, _ev = roster
        db_session.add(SupplierProduct(supplier_key="Service Foods", code="SF1"))
        db_session.commit()
        res = client.put(
            f"/api/supplier-invoice-specs/{sf.id}",
            headers=admin_headers,
            json={"name": "Service Foods Group"},
        )
        assert res.status_code == 200, res.text
        keys = {r.supplier_key for r in db_session.query(SupplierProduct).all()}
        assert keys == {"Service Foods Group"}


def _sample(db_session, spec, analysis):
    s = SupplierSpecSample(
        spec_id=spec.id,
        label="IN11413982.pdf",
        pdf_bytes=b"%PDF-",
        expected={"invoice_number": "IN11413982"},
        analysis=analysis,
    )
    db_session.add(s)
    db_session.commit()
    return s


def _proposal(**over):
    base = {
        "status": "ready",
        "green": True,
        "proposed_instructions": "",
        "ground_truth": {"invoice_number": "IN11413982"},
    }
    base.update(over)
    return base


class TestSenseiAliasMerge:
    def test_the_merge_no_longer_copies_local_spellings(self, db_session, roster):
        """The write that caused the incident. Moving a sample to its real
        spec must carry the source spec's NAME only — its aliases are other
        accounts' spellings, which belong in Loaded, not multiplied onto a
        global row. (And the roster gate below must not be a brake on this
        ordinary, clean merge.)"""
        sf, _ev = roster
        dupe = SupplierInvoiceSpec(
            name="SERVICE FOODS LTD",
            aliases=["Service Foods Online", "SERVICE FOODS - AUCKLAND FOODSERVICE"],
            instructions="",
        )
        db_session.add(dupe)
        db_session.commit()
        sample = _sample(db_session, dupe, _proposal(alias_of="Service Foods"))

        spec_dojo.apply_analysis_proposal(db_session, sample)

        assert sf.aliases == ["Service Foods Auckland", "SERVICE FOODS LTD"]
        assert sample.spec_id == sf.id  # the sample moved
        assert (
            db_session.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.id == dupe.id)
            .first()
            is None
        )  # the empty duplicate row is gone

    def test_an_alias_claimed_by_a_third_spec_is_refused(self, db_session, roster):
        """One bad adjudication must not be able to make every future invoice
        for a business a coin toss."""
        _sf, ev = roster
        dupe = SupplierInvoiceSpec(name="Service Foods Auckland", instructions="")
        db_session.add(dupe)
        db_session.commit()
        sample = _sample(db_session, dupe, _proposal(alias_of="Eurovintage"))

        spec_dojo.apply_analysis_proposal(db_session, sample)

        # 'Service Foods Auckland' already belongs to the Service Foods spec,
        # so it is NOT added to Eurovintage.
        assert ev.aliases == ["EuroVintage Ltd"]

    def test_reapplying_when_the_sample_already_moved_is_not_an_error(
        self, db_session, roster
    ):
        """A double click used to raise "alias target spec 'X' no longer
        exists" — alarming, and untrue."""
        sf, _ev = roster
        sample = _sample(db_session, sf, _proposal(alias_of="Service Foods"))
        spec_dojo.apply_analysis_proposal(db_session, sample)  # must not raise
        assert sample.spec_id == sf.id


class TestTheRosterGate:
    """The whole-roster check that runs before any spec write commits.

    The per-write guards ask "is this one field legal". The gate asks "is the
    roster still coherent afterwards" — which is the question nobody was
    asking, and the reason a wrong alias survived for a day and reinforced
    itself.
    """

    def test_a_merge_that_drags_a_foreign_sample_is_refused(self, db_session, roster):
        """Applying `alias_of` moves EVERY sample off the source spec. If one
        of them is another supplier's invoice it lands under a prompt that
        cannot read it — invisible to any single-field guard, because each
        individual write is perfectly legal."""
        sf, ev = roster
        dupe = SupplierInvoiceSpec(name="Service Foods NZ", aliases=[], instructions="")
        db_session.add(dupe)
        db_session.commit()
        # A Eurovintage invoice mistakenly sitting on the duplicate row.
        stray = SupplierSpecSample(
            spec_id=dupe.id,
            label="1229552.pdf",
            pdf_bytes=b"%PDF-",
            last_run={"extraction": {"supplier_name": "EuroVintage Ltd"}},
        )
        db_session.add(stray)
        sample = _sample(db_session, dupe, _proposal(alias_of="Service Foods"))

        with pytest.raises(ValueError, match="break the spec roster"):
            spec_dojo.apply_analysis_proposal(db_session, sample)

        # Nothing was written: the savepoint undid the merge, so the stray
        # sample stayed put and the target spec's aliases are untouched.
        assert stray.spec_id == dupe.id
        assert sf.aliases == ["Service Foods Auckland"]


class TestSenseiCleansTheRoster:
    def test_a_misfiled_alias_is_removed(self, db_session, roster):
        sf, ev = roster
        ev.aliases = ["EuroVintage Ltd", "Service Foods"]
        db_session.commit()
        sample = _sample(
            db_session,
            sf,
            _proposal(
                wrong_aliases=[
                    {"spec_id": ev.id, "spec": "Eurovintage", "alias": "Service Foods"}
                ]
            ),
        )
        spec_dojo.apply_analysis_proposal(db_session, sample)
        assert ev.aliases == ["EuroVintage Ltd"]

    def test_a_spec_named_for_a_branch_can_be_renamed_to_the_business(
        self, db_session, roster
    ):
        branch = SupplierInvoiceSpec(
            name="Trents Wholesale Limited Trents Dunedin Branch", instructions="x"
        )
        db_session.add(branch)
        db_session.commit()
        db_session.add(
            SupplierProduct(supplier_key=branch.name, code="T1", provenance="enriched")
        )
        db_session.commit()
        sample = _sample(db_session, branch, _proposal(canonical_name="Trents"))
        spec_dojo.apply_analysis_proposal(db_session, sample)
        assert branch.name == "Trents"
        # Its products follow it — they were left under the old name before.
        assert [r.supplier_key for r in db_session.query(SupplierProduct).all()] == [
            "Trents"
        ]

    def test_a_rename_to_a_name_with_no_business_is_refused(self, db_session, roster):
        _sf, ev = roster
        sample = _sample(db_session, ev, _proposal(canonical_name="PTY LTD"))
        spec_dojo.apply_analysis_proposal(db_session, sample)
        assert ev.name == "Eurovintage"

    def test_a_merge_takes_the_duplicates_products_to_the_business(
        self, db_session, roster
    ):
        """A product both rows know keeps the better-ranked reading."""
        sf, _ev = roster
        dupe = SupplierInvoiceSpec(name="SERVICE FOODS LTD", instructions="")
        db_session.add(dupe)
        db_session.add_all(
            [
                SupplierProduct(
                    supplier_key="SERVICE FOODS LTD", code="A", provenance="enriched"
                ),
                SupplierProduct(
                    supplier_key="SERVICE FOODS LTD", code="B", provenance="printed"
                ),
                SupplierProduct(
                    supplier_key="Service Foods", code="B", provenance="enriched"
                ),
            ]
        )
        db_session.commit()
        sample = _sample(db_session, dupe, _proposal(alias_of="Service Foods"))
        spec_dojo.apply_analysis_proposal(db_session, sample)
        rows = db_session.query(SupplierProduct).order_by(SupplierProduct.code).all()
        assert [(r.supplier_key, r.code, r.provenance) for r in rows] == [
            ("Service Foods", "A", "enriched"),
            ("Service Foods", "B", "enriched"),
        ]

    def test_a_rename_onto_another_specs_identity_is_refused(self, db_session, roster):
        _sf, ev = roster
        sample = _sample(
            db_session, ev, _proposal(canonical_name="Service Foods Auckland")
        )
        spec_dojo.apply_analysis_proposal(db_session, sample)
        assert ev.name == "Eurovintage"


class TestNoDuplicateSpecIsBorn:
    def test_a_known_alias_reuses_the_existing_spec(self, db_session, roster):
        """Duplicate spec ROWS are born at staging time, before the sensei
        ever runs — so the account's other spellings must be offered here."""
        sf, _ev = roster
        spec, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "SERVICE FOODS LTD", "Service Foods Auckland"
        )
        assert spec.id == sf.id and created is False

    def test_a_name_printed_over_two_lines_is_a_name_and_an_alias(
        self, db_session, roster
    ):
        """M H Luxe, 15 Oct 2026: the letterhead's two lines became one spec
        name, which no invoice's supplier ever matched."""
        spec, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "M H Luxe Holdings Pty Ltd\nLuxe Bakery Sydney"
        )
        assert created is True
        assert (spec.name, spec.aliases) == (
            "M H Luxe Holdings Pty Ltd",
            ["Luxe Bakery Sydney"],
        )
        found, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "Luxe Bakery Sydney"
        )
        assert (found.id, created) == (spec.id, False)

    def test_the_first_line_that_names_a_business_names_the_layout(
        self, db_session, roster
    ):
        """Bessie & Engineers, 20 Oct 2026: Loaded holds Fistonich as two
        lines, "AF" then the name, and the layout was called "AF"."""
        spec, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "AF\nFistonich Family VINEYARDS"
        )
        assert created is True
        assert (spec.name, spec.aliases) == ("Fistonich Family VINEYARDS", [])

    @pytest.mark.parametrize("fragment", ["PTY LTD", "LTD", "PTY.", "&", "AF"])
    def test_a_name_that_names_no_business_files_nothing(
        self, db_session, roster, fragment
    ):
        """Loaded's scan read some Somewhere North statements' supplier as
        "PTY LTD", "LTD" or "PTY." (16–17 Oct 2026), and each became a layout
        that matched every supplier sharing the suffix."""
        with pytest.raises(RuntimeError, match="names no business"):
            spec_dojo.find_or_create_spec_for_supplier(db_session, fragment)
        assert (
            db_session.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.name == fragment)
            .first()
            is None
        )

    def test_a_stolen_alias_no_longer_beats_the_real_spec(self, db_session, roster):
        """The incident, at the staging door. Even with 'Service Foods'
        wrongly listed on the wine spec, a spec whose own NAME is the supplier
        outranks another spec's alias — so the sample is filed correctly.
        Under the old alphabetical first-match-wins it went to Eurovintage."""
        sf, ev = roster
        ev.aliases = ["EuroVintage Ltd", "Service Foods"]
        db_session.commit()
        spec, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "Service Foods"
        )
        assert created is False and spec.id == sf.id

    def test_a_genuine_tie_creates_a_visible_row_rather_than_guessing(
        self, db_session, roster
    ):
        """When BOTH specs match only by alias there is no principled winner,
        so nothing is chosen: a new row appears for a human (or the sensei) to
        resolve, instead of a silently wrong prompt."""
        _sf, ev = roster
        ev.aliases = ["EuroVintage Ltd", "Service Foods Auckland"]
        db_session.commit()
        spec, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "SERVICE FOODS AUCKLAND"
        )
        assert created is True


class TestTheSenseiAsksUnderEverySpellingItKnows:
    """Kaans, 26 Aug 2026: nine invoices a day for three weeks.

    The spec is filed as "Kaan's Catering Supplies"; Loaded's feed calls the
    supplier 'Kaans Catering'. Every guard on the training path asked with the
    bare feed name alone, so a working spec looked absent — the "already ruled"
    check could never fire, and each new invoice re-staged a dojo sample and
    re-ran analysis over that spec with auto-apply live.

    The aliases were there the whole time: they are the account's own record of
    which spellings are one business, and they already decide which spec
    extraction uses. These pin that the training guard asks the same question.
    """

    @pytest.fixture
    def kaans(self, db_session):
        spec = SupplierInvoiceSpec(
            name="Kaan's Catering Supplies",
            aliases=[],
            instructions="External Document No. carries our PO",
        )
        db_session.add(spec)
        db_session.commit()
        return spec

    def test_the_bare_feed_name_misses_it_and_an_alias_finds_it(
        self, db_session, kaans
    ):
        """The starting condition — not a wish, the actual behaviour that made
        the guard fall through — and the hint that closes it."""
        assert spec_dojo.find_spec_for_supplier(db_session, "Kaans Catering") is None
        found = spec_dojo.find_spec_for_supplier(
            db_session,
            "Kaans Catering",
            "CATERING SUPPLIES LTD",
            "Kaan's Catering Supplies Ltd",
        )
        assert found is not None and found.id == kaans.id

    def test_a_covered_supplier_is_not_retrained(self, db_session, kaans, monkeypatch):
        """The consequence that cost money every morning: with the hints, the
        guard fires and nothing is staged or analysed."""
        from app.services import invoice_review as IR

        monkeypatch.setattr(
            "app.services.spec_dojo.train_supplier",
            lambda *_a, **_k: pytest.fail("a supplier with a spec was retrained"),
        )
        trained = IR._maybe_sensei(
            None,
            db_session,
            "v-1",
            "inv-1",
            "Kaans Catering",
            "Kaan's Catering Supplies Ltd",
        )
        assert trained is False

    def test_a_genuinely_new_supplier_is_still_trained(
        self, db_session, kaans, monkeypatch
    ):
        """The guard must not become a blanket 'never train' — that would just
        move the Kaans failure to every supplier without a spec."""
        from app.services import invoice_review as IR

        seen = {}
        monkeypatch.setattr(
            "app.services.spec_dojo.train_supplier",
            lambda db, venue_id, invoice_id, supplier_name, aliases=(): (
                seen.update(supplier_name=supplier_name, aliases=list(aliases))
                or {"status": "trained"}
            ),
        )
        trained = IR._maybe_sensei(
            None, db_session, "v-1", "inv-9", "Brand New Butchery", "BNB Ltd"
        )
        assert trained is True
        assert seen["supplier_name"] == "Brand New Butchery"
        assert seen["aliases"] == ["BNB Ltd"]

    def test_the_hints_reach_the_training_tool_s_own_guard(
        self, db_session, kaans, monkeypatch
    ):
        """The tool re-checks before staging. Called directly (the dojo's own
        entry point) it must reach the same conclusion, or the duplicate work
        simply moves one layer down."""

        bind_sessions(monkeypatch, db_session, "_ConfigSessionLocal")
        monkeypatch.setattr(
            "app.services.spec_dojo.stage_invoice_sample",
            lambda *_a, **_k: pytest.fail("staged a sample for a covered supplier"),
        )
        from app.services import spec_dojo

        out = spec_dojo.train_supplier(
            None,
            "v-1",
            "inv-1",
            "Kaans Catering",
            aliases=["Kaan's Catering Supplies Ltd"],
        )
        assert out["status"] == "skipped"
        assert out["spec_name"] == "Kaan's Catering Supplies"


class TestTheRosterIsAskedAboutTheNameOnThePaper:
    """Step 3 — a business filed under someone else's Loaded record.

    The sensei has only ever been asked about LOADED's supplier name. So when
    a Neat Meat invoice sits on the Coca Cola record, Loaded's name matches a
    spec, the guard says "covered", and the business printed on the paper is
    never trained — for ever.

    Asking about the printed name closes that. The distinction that keeps this
    consistent with 76b23e5, which banned per-account spellings from global
    specs: what a VENUE typed into Loaded is account-local, but a printed name
    is not — every venue receiving from that supplier sees the same paper. It
    is the right thing to key a global row on.
    """

    @pytest.fixture
    def roster(self, db_session):
        coke = SupplierInvoiceSpec(
            name="Coca Cola", aliases=[], instructions="coke rules"
        )
        db_session.add(coke)
        db_session.commit()
        return coke

    def test_a_printed_business_with_no_spec_is_trained(
        self, db_session, roster, monkeypatch
    ):
        seen = {}
        monkeypatch.setattr(
            "app.services.spec_dojo.train_supplier",
            lambda db, venue_id, invoice_id, supplier_name, aliases=(): (
                seen.update(supplier_name=supplier_name, aliases=list(aliases))
                or {"status": "trained"}
            ),
        )
        from app.services import invoice_review as IR

        assert IR._maybe_sensei(None, db_session, "v-1", "inv-1", "Neat Meat") is True
        assert seen["supplier_name"] == "Neat Meat"

    def _stage(self, monkeypatch, db_session, roster, **kw):
        """Stage through the real helper and record what the roster is asked:
        the name and the hints. Loaded files the copy under Coca Cola, whose
        record carries the alias 'COKE NZ'."""
        filed = {}
        bind_sessions(monkeypatch, db_session, "_ConfigSessionLocal")
        monkeypatch.setattr(
            "app.services.spec_dojo.find_or_create_spec_for_supplier",
            lambda _c, name, *hints: (
                filed.update(name=name, hints=list(hints)) or (roster, False)
            ),
        )
        install_fake_loaded(
            monkeypatch, supplier="Coca Cola", supplier_id="s", aliases=["COKE NZ"]
        )
        try:
            spec_dojo.stage_invoice_sample(None, "v-1", "inv-1", draft=True, **kw)
        except Exception:  # noqa: BLE001 — only the filing decision matters here
            pass
        return filed

    def test_the_sample_is_filed_under_the_printed_name_not_loadeds(
        self, db_session, roster, monkeypatch
    ):
        """`stage_invoice_sample` re-reads Loaded's supplierName and used to
        discard the caller's — which would have thrown the printed name away
        and filed the Neat Meat copy under Coca Cola.

        And Loaded's aliases are withheld from a printed name: they are the
        OTHER business's spellings. Offering them would file this sample under
        that business's spec — the Eurovintage fault arriving by a new road."""
        filed = self._stage(monkeypatch, db_session, roster, supplier_name="Neat Meat")
        assert filed["name"] == "Neat Meat"
        assert filed["hints"] == []

    def test_loadeds_own_name_still_carries_its_aliases(
        self, db_session, roster, monkeypatch
    ):
        """The ordinary path is unchanged: same business, aliases offered, so
        no duplicate spec row is born for a spelling variant."""
        filed = self._stage(monkeypatch, db_session, roster)
        assert filed["name"] == "Coca Cola" and filed["hints"] == ["COKE NZ"]
