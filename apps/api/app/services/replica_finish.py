"""Finish the Replica: every rule Norm applies to an invoice, in one place.

The Replica is what we think the invoice says. ``invoice_replica.build_replica``
reads the supplier's invoice and resolves it against the catalogue; the rules
here need more than the invoice — Loaded's draft, the order, the venue's units
and suppliers, the arithmetic — and they write their answers INTO the replica,
each with a line in ``resolution_log``. The card then shows exactly where the
Replica and Loaded differ (``invoice_review.build_suggestions``) and nothing
else.

Until 12 Oct 2026 these rules ran AFTER that comparison, adding and replacing
suggestions of their own, so the card could contradict the Replica tab:
IN11656852's replica said SHALLOTS while the card proposed BANANA SHALLOTS
from the order.

A value a rule only GUESSED is marked (``unit_guess``, ``supplier_guess``,
``po_guess``, ``unit_cost_guess``) with its reason beside it (``*_why``).
Norm never applies a guess unattended unless a venue switch says it may.

Every rule is best-effort: one that fails logs and leaves the replica as the
invoice read it, so the review still runs.
"""

from __future__ import annotations

import logging
import re

from app.services import invoice_line_match as line_match
from app.services.invoice_po_reference import (
    candidates_for_code,
    order_rows_for,
    resolve_loaded_line,
)

logger = logging.getLogger(__name__)

#: A blocker's id, as the replica raises it.
TOTALS_ISSUE = "totals_inconsistent"
UNIT_ISSUES = ("unit_missing", "unit_unconfirmed")

#: The arbiter's field names → the replica's.
_ARBITER_LINE_FIELD = {
    "quantity": "quantity_received",
    "unit_price": "unit_cost",
    "line_total": "total_cost",
}
_ARBITER_HEADER_FIELD = {
    "subtotal": "subtotal",
    "tax": "tax_amount",
    "discount": "discount_amount",
    "total": "total",
}

#: A cent of drift across a dozen lines is rounding, not a disagreement.
_TOTALS_TOL = 0.02

#: Loaded receives an invoice whose total is within ten cents of its own
#: arithmetic, showing the difference as "Rounding" (Mercury's
#: calculateFooterTotals, LOADED-5809); further out it refuses the receive
#: (invoice-totals-mismatch, Bidfood 90ea78ed at 19c). So "the totals match"
#: means this.
LOADED_ROUNDING = 0.10


def _agree(a: float, b: float, tol: float = _TOTALS_TOL) -> bool:
    """Within ``tol`` — with room for float error, since 56.74 − 56.72 is
    0.020000000000004 and two cents would otherwise read as more than two."""
    return abs(a - b) <= tol + 1e-9


_PO_LIST = "/1.0/stock/internal/purchase-orders?from=1901-01-01&to=9999-12-31"


def _f(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _lines(doc: dict) -> list[dict]:
    return [ln for ln in doc.get("lines") or [] if isinstance(ln, dict)]


def mark_fix(target: dict, field: str, issue_id: str) -> None:
    """Record that ``field`` was set to clear blocker ``issue_id`` — the
    comparison tags the resulting suggestion so the card and autopilot know
    which blocker it answers."""
    target.setdefault("fixes", {})[field] = issue_id


# ---------------------------------------------------------------------------
# Rule 1 — the stock item, when a supplier code covers several
# ---------------------------------------------------------------------------

# Letters and digits apart: the invoice's "NO10" carries the "10" of
# TANQUERAY 10, and "6X197MM" the "197" of "6 X 197MM".
_WORD = re.compile(r"[a-z]+|[0-9]+")

#: Replica matches made from the invoice's own words — deterministic, so they
#: ARE the wording's answer (``invoice_line_match``).
_WORDING_MATCHES = ("description_exact", "description_substring")


def _words(text: object) -> set[str]:
    """Lower-case words with a trailing plural 's' dropped, so the invoice's
    'Beef Bones' and the catalogue's 'BONE MARROW' share 'bone'."""
    out = set()
    for w in _WORD.findall(str(text or "").lower()):
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and w.isalpha() else w)
    return out


def wording_names(description: object, name: object) -> int:
    """How fully the invoice's wording names this: the number of words in
    ``name`` when every one of them is on the line, else 0."""
    words = _words(name)
    return len(words) if words and words <= _words(description) else 0


