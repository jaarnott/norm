"""Supplier identity: which business is this, and whose layout is it?

The incident these all descend from (11 Aug 2026, invoice IN11413982): a
SERVICE FOODS LTD invoice was extracted with the EUROVINTAGE wine-wholesaler
prompt, filed in the dojo under that wholesaler, and left with no Loaded
supplier at all. Three symptoms, one cause — the printed name was matched by
hand-rolled string comparison in several places that disagreed.
"""

from types import SimpleNamespace

from app.services.supplier_identity import (
    alias_candidates,
    alias_conflict,
    match_spec,
    norm,
    resolve_supplier,
    words,
)

MAIN = "Main prompt"


def spec(name, aliases=(), enabled=True, id=None):
    return SimpleNamespace(
        id=id or name, name=name, aliases=list(aliases), enabled=enabled
    )


# The real roster at the time of the incident.
SERVICE_FOODS = spec("Service Foods", ["Service Foods Auckland"])
EUROVINTAGE_CLEAN = spec("Eurovintage", ["EuroVintage Ltd"])
EUROVINTAGE_DIRTY = spec(
    "Eurovintage", ["EuroVintage Ltd", "Service Foods", "Service Foods Auckland"]
)


class TestNormalisation:
    def test_norm_keeps_only_alphanumerics(self):
        assert norm("SERVICE FOODS LTD.") == "servicefoodsltd"
        assert norm("  Euro-Vintage (NZ) ") == "eurovintagenz"
        assert norm(None) == ""

    def test_words_drops_legal_suffixes_only(self):
        # 'service' and 'foods' distinguish real businesses and must survive;
        # 'ltd' never distinguishes anything.
        assert words("SERVICE FOODS LTD") == {"service", "foods"}
        assert words("Trents Wholesale Limited") == {"trents", "wholesale"}


class TestSpecMatching:
    def test_the_incident_the_right_spec_wins(self):
        """A Service Foods invoice must not get a wine wholesaler's prompt."""
        got, how = match_spec(
            [EUROVINTAGE_CLEAN, SERVICE_FOODS],
            ["SERVICE FOODS AUCKLAND"],
            main_prompt_name=MAIN,
        )
        assert got is SERVICE_FOODS and how == "alias"

    def test_alphabetical_order_no_longer_decides(self):
        """The old rule was first-match-wins over an alphabetical query, so
        'Eurovintage' beat 'Service Foods' on the letter E."""
        for roster in (
            [EUROVINTAGE_CLEAN, SERVICE_FOODS],
            [SERVICE_FOODS, EUROVINTAGE_CLEAN],
        ):
            got, _ = match_spec(roster, ["SERVICE FOODS LTD"], main_prompt_name=MAIN)
            assert got is SERVICE_FOODS

    def test_a_stolen_alias_is_ambiguous_not_silently_wrong(self):
        """With the corrupt data in place, no spec is confidently right — so
        no spec is chosen. That means the generic prompt plus a sensei pass,
        which is self-correcting; a wrong spec is not."""
        got, why = match_spec(
            [EUROVINTAGE_DIRTY, SERVICE_FOODS],
            ["SERVICE FOODS AUCKLAND"],
            main_prompt_name=MAIN,
        )
        assert got is None and why == "ambiguous"

    def test_exact_name_beats_another_specs_alias(self):
        mine, thief = spec("Cassels", []), spec("Sawmill", ["Cassels"])
        got, how = match_spec([thief, mine], ["Cassels"], main_prompt_name=MAIN)
        assert got is mine and how == "name"

    def test_most_specific_containment_wins(self):
        short, long = spec("Trents"), spec("Trents Wholesale")
        got, _ = match_spec(
            [short, long], ["Trents Wholesale Limited"], main_prompt_name=MAIN
        )
        assert got is long

    def test_any_known_name_can_match(self):
        """Loaded's aliases are the account's spellings; a global spec named
        for any one of them still matches."""
        got, _ = match_spec(
            [SERVICE_FOODS],
            ["SOMETHING UNRECOGNISABLE", "SERVICE FOODS AUCKLAND"],
            main_prompt_name=MAIN,
        )
        assert got is SERVICE_FOODS

    def test_main_prompt_and_disabled_rows_never_match(self):
        assert match_spec(
            [spec(MAIN, ["Service Foods"])], ["Service Foods"], main_prompt_name=MAIN
        ) == (None, None)
        assert match_spec(
            [spec("Service Foods", enabled=False)],
            ["Service Foods"],
            main_prompt_name=MAIN,
        ) == (None, None)

    def test_short_candidates_are_ignored(self):
        # 'CB' would substring-match half the supplier list.
        assert match_spec(
            [spec("CB", []), spec("Bidfood")], ["CB Foods"], main_prompt_name=MAIN
        ) == (None, None)

    def test_no_match_is_not_a_match(self):
        assert match_spec([SERVICE_FOODS], ["Harbour Fish"], main_prompt_name=MAIN) == (
            None,
            None,
        )


