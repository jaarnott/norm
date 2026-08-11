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

from app.db.config_models import SupplierInvoiceSpec, SupplierSpecSample
from app.services import spec_dojo


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
    def test_the_router_refuses_another_specs_name(self, client, admin_headers, roster):
        sf, ev = roster
        res = client.put(
            f"/api/supplier-invoice-specs/{ev.id}",
            headers=admin_headers,
            json={"aliases": ["EuroVintage Ltd", "Service Foods"]},
        )
        assert res.status_code == 409, res.text
        assert "Service Foods" in res.json()["detail"]

    def test_the_router_refuses_another_specs_alias(
        self, client, admin_headers, roster
    ):
        _sf, ev = roster
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
        global row."""
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

    def test_a_clean_merge_still_goes_through(self, db_session, roster):
        """The gate must not be a brake on ordinary work."""
        sf, _ev = roster
        dupe = SupplierInvoiceSpec(name="SERVICE FOODS LTD", aliases=[], instructions="")
        db_session.add(dupe)
        db_session.commit()
        sample = _sample(db_session, dupe, _proposal(alias_of="Service Foods"))
        spec_dojo.apply_analysis_proposal(db_session, sample)
        assert sample.spec_id == sf.id
        assert "SERVICE FOODS LTD" in sf.aliases


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
        sample = _sample(db_session, branch, _proposal(canonical_name="Trents"))
        spec_dojo.apply_analysis_proposal(db_session, sample)
        assert branch.name == "Trents"

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

    def test_an_unknown_supplier_still_gets_its_own_spec(self, db_session, roster):
        spec, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "Brand New Butchery"
        )
        assert created is True and spec.name == "Brand New Butchery"

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
