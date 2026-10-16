"""Import Orbit's Functions data into one Norm organisation (wipe and reload).

Read-only against Orbit. Each run replaces the organisation's Functions rows
with what Orbit holds now, in one transaction — or changes nothing, when the
sanity gate trips or ``--dry-run`` is given. The rules (what is never wiped,
deterministic ids, the gate) are in ``app/services/functions/importer.py``.

    uv run python scripts/migrate_orbit_functions.py --org cook-brothers --dry-run
    uv run python scripts/migrate_orbit_functions.py --org cook-brothers --skip-venue CBI
    uv run python scripts/migrate_orbit_functions.py --org <id> --report-json /tmp/import.json

Exit codes: 0 imported (or, with --dry-run, would import); 1 refused (live
organisation, unmapped venue, venue without a timezone, Orbit unreadable, bad
argument); 2 the sanity gate failed — rolled back, nothing written.

The target database is ``DATABASE_URL`` (the app's own setting), so the same
command serves local now and production later — run it against production
only with the owner's go-ahead. The organisation must not be ``live``.

Credentials: ``$ORBIT_SUPABASE_CREDS``, else ``.local/orbit-supabase.json`` at
the repo root (a worktree falls back to its main checkout's). Never printed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.migrate_orbit_training import creds_path  # noqa: E402 — path set above


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Import Orbit Functions into a Norm organisation"
    )
    ap.add_argument("--org", required=True, help="organisation id or slug")
    ap.add_argument(
        "--dry-run", action="store_true", help="do everything, then roll back"
    )
    ap.add_argument(
        "--skip-venue",
        action="append",
        default=[],
        metavar="NAME",
        help="an Orbit venue to leave out (repeatable); its rows are reported as skipped",
    )
    ap.add_argument("--report-json", help="also write the report as JSON to this path")
    ap.add_argument(
        "--no-files",
        action="store_true",
        help="do not copy images (proposal <img> tags not on Norm are then removed by the sanitiser)",
    )
    ap.add_argument(
        "--now",
        metavar="ISO_TIMESTAMP",
        help="the run's clock (default: now) — dates the import derives (follow-ups and "
        "archiving by rule, parity's 'today') come from it, so a run can be reproduced",
    )
    ap.add_argument(
        "--threshold",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override a sanity-gate threshold, e.g. max_dropped_share=0.05",
    )
    return ap


def orbit_client(creds: pathlib.Path | None = None):
    from app.services.functions.importer import OrbitPostgrest

    cfg = json.loads((creds or creds_path()).read_text())
    if not cfg.get("service_role_key") or not cfg.get("url"):
        raise SystemExit("Orbit credentials need url and service_role_key")
    return OrbitPostgrest(cfg["url"], cfg["service_role_key"])


def main(argv: list[str] | None = None, *, orbit=None, db=None) -> int:
    """CLI entry. ``orbit``/``db`` are injectable so tests run with no network
    and inside their own transaction."""
    from app.services.functions.importer import (
        ImportRefused,
        OrbitReadError,
        Thresholds,
        run_import,
    )

    args = build_parser().parse_args(argv)
    try:
        thresholds = Thresholds.parse(args.threshold)
        now = dt.datetime.fromisoformat(args.now) if args.now else None
        if now is not None and now.tzinfo is None:
            raise ValueError("--now needs a timezone, e.g. 2026-10-12T09:00:00+13:00")
    except ValueError as exc:
        print(str(exc))
        return 1
    owns_db = db is None
    if owns_db:
        sys.stdout.reconfigure(line_buffering=True)
        from app.db.engine import SessionLocal

        db = SessionLocal()
    try:
        try:
            result = run_import(
                db,
                orbit or orbit_client(),
                org=args.org,
                dry_run=args.dry_run,
                skip_venues=args.skip_venue,
                thresholds=thresholds,
                copy_files=not args.no_files,
                commit=owns_db,
                now=now,
            )
        except ImportRefused as exc:
            print(f"refused: {exc}")
            return 1
        except OrbitReadError as exc:
            # Nothing was written: Orbit is read in full before the transaction.
            print(f"could not read Orbit: {exc}")
            return 1
        print("\n".join(result.report.lines()))
        if args.report_json:
            pathlib.Path(args.report_json).write_text(
                json.dumps(result.report.as_dict(), indent=2, ensure_ascii=False)
            )
            print(f"\nreport written to {args.report_json}")
        return result.exit_code
    finally:
        if owns_db:
            db.rollback()  # releases the advisory lock after a dry run / failed gate
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
