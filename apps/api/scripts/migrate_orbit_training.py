"""Mirror Orbit's training data into the Norm Training app's storage.

Reads Orbit's Supabase directly over PostgREST and writes ``app_records`` in the
``hr_suite`` namespace. **Read-only against Orbit** — nothing here writes back,
so it can be run against a live system as often as you like.

Why not through Orbit's MCP connector, which Norm already has: all of its
training programs are group-wide (``venue_id IS NULL``) and its API filters
programs by ``venue_id IN (...)``, so that surface returns **zero** programs and
**zero** assignments. Migrating through it would have looked like a clean run
over an empty dataset.

**A mirror, not a seed.** Orbit stays the system of record until cut-over, so
every run makes Norm's copy match it:

- every record carries the Orbit uuid it came from (``data->>'orbit_id'``) and
  a re-run updates it in place — a row whose data did not change is left alone,
  so a second run in a row reports no changes at all;
- a row whose ``orbit_id`` no longer exists in Orbit is DELETED. Only rows that
  carry an ``orbit_id`` are ever considered: anything created in Norm is never
  touched;
- a delete that looks like a broken read (a collection emptied, or more than a
  quarter of it going at once) stops the run before anything is committed,
  unless ``--allow-mass-delete`` says the operator has read the dry-run.

Run order is training → hiring → files: hiring maps people this script wrote,
and the files step copies bytes for records both of them wrote.

    uv run python scripts/migrate_orbit_training.py --org-email admin@norm.local --dry-run
    uv run python scripts/migrate_orbit_training.py --org-email admin@norm.local

Credentials: ``$ORBIT_SUPABASE_CREDS``, else ``.local/orbit-supabase.json`` at
the repo root (git-ignored; a git worktree falls back to its main checkout's).
They are never printed, and neither is any row's content.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.parse
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

#: scripts/ → api/ → apps/ → the repo root, where the git-ignored .local lives.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CREDS_ENV = "ORBIT_SUPABASE_CREDS"
CREDS_NAME = "orbit-supabase.json"
NAMESPACE = "hr_suite"

#: The collections THIS script owns — the only ones its delete pass may touch.
TRAINING_COLLECTIONS = (
    "people",
    "programs",
    "modules",
    "sections",
    "content",
    "assignments",
    "completions",
    "plans",
    "plan_sections",
    "module_signoffs",
    "capability_frameworks",
    "review_cycles",
    "performance_reviews",
    "performance_review_emails",
    "performance_review_email_settings",
    "remuneration",
    "auto_enroll_rules",
    "reminder_settings",
    "reminder_emails",
)

#: A delete pass bigger than this (rows AND share of the collection) is far
#: more likely to be a broken read than real churn in Orbit — see Mirror.guard.
MASS_DELETE_MIN = 25
MASS_DELETE_SHARE = 0.25

#: Orbit venue name -> Norm venue name, for names that cannot be matched by
#: rule. Norm's venue names differ BETWEEN environments (local calls one
#: "Bessie & Royals", production "Bessie & Engineers"), so a single hardcoded
#: map is wrong by construction — `resolve_venues` below matches by rule and
#: prints what it decided, and this is only for genuine exceptions.
VENUE_ALIASES: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Credentials and the read-only Orbit client
# ---------------------------------------------------------------------------


def _main_checkout(root: pathlib.Path) -> pathlib.Path | None:
    """The main checkout behind a git worktree, or None.

    ``.local`` is per checkout and git-ignored, so a worktree never has one of
    its own. A worktree's ``.git`` is a FILE reading
    ``gitdir: <main>/.git/worktrees/<name>`` — which names the main checkout.
    """
    marker = root / ".git"
    if not marker.is_file():
        return None
    text = marker.read_text().strip()
    if not text.startswith("gitdir:"):
        return None
    gitdir = pathlib.Path(text.split(":", 1)[1].strip())
    if gitdir.parent.name == "worktrees":
        return gitdir.parent.parent.parent
    return None


def creds_path() -> pathlib.Path:
    """Where Orbit's service credentials live: the env override, else the repo
    root's ``.local`` (or the main checkout's, from a worktree)."""
    override = os.environ.get(CREDS_ENV)
    if override:
        return pathlib.Path(override).expanduser()
    candidates = [REPO_ROOT / ".local" / CREDS_NAME]
    main = _main_checkout(REPO_ROOT)
    if main is not None:
        candidates.append(main / ".local" / CREDS_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "no Orbit credentials — looked in "
        + ", ".join(str(c) for c in candidates)
        + f"; set {CREDS_ENV} to the file"
    )


#: One keep-alive connection pool for every request to Orbit. Opening a fresh
#: TCP connection per request (what urllib does) exhausts the dev box's Cloud
#: NAT ports for a single destination within a minute — each closed port is
#: held ~2 minutes — and a copy of 2,000 files then crawls at ~1 file/second
#: with connects stuck in SYN-SENT. Reusing a handful of connections avoids it.
_client = None
_client_lock = threading.Lock()


def _http():
    global _client
    with _client_lock:
        if _client is None:
            import httpx

            # No redirects: the service key travels on authenticated reads,
            # and httpx would forward a custom `apikey` header to wherever a
            # redirect pointed. Supabase serves objects directly.
            _client = httpx.Client(
                http2=False,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
            )
        return _client


class OrbitHTTPError(RuntimeError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url.split('?', 1)[0]}")
        self.status = status


def _http_get(
    url: str, headers: dict | None = None, *, method: str = "GET", timeout: int = 120
):
    """GET (or HEAD) with a few retries for the transient failures Supabase's
    storage throws under load. Returns ``(body, response headers)``. A 4xx is
    not transient and is raised at once."""
    import httpx

    if method not in ("GET", "HEAD"):
        raise ValueError("the Orbit client only ever reads")  # never a write
    last: Exception | None = None
    for attempt in range(4):
        try:
            resp = _http().request(method, url, headers=headers or {}, timeout=timeout)
        except httpx.TransportError as exc:
            last = exc
        else:
            if resp.status_code < 400:
                return resp.content, resp.headers
            if resp.status_code < 500:
                raise OrbitHTTPError(resp.status_code, url)
            last = OrbitHTTPError(resp.status_code, url)
        time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


class Orbit:
    """A read-only Supabase client: PostgREST rows and storage objects.

    Only ever issues GETs (and a HEAD to count). Nothing here can change Orbit.
    """

    def __init__(self, creds: pathlib.Path | None = None) -> None:
        path = creds or creds_path()
        cfg = json.loads(path.read_text())
        key = cfg.get("service_role_key") or ""
        if not key:
            raise SystemExit(f"no service_role_key in {path}")
        root = cfg["url"].rstrip("/")
        self.base = root + "/rest/v1"
        self.storage = root + "/storage/v1"
        self.public_prefix = self.storage + "/object/public/"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def rows(self, table: str, select: str = "*", page: int = 1000) -> list[dict]:
        """Every row of a table, in a STABLE order.

        Keyset-paged on ``id`` (``id > last``) rather than offset-paged: on a
        live table an insert or delete mid-read shifts every later offset, so
        offset pages skip or repeat rows — and for a mirror a skipped row reads
        as "deleted in Orbit". Keyset pages cannot skip a row that existed for
        the whole read.
        """
        cols = select
        if select != "*" and "id" not in [c.strip() for c in select.split(",")]:
            cols = "id," + select
        out: list[dict] = []
        last = None
        while True:
            query = f"select={cols}&order=id.asc&limit={page}"
            if last is not None:
                bound = str(last).lower() if isinstance(last, bool) else str(last)
                query += f"&id=gt.{urllib.parse.quote(bound)}"
            body, _ = _http_get(f"{self.base}/{table}?{query}", self.headers)
            batch = json.loads(body.decode())
            out.extend(batch)
            if len(batch) < page:
                return out
            if batch[-1]["id"] == last:
                raise RuntimeError(f"{table}: keyset paging made no progress")
            last = batch[-1]["id"]

    def count(self, table: str) -> int:
        """Orbit's own row count (HEAD + count=exact) — for reconciliation."""
        _, headers = _http_get(
            f"{self.base}/{table}?select=*",
            {**self.headers, "Prefer": "count=exact", "Range": "0-0"},
            method="HEAD",
        )
        total = (headers.get("content-range") or "*/0").rsplit("/", 1)[-1]
        return int(total) if total.isdigit() else 0

    def fetch_url(self, url: str) -> tuple[bytes, str]:
        """A public object by its URL. Deliberately WITHOUT our key: a public
        bucket needs none, and the key must never travel to a URL taken from
        row content. Only Orbit's own public storage: a URL from row content
        must not make this script fetch anything else (an internal address,
        another host)."""
        if not str(url or "").startswith(self.public_prefix) or "/../" in url:
            # A 400-style refusal: the files script records it as one failed
            # file and carries on.
            raise OrbitHTTPError(
                400, "refused (not Orbit public storage): " + str(url)[:120]
            )
        body, headers = _http_get(url, timeout=90)
        return body, headers.get("content-type") or "application/octet-stream"

    def fetch_object(self, bucket: str, path: str) -> tuple[bytes, str]:
        """A PRIVATE storage object (e.g. a CV), read with the service key via
        the authenticated object endpoint — a plain GET, no signed URL minted."""
        parts = str(path or "").replace("\\", "/").split("/")
        if not path or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
            # A storage_path from row content must stay inside its bucket —
            # `..` would walk the service key to other storage endpoints.
            raise OrbitHTTPError(400, "refused storage path: " + str(path)[:120])
        url = f"{self.storage}/object/authenticated/{bucket}/{urllib.parse.quote(path)}"
        body, headers = _http_get(url, self.headers, timeout=90)
        return body, headers.get("content-type") or "application/octet-stream"


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _pick(row: dict, *keys: str) -> dict:
    """The named fields, dropping the ones Orbit left null — a record should
    not be full of explicit nulls just because the source column exists."""
    return {k: row[k] for k in keys if row.get(k) is not None}


