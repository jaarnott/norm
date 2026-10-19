# ruff: noqa: F821 — sandbox-injected names (json); not imports.
#
# Canonical function_code for `loaded_legacy.record_recipe` — create a Loaded
# recipe, or write a change to one. The one recipe write: the agent's tool,
# and what the recipe editor and the claude.ai recipe card save through
# (app/services/recipe_save.py runs it), so there is a single path.
#
# History:
#   - Sep 2026: a wrapper around the Cook Brothers App's (Orbit's)
#     `kitchen_record_recipe`, which held one Loaded login for every venue.
#   - 19 Oct 2026: Loaded Legacy took it over (docs/plans/loaded-legacy-
#     takeover.md) and it writes through Loaded's older API itself:
#       create  POST /wapi/r/StockRecipeVersions — the first version with its
#               recipe in one body, as Loaded's web app sends it;
#       edit    read the recipe and the version, apply the change, PUT
#               /wapi/r/StockRecipeVersions/{id} — the whole version, its
#               recipe nested, ?validUntil= the next version's start.
#
# What it does differently from Orbit's function (read 19 Oct 2026):
#   - Names are matched by Norm's own matcher (`loadedhub.draft_recipe`,
#     app/services/recipe_draft.py — built after a production thread where
#     half of Orbit's lookups failed). A line it isn't sure of stops the write
#     and is named with its candidates; nothing is picked fuzzily.
#   - `merge` merges. Orbit read the lines from the wrong level of its own
#     answer, so merge replaced the recipe with the lines sent.
#   - Allergens aren't written: Loaded keeps none on this path (Orbit kept them
#     in its own food-control-plan table). The result says so.
#
# Loaded's rules it keeps (verified live, 19 Oct 2026 and before):
#   - quantities are stored in base units: display × the unit's ratio;
#   - the recipe nested in a save has no versions, its images are JSON text
#     without their src, and its notes' <img> tags lose their src — what
#     Loaded's web app's save() sends (Mercury recipes.factory.js);
#   - a recipe read naming another company answers 404, so an edit's first
#     read is also the check that the venue's Loaded Legacy company is right;
#   - recipes shared with other companies can't be saved here (500).
#
# Approval: the row's method is POST, so the tool loop asks before it runs;
# for an edit the card lists each line before → after (from the read), and
# the approved run reads again and stops if the recipe moved meanwhile.
#
# Requires consolidator_config: {"max_api_calls": 6, "allowed_write_actions":
#   ["loaded_legacy.create_recipe_version", "loaded_legacy.save_recipe_version"]}

_FIELDS = (
    "recipe_id",
    "version_id",
    "name",
    "notes",
    "yield_quantity",
    "yield_unit",
    "yield_unit_ratio",
    "yield_unit_name",
    "is_counted_in_stocktake",
    "mode",
    "ingredients",
)
_MODES = ("replace", "merge")
_KINDS = ("item", "recipe")
_LL = "loaded_legacy"


def _parse(value):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return value
    return value


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _boolean(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    return None


def _err(result):
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])
    return None


def _check_ingredients(lines):
    """(clean lines, problems)."""
    if isinstance(lines, dict):
        lines = [lines]
    if not isinstance(lines, list):
        return None, ["ingredients must be a list of lines"]
    clean, problems = [], []
    for i, ln in enumerate(lines):
        label = f"ingredients[{i}]"
        if not isinstance(ln, dict):
            problems.append(f"{label} is not an object")
            continue
        ln = dict(ln)
        if "remove" in ln:
            ln["remove"] = _boolean(ln["remove"])
        if ln.get("remove"):
            if not (ln.get("name") or ln.get("ref_id") or ln.get("line_id")):
                problems.append(f"{label} needs a name, ref_id or line_id to remove")
            clean.append(ln)
            continue
        if ln.get("kind") not in _KINDS:
            problems.append(f"{label} needs kind 'item' (stock item) or 'recipe'")
        if not ln.get("unit"):
            problems.append(f"{label} needs a unit")
        quantity = _number(ln.get("quantity"))
        if quantity is None:
            problems.append(f"{label} needs a numeric quantity")
        else:
            ln["quantity"] = quantity
        if not (ln.get("name") or ln.get("ref_id")):
            problems.append(f"{label} needs a name (or ref_id)")
        clean.append(ln)
    return clean, problems


