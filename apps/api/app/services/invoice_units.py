"""Unit-of-measure parsing for invoice receiving.

THE single implementation (the sandboxed consolidator's mirror copy was
retired with the replica-primary refactor — the engine no longer does unit
math). Used by the replica's unit resolution and the invoice-fixes handler
to match a delivered unit to an existing Loaded unit.
"""

from __future__ import annotations

import re

_UOM_WORDS: dict[str, tuple[str, float]] = {
    "kg": ("weight", 1000),
    "kgs": ("weight", 1000),
    "kilo": ("weight", 1000),
    "kilos": ("weight", 1000),
    "kilogram": ("weight", 1000),
    "kilograms": ("weight", 1000),
    "g": ("weight", 1),
    "gm": ("weight", 1),
    "gr": ("weight", 1),
    "gram": ("weight", 1),
    "grams": ("weight", 1),
    "l": ("volume", 1000),
    "lt": ("volume", 1000),
    "ltr": ("volume", 1000),
    "litre": ("volume", 1000),
    "liter": ("volume", 1000),
    "litres": ("volume", 1000),
    "liters": ("volume", 1000),
    "ml": ("volume", 1),
    "mls": ("volume", 1),
    "ea": ("count", 1),
    "each": ("count", 1),
    "pc": ("count", 1),
    "pcs": ("count", 1),
    "piece": ("count", 1),
    "pieces": ("count", 1),
    "pack": ("count", 1),
    "pk": ("count", 1),
    "doz": ("count", 12),
    "dozen": ("count", 12),
    "dz": ("count", 12),
    "pair": ("count", 2),
}
_UOM_VAGUE = {
    "pkt",
    "packet",
    "box",
    "carton",
    "ctn",
    "outer",
    "unit",
    "case",
    "cs",
    "bx",
    "un",
}

# Bare words that say how goods were BUNDLED, never what one delivered item
# is. Includes bare 'pack' — which parse_unit reads as ('count', 1), so it
# looks measurable — but NOT 'each'/'ea': a count of one genuinely is the
# delivered unit for charge-style lines (Bidfood CARTONS→Each, 17 Aug 2026).
# A counted pack ('12 pack', '6pk') is real information and never matches
# here because matching is whole-name only.
_PACKAGING_WORDS = _UOM_VAGUE | {"pack", "packs", "pk", "item", "each item"}


# A charge, not a product: delivery fees, freight, levies, surcharges. Whole
# words, so 'Charger', 'Coffee' and 'Pizza Delivery Box' are products; a line
# that is only "Delivery" ('Delivery - Order Under $200') is a charge.
_CHARGE = re.compile(
    r"\b(freight|shipping|postage|courier|surcharges?|levy|levies|fees?|charges?)\b"
    r"|^\s*delivery\s*(?:$|[-–:(])"
    # A bin emptied ('Service 240l general waste bin', 'Waste Service - 660L
    # Comingle Bin') and a rounding line — never 'Food Service' stock.
    r"|\bservice\b.*\bbins?\b|\bwaste\s+service\b|\brounding\b",
    re.IGNORECASE,
)


def is_charge_line(description: object) -> bool:
    """True for a line that bills a charge rather than delivering stock.

    Its unit is one charge — Each — so nothing needs working out: on 15 Oct
    2026 the web-searching estimator was asked about Prime Fresh's 'DELIVERY
    FEE' 26 times, Alsco's 'Environment & Energy Charge' on every invoice,
    and answered 'each' every time.
    """
    return bool(_CHARGE.search(str(description or "")))


def is_packaging_word(text: object) -> bool:
    """True when the whole name is a bare packaging word ('PACK', 'CTN').

    Venues often carry a unit literally named PACK, so such a word resolves
    "successfully" by name and a sizeless line silently receives in a
    meaningless unit (Trents 5973784, 18 Aug 2026). Callers use this to
    refuse the word as unit EVIDENCE and to keep magnitude equivalence from
    laundering an each-line into a packaging-named unit.
    """
    return " ".join(str(text or "").strip().lower().split()) in _PACKAGING_WORDS