def orbit_alias(field_name: str) -> str:
    """Where a person reference's raw Orbit id is kept: ``manager_id`` →
    ``manager_orbit_id``, ``completed_by`` → ``completed_by_orbit_id``."""
    base = field_name[:-3] if field_name.endswith("_id") else field_name
    return f"{base}_orbit_id"


def person_ref(data: dict, field_name: str, orbit_person_id, people: dict) -> None:
    """Point ``field_name`` at the Norm person an Orbit team-member id became.

    The raw Orbit id is ALWAYS kept beside it (see ``orbit_alias``): a person
    who is not migrated yet still leaves a trail, and a later run that does
    migrate them can resolve it. Where no Norm person exists the field itself
    is simply absent — never a raw Orbit id masquerading as a Norm one.
    """
    if not orbit_person_id:
        return
    data[orbit_alias(field_name)] = orbit_person_id
    norm_id = people.get(orbit_person_id)
    if norm_id:
        data[field_name] = norm_id


def person_refs(data: dict, field_name: str, orbit_ids, people: dict) -> None:
    """``person_ref`` for a LIST of team-member ids: ``interviewer_ids`` holds
    the Norm people that resolved, ``interviewer_orbit_ids`` every raw id."""
    raw = [i for i in (orbit_ids or []) if i]
    if not raw:
        return
    base = field_name[:-4] if field_name.endswith("_ids") else field_name
    data[f"{base}_orbit_ids"] = raw
    data[field_name] = [people[i] for i in raw if i in people]


# ---- files: one linking rule, shared by every script ------------------------
#
# The training/hiring scripts rebuild records from Orbit on every run, which
# used to revert evidence links to Orbit URLs (the 21 Aug run left 3 of 439
# linked). So linking is a pure function of (Orbit data, files already copied)
# and every script applies it: whichever runs, the record comes out the same.

#: A Supabase storage URL embedded in content (an <img src> in rich text, a
#: video's url). Stops at a quote, bracket, whitespace or backslash.
_STORAGE_URL = re.compile(
    r"https://[a-z0-9]+\.supabase\.co/storage/v1/object/public/[^\s\"'<>)\\]+"
)


def storage_ref(bucket: str, path: str) -> str:
    """The ``source_ref`` a PRIVATE storage object is filed under. Not a URL:
    a signed URL changes on every mint, and this must not."""
    return f"orbit-storage:{bucket}/{path}"


def file_entry_url(entry) -> str | None:
    """The Orbit URL behind one ``result.files`` entry, linked or not."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("source_url") or entry.get("url") or entry.get("path")
    return None


def link_file_entry(entry, copied: dict):
    """One evidence entry, pointed at its Norm file when the URL was copied.

    ``copied`` maps source_ref → ``{"id", "filename"}``. The linked shape —
    ``{file_id, name, source_url}`` — is the one the 18 Aug copy wrote, and it
    keeps the origin so a re-run recognises it. Not copied (yet): left as is.
    """
    url = file_entry_url(entry)
    hit = copied.get(url) if url else None
    if hit:
        return {"file_id": hit["id"], "name": hit["filename"], "source_url": url}
    if isinstance(entry, dict) and entry.get("file_id") and entry.get("source_url"):
        # A link to a copy that no longer exists: back to the Orbit URL it
        # came from — exactly what a fresh mirror of that entry would write.
        return entry["source_url"]
    return entry


def link_result_files(result, copied: dict):
    """A completion's ``result`` with its ``files`` list linked (a copy)."""
    if not isinstance(result, dict) or not result.get("files"):
        return result
    out = dict(result)
    out["files"] = [link_file_entry(e, copied) for e in result.get("files") or []]
    return out