class TestAliasCandidates:
    SUPS = [
        {"id": "sf", "name": "SERVICE FOODS AUCKLAND"},
        {"id": "cb", "name": "CRYSTAL BAY FOODS"},
        {"id": "ev", "name": "Eurovintage"},
        {"id": "cf", "name": "Computer Food"},
    ]

    def test_shared_words_find_the_supplier_containment_cannot(self):
        """The bug: aliases were fetched only for suppliers already matching
        by containment — but 'SERVICE FOODS LTD' and 'SERVICE FOODS AUCKLAND'
        share none, so the alias list holding 'SERVICE FOODS LTD' verbatim was
        never read, and the invoice resolved to nothing."""
        got = alias_candidates(["SERVICE FOODS LTD"], self.SUPS)
        assert got and got[0]["id"] == "sf"

    def test_a_single_shared_word_still_ranks_below_two(self):
        got = alias_candidates(["SERVICE FOODS LTD"], self.SUPS, limit=4)
        assert [s["id"] for s in got][0] == "sf"
        assert "ev" not in [s["id"] for s in got]

    def test_nothing_in_common_fetches_nothing(self):
        assert alias_candidates(["Harbour Fish"], self.SUPS) == []

    def test_deleted_suppliers_are_never_candidates(self):
        dead = [{"id": "sf", "name": "SERVICE FOODS AUCKLAND", "removedAt": "2026-01"}]
        assert alias_candidates(["SERVICE FOODS LTD"], dead) == []


class TestSupplierResolution:
    SUPS = [
        {"id": "sf", "name": "SERVICE FOODS AUCKLAND"},
        {"id": "ev", "name": "Eurovintage"},
    ]

    def test_the_incident_resolves_through_loadeds_own_alias(self):
        """Loaded already held 'SERVICE FOODS LTD' as an alias of the supplier
        — every fact needed was in the system."""
        s, how = resolve_supplier(
            ["SERVICE FOODS LTD"],
            self.SUPS,
            {"sf": ["SERVICE FOODS LTD", "Service Foods Online"]},
        )
        assert s["id"] == "sf" and how == "exact"

    def test_loadeds_own_name_rescues_an_unrecognisable_copy(self):
        """An invoice raised from a purchase order carries the supplier a
        human chose at order time — the one supplier fact that isn't OCR."""
        s, how = resolve_supplier(
            ["S3RV1CE F00DS", "SERVICE FOODS AUCKLAND"], self.SUPS
        )
        assert s["id"] == "sf" and how == "exact"

    def test_the_copy_outranks_loadeds_guess(self):
        s, _ = resolve_supplier(["Eurovintage", "SERVICE FOODS AUCKLAND"], self.SUPS)
        assert s["id"] == "ev"

    def test_ambiguity_resolves_to_nothing(self):
        sups = [
            {"id": "a", "name": "Akaroa Salmon"},
            {"id": "b", "name": "Akaroa Salmon South"},
        ]
        assert resolve_supplier(["Akaroa"], sups) == (None, None)

    def test_deleted_suppliers_are_never_resolved(self):
        dead = [{"id": "x", "name": "Harbour Fish", "datestampDeleted": "2026-01"}]
        assert resolve_supplier(["Harbour Fish"], dead) == (None, None)

    def test_a_nameless_copy_resolves_to_nothing(self):
        assert resolve_supplier([None, ""], self.SUPS) == (None, None)


class TestAliasConflict:
    def test_an_alias_owned_by_another_specs_name_is_a_conflict(self):
        """Exactly the write that broke production: 'Service Foods' added as
        an alias of Eurovintage while a 'Service Foods' spec existed."""
        assert (
            alias_conflict([SERVICE_FOODS], "Service Foods", spec_id="euro")
            == "Service Foods"
        )

    def test_an_alias_owned_by_another_specs_alias_is_a_conflict(self):
        assert (
            alias_conflict([SERVICE_FOODS], "SERVICE FOODS AUCKLAND", spec_id="euro")
            == "Service Foods"
        )

    def test_a_spec_never_conflicts_with_itself(self):
        assert (
            alias_conflict(
                [SERVICE_FOODS], "Service Foods Auckland", spec_id="Service Foods"
            )
            is None
        )

    def test_an_unclaimed_name_is_free(self):
        assert alias_conflict([SERVICE_FOODS], "Bidfood", spec_id="euro") is None
