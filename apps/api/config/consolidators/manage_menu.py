# ruff: noqa: F821 — sandbox-injected names (json); not imports.
#
# Canonical function_code for `loadedhub.manage_menu` — THE menu write tool
# (installed by scripts/sync_menus_config.py, Sep 2026). One tool, three ops,
# each reaching the raw write that always did the work:
#
#   create   the full menu                          -> create_menu
#   update   menu_id + deltas, merged server-side   -> update_menu
#            into the whole MenuModel Loaded wants back (its PUT replaces the
#            menu; a field not sent is gone), then RE-READ to prove what landed
#   delete   menu_id                                -> delete_menu (soft)
#
# It absorbs create_menu, update_menu and delete_menu on the agent surface.
# Those asked the model to carry the entire MenuModel — every section and line
# — through its context and echo it back around one price change: expensive,
# and one dropped line away from deleting a dish. The model now sends deltas.
#
# Update deltas, all optional:
#   changes        {"name": ...}
#   line_changes   [{"line_id" | "name" (+ "section" to disambiguate),
#                    "price": ..., "new_name": ..., "move_to": ...}]
#   add_lines      [{"section": ..., "name": ..., "price": ...,
#                    "recipe_id": ... | "stock_item_id": ...}]  (a missing
#                  section is created)
#   remove_lines   ["line_id" | "name", ...]
#   remove_sections ["section name", ...]   (with their lines)
#
# Delete needs the menu's NAME as well as its id, checked against Loaded
# before anything is deleted — the approval card then names what goes, not
# a bare UUID.
#
# Consolidator review (1 Oct 2026): `section` only narrowed a line_changes
# match when line_id was ALSO given — the one case it isn't needed — so two
# same-named dishes could never be told apart; and a price that wasn't a
# plain number ("$12.50", "twelve") became a $0 dish on create and add_lines.
# Prices now accept "$12.50" and refuse anything else.
#
# New lines carry no id (Loaded assigns one). Because a PUT can silently drop
# a line it does not accept, the menu is read back after the write and the
# result says which added lines are actually there — never trust the 200.
#
# Arguments that arrive as JSON STRINGS are parsed (the model does that when a
# field is typed as a string — the manage_stock_item incident, 27 Aug 2026).
#
# The tool's method is PUT so the agent loop's human-approval gate holds; the
# raw writes are declared in allowed_write_actions.
#
# Requires consolidator_config:
#   {"max_api_calls": 3, "allowed_write_actions": ["create_menu", "update_menu",
#    "delete_menu"]}
#   (update = read + PUT + re-read; delete = read + delete.)

_OPS = ("create", "update", "delete")


def _parse(value):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None
    return value


def _obj(v):
    p = _parse(v)
    return p if isinstance(p, dict) else None


def _lst(v):
    p = _parse(v)
    if isinstance(p, dict):
        return [p]
    if isinstance(p, str):
        return [p]
    return p if isinstance(p, list) else None


def _lower(s):
    return str(s or "").strip().lower()


