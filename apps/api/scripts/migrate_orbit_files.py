"""Copy Orbit's files into Norm's app file storage, and keep them linked.

Three kinds of bytes hang off the mirrored records:

- **evidence** — photos of signed shift checklists and uploaded exercise
  evidence, referenced as URLs inside each completion's ``result.files``
  (Orbit's PUBLIC ``training-media`` bucket: anyone who ever saw a link keeps
  access, and nothing can delete them);
- **CVs** — ``candidate_documents`` rows, in Orbit's PRIVATE
  ``candidate-documents`` bucket (read with the service key);
- **media** — images and videos embedded in training content (an ``<img>`` in
  rich text, a video's ``url``), also in ``training-media``.

Each is copied into ``app_files`` (where a fetch re-runs the same permission
check the record gets), owned by the record it belongs to, and filed under a
``source_ref`` naming where it came from — so a re-run recognises it.

**Linking is re-done on every run.** The training/hiring mirrors rebuild their
records from Orbit, and the 21 Aug run reverted evidence links to Orbit URLs
(3 of 439 survived) while this script, seeing the URLs already copied, skipped
them and never re-linked. Now both sides apply one linking rule (see
``link_file_entry`` in migrate_orbit_training.py) and this script also re-links
everything already copied, so the result is the same whichever ran last:

- evidence: ``result.files`` entries become ``{file_id, name, source_url}``;
- CVs: the document row gets ``file_id``;
- media: the content row gets ``files: [{file_id, name, source_url}]``. Its
  body keeps the Orbit URLs — the Training app cannot yet resolve a Norm file
  inside rich text or a <video>, so rewriting them would break rendering.

Copied files whose record no longer exists (the mirror deleted it) are removed.
Files uploaded in Norm (no ``source_ref``) are never touched.

    uv run python scripts/migrate_orbit_files.py --org-email admin@norm.local --dry-run
    uv run python scripts/migrate_orbit_files.py --org-email admin@norm.local --limit 25
    uv run python scripts/migrate_orbit_files.py --org-email admin@norm.local
"""

from __future__ import annotations

import argparse
import copy
import pathlib
import sys
import urllib.parse
import uuid
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.migrate_orbit_hiring import CV_BUCKET  # noqa: E402 — path set above
from scripts.migrate_orbit_training import (  # noqa: E402
    NAMESPACE,
    Orbit,
    RunResult,
    TargetError,
    content_files,
    content_media_urls,
    copied_files,
    file_entry_url,
    link_result_files,
    resolve_org,
    storage_ref,
)

KINDS = ("evidence", "cvs", "media")
#: Collections whose files this script owns (and may clean up).
FILE_COLLECTIONS = ("completions", "candidate_documents", "content")
_CHUNK = 20


@dataclass
class Job:
    """One file to copy: where from, and which record it belongs to."""

    kind: str
    ref: str
    owner: object  # the AppRecord
    filename: str
    content_type: str | None
    url: str | None = None
    bucket: str | None = None
    path: str | None = None


def _basename(url: str) -> str:
    name = urllib.parse.unquote(url.split("?", 1)[0].rsplit("/", 1)[-1])
    return (name or "file")[:255]


def _orbit_rows(db, org_id: str, collection: str) -> list:
    """Mirrored records only. A record created in Norm has no orbit_id and is
    never touched here."""
    from app.db.models import AppRecord

    return [
        r
        for r in db.query(AppRecord).filter(
            AppRecord.namespace == NAMESPACE,
            AppRecord.organization_id == org_id,
            AppRecord.collection == collection,
        )
        if (r.data or {}).get("orbit_id")
    ]


