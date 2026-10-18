# ruff: noqa: F821 — sandbox-injected names (json, preview); not imports.
#
# Canonical function_code for `loadedhub.manage_supplier` — a venue's Loaded
# supplier records. Installed by scripts/sync_manage_supplier_config.py,
# Oct 2026. Two ops:
#
#   review_aliases  reads; never asks. Every supplier's aliases (the other
#                   names Loaded files a scanned invoice under a supplier by),
#                   with the ones that look wrong flagged.
#   remove_alias    a write; asks first unless the person always allows it.
#                   DELETE /suppliers/{id}/aliases/{aliasId} -> 204 (seen in
#                   Loaded's own app, 18 Oct 2026).
#
# Why it exists: St. Malo Bakery at Somewhere North carried the alias "St.",
# and Loaded filed 19 Sydney Waste Services bills ($12,170) under the bakery
# (17 Oct 2026). Norm only ever READ these lists; nothing could flag or fix one.
#
# The flags (advice "remove" unless said otherwise):
#   blank                 no text at all
#   venue_name            the venue's own name — the bill-to block, read as the
#                         supplier ('Bessie' on two Bessie & Engineers records)
#   duplicate             the same text twice on one record (keep one)
#   too_short             too short, or only generic words where the supplier
#                         has a telling one ("St.", "Ltd", 'FRESH' on Prime
#                         Fresh & Co). A name that IS generic words keeps them:
#                         'SERVICE FOODS LTD' on Service Foods is its name.
#   another_supplier      it IS another supplier record's name at the venue —
#                         the account itself says they're different businesses
#   on_two_suppliers      advice "check": the same alias on two records, and
#                         Loaded can only file it under one — keep it on the
#                         right one ('Bidfood Limited' on both Bidfood records)
#   unrelated             advice "check": shares no telling word with the
#                         supplier's name or its other aliases — a legal or
#                         former trading name looks like this too ('PNM SYDNEY
#                         PTY LTD' is Premier Northpak). Plurals and run-
#                         together words count ('Aitken & Co' is Aitkens,
#                         'Euro Vintage' is Eurovintage).
#
# A write the model never makes without the evidence: remove_alias names the
# supplier (name or id) and each alias (as review_aliases lists it, or its id).
# An alias that isn't on that record stops the call — nothing removed.
#
# Approval (services/previews.py): the card lists every alias it removes; the
# fingerprint covers the alias ids, so an approval never lands on a list that
# changed since. After deleting it reads the aliases back.
#
# Requires consolidator_config:
#   {"max_api_calls": 200, "allowed_write_actions": ["delete_supplier_alias_raw"]}
#   (review: the supplier list + one alias read per supplier.)

OPS = ("review_aliases", "remove_alias")

#: Words that never tell one business from another.
GENERIC = {
    "the",
    "and",
    "of",
    "co",
    "company",
    "ltd",
    "limited",
    "pty",
    "inc",
    "llc",
    "group",
    "holdings",
    "trading",
    "enterprises",
    "international",
    "nz",
    "au",
    "new",
    "zealand",
    "australia",
    "services",
    "service",
    "foods",
    "food",
    "fresh",
    "supplies",
    "supply",
    "distributors",
    "distribution",
    "wholesale",
    "wholesalers",
    "produce",
    "invoice",
    "tax",
    "supplier",
    "st",
    "street",
    "rd",
    "road",
    "unit",
    "level",
    "po",
    "box",
    "artisan",
    "bakers",
    "foodservice",
    "online",
    "dc",
    "division",
    "attention",
    "trading",
    "as",
    "sydney",
    "melbourne",
    "brisbane",
    "perth",
    "adelaide",
    "auckland",
    "wellington",
    "christchurch",
    "dunedin",
    "hamilton",
    "queenstown",
}

#: An alias with fewer letters than this can't name a business on its own.
MIN_LETTERS = 4

#: Loaded calls a review may spend on alias reads (the cap is 200 per run).
MAX_ALIAS_READS = 190