def _settled(line):
    """A line that already names its stock item or sub-recipe and its unit with
    the unit's ratio (what the recipe editor sends) needs no matching."""
    return bool(
        line.get("ref_id") and line.get("unit") and _number(line.get("unit_ratio"))
    )


# ------------------------------------------------------------- matching ----


def _match(params, body, lines, call_api):
    """Settle names and units with Norm's matcher. Returns (lines with
    ref_id/unit/unit_ratio, yield unit or None, the recipe already called
    this name or None, problems)."""
    want_yield = bool(body.get("yield_unit")) and not _number(
        body.get("yield_unit_ratio")
    )
    todo = [
        i for i, ln in enumerate(lines) if not ln.get("remove") and not _settled(ln)
    ]
    if not todo and not want_yield:
        return lines, None, None, []
    draft = call_api(
        "loadedhub",
        "draft_recipe",
        {
            "venue": params.get("venue"),
            "venue_id": params.get("venue_id"),
            "recipes": [
                {
                    "name": body.get("name") or "recipe",
                    "yield_quantity": body.get("yield_quantity") or 1,
                    "yield_unit": body.get("yield_unit") or "",
                    "lines": [
                        {
                            "kind": ln.get("kind"),
                            "name": ln.get("name"),
                            "ref_id": ln.get("ref_id"),
                            "quantity": ln.get("quantity"),
                            "unit": ln.get("unit"),
                        }
                        for ln in (lines[i] for i in todo)
                    ],
                }
            ],
        },
    )
    if _err(draft):
        return None, None, None, [f"couldn't match the ingredients: {_err(draft)}"]
    drafts = draft.get("recipes") if isinstance(draft, dict) else None
    d = drafts[0] if isinstance(drafts, list) and drafts else {}
    got = d.get("lines") or []
    problems = []
    out = list(lines)
    for i, m in zip(todo, got):
        ln = lines[i]
        status = (m.get("match") or {}).get("status")
        if status != "matched":
            why = "; ".join((m.get("match") or {}).get("reasons") or []) or "no match"
            cands = [
                c.get("name") for c in (m.get("match") or {}).get("candidates") or []
            ]
            problems.append(
                f"'{ln.get('name') or ln.get('ref_id')}': {why}"
                + (
                    f" (candidates: {', '.join(str(c) for c in cands)})"
                    if cands
                    else ""
                )
            )
            continue
        out[i] = {
            **ln,
            "kind": m.get("kind") or ln.get("kind"),
            "ref_id": m.get("ref_id"),
            "name": m.get("name") or ln.get("name"),
            "unit": m.get("unit_id"),
            "unit_name": m.get("unit_name"),
            "unit_ratio": m.get("unit_ratio"),
            "quantity": m.get("quantity"),
        }
    if len(got) != len(todo):
        problems.append("the matcher didn't answer for every line")
    yunit = None
    if want_yield:
        if d.get("yield_unit_id"):
            yunit = {
                "id": d["yield_unit_id"],
                "name": d.get("yield_unit_name"),
                "ratio": d.get("yield_unit_ratio") or 1,
            }
        else:
            problems.append(
                f"no Loaded unit called '{body.get('yield_unit')}' for the yield"
            )
    return out, yunit, d.get("duplicate_of"), problems


# --------------------------------------------------------------- Loaded ----


def _web_line(ln, version_id, old=None):
    """One version line as Loaded's web app sends it. An existing line keeps
    its Loaded fields (and id); quantity in base units."""
    ratio = _number(ln.get("unit_ratio")) or 1
    item = ln.get("kind") != "recipe"
    out = dict(old or {})
    out.update(
        {
            "versionId": version_id,
            "itemId": ln["ref_id"] if item else None,
            "stockItemId": ln["ref_id"] if item else None,
            "itemName": ln.get("name") if item else None,
            "stockItemName": ln.get("name") if item else None,
            "recipeId": None if item else ln["ref_id"],
            "recipeName": None if item else ln.get("name"),
            "unitId": ln.get("unit"),
            "unitName": ln.get("unit_name") or (old or {}).get("unitName"),
            "unitRatio": ratio,
            "quantity": round(float(ln["quantity"]) * ratio, 9),
            "deletedAt": None,
        }
    )
    if not version_id:
        out.pop("versionId", None)
    return out


