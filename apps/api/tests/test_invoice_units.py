"""Invoice units: the equivalence rules and the catalogue seam built on them.

``services/invoice_units`` is the single implementation of unit equivalence
(the sandboxed consolidator's mirror copy was retired with the replica-primary
refactor), and these are its only direct tests. Below them, the catalogue's
resolve-or-create seam (``services/invoice_catalog``) that the create-unit
endpoint, the claude.ai card and autopilot share.

Moved here from test_invoice_fixes_handler.py, where they sat beside the
retired ``link_po`` / ``unit`` fix appliers.
"""

import pytest

from app.services import invoice_catalog as CAT
from app.services import invoice_units as U
from app.services.invoice_units import is_multipack, multipack_equal, units_equivalent

UNITS = [
    {"id": "u-each", "name": "Each", "ratio": 1.0, "stockUnitType": "Count"},
    {"id": "u-kilo", "name": "Kilo", "ratio": 1.0, "stockUnitType": "Weight"},
    {"id": "u-100pc", "name": "100 piece", "ratio": 100.0, "stockUnitType": "Count"},
]


class FakeLoaded:
    """Serves the unit list; records writes."""

    def __init__(self, units):
        self._units = units
        self.writes = []  # (method, path, body)

    def get(self, path):
        assert path.startswith("/1.0/stock/internal/units"), f"unexpected GET {path}"
        return self._units

    def request(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {}


class _CreatingUnits(FakeLoaded):
    """Loaded answering POST /units with the created unit."""

    def request(self, method, path, body=None):
        self.writes.append((method, path, body))
        if method == "POST" and path.endswith("/units"):
            return {
                "id": "u-new",
                "name": body["name"],
                "ratio": body["ratio"],
                "stockUnitType": body["stockUnitType"],
            }
        return {}


def _no_llm(name, db):
    raise AssertionError(f"the LLM was asked about {name!r}")


# ---- services/invoice_units ------------------------------------------------


def test_is_multipack_tolerates_spacing():
    # Spaced pack notation is the same pack — '6x 750ml' == '6x750ml'.
    for good in ("5x3kg", "6x750ml", "6x 750ml", "4 x 6 pack", "2X12"):
        assert is_multipack(good), good
    for bad in ("Case(s)", "750ml", "x2", "box", "", None):
        assert not is_multipack(bad), bad


def test_multipack_equal():
    # Component-wise pack equality ('6x1L' == '6 X 1 Litre'), never across
    # pack shapes.
    assert multipack_equal("6x1L", "6 X 1 Litre")
    assert multipack_equal("6x750ml", "6x 750ml")
    assert not multipack_equal("6x750ml", "6x1L")
    assert not multipack_equal("4x6 pack", "24 pack")
    assert not multipack_equal("2x12 pack", "24 pack")
    # Case/whitespace never distinguish; digits and DOTS do (user rule,
    # 08 Aug 2026 — the alnum norm let '1.9 KG' name-match '19 KG').
    assert multipack_equal("Each", " each")
    assert not multipack_equal("6x1.5L", "6x15L")


def test_units_equivalent():
    # Hancocks 4358010, 08 Aug 2026: a copy printing only a pack COUNT names
    # the same pack as the sized multipack, and EA names a single sized
    # bottle — neither may churn a unit suggestion. Counts still gate
    # everything else.
    # Pack "6 PK" vs the Loaded multipack it denotes
    assert units_equivalent("6x750mL", "6 pack")
    assert units_equivalent("6 pk", "6x 750ml")
    assert units_equivalent("dozen", "12x330ml")
    # EA vs the single-bottle unit
    assert units_equivalent("375 mL", "each")
    assert units_equivalent("750 mL", "EA")
    # Magnitude equality still holds
    assert units_equivalent("0.7 L", "700 mL")
    # Counts must actually match
    assert not units_equivalent("6x750mL", "12 pack")
    assert not units_equivalent("each", "12 pack")
    assert not units_equivalent("24 pack", "4x6 pack")
    # 'each' never absorbs weight-priced units (quantities mean kilos)
    assert not units_equivalent("1.9 KG", "each")
    # Vague packaging words still aren't equivalence material
    assert not units_equivalent("Case(s)", "6x750ml")
    assert not units_equivalent("Case(s)", "750ml")


# ---- invoice_catalog.resolve_unit ------------------------------------------


def test_multipack_matches_exact_name_only():
    # A multipack proposed name resolves by EXACT name; it is NOT fuzzily
    # reused for a same-ratio simple unit — an unmatched multipack → None
    # (so get_or_create_unit creates it).
    units = UNITS + [
        {"id": "u-15kg", "name": "15 KG", "ratio": 15.0, "stockUnitType": "Weight"}
    ]
    assert CAT.resolve_unit(FakeLoaded(units), "5x3kg") is None
    units2 = units + [
        {"id": "u-5x3", "name": "5x3kg", "ratio": 15.0, "stockUnitType": "Weight"}
    ]
    assert CAT.resolve_unit(FakeLoaded(units2), "5x3kg")["id"] == "u-5x3"


def test_resolve_unit_keeps_dots_distinct():
    # '1.9 KG' must never NAME-match '19 KG' (the old alnum norm merged them);
    # with no name hit and different magnitudes, resolution correctly fails.
    units = UNITS + [
        {"id": "u-19kg", "name": "19 KG", "ratio": 19.0, "stockUnitType": "Weight"}
    ]
    lh = FakeLoaded(units)
    assert CAT.resolve_unit(lh, "1.9 KG") is None
    # Case/whitespace variants of the SAME name still resolve.
    assert CAT.resolve_unit(lh, " 19 kg ")["id"] == "u-19kg"


def test_resolve_unit_component_equivalent_multipack():
    # Accepting '6x1L' must RESOLVE to the existing '6 X 1 Litre' unit rather
    # than creating a near-duplicate; a differently-shaped pack still creates.
    units = UNITS + [
        {"id": "u-6x1l", "name": "6 X 1 Litre", "ratio": 6.0, "stockUnitType": "Volume"}
    ]
    lh = FakeLoaded(units)
    assert CAT.resolve_unit(lh, "6x1L")["id"] == "u-6x1l"
    assert CAT.resolve_unit(lh, "6x 1 litre")["id"] == "u-6x1l"
    assert CAT.resolve_unit(lh, "12x1L") is None  # different count → create


# ---- invoice_catalog.get_or_create_unit -------------------------------------


class TestGetOrCreateUnit:
    """The shared resolve-or-create seam behind the
    /invoice-fixes/create-unit endpoint, the claude.ai card and autopilot."""

    def test_existing_unit_short_circuits_without_writes(self):
        lh = FakeLoaded(UNITS)
        unit, created = CAT.get_or_create_unit(lh, "100 piece", None)
        assert unit["id"] == "u-100pc"
        assert created is False
        # "1 each" is guideline-equivalent to the Loaded "Each" unit.
        unit, created = CAT.get_or_create_unit(lh, "1 each", None)
        assert (unit["id"], created) == ("u-each", False)
        assert lh.writes == []

    @pytest.mark.parametrize(
        "name,ratio,unit_type",
        [
            ("49.5L", 49.5, "Volume"),
            # An OUTER multipack is created with the size read off the name
            # (5 x 3kg = 15 kg).
            ("5x3kg", 15.0, "Weight"),
        ],
    )
    def test_a_name_that_reads_is_created_without_the_llm(
        self, monkeypatch, name, ratio, unit_type
    ):
        monkeypatch.setattr(CAT, "llm_unit_spec", _no_llm)
        lh = _CreatingUnits(UNITS)
        unit, created = CAT.get_or_create_unit(lh, name, None)
        assert created is True
        assert unit["id"] == "u-new"
        assert lh.writes == [
            (
                "POST",
                "/1.0/stock/internal/units",
                {"name": name, "ratio": ratio, "stockUnitType": unit_type},
            )
        ]

    def test_a_name_that_does_not_read_asks_the_llm(self, monkeypatch):
        # Metres are descriptive, not a stock dimension: the parser can't say,
        # so the LLM's reading (8 rolls) is used.
        monkeypatch.setattr(
            CAT,
            "llm_unit_spec",
            lambda name, db: {"ratio": 8.0, "stock_unit_type": "Count"},
        )
        lh = _CreatingUnits(UNITS)
        CAT.get_or_create_unit(lh, "8x300m", None)
        assert lh.writes[0][2] == {
            "name": "8x300m",
            "ratio": 8.0,
            "stockUnitType": "Count",
        }

    def test_the_persons_own_size_wins(self, monkeypatch):
        # The new-unit form: what the person entered is what Loaded gets, even
        # where the name alone would read differently.
        monkeypatch.setattr(CAT, "llm_unit_spec", _no_llm)
        lh = _CreatingUnits(UNITS)
        CAT.get_or_create_unit(lh, "Case", None, ratio=24, unit_type="Count")
        assert lh.writes[0][2] == {
            "name": "Case",
            "ratio": 24.0,
            "stockUnitType": "Count",
        }

    @pytest.mark.parametrize(
        "ratio,unit_type",
        [(0, "Count"), (-1, "Weight"), ("lots", "Count"), (2, "Length")],
    )
    def test_a_bad_size_or_type_is_refused(self, ratio, unit_type):
        lh = _CreatingUnits(UNITS)
        with pytest.raises(CAT.CatalogError) as e:
            CAT.get_or_create_unit(lh, "Case", None, ratio=ratio, unit_type=unit_type)
        assert e.value.status == 400
        assert lh.writes == []

    def test_an_existing_unit_is_used_whatever_size_was_entered(self):
        # Resolve-first beats the form: a twin of an existing unit is never made.
        lh = _CreatingUnits(UNITS)
        unit, created = CAT.get_or_create_unit(
            lh, "each", None, ratio=1, unit_type="Count"
        )
        assert (unit["id"], created) == ("u-each", False)
        assert lh.writes == []

    def test_unresolvable_spec_raises_and_writes_nothing(self, monkeypatch):
        # A bare packaging word ("carton") isn't a real unit; the LLM spec
        # resolve declines → refused, and no bad unit is created.
        monkeypatch.setattr(CAT, "llm_unit_spec", lambda name, db: None)
        lh = FakeLoaded(UNITS)
        with pytest.raises(CAT.CatalogError, match="couldn't work out the size") as e:
            CAT.get_or_create_unit(lh, "carton", None)
        assert e.value.status == 422
        assert lh.writes == []


@pytest.mark.parametrize(
    "name,spec",
    [
        ("500g", {"ratio": 0.5, "stock_unit_type": "Weight"}),
        ("6x700ml", {"ratio": 4.2, "stock_unit_type": "Volume"}),
        ("4x6 pack", {"ratio": 24.0, "stock_unit_type": "Count"}),
        ("12 pack", {"ratio": 12.0, "stock_unit_type": "Count"}),
        ("5x3kg", {"ratio": 15.0, "stock_unit_type": "Weight"}),
        ("carton", None),
        ("8x300m", None),
    ],
)
def test_parsed_unit_spec_matches_the_llm_conventions(name, spec):
    # The answers llm_unit_spec's prompt documents, without the call.
    assert CAT.parsed_unit_spec(name) == spec


class TestOnlyMeasurementUnits:
    """The owner, 17 Oct 2026: "norm should not be using non measurement units
    at all eg. bunch, market etc as they cant be used in recipes or
    stocktaking". Names from the three AU venues' real unit lists."""

    @pytest.mark.parametrize(
        "name,utype,ratio",
        [
            ("Kilo", "Weight", 1),
            ("Gram", "Weight", 0.001),
            ("Each", "Count", 1),
            ("EA", "Count", 1),
            ("Dozen", "Count", 12),
            ("180 Pack", "Count", 180),
            ("700 Grams", "Weight", 0.7),
            ("Punnet (400g)", "Weight", 0.4),
            ("12 Pack (1L)", "Volume", 12),
            ("190gx6", "Weight", 1.14),
            ("300ML - 8 PACK", "Volume", 2.4),
            ("4x12 unit", "Count", 48),
            ("Tray (22 units)", "Count", 22),
            ("Box of 12", "Count", 12),
            ("2.27KG (120 Slices)", "Count", 120),
            ("5L Keg", "Volume", 5),
            ("Keg", "Volume", 50),
            ("Triple", "Volume", 0.09),
            ("6x1L", "Volume", 6),
            ("48x250", "Count", 48),
        ],
    )
    def test_a_measurement_is_kept(self, name, utype, ratio):
        assert U.is_measurement({"name": name, "stockUnitType": utype, "ratio": ratio})

    @pytest.mark.parametrize(
        "name,utype,ratio",
        [
            ("Box", "Volume", 250),  # Loaded's default: 250 litres
            ("Bunch", "Count", 1),
            ("Market", "Count", 1),
            ("Punnet", "Count", 1),
            ("CARTON", "Count", 1),
            ("Serving", "Count", 1),
            ("Ream", "Count", 500),
            ("1 KG", "Weight", 1000),  # Somewhere North's: 1,000 kg a bag
            ("2500 Pack", "Count", 1),
            ("285 Grams", "Volume", 0.285),
        ],
    )
    def test_anything_else_is_not(self, name, utype, ratio):
        assert not U.is_measurement(
            {"name": name, "stockUnitType": utype, "ratio": ratio}
        )

    def test_the_unit_list_reaches_norm_with_them_marked_deleted(self):
        rows = [
            {"id": "u-kilo", "name": "Kilo", "ratio": 1, "stockUnitType": "Weight"},
            {"id": "u-box", "name": "Box", "ratio": 250, "stockUnitType": "Volume"},
            {
                "id": "u-old",
                "name": "Bunch",
                "ratio": 1,
                "stockUnitType": "Count",
                "datestampDeleted": "2025-03-01T00:00:00Z",
            },
        ]
        out = U.hide_non_measurement_units(
            "https://api.loadedhub.com/1.0/stock/internal/units?includeDeleted=true",
            rows,
        )
        by_id = {u["id"]: u for u in out}
        assert not by_id["u-kilo"].get("datestampDeleted")
        assert by_id["u-box"]["datestampDeleted"] == U.HIDDEN_UNIT_MARK
        assert by_id["u-box"]["hiddenByNorm"] == "not a measurement"
        # Loaded's own deletion is left exactly as it was.
        assert by_id["u-old"]["datestampDeleted"] == "2025-03-01T00:00:00Z"

    def test_other_responses_pass_through(self):
        rows = [{"id": "x", "name": "Box", "ratio": 250, "stockUnitType": "Volume"}]
        assert U.hide_non_measurement_units("/1.0/stock/internal/items", rows) == [
            {"id": "x", "name": "Box", "ratio": 250, "stockUnitType": "Volume"}
        ]
