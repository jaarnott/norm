"""Stop get_roster reporting another venue's hours.

`get_roster` asked LoadedHub for `includeCrossVenueShifts=true`, then the tool's
`response_transform` filtered those very shifts back out
(`rosteredShifts[].isFromOtherVenue equals false`) — while passing the
roster-level `totalHours` through untouched.

The result was a payload whose header described every venue and whose shift list
described one. For La Zwppa, week of 27 Jul:

    header totalHours : 332.25   (140 shifts, all venues)
    shifts returned   :  66      (this venue) summing to 146.5

An agent reading `totalHours` reported 332.25 hours; one summing the shifts
reported 146.5. Both "correct", 2.3x apart — which is how it surfaced, when one
agent consulted another and the numbers disagreed.

Fetching the right scope in the first place fixes it at the source: LoadedHub's
header is then computed over exactly the shifts it returns. Verified across six
weeks (8 Jun → 27 Jul): with the flag off, header == sum of the shifts'
`totalHours` every week, exactly.

Note the header is *paid* hours — duration minus unpaid breaks (283.25 worked
hours - 1.5 break hours = 281.75 for the week of 20 Jul). That semantic is
LoadedHub's and is worth preserving, which is why this fixes the request rather
than recomputing the total ourselves.

The transform's isFromOtherVenue filter stays as a harmless safety net.

Usage:
    .venv/bin/python scripts/sync_roster_venue_scope.py --dry-run
    .venv/bin/python scripts/sync_roster_venue_scope.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
ACTION = "get_roster"
OLD = "includeCrossVenueShifts=true"
NEW = "includeCrossVenueShifts=false"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        tool = next((t for t in tools if t.get("action") == ACTION), None)
        if not tool:
            raise SystemExit(f"No {CONNECTOR}.{ACTION} tool")

        path = tool.get("path_template") or ""
        if NEW in path:
            print("already scoped to this venue — nothing to do")
            return
        if OLD not in path:
            raise SystemExit(
                f"path_template does not contain {OLD!r}; refusing to guess.\n  {path}"
            )

        print(f"  before: {path}")
        print(f"  after : {path.replace(OLD, NEW)}")
        if args.dry_run:
            print("(dry run — nothing written)")
            return

        tool["path_template"] = path.replace(OLD, NEW)
        spec.tools = tools
        flag_modified(spec, "tools")
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