def _err(r):
    if isinstance(r, dict) and r.get("error"):
        return str(r["error"])
    return None


def _norm(s):
    """Lower case, punctuation as spaces, single-spaced. No regex in here."""
    out = []
    for ch in str(s or "").lower():
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def _stem(w):
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def _words(s):
    return {_stem(w) for w in _norm(s).split() if len(w) >= 3 and w not in GENERIC}


def _compact(s):
    """The telling words run together: 'Euro Vintage Ltd' and 'Eurovintage'
    both read 'eurovintage'."""
    return "".join(w for w in _norm(s).split() if w not in GENERIC)


def _related(text, others):
    """Does this alias name the same business as any of ``others``?"""
    mine, comp, phrase = _words(text), _compact(text), _norm(text)
    for o in others:
        if mine & _words(o):
            return True
        oc, op = _compact(o), _norm(o)
        if len(comp) >= 4 and len(oc) >= 4 and (comp in oc or oc in comp):
            return True
        # A name made only of generic words ('SERVICE FOODS AUCKLAND') has no
        # telling word to share, so its phrase is compared instead.
        if (
            not mine
            and not _words(o)
            and (phrase in op or op in phrase or set(phrase.split()) & set(op.split()))
        ):
            return True
    return False


def _letters(s):
    return sum(1 for ch in str(s or "") if ch.isalpha())


def _parse(value):
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _list(value):
    """Alias names / ids from a list, a JSON string or one per line. Never
    split on commas: a legal name has them ('Smith, Jones & Co')."""
    v = _parse(value)
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [p.strip() for p in v.split("\n") if p.strip()] if "\n" in v else [v]
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            if isinstance(x, dict):
                x = x.get("id") or x.get("alias") or x.get("name")
            if x is not None:
                out.append(str(x))
        return out
    return [str(v)]


def _shown(name):
    return name if str(name or "").strip() else "(blank)"


def _live(suppliers):
    return [
        s
        for s in suppliers
        if isinstance(s, dict)
        and s.get("id")
        and not s.get("removedAt")
        and not s.get("datestampDeleted")
    ]


def _closest(name, suppliers, n=3):
    want = _words(name) or set(_norm(name).split())
    scored = []
    for s in suppliers:
        have = _words(s.get("name")) or set(_norm(s.get("name")).split())
        common = len(want & have)
        if common:
            scored.append((-common, str(s.get("name"))))
    return [nm for _, nm in sorted(scored)[:n]]


def _find_supplier(wanted, suppliers):
    """(record, None) or (None, error dict)."""
    key = str(wanted or "").strip()
    if not key:
        return None, {"error": "say which supplier — its name or id"}
    for s in suppliers:
        if str(s.get("id")).lower() == key.lower():
            return s, None
    hits = [s for s in suppliers if _norm(s.get("name")) == _norm(key)]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, {
            "error": f"{len(hits)} suppliers are called '{key}' — give the id",
            "ids": [s.get("id") for s in hits],
        }
    return None, {
        "error": f"no supplier at this venue is called '{key}'. Nothing changed.",
        "closest": _closest(key, suppliers),
    }


def _aliases(call_api, venue, supplier_id):
    rows = call_api(
        "loadedhub",
        "get_supplier_aliases",
        {"venue": venue, "supplier_id": supplier_id},
    )
    if _err(rows):
        return None, _err(rows)
    if not isinstance(rows, list):
        return None, "Loaded returned no alias list"
    return [r for r in rows if isinstance(r, dict) and r.get("id")], None


