"""Seed ``ordermentum_pairs`` with the pairs already confirmed by hand.

Norm learns each Ordermentum supplier's number gap (its order number minus
Ordermentum's invoice number: 450 for every Sonoma pair) from settled pairs,
and only trusts an exact gap once a supplier has five of them
(services/ordermentum_pairs, docs/plans/ordermentum-pairing.md). The clean-ups
of 17 and 21 Oct 2026 confirmed several hundred pairs at Applegum Cafe,
Somewhere North and Little West; seeding them means the steady suppliers start
with their history instead of earning it again.

The pairs are customer data, so they are not in the repo: pass the JSON file
the clean-up produced — a list of objects with ``venue_id``, ``supplier_name``,
``ordermentum_invoice_id``, ``ordermentum_number``, ``supplier_invoice_id``,
``supplier_order_number``, ``delivered_on`` (YYYY-MM-DD) and, optionally,
``later_invoice_id``. A pair already recorded is left as it is.

Usage:
    .venv/bin/python scripts/seed_ordermentum_pairs.py pairs.json            # dry run
    .venv/bin/python scripts/seed_ordermentum_pairs.py pairs.json --apply
"""

import argparse
import datetime as dt
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pairs", help="JSON file of confirmed pairs")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()

    from app.db.engine import SessionLocal
    from app.db.models import OrdermentumPair
    from app.services.ordermentum_pairs import supplier_key

    pairs = json.loads(pathlib.Path(args.pairs).read_text())
    s = SessionLocal()
    added = Counter()
    try:
        have = {
            (o, p)
            for o, p in s.query(
                OrdermentumPair.ordermentum_invoice_id,
                OrdermentumPair.supplier_invoice_id,
            ).all()
        }
        for p in pairs:
            ident = (p["ordermentum_invoice_id"], p["supplier_invoice_id"])
            if ident in have:
                added["already recorded"] += 1
                continue
            om, sup = p.get("ordermentum_number"), p.get("supplier_order_number")
            row = OrdermentumPair(
                venue_id=p.get("venue_id"),
                supplier_key=supplier_key(p["supplier_name"]),
                supplier_name=p["supplier_name"],
                ordermentum_invoice_id=p["ordermentum_invoice_id"],
                ordermentum_number=om,
                supplier_invoice_id=p["supplier_invoice_id"],
                supplier_order_number=sup,
                gap=(sup - om) if om is not None and sup is not None else None,
                delivered_on=dt.date.fromisoformat(p["delivered_on"])
                if p.get("delivered_on")
                else None,
                status="paired",
                how="seed",
                later_invoice_id=p.get("later_invoice_id"),
            )
            s.add(row)
            have.add(ident)
            added[supplier_key(p["supplier_name"])] += 1
        if args.apply:
            s.commit()
        else:
            s.rollback()
    finally:
        s.close()
    print(("written" if args.apply else "dry run — would write") + ":")
    for k, n in sorted(added.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {n}")


if __name__ == "__main__":
    main()