# ---------------------------------------------------------------------------
# Measurement units only (owner, 17 Oct 2026)
# ---------------------------------------------------------------------------
#
# "norm should not be using non measurement units at all eg. bunch, market etc
# as they cant be used in recipes or stocktaking." A Loaded unit Norm may use
# states its quantity: a weight, a volume, or a count of identical items
# ('Each', '12 Pack', '180 Pack' — "recipes use '2 eggs'"). Any other unit
# reaches Norm marked deleted, at the two places it reads Loaded's unit list
# (the app's Loaded client and the connector HTTP layer): everything that picks
# a unit already skips deleted ones, and reports — which must still name the
# units old records use — already read deleted ones. Loaded itself is untouched.

#: Whole names that only say how goods were bundled (Box is Loaded's default
#: VOLUME unit of 250 L; Bunch, Punnet and Market are counts of 1).
_BUNDLING_WORDS = _PACKAGING_WORDS | {
    "bag",
    "bags",
    "tray",
    "trays",
    "bunch",
    "bunches",
    "market",
    "punnet",
    "punnets",
    "crate",
    "sack",
}
_SIZE = (
    r"\d+(?:\.\d+)?\s*(?:kg|kgs|g|gm|gms|gr|gram|grams|ml|l|lt|ltr|litre|litres"
    r"|liter|liters)"
)
_COUNT_WORDS = r"(?:units?|packs?|pk|pcs|pieces?|ea|each|slices?|sheets?)"
#: Marker set on a unit Norm hides: Loaded's own deleted units carry a date here.
HIDDEN_UNIT_MARK = "1970-01-01T00:00:00+00:00"
_UNITS_PATH = "/1.0/stock/internal/units"


def _name_quantities(name: str) -> list[dict]:
    """Every quantity a unit's name states, as ``{ratio, stock_unit_type}``.
    Several readings, because Loaded's names come in many shapes: '6x190g',
    '190gx6', '12 Pack (1L)', '300ML - 8 PACK', 'Punnet (400g)', '4x12 unit',
    'Tray (22 units)', 'Box of 12', '5L Keg'."""
    from app.services.invoice_catalog import parsed_unit_spec

    n = " ".join(name.lower().split())
    cands = [n]
    m = re.match(r"^(" + _SIZE + r")\s*x\s*(\d+)$", n)
    if m:
        cands.append(f"{m.group(2)}x{m.group(1)}")
    m = re.match(r"^(\d+)\s*(?:pack|pk)\s*\(([^)]+)\)$", n)
    if m:
        cands.append(f"{m.group(1)}x{m.group(2)}")
    m = re.match(r"^(\d+)\s*x\s*\d", n)
    if m:
        cands.append(f"{m.group(1)} pack")
    m = re.match(r"^(\d+)\s*x\s*(\d+)(?:\s*" + _COUNT_WORDS + r")?$", n)
    if m:
        cands.append(f"{int(m.group(1)) * int(m.group(2))} pack")
    m = re.match(r"^(" + _SIZE + r")\s*[-–]?\s*(\d+)\s*(?:pack|pk)$", n)
    if m:
        cands.append(f"{m.group(2)}x{m.group(1)}")
    cands += [f"{c} pack" for c in re.findall(r"\bof\s+(\d+)\b", n)]
    for inner in re.findall(r"\(([^)]*)\)", n):
        cands.append(inner)
        c = re.match(r"^(\d+)\s*" + _COUNT_WORDS + r"$", inner.strip())
        if c:
            cands.append(f"{c.group(1)} pack")
    cands += re.findall(r"\b(" + _SIZE + r")\b", n)
    out: list[dict] = []
    for c in cands:
        spec = parsed_unit_spec(c)
        if spec and spec not in out:
            out.append(spec)
    return out


