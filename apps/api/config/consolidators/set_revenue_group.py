# ruff: noqa: F821 — sandbox-injected names (json, preview); not imports.
#
# Canonical function_code for `loadedhub.set_revenue_group` — put POS items
# into a revenue group (Food, Beverage, Other) or take them out (Unassigned).
# Installed by scripts/sync_revenue_groups_config.py, Oct 2026; plan:
# docs/plans/pos-revenue-groups.md. The one revenue-group tool: Norm finds the
# items with get_sales (breakdown 'items' — each row's group and category, by
# sales; category 'Uncategorised' lists the ones to sort) and passes their names.
# Those names are the POS items' own (checked 17 Oct 2026: all 642 item names
# sold in a week at three venues are in /api/pos/items exactly).
#
# Loaded's API, checked against its source (ProjectCyrus
# ItemRevenueGroupController) and on its test environment, 17 Oct 2026:
#   PUT /api/pos/items/revenue-group  {"posItemIds": [...], "revenueGroupName": "Food"}
#   200 echoes the body; null clears. Loaded saves the ids ONE AT A TIME and
#   answers an id it doesn't know with a bare 500 — so the ids before it are
#   already changed. Every id sent here comes from the read just made.
#   The group names are case-insensitive; anything else is a 400.
#
# Which items:
#   items        names or posIdentifiers. A name matches every LIVE item with
#                exactly that name (case and spacing ignored) — the same dish
#                sold at three prices moves together. Any name that matches
#                nothing stops the whole call (nothing written) and comes back
#                with the closest names, so a typo never half-applies.
#   pos_category every live item in that POS category; only_unassigned
#                (default true here) leaves items already in a group alone.
#   assignments  [{revenue_group, items | pos_category}] — several groups in
#                one call, so a whole categorisation pass is ONE approval.
# Items already in the group they're being put in are skipped and counted.
#
# Approval (services/previews.py): preview(...) shows every item it changes,
# before → after, with the warning that Loaded reports them under the group
# from now on. The fingerprint covers each item's group at preview time, so an
# approval never lands on items that moved since. Someone who switched the
# tool to "always allow" gets no card: the call simply runs (owner, 17 Oct
# 2026: Norm sets them without asking once that's on).
#
# After writing it reads the items back and reports any Loaded didn't keep.
#
# Requires consolidator_config:
#   {"max_api_calls": 20, "allowed_write_actions": ["set_pos_items_revenue_group_raw"]}
#   (read + one PUT per group per 200 items + read back.)

GROUPS = ("Food", "Beverage", "Other")
UNASSIGNED = "Unassigned"
CHUNK = 200


def _lower(s):
    return " ".join(str(s or "").strip().lower().split())


def _parse(value):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text[:1] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _names(value):
    """A list of item names / ids from a list, a JSON string or one name per
    line. Never split on commas: dish names have them ("Fish, chips & salad")."""
    v = _parse(value)
    if v is None:
        return []
    if isinstance(v, str):
        return (
            [p.strip() for p in v.split("\n") if p.strip()]
            if "\n" in v
            else [v.strip()]
        )
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            if isinstance(x, dict):
                x = x.get("name") or x.get("id") or x.get("posIdentifier")
            if x is not None and str(x).strip():
                out.append(str(x).strip())
        return out
    return [str(v).strip()]


def _bool(v, default):
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return _lower(v) in ("1", "true", "yes", "y")


def _err(r):
    if isinstance(r, dict) and r.get("error"):
        return str(r["error"])
    return None


def _group(value):
    v = _lower(value)
    for g in GROUPS:
        if v == g.lower():
            return g
    if v in ("unassigned", "none", "unset", "no group", "clear"):
        return UNASSIGNED
    return None


def group_of(item):
    g = item.get("revenueGroup")
    return g if g in GROUPS else UNASSIGNED


def _money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def _label(item):
    price = _money(item.get("price"))
    return f"{item.get('name')} ({price})" if price else str(item.get("name"))


def _closest(name, live, n=3):
    """Live item names sharing the most words with ``name`` — no regex in the
    sandbox, and difflib isn't there either."""
    want = set(_lower(name).split())
    if not want:
        return []
    scored = {}
    for i in live:
        nm = str(i.get("name") or "")
        have = set(_lower(nm).split())
        common = len(want & have)
        if common:
            scored[nm] = max(scored.get(nm, 0), common / len(want | have))
    best = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [nm for nm, _ in best[:n]]


def _assignments(params):
    """[{group, items, pos_category, only_unassigned}] from either calling style."""
    raw = _parse(params.get("assignments"))
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    if isinstance(raw, list) and raw:
        for a in raw:
            if isinstance(a, dict):
                out.append(a)
    if (
        params.get("revenue_group") is not None
        or params.get("items")
        or params.get("pos_category")
    ):
        out.append(
            {
                "revenue_group": params.get("revenue_group"),
                "items": params.get("items"),
                "pos_category": params.get("pos_category"),
                "only_unassigned": params.get("only_unassigned"),
            }
        )
    return out


