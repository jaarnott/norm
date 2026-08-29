"""Audit supplier→spec matching against every venue's REAL Loaded suppliers.

Unit tests prove the matcher does what it says; only this proves the answers
are right for the data we actually have. It asks, for every supplier record in
every connected venue, which layout spec that supplier's invoices would get —
and reports the three ways that goes wrong:

  UNREACHABLE  a spec no supplier in any venue can select. It will never be
               used in production, however good its text is. (Both 'Trents'
               specs were in this state: every account names the supplier
               plain 'Trents', and a spec name is never matched against a
               SHORTER supplier name.)
  AMBIGUOUS    two specs tie for one supplier, so neither is chosen — usually
               an alias filed on the wrong spec.
  SURPRISING   the spec chosen shares no identity word with the supplier,
               which is what a stolen alias looks like from the outside
               ('SERVICE FOODS AUCKLAND' -> the 'Eurovintage' spec).

Read-only. Run from apps/api:

    set -a && source ../../.env && set +a && uv run python scripts/audit_supplier_matching.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db.config_models import SupplierInvoiceSpec  # noqa: E402
from app.db.engine import SessionLocal, _ConfigSessionLocal  # noqa: E402
from app.db.models import Connection, Venue  # noqa: E402
from app.services.invoice_extraction import MAIN_PROMPT_NAME  # noqa: E402
from app.services.received_invoice import LoadedInvoiceClient  # noqa: E402
from app.services.supplier_identity import (  # noqa: E402
    live_suppliers,
    match_spec,
    words,
)


def main() -> int:
    db, cdb = SessionLocal(), _ConfigSessionLocal()
    specs = [
        s
        for s in cdb.query(SupplierInvoiceSpec).filter(
            SupplierInvoiceSpec.enabled.is_(True)
        )
        if s.name != MAIN_PROMPT_NAME
    ]
    venue_ids = {
        c.venue_id
        for c in db.query(Connection).filter(
            Connection.connector_name == "loadedhub",
            Connection.enabled == "true",
        )
        if c.venue_id
    }

    reached: set[str] = set()
    ambiguous: list[str] = []
    surprising: list[str] = []
    matched = unmatched = 0

    for vid in sorted(venue_ids):
        venue = db.query(Venue).filter(Venue.id == vid).first()
        vname = venue.name if venue else vid
        try:
            lh = LoadedInvoiceClient(db, cdb, vid)
            suppliers = live_suppliers(lh.get("/1.0/stock/internal/suppliers"))
        except Exception as exc:  # noqa: BLE001
            print(f"  {vname}: suppliers unavailable ({str(exc)[:60]})")
            continue
        print(f"  {vname}: {len(suppliers)} suppliers")
        for s in suppliers:
            name = s.get("name") or ""
            spec, how = match_spec(specs, [name], main_prompt_name=MAIN_PROMPT_NAME)
            if how == "ambiguous":
                ambiguous.append(f"{vname}: '{name}'")
                unmatched += 1
                continue
            if spec is None:
                unmatched += 1
                continue
            matched += 1
            reached.add(spec.name)
            if not (
                words(name)
                & (
                    words(spec.name)
                    | set().union(*(words(a) for a in (spec.aliases or [])) or [set()])
                )
            ):
                surprising.append(f"{vname}: '{name}' -> '{spec.name}' ({how})")

    print(f"\nmatched {matched} supplier records, {unmatched} matched no spec")

    unreachable = sorted({s.name for s in specs} - reached)
    for title, rows in (
        ("UNREACHABLE specs (no supplier anywhere selects them)", unreachable),
        ("AMBIGUOUS (two specs tie — nothing is chosen)", sorted(set(ambiguous))),
        (
            "SURPRISING (chosen spec shares no word with the supplier)",
            sorted(set(surprising)),
        ),
    ):
        print(f"\n{title}: {len(rows)}")
        for r in rows:
            print(f"  - {r}")

    return 1 if (unreachable or ambiguous or surprising) else 0


if __name__ == "__main__":
    raise SystemExit(main())
