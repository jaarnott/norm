"""Name the organisation of each organisation-wide (venue-less) connection.

Migration bk2m4o6q8s0u gave every venue's connection its venue's
organisation. A venue-less row can't be attributed from the data, and since
that change it serves no organisation until it names one
(services/connection_scope.py). This lists them and, with --assign, sets each
one's organisation. Production on 23 Oct 2026: one row, BambooHR, Cook
Brothers Hospitality's.

Usage (from apps/api, .env loaded, DATABASE_URL at the target database):
    uv run python scripts/attribute_org_wide_connections.py
    uv run python scripts/attribute_org_wide_connections.py \\
        --assign "bamboohr=Cook Brothers Hospitality" --apply
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

#: Platform connectors keep no organisation (routers/connectors.py)
PLATFORM = {"anthropic"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assign",
        action="append",
        default=[],
        help="connector=Organisation name (repeatable)",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from app.db.engine import SessionLocal
    from app.db.models import Connection, Organization

    db = SessionLocal()
    try:
        rows = (
            db.query(Connection)
            .filter(
                Connection.venue_id.is_(None),
                Connection.user_id.is_(None),
                Connection.organization_id.is_(None),
            )
            .all()
        )
        rows = [r for r in rows if r.connector_name not in PLATFORM]
        print(f"{len(rows)} organisation-wide connection(s) with no organisation:")
        for r in rows:
            print(f"  {r.connector_name} (row {r.id[:8]}, enabled {r.enabled})")
        wanted = dict(a.split("=", 1) for a in args.assign)
        changes = []
        for connector, org_name in wanted.items():
            matching = [r for r in rows if r.connector_name == connector]
            if len(matching) != 1:
                sys.exit(f"{connector}: {len(matching)} unattributed rows, expected 1")
            orgs = db.query(Organization).filter(Organization.name == org_name).all()
            if len(orgs) != 1:
                sys.exit(f"{org_name!r}: {len(orgs)} organisations, expected 1")
            matching[0].organization_id = orgs[0].id
            changes.append(f"{connector} -> {org_name}")
        for c in changes:
            print(("" if args.apply else "[dry-run] ") + c)
        if args.apply and changes:
            db.commit()
            print("committed")
        else:
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
