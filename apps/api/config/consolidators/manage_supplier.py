# ruff: noqa: F821 — sandbox-injected names (json, preview); not imports.
#
# Canonical function_code for `loadedhub.manage_supplier` — a venue's Loaded
# supplier records. Installed by scripts/sync_manage_supplier_config.py,
# Oct 2026. Six ops:
#
#   review_aliases  reads; never asks. Every supplier's aliases (the other
#                   names Loaded files a scanned invoice under a supplier by),
#                   with the ones that look wrong flagged.
#   remove_alias    a write; asks first unless the person always allows it.
#                   DELETE /suppliers/{id}/aliases/{aliasId} -> 204 (seen in
#                   Loaded's own app, 18 Oct 2026).
#   update          a write: name, contact, phone, email, customer number,
#                   address. PUT /suppliers/{id} with the whole record (emails
#                   are replace-all, so the others are sent back as they are).
#   retire /        a write: the same PUT with removedAt set / null. Loaded
#   restore         hides a retired supplier's item variants, so a retire says
#                   which items buy from it.
#   merge           a write: Loaded's OWN merge (PUT loadedhub.com/api/stock/
#                   suppliers/Merge, through the venue's Loaded Legacy login —
#                   what Loaded's supplier screen calls). It moves item
#                   variants, item defaults, orders, invoices and statements to
#                   into_supplier and retires supplier. Aliases and tenders stay
#                   behind; Loaded has no way to add an alias.
#
# The Catalogue page's four (update, retire, restore, merge) came on 23 Oct
# 2026 (docs/plans/stock-catalogue-page.md), each tried on Loaded's test
# company first.
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
#   {"max_api_calls": 200, "allowed_write_actions": ["delete_supplier_alias_raw",
#    "update_supplier_raw", "loaded_legacy.merge_suppliers"]}
#   (review: the supplier list + one alias read per supplier.)

OPS = ("review_aliases", "remove_alias", "update", "retire", "restore", "merge")

_LL = "loaded_legacy"
#: Received invoices are counted back to here.
_HISTORY_FROM = "2000-01-01T00:00:00Z"

#: What op 'update' may change: the name it is given -> Loaded's field.
_EDITABLE = {
    "name": "name",
    "contact": "contactName",
    "contactName": "contactName",
    "contact_name": "contactName",
    "phone": "phone1",
    "phone1": "phone1",
    "phone2": "phone2",
    "fax": "fax",
    "customer_number": "customerNumber",
    "customerNumber": "customerNumber",
    "address1": "address1",
    "address2": "address2",
    "address3": "address3",
    "city": "city",
    "postcode": "postcode",
}
_FIELD_LABELS = {
    "name": "Name",
    "contactName": "Contact",
    "phone1": "Phone",
    "phone2": "Second phone",
    "fax": "Fax",
    "customerNumber": "Customer number",
    "address1": "Address",
    "address2": "Address line 2",
    "address3": "Address line 3",
    "city": "City",
    "postcode": "Postcode",
}

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


# ------------------------------------------------- the catalogue's ops ----


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tomorrow_iso():
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    return t.isoformat()[:19] + "Z"


def _obj(value):
    v = _parse(value)
    return v if isinstance(v, dict) else None


def _rows(value):
    if isinstance(value, dict):
        for key in ("data", "items", "results", "value"):
            if isinstance(value.get(key), list):
                return value[key]
    return value if isinstance(value, list) else []


def _primary_email(rec):
    emails = [e for e in rec.get("emails") or [] if isinstance(e, dict)]
    for e in emails:
        if e.get("isPrimary"):
            return e.get("email")
    return emails[0].get("email") if emails else None


def _with_email(rec, email):
    """The record's emails with the primary one set to ``email`` (blank
    removes it). Loaded's emails are replace-all: the others go back as they
    are."""
    emails = [dict(e) for e in rec.get("emails") or [] if isinstance(e, dict)]
    primary = None
    for e in emails:
        if e.get("isPrimary"):
            primary = e
            break
    if primary is None and emails:
        primary = emails[0]
    email = str(email or "").strip()
    if not email:
        return [e for e in emails if e is not primary]
    if primary is None:
        emails.append({"email": email, "isPrimary": True})
    else:
        primary["email"] = email
        primary["isPrimary"] = True
    return emails