def settle_items(
    data: dict,
    replica: dict,
    pairs: dict[str, str],
    catalogue: list[dict] | None,
    log: list,
) -> None:
    """Which stock item a line is, when the supplier's code covers several.

    Angus Meats sells canoe-cut marrow, 1-inch marrow and plain bones under one
    code (BONES); Service Foods sells SHALLOTS and BANANA SHALLOTS under VEGF50.
    The code alone can't say which was delivered, so (user, 12 Oct 2026):

    1. the invoice's wording, when it names exactly one of them — the
       replica's own match on the line's description, or the one item whose
       name (or its supplier's description of it) is all there on the line
       ("SHALLOTS (PER KG)" names SHALLOTS and not BANANA SHALLOTS);
    2. else the order this delivery was raised against — the row for one of
       them, nearest in quantity, each row claimed once ("Beef Bones" on two
       lines against an order for 6 kg canoe cut and 14 kg 1-inch);
    3. else what Loaded has: the item its line is linked to, or for an
       unlinked line its screen's code match (the first in catalogue order).

    The order used to overrule the invoice here, and booked shallots that
    arrived as shallots to BANANA SHALLOTS (IN11656852). Step 3 must be the
    LINE's item, not the catalogue's first: Freeman & Grey's VEGF16 is on
    both CORIANDER FRESH and EGGPLANT, and Loaded had the coriander.
    """
    if not catalogue:
        return
    supplier_id = replica.get("linked_supplier_id") or data.get("linked_supplier_id")
    if not supplier_id:
        return
    rows = _order_rows(data, replica)
    working = {str(ln.get("id")): ln for ln in _lines(data)}
    claimed: set[int] = set()
    matched: set[str] = set()
    for k, rl in enumerate(_lines(replica)):
        candidates = candidates_for_code(catalogue, supplier_id, rl.get("code"))
        if len(candidates) < 2:
            continue
        ids = {str(c.get("id")): c for c in candidates}
        desc = rl.get("description")
        own = ids.get(str(rl.get("linked_item_id") or ""))
        if own is not None and rl.get("matched_by") in _WORDING_MATCHES:
            named = [own]
        else:
            # The item the line names most fully: "SALAMI PEPPERONI SLICED"
            # names SALAMI and PEPPERONI SLICED, and the venue received it as
            # the latter (Freeman & Grey 110170296). A tie is no answer.
            score = {
                str(c.get("id")): max(
                    wording_names(desc, c.get("name")),
                    wording_names(
                        desc,
                        (
                            line_match.supplier_variant(c, supplier_id, rl.get("code"))
                            or {}
                        ).get("description"),
                    ),
                )
                for c in candidates
            }
            best = max(score.values(), default=0)
            named = [c for c in candidates if best and score[str(c.get("id"))] == best]
        pick, by, why = None, None, None
        if len(named) == 1:
            pick, by = named[0], "invoice wording"
            why = f"the invoice names '{pick.get('name')}'"
        else:
            options = [
                pl
                for pl in rows
                if id(pl) not in claimed and str(pl.get("itemId")) in ids
            ]
            if options:
                qty = _f(rl.get("quantity_received"))
                if qty is not None:
                    options.sort(
                        key=lambda pl: abs((_f(pl.get("quantityOrdered")) or 0.0) - qty)
                    )
                row = options[0]
                claimed.add(id(row))
                pick, by = ids[str(row.get("itemId"))], "order"
                why = (
                    f"the order for this delivery is '{row.get('itemName')}' "
                    f"({row.get('quantityOrdered')} {row.get('unitName') or ''}".rstrip()
                    + ")"
                )
            else:
                ln = working.get(str(pairs.get(str(rl.get("id"))) or ""))
                pick = ids.get(str((ln or {}).get("linked_item_id") or ""))
                if pick is None:
                    pick, _ = resolve_loaded_line(
                        catalogue, supplier_id, rl.get("code")
                    )
                by = "Loaded"
                why = f"Loaded has '{(pick or {}).get('name')}'"
        if pick is None:
            continue
        rl["item_why"] = (
            f"code '{rl.get('code')}' covers {len(candidates)} stock items — {why}"
        )
        log.append(f"line {k + 1} '{rl.get('description')}': {rl['item_why']}")
        if str(pick.get("id")) == str(rl.get("linked_item_id") or ""):
            continue
        rl["linked_item_id"] = pick.get("id")
        rl["item_name"] = pick.get("name")
        rl["matched_by"] = by
        variant = line_match.supplier_variant(pick, supplier_id, rl.get("code"))
        rl["linked_brand_id"] = (variant or {}).get("brandId") or pick.get(
            "defaultBrandId"
        )
        matched.add(str(rl.get("id")))
    if matched:
        # A line this settled is no longer unmatched.
        replica["issues"] = [
            i
            for i in replica.get("issues") or []
            if not (
                i.get("code") == "item_unmatched" and str(i.get("line_id")) in matched
            )
        ]


def _order_rows(data: dict, replica: dict) -> list[dict]:
    """The rows of the order this delivery belongs to — the one Loaded links,
    else the one the Replica resolved from the invoice (Glass Goose 110185577:
    Loaded held only the number, and the straws' order went unread)."""
    rows = order_rows_for(data)
    if rows:
        return rows
    ref = data.get("po_reference")
    po = replica.get("linked_purchase_order_id")
    if po and isinstance(ref, dict) and ref.get("po_id") == po:
        return [pl for pl in ref.get("lines") or [] if isinstance(pl, dict)]
    return []


# ---------------------------------------------------------------------------
# Rule 2 — the delivered unit
# ---------------------------------------------------------------------------


def _set_unit(rl: dict, unit: dict, *, by: str, why: str, guess: bool) -> None:
    if guess and "unit_read" not in rl:
        # What the invoice itself gave, kept for a line still to be added:
        # adding a line is applied unattended, a guessed unit is not.
        rl["unit_read"] = {
            "unit": rl.get("unit"),
            "linked_unit_id": rl.get("linked_unit_id"),
            "unit_ratio": rl.get("unit_ratio"),
        }
    rl["linked_unit_id"] = unit.get("id")
    rl["unit"] = unit.get("name")
    rl["unit_ratio"] = unit.get("ratio")
    rl["unit_by"] = by
    rl["unit_why"] = why
    rl["unit_guess"] = guess