def _old_ref(old):
    return old.get("stockItemId") or old.get("itemId") or old.get("recipeId")


def _old_name(old):
    return old.get("stockItemName") or old.get("itemName") or old.get("recipeName")


def _find_old(ln, pool, used):
    """The existing line this one changes: by line id, then by what it is,
    then by name."""
    tests = (
        lambda o: ln.get("line_id") and o.get("id") == ln["line_id"],
        lambda o: ln.get("ref_id") and _old_ref(o) == ln["ref_id"],
        lambda o: (
            (ln.get("name") or "").strip().lower()
            and str(_old_name(o) or "").strip().lower()
            == (ln.get("name") or "").strip().lower()
        ),
    )
    for test in tests:
        for i, o in enumerate(pool):
            if i not in used and test(o):
                return i
    return None


def _apply_lines(version, lines, mode, version_id):
    """The version's new lines. replace: exactly the lines sent (an existing
    line keeps its id when it is the same item or sub-recipe). merge: the
    lines sent change or add, `remove` drops, every other line stays."""
    pool = [o for o in version.get("lines") or [] if isinstance(o, dict)]
    used = set()
    out = []
    changed = {}
    for ln in lines:
        k = _find_old(ln, pool, used)
        if ln.get("remove"):
            if k is not None:
                used.add(k)
            continue
        if k is not None:
            used.add(k)
            changed[k] = _web_line(ln, version_id, pool[k])
        else:
            out.append(_web_line(ln, version_id))
    if mode == "merge":
        kept = []
        for i, o in enumerate(pool):
            if i in changed:
                kept.append(changed[i])
            elif i not in used:
                kept.append(o)
        out = kept + out
    else:
        out = [changed[i] for i in sorted(changed)] + out
    for order, line in enumerate(out):
        line["lineOrder"] = order
    return out


def _web_recipe(rec):
    """The recipe as Loaded's web app nests it in a save: no versions, images
    as JSON text without their src, the notes' <img> tags without src."""
    w = {k: v for k, v in rec.items() if k not in ("versions", "currentVersion")}
    images = rec.get("imagesJson")
    if isinstance(images, str):
        try:
            images = json.loads(images) if images else []
        except ValueError:
            images = []
    images = images if isinstance(images, list) else []
    clean = []
    for im in images:
        if isinstance(im, dict):
            im = {k: v for k, v in im.items() if k != "src"}
        clean.append(im)
    w["imagesJson"] = json.dumps(clean)
    main = rec.get("mainImageJson")
    if isinstance(main, str) and main:
        try:
            main = json.loads(main)
        except ValueError:
            main = None
    if isinstance(main, dict):
        w["mainImageJson"] = json.dumps({k: v for k, v in main.items() if k != "src"})
    if isinstance(w.get("notes"), str):
        w["notes"] = _strip_img_src(w["notes"])
    return w


def _strip_img_src(html):
    """<img ... src="..."> → <img ...> (the sandbox has no re)."""
    out, i = [], 0
    while True:
        j = html.find("<img", i)
        if j < 0:
            out.append(html[i:])
            return "".join(out)
        end = html.find(">", j)
        if end < 0:
            out.append(html[i:])
            return "".join(out)
        out.append(html[i:j])
        tag = html[j : end + 1]
        s = tag.find(' src="')
        if s >= 0:
            e = tag.find('"', s + 6)
            if e >= 0:
                tag = tag[:s] + tag[e + 1 :]
        out.append(tag)
        i = end + 1


def _versions(rec):
    vs = [v for v in rec.get("versions") or [] if isinstance(v, dict)]
    return sorted(
        vs,
        key=lambda v: (_number(v.get("versionNo")) or 0, str(v.get("validFrom") or "")),
    )