def is_measurement(unit: dict) -> bool:
    """Is this Loaded unit a measurement Norm may use?

    - A bundling word on its own ('Box', 'Bunch', 'CARTON') never is.
    - A name that states a quantity must agree with Loaded's own type and ratio
      for the unit — which rejects Somewhere North's '1 KG' (ratio 1000) and
      '2500 Pack' (ratio 1) as surely as a bundling word.
    - A name that states none: a weight or volume Loaded defines stands ('Keg'
      50 L, a bar 'Triple' 90 ml); a count must state its number ('Serving',
      'Single', 'Ream' don't)."""
    name = " ".join(str(unit.get("name") or "").lower().split())
    if not name or name in _BUNDLING_WORDS:
        return False
    utype = unit.get("stockUnitType")
    try:
        ratio = float(unit.get("ratio") or 0)
    except (TypeError, ValueError):
        return False
    stated = _name_quantities(name)
    if stated:
        return any(
            (not utype or s["stock_unit_type"] == utype)
            and abs(s["ratio"] - ratio) <= 1e-6 * max(1.0, ratio)
            for s in stated
        )
    return utype in ("Weight", "Volume")


def hide_non_measurement_units(url: object, payload: object) -> object:
    """Loaded's unit list with every non-measurement unit marked deleted (see
    the block comment above). Any other response passes through untouched."""
    path = str(url or "").split("?")[0].rstrip("/")
    if not path.endswith(_UNITS_PATH) or not isinstance(payload, list):
        return payload
    for unit in payload:
        if (
            isinstance(unit, dict)
            and not unit.get("datestampDeleted")
            and not is_measurement(unit)
        ):
            unit["datestampDeleted"] = HIDDEN_UNIT_MARK
            unit["hiddenByNorm"] = "not a measurement"
    return payload


# Category → the unit TYPES a delivered unit may be. Validators independent
# of any venue's data. A beverage may be a volume OR a count: poured/portioned
# drinks (wine, spirits, kegs, cordials) are volumes, but a drink sold as a whole
# sealed each (bottled/canned beer, cider, RTDs, soft drinks) is a pack of eaches
# — a count (the user's rule, 27 Aug 2026, superseding the 18-Aug "beverages are
# always volumes"). The poured-vs-whole call is made in the extraction/resolver
# prompts, not by a blanket type ban; this only stops a legitimate pack being
# rejected as a type violation.
# Deliberately small and data-driven; enforced only where the category is
# KNOWN (dojo/enrichment set categories — 'unknown' constrains nothing).
CATEGORY_UNIT_TYPES: dict[str, set[str]] = {
    "beverage": {"volume", "count"},
    # food is NOT constrained: fixed packs (weight/volume), random weight
    # (Kilo) and genuine counts (each pie) are all legitimate.
    "packaging": {"count"},
    "fee": {"count"},
    # Herbs and greens sold by the bunch are booked by weight: a count of
    # bunches is booking 'bunch' (is_bunch_line).
    "bunch": {"weight"},
}

#: Sold by the bunch: 'HERB BASIL BCH', 'HERB CORIANDER 5-6 M/B' (a market
#: bunch), 'KALE BUNCH'.
_BUNCH = re.compile(r"\b(bch|bunch(?:es)?|m/b)\b", re.IGNORECASE)


def is_bunch_line(description: object) -> bool:
    """True for a line sold by the bunch. Its unit is a weight: Norm's
    estimator counted Grange Gully Bistro's coriander as '6 Pack' (six retail
    bunches in a market bunch) while dill, with the same wording, was 500 g
    (23 Oct 2026)."""
    return bool(_BUNCH.search(str(description or "")))


def unit_type_allowed(category: object, unit_type: object) -> bool:
    """False only when the category is known AND forbids this unit type."""
    allowed = CATEGORY_UNIT_TYPES.get(str(category or "").strip().lower())
    if not allowed or not unit_type:
        return True
    return str(unit_type).strip().lower() in allowed


