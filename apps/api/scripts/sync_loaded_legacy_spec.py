"""Create / update / remove the ``loaded_legacy`` connector (TEMPORARY).

Two writes to the shared config DB, both from ``app/connectors/loaded_legacy.py``:

1. the ``connector_specs`` row ``loaded_legacy`` (``SPEC``): shown in Settings →
   Connector specs; ``auth_type`` "password_login"; credential fields username
   and password; the Loaded calls Norm's OAuth token can't make, as
   ``engine_only`` endpoints (never shown to an LLM), and the ``record_recipe``
   tool with its code from config/consolidators/record_recipe.py — on the spec
   but claimed by no App until ``sync_loaded_legacy_takeover.py`` runs;
2. ``loaded_legacy`` added to the connections of the Loaded Kitchen App's
   recipe editor, so Settings → Connections lists it per venue with the
   standard connect card (the takeover then makes it the only one).

The config DB is shared by every environment, so this is live everywhere at
once. Run it only where the code that knows "password_login" is deployed:
older code would save a typed password unencrypted. Each row as it was is
printed first (a backup to paste back). Only the App row it names is touched —
not the rest of the catalog (sync_marketplace_catalog.py applies every diff).

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_loaded_legacy_spec.py --dry-run
    uv run python scripts/sync_loaded_legacy_spec.py
    uv run python scripts/sync_loaded_legacy_spec.py --remove   # when it goes
"""

import argparse
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _row_dump(row, model) -> str:
    return json.dumps(
        {c.name: getattr(row, c.name) for c in model.__table__.columns}, default=str
    )


def _set_connection(app_row, component: str, connector: str, present: bool) -> bool:
    """Add or remove ``connector`` in the component entry's connections.
    Returns whether anything changed."""
    from sqlalchemy.orm.attributes import flag_modified

    comp = copy.deepcopy(app_row.composition or {})
    changed = False
    for entry in comp.get("components") or []:
        if not isinstance(entry, dict) or entry.get("key") != component:
            continue
        conns = list(entry.get("connections") or [])
        if present and connector not in conns:
            conns.append(connector)
            changed = True
        if not present and connector in conns:
            conns.remove(connector)
            changed = True
        if conns:
            entry["connections"] = conns
        else:
            entry.pop("connections", None)
    if changed:
        app_row.composition = comp
        flag_modified(app_row, "composition")
    return changed


#: Each tool's call budget, stated here as a literal so
#: tests/test_consolidator_budgets.py checks it against the file's own header
#: (draft_recipe's match, the recipe, its version, the write, the read-back).
TOOL_CONFIG = [
    {"action": "record_recipe", "consolidator_config": {"max_api_calls": 6}},
]


def _with_code(spec: dict) -> dict:
    """SPEC with each tool's function_code from config/consolidators/ and its
    budget from TOOL_CONFIG."""
    out = copy.deepcopy(spec)
    folder = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
    budgets = {t["action"]: t["consolidator_config"] for t in TOOL_CONFIG}
    for tool in out.get("tools") or []:
        cfg = tool.setdefault("consolidator_config", {})
        cfg.update(budgets[tool["action"]])
        cfg["function_code"] = (folder / f"{tool['action']}.py").read_text(
            encoding="utf-8"
        )
    return out


def main() -> None:
    from app.connectors.loaded_legacy import APP_SLUG, COMPONENT, CONNECTOR, SPEC
    from app.db.config_models import ConnectionSpec, MarketplaceApp
    from app.db.engine import _ConfigSessionLocal

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args()

    db = _ConfigSessionLocal()
    spec = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .first()
    )
    app = db.query(MarketplaceApp).filter(MarketplaceApp.slug == APP_SLUG).first()
    if spec is not None:
        print("BEFORE spec:", _row_dump(spec, ConnectionSpec))
    if app is None:
        print(f"no App '{APP_SLUG}' — the connection won't be listed on Connections")
    else:
        print(
            f"BEFORE {APP_SLUG} composition:", json.dumps(app.composition, default=str)
        )

    if args.remove:
        if spec is not None:
            db.delete(spec)
        print("removing the spec row")
        if app is not None and _set_connection(app, COMPONENT, CONNECTOR, False):
            print(f"removing {CONNECTOR} from {APP_SLUG}/{COMPONENT}")
    else:
        if spec is None:
            spec = ConnectionSpec(connector_name=CONNECTOR)
            db.add(spec)
        for key, value in _with_code(SPEC).items():
            setattr(spec, key, value)
        spec.enabled = True
        print(
            "writing the spec row — endpoints",
            [e["action"] for e in SPEC["endpoints"]],
            "tools",
            [t["action"] for t in SPEC["tools"]],
        )
        if app is not None and _set_connection(app, COMPONENT, CONNECTOR, True):
            print(f"listing {CONNECTOR} on {APP_SLUG}/{COMPONENT}")
    if args.dry_run:
        db.rollback()
        print("rolled back (dry run)")
    else:
        db.commit()
        print("committed")


if __name__ == "__main__":
    main()