def _read(body, call_api):
    """(recipe, version, valid_until) as Loaded has them now — or an error."""
    rec = call_api(_LL, "get_recipe", {"recipe_id": body["recipe_id"]})
    if _err(rec) or not isinstance(rec, dict) or not rec.get("id"):
        return None, (
            f"Couldn't read recipe {body['recipe_id']} through Loaded Legacy "
            f"({_err(rec) or 'nothing came back'}). If the recipe exists, the venue's "
            "Loaded company may be wrong — check it in Settings → Venues."
        )
    versions = _versions(rec)
    vid = body.get("version_id") or (rec.get("currentVersion") or {}).get("id")
    if not vid and versions:
        vid = versions[-1].get("id")
    idx = None
    for i, v in enumerate(versions):
        if v.get("id") == vid:
            idx = i
    if idx is None:
        return None, (
            "That version of the recipe isn't in Loaded any more — reopen the "
            "recipe and try again."
        )
    valid_until = (
        versions[idx + 1].get("validFrom") if idx + 1 < len(versions) else None
    )
    ver = call_api(_LL, "get_recipe_version", {"version_id": vid})
    if _err(ver) or not isinstance(ver, dict):
        return (
            None,
            f"Couldn't read the recipe's version ({_err(ver) or 'nothing came back'}).",
        )
    return (rec, ver, valid_until), None


def _yield(target, body, yunit, current=None):
    """Write the yield (Loaded's spelling, 'yeild', kept) in base units."""
    if yunit is None and "yield_quantity" not in body:
        return
    cur = current or {}
    if yunit is None:
        yunit = {
            "id": cur.get("yeildUnitId"),
            "name": cur.get("yeildUnitName"),
            "ratio": _number(cur.get("yeildUnitRatio")) or 1,
        }
    qty = body.get("yield_quantity")
    if qty is None:
        qty = (_number(cur.get("yeildQuantity")) or 0) / (
            _number(cur.get("yeildUnitRatio")) or 1
        )
    ratio = _number(yunit.get("ratio")) or 1
    for spelling in ("yeild", "yield"):
        target[spelling + "UnitId"] = yunit.get("id")
        target[spelling + "UnitName"] = yunit.get("name")
        target[spelling + "UnitRatio"] = ratio
        target[spelling + "Quantity"] = round(float(qty) * ratio, 9)


def _kept(after, sent):
    """Did Loaded keep the lines it was sent?"""
    got = [ln for ln in (after or {}).get("lines") or [] if isinstance(ln, dict)]
    want = sent.get("lines") or []
    if len(got) != len(want):
        return False

    def key(x):
        return (
            str(_old_ref(x)),
            str(x.get("unitId")),
            round(_number(x.get("quantity")) or 0, 6),
        )

    return sorted(key(x) for x in got) == sorted(key(x) for x in want)


# ------------------------------------------------------------------ run ----


