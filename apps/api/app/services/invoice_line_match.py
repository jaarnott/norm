"""Line ↔ catalogue matching for supplier invoices — the shared primitives.

Two directions share these rules:

- The review ENGINE matches a Loaded line against the invoice COPY's lines
  (``plain_match``) and, via a known stock item's supplier variants, lets a
  variant claim a copy line (``variant_claim``). Those two are VERBATIM
  ports of the sandboxed consolidator's ``_plain_match``/``_variant_claim``
  (config/consolidators/review_and_receive_invoices.py) — the sandbox can't
  import app modules, so the logic is duplicated there by design and pinned
  by a parity test (the ``invoice_units`` precedent).

- The replica builder matches an EXTRACTED line against the venue's whole
  catalogue (``CatalogueIndex`` + ``match_line``): Loaded's own ingestion
  convention, keyed on ``(supplierId, normalized stockCode)`` first
  (verified live: BROCCOLI delivered VEGF0223 vs ordered 165618 — the CODE
  is the key, not the item), then variant/item descriptions with the same
  8-char substring floor and uniqueness discipline as the engine.

Conservative by design throughout: a wrong claim is worse than no claim —
ambiguity returns None and the caller falls back to the LLM matcher.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _norm(text: object) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


LINE_TOL = Decimal("0.01")


def plain_match(ln: dict, pool: list[dict]) -> dict | None:
    """The two first-class pairing rules, shared by the dry-run and the real
    pass: exact normalized code, then description substring either way.
    Originally a verbatim port of the engine's ``_plain_match``; since the
    replica-primary refactor this is the only implementation."""
    for cand in pool:
        if _norm(cand.get("code")) and _norm(cand.get("code")) == _norm(ln.get("code")):
            return cand
    for cand in pool:
        if _norm(cand.get("description")) and (
            _norm(cand.get("description")) in _norm(ln.get("description"))
            or _norm(ln.get("description")) in _norm(cand.get("description"))
        ):
            return cand
    return None


def variant_claim(
    ln: dict, item: dict, supplier_id: str | None, candidates: list[dict]
) -> dict | None:
    """Match a Loaded line against doc lines via the stock item's supplier
    variants. Conservative by design: a wrong claim clears the mismatch
    reason and can let autopilot auto-receive, so only a UNIQUE hit (or a
    tie broken by the line total) claims. Exact tiers first, then substring
    with a length floor so short generic fragments can't match.
    Originally a verbatim port of the engine's ``_variant_claim``; since the
    replica-primary refactor this is the only implementation."""
    variants = [
        v
        for v in (item.get("suppliers") or [])
        if isinstance(v, dict)
        and not (v.get("datestampDeleted") or v.get("removedAt") or v.get("deletedAt"))
    ]
    scoped = [v for v in variants if v.get("supplierId") == supplier_id]
    variants = scoped or variants
    texts = [_norm(v.get("description")) for v in variants] + [_norm(item.get("name"))]
    texts = [t for t in texts if t]
    codes = [_norm(v.get("stockCode")) for v in variants]
    codes = [c for c in codes if c]

    hits = []
    for cand in candidates:
        cdesc = _norm(cand.get("description"))
        ccode = _norm(cand.get("code"))
        if (ccode and ccode in codes) or (cdesc and cdesc in texts):
            hits.append(cand)
    if not hits:
        for cand in candidates:
            cdesc = _norm(cand.get("description"))
            if not cdesc:
                continue
            for t in texts:
                small, big = (t, cdesc) if len(t) <= len(cdesc) else (cdesc, t)
                if len(small) >= 8 and small in big:
                    hits.append(cand)
                    break
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # Loaded renamed totalCost → totalCostExclTax (05 Aug 2026): new name
        # first, old as fallback — exactly the engine's _ln_tot.
        tot = ln.get("totalCostExclTax")
        if tot is None:
            tot = ln.get("totalCost")
        ln_tot = _dec(tot)
        if ln_tot is not None:
            close_hits = [
                c
                for c in hits
                if _dec(c.get("line_total_ex_tax")) is not None
                and abs(_dec(c.get("line_total_ex_tax")) - ln_tot) <= LINE_TOL
            ]
            if len(close_hits) == 1:
                return close_hits[0]
    return None


class CatalogueIndex:
    """In-memory index over the venue catalogue (one bulk items fetch, with
    ``suppliers[]`` variants embedded — verified live: no per-item calls).

    Lookup tiers mirror Loaded's own ingestion and the engine's discipline:

    1. ``(supplier_id, norm(code))`` — the variant stockCode for THIS
       supplier (Loaded's receive-time key).
    2. ``norm(code)`` on any supplier's variant — unscoped fallback, unique
       hit only (a code shared by two suppliers' variants stays unmatched).
    3. Exact normalized description — variant description or item name,
       unique hit only.
    4. Substring with the 8-char floor over variant descriptions and item
       names — unique hit only.

    A tie at any tier (or a live-variant-less catalogue miss) returns None —
    the caller falls back to the LLM matcher.
    """

    def __init__(self) -> None:
        self.by_supplier_code: dict[tuple[str, str], list[dict]] = {}
        self.by_code: dict[str, list[dict]] = {}
        self.by_text: dict[str, list[dict]] = {}
        # (normalized text, item) pairs for the substring tier
        self.texts: list[tuple[str, dict]] = []

    @classmethod
    def build(cls, items: list[dict]) -> "CatalogueIndex":
        idx = cls()
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            name_n = _norm(item.get("name"))
            if name_n:
                idx.by_text.setdefault(name_n, []).append(item)
                idx.texts.append((name_n, item))
            for v in item.get("suppliers") or []:
                if not isinstance(v, dict) or (
                    v.get("datestampDeleted")
                    or v.get("removedAt")
                    or v.get("deletedAt")
                ):
                    continue
                code_n = _norm(v.get("stockCode"))
                if code_n:
                    sup = str(v.get("supplierId") or "")
                    idx.by_supplier_code.setdefault((sup, code_n), []).append(item)
                    idx.by_code.setdefault(code_n, []).append(item)
                desc_n = _norm(v.get("description"))
                if desc_n:
                    idx.by_text.setdefault(desc_n, []).append(item)
                    idx.texts.append((desc_n, item))
        return idx

    @staticmethod
    def _unique(items: list[dict]) -> dict | None:
        ids = {i.get("id") for i in items}
        return items[0] if len(ids) == 1 else None

    def match_line(
        self, code: object, description: object, supplier_id: str | None
    ) -> tuple[dict | None, str | None]:
        """Resolve one extracted line → (item, matched_by) or (None, None)."""
        code_n = _norm(code)
        desc_n = _norm(description)
        if code_n and supplier_id:
            hit = self._unique(
                self.by_supplier_code.get((str(supplier_id), code_n), [])
            )
            if hit:
                return hit, "supplier_code"
        if code_n:
            hit = self._unique(self.by_code.get(code_n, []))
            if hit:
                return hit, "code"
        if desc_n:
            hit = self._unique(self.by_text.get(desc_n, []))
            if hit:
                return hit, "description_exact"
            subs = []
            seen_ids = set()
            for t, item in self.texts:
                small, big = (t, desc_n) if len(t) <= len(desc_n) else (desc_n, t)
                if len(small) >= 8 and small in big and item.get("id") not in seen_ids:
                    subs.append(item)
                    seen_ids.add(item.get("id"))
            if len(subs) == 1:
                return subs[0], "description_substring"
        return None, None


def supplier_variant(item: dict, supplier_id: str | None, code: object) -> dict | None:
    """The live variant on ``item`` for this supplier — by normalized
    stockCode first, else the supplier's default/first variant. Mirrors the
    receive path's ``(supplierId, normalized stockCode)`` convention."""
    live = [
        v
        for v in (item.get("suppliers") or [])
        if isinstance(v, dict)
        and not (v.get("datestampDeleted") or v.get("removedAt") or v.get("deletedAt"))
    ]
    scoped = [
        v for v in live if str(v.get("supplierId") or "") == str(supplier_id or "")
    ]
    code_n = _norm(code)
    if code_n:
        for v in scoped:
            if _norm(v.get("stockCode")) == code_n:
                return v
    for v in scoped:
        if v.get("defaultForSupplier"):
            return v
    if scoped:
        return scoped[0]
    return None