def parse_unit(text: object) -> tuple[str, float] | None:
    """'500g' -> ('weight', 500); '5L' -> ('volume', 5000); '12 pack' ->
    ('count', 12); 'Kilo' -> ('weight', 1000); 'pkt' -> None."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    # 'Pack of 2500' is a venue's name for a 2500 Pack (No Trace straws).
    m = re.fullmatch(r"pack\s+of\s+(\d+)", s)
    if m:
        s = f"{m.group(1)} pack"
    num, word = "", ""
    for ch in s:
        if ch.isdigit() or (ch == "." and num and "." not in num):
            if word:
                return None
            num += ch
        elif ch.isalpha():
            word += ch
        elif ch in (" ", "-"):
            continue
        else:
            return None
    if word in _UOM_VAGUE:
        return None
    entry = _UOM_WORDS.get(word)
    if not entry:
        return None
    utype, factor = entry
    if not num:
        return (utype, factor)
    try:
        return (utype, float(num) * factor)
    except ValueError:
        return None


_SIZE_FIRST = re.compile(r"^(\d+(?:\.\d+)?)([a-z]+)x(\d+)$")


def count_first(text: object) -> str:
    """A pack written size first, '700ml x 6' / '300gx12', as the count-first
    form every other check reads ('6x700ml', '12x300g'). Anything else comes
    back unchanged. Passata printed '700ml x 6' was booked as '4.2 KG' and
    zucchini pickles '300g x 12' as '5 KG' because the size-first form was not
    recognised as a pack at all (18 Oct 2026)."""
    s = "".join(str(text or "").lower().split())
    m = _SIZE_FIRST.match(s)
    if m and parse_unit(m.group(1) + m.group(2)) is not None:
        return f"{m.group(3)}x{m.group(1)}{m.group(2)}"
    return str(text or "")


def is_multipack(text: object) -> bool:
    """True for an 'NxM' compound unit like '5x3kg' / '6x700ml' (digit-x-digit),
    including one written size first ('700ml x 6', see ``count_first``).

    Whitespace-tolerant ('6x 750ml' / '4 x 6 pack' print with spaces but are
    the same pack). parse_unit can't compare these, so multipacks are matched
    by exact name and a to-be-created multipack's ratio/type is resolved by
    the LLM, not parsed.
    """
    s = "".join(count_first(text).lower().split())
    i = s.find("x")
    return i > 0 and s[i - 1].isdigit() and i + 1 < len(s) and s[i + 1].isdigit()


def _unit_norm(text: object) -> str:
    """Unit-name key: lowercase, whitespace ignored — case and spacing never
    distinguish units ('Each'/'each', '1kg'/'1 kg'), but digits and DOTS do
    ('1.9 KG' vs '19 KG' are different sizes), so punctuation is kept."""
    return "".join(str(text or "").lower().split())


def multipack_equal(a: object, b: object) -> bool:
    """True when two unit NAMES denote the same pack.

    Multipacks compare component-wise: the counts must match and the inner
    sizes compare by parse_unit magnitude — '6x1L' == '6 X 1 Litre'.
    Unparseable inners fall back to name equality. A multipack never equals a
    differently-shaped name ('4x6 pack' vs '24 pack') — units stay as printed.
    Mirror of _multipack_equal in
    config/consolidators/review_and_receive_invoices.py.
    """
    a, b = count_first(a), count_first(b)
    if _unit_norm(a) == _unit_norm(b):
        return True
    if not (is_multipack(a) and is_multipack(b)):
        return False

    def _split(u: object) -> tuple[str, str]:
        s = "".join(str(u or "").lower().split())
        i = s.find("x")
        return s[:i], s[i + 1 :]

    ca, ia = _split(a)
    cb, ib = _split(b)
    if not ca or ca != cb:
        return False
    pa, pb = parse_unit(ia), parse_unit(ib)
    if pa and pb:
        return pa[0] == pb[0] and abs(pa[1] - pb[1]) < 0.001
    return _unit_norm(ia) == _unit_norm(ib)


def _same_amount(a: float, b: float) -> bool:
    """The same amount, allowing rounding only: within 0.1% (453.6 g is 454 g,
    2.8 kg is not 2.84 kg)."""
    return abs(a - b) <= max(0.001, 0.001 * max(abs(a), abs(b)))


def match_unit_record(name: object, units: list[dict] | None) -> dict | None:
    """The venue's unit record that IS this unit: the one check every step
    uses before Norm suggests creating a unit (owner, 19 Oct 2026: work the
    unit out first, then see if that unit "or a very similar one (very high
    confidence)" is already in Loaded, "since we dont want to just create
    extra units").

    - The same name, ignoring case and spacing: '15kg' is '15 KG'.
    - A pack matches only the same pack: '6 x 750ML' is '6x750ml', and a pack
      written size first ('700ml x 6') reads the same. A pack never matches a
      single size of the same total: '3x5kg' is not '15 KG' (the owner: 3x5kg
      is the right unit for those fries) and '6x1L' is not '6 L'.
    - A single size matches the record of the same kind and amount, allowing
      rounding only: '4500ml' is '4.5 L' and '453.6 g' is '454 Grams', but
      '2.8kg' is not '2.84 KG'. A packaging-word record ('PACK') is matched
      only by its exact name.

    Records come in the list's order; the first match wins."""
    text = str(name or "").replace("×", "x").strip()
    if not text:
        return None
    live = [
        u for u in units or [] if isinstance(u, dict) and not u.get("datestampDeleted")
    ]
    key = _unit_norm(text)
    for u in live:
        if _unit_norm(str(u.get("name") or "").replace("×", "x")) == key:
            return u
    if is_multipack(text):
        for u in live:
            if multipack_equal(str(u.get("name") or "").replace("×", "x"), text):
                return u
        return None
    parsed = parse_unit(text)
    if parsed:
        for u in live:
            # Magnitude equivalence must not launder identity: 'EA' parses as
            # a count of 1 and so does a unit literally named 'PACK', but
            # receiving each-lines in PACK is meaningless (Trents 5973784,
            # 18 Aug 2026). A packaging-word-NAMED unit is matched only by its
            # name (the first tier).
            if is_packaging_word(u.get("name")):
                continue
            pu = parse_unit(u.get("name"))
            if pu and pu[0] == parsed[0] and _same_amount(pu[1], parsed[1]):
                return u
    return None