def run(params, call_api, log):
    body = {k: params[k] for k in _FIELDS if params.get(k) not in (None, "")}
    notes_out = []
    if params.get("allergens") not in (None, "", []):
        notes_out.append(
            "Allergens weren't saved: Loaded keeps no allergen statuses on recipes."
        )
    editing = bool(body.get("recipe_id"))
    if not editing and not body.get("name"):
        return {"error": "A new recipe needs a name (or pass recipe_id to edit one)."}

    problems = []
    if "ingredients" in body:
        body["ingredients"], found = _check_ingredients(_parse(body["ingredients"]))
        problems += found
    if "yield_quantity" in body:
        body["yield_quantity"] = _number(body["yield_quantity"])
        if body["yield_quantity"] is None:
            problems.append("yield_quantity must be a number")
    if "is_counted_in_stocktake" in body:
        body["is_counted_in_stocktake"] = _boolean(body["is_counted_in_stocktake"])
        if body["is_counted_in_stocktake"] is None:
            problems.append("is_counted_in_stocktake must be true or false")
    mode = str(body.get("mode") or "").strip().lower()
    if mode and mode not in _MODES:
        problems.append("mode must be 'merge' or 'replace'")
    if editing and "ingredients" in body and not mode:
        # Sending only the changed line must never silently delete the rest.
        problems.append(
            "editing a recipe's ingredients needs mode: 'merge' changes or adds "
            "the lines you send and keeps the rest; 'replace' makes your list "
            "the whole recipe"
        )
    if not editing:
        mode = "replace"
        if not body.get("yield_unit"):
            problems.append("a new recipe needs a yield_unit (and yield_quantity)")
        if not [ln for ln in body.get("ingredients") or [] if not ln.get("remove")]:
            problems.append("a new recipe needs its ingredient lines")
    if problems:
        return {"error": "nothing written — " + "; ".join(problems)}

    lines, yunit, same_name, problems = _match(
        params, body, body.get("ingredients") or [], call_api
    )

    def _same(a, b):
        return " ".join(str(a or "").lower().split()) == " ".join(
            str(b or "").lower().split()
        )

    if not editing and same_name and _same(same_name.get("name"), body.get("name")):
        # 2–3 Oct 2026: Orbit's create made several recipes twice in one thread.
        problems.append(
            f"a recipe called {same_name.get('name') or body.get('name')!r} already "
            f"exists (recipe_id {same_name.get('id')}) — edit it with recipe_id, or "
            "give the new recipe a different name"
        )
    if problems:
        return {"error": "nothing written — " + "; ".join(problems)}
    if (
        yunit is None
        and body.get("yield_unit")
        and _number(body.get("yield_unit_ratio"))
    ):
        yunit = {
            "id": body["yield_unit"],
            "name": body.get("yield_unit_name"),
            "ratio": _number(body["yield_unit_ratio"]),
        }

    if not editing:
        return _create(body, lines, yunit, call_api, log, notes_out)

    now, rerr = _read(body, call_api)
    if rerr:
        return {"error": rerr}
    rec, ver, valid_until = now
    version_id = ver.get("id") or body.get("version_id")
    sent = {k: v for k, v in ver.items()}
    if "ingredients" in body:
        sent["lines"] = _apply_lines(ver, lines, mode, version_id)
    _yield(sent, body, yunit, ver)
    recipe = _web_recipe(rec)
    if body.get("name"):
        recipe["name"] = str(body["name"]).strip()
    if "notes" in body:
        recipe["notes"] = _strip_img_src(str(body["notes"]))
    if "is_counted_in_stocktake" in body:
        recipe["stocktake"] = body["is_counted_in_stocktake"]
    sent["recipe"] = recipe

    preview(
        lambda: _card(rec, ver, sent, body, mode),
        basis={"version": ver, "name": rec.get("name"), "notes": rec.get("notes")},
    )
    log(
        f"record_recipe: edit {rec.get('name')!r}, {len(sent.get('lines') or [])} line(s)"
        + (f", mode {mode}" if "ingredients" in body else "")
    )
    out = call_api(
        _LL,
        "save_recipe_version",
        {"version_id": version_id, "valid_until": valid_until or "", "body": sent},
    )
    if _err(out):
        if rec.get("isShared"):
            return {
                "error": (
                    "Loaded refused the save. This recipe is shared with other "
                    "companies, and Loaded's older API doesn't save shared recipes — "
                    "change it in Loaded."
                )
            }
        return {"error": f"Loaded refused the save: {_err(out)}"}
    if "ingredients" in body:
        after = call_api(_LL, "get_recipe_version", {"version_id": version_id})
        if _err(after) or not _kept(after, sent):
            return {
                "error": (
                    "Loaded accepted the save but the recipe doesn't show the change — "
                    "reopen the recipe to check it."
                )
            }
    result = {
        "saved": True,
        "created": False,
        "recipe_id": rec.get("id"),
        "version_id": version_id,
        "name": recipe.get("name"),
        "lines": len(sent.get("lines") or []),
    }
    if notes_out:
        result["note"] = " ".join(notes_out)
    return result


def _create(body, lines, yunit, call_api, log, notes_out):
    sent = {
        "versionNo": 1,
        "lines": [],
        "recipe": {
            "imagesJson": "[]",
            "name": str(body["name"]).strip(),
            "notes": _strip_img_src(str(body.get("notes") or "")),
            "stocktake": bool(body.get("is_counted_in_stocktake")),
        },
    }
    sent["lines"] = _apply_lines({"lines": []}, lines, "replace", None)
    _yield(sent, {**body, "yield_quantity": body.get("yield_quantity") or 1}, yunit)
    preview(lambda: _card({}, {}, sent, body, "replace"))
    log(
        f"record_recipe: create {sent['recipe']['name']!r}, {len(sent['lines'])} line(s)"
    )
    out = call_api(_LL, "create_recipe_version", {"body": sent})
    if _err(out):
        return {"error": f"Loaded refused the new recipe: {_err(out)}"}
    recipe_id, version_id = _created(sent["recipe"]["name"], call_api, log)
    result = {
        "saved": True,
        "created": True,
        "recipe_id": recipe_id,
        "version_id": version_id,
        "name": sent["recipe"]["name"],
        "lines": len(sent["lines"]),
    }
    if notes_out:
        result["note"] = " ".join(notes_out)
    return result


