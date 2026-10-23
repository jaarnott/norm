"""Add the Catalogue page to the Loaded Stock App (Oct 2026).

docs/plans/stock-catalogue-page.md. The page's component, ``stock_catalogue``,
goes into Loaded Stock's composition — exactly the entry
sync_marketplace_catalog.py declares for it, and nothing else. That script
rewrites the whole row from its own list, so running it applies every
difference between the repo and the live catalogue (pricing held back, a claim
another change added since …); this one touches only the page.

Run it AFTER the code that renders the page is deployed (a catalogue entry the
web app doesn't know shows a menu item that opens nothing), and after the tool
installers:

    sync_loaded_legacy_spec.py, sync_stock_config.py,
    sync_manage_stock_item_config.py, sync_manage_supplier_config.py

Usage:
    uv run python scripts/sync_stock_catalogue_page.py [--dry-run]
"""

from __future__ import annotations

import argparse
import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

APP = "loaded-stock"
KEY = "stock_catalogue"
#: It sits after this one, as the catalogue script lists it.
AFTER = "supplier_tenders"


def component() -> dict:
    """The page's entry, as sync_marketplace_catalog.py declares it."""
    import sync_marketplace_catalog as catalog

    app = next(a for a in catalog.APPS if a.get("slug") == APP)
    return next(c for c in app["composition"]["components"] if c["key"] == KEY)


def plan(composition: dict) -> tuple[dict, str | None]:
    """The composition with the page in it, and what changed (None: nothing)."""
    out = copy.deepcopy(composition or {})
    comps = list(out.get("components") or [])
    want = component()
    at = next((i for i, c in enumerate(comps) if c.get("key") == KEY), None)
    if at is not None:
        if comps[at] == want:
            return out, None
        comps[at] = want
        what = f"updated {KEY}"
    else:
        after = next((i for i, c in enumerate(comps) if c.get("key") == AFTER), None)
        comps.insert(len(comps) if after is None else after + 1, want)
        what = f"added {KEY}"
    out["components"] = comps
    return out, what


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import MarketplaceApp
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        row = db.query(MarketplaceApp).filter(MarketplaceApp.slug == APP).first()
        if row is None:
            raise SystemExit(f"{APP} isn't in the catalogue")
        composition, what = plan(row.composition)
        if what is None:
            print(f"{APP}: the Catalogue page is already there")
            return
        if dry_run:
            print(f"DRY RUN — {APP}: would have {what}")
            return
        row.composition = composition
        flag_modified(row, "composition")
        db.commit()
        print(f"{APP}: {what}")
        from app.services.config_validator import validate_config

        print(f"validate_config: {validate_config(config_db=db)}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(dry_run=parser.parse_args().dry_run)