def settle_units(
    data: dict,
    replica: dict,
    pairs: dict[str, str],
    units: list[dict] | None,
    choose,
    log: list,
) -> None:
    """The delivered unit of every line the invoice left open.

    The replica reads the unit off the invoice (variant → the page → Norm's
    catalogue → the printed column). This finishes the rest:

    - The batched resolver's answer replaces a bare charge word ("EA") or fills
      a line Loaded has no unit for — high or medium confidence; a line still
      to be added takes only a high one.
    - A line with no unit at all (``unit_missing``) takes the resolver's answer
      at any confidence, else the unit Loaded's line holds, else the venue's
      chooser (``choose``, unit_guess). That is a guess: a person accepts it,
      or the receive-without-a-unit switch lets Norm (the user, 12 Oct 2026:
      "with this setting on we want norm to pick the unit").
    - A unit the invoice couldn't confirm (``unit_unconfirmed``) keeps the
      replica's stand-in, marked a guess for the same reason.
    """
    working = {str(ln.get("id")): ln for ln in _lines(data) if ln.get("id")}
    by_id = {
        str(u.get("id")): u for u in units or [] if isinstance(u, dict) and u.get("id")
    }
    blocked = {
        str(i.get("line_id")): i.get("code")
        for i in replica.get("issues") or []
        if i.get("code") in UNIT_ISSUES
    }

    def _unit(uid: object, name: object = None, ratio: object = None) -> dict:
        known = by_id.get(str(uid)) or {}
        return {
            "id": uid,
            "name": name or known.get("name"),
            "ratio": ratio if ratio is not None else known.get("ratio"),
        }

    for k, rl in enumerate(_lines(replica)):
        rid = str(rl.get("id"))
        wid = pairs.get(rid)
        ln = working.get(str(wid)) if wid else None
        to_add = ln is None
        desc = rl.get("description") or rl.get("code") or "line"
        ur = (
            rl.get("unit_resolved")
            if isinstance(rl.get("unit_resolved"), dict)
            else None
        )
        code = blocked.get(rid)

        if (
            ur
            and ur.get("unit_id")
            and ur.get("unit_id") != rl.get("linked_unit_id")
            and ur.get("confidence") in (("high",) if to_add else ("high", "medium"))
            and not (ln or {}).get("linked_unit_id")
        ):
            why = (
                f"no size is printed for '{desc}' — {ur.get('why')}"
                if not rl.get("linked_unit_id")
                else (
                    f"'{rl.get('unit')}' is how '{desc}' is charged, not the pack "
                    f"size — {ur.get('why')}"
                )
            )
            _set_unit(
                rl,
                _unit(ur["unit_id"], ur.get("unit_name"), ur.get("unit_ratio")),
                by="resolver",
                why=why,
                # Decisive enough to apply on its own only when nothing stops
                # the receive on this line; a unit blocker keeps it a guess.
                guess=code == "unit_missing",
            )
            log.append(f"line {k + 1} unit: '{rl.get('unit')}' ({why})")
            continue

        if code == "unit_unconfirmed" and rl.get("linked_unit_id"):
            rl["unit_guess"] = True
            rl.setdefault(
                "unit_why",
                f"the invoice's unit for '{desc}' can't be read — "
                f"'{rl.get('unit')}' is the best answer",
            )
            continue
        if code not in UNIT_ISSUES or rl.get("linked_unit_id"):
            continue
        if rl.get("unit_create_name"):
            continue  # the answer is a unit Loaded doesn't have yet

        pick, by, why = None, None, None
        if ur and ur.get("unit_id"):
            pick, by = (
                _unit(ur["unit_id"], ur.get("unit_name"), ur.get("unit_ratio")),
                ("resolver"),
            )
            why = (
                f"no unit could be read from the invoice for '{desc}' — "
                f"{ur.get('why') or 'the unit resolver'}"
            )
        elif ln is not None and ln.get("linked_unit_id"):
            pick, by = (
                _unit(ln["linked_unit_id"], ln.get("unit"), ln.get("unit_ratio")),
                ("Loaded"),
            )
            why = (
                f"no unit could be read from the invoice for '{desc}' — "
                f"Loaded has '{ln.get('unit')}'"
            )
        elif choose is not None:
            try:
                chosen = choose(ln if ln is not None else rl)
            except Exception as exc:  # noqa: BLE001 — a proposal, never an error
                logger.info("unit chooser failed on %s: %s", rid, exc)
                chosen = None
            if isinstance(chosen, dict) and chosen.get("id"):
                pick, by = (
                    _unit(chosen["id"], chosen.get("name"), chosen.get("ratio")),
                    ("chooser"),
                )
                why = f"no unit could be read from the invoice for '{desc}' — " + (
                    chosen.get("why")
                    or f"'{chosen.get('name')}' is the closest of the venue's units"
                )
        if pick is None:
            log.append(f"line {k + 1} unit: nothing to go on — a person must choose")
            continue
        _set_unit(rl, pick, by=by, why=why, guess=True)
        log.append(f"line {k + 1} unit: '{pick.get('name')}' ({by}, a guess)")


# ---------------------------------------------------------------------------
# Rule 3 — the supplier
# ---------------------------------------------------------------------------


