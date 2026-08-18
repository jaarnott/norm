"""Recompute stored autopilot outcomes with the current classifier.

Two defects made `manual_edits` report fields nobody typed, and every affected
receive was recorded as `edited`. The rates are computed from the STORED rows,
so fixing the classifier only helps future receives — the history keeps saying
autopilot would have been wrong.

  1. The header baseline was read from the wrong level of `loaded_snapshot`,
     so every populated header field always "differed" (29 of the first 31
     production rows, 4-9 phantom edits each).
  2. `seed_working_from_loaded` completes a line from the supplier variant
     AFTER the baseline is captured, so the fields Norm filled in read as
     hand edits (Service Foods IN11437881: unit, linked_unit_id, unit_ratio,
     linked_item_id).

This re-derives each row from the working document it was recorded against.
Fix 1 applies exactly to old documents — the baseline is there, it was simply
read wrongly. Fix 2 CANNOT be applied retroactively: it depends on
`server_filled`, which old documents do not carry, and guessing would clear
genuine hand-links (linking an item Norm never suggested is exactly the case
the metric exists to catch). Those rows are reported, not rewritten.

Usage:
    uv run python scripts/backfill_autopilot_outcomes.py            # dry run
    uv run python scripts/backfill_autopilot_outcomes.py --apply
    DATABASE_URL=... uv run python scripts/backfill_autopilot_outcomes.py --apply
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

#: The fields seeding completes. A row still `edited` on nothing but these,
#: where Loaded's own line held no value, is very likely fix 2 — but "very
#: likely" is not good enough to overwrite a measurement with.
_SEEDED_FIELDS = {"unit", "linked_unit_id", "unit_ratio", "linked_item_id"}


def _suspect_seeded(doc: dict, fields: list[str]) -> bool:
    """True when every remaining edit looks like seeding rather than a person.

    Seeding fills a link Loaded left empty, so an empty baseline is the signal
    — except for `unit`, which it OVERWRITES with the variant's name in the
    same breath as setting `linked_unit_id` (Loaded said "KG", the variant says
    "Kilo"). So `unit` counts only alongside a `linked_unit_id` that Loaded
    itself had empty; on its own it is someone retyping a unit, which is
    exactly what the metric is for.
    """
    if not fields or any(not f.startswith("line:") for f in fields):
        return False
    snap = {
        str(ln.get("id")): ln
        for ln in ((doc.get("loaded_snapshot") or {}).get("lines") or [])
        if isinstance(ln, dict)
    }
    by_line: dict[str, set[str]] = {}
    for path in fields:
        lid, _, field = path[len("line:") :].partition(".")
        if field not in _SEEDED_FIELDS:
            return False
        by_line.setdefault(lid, set()).add(field)

    for lid, touched in by_line.items():
        base = snap.get(lid) or {}
        linked_unit_seeded = (
            "linked_unit_id" in touched and base.get("linked_unit_id") is None
        )
        for field in touched:
            if field == "unit":
                if not linked_unit_seeded:
                    return False
            elif base.get(field) is not None:
                return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    from app.db.engine import SessionLocal
    from app.db.models import InvoiceAutopilotOutcome, WorkingDocument
    from app.services.autopilot_metrics import classify_outcome

    db = SessionLocal()
    changed: list[str] = []
    suspect: list[str] = []
    unchanged = skipped = collided = 0
    try:
        rows = (
            db.query(InvoiceAutopilotOutcome)
            .order_by(InvoiceAutopilotOutcome.created_at)
            .all()
        )
        # Every (venue, invoice, outcome) already taken — the table's unique
        # key. Re-deriving into one that exists would raise, so those are
        # reported rather than forced.
        taken = {(r.venue_id, r.invoice_id, r.outcome) for r in rows}

        for row in rows:
            if not row.working_document_id:
                skipped += 1
                continue
            doc = (
                db.query(WorkingDocument)
                .filter(WorkingDocument.id == row.working_document_id)
                .first()
            )
            data = (doc.data if doc else None) or {}
            if not isinstance(data, dict) or not data.get("loaded_snapshot"):
                skipped += 1
                continue

            # `dojo` is a human verdict ("Norm can't do this one"), not a
            # measurement of the document — never re-derive it.
            if row.outcome == "dojo":
                unchanged += 1
                continue

            fresh = classify_outcome(data, received=bool(row.received))
            label = row.reference_number or row.invoice_id

            still = (fresh.get("detail") or {}).get("manual_fields") or []
            if _suspect_seeded(data, still):
                suspect.append(
                    f"{label}: still {fresh['outcome']} on "
                    f"{len(still)} field(s) the server filled"
                )

            if fresh["outcome"] == row.outcome and fresh["manual_edit_count"] == (
                row.manual_edit_count or 0
            ):
                unchanged += 1
                continue

            if (
                fresh["outcome"] != row.outcome
                and (
                    row.venue_id,
                    row.invoice_id,
                    fresh["outcome"],
                )
                in taken
            ):
                collided += 1
                continue

            changed.append(
                f"{label}: {row.outcome} → {fresh['outcome']} "
                f"(manual {row.manual_edit_count} → {fresh['manual_edit_count']})"
            )
            if args.apply:
                taken.discard((row.venue_id, row.invoice_id, row.outcome))
                taken.add((row.venue_id, row.invoice_id, fresh["outcome"]))
                for field in (
                    "outcome",
                    "suggestion_count",
                    "accepted_count",
                    "dismissed_count",
                    "pending_count",
                    "manual_edit_count",
                    "blocking_issue_count",
                    "issues_waved_count",
                    "confidence",
                ):
                    setattr(row, field, fresh[field])
                detail = dict(row.detail or {})
                detail.update(fresh["detail"])
                # Say so on the row itself: a rewritten measurement that does
                # not admit it was rewritten is worse than the wrong number.
                detail["recomputed"] = True
                row.detail = detail

        if args.apply:
            db.commit()

        for line in changed:
            print("  " + line)
        print(
            f"\n{len(changed)} row(s) {'updated' if args.apply else 'would change'}, "
            f"{unchanged} already correct, {skipped} without a usable document, "
            f"{collided} left alone (an outcome row already exists)"
        )
        if suspect:
            print(
                f"\n{len(suspect)} row(s) still read `edited` on fields the SERVER "
                "probably filled (fix 2). Old documents don't record what was\n"
                "filled, and guessing would clear genuine hand-links, so these "
                "are left as they are:"
            )
            for line in suspect[:20]:
                print("  " + line)
        if not args.apply:
            print("\n(dry run — nothing written; re-run with --apply)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
