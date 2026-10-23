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

import re

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


#: Names that are Loaded saying "I don't know", never an identity. Matching
#: one resolved a Bidfood invoice to the venue's literal "[Unnamed Supplier]"
#: record and the card read Ready-to-receive with no supplier (110016259,
#: 19 Aug 2026).
_PLACEHOLDER_NORMS = {"unnamedsupplier"}


def is_placeholder_supplier_name(name: object) -> bool:
    """True for empty names and Loaded's unnamed-supplier placeholder."""
    return norm(name) in _PLACEHOLDER_NORMS or not norm(name)


def words(text: object) -> set[str]:
    """Identity-bearing words: lowercase tokens, legal suffixes dropped."""
    return set(_tokens(text))


def _tokens(text: object) -> list[str]:
    """The name's words in order: lowercase alphanumeric runs, legal suffixes
    dropped."""
    spaced = "".join(c if c.isalnum() else " " for c in str(text or "").lower())
    return [t for t in spaced.split() if t not in _NOISE]


def has_identity(name: object) -> bool:
    """Whether a name says WHO a business is: it needs a word of three or more
    letters that is not a legal suffix.

    Loaded's document scanning sometimes reads only a fragment of a supplier's
    name, and Norm named layouts after the fragments: "PTY LTD", "LTD", "PTY."
    and "&" (16–17 Oct 2026), and "AF", the first line of a name printed over
    two (20 Oct). A name like that fits every supplier that shares the suffix:
    14 Foodlink, Select Fresh and Brighton invoices were received under Reward
    Supply Co. Pty Ltd because a layout called "PTY LTD" said so (21 Oct). So
    no such name may name a layout, be an alias, or match anything."""
    return any(len(t) >= 3 for t in _tokens(name))


def identity_name(text: object) -> str | None:
    """The name to file a business under: the first line of ``text`` that has
    an identity, or None when no line has one. "AF" above "Fistonich Family
    VINEYARDS" is Fistonich Family VINEYARDS."""
    for line in str(text or "").splitlines():
        if has_identity(line):
            return line.strip()
    return None


#: 'Lambert Ltd T/A Sea 2 You', 'Komulla Pty Ltd trading as Far Meadow Lodge'.
_TRADING_AS = re.compile(r"\s(?:t/a|t\.a\.|trading\s+as)\s", re.I)
#: Legal suffixes at the END of a name ('Reward Supply Co. Pty Ltd').
_LEGAL_TAIL = re.compile(
    r"(?:[\s,]+\(?(?:pty|ltd|limited|proprietary|inc|incorporated|nz)\.?\)?)+[\s.,]*$",
    re.I,
)


def trading_name(printed: object) -> str:
    """The name a business trades under, for a supplier record Norm creates:
    the part after 'T/A' or 'trading as' when the invoice prints one, with
    legal suffixes dropped from the end. 'Lambert Ltd T/A Sea 2 You' is Sea 2
    You, 'Reward Supply Co. Pty Ltd' is Reward Supply Co, as the venues' own
    lists name them (onboarding comparison, 21 Oct 2026). Never a name with
    no business in it: the printed name stands when nothing else is left.
    A letterhead over two lines names the business twice, legal name above
    trading name ('M H Luxe Holdings Pty Ltd' / 'Luxe Bakery Sydney'): the
    line that isn't the legal name is the trading name."""
    lines = [ln.strip() for ln in str(printed or "").splitlines() if has_identity(ln)]
    plain = [ln for ln in lines if not _LEGAL_TAIL.search(ln)]
    name = (
        plain[0]
        if len(lines) > 1 and plain
        else identity_name(printed) or str(printed or "").strip()
    )
    parts = _TRADING_AS.split(f" {name} ")
    if len(parts) > 1 and has_identity(parts[-1]):
        name = parts[-1].strip()
    short = _LEGAL_TAIL.sub("", name).strip(" ,.-")
    return short if has_identity(short) else name.strip()


def same_name(a: object, b: object) -> bool:
    """Whether two names are one name, legal suffixes aside ('SERVICE FOODS
    LTD' and 'Service Foods')."""
    if norm(a) == norm(b):
        return bool(norm(a))
    pa = "".join(_tokens(a))
    return bool(pa) and pa == "".join(_tokens(b))


def _targets(names) -> list[str]:
    """The normalised identity hints, deduplicated but ORDER PRESERVED —
    callers rank hints by authority and that ranking has to survive. A hint
    with no identity ('PTY LTD') is dropped: it would match half the supplier
    list."""
    out: list[str] = []
    for x in names:
        if is_placeholder_supplier_name(x) or not has_identity(x):
            continue  # "[Unnamed Supplier]" is a shrug, not a hint
        n = norm(x)
        if n not in out:
            out.append(n)
    return out


def _distinctive(tokens: list[str]) -> bool:
    """Whether a name may match by being INSIDE another one. A single word
    that only says what is sold ('FRESH') is inside Select Fresh, Bidfresh
    and Fresh Connection alike, so it may only match a name equal to it."""
    return len(tokens) > 1 or _stem(tokens[0]) not in {_stem(w) for w in _TRADE_WORDS}


