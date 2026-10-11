"""Draft recipes for the recipe editor — the agent writes, Norm matches.

Built from a production thread (aad94383, 2–4 Oct 2026): a chef and the agent
spent $30 and 64 model-minutes creating 14 recipes, and half of the
``record_recipe`` saves failed — unit names Loaded doesn't use ("g", "cup"),
ingredient names that matched nothing or matched three sub-recipes, a yield
unit of the wrong kind for the item. Each failure was a round trip and another
approval card, and the recipe cards the agent "opened for review" showed no
draft at all.

So the agent no longer resolves ids: it sends the recipe as written — name,
kind, yield, method, lines — and this module matches every line against the
venue's stock items, sub-recipes and units, confidently or not at all. A line
it isn't sure about stays unresolved with its best candidates, and the editor
shows it in amber for the chef to settle (pick a candidate, search, or create
the stock item). The chef's Save is the approval, so creating a recipe needs
no approval card.

``match_recipe`` is pure (catalogue lists in, draft out) so it is tested on
fixtures; ``load_catalogue`` reads the lists through the recipe editor's own
component API.
"""

from __future__ import annotations

import logging
import re
import uuid

logger = logging.getLogger(__name__)

#: Loaded's stock-item ``unitType`` → the unit list's ``stockUnitType``.
UNIT_TYPE_NAMES = {0: "Weight", 1: "Volume", 2: "Count"}

#: How a recipe spells a unit → Loaded's names for it, in order of preference.
#: Matched against the venue's unit list case-insensitively, so "Gram" and
#: "grams" both find the venue's gram unit. Kitchen measures Loaded has no
#: unit for (cups, spoons) are left out on purpose: they stay unresolved and
#: the chef picks the unit.
UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "g": ("gram", "grams", "g"),
    "gm": ("gram", "grams", "g"),
    "gms": ("gram", "grams", "g"),
    "gr": ("gram", "grams", "g"),
    "gram": ("gram", "grams", "g"),
    "grams": ("gram", "grams", "g"),
    "kg": ("kilo", "kilogram", "kilograms", "kilos", "kg"),
    "kgs": ("kilo", "kilogram", "kilograms", "kilos", "kg"),
    "kilo": ("kilo", "kilogram", "kilograms", "kilos", "kg"),
    "kilos": ("kilo", "kilogram", "kilograms", "kilos", "kg"),
    "kilogram": ("kilo", "kilogram", "kilograms", "kilos", "kg"),
    "kilograms": ("kilo", "kilogram", "kilograms", "kilos", "kg"),
    "ml": ("millilitre", "millilitres", "milliliter", "milliliters", "ml", "mls"),
    "mls": ("millilitre", "millilitres", "milliliter", "milliliters", "ml", "mls"),
    "millilitre": (
        "millilitre",
        "millilitres",
        "milliliter",
        "milliliters",
        "ml",
        "mls",
    ),
    "millilitres": (
        "millilitre",
        "millilitres",
        "milliliter",
        "milliliters",
        "ml",
        "mls",
    ),
    "milliliter": (
        "millilitre",
        "millilitres",
        "milliliter",
        "milliliters",
        "ml",
        "mls",
    ),
    "milliliters": (
        "millilitre",
        "millilitres",
        "milliliter",
        "milliliters",
        "ml",
        "mls",
    ),
    "l": ("litre", "litres", "liter", "liters", "l", "lt", "ltr"),
    "lt": ("litre", "litres", "liter", "liters", "l", "lt", "ltr"),
    "ltr": ("litre", "litres", "liter", "liters", "l", "lt", "ltr"),
    "litre": ("litre", "litres", "liter", "liters", "l", "lt", "ltr"),
    "litres": ("litre", "litres", "liter", "liters", "l", "lt", "ltr"),
    "liter": ("litre", "litres", "liter", "liters", "l", "lt", "ltr"),
    "liters": ("litre", "litres", "liter", "liters", "l", "lt", "ltr"),
    "each": ("each", "ea", "unit", "single"),
    "ea": ("each", "ea", "unit", "single"),
    "pc": ("each", "ea", "unit", "single", "piece"),
    "pcs": ("each", "ea", "unit", "single", "piece"),
    "piece": ("each", "ea", "unit", "single", "piece"),
    "pieces": ("each", "ea", "unit", "single", "piece"),
    "unit": ("each", "ea", "unit", "single"),
    "units": ("each", "ea", "unit", "single"),
    "whole": ("each", "ea", "unit", "single"),
    "serve": ("serving", "serve", "serves", "portion", "each"),
    "serves": ("serving", "serve", "serves", "portion", "each"),
    "serving": ("serving", "serve", "serves", "portion", "each"),
    "servings": ("serving", "serve", "serves", "portion", "each"),
    "portion": ("portion", "serving", "serve", "each"),
    "portions": ("portion", "serving", "serve", "each"),
    "bunch": ("bunch", "bunches"),
    "bunches": ("bunch", "bunches"),
    "punnet": ("punnet", "punnets"),
    "pack": ("pack", "packet"),
    "packet": ("packet", "pack"),
    "bottle": ("bottle", "btl"),
    "btl": ("bottle", "btl"),
    "can": ("can", "tin"),
    "tin": ("tin", "can"),
    "slice": ("slice", "slices"),
    "slices": ("slice", "slices"),
    "sheet": ("sheet", "sheets"),
    "sheets": ("sheet", "sheets"),
    "leaf": ("leaf", "leaves"),
    "leaves": ("leaf", "leaves"),
    "clove": ("clove", "cloves"),
    "cloves": ("clove", "cloves"),
}

