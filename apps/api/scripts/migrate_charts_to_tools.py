"""Move every saved report chart onto a TOOL (Sep 2026 — only tools reach an
LLM, and charts follow the same rule; docs/tool-architecture-strategy.md).

Charts re-run a saved call on refresh. Until now most called raw or transformed
endpoints directly (get_roster, get_sales_data, get_purchase_orders_summary…),
four called actions that don't exist (xero.get_monthly_spend,
loadedhub.get_pos_sales), and several dashboard-template charts plotted fields
their call never returned. This re-points each one at the tool that answers its
question, names the list to plot (``script["rows"]``), maps the chart's fields
to the tool's, and pins a named venue by id.

Main DB, per environment. Default is a DRY RUN that prints every change.

    uv run python scripts/migrate_charts_to_tools.py [--main-url URL]           # dry run
    uv run python scripts/migrate_charts_to_tools.py [--main-url URL] --apply   # backup, then write
    uv run python scripts/migrate_charts_to_tools.py [--main-url URL] --restore FILE

``--apply`` writes a JSON backup of every changed chart first
(``~/norm-chart-backups/``); ``--restore`` puts those rows back. Refresh the
reports afterwards (POST /api/reports/{id}/refresh) to fill the new data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

BACKUP_DIR = pathlib.Path.home() / "norm-chart-backups"


def _templates(apply: bool) -> None:
    """Dashboard templates (config DB, shared by every environment): every
    chart definition onto a tool, so a new dashboard starts on tools."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import DashboardTemplate
    from app.db.engine import _ConfigSessionLocal
    from app.services.chart_tools import to_tool_chart

    cdb = _ConfigSessionLocal()
    try:
        backup, changed = [], 0
        for tmpl in cdb.query(DashboardTemplate).all():
            new_charts = []
            for c in tmpl.charts or []:
                p = to_tool_chart(
                    c.get("title"),
                    c.get("chart_type"),
                    c.get("script"),
                    c.get("chart_spec"),
                )
                if isinstance(p, tuple):
                    title, ctype, script, spec, note = p
                    print(
                        f"[{tmpl.slug}] {c.get('title')} -> {script['connector']}.{script['action']}"
                        + (f" «{title}»" if title != c.get("title") else "")
                    )
                    new_charts.append(
                        {
                            **c,
                            "title": title,
                            "chart_type": ctype,
                            "script": script,
                            "chart_spec": spec,
                        }
                    )
                    changed += 1
                else:
                    if p == "UNMAPPED":
                        print(f"UNMAPPED [{tmpl.slug}] {c.get('title')}")
                    new_charts.append(c)
            backup.append({"slug": tmpl.slug, "charts": tmpl.charts})
            if apply:
                tmpl.charts = new_charts
                flag_modified(tmpl, "charts")
        print(f"{changed} template chart(s) to move")
        if apply and changed:
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = BACKUP_DIR / f"templates-{stamp}.json"
            path.write_text(json.dumps(backup, indent=1, default=str))
            cdb.commit()
            print(f"applied; backup {path}")
        else:
            cdb.rollback()
    finally:
        cdb.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--main-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore")
    ap.add_argument(
        "--templates",
        action="store_true",
        help="rewrite the dashboard templates in the SHARED config DB instead of saved charts",
    )
    args = ap.parse_args()

    if args.templates:
        return _templates(args.apply)

    from sqlalchemy import create_engine, text

    from app.services.chart_tools import to_tool_chart

    engine = create_engine(args.main_url)

    if args.restore:
        rows = json.loads(pathlib.Path(args.restore).read_text())
        with engine.begin() as conn:
            for r in rows:
                conn.execute(
                    text(
                        "UPDATE report_charts SET title=:title, chart_type=:chart_type, script=CAST(:script AS JSON), chart_spec=CAST(:chart_spec AS JSON) WHERE id=:id"
                    ),
                    {
                        **r,
                        "script": json.dumps(r["script"]),
                        "chart_spec": json.dumps(r["chart_spec"]),
                    },
                )
        print(f"restored {len(rows)} chart(s) from {args.restore}")
        return

    with engine.connect() as conn:
        venues = {
            name: vid
            for vid, name in conn.execute(
                text("SELECT id, name FROM venues")
            ).fetchall()
        }
        charts = (
            conn.execute(
                text(
                    "SELECT rc.id, r.title AS report, rc.title, rc.chart_type, rc.script, rc.chart_spec "
                    "FROM report_charts rc JOIN reports r ON r.id = rc.report_id ORDER BY r.title, rc.position"
                )
            )
            .mappings()
            .all()
        )

    changes, backups, unmapped = [], [], []
    for c in charts:
        p = to_tool_chart(c["title"], c["chart_type"], c["script"], c["chart_spec"])
        if p is None:
            continue
        if p == "UNMAPPED":
            unmapped.append(c)
            continue
        new_title, new_type, new_script, new_spec, note = p
        old = c["script"] or {}
        venue_name = (old.get("params") or {}).get("venue")
        if old.get("venue_id"):
            new_script["venue_id"] = old["venue_id"]
        elif venue_name and venue_name in venues:
            new_script["venue_id"] = venues[venue_name]
        changes.append((c, new_title, new_type, new_script, new_spec, note))
        backups.append(
            {
                "id": c["id"],
                "title": c["title"],
                "chart_type": c["chart_type"],
                "script": c["script"],
                "chart_spec": c["chart_spec"],
            }
        )

    for c, new_title, new_type, new_script, new_spec, note in changes:
        old = c["script"] or {}
        print(
            f"[{c['report'][:24]}] {c['title'][:34]:34s} {old.get('connector')}.{old.get('action')} -> "
            f"{new_script['connector']}.{new_script['action']} {json.dumps(new_script['params'])}"
            + (f"  «{new_title}»" if new_title != c["title"] else "")
            + (f"  ({note})" if note else "")
        )
    for c in unmapped:
        print(
            f"UNMAPPED [{c['report']}] {c['title']} {(c['script'] or {}).get('action')}"
        )
    print(f"{len(changes)} chart(s) to move, {len(unmapped)} unmapped")

    if not args.apply or not changes:
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    host = (engine.url.host or "db").replace(".", "_")
    backup = BACKUP_DIR / f"charts-{host}-{engine.url.port}-{stamp}.json"
    backup.write_text(json.dumps(backups, indent=1, default=str))
    with engine.begin() as conn:
        for c, new_title, new_type, new_script, new_spec, _ in changes:
            conn.execute(
                text(
                    "UPDATE report_charts SET title=:title, chart_type=:chart_type, script=CAST(:script AS JSON), "
                    "chart_spec=CAST(:chart_spec AS JSON), data=CAST('[]' AS JSON), updated_at=now() WHERE id=:id"
                ),
                {
                    "id": c["id"],
                    "title": new_title,
                    "chart_type": new_type,
                    "script": json.dumps(new_script),
                    "chart_spec": json.dumps(new_spec),
                },
            )
    print(f"applied; backup {backup}")


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    main()