def settle_supplier(
    data: dict,
    replica: dict,
    suppliers: list[dict] | None,
    log: list,
    read_details=None,
) -> None:
    """The supplier, when the printed name matches no Loaded record and
    Loaded's draft has none either: the closest record (a guess), else a new
    supplier under the printed name. Autopilot may create one only when
    nothing is close (see invoice_review.autopilot_accept) — a duplicate
    supplier sends every later invoice from that business to the wrong
    extraction layout.

    A new supplier also carries the address and contacts its invoice prints
    (``read_details(name)`` → invoice_extraction.read_supplier_details), so
    the record Norm creates is one a venue can order from, not a bare name."""
    if data.get("linked_supplier_id") or replica.get("linked_supplier_id"):
        return
    issue = next(
        (
            i
            for i in replica.get("issues") or []
            if i.get("code") == "supplier_unresolved"
        ),
        None,
    )
    name = ((issue or {}).get("data") or {}).get("supplier_name")
    if not name:
        return
    from app.services import supplier_identity

    live = [
        s
        for s in suppliers or []
        if isinstance(s, dict)
        and s.get("id")
        and not s.get("removedAt")
        and not s.get("datestampDeleted")
    ]
    close = supplier_identity.alias_candidates([name], live) if live else []
    best = close[0] if close else None
    if isinstance(best, dict) and best.get("id"):
        replica["linked_supplier_id"] = best["id"]
        replica["supplier_name"] = best.get("name")
        replica["supplier_guess"] = True
        replica["supplier_why"] = (
            f"no Loaded supplier is called '{name}' — the closest is '{best.get('name')}'"
        )
    else:
        replica["supplier_create_name"] = name
        replica["supplier_why"] = f"no Loaded supplier is called '{name}' — create it"
        details = read_details(name) if read_details else None
        if details:
            replica["supplier_details"] = details
    log.append(f"supplier: {replica['supplier_why']}")


# ---------------------------------------------------------------------------
# Rule 4 — the order
# ---------------------------------------------------------------------------


def settle_order(data: dict, replica: dict, lh, log: list) -> None:
    """The order, when the invoice prints none that resolves and Loaded's
    draft links none: the supplier's open order carrying the most of this
    delivery's stock items. Loaded's list holds no lines, so the five most
    recent are read in full. A guess — never linked unattended. Nothing shared
    means it isn't the order, and the invoice is received without one."""
    if data.get("linked_purchase_order_id") or replica.get("linked_purchase_order_id"):
        return
    if data.get("is_credit_note") or replica.get("is_credit_note"):
        return
    codes = {i.get("code") for i in replica.get("issues") or []}
    if codes & {"po_split_order", "po_doubled_up"}:
        return  # the order's story is already told
    supplier_id = data.get("linked_supplier_id") or replica.get("linked_supplier_id")
    if lh is None or not supplier_id:
        return
    try:
        pos = lh.get(_PO_LIST)
        pos = pos if isinstance(pos, list) else (pos or {}).get("data") or []
    except Exception as exc:  # noqa: BLE001 — a proposal, never an error
        logger.info("order list unavailable: %s", exc)
        return
    me = data.get("invoice_id")
    candidates = [
        p
        for p in pos
        if isinstance(p, dict)
        and p.get("id")
        and p.get("supplierId") == supplier_id
        and not p.get("datestampDeleted")
        and not p.get("isReceived")
        and not p.get("invoicedAt")
        and p.get("linkedInvoiceId") in (None, "", me)
        and str(p.get("status") or "").lower() not in ("failed", "cancelled")
    ]
    if not candidates:
        return
    items = {
        ln.get("linked_item_id")
        for ln in _lines(data)
        if ln.get("linked_item_id") and not ln.get("struck")
    }
    candidates.sort(key=lambda p: str(p.get("createdAt") or ""), reverse=True)
    best, best_hits = None, 0
    for p in candidates[:5]:
        try:
            po = lh.get(f"/1.0/stock/internal/purchase-orders/{p['id']}")
        except Exception as exc:  # noqa: BLE001
            logger.info("order %s unreadable: %s", p.get("orderNumber"), exc)
            continue
        on_order = {
            row.get("itemId")
            for row in (po or {}).get("lines") or []
            if isinstance(row, dict) and row.get("itemId")
        }
        hits = len(items & on_order)
        if hits > best_hits:
            best, best_hits = p, hits
    if best is None:
        return
    number = best.get("orderNumber")
    when = str(best.get("createdAt") or "")[:10]
    replica["linked_purchase_order_id"] = best["id"]
    replica["purchase_order_number"] = number
    replica["po_guess"] = True
    replica["po_why"] = (
        f"no order is linked — order {number}"
        + (f" from {when}" if when else "")
        + f" has {best_hits} of this delivery's {len(items)} items on it"
    )
    log.append(f"order: {replica['po_why']}")


# ---------------------------------------------------------------------------
# Rule 6 — totals that don't add up
# ---------------------------------------------------------------------------


def _line_total(ln: dict) -> float:
    q, c = _f(ln.get("quantity_received")), _f(ln.get("unit_cost"))
    if q is not None and c is not None:
        return round(q * c, 4)
    return _f(ln.get("total_cost")) or 0.0


def _totals_view(data: dict) -> tuple[float, float, float, float | None]:
    """``(line_sum, tax, derived_total, declared_total)`` — the arithmetic the
    card and Loaded both do (Loaded's ``calculateFooterTotals``): each line as
    quantity × unit cost, its tax at its own rate, and the total as lines +
    tax − the invoice's discount.

    Tax is worked from the rate, never read off the line: Loaded stored that
    figure for Loaded's cost, so a rule that moved the cost and kept it
    balanced to the wrong total (Fresh Connection 30049130, 14 Oct 2026:
    42.99 → 29.43 against tax still 15% of 42.99). A line with no rate keeps
    its stored tax; with no tax on any line, the header's stands."""
    incl = bool(data.get("unit_cost_includes_tax"))
    live = [ln for ln in _lines(data) if not ln.get("struck")]
    line_sum = tax = 0.0
    taxed = False
    for ln in live:
        lt = _line_total(ln)
        line_sum += lt
        rate = _f(ln.get("sale_tax_rate"))
        if rate is not None:
            tax += lt - lt / (1 + rate) if incl else lt * rate
            taxed = True
        else:
            t = _f(ln.get("tax_amount")) or 0.0
            tax += t
            taxed = taxed or bool(t)
    if not taxed:
        tax = _f(data.get("tax_amount")) or 0.0
    excl = line_sum - tax if incl else line_sum
    discount = _f(data.get("discount_amount")) or 0.0
    derived = round(round(excl, 2) + round(tax, 2) - discount, 2)
    return line_sum, tax, derived, _f(data.get("total"))


