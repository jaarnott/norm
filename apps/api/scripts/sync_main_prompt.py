"""Make the shared "Main prompt" row match the code's BUILTIN_MAIN_PROMPT.

The invoice-reading prompt lives twice: the admin-editable row in the shared
config DB (what every environment reads) and the built-in copy in
``services/invoice_extraction.py`` (the fallback and the test harness's
reference). A change to the reading rules ships in the code, then this script
puts the same text in the row. Both must change together.

The row is part of every reading's cache key, so the venues' invoices not yet
received are read again once, on their next review (about 10c each).

Shows the difference and writes nothing unless --apply. The row as it was is
saved to ~/norm-split-backups first.

Usage:
    .venv/bin/python scripts/sync_main_prompt.py           # show the difference
    .venv/bin/python scripts/sync_main_prompt.py --apply   # write it
"""

import argparse
import datetime as dt
import difflib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import SupplierInvoiceSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services.invoice_extraction import BUILTIN_MAIN_PROMPT, MAIN_PROMPT_NAME

    db = _ConfigSessionLocal()
    try:
        row = (
            db.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.name == MAIN_PROMPT_NAME)
            .first()
        )
        if row is None:
            sys.exit(
                f"no '{MAIN_PROMPT_NAME}' row — the built-in copy is in use already"
            )
        current = row.instructions or ""
        if current == BUILTIN_MAIN_PROMPT:
            print("the row already matches the built-in prompt — nothing to do")
            return
        for line in difflib.unified_diff(
            current.splitlines(),
            BUILTIN_MAIN_PROMPT.splitlines(),
            "row",
            "built-in",
            lineterm="",
            n=0,
        ):
            print(line)
        if not args.apply:
            print("\nshown only — rerun with --apply to write it")
            return
        path = os.path.expanduser(
            f"~/norm-split-backups/main-prompt-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"id": row.id, "name": row.name, "instructions": current}, fh)
        row.instructions = BUILTIN_MAIN_PROMPT
        db.commit()
        print(f"\nwritten; the previous text is in {path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