def _linked_count(result) -> int:
    if not isinstance(result, dict):
        return 0
    return sum(
        1 for e in result.get("files") or [] if isinstance(e, dict) and e.get("file_id")
    )


def _strings(value):
    """Every string inside a JSON value, depth first, in order."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def content_media_urls(body) -> list[str]:
    """Orbit storage URLs embedded in a content body (an <img src> inside rich
    text, a video's ``url``), in order of appearance, de-duplicated."""
    seen: list[str] = []
    for text in _strings(body):
        for url in _STORAGE_URL.findall(text):
            url = html.unescape(url)  # an attribute may carry &amp;
            if url not in seen:
                seen.append(url)
    return seen


def content_files(body, copied: dict) -> list[dict]:
    """The Norm file references for a content body's embedded media.

    The body keeps its Orbit URLs, so it renders exactly as before — the app
    has no way yet to resolve a Norm file inside rich text or a <video>. This
    list (same shape as evidence links) is what lets it swap them later.
    """
    out = []
    for url in content_media_urls(body):
        hit = copied.get(url)
        if hit:
            out.append(
                {"file_id": hit["id"], "name": hit["filename"], "source_url": url}
            )
    return out


def copied_files(db, org_id: str) -> dict[str, dict]:
    """Files already copied into Norm, by source_ref. Metadata only — the
    bytes column is never loaded (hundreds of MB once media is in)."""
    from app.db.models import AppFile

    out: dict[str, dict] = {}
    q = (
        db.query(AppFile.id, AppFile.source_ref, AppFile.filename, AppFile.created_at)
        .filter(
            AppFile.namespace == NAMESPACE,
            AppFile.organization_id == org_id,
            AppFile.source_ref.isnot(None),
        )
        .order_by(AppFile.created_at, AppFile.id)
    )
    for file_id, ref, filename, _ in q:
        # Oldest wins, so a duplicate copy never flips which file a link names.
        out.setdefault(ref, {"id": file_id, "filename": filename})
    return out


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------


def _venue_key(name: str) -> str:
    """A venue name reduced to something comparable across systems: lowercase,
    no leading 'the', alphanumerics only. 'The Glass Goose' == 'Glass Goose'."""
    text = str(name or "").strip().lower()
    if text.startswith("the "):
        text = text[4:]
    return "".join(ch for ch in text if ch.isalnum())


def resolve_venues(orbit_venues: list[dict], norm_venues: dict[str, str]):
    """Map Orbit venues onto Norm's, and say exactly what happened.

    Three passes, narrowest first: exact name, then an explicit alias, then a
    normalised prefix match ('Bessie' -> 'Bessie & Engineers'). Anything left
    over lands group-wide, which is visible to everyone rather than lost — but
    it is REPORTED, because silently widening a venue's rows to the whole group
    is exactly the kind of thing nobody notices until it matters.
    """
    mapping: dict[str, str] = {}
    decided: list[str] = []
    unmapped: list[str] = []
    for v in orbit_venues:
        name = v.get("name") or ""
        target = None
        if name in norm_venues:
            target, how = norm_venues[name], "exact"
        elif VENUE_ALIASES.get(name) in norm_venues:
            target, how = norm_venues[VENUE_ALIASES[name]], "alias"
        else:
            key = _venue_key(name)
            hits = [
                (nname, vid)
                for nname, vid in norm_venues.items()
                if _venue_key(nname) == key
                or _venue_key(nname).startswith(key)
                or key.startswith(_venue_key(nname))
            ]
            if len(hits) == 1:
                target, how = hits[0][1], f"matched '{hits[0][0]}'"
            elif len(hits) > 1:
                # Ambiguous is not a licence to guess.
                unmapped.append(f"{name} (ambiguous: {', '.join(h[0] for h in hits)})")
                continue
        if target:
            mapping[v["id"]] = target
            decided.append(f"{name} -> {how}")
        else:
            unmapped.append(name)
    return mapping, decided, unmapped


def venue_map_for(db, orbit, org_id: str, log=print) -> dict[str, str]:
    from app.db.models import Venue

    norm_venues = {
        v.name: v.id
        for v in db.query(Venue).filter(Venue.organization_id == org_id).all()
    }
    mapping, decided, unmapped = resolve_venues(
        orbit.rows("venues", "id,name"), norm_venues
    )
    for line in decided:
        log(f"  venue {line}")
    if unmapped:
        # Not fatal: a row scoped to a venue Norm does not have becomes
        # group-wide, which is visible to everyone rather than lost.
        log(
            f"  ! no Norm venue for: {', '.join(sorted(unmapped))} → those rows land group-wide"
        )
    return mapping


# ---------------------------------------------------------------------------
# The target organization
# ---------------------------------------------------------------------------


class TargetError(Exception):
    pass


def resolve_org(
    db, org_email: str, org_id: str | None = None, expect_org: str | None = None
):
    """(user, organization) the data lands in, refusing anything ambiguous.

    A member with several organizations must name one (``--org-id``): picking
    "the first membership" is how a mirror lands in the wrong tenant.
    """
    from sqlalchemy import func

    from app.db.models import Organization, OrganizationMembership, User

    user = db.query(User).filter(func.lower(User.email) == org_email.lower()).first()
    if not user:
        raise TargetError(f"no user {org_email}")
    memberships = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .all()
    )
    if org_id:
        memberships = [m for m in memberships if m.organization_id == org_id]
    if not memberships:
        raise TargetError(f"{org_email} has no matching organization membership")
    if len(memberships) > 1:
        names = []
        for m in memberships:
            org = db.get(Organization, m.organization_id)
            names.append(f"{org.name if org else '?'} ({m.organization_id})")
        raise TargetError(
            f"{org_email} belongs to {len(memberships)} organizations — pass "
            f"--org-id: {'; '.join(names)}"
        )
    org = db.get(Organization, memberships[0].organization_id)
    if org is None:
        raise TargetError("membership points at a missing organization")
    if expect_org is not None and org.name != expect_org:
        raise TargetError(
            f"target organization is '{org.name}', not the expected '{expect_org}'"
        )
    return user, org


# ---------------------------------------------------------------------------
# The mirror: upsert by orbit_id, change detection, delete pass
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    code: int
    stats: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    org_name: str | None = None