def _re_read(call_api, venue, supplier_id):
    rows = call_api(
        "loadedhub", "get_suppliers", {"venue": venue, "include_deleted": "true"}
    )
    for r in _rows(rows):
        if isinstance(r, dict) and r.get("id") == supplier_id:
            return r
    return None


def _items_buying(call_api, venue, supplier_id):
    """(items with a variant from the supplier, items defaulting to it) — live
    items; None, None when Loaded didn't answer."""
    raw = call_api("loadedhub", "get_stock_items_detail_raw", {"venue": venue})
    if _err(raw) or not isinstance(_rows(raw), list):
        return None, None
    with_variant, defaulting = [], []
    for it in _rows(raw):
        if not isinstance(it, dict) or it.get("datestampRemoved"):
            continue
        if any(
            isinstance(v, dict) and v.get("supplierId") == supplier_id
            for v in it.get("suppliers") or []
        ):
            with_variant.append(it)
        if it.get("defaultSupplierId") == supplier_id:
            defaulting.append(it)
    return with_variant, defaulting


def _open_orders(call_api, venue, supplier_id):
    raw = call_api("loadedhub", "list_purchase_orders", {"venue": venue})
    return [
        po
        for po in _rows(raw)
        if isinstance(po, dict)
        and po.get("supplierId") == supplier_id
        and not po.get("isReceived")
    ]


def _names_of(items, n=6):
    names = sorted(str(i.get("name")) for i in items)
    return ", ".join(names[:n]) + (
        f" and {len(names) - n} more" if len(names) > n else ""
    )


def _update(params, call_api, venue, all_rows):
    live = _live(all_rows)
    rec, problem = _find_supplier(params.get("supplier"), live)
    if problem:
        return problem
    changes = _obj(params.get("changes"))
    if not changes:
        return {
            "error": "op 'update' needs `changes` — e.g. {'name': ..., 'email': ..., "
            "'phone': ..., 'contact': ..., 'customer_number': ...}"
        }
    sent = dict(rec)
    rows = []
    unknown = []
    for key, value in changes.items():
        if key == "email":
            before = _primary_email(rec)
            if str(value or "").strip() != str(before or "").strip():
                sent["emails"] = _with_email(rec, value)
                rows.append(
                    {"field": "Email", "before": before, "after": value or "(none)"}
                )
            continue
        field = _EDITABLE.get(key)
        if not field:
            unknown.append(key)
            continue
        value = None if value is None else str(value).strip()
        if field == "name" and not value:
            return {"error": "a supplier needs a name — nothing changed"}
        if (rec.get(field) or None) != (value or None):
            sent[field] = value or None
            rows.append(
                {
                    "field": _FIELD_LABELS[field],
                    "before": rec.get(field),
                    "after": value,
                }
            )
    if unknown:
        return {
            "error": f"can't change {', '.join(unknown)} — the fields are name, contact, "
            "phone, phone2, fax, email, customer_number, address1-3, city, postcode. "
            "Nothing changed."
        }
    if not rows:
        return {
            "result": "no differences — nothing written",
            "supplier": rec.get("name"),
        }
    if sent.get("name") != rec.get("name"):
        clash = [
            s.get("name")
            for s in live
            if s.get("id") != rec.get("id")
            and _norm(s.get("name")) == _norm(sent["name"])
        ]
        if clash:
            return {
                "error": f"another supplier is already called {clash[0]!r} — merge the "
                "two instead (op 'merge'). Nothing changed."
            }
    preview(
        lambda: {
            "title": "Change a supplier",
            "target": rec.get("name"),
            "venue": venue,
            "changes": rows,
        },
        basis={
            "supplier": rec.get("id"),
            "before": {f: rec.get(f) for f in _FIELD_LABELS},
            "emails": rec.get("emails"),
        },
    )
    out = call_api(
        "loadedhub",
        "update_supplier_raw",
        {"venue": venue, "supplier_id": rec.get("id"), "supplier": sent},
    )
    if _err(out):
        return {"error": f"Loaded refused: {_err(out)}", "supplier": rec.get("name")}
    after = _re_read(call_api, venue, rec.get("id"))
    result = {
        "result": "updated",
        "supplier": sent.get("name"),
        "supplier_id": rec.get("id"),
    }
    if after is None:
        result["read_back"] = "couldn't re-read the supplier to confirm"
    else:
        lost = [
            _FIELD_LABELS[f]
            for f in _FIELD_LABELS
            if (after.get(f) or None) != (sent.get(f) or None)
        ]
        if "emails" in sent and _primary_email(after) != _primary_email(sent):
            lost.append("Email")
        if lost:
            result["not_kept_by_loaded"] = lost
    return result


