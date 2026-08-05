"""Stop get_budgets dropping the first day of every date range.

LoadedHub's budget endpoint (`/api/budgets?from=&to=`) parses the bare `from`
and `to` as UTC, but budget entries are stamped at the venue's LOCAL midnight
(`…T00:00:00+12:00`). NZ midnight is noon the previous day in UTC, so the
requested `from` day sits just outside the window and is silently dropped, while
`to` (inclusive) lands correctly. Every weekly budget therefore came back a day
short — understated by its first day.

Verified live against real LoadedHub (Bessie & Engineers, week of 27 Jul 2026):

    from=2026-07-27  ->  [28 Jul .. 2 Aug]   (Monday 27 dropped — the bug)
    from=2026-07-26  ->  [27 Jul .. 2 Aug]   (the intended week, complete)

So requesting one day earlier and letting LoadedHub drop THAT day returns the
window that was actually asked for. The `shift_days` Jinja filter
(app/connectors/template_filters.py) does it in the path template; only
`from` moves — `to` is already correct.

ORDER OF ROLLOUT: the `shift_days` filter must be DEPLOYED to every environment
(it ships in app code) BEFORE this config change is applied, or the template
render fails with an unknown filter. Deploy first, then run this.

Usage:
    .venv/bin/python scripts/sync_budget_date_fix.py --dry-run
    .venv/bin/python scripts/sync_budget_date_fix.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
ACTION = "get_budgets"
OLD = "from={{ from_date }}"
NEW = "from={{ from_date | shift_days(-1) }}"


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
            print("already shifting from_date by -1 — nothing to do")
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
