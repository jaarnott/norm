"""Loaded Legacy takes over the Cook Brothers App's Loaded passthroughs.

19 Oct 2026, owner: "replace the tools in the cook brothers app that we're
using as passthroughs … remove the tools from the cook brothers app and put
them into the loaded legacy app and then update the components and agents to
use the new tools." Plan: docs/plans/loaded-legacy-takeover.md.

Run AFTER ``sync_loaded_legacy_spec.py`` (which writes Loaded Legacy's spec,
``record_recipe`` included) and after the venues are connected — from then on
recipe saves, stocktake lists and tenders need a venue's Loaded Legacy
connection. The script lists the venues that use LoadedHub but have no Loaded
Legacy connection (in the app database it runs against) and stops unless told
``--anyway``.

What it changes in the shared config DB (live in every environment at once;
each row as it was is backed up first, by scripts/tool_row_updates.py):

- ``loadedhub.get_stocktakes``: code from config/consolidators/
  get_stocktakes.py (its list now through Loaded Legacy), 5 calls;
- the Loaded Kitchen App claims ``loaded_legacy.record_recipe`` instead of
  ``cook_brothers_app.record_recipe``; its recipe editor's connection is
  Loaded Legacy alone;
- the Loaded Stock App's supplier tenders connection is Loaded Legacy;
- Executive Chef's unattended ``record_recipe`` moves to a Loaded Legacy
  binding; Procurement's disabled tender capability goes. A Cook Brothers App
  binding left with no capabilities is DELETED, never left empty — an empty
  capability list exposes every action of the connector;
- the create_recipes playbook's sentence on record_recipe (names are now
  matched by Norm);
- the Cook Brothers App loses the ``record_recipe`` tool and the five
  endpoints Norm used to reach Loaded through it. Its other functions
  (functions, training, marketing, food control plan) are Orbit's own data
  and stay.

Usage (from apps/api, .env loaded; DATABASE_URL = the environment's app DB):
    uv run python scripts/sync_loaded_legacy_takeover.py --dry-run
    uv run python scripts/sync_loaded_legacy_takeover.py
"""

from __future__ import annotations

import argparse
import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import RowUpdate, apply_row_updates, code_of  # noqa: E402

CB = "cook_brothers_app"
LL = "loaded_legacy"
#: The Cook Brothers App endpoints that only passed calls through to Loaded.
CB_PASSTHROUGHS = (
    "kitchen_record_recipe",
    "kitchen_get_recipe",
    "stock_find_stocktakes",
    "stock_loadedhub_tender",
    "kitchen_get_product_sales",
)
CLAIMS = {"recipes-and-costing": (f"{CB}.record_recipe", f"{LL}.record_recipe")}
CONNECTIONS = {
    ("recipes-and-costing", "recipe_editor"): [LL],
    ("supplier-tenders", "supplier_tenders"): [LL],
}
#: (agent, Cook Brothers App capability) → the Loaded Legacy one, or None to drop.
BINDINGS = {
    ("executive_chef", "record_recipe"): {
        "action": "record_recipe",
        "label": "Create or edit a recipe and save it to Loaded (approve-gated).",
        "enabled": True,
    },
    ("procurement", "stock_loadedhub_tender"): None,
}
PLAYBOOK = "create_recipes"
OLD_SENTENCE = (
    "use record_recipe instead: it needs Loaded's own unit names (get_stock "
    "view 'reference' kind 'units') and exact item names, it asks for approval, "
    "and it returns the new recipe's id."
)
NEW_SENTENCE = (
    "use record_recipe instead: name the ingredients and units as written — "
    "Norm matches them, and writes nothing if it isn't sure of a line (it names "
    "the candidates) — it asks for approval, and it returns the new recipe's id."
)


def unconnected_venues() -> list[str]:
    """Venues with a LoadedHub connection and no usable Loaded Legacy one (the
    app DB this runs against)."""
    from app.db.engine import SessionLocal
    from app.db.models import Connection, Venue

    db = SessionLocal()
    try:
        rows = (
            db.query(Connection)
            .filter(
                Connection.connector_name.in_(("loadedhub", LL)),
                Connection.enabled == "true",
                Connection.venue_id.isnot(None),
            )
            .all()
        )
        on_loaded = {r.venue_id for r in rows if r.connector_name == "loadedhub"}
        ready = {
            r.venue_id
            for r in rows
            if r.connector_name == LL
            and (r.config or {}).get("username")
            and (r.config or {}).get("x_loaded_company_id")
        }
        missing = on_loaded - ready
        names = {v.id: v.name for v in db.query(Venue).filter(Venue.id.in_(missing))}
        return sorted(names.get(v, v) for v in missing)
    finally:
        db.close()


def _set_list(obj: dict, key: str, value) -> bool:
    if obj.get(key) == value:
        return False
    obj[key] = value
    return True