def _retire_restore(params, call_api, venue, all_rows, retire):
    pool = (
        _live(all_rows)
        if retire
        else [
            r
            for r in all_rows
            if isinstance(r, dict) and r.get("id") and r.get("removedAt")
        ]
    )
    rec, problem = _find_supplier(params.get("supplier"), pool)
    if problem:
        if not retire and "no supplier" in str(problem.get("error")):
            problem["error"] = (
                f"no retired supplier here is called '{params.get('supplier')}'. "
                "Nothing changed."
            )
        return problem
    name = rec.get("name")
    warnings = []
    if retire:
        with_variant, defaulting = _items_buying(call_api, venue, rec.get("id"))
        if with_variant:
            warnings.append(
                f"{len(with_variant)} item(s) buy from {name} ({_names_of(with_variant)}). "
                f"Loaded hides a retired supplier's codes and prices on them — merge "
                f"{name} into another supplier instead to keep them."
            )
        if defaulting:
            warnings.append(
                f"{len(defaulting)} item(s) have {name} as their default supplier."
            )
        if with_variant is None:
            warnings.append("Norm couldn't check which items buy from it.")
        orders = _open_orders(call_api, venue, rec.get("id"))
        if orders:
            warnings.append(
                f"{len(orders)} open order(s) are with {name}: "
                + ", ".join(str(o.get("orderNumber")) for o in orders[:8])
            )
    preview(
        lambda: {
            "title": "Retire a supplier" if retire else "Restore a supplier",
            "target": name,
            "venue": venue,
            "changes": [
                {
                    "field": "Status",
                    "before": "active" if retire else "retired",
                    "after": "retired" if retire else "active",
                }
            ],
            "warnings": warnings,
        },
        basis={"supplier": rec.get("id"), "removed": rec.get("removedAt")},
    )
    sent = dict(rec)
    sent["removedAt"] = _now_iso() if retire else None
    out = call_api(
        "loadedhub",
        "update_supplier_raw",
        {"venue": venue, "supplier_id": rec.get("id"), "supplier": sent},
    )
    if _err(out):
        return {"error": f"Loaded refused: {_err(out)}", "supplier": name}
    after = _re_read(call_api, venue, rec.get("id"))
    result = {
        "result": "retired" if retire else "restored",
        "supplier": name,
        "supplier_id": rec.get("id"),
    }
    if after is None or bool(after.get("removedAt")) != retire:
        result["warning"] = (
            "Loaded answered, but the supplier doesn't read back changed"
        )
    return result