def run(
    db,
    orbit,
    *,
    org_email: str,
    org_id: str | None = None,
    expect_org: str | None = None,
    dry_run: bool = False,
    limit: int = 0,
    kinds=KINDS,
    workers: int = 4,
    max_mb: float = 50.0,
    log=print,
) -> RunResult:
    from app.db.models import AppFile, AppRecord

    try:
        user, org = resolve_org(db, org_email, org_id, expect_org)
    except TargetError as exc:
        log(str(exc))
        return RunResult(code=1)
    log(f"target org: {org.name} ({org.id}) — via {user.email}")
    org_id = org.id

    copied = copied_files(db, org_id)
    log(f"{len(copied)} file(s) already copied into Norm")

    completions = _orbit_rows(db, org_id, "completions") if "evidence" in kinds else []
    documents = _orbit_rows(db, org_id, "candidate_documents") if "cvs" in kinds else []
    content = _orbit_rows(db, org_id, "content") if "media" in kinds else []

    # ---- 1. plan: every source not yet copied ------------------------------
    jobs: list[Job] = []
    planned: set[str] = set()
    refs = Counter()
    odd: list[str] = []

    def plan(job: Job) -> None:
        refs[job.kind] += 1
        if job.ref in copied or job.ref in planned:
            return
        planned.add(job.ref)
        jobs.append(job)

    for record in completions:
        result = (record.data or {}).get("result")
        for entry in (result.get("files") if isinstance(result, dict) else None) or []:
            url = file_entry_url(entry)
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                odd.append(f"completion {record.id[:8]}… — not a URL: {str(url)[:40]}")
                continue
            plan(Job("evidence", url, record, _basename(url), None, url=url))

    for record in documents:
        data = record.data or {}
        path = data.get("storage_path")
        if not path:
            continue
        plan(
            Job(
                "cvs",
                storage_ref(CV_BUCKET, path),
                record,
                str(data.get("file_name") or _basename(path))[:255],
                data.get("mime_type"),
                bucket=CV_BUCKET,
                path=path,
            )
        )

    for record in content:
        for url in content_media_urls((record.data or {}).get("body")):
            plan(Job("media", url, record, _basename(url), None, url=url))

    by_kind = Counter(j.kind for j in jobs)
    for kind in kinds:
        log(
            f"  {kind:9} {refs[kind]:>5} reference(s); {by_kind[kind]:>5} not yet copied"
        )
    for line in odd[:10]:
        log(f"    ? {line}")

    # ---- 2. copy (skipped entirely in a dry-run) ----------------------------
    copied_now = Counter()
    failed = Counter()
    too_big = Counter()
    bytes_in = 0
    todo = jobs[:limit] if limit else jobs
    if dry_run:
        log(f"\n  would copy {len(todo)} file(s) — a dry-run downloads nothing")
    else:

        def fetch(job: Job):
            try:
                if job.url:
                    return job, *orbit.fetch_url(job.url), None
                return job, *orbit.fetch_object(job.bucket, job.path), None
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the run
                return job, None, None, exc

        def store(job: Job, blob, content_type, exc) -> None:
            nonlocal bytes_in
            if exc is not None or not blob:
                failed[job.kind] += 1
                log(f"  ! {job.kind} {job.filename[:48]} — {exc or 'empty'}")
                return
            if len(blob) > max_mb * 1024 * 1024:
                too_big[job.kind] += 1
                log(
                    f"  ! {job.kind} {job.filename[:48]} — "
                    f"{len(blob) / 1024 / 1024:.1f} MB > --max-mb {max_mb:g}"
                )
                return
            row = AppFile(
                id=str(uuid.uuid4()),
                namespace=NAMESPACE,
                organization_id=org_id,
                venue_id=job.owner.venue_id,
                collection=job.owner.collection,
                record_id=job.owner.id,
                filename=job.filename,
                content_type=(
                    job.content_type or content_type or "application/octet-stream"
                )[:120],
                size_bytes=len(blob),
                data=blob,
                source_ref=job.ref,
                created_by=user.id,
            )
            db.add(row)
            copied[job.ref] = {"id": row.id, "filename": row.filename}
            copied_now[job.kind] += 1
            bytes_in += len(blob)

        # A sliding window, not fixed batches: one slow file must not stall
        # the others, and at most 2 x workers downloads are held in memory.
        # Downloads run in threads; every DB write stays on this thread.
        queue = iter(todo)
        in_flight: set = set()
        done = uncommitted = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:

            def refill() -> None:
                while len(in_flight) < 2 * max(1, workers):
                    job = next(queue, None)
                    if job is None:
                        return
                    in_flight.add(pool.submit(fetch, job))

            refill()
            while in_flight:
                finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in finished:
                    in_flight.discard(future)
                    store(*future.result())
                    done += 1
                    uncommitted += 1
                    if done % 200 == 0 or done == len(todo):
                        log(
                            f"  … {done}/{len(todo)} fetched, "
                            f"{bytes_in / 1024 / 1024:.0f} MB"
                        )
                if uncommitted >= _CHUNK:
                    # Commit as we go: a long copy that dies at file 1,900
                    # keeps the 1,899 before it, and the next run links them.
                    db.commit()
                    uncommitted = 0
                refill()
        db.commit()

    # ---- 3. (re)link every record to whatever is copied ---------------------
    if todo and not dry_run:
        # The per-chunk commits expired every loaded record; one query each
        # beats ten thousand lazy reloads.
        if "evidence" in kinds:
            completions = _orbit_rows(db, org_id, "completions")
        if "cvs" in kinds:
            documents = _orbit_rows(db, org_id, "candidate_documents")
        if "media" in kinds:
            content = _orbit_rows(db, org_id, "content")
    relinked = Counter()
    referenced: set[str] = set()

    def update(record, data: dict, kind: str) -> None:
        if data == (record.data or {}):
            return
        relinked[kind] += 1
        if not dry_run:
            record.data = copy.deepcopy(data)
            record.updated_by = user.id

    for record in completions:
        data = dict(record.data or {})
        result = data.get("result")
        if isinstance(result, dict) and result.get("files"):
            data["result"] = link_result_files(result, copied)
            for e in data["result"]["files"]:
                if isinstance(e, dict) and e.get("file_id"):
                    referenced.add(e["file_id"])
        update(record, data, "evidence")

    for record in documents:
        data = dict(record.data or {})
        hit = (
            copied.get(storage_ref(CV_BUCKET, data["storage_path"]))
            if data.get("storage_path")
            else None
        )
        if hit:
            data["file_id"] = hit["id"]
            referenced.add(hit["id"])
        else:
            data.pop("file_id", None)
        update(record, data, "cvs")

    for record in content:
        data = dict(record.data or {})
        files = content_files(data.get("body"), copied)
        if files:
            data["files"] = files
            referenced.update(f["file_id"] for f in files)
        else:
            data.pop("files", None)
        update(record, data, "media")

    # ---- 4. clean up copies whose record is gone ----------------------------
    live_records = {
        rid
        for (rid,) in db.query(AppRecord.id).filter(
            AppRecord.namespace == NAMESPACE,
            AppRecord.organization_id == org_id,
            AppRecord.collection.in_(FILE_COLLECTIONS),
        )
    }
    orphans: list[str] = []
    unreferenced_kept = 0
    for file_id, collection, record_id in db.query(
        AppFile.id, AppFile.collection, AppFile.record_id
    ).filter(
        AppFile.namespace == NAMESPACE,
        AppFile.organization_id == org_id,
        AppFile.source_ref.isnot(None),
        AppFile.collection.in_(FILE_COLLECTIONS),
    ):
        if file_id in referenced:
            continue
        if record_id in live_records:
            unreferenced_kept += 1
        else:
            orphans.append(file_id)
    # Only judge references for the kinds this run looked at — a run limited
    # to --only cvs has not computed evidence references.
    if set(kinds) != set(KINDS):
        orphans = []
        unreferenced_kept = 0

    # ---- report ------------------------------------------------------------
    log("")
    log(f"  {'kind':9} {'copied':>7} {'failed':>7} {'too big':>8} {'relinked':>9}")
    for kind in kinds:
        log(
            f"  {kind:9} {copied_now[kind]:>7} {failed[kind]:>7} {too_big[kind]:>8} "
            f"{relinked[kind]:>9}"
        )
    log(f"  {bytes_in / 1024 / 1024:.1f} MB copied this run")
    if orphans:
        log(f"  {len(orphans)} copied file(s) whose record is gone — deleting")
    if unreferenced_kept:
        log(
            f"  {unreferenced_kept} copied file(s) no longer referenced by their "
            "(still existing) record — kept"
        )
    if failed:
        log("  FAILED files stay pointing at Orbit; a re-run retries them")

    stats = {
        "refs": dict(refs),
        "pending": dict(by_kind),
        "copied": dict(copied_now),
        "failed": dict(failed),
        "too_big": dict(too_big),
        "relinked": dict(relinked),
        "orphans": len(orphans),
    }
    if dry_run:
        log("\n--dry-run: nothing written")
        return RunResult(code=0, stats=stats, org_name=org.name)
    if orphans:
        db.query(AppFile).filter(AppFile.id.in_(orphans)).delete(
            synchronize_session=False
        )
    db.commit()
    log("\nfiles mirrored")
    return RunResult(code=0, stats=stats, org_name=org.name)


