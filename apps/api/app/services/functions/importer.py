"""Orbit → Norm Functions: one organisation's data, wiped and reloaded.

Orbit stays the system of record until cut-over, and Norm runs Functions in
``shadow`` mode beside it. Every run of this importer makes the organisation's
Functions tables match Orbit *now*, in ONE transaction:

    advisory lock (per org) → delete the org's Functions business rows →
    insert everything transformed from Orbit → sanity gate → commit

or roll back, leaving the previous copy untouched. ``dry_run`` does all of it
and rolls back, so its report is exactly what a real run would do.

**Wipe-and-reload, not upsert.** A mirror that diffs and patches needs a
delete pass that can tell "Orbit deleted it" from "the read broke" (the HR
mirrors' delete guard). Reloading inside one transaction gets both for free:
a broken read trips the sanity gate and nothing is committed.

**Deterministic ids.** ``import_id(org, orbit_table, orbit_id)`` is a UUID5
under a fixed namespace, so a row keeps its id across reloads: deep links,
agent references and thread links survive the nightly run. Norm itself only
ever creates uuid4 ids, which is how the importer recognises its own rows.

What is never wiped:

* ``fn_files`` — content-addressed (sha256 per org), only ever added to.
* Mailbox accounts CONNECTED in Norm (``connection_id`` set) and their mirror
  rows (threads, messages, labels, retry, thread folders): their sync owns
  them. Placeholder (unconnected) accounts are reloaded with their rows.
* Form submissions Norm received itself (uuid4 ids): in shadow mode the web
  forms may already post to Norm, and Orbit never sees those.

It refuses to run when the organisation is ``live`` — checked first, and again
under a share lock right before commit. Orbit is only ever read: GET (and HEAD
for counts), and files only from Orbit's own public storage.

**Structured, not copied** (the plan's "Data model"): one minimum-spend model
(proposal lines and event spend options merged), menus linked rather than
duplicated, contact copies resolved to contact rows, area names to area ids,
booking-type junk to the enum, Orbit's instants to venue-local plans, money to
integer cents, Orbit team members to Norm users by email (their name kept as
a label when nobody matches). Everything the transform could not carry is
counted in the report with its reason — a silent drop reads as "there was
nothing there", which a parity check must never let anyone believe.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html as html_lib
import io
import json
import re
import time
import urllib.parse
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from app.db import functions_models as fm
from app.db.models import Base, Organization, OrganizationMembership, User, Venue
from app.services.functions import forms as forms_svc
from app.services.functions import money
from app.services.functions import status as derived
from app.services.functions.errors import Invalid
from app.services.functions.timeutil import validate_timezone

#: uuid5(NAMESPACE_URL, "https://bettercallnorm.com/functions/orbit-import").
#: NEVER change it: every imported row's id is derived from it, and a new
#: namespace would give every record a new id (breaking every link to them).
NAMESPACE = uuid.UUID("38027f9b-6d4e-5498-98be-34d2db8b5488")

#: Where F5 serves an imported image (proposal, brand, area). Proposal HTML and
#: brand fields are rewritten to this path; relative, so it resolves on
#: whichever host serves the page (bettercallnorm.com in production).
PUBLIC_FILE_PATH = "/api/public/files/{file_id}"

#: Who imported facts are attributed to when Orbit recorded no person.
ACTOR = "Orbit"

#: Orbit "confirmed" enquiries with no booking (owner's rule, 12 Oct 2026):
#: recent ones are put in front of staff, historic ones archived.
UNBOOKED_FOLLOW_UP = (
    "Orbit marked this confirmed without a booking — record the customer's "
    "confirmation (creates the event) or close it."
)
UNBOOKED_ARCHIVE_NOTE = (
    "Imported from Orbit, where it was marked confirmed without a booking."
)
_RECENT_PREFERRED = dt.timedelta(days=14)
_RECENT_CREATED = dt.timedelta(days=60)
MAX_FILE_BYTES = 15 * 1024 * 1024
_LOCK_KEY = "norm-functions-import:{org_id}"

_BOOKING_TYPES = frozenset(fm.BOOKING_TYPES)


def import_id(org_id: str, table: str, key: str) -> str:
    """The id an Orbit row gets in Norm — the same on every run."""
    return str(uuid.uuid5(NAMESPACE, f"{org_id}:{table}:{key}"))


def is_import_id(value: str | None) -> bool:
    """Whether an id was minted here (UUID5) rather than by Norm (UUID4)."""
    try:
        return uuid.UUID(str(value)).version == 5
    except (TypeError, ValueError):
        return False


class ImportRefused(Exception):
    """The import cannot run at all: a live organisation, an Orbit venue with
    data and no Norm venue, a venue without a timezone, a concurrent run."""


# ===========================================================================
# Reading Orbit (GET only)
# ===========================================================================


class OrbitSource(Protocol):
    def rows(
        self, table: str, *, select: str = "*", filters=(), key=("id",)
    ) -> list[dict]: ...

    def count(self, table: str, filters=()) -> int: ...

    def fetch_file(self, url: str) -> tuple[bytes, str]: ...


class OrbitReadError(RuntimeError):
    pass


class OrbitPostgrest:
    """Orbit's Supabase, read-only: PostgREST rows and public storage objects.

    Only ever issues GET (and HEAD to count). Redirects are never followed —
    the service key travels on row reads and must not be forwarded anywhere.
    Files come only from the project's own public storage, without the key,
    capped in size: a URL found in row content must not make this fetch
    anything else.
    """

    def __init__(
        self,
        url: str,
        service_key: str,
        *,
        client=None,
        page: int = 1000,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        import httpx

        root = url.rstrip("/")
        parts = urllib.parse.urlsplit(root)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("Orbit URL must be https://<project>.supabase.co")
        self.rest = root + "/rest/v1"
        self.public_prefix = root + "/storage/v1/object/public/"
        self.page = page
        self.max_file_bytes = max_file_bytes
        self._headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
        }
        self._client = client or httpx.Client(
            follow_redirects=False,
            timeout=120,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )

    def _request(self, method: str, url: str, headers: dict | None = None):
        import httpx

        if method not in ("GET", "HEAD"):
            raise ValueError("the Orbit client only ever reads")
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._client.request(method, url, headers=headers or {})
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code < 300:
                    return resp
                if resp.status_code < 500:
                    raise OrbitReadError(
                        f"HTTP {resp.status_code} for {url.split('?', 1)[0]}"
                    )
                last = OrbitReadError(
                    f"HTTP {resp.status_code} for {url.split('?')[0]}"
                )
            time.sleep(1 + attempt)
        raise OrbitReadError(str(last))

    @staticmethod
    def _quote(value) -> str:
        """A value inside a PostgREST logical filter, quoted so commas, dots
        and brackets in it cannot change the expression."""
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def rows(
        self, table: str, *, select: str = "*", filters=(), key=("id",)
    ) -> list[dict]:
        """Every row matching ``filters``, keyset-paged on ``key`` (unique
        within the filter). Keyset, not offset: on a live table an insert
        mid-read shifts every later offset page, and a skipped row would read
        as a deleted one."""
        key = (key,) if isinstance(key, str) else tuple(key)
        out: list[dict] = []
        last: dict | None = None
        while True:
            params = [("select", select)] + [tuple(f.split("=", 1)) for f in filters]
            params += [
                ("order", ",".join(f"{k}.asc" for k in key)),
                ("limit", str(self.page)),
            ]
            if last is not None:
                if len(key) == 1:
                    params.append((key[0], f"gt.{last[key[0]]}"))
                else:
                    a, b = key
                    params.append(
                        (
                            "or",
                            f"({a}.gt.{self._quote(last[a])},"
                            f"and({a}.eq.{self._quote(last[a])},{b}.gt.{self._quote(last[b])}))",
                        )
                    )
            query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
            batch = self._request(
                "GET", f"{self.rest}/{table}?{query}", self._headers
            ).json()
            out.extend(batch)
            if len(batch) < self.page:
                return out
            nxt = {k: batch[-1][k] for k in key}
            if nxt == last:
                raise OrbitReadError(f"{table}: keyset paging made no progress")
            last = nxt

    def count(self, table: str, filters=()) -> int:
        """Orbit's own row count (HEAD + count=exact) — the read check."""
        params = [("select", "*")] + [tuple(f.split("=", 1)) for f in filters]
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        resp = self._request(
            "HEAD",
            f"{self.rest}/{table}?{query}",
            {**self._headers, "Prefer": "count=exact", "Range": "0-0"},
        )
        total = (resp.headers.get("content-range") or "*/0").rsplit("/", 1)[-1]
        return int(total) if total.isdigit() else 0

    def fetch_file(self, url: str) -> tuple[bytes, str]:
        canonical = str(url or "").split("?", 1)[0]
        path = canonical[len(self.public_prefix) :]
        if (
            not canonical.startswith(self.public_prefix)
            or not path
            or any(p in ("", ".", "..") for p in urllib.parse.unquote(path).split("/"))
        ):
            raise OrbitReadError(
                "refused (not Orbit public storage): " + canonical[:120]
            )
        import httpx

        too_big = OrbitReadError(
            f"refused (over {self.max_file_bytes} bytes): {canonical[:120]}"
        )
        try:
            # Streamed and counted: a response is abandoned the moment it
            # passes the cap, whatever its Content-Length claims.
            with self._client.stream("GET", canonical) as resp:  # no key: public
                if resp.status_code >= 300:
                    raise OrbitReadError(
                        f"HTTP {resp.status_code} for {canonical[:120]}"
                    )
                declared = resp.headers.get("content-length")
                if (
                    declared
                    and declared.isdigit()
                    and int(declared) > self.max_file_bytes
                ):
                    raise too_big
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > self.max_file_bytes:
                        raise too_big
                return bytes(body), resp.headers.get("content-type") or ""
        except httpx.HTTPError as exc:
            raise OrbitReadError(f"{type(exc).__name__} for {canonical[:120]}") from exc


# Columns read from each Orbit table. Never a token column (the Gmail
# accounts carry OAuth tokens nothing here needs), never a message body.
_FULL_READS: dict[str, str] = {
    "venues": "id,name,timezone,minimum_spend_approver_id,minimum_spend_override_approver_id",
    "team_members": "id,name,email,google_email",
    "contacts": "*",
    "contact_emails": "*",
    "enquiries": "*",
    "enquiry_status_events": "*",
    "lead_attributions": "*",
    "venue_functions": "*",
    "venue_function_spend_options": "*",
    "contact_proposals": "*",
    "contact_proposal_spend_lines": "*",
    "min_spend_approval_events": "*",
    "enquiry_preorder_menus": "*",
    "venue_function_preorder_menus": "*",
    "preorder_templates": "*",
    "preorder_template_items": "*",
    "venue_function_post_confirm_edits": "*",
    "venue_function_status_history": "*",
    "venue_function_timings": "*",
    "enquiry_quote_documents": "*",
    "enquiry_quote_versions": "*",
    "enquiry_quote_events": "*",
    "venue_event_areas": "*",
    "venue_function_option_visibility": "*",
    "venue_ai_context": "*",
    "venue_menu_links": "*",
    "venue_brand_styles": "*",
    "venue_quote_templates": "id,venue_id,template_html,template_css,created_at,updated_at",
    "quote_images": "*",
    "function_reply_templates": "*",
    "web_forms": "*",
    "web_form_fields": "*",
    "web_form_submissions": "*",
    "venue_gmail_accounts": "id,venue_id,google_email,display_name,signature_html,created_at,updated_at",
    "enquiry_gmail_threads": "*",
}
_VENUE_MAIL = "account_source=eq.venue_gmail_accounts"
_THREAD_COLS = (
    "account_id,thread_id,subject,snippet,last_message_at,last_from_email,"
    "last_from_name,message_count,unread_count,label_ids,created_at,updated_at"
)
_MESSAGE_COLS = (
    "message_id,thread_id,account_id,from_name,from_email,to_emails,cc_emails,"
    "reply_to_email,subject,snippet,internal_date,label_ids,unread,is_inbound,"
    "is_internal,is_auto_reply,created_at,updated_at"
)
_LABEL_COLS = "account_id,label_id,label_name,actioned_kind,updated_at"


def read_orbit(source: OrbitSource, report: ImportReport, log=print) -> dict[str, list]:
    """Everything the transform needs, read before any database write."""
    data: dict[str, list] = {}
    for table, cols in _FULL_READS.items():
        rows = source.rows(table, select=cols)
        data[table] = rows
        report.read(table, len(rows), source.count(table))
    log(
        f"  read {sum(len(v) for v in data.values())} rows from {len(_FULL_READS)} tables"
    )

    accounts = sorted(a["id"] for a in data["venue_gmail_accounts"])
    data["gmail_mailbox_labels"] = [
        r
        for a in accounts
        for r in source.rows(
            "gmail_mailbox_labels",
            select=_LABEL_COLS,
            filters=(f"account_id=eq.{a}", _VENUE_MAIL),
            key=("label_id",),
        )
    ]
    linked = sorted({t["thread_id"] for t in data["enquiry_gmail_threads"]})
    data["gmail_threads"] = _rows_in(
        source,
        "gmail_threads",
        "thread_id",
        linked,
        _THREAD_COLS,
        ("thread_id", "account_id"),
    )
    data["gmail_messages"] = _rows_in(
        source, "gmail_messages", "thread_id", linked, _MESSAGE_COLS, ("message_id",)
    )
    # Threads carrying an "Actioned - …" label: their folder.
    actioned: dict[str, list[str]] = defaultdict(list)
    for r in data["gmail_mailbox_labels"]:
        if r.get("actioned_kind"):
            actioned[r["account_id"]].append(r["label_id"])
    data["gmail_folder_threads"] = [
        r
        for a, labels in sorted(actioned.items())
        for r in source.rows(
            "gmail_threads",
            select="account_id,thread_id,label_ids,updated_at",
            filters=(
                f"account_id=eq.{a}",
                _VENUE_MAIL,
                "label_ids=ov.{" + ",".join(sorted(labels)) + "}",
            ),
            key=("thread_id",),
        )
    ]
    for table in (
        "gmail_mailbox_labels",
        "gmail_threads",
        "gmail_messages",
        "gmail_folder_threads",
    ):
        report.read(table, len(data[table]))
    # When each event's client link was created — evidence only (the token
    # itself is never read: old customer links are not migrated).
    data["venue_function_tokens"] = source.rows(
        "venue_function_tokens", select="id,function_id,created_at"
    )
    log(
        f"  mail: {len(data['gmail_threads'])} linked threads, "
        f"{len(data['gmail_messages'])} messages, "
        f"{len(data['gmail_folder_threads'])} filed threads"
    )
    return data


def _rows_in(source, table, column, values, select, key, chunk: int = 80) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(values), chunk):
        part = ",".join(OrbitPostgrest._quote(v) for v in values[i : i + chunk])
        out.extend(
            source.rows(
                table,
                select=select,
                filters=(f"{column}=in.({part})", _VENUE_MAIL),
                key=key,
            )
        )
    return out


# ===========================================================================
# The transform report
# ===========================================================================


@dataclass
class Thresholds:
    """What the sanity gate tolerates. Defaults are conservative: a run that
    trips one changes nothing, and the report says which."""

    #: |rows read − Orbit's count| per table, beyond churn during the read.
    read_tolerance_rows: int = 2
    read_tolerance_share: float = 0.005
    #: Rows of a core table dropped for a data reason (not --skip-venue).
    max_dropped_share: float = 0.02
    max_dropped_rows: int = 10
    #: Planned time fields (events, options) that could not be read.
    max_unparseable_time_share: float = 0.02
    #: Events whose area text matched no area of their venue.
    max_unknown_area_share: float = 0.10
    #: Booking spend options that matched none of their proposal's lines.
    max_unmatched_option_share: float = 0.10
    #: Below this many, a share threshold is not applied (2 of 3 is not a trend).
    small_count: int = 5

    @classmethod
    def parse(cls, pairs) -> Thresholds:
        out = cls()
        for pair in pairs or ():
            name, _, value = str(pair).partition("=")
            if not hasattr(out, name.strip()):
                raise ValueError(f"unknown threshold {name!r}")
            kind = type(getattr(out, name.strip()))
            setattr(out, name.strip(), kind(value))
        return out


#: The Orbit tables whose losses fail the gate (everything else is reported).
_CORE = (
    "contacts",
    "enquiries",
    "venue_functions",
    "contact_proposal_spend_lines",
    "venue_function_preorder_menus",
    "enquiry_gmail_threads",
    "web_form_submissions",
)
_SKIPPED_VENUE = "venue skipped (--skip-venue)"
#: Report keys the sanity gate reads (one spelling, used at both ends).
_UNMATCHED_OPTION = "booking converted from a proposal: option matched none of its lines (kept on the event)"
_DIRECT_OPTION = (
    "booking made directly in Orbit: its confirmed option is the event's minimum spend"
)
_AREA_MATCHED = "event area matched by name"
_AREA_UNKNOWN = "event area text matched no area of its venue (kept in notes)"


@dataclass
class ImportReport:
    """What a run read, wrote, merged and dropped — printed every run and
    returned as a dict (``as_dict``) for ``--report-json``."""

    org: dict = field(default_factory=dict)
    dry_run: bool = False
    sources: dict = field(default_factory=dict)
    written: dict = field(default_factory=dict)
    deleted: dict = field(default_factory=dict)
    kept: dict = field(default_factory=dict)
    venues: dict = field(default_factory=dict)
    people: dict = field(default_factory=dict)
    tallies: dict = field(default_factory=lambda: defaultdict(Counter))
    examples: dict = field(default_factory=lambda: defaultdict(list))
    gate: dict = field(default_factory=dict)
    parity: dict = field(default_factory=dict)
    #: Orbit "confirmed" enquiries with no booking: group -> entries.
    reviewed: dict = field(default_factory=lambda: {"follow_up": [], "archived": []})
    outcome: str = ""

    def _src(self, table: str) -> dict:
        return self.sources.setdefault(
            table,
            {
                "read": 0,
                "orbit_count": None,
                "imported": 0,
                "merged": 0,
                "dropped": Counter(),
            },
        )

    def read(self, table: str, n: int, orbit_count: int | None = None) -> None:
        src = self._src(table)
        src["read"] = n
        src["orbit_count"] = orbit_count

    def imported(self, table: str, n: int = 1) -> None:
        self._src(table)["imported"] += n

    def merged(self, table: str, reason: str, n: int = 1) -> None:
        self._src(table)["merged"] += n
        self.tally("merged", f"{table}: {reason}", n)

    def dropped(
        self, table: str, reason: str, n: int = 1, example: str | None = None
    ) -> None:
        self._src(table)["dropped"][reason] += n
        if example:
            self.example(f"{table}: {reason}", example)

    def tally(self, section: str, key: str, n: int = 1) -> None:
        self.tallies[section][key] += n

    def example(self, key: str, value: str, cap: int = 5) -> None:
        if len(self.examples[key]) < cap and value not in self.examples[key]:
            self.examples[key].append(value)

    def as_dict(self) -> dict:
        return json.loads(
            json.dumps(
                {
                    "org": self.org,
                    "dry_run": self.dry_run,
                    "outcome": self.outcome,
                    "venues": self.venues,
                    "people": self.people,
                    "sources": self.sources,
                    "written": self.written,
                    "deleted": self.deleted,
                    "kept": self.kept,
                    "tallies": {k: dict(v) for k, v in self.tallies.items()},
                    "examples": dict(self.examples),
                    "gate": self.gate,
                    "parity": self.parity,
                    "reviewed_by_rule": self.reviewed,
                },
                default=str,
            )
        )

    def lines(self) -> list[str]:
        out = [
            f"Orbit → Norm Functions — {self.org.get('name')} ({self.org.get('id')})"
            f"{' — DRY RUN' if self.dry_run else ''}",
            f"mode: {self.org.get('mode')}",
            "",
            "venues:",
        ]
        out += [f"  {line}" for line in self.venues.get("mapped", [])]
        out += [f"  skipped: {line}" for line in self.venues.get("skipped", [])]
        out += [
            f"  no data, not mapped: {line}" for line in self.venues.get("ignored", [])
        ]
        p = self.people
        if p:
            out += [
                "",
                f"people: {p.get('referenced', 0)} Orbit team members referenced, "
                f"{p.get('matched', 0)} matched to Norm users by email, "
                f"{len(p.get('unmatched', {}))} kept as name labels",
            ]
            top = sorted(p.get("unmatched", {}).items(), key=lambda kv: -kv[1])[:12]
            if top:
                out.append("  unmatched: " + ", ".join(f"{n} ×{c}" for n, c in top))
        out += [
            "",
            f"{'Orbit table':<36}{'read':>7}{'count':>8}{'imported':>10}{'merged':>8}{'dropped':>9}",
        ]
        for table, s in self.sources.items():
            dropped = sum(s["dropped"].values())
            count = "" if s["orbit_count"] is None else s["orbit_count"]
            out.append(
                f"{table:<36}{s['read']:>7}{count!s:>8}{s['imported']:>10}{s['merged']:>8}{dropped:>9}"
            )
            for reason, n in sorted(s["dropped"].items(), key=lambda kv: -kv[1]):
                out.append(f"{'':<6}- {n} {reason}")
        out += ["", "Norm rows written:"]
        out += [f"  {t:<34}{n:>7}" for t, n in self.written.items()]
        if self.kept:
            out += ["kept (not wiped):"] + [
                f"  {t:<34}{n:>7}" for t, n in self.kept.items()
            ]
        for section, counts in self.tallies.items():
            out += ["", f"{section}:"]
            for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                out.append(f"  {n:>6}  {key}")
        if any(self.reviewed.values()):
            out += [
                "",
                'Orbit "confirmed" without a booking — reviewed by rule (left out of parity):',
            ]
            for group, title in (
                (
                    "follow_up",
                    "follow-up set to today (preferred date ≥ today − 14 days, or created ≤ 60 days ago)",
                ),
                ("archived", "archived (historic), with a note"),
            ):
                entries = self.reviewed[group]
                why = Counter(x["why"] for x in entries)
                out.append(
                    f"  {title}: {len(entries)} ({', '.join(f'{n} {k}' for k, n in why.most_common())})"
                )
                for x in entries:
                    out.append(
                        f"    {x['orbit_id'][:8]}  {x['venue']:<20} preferred {x['preferred_date'] or '—':<10} "
                        f"created {x['created']}  {(x['subject'] or '(no subject)')[:60]}"
                    )
        if self.examples:
            out += ["", "examples:"]
            for key, values in self.examples.items():
                out.append(f"  {key}: " + " | ".join(values))
        if self.gate:
            out += [
                "",
                f"sanity gate: {'PASSED' if self.gate.get('passed') else 'FAILED'}",
            ]
            out += [
                f"  {'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}"
                for c in self.gate["checks"]
            ]
        if self.parity:
            out += ["", "parity (Norm derived vs Orbit stored):"] + parity_lines(
                self.parity
            )
        if self.outcome:
            out += ["", self.outcome]
        return out