class Mirror:
    """Upserts a script's records by ``orbit_id`` and deletes what Orbit lost.

    ``owned`` is the set of collections the calling script is responsible for:
    its delete pass never reaches outside them (hiring must not delete
    training's rows because it did not read them).
    """

    def __init__(self, db, *, org_id: str, user_id: str, dry_run: bool, owned) -> None:
        from app.db.models import AppRecord

        self.db = db
        self.org_id = org_id
        self.user_id = user_id
        self.dry_run = dry_run
        self.owned = tuple(owned)
        #: (collection, orbit_id) -> the existing AppRecord. Orbit-sourced
        #: rows only: a row without an orbit_id was created in Norm and is
        #: invisible to the mirror — it can be neither updated nor deleted.
        self.rows: dict[tuple[str, str], object] = {}
        #: (collection, orbit_id) -> a Norm id allocated before the row is
        #: written (forward references, and every new row in a dry-run).
        self.allocated: dict[tuple[str, str], str] = {}
        #: collection -> orbit_id -> Norm id, for rows written this run.
        self.ids: dict[str, dict[str, str]] = defaultdict(dict)
        #: collection -> Orbit ids that EXIST in Orbit, written or not. The
        #: delete pass keeps exactly these.
        self.present: dict[str, set] = defaultdict(set)
        self.written: dict[str, set] = defaultdict(set)
        self.source_rows: dict[str, int] = {}
        self.stats: dict[str, Counter] = defaultdict(Counter)
        self.doomed: dict[str, list] = {}
        #: Rows sharing a (collection, orbit_id) with an older one. Never
        #: updated or deleted here — which row other records point at cannot
        #: be known — but reported, so a human can look.
        self.duplicates: Counter = Counter()
        q = (
            self.db.query(AppRecord)
            .filter(
                AppRecord.namespace == NAMESPACE, AppRecord.organization_id == org_id
            )
            .order_by(AppRecord.created_at, AppRecord.id)
        )
        for row in q:
            orbit_id = (row.data or {}).get("orbit_id")
            if not orbit_id:
                continue
            key = (row.collection, str(orbit_id))
            if key in self.rows:
                self.duplicates[row.collection] += 1
                continue
            self.rows[key] = row
        #: Per-collection row counts BEFORE this run — what the mass-delete
        #: guard measures against, identically in a dry-run and a real one.
        self.initial: Counter = Counter(c for (c, _) in self.rows)

    # -- reading what exists --------------------------------------------------

    def existing_ids(self, collection: str) -> dict[str, str]:
        """orbit_id -> Norm id for rows already in Norm (any run)."""
        return {o: r.id for (c, o), r in self.rows.items() if c == collection}

    def source(self, collection: str, orbit_ids, total: int | None = None) -> None:
        """Declare the Orbit ids that exist for ``collection`` — including the
        ones this run skips. A skipped row is kept (and reported stale), never
        deleted: only a row Orbit no longer has may go."""
        ids = {str(i) for i in orbit_ids if i is not None}
        self.present[collection].update(ids)
        self.source_rows[collection] = (
            total if total is not None else len(self.present[collection])
        )

    def id_for(self, collection: str, orbit_id: str) -> str:
        """The Norm id an Orbit row has (or will have once written)."""
        key = (collection, str(orbit_id))
        row = self.rows.get(key)
        if row is not None:
            return row.id
        return self.allocated.setdefault(key, str(uuid.uuid4()))

    # -- writing ------------------------------------------------------------

    def write(
        self, collection: str, orbit_id, data: dict, venue_id: str | None = None
    ) -> str:
        from app.db.models import AppRecord

        orbit_id = str(orbit_id)
        data = {**data, "orbit_id": orbit_id}
        key = (collection, orbit_id)
        self.present[collection].add(orbit_id)
        self.written[collection].add(orbit_id)
        bucket = self.stats[collection]
        row = self.rows.get(key)
        if row is None:
            new_id = self.id_for(collection, orbit_id)
            if not self.dry_run:
                row = AppRecord(
                    id=new_id,
                    namespace=NAMESPACE,
                    organization_id=self.org_id,
                    venue_id=venue_id,
                    collection=collection,
                    data=data,
                    created_by=self.user_id,
                    updated_by=self.user_id,
                )
                self.db.add(row)
                self.rows[key] = row
            bucket["new"] += 1
            self.ids[collection][orbit_id] = new_id
            return new_id
        if (row.data or {}) == data and row.venue_id == venue_id:
            # Untouched: no write, no updated_at bump — this is what makes a
            # second run in a row report nothing.
            bucket["unchanged"] += 1
        else:
            if not self.dry_run:
                row.data = copy.deepcopy(data)
                row.venue_id = venue_id
                row.updated_by = self.user_id
            bucket["updated"] += 1
        self.ids[collection][orbit_id] = row.id
        return row.id

    # -- the delete pass ------------------------------------------------------

    def plan_deletes(self) -> dict[str, list]:
        """Rows in this script's collections whose orbit_id Orbit no longer
        has. Computed once every collection has been read."""
        doomed: dict[str, list] = defaultdict(list)
        for (collection, orbit_id), row in self.rows.items():
            if collection not in self.owned:
                continue
            if orbit_id not in self.present.get(collection, set()):
                doomed[collection].append(row)
        for collection, rows in doomed.items():
            self.stats[collection]["deleted"] = len(rows)
        for collection in self.owned:
            stale = (
                set(self.present.get(collection, set()))
                - self.written.get(collection, set())
            ) & {o for (c, o) in self.rows if c == collection}
            if stale:
                self.stats[collection]["kept_stale"] = len(stale)
        self.doomed = dict(doomed)
        return self.doomed

    def guard(self) -> list[str]:
        """Collections whose delete pass looks like a broken read: Orbit
        returned nothing for a collection Norm has rows for, or the deletes
        exceed both MASS_DELETE_MIN rows and MASS_DELETE_SHARE of it."""
        tripped = []
        for collection, rows in self.doomed.items():
            have = self.initial[collection]
            n = len(rows)
            emptied = not self.present.get(collection)
            if emptied or (n > MASS_DELETE_MIN and n > have * MASS_DELETE_SHARE):
                tripped.append(f"{collection}: {n} of {have} would be deleted")
        return tripped

    def apply_deletes(self) -> int:
        if self.dry_run:
            return 0
        n = 0
        for rows in self.doomed.values():
            for row in rows:
                self.db.delete(row)
                n += 1
        return n

    # -- reporting ------------------------------------------------------------

    def report(self, log=print, notes: dict | None = None) -> None:
        log("")
        log(
            f"  {'collection':34} {'orbit':>6} {'new':>6} {'updated':>8} "
            f"{'same':>6} {'deleted':>8} {'stale':>6}"
        )
        for collection in self.owned:
            s = self.stats.get(collection, Counter())
            src = self.source_rows.get(
                collection, len(self.present.get(collection, ()))
            )
            line = (
                f"  {collection:34} {src:>6} {s['new']:>6} {s['updated']:>8} "
                f"{s['unchanged']:>6} {s['deleted']:>8} {s['kept_stale']:>6}"
            )
            if notes and notes.get(collection):
                line += f"   {notes[collection]}"
            log(line)
        log(
            "  (orbit = rows in Orbit's source; stale = kept, not refreshed — "
            "still in Orbit but filtered out this run)"
        )
        for collection, n in sorted(self.duplicates.items()):
            log(
                f"  ! {collection}: {n} duplicate row(s) share an orbit_id — left alone"
            )

    def plain_stats(self) -> dict[str, dict[str, int]]:
        return {c: dict(v) for c, v in self.stats.items()}