def run(params, call_api, log):
    venue = params.get("venue")
    if not venue:
        return {"error": "venue is required — revenue groups are set per venue"}
    wanted = _assignments(params)
    if not wanted:
        return {
            "error": "say which items: revenue_group with items or pos_category, "
            "or assignments [{revenue_group, items | pos_category}]"
        }

    # Settle the request before reading anything from Loaded.
    bad_groups = sorted(
        {
            str(a.get("revenue_group"))
            for a in wanted
            if _group(a.get("revenue_group")) is None
        }
    )
    if bad_groups:
        return {
            "error": f"revenue_group must be one of {', '.join(GROUPS)} or "
            f"{UNASSIGNED} — got {', '.join(bad_groups)}. Nothing changed."
        }
    for a in wanted:
        if not _names(a.get("items")) and not _lower(a.get("pos_category")):
            return {
                "error": f"which items should go in {_group(a.get('revenue_group'))}? "
                "Give items or pos_category. Nothing changed."
            }

    items = call_api("loadedhub", "get_pos_items_raw", {"venue": venue})
    if not isinstance(items, list):
        return {
            "error": _err(items) or f"Loaded didn't return the POS items for {venue}"
        }
    live = [i for i in items if isinstance(i, dict) and not i.get("whenDeleted")]
    by_id = {
        str(i.get("posIdentifier")).lower(): i for i in live if i.get("posIdentifier")
    }
    by_name = {}
    for i in live:
        by_name.setdefault(_lower(i.get("name")), []).append(i)

    target = {}  # posIdentifier -> group (the last assignment naming it wins)
    unknown = []
    for a in wanted:
        group = _group(a.get("revenue_group"))
        names = _names(a.get("items"))
        category = _lower(a.get("pos_category"))
        picked = []
        for nm in names:
            hit = by_id.get(nm.lower())
            if hit is not None:
                picked.append(hit)
                continue
            hits = by_name.get(_lower(nm))
            if hits:
                picked.extend(hits)
            else:
                unknown.append(nm)
        if category:
            only_unassigned = _bool(a.get("only_unassigned"), True)
            for i in live:
                if _lower(i.get("itemCategoryName")) != category:
                    continue
                if only_unassigned and group_of(i) != UNASSIGNED:
                    continue
                picked.append(i)
        for i in picked:
            target[str(i.get("posIdentifier"))] = group

    if unknown:
        return {
            "error": f"{len(unknown)} item name(s) match no live POS item at {venue}. "
            "Nothing changed — fix the names and call again.",
            "not_found": [
                {"name": nm, "closest": _closest(nm, live)} for nm in unknown[:40]
            ],
        }

    changes = []
    already = 0
    for pid, group in target.items():
        item = by_id.get(pid.lower())
        if item is None:
            continue
        if group_of(item) == group:
            already += 1
            continue
        changes.append(
            {"id": pid, "item": item, "before": group_of(item), "after": group}
        )
    changes.sort(key=lambda c: (c["after"], _lower(c["item"].get("name"))))

    if not changes:
        return {
            "venue": venue,
            "changed": 0,
            "already_in_group": already,
            "note": "Nothing to change — every item named is already in that group.",
        }

    per_group = {}
    for c in changes:
        per_group[c["after"]] = per_group.get(c["after"], 0) + 1
    counts = ", ".join(f"{n} to {g}" for g, n in sorted(per_group.items()))
    basis = sorted([c["id"], c["before"], c["after"]] for c in changes)
    preview(
        lambda: {
            "title": "Set revenue groups",
            "target": f"{len(changes)} POS item(s): {counts}",
            "venue": venue,
            "changes": [
                {"field": _label(c["item"]), "before": c["before"], "after": c["after"]}
                for c in changes
            ],
            "warnings": [
                "Loaded reports these items under their revenue group from now "
                "on — sales and cost of goods by category change with them."
            ],
            "note": f"{already} already in that group, left as they are."
            if already
            else None,
        },
        basis=basis,
    )

    written = 0
    failed = []
    for group in sorted(per_group):
        ids = [c["id"] for c in changes if c["after"] == group]
        name = None if group == UNASSIGNED else group
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            out = call_api(
                "loadedhub",
                "set_pos_items_revenue_group_raw",
                {
                    "venue": venue,
                    "body": {"posItemIds": chunk, "revenueGroupName": name},
                },
            )
            if _err(out):
                failed.append({"group": group, "items": len(chunk), "error": _err(out)})
                log(f"{group}: {len(chunk)} item(s) failed — {_err(out)}")
            else:
                written += len(chunk)

    # Read back: Loaded's bulk PUT echoes the request, which proves nothing.
    after = call_api("loadedhub", "get_pos_items_raw", {"venue": venue})
    not_kept = []
    if isinstance(after, list):
        now = {str(i.get("posIdentifier")): i for i in after if isinstance(i, dict)}
        for c in changes:
            i = now.get(c["id"])
            if i is None or group_of(i) != c["after"]:
                not_kept.append(
                    {
                        "item": c["item"].get("name"),
                        "wanted": c["after"],
                        "now": group_of(i) if i else "missing",
                    }
                )
        confirmed = len(changes) - len(not_kept)
    else:
        confirmed = None

    result = {
        "venue": venue,
        "changed": confirmed if confirmed is not None else written,
        "by_group": per_group,
        "already_in_group": already,
        "note": "Loaded reports these items under their revenue group from now on.",
    }
    if confirmed is None:
        result["read_back"] = "couldn't re-read the items to confirm — " + (
            _err(after) or "no answer"
        )
    if not_kept:
        result["not_kept"] = not_kept[:40]
        result["not_kept_count"] = len(not_kept)
    if failed:
        result["failed"] = failed
        result["error"] = f"Loaded refused part of the change at {venue}: " + "; ".join(
            f"{f['group']} ({f['items']} items): {f['error']}" for f in failed
        )
    return result
