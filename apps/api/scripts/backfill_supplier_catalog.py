"""Seed Norm's supplier-product catalogue from evidence we already hold.

    python -m scripts.backfill_supplier_catalog [--dry-run]

Two passes, in provenance order:
1. Dojo baselines (config DB) — admin-verified expected extractions,
   provenance ``human`` (the top tier: a person confirmed these values).
2. The CURRENT environment's extraction archive (``document_extractions``) —
   sizes the suppliers printed on real invoice pages, provenance ``printed``.

Idempotent: evidence is deduped per invoice number, so re-runs (and the live
observe-on-extract write-back re-seeing the same invoices) accumulate
nothing. Run locally first; run against production via the Cloud SQL proxy
recipe after deploy — its archive is the real prize.

Reads the env exactly like the app (DATABASE_URL + CONFIG_DATABASE_URL);
no LLM calls, no Loaded calls, no writes outside ``supplier_products``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scripts.backfill_supplier_catalog")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.backfill_supplier_catalog")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written, then roll back",
    )
    args = parser.parse_args(argv)

    from app.db.config_models import SupplierInvoiceSpec, SupplierProduct
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import DocumentExtraction
    from app.services import supplier_catalog

    db = SessionLocal()
    config_db = (_ConfigSessionLocal or SessionLocal)()
    stats: Counter = Counter()
    try:
        # ---- Pass 1: dojo baselines (human) ----
        from app.db.config_models import SupplierSpecSample

        spec_names = {s.id: s.name for s in config_db.query(SupplierInvoiceSpec).all()}
        for sample in (
            config_db.query(SupplierSpecSample)
            .filter(SupplierSpecSample.expected.isnot(None))
            .all()
        ):
            key = spec_names.get(sample.spec_id)
            if not key:
                stats["baseline_no_spec"] += 1
                continue
            out = supplier_catalog.observe_extraction(
                config_db,
                sample.expected,
                provenance="human",
                supplier_key=key,
            )
            stats["baseline_lines"] += out["observed"]
            stats["baselines"] += 1

        # ---- Pass 2: the extraction archive (printed) ----
        rows = (
            db.query(DocumentExtraction)
            .filter(DocumentExtraction.action == "download_invoice_file")
            .all()
        )
        for row in rows:
            out = supplier_catalog.observe_extraction(
                config_db, row.data, provenance="printed"
            )
            if out["skipped"] == "no supplier spec":
                stats["archive_no_spec"] += 1
            else:
                stats["archive_lines"] += out["observed"]
                stats["archive_rows"] += 1

        # ---- Summary ----
        products = config_db.query(SupplierProduct).all()
        by_prov = Counter(p.provenance for p in products)
        answered = sum(
            1 for p in products if p.unit_name or p.pack_type == "random_weight"
        )
        conflicts = sum(
            1
            for p in products
            if p.unit_name is None
            and any((p.evidence or {}).get(t) for t in ("human", "printed"))
        )
        print(
            f"baselines: {stats['baselines']} observed "
            f"({stats['baseline_lines']} sized lines); "
            f"archive rows: {stats['archive_rows']} observed "
            f"({stats['archive_lines']} sized lines), "
            f"{stats['archive_no_spec']} skipped (no supplier spec)"
        )
        print(
            f"catalogue: {len(products)} products | answered: {answered} | "
            f"open questions (conflicts): {conflicts} | provenance: "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_prov.items()))
        )
        if args.dry_run:
            config_db.rollback()
            print("dry run — rolled back")
        else:
            config_db.commit()
            print("committed")
        return 0
    finally:
        db.close()
        config_db.close()


if __name__ == "__main__":
    sys.exit(main())