def _inside(tokens: list[str], hint: list[str]) -> bool:
    """Whether a name's words are all in the hint, word by word ('Select
    Fresh' is inside 'Select Fresh Providores Pty Ltd'; 'Lion' is not inside
    'Lionel's Bakery'). Spacing may differ: 'FreshConnection' is inside 'Fresh
    Connection Ltd', 'Kiwi Fish' inside 'KIWIFISH INTERNATIONAL'."""
    if not tokens or not hint:
        return False
    stems = {_stem(t) for t in hint}
    if all(_stem(t) in stems for t in tokens):
        return True
    joined = "".join(tokens)
    for i in range(len(hint)):
        run = ""
        for t in hint[i:]:
            run += t
            if run == joined:
                return True
            if len(run) >= len(joined):
                break
    return False


def _rank(candidate: str, hints: list[tuple]) -> tuple[int, int] | None:
    """How well one candidate name matches, lower = better.

    (0, …) the same name · (1, …) the same name once legal suffixes are
    dropped ('SERVICE FOODS LTD' is 'Service Foods') · (2, …) the candidate's
    words are inside an identity hint. Ties break on LENGTH: the most specific
    match wins, which is what stops a short generic alias beating a full exact
    name. A name with no identity never matches.
    """
    if not has_identity(candidate):
        return None
    c, tokens = norm(candidate), _tokens(candidate)
    phrase = "".join(tokens)
    if any(c == h for h, _p, _t in hints):
        return (0, -len(phrase))
    if any(phrase == p for _h, p, _t in hints):
        return (1, -len(phrase))
    if _distinctive(tokens) and any(_inside(tokens, t) for _h, _p, t in hints):
        return (2, -len(phrase))
    return None


def match_spec(specs, names, *, main_prompt_name: str):
    """The global layout spec for a supplier, or (None, reason).

    Precedence: exact NAME, then exact ALIAS, then containment — most specific
    first within each. A genuine tie between two DIFFERENT specs returns
    ``(None, "ambiguous")`` rather than picking one: the old code took the
    alphabetically-first match, which is how 'Eurovintage' silently claimed
    Service Foods invoices. No spec means the generic prompt plus a sensei
    pass, which is self-correcting; a confidently wrong spec is not.

    ``how`` is "name" or "alias" when the hint IS that name (legal suffixes
    aside), "contains" when the name was only found inside it. Only the
    first two may say which business an invoice is from.
    """
    targets = [
        (norm(x), "".join(_tokens(x)), _tokens(x))
        for x in names
        if not is_placeholder_supplier_name(x) and has_identity(x)
    ]
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
    how = ("name", "alias")[top_score[1]] if top_score[0] < 2 else "contains"
    return top_spec, how


def live_suppliers(suppliers) -> list[dict]:
    """Loaded rows that still exist — both of Loaded's deletion markers."""
    return [
        s
        for s in (suppliers or [])
        if isinstance(s, dict)
        and s.get("id")
        and not (s.get("removedAt") or s.get("datestampDeleted"))
        # The unnamed-supplier placeholder is not an identity — nothing may
        # resolve TO it, in any tier, from any caller.
        and not is_placeholder_supplier_name(s.get("name"))
    ]


def admissible_aliases(supplier_name, aliases, suppliers) -> list[str]:
    """The aliases that may act as identity hints for this supplier.

    Loaded's alias list is not a curated registry — it is what its own document
    scanning has seen, so it holds OCR debris ('BIDERESH CHRISTCHURCH',
    'Burovintage Ltd') and, where a supplier record was renamed or repurposed,
    the name of a DIFFERENT business. That matters because these hints choose
    the extraction prompt: Glass Goose's 'La Zeppa' record carries the alias
    'Oravida', and every invoice from the sister venue was read with Oravida's
    layout.

    The account is the authority on its own identities, so let it answer: an
    alias that is the name of ANOTHER live supplier record in the same account
    is the account stating those are two businesses, and cannot also be this
    one's spelling. Nothing else is second-guessed — a legal name that looks
    nothing like the trading name ('Ahi Mokopuna Limited Partnership' for
    Akaroa Salmon) is exactly what an alias list is for.
    """
    own = norm(supplier_name)
    others = {
        norm(s.get("name")) for s in live_suppliers(suppliers) if norm(s.get("name"))
    }
    others.discard(own)
    return [a for a in (aliases or []) if norm(a) not in others]


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