def _exact_total(data: dict) -> float:
    """The invoice total before Loaded rounds the subtotal and tax to cents —
    what a spread solves against, so a rounded-away fraction of a cent of tax
    doesn't move the costs."""
    line_sum, tax, _, _ = _totals_view(data)
    incl = bool(data.get("unit_cost_includes_tax"))
    return line_sum + (0.0 if incl else tax) - (_f(data.get("discount_amount")) or 0.0)


def _gross(ln: dict, incl: bool) -> float:
    """What a dollar of this line's cost adds to the invoice total: the dollar
    and its tax. Just the dollar when costs include tax, or when the line has
    no rate (its stored tax doesn't move with the cost)."""
    rate = _f(ln.get("sale_tax_rate"))
    return 1.0 if incl or rate is None else 1.0 + rate


def spread_costs(
    data: dict, fixed: set[str] | frozenset = frozenset()
) -> dict[str, float]:
    """The last answer to totals that don't add up: each line's cost moved by
    its share of the difference, so the invoice comes to its total — the
    remainder cent on the biggest line. A line's tax moves with its cost, so
    each moves by its share ÷ (1 + its rate). ``{line_id: new unit cost}`` for
    the lines that move; empty when the lines already add up. ``fixed``: ids
    of lines that count toward the total but must not move."""
    _, _, derived, declared = _totals_view(data)
    if declared is None or _agree(derived, declared):
        return {}
    incl = bool(data.get("unit_cost_includes_tax"))
    live = []
    for ln in _lines(data):
        if ln.get("struck") or str(ln.get("id")) in fixed:
            continue
        q, c = _f(ln.get("quantity_received")), _f(ln.get("unit_cost"))
        if q and c is not None:
            live.append((ln, q, c, _gross(ln, incl)))
    weight = sum(abs(q * c * g) for _, q, c, g in live)
    if not live or not weight:
        return {}
    # Measured over EVERY line (one without a quantity or cost still counts
    # toward the total), spread over those that can move.
    gap = declared - _exact_total(data)
    new_cost = {
        str(ln["id"]): round(c + gap * abs(q * c * g) / weight / (q * g), 4)
        for ln, q, c, g in live
    }
    trial = {
        **data,
        "lines": [
            {**ln, "unit_cost": new_cost.get(str(ln.get("id")), ln.get("unit_cost"))}
            for ln in _lines(data)
        ],
    }
    _, _, now, _ = _totals_view(trial)
    big, bq, _, bg = max(live, key=lambda t: abs(t[1] * t[2] * t[3]))
    new_cost[str(big["id"])] = round(
        new_cost[str(big["id"])] + (declared - now) / (bq * bg), 4
    )
    return {
        lid: cost
        for (ln, _q, c, _g), (lid, cost) in zip(live, new_cost.items())
        if abs(cost - c) >= 0.00005
    }


