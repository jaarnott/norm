# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `loadedhub.get_menus` — THE menu lookup
# (installed by scripts/sync_menus_config.py, Sep 2026). Replaces the two raw
# menu reads on the agent surface: list_menus (every menu with every line:
# ~10,600 tokens for La Zeppa's eight menus) and get_menu (one raw MenuModel,
# ~2,000 tokens). Both stay as engine-only backends this code calls.
#
# The model chooses what it wants:
#   (no args)        slim rows — {id, name, sections, lines} for live menus
#   query=           dishes whose name contains the text, across every menu
#                    (and menus whose name does) — "which menu is the espresso
#                    martini on, and at what price?" in one call
#   menu_id=         ONE menu: detail 'summary' (sections → lines with name,
#                    price and what the line points at) or 'full' (Loaded's
#                    raw MenuModel, which the menu editor parses)
#
# Token doctrine: names over ids. A line keeps its own id only because
# manage_menu edits lines by id or name; recipe/stock item ids stay out of
# summaries — get_recipes / get_stock resolve names when needed.
#
# Requires consolidator_config: {"max_api_calls": 2}


def _lower(s):
    return str(s or "").strip().lower()


def _err(r):
    if isinstance(r, dict) and r.get("error"):
        return str(r["error"])
    return None


def _kind(line):
    if line.get("recipeId"):
        return "recipe"
    if line.get("stockItemId"):
        return "stock item"
    return "text"


def _line_summary(line):
    return {
        "id": line.get("id"),
        "name": line.get("name"),
        "price": line.get("workingPrice"),
        "kind": _kind(line),
    }


def _summary(menu):
    sections = []
    for g in menu.get("groups") or []:
        if not isinstance(g, dict):
            continue
        lines = [
            _line_summary(ln)
            for ln in sorted(
                [x for x in (g.get("lines") or []) if isinstance(x, dict)],
                key=lambda x: x.get("lineOrder") or 0,
            )
        ]
        sections.append({"name": g.get("name"), "lines": lines})
    return {
        "id": menu.get("id"),
        "name": menu.get("name"),
        "created": str(menu.get("createdAt") or "")[:10],
        "sections": sections,
        "lines": sum(len(s["lines"]) for s in sections),
    }


def run(params, call_api, log):
    venue = params.get("venue")
    menu_id = params.get("menu_id")
    query = _lower(params.get("query"))
    detail = _lower(params.get("detail")) or "summary"
    limit = int(params.get("limit") or 50)

    if menu_id:
        menu = call_api("loadedhub", "get_menu", {"venue": venue, "menu_id": menu_id})
        if not isinstance(menu, dict) or _err(menu):
            return {"error": _err(menu) or f"menu {menu_id} not found at {venue}"}
        if detail == "full":
            # The raw MenuModel, tagged so the MCP menu editor can claim it.
            out = dict(menu)
            out["detail"] = "full"
            return out
        out = _summary(menu)
        out["detail"] = "summary"
        return out

    menus = call_api("loadedhub", "list_menus", {"venue": venue})
    if not isinstance(menus, list):
        return {"error": _err(menus) or "menu list unavailable"}
    live = [m for m in menus if isinstance(m, dict) and not m.get("deletedAt")]

    if query:
        hits = []
        menu_hits = []
        for m in live:
            if query in _lower(m.get("name")):
                menu_hits.append({"id": m.get("id"), "name": m.get("name")})
            for g in m.get("groups") or []:
                for ln in g.get("lines") or []:
                    if isinstance(ln, dict) and query in _lower(ln.get("name")):
                        hits.append(
                            {
                                "dish": ln.get("name"),
                                "price": ln.get("workingPrice"),
                                "kind": _kind(ln),
                                "menu": m.get("name"),
                                "menu_id": m.get("id"),
                                "section": g.get("name"),
                                "line_id": ln.get("id"),
                            }
                        )
        return {
            "query": params.get("query"),
            "menus_matching": menu_hits,
            "dishes_matching": hits[:limit],
            "total_dishes_matching": len(hits),
            "menus_searched": len(live),
        }

    rows = []
    for m in live:
        groups = [g for g in (m.get("groups") or []) if isinstance(g, dict)]
        rows.append(
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "sections": len(groups),
                "lines": sum(len(g.get("lines") or []) for g in groups),
                "created": str(m.get("createdAt") or "")[:10],
            }
        )
    rows.sort(key=lambda r: _lower(r["name"]))
    return {
        "menus": rows[:limit],
        "total": len(rows),
        "deleted_excluded": len(menus) - len(live),
        "note": (
            "pass menu_id for a menu's sections and dishes, or query (dish or "
            "menu name) to find where something is sold"
        ),
    }
