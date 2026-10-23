"""Import Orbit's Finance data (payroll inputs, entertainers, budgets) into
one Norm organisation — the shadow-period reload, run by hand.

Read-only against Orbit. Each run makes the organisation's Orbit-authored
rows match what Orbit holds now, in one transaction — or changes nothing,
when a sanity gate trips or ``--dry-run`` is given. The rules (deterministic
ids, never overwriting a Norm-authored row, refusing a live module, the
gates) are in ``app/services/finance_core/importer.py``.

    uv run python scripts/migrate_orbit_finance.py --org cook-brothers --dry-run
    uv run python scripts/migrate_orbit_finance.py --org cook-brothers --module payroll --dry-run
    uv run python scripts/migrate_orbit_finance.py --org cook-brothers --skip-venue CBI --no-files
    uv run python scripts/migrate_orbit_finance.py --org <id> --report-json /tmp/import.json --no-people
    uv run python scripts/migrate_orbit_finance.py --org cook-brothers --write-xero-mapping
    uv run python scripts/migrate_orbit_finance.py --org cook-brothers --settings-only --dry-run

Exit codes: 0 imported (or, with --dry-run, would import); 1 refused (a live
module, Orbit unreadable, models missing, bad argument); 2 a sanity gate
failed — rolled back, nothing written.

The target database is ``DATABASE_URL`` (the app's own setting), so the same
command serves local now and production later — run it against production
only with the owner's go-ahead. ``--write-xero-mapping`` is the one step
that touches a connection row (the venue's Xero ``xero_mapping`` blocks,
filling only keys nobody has set); the coordinator runs it, once.

``--settings-only`` writes ONLY the payroll settings (``pay_entities``: every
setting on Payroll settings → Entities, as Orbit applies them) and reports
the Xero mapping the full import would set — so production can carry the
settings before the full import. A setting someone changed in Norm is never
overwritten. Orbit names the sick-leave approver only in its email wording,
so pass it with ``--sick-approver NAME`` (blank otherwise; never blanked once
set).

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


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Import Orbit Finance data into a Norm organisation"
    )
    ap.add_argument("--org", required=True, help="organisation id or slug")
    ap.add_argument(
        "--module",
        action="append",
        default=[],
        choices=["payroll", "entertainers", "finance"],
        help="a module to import (repeatable; default: all three)",
    )
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
    ap.add_argument("--no-files", action="store_true", help="do not copy invoice files")
    ap.add_argument(
        "--write-xero-mapping",
        action="store_true",
        help="also merge the xero_mapping blocks into each venue's Xero connection row (keys nobody set)",
    )
    ap.add_argument(
        "--settings-only",
        action="store_true",
        help="write ONLY the payroll settings (pay_entities) and report the Xero mapping "
        "the full import would set",
    )
    ap.add_argument(
        "--sick-approver",
        metavar="NAME",
        help="the sick-leave approver to set on every entity written (Orbit holds the "
        "name only in its email wording)",
    )
    ap.add_argument("--report-json", help="also write the report as JSON to this path")
    ap.add_argument(
        "--no-people",
        action="store_true",
        help="leave people's names out of the printed report (the JSON keeps them)",
    )
    ap.add_argument(
        "--legacy-weeks-json",
        metavar="PATH",
        help="a JSON object {loaded leave request id: 'YYYY-MM-DD'} resolved from Loaded's "
        "day lines, for the legacy payouts keyed by request id only (else their created week)",
    )
    ap.add_argument(
        "--now",
        metavar="ISO_TIMESTAMP",
        help="the run's clock (default: now), so a run can be reproduced",
    )
    ap.add_argument(
        "--creds",
        metavar="PATH",
        help="the Orbit credential file (else the usual places)",
    )
    return ap


def main(argv: list[str] | None = None, *, orbit=None, db=None) -> int:
    """CLI entry. ``orbit``/``db`` are injectable so tests run with no network
    and inside their own transaction."""
    from app.services.finance_core.importer import (
        ImportRefused,
        OrbitReadError,
        orbit_client,
        run_import,
        run_settings_import,
    )

    args = build_parser().parse_args(argv)
    if args.settings_only and (args.module or args.write_xero_mapping):
        print(
            "--settings-only writes only the payroll settings: leave out --module "
            "and --write-xero-mapping (the mapping goes with the full import)"
        )
        return 1
    try:
        now = dt.datetime.fromisoformat(args.now) if args.now else None
        if now is not None and now.tzinfo is None:
            raise ValueError("--now needs a timezone, e.g. 2026-10-20T09:00:00+13:00")
        legacy_weeks = None
        if args.legacy_weeks_json:
            raw = json.loads(pathlib.Path(args.legacy_weeks_json).read_text())
            legacy_weeks = {str(k): dt.date.fromisoformat(v) for k, v in raw.items()}
    except (ValueError, OSError) as exc:
        print(str(exc))
        return 1
    owns_db = db is None
    if owns_db:
        sys.stdout.reconfigure(line_buffering=True)
        from app.db.engine import SessionLocal

        db = SessionLocal()
    try:
        try:
            source = orbit or orbit_client(
                pathlib.Path(args.creds) if args.creds else None
            )
            if args.settings_only:
                result = run_settings_import(
                    db,
                    source,
                    org=args.org,
                    dry_run=args.dry_run,
                    skip_venues=args.skip_venue,
                    sick_approver=args.sick_approver,
                    commit=owns_db,
                    now=now,
                )
                print("\n".join(result.report.lines(people=False)))
                if args.report_json:
                    pathlib.Path(args.report_json).write_text(
                        json.dumps(
                            result.report.as_dict(), indent=2, ensure_ascii=False
                        )
                    )
                return result.exit_code
            result = run_import(
                db,
                source,
                org=args.org,
                modules=args.module or ("payroll", "entertainers", "finance"),
                dry_run=args.dry_run,
                skip_venues=args.skip_venue,
                copy_files=not args.no_files,
                write_xero_mapping=args.write_xero_mapping,
                commit=owns_db,
                now=now,
                legacy_weeks=legacy_weeks,
                sick_approver=args.sick_approver,
            )
        except ImportRefused as exc:
            print(f"refused: {exc}")
            return 1
        except OrbitReadError as exc:
            # Nothing was written: Orbit is read in full before the transaction.
            print(f"could not read Orbit: {exc}")
            return 1
        print("\n".join(result.report.lines(people=not args.no_people)))
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