def _merge(params, call_api, venue, all_rows):
    live = _live(all_rows)
    src, p1 = _find_supplier(params.get("supplier"), live)
    if p1:
        return p1
    dst, p2 = _find_supplier(params.get("into_supplier"), live)
    if p2:
        p2["error"] = "into_supplier: " + str(p2.get("error"))
        return p2
    if src.get("id") == dst.get("id"):
        return {"error": "a supplier can't be merged into itself"}
    sname, dname = src.get("name"), dst.get("name")

    with_variant, defaulting = _items_buying(call_api, venue, src.get("id"))
    if with_variant is None:
        return {
            "error": "couldn't read the stock items, so nothing changed — try again"
        }
    orders = _open_orders(call_api, venue, src.get("id"))
    invoiced = call_api(
        "loadedhub",
        "get_invoiced_for_supplier_raw",
        {
            "venue": venue,
            "supplier_id": src.get("id"),
            "from": _HISTORY_FROM,
            "to": _tomorrow_iso(),
        },
    )
    invoices = [i for i in _rows(invoiced) if isinstance(i, dict) and i.get("id")]
    aliases, _problem = _aliases(call_api, venue, src.get("id"))
    tenders = [
        t
        for t in _rows(call_api(_LL, "list_tenders", {"venue": venue}))
        if isinstance(t, dict)
        and t.get("supplierId") == src.get("id")
        and not t.get("datestampDeleted")
    ]

    def card():
        changes = []
        if with_variant:
            changes.append(
                {
                    "field": "Item variants",
                    "before": f"{len(with_variant)} item(s) buy from {sname}",
                    "after": f"they buy from {dname}",
                }
            )
        if defaulting:
            changes.append(
                {
                    "field": "Default supplier",
                    "before": f"{len(defaulting)} item(s): {sname}",
                    "after": dname,
                }
            )
        for o in orders[:20]:
            changes.append(
                {
                    "field": f"Open order {o.get('orderNumber')}",
                    "before": sname,
                    "after": dname,
                }
            )
        if invoices:
            changes.append(
                {
                    "field": "Invoices",
                    "before": f"{len(invoices)} from {sname}",
                    "after": f"from {dname}",
                }
            )
        changes.append({"field": sname, "before": "active", "after": "retired"})
        warnings = []
        if invoices:
            warnings.append(
                f"Loaded files new documents by name: it adds {sname}'s name to "
                f"{dname} as it moves the invoices. Review {dname}'s aliases "
                "afterwards."
            )
        if aliases:
            warnings.append(
                f"{sname}'s {len(aliases)} alias(es) stay on the retired record: "
                + ", ".join(_shown(a.get("name")) for a in aliases[:8])
            )
        if tenders:
            warnings.append(
                f"{len(tenders)} tender(s) with {sname} stay with it — make them again "
                f"for {dname}."
            )
        return {
            "title": "Merge suppliers",
            "target": f"{sname} into {dname}",
            "venue": venue,
            "changes": changes,
            "warnings": warnings,
        }

    preview(
        card,
        basis={
            "supplier": src.get("id"),
            "into": dst.get("id"),
            "items": sorted(i.get("id") for i in with_variant),
            "orders": sorted(o.get("id") for o in orders),
            "invoices": sorted(i.get("id") for i in invoices),
        },
    )
    out = call_api(
        _LL,
        "merge_suppliers",
        {"venue": venue, "source_id": src.get("id"), "dest_id": dst.get("id")},
    )
    if _err(out):
        return {"error": f"Loaded refused the merge: {_err(out)}", "supplier": sname}
    after = _re_read(call_api, venue, src.get("id"))
    result = {
        "result": "merged",
        "supplier": sname,
        "into": dname,
        "moved": {
            "items": len(with_variant),
            "defaults": len(defaulting),
            "orders": len(orders),
            "invoices": len(invoices),
        },
    }
    if invoices:
        result["note"] = (
            f"Review {dname}'s aliases (op review_aliases): Loaded adds the moved "
            "invoices' printed names."
        )
    if not (after and after.get("removedAt")):
        result["warning"] = f"Loaded answered, but {sname} doesn't read back as retired"
    return result


def run(params, call_api, log):
    venue = params.get("venue")
    if not venue:
        return {"error": "venue is required — supplier records belong to a venue"}
    op = str(params.get("op") or "review_aliases").strip().lower()
    if op not in OPS:
        return {"error": f"op must be one of {', '.join(OPS)} — got '{op}'"}
    # Retired ones too: restore needs them, and they must never be picked by
    # name for anything else (_live below).
    rows = call_api(
        "loadedhub", "get_suppliers", {"venue": venue, "include_deleted": "true"}
    )
    if not isinstance(rows, list):
        return {
            "error": _err(rows) or f"Loaded didn't return the suppliers for {venue}"
        }
    suppliers = _live(rows)
    if op == "review_aliases":
        return _review(params, call_api, venue, suppliers)
    if op == "remove_alias":
        return _remove(params, call_api, venue, suppliers)
    if op == "update":
        return _update(params, call_api, venue, rows)
    if op in ("retire", "restore"):
        return _retire_restore(params, call_api, venue, rows, op == "retire")
    return _merge(params, call_api, venue, rows)