_WORD = re.compile(r"[a-z0-9]+")
_PREFIX = re.compile(r"^[a-z ]{2,20}\s+-\s+")  # "component - ", "dish - ", "entree - "
_STOP = {"of", "the", "and", "a", "an", "for", "with", "in", "fresh", "to"}
_MAX_CANDIDATES = 3


# -------------------------------------------------------------- text ----


def _norm(text: object) -> str:
    return " ".join(_WORD.findall(str(text or "").lower()))


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("oes"):
        return word[:-2]
    if len(word) > 3 and word.endswith("es") and word[-3] in "sxz":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: object) -> frozenset[str]:
    return frozenset(
        _singular(w) for w in _WORD.findall(str(text or "").lower()) if w not in _STOP
    )


def _strip_prefix(name: object) -> str:
    """'COMPONENT - LAMB RUB' → 'lamb rub', so a line that says 'lamb rub'
    finds the component."""
    return _norm(_PREFIX.sub("", str(name or "").lower().strip()))


# --------------------------------------------------------- catalogue ----


def load_catalogue(venue_id: str, db, config_db) -> dict:
    """The venue's stock items, sub-recipes and units, through the recipe
    editor's component API (the same reads the editor makes)."""
    from app.services.component_api import execute_component_action

    def read(action: str) -> list:
        out = execute_component_action(
            "recipe_editor", action, {}, venue_id, db, config_db
        )
        data = out.get("data") if isinstance(out, dict) else out
        return data if isinstance(data, list) else []

    return {
        "items": read("list_stock_items"),
        "recipes": read("list_recipes"),
        "units": read("list_units"),
    }


def _live_items(items: list) -> list[dict]:
    return [
        i
        for i in items
        if isinstance(i, dict)
        and i.get("id")
        and not i.get("datestampDeleted")
        and not i.get("datestampRemoved")
    ]


def _live_recipes(recipes: list) -> list[dict]:
    return [
        r
        for r in recipes
        if isinstance(r, dict) and r.get("id") and not r.get("deletedAt")
    ]


def _live_units(units: list) -> list[dict]:
    return [
        u
        for u in units
        if isinstance(u, dict) and u.get("id") and not u.get("datestampDeleted")
    ]


# ------------------------------------------------------------- names ----


def _rank(
    query: str, pool: list[dict], names: dict[str, str]
) -> list[tuple[int, dict]]:
    """Candidates for ``query`` from ``pool``, best first, as (score, row).

    Scores: 3 exact name · 2 same words in any order (SAUCE WORCESTERSHIRE =
    worcestershire sauce) · 1 every word of the query is in the name (lemon →
    LEMONS, popcorn → POPCORN KERNELS) · 0 most words shared.
    """
    q_norm = _norm(query)
    q_tok = _tokens(query)
    if not q_tok:
        return []
    scored = []
    for row in pool:
        n = names[row["id"]]
        n_tok = _tokens(n)
        if not n_tok:
            continue
        if n == q_norm:
            score = 3
        elif n_tok == q_tok:
            score = 2
        elif q_tok <= n_tok and len(n_tok) - len(q_tok) <= 2:
            score = 1
        else:
            shared = len(q_tok & n_tok)
            if shared and shared / len(q_tok | n_tok) >= 0.5:
                score = 0
            else:
                continue
        scored.append((score, row))
    scored.sort(key=lambda s: (-s[0], len(names[s[1]["id"]]), names[s[1]["id"]]))
    return scored