def finish(
    db,
    mirror: Mirror,
    *,
    dry_run: bool,
    allow_mass_delete: bool,
    log=print,
    notes: dict | None = None,
) -> int:
    """Delete pass, guard, report, then commit (or not). Returns the exit code."""
    mirror.plan_deletes()
    mirror.report(log, notes)
    for collection, rows in sorted(mirror.doomed.items()):
        sample = ", ".join(str((r.data or {}).get("orbit_id"))[:8] for r in rows[:5])
        more = f" (+{len(rows) - 5} more)" if len(rows) > 5 else ""
        log(f"  - delete {collection}: {len(rows)} — orbit ids {sample}…{more}")
    tripped = mirror.guard()
    if tripped:
        log("\n  !! delete pass looks like a broken read, not real churn:")
        for line in tripped:
            log(f"     {line}")
        if not allow_mass_delete:
            if dry_run:
                log("     a real run would STOP here; re-run with --allow-mass-delete")
                log("     only once these deletes are confirmed real")
            else:
                # Not committed; the session's owner discards it (main()
                # rolls back on any non-zero code).
                log(
                    "     stopping — nothing committed (--allow-mass-delete to override)"
                )
                return 2
    if dry_run:
        log("\n--dry-run: nothing written")
        return 0
    mirror.apply_deletes()
    db.commit()
    log("\nmirrored")
    return 0


# ---------------------------------------------------------------------------
# Remuneration: Orbit's own "current rate" rule
# ---------------------------------------------------------------------------


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _hourly_from_raw(raw) -> float | None:
    """Orbit ``hourlyFromRaw``: LoadedHub nests the hourly figure in role
    assignments; the highest positive one wins."""
    rates: list = []

    def collect(roles):
        if not isinstance(roles, list):
            return
        for r in roles:
            rate = r.get("hourlyRate") if isinstance(r, dict) else None
            if _is_number(rate) and rate > 0:
                rates.append(rate)

    raw = raw if isinstance(raw, dict) else {}
    collect(raw.get("memberRoles"))
    for va in raw.get("venueAssignments") or []:
        if isinstance(va, dict):
            collect(va.get("memberRoles"))
    return max(rates) if rates else None


def current_rate(history: list[dict]) -> dict | None:
    """One person's current pay, by Orbit's ``currentRateForMember``.

    Rows newest ``valid_from`` first (Postgres DESC: nulls first); the first
    LIVE row wins (not deleted at source, active in LoadedHub, no valid_to),
    else the first not deleted at source, else the newest. Ties on valid_from
    are broken by id — Orbit's own order there is undefined, and a mirror must
    pick the same row every run.
    """
    if not history:
        return None
    rows = sorted(
        history,
        key=lambda r: (r.get("valid_from") is None, r.get("valid_from") or "", r["id"]),
        reverse=True,
    )
    live = [
        r
        for r in rows
        if not r.get("deleted_at_source")
        and r.get("is_active_in_loadedhub")
        and not r.get("valid_to")
    ]
    row = (
        live[0]
        if live
        else next((r for r in rows if not r.get("deleted_at_source")), rows[0])
    )
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    raw_type = raw.get("type")
    if raw_type is None:
        raw_type = raw.get("remunerationType")
    if raw_type is None:
        raw_type = row.get("remuneration_type")
    pay_type = (
        "salary" if str(raw_type or "").lower().startswith("salary") else "hourly"
    )
    if pay_type == "salary":
        salary = row.get("salary_rate")
        if salary is None:
            salary = raw.get("salaryRate")
        rate = salary if _is_number(salary) else None
    else:
        rate = row.get("hourly_rate") if _is_number(row.get("hourly_rate")) else None
        if rate is None:
            rate = _hourly_from_raw(raw)
    out = {"pay_type": pay_type, "rate": rate, "history_id": row["id"]}
    for key in ("synced_at", "valid_from", "loadedhub_staff_id", "payroll_company_id"):
        if row.get(key) is not None:
            out[key] = row[key]
    return out


# ---------------------------------------------------------------------------
# The training mirror
# ---------------------------------------------------------------------------