def _created(name, call_api, log):
    """Loaded answers a create with 204 and no ids (verified in its test
    environment, 19 Oct 2026): the new recipe is the newest live one with this
    name, and its version is that recipe's current one."""
    quoted = str(name).replace("'", "''")
    found = call_api(
        _LL,
        "find_recipes",
        {"filter": f"Name eq '{quoted}' and DatestampDeleted eq null"},
    )
    rows = [
        r for r in (found if isinstance(found, list) else []) if isinstance(r, dict)
    ]
    if not rows:
        log(f"record_recipe: created {name!r} but couldn't find it by name")
        return None, None
    rows.sort(key=lambda r: str(r.get("datestampCreated") or ""), reverse=True)
    rec = call_api(_LL, "get_recipe", {"recipe_id": rows[0].get("id")})
    current = (rec.get("currentVersion") if isinstance(rec, dict) else None) or {}
    return rows[0].get("id"), current.get("id")


# ------------------------------------------------------------- preview ----


def _plain_text(markup, limit=120):
    """Recipe notes are HTML; the card shows the words."""
    out, inside = [], False
    for ch in str(markup or ""):
        if ch == "<":
            inside = True
        elif ch == ">":
            inside = False
            out.append(" ")
        elif not inside:
            out.append(ch)
    text = " ".join("".join(out).replace("&amp;", "&").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _amount(quantity, unit):
    if quantity is None:
        return "—"
    if isinstance(quantity, float) and quantity == int(quantity):
        quantity = int(quantity)
    return f"{quantity} {unit or ''}".strip()


def _display(line):
    ratio = _number(line.get("unitRatio")) or 1
    qty = (_number(line.get("quantity")) or 0) / ratio
    return _amount(round(qty, 4), line.get("unitName"))


def _yield_text(v):
    if not v or v.get("yeildUnitId") is None:
        return None
    ratio = _number(v.get("yeildUnitRatio")) or 1
    return _amount(
        round((_number(v.get("yeildQuantity")) or 0) / ratio, 4), v.get("yeildUnitName")
    )


def _card(rec, ver, sent, body, mode):
    """What this call changes, for the approval card."""
    editing = bool(rec)
    changes, warnings = [], []

    def change(field, before, after):
        row = {"field": field, "after": after}
        if editing:
            row["before"] = before
        changes.append(row)

    new = sent.get("recipe") or {}
    if new.get("name") != rec.get("name"):
        change("Name", rec.get("name"), new.get("name"))
    if "notes" in body and new.get("notes") != rec.get("notes"):
        change(
            "Method / notes",
            _plain_text(rec.get("notes")),
            _plain_text(new.get("notes")),
        )
    if _yield_text(sent) != _yield_text(ver):
        change("Yield", _yield_text(ver), _yield_text(sent))
    if "is_counted_in_stocktake" in body and new.get("stocktake") != rec.get(
        "stocktake"
    ):
        change(
            "Counted in stocktake",
            None
            if rec.get("stocktake") is None
            else ("Yes" if rec.get("stocktake") else "No"),
            "Yes" if new.get("stocktake") else "No",
        )
    if "ingredients" in body:
        before = {}
        for o in ver.get("lines") or []:
            before.setdefault(str(_old_ref(o)), o)
        after_refs = set()
        for ln in sent.get("lines") or []:
            ref = str(_old_ref(ln))
            after_refs.add(ref)
            old = before.get(ref)
            name = _old_name(ln) or ref
            if old is None:
                changes.append(
                    {"field": f"Ingredient · {name}", "after": _display(ln) + " (new)"}
                )
            elif _display(old) != _display(ln) or old.get("unitId") != ln.get("unitId"):
                change(f"Ingredient · {name}", _display(old), _display(ln))
        dropped = [o for ref, o in before.items() if ref not in after_refs]
        for o in dropped:
            change(f"Ingredient · {_old_name(o)}", _display(o), "Removed")
        if dropped and mode == "replace":
            warnings.append(
                f"Replace: the recipe becomes exactly the {len(sent.get('lines') or [])} "
                f"line(s) sent, so {len(dropped)} current line(s) are removed."
            )
    return {
        "title": "Change a recipe" if editing else "Create a recipe",
        "target": new.get("name") or rec.get("name") or body.get("recipe_id"),
        "changes": changes,
        "warnings": warnings,
    }