def _pick(scored: list[tuple[int, dict]]) -> tuple[dict | None, list[dict]]:
    """(the one confident match, or None; the candidates to show)."""
    if not scored:
        return None, []
    best, row = scored[0]
    same = [r for s, r in scored if s == best]
    if best >= 2 and len(same) == 1:
        return row, []
    if best == 1 and len(same) == 1 and (len(scored) == 1 or scored[1][0] < 1):
        return row, []
    return None, [r for _, r in scored[:_MAX_CANDIDATES]]


# ------------------------------------------------------------- units ----


def _unit_index(units: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for u in _live_units(units):
        idx.setdefault(_norm(u.get("name")), []).append(u)
    return idx


def resolve_unit(
    written: object, units: list[dict], want_type: str | None = None
) -> tuple[dict | None, list[dict]]:
    """The venue unit a recipe's unit text means.

    Returns (unit, candidates). ``want_type`` (Weight/Volume/Count) is the
    matched item's kind: a unit of another kind is not returned — Loaded
    rejects 500 ml of a weight item — but is offered as a candidate so the
    card can say why.
    """
    text = _norm(written)
    if not text:
        return None, []
    idx = _unit_index(units)
    wanted = UNIT_ALIASES.get(text) or (text,)
    found: list[dict] = []
    for alias in wanted:
        for u in idx.get(alias, ()):
            if u not in found:
                found.append(u)
    if not found:
        # "Gram" vs "Grams", "Kilo" vs "Kilogram": prefix of the first alias
        stem = wanted[0]
        for name, rows in idx.items():
            if name.startswith(stem) and len(stem) >= 3:
                found.extend(r for r in rows if r not in found)
    if not found:
        return None, []
    if want_type:
        typed = [u for u in found if u.get("stockUnitType") == want_type]
        if typed:
            return typed[0], []
        return None, found[:_MAX_CANDIDATES]
    return found[0], []


# ------------------------------------------------------------- lines ----


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    frac = re.match(r"^(\d+)\s*/\s*(\d+)$", text)
    if frac and int(frac.group(2)):
        return int(frac.group(1)) / int(frac.group(2))
    m = re.match(r"^(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1))
    return None


def _item_unit_type(item: dict) -> str | None:
    ut = item.get("unitType")
    if isinstance(ut, int):
        return UNIT_TYPE_NAMES.get(ut)
    if isinstance(ut, str) and ut in UNIT_TYPE_NAMES.values():
        return ut
    return None


def _recipe_unit_type(recipe: dict, unit_by_id: dict[str, dict]) -> str | None:
    cv = recipe.get("currentVersion") or {}
    u = unit_by_id.get(str(cv.get("yieldUnitId") or ""))
    return u.get("stockUnitType") if u else None


def match_line(line: dict, cat: dict, batch_names: set[str] | None = None) -> dict:
    """One ingredient line as written → an editor line, matched or flagged.

    ``line``: {name, quantity, unit, kind?, ref_id?}. ``cat``: the output of
    ``load_catalogue``. ``batch_names``: other recipes drafted in the same
    call, so a dish line naming a component that isn't in Loaded yet says so.
    """
    items = _live_items(cat.get("items") or [])
    recipes = _live_recipes(cat.get("recipes") or [])
    units = _live_units(cat.get("units") or [])
    unit_by_id = {u["id"]: u for u in units}
    item_names = {i["id"]: _norm(i.get("name")) for i in items}
    recipe_names = {r["id"]: _strip_prefix(r.get("name") or "") for r in recipes}

    written_name = str(line.get("name") or "").strip()
    written_qty = line.get("quantity")
    written_unit = str(line.get("unit") or "").strip()
    kind_hint = line.get("kind") if line.get("kind") in ("item", "recipe") else None
    out = {
        "id": str(uuid.uuid4()),
        "kind": kind_hint or "item",
        "ref_id": None,
        "name": written_name,
        "quantity": 0.0,
        "unit_id": None,
        "unit_name": None,
        "unit_ratio": 1.0,
        "stock_unit_name": None,
        "stock_unit_ratio": 1.0,
        "item_deleted": False,
        "match": {
            "status": "unresolved",
            "reasons": [],
            "candidates": [],
            "as_written": {
                "name": written_name,
                "quantity": written_qty,
                "unit": written_unit,
            },
        },
    }
    reasons: list[str] = out["match"]["reasons"]

    # --- what it is
    chosen: dict | None = None
    kind: str | None = None
    ref = str(line.get("ref_id") or "").strip()
    if ref:
        for r in recipes:
            if r["id"] == ref:
                chosen, kind = r, "recipe"
        for i in items:
            if i["id"] == ref:
                chosen, kind = i, "item"
        if chosen is None:
            reasons.append("ref_id matches nothing at this venue")
    if chosen is None and written_name:
        rec_scored = (
            _rank(written_name, recipes, recipe_names) if kind_hint != "item" else []
        )
        item_scored = (
            _rank(written_name, items, item_names) if kind_hint != "recipe" else []
        )
        rec_pick, rec_cands = _pick(rec_scored)
        item_pick, item_cands = _pick(item_scored)
        rec_best = rec_scored[0][0] if rec_scored else -1
        item_best = item_scored[0][0] if item_scored else -1
        # The better-scoring pool decides; a sub-recipe wins a tie (a kitchen's
        # "polenta" is the component it makes, not the raw grain). A pool that
        # scores best but holds two equal names is ambiguous — the weaker pool
        # never wins by default (two POLENTA components must not fall through
        # to POLENTA FINE GROUND). A sub-recipe is picked only on its exact
        # name or the same words: extra words in a recipe's name change the
        # dish ("olive oil" is not OLIVE OIL PANNACOTTA), whereas a stock
        # item's extra words qualify the same thing (POPCORN KERNELS).
        if rec_pick is not None and rec_best >= 2 and rec_best >= item_best:
            chosen, kind = rec_pick, "recipe"
        elif item_pick is not None and (item_best > rec_best or rec_best < 2):
            chosen, kind = item_pick, "item"
        else:
            # Candidates from both pools, best first, a sub-recipe ahead of an
            # item on equal score — but never one kind crowding out the other.
            by_kind = {
                "recipe": [(s, "recipe", r) for s, r in rec_scored],
                "item": [(s, "item", i) for s, i in item_scored],
            }
            picked: list = []
            for k in ("recipe", "item"):
                picked += by_kind[k][:2]
            rest = [t for k in ("recipe", "item") for t in by_kind[k][2:]]
            picked.sort(key=lambda t: (-t[0], t[1] != "recipe"))
            merged = picked + sorted(rest, key=lambda t: (-t[0], t[1] != "recipe"))
            out["match"]["candidates"] = [
                {"id": row["id"], "name": row.get("name"), "kind": k}
                for _, k, row in merged[: _MAX_CANDIDATES + 1]
            ]
            cands = out["match"]["candidates"]
            if rec_best >= 2 and len(rec_cands) > 1:
                reasons.append(
                    f"{len(rec_cands)} sub-recipes could be '{written_name}'"
                )
            elif cands:
                reasons.append("not sure which of these it is")
            else:
                reasons.append("no stock item or sub-recipe matches")
                if batch_names and _strip_prefix(written_name) in batch_names:
                    reasons[-1] = (
                        "a recipe in this batch, not in Loaded yet — save that one first"
                    )
    elif chosen is None:
        reasons.append("no ingredient name")

    want_type: str | None = None
    if chosen is not None:
        out["kind"] = kind
        out["ref_id"] = chosen["id"]
        out["name"] = str(chosen.get("name") or written_name).strip()
        if kind == "item":
            want_type = _item_unit_type(chosen)
            cu = unit_by_id.get(str(chosen.get("countingUnitId") or ""))
            if cu:
                out["stock_unit_name"] = cu.get("name")
                out["stock_unit_ratio"] = float(cu.get("ratio") or 1)
        else:
            want_type = _recipe_unit_type(chosen, unit_by_id)

    # --- how much
    qty = _number(written_qty)
    if qty is None:
        reasons.append(
            "needs a number for the quantity"
            + (f" ('{written_qty}')" if str(written_qty or "").strip() else "")
        )
    else:
        out["quantity"] = qty

    # --- in what
    unit, unit_cands = (
        resolve_unit(written_unit, units, want_type) if written_unit else (None, [])
    )
    if unit is None and chosen is not None and kind == "item" and not written_unit:
        # No unit written: a stock item's own counting unit (what the editor
        # picks too).
        unit = unit_by_id.get(str(chosen.get("countingUnitId") or ""))
    if unit is not None:
        out["unit_id"] = unit["id"]
        out["unit_name"] = unit.get("name")
        out["unit_ratio"] = float(unit.get("ratio") or 1)
    elif unit_cands and want_type:
        names = ", ".join(str(u.get("name")) for u in unit_cands)
        reasons.append(
            f"'{written_unit}' ({names}) is not a {want_type.lower()} unit, "
            f"and this is a {want_type.lower()} item"
        )
    elif written_unit:
        reasons.append(f"no Loaded unit called '{written_unit}'")
    else:
        reasons.append("needs a unit")

    if chosen is not None and qty is not None and unit is not None:
        out["match"] = {**out["match"], "status": "matched", "reasons": []}
    return out


# ----------------------------------------------------------- recipes ----


def _find_duplicate(name: str, recipes: list[dict]) -> dict | None:
    """An existing recipe with this name (prefix-insensitive), or None."""
    target = _strip_prefix(name)
    if not target:
        return None
    for r in _live_recipes(recipes):
        if _strip_prefix(r.get("name") or "") == target or _norm(
            r.get("name")
        ) == _norm(name):
            return {"id": r["id"], "name": r.get("name")}
    return None


def match_recipe(
    recipe: dict, cat: dict, venue_id: str, batch_names: set[str] | None = None
) -> dict:
    """One recipe as the agent wrote it → the editor's draft (a working
    document's ``data``), every line matched or flagged.

    ``recipe``: {name, kind: component|dish, yield_quantity, yield_unit,
    method|notes, lines: [{name, quantity, unit, kind?, ref_id?}]}.
    """
    units = _live_units(cat.get("units") or [])
    name = str(recipe.get("name") or "").strip()
    rtype = (
        str(recipe.get("kind") or recipe.get("recipe_type") or "component")
        .strip()
        .lower()
    )
    if rtype not in ("component", "dish"):
        rtype = "component"
    yqty = _number(recipe.get("yield_quantity"))
    yunit, _ = resolve_unit(recipe.get("yield_unit"), units)
    problems: list[str] = []
    if not name:
        problems.append("no name")
    if yqty is None:
        problems.append("needs a yield quantity")
    if yunit is None:
        problems.append(
            f"no Loaded unit called '{recipe.get('yield_unit')}' for the yield"
            if recipe.get("yield_unit")
            else "needs a yield unit"
        )
    raw_lines = recipe.get("lines") or recipe.get("ingredients") or []
    if isinstance(raw_lines, dict):
        raw_lines = [raw_lines]
    lines = [
        match_line(ln, cat, batch_names) for ln in raw_lines if isinstance(ln, dict)
    ]
    if not lines:
        problems.append("no ingredient lines")
    unresolved = sum(1 for ln in lines if ln["match"]["status"] != "matched")
    return {
        "draft_id": str(uuid.uuid4()),
        "venue_id": venue_id,
        "recipe_id": "",
        "version_id": "",
        "name": name,
        "recipe_type": rtype,
        "is_counted_in_stocktake": rtype == "component",
        "notes": str(recipe.get("method") or recipe.get("notes") or ""),
        "yield_quantity": yqty if yqty is not None else 0.0,
        "yield_unit_id": yunit["id"] if yunit else None,
        "yield_unit_name": yunit.get("name") if yunit else None,
        "lines": lines,
        "versions": [],
        "duplicate_of": _find_duplicate(name, cat.get("recipes") or []),
        "problems": problems,
        "unresolved_count": unresolved,
        "source": "draft_recipe",
    }


def draft_recipes(recipes: list[dict], cat: dict, venue_id: str) -> list[dict]:
    """Every recipe in one call, each knowing the others' names."""
    batch = {_strip_prefix(r.get("name")) for r in recipes if isinstance(r, dict)} - {
        ""
    }
    return [
        match_recipe(r, cat, venue_id, batch) for r in recipes if isinstance(r, dict)
    ]


def summarise(drafts: list[dict]) -> list[dict]:
    """What the model is told about each card — enough to say what needs the
    chef, not the whole draft."""
    out = []
    for d in drafts:
        flagged = [
            {
                "line": ln["match"]["as_written"].get("name"),
                "why": "; ".join(ln["match"]["reasons"]),
                "candidates": [c["name"] for c in ln["match"]["candidates"]],
            }
            for ln in d["lines"]
            if ln["match"]["status"] != "matched"
        ]
        out.append(
            {
                "name": d["name"],
                "type": d["recipe_type"],
                "lines": len(d["lines"]),
                "matched": len(d["lines"]) - d["unresolved_count"],
                "needs_the_chef": flagged,
                "problems": d["problems"],
                "already_exists": d["duplicate_of"],
            }
        )
    return out