def read_printed_prices(view: dict, replica: dict) -> dict | None:
    """Ways an invoice's figures are all right but were read wrong, worked out
    from the figures themselves instead of spread as a guess:

    - a line whose printed total isn't quantity × price: the line total is
      what was charged. Bidfood prints its list price and charges about 5%
      less (La Zeppa 110185654: 1 × 66.25, line 62.94) — the cost is line
      total ÷ quantity. When the line total is a whole multiple of the price
      (Bidfood 110167263: "2 × 41.11", line 164.44) the quantity was likely
      misread instead — 4 at 41.11, or 2 at 82.22; the figures can't say
      which, so that one is a guess;
    - prices printed INCLUDING GST, filed as ex-tax: each cost ÷ (1 + rate);
    - a discount printed off the bottom: the receive can't send Loaded a
      discount, so it comes off every line's cost by the same share — what
      the venue actually paid per unit. Fresh Connection 30049130 (14 Oct
      2026) was this and the one before: 5 × $42.99 incl GST, less $30.91 ex
      GST = $31.20 a carton ex GST, $23.40 GST, $179.40.

    A reading counts only when Loaded's arithmetic on it reaches the printed
    total AND the printed GST or subtotal agrees too — two figures, not one
    (Lion 94817415 prints a subtotal without its fuel surcharge, so demanding
    every figure turned away a reading its total and GST both confirm).

    Lines the invoice doesn't bill (struck in the view) and $0 leftovers are
    ignored. Returns ``{"lines": {view line id: {"unit_cost", maybe
    "quantity_received"}}, "whys": {id: str}, "discount": bool, "guess":
    {ids}}``, or None when the lines already reach the total or nothing fits.
    """
    if replica.get("is_credit_note") or view.get("unit_cost_includes_tax"):
        return None
    total = _f(replica.get("total"))
    subtotal, printed_tax = _f(replica.get("subtotal")), _f(replica.get("tax_amount"))
    if not total or total < 0 or (subtotal is None and printed_tax is None):
        return None
    rows = []
    for ln in _lines(view):
        if ln.get("struck"):
            continue
        q, c, r = (
            _f(ln.get("quantity_received")),
            _f(ln.get("unit_cost")),
            _f(ln.get("sale_tax_rate")),
        )
        if not q:
            continue  # a $0 leftover adds nothing
        if c is None or r is None:
            return None  # a line we can't reason about — leave it to the guess
        rows.append((str(ln.get("id")), q, c, r, _f(ln.get("printed_total"))))
    if not rows:
        return None
    loaded_discount = _f(view.get("discount_amount")) or 0.0
    # Loaded already carrying a discount keeps it (the receive leaves it).
    discount = 0.0 if loaded_discount else abs(_f(replica.get("discount_amount")) or 0)
    tol = _TOTALS_TOL + 0.01 * len(rows)
    rates = {i: r for i, _q, _c, r, _p in rows}

    def figures(qty: dict, cost: dict) -> tuple[float, float, float]:
        ex = sum(qty[i] * cost[i] for i in qty)
        tx = sum(qty[i] * cost[i] * rates[i] for i in qty)
        return ex, tx, round(round(ex, 2) + round(tx, 2) - loaded_discount, 2)

    def evidence(qty: dict, cost: dict, off: float = 0.0) -> bool:
        ex, tx, came_to = figures(qty, cost)
        if not _agree(came_to, total):
            return False
        tax_ok = printed_tax is not None and _agree(tx, printed_tax, tol)
        sub_ok = subtotal is not None and (
            _agree(ex, subtotal, tol) or _agree(ex + off, subtotal, tol)
        )
        return tax_ok or sub_ok

    qty0 = {i: q for i, q, _c, _r, _p in rows}
    cost0 = {i: c for i, _q, c, _r, _p in rows}
    if _agree(figures(qty0, cost0)[2], total):
        return None

    def result(qty, cost, whys, *, discount_off=False, guess=()) -> dict:
        lines = {}
        for i in qty:
            change = {}
            if not _agree(cost[i], cost0[i], 0.00005):
                change["unit_cost"] = cost[i]
            if not _agree(qty[i], qty0[i], 0.0005):
                change["quantity_received"] = qty[i]
            if change:
                change.setdefault("unit_cost", cost[i])
                lines[i] = change
        return {
            "lines": lines,
            "whys": {i: whys[i] for i in lines},
            "discount": discount_off,
            "guess": set(guess) & set(lines),
        }

    # 1. The line totals are what was charged.
    qty1, cost1, whys1, guessed = dict(qty0), dict(cost0), {}, set()
    for i, q, c, _r, charged in rows:
        if charged is None or abs(q * c - charged) <= 0.01:
            continue
        k = charged / c if c else 0.0
        if round(k) >= 1 and abs(k - round(k)) < 1e-3 and abs(round(k) - q) >= 1:
            qty1[i] = float(round(k))
            guessed.add(i)
            whys1[i] = (
                f"the invoice charges {charged:.2f} for the line: {round(k)} at "
                f"{c:.2f}, not the {q:g} it prints (or {q:g} at {charged / q:.2f}) — "
                "check the quantity"
            )
        else:
            cost1[i] = round(charged / q, 4)
            whys1[i] = (
                f"the invoice charges {charged:.2f} for the line, not {q:g} × "
                f"{c:.2f} = {q * c:.2f} — the line total is what was charged"
            )
    if whys1 and evidence(qty1, cost1):
        return result(qty1, cost1, whys1, guess=guessed)

    # 2. Prices including GST, and/or a discount off the bottom.
    readings = [
        (False, "ex"),
        (False, "incl"),
        (True, None),
        (True, "ex"),
        (True, "incl"),
    ]
    for incl, kind in readings:
        if kind and not discount:
            continue
        ex = {i: c / (1 + r) if incl else c for i, _q, c, r, _p in rows}
        s_ex = sum(qty0[i] * ex[i] for i in ex)
        gross = sum(qty0[i] * ex[i] * (1 + rates[i]) for i in ex)
        off = 0.0 if not kind else discount if kind == "ex" else discount * s_ex / gross
        if s_ex <= 0 or off >= s_ex:
            continue
        # Land exactly on the printed subtotal (before the discount or after
        # it) when it is printed and agrees.
        if subtotal is not None and _agree(s_ex, subtotal, tol):
            net = subtotal - off
        elif subtotal is not None and _agree(s_ex - off, subtotal, tol):
            net = subtotal
        else:
            net = s_ex - off
        cost = {i: round(ex[i] * net / s_ex, 4) for i in ex}
        big = max(ex, key=lambda i: abs(qty0[i] * ex[i]))
        cost[big] = round(
            cost[big] + (net - sum(qty0[i] * cost[i] for i in cost)) / qty0[big], 4
        )
        if not evidence(qty0, cost, off):
            continue
        parts = []
        if incl:
            parts.append("the invoice's prices include GST")
        if kind:
            parts.append(
                f"its {discount:.2f} discount ({kind} GST) is taken off the line "
                "costs (Loaded is never sent a discount)"
            )
        why = " and ".join(parts)
        return result(qty0, cost, {i: why for i in cost}, discount_off=bool(kind))
    return None