def run(
    db,
    orbit,
    *,
    org_email: str,
    org_id: str | None = None,
    expect_org: str | None = None,
    dry_run: bool = False,
    allow_mass_delete: bool = False,
    log=print,
) -> RunResult:
    from app.db.models import OrganizationMembership, User

    try:
        user, org = resolve_org(db, org_email, org_id, expect_org)
    except TargetError as exc:
        log(str(exc))
        return RunResult(code=1)
    log(f"target org: {org.name} ({org.id}) — via {user.email}")
    org_id = org.id

    venue_map = venue_map_for(db, orbit, org_id, log)

    def norm_venue(orbit_venue_id):
        return venue_map.get(orbit_venue_id) if orbit_venue_id else None

    mirror = Mirror(
        db, org_id=org_id, user_id=user.id, dry_run=dry_run, owned=TRAINING_COLLECTIONS
    )
    copied = copied_files(db, org_id)
    #: Rows deliberately not carried, and why. A silent skip reads as "there
    #: was nothing there" — the one thing a reconciliation must never let you
    #: believe.
    skipped: list[str] = []
    notes: dict[str, str] = {}

    log("reading Orbit…")

    # ---- people --------------------------------------------------------
    # ~4,100 team_members exist but only ~120 are active. Migrating all of
    # them would bury the ones who matter in four thousand rows of former
    # staff, so this takes the active ones PLUS anyone history hangs off
    # (training, plans, a performance review) PLUS anyone already mirrored —
    # someone who left keeps their record, marked inactive, rather than
    # vanishing while Orbit still has them.
    members = orbit.rows(
        "team_members",
        "id,name,email,role,is_active,loadedhub_id,loadedhub_default_role_name,"
        "manager_id,payroll_company_id",
    )
    member_venues = orbit.rows("team_member_venues", "team_member_id,venue_id")
    assignments = orbit.rows("training_assignments")
    plans = orbit.rows("training_plans")
    reviews = orbit.rows("performance_reviews")

    referenced = {a["team_member_id"] for a in assignments}
    referenced |= {p["team_member_id"] for p in plans}
    referenced |= {r["team_member_id"] for r in reviews if r.get("team_member_id")}
    already = set(mirror.existing_ids("people"))
    venues_by_member: dict[str, list[str]] = {}
    for mv in member_venues:
        mapped = norm_venue(mv.get("venue_id"))
        if mapped and mapped not in venues_by_member.get(mv["team_member_id"], []):
            venues_by_member.setdefault(mv["team_member_id"], []).append(mapped)

    included = [
        m
        for m in members
        if m.get("is_active") or m["id"] in referenced or m["id"] in already
    ]
    mirror.source("people", [m["id"] for m in members], total=len(members))
    # Allocate every person's Norm id up front: a manager can sort after the
    # people who report to them.
    people = {m["id"]: mirror.id_for("people", m["id"]) for m in included}

    # Link to a Norm login only where an email matches a member of THIS
    # organization exactly. Nobody needs a Norm account to be trained; this is
    # a convenience (and the hook for "my team"), not the identity.
    org_users = {
        (u.email or "").lower(): u.id
        for u in db.query(User)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .filter(OrganizationMembership.organization_id == org_id)
        .all()
        if u.email
    }
    linked = 0
    for m in included:
        data = _pick(m, "name", "email", "role", "loadedhub_id", "payroll_company_id")
        data["is_active"] = bool(m.get("is_active"))
        if m.get("loadedhub_default_role_name"):
            data["loadedhub_role"] = m["loadedhub_default_role_name"]
        venues = venues_by_member.get(m["id"])
        if venues:
            data["venues"] = venues
        person_ref(data, "manager_id", m.get("manager_id"), people)
        norm_user = org_users.get(str(m.get("email") or "").lower())
        if norm_user:
            data["norm_user_id"] = norm_user
            linked += 1
        mirror.write("people", m["id"], data)
    notes["people"] = f"{len(included)} carried; {linked} linked to a Norm login"

    # ---- the program tree ---------------------------------------------
    programs = orbit.rows("training_programs")
    mirror.source("programs", [p["id"] for p in programs])
    for p in programs:
        data = _pick(
            p,
            "name",
            "description",
            "icon",
            "default_due_days",
            "sort_index",
            "variants",
        )
        data["is_active"] = bool(p.get("is_active"))
        data["requires_plan"] = bool(p.get("requires_plan"))
        if p.get("auto_enroll_filter"):
            data["auto_enroll_filter"] = p["auto_enroll_filter"]
        mirror.write("programs", p["id"], data, norm_venue(p.get("venue_id")))

    modules = orbit.rows("training_modules")
    mirror.source("modules", [m["id"] for m in modules])
    for m in modules:
        parent = mirror.ids["programs"].get(m["program_id"])
        if not parent:
            skipped.append(f"module {m['id'][:8]}… — program not carried")
            continue
        data = _pick(m, "name", "description", "sort_index")
        data["program_id"] = parent
        data["requires_signoff"] = bool(m.get("requires_signoff"))
        mirror.write("modules", m["id"], data)

    sections = orbit.rows("training_sections")
    mirror.source("sections", [s["id"] for s in sections])
    for s in sections:
        parent = mirror.ids["modules"].get(s["module_id"])
        if not parent:
            skipped.append(f"section {s['id'][:8]}… — module not carried")
            continue
        data = _pick(
            s,
            "name",
            "section_type",
            "introduction",
            "instructions",
            "trainer_time_minutes",
            "trainee_time_minutes",
            "sort_index",
        )
        data["module_id"] = parent
        mirror.write("sections", s["id"], data)

    content_rows = orbit.rows("training_content")
    mirror.source("content", [c["id"] for c in content_rows])
    media_linked = 0
    for c in content_rows:
        section = mirror.ids["sections"].get(c.get("section_id"))
        if not section:
            # Orbit has content rows that predate sections; without a section
            # they are unreachable in the tree and would only inflate progress
            # denominators.
            skipped.append(f"content {c['id'][:8]}… — no section")
            continue
        data = _pick(c, "title", "content_type", "sort_index", "effective_from")
        data["section_id"] = section
        data["module_id"] = mirror.ids["modules"].get(c.get("module_id"))
        if c.get("content") not in (None, {}):
            data["body"] = c["content"]
            files = content_files(c["content"], copied)
            if files:
                data["files"] = files
                media_linked += len(files)
        mirror.write("content", c["id"], data)
    notes["content"] = f"{media_linked} embedded media linked to Norm files"

    # ---- enrolments and progress ---------------------------------------
    mirror.source("assignments", [a["id"] for a in assignments])
    for a in assignments:
        program = mirror.ids["programs"].get(a["program_id"])
        person = people.get(a["team_member_id"])
        if not program or not person:
            skipped.append(f"assignment {a['id'][:8]}… — program or person not carried")
            continue
        data = _pick(
            a,
            "status",
            "due_date",
            "assigned_at",
            "completed_at",
            "variant_id",
            "variant_name",
        )
        data["program_id"] = program
        data["person_id"] = person
        person_ref(data, "assigned_by", a.get("assigned_by"), people)
        data.setdefault("status", "assigned")
        mirror.write("assignments", a["id"], data, norm_venue(a.get("venue_id")))

    completions = orbit.rows("training_content_completions")
    mirror.source("completions", [c["id"] for c in completions])
    evidence_linked = 0
    for c in completions:
        assignment = mirror.ids["assignments"].get(c["assignment_id"])
        content = mirror.ids["content"].get(c["content_id"])
        if not assignment or not content:
            skipped.append(
                f"completion {c['id'][:8]}… — assignment or content not carried"
            )
            continue
        data = {"assignment_id": assignment, "content_id": content}
        if c.get("completed_at"):
            data["completed_at"] = c["completed_at"]
        # Who completed/submitted each item — provenance the tracker's
        # sign-off view and any audit needs.
        person_ref(data, "completed_by", c.get("completed_by"), people)
        # Orbit keeps the sign-off state inside a `result` blob; the app reads
        # flat keys, and this is the rule the whole tracker turns on — flatten
        # it here rather than teach every reader the blob.
        result = c.get("result") or {}
        if isinstance(result, dict):
            for key in (
                "awaiting_signoff",
                "signoff_at",
                "signoff_notes",
                "rejected",
                "rejected_at",
                "rejection_notes",
            ):
                if result.get(key) is not None:
                    data[key] = result[key]
            person_ref(data, "signoff_by", result.get("signoff_by"), people)
            person_ref(data, "rejected_by", result.get("rejected_by"), people)
        if result:
            # Evidence links to Norm copies wherever the bytes were copied —
            # recomputed every run, so a refresh never reverts them to Orbit.
            data["result"] = link_result_files(result, copied)
            evidence_linked += _linked_count(data["result"])
        mirror.write("completions", c["id"], data)
    notes["completions"] = f"{evidence_linked} evidence files linked"

    mirror.source("plans", [p["id"] for p in plans])
    for p in plans:
        program = mirror.ids["programs"].get(p["program_id"])
        person = people.get(p["team_member_id"])
        if not program or not person:
            skipped.append(f"plan {p['id'][:8]}… — program or person not carried")
            continue
        data = _pick(p, "status", "variant_id", "variant_name", "created_at")
        data["program_id"] = program
        data["person_id"] = person
        person_ref(data, "created_by", p.get("created_by"), people)
        data.setdefault("status", "active")
        mirror.write("plans", p["id"], data, norm_venue(p.get("venue_id")))

    plan_sections = orbit.rows("training_plan_sections")
    mirror.source("plan_sections", [ps["id"] for ps in plan_sections])
    for ps in plan_sections:
        plan = mirror.ids["plans"].get(ps["plan_id"])
        section = mirror.ids["sections"].get(ps["section_id"])
        if not plan or not section:
            skipped.append(
                f"plan_section {ps['id'][:8]}… — plan or section not carried"
            )
            continue
        data = _pick(ps, "due_date", "start_time", "status", "completed_at")
        data["plan_id"] = plan
        data["section_id"] = section
        if ps.get("trainer_id"):
            data["trainer_orbit_id"] = ps["trainer_id"]
            if people.get(ps["trainer_id"]):
                data["trainer_person_id"] = people[ps["trainer_id"]]
        mirror.write("plan_sections", ps["id"], data)

    # Module sign-offs: a manager signing a whole module off for one
    # enrolment. Empty in Orbit today, carried so the first one is not lost.
    signoffs = orbit.rows("training_module_signoffs")
    mirror.source("module_signoffs", [s["id"] for s in signoffs])
    for s in signoffs:
        assignment = mirror.ids["assignments"].get(s.get("assignment_id"))
        module = mirror.ids["modules"].get(s.get("module_id"))
        if not assignment or not module:
            skipped.append(
                f"module_signoff {s['id'][:8]}… — assignment or module not carried"
            )
            continue
        data = _pick(s, "signed_off_at", "notes")
        data["assignment_id"] = assignment
        data["module_id"] = module
        person_ref(data, "signed_off_by", s.get("signed_off_by"), people)
        mirror.write("module_signoffs", s["id"], data)

    # ---- capability frameworks -----------------------------------------
    categories = orbit.rows("capability_categories")
    capabilities = orbit.rows("capabilities")
    caps_by_category: dict[str, list[dict]] = {}
    for cap in capabilities:
        # Keep the Orbit capability id: performance-review ratings point at
        # it, and an in-app category editor needs a stable handle.
        caps_by_category.setdefault(cap["category_id"], []).append(
            {
                "id": cap["id"],
                **_pick(
                    cap,
                    "name",
                    "level1_descriptor",
                    "level2_descriptor",
                    "level3_descriptor",
                    "sort_index",
                ),
            }
        )
    cats_by_framework: dict[str, list[dict]] = {}
    for cat in categories:
        cats_by_framework.setdefault(cat["framework_id"], []).append(
            {
                "id": cat["id"],
                **_pick(cat, "name", "sort_index"),
                "capabilities": sorted(
                    caps_by_category.get(cat["id"], []),
                    key=lambda c: (c.get("sort_index") or 0, c["id"]),
                ),
            }
        )
    frameworks = orbit.rows("capability_frameworks")
    mirror.source("capability_frameworks", [f["id"] for f in frameworks])
    for f in frameworks:
        data = _pick(f, "name", "role_label", "baseline_prerequisites")
        data["is_active"] = bool(f.get("is_active"))
        # Nested rather than three collections: a framework is read whole,
        # and its categories and capabilities have no life of their own.
        data["categories"] = sorted(
            cats_by_framework.get(f["id"], []),
            key=lambda c: (c.get("sort_index") or 0, c["id"]),
        )
        mirror.write("capability_frameworks", f["id"], data)

    # ---- review cycles, then performance reviews -----------------------
    # Cycles first, so a review can point at its Norm cycle id.
    assignments_by_cycle: dict[str, list[dict]] = {}
    for ca in orbit.rows("review_cycle_assignments"):
        person = people.get(ca.get("team_member_id"))
        if not person:
            continue
        assignments_by_cycle.setdefault(ca["cycle_id"], []).append(
            {
                "person_id": person,
                "framework_id": mirror.ids["capability_frameworks"].get(
                    ca.get("framework_id")
                ),
            }
        )
    cycles = orbit.rows("review_cycles")
    mirror.source("review_cycles", [rc["id"] for rc in cycles])
    for rc in cycles:
        data = _pick(rc, "name", "cadence", "due_dates", "lead_days")
        data["is_active"] = bool(rc.get("is_active"))
        if rc.get("auto_enroll_filter"):
            data["auto_enroll_filter"] = rc["auto_enroll_filter"]
        data["default_framework_id"] = mirror.ids["capability_frameworks"].get(
            rc.get("default_framework_id")
        )
        data["assignments"] = assignments_by_cycle.get(rc["id"], [])
        mirror.write("review_cycles", rc["id"], data)

    ratings_by_review: dict[str, list[dict]] = {}
    for r in orbit.rows("performance_review_capability_ratings"):
        ratings_by_review.setdefault(r["review_id"], []).append(
            _pick(
                r,
                "capability_id",
                "employee_level",
                "manager_level",
                "employee_comment",
                "manager_comment",
            )
        )
    goals_by_review: dict[str, list[dict]] = {}
    for g in orbit.rows("performance_review_previous_goals"):
        goals_by_review.setdefault(g["review_id"], []).append(
            {
                "id": g["id"],
                **_pick(
                    g,
                    "text",
                    "employee_outcome",
                    "employee_comment",
                    "manager_outcome",
                    "manager_comment",
                    "sort_index",
                ),
            }
        )
    mirror.source("performance_reviews", [r["id"] for r in reviews])
    soft_deleted = 0
    for r in reviews:
        person = people.get(r.get("team_member_id"))
        if not person:
            skipped.append(
                f"performance_review {r['id'][:8]}… — team member not carried"
            )
            continue
        data = _pick(
            r,
            "status",
            "due_date",
            "employee_reflection",
            "manager_feedback",
            "next_goals",
            "summary_email_html",
            "pdf_storage_path",
            "employee_submitted_at",
            "manager_submitted_at",
            "completed_at",
            "goals_email_sent_at",
            "goals_email_to",
            "current_pay_rate",
            "current_pay_type",
            "proposed_pay_rate",
            "pay_notes",
            "pay_proposed_at",
            "no_pay_change",
            # Soft delete: Orbit hides these in its UI, but they are real
            # rows (and restorable there) — they arrive marked, not dropped.
            "deleted_at",
            "created_at",
        )
        # Orbit renamed the meeting state; both mean "ready to meet".
        if data.get("status") == "in_meeting":
            data["status"] = "ready_for_meeting"
        data["person_id"] = person
        person_ref(data, "manager_id", r.get("manager_id"), people)
        person_ref(data, "completed_by", r.get("completed_by"), people)
        person_ref(data, "pay_proposed_by", r.get("pay_proposed_by"), people)
        person_ref(data, "deleted_by", r.get("deleted_by"), people)
        data["cycle_id"] = mirror.ids["review_cycles"].get(r.get("cycle_id"))
        data["framework_id"] = mirror.ids["capability_frameworks"].get(
            r.get("framework_id")
        )
        # Rows arrive ordered by id, so this order is stable run to run.
        data["capability_ratings"] = ratings_by_review.get(r["id"], [])
        data["previous_goals"] = sorted(
            goals_by_review.get(r["id"], []),
            key=lambda x: (x.get("sort_index") or 0, x["id"]),
        )
        if r.get("deleted_at"):
            soft_deleted += 1
        mirror.write("performance_reviews", r["id"], data)
    notes["performance_reviews"] = f"{soft_deleted} soft-deleted (carried, marked)"

    # The review email log and its on/off switch (sending stays off in Norm
    # until cut-over; this is history).
    review_emails = orbit.rows("performance_review_emails")
    mirror.source("performance_review_emails", [e["id"] for e in review_emails])
    for e in review_emails:
        data = _pick(
            e,
            "recipient_role",
            "kind",
            "to_email",
            "subject",
            "body_html",
            "status",
            "error",
            "is_test",
            "sent_at",
        )
        if e.get("review_id"):
            data["review_orbit_id"] = e["review_id"]
            review = mirror.ids["performance_reviews"].get(e["review_id"])
            if review:
                data["review_id"] = review
        raw_ids = [i for i in (e.get("review_ids") or []) if isinstance(i, str)]
        if raw_ids:
            data["review_orbit_ids"] = raw_ids
            data["review_ids"] = [
                mirror.ids["performance_reviews"][i]
                for i in raw_ids
                if i in mirror.ids["performance_reviews"]
            ]
        person_ref(data, "person_id", e.get("team_member_id"), people)
        mirror.write("performance_review_emails", e["id"], data)

    email_settings = orbit.rows("performance_review_email_settings")
    mirror.source(
        "performance_review_email_settings", [str(s["id"]) for s in email_settings]
    )
    for s in email_settings:
        data = _pick(s, "updated_at")
        data["enabled"] = bool(s.get("enabled"))
        # Singleton row; its id is a bool in Orbit — key it as a string.
        mirror.write("performance_review_email_settings", str(s["id"]), data)

    # ---- remuneration: one row per person, the rate Orbit shows as current
    history = orbit.rows(
        "team_member_remuneration_history",
        "id,team_member_id,loadedhub_staff_id,payroll_company_id,remuneration_type,"
        "hourly_rate,salary_rate,valid_from,valid_to,is_active_in_loadedhub,"
        "deleted_at_source,synced_at,raw",
    )
    history_by_member: dict[str, list[dict]] = defaultdict(list)
    for h in history:
        if h.get("team_member_id"):
            history_by_member[h["team_member_id"]].append(h)
    # Keyed by the Orbit TEAM MEMBER id (one row per person) — so a new rate
    # updates the person's row in place rather than replacing it.
    mirror.source("remuneration", list(history_by_member), total=len(history_by_member))
    for member_id, rows in history_by_member.items():
        person = people.get(member_id)
        if not person:
            continue  # a former employee nobody carries; not worth a row each
        rate = current_rate(rows)
        if rate is None:
            continue
        data = {"person_id": person, **rate}
        mirror.write("remuneration", member_id, data)
    notes["remuneration"] = (
        f"{len(history)} history rows; rate kept for carried people only"
    )

    # ---- automation config (enrolment rules, reminder cadence + log) ---
    rules = orbit.rows("training_auto_enroll_rules")
    mirror.source("auto_enroll_rules", [r["id"] for r in rules])
    for r in rules:
        program = mirror.ids["programs"].get(r.get("program_id"))
        if not program:
            skipped.append(f"auto_enroll_rule {r['id'][:8]}… — program not carried")
            continue
        data = _pick(r, "role", "created_at")
        data["program_id"] = program
        mirror.write("auto_enroll_rules", r["id"], data, norm_venue(r.get("venue_id")))

    reminder_settings = orbit.rows("training_reminder_settings")
    mirror.source("reminder_settings", [str(s["id"]) for s in reminder_settings])
    for s in reminder_settings:
        data = _pick(s, "interval_days", "updated_at")
        data["enabled"] = bool(s.get("enabled"))
        # Singleton row; its id is a bool in Orbit — key it as a string.
        mirror.write("reminder_settings", str(s["id"]), data)

    reminder_emails = orbit.rows("training_reminder_emails")
    mirror.source("reminder_emails", [e["id"] for e in reminder_emails])
    for e in reminder_emails:
        data = _pick(
            e,
            "to_email",
            "subject",
            "body_html",
            "status",
            "error",
            "item_count",
            "sent_at",
            "created_at",
        )
        person = people.get(e.get("team_member_id"))
        if person:
            data["person_id"] = person
        mirror.write("reminder_emails", e["id"], data)

    # ---- reconcile, delete pass, commit --------------------------------
    if skipped:
        log(f"\n  {len(skipped)} row(s) not carried:")
        for line in skipped[:40]:
            log(f"    ! {line}")
        if len(skipped) > 40:
            log(f"    … and {len(skipped) - 40} more")
    code = finish(
        db,
        mirror,
        dry_run=dry_run,
        allow_mass_delete=allow_mass_delete,
        log=log,
        notes=notes,
    )
    return RunResult(
        code=code, stats=mirror.plain_stats(), skipped=skipped, org_name=org.name
    )


def build_parser(description: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument(
        "--org-email",
        required=True,
        help="a member of the target organization; its name is printed first",
    )
    ap.add_argument(
        "--org-id", help="which organization, when that member belongs to several"
    )
    ap.add_argument(
        "--expect-org",
        help="refuse to run unless the target organization has exactly this name",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--allow-mass-delete",
        action="store_true",
        help="let a delete pass through that the broken-read guard stopped",
    )
    return ap


def main(argv: list[str] | None = None, *, orbit=None, db=None) -> int:
    """CLI entry. ``orbit``/``db`` are injectable so tests run with no network
    and inside their own transaction."""
    args = build_parser("Mirror Orbit's training data into Norm").parse_args(argv)
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
            allow_mass_delete=args.allow_mass_delete,
        )
        if owns_db and (result.code or args.dry_run):
            db.rollback()
        return result.code
    finally:
        if owns_db:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
