"""Roster invariants — the look at the whole spec list that never happened.

Fixtures are the real rows from the 11 Aug 2026 incident and the Trents pair,
because a check calibrated on invented data proves nothing about the roster we
actually have.
"""

from types import SimpleNamespace

from app.services.roster_health import (
    check_alias_collisions,
    check_misfiled_samples,
    check_near_duplicates,
    check_untested_specs,
    regressions,
    roster_issues,
)

MAIN = "Main prompt"


def spec(name, aliases=(), instructions="x", enabled=True, id=None):
    return SimpleNamespace(
        id=id or name,
        name=name,
        aliases=list(aliases),
        instructions=instructions,
        enabled=enabled,
    )


def sample(spec_id, label, supplier=None, where="last_run"):
    blob = {"supplier_name": supplier} if supplier else None
    return SimpleNamespace(
        id=f"{spec_id}:{label}",
        spec_id=spec_id,
        label=label,
        expected=blob if where == "expected" else None,
        last_run={"extraction": blob} if where == "last_run" and blob else None,
        analysis={"ground_truth": blob} if where == "analysis" and blob else None,
    )


# The roster as it was, and as it should be.
SF = spec("Service Foods", ["Service Foods Auckland"])
EV_CLEAN = spec("Eurovintage", ["EuroVintage Ltd"])
# The Eurovintage row exactly as it stood: BOTH stolen aliases.
EV_DIRTY = spec(
    "Eurovintage",
    ["EuroVintage Ltd", "Service Foods", "Service Foods Auckland"],
    id="Eurovintage",
)
CLEAN = [SF, EV_CLEAN]


class TestAliasCollisions:
    def test_the_incident_is_a_collision(self):
        """Two, exactly as the live config DB held them: 'Service Foods'
        against the Service Foods spec's NAME, and 'Service Foods Auckland'
        against its alias."""
        got = check_alias_collisions([EV_DIRTY, SF], main_prompt_name=MAIN)
        assert len(got) == 2
        assert all(i.kind == "alias_collision" and i.severity == "error" for i in got)
        assert {"'Service Foods'", "'Service Foods Auckland'"} == {
            i.problem.split(" is claimed")[0] for i in got
        }

    def test_a_clean_roster_is_silent(self):
        assert check_alias_collisions(CLEAN, main_prompt_name=MAIN) == []

    def test_the_main_prompt_and_disabled_rows_are_out_of_scope(self):
        rows = [
            SF,
            spec(MAIN, ["Service Foods"]),
            spec("Off", ["Service Foods"], enabled=False),
        ]
        assert check_alias_collisions(rows, main_prompt_name=MAIN) == []


class TestNearDuplicates:
    def test_the_two_trents_rows_are_flagged(self):
        a = spec("Trents Wholesale")
        b = spec("Trents Wholesale Limited Trents Dunedin Branch")
        got = check_near_duplicates([a, b], main_prompt_name=MAIN)
        assert len(got) == 1 and got[0].kind == "near_duplicate_spec"

    def test_a_stolen_alias_also_reads_as_a_duplicate(self):
        """The real Eurovintage row claimed both 'Service Foods' AND 'Service
        Foods Auckland', which is every identity word the Service Foods spec
        has — so two independent checks saw it, not one."""
        got = check_near_duplicates([EV_DIRTY, SF], main_prompt_name=MAIN)
        assert len(got) == 1

    def test_the_real_roster_produces_no_false_positives(self):
        """Both pairs that merely SHARE a word are different businesses. The
        subset rule is what keeps them out; a looser rule would flag both."""
        rows = [
            spec("Bidfood", ["Bidfood Limited", "Bidvest Food Service"]),
            spec("Service Foods", ["Service Foods Auckland"]),
            spec("Noisy Brewery"),
            spec("Sawmill", ["The Sawmill Brewing Company", "Sawmill Brewery"]),
            spec("Harbour Fish"),
            spec("Trents Wholesale", ["Trents", "Trents Wholesale Limited"]),
        ]
        assert check_near_duplicates(rows, main_prompt_name=MAIN) == []


