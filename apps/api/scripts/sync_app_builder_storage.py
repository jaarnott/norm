"""Tell the App Builder that apps can keep their own records.

An app built in Norm may declare storage and keep rows of its own, but the live
`build_an_app` skill never said so, nor did `list_app_capabilities`. So Norm
told an owner that an end-of-night report app "can't store records", and that
starred points couldn't be gathered for the week (thread 029ca35b, 20 Oct 2026).

This inserts STORAGE_SECTION into the live skill just before "## logic_source".
The same text is in scripts/sync_app_builder_agent.py (the repo copy of the
prompt); tests/test_app_builder_style.py keeps them equal. Idempotent. The
config DB is shared by every environment, so a real run reaches production at
once: dry-run first.

Usage:
    uv run python scripts/sync_app_builder_storage.py --dry-run
    uv run python scripts/sync_app_builder_storage.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ANCHOR = "## logic_source (optional — most apps don't need it)\n"

STORAGE_SECTION = """## Keeping records (storage)
An app can keep rows of its own — a nightly report, a checklist, notes — for its organisation. Declare it in `spec`:
- `storage`: {"namespace": "<snake_case, unique to this app, e.g. end_of_night_reports>", "collections": ["<name>", ...]} — the ONLY collections it may touch. Names are letters, digits, - and _. Norm's own apps' namespaces are reserved.
- In `ui_source`: `window.norm.storage.list(collection, {where, venue_id, order_by, descending, limit})`, `.get(collection, id)`, `.create(collection, data, venueId)`, `.update(collection, id, data, venueId)`, `.remove(collection, id)` — each a Promise. A row comes back as its data plus `id`, `venue_id`, `created_at`, `updated_at`. `where` matches top-level fields exactly.
- In `logic_source`: `store.list(...)`, `store.get(collection, id)`, `store.put(collection, data, record_id=None, venue_id=None)`, `store.delete(collection, id)`, `store.count(...)`.
- Save the venue on a row (`venueId` / `venue_id`) when it belongs to one venue, so the list follows the venue picker; pass `""` for a group-wide row.
- Rows are the organisation's: everyone the app is shared with sees them. Use storage for things people write in the app (reports, ratings, starred points) — never to copy data a connector action already returns.
- An app built here can't send email or post to other apps. Gather what's needed on a screen of its own instead (e.g. a "This week" tab listing the starred points) and say so.

"""


def patch(text: str) -> tuple[str, bool]:
    if STORAGE_SECTION in text:
        return text, False
    if text.count(ANCHOR) != 1:
        raise SystemExit(
            "build_an_app: the logic_source heading isn't there once — edit by hand"
        )
    return text.replace(ANCHOR, STORAGE_SECTION + ANCHOR, 1), True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.db.config_models import Playbook
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        pb = db.query(Playbook).filter(Playbook.slug == "build_an_app").first()
        if pb is None:
            raise SystemExit("no build_an_app skill in the config DB")
        new, changed = patch(pb.instructions or "")
        if not changed:
            print("build_an_app: storage section already there — nothing to do")
            return
        print("build_an_app: + storage section (before logic_source)")
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        pb.instructions = new
        db.commit()
        print("written")
    finally:
        db.close()


if __name__ == "__main__":
    main()