def main(argv: list[str] | None = None, *, orbit=None, db=None) -> int:
    """CLI entry. ``orbit``/``db`` are injectable so tests run with no network
    and inside their own transaction."""
    ap = argparse.ArgumentParser(description="Copy Orbit's files into Norm")
    ap.add_argument(
        "--org-email",
        required=True,
        help="a member of the target organization; its name is printed first",
    )
    ap.add_argument("--org-id", help="which organization, when the member has several")
    ap.add_argument(
        "--expect-org", help="refuse unless the target organization has this name"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N downloads")
    ap.add_argument(
        "--only",
        default=",".join(KINDS),
        help=f"comma-separated subset of {', '.join(KINDS)}",
    )
    ap.add_argument("--workers", type=int, default=4, help="parallel downloads")
    ap.add_argument(
        "--max-mb", type=float, default=50.0, help="skip (and report) bigger files"
    )
    args = ap.parse_args(argv)
    kinds = tuple(k.strip() for k in args.only.split(",") if k.strip())
    unknown = set(kinds) - set(KINDS)
    if unknown:
        ap.error(f"unknown --only kind(s): {', '.join(sorted(unknown))}")

    owns_db = db is None
    if owns_db:
        # Progress lines must reach a log file as they happen, not at exit.
        sys.stdout.reconfigure(line_buffering=True)
        from app.db.engine import SessionLocal

        db = SessionLocal()
    try:
        result = run(
            db,
            orbit or Orbit(),
            org_email=args.org_email,
            org_id=args.org_id,
            expect_org=args.expect_org,
            dry_run=args.dry_run,
            limit=args.limit,
            kinds=kinds,
            workers=args.workers,
            max_mb=args.max_mb,
        )
        if owns_db and (result.code or args.dry_run):
            db.rollback()
        return result.code
    finally:
        if owns_db:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