def settle_totals(
    data: dict,
    replica: dict,
    pairs: dict[str, str],
    extraction: dict | None,
    ask,
    db,
    log: list,
) -> None:
    """Totals that don't add up (the user: "force norm to always resolve
    this"). In order:

    1. a misread figure — the model is asked which printed number is wrong,
       and its answer counts only if correcting it makes every figure agree
       (``invoice_arbiter`` validates);
    2. Loaded's lines already add up to Loaded's total — then the invoice's
       printed figures are the odd ones out, and the Replica keeps Loaded's
       total.

    Whether the invoice then balances is decided AFTER the suggestions exist,
    on everything they apply (``invoice_review.balance_after_suggestions``):
    that is the check, and spreading the costs is its last resort. The
    copy's own figures failing to reconcile only tells this rule to look.

    Before any of that, and whether or not the copy's totals were flagged:
    prices printed including GST, or a discount off the bottom, are read
    exactly (``read_printed_prices``). A discount is otherwise invisible to
    the copy's check (lines + tax − discount = total holds) yet the receive
    can't send it, so Loaded would land on the wrong total.
    """
    issue = next(
        (i for i in replica.get("issues") or [] if i.get("code") == TOTALS_ISSUE), None
    )
    issue_id = (issue or {}).get("id") or TOTALS_ISSUE
    if _settle_printed_reading(data, replica, pairs, issue, issue_id, log):
        return
    if issue is None:
        return
    verdict: dict = {}
    if extraction and (ask is not None or db is not None):
        from app.services import invoice_arbiter

        verdict = (
            invoice_arbiter.diagnose_totals(
                [
                    {
                        "id": f"rep-{k}",
                        "description": xl.get("description"),
                        "quantity": xl.get("quantity"),
                        "unit_price": xl.get("unit_price_ex_tax"),
                        "line_total": xl.get("line_total_ex_tax"),
                    }
                    for k, xl in enumerate(extraction.get("lines") or [])
                ],
                {
                    "subtotal": extraction.get("subtotal_ex_tax"),
                    "tax": extraction.get("tax_amount"),
                    "discount": extraction.get("discount_amount"),
                    "total": extraction.get("total_incl_tax"),
                },
                [issue.get("message") or ""],
                ask_llm=ask,
                db=db,
            )
            or {}
        )
    rep_lines = {str(rl.get("id")): rl for rl in _lines(replica)}
    why = f"the invoice's totals don't add up — {verdict.get('why')}"
    fixed = False
    for c in verdict.get("corrections") or []:
        if c.get("scope") == "line":
            rl = rep_lines.get(str(c.get("line_id") or ""))
            field = _ARBITER_LINE_FIELD.get(str(c.get("field")))
            if rl is None or not field:
                continue
            rl[field] = c.get("proposed")
            rl[f"{field}_set"] = True
            rl[f"{field}_why"] = why
            rl["fixes_confidence"] = verdict.get("confidence")
            mark_fix(rl, field, issue_id)
        else:
            field = _ARBITER_HEADER_FIELD.get(str(c.get("field")))
            if not field:
                continue
            replica[field] = c.get("proposed")
            replica[f"{field}_why"] = why
            replica["fixes_confidence"] = verdict.get("confidence")
            mark_fix(replica, field, issue_id)
        fixed = True
    if fixed:
        log.append(f"totals: {why}")
        return

    # Loaded's lines, with the Replica's answers on them, already come to
    # Loaded's total: then the printed figures are the odd ones out, and the
    # Replica keeps Loaded's total. Proposing the printed one would unbalance
    # an invoice that adds up, and the balancing after the suggestions
    # (invoice_review.balance_after_suggestions) would bend every cost to fit
    # a misprint.
    view = _answer_view(data, replica, pairs)
    _, _, derived, declared = _totals_view(view)
    printed = _f(replica.get("total"))
    if (
        declared is not None
        and printed is not None
        and _agree(derived, declared, LOADED_ROUNDING)
        and not _agree(printed, declared)
    ):
        replica["total"] = declared
        replica["total_why"] = (
            f"the invoice's printed totals don't add up, but its lines come to "
            f"Loaded's total ({declared:.2f}) — keep it"
        )
        log.append(f"totals: {replica['total_why']}")


def _settle_printed_reading(
    data: dict,
    replica: dict,
    pairs: dict[str, str],
    issue: dict | None,
    issue_id: str,
    log: list,
) -> bool:
    """Apply ``read_printed_prices`` to the Replica's lines. Sure where the
    figures decide it; a guess where they can't (a whole-multiple line)."""
    view = _answer_view(data, replica, pairs)
    reading = read_printed_prices(view, replica)
    if not reading or not reading["lines"]:
        return False
    rep_for = {str(w): str(r) for r, w in pairs.items()}
    rep_lines = {str(rl.get("id")): rl for rl in _lines(replica)}
    # A line the Replica adds carries its own id in the view.
    targets = {
        vid: rep_lines.get(rep_for.get(vid, "")) or rep_lines.get(vid)
        for vid in reading["lines"]
    }
    if any(rl is None for rl in targets.values()):
        return False
    for vid, change in reading["lines"].items():
        rl, guess = targets[vid], vid in reading["guess"]
        why = reading["whys"][vid]
        if "quantity_received" in change:
            rl["quantity_received"] = change["quantity_received"]
            rl["quantity_received_why"] = why
            rl["quantity_received_guess"] = guess
            if issue is not None:
                mark_fix(rl, "quantity_received", issue_id)
        cost = change["unit_cost"]
        q = _f(rl.get("quantity_received"))
        rl["unit_cost"] = cost
        if q is not None:
            rl["total_cost"] = round(q * cost, 4)
        rl["unit_cost_set"] = True
        rl["unit_cost_guess"] = guess
        shown = (
            f"{cost:.2f}"
            if abs(cost * 100 - round(cost * 100)) < 1e-6
            else f"{cost:.4f}"
        )
        rl["unit_cost_why"] = (
            why
            if guess
            else f"{why} — {shown} a unit ex GST makes every printed figure agree"
        )
        if issue is not None:
            mark_fix(rl, "unit_cost", issue_id)
    if reading["discount"]:
        # Inside the costs now; as a figure of its own it would come off twice.
        replica["discount_absorbed"] = replica.get("discount_amount")
        replica["discount_amount"] = None
    log.append(
        "totals: the invoice's figures read exactly — "
        + "; ".join(sorted(set(reading["whys"].values())))[:400]
    )
    return True


