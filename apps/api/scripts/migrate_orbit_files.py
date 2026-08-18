"""Copy Orbit's training evidence into Norm's app file storage.

868 files hang off 412 completions — photos of signed shift checklists, uploaded
exercise evidence. In Orbit they live in the **public** `training-media` bucket
and are referenced only as URLs inside a JSONB blob, so anyone who ever saw a
link keeps access indefinitely and nothing can delete them.

This copies the bytes into `app_files`, where a fetch re-runs the same
permission check the record itself gets, and rewrites each completion's
`result.files` to point at the Norm file id — keeping the original URL beside
it as `source_url` so nothing is lost and a re-run can recognise what it
already did.

Idempotent: a file already carrying that `source_ref` is skipped.

    uv run python scripts/migrate_orbit_files.py --org-email admin@norm.local --dry-run
    uv run python scripts/migrate_orbit_files.py --org-email admin@norm.local --limit 25
    uv run python scripts/migrate_orbit_files.py --org-email admin@norm.local
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.migrate_orbit_training import NAMESPACE  # noqa: E402 — path set above

#: Orbit's evidence is served from a public bucket, so no credential is needed
#: to read it — which is precisely the problem being fixed.
_TIMEOUT = 60


def _download(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(), resp.headers.get("content-type", "application/octet-stream")


def _refs(result: dict) -> list[str]:
    out = []
    for entry in (result or {}).get("files") or []:
        url = (
            entry if isinstance(entry, str) else (entry.get("url") or entry.get("path"))
        )
        if url:
            out.append(url)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-email", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N files")
    args = ap.parse_args()

    from app.db.engine import SessionLocal
    from app.db.models import AppFile, AppRecord, OrganizationMembership, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.org_email).first()
        membership = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == user.id)
            .first()
            if user
            else None
        )
        if not membership:
            print(f"no org membership for {args.org_email}")
            return 1
        org_id = membership.organization_id

        done = {
            f.source_ref
            for f in db.query(AppFile)
            .filter(AppFile.namespace == NAMESPACE, AppFile.organization_id == org_id)
            .all()
            if f.source_ref
        }

        completions = (
            db.query(AppRecord)
            .filter(
                AppRecord.namespace == NAMESPACE,
                AppRecord.organization_id == org_id,
                AppRecord.collection == "completions",
            )
            .all()
        )
        # The file list lives inside the completion's `result` blob, which the
        # training migration carried across whole.
        with_files = [
            c for c in completions if _refs((c.data or {}).get("result") or {})
        ]
        total_refs = sum(
            len(_refs((c.data or {}).get("result") or {})) for c in with_files
        )
        print(f"{len(with_files)} completions carry {total_refs} file reference(s)")
        print(f"{len(done)} already copied")

        copied = skipped = failed = 0
        bytes_in = 0
        for record in with_files:
            data = dict(record.data or {})
            result = dict(data.get("result") or {})
            rewritten = []
            changed = False
            for entry in result.get("files") or []:
                url = (
                    entry
                    if isinstance(entry, str)
                    else (entry.get("url") or entry.get("path"))
                )
                if not url:
                    rewritten.append(entry)
                    continue
                if url in done:
                    skipped += 1
                    rewritten.append(entry)
                    continue
                if args.limit and copied >= args.limit:
                    rewritten.append(entry)
                    continue
                try:
                    blob, content_type = _download(url)
                except Exception as exc:  # noqa: BLE001 — one bad file must not stop the run
                    failed += 1
                    print(f"  ! {url.rsplit('/', 1)[-1][:48]} — {exc}")
                    rewritten.append(entry)
                    continue

                filename = url.rsplit("/", 1)[-1][:255]
                if not args.dry_run:
                    row = AppFile(
                        id=str(uuid.uuid4()),
                        namespace=NAMESPACE,
                        organization_id=org_id,
                        venue_id=record.venue_id,
                        collection="completions",
                        record_id=record.id,
                        filename=filename,
                        content_type=content_type,
                        size_bytes=len(blob),
                        data=blob,
                        source_ref=url,
                        created_by=user.id,
                    )
                    db.add(row)
                    db.flush()
                    # Point at the Norm file, keep the origin beside it.
                    rewritten.append(
                        {"file_id": row.id, "name": filename, "source_url": url}
                    )
                    changed = True
                else:
                    rewritten.append(entry)
                copied += 1
                bytes_in += len(blob)
                done.add(url)

            if changed and not args.dry_run:
                result["files"] = rewritten
                data["result"] = result
                # The flattened keys stay as they are — only the file list moves.
                record.data = data

        print()
        print(f"  copied  {copied} file(s), {bytes_in / 1024 / 1024:.1f} MB")
        print(f"  skipped {skipped} already present")
        if failed:
            print(f"  FAILED  {failed} — left pointing at Orbit")
        if args.dry_run:
            db.rollback()
            print("\n--dry-run: nothing written")
            return 0
        db.commit()
        print("\nmigrated")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