def _flags(supplier, aliases, names_elsewhere, alias_owners, venue):
    """Each alias that looks wrong, with why and what to do."""
    out = []
    own_name = _norm(supplier.get("name"))
    own_telling = _words(supplier.get("name"))
    where = _norm(venue)
    seen = set()
    for a in aliases:
        text = a.get("name")
        key = _norm(text)
        flag = None
        if not key:
            flag = ("blank", "remove", "it has no text, so it identifies nothing")
        elif (
            _letters(text) >= 3
            and where
            and (key in where or where in key)
            and not (own_name in where or where in own_name)
        ):
            flag = (
                "venue_name",
                "remove",
                "it is this venue's own name — the bill-to on an invoice, not "
                "the supplier",
            )
        elif key in seen:
            flag = ("duplicate", "remove", "the same name is on this record twice")
        elif _letters(text) < MIN_LETTERS or (
            not _words(text)
            and own_telling
            and len([w for w in key.split() if len(w) >= 3]) <= 2
        ):
            flag = (
                "too_short",
                "remove",
                "too short or generic to identify a business — it matches "
                "almost any document",
            )
        elif key != own_name and key in names_elsewhere:
            flag = (
                "another_supplier",
                "remove",
                f"it is the name of another supplier here, '{names_elsewhere[key]}'",
            )
        elif len(alias_owners.get(key, ())) > 1:
            others = sorted(n for n in alias_owners[key] if n != supplier.get("name"))
            flag = (
                "on_two_suppliers",
                "check",
                f"it is also an alias of {', '.join(repr(o) for o in others)} — "
                "Loaded can only file it under one, so keep it on the right one",
            )
        elif not _related(
            text,
            [supplier.get("name")] + [b.get("name") for b in aliases if b is not a],
        ):
            flag = (
                "unrelated",
                "check",
                "it shares no word with the supplier's name or its other "
                "aliases — fine if it is a legal or former trading name",
            )
        if key:
            seen.add(key)
        if flag:
            out.append(
                {
                    "supplier": supplier.get("name"),
                    "supplier_id": supplier.get("id"),
                    "alias": _shown(text),
                    "alias_id": a.get("id"),
                    "issue": flag[0],
                    "advice": flag[1],
                    "why": flag[2],
                }
            )
    return out


def _review(params, call_api, venue, suppliers):
    one = params.get("supplier")
    if one:
        rec, problem = _find_supplier(one, suppliers)
        if problem:
            return problem
        targets = [rec]
    else:
        targets = sorted(suppliers, key=lambda s: _norm(s.get("name")))
    skipped = []
    if len(targets) > MAX_ALIAS_READS:
        skipped = [s.get("name") for s in targets[MAX_ALIAS_READS:]]
        targets = targets[:MAX_ALIAS_READS]

    lists = {}
    failed = []
    for s in targets:
        rows, problem = _aliases(call_api, venue, s.get("id"))
        if problem:
            failed.append({"supplier": s.get("name"), "error": problem})
            continue
        lists[s.get("id")] = rows

    names_elsewhere = {_norm(s.get("name")): s.get("name") for s in suppliers}
    alias_owners = {}
    for s in targets:
        for a in lists.get(s.get("id")) or []:
            key = _norm(a.get("name"))
            if key:
                alias_owners.setdefault(key, set()).add(s.get("name"))

    flagged = []
    for s in targets:
        flagged.extend(
            _flags(
                s, lists.get(s.get("id")) or [], names_elsewhere, alias_owners, venue
            )
        )
    flagged.sort(key=lambda f: (f["advice"] != "remove", _norm(f["supplier"])))

    result = {
        "venue": venue,
        "suppliers_checked": len(lists),
        "aliases": sum(len(v) for v in lists.values()),
        "flagged": flagged,
        "to_remove": sum(1 for f in flagged if f["advice"] == "remove"),
        "to_check": sum(1 for f in flagged if f["advice"] == "check"),
    }
    if one:
        rec = targets[0]
        result["supplier"] = {
            "name": rec.get("name"),
            "id": rec.get("id"),
            "aliases": [
                {"alias": _shown(a.get("name")), "alias_id": a.get("id")}
                for a in lists.get(rec.get("id")) or []
            ],
        }
    if skipped:
        result["not_checked"] = skipped
        result["note"] = (
            f"{len(skipped)} supplier(s) not checked in this call — name one "
            "with supplier to check it."
        )
    if failed:
        result["failed"] = failed
    return result


