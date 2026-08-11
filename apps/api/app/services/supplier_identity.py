"""Who is this supplier, and whose layout is this invoice?

Two questions, previously answered by four independent string matchers that
disagreed. On 11 Aug 2026 a SERVICE FOODS LTD invoice was extracted with a
WINE WHOLESALER's prompt, filed in the dojo under that wholesaler, and left
with no Loaded supplier at all — three symptoms, one cause.

The separation that makes this scale:

**Supplier identity is per Loaded account.** One account alone spells this
business six ways ('SERVICE FOODS LTD', 'SERVICE FOODS', 'SERVICE FOODS -
AUCKLAND FOODSERVICE', 'Service Foods Online'...). Loaded already carries that
list per supplier record and the venue maintains it. Norm does not copy it.

**A layout spec is global.** Service Foods prints the same template for every
venue in every account. So a spec needs ONE canonical business name — never
the union of every customer's spelling. Absorbing per-account variance into
the global spec is what broke: 'Service Foods' had been merged onto the
Eurovintage spec's aliases, and with thousands of accounts that list would
grow without bound and collide more often with every one added.

So: resolve the printed name against the ACCOUNT's supplier records (using
Loaded's own aliases), then match the resolved supplier's full identity set
against the GLOBAL specs. Each step asks the layer that owns the answer.

Everything here is pure — no DB, no HTTP. Callers supply the rows.
"""

from __future__ import annotations

# Legal/registration suffixes carry no identity: 'SERVICE FOODS LTD' and
# 'Service Foods Limited' are one business. Kept deliberately short — a word
# that distinguishes two real businesses must never be in here ('Foods' and
# 'Service' are load-bearing, and stay out).
_NOISE = {
    "ltd",
    "limited",
    "co",
    "company",
    "pty",
    "inc",
    "incorporated",
    "nz",
    "the",
}


def norm(text: object) -> str:
    """Lowercase alphanumerics only — the one normaliser.

    Four copies of this used to live in invoice_replica, invoice_extraction,
    received_invoice and the supplier-specs router. They were identical, which
    is exactly why they were dangerous: nothing made them stay identical.
    """
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def words(text: object) -> set[str]:
    """Identity-bearing words: lowercase tokens, legal suffixes dropped."""
    out: set[str] = set()
    token = ""
    for ch in str(text or "").lower():
        if ch.isalnum():
            token += ch
        else:
            if token and token not in _NOISE:
                out.add(token)
            token = ""
    if token and token not in _NOISE:
        out.add(token)
    return out


def _targets(names) -> list[str]:
    """The normalised identity hints, ≥3 chars (shorter substring-matches half
    the supplier list), deduplicated but ORDER PRESERVED — callers rank hints
    by authority and that ranking has to survive."""
    out: list[str] = []
    for n in (norm(x) for x in names):
        if len(n) >= 3 and n not in out:
            out.append(n)
    return out


def _rank(candidate: str, targets: set[str]) -> tuple[int, int] | None:
    """How well one candidate name matches, lower = better.

    (0, …) equality · (1, …) the candidate is contained in an identity hint.
    Ties break on LENGTH: the most specific match wins, which is what stops a
    short generic alias beating a full exact name.
    """
    c = norm(candidate)
    if len(c) < 3:
        return None
    if c in targets:
        return (0, -len(c))
    if any(c in t for t in targets):
        return (1, -len(c))
    return None


def match_spec(specs, names, *, main_prompt_name: str):
    """The global layout spec for a supplier, or (None, reason).

    Precedence: exact NAME, then exact ALIAS, then containment — most specific
    first within each. A genuine tie between two DIFFERENT specs returns
    ``(None, "ambiguous")`` rather than picking one: the old code took the
    alphabetically-first match, which is how 'Eurovintage' silently claimed
    Service Foods invoices. No spec means the generic prompt plus a sensei
    pass, which is self-correcting; a confidently wrong spec is not.
    """
    targets = _targets(names)
    if not targets:
        return None, None
    ranked: list[tuple[tuple[int, int, int], object]] = []
    for sp in specs:
        if sp.name == main_prompt_name or not sp.enabled:
            continue
        best: tuple[int, int, int] | None = None
        # tier 0 = the spec's own name, 1 = one of its aliases.
        for tier, cand in [(0, sp.name)] + [(1, a) for a in (sp.aliases or [])]:
            r = _rank(cand, targets)
            if r is None:
                continue
            score = (r[0], tier, r[1])
            if best is None or score < best:
                best = score
        if best is not None:
            ranked.append((best, sp))
    if not ranked:
        return None, None
    ranked.sort(key=lambda x: x[0])
    top_score, top_spec = ranked[0]
    for score, sp in ranked[1:]:
        if score == top_score and str(sp.id) != str(top_spec.id):
            return None, "ambiguous"
    how = ("name", "alias")[top_score[1]] if top_score[0] == 0 else "contains"
    return top_spec, how