# ===========================================================================
# Small conversions
# ===========================================================================


def _ts(value) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _date(value, tz: ZoneInfo) -> dt.date | None:
    """A date column, or the venue-local date of a timestamp column."""
    if not value:
        return None
    text_value = str(value)
    if len(text_value) == 10:
        try:
            return dt.date.fromisoformat(text_value)
        except ValueError:
            return None
    ts = _ts(text_value)
    return ts.astimezone(tz).date() if ts else None


_CLOCK = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?(?::(\d{2}))?\s*([ap]\.?m\.?)?$", re.I)


def parse_clock(value) -> dt.time | None | bool:
    """A local clock time written by a person: ``18:30``, ``6:30pm``,
    ``6pm``, ``18.30``. None for blank; False when it cannot be read."""
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in ("noon", "midday"):
        return dt.time(12, 0)
    if raw == "midnight":
        return dt.time(0, 0)
    m = _CLOCK.match(raw)
    if not m:
        return False
    hour, minute, second = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
    meridiem = (m.group(4) or "").replace(".", "")
    if meridiem:
        if not 1 <= hour <= 12:
            return False
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif m.group(2) is None:
        return False  # a bare number ("7") is not a time
    if hour > 23 or minute > 59 or second > 59:
        return False
    return dt.time(hour, minute, second)


def to_cents(value) -> int | None:
    """Money as integer cents, rounding half up (Orbit stored numeric)."""
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _venue_key(name: str | None) -> str:
    text_value = str(name or "").strip().lower()
    if text_value.startswith("the "):
        text_value = text_value[4:]
    return "".join(ch for ch in text_value if ch.isalnum())


def _clean(value) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _email(value) -> str | None:
    candidate = str(value or "").strip().lower()
    # Capped, then a linear pattern (the old one backtracked quadratically).
    ok = len(candidate) <= 254 and re.fullmatch(
        r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+", candidate
    )
    return candidate if ok else None


def _text_to_html(body: str | None) -> str:
    """A plain-text reply template as simple HTML paragraphs."""
    paragraphs = re.split(r"\n\s*\n", str(body or "").strip())
    return "".join(
        "<p>" + html_lib.escape(p).replace("\n", "<br>") + "</p>"
        for p in paragraphs
        if p.strip()
    )


_STORAGE_URL = re.compile(
    r"https://[a-z0-9-]+\.supabase\.co/storage/v1/object/public/[^\s\"'<>)\\]+"
)


def _storage_urls(*texts) -> list[str]:
    """Orbit public-storage URLs inside some HTML/CSS/strings, canonical
    (entity-decoded, no query string), in order, de-duplicated."""
    seen: list[str] = []
    for value in texts:
        for raw in _STORAGE_URL.findall(value or ""):
            url = html_lib.unescape(raw).split("?", 1)[0]
            if url not in seen:
                seen.append(url)
    return seen


def rewrite_storage_urls(value: str | None, files: dict[str, str]) -> str | None:
    """Every Orbit storage URL that was copied, pointed at Norm's public file
    path (query strings and ``&amp;`` forms included). URLs not copied stay —
    the report counts them."""
    if not value:
        return value

    def swap(m: re.Match) -> str:
        file_id = files.get(html_lib.unescape(m.group(0)).split("?", 1)[0])
        return PUBLIC_FILE_PATH.format(file_id=file_id) if file_id else m.group(0)

    return _STORAGE_URL.sub(swap, value)


# ===========================================================================
# Web forms: Orbit's field set and style profile as F5 accepts them
# ===========================================================================

#: Every field type Orbit's builder offers (orbit src/services/webForms.ts),
#: as Norm's FORM_FIELD_TYPES. Orbit's checkboxes are a multi-select when they
#: carry options, exactly like Norm's.
ORBIT_FIELD_TYPES = {
    "text": "text",
    "email": "email",
    "phone": "phone",
    "textarea": "long_text",
    "number": "number",
    "date": "date",
    "select": "choice",
    "radio": "choice",
    "checkbox": "checkbox",
    "date_list": "dates",
    "time_range": "time_range",
    "name": "name",
    "section": "section",
}
FORM_MAX_OPTIONS = forms_svc.MAX_OPTIONS
#: The theme settings forms.js applies — F5 keeps the list (``forms.THEME_KEYS``,
#: in step with forms.js) and accepts any number of them.
FORM_THEME_KEYS = forms_svc.THEME_KEYS
#: maps_to a field gets from its type alone; the rest come from its key.
_MAPS_BY_TYPE = {
    "name": "contact.name",
    "email": "contact.email",
    "phone": "contact.phone",
    "date": "preferred_date",
    "dates": "preferred_dates",
    "time_range": "preferred_time",
}
_MAPS_BY_KEY = {
    "guests": ("number", "text"),
    "contact.company": ("text",),
    "booking_type": ("choice",),
    "subject": ("text",),
    "client_note": ("long_text",),
}
_KEY_WORDS = {
    "guests": ("guest", "group_size", "party_size", "headcount", "pax", "attendees"),
    "contact.company": ("company", "organisation", "organization", "business"),
    "booking_type": ("type_of_event", "event_type", "booking_type", "function_type"),
    "subject": ("occasion", "subject", "event_name"),
    "client_note": (
        "message",
        "notes",
        "details",
        "comments",
        "anything_else",
        "tell_us",
    ),
}


def _snake(key) -> str:
    """forms.js's rule: ``inputRadius`` → ``input_radius``."""
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).lower()


def _field_key(raw) -> str:
    """A field key F5 accepts (``^[a-z][a-z0-9_]{0,39}$``)."""
    key = re.sub(r"[^a-z0-9_]+", "_", _snake(raw or "").strip().lower()).strip("_")
    if key and not key[0].isalpha():
        key = f"f_{key}"
    return key[:40]


def _field_maps_to(kind: str, key: str) -> str | None:
    if kind in _MAPS_BY_TYPE:
        return _MAPS_BY_TYPE[kind]
    for target, kinds in _MAPS_BY_KEY.items():
        if kind in kinds and any(word in key for word in _KEY_WORDS[target]):
            return target
    return None


def _default_label(kind: str, key: str) -> str:
    """A label for a field Orbit left blank. A blank section was a plain
    divider in Orbit; F5 requires a label, so it gets a short heading."""
    words = key.removesuffix("_section").replace("_", " ").strip()
    if kind == "section":
        if "contact" in words or "detail" in words or "about_you" in key:
            return "Your details"
        if "event" in words or "function" in words or "booking" in words:
            return "Your event"
    return (words or kind).capitalize()


# ===========================================================================
# The plan: every row to insert, keyed by table and id
# ===========================================================================

_MODELS = {m.__tablename__: m for m in fm.FUNCTIONS_MODELS}
#: Insert order (parents first). Deletes run in reverse.
_ORDER = [t.name for t in Base.metadata.sorted_tables if t.name in fm.FUNCTIONS_TABLES]


class _Plan:
    def __init__(self, org_id: str):
        self.org_id = org_id
        self.rows: dict[str, dict[str, dict]] = defaultdict(dict)

    def add(self, model, row: dict) -> dict:
        table = model.__tablename__
        if row["id"] in self.rows[table]:
            raise RuntimeError(f"importer bug: {table} id {row['id']} planned twice")
        row["organization_id"] = self.org_id
        if "version" in model.__table__.c and "version" not in row:
            row["version"] = 1
        self.rows[table][row["id"]] = row
        return row

    def complete(self) -> None:
        """Give every row every column — executemany needs one shape per
        table — from the column's Python default, and refuse a NOT NULL hole
        here, by name, rather than as a constraint error mid-insert."""
        for table, rows in self.rows.items():
            columns = _MODELS[table].__table__.c
            for row in rows.values():
                unknown = set(row) - set(columns.keys())
                if unknown:
                    raise RuntimeError(
                        f"importer bug: {table} has no {sorted(unknown)}"
                    )
                for col in columns:
                    if col.name in row:
                        continue
                    default = col.default
                    if default is not None and default.is_scalar:
                        row[col.name] = default.arg
                    elif default is not None and default.is_callable:
                        row[col.name] = default.arg(None)
                    else:
                        row[col.name] = None
                for col in columns:
                    if row[col.name] is None and not col.nullable:
                        raise RuntimeError(f"importer bug: {table}.{col.name} is NULL")

    def count(self, table: str) -> int:
        return len(self.rows.get(table, {}))


# ===========================================================================
# The transform
# ===========================================================================

_SKIP = object()  # a row whose venue was skipped or not mapped
#: Orbit's inbox reads a thread with several "Actioned - …" labels in this
#: order (and flags it for review); the import keeps the same answer.
_FOLDER_PRECEDENCE = ("reservation", "enquiry", "other")


@dataclass
class _Keep:
    """Rows of this organisation the wipe must leave alone."""

    connected_accounts: dict = field(default_factory=dict)  # lower(email) -> row
    native_submissions: list = field(default_factory=list)  # (id, form_id, enquiry_id)
    files_by_sha: dict = field(default_factory=dict)
    files_by_ref: dict = field(default_factory=dict)
    foreign_slugs: set = field(default_factory=set)  # web-form slugs other orgs hold


