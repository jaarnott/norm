"""Remove the tenders OAuth scopes from the loadedhub connector spec.

History: added 29 Aug 2026 in the hope the ``cookbrothers`` OAuth client could
request ``stock:tenders:r/rw`` (Loaded's TendersApi accepts the Stock
permission OR those scopes). Loaded's client does NOT have the tenders scopes
available, so requesting them is at best a no-op and at worst breaks the
authorize step. Tenders go through the Cook Brothers App instead — the same
path as recipe writes, whose stored Loaded session carries the Stock
permission (docs/apps-marketplace-plan.md Phase 2, revised).

This script now REMOVES the two scopes so a re-run enforces the end state.

Usage:
    .venv/bin/python scripts/sync_loadedhub_tender_scopes.py --dry-run
    .venv/bin/python scripts/sync_loadedhub_tender_scopes.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
DEAD_SCOPES = {"stock:tenders:r", "stock:tenders:rw"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            sys.exit(f"No connector spec named {CONNECTOR}")
        oauth = dict(spec.oauth_config or {})
        scopes = (oauth.get("scopes") or "").split()
        kept = [s for s in scopes if s not in DEAD_SCOPES]
        if kept == scopes:
            print("tenders scopes already absent — nothing to do")
            return
        if args.dry_run:
            print(f"WOULD remove: {' '.join(s for s in scopes if s in DEAD_SCOPES)}")
            return
        oauth["scopes"] = " ".join(kept)
        spec.oauth_config = oauth
        spec.version = (spec.version or 0) + 1
        flag_modified(spec, "oauth_config")
        db.commit()
        print(f"removed tenders scopes, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