def _remove(params, call_api, venue, suppliers):
    rec, problem = _find_supplier(params.get("supplier"), suppliers)
    if problem:
        return problem
    wanted = _list(params.get("aliases"))
    if not wanted:
        return {
            "error": "which aliases? Give them as review_aliases lists them, or "
            "their alias_id. Nothing changed."
        }
    rows, problem = _aliases(call_api, venue, rec.get("id"))
    if problem:
        return {"error": f"couldn't read {rec.get('name')}'s aliases: {problem}"}

    by_id = {str(a.get("id")).lower(): a for a in rows}
    picked = []
    unknown = []
    for w in wanted:
        hit = by_id.get(w.strip().lower())
        if hit is None:
            key = _norm(w)
            blank = key in ("", "blank")
            hits = [
                a
                for a in rows
                if (not _norm(a.get("name")) if blank else _norm(a.get("name")) == key)
            ]
            hit = hits[0] if hits else None
            # The same text twice is one request per copy, never two for one.
            for h in hits:
                if h not in picked:
                    hit = h
                    break
        if hit is None:
            unknown.append(w)
        elif hit not in picked:
            picked.append(hit)
    if unknown:
        return {
            "error": f"{len(unknown)} alias(es) aren't on {rec.get('name')}. "
            "Nothing changed.",
            "not_found": unknown,
            "aliases": [
                {"alias": _shown(a.get("name")), "alias_id": a.get("id")} for a in rows
            ],
        }

    supplier = rec.get("name")
    preview(
        lambda: {
            "title": "Remove supplier aliases",
            "target": f"{supplier}: {len(picked)} alias(es)",
            "venue": venue,
            "changes": [
                {"field": "alias", "before": _shown(a.get("name")), "after": "removed"}
                for a in picked
            ],
            "warnings": [
                f"Loaded stops filing documents that print these names under "
                f"{supplier}, and Norm stops treating them as its names."
            ],
        },
        basis=sorted(str(a.get("id")) for a in picked),
    )

    failed = []
    for a in picked:
        out = call_api(
            "loadedhub",
            "delete_supplier_alias_raw",
            {"venue": venue, "supplier_id": rec.get("id"), "alias_id": a.get("id")},
        )
        if _err(out):
            failed.append({"alias": _shown(a.get("name")), "error": _err(out)})

    after, problem = _aliases(call_api, venue, rec.get("id"))
    still = []
    if after is not None:
        left = {str(a.get("id")) for a in after}
        still = [_shown(a.get("name")) for a in picked if str(a.get("id")) in left]
    result = {
        "venue": venue,
        "supplier": supplier,
        "removed": [
            _shown(a.get("name"))
            for a in picked
            if after is not None and _shown(a.get("name")) not in still
        ],
        "aliases_now": [_shown(a.get("name")) for a in after or []],
    }
    if after is None:
        result["read_back"] = f"couldn't re-read the aliases to confirm — {problem}"
    if still:
        result["not_removed"] = still
    if failed:
        result["failed"] = failed
        result["error"] = (
            f"Loaded refused to remove {len(failed)} alias(es) from {supplier}"
        )
    return result


def run(params, call_api, log):
    venue = params.get("venue")
    if not venue:
        return {"error": "venue is required — supplier records belong to a venue"}
    op = str(params.get("op") or "review_aliases").strip().lower()
    if op not in OPS:
        return {"error": f"op must be one of {', '.join(OPS)} — got '{op}'"}
    rows = call_api("loadedhub", "get_suppliers", {"venue": venue})
    if not isinstance(rows, list):
        return {
            "error": _err(rows) or f"Loaded didn't return the suppliers for {venue}"
        }
    suppliers = _live(rows)
    if op == "review_aliases":
        return _review(params, call_api, venue, suppliers)
    return _remove(params, call_api, venue, suppliers)