class _Transform:
    """Orbit rows in, planned Norm rows out. Pure apart from the read of the
    organisation's venues and members done by the caller."""

    def __init__(
        self,
        *,
        org,
        venues: list,
        members: dict,
        data: dict,
        keep: _Keep,
        report: ImportReport,
        skip_venues=(),
        now: dt.datetime,
    ):
        self.org = org
        self.org_id = org.id
        #: The run's one clock: every date the transform derives (rather than
        #: reads from Orbit) comes from it, so a run is reproducible.
        self.now = now
        self.data = data
        self.keep = keep
        self.report = report
        self.plan = _Plan(org.id)
        self.org_tz = ZoneInfo(org.default_timezone or "Pacific/Auckland")
        self.norm_venues = {v.id: v for v in venues}
        self.members = members  # lower(email) -> user id
        self.team = {t["id"]: t for t in data["team_members"]}
        self._people: dict[str, dict] = {}
        self.venue_map: dict[str, str] = {}
        self.skipped_venues: set[str] = set()
        self._map_venues(venues, skip_venues)
        # id maps: Orbit id -> Norm id (only for rows that were imported)
        self.contacts: dict[str, str] = {}
        self.email_owner: dict[str, str] = {}  # email -> Norm contact id
        self.enquiries: dict[str, dict] = {}  # orbit id -> planned row
        self.events: dict[str, dict] = {}
        self.areas: dict[
            str, tuple[str, str]
        ] = {}  # orbit area -> (norm area, norm venue)
        self.area_names: dict[str, dict[str, str]] = defaultdict(
            dict
        )  # venue -> key -> id
        self.templates: dict[str, dict] = {}
        self.form_ids: dict[str, str] = {}
        self.accounts: dict[str, dict] = {}  # orbit account -> {id, placeholder, venue}
        self.thread_account: dict[str, str] = {}  # orbit thread id -> orbit account id
        self._link_times: dict[str, list] = defaultdict(list)
        for t in data.get("venue_function_tokens", []):
            if _ts(t.get("created_at")):
                self._link_times[t["function_id"]].append(_ts(t["created_at"]))
        self.wanted_files: dict[str, set] = defaultdict(set)  # url -> purposes
        self.file_ids: dict[str, str] = {}  # url -> fn_files id
        self._brand_files: list = []
        self._quote_images: list = []
        self._table_plans: list = []
        #: Orbit enquiry id -> "follow_up" | "archived" (review_unbooked_confirmed).
        self.reviewed: dict[str, str] = {}

    # ---- shared helpers -----------------------------------------------------

    def id(self, table: str, key) -> str:
        return import_id(self.org_id, table, str(key))

    def stamp(self, row: dict, created, updated=None) -> dict:
        """Orbit's own timestamps — never now(), so a reload is identical."""
        created_at = (
            _ts(created)
            or _ts(updated)
            or dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
        )
        row["created_at"] = created_at
        row["updated_at"] = _ts(updated) or created_at
        return row

    def person(self, member_id, fallback_name=None) -> tuple[str | None, str | None]:
        """``(norm_user_id, label)`` for an Orbit team member: the Norm user
        who is a member of this org with the same email, else the member's
        name as a label (``fallback_name`` when Orbit recorded only that)."""
        if not member_id and not fallback_name:
            return None, None
        tm = self.team.get(member_id) if member_id else None
        name = (
            _clean((tm or {}).get("name")) or _clean(fallback_name) or "Unknown (Orbit)"
        )
        user_id = None
        for candidate in ((tm or {}).get("email"), (tm or {}).get("google_email")):
            email = _email(candidate)
            if email and email in self.members:
                user_id = self.members[email]
                break
        entry = self._people.setdefault(
            member_id or f"name:{name}", {"name": name, "user": user_id, "refs": 0}
        )
        entry["refs"] += 1
        return (user_id, None) if user_id else (None, name)

    def people_summary(self) -> dict:
        unmatched: Counter = Counter()
        for entry in self._people.values():
            if not entry["user"]:
                unmatched[entry["name"]] += entry["refs"]
        return {
            "referenced": len(self._people),
            "matched": sum(1 for e in self._people.values() if e["user"]),
            "unmatched": dict(unmatched),
        }

    def venue(self, orbit_venue_id, table: str):
        """The Norm venue for an Orbit venue id; None for a group-wide row;
        ``_SKIP`` (with the drop recorded) when that venue is not imported."""
        if not orbit_venue_id:
            return None
        norm = self.venue_map.get(orbit_venue_id)
        if norm:
            return norm
        self._drop_for_venue(table, orbit_venue_id, 1)
        return _SKIP

    def _drop_for_venue(self, table: str, orbit_venue_id: str, n: int) -> None:
        if n:
            skipped = orbit_venue_id in self.skipped_venues
            self.report.dropped(
                table, _SKIPPED_VENUE if skipped else "venue not in Norm", n
            )

    def tz(self, norm_venue_id: str | None) -> ZoneInfo:
        if norm_venue_id is None:
            return self.org_tz
        return ZoneInfo(self.norm_venues[norm_venue_id].timezone)

    def clock(self, value, what: str) -> dt.time | None:
        parsed = parse_clock(value)
        if parsed is False:
            self.report.tally("times", f"unparseable {what} (left empty)")
            self.report.example(f"unparseable {what}", str(value)[:40])
            return None
        if parsed is not None:
            self.report.tally("time fields", what)
        return parsed

    def window(self, start, end, tz: ZoneInfo, what: str):
        """An Orbit instant pair as a venue-local plan: ``(date, start, end,
        ends_next_day)``. Orbit stored plans as instants, so the Norm venue's
        zone turns them back into what the venue meant; the oddities are
        counted rather than guessed at silently."""
        s, e = _ts(start), _ts(end)
        if s is None:
            if start:
                self.report.tally("times", f"unparseable {what} start")
            return None, None, None, False
        self.report.tally("time fields", f"{what} window")
        ls = s.astimezone(tz)
        start_time = ls.time().replace(microsecond=0)
        if start_time == dt.time(0, 0):
            # Orbit's date-only bookings sit at local midnight.
            start_time = None
            self.report.tally("times", f"{what} starting at local midnight → date only")
        if e is None or e == s:
            if e is not None:
                self.report.tally(
                    "times", f"{what} ending when it starts → no end time"
                )
            return ls.date(), start_time, None, False
        le = e.astimezone(tz)
        end_time = le.time().replace(microsecond=0)
        days = (le.date() - ls.date()).days
        later = start_time is None or end_time > start_time
        if days == 0 and later:
            return ls.date(), start_time, end_time, False
        if days == 1 and not later:
            return ls.date(), start_time, end_time, True
        if days == 0:
            # The usual cause: a finish after midnight saved on the start's
            # date (19:00–00:30). Read as next day; flag the implausible.
            span = dt.datetime.combine(
                dt.date(2000, 1, 2), end_time
            ) - dt.datetime.combine(dt.date(2000, 1, 1), start_time)
            self.report.tally(
                "times",
                f"{what} ending before it starts → read as next day"
                + (" (over 12 hours — check)" if span > dt.timedelta(hours=12) else ""),
            )
            return ls.date(), start_time, end_time, True
        if days == 1:
            self.report.tally(
                "times", f"{what} longer than 24 hours (end kept, next day)"
            )
            return ls.date(), start_time, end_time, True
        self.report.tally(
            "times", f"{what} end {days:+d} days from its start → end dropped"
        )
        return ls.date(), start_time, None, False

    def cents(self, value, what: str) -> int | None:
        cents = to_cents(value)
        if cents is None and value not in (None, ""):
            self.report.tally("money", f"unreadable {what} (left empty)")
        elif cents is not None and Decimal(str(value)) * 100 != cents:
            self.report.tally("money", f"{what} rounded to the cent")
        return cents

    # ---- venues ------------------------------------------------------------

    def _map_venues(self, venues: list, skip_venues) -> None:
        orbit = self.data["venues"]
        names = {v.name: v.id for v in venues}
        wanted_skips = {_venue_key(s): s for s in skip_venues}
        has_data = _venue_data_counts(self.data)
        mapped, skipped, ignored, problems = [], [], [], []
        for ov in sorted(orbit, key=lambda v: v["name"] or ""):
            name, key = ov["name"] or "", _venue_key(ov["name"])
            rows = sum(has_data[ov["id"]].values())
            detail = ", ".join(
                f"{t} {n}" for t, n in sorted(has_data[ov["id"]].items())
            )
            if key in wanted_skips:
                wanted_skips.pop(key)
                self.skipped_venues.add(ov["id"])
                skipped.append(f"{name} ({rows} rows: {detail or 'none'})")
                continue
            target, how = None, ""
            if name in names:
                target, how = names[name], "exact"
            else:
                hits = [
                    (n, vid)
                    for n, vid in names.items()
                    if _venue_key(n) == key
                    or _venue_key(n).startswith(key)
                    or key.startswith(_venue_key(n))
                ]
                if len(hits) == 1:
                    target, how = hits[0][1], f"matched '{hits[0][0]}'"
                elif len(hits) > 1:
                    how = "ambiguous: " + ", ".join(sorted(h[0] for h in hits))
            if target:
                self.venue_map[ov["id"]] = target
                mapped.append(f"{name} → {self.norm_venues[target].name} ({how})")
            elif rows:
                problems.append(f"{name} ({detail}){' — ' + how if how else ''}")
            else:
                ignored.append(name)
        if wanted_skips:
            raise ImportRefused(
                "--skip-venue names no Orbit venue: "
                + ", ".join(sorted(wanted_skips.values()))
            )
        self.report.venues = {"mapped": mapped, "skipped": skipped, "ignored": ignored}
        if problems:
            raise ImportRefused(
                "Orbit venues with data have no Norm venue in this organisation: "
                + "; ".join(problems)
                + ". Create/rename the venue in Norm, or pass --skip-venue NAME."
            )
        missing = []
        for norm_id in sorted(set(self.venue_map.values())):
            v = self.norm_venues[norm_id]
            try:
                validate_timezone(v.timezone or "")
            except Invalid:
                missing.append(f"{v.name} ({v.timezone or 'none'})")
        if missing:
            raise ImportRefused(
                "Norm venues without a valid IANA timezone (Functions needs one): "
                + ", ".join(missing)
                + ". Set it in Settings → Venues (e.g. Pacific/Auckland)."
            )

    # ---- run ---------------------------------------------------------------

    def run(self) -> _Plan:
        self.venue_profile()
        self.templates_and_replies()
        self.contacts_and_emails()
        self.mailboxes()  # before enquiries: proposal events resolve accounts
        self.enquiry_rows()
        self.event_rows()
        self.review_unbooked_confirmed()
        self.min_spends()
        self.menus()
        self.proposals()
        self.forms()
        self.mail_rows()
        self._account_children_of_dropped()
        self.report.people = self.people_summary()
        return self.plan

    def _account_children_of_dropped(self) -> None:
        """Rows that hang off a record the transform dropped are dropped with
        it — counted here so every row read is accounted for."""
        d, r = self.data, self.report
        for s in d["enquiry_status_events"]:
            if s["enquiry_id"] not in self.enquiries:
                r.dropped("enquiry_status_events", "enquiry not imported")
        for table in (
            "venue_function_post_confirm_edits",
            "venue_function_status_history",
            "venue_function_timings",
        ):
            for row in d[table]:
                if row["function_id"] not in self.events:
                    r.dropped(table, "booking not imported")
        known = {line["id"] for line in d["contact_proposal_spend_lines"]}
        for ev in d["min_spend_approval_events"]:
            if ev.get("line_id") in known and ev["line_id"] not in self.imported_lines:
                r.dropped("min_spend_approval_events", "line not imported")

    # ---- venue profile -----------------------------------------------------

    def venue_profile(self) -> None:
        d, r = self.data, self.report
        for a in sorted(
            d["venue_event_areas"], key=lambda a: (a.get("sort_index") or 0, a["id"])
        ):
            v = self.venue(a["venue_id"], "venue_event_areas")
            if v is _SKIP or v is None:
                if v is None:
                    r.dropped("venue_event_areas", "no venue")
                continue
            types = [t for t in a.get("event_types") or [] if t in _BOOKING_TYPES]
            for bad in set(a.get("event_types") or []) - _BOOKING_TYPES:
                r.tally(
                    "booking types",
                    f"area booking type '{bad}' not a Norm type (dropped)",
                )
            row = self.plan.add(
                fm.FnVenueArea,
                self.stamp(
                    {
                        "id": self.id("venue_event_areas", a["id"]),
                        "venue_id": v,
                        "name": a["name"],
                        "description": _clean(a.get("description")),
                        "capacity_seated": a.get("capacity_seated"),
                        "capacity_standing": a.get("capacity_standing"),
                        "booking_types": types,
                        "requires_approval": bool(a.get("requires_approval", True)),
                        "min_spend_notes": _clean(a.get("min_spend_notes")),
                        "active": bool(a.get("active", True)),
                        "sort_order": int(a.get("sort_index") or 0),
                    },
                    a.get("created_at"),
                    a.get("updated_at"),
                ),
            )
            self.areas[a["id"]] = (row["id"], v)
            self.area_names[v].setdefault(_venue_key(a["name"]), row["id"])
            r.imported("venue_event_areas")

        # One settings row per venue: approvers, policies, menu links, fields.
        venues = {v["id"]: v for v in d["venues"]}
        ai = defaultdict(list)
        for row in d["venue_ai_context"]:
            ai[row["venue_id"]].append(row)
        links = defaultdict(list)
        for row in d["venue_menu_links"]:
            links[row["venue_id"]].append(row)
        vis = defaultdict(list)
        for row in d["venue_function_option_visibility"]:
            vis[row["venue_id"]].append(row)
        for ov_id in sorted(set(self.venue_map) | set(ai) | set(links) | set(vis)):
            if ov_id not in self.venue_map:
                for table, rows in (
                    ("venue_ai_context", ai),
                    ("venue_menu_links", links),
                    ("venue_function_option_visibility", vis),
                ):
                    self._drop_for_venue(table, ov_id, len(rows.get(ov_id, [])))
                continue
            ov = venues.get(ov_id, {})
            approvers = {}
            for role, column in (
                ("approver", "minimum_spend_approver_id"),
                ("override approver", "minimum_spend_override_approver_id"),
            ):
                member = ov.get(column)
                approvers[role] = self.person(member)[0] if member else None
                if member and not approvers[role]:
                    r.tally(
                        "people", f"venue min-spend {role} not a Norm user (left unset)"
                    )
                    r.example(
                        f"unset {role}",
                        f"{ov.get('name')}: {self.team.get(member, {}).get('name')}",
                    )
            approver, overrider = approvers["approver"], approvers["override approver"]
            policies = _policies_text(
                [c for c in ai.get(ov_id, []) if c.get("active", True)]
            )
            for c in ai.get(ov_id, []):
                if c.get("active", True):
                    r.imported("venue_ai_context")
                else:
                    r.dropped("venue_ai_context", "inactive")
            menu_links = []
            for link in sorted(
                links.get(ov_id, []), key=lambda x: (x.get("sort_index") or 0, x["id"])
            ):
                menu_links.append(
                    {
                        "label": link.get("label"),
                        "url": link.get("url"),
                        "kind": link.get("kind"),
                        "description": link.get("description"),
                        "active": bool(link.get("active", True)),
                        "sort": int(link.get("sort_index") or 0),
                    }
                )
                r.imported("venue_menu_links")
            enabled: dict[str, dict[str, bool]] = {}
            for row in sorted(
                vis.get(ov_id, []), key=lambda x: (x["event_type"], x["option_key"])
            ):
                if row["event_type"] not in _BOOKING_TYPES:
                    r.dropped(
                        "venue_function_option_visibility", "unknown booking type"
                    )
                    continue
                if bool(row.get("visible")) != bool(row.get("visible_admin")):
                    r.tally(
                        "visibility",
                        "staff and client flags differed (client flag kept)",
                    )
                enabled.setdefault(row["event_type"], {})[row["option_key"]] = bool(
                    row.get("visible")
                )
                r.imported("venue_function_option_visibility")
            if not (approver or overrider or policies or menu_links or enabled):
                continue
            stamps = [
                x.get("updated_at")
                for x in ai.get(ov_id, []) + links.get(ov_id, []) + vis.get(ov_id, [])
            ]
            stamp = max((s for s in stamps if s), default=None)
            self.plan.add(
                fm.FnVenueFunctionSettings,
                self.stamp(
                    {
                        "id": self.id("venues.function_settings", ov_id),
                        "venue_id": self.venue_map[ov_id],
                        "approver_user_id": approver,
                        "override_approver_user_id": overrider,
                        "policies": policies,
                        "menu_links": menu_links,
                        "enabled_fields": enabled,
                        "notify_emails": [],
                    },
                    stamp,
                    stamp,
                ),
            )

        # Brand: identity, template, image library.
        templates = {
            t["venue_id"]: t
            for t in sorted(
                d["venue_quote_templates"], key=lambda t: t.get("updated_at") or ""
            )
            if (t.get("template_html") or "").strip()
            or (t.get("template_css") or "").strip()
        }
        for t in d["venue_quote_templates"]:
            if t["venue_id"] in templates and templates[t["venue_id"]] is t:
                continue
            r.dropped(
                "venue_quote_templates",
                "empty template (Orbit renders from brand styles)"
                if not (
                    (t.get("template_html") or "").strip()
                    or (t.get("template_css") or "").strip()
                )
                else "older template of the venue",
            )
        styles: dict[str, dict] = {}
        for b in sorted(
            d["venue_brand_styles"], key=lambda b: (b.get("updated_at") or "", b["id"])
        ):
            if b["venue_id"] in styles:
                r.dropped("venue_brand_styles", "older brand of the same venue")
            styles[b["venue_id"]] = b
        quote_images = {q["id"]: q for q in d["quote_images"]}
        for ov_id in sorted(set(styles) | set(templates)):
            v = self.venue(
                ov_id,
                "venue_brand_styles" if ov_id in styles else "venue_quote_templates",
            )
            if v is _SKIP or v is None:
                if ov_id in styles and ov_id in templates:
                    self._drop_for_venue("venue_quote_templates", ov_id, 1)
                continue
            b = styles.get(ov_id) or {}
            t = templates.get(ov_id) or {}
            style = dict(b.get("style") or {})
            for extra in ("image_radius", "cover_background", "closing_background"):
                if b.get(extra) is not None:
                    style[extra] = b[extra]
            colours = (
                self._brand_colours(b, style, self.norm_venues[v].name) if b else {}
            )
            fonts = {"heading": style.get("headingFont"), "body": style.get("bodyFont")}
            signoff = {
                "title": _clean(b.get("signoff_title")),
                "body": _clean(b.get("signoff_body")),
                "contact": _clean(b.get("signoff_contact")),
            }
            row = self.plan.add(
                fm.FnVenueBrand,
                self.stamp(
                    {
                        "id": self.id(
                            "venue_brand_styles", b.get("id") or f"template:{ov_id}"
                        ),
                        "venue_id": v,
                        "wordmark": _clean(b.get("wordmark")),
                        "tagline": _clean(b.get("tagline")),
                        "colours": colours,
                        "fonts": {k: f for k, f in fonts.items() if f},
                        "terms": [
                            _term_text(x)
                            for x in (b.get("quote_terms") or [])
                            if _term_text(x)
                        ],
                        "inclusions": [
                            str(x).strip()
                            for x in (b.get("quote_inclusions") or [])
                            if str(x).strip()
                        ],
                        "signoff": {k: s for k, s in signoff.items() if s},
                        "style": style,
                        "footer_contact": _clean(b.get("footer_contact")),
                        "valid_days": int(b.get("quote_valid_days") or 14),
                        "template_html": _clean(t.get("template_html")),
                        "template_css": _clean(t.get("template_css")),
                    },
                    b.get("created_at") or t.get("created_at"),
                    b.get("updated_at") or t.get("updated_at"),
                ),
            )
            if b:
                r.imported("venue_brand_styles")
                if any(isinstance(x, dict) for x in b.get("quote_terms") or []):
                    r.tally(
                        "brand",
                        "terms {title, body} flattened to 'Title — body' strings",
                    )
            if t:
                r.imported("venue_quote_templates")
            for field_name, url in (("logo_file_id", b.get("logo_url")),):
                if url:
                    self._want(url, "brand logo")
                    self._brand_files.append(
                        (row, field_name, html_lib.unescape(url).split("?", 1)[0])
                    )
            for field_name, image_id in (
                ("cover_file_id", b.get("cover_image_id")),
                ("closing_file_id", b.get("closing_image_id")),
            ):
                q = quote_images.get(image_id) if image_id else None
                if q and q.get("public_url"):
                    self._brand_files.append(
                        (row, field_name, q["public_url"].split("?", 1)[0])
                    )
            for url in _storage_urls(
                *(s for s in style.values() if isinstance(s, str))
            ):
                self._want(url, "brand style")
        for q in sorted(
            d["quote_images"], key=lambda q: (q.get("created_at") or "", q["id"])
        ):
            v = self.venue(q["venue_id"], "quote_images")
            if v is _SKIP or v is None:
                continue
            if not q.get("public_url"):
                r.dropped("quote_images", "no URL")
                continue
            self._want(q["public_url"], "proposal image library")
            self._quote_images.append((q, v))

    def sanitise_rich_text(self) -> None:
        """Stored HTML is clean HTML. Every rich-text column goes through the
        cleaner its owner's save path uses — proposals, sent versions and
        brand templates F5's ``sanitize_proposal`` (after the image rewrite,
        so copied images survive and the rest go), reply templates and form
        intros F5's ``sanitize_html``, mailbox signatures F4's outbound
        cleaner. Displays sanitise again anyway; this keeps what a Code tab
        shows honest. What each pass removed is counted."""
        from app.services.functions.mailbox.display import clean_outbound_html
        from app.services.functions.sanitize import sanitize_html, sanitize_proposal

        def fragment(value):
            return sanitize_html(value)[0]

        rows = self.plan.rows
        for what, table, html_col, css_col in (
            ("proposal", "fn_proposals", "html", "css"),
            ("sent proposal version", "fn_proposal_versions", "html", "css"),
            ("brand template", "fn_venue_brands", "template_html", "template_css"),
        ):
            for row in rows.get(table, {}).values():
                if not (row.get(html_col) or row.get(css_col)):
                    continue
                before_html, before_css = row[html_col] or "", row[css_col] or ""
                try:
                    after_html, after_css = sanitize_proposal(before_html, before_css)
                except Invalid:
                    after_html, after_css = "", ""
                    self.report.tally(
                        "sanitiser", f"{what}: too large or deep to clean (emptied)"
                    )
                self._removed(what, before_html, after_html, before_css, after_css)
                row[html_col] = (
                    after_html if after_html or table != "fn_venue_brands" else None
                )
                row[css_col] = (
                    after_css if after_css or table != "fn_venue_brands" else None
                )
        for what, table, col, clean in (
            ("reply template", "fn_reply_templates", "body_html", fragment),
            ("form intro", "fn_web_forms", "intro_html", fragment),
            (
                "mailbox signature",
                "fn_mailbox_accounts",
                "signature_html",
                clean_outbound_html,
            ),
        ):
            for row in rows.get(table, {}).values():
                if not row.get(col):
                    continue
                try:
                    after = clean(row[col])
                except Invalid:
                    after = ""
                    self.report.tally(
                        "sanitiser", f"{what}: too large or deep to clean (emptied)"
                    )
                self._removed(what, row[col], after)
                row[col] = after if after or col == "body_html" else None

    def _removed(
        self, what, before_html, after_html, before_css="", after_css=""
    ) -> None:
        """Count what a sanitiser pass took out (markup it merely re-wrote —
        quoting, entities, closing tags — is not a removal)."""
        r = self.report
        lost = _tags(before_html) - _tags(after_html)
        changed = False
        for tag, n in sorted(lost.items()):
            if tag == "style" and after_css:
                r.tally(
                    "sanitiser", f"{what}: <style> block moved into the stylesheet", n
                )
            else:
                r.tally("sanitiser", f"{what}: <{tag}> removed", n)
                changed = True
        handlers = len(_HANDLER.findall(before_html)) - len(
            _HANDLER.findall(after_html)
        )
        if handlers > 0:
            r.tally("sanitiser", f"{what}: event-handler attributes removed", handlers)
            changed = True
        img_src = len(re.findall(r"<img[^>]*\ssrc\s*=", before_html, re.I)) - len(
            re.findall(r"<img[^>]*\ssrc\s*=", after_html, re.I)
        )
        if img_src > 0:
            r.tally("sanitiser", f"{what}: image sources not on Norm removed", img_src)
            changed = True
        before_all = (before_css + before_html).lower()
        after_all = (after_css + after_html).lower()
        for token, label in (
            ("@import", "@import rules"),
            ("@font-face", "@font-face rules"),
            ("url(", "CSS url() references"),
            ("expression(", "CSS expressions"),
        ):
            n = before_all.count(token) - after_all.count(token)
            if n > 0:
                r.tally("sanitiser", f"{what}: {label} removed", n)
                changed = True
        if changed:
            r.tally("sanitiser", f"{what}s with something removed")

    def _brand_colours(self, b: dict, style: dict, venue: str) -> dict:
        """Orbit's palette (quote/brand.ts ``quotePalette``) in Norm's terms.

        Norm's ``panel`` is the light box behind menus and spaces, read in
        the body text colour; Orbit's ``panelColor`` is the opposite — a dark
        band (the minimum-spend bar) with light text on it. Copied across, it
        put Glass Goose's menus at 1.3:1. So ``panel`` becomes what Orbit
        itself uses behind body text, its ``soft`` tint (the accent at 7% over
        the background), kept only when the text reads on it (WCAG 4.5:1) —
        else Norm's default applies. Every colour must be hex (F3's rule)."""
        r = self.report
        background = style.get("background")
        if _hex(background) is None and str(background or "").strip().lower() in (
            "",
            "transparent",
            "none",
        ):
            background = "#ffffff"  # Orbit's own rule for a see-through background
        # Each role takes the first usable (hex) colour Orbit offers for it,
        # in quotePalette's order.
        accents = (
            b.get("accent_color"),
            style.get("buttonBackground"),
            style.get("inputFocusColor"),
        )
        candidates = {
            "primary": (style.get("headingColor"), *accents),
            "accent": accents,
            "text": (style.get("foreground"),),
            "background": (background,),
        }
        out = {}
        for key, values in candidates.items():
            usable = [_hex(v) for v in values if _hex(v)]
            out[key] = usable[0] if usable else None
            if values[0] and not _hex(values[0]):
                r.tally(
                    "brand",
                    f"{key} colour '{values[0]}' is not hex (next choice or Norm's default)",
                )
        tint = _blend(out["accent"], out["background"] or "#ffffff", 0.07)
        text = out["text"] or "#1a1a1a"
        if tint and _contrast(text, tint) >= 4.5:
            out["panel"] = tint
        if b.get("panel_color"):
            r.tally(
                "brand",
                "Orbit panel colour (a dark band) not used as Norm's panel (a light box)",
            )
        colours = {k: c for k, c in out.items() if c}
        panel = colours.get("panel", "#f6f3ee")
        r.example(
            "brand colours",
            f"{venue}: panel Orbit {b.get('panel_color') or '—'} (text {_contrast(text, _hex(b.get('panel_color')) or panel):.1f}:1)"
            f" → Norm {panel} (text {_contrast(text, panel):.1f}:1); text on background "
            f"{_contrast(text, colours.get('background', '#ffffff')):.1f}:1",
            cap=20,
        )
        return colours

    def _want(self, url: str, purpose: str) -> None:
        self.wanted_files[html_lib.unescape(url).split("?", 1)[0]].add(purpose)

    # ---- templates and reply templates ------------------------------------

    def templates_and_replies(self) -> None:
        d, r = self.data, self.report
        stage_of = {"new_enquiry": "first_reply", "follow_up": "follow_up"}
        for t in sorted(
            d["function_reply_templates"],
            key=lambda t: (t.get("sort_index") or 0, t["id"]),
        ):
            v = self.venue(t.get("venue_id"), "function_reply_templates")
            if v is _SKIP:
                continue
            stage = stage_of.get(t.get("classification"))
            if not stage:
                r.dropped(
                    "function_reply_templates",
                    f"classification '{t.get('classification')}'",
                )
                continue
            booking_type = (
                t.get("event_type") if t.get("event_type") in _BOOKING_TYPES else None
            )
            if booking_type is None:
                r.tally(
                    "booking types",
                    f"reply template for '{t.get('event_type')}' → all booking types",
                )
            self.plan.add(
                fm.FnReplyTemplate,
                self.stamp(
                    {
                        "id": self.id("function_reply_templates", t["id"]),
                        "venue_id": v,
                        "booking_type": booking_type,
                        "stage": stage,
                        "name": t.get("name") or "Reply",
                        "subject": _clean(t.get("subject")),
                        "body_html": t.get("body_html") or _text_to_html(t.get("body")),
                        "active": bool(t.get("is_active", True)),
                        "sort_order": int(t.get("sort_index") or 0),
                    },
                    t.get("created_at"),
                    t.get("updated_at"),
                ),
            )
            r.imported("function_reply_templates")

        items = defaultdict(list)
        for i in d["preorder_template_items"]:
            items[i["template_id"]].append(i)
        for t in sorted(
            d["preorder_templates"], key=lambda t: (t.get("created_at") or "", t["id"])
        ):
            v = self.venue(t.get("venue_id"), "preorder_templates")
            if v is _SKIP or v is None:
                if v is None:
                    r.dropped("preorder_templates", "no venue")
                r.dropped(
                    "preorder_template_items",
                    "template not imported",
                    len(items[t["id"]]),
                )
                continue
            is_set = bool(t.get("is_set_menu"))
            row = self.plan.add(
                fm.FnPreorderTemplate,
                self.stamp(
                    {
                        "id": self.id("preorder_templates", t["id"]),
                        "venue_id": v,
                        "name": t.get("name") or "Menu",
                        "description": _clean(t.get("description")),
                        "is_set_menu": is_set,
                        "price_per_person_cents": self.cents(
                            t.get("price_per_person"), "set-menu price"
                        )
                        if is_set
                        else None,
                        "active": bool(t.get("is_active", True)),
                        "sort_order": 0,
                    },
                    t.get("created_at"),
                    t.get("updated_at"),
                ),
            )
            self.templates[t["id"]] = row
            r.imported("preorder_templates")
            for i in sorted(
                items[t["id"]], key=lambda i: (i.get("sort_order") or 0, i["id"])
            ):
                kind = "header" if i.get("item_type") == "header" else "item"
                price = None
                if kind == "item" and not is_set:
                    price = self.cents(i.get("unit_price"), "template dish price")
                elif kind == "item" and float(i.get("unit_price") or 0) > 0:
                    r.tally(
                        "menus",
                        "set-menu template dish price dropped (set menus are priced per person)",
                    )
                self.plan.add(
                    fm.FnPreorderTemplateItem,
                    self.stamp(
                        {
                            "id": self.id("preorder_template_items", i["id"]),
                            "template_id": row["id"],
                            "kind": kind,
                            "name": i.get("name") or "Item",
                            "description": _clean(i.get("description")),
                            "unit_price_cents": price,
                            "default_percent": _percent(i.get("default_percentage")),
                            "dietaries": [],
                            "loaded_recipe_id": _clean(i.get("recipe_id")),
                            "loaded_stock_item_id": _clean(i.get("stock_item_id")),
                            "sort_order": int(i.get("sort_order") or 0),
                        },
                        i.get("created_at"),
                        i.get("created_at"),
                    ),
                )
                r.imported("preorder_template_items")

    # ---- contacts ------------------------------------------------------------

    def contacts_and_emails(self) -> None:
        d, r = self.data, self.report
        contacts = sorted(
            d["contacts"], key=lambda c: (c.get("created_at") or "", c["id"])
        )
        rank = {c["id"]: n for n, c in enumerate(contacts)}
        names = {}
        for c in contacts:
            name = (
                _clean(c.get("contact_name"))
                or _clean(c.get("company"))
                or _clean(c.get("contact_email"))
                or "Unnamed contact"
            )
            if not _clean(c.get("contact_name")):
                r.tally("contacts", "no name in Orbit (company/email/placeholder used)")
            names[c["id"]] = name
            row = self.plan.add(
                fm.FnContact,
                self.stamp(
                    {
                        "id": self.id("contacts", c["id"]),
                        "name": name,
                        "company": _clean(c.get("company")),
                        "phone": _clean(c.get("contact_phone")),
                        "notes": _clean(c.get("notes")),
                    },
                    c.get("created_at"),
                    c.get("updated_at"),
                ),
            )
            self.contacts[c["id"]] = row["id"]
            r.imported("contacts")

        # Every address once per organisation: the contact whose own address it
        # is wins, then the one Orbit marked primary, then the oldest contact.
        claims: dict[str, list] = defaultdict(list)
        for c in contacts:
            email = _email(c.get("contact_email"))
            if c.get("contact_email") and not email:
                r.tally("contacts", "malformed contact email (dropped)")
            if email:
                claims[email].append(((0, 0, rank[c["id"]]), c["id"], None))
        for e in d["contact_emails"]:
            email = _email(e.get("email"))
            if not email or e["contact_id"] not in self.contacts:
                r.dropped(
                    "contact_emails",
                    "malformed address" if not email else "contact missing",
                )
                continue
            primary = 0 if e.get("is_primary") else 1
            claims[email].append(
                ((1, primary, rank.get(e["contact_id"], 1 << 30)), e["contact_id"], e)
            )
        owned: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for email, cl in sorted(claims.items()):
            cl.sort(key=lambda x: (x[0], x[1]))
            owner = cl[0][1]
            owned[owner].append(
                (
                    email,
                    0 if cl[0][2] is None else (1 if cl[0][2].get("is_primary") else 2),
                )
            )
            self.email_owner[email] = self.contacts[owner]
            contacts_sharing = {x[1] for x in cl}
            if len(contacts_sharing) > 1:
                distinct = {names[x].strip().lower() for x in contacts_sharing}
                kind = "same name" if len(distinct) == 1 else "different names"
                r.tally(
                    "contacts",
                    f"addresses shared by several Orbit contacts — {kind} (reported, not merged)",
                )
                if kind == "different names":
                    r.example(
                        "shared address, different names",
                        f"{email.split('@')[0][:3]}…@{email.split('@')[-1]} ×{len(contacts_sharing)}",
                    )
            for _, contact_id, e in cl:
                if e is None:
                    continue
                if contact_id == owner:
                    r.imported("contact_emails")
                else:
                    r.merged("contact_emails", "address belongs to another contact")
        by_id = {c["id"]: c for c in contacts}
        for contact_oid, emails in owned.items():
            # Primary: the contact's own address, else Orbit's primary row.
            emails.sort(key=lambda x: (x[1], x[0]))
            for n, (email, _) in enumerate(emails):
                self._add_email(
                    self.contacts[contact_oid],
                    email,
                    primary=n == 0,
                    stamp=by_id[contact_oid],
                )

    def _add_email(
        self, contact_id: str, email: str, *, primary: bool, stamp: dict
    ) -> None:
        self.plan.add(
            fm.FnContactEmail,
            self.stamp(
                {
                    "id": self.id("contact_email", email),
                    "contact_id": contact_id,
                    "email": email,
                    "is_primary": primary,
                    "source": "import",
                },
                stamp.get("created_at"),
                stamp.get("updated_at"),
            ),
        )

    def contact_for(
        self, key: str, *, email, name, company=None, phone=None, stamp: dict
    ) -> str | None:
        """A contact for a copy on an event or form: the owner of the email,
        else a new contact (deterministic id from ``key``)."""
        address = _email(email)
        if address and address in self.email_owner:
            self.report.tally("contacts", "event/invoice contact matched by email")
            return self.email_owner[address]
        label = _clean(name) or _clean(company) or address
        if not label:
            return None
        row = self.plan.add(
            fm.FnContact,
            self.stamp(
                {
                    "id": self.id("contacts.created", key),
                    "name": label,
                    "company": _clean(company),
                    "phone": _clean(phone),
                },
                stamp.get("created_at"),
                stamp.get("updated_at"),
            ),
        )
        if address:
            self.email_owner[address] = row["id"]
            self._add_email(row["id"], address, primary=True, stamp=stamp)
        self.report.tally(
            "contacts",
            "contact created from an event's contact copy"
            if not key.startswith("invoice")
            else "invoice contact created",
        )
        return row["id"]

    # ---- enquiries -------------------------------------------------------------

    def _booking_type(self, raw, *, linked=(), area_ids=(), ai=None, what="enquiry"):
        """Orbit's free-text booking type as the enum, from the strongest
        evidence available: Orbit's own value, the linked record's (an
        enquiry's booked event, an event's enquiry), the single type its
        quoted areas are offered for, Orbit's AI classification. Nothing is
        guessed from the words ("cocktail" could be either kind of event)."""
        value = _clean(raw)
        if value in _BOOKING_TYPES:
            return value
        result, evidence = None, "left blank"
        valid_linked = sorted({t for t in linked if t in _BOOKING_TYPES})
        area_types = set()
        for a in area_ids:
            types = [t for t in (self._area_types.get(a) or []) if t in _BOOKING_TYPES]
            area_types.add(types[0] if len(types) == 1 else None)
        if len(valid_linked) == 1:
            result = valid_linked[0]
            evidence = (
                "booked event's type" if what == "enquiry" else "its enquiry's type"
            )
        elif area_types and None not in area_types and len(area_types) == 1:
            result, evidence = area_types.pop(), "single type of the quoted area"
        elif ai in _BOOKING_TYPES:
            result, evidence = ai, "Orbit's AI classification"
        self.report.tally(
            "booking types",
            f"{what} '{value or '(blank)'}' → {result or 'blank'} ({evidence})",
        )
        return result

    @property
    def _area_types(self) -> dict:
        if not hasattr(self, "_area_types_cache"):
            self._area_types_cache = {
                a["id"]: a.get("event_types") for a in self.data["venue_event_areas"]
            }
        return self._area_types_cache

    def enquiry_rows(self) -> None:
        d, r = self.data, self.report
        functions_by_enquiry = defaultdict(list)
        for f in d["venue_functions"]:
            if f.get("enquiry_id"):
                functions_by_enquiry[f["enquiry_id"]].append(f)
        won = {
            e["won_function_id"]: e["id"]
            for e in d["enquiries"]
            if e.get("won_function_id")
        }
        for f in d["venue_functions"]:
            if f["id"] in won and not f.get("enquiry_id"):
                functions_by_enquiry[won[f["id"]]].append(f)
        proposals_by_enquiry = defaultdict(list)
        for p in d["contact_proposals"]:
            proposals_by_enquiry[p["enquiry_id"]].append(p)
        lines_by_proposal = defaultdict(list)
        for line in d["contact_proposal_spend_lines"]:
            lines_by_proposal[line["proposal_id"]].append(line)
        attribution = defaultdict(list)
        for a in d["lead_attributions"]:
            if a.get("enquiry_id"):
                attribution[a["enquiry_id"]].append(a)
        contacts = {c["id"]: c for c in d["contacts"]}
        status_events = defaultdict(list)
        for s in d["enquiry_status_events"]:
            status_events[s["enquiry_id"]].append(s)
        sources = {
            "email": "email",
            "manual": "manual",
            "mcp": "agent",
            "legacy_event": "import",
            "migrated": "import",
            "web_form": "web_form",
            "phone": "phone",
        }

        for e in sorted(
            d["enquiries"], key=lambda e: (e.get("created_at") or "", e["id"])
        ):
            v = self.venue(e.get("venue_id"), "enquiries")
            if v is _SKIP:
                continue
            contact = self.contacts.get(e["contact_id"])
            if not contact:
                r.dropped("enquiries", "contact missing in Orbit")
                continue
            tz = self.tz(v)
            area_ids = [
                line.get("area_id") or p.get("area_id")
                for p in proposals_by_enquiry[e["id"]]
                for line in lines_by_proposal[p["id"]] or [p]
                if line.get("area_id") or p.get("area_id")
            ]
            booking_type = self._booking_type(
                e.get("function_subtype"),
                linked=[
                    f.get("function_subtype")
                    for f in functions_by_enquiry[e["id"]]
                    if f.get("status") != "cancelled"
                ],
                area_ids=area_ids,
                ai=e.get("ai_event_type"),
            )
            leads = sorted(
                attribution[e["id"]], key=lambda a: (a.get("created_at") or "", a["id"])
            )
            lead = leads[0] if leads else None
            if len(leads) > 1:
                r.tally("enquiries", "several lead attributions (first touch kept)")
            source = sources.get(e.get("source") or "", "other")
            if source == "other":
                r.tally("enquiries", f"source '{e.get('source')}' → other")
            if lead and (lead.get("submission_id") or lead.get("form_id")):
                source = "web_form"
            start = _date(e.get("preferred_date_start"), tz)
            end = _date(e.get("preferred_date_end"), tz)
            if start and end and end < start:
                r.tally(
                    "times", "enquiry preferred end date before its start (end dropped)"
                )
                end = None
            follow = _date(e.get("callback_date"), tz)
            contact_row = contacts.get(e["contact_id"]) or {}
            owner = None
            if contact_row.get("assigned_to_member_id"):
                owner = self.person(contact_row["assigned_to_member_id"])[0]
                if not owner:
                    r.tally(
                        "people",
                        "enquiry owner (contact's assignee) not a Norm user (left unset)",
                    )
            row = self.plan.add(
                fm.FnEnquiry,
                self.stamp(
                    {
                        "id": self.id("enquiries", e["id"]),
                        "contact_id": contact,
                        "venue_id": v,
                        "booking_type": booking_type,
                        "subject": _clean(e.get("subject")),
                        "guests": e.get("guest_count"),
                        "preferred_date_start": start,
                        "preferred_date_end": end,
                        "source": source,
                        "utm_source": _clean((lead or {}).get("utm_source")),
                        "utm_medium": _clean((lead or {}).get("utm_medium")),
                        "utm_campaign": _clean((lead or {}).get("utm_campaign")),
                        "utm_term": _clean((lead or {}).get("utm_term")),
                        "utm_content": _clean((lead or {}).get("utm_content")),
                        "gclid": _clean((lead or {}).get("gclid")),
                        "referrer": _clean((lead or {}).get("referrer")),
                        "landing_page": _clean((lead or {}).get("landing_page")),
                        "owner_user_id": owner,
                        "follow_up_date": follow,
                        "follow_up_reason": _clean(e.get("callback_reason")),
                        "client_note": _clean(e.get("client_note")),
                        "notes": _clean(e.get("notes")),
                        "created_by_label": ACTOR,
                    },
                    e.get("created_at"),
                    e.get("updated_at"),
                ),
            )
            self.enquiries[e["id"]] = row
            r.imported("enquiries")
            self._closures(e, row, status_events[e["id"]])

    def _closures(self, e: dict, row: dict, events: list) -> None:
        """Lost / reopened facts: Orbit's status-event history, made to agree
        with the enquiry's current status (the history has gaps — older
        transitions were never logged)."""
        r = self.report
        facts = []
        for s in sorted(events, key=lambda s: (s.get("created_at") or "", s["id"])):
            if s.get("to_status") == "lost" and s.get("from_status") != "lost":
                facts.append(("lost", s))
            elif s.get("from_status") == "lost" and s.get("to_status") != "lost":
                facts.append(("reopened", s))
            if s.get("to_status") == s.get("from_status"):
                r.tally("enquiries", "status event without a change (ignored)")
        is_lost = e.get("status") == "lost"
        last_kind = facts[-1][0] if facts else None
        out = []
        for n, (kind, s) in enumerate(facts):
            final_lost = is_lost and kind == "lost" and n == len(facts) - 1
            reason = (
                _clean(s.get("reason"))
                or (_clean(e.get("lost_reason")) if final_lost else None)
                or (
                    "Closed as lost in Orbit (no reason recorded)"
                    if kind == "lost"
                    else "Reopened in Orbit (no reason recorded)"
                )
            )
            named = _clean(s.get("actor_label"))
            if s.get("actor_member_id"):
                user, label = self.person(s["actor_member_id"], named)
            elif named and named != "System":
                user, label = self.person(None, named)
            else:
                user, label = None, ACTOR  # Orbit's own sweeps
            out.append((kind, reason, user, label, _ts(s.get("created_at")), s["id"]))
        when = _ts(e.get("updated_at")) or _ts(e.get("created_at"))
        if out and out[-1][4] and when <= out[-1][4]:
            when = out[-1][4] + dt.timedelta(seconds=1)
        if is_lost and last_kind != "lost":
            out.append(
                (
                    "lost",
                    _clean(e.get("lost_reason"))
                    or "Closed as lost in Orbit (no reason recorded)",
                    None,
                    ACTOR,
                    when,
                    "current",
                )
            )
            r.tally(
                "enquiries",
                "lost with no logged transition (closure dated from the enquiry)",
            )
        elif not is_lost and last_kind == "lost":
            out.append(
                (
                    "reopened",
                    "Reopened in Orbit (no reason recorded)",
                    None,
                    ACTOR,
                    when,
                    "current",
                )
            )
            r.tally("enquiries", "reopened with no logged transition")
        for kind, reason, user, label, at, key in out:
            self.plan.add(
                fm.FnEnquiryClosure,
                {
                    "id": self.id("enquiries.closure", f"{e['id']}:{key}"),
                    "enquiry_id": row["id"],
                    "kind": kind,
                    "reason": reason,
                    "actor_user_id": user,
                    "actor_label": label,
                    "occurred_at": at,
                    "created_at": at,
                    "updated_at": at,
                },
            )
            r.tally("facts", f"enquiry {kind}")
        used = {key for *_, key in out}
        for s in events:
            if s["id"] in used:
                r.imported("enquiry_status_events")
            else:
                r.merged(
                    "enquiry_status_events",
                    "status move derived in Norm (not a closure)",
                )

    # ---- events ----------------------------------------------------------------

    def event_rows(self) -> None:
        d, r = self.data, self.report
        enquiries = {e["id"]: e for e in d["enquiries"]}
        won = {
            e["won_function_id"]: e["id"]
            for e in d["enquiries"]
            if e.get("won_function_id")
        }
        history = defaultdict(list)
        for h in d["venue_function_status_history"]:
            history[h["function_id"]].append(h)
        timings = defaultdict(list)
        for t in d["venue_function_timings"]:
            timings[t["function_id"]].append(t)
        edits = defaultdict(list)
        for p in d["venue_function_post_confirm_edits"]:
            edits[p["function_id"]].append(p)
        spend_area = {}
        for o in d["venue_function_spend_options"]:
            if o.get("spend_status") == "confirmed" and o.get("area_id"):
                spend_area.setdefault(o["function_id"], o["area_id"])

        functions = sorted(
            d["venue_functions"], key=lambda f: (f.get("created_at") or "", f["id"])
        )
        # One live event per enquiry: the enquiry's won function, else the
        # oldest live one. The rest keep their data, unlinked, and are counted.
        live_owner: dict[str, str] = {}
        for f in functions:
            eid = f.get("enquiry_id") or won.get(f["id"])
            if not eid or f.get("status") == "cancelled":
                continue
            current = live_owner.get(eid)
            if current is None or (
                enquiries.get(eid, {}).get("won_function_id") == f["id"]
            ):
                live_owner[eid] = f["id"]

        for f in functions:
            v = self.venue(f.get("venue_id"), "venue_functions")
            if v is _SKIP or v is None:
                if v is None:
                    r.dropped("venue_functions", "no venue")
                continue
            tz = self.tz(v)
            eid = f.get("enquiry_id") or won.get(f["id"])
            enquiry = self.enquiries.get(eid) if eid else None
            if eid and not enquiry:
                r.tally("events", "linked enquiry not imported (event kept without it)")
            if (
                enquiry
                and f.get("status") != "cancelled"
                and live_owner.get(eid) != f["id"]
            ):
                r.tally(
                    "events",
                    "second live booking of one enquiry (kept, unlinked from it)",
                )
                enquiry = None
            contact = self._event_contact(f, enquiry)
            invoice = None
            if (
                _clean(f.get("invoice_contact_email"))
                or _clean(f.get("invoice_contact_name"))
                or _clean(f.get("invoice_company_name"))
            ):
                invoice = self.contact_for(
                    f"invoice:{f['id']}",
                    email=f.get("invoice_contact_email"),
                    name=f.get("invoice_contact_name"),
                    company=f.get("invoice_company_name"),
                    stamp=f,
                )
            event_date, start, end, next_day = self.window(
                f.get("function_start_at"), f.get("function_end_at"), tz, "event"
            )
            if event_date is None:
                r.dropped("venue_functions", "no readable date")
                continue
            if event_date.year < 2015:
                r.tally("times", "event date before 2015 (kept as recorded)")
            area_id = self._event_area(f, v, spend_area.get(f["id"]))
            notes = _clean(f.get("notes"))
            extra = []
            if _clean(f.get("area")) and not area_id:
                extra.append(f"Area (from Orbit): {f['area'].strip()}")
            if _clean(f.get("drive_file_url")):
                extra.append(f"Function sheet: {f['drive_file_url'].strip()}")
                r.tally("events", "Google Drive function-sheet link kept in notes")
            if extra:
                notes = "\n\n".join([notes] + extra if notes else extra)
            food_timing = f.get("food_timing_type")
            if food_timing and food_timing not in fm.FOOD_TIMING_TYPES:
                r.tally("events", f"food timing '{food_timing}' unknown (left empty)")
                food_timing = None
            booking_type = self._booking_type(
                f.get("function_subtype"),
                linked=[enquiry["booking_type"]]
                if enquiry and enquiry.get("booking_type")
                else (),
                area_ids=[a for a in (spend_area.get(f["id"]),) if a],
                what="event",
            )
            cancelled_at, cancel_reason, cancelled_by = None, None, None
            if f.get("status") == "cancelled":
                hist = [
                    h for h in history[f["id"]] if h.get("to_status") == "cancelled"
                ]
                last = (
                    max(hist, key=lambda h: h.get("created_at") or "") if hist else None
                )
                cancelled_at = _ts((last or {}).get("created_at")) or _ts(
                    f.get("updated_at")
                )
                cancel_reason = "Cancelled in Orbit"
                cancelled_by = (
                    self.person(last.get("changed_by"), last.get("changed_by_name"))[0]
                    if last
                    else None
                )
            if f.get("total_revenue"):
                r.tally("events", "stored total revenue not carried (Norm derives it)")
            if f.get("prepaid_status"):
                r.tally("events", "prepaid status not carried (payment method kept)")
            row = self.plan.add(
                fm.FnEvent,
                self.stamp(
                    {
                        "id": self.id("venue_functions", f["id"]),
                        "enquiry_id": enquiry["id"] if enquiry else None,
                        "contact_id": contact,
                        "invoice_contact_id": invoice,
                        "venue_id": v,
                        "area_id": area_id,
                        "booking_type": booking_type,
                        "name": _clean(f.get("event_name"))
                        or _clean(f.get("name"))
                        or "Function",
                        "event_date": event_date,
                        "start_time": start,
                        "end_time": end,
                        "ends_next_day": next_day,
                        "guests": f.get("guest_count"),
                        "description": _clean(f.get("description")),
                        "notes": notes,
                        "special_instructions": _clean(f.get("special_instructions")),
                        "allergies": _clean(f.get("allergies")),
                        "bar_tab_cents": max(
                            self.cents(f.get("bar_tab"), "bar tab") or 0, 0
                        ),
                        "bar_tab_authorised_people": _clean(
                            f.get("bar_tab_authorised_people")
                        ),
                        "bar_tab_restrictions": _clean(f.get("bar_tab_restrictions")),
                        "bar_tab_notes": _clean(f.get("bar_tab_notes")),
                        "arrival_drinks": _clean(f.get("arrival_drinks")),
                        "table_ordering": bool(f.get("table_ordering")),
                        "table_ordering_limit_cents": self.cents(
                            f.get("table_ordering_limit"), "table-ordering limit"
                        ),
                        "payment_method": _clean(f.get("payment_method")),
                        "deposit_cents": self.cents(f.get("deposit_amount"), "deposit"),
                        "deposit_status": _clean(f.get("deposit_status")),
                        "food_timing_type": food_timing,
                        "food_time": self.clock(
                            f.get("food_timing_time"), "event food time"
                        ),
                        "food_end_time": self.clock(
                            f.get("food_timing_end_time"), "event food end time"
                        ),
                        "food_notes": _clean(f.get("food_notes")),
                        "arrival_time": self.clock(
                            f.get("arrival_time"), "event arrival time"
                        ),
                        "seated_time": self.clock(
                            f.get("seated_time"), "event seated time"
                        ),
                        "timing_preference": _clean(f.get("timing_preference")),
                        "timing_notes": _clean(f.get("timing_notes")),
                        "table_plan": _clean(f.get("table_plan")),
                        "created_via": "import",
                        "confirmation_channel": "import",
                        "created_by_label": ACTOR,
                        "cancelled_at": cancelled_at,
                        "cancel_reason": cancel_reason,
                        "cancelled_by_user_id": cancelled_by,
                    },
                    f.get("created_at"),
                    f.get("updated_at"),
                ),
            )
            self.events[f["id"]] = row
            r.imported("venue_functions")
            if _clean(f.get("image_url")):
                self._want(f["image_url"], "event table plan")
                self._table_plans.append(
                    (row, html_lib.unescape(f["image_url"]).split("?", 1)[0])
                )
            self._event_children(f, row, start, timings[f["id"]])
            self._event_facts(f, row, history[f["id"]], edits[f["id"]])

    def review_unbooked_confirmed(self) -> None:
        """Enquiries Orbit calls confirmed that have no booking here.

        Orbit's status was set by hand, or on an approved minimum spend —
        which in Norm only means "OK to offer"; the customer's confirmation is
        what creates the event. No booking is invented, and none is left
        silently in the open pipeline either: a recent one (preferred date at
        most 14 days past, or created in the last 60 days) gets a follow-up
        dated today so it surfaces as needing attention; a historic one is
        archived with a note. "Today" and "now" are the run's clock."""
        d, r = self.data, self.report
        live = {
            e["enquiry_id"]
            for e in self.events.values()
            if e["enquiry_id"] and not e["cancelled_at"]
        }
        lines = defaultdict(list)
        for line in d["contact_proposal_spend_lines"]:
            lines[line["proposal_id"]].append(line)
        approved = {
            p["enquiry_id"]
            for p in d["contact_proposals"]
            if any(x.get("status") == "approved" for x in lines[p["id"]])
        }
        for e in sorted(
            d["enquiries"], key=lambda e: (e.get("created_at") or "", e["id"])
        ):
            row = self.enquiries.get(e["id"])
            if row is None or e.get("status") != "confirmed" or row["id"] in live:
                continue
            today = self.now.astimezone(self.tz(row["venue_id"])).date()
            preferred = row["preferred_date_start"]
            recent = (
                preferred is not None and preferred >= today - _RECENT_PREFERRED
            ) or row["created_at"] >= self.now - _RECENT_CREATED
            venue = self.norm_venues.get(row["venue_id"])
            entry = {
                "orbit_id": e["id"],
                "norm_id": row["id"],
                "subject": row["subject"],
                "venue": venue.name if venue else "(group-wide)",
                "preferred_date": preferred.isoformat() if preferred else None,
                "created": row["created_at"].date().isoformat(),
                "why": "approved minimum spend, no booking"
                if e["id"] in approved
                else "status set by hand, no booking",
            }
            if recent:
                if row["follow_up_date"] or row["follow_up_reason"]:
                    # Orbit's own callback is not lost: it moves to the notes.
                    kept = f"Orbit callback: {row['follow_up_date'] or ''} {row['follow_up_reason'] or ''}"
                    row["notes"] = "\n\n".join(
                        x for x in (row["notes"], kept.strip()) if x
                    )
                    r.tally(
                        "enquiries",
                        "Orbit callback moved to notes (the rule's follow-up replaces it)",
                    )
                row["follow_up_date"] = today
                row["follow_up_reason"] = UNBOOKED_FOLLOW_UP
                group = "follow_up"
            else:
                row["archived_at"] = self.now
                row["notes"] = "\n\n".join(
                    x for x in (row["notes"], UNBOOKED_ARCHIVE_NOTE) if x
                )
                group = "archived"
            self.reviewed[e["id"]] = group
            r.reviewed[group].append(entry)

    def _event_contact(self, f: dict, enquiry: dict | None) -> str:
        r = self.report
        if f.get("contact_id") in self.contacts:
            return self.contacts[f["contact_id"]]
        if enquiry:
            r.tally("contacts", "event contact taken from its enquiry")
            return enquiry["contact_id"]
        found = self.contact_for(
            f"event:{f['id']}",
            email=f.get("contact_email"),
            name=f.get("contact_name") or f.get("name"),
            company=f.get("company"),
            phone=f.get("contact_phone"),
            stamp=f,
        )
        if found:
            return found
        row = self.plan.add(
            fm.FnContact,
            self.stamp(
                {
                    "id": self.id("contacts.created", f"event:{f['id']}"),
                    "name": "Unknown contact",
                    "notes": "Imported from an Orbit booking with no contact details.",
                },
                f.get("created_at"),
                f.get("updated_at"),
            ),
        )
        r.tally("contacts", "placeholder contact for a booking with no contact details")
        return row["id"]

    def _event_area(self, f: dict, venue_id: str, spend_area: str | None) -> str | None:
        text_value = _clean(f.get("area"))
        if text_value:
            found = self.area_names[venue_id].get(_venue_key(text_value))
            if found:
                self.report.tally("areas", _AREA_MATCHED)
                return found
            self.report.tally("areas", _AREA_UNKNOWN)
            self.report.example(
                "unknown area", f"{self.norm_venues[venue_id].name}: {text_value}"
            )
            return None
        if (
            spend_area
            and spend_area in self.areas
            and self.areas[spend_area][1] == venue_id
        ):
            self.report.tally("areas", "event area taken from its booked minimum spend")
            return self.areas[spend_area][0]
        return None

    def _event_children(
        self, f: dict, row: dict, start: dt.time | None, timing_rows: list
    ) -> None:
        r = self.report
        n = 0
        for t in sorted(
            timing_rows, key=lambda t: (t.get("created_at") or "", t["id"])
        ):
            for item in t.get("items") or []:
                raw = _clean(item.get("time_label"))
                at = parse_clock(raw) if raw else None
                if at is False:
                    at = None
                    r.tally(
                        "times", "run-sheet time kept as a label (not a clock time)"
                    )
                elif at is not None:
                    r.tally("time fields", "run-sheet time")
                self.plan.add(
                    fm.FnEventTiming,
                    self.stamp(
                        {
                            "id": self.id("venue_function_timings", f"{t['id']}:{n}"),
                            "event_id": row["id"],
                            "at_time": at,
                            "ends_next_day": bool(
                                at and start and at < start and at < dt.time(12)
                            ),
                            "label": None if at else raw,
                            "activity": _clean(item.get("activity")) or "",
                            "is_heading": item.get("item_type") == "header",
                            "sort_order": n,
                        },
                        t.get("created_at"),
                        t.get("created_at"),
                    ),
                )
                n += 1
            r.imported("venue_function_timings")
        for kind, items in (
            ("security", f.get("security")),
            ("entertainment", f.get("entertainment")),
            ("av", f.get("av")),
        ):
            for n, item in enumerate(items or []):
                if not isinstance(item, dict):
                    r.tally("events", f"{kind} entry not an object (dropped)")
                    continue
                key = self.id(f"venue_functions.{kind}", f"{f['id']}:{n}")
                if kind == "av":
                    if item.get("type") not in fm.AV_KINDS:
                        r.tally(
                            "events", f"AV type '{item.get('type')}' unknown (dropped)"
                        )
                        continue
                    self.plan.add(
                        fm.FnEventAv,
                        self.stamp(
                            {
                                "id": key,
                                "event_id": row["id"],
                                "kind": item["type"],
                                "details": _clean(item.get("details")),
                                "sort_order": n,
                            },
                            f.get("created_at"),
                            f.get("updated_at"),
                        ),
                    )
                    continue
                s = self.clock(item.get("start_time"), f"{kind} start")
                e = self.clock(item.get("end_time"), f"{kind} end")
                common = {
                    "id": key,
                    "event_id": row["id"],
                    "name": _clean(item.get("name")),
                    "start_time": s,
                    "end_time": e,
                    "ends_next_day": bool(s and e and e <= s),
                    "sort_order": n,
                }
                if kind == "security":
                    self.plan.add(
                        fm.FnEventSecurity,
                        self.stamp(common, f.get("created_at"), f.get("updated_at")),
                    )
                elif item.get("type") in fm.ENTERTAINMENT_KINDS:
                    self.plan.add(
                        fm.FnEventEntertainment,
                        self.stamp(
                            {
                                **common,
                                "kind": item["type"],
                                "link": _clean(item.get("link")),
                            },
                            f.get("created_at"),
                            f.get("updated_at"),
                        ),
                    )
                else:
                    r.tally(
                        "events",
                        f"entertainment type '{item.get('type')}' unknown (dropped)",
                    )

    def _event_facts(self, f: dict, row: dict, history: list, edits: list) -> None:
        """Section confirmations, client edits, link sent, created/cancelled
        and post-confirmation edits — the facts Norm derives the status from.

        ``actor_kind`` says who acted when Orbit knew (a client confirmed, a
        staff member skipped) because status reads it: an imported client
        edit must count as a client edit. Facts whose actor Orbit never
        recorded are ``import``."""
        r = self.report
        done_at: dict[str, dt.datetime] = {}
        for sec in fm.EVENT_SECTIONS:
            confirmed = _ts(f.get(f"client_{sec}_confirmed_at"))
            skipped = _ts(f.get(f"client_{sec}_skipped_at"))
            if confirmed:
                self._section(row, sec, "confirmed", "client", confirmed, f["id"])
            if skipped:
                user, label = self.person(
                    f.get(f"client_{sec}_skipped_by"),
                    f.get(f"client_{sec}_skipped_by_name"),
                )
                self._section(
                    row,
                    sec,
                    "skipped",
                    "staff",
                    skipped,
                    f["id"],
                    user=user,
                    label=label,
                    note=_clean(f.get(f"client_{sec}_skipped_note")),
                )
            if confirmed or skipped:
                done_at[sec] = max(t for t in (confirmed, skipped) if t)
        all_done_at = (
            max(done_at.values()) if len(done_at) == len(fm.EVENT_SECTIONS) else None
        )
        for sec in fm.EVENT_SECTIONS:
            edited = _ts(f.get(f"client_{sec}_edited_at"))
            if edited:
                self._change(
                    row,
                    "edited",
                    "client",
                    edited,
                    f"{f['id']}:edit:{sec}",
                    section=sec,
                    after=bool(all_done_at and edited > all_done_at),
                )
        sent = _ts(f.get("client_link_sent_at"))
        if sent:
            self._change(
                row, "link_sent", "import", sent, f"{f['id']}:link_sent", label=ACTOR
            )
        elif f.get("status") == "sent_to_client":
            # Before Orbit stamped client_link_sent_at, staff set this status
            # by hand when they sent the link: the status IS the evidence.
            # Its time: when the client link was made, else the last update.
            link_made = min(self._link_times.get(f["id"], []), default=None)
            self._change(
                row,
                "link_sent",
                "import",
                link_made or _ts(f.get("updated_at")),
                f"{f['id']}:link_sent",
                label=ACTOR,
                note="Orbit recorded this as sent to the client without a send time.",
            )
            r.tally(
                "events",
                "marked sent to client with no send time (link time from "
                + ("the client link's creation)" if link_made else "the last update)"),
            )
        created = [
            h
            for h in history
            if not h.get("from_status") and h.get("to_status") == "booked"
        ]
        if created:
            h = min(created, key=lambda h: (h.get("created_at") or "", h["id"]))
            user, label = self.person(h.get("changed_by"), h.get("changed_by_name"))
            kind = (
                "staff" if (h.get("source") == "app" and (user or label)) else "system"
            )
            self._change(
                row,
                "created",
                kind,
                _ts(h.get("created_at")),
                f"{f['id']}:created",
                user=user,
                label=label or (None if user else ACTOR),
            )
        else:
            self._change(
                row,
                "created",
                "import",
                row["created_at"],
                f"{f['id']}:created",
                label=ACTOR,
            )
        for h in history:
            if h in created[:1]:
                r.imported("venue_function_status_history")
            elif h.get("to_status") == "cancelled":
                r.imported("venue_function_status_history")
            else:
                r.merged("venue_function_status_history", "status move derived in Norm")
        if row["cancelled_at"]:
            self._change(
                row,
                "cancelled",
                "import",
                row["cancelled_at"],
                f"{f['id']}:cancelled",
                label=ACTOR,
                note=row["cancel_reason"],
            )
        for p in sorted(edits, key=lambda p: (p.get("created_at") or "", p["id"])):
            user, label = self.person(p.get("edited_by"), p.get("edited_by_name"))
            self._change(
                row,
                "edited",
                "staff",
                _ts(p.get("created_at")),
                f"post:{p['id']}",
                user=user,
                label=label,
                note=_clean(p.get("note")),
                after=True,
            )
            r.imported("venue_function_post_confirm_edits")

    def _section(
        self, row, sec, kind, actor_kind, at, fid, *, user=None, label=None, note=None
    ):
        self.plan.add(
            fm.FnEventSectionConfirmation,
            {
                "id": self.id("venue_functions.section", f"{fid}:{sec}:{kind}"),
                "event_id": row["id"],
                "section": sec,
                "kind": kind,
                "actor_kind": actor_kind,
                "actor_user_id": user,
                "actor_label": label,
                "note": note,
                "occurred_at": at,
                "created_at": at,
                "updated_at": at,
            },
        )
        self.report.tally("facts", f"section {kind} ({actor_kind})")

    def _change(
        self,
        row,
        kind,
        actor_kind,
        at,
        key,
        *,
        user=None,
        label=None,
        note=None,
        section=None,
        after=False,
    ):
        self.plan.add(
            fm.FnEventChange,
            {
                "id": self.id("venue_functions.change", key),
                "event_id": row["id"],
                "kind": kind,
                "actor_kind": actor_kind,
                "actor_user_id": user,
                "actor_label": label,
                "section": section,
                "changes": None,
                "after_confirmation": after,
                "note": note,
                "occurred_at": at,
                "created_at": at,
                "updated_at": at,
            },
        )
        self.report.tally("facts", f"event {kind} ({actor_kind})")

    # ---- minimum spends ------------------------------------------------------------

    def min_spends(self) -> None:
        """Proposal lines and event spend options become ONE set of options.

        A line is an option on its enquiry. When Orbit converted its proposal
        into a booking it copied each line into a ``confirmed`` spend option on
        the function — that copy IS the line (same window and amount), so the
        line gains ``event_id`` and the copy is merged away. Spend options that
        match no line (bookings made directly) become options of their event.
        Norm allows one booked option per event; the others are withdrawn as
        Norm's own confirmation would ("Another option was confirmed"), or
        dropped when there is no enquiry to hold them — each counted.
        """
        d, r = self.data, self.report
        lines_by_proposal = defaultdict(list)
        for line in d["contact_proposal_spend_lines"]:
            lines_by_proposal[line["proposal_id"]].append(line)
        approvals_by_line = defaultdict(list)
        for ev in d["min_spend_approval_events"]:
            approvals_by_line[ev.get("line_id")].append(ev)
        known_lines = {line["id"] for line in d["contact_proposal_spend_lines"]}
        for ev in d["min_spend_approval_events"]:
            if ev.get("line_id") not in known_lines:
                r.dropped("min_spend_approval_events", "its line was deleted in Orbit")
        line_rows: dict[str, dict] = {}
        line_proposal: dict[str, dict] = {}
        for p in sorted(
            d["contact_proposals"], key=lambda p: (p.get("created_at") or "", p["id"])
        ):
            lines = sorted(
                lines_by_proposal[p["id"]],
                key=lambda x: (x.get("created_at") or "", x["id"]),
            )
            enquiry = self.enquiries.get(p.get("enquiry_id"))
            if not enquiry:
                r.dropped("contact_proposals", "enquiry not imported")
                r.dropped(
                    "contact_proposal_spend_lines", "enquiry not imported", len(lines)
                )
                continue
            v = self.venue(p.get("venue_id"), "contact_proposals")
            if v is _SKIP or v is None:
                if v is None:
                    r.dropped("contact_proposals", "no venue")
                r.dropped(
                    "contact_proposal_spend_lines",
                    "proposal venue not imported",
                    len(lines),
                )
                continue
            r.imported("contact_proposals")
            if not lines:
                r.tally("min spends", "proposal with no lines (nothing to carry)")
            for n, line in enumerate(lines):
                start = line.get("start_at") or p.get("proposed_start")
                # Orbit's conversion copied ``end or proposed_end or proposed_start``.
                end = line.get("end_at") or p.get("proposed_end")
                ms = self._option_row(
                    key=("contact_proposal_spend_lines", line["id"]),
                    enquiry=enquiry,
                    event=None,
                    venue=v,
                    area=line.get("area_id") or p.get("area_id"),
                    label=line.get("label") or p.get("label"),
                    amount=line.get("amount"),
                    start=start,
                    end=end,
                    sort=n,
                    stamp=line,
                )
                ms["_window"] = (_ts(start), _ts(end or start))
                line_rows[line["id"]] = ms
                line_proposal[line["id"]] = p
                r.imported("contact_proposal_spend_lines")
                self._line_approvals(line, ms, approvals_by_line.get(line["id"], []))
        self.imported_lines = set(line_rows)

        # Event spend options, matched to the lines their booking came from.
        options_by_function = defaultdict(list)
        for o in d["venue_function_spend_options"]:
            options_by_function[o["function_id"]].append(o)
        converted = defaultdict(list)
        for line_id, p in line_proposal.items():
            if p.get("converted_function_id"):
                converted[p["converted_function_id"]].append(line_rows[line_id])
        booked: dict[str, dict] = {}
        for fid in sorted(set(options_by_function) | set(converted)):
            options = sorted(
                options_by_function.get(fid, []),
                key=lambda o: (o.get("created_at") or "", o["id"]),
            )
            event = self.events.get(fid)
            if event is None:
                r.dropped(
                    "venue_function_spend_options", "booking not imported", len(options)
                )
                continue
            free_lines = list(converted.get(fid, []))
            confirmed = [o for o in options if o.get("spend_status") == "confirmed"]
            others = [o for o in options if o.get("spend_status") != "confirmed"]
            unmatched = []
            for o in confirmed:
                line = self._match_line(o, free_lines)
                if line is None:
                    unmatched.append(o)
                    continue
                free_lines.remove(line)
                r.merged(
                    "venue_function_spend_options",
                    "copy of the converted proposal line",
                )
                self._book(booked, event, line, o)
            if len(unmatched) == 1 and len(free_lines) == 1:
                # One line, one booking option, different numbers: the same
                # minimum spend edited after booking — the booking's values win.
                o, line = unmatched.pop(), free_lines.pop()
                self._take_option_values(line, o, event)
                r.merged(
                    "venue_function_spend_options",
                    "converted line, edited after booking",
                )
                self._book(booked, event, line, o)
            for o in unmatched:
                ms = self._event_option(o, event, booked)
                if ms is None:
                    continue
                r.imported("venue_function_spend_options")
                r.tally(
                    "min spends",
                    _UNMATCHED_OPTION if fid in converted else _DIRECT_OPTION,
                )
            for line in free_lines:
                if event["id"] not in booked:
                    r.tally(
                        "min spends",
                        "converted proposal line with no copy on the booking (booked anyway)",
                    )
                    self._book(booked, event, line, None)
            if (
                others
                and event["id"] not in booked
                and len(others) == 1
                and not confirmed
            ):
                r.tally(
                    "min spends",
                    "booking's only option was approved, not confirmed (taken as booked)",
                )
                self._event_option(others[0], event, booked, force_book=True)
                r.imported("venue_function_spend_options")
                others = []
            for o in others:
                if event.get("enquiry_id"):
                    self._event_option(o, event, booked, as_enquiry_option=True)
                    r.imported("venue_function_spend_options")
                else:
                    r.dropped(
                        "venue_function_spend_options",
                        "unconfirmed option of a booking with no enquiry to hold it",
                    )
        # A booked enquiry can confirm nothing else (confirm_option refuses):
        # its other open options are withdrawn, as Norm's confirmation does.
        booked_enquiries = {
            e["enquiry_id"]: e
            for e in self.events.values()
            if e.get("enquiry_id") and not e.get("cancelled_at")
        }
        for ms in self.plan.rows[fm.FnMinSpend.__tablename__].values():
            event = booked_enquiries.get(ms["enquiry_id"])
            if ms["event_id"] or ms["withdrawn_at"] or event is None:
                continue
            ms["withdrawn_at"] = event["created_at"]
            if event["id"] in booked:
                ms["withdraw_reason"] = "Another option was confirmed"
                r.tally(
                    "min spends",
                    "other options of a booked enquiry withdrawn (as Norm's confirmation does)",
                )
            else:
                ms["withdraw_reason"] = "The enquiry was booked without this option"
                r.tally(
                    "min spends",
                    "options of an enquiry booked without a minimum spend withdrawn",
                )
        accepted = [
            p
            for p in d["contact_proposals"]
            if p.get("status") == "accepted" and not p.get("converted_function_id")
        ]
        for p in accepted:
            for line in lines_by_proposal[p["id"]]:
                ms = line_rows.get(line["id"])
                if ms is None or ms["event_id"]:
                    continue
                self._response(
                    ms,
                    ms["updated_at"],
                    "Accepted in Orbit; no booking was created there.",
                )
                r.tally(
                    "min spends",
                    "accepted in Orbit but never booked (customer confirm recorded, no event)",
                )

    def _option_row(
        self,
        *,
        key,
        enquiry,
        event,
        venue,
        area,
        label,
        amount,
        start,
        end,
        sort,
        stamp,
        withdrawn=None,
        reason=None,
    ) -> dict:
        r = self.report
        area_id = None
        if area:
            found = self.areas.get(area)
            if found and found[1] == venue:
                area_id = found[0]
            else:
                r.tally("areas", "option area not at the option's venue (left empty)")
        amount_cents = self.cents(amount, "minimum spend")
        if amount_cents is None or amount_cents < 0:
            r.tally("money", "minimum spend missing or negative (0 used)")
            amount_cents = 0
        date, s, e, nd = self.window(start, end, self.tz(venue), "minimum spend")
        return self.plan.add(
            fm.FnMinSpend,
            self.stamp(
                {
                    "id": self.id(*key),
                    "enquiry_id": enquiry["id"] if enquiry else None,
                    "event_id": event["id"] if event else None,
                    "venue_id": venue,
                    "area_id": area_id,
                    "label": _clean(label),
                    "amount_cents": amount_cents,
                    "event_date": date,
                    "start_time": s,
                    "end_time": e,
                    "ends_next_day": nd,
                    "sort_order": sort,
                    "withdrawn_at": withdrawn,
                    "withdraw_reason": reason,
                    "created_by_label": ACTOR,
                },
                stamp.get("created_at"),
                stamp.get("updated_at"),
            ),
        )

    @staticmethod
    def _match_line(option: dict, lines: list[dict]) -> dict | None:
        """The converted line a booking's spend option was copied from: same
        window and amount, else the same amount (a window edited later)."""
        cents = to_cents(option.get("amount"))
        window = (
            _ts(option.get("start_at")),
            _ts(option.get("end_at") or option.get("start_at")),
        )
        same_amount = [ln for ln in lines if ln["amount_cents"] == cents]
        exact = [ln for ln in same_amount if ln.get("_window") == window]
        return (exact or same_amount or [None])[0]

    def _take_option_values(self, ms: dict, option: dict, event: dict) -> None:
        cents = to_cents(option.get("amount"))
        if cents is not None and cents >= 0:
            ms["amount_cents"] = cents
        date, s, e, nd = self.window(
            option.get("start_at"),
            option.get("end_at"),
            self.tz(event["venue_id"]),
            "minimum spend",
        )
        if date is not None:
            ms.update(event_date=date, start_time=s, end_time=e, ends_next_day=nd)
        found = self.areas.get(option.get("area_id")) if option.get("area_id") else None
        if found and found[1] == ms["venue_id"]:
            ms["area_id"] = found[0]
        self.report.tally(
            "min spends",
            "converted line edited after booking (booking's amount/window kept)",
        )

    def _book(self, booked: dict, event: dict, ms: dict, option: dict | None) -> None:
        if event["id"] in booked:
            ms["withdrawn_at"] = event["created_at"]
            ms["withdraw_reason"] = (
                "A second option was confirmed on this booking in Orbit"
            )
            self.report.tally(
                "min spends", "second confirmed option on one booking (withdrawn)"
            )
            return
        ms["event_id"] = event["id"]
        booked[event["id"]] = ms
        self._response(ms, event["created_at"], "Booked in Orbit.")

    def _event_option(
        self,
        o: dict,
        event: dict,
        booked: dict,
        *,
        force_book=False,
        as_enquiry_option=False,
    ) -> dict | None:
        enquiry = (
            self.enquiries_by_id.get(event.get("enquiry_id"))
            if event.get("enquiry_id")
            else None
        )
        to_event = (force_book or not as_enquiry_option) and event["id"] not in booked
        if not to_event and not enquiry:
            self.report.dropped(
                "venue_function_spend_options",
                "second confirmed option of a booking with no enquiry",
            )
            return None
        ms = self._option_row(
            key=("venue_function_spend_options", o["id"]),
            enquiry=enquiry,
            event=None,
            venue=event["venue_id"],
            area=o.get("area_id"),
            label=o.get("label"),
            amount=o.get("amount"),
            start=o.get("start_at"),
            end=o.get("end_at"),
            sort=0,
            stamp=o,
        )
        if o.get("approved"):
            user, label = self.person(o.get("approved_by"))
            self._approval(
                ms,
                "approve",
                _ts(o.get("approved_at")) or ms["created_at"],
                user,
                label or (None if user else ACTOR),
                None,
                ms["amount_cents"],
                f"option:{o['id']}",
            )
        if to_event:
            self._book(booked, event, ms, o)
        elif event["id"] in booked:
            ms["withdrawn_at"] = event["created_at"]
            ms["withdraw_reason"] = "Another option was confirmed"
        return ms

    @property
    def enquiries_by_id(self) -> dict:
        if len(getattr(self, "_by_norm_id", {})) != len(self.enquiries):
            self._by_norm_id = {row["id"]: row for row in self.enquiries.values()}
        return self._by_norm_id

    def _line_approvals(self, line: dict, ms: dict, events: list) -> None:
        """Orbit's approval log for the line, made to agree with the line's
        current status (the log only began on 7 Oct 2026)."""
        r = self.report
        last_kind, last_at = None, None
        for ev in sorted(events, key=lambda e: (e.get("created_at") or "", e["id"])):
            if ev.get("action") == "approve":
                kind = (
                    "override" if ev.get("approval_type") == "override" else "approve"
                )
            elif ev.get("action") == "unapprove":
                kind = "revoke"
            else:
                r.dropped("min_spend_approval_events", f"action '{ev.get('action')}'")
                continue
            user, label = self.person(ev.get("member_id"), ev.get("member_name"))
            reason = _clean(ev.get("note"))
            if kind == "override" and not reason:
                reason = "Override recorded in Orbit (no note)"
            at = _ts(ev.get("created_at"))
            self._approval(
                ms,
                kind,
                at,
                user,
                label,
                reason,
                self.cents(ev.get("amount"), "approved amount")
                if ev.get("amount") is not None
                else ms["amount_cents"],
                f"event:{ev['id']}",
            )
            r.imported("min_spend_approval_events")
            last_kind, last_at = kind, at
        approved = line.get("status") == "approved"
        if approved and last_kind not in ("approve", "override"):
            kind = "override" if line.get("approval_type") == "override" else "approve"
            at = _ts(line.get("approved_at")) or _ts(line.get("updated_at"))
            if last_at and at <= last_at:
                at = last_at + dt.timedelta(seconds=1)
            user, label = self.person(line.get("approved_by"))
            reason = _clean(line.get("approval_note"))
            if kind == "override" and not reason:
                reason = "Override recorded in Orbit (no note)"
            self._approval(
                ms,
                kind,
                at,
                user,
                label or (None if user else ACTOR),
                reason,
                ms["amount_cents"],
                f"line:{line['id']}",
            )
        elif not approved and last_kind in ("approve", "override"):
            at = max(
                _ts(line.get("updated_at")) or last_at,
                last_at + dt.timedelta(seconds=1),
            )
            self._approval(
                ms,
                "revoke",
                at,
                None,
                ACTOR,
                "Unapproved in Orbit (no log entry)",
                ms["amount_cents"],
                f"line-revoke:{line['id']}",
            )

    def _approval(self, ms, kind, at, user, label, reason, amount, key) -> None:
        self.plan.add(
            fm.FnMinSpendApproval,
            {
                "id": self.id("min_spend.approval", key),
                "min_spend_id": ms["id"],
                "kind": kind,
                "actor_user_id": user,
                "actor_label": label,
                "reason": reason,
                "amount_cents": amount,
                "automatic": False,
                "occurred_at": at,
                "created_at": at,
                "updated_at": at,
            },
        )
        self.report.tally("facts", f"min spend {kind}")

    def _response(self, ms: dict, at, note: str) -> None:
        self.plan.add(
            fm.FnMinSpendResponse,
            {
                "id": self.id("min_spend.response", ms["id"]),
                "min_spend_id": ms["id"],
                "kind": "confirm",
                "channel": "import",
                "note": note,
                "occurred_at": at,
                "created_at": at,
                "updated_at": at,
            },
        )
        self.report.tally("facts", "min spend customer confirm (channel import)")

    # ---- menus -------------------------------------------------------------------

    def menus(self) -> None:
        """Pre-order menus: one record each. An event menu that is the copy
        Orbit made from the enquiry's menu at booking is THE enquiry menu,
        linked to both (the event copy carries the later edits). Events with
        no menu rows carry their food only in ``food_preorders`` — Orbit's own
        fallback — which becomes an a-la-carte menu; where menus exist it is a
        copy and is dropped."""
        d, r = self.data, self.report
        fn_by_id = {f["id"]: f for f in d["venue_functions"]}
        won = {
            e["won_function_id"]: e["id"]
            for e in d["enquiries"]
            if e.get("won_function_id")
        }
        enquiry_menus = defaultdict(list)
        for m in sorted(
            d["enquiry_preorder_menus"],
            key=lambda m: (m.get("created_at") or "", m["id"]),
        ):
            enquiry_menus[(m["enquiry_id"], m.get("venue_id"))].append(m)
        merged_into: dict[str, dict] = {}
        event_menu_fns: Counter = Counter()
        for m in sorted(
            d["venue_function_preorder_menus"],
            key=lambda m: (m.get("created_at") or "", m["id"]),
        ):
            event = self.events.get(m["function_id"])
            if event is None:
                r.dropped("venue_function_preorder_menus", "booking not imported")
                continue
            event_menu_fns[m["function_id"]] += 1
            f = fn_by_id[m["function_id"]]
            eid = f.get("enquiry_id") or won.get(f["id"])
            source = None
            if event.get("enquiry_id") and eid:
                for cand in enquiry_menus.get((eid, f.get("venue_id")), []):
                    if cand["id"] in merged_into:
                        continue
                    if (
                        cand.get("preorder_template_id"),
                        cand.get("template_name"),
                    ) == (m.get("preorder_template_id"), m.get("template_name")):
                        source = cand
                        merged_into[cand["id"]] = m
                        break
            menu = self._menu_row(
                ("venue_function_preorder_menus", m["id"]),
                m,
                enquiry_id=event["enquiry_id"] if source else None,
                event_id=event["id"],
                venue_id=event["venue_id"],
                sort=event_menu_fns[m["function_id"]] - 1,
            )
            r.imported("venue_function_preorder_menus")
            if source:
                r.merged(
                    "enquiry_preorder_menus",
                    "the booking's copy of it carries the same menu",
                )
            self._menu_items(
                menu, m.get("items"), m["id"], is_set=bool(m.get("is_set_menu"))
            )
        for (eid, ov), rows in enquiry_menus.items():
            for n, m in enumerate(rows):
                if m["id"] in merged_into:
                    continue
                enquiry = self.enquiries.get(eid)
                if enquiry is None:
                    r.dropped("enquiry_preorder_menus", "enquiry not imported")
                    continue
                v = self.venue(ov, "enquiry_preorder_menus")
                if v is _SKIP or v is None:
                    if v is None:
                        r.dropped("enquiry_preorder_menus", "no venue")
                    continue
                menu = self._menu_row(
                    ("enquiry_preorder_menus", m["id"]),
                    m,
                    enquiry_id=enquiry["id"],
                    event_id=None,
                    venue_id=v,
                    sort=n,
                )
                r.imported("enquiry_preorder_menus")
                self._menu_items(
                    menu, m.get("items"), m["id"], is_set=bool(m.get("is_set_menu"))
                )
        for f in d["venue_functions"]:
            items = [i for i in (f.get("food_preorders") or []) if isinstance(i, dict)]
            if not items:
                continue
            if f["id"] in event_menu_fns:
                r.tally(
                    "menus", "food_preorders copy dropped (the booking has menu rows)"
                )
                continue
            event = self.events.get(f["id"])
            if event is None:
                continue
            menu = self._menu_row(
                ("venue_functions.food_preorders", f["id"]),
                {
                    "template_name": "Food pre-order",
                    "is_set_menu": False,
                    "guest_count": f.get("guest_count") or 0,
                    "created_at": f.get("created_at"),
                    "updated_at": f.get("updated_at"),
                },
                enquiry_id=None,
                event_id=event["id"],
                venue_id=event["venue_id"],
                sort=0,
            )
            r.tally(
                "menus",
                "food_preorders became the booking's a-la-carte menu (no menu rows)",
            )
            self._menu_items(
                menu, items, f"fp:{f['id']}", is_set=False, categories=True
            )

    def _menu_row(self, key, m, *, enquiry_id, event_id, venue_id, sort) -> dict:
        is_set = bool(m.get("is_set_menu"))
        template = (
            self.templates.get(m.get("preorder_template_id"))
            if m.get("preorder_template_id")
            else None
        )
        if m.get("preorder_template_id") and template is None:
            self.report.tally(
                "menus", "menu's template not imported (menu kept, unlinked)"
            )
        guests = m.get("guest_count") or 0
        return self.plan.add(
            fm.FnPreorderMenu,
            self.stamp(
                {
                    "id": self.id(*key),
                    "enquiry_id": enquiry_id,
                    "event_id": event_id,
                    "venue_id": venue_id,
                    "template_id": template["id"] if template else None,
                    "name": _clean(m.get("template_name")) or "Menu",
                    "is_set_menu": is_set,
                    "price_per_person_cents": self.cents(
                        m.get("price_per_person"), "set-menu price"
                    )
                    if is_set
                    else None,
                    "guest_count": max(int(guests), 0),
                    "sort_order": sort,
                },
                m.get("created_at"),
                m.get("updated_at") or m.get("created_at"),
            ),
        )

    def _menu_items(
        self, menu: dict, items, key: str, *, is_set: bool, categories=False
    ) -> None:
        r = self.report
        orbit_total, rows = 0, []
        category = None
        n = 0
        for item in items or []:
            if not isinstance(item, dict):
                continue
            kind = "header" if item.get("item_type") == "header" else "item"
            if (
                categories
                and kind == "item"
                and _clean(item.get("category"))
                and item.get("category") != category
            ):
                category = item.get("category")
                rows.append(
                    self._item_row(
                        menu,
                        f"{key}:{n}",
                        n,
                        {"kind": "header", "name": category.strip()},
                    )
                )
                n += 1
            qty_raw = item.get("quantity") or 0
            try:
                qty = float(qty_raw)
            except (TypeError, ValueError):
                qty = 0
            if qty != int(qty) or qty < 0:
                r.tally("menus", "fractional/negative quantity rounded")
            price = None
            if kind == "item":
                if is_set:
                    if float(item.get("unit_price") or 0) > 0:
                        r.tally(
                            "menus",
                            "set-menu dish price dropped (set menus are priced per person)",
                        )
                else:
                    price = self.cents(item.get("unit_price"), "menu dish price")
                    orbit_total += to_cents(item.get("total")) or 0
            dietaries = (
                item.get("dietaries") if isinstance(item.get("dietaries"), list) else []
            )
            if not dietaries and _clean(item.get("dietary")):
                dietaries = [
                    x.strip() for x in str(item["dietary"]).split(",") if x.strip()
                ]
            name = (
                _clean(item.get("item"))
                or _clean(item.get("description"))
                or ("Section" if kind == "header" else "Item")
            )
            description = _clean(item.get("description"))
            rows.append(
                self._item_row(
                    menu,
                    f"{key}:{n}",
                    n,
                    {
                        "kind": kind,
                        "name": name,
                        "description": description
                        if description and description != name
                        else None,
                        "quantity": max(int(round(qty)), 0) if kind == "item" else 0,
                        "unit_price_cents": price,
                        "default_percent": _percent(item.get("default_percentage")),
                        "dietaries": [str(x) for x in dietaries],
                        "loaded_recipe_id": _clean(item.get("recipe_id")),
                        "loaded_stock_item_id": _clean(item.get("stock_item_id")),
                    },
                )
            )
            n += 1
        if not is_set and money.menu_food_cents(menu, rows) != orbit_total:
            r.tally(
                "menus",
                "a-la-carte menu whose Orbit line totals ≠ quantity × price (Norm uses quantity × price)",
            )

    def _item_row(self, menu, key, n, fields) -> dict:
        return self.plan.add(
            fm.FnPreorderMenuItem,
            {
                "id": self.id("preorder_menu_item", key),
                "menu_id": menu["id"],
                "sort_order": n,
                "created_at": menu["created_at"],
                "updated_at": menu["updated_at"],
                **fields,
            },
        )

    # ---- proposals -----------------------------------------------------------------

    def proposals(self) -> None:
        d, r = self.data, self.report
        docs = {doc["enquiry_id"]: doc for doc in d["enquiry_quote_documents"]}
        versions = defaultdict(list)
        for v in d["enquiry_quote_versions"]:
            versions[v["enquiry_id"]].append(v)
        self.proposal_rows: list[dict] = []
        self.version_rows: list[dict] = []
        token_version: dict[str, str] = {}
        for eid in sorted(set(docs) | set(versions)):
            enquiry = self.enquiries.get(eid)
            doc = docs.get(eid)
            vs = sorted(
                versions.get(eid, []),
                key=lambda v: (v.get("version_number") or 0, v["id"]),
            )
            if enquiry is None:
                if doc:
                    r.dropped("enquiry_quote_documents", "enquiry not imported")
                if vs:
                    r.dropped("enquiry_quote_versions", "enquiry not imported", len(vs))
                continue
            base = doc or vs[-1]
            user, label = self.person((doc or {}).get("updated_by_member_id"))
            row = self.plan.add(
                fm.FnProposal,
                self.stamp(
                    {
                        "id": self.id("enquiry_quote_documents", doc["id"])
                        if doc
                        else self.id("enquiry_quote_versions.proposal", eid),
                        "enquiry_id": enquiry["id"],
                        "html": base.get("body_html") or "",
                        "css": base.get("custom_css") or "",
                        "based_on_brand_version": None,
                        "updated_by_user_id": user,
                        "updated_by_label": label,
                    },
                    base.get("created_at"),
                    base.get("updated_at") or base.get("created_at"),
                ),
            )
            self.proposal_rows.append(row)
            self._want_html(row, "proposal")
            if doc:
                r.imported("enquiry_quote_documents")
                if _clean(doc.get("email_subject")) or _clean(doc.get("email_body")):
                    r.tally(
                        "proposals",
                        "covering-email draft on the proposal not carried (no drafts imported)",
                    )
            for v in vs:
                user, label = self.person(v.get("sent_by_member_id"))
                vrow = self.plan.add(
                    fm.FnProposalVersion,
                    self.stamp(
                        {
                            "id": self.id("enquiry_quote_versions", v["id"]),
                            "proposal_id": row["id"],
                            "enquiry_id": enquiry["id"],
                            "version_number": int(v.get("version_number") or 1),
                            "html": v.get("body_html") or "",
                            "css": v.get("custom_css") or "",
                            "options": [],
                            "sent_at": _ts(v.get("created_at")),
                            "sent_by_user_id": user,
                            "sent_by_label": label,
                        },
                        v.get("created_at"),
                        v.get("created_at"),
                    ),
                )
                self.version_rows.append(vrow)
                self._want_html(vrow, "sent proposal version")
                if v.get("quote_token"):
                    token_version[v["quote_token"]] = vrow["id"]
                r.imported("enquiry_quote_versions")
                r.tally(
                    "proposals",
                    "sent version imported without its option list (Orbit kept only a total)",
                )

        kinds = {"sent": "sent", "marked_sent": "sent", "viewed": "viewed"}
        has_sent = set()
        for qe in sorted(
            d["enquiry_quote_events"],
            key=lambda q: (q.get("created_at") or "", q["id"]),
        ):
            enquiry = self.enquiries.get(qe["enquiry_id"])
            kind = kinds.get(qe.get("kind"))
            if enquiry is None:
                r.dropped("enquiry_quote_events", "enquiry not imported")
                continue
            if kind is None:
                r.dropped(
                    "enquiry_quote_events", f"'{qe.get('kind')}' (not a send or a view)"
                )
                continue
            user, label = self.person(qe.get("member_id"))
            account = self.accounts.get(
                self.thread_account.get(qe.get("gmail_thread_id") or ""), {}
            )
            at = _ts(qe.get("created_at"))
            self.plan.add(
                fm.FnProposalEvent,
                {
                    "id": self.id("enquiry_quote_events", qe["id"]),
                    "enquiry_id": enquiry["id"],
                    "proposal_version_id": token_version.get(qe.get("token") or ""),
                    "kind": kind,
                    "source": "import",
                    "account_id": account.get("id"),
                    "gmail_message_id": _clean(qe.get("gmail_message_id")),
                    "actor_user_id": user,
                    "actor_label": label or (None if user else ACTOR),
                    "occurred_at": at,
                    "created_at": at,
                    "updated_at": at,
                },
            )
            r.imported("enquiry_quote_events")
            if kind == "sent":
                has_sent.add(qe["enquiry_id"])
        # Orbit's own "quoted" evidence: proposal_sent_at (legacy quotes too).
        for e in d["enquiries"]:
            enquiry = self.enquiries.get(e["id"])
            if enquiry is None or not e.get("proposal_sent_at") or e["id"] in has_sent:
                continue
            at = _ts(e["proposal_sent_at"])
            self.plan.add(
                fm.FnProposalEvent,
                {
                    "id": self.id("enquiries.proposal_sent", e["id"]),
                    "enquiry_id": enquiry["id"],
                    "kind": "sent",
                    "source": "import",
                    "actor_label": ACTOR,
                    "occurred_at": at,
                    "created_at": at,
                    "updated_at": at,
                },
            )
            r.tally(
                "proposals", "quote sent per Orbit's proposal_sent_at (no send logged)"
            )

    def _want_html(self, row: dict, purpose: str) -> None:
        for url in _storage_urls(row["html"], row["css"]):
            self._want(url, purpose)
        external = re.findall(
            r"https?://(?![a-z0-9-]+\.supabase\.co/storage)([^/\s\"'<>)]+)",
            row["html"] + row["css"],
        )
        for host in external:
            self.report.tally(
                "proposals",
                f"external reference in Orbit's HTML ({host.split(':')[0]})",
            )

    # ---- web forms -------------------------------------------------------------------

    def forms(self) -> None:
        d, r = self.data, self.report
        fields_by_form = defaultdict(list)
        for x in d["web_form_fields"]:
            fields_by_form[x["form_id"]].append(x)
        for wf in sorted(
            d["web_forms"], key=lambda w: (w.get("created_at") or "", w["id"])
        ):
            v = self.venue(wf.get("venue_id"), "web_forms")
            if v is _SKIP or v is None:
                r.dropped(
                    "web_form_fields",
                    "form not imported",
                    len(fields_by_form[wf["id"]]),
                )
                continue
            if wf["slug"] in self.keep.foreign_slugs:
                r.dropped(
                    "web_forms", "slug already used by another organisation in Norm"
                )
                r.dropped(
                    "web_form_fields",
                    "form not imported",
                    len(fields_by_form[wf["id"]]),
                )
                continue
            theme = self._form_theme(wf)
            row = self.plan.add(
                fm.FnWebForm,
                self.stamp(
                    {
                        "id": self.id("web_forms", wf["id"]),
                        "venue_id": v,
                        "slug": wf["slug"],
                        "name": wf.get("name") or wf["slug"],
                        "active": bool(wf.get("is_active", True)),
                        "theme": theme,
                        "intro_html": _clean(wf.get("intro_html")),
                        "submit_label": _clean(wf.get("submit_button_label"))
                        or "Send enquiry",
                        "success_message": _clean(wf.get("success_message")),
                        "recipient_email": _clean(wf.get("recipient_email")),
                        "subject_template": _clean(wf.get("subject_template")),
                        "reply_to_field_key": _clean(wf.get("reply_to_field_key")),
                        "honeypot_field": _clean(wf.get("honeypot_field_name"))
                        or "website",
                    },
                    wf.get("created_at"),
                    wf.get("updated_at"),
                ),
            )
            self.form_ids[wf["id"]] = row["id"]
            r.imported("web_forms")
            self._form_fields(row, fields_by_form[wf["id"]])

        leads = {
            a["submission_id"]: a
            for a in d["lead_attributions"]
            if a.get("submission_id")
        }
        statuses = set(fm.FORM_DELIVERY_STATUSES)
        for s in sorted(
            d["web_form_submissions"],
            key=lambda s: (s.get("submitted_at") or "", s["id"]),
        ):
            form = self.form_ids.get(s["form_id"])
            v = self.venue(s.get("venue_id"), "web_form_submissions")
            if v is _SKIP:
                continue
            if form is None or v is None:
                r.dropped("web_form_submissions", "form not imported")
                continue
            lead = leads.get(s["id"]) or {}
            enquiry = (
                self.enquiries.get(lead.get("enquiry_id"))
                if lead.get("enquiry_id")
                else None
            )
            status = (
                s.get("delivery_status")
                if s.get("delivery_status") in statuses
                else "skipped"
            )
            if status != s.get("delivery_status"):
                r.tally(
                    "forms", f"delivery status '{s.get('delivery_status')}' → skipped"
                )
            self.plan.add(
                fm.FnFormSubmission,
                self.stamp(
                    {
                        "id": self.id("web_form_submissions", s["id"]),
                        "form_id": form,
                        "venue_id": v,
                        "payload": s.get("payload") or {},
                        "submitted_at": _ts(s.get("submitted_at"))
                        or _ts(s.get("created_at")),
                        "utm_source": _clean(lead.get("utm_source")),
                        "utm_medium": _clean(lead.get("utm_medium")),
                        "utm_campaign": _clean(lead.get("utm_campaign")),
                        "utm_term": _clean(lead.get("utm_term")),
                        "utm_content": _clean(lead.get("utm_content")),
                        "gclid": _clean(lead.get("gclid")),
                        "gbraid": _clean(lead.get("gbraid")),
                        "wbraid": _clean(lead.get("wbraid")),
                        "referrer": _clean(lead.get("referrer")),
                        "landing_page": _clean(lead.get("landing_page")),
                        "ref_code": _clean(lead.get("ref_code")),
                        "ip_hash": _clean(s.get("ip_hash")),
                        "user_agent": _clean(s.get("user_agent")),
                        "delivery_status": status,
                        "delivery_error": _clean(s.get("delivery_error")),
                        "delivery_message_id": _clean(s.get("message_id")),
                        "enquiry_id": enquiry["id"] if enquiry else None,
                    },
                    s.get("created_at"),
                    s.get("created_at"),
                ),
            )
            r.imported("web_form_submissions")
            if lead:
                r.imported("lead_attributions")
        for a in d["lead_attributions"]:
            if not a.get("submission_id"):
                if a.get("enquiry_id") in self.enquiries:
                    r.merged("lead_attributions", "kept on the enquiry (no submission)")
                else:
                    r.dropped(
                        "lead_attributions", "no submission and no imported enquiry"
                    )
            elif (
                self.plan.rows[fm.FnFormSubmission.__tablename__].get(
                    self.id("web_form_submissions", a["submission_id"])
                )
                is None
            ):
                r.dropped("lead_attributions", "submission not imported")

    def _form_fields(self, form: dict, fields: list) -> None:
        """Orbit's fields as F5's field set: every Orbit type has a Norm type,
        ``maps_to`` follows the field TYPE (a multi-date answer can only fill
        ``preferred_dates``), options take the shape F5's builder saves, and a
        blank label gets a readable one (F5 requires a label)."""
        r = self.report
        used_maps: set[str] = set()
        keys: set[str] = set()
        for x in sorted(fields, key=lambda x: (x.get("sort_order") or 0, x["id"])):
            kind = ORBIT_FIELD_TYPES.get(x.get("type"))
            if kind is None:
                r.tally("forms", f"field type '{x.get('type')}' unknown → text")
                kind = "text"
            key = _field_key(x.get("key"))
            if key != x.get("key"):
                r.tally(
                    "forms",
                    "field key made valid for Norm (lowercase, letters/digits/_)",
                )
            if not key or key in keys:
                r.dropped("web_form_fields", "duplicate or empty key on the form")
                continue
            keys.add(key)
            maps_to = _field_maps_to(kind, key)
            if maps_to in used_maps:
                maps_to = None  # the first field of the form fills it
            if maps_to:
                used_maps.add(maps_to)
            label = _clean(x.get("label"))
            if not label:
                label = _default_label(kind, key)
                r.tally(
                    "forms",
                    f"blank {'section ' if kind == 'section' else ''}label → '{label}'",
                )
            options = self._field_options(kind, x)
            if x.get("validation") and kind != "number":
                r.tally("forms", "field validation rules not carried")
            self.plan.add(
                fm.FnWebFormField,
                self.stamp(
                    {
                        "id": self.id("web_form_fields", x["id"]),
                        "form_id": form["id"],
                        "key": key,
                        "label": label[:200],
                        "type": kind,
                        "required": bool(x.get("is_required")) and kind != "section",
                        "options": options,
                        "placeholder": (_clean(x.get("placeholder")) or "")[:200]
                        or None,
                        "help_text": (_clean(x.get("help_text")) or "")[:500] or None,
                        "maps_to": maps_to,
                        "sort_order": int(x.get("sort_order") or 0),
                    },
                    x.get("created_at"),
                    x.get("updated_at"),
                ),
            )
            r.imported("web_form_fields")
            r.tally("form field types", f"{x.get('type')} → {kind}")

    def _field_options(self, kind: str, x: dict):
        """``[{value, label}]`` for choice/checkbox, ``{min, max}`` for a
        number (from Orbit's validation), nothing otherwise — what F5's
        builder accepts, so an imported form can be re-saved as it is."""
        raw = x.get("options")
        if kind == "number":
            limits = (
                x.get("validation") if isinstance(x.get("validation"), dict) else {}
            )
            out = {}
            for k in ("min", "max"):
                try:
                    if limits.get(k) is not None:
                        out[k] = float(limits[k])
                except (TypeError, ValueError):
                    pass
            return out or None
        if kind not in ("choice", "checkbox"):
            if raw:
                self.report.tally("forms", f"options on a {kind} field dropped")
            return None
        out = []
        for o in raw if isinstance(raw, list) else []:
            if isinstance(o, dict):
                label = str(o.get("label") or o.get("value") or "").strip()
                value = str(o.get("value") or label).strip()
            else:
                label = value = str(o).strip()
            if label:
                out.append({"value": value[:200], "label": label[:200]})
        if len(out) > FORM_MAX_OPTIONS:
            self.report.tally("forms", f"choices beyond {FORM_MAX_OPTIONS} dropped")
            out = out[:FORM_MAX_OPTIONS]
        if kind == "choice" and not out:
            self.report.tally(
                "forms", "choice field with no choices (F5's builder will ask for some)"
            )
        return out or None

    def _form_theme(self, wf: dict) -> dict:
        """Orbit's style profile as Norm theme tokens: camelCase → snake_case
        (forms.js's own rule), only the keys forms.js applies, only plain
        values F5 accepts (≤ 200 chars, no markup/braces/url()) — so the staff
        builder can re-save an imported theme unchanged. What is dropped is
        counted."""
        r = self.report
        raw = {"brand_color": wf.get("brand_color"), **(wf.get("style") or {})}
        theme: dict[str, str] = {}
        for key, value in raw.items():
            if value is None or value == "":
                continue
            name = _snake(key)
            if name not in FORM_THEME_KEYS:
                r.tally(
                    "form themes",
                    f"'{key}' dropped (not a theme setting forms.js applies)",
                )
                continue
            text_value = (
                "" if isinstance(value, (bool, dict, list)) else str(value).strip()
            )
            if (
                not text_value
                or len(text_value) > 200
                or re.search(r"[<>{};\\]", text_value)
                or "url(" in text_value.lower()
            ):
                r.tally("form themes", f"'{key}' dropped (not a plain value)")
                continue
            theme[name] = text_value
        return theme

    # ---- mail ------------------------------------------------------------------------

    def mailboxes(self) -> None:
        """Placeholder accounts for Orbit's venue mailboxes. A mailbox already
        CONNECTED in Norm keeps its row (its sync owns it and its mirror); its
        thread links and folders point at that row instead. F4's connect flow
        attaches a connection to a placeholder by (org, google_email)."""
        d, r = self.data, self.report
        for a in sorted(
            d["venue_gmail_accounts"],
            key=lambda a: (a.get("created_at") or "", a["id"]),
        ):
            email = _email(a.get("google_email"))
            v = self.venue(a.get("venue_id"), "venue_gmail_accounts")
            if v is _SKIP or v is None or not email:
                if email is None:
                    r.dropped("venue_gmail_accounts", "no address")
                continue
            connected = self.keep.connected_accounts.get(email)
            if connected is not None:
                self.accounts[a["id"]] = {
                    "id": connected.id,
                    "placeholder": False,
                    "venue": connected.venue_id,
                }
                r.merged(
                    "venue_gmail_accounts", "already connected in Norm (its row kept)"
                )
                if connected.venue_id != v:
                    r.tally(
                        "mail",
                        "connected Norm mailbox is at a different venue than in Orbit",
                    )
                continue
            row = self.plan.add(
                fm.FnMailboxAccount,
                self.stamp(
                    {
                        "id": self.id("venue_gmail_accounts", a["id"]),
                        "venue_id": v,
                        "connection_id": None,
                        "google_email": email,
                        "display_name": _clean(a.get("display_name")),
                        "status": "needs_reconnect",
                        "signature_html": _clean(a.get("signature_html")),
                    },
                    a.get("created_at"),
                    a.get("updated_at"),
                ),
            )
            self.accounts[a["id"]] = {"id": row["id"], "placeholder": True, "venue": v}
            r.imported("venue_gmail_accounts")
        # Which mailbox each linked thread lives in (Orbit linked threads by id alone).
        found = defaultdict(set)
        for t in d["gmail_threads"]:
            found[t["thread_id"]].add(t["account_id"])
        enquiry_venue = {e["id"]: e.get("venue_id") for e in d["enquiries"]}
        orbit_venue_account = {
            a["venue_id"]: a["id"] for a in d["venue_gmail_accounts"]
        }
        orbit_accounts = {a["id"] for a in d["venue_gmail_accounts"]}
        for link in d["enquiry_gmail_threads"]:
            tid = link["thread_id"]
            if tid in self.thread_account:
                continue
            accounts = found.get(tid, set())
            if len(accounts) == 1:
                self.thread_account[tid] = next(iter(accounts))
            elif len(accounts) > 1:
                pick = (
                    link.get("attached_by_account_id")
                    if link.get("attached_by_account_id") in accounts
                    else (
                        orbit_venue_account.get(enquiry_venue.get(link["enquiry_id"]))
                    )
                )
                if pick in accounts:
                    self.thread_account[tid] = pick
                else:
                    self.thread_account[tid] = sorted(accounts)[0]
                    r.tally("mail", "thread in several mailboxes (first by id used)")
            elif link.get("attached_by_account_id") in orbit_accounts:
                self.thread_account[tid] = link["attached_by_account_id"]
                r.tally(
                    "mail",
                    "linked thread not mirrored in Orbit (mailbox from the link)",
                )

    def mail_rows(self) -> None:
        d, r = self.data, self.report
        # Thread links: always reloaded — staff link threads in Orbit until cut-over.
        live = set()
        for link in sorted(
            d["enquiry_gmail_threads"],
            key=lambda x: (x.get("linked_at") or "", x["id"]),
        ):
            enquiry = self.enquiries.get(link["enquiry_id"])
            account = self.accounts.get(self.thread_account.get(link["thread_id"]))
            if enquiry is None:
                r.dropped("enquiry_gmail_threads", "enquiry not imported")
                continue
            if account is None:
                r.dropped(
                    "enquiry_gmail_threads", "mailbox unknown (not a venue mailbox)"
                )
                continue
            key = (account["id"], link["thread_id"], enquiry["id"])
            unlinked = _ts(link.get("unlinked_at"))
            if not unlinked and key in live:
                r.dropped("enquiry_gmail_threads", "duplicate live link")
                continue
            if not unlinked:
                live.add(key)
            user, label = self.person(
                link.get("linked_by_member_id") or link.get("attached_by_member_id")
            )
            linked_at = _ts(link.get("linked_at"))
            self.plan.add(
                fm.FnThreadLink,
                {
                    "id": self.id("enquiry_gmail_threads", link["id"]),
                    "account_id": account["id"],
                    "gmail_thread_id": link["thread_id"],
                    "enquiry_id": enquiry["id"],
                    "linked_by_user_id": user,
                    "linked_by_label": label or (None if user else ACTOR),
                    "linked_at": linked_at,
                    "unlinked_at": unlinked,
                    "created_at": linked_at,
                    "updated_at": unlinked or linked_at,
                },
            )
            r.imported("enquiry_gmail_threads")

        placeholders = {oid: a for oid, a in self.accounts.items() if a["placeholder"]}
        connected = {oid for oid, a in self.accounts.items() if not a["placeholder"]}
        for row in d["gmail_mailbox_labels"]:
            account = placeholders.get(row["account_id"])
            if account is None:
                r.dropped(
                    "gmail_mailbox_labels",
                    "mailbox connected in Norm (its sync owns labels)"
                    if row["account_id"] in connected
                    else "mailbox not imported",
                )
                continue
            folder = {
                "booking": "enquiry",
                "event": "reservation",
                "uncategorised": "other",
            }.get(row.get("actioned_kind") or "")
            stamp = row.get("updated_at")
            self.plan.add(
                fm.FnMailboxLabel,
                self.stamp(
                    {
                        "id": self.id(
                            "gmail_mailbox_labels",
                            f"{row['account_id']}:{row['label_id']}",
                        ),
                        "account_id": account["id"],
                        "gmail_label_id": row["label_id"],
                        "name": row.get("label_name") or row["label_id"],
                        "label_type": "user"
                        if str(row["label_id"]).startswith("Label_")
                        else "system",
                        "folder": folder,
                    },
                    stamp,
                    stamp,
                ),
            )
            r.imported("gmail_mailbox_labels")

        # Message metadata of LINKED threads in placeholder mailboxes: the
        # facts "replied" is derived from, before any mailbox is connected.
        messages = defaultdict(list)
        for m in d["gmail_messages"]:
            messages[(m["account_id"], m["thread_id"])].append(m)
        for t in sorted(
            d["gmail_threads"], key=lambda t: (t["account_id"], t["thread_id"])
        ):
            account = placeholders.get(t["account_id"])
            msgs = sorted(
                messages.get((t["account_id"], t["thread_id"]), []),
                key=lambda m: (
                    m.get("internal_date") or m.get("created_at") or "",
                    m["message_id"],
                ),
            )
            if account is None:
                why = (
                    "mailbox connected in Norm (its sync owns the mirror)"
                    if t["account_id"] in connected
                    else "mailbox not imported"
                )
                r.dropped("gmail_threads", why)
                if msgs:
                    r.dropped("gmail_messages", why, len(msgs))
                continue
            if self.thread_account.get(t["thread_id"]) != t["account_id"]:
                r.dropped(
                    "gmail_threads",
                    "same thread id in another mailbox (link points elsewhere)",
                )
                if msgs:
                    r.dropped("gmail_messages", "thread not imported", len(msgs))
                continue
            kept = []
            for m in msgs:
                if "DRAFT" in (m.get("label_ids") or []):
                    r.dropped("gmail_messages", "draft (drafts are not imported)")
                    continue
                kept.append(m)
            thread_id = self.id("gmail_threads", f"{t['account_id']}:{t['thread_id']}")
            last = kept[-1] if kept else None
            self.plan.add(
                fm.FnMailboxThread,
                self.stamp(
                    {
                        "id": thread_id,
                        "account_id": account["id"],
                        "gmail_thread_id": t["thread_id"],
                        "subject": _clean((kept[0] if kept else t).get("subject")),
                        "snippet": _clean((last or t).get("snippet")),
                        "last_message_at": _ts(last.get("internal_date"))
                        if last
                        else _ts(t.get("last_message_at")),
                        "last_from_email": _clean((last or {}).get("from_email"))
                        if last
                        else _clean(t.get("last_from_email")),
                        "last_from_name": _clean((last or {}).get("from_name"))
                        if last
                        else _clean(t.get("last_from_name")),
                        "message_count": len(kept)
                        if kept
                        else int(t.get("message_count") or 0),
                        "unread_count": sum(1 for m in kept if m.get("unread"))
                        if kept
                        else int(t.get("unread_count") or 0),
                        "label_ids": list(t.get("label_ids") or []),
                    },
                    t.get("created_at"),
                    t.get("updated_at"),
                ),
            )
            r.imported("gmail_threads")
            for m in kept:
                internal = _ts(m.get("internal_date")) or _ts(m.get("created_at"))
                self.plan.add(
                    fm.FnMailboxMessage,
                    self.stamp(
                        {
                            "id": self.id(
                                "gmail_messages", f"{m['account_id']}:{m['message_id']}"
                            ),
                            "account_id": account["id"],
                            "thread_id": thread_id,
                            "gmail_message_id": m["message_id"],
                            "gmail_thread_id": m["thread_id"],
                            "internal_date": internal,
                            "from_email": _clean(m.get("from_email")),
                            "from_name": _clean(m.get("from_name")),
                            "to_emails": list(m.get("to_emails") or []),
                            "cc_emails": list(m.get("cc_emails") or []),
                            "reply_to_email": _clean(m.get("reply_to_email")),
                            "subject": _clean(m.get("subject")),
                            "snippet": _clean(m.get("snippet")),
                            "label_ids": list(m.get("label_ids") or []),
                            "is_inbound": bool(m.get("is_inbound", True)),
                            "is_internal": bool(m.get("is_internal")),
                            "is_auto_reply": bool(m.get("is_auto_reply")),
                            "unread": bool(m.get("unread")),
                        },
                        m.get("created_at"),
                        m.get("updated_at"),
                    ),
                )
                r.imported("gmail_messages")

        # Folders: each placeholder mailbox's threads carrying an Actioned label.
        actioned = {}
        for row in d["gmail_mailbox_labels"]:
            folder = {
                "booking": "enquiry",
                "event": "reservation",
                "uncategorised": "other",
            }.get(row.get("actioned_kind") or "")
            if folder:
                actioned[(row["account_id"], row["label_id"])] = folder
        for t in d["gmail_folder_threads"]:
            account = placeholders.get(t["account_id"])
            if account is None:
                r.dropped(
                    "gmail_folder_threads",
                    "mailbox connected in Norm (its sync owns folders)"
                    if t["account_id"] in connected
                    else "mailbox not imported",
                )
                continue
            folders = sorted(
                {
                    actioned[(t["account_id"], lab)]
                    for lab in t.get("label_ids") or []
                    if (t["account_id"], lab) in actioned
                },
                key=_FOLDER_PRECEDENCE.index,
            )
            if not folders:
                r.dropped("gmail_folder_threads", "no Actioned label after all")
                continue
            if len(folders) > 1:
                r.tally(
                    "mail",
                    "thread with several Actioned labels (reservation > enquiry > other, as Orbit reads them)",
                )
            at = _ts(t.get("updated_at"))
            self.plan.add(
                fm.FnThreadFolder,
                {
                    "id": self.id(
                        "gmail_threads.folder", f"{t['account_id']}:{t['thread_id']}"
                    ),
                    "account_id": account["id"],
                    "gmail_thread_id": t["thread_id"],
                    "folder": folders[0],
                    "set_by_label": f"{ACTOR} (Gmail label)",
                    "set_at": at,
                    "gmail_synced_at": at,
                    "created_at": at,
                    "updated_at": at,
                },
            )
            r.imported("gmail_folder_threads")

    # ---- files ---------------------------------------------------------------------

    def resolve_files(
        self, source: OrbitSource | None, now: dt.datetime, log=print
    ) -> None:
        """Copy every wanted Orbit image into ``fn_files`` (unless already
        there), then point proposals, brand fields and the image library at
        them. A file that cannot be copied leaves its Orbit URL in place and
        is counted."""
        r = self.report
        for url in sorted(self.wanted_files):
            purposes = ", ".join(sorted(self.wanted_files[url]))
            existing = self.keep.files_by_ref.get(url)
            if existing:
                self.file_ids[url] = existing
                r.tally("files", f"already in Norm ({purposes})")
                continue
            if source is None:
                r.tally("files", f"not copied — file copy switched off ({purposes})")
                continue
            try:
                body, declared = source.fetch_file(url)
                content_type, width, height = _image_info(body, declared)
            except Exception as exc:  # noqa: BLE001 — one bad file is a counted miss
                r.tally("files", f"copy failed ({purposes})")
                r.example(
                    "file copy failed",
                    f"{url.rsplit('/', 1)[-1][:40]}: {str(exc)[:80]}",
                )
                continue
            sha = hashlib.sha256(body).hexdigest()
            file_id = self.keep.files_by_sha.get(sha)
            if file_id is None:
                file_id = self.id("fn_files", sha)
                if file_id not in self.plan.rows[fm.FnFile.__tablename__]:
                    self.plan.add(
                        fm.FnFile,
                        {
                            "id": file_id,
                            "sha256": sha,
                            "filename": urllib.parse.unquote(url.rsplit("/", 1)[-1])[
                                :255
                            ]
                            or "image",
                            "content_type": content_type,
                            "size_bytes": len(body),
                            "width": width,
                            "height": height,
                            "data": body,
                            "source_ref": url,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
                    r.tally("files", f"copied ({purposes})")
                else:
                    r.tally("files", f"same bytes as another copied URL ({purposes})")
            else:
                r.tally("files", f"same bytes already in Norm ({purposes})")
            self.file_ids[url] = file_id
        log(
            f"  files: {len(self.file_ids)} of {len(self.wanted_files)} images available in Norm"
        )

        for row in getattr(self, "proposal_rows", []) + getattr(
            self, "version_rows", []
        ):
            before = len(_storage_urls(row["html"], row["css"]))
            row["html"] = rewrite_storage_urls(row["html"], self.file_ids)
            row["css"] = rewrite_storage_urls(row["css"], self.file_ids)
            left = len(_storage_urls(row["html"], row["css"]))
            if before > left:
                r.tally("files", "proposal image URLs rewritten to Norm", before - left)
            if left:
                r.tally(
                    "files",
                    "proposal image URLs left pointing at Orbit (not copied)",
                    left,
                )
        for brand in self.plan.rows.get(fm.FnVenueBrand.__tablename__, {}).values():
            brand["style"] = {
                k: rewrite_storage_urls(v, self.file_ids) if isinstance(v, str) else v
                for k, v in brand["style"].items()
            }
        for row, field_name, url in self._brand_files:
            if url in self.file_ids:
                row[field_name] = self.file_ids[url]
            else:
                r.tally(
                    "files",
                    f"brand {field_name.replace('_file_id', '')} image not copied (left empty)",
                )
        for row, url in self._table_plans:
            row["table_plan_file_id"] = self.file_ids.get(url)
            r.tally(
                "files",
                "event table plan linked"
                if row["table_plan_file_id"]
                else "event table plan not copied (left empty)",
            )
        for q, venue_id in self._quote_images:
            file_id = self.file_ids.get(q["public_url"].split("?", 1)[0])
            if not file_id:
                r.dropped("quote_images", "image not copied")
                continue
            user, _ = (
                self.person(q.get("created_by"))
                if q.get("created_by")
                else (None, None)
            )
            self.plan.add(
                fm.FnProposalImage,
                self.stamp(
                    {
                        "id": self.id("quote_images", q["id"]),
                        "venue_id": venue_id,
                        "file_id": file_id,
                        "name": q.get("name") or "image",
                        "caption": _clean(q.get("caption")),
                        "tags": list(q.get("tags") or []),
                        "created_by_user_id": user,
                    },
                    q.get("created_at"),
                    q.get("updated_at"),
                ),
            )
            r.imported("quote_images")


def _hex(value) -> str | None:
    """A ``#rgb`` / ``#rrggbb`` / ``#rrggbbaa`` colour as ``#rrggbb``
    lowercase (alpha dropped), else None."""
    text_value = str(value or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", text_value):
        return "#" + "".join(ch * 2 for ch in text_value[1:])
    if re.fullmatch(r"#[0-9a-f]{6}([0-9a-f]{2})?", text_value):
        return text_value[:7]
    return None


def _rgb(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _blend(colour: str | None, over: str, alpha: float) -> str | None:
    """``colour`` at ``alpha`` over ``over``, as a solid hex."""
    if not colour or not _hex(over):
        return None
    top, base = _rgb(colour), _rgb(_hex(over))
    return "#" + "".join(f"{round(b + (t - b) * alpha):02x}" for t, b in zip(top, base))


def _contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two hex colours."""

    def luminance(colour: str) -> float:
        def channel(v: int) -> float:
            c = v / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b_ = (channel(v) for v in _rgb(_hex(colour) or "#000000"))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_

    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


_TAG = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)")
_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)


def _tags(html: str) -> Counter:
    return Counter(t.lower() for t in _TAG.findall(html or ""))


def _percent(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _term_text(term) -> str | None:
    if isinstance(term, dict):
        title, body = _clean(term.get("title")), _clean(term.get("body"))
        return f"{title} — {body}" if title and body else (title or body)
    return _clean(term)


def _policies_text(contexts: list[dict]) -> str | None:
    """Orbit's venue AI context (overview, policies, booking rules, extras)
    as the venue's one policies text."""
    parts = []
    for c in contexts:
        for key, heading in (
            ("overview", "Overview"),
            ("policies", "Policies"),
            ("booking_rules", "Booking rules"),
            ("extras", "Extras"),
        ):
            body = _clean(c.get(key))
            if body:
                parts.append(f"{heading}\n{body}")
    return "\n\n".join(parts) or None


def _image_info(body: bytes, declared: str) -> tuple[str, int | None, int | None]:
    """The real image type and size, from the bytes — a storage error page
    must never be filed as a proposal image."""
    from PIL import Image

    if not body:
        raise ValueError("empty file")
    try:
        with Image.open(io.BytesIO(body)) as img:
            fmt = (img.format or "").lower()
            width, height = img.size
    except Exception as exc:  # noqa: BLE001 — anything PIL can't open is refused
        raise ValueError(f"not an image ({declared or 'unknown type'})") from exc
    types = {
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    if fmt not in types:
        raise ValueError(f"unsupported image format {fmt or declared!r}")
    return types[fmt], width, height


def _venue_data_counts(data: dict) -> dict[str, Counter]:
    """Rows per Orbit venue that would be imported — what makes an unmapped
    venue a blocker rather than a footnote."""
    tables = (
        "enquiries",
        "venue_functions",
        "venue_event_areas",
        "function_reply_templates",
        "preorder_templates",
        "web_forms",
        "web_form_submissions",
        "venue_gmail_accounts",
        "quote_images",
        "venue_brand_styles",
        "venue_menu_links",
        "venue_ai_context",
        "venue_function_option_visibility",
        "enquiry_preorder_menus",
        "contact_proposals",
    )
    out: dict[str, Counter] = defaultdict(Counter)
    for table in tables:
        for row in data.get(table, []):
            if row.get("venue_id"):
                out[row["venue_id"]][table] += 1
    for row in data.get("venue_quote_templates", []):
        if (row.get("template_html") or "").strip() or (
            row.get("template_css") or ""
        ).strip():
            out[row["venue_id"]]["venue_quote_templates"] += 1
    return out


# ===========================================================================
# The database phase
# ===========================================================================

#: Mirror tables: rows of a CONNECTED mailbox belong to its sync.
_MIRROR = (
    "fn_mailbox_threads",
    "fn_mailbox_messages",
    "fn_mailbox_labels",
    "fn_mailbox_retry",
    "fn_thread_folders",
)


def resolve_org(db: Session, ref: str) -> Organization:
    org = (
        db.query(Organization)
        .filter((Organization.id == ref) | (Organization.slug == ref))
        .first()
    )
    if org is None:
        raise ImportRefused(f"no organisation with id or slug {ref!r}")
    return org


def _members(db: Session, org_id: str) -> dict[str, str]:
    rows = (
        db.query(User.id, User.email)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .filter(OrganizationMembership.organization_id == org_id)
        .all()
    )
    return {e.strip().lower(): uid for uid, e in rows if e}


def _keep_state(db: Session, org_id: str, slugs: list[str]) -> _Keep:
    keep = _Keep()
    for acc in db.query(fm.FnMailboxAccount).filter(
        fm.FnMailboxAccount.organization_id == org_id,
        fm.FnMailboxAccount.connection_id.isnot(None),
    ):
        keep.connected_accounts[acc.google_email.strip().lower()] = acc
    for sid, form_id, enquiry_id in db.query(
        fm.FnFormSubmission.id,
        fm.FnFormSubmission.form_id,
        fm.FnFormSubmission.enquiry_id,
    ).filter(fm.FnFormSubmission.organization_id == org_id):
        if not is_import_id(sid):
            keep.native_submissions.append((sid, form_id, enquiry_id))
    for fid, sha, ref in db.query(
        fm.FnFile.id, fm.FnFile.sha256, fm.FnFile.source_ref
    ).filter(fm.FnFile.organization_id == org_id):
        keep.files_by_sha[sha] = fid
        if ref:
            keep.files_by_ref.setdefault(ref, fid)
    if slugs:
        keep.foreign_slugs = {
            s
            for (s,) in db.query(fm.FnWebForm.slug).filter(
                fm.FnWebForm.slug.in_(slugs), fm.FnWebForm.organization_id != org_id
            )
        }
    return keep


def norm_authored_rows(db: Session, org_id: str) -> dict[str, int]:
    """Functions rows of the organisation the importer did NOT write (not a
    uuid5 import id), outside what it keeps on purpose: ``fn_files``, form
    submissions Norm received, mailboxes connected in Norm and their mirror.

    Anything here was authored in Norm — the organisation was live at some
    point (shadow refuses every other write). A reload would delete it, so
    the importer refuses instead: once Norm holds data of its own, it is the
    system of record and Orbit is never loaded over it again."""
    connected = [
        r[0]
        for r in db.query(fm.FnMailboxAccount.id).filter(
            fm.FnMailboxAccount.organization_id == org_id,
            fm.FnMailboxAccount.connection_id.isnot(None),
        )
    ]
    out = {}
    for table_name in _ORDER:
        if table_name in ("fn_files", "fn_form_submissions"):
            continue
        t = _MODELS[table_name].__table__
        native = ~((func.length(t.c.id) == 36) & (func.substr(t.c.id, 15, 1) == "5"))
        q = (
            select(func.count())
            .select_from(t)
            .where(t.c.organization_id == org_id, native)
        )
        if table_name == "fn_mailbox_accounts":
            q = q.where(t.c.connection_id.is_(None))
        elif table_name in _MIRROR and connected:
            q = q.where(t.c.account_id.not_in(connected))
        n = db.execute(q).scalar()
        if n:
            out[table_name] = n
    return out


def _refuse_if_norm_authored(db: Session, org) -> None:
    found = norm_authored_rows(db, org.id)
    if found:
        listed = ", ".join(
            f"{t} {n}" for t, n in sorted(found.items(), key=lambda kv: -kv[1])[:8]
        )
        raise ImportRefused(
            f"{org.name} has Functions records created in Norm ({listed}) — it has been live. "
            "The importer only ever reloads data it wrote itself, so it will not run here again; "
            "nothing was written."
        )


def _wipe(
    db: Session, org_id: str, keep: _Keep, plan: _Plan, report: ImportReport
) -> None:
    """Delete the org's Functions business rows, children first (the
    business references are NO ACTION by design)."""
    connected = [a.id for a in keep.connected_accounts.values()]
    native_forms = sorted({f for _, f, _ in keep.native_submissions})
    orphaned = set(native_forms) - set(plan.rows.get("fn_web_forms", {}))
    if orphaned:
        report.kept["fn_web_forms (gone from Orbit; Norm submissions point at it)"] = (
            len(orphaned)
        )
    if keep.native_submissions:
        # A Norm-received submission must not pin an imported enquiry.
        db.execute(
            update(fm.FnFormSubmission.__table__)
            .where(
                fm.FnFormSubmission.organization_id == org_id,
                fm.FnFormSubmission.id.in_(
                    [s for s, _, e in keep.native_submissions if e]
                ),
            )
            .values(enquiry_id=None)
        )
        report.kept["fn_form_submissions"] = len(keep.native_submissions)
    for table_name in reversed(_ORDER):
        if table_name == "fn_files":
            continue
        t = _MODELS[table_name].__table__
        q = delete(t).where(t.c.organization_id == org_id)
        if table_name == "fn_mailbox_accounts":
            q = q.where(t.c.connection_id.is_(None))
        elif table_name in _MIRROR and connected:
            q = q.where(t.c.account_id.not_in(connected))
        elif table_name == "fn_form_submissions":
            q = q.where(func.substr(t.c.id, 15, 1) == "5", func.length(t.c.id) == 36)
        elif table_name == "fn_web_forms" and native_forms:
            q = q.where(t.c.id.not_in(native_forms))
        deleted = db.execute(q).rowcount
        if deleted:
            report.deleted[table_name] = deleted
    if connected:
        report.kept["fn_mailbox_accounts (connected)"] = len(connected)


def _insert(db: Session, plan: _Plan, keep: _Keep, report: ImportReport) -> None:
    native_forms = {f for _, f, _ in keep.native_submissions}
    for table_name in _ORDER:
        rows = list(plan.rows.get(table_name, {}).values())
        if not rows:
            continue
        t = _MODELS[table_name].__table__
        if table_name == "fn_web_forms" and native_forms:
            # Forms a kept submission points at were not deleted: update them.
            for row in [x for x in rows if x["id"] in native_forms]:
                db.execute(update(t).where(t.c.id == row["id"]).values(**row))
            rows = [x for x in rows if x["id"] not in native_forms]
        for i in range(0, len(rows), 2000):
            db.execute(insert(t), rows[i : i + 2000])
        report.written[table_name] = len(plan.rows[table_name])


def _db_counts(db: Session, org_id: str, plan: _Plan) -> dict[str, tuple[int, int]]:
    """Per table: (rows planned, planned ids now present) — proof the
    inserts landed, read back rather than assumed."""
    out = {}
    for table_name, rows in plan.rows.items():
        if not rows:
            continue
        t = _MODELS[table_name].__table__
        ids = list(rows)
        found = 0
        for i in range(0, len(ids), 5000):
            found += db.execute(
                select(func.count())
                .select_from(t)
                .where(t.c.organization_id == org_id, t.c.id.in_(ids[i : i + 5000]))
            ).scalar()
        out[table_name] = (len(rows), found)
    return out


def _gate(report: ImportReport, thresholds: Thresholds, counts: dict) -> dict:
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    for table, s in report.sources.items():
        if s["orbit_count"] is None:
            continue
        slack = max(
            thresholds.read_tolerance_rows,
            int(s["orbit_count"] * thresholds.read_tolerance_share),
        )
        check(
            f"read {table}",
            abs(s["read"] - s["orbit_count"]) <= slack,
            f"{s['read']} read vs {s['orbit_count']} in Orbit",
        )
    for table, s in report.sources.items():
        accounted = s["imported"] + s["merged"] + sum(s["dropped"].values())
        if (
            table in _FULL_READS
            and table not in ("venues", "team_members")
            and accounted != s["read"]
        ):
            check(
                f"accounted {table}",
                False,
                f"{s['read']} read but {accounted} imported/merged/dropped — rows lost without a reason",
            )
    for table in _CORE:
        s = report.sources.get(table)
        if not s or not s["read"]:
            continue
        lost = sum(n for reason, n in s["dropped"].items() if reason != _SKIPPED_VENUE)
        limit = max(
            thresholds.max_dropped_rows, int(s["read"] * thresholds.max_dropped_share)
        )
        check(
            f"dropped {table}",
            lost <= limit,
            f"{lost} dropped for data reasons (limit {limit})",
        )
    t = report.tallies
    unparseable = sum(
        n for k, n in t.get("times", {}).items() if k.startswith("unparseable")
    )
    fields_total = max(sum(t.get("time fields", {}).values()), 1)
    check(
        "unparseable times",
        unparseable
        <= max(
            thresholds.small_count, fields_total * thresholds.max_unparseable_time_share
        ),
        f"{unparseable} of {fields_total} time fields",
    )
    unknown = t.get("areas", {}).get(_AREA_UNKNOWN, 0)
    named = unknown + t.get("areas", {}).get(_AREA_MATCHED, 0)
    check(
        "unknown areas",
        unknown
        <= max(thresholds.small_count, named * thresholds.max_unknown_area_share),
        f"{unknown} of {named} event area names",
    )
    opts = report.sources.get("venue_function_spend_options", {})
    unmatched = t.get("min spends", {}).get(_UNMATCHED_OPTION, 0)
    check(
        "unmatched spend options",
        unmatched
        <= max(
            thresholds.small_count,
            opts.get("read", 0) * thresholds.max_unmatched_option_share,
        ),
        f"{unmatched} of {opts.get('read', 0)} booking spend options",
    )
    short = {k: v for k, v in counts.items() if v[0] != v[1]}
    check(
        "rows written",
        not short,
        "all planned rows present" if not short else f"missing: {short}",
    )
    return {"passed": all(c["ok"] for c in checks), "checks": checks}


# ===========================================================================
# Parity: Norm's derived state vs Orbit's stored status
# ===========================================================================


def parity(
    db: Session,
    org_id: str,
    data: dict,
    now: dt.datetime | None = None,
    reviewed: dict[str, str] | None = None,
) -> dict:
    """Norm's derived enquiry stage and event status (status.py) against the
    status Orbit stored, per record, with the reason for each mismatch.

    ``reviewed`` (Orbit enquiry id -> group) are the Orbit-"confirmed"
    enquiries with no booking that the import handled by rule: they are left
    out of the comparison and counted on their own."""
    reviewed = reviewed or {}
    now = now or dt.datetime.now(dt.timezone.utc)
    enq_sel = derived.enquiry_status_select(
        org_id, now, valid_tz=derived.valid_timezones(db, org_id)
    ).subquery()
    norm_enq = {r.enquiry_id: r for r in db.execute(select(enq_sel))}
    ev_sel = derived.event_status_select(org_id).subquery()
    norm_ev = {r.event_id: r for r in db.execute(select(ev_sel))}

    approved_lines = set()
    lines_by_proposal = defaultdict(list)
    for line in data["contact_proposal_spend_lines"]:
        lines_by_proposal[line["proposal_id"]].append(line)
    for p in data["contact_proposals"]:
        if any(x.get("status") == "approved" for x in lines_by_proposal[p["id"]]):
            approved_lines.add(p["enquiry_id"])
    venues = {v["id"]: v["name"] for v in data["venues"]}

    def matrix_add(m, a, b):
        m.setdefault(a, Counter())[b] += 1

    enq_matrix, enq_reasons, enq_by_venue = {}, Counter(), defaultdict(lambda: [0, 0])
    by_rule: dict[str, Counter] = defaultdict(Counter)
    for e in data["enquiries"]:
        nid = import_id(org_id, "enquiries", e["id"])
        row = norm_enq.get(nid)
        if row is None:
            continue
        orbit, norm = e.get("status") or "new", row.stage
        if e["id"] in reviewed:
            by_rule[reviewed[e["id"]]][norm] += 1
            continue
        matrix_add(enq_matrix, orbit, norm)
        venue = venues.get(e.get("venue_id"), "(group-wide)")
        enq_by_venue[venue][1] += 1
        if orbit == norm:
            enq_by_venue[venue][0] += 1
            continue
        enq_reasons[_enquiry_reason(e, row, e["id"] in approved_lines)] += 1

    ev_matrix, ev_reasons, ev_by_venue = {}, Counter(), defaultdict(lambda: [0, 0])
    for f in data["venue_functions"]:
        nid = import_id(org_id, "venue_functions", f["id"])
        row = norm_ev.get(nid)
        if row is None:
            continue
        orbit, norm = f.get("status") or "booked", row.status
        matrix_add(ev_matrix, orbit, norm)
        venue = venues.get(f.get("venue_id"), "?")
        ev_by_venue[venue][1] += 1
        if orbit == norm:
            ev_by_venue[venue][0] += 1
            continue
        ev_reasons[_event_reason(f, row)] += 1

    def summary(matrix, by_venue, reasons):
        total = sum(sum(c.values()) for c in matrix.values())
        same = sum(c.get(k, 0) for k, c in matrix.items())
        return {
            "total": total,
            "match": same,
            "share": round(same / total, 4) if total else None,
            "matrix": {k: dict(v) for k, v in sorted(matrix.items())},
            "by_venue": {
                k: {"match": v[0], "total": v[1]} for k, v in sorted(by_venue.items())
            },
            "mismatch_reasons": dict(reasons.most_common()),
        }

    enquiries = summary(enq_matrix, enq_by_venue, enq_reasons)
    enquiries["reviewed_by_rule"] = {
        group: {"total": sum(stages.values()), "norm_stage": dict(stages)}
        for group, stages in sorted(by_rule.items())
    }
    return {
        "computed_at": now.isoformat(),
        "enquiries": enquiries,
        "events": summary(ev_matrix, ev_by_venue, ev_reasons),
    }


def _enquiry_reason(e: dict, row, has_approved_line: bool) -> str:
    orbit, norm = e.get("status") or "new", row.stage
    if orbit == "confirmed" and norm != "confirmed":
        if has_approved_line:
            return (
                "Orbit: confirmed by an APPROVED minimum spend, no booking — Norm confirms only on "
                "the customer's confirmation (an event)"
            )
        return "Orbit: confirmed with no booking and no approved minimum spend"
    if norm == "confirmed" and orbit in ("new", "replied", "quoted"):
        return "Orbit status never advanced although a booking exists"
    if norm == "lost" or orbit == "lost":
        return f"closure history disagrees (Orbit {orbit}, Norm {norm})"
    if orbit in ("replied", "quoted") and norm == "new":
        if e.get("last_outbound_at") and not row.last_outbound_at:
            return (
                "Orbit saw an outbound email (last_outbound_at) that is not on a linked thread of a "
                "venue mailbox (personal mailbox, or a thread since unlinked)"
            )
        return f"Orbit {orbit} without outbound mail on a linked venue thread"
    if orbit == "new" and norm == "replied":
        return "an outbound email is on a linked thread but Orbit's status stayed new"
    if orbit == "new" and norm == "quoted":
        return "a quote send is recorded but Orbit's status stayed new"
    if orbit == "quoted" and norm == "replied":
        return "Orbit quoted without a recorded send"
    if orbit == "replied" and norm == "quoted":
        return "a quote send is recorded but Orbit's status stayed replied"
    return f"other (Orbit {orbit}, Norm {norm})"


#: When Orbit's venue_functions status trigger (status from section facts)
#: was installed — statuses last written before it followed older app logic.
_ORBIT_STATUS_TRIGGER = "2026-08-13"


def _event_reason(f: dict, row) -> str:
    orbit, norm = f.get("status") or "booked", row.status
    edited = any(f.get(f"client_{s}_edited_at") for s in fm.EVENT_SECTIONS)
    done = [
        s
        for s in fm.EVENT_SECTIONS
        if f.get(f"client_{s}_confirmed_at") or f.get(f"client_{s}_skipped_at")
    ]
    stale = (f.get("updated_at") or "") < _ORBIT_STATUS_TRIGGER
    if orbit in ("booked", "sent_to_client") and norm == "in_progress" and edited:
        return "client edited, but Orbit's stored status never moved on" + (
            " (last updated before Orbit's status trigger of 13 Aug 2026)"
            if stale
            else ""
        )
    if orbit == "confirmed" and norm != "confirmed" and len(done) < 3:
        return (
            "Orbit kept 'confirmed' after a section was cleared (its status is sticky)"
        )
    if orbit == "sent_to_client" and norm == "booked":
        return "Orbit sent_to_client without a link-sent time"
    if orbit == "booked" and norm == "sent_to_client":
        return "link sent but Orbit's status stayed booked"
    if orbit == "in_progress" and norm in ("booked", "sent_to_client"):
        if done and not edited:
            return (
                "Orbit's old app set 'in progress' on a section confirmation (no client edit); "
                "its trigger never moves a status back, its current rule would not set it"
            )
        return "Orbit in progress without a client edit recorded"
    if orbit != "confirmed" and norm == "confirmed":
        return "all three sections done but Orbit's status did not move"
    return f"other (Orbit {orbit}, Norm {norm})"


def parity_lines(p: dict) -> list[str]:
    out = []
    for what in ("enquiries", "events"):
        s = p.get(what) or {}
        if not s.get("total"):
            continue
        out.append(
            f"  {what}: {s['match']}/{s['total']} match ({s['share'] * 100:.1f}%)"
        )
        for orbit, row in s["matrix"].items():
            out.append(
                f"    Orbit {orbit:<15} → "
                + ", ".join(f"{k} {v}" for k, v in sorted(row.items()))
            )
        for venue, v in s["by_venue"].items():
            out.append(f"    {venue:<22} {v['match']}/{v['total']}")
        for reason, n in s["mismatch_reasons"].items():
            out.append(f"    {n:>5}  {reason}")
        for group, r in (s.get("reviewed_by_rule") or {}).items():
            stages = ", ".join(f"{k} {v}" for k, v in sorted(r["norm_stage"].items()))
            out.append(
                f"    reviewed by rule, not compared — {group.replace('_', '-')}: {r['total']} (Norm: {stages})"
            )
    return out


# ===========================================================================
# Entry point
# ===========================================================================


@dataclass
class ImportResult:
    #: Committed (real run), or would have been (dry run).
    ok: bool
    committed: bool
    report: ImportReport
    plan: _Plan | None = None

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 2


def run_import(
    db: Session,
    source: OrbitSource,
    *,
    org: str,
    dry_run: bool = False,
    skip_venues=(),
    thresholds: Thresholds | None = None,
    copy_files: bool = True,
    with_parity: bool = True,
    commit: bool = True,
    now: dt.datetime | None = None,
    log=print,
) -> ImportResult:
    """Import Orbit into one organisation: read → transform → (one
    transaction) lock, wipe, insert, gate → commit or roll back.

    The database work runs in a SAVEPOINT, so a dry run or a failed gate
    rolls back to exactly where the caller's session was; ``commit`` then
    commits the session after a passing real run. ``ImportRefused`` (and an
    ``OrbitReadError`` while reading) means nothing was written."""
    thresholds = thresholds or Thresholds()
    now = now or dt.datetime.now(dt.timezone.utc)
    report = ImportReport(dry_run=dry_run)
    target = resolve_org(db, org)
    report.org = {
        "id": target.id,
        "name": target.name,
        "slug": target.slug,
        "mode": target.functions_mode,
    }
    if target.functions_mode == "live":
        raise ImportRefused(
            f"{target.name} is live — the importer never overwrites a live organisation."
        )
    _refuse_if_norm_authored(db, target)  # before reading anything: fail fast

    log("reading Orbit…")
    data = read_orbit(source, report, log)
    venues = db.query(Venue).filter(Venue.organization_id == target.id).all()
    keep = _keep_state(db, target.id, [w["slug"] for w in data["web_forms"]])
    transform = _Transform(
        org=target,
        venues=venues,
        members=_members(db, target.id),
        data=data,
        keep=keep,
        report=report,
        skip_venues=skip_venues,
        now=now,
    )
    log("transforming…")
    plan = transform.run()
    transform.resolve_files(source if copy_files else None, now, log)
    transform.sanitise_rich_text()
    for row in plan.rows.get(fm.FnMinSpend.__tablename__, {}).values():
        row.pop("_window", None)
    plan.complete()

    log("writing (one transaction)…")
    nested = db.begin_nested()
    try:
        locked = db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
            {"k": _LOCK_KEY.format(org_id=target.id)},
        ).scalar()
        if not locked:
            raise ImportRefused("another import is running for this organisation")
        # Again under the lock, right before the wipe: the guard that matters.
        _refuse_if_norm_authored(db, target)
        _wipe(db, target.id, keep, plan, report)
        _insert(db, plan, keep, report)
        db.flush()
        report.gate = _gate(report, thresholds, _db_counts(db, target.id, plan))
        if with_parity:
            report.parity = parity(db, target.id, data, now, transform.reviewed)
        # Fence: the mode is read again, under a share lock held until commit,
        # so going live cannot slip in between the first check and now.
        mode = db.execute(
            select(Organization.functions_mode)
            .where(Organization.id == target.id)
            .with_for_update(read=True)
        ).scalar()
        if mode == "live":
            raise ImportRefused(
                f"{target.name} went live during the import — nothing written."
            )
        passed = report.gate["passed"]
        if dry_run or not passed:
            nested.rollback()
            report.outcome = (
                "dry run: rolled back — nothing written"
                if passed
                else "SANITY GATE FAILED: rolled back — nothing written"
            )
            if dry_run and not passed:
                report.outcome = "dry run: rolled back — and a real run would STOP at the sanity gate"
            return ImportResult(ok=passed, committed=False, report=report, plan=plan)
        nested.commit()
        if commit:
            db.commit()
        report.outcome = "imported and committed"
        return ImportResult(ok=True, committed=True, report=report, plan=plan)
    except BaseException:
        if nested.is_active:
            nested.rollback()
        raise


__all__ = [
    "ImportRefused",
    "ImportReport",
    "ImportResult",
    "NAMESPACE",
    "OrbitPostgrest",
    "PUBLIC_FILE_PATH",
    "Thresholds",
    "import_id",
    "is_import_id",
    "parity",
    "parse_clock",
    "rewrite_storage_urls",
    "run_import",
    "to_cents",
]
