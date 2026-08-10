"""Repair received invoice lines whose description isn't their stock item's name.

**The bug.** Loaded's invariant is that an invoice line linked to a stock item
carries THAT ITEM'S name as its description — its own client sets
``description: item.name`` on every link (mercury React bundle), and 99 of 99
linked lines across 18 human-received invoices obey it, including cases where
the supplier variant's description differs from the item name. Norm's
``do_receive`` left the supplier's raw line text instead, so a correctly
matched line reads as unmatched on Loaded's screens (Eurovintage 1229552:
"Rosabel Dry Rose 2024 6x 750ml" on a line linked to ROSABEL PAYS D'OC ROSE).

``received_invoice._apply_item_descriptions`` fixes it going forward. This
script repairs what already shipped.

**Detection** is against Loaded, not our own database: production Norm, the
local dev instance and any other client all write to the same venues, so only
Loaded knows the full damage. A linked line whose description differs from its
item's current name is the signature.

**False-positive guard.** An item renamed AFTER an old human receive would look
identical. Two defences: the window (``--days``, default 30 — the Norm era),
and ``--require-code-match``… no: instead the report prints every candidate
with its received date so a human can eyeball the set before ``--apply``.

Usage (dry run is the default; nothing is written without ``--apply``):

    uv run python scripts/backfill_linked_line_descriptions.py --days 30
    uv run python scripts/backfill_linked_line_descriptions.py --days 30 --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="received-feed window")
    ap.add_argument("--apply", action="store_true", help="write the repairs")
    ap.add_argument("--venue", default=None, help="limit to one venue name")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import ConnectorConfig, Venue
    from app.services.received_invoice import LoadedInvoiceClient

    db, cdb = SessionLocal(), _ConfigSessionLocal()
    total_bad_lines = total_bad_invoices = total_fixed = 0
    try:
        venues = {v.id: v.name for v in db.query(Venue).all()}
        creds = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == "loadedhub",
                ConnectorConfig.enabled == "true",
            )
            .all()
        )
        today = dt.date.today()
        frm = (today - dt.timedelta(days=args.days)).isoformat()
        to = (today + dt.timedelta(days=1)).isoformat()

        for cred in creds:
            name = venues.get(cred.venue_id, cred.venue_id)
            if args.venue and args.venue.lower() not in name.lower():
                continue
            try:
                lh = LoadedInvoiceClient(db, cdb, cred.venue_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] no client: {exc}")
                continue

            feed = lh.get(
                "/1.0/stock/internal/stock-received"
                f"?from={frm}&to={to}&property=Invoiced"
                "&includeAdjustingInvoices=true&ifNoneGetLastReceived=false"
            )
            rows = [
                r
                for r in (feed if isinstance(feed, list) else [])
                if isinstance(r, dict) and r.get("type") != "PurchaseOrder" and r.get("id")
            ]
            print(f"\n[{name}] {len(rows)} received invoices in the last {args.days} days")

            items: dict[str, str | None] = {}

            def item_name(iid: str, _lh=lh) -> str | None:
                if iid not in items:
                    try:
                        it = _lh.get(f"/1.0/stock/internal/items/{iid}")
                        items[iid] = (it or {}).get("name") if isinstance(it, dict) else None
                    except Exception:  # noqa: BLE001
                        items[iid] = None
                return items[iid]

            def fetch(row, _lh=lh):
                try:
                    return row, _lh.invoice(row["id"])
                except Exception:  # noqa: BLE001
                    return row, None

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                details = list(pool.map(fetch, rows))

            for row, det in details:
                if not isinstance(det, dict):
                    continue
                bad = []
                for ln in det.get("lines") or []:
                    if ln.get("deletedAt") or not ln.get("linkedItemId"):
                        continue
                    nm = item_name(str(ln["linkedItemId"]))
                    if nm and str(nm).strip() != str(ln.get("description") or "").strip():
                        bad.append((ln, nm))
                if not bad:
                    continue
                total_bad_invoices += 1
                total_bad_lines += len(bad)
                recv = str(det.get("receivedAt") or "")[:10]
                print(
                    f"  {str(det.get('referenceNumber'))[:16]:16} recv={recv} "
                    f"{len(bad)} line(s) — e.g. {str(bad[0][0].get('description'))[:30]!r} "
                    f"→ {str(bad[0][1])[:30]!r}"
                )
                if not args.apply:
                    continue
                for ln, nm in bad:
                    ln["description"] = nm
                try:
                    lh.request(
                        "PUT", f"/1.0/stock/internal/invoices/{det['id']}", det
                    )
                    total_fixed += len(bad)
                    print("      repaired ✓")
                except Exception as exc:  # noqa: BLE001
                    print(f"      FAILED: {exc}")

        print(
            f"\n{'APPLIED' if args.apply else 'DRY RUN'}: "
            f"{total_bad_invoices} invoice(s), {total_bad_lines} line(s)"
            + (f", {total_fixed} repaired" if args.apply else "")
        )
    finally:
        db.close()
        cdb.close()


if __name__ == "__main__":
    main()
