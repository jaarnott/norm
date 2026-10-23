"""Supplier identity: which business is this, and whose layout is it?

The incident these all descend from (11 Aug 2026, invoice IN11413982): a
SERVICE FOODS LTD invoice was extracted with the EUROVINTAGE wine-wholesaler
prompt, filed in the dojo under that wholesaler, and left with no Loaded
supplier at all. Three symptoms, one cause — the printed name was matched by
hand-rolled string comparison in several places that disagreed.
"""

from types import SimpleNamespace

from app.services.supplier_identity import (
    plausible_matches,
    admissible_aliases,
    alias_candidates,
    alias_conflict,
    has_identity,
    identity_name,
    match_spec,
    norm,
    resolve_supplier,
    trading_name,
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


class TestANameMustNameABusiness:
    """The rogue layouts (16–21 Oct 2026). Loaded's scan read some suppliers
    as "PTY LTD", "LTD", "PTY." or "&", and a two-line name as "AF"; each
    became a layout's name, and "PTY LTD" fitted every 'Pty Ltd' supplier — a
    Foodlink Australia invoice matched it, and 14 invoices from three
    businesses were received under Reward Supply Co. Pty Ltd."""

    ROGUES = [spec(n) for n in ("PTY LTD", "LTD", "PTY.", "&", "AF")]

    def test_a_legal_suffix_or_initials_alone_is_no_business(self):
        for name in ("PTY LTD", "LTD", "PTY.", "&", "AF", "Pty Ltd", "the", ""):
            assert not has_identity(name), name
        for name in ("ILT", "Foodlink Australia", "B&E FOODS", "Allie's"):
            assert has_identity(name), name

    def test_a_two_line_name_is_named_by_its_first_real_line(self):
        assert identity_name("AF\nFistonich Family VINEYARDS") == (
            "Fistonich Family VINEYARDS"
        )
        assert identity_name("M H Luxe Holdings Pty Ltd\nLuxe Bakery Sydney") == (
            "M H Luxe Holdings Pty Ltd"
        )
        assert identity_name("PTY LTD") is None

    def test_a_rogue_layout_matches_nothing(self):
        for printed in (
            "Foodlink Australia Pty Ltd",
            "Select Fresh Providores Pty Ltd",
            "Reward Supply Co. Pty Ltd",
            "Lambert Ltd T/A Sea 2 You",
            "PTY LTD",
        ):
            assert match_spec(self.ROGUES, [printed], main_prompt_name=MAIN) == (
                None,
                None,
            ), printed

    def test_a_name_matches_word_by_word(self):
        """Squashed letters found 'fresho' inside 'Just Fresh Online Limited'
        and 'lion' inside 'Lionel'. Spacing still may differ."""
        fresho, lion, fc = spec("FRESHO"), spec("Lion"), spec("FreshConnection")
        roster = [fresho, lion, fc]
        assert match_spec(
            roster, ["Just Fresh Online Limited"], main_prompt_name=MAIN
        ) == (
            None,
            None,
        )
        assert match_spec(roster, ["Lionel's Bakery"], main_prompt_name=MAIN)[0] is None
        assert match_spec(roster, ["Lion NZ Ltd"], main_prompt_name=MAIN)[0] is lion
        assert (
            match_spec(roster, ["Fresh Connection Ltd"], main_prompt_name=MAIN)[0] is fc
        )

    def test_a_single_trade_word_only_matches_itself(self):
        """'FRESH' was inside Select Fresh, Bidfresh and Sydney Direct Fresh."""
        fresh = spec("FRESH")
        assert match_spec(
            [fresh], ["Select Fresh Providores"], main_prompt_name=MAIN
        ) == (
            None,
            None,
        )
        assert match_spec([fresh], ["Fresh Ltd"], main_prompt_name=MAIN) == (
            fresh,
            "name",
        )

    def test_a_legal_suffix_does_not_stop_a_name_being_the_name(self):
        """Only a match on the name itself may say which business it is, so
        'SERVICE FOODS LTD' must count as the name 'Service Foods', and a
        name found inside a longer one must not."""
        assert match_spec(
            [SERVICE_FOODS], ["SERVICE FOODS LTD"], main_prompt_name=MAIN
        ) == (SERVICE_FOODS, "name")
        assert match_spec(
            [SERVICE_FOODS], ["Service Foods Christchurch"], main_prompt_name=MAIN
        ) == (SERVICE_FOODS, "contains")

    def test_a_record_with_no_business_in_its_name_is_never_resolved(self):
        """Loaded's scanning left aliases like 'Ltd' on supplier records."""
        sups = [
            {"id": "reward", "name": "Reward Supply Co. Pty Ltd"},
            {"id": "junk", "name": "Ltd"},
        ]
        assert resolve_supplier(["PTY LTD"], sups) == (None, None)
        assert resolve_supplier(["Lambert Ltd T/A Sea 2 You"], sups) == (None, None)
        assert resolve_supplier(
            ["Sea 2 You"], [{"id": "s", "name": "Sea 2 You"}], {"s": ["Ltd"]}
        ) == ({"id": "s", "name": "Sea 2 You"}, "exact")


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
        # A single shared word still ranks below two.
        got = alias_candidates(["SERVICE FOODS LTD"], self.SUPS, limit=4)
        assert [s["id"] for s in got][0] == "sf"
        assert "ev" not in [s["id"] for s in got]

    def test_nothing_in_common_fetches_nothing(self):
        assert alias_candidates(["Harbour Fish"], self.SUPS) == []

    def test_deleted_suppliers_are_never_candidates(self):
        dead = [{"id": "sf", "name": "SERVICE FOODS AUCKLAND", "removedAt": "2026-01"}]
        assert alias_candidates(["SERVICE FOODS LTD"], dead) == []


class TestPlausibleMatches:
    """Could this existing supplier be the business the invoice prints? The
    question behind "the closest supplier" and behind autopilot refusing to
    create a duplicate. Applegum Cafe SI650438 (15 Oct 2026): 'Blue Seas Food
    Services' was proposed as 'PFD Food Services' — they share only trade
    words — and autopilot could never create Blue Seas because of it.
    Every case here is a real printed name against its venue's suppliers."""

    def _names(self, printed, suppliers):
        rows = [{"id": str(i), "name": n} for i, n in enumerate(suppliers)]
        return [s["name"] for s in plausible_matches(printed, rows)]

    def test_trade_words_alone_are_not_the_same_business(self):
        assert self._names("Blue Seas Food Services", ["PFD Food Services"]) == []
        assert self._names("Tasman Liquor Company Limited", ["Allied Liquor"]) == []
        assert (
            self._names("BIDFRESH CHRISTCHURCH", ["Service Foods Christchurch"]) == []
        )
        assert (
            self._names(
                "Mt Roskill Liquor Merchants Ltd Gilmours", ["Federal Merchants"]
            )
            == []
        )
        assert self._names("PNM SYDNEY PTY LTD", ["Sydney Direct Fresh Produce"]) == []

    def test_a_shared_distinctive_word_is(self):
        assert self._names("Neat Meat Limited", ["CARVE MEAT", "NEAT MEATS"]) == [
            "NEAT MEATS"
        ]
        assert self._names("Aitken & Co Limited", ["Aitkens"]) == ["Aitkens"]
        assert self._names("Bidfood Limited", ["Bidfoods fresh produce"]) == [
            "Bidfoods fresh produce"
        ]

    def test_one_name_inside_the_other_is(self):
        # They share no word, but 'KIWIFISH' is the business.
        assert self._names("Kiwi Fish International Limited", ["KIWIFISH"]) == [
            "KIWIFISH"
        ]

    def test_a_name_of_trade_words_must_match_as_a_phrase(self):
        # The 10 Aug 2026 duplicate: SERVICE FOODS LTD is Service Foods
        # Auckland — and not Bidvest Food Service, which has the words only.
        assert self._names(
            "SERVICE FOODS LTD", ["Bidvest Food Service", "SERVICE FOODS AUCKLAND"]
        ) == ["SERVICE FOODS AUCKLAND"]

    def test_deleted_and_placeholder_records_never_match(self):
        rows = [
            {"id": "1", "name": "NEAT MEATS", "removedAt": "2026-01"},
            {"id": "2", "name": "[Unnamed Supplier]"},
        ]
        assert plausible_matches("Neat Meat Limited", rows) == []


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


class TestTheAccountMayNotContradictItself:
    """Loaded's alias list is not a curated registry.

    It is what Loaded's own document scanning has seen, so it holds OCR debris
    ('BIDERESH CHRISTCHURCH', 'Burovintage Ltd') and, where a supplier record
    was renamed or repurposed, the name of a different business outright. Those
    aliases choose the extraction prompt: Glass Goose's 'La Zeppa' record
    carries the alias 'Oravida', so every invoice from the sister venue was
    read with Oravida's layout. Across the five venues, 160 of 376 records
    carry aliases and two of them name another business.

    The account is the authority on its own identities, so it is asked: an
    alias that IS another live supplier record cannot also be this one's
    spelling. Nothing else is second-guessed.
    """

    ACCOUNT = [
        {"id": "1", "name": "La Zeppa"},
        {"id": "2", "name": "Oravida"},
        {"id": "3", "name": "Kaans Catering"},
        {"id": "4", "name": "Akaroa Salmon"},
    ]

    def test_an_alias_naming_another_supplier_record_is_dropped(self):
        """The live fault, verbatim."""
        assert admissible_aliases("La Zeppa", ["Oravida"], self.ACCOUNT) == []

    def test_an_ordinary_spelling_survives(self):
        """The Kaans win this whole exercise exists to keep."""
        keep = ["CATERING SUPPLIES LTD", "Kaan's Catering Supplies Ltd"]
        assert admissible_aliases("Kaans Catering", keep, self.ACCOUNT) == keep

    def test_a_legal_name_that_looks_nothing_like_the_trading_name_survives(self):
        """Exactly what an alias list is FOR — 'Ahi Mokopuna Limited
        Partnership' is Akaroa Salmon. Only the account's own contradiction
        disqualifies an alias, never mere dissimilarity."""
        assert admissible_aliases(
            "Akaroa Salmon", ["Ahi Mokopuna Limited Partnership"], self.ACCOUNT
        ) == ["Ahi Mokopuna Limited Partnership"]

    def test_a_supplier_may_carry_its_own_name(self):
        """Loaded often lists the record's own name among its aliases; that is
        not a contradiction with anything."""
        assert admissible_aliases("La Zeppa", ["LA ZEPPA"], self.ACCOUNT) == [
            "LA ZEPPA"
        ]

    def test_spelling_and_punctuation_do_not_smuggle_one_past(self):
        assert admissible_aliases("La Zeppa", ["  oravida "], self.ACCOUNT) == []

    def test_a_deleted_supplier_record_does_not_veto(self):
        """Only LIVE records speak for the account — a removed one is not a
        statement that two businesses exist today."""
        account = [*self.ACCOUNT[:1], {"id": "2", "name": "Oravida", "removedAt": "x"}]
        assert admissible_aliases("La Zeppa", ["Oravida"], account) == ["Oravida"]

    def test_no_account_list_means_nothing_is_vetoed(self):
        """The list is best-effort (one Loaded call). If it is unavailable the
        aliases must still work — degrade, never blank."""
        assert admissible_aliases("La Zeppa", ["Oravida"], []) == ["Oravida"]


class TestSupplierPlaceholder:
    """The 110016259 regression (19 Aug 2026): Loaded's literal
    '[Unnamed Supplier]' record exact-matched the DRAFT's placeholder name
    hint, and the low-authority exact beat the printed name's correct
    containment match to Bidfood. Placeholders are never identities, and
    every tier runs for a hint before the next hint is consulted. (Moved from
    test_invoice_replica.py on 17 Oct 2026; its build_replica pin stays there.)"""

    PLACEHOLDER = {"id": "sup-junk", "name": "[Unnamed Supplier]"}
    POOL = [
        PLACEHOLDER,
        {"id": "sup-bidfood", "name": "Bidfood"},
        {"id": "sup-bff", "name": "Bidfoods fresh produce"},
    ]

    def test_placeholder_names_are_recognised(self):
        from app.services.supplier_identity import is_placeholder_supplier_name

        assert is_placeholder_supplier_name("[Unnamed Supplier]")
        assert is_placeholder_supplier_name("unnamed supplier")
        assert is_placeholder_supplier_name("")
        assert is_placeholder_supplier_name("   ")
        assert not is_placeholder_supplier_name("Bidfood")
        assert not is_placeholder_supplier_name("Unnamed Brewing Co")

    def test_the_110016259_pin(self):
        s, by = resolve_supplier(
            ["BIDFOOD FSV DUNEDIN", "[Unnamed Supplier]"], self.POOL
        )
        assert s["id"] == "sup-bidfood"
        assert by == "containment"

    def test_a_lower_authority_exact_never_beats_the_copys_containment(self):
        pool = [
            {"id": "sup-a", "name": "Bidfood"},
            {"id": "sup-b", "name": "Totally Other Ltd"},
        ]
        s, by = resolve_supplier(["BIDFOOD FSV DUNEDIN", "Totally Other Ltd"], pool)
        assert s["id"] == "sup-a" and by == "containment"

    def test_placeholder_only_hints_resolve_nothing(self):
        assert resolve_supplier(["[Unnamed Supplier]"], self.POOL) == (None, None)

    def test_ambiguity_on_the_copy_defers_to_loadeds_human_choice(self):
        pool = [
            {"id": "sup-a", "name": "Bidfood North"},
            {"id": "sup-b", "name": "Bidfood South"},
        ]
        # the copy is ambiguous; Loaded's supplier (chosen at order time)
        # breaks the tie via its own exact match
        s, by = resolve_supplier(["Bidfood", "Bidfood South"], pool)
        assert s["id"] == "sup-b" and by == "exact"


class TestTradingName:
    """The name a new supplier record gets: what the business trades as, as
    the venues' own lists name it (onboarding comparison, 21 Oct 2026)."""

    def test_the_name_after_trading_as(self):
        assert trading_name("Lambert Ltd T/A Sea 2 You") == "Sea 2 You"
        assert (
            trading_name("Komulla Pty Ltd trading as Far Meadow Lodge")
            == "Far Meadow Lodge"
        )

    def test_legal_suffixes_are_dropped_from_the_end(self):
        assert trading_name("Reward Supply Co. Pty Ltd") == "Reward Supply Co"
        assert trading_name("Pembroke Patisserie Limited") == "Pembroke Patisserie"
        assert trading_name("CITY MEATS (N.S.W.) PTY. LIMITED") == "CITY MEATS (N.S.W.)"

    def test_a_letterhead_over_two_lines_takes_the_trading_line(self):
        assert (
            trading_name("M H Luxe Holdings Pty Ltd\nLuxe Bakery Sydney")
            == "Luxe Bakery Sydney"
        )

    def test_never_a_name_with_no_business_in_it(self):
        assert trading_name("ECOFRY") == "ECOFRY"
        assert trading_name("PTY LTD") == "PTY LTD"
