"""Unit-of-measure parsing for invoice fixes.

Mirrors the `parse_unit` in config/consolidators/review_and_receive_invoices.py
(the sandboxed consolidator can't import app modules, so the logic is
duplicated there by design). Kept in sync deliberately; both are covered by
tests. Used by the invoice-fixes handler to match a proposed delivered unit
to an existing Loaded unit.
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
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


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
