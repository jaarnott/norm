"""Print the consolidator-migration dashboard (CLI face of the service).

Same payload the admin panel renders (Settings → Connectors → Consolidator
coverage), against whatever DATABASE_URL/CONFIG_DATABASE_URL this process
sees — locally that means local usage numbers; run in prod (or point
DATABASE_URL at it) for real usage ranking.

Usage:
    uv run python scripts/consolidator_coverage.py [--days 30]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")


def main() -> None:
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.services.consolidator_coverage import coverage_report

    days = 30
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    db, cdb = SessionLocal(), _ConfigSessionLocal()
    try:
        r = coverage_report(db, cdb, days=days)
    finally:
        db.close()
        cdb.close()

    t = r["totals"]
    print(
        f"Consolidator coverage ({r['window_days']}d usage): "
        f"{t['consolidator']} consolidators · {t['backend']} backends · "
        f"{t['raw']} raw · {t['leaks']} LEAKS"
    )
    for c in r["connectors"]:
        n = c["counts"]
        if not (n["consolidator"] or n["backend"] or c["leaks"]):
            continue
        print(
            f"\n{c['connector']}: {n['consolidator']} consolidated, "
            f"{n['backend']} backend, {n['raw']} raw"
        )
        for leak in c["leaks"]:
            print(
                f"  LEAK {leak['action']:<32} → use {leak['superseded_by']}"
                f"  ({leak['calls_30d']} calls; {', '.join(leak['agents']) or 'mcp'})"
            )
        for d in c["drift"]:
            print(f"  DRIFT {d['action']:<31} {d['state']}")
        for b in c["backlog"][:8]:
            if b["calls_30d"]:
                print(f"  next {b['action']:<32} {b['calls_30d']} calls")
    print()


if __name__ == "__main__":
    main()