def live_suppliers(suppliers) -> list[dict]:
    """Loaded rows that still exist — both of Loaded's deletion markers."""
    return [
        s
        for s in (suppliers or [])
        if isinstance(s, dict)
        and s.get("id")
        and not (s.get("removedAt") or s.get("datestampDeleted"))
    ]


def alias_candidates(names, suppliers, *, limit: int = 3) -> list[dict]:
    """Which suppliers are worth an aliases fetch, best first.

    The old rule was "suppliers whose name already contains, or is contained
    in, the printed name" — which needs the answer in order to ask the
    question. 'SERVICE FOODS LTD' and 'SERVICE FOODS AUCKLAND' share no
    containment, so Loaded's alias list was never fetched — even though it
    held 'SERVICE FOODS LTD' verbatim and would have matched outright.

    Shared identity WORDS survive a different tail, so they are the right
    prefilter: 2 shared words here, 0-1 for every other supplier in the venue.
    """
    hint_words: set[str] = set()
    for n in names:
        hint_words |= words(n)
    if not hint_words:
        return []
    scored = []
    for s in live_suppliers(suppliers):
        overlap = len(hint_words & words(s.get("name")))
        if overlap:
            scored.append((-overlap, len(norm(s.get("name"))), s))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [s for _, _, s in scored[:limit]]


def resolve_supplier(names, suppliers, aliases_by_id=None):
    """Identity hints → the account's supplier record, or (None, None).

    ``names`` is ordered by authority: the name PRINTED on the copy first,
    then whatever Loaded itself records for the invoice. The copy leads
    because Loaded's own value is its OCR of the same paper; Loaded's value
    still earns a place because when the invoice came from a purchase order it
    carries the supplier a human chose at order time.

    Exact equality (name or Loaded alias) beats containment, and an ambiguous
    containment resolves to nothing — the caller falls back to the LLM matcher.
    """
    targets = _targets(names)
    if not targets:
        return None, None
    aliases_by_id = aliases_by_id or {}
    live = live_suppliers(suppliers)

    def known(s: dict) -> list[str]:
        return [s.get("name")] + list(aliases_by_id.get(str(s.get("id")), []))

    # Hint by hint, in the caller's order of authority: the copy is asked
    # first and only an unresolvable copy defers to Loaded's own value.
    # Iterating suppliers first would silently let a lower-authority hint win
    # whenever its supplier happened to sort earlier in Loaded's list.
    for target in targets:
        for s in live:
            if any(norm(n) == target for n in known(s) if n):
                return s, "exact"
    for target in targets:
        hits, seen = [], set()
        for s in live:
            for n in known(s):
                c = norm(n)
                if len(c) >= 3 and (c in target or target in c):
                    if s.get("id") not in seen:
                        seen.add(s.get("id"))
                        hits.append(s)
                    break
        if len(hits) == 1:
            return hits[0], "containment"
        if hits:
            return None, None  # ambiguous on the strongest hint that matched
    return None, None


def alias_conflict(specs, candidate: str, *, spec_id: object = None) -> str | None:
    """The name of another spec already claiming this alias, else None.

    An alias is an identity claim on a global row, so two specs claiming the
    same string is never right — it makes the match order decide which layout
    a document gets. 'Service Foods' sat on BOTH the Service Foods spec (as
    its name) and the Eurovintage spec (as an alias), and Eurovintage won on
    alphabetical order. Checked on every write path, including the sensei's
    own alias merge, which used to bypass validation entirely.
    """
    c = norm(candidate)
    if not c:
        return None
    for sp in specs:
        if spec_id is not None and str(sp.id) == str(spec_id):
            continue
        if norm(sp.name) == c:
            return str(sp.name)
        if any(norm(a) == c for a in (sp.aliases or [])):
            return str(sp.name)
    return None