#: Words that say what a supplier SELLS, not who it is. Two businesses that
#: share only these are not the same business: 'Blue Seas Food Services' is
#: not 'PFD Food Services' (Applegum Cafe SI650438, 15 Oct 2026 — linked to
#: PFD as "the closest", which also stopped autopilot creating Blue Seas).
#: Places are here too: half the account's suppliers are in Auckland.
_TRADE_WORDS = {
    "and",
    "of",
    "online",
    "direct",
    "group",
    "holdings",
    "enterprises",
    "international",
    "global",
    "trading",
    "traders",
    "trade",
    "imports",
    "merchant",
    "merchants",
    "importers",
    "export",
    "exports",
    "industries",
    "products",
    "solutions",
    "food",
    "foods",
    "service",
    "services",
    "fresh",
    "produce",
    "wholesale",
    "wholesaler",
    "wholesalers",
    "distributor",
    "distributors",
    "distribution",
    "supply",
    "supplies",
    "supplier",
    "suppliers",
    "hospitality",
    "catering",
    "caterer",
    "caterers",
    "market",
    "markets",
    "meat",
    "meats",
    "butcher",
    "butchery",
    "seafood",
    "seafoods",
    "fish",
    "poultry",
    "dairy",
    "bakery",
    "bakehouse",
    "bread",
    "beverage",
    "beverages",
    "drinks",
    "liquor",
    "wine",
    "wines",
    "spirits",
    "beer",
    "brewing",
    "brewery",
    "coffee",
    "tea",
    "packaging",
    "cleaning",
    "hygiene",
    "chemicals",
    "kitchen",
    "specialty",
    "speciality",
    "gourmet",
    "quality",
    "premium",
    "farm",
    "farms",
    "new",
    "zealand",
    "auckland",
    "wellington",
    "christchurch",
    "dunedin",
    "hamilton",
    "tauranga",
    "north",
    "south",
    "island",
    "east",
    "west",
    "central",
    # Australia too (Applegum Cafe, 15 Oct 2026: 'PNM SYDNEY PTY LTD' was
    # proposed as 'Sydney Direct Fresh Produce', so PNM was never created).
    "australia",
    "australian",
    "sydney",
    "melbourne",
    "brisbane",
    "perth",
    "adelaide",
    "hobart",
    "darwin",
    "canberra",
    "nsw",
    "vic",
    "qld",
    "tas",
}


def _stem(word: str) -> str:
    """'Seas' and 'Sea', 'Bidfoods' and 'Bidfood' are one word."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _phrase(text: object) -> str:
    """The name as one run of letters and digits, legal suffixes dropped:
    'SERVICE FOODS LTD' -> 'servicefoods'."""
    tokens = "".join(c if c.isalnum() else " " for c in str(text or "").lower()).split()
    return "".join(w for w in tokens if w not in _NOISE)


def plausible_matches(name: object, suppliers, *, limit: int = 3) -> list[dict]:
    """The account's suppliers that could be the business ``name`` prints —
    the ones a new record would duplicate — best first.

    A shared DISTINCTIVE word is required: sharing only trade words ('Food',
    'Services', 'Fresh') proves nothing. A name made only of trade words
    ('SERVICE FOODS LTD') is its own identity, so a match must carry it as a
    phrase — 'SERVICE FOODS AUCKLAND' does (the duplicate that sent every
    Service Foods invoice to the wrong layout, 10 Aug 2026); 'Bidvest Food
    Service' has the words but not the name.

    ``alias_candidates`` stays the broad prefilter for which alias lists are
    worth fetching; this is the narrow question "is it the same business?"."""
    mine = {_stem(w) for w in words(name)}
    trade = {_stem(w) for w in _TRADE_WORDS}
    core = mine - trade
    if not mine:
        return []
    phrase = _phrase(name)
    scored = []
    for s in live_suppliers(suppliers):
        theirs = {_stem(w) for w in words(s.get("name"))}
        their_phrase = _phrase(s.get("name"))
        short = min(phrase, their_phrase, key=len)
        # One name inside the other: 'KIWIFISH' is 'Kiwi Fish International
        # Limited', though they share no word.
        contained = len(short) >= 5 and (
            their_phrase in phrase or phrase in their_phrase
        )
        if core:
            score = len(core & theirs) or (1 if contained else 0)
        else:
            score = len(mine) if phrase in norm(s.get("name")) else 0
        if score:
            scored.append((-score, len(norm(s.get("name"))), s))
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
        # A record name or Loaded alias with no identity ('Ltd', which Loaded's
        # scanning left on several records) is inside every hint sharing it.
        names = [s.get("name")] + list(aliases_by_id.get(str(s.get("id")), []))
        return [n for n in names if has_identity(n)]

    # Hint by hint, in the caller's order of authority — and ALL tiers run
    # for a hint before the next hint is consulted. Running the exact tier
    # over every hint first inverted the authority: Loaded's low-authority
    # hint exact-matched its own placeholder record while the printed name's
    # correct containment match never got a turn (110016259, 19 Aug 2026).
    # An ambiguous containment DEFERS to the next hint rather than giving up:
    # when the invoice came from an order, Loaded's supplier is the human
    # choice that breaks exactly this kind of tie.
    for target in targets:
        for s in live:
            if any(norm(n) == target for n in known(s) if n):
                return s, "exact"
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
