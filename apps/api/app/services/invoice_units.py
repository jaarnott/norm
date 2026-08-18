"""Unit-of-measure parsing for invoice receiving.

THE single implementation (the sandboxed consolidator's mirror copy was
retired with the replica-primary refactor — the engine no longer does unit
math). Used by the replica's unit resolution and the invoice-fixes handler
to match a delivered unit to an existing Loaded unit.
"""

from __future__ import annotations

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


def is_packaging_word(text: object) -> bool:
    """True when the whole name is a bare packaging word ('PACK', 'CTN').

    Venues often carry a unit literally named PACK, so such a word resolves
    "successfully" by name and a sizeless line silently receives in a
    meaningless unit (Trents 5973784, 18 Aug 2026). Callers use this to
    refuse the word as unit EVIDENCE and to keep magnitude equivalence from
    laundering an each-line into a packaging-named unit.
    """
    return " ".join(str(text or "").strip().lower().split()) in _PACKAGING_WORDS


# Category → the unit TYPES a delivered unit may be. Validators independent
# of any venue's data — a beverage stocked as a count is a setup error, not a
# preference (the user's rule, 18 Aug 2026: beverages are always volumes).
# Deliberately small and data-driven; enforced only where the category is
# KNOWN (dojo/enrichment set categories — 'unknown' constrains nothing).
CATEGORY_UNIT_TYPES: dict[str, set[str]] = {
    "beverage": {"volume"},
    # food is NOT constrained: fixed packs (weight/volume), random weight
    # (Kilo) and genuine counts (each pie) are all legitimate.
    "packaging": {"count"},
    "fee": {"count"},
}


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


def is_multipack(text: object) -> bool:
    """True for an 'NxM' compound unit like '5x3kg' / '6x700ml' (digit-x-digit).

    Whitespace-tolerant ('6x 750ml' / '4 x 6 pack' print with spaces but are
    the same pack). parse_unit can't compare these, so multipacks are matched
    by exact name and a to-be-created multipack's ratio/type is resolved by
    the LLM, not parsed.
    Mirror of _is_multipack in config/consolidators/review_and_receive_invoices.py.
    """
    s = "".join(str(text or "").lower().split())
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


def _outer_count(u: object) -> float | None:
    """The leading count of an 'NxM' multipack name ('6x750mL' → 6.0), else None."""
    if not is_multipack(u):
        return None
    s = "".join(str(u or "").lower().split())
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
