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