def other_config(db, dry_run: bool) -> list[str]:
    """Everything that isn't a tool row: endpoints, App claims and
    connections, bindings, the playbook."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import (
        AgentConnectionBinding,
        ConnectionSpec,
        MarketplaceApp,
        Playbook,
    )

    lines: list[str] = []
    ll = db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == LL).first()
    if ll is None or spec_rows.find_tool(ll, "record_recipe") is None:
        raise SystemExit(
            "loaded_legacy has no record_recipe tool — run "
            "scripts/sync_loaded_legacy_spec.py first"
        )

    # The Cook Brothers App's passthrough endpoints.
    cb = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CB)
        .with_for_update()
        .first()
    )
    if cb is not None and isinstance(cb.endpoints, list):
        gone = [e["action"] for e in cb.endpoints if e.get("action") in CB_PASSTHROUGHS]
        if gone:
            lines.append(f"{CB}: remove endpoints {gone}")
            print(
                f"BEFORE {CB} endpoints:",
                [e for e in cb.endpoints if e["action"] in gone],
            )
            if not dry_run:
                cb.endpoints = [
                    e for e in cb.endpoints if e.get("action") not in CB_PASSTHROUGHS
                ]
                flag_modified(cb, "endpoints")
                cb.version = (cb.version or 0) + 1

    # App claims and component connections.
    apps = {
        a.slug: a
        for a in db.query(MarketplaceApp)
        .filter(
            MarketplaceApp.slug.in_({s for s in CLAIMS} | {s for s, _ in CONNECTIONS})
        )
        .with_for_update()
        .all()
    }
    for slug, app in sorted(apps.items()):
        comp = copy.deepcopy(app.composition or {})
        changed = []
        if slug in CLAIMS:
            old, new = CLAIMS[slug]
            tools = list(comp.get("tools") or [])
            if old in tools or new not in tools:
                tools = [new if t == old else t for t in tools]
                if new not in tools:
                    tools.append(new)
                comp["tools"] = tools
                changed.append(f"claims {new} (was {old})")
        for entry in comp.get("components") or []:
            want = CONNECTIONS.get((slug, entry.get("key")))
            if want is not None and _set_list(entry, "connections", list(want)):
                changed.append(f"{entry['key']} connections → {want}")
        if changed:
            print(f"BEFORE {slug} composition:", app.composition)
            lines.append(f"app {slug}: " + "; ".join(changed))
            if not dry_run:
                app.composition = comp
                flag_modified(app, "composition")

    # Bindings: move or drop each Cook Brothers App capability.
    for (agent, action), replacement in BINDINGS.items():
        b = (
            db.query(AgentConnectionBinding)
            .filter_by(agent_slug=agent, connector_name=CB)
            .with_for_update()
            .first()
        )
        if b is not None and any(
            isinstance(c, dict) and c.get("action") == action
            for c in b.capabilities or []
        ):
            caps = [
                c
                for c in b.capabilities
                if not (isinstance(c, dict) and c.get("action") == action)
            ]
            print(f"BEFORE binding {agent}/{CB}:", b.capabilities)
            if caps:
                lines.append(f"binding {agent}/{CB}: drop {action}")
                if not dry_run:
                    b.capabilities = caps
                    flag_modified(b, "capabilities")
            else:
                # An empty list would expose EVERY Cook Brothers App action.
                lines.append(
                    f"binding {agent}/{CB}: delete (its only capability was {action})"
                )
                if not dry_run:
                    db.delete(b)
        if replacement is None:
            continue
        nb = (
            db.query(AgentConnectionBinding)
            .filter_by(agent_slug=agent, connector_name=LL)
            .with_for_update()
            .first()
        )
        have = [c for c in (nb.capabilities if nb else []) or [] if isinstance(c, dict)]
        if any(c.get("action") == replacement["action"] for c in have):
            continue
        lines.append(f"binding {agent}/{LL}: add {replacement['action']}")
        if not dry_run:
            if nb is None:
                db.add(
                    AgentConnectionBinding(
                        agent_slug=agent,
                        connector_name=LL,
                        capabilities=[replacement],
                        enabled=True,
                    )
                )
            else:
                nb.capabilities = have + [replacement]
                flag_modified(nb, "capabilities")

    # The playbook's sentence on record_recipe.
    pb = db.query(Playbook).filter(Playbook.slug == PLAYBOOK).with_for_update().first()
    if pb is not None and OLD_SENTENCE in (pb.instructions or ""):
        lines.append(
            f"playbook {PLAYBOOK}: record_recipe sentence (names matched by Norm)"
        )
        if not dry_run:
            pb.instructions = pb.instructions.replace(OLD_SENTENCE, NEW_SENTENCE)
    return lines


def updates() -> list[RowUpdate]:
    return [
        RowUpdate(
            "loadedhub",
            "get_stocktakes",
            code=code_of("get_stocktakes.py"),
            config={"max_api_calls": 5},
        ),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--anyway",
        action="store_true",
        help="go ahead although some venues have no Loaded Legacy connection",
    )
    args = ap.parse_args()

    missing = unconnected_venues()
    if missing:
        print(
            f"{len(missing)} venue(s) use LoadedHub but have no Loaded Legacy "
            "connection — after this they can't save recipes, list stocktakes or "
            "see tenders until connected:"
        )
        for name in missing:
            print("   -", name)
        if not (args.dry_run or args.anyway):
            raise SystemExit(
                "Connect them first (Settings → Venues), or pass --anyway."
            )
    else:
        print("every venue on LoadedHub has a Loaded Legacy connection")

    apply_row_updates(
        updates(),
        dry_run=args.dry_run,
        label="loaded-legacy-takeover",
        delete=((CB, "record_recipe"),),
        also=other_config,
    )


if __name__ == "__main__":
    main()