def _err(r):
    if isinstance(r, dict) and r.get("error"):
        return str(r["error"])
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price(v):
    """A menu price: a number, or text like "$12.50" / "1,250"; else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().lstrip("$").replace(",", "").strip()
    return _num(v)


# --------------------------------------------------------------- create ----


def _create(params, venue, call_api, log):
    menu = _obj(params.get("menu"))
    if menu is None:
        return {
            "error": (
                "op 'create' needs `menu`: a JSON object {name, sections:[{name, "
                "lines:[{name, price, recipe_id|stock_item_id}]}]}"
            )
        }
    name = menu.get("name")
    if not name:
        return {"error": "menu needs a name"}
    groups = []
    order = 0
    bad = []
    for s in menu.get("sections") or menu.get("groups") or []:
        if not isinstance(s, dict):
            continue
        lines = []
        for ln in s.get("lines") or []:
            if not isinstance(ln, dict) or not ln.get("name"):
                continue
            price = _price(ln.get("price", ln.get("workingPrice")))
            if price is None:
                bad.append(
                    f"'{ln.get('name')}' has no usable price "
                    f"({ln.get('price', ln.get('workingPrice'))!r}) — pass a number, 0 if free"
                )
                continue
            order += 1
            lines.append(
                {
                    "name": ln.get("name"),
                    "workingPrice": price,
                    "recipeId": ln.get("recipe_id") or ln.get("recipeId"),
                    "stockItemId": ln.get("stock_item_id") or ln.get("stockItemId"),
                    "lineOrder": ln.get("lineOrder") or order,
                    "salesTaxRateId": ln.get("salesTaxRateId"),
                }
            )
        groups.append({"name": s.get("name"), "lines": lines})
    if bad:
        # A menu is created whole or not at all: half a menu with $0 dishes
        # on it is worse than asking again.
        return {"error": "nothing created — " + "; ".join(bad)}
    body = {"name": name, "groups": groups}
    log(f"creating menu '{name}' with {len(groups)} section(s)")
    out = call_api("loadedhub", "create_menu", {"venue": venue, "menu": body})
    if _err(out):
        return {"error": _err(out), "attempted": name}
    return {
        "result": "created",
        "name": name,
        "menu_id": out.get("id") if isinstance(out, dict) else None,
        "sections": len(groups),
        "lines": sum(len(g["lines"]) for g in groups),
    }


# --------------------------------------------------------------- update ----


def _find_line(groups, ref, section=None):
    """(group, line) for a line id or name; None if absent or ambiguous."""
    ref_l = _lower(ref)
    matches = []
    for g in groups:
        if section and _lower(g.get("name")) != _lower(section):
            continue
        for ln in g.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            if str(ln.get("id")) == str(ref) or _lower(ln.get("name")) == ref_l:
                matches.append((g, ln))
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"no line '{ref}'" + (
            f" in section '{section}'" if section else ""
        )
    return None, (f"'{ref}' is on {len(matches)} sections — pass section to say which")


def _find_group(groups, name):
    for g in groups:
        if _lower(g.get("name")) == _lower(name):
            return g
    return None


def _update(params, venue, call_api, log):
    menu_id = params.get("menu_id")
    if not menu_id:
        return {"error": "op 'update' needs menu_id — look the menu up with get_menus"}
    given = {
        "changes": params.get("changes"),
        "line_changes": params.get("line_changes"),
        "add_lines": params.get("add_lines"),
        "remove_lines": params.get("remove_lines"),
        "remove_sections": params.get("remove_sections"),
    }
    parsed = {}
    bad = []
    for k, v in given.items():
        if v in (None, ""):
            parsed[k] = {} if k == "changes" else []
            continue
        p = _obj(v) if k == "changes" else _lst(v)
        if p is None:
            bad.append(k)
        parsed[k] = p or ({} if k == "changes" else [])
    if bad:
        return {
            "error": (
                f"{', '.join(bad)} could not be read as JSON — pass an object "
                "(changes) or a list (the others), not text"
            )
        }
    if not any(parsed.values()):
        return {
            "error": "nothing to change — pass changes, line_changes, add_lines, remove_lines or remove_sections"
        }

    menu = call_api("loadedhub", "get_menu", {"venue": venue, "menu_id": menu_id})
    if not isinstance(menu, dict) or _err(menu):
        return {"error": _err(menu) or f"menu {menu_id} not found at {venue}"}
    groups = [g for g in (menu.get("groups") or []) if isinstance(g, dict)]
    for g in groups:
        g["lines"] = [ln for ln in (g.get("lines") or []) if isinstance(ln, dict)]

    changed = []
    skipped = []

    new_name = (
        parsed["changes"].get("name") if isinstance(parsed["changes"], dict) else None
    )
    for k in parsed["changes"] or {}:
        if k != "name":
            skipped.append(f"changes.{k}: only 'name' can be changed here")
    if new_name and new_name != menu.get("name"):
        changed.append(f"renamed '{menu.get('name')}' -> '{new_name}'")
        menu["name"] = new_name

    for ref in parsed["remove_lines"]:
        ref_v = ref.get("line_id") or ref.get("name") if isinstance(ref, dict) else ref
        sec = ref.get("section") if isinstance(ref, dict) else None
        found, why = _find_line(groups, ref_v, sec)
        if not found:
            skipped.append(f"remove_lines: {why}")
            continue
        g, ln = found
        g["lines"].remove(ln)
        changed.append(f"removed '{ln.get('name')}' from '{g.get('name')}'")

    for lc in parsed["line_changes"]:
        if not isinstance(lc, dict):
            continue
        ref_v = lc.get("line_id") or lc.get("name")
        found, why = _find_line(groups, ref_v, lc.get("section"))
        if not found:
            skipped.append(f"line_changes: {why}")
            continue
        g, ln = found
        if lc.get("price") is not None:
            price = _price(lc["price"])
            if price is None:
                skipped.append(f"line_changes: price '{lc['price']}' is not a number")
            elif price != ln.get("workingPrice"):
                changed.append(
                    f"'{ln.get('name')}' price {ln.get('workingPrice')} -> {price}"
                )
                ln["workingPrice"] = price
        if lc.get("new_name") and lc["new_name"] != ln.get("name"):
            changed.append(f"renamed line '{ln.get('name')}' -> '{lc['new_name']}'")
            ln["name"] = lc["new_name"]
        if lc.get("move_to"):
            target = _find_group(groups, lc["move_to"])
            if target is None:
                skipped.append(f"line_changes: no section '{lc['move_to']}' to move to")
            elif target is not g:
                g["lines"].remove(ln)
                target["lines"].append(ln)
                changed.append(f"moved '{ln.get('name')}' to '{target.get('name')}'")

    # Sections go last, so a line moved out of one first survives.
    for sec in parsed["remove_sections"]:
        g = _find_group(groups, sec)
        if g is None:
            skipped.append(f"remove_sections: no section '{sec}'")
            continue
        groups.remove(g)
        changed.append(
            f"removed section '{g.get('name')}' ({len(g.get('lines') or [])} lines)"
        )

    added = []
    for al in parsed["add_lines"]:
        if not isinstance(al, dict) or not al.get("name"):
            skipped.append("add_lines: a line needs a name")
            continue
        sec_name = al.get("section")
        if not sec_name:
            skipped.append(f"add_lines: '{al['name']}' needs a section")
            continue
        price = _price(al.get("price"))
        if price is None:
            skipped.append(
                f"add_lines: '{al['name']}' has no usable price ({al.get('price')!r}) "
                "— pass a number, 0 if free"
            )
            continue
        g = _find_group(groups, sec_name)
        if g is None:
            g = {"name": sec_name, "lines": []}
            groups.append(g)
            changed.append(f"added section '{sec_name}'")
        orders = [x.get("lineOrder") or 0 for x in g["lines"]]
        g["lines"].append(
            {
                "name": al["name"],
                "workingPrice": price,
                "recipeId": al.get("recipe_id"),
                "stockItemId": al.get("stock_item_id"),
                "lineOrder": (max(orders) + 1) if orders else 1,
                "salesTaxRateId": None,
                "menuId": menu_id,
            }
        )
        added.append((sec_name, al["name"]))
        changed.append(f"added '{al['name']}' to '{sec_name}'")

    if not changed:
        return {
            "menu_id": menu_id,
            "name": menu.get("name"),
            "result": "no differences — nothing written",
            "skipped": skipped,
        }

    menu["groups"] = groups
    log(f"updating menu '{menu.get('name')}': {len(changed)} change(s)")
    out = call_api(
        "loadedhub", "update_menu", {"venue": venue, "menu_id": menu_id, "menu": menu}
    )
    if _err(out):
        return {"error": _err(out), "attempted": changed}

    result = {
        "menu_id": menu_id,
        "name": menu.get("name"),
        "changed": changed,
        "skipped": skipped,
        "result": "updated",
    }
    if added:
        # Prove the new lines landed: Loaded's PUT can drop a line it does
        # not accept while still answering 200.
        after = call_api("loadedhub", "get_menu", {"venue": venue, "menu_id": menu_id})
        present = set()
        if isinstance(after, dict):
            for g in after.get("groups") or []:
                for ln in g.get("lines") or []:
                    if isinstance(ln, dict):
                        present.add((_lower(g.get("name")), _lower(ln.get("name"))))
        landed = [n for s, n in added if (_lower(s), _lower(n)) in present]
        missing = [n for s, n in added if (_lower(s), _lower(n)) not in present]
        result["added_lines_confirmed"] = landed
        if missing:
            result["added_lines_MISSING_after_write"] = missing
            result["result"] = (
                "updated — but Loaded dropped some added lines; see added_lines_MISSING_after_write"
            )
    return result


# --------------------------------------------------------------- delete ----


def _delete(params, venue, call_api, log):
    menu_id = params.get("menu_id")
    name = str(params.get("name") or "").strip()
    if not menu_id or not name:
        return {
            "error": (
                "op 'delete' needs menu_id AND the menu's name (from get_menus) — "
                "the name is checked before anything is deleted"
            )
        }
    menu = call_api("loadedhub", "get_menu", {"venue": venue, "menu_id": menu_id})
    if not isinstance(menu, dict) or _err(menu):
        return {"error": _err(menu) or f"menu {menu_id} not found at {venue}"}
    if _lower(menu.get("name")) != _lower(name):
        return {
            "error": (
                f"menu {menu_id} is '{menu.get('name')}', not '{name}' — nothing "
                "deleted. Check the id with get_menus."
            )
        }
    log(f"deleting menu '{menu.get('name')}' ({menu_id})")
    out = call_api("loadedhub", "delete_menu", {"venue": venue, "menu_id": menu_id})
    if _err(out):
        return {"error": _err(out)}
    return {"result": "deleted", "menu_id": menu_id, "name": menu.get("name")}


# ------------------------------------------------------------------ run ----


def run(params, call_api, log):
    op = _lower(params.get("op"))
    venue = params.get("venue")
    if op not in _OPS:
        return {
            "error": f"op must be one of {', '.join(_OPS)} (got '{params.get('op')}')"
        }
    if op == "create":
        return _create(params, venue, call_api, log)
    if op == "update":
        return _update(params, venue, call_api, log)
    return _delete(params, venue, call_api, log)