def _answer_view(data: dict, replica: dict, pairs: dict[str, str]) -> dict:
    """The invoice as accepting its review would leave it: Loaded's draft
    with the Replica's quantity and unit cost on every line the two share,
    the lines the invoice doesn't bill struck, and the ones only the invoice
    has added. Header figures stay Loaded's: the total is what the lines must
    reach."""
    from app.services.invoice_review import _reconciled_unit_cost, _storable_qty

    rep_for = {str(w): str(r) for r, w in pairs.items()}
    rep_lines = {str(rl.get("id")): rl for rl in _lines(replica)}
    lines = []
    for ln in _lines(data):
        v = dict(ln)
        rl = rep_lines.get(rep_for.get(str(ln.get("id")), ""))
        if rl is None:
            # A line the invoice doesn't bill: accepting the review strikes it.
            v["struck"] = True
        else:
            if rl.get("quantity_received") is not None:
                v["quantity_received"] = _storable_qty(rl.get("quantity_received"))
            cost = (
                rl.get("unit_cost")
                if rl.get("unit_cost_set")
                else _reconciled_unit_cost(rl)[0]
            )
            if _f(cost) is not None:
                v["unit_cost"] = cost
            # What the invoice charged for the line — read_printed_prices
            # weighs it against quantity × price.
            v["printed_total"] = rl.get("total_cost")
        lines.append(v)
    # A line the invoice bills that Loaded's draft lacks: accepting the review
    # adds it (Aitkens 178549's freight).
    paired = set(pairs)
    for rl in _lines(replica):
        if str(rl.get("id")) in paired:
            continue
        lines.append(
            {
                "id": str(rl.get("id")),
                "description": rl.get("description"),
                "quantity_received": _storable_qty(rl.get("quantity_received")),
                "unit_cost": rl.get("unit_cost")
                if rl.get("unit_cost_set")
                else _reconciled_unit_cost(rl)[0],
                "sale_tax_rate": rl.get("sale_tax_rate"),
                "printed_total": rl.get("total_cost"),
            }
        )
    return {
        "tax_amount": data.get("tax_amount"),
        "total": data.get("total"),
        # Both change what the lines must come to — the card counts them.
        "discount_amount": data.get("discount_amount"),
        "unit_cost_includes_tax": data.get("unit_cost_includes_tax"),
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Rule 7 — brands
# ---------------------------------------------------------------------------


def settle_brands(data: dict, replica: dict, pairs: dict[str, str], log: list) -> None:
    """Brands. The invoice isn't read for them — that would put a suggestion
    on nearly every line for nothing — so a line's brand is the one Loaded's
    line names; when Loaded has no record of it, the answer is a new brand
    under that name (Loaded won't receive a line naming a brand it doesn't
    have: Bidfood 109945346, BIOZYME)."""
    rep_for = {str(w): str(r) for r, w in pairs.items()}
    rep_lines = {str(rl.get("id")): rl for rl in _lines(replica)}
    for ln in _lines(data):
        if ln.get("struck"):
            continue
        brand = str(ln.get("brand") or "").strip()
        if not brand or ln.get("linked_brand_id"):
            continue
        rl = rep_lines.get(rep_for.get(str(ln.get("id")), ""))
        if rl is not None:
            rl["brand"] = brand
            rl["brand_create_name"] = brand
        name = ln.get("item_name") or ln.get("description") or ln.get("code") or "line"
        log.append(f"brand '{brand}' on '{name}' isn't in Loaded — create it")


# ---------------------------------------------------------------------------


def finish_replica(
    data: dict,
    replica: dict,
    pairs: dict[str, str],
    *,
    catalogue: list[dict] | None = None,
    units: list[dict] | None = None,
    suppliers: list[dict] | None = None,
    lh=None,
    choose_unit=None,
    totals_ask=None,
    db=None,
    extraction: dict | None = None,
    read_supplier_details=None,
) -> None:
    """Apply every rule to ``replica`` in place. ``pairs`` maps replica line
    ids to the Loaded lines they describe (``invoice_review.pair_lines``)."""
    log = replica.setdefault("resolution_log", [])
    rules = (
        (
            "supplier",
            lambda: settle_supplier(
                data, replica, suppliers, log, read_supplier_details
            ),
        ),
        ("items", lambda: settle_items(data, replica, pairs, catalogue, log)),
        ("units", lambda: settle_units(data, replica, pairs, units, choose_unit, log)),
        ("order", lambda: settle_order(data, replica, lh, log)),
        (
            "totals",
            lambda: settle_totals(
                data, replica, pairs, extraction, totals_ask, db, log
            ),
        ),
        ("brands", lambda: settle_brands(data, replica, pairs, log)),
    )
    for name, rule in rules:
        try:
            rule()
        except Exception as exc:  # noqa: BLE001 — one rule must not cost the rest
            logger.warning("replica rule '%s' failed: %s", name, exc)
            log.append(f"{name}: rule failed ({exc})")