class TestMisfiledSamples:
    def test_a_service_foods_invoice_under_the_wine_spec_is_flagged(self):
        """The residue of the incident: after the aliases were cleaned, two
        SERVICE FOODS invoices were still filed under Eurovintage."""
        s = sample("Eurovintage", "IN11413982.pdf", "SERVICE FOODS LTD")
        got = check_misfiled_samples(CLEAN, [s], main_prompt_name=MAIN)
        assert len(got) == 1 and got[0].kind == "misfiled_sample"
        assert "Service Foods" in got[0].fix

    def test_a_correctly_filed_sample_is_silent(self):
        s = sample("Eurovintage", "1229552.pdf", "EuroVintage Ltd")
        assert check_misfiled_samples(CLEAN, [s], main_prompt_name=MAIN) == []

    def test_every_place_the_extraction_might_live_is_read(self):
        for where in ("expected", "last_run", "analysis"):
            s = sample("Eurovintage", "x.pdf", "SERVICE FOODS LTD", where=where)
            assert len(check_misfiled_samples(CLEAN, [s], main_prompt_name=MAIN)) == 1

    def test_a_never_run_sample_is_not_judged(self):
        """No extraction means no evidence — silence, not a guess."""
        s = sample("Eurovintage", "new.pdf", None)
        assert check_misfiled_samples(CLEAN, [s], main_prompt_name=MAIN) == []

    def test_an_unmatchable_supplier_is_not_a_misfiling(self):
        s = sample("Eurovintage", "x.pdf", "Some Unknown Butchery")
        assert check_misfiled_samples(CLEAN, [s], main_prompt_name=MAIN) == []


class TestUntestedSpecs:
    def test_instructions_with_no_sample(self):
        got = check_untested_specs([SF], [], main_prompt_name=MAIN)
        assert len(got) == 1 and got[0].kind == "untested_spec"

    def test_no_instructions_and_no_sample_is_an_orphan(self):
        got = check_untested_specs(
            [spec("Ghost", instructions="")], [], main_prompt_name=MAIN
        )
        assert len(got) == 1 and got[0].kind == "orphan_spec"

    def test_a_covered_spec_is_silent(self):
        s = sample("Service Foods", "a.pdf", "SERVICE FOODS LTD")
        assert check_untested_specs([SF], [s], main_prompt_name=MAIN) == []


class TestTheGate:
    """`regressions` is what the write gate consults."""

    def test_the_corrupting_write_is_caught(self):
        """Replaying the incident: adding 'Service Foods' to the wine spec
        introduces a collision that was not there before, so the gate refuses
        it. This is the single check that would have prevented all of it."""
        before = roster_issues(CLEAN, [], main_prompt_name=MAIN)
        after = roster_issues([EV_DIRTY, SF], [], main_prompt_name=MAIN)
        new = regressions(before, after)
        assert any(i.kind == "alias_collision" for i in new)

    def test_a_repair_is_allowed_through(self):
        """The reverse direction must NOT be blocked, or the roster can never
        be cleaned: going from the corrupt state to the clean one introduces
        nothing."""
        before = roster_issues([EV_DIRTY, SF], [], main_prompt_name=MAIN)
        after = roster_issues(CLEAN, [], main_prompt_name=MAIN)
        assert regressions(before, after) == []

    def test_a_pre_existing_finding_does_not_block_unrelated_work(self):
        """The gate asks 'does this make it worse', never 'is it perfect' —
        otherwise one old wart freezes every future change."""
        dirty = [EV_DIRTY, SF]
        before = roster_issues(dirty, [], main_prompt_name=MAIN)
        after = roster_issues(dirty + [spec("Harbour Fish")], [], main_prompt_name=MAIN)
        assert regressions(before, after) == []

    def test_moving_a_sample_to_its_real_spec_is_allowed(self):
        mis = [sample("Eurovintage", "IN11413982.pdf", "SERVICE FOODS LTD")]
        fixed = [sample("Service Foods", "IN11413982.pdf", "SERVICE FOODS LTD")]
        before = roster_issues(CLEAN, mis, main_prompt_name=MAIN)
        after = roster_issues(CLEAN, fixed, main_prompt_name=MAIN)
        assert regressions(before, after) == []
        assert any(i.kind == "misfiled_sample" for i in before)