def _outer_count(u: object) -> float | None:
    """The leading count of an 'NxM' multipack name ('6x750mL' → 6.0), else None."""
    if not is_multipack(u):
        return None
    s = "".join(count_first(u).lower().split())
    try:
        return float(s[: s.find("x")])
    except ValueError:
        return None


def units_equivalent(a: object, b: object) -> bool:
    """True when two unit names denote the same DELIVERED pack.

    Beyond multipack_equal and magnitude equality ('0.7 L' == '700 mL'), a
    copy that prints only a pack COUNT still names the same pack: '6 pack' ==
    '6x750mL', and 'each'/'EA' == a single sized-bottle unit ('750 mL' —
    volume only; 'each' vs a WEIGHT unit stays a mismatch, weight-priced
    quantities mean kilos, not items). Count-vs-count still compares counts,
    so 'each' vs '12 pack' and '24 pack' vs '4x6 pack' remain mismatches.
    Mirror of _units_equivalent in
    config/consolidators/review_and_receive_invoices.py.
    """
    if multipack_equal(a, b):
        return True
    pa, pb = parse_unit(a), parse_unit(b)
    if pa and pb and pa[0] == pb[0] and abs(pa[1] - pb[1]) < 0.001:
        return True
    for p, other in ((pa, b), (pb, a)):
        if not p or p[0] != "count":
            continue
        oc = _outer_count(other)
        if oc is not None:
            if abs(oc - p[1]) < 0.001:
                return True
        else:
            po = parse_unit(other)
            if po and po[0] == "volume" and abs(p[1] - 1) < 0.001:
                return True
    return False
