"""Orbit → Loaded Payroll + Norm Finance: one organisation's finance data,
reloaded from Orbit while Norm runs the modules in ``shadow``.

Orbit stays the system of record until each module is ``live``. Until then
every run of this importer makes the organisation's imported rows match Orbit
*now*, in ONE transaction:

    advisory lock (per org) → upsert every Orbit-authored row (uuid5) →
    delete Orbit-authored rows Orbit no longer has → sanity gates → commit

or roll back, leaving the previous copy untouched. ``dry_run`` does all of it
and rolls back, so its report is exactly what a real run would do.

**Deterministic ids.** ``import_id(org, orbit_table, key)`` is a UUID5 under a
fixed namespace, so a row keeps its id across reloads. Norm itself only ever
creates uuid4 ids, which is how the importer tells its own rows from the
organisation's — and the one rule the shadow period rests on:

**A Norm-authored row is never overwritten.** Rehearsal inputs, a test
performance approved on a phone, a payout keyed in Norm: the importer reloads
only rows it wrote itself (uuid5). When an Orbit row lands on the same natural
key as a Norm-authored one (the same pay week's ACE inputs, the same leave
request and week) the Orbit row is left out and the collision is reported —
and children of a week Norm created attach to Norm's week row, so the "already
pushed" guard still sees Orbit's journal. An Orbit-authored row that a
Norm-authored row references is kept rather than deleted.

**Refuses when live.** A module that is ``live`` is Norm's record; the
importer refuses to touch its tables (checked first, and again under a share
lock right before commit).

**Sanity gates, computed in the same run.** Orbit's own counts (HEAD) and
sums (a second, narrow read of the numeric columns) are compared with what
the transform planned: ACE gross per entity-week to the cent, live journals
and intercompany documents in count and amount, adjustments, leave requested
and paid, gratuity shares against their pools, the locked budget per
venue-month, every push row carrying a Xero id, every payout resolving to one
(venue, request, week), every Orbit venue mapped. Any failure rolls back.
The 18-Oct-2026 figures the plan quotes are printed beside each gate for the
reader; they decide nothing.

**Structured, not copied** (plan §6.4): Orbit's ``::week::`` payout keys are
split; legacy payouts get a pay week and the ``orbit_legacy`` flag; the ten
performer setup types become four lanes; weekly series are reconstructed from
the materialised shifts; Orbit's stored Sales Manager becomes the closing
manager with ``source = orbit``; send dates become Emails-page history rows;
account mappings collapse to the live keys of ``Connection.config.xero_mapping``
(reported, written only on request); invoice PDFs land in ``ent_files`` with
the check waived (paid history, never re-read). Everything the transform could
not carry is counted with its reason — a silent drop reads as "there was
nothing there", which parity must never let anyone believe.

Orbit is only ever read: GET and HEAD through PostgREST with the service key
(``ORBIT_SUPABASE_CREDS`` or ``.local/orbit-supabase.json``, D31), files only
from Orbit's own storage through the signed URLs Orbit stored, without the
key. Nothing here prints a credential or a person's details.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import pathlib
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

from app.db.models import Connection, Organization, OrganizationMembership, User, Venue

#: uuid5(NAMESPACE_URL, "https://bettercallnorm.com/finance/orbit-import").
#: NEVER change it: every imported row's id derives from it, and a new
#: namespace would give every record a new id (breaking every link to them).
NAMESPACE = uuid.UUID("c3b2e0a2-5f0e-5d0a-9d3c-7b6a1f2d8e41")

#: Who imported facts are attributed to when Orbit recorded no person.
ACTOR = "Orbit"
SOURCE = "orbit"
SOURCE_LEGACY = "orbit_legacy"
MAX_FILE_BYTES = 10 * 1024 * 1024  # the portal's own cap (plan §11.4)
_LOCK_KEY = "norm-finance-import:{org_id}"

MODULES = ("payroll", "entertainers", "finance")

CREDS_ENV = "ORBIT_SUPABASE_CREDS"
CREDS_NAME = "orbit-supabase.json"


def import_id(org_id: str, table: str, key) -> str:
    """The id an Orbit row gets in Norm — the same on every run."""
    return str(uuid.uuid5(NAMESPACE, f"{org_id}:{table}:{key}"))


def is_import_id(value: str | None) -> bool:
    """Whether an id was minted here (UUID5) rather than by Norm (UUID4)."""
    try:
        return uuid.UUID(str(value)).version == 5
    except (TypeError, ValueError):
        return False


class ImportRefused(Exception):
    """The import cannot run at all: a live module, an Orbit venue with data
    and no Norm venue, missing models, a concurrent run."""


class OrbitReadError(RuntimeError):
    pass


# ===========================================================================
# Orbit, read-only
# ===========================================================================


class OrbitSource(Protocol):
    def rows(
        self, table: str, *, select: str = "*", filters=(), key=("id",)
    ) -> list[dict]: ...

    def count(self, table: str, filters=()) -> int: ...

    def fetch_signed(self, url: str) -> tuple[bytes, str]: ...


class OrbitPostgrest:
    """Orbit's Supabase, read-only: PostgREST rows and storage objects.

    Only ever issues GET (and HEAD to count). Redirects are never followed —
    the service key travels on row reads and must not be forwarded anywhere.
    Files come only from the project's own storage through the signed URL
    Orbit stored on the row (its token is the credential; the service key is
    never sent there), capped in size.
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
        self.signed_prefix = root + "/storage/v1/object/sign/"
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

    def fetch_signed(self, url: str) -> tuple[bytes, str]:
        """Bytes behind a signed storage URL Orbit stored on a shift row. The
        URL's own token is the credential; the service key is never sent.
        Anything outside this project's signed-object path is refused."""
        import httpx

        canonical = str(url or "")
        path = canonical.split("?", 1)[0][len(self.signed_prefix) :]
        if (
            not canonical.startswith(self.signed_prefix)
            or not path
            or any(p in ("", ".", "..") for p in urllib.parse.unquote(path).split("/"))
        ):
            raise OrbitReadError(
                "refused (not Orbit signed storage): "
                + canonical.split("?", 1)[0][:120]
            )
        too_big = OrbitReadError(f"refused (over {self.max_file_bytes} bytes)")
        try:
            # Streamed and counted: a response is abandoned the moment it
            # passes the cap, whatever its Content-Length claims.
            with self._client.stream("GET", canonical) as resp:
                if resp.status_code >= 300:
                    raise OrbitReadError(f"HTTP {resp.status_code} for a signed file")
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
            raise OrbitReadError(f"{type(exc).__name__} for a signed file") from exc


def _main_checkout(root: pathlib.Path) -> pathlib.Path | None:
    """The main checkout behind a git worktree, or None (``.local`` is per
    checkout and git-ignored, so a worktree never has one of its own)."""
    marker = root / ".git"
    if not marker.is_file():
        return None
    marker_text = marker.read_text().strip()
    if not marker_text.startswith("gitdir:"):
        return None
    gitdir = pathlib.Path(marker_text.split(":", 1)[1].strip())
    if gitdir.parent.name == "worktrees":
        return gitdir.parent.parent.parent
    return None


def creds_path() -> pathlib.Path | None:
    """Where Orbit's read credential lives: ``$ORBIT_SUPABASE_CREDS`` (the
    production route, D31 — a Secret Manager file mounted or written at
    start), else the repo root's ``.local`` (or the main checkout's, from a
    worktree). None when nothing is there: a nightly run then records "no
    Orbit credential" instead of failing loudly every five minutes."""
    override = os.environ.get(CREDS_ENV)
    if override:
        p = pathlib.Path(override).expanduser()
        return p if p.is_file() else None
    # importer.py → finance_core → services → app → api → apps → the repo root
    root = pathlib.Path(__file__).resolve().parents[5]
    candidates = [root / ".local" / CREDS_NAME]
    main = _main_checkout(root)
    if main is not None:
        candidates.append(main / ".local" / CREDS_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def orbit_client(path: pathlib.Path | None = None) -> OrbitPostgrest:
    """An Orbit client from the credential file. The key is read and handed
    to the client; it is never logged, printed or returned."""
    path = path or creds_path()
    if path is None:
        raise ImportRefused(
            f"no Orbit credential — set {CREDS_ENV} to the credential file (D31)"
        )
    cfg = json.loads(path.read_text())
    if not cfg.get("service_role_key") or not cfg.get("url"):
        raise ImportRefused("Orbit credentials need url and service_role_key")
    return OrbitPostgrest(cfg["url"], cfg["service_role_key"])


# ---------------------------------------------------------------------------
# What is read. Columns are named: never a token column, never Orbit's
# Xero connection rows (their refresh tokens are not Norm's to hold).
# ---------------------------------------------------------------------------

_BUDGET_FILTER = "financial_budget=not.is.null"

#: table -> (module, select, filters, keyset key)
_READS: dict[str, tuple[str, str, tuple, tuple]] = {
    "venues": (
        "core",
        "id,name,slug,timezone,loadedhub_company_id,loadedhub_company_name,allowance_codes,pos_day_start",
        (),
        ("id",),
    ),
    "team_members": ("core", "id,name,email,google_email", (), ("id",)),
    "xero_connections": (
        "core",
        "venue_id,xero_tenant_id,tenant_name,status",
        (),
        ("xero_tenant_id",),
    ),
    "wage_recon_weeks": ("payroll", "*", (), ("id",)),
    "wage_recon_ace_inputs": ("payroll", "*", (), ("id",)),
    "wage_recon_manual_lines": ("payroll", "*", (), ("id",)),
    "wage_recon_accrued_pay": ("payroll", "*", (), ("id",)),
    "wage_recon_xero_pushes": ("payroll", "*", (), ("id",)),
    "wage_recon_xero_intercompany_pushes": ("payroll", "*", (), ("id",)),
    "xero_account_mappings": ("payroll", "*", (), ("id",)),
    "xero_intercompany_contacts": (
        "payroll",
        "*",
        (),
        ("xero_tenant_id", "counterparty_venue_id"),
    ),
    "leave_request_payouts": ("payroll", "*", (), ("request_id",)),
    "payrun_gratuity_batches": ("payroll", "*", (), ("id",)),
    "payrun_gratuity_allocations": ("payroll", "*", (), ("id",)),
    "entertainers": ("entertainers", "*", (), ("id",)),
    "entertainer_shifts": ("entertainers", "*", (), ("id",)),
    "entertainer_shift_time_changes": ("entertainers", "*", (), ("id",)),
    "entertainer_xero_contacts": ("entertainers", "*", (), ("id",)),
    "entertainer_group_members": ("entertainers", "*", (), ("id",)),
    "budgets": ("finance", "*", (_BUDGET_FILTER,), ("id",)),
    "finance_budgets": ("finance", "*", (), ("id",)),
    "sales_forecast_factor_types": ("finance", "id,venue_id,name,active", (), ("id",)),
}

#: Lookup tables read to drive another read, never imported row by row.
_LOOKUPS = frozenset({"sales_forecast_factor_types"})

#: The Orbit tables whose rows must all be accounted for (imported, merged or
#: dropped with a reason) and whose read count is checked against Orbit's.
_SOURCE_TABLES = tuple(
    t for t, (m, *_rest) in _READS.items() if m != "core" and t not in _LOOKUPS
)

#: The event factor type whose rows become forecast drivers (plan §6.4).
EVENT_FACTOR_TYPE = "event"


def tables_for(modules) -> set[str]:
    wanted = set(modules)
    return {t for t, (m, *_rest) in _READS.items() if m == "core" or m in wanted}


def read_orbit(source: OrbitSource, modules, report: ImportReport, log=print) -> dict:
    """Every table the requested modules need, in full, plus Orbit's own
    count of each — read before any transaction opens, so a broken read
    leaves nothing half-done. The event factors follow their types."""
    data: dict[str, list] = {}
    for table in tables_for(modules):
        module, sel, filters, key = _READS[table]
        try:
            rows = source.rows(table, select=sel, filters=filters, key=key)
        except OrbitReadError as exc:
            if table == "entertainer_group_members":
                # Empty in Orbit (0 rows, plan §6.4) and its columns unknown:
                # an unreadable table here is not a reason to stop.
                report.tally("notes", f"entertainer_group_members not read ({exc})")
                data[table] = []
                continue
            raise
        data[table] = rows
        count = source.count(table, filters) if module != "core" else None
        report.read(table, len(rows), count)
        log(
            f"  {table}: {len(rows)} rows"
            + (f" (Orbit counts {count})" if count is not None else "")
        )
    if "finance" in modules:
        type_ids = [
            t["id"]
            for t in data.get("sales_forecast_factor_types", [])
            if str(t.get("name") or "").strip().lower() == EVENT_FACTOR_TYPE
        ]
        factors: list[dict] = []
        for i in range(0, len(type_ids), 50):
            part = ",".join(OrbitPostgrest._quote(v) for v in type_ids[i : i + 50])
            factors.extend(
                source.rows(
                    "sales_forecast_factors",
                    select="id,venue_id,factor_date,factor_type_id,label,impact,created_at,updated_at",
                    filters=(f"factor_type_id=in.({part})",),
                    key=("id",),
                )
            )
        data["sales_forecast_factors"] = factors
        report.read("sales_forecast_factors", len(factors), None)
        log(f"  sales_forecast_factors (Event): {len(factors)} rows")
    return data


# ---------------------------------------------------------------------------
# The baseline: Orbit's own sums, from a second narrow read of the numeric
# columns over the SAME filters. The gate compares the transform's planned
# totals with these, so a transform that drops or doubles money is caught by
# Orbit's figures, not by its own.
# ---------------------------------------------------------------------------


def read_baseline(source: OrbitSource, modules) -> dict:
    """Orbit's own figures, row by row with their venue, so the gate can sum
    them over exactly the venues the run maps (an unmapped venue fails its
    own gate; a skipped one is left out on both sides)."""
    out: dict = {}
    if "payroll" in modules:
        out["ace"] = [
            (r["venue_id"], str(r["week_ending"]), to_cents(r["gross_wages"]) or 0)
            for r in source.rows(
                "wage_recon_ace_inputs",
                select="id,venue_id,week_ending,gross_wages",
                key=("id",),
            )
        ]
        out["journals"] = [
            (
                r["venue_id"],
                bool(r.get("voided_at")),
                to_cents(r["total_debit"]) or 0,
                bool(r.get("xero_manual_journal_id")),
            )
            for r in source.rows(
                "wage_recon_xero_pushes",
                select="id,venue_id,voided_at,total_debit,xero_manual_journal_id",
                key=("id",),
            )
        ]
        out["ico"] = [
            (
                r["venue_id"],
                r.get("counterparty_venue_id"),
                bool(r.get("voided_at")),
                to_cents(r["total"]) or 0,
                bool(r.get("xero_invoice_id")),
            )
            for r in source.rows(
                "wage_recon_xero_intercompany_pushes",
                select="id,venue_id,counterparty_venue_id,voided_at,total,xero_invoice_id",
                key=("id",),
            )
        ]
        out["adjustments"] = [
            (r["venue_id"], to_cents(r["amount"]) or 0)
            for r in source.rows(
                "wage_recon_manual_lines", select="id,venue_id,amount", key=("id",)
            )
        ]
        out["payouts"] = [
            (
                r["venue_id"],
                to_cents(r["requested_amount"]) or 0,
                to_cents(r["paid_amount"]) or 0,
            )
            for r in source.rows(
                "leave_request_payouts",
                select="request_id,venue_id,requested_amount,paid_amount",
                key=("request_id",),
            )
        ]
        out["batches"] = {
            b["id"]: (b["venue_id"], to_cents(b["total_amount"]) or 0)
            for b in source.rows(
                "payrun_gratuity_batches",
                select="id,venue_id,total_amount",
                key=("id",),
            )
        }
        out["allocations"] = [
            (a["batch_id"], to_cents(a["amount"]) or 0)
            for a in source.rows(
                "payrun_gratuity_allocations", select="id,batch_id,amount", key=("id",)
            )
        ]
    if "finance" in modules:
        out["budgets"] = [
            (
                r["venue_id"],
                str(r["budget_date"])[:7],
                to_cents(r["financial_budget"]) or 0,
            )
            for r in source.rows(
                "budgets",
                select="id,venue_id,budget_date,financial_budget",
                filters=(_BUDGET_FILTER,),
                key=("id",),
            )
        ]
    return out


def baseline_totals(baseline: dict, mapped) -> dict:
    """The gate's Orbit-side figures over the mapped venues only."""
    mapped = set(mapped)
    out: dict = {}
    if "ace" in baseline:
        out["ace_gross"] = {
            (v, w): cents for v, w, cents in baseline["ace"] if v in mapped
        }
        live = [j for j in baseline["journals"] if j[0] in mapped and not j[1]]
        out["journals"] = {
            "live": len(live),
            "debit_cents": sum(j[2] for j in live),
            "without_xero_id": sum(
                1 for j in baseline["journals"] if j[0] in mapped and not j[3]
            ),
        }
        rows = [i for i in baseline["ico"] if i[0] in mapped and i[1] in mapped]
        live_ico = [i for i in rows if not i[2]]
        out["ico"] = {
            "live": len(live_ico),
            "total_cents": sum(i[3] for i in live_ico),
            "without_xero_id": sum(1 for i in rows if not i[4]),
        }
        out["adjustments_cents"] = sum(
            c for v, c in baseline["adjustments"] if v in mapped
        )
        payouts = [p for p in baseline["payouts"] if p[0] in mapped]
        out["leave"] = {
            "rows": len(payouts),
            "requested_cents": sum(p[1] for p in payouts),
            "paid_cents": sum(p[2] for p in payouts),
        }
        batches = {
            b: pool for b, (v, pool) in baseline["batches"].items() if v in mapped
        }
        per_batch: dict[str, int] = defaultdict(int)
        for batch, cents in baseline["allocations"]:
            if batch in batches:
                per_batch[batch] += cents
        out["gratuities"] = {
            "allocated_cents": sum(per_batch.values()),
            "per_batch": dict(per_batch),
            "pools": batches,
        }
    if "budgets" in baseline:
        per_month: dict[tuple, int] = defaultdict(int)
        for v, month, cents in baseline["budgets"]:
            if v in mapped:
                per_month[(v, month)] += cents
        out["budget_months"] = dict(per_month)
    return out


# ===========================================================================
# Small conversions (pure; copied from the Functions importer, not imported:
# the contract keeps the two modules apart)
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


def _date(value) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _time(value) -> dt.time | None:
    if not value:
        return None
    if isinstance(value, dt.time):
        return value
    try:
        return dt.time.fromisoformat(str(value)[:8])
    except ValueError:
        return None


def to_cents(value) -> int | None:
    """Money as integer cents, rounding half up (Orbit stored numeric)."""
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _clean(value) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _email(value) -> str | None:
    candidate = str(value or "").strip().lower()
    ok = len(candidate) <= 254 and re.fullmatch(
        r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+", candidate
    )
    return candidate if ok else None


def week_start_of(day: dt.date) -> dt.date:
    """The Monday of a date's week (pay weeks start on Monday)."""
    return day - dt.timedelta(days=day.weekday())


def week_start_from_ending(week_ending) -> dt.date | None:
    """Orbit keys reconciliation rows by the Sunday; Norm by the Monday."""
    end = _date(week_ending)
    return end - dt.timedelta(days=6) if end else None


def local_week_of(
    ts: dt.datetime | None, tz: ZoneInfo, day_start: dt.time
) -> dt.date | None:
    """The pay week a timestamp falls in, by the venue's trading day: before
    the day start it still belongs to the previous calendar day's week."""
    if ts is None:
        return None
    local = ts.astimezone(tz)
    trading_day = local.date()
    if local.time() < day_start:
        trading_day -= dt.timedelta(days=1)
    return week_start_of(trading_day)


def hours_between(start: dt.time | None, end: dt.time | None) -> float | None:
    """Clock hours of a performance, wrapping past midnight when the end is
    not after the start (Orbit's convention)."""
    if start is None or end is None:
        return None
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    if e <= s:
        e += 24 * 60
    return (e - s) / 60


def fiscal_year_of(day: dt.date, start_month: int) -> int:
    """The fiscal year a date belongs to, named by the year it ends in
    (April 2026 – March 2027 is FY2027 with ``start_month = 4``)."""
    return day.year + 1 if start_month > 1 and day.month >= start_month else day.year


# ===========================================================================
# Pure transforms — the rules, each testable on fixture rows
# ===========================================================================

#: IRD tax codes a Norm performer row may carry (IR330 / IR330C). Orbit's
#: free-text column holds 19 spellings of a handful of these.
TAX_CODES = frozenset(
    {
        "M", "M SL", "ME", "ME SL", "S", "S SL", "SB", "SB SL", "SH", "SH SL",
        "ST", "ST SL", "SA", "SA SL", "CAE", "EDW", "NSW", "ND", "STC", "WT",
    }
)  # fmt: skip


def normalise_tax_code(raw) -> tuple[str | None, str | None]:
    """``(code, problem)``: the IRD code Orbit's spelling means, or None with
    the reason. Case and spacing are forgiven ("msl" → "M SL", "Sl" → "S" +
    student loan is NOT assumed: "Sl" means S SL? No — it is the code S with
    a stray letter; it is listed for a human). Student-loan suffixes are
    only accepted where IRD has them."""
    spelling = _clean(raw)
    if spelling is None:
        return None, None
    compact = re.sub(r"[\s_\-/]+", "", spelling.upper())
    if compact in TAX_CODES:
        return compact, None
    if (
        compact.endswith("SL")
        and compact[:-2] in TAX_CODES
        and f"{compact[:-2]} SL" in TAX_CODES
    ):
        return f"{compact[:-2]} SL", None
    if compact == "WTSL":
        # Withholding tax has no student-loan variant: the contractor settles
        # the loan with IRD. Kept as WT and listed.
        return "WT", f"{spelling!r} read as WT (no student-loan variant exists)"
    return None, f"{spelling!r} is not an IRD tax code"


#: Orbit setup types that mean "paid on an invoice" (02 §12).
_INVOICE_SETUPS = frozenset({"company", "booking_agent", "band_leader", "partnership"})


def lane_for(performer: dict) -> tuple[str, str | None]:
    """``(lane, review_reason)`` for an Orbit performer — the 02 §12 mapping.
    Rows the mapping cannot settle get the lane Orbit effectively paid them
    through today and a review reason, never a silent guess."""
    method = _clean(performer.get("payment_method"))
    setup = _clean(performer.get("setup_type"))
    code, _ = normalise_tax_code(performer.get("tax_code"))
    if method == "invoice_exemption" or setup == "exemption":
        return "exemption", None
    if method == "payroll":
        if code == "WT":
            return "payroll_contractor", None
        if setup in ("band_leader", "booking_agent", "company", "partnership"):
            # Paid through payroll in Orbit although set up as a band leader or
            # company: Orbit's export line went to Ace; kept there, flagged.
            return "payroll_employee", f"paid by payroll but set up as {setup}"
        return "payroll_employee", None
    if method in ("band_leader", "partnership"):
        return "invoice", None
    if method == "invoice_company":
        if setup in _INVOICE_SETUPS:
            return "invoice", None
        if setup == "sole_trader":
            return (
                "invoice",
                "a sole trader paid on invoices — withholding tax may apply",
            )
        if setup == "payroll":
            return "invoice", "set up as payroll but paid on invoices"
        return "invoice", "Orbit setup type unknown — confirm the lane"
    if method == "sole_trader":
        if code == "WT":
            return "payroll_contractor", None
        return "invoice", "a sole trader with no WT tax code — confirm the lane"
    # No payment method at all.
    if _clean(performer.get("payroll_code")):
        return ("payroll_contractor" if code == "WT" else "payroll_employee"), (
            "no payment method in Orbit; has a payroll code"
        )
    return "invoice", "no payment method or setup type in Orbit — confirm the lane"


PAYOUT_WEEK_SEPARATOR = "::week::"


def split_payout_key(request_id: str) -> tuple[str, dt.date | None]:
    """Orbit's payout key: ``<loaded request id>::week::<monday>`` since July
    2026, the bare request id before (the legacy rows)."""
    raw = str(request_id or "")
    if PAYOUT_WEEK_SEPARATOR in raw:
        loaded_id, _, week = raw.partition(PAYOUT_WEEK_SEPARATOR)
        return loaded_id, _date(week)
    return raw, None


def workflow_kind(leave_type) -> str:
    label = str(leave_type or "").strip().lower()
    if label.startswith("annual"):
        return "annual"
    if label.startswith("sick"):
        return "sick"
    return "generic"


def series_rule(shifts: list[dict]) -> dict:
    """The weekly rule a run of materialised shifts was made from: the
    weekday(s), the usual times and amount, the interval in weeks (the
    smallest gap between occurrences on the same weekday), first and last
    dates. Orbit kept only the series id; Norm stores the rule."""
    dates = sorted({_date(s["shift_date"]) for s in shifts if _date(s["shift_date"])})
    # ISO weekdays (Monday 1 … Sunday 7), as ent_series stores them.
    weekdays = Counter(d.isoweekday() for d in dates)
    by_weekday: dict[int, list[dt.date]] = defaultdict(list)
    for d in dates:
        by_weekday[d.isoweekday()].append(d)
    gaps = [
        (b - a).days // 7
        for days in by_weekday.values()
        for a, b in zip(days, days[1:])
        if (b - a).days % 7 == 0 and (b - a).days > 0
    ]
    interval = math.gcd(*gaps) if gaps else 1
    starts = Counter(_time(s.get("start_time")) for s in shifts)
    ends = Counter(_time(s.get("end_time")) for s in shifts)
    amounts = Counter(to_cents(s.get("agreed_amount")) for s in shifts)
    return {
        "weekday": weekdays.most_common(1)[0][0] if weekdays else None,
        "weekdays": sorted(weekdays),
        "interval_weeks": max(1, interval),
        "start_time": starts.most_common(1)[0][0] if starts else None,
        "end_time": ends.most_common(1)[0][0] if ends else None,
        "agreed_cents": amounts.most_common(1)[0][0] if amounts else None,
        "starts_on": dates[0] if dates else None,
        "ends_on": dates[-1] if dates else None,
        "occurrences": len(dates),
    }


#: Orbit's 23 ``xero_account_mappings.line_type`` keys → the keys
#: ``Connection.config.xero_mapping.payroll`` carries (plan §6.1). Dropped:
#: the six dead ``ent_*`` keys, ``pnl_djs_*`` and ``pnl_marketing`` (an
#: adjustment carries its own GL code). ``rounding`` is new in Norm and left
#: for the settings panel.
PAYROLL_MAPPING_KEYS = {
    "wages_payable": "wages_payable",
    "payroll_taxes_payable": "payroll_taxes_payable",
    "unpaid_wages": "unpaid_wages",
    "security_owed": "security_owed",
    "intercompany_wages": "intercompany_wages",
    "pnl_bar_floor": "bar_floor",
    "pnl_kitchen": "kitchen",
    "pnl_general_servers": "other_team",
    "pnl_salaries": "salaries",
    "pnl_site_manager_bonus": "site_manager_bonus",
    "pnl_leave_entitlements": "leave_entitlements",
    "pnl_entertainers": "entertainers",
    "entertainment_expense": "entertainment_expense",
    "pnl_gratuities_reclass": "gratuities_reclass",
}
NORM_PAYROLL_KEYS = tuple(PAYROLL_MAPPING_KEYS.values()) + ("rounding",)


def xero_mapping_blocks(
    mappings: list[dict],
    contacts: list[dict],
    tenant_venue: dict[str, str],
    venue_map: dict[str, str],
) -> tuple[dict[str, dict], Counter]:
    """Per Norm venue, the two ``xero_mapping`` blocks the plan names, from
    Orbit's per-venue account rows and per-tenant counterparty contacts.
    Returns ``(blocks, dropped)`` where dropped counts the rows not carried
    and why — a mapping is settings, so nothing here is silent either."""
    blocks: dict[str, dict] = {}
    dropped: Counter = Counter()
    for m in mappings:
        venue = venue_map.get(m.get("venue_id"))
        if not venue:
            dropped["venue not in Norm"] += 1
            continue
        key = PAYROLL_MAPPING_KEYS.get(str(m.get("line_type")))
        if key is None:
            dropped[f"line type not carried: {m.get('line_type')}"] += 1
            continue
        code = _clean(m.get("xero_account_code"))
        if not code:
            dropped["no account code"] += 1
            continue
        block = blocks.setdefault(
            venue, {"payroll": {}, "intercompany": {"contacts": {}}}
        )
        block["payroll"][key] = {
            "code": code,
            "name": _clean(m.get("xero_account_name")),
        }
    for venue, block in blocks.items():
        wages = block["payroll"].get("intercompany_wages")
        leave = block["payroll"].get("leave_entitlements")
        if wages:
            block["intercompany"]["wages_account"] = wages["code"]
        if leave:
            block["intercompany"]["leave_account"] = leave["code"]
    for c in contacts:
        venue = venue_map.get(tenant_venue.get(c.get("xero_tenant_id")))
        counterparty = venue_map.get(c.get("counterparty_venue_id"))
        if not venue:
            dropped["contact: tenant's venue not in Norm"] += 1
            continue
        if not counterparty:
            dropped["contact: counterparty venue not in Norm"] += 1
            continue
        block = blocks.setdefault(
            venue, {"payroll": {}, "intercompany": {"contacts": {}}}
        )
        block["intercompany"]["contacts"][counterparty] = {
            "contact_id": _clean(c.get("xero_contact_id")),
            "name": _clean(c.get("xero_contact_name")),
        }
    return blocks, dropped


#: ``finance_budgets.line_item`` → ``fin_budget_lines.line_item`` and whether
#: the value is a ratio or dollars (D33: the four lines not carried are
#: ``intercompany_fees``, ``finance_costs``, ``income_tax`` (no rows) and
#: ``rebates`` in dollars — rebates are kept as a percentage only).
BUDGET_LINE_ITEMS = {
    "wage_pct": ("wage_pct", "ratio"),
    "wage_amount": ("wage_cents", "cents"),
    "total_cogs_pct": ("cogs_pct", "ratio"),
    "food_cogs_pct": ("food_cogs_pct", "ratio"),
    "bev_cogs_pct": ("bev_cogs_pct", "ratio"),
    "leave_entitlements": ("leave_cents", "cents"),
    "operating_expenses": ("opex_cents", "cents"),
    "depreciation": ("depreciation_cents", "cents"),
    "rebates_pct": ("rebates_pct", "ratio"),
}


def email_history(performer: dict, shifts: list[dict]) -> list[dict]:
    """Orbit's send dates as Emails-page history: one row per known send, the
    kind and recipient Orbit's function targeted, the send number where
    Orbit kept a count. No copy of any email exists — these are dates."""
    out: list[dict] = []

    def add(kind, recipient, at, *, performance=None, number=1, count=None):
        ts = _ts(at)
        if ts is None:
            return
        out.append(
            {
                "kind": kind,
                "recipient_kind": recipient,
                "sent_at": ts,
                "send_number": number,
                "send_count": count,
                "performance": performance,
            }
        )

    add("invite", "performer", performer.get("invite_sent_at"))
    reminders = int(performer.get("reminder_count") or 0)
    if reminders:
        add(
            "invite_reminder",
            "performer",
            performer.get("last_reminder_sent_at"),
            number=reminders,
            count=reminders,
        )
    for s in shifts:
        n = int(s.get("confirmation_email_send_count") or 0)
        first, last = (
            s.get("confirmation_email_first_sent_at"),
            s.get("confirmation_email_last_sent_at"),
        )
        if first:
            add(
                "confirm_request",
                "performer",
                first,
                performance=s,
                number=1,
                count=n or 1,
            )
        if last and n > 1 and _ts(last) != _ts(first):
            add("confirm_reminder", "performer", last, performance=s, number=n, count=n)
        n = int(s.get("sales_manager_email_send_count") or 0)
        first, last = (
            s.get("sales_manager_email_first_sent_at"),
            s.get("sales_manager_email_last_sent_at"),
        )
        if first:
            add(
                "manager_approval",
                "closing_manager",
                first,
                performance=s,
                number=1,
                count=n or 1,
            )
        if last and n > 1 and _ts(last) != _ts(first):
            add(
                "manager_approval",
                "closing_manager",
                last,
                performance=s,
                number=n,
                count=n,
            )
        if s.get("mismatch_email_sent_at"):
            # Orbit's mismatch function wrote to the performer and to the
            # venue's managers in one go (send-entertainer-mismatch-email).
            add("mismatch", "performer", s["mismatch_email_sent_at"], performance=s)
            add("mismatch", "venue_manager", s["mismatch_email_sent_at"], performance=s)
    return out


def gst_audit(shifts: list[dict], performers: dict[str, dict]) -> list[dict]:
    """02 §13 item 6: bills Orbit pushed with GST (``INPUT2`` keyed off the
    payment method alone) for performers recorded as not GST-registered —
    each one a bill the accountant may need to correct. Ids only."""
    out = []
    for s in shifts:
        if not s.get("xero_bill_id"):
            continue
        p = performers.get(s.get("entertainer_id")) or {}
        if _clean(p.get("payment_method")) == "invoice_company" and not p.get(
            "is_gst_registered"
        ):
            out.append(
                {
                    "shift_id": s["id"],
                    "performer_id": p.get("id"),
                    "date": str(s.get("shift_date")),
                    "xero_bill_id": s["xero_bill_id"],
                    "tenant": s.get("xero_bill_tenant_id"),
                }
            )
    return out


# ===========================================================================
# The report
# ===========================================================================

#: The 18-Oct-2026 figures the plan quotes (§6.4) — shown beside each gate
#: for the reader. Every count is a moving one; none of these decides anything.
PLAN_BASELINE = {
    "ace": "135 ACE weeks ≈ $1.86m",
    "pushes": "81 live journals / 433 live ICO documents, $443,176.68 of ICO",
    "adjustments": "Σ adjustments $151,611.49",
    "leave": "leave $142,946.54 requested / $115,135.41 paid over 321 payouts",
    "gratuities": "gratuities $16,970.94",
    "budget": "FY2027 $14.22m",
    "entertainers": "162 performers / 484 performances / 1,244 changes / 8 Xero contacts / 93 bills / 135 invoice files",
}


@dataclass
class ImportReport:
    """What a run read, wrote, dropped and decided — printed every run and
    returned as a dict (``as_dict``) for ``--report-json``. People appear by
    name only in the D30 list (the owner needs them); every other list holds
    ids and dates."""

    org: dict = field(default_factory=dict)
    dry_run: bool = False
    modules: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    written: dict = field(default_factory=dict)
    venues: dict = field(default_factory=dict)
    people: dict = field(default_factory=dict)
    tallies: dict = field(default_factory=lambda: defaultdict(Counter))
    examples: dict = field(default_factory=lambda: defaultdict(list))
    collisions: list = field(default_factory=list)
    decisions: dict = field(default_factory=dict)
    xero_mapping: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    outcome: str = ""

    def _src(self, table: str) -> dict:
        return self.sources.setdefault(
            table, {"read": 0, "orbit_count": None, "imported": 0, "dropped": Counter()}
        )

    def read(self, table: str, n: int, orbit_count: int | None = None) -> None:
        src = self._src(table)
        src["read"] = n
        src["orbit_count"] = orbit_count

    def imported(self, table: str, n: int = 1) -> None:
        self._src(table)["imported"] += n

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
                    "modules": self.modules,
                    "outcome": self.outcome,
                    "venues": self.venues,
                    "people": self.people,
                    "sources": self.sources,
                    "written": self.written,
                    "collisions": self.collisions,
                    "tallies": {k: dict(v) for k, v in self.tallies.items()},
                    "examples": dict(self.examples),
                    "decisions": self.decisions,
                    "xero_mapping": self.xero_mapping,
                    "gate": self.gate,
                },
                default=str,
            )
        )

    def lines(self, *, people: bool = True) -> list[str]:
        out = [
            f"Orbit → Norm Finance — {self.org.get('name')} ({self.org.get('id')})"
            f"{' — DRY RUN' if self.dry_run else ''}",
            "modes: "
            + ", ".join(f"{m} {v}" for m, v in (self.org.get("modes") or {}).items()),
            "modules: " + ", ".join(self.modules),
            "",
            "venues:",
        ]
        out += [f"  {line}" for line in self.venues.get("mapped", [])]
        out += [f"  skipped: {line}" for line in self.venues.get("skipped", [])]
        out += [f"  NOT MAPPED: {line}" for line in self.venues.get("unmapped", [])]
        p = self.people
        if p:
            out += [
                "",
                f"people (D30): {p.get('referenced', 0)} Orbit team members referenced as authors or approvers, "
                f"{p.get('matched', 0)} matched to Norm users by email, {len(p.get('unmatched', {}))} without a Norm login",
            ]
            if people:
                for name, refs in sorted(
                    p.get("unmatched", {}).items(), key=lambda kv: -kv[1]
                ):
                    out.append(f"  no Norm user: {name} ×{refs}")
                for name, refs in sorted(
                    p.get("matched_names", {}).items(), key=lambda kv: -kv[1]
                ):
                    out.append(f"  matched: {name} ×{refs}")
        out += [
            "",
            f"{'Orbit table':<40}{'read':>7}{'count':>8}{'imported':>10}{'dropped':>9}",
        ]
        for table, s in self.sources.items():
            dropped = sum(s["dropped"].values())
            count = "" if s["orbit_count"] is None else s["orbit_count"]
            out.append(
                f"{table:<40}{s['read']:>7}{count!s:>8}{s['imported']:>10}{dropped:>9}"
            )
            for reason, n in sorted(s["dropped"].items(), key=lambda kv: -kv[1]):
                out.append(f"{'':<6}- {n} {reason}")
        out += ["", "Norm rows:"]
        out += [
            f"  {t:<34}{w.get('insert', 0):>7} new{w.get('update', 0):>7} updated{w.get('delete', 0):>7} deleted{w.get('kept', 0):>5} kept"
            for t, w in self.written.items()
        ]
        if self.collisions:
            out += ["", "collisions with rows authored in Norm (left as they are):"]
            out += [f"  {c}" for c in self.collisions]
        for section, counts in self.tallies.items():
            out += ["", f"{section}:"]
            for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                out.append(f"  {n:>6}  {key}")
        if self.xero_mapping:
            out += [
                "",
                "xero_mapping blocks that WOULD be set (the Xero connection row per venue):",
            ]
            for venue, block in self.xero_mapping.items():
                pay = block.get("payroll", {})
                contacts = block.get("intercompany", {}).get("contacts", {})
                missing = [k for k in NORM_PAYROLL_KEYS if k not in pay]
                out.append(
                    f"  {venue}: {len(pay)} payroll accounts, {len(contacts)} counterparty contacts"
                    + (f"; missing {', '.join(missing)}" if missing else "")
                    + f"; {block.get('status', 'reported only')}"
                )
        if self.decisions:
            out += ["", "for a person to decide:"]
            for key, items in self.decisions.items():
                if isinstance(items, dict):
                    out.append(f"  {key}: {len(items)}")
                    for k, v in list(items.items())[:60]:
                        out.append(f"    {k}: {v}")
                else:
                    out.append(f"  {key}: {len(items)}")
                    for item in items[:60]:
                        out.append(f"    {item}")
                    if len(items) > 60:
                        out.append(
                            f"    … and {len(items) - 60} more (see --report-json)"
                        )
        if self.examples:
            out += ["", "examples:"]
            for key, values in self.examples.items():
                out.append(f"  {key}: " + " | ".join(values))
        if self.gate:
            out += [
                "",
                f"sanity gates: {'PASSED' if self.gate.get('passed') else 'FAILED'}",
            ]
            for c in self.gate["checks"]:
                out.append(
                    f"  {'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}"
                )
                if c.get("baseline"):
                    out.append(f"       18 Oct 2026 baseline: {c['baseline']}")
        if self.outcome:
            out += ["", self.outcome]
        return out


# ===========================================================================
# The plan: rows per Norm table, every column, deterministic ids
# ===========================================================================


def _models() -> dict:
    """Table name → model, from the three model modules F1 owns. Resolved at
    run time so the transform layer imports (and its tests run) before the
    models exist; a run without them refuses with the list."""
    missing = []
    found: dict = {}
    for module_name in ("payroll_models", "entertainers_models", "finance_models"):
        try:
            module = __import__(f"app.db.{module_name}", fromlist=["*"])
        except ImportError:
            missing.append(module_name)
            continue
        for value in vars(module).values():
            table = getattr(value, "__tablename__", None)
            if isinstance(table, str) and hasattr(value, "__table__"):
                found[table] = value
    if missing:
        raise ImportRefused("model modules not present yet: " + ", ".join(missing))
    return found


#: Tables per module — what a module's mode guards and what its stale-row
#: delete pass may touch. ``pay_xero_pushes`` is shared: the entertainer-bill
#: rows belong to the entertainers module (``kind = entertainer_bill``).
MODULE_TABLES = {
    "payroll": (
        "pay_entities",
        "pay_weeks",
        "pay_ace_inputs",
        "pay_adjustments",
        "pay_accruals",
        "pay_xero_pushes",
        "pay_leave_payouts",
        "pay_gratuity_batches",
        "pay_gratuity_allocations",
    ),
    "entertainers": (
        "ent_performers",
        "ent_band_members",
        "ent_series",
        "ent_performances",
        "ent_performance_changes",
        "ent_performer_xero_contacts",
        "ent_files",
        "ent_email_links",
        "pay_xero_pushes",
    ),
    "finance": (
        "fin_budget_versions",
        "fin_budget_days",
        "fin_budget_lines",
        "fin_forecast_drivers",
    ),
}

#: Never deleted by the stale pass: content-addressed, only ever added to.
_NEVER_DELETED = frozenset({"ent_files", "pay_files"})

#: Natural keys a Norm-authored row may already hold. A planned Orbit row on
#: one of these is a collision: left out, reported.
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "pay_entities": ("venue_id",),
    "pay_weeks": ("venue_id", "week_start"),
    "pay_ace_inputs": ("week_id",),
    "pay_accruals": ("week_id",),
    "pay_leave_payouts": ("loaded_request_id", "week_start"),
    "pay_xero_pushes": ("idempotency_key",),
    "ent_performer_xero_contacts": ("performer_id", "xero_tenant_id"),
    "fin_budget_days": ("version_id", "date"),
    "fin_budget_lines": ("venue_id", "fiscal_year", "month", "line_item"),
}


class _Plan:
    def __init__(self, org_id: str):
        self.org_id = org_id
        self.rows: dict[str, dict[str, dict]] = defaultdict(dict)

    def add(self, table: str, row: dict) -> dict:
        if row["id"] in self.rows[table]:
            raise RuntimeError(f"importer bug: {table} id {row['id']} planned twice")
        row["organization_id"] = self.org_id
        self.rows[table][row["id"]] = row
        return row

    def count(self, table: str) -> int:
        return len(self.rows.get(table, {}))

    def complete(self, models: dict) -> None:
        """Give every row every column — executemany needs one shape per
        table — from the column's Python default, and refuse a NOT NULL hole
        or a column the model lacks here, by name, rather than mid-insert."""
        problems: list[str] = []
        for table, rows in self.rows.items():
            model = models.get(table)
            if model is None:
                problems.append(f"{table}: no model")
                continue
            columns = model.__table__.c
            unknown: set[str] = set()
            for row in rows.values():
                unknown |= set(row) - set(columns.keys())
                for col in columns:
                    # A missing key, or an explicit None on a NOT NULL column
                    # (an Orbit blank), takes the column's own default: an
                    # explicit NULL would reach the database as one.
                    if row.get(col.name) is not None or (
                        col.name in row and col.nullable
                    ):
                        continue
                    default = col.default
                    if default is not None and default.is_scalar:
                        row[col.name] = default.arg
                    elif default is not None and default.is_callable:
                        row[col.name] = default.arg(None)
                    elif "version" == col.name:
                        row[col.name] = 1
                    else:
                        row[col.name] = None
            if unknown:
                problems.append(f"{table} has no column {sorted(unknown)}")
                continue
            for row in rows.values():
                for col in columns:
                    if row[col.name] is None and not col.nullable:
                        problems.append(f"{table}.{col.name} is NULL on {row['id']}")
                        break
        if problems:
            raise ImportRefused(
                "the plan does not fit the models: " + "; ".join(problems[:12])
            )


_SKIP = object()


class _Transform:
    """Orbit rows in, planned Norm rows out. Pure apart from the venue,
    connection and member lookups the caller passes in."""

    def __init__(
        self,
        *,
        org,
        modes: dict,
        modules,
        venues: list,
        company_venues: dict[str, str],
        xero_venues: set[str],
        members: dict[str, str],
        data: dict,
        report: ImportReport,
        skip_venues=(),
        now: dt.datetime,
        legacy_weeks: dict[str, dt.date] | None = None,
        file_ids: dict[str, str] | None = None,
    ):
        self.org = org
        self.org_id = org.id
        self.modes = modes
        self.modules = list(modules)
        self.now = now
        self.data = data
        self.report = report
        self.plan = _Plan(org.id)
        self.org_tz = ZoneInfo(
            getattr(org, "default_timezone", None) or "Pacific/Auckland"
        )
        self.fy_start = int(getattr(org, "fiscal_year_start_month", None) or 4)
        self.norm_venues = {v.id: v for v in venues}
        self.company_venues = company_venues  # loaded company id -> norm venue id
        self.xero_venues = xero_venues  # norm venues holding a Xero connection
        self.members = members  # lower(email) -> user id
        self.team = {t["id"]: t for t in data.get("team_members", [])}
        self._people: dict[str, dict] = {}
        self.venue_map: dict[str, str] = {}
        self.orbit_venue: dict[str, dict] = {}
        self.skipped_venues: set[str] = set()
        self.unmapped: list[str] = []
        self.legacy_weeks = legacy_weeks or {}
        self.file_ids = file_ids or {}  # signed url (no token) -> ent_files id
        self.wanted_files: dict[str, list] = defaultdict(list)  # url -> performance ids
        self.invoice_facts: dict[str, dict] = {}  # url (no token) -> number, cents
        self.tenant_venue = {
            c["xero_tenant_id"]: c["venue_id"] for c in data.get("xero_connections", [])
        }
        self.week_ids: dict[tuple[str, dt.date], str] = {}
        self.performer_ids: dict[str, str] = {}  # orbit id -> norm id
        self.performance_ids: dict[str, str] = {}
        self.decisions: dict[str, list | dict] = defaultdict(list)
        self.sums: dict = {}
        self.xero_blocks: dict[str, dict] = {}
        self._map_venues(skip_venues)

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

    def person(self, ref, fallback_name=None) -> tuple[str | None, str | None]:
        """``(norm_user_id, label)`` for an Orbit team member id (or a bare
        name Orbit stored): the Norm user who is a member of this org with
        the same email, else the name as a label. Every reference is counted
        for the D30 list."""
        if not ref and not fallback_name:
            return None, None
        tm = self.team.get(ref) if ref else None
        if tm is None and ref and not fallback_name and ref not in self.team:
            # Not a team member id: Orbit stored a name (or an auth uid).
            fallback_name = str(ref) if not _looks_like_uuid(ref) else None
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
            ref or f"name:{name}", {"name": name, "user": user_id, "refs": 0}
        )
        entry["refs"] += 1
        return (user_id, None) if user_id else (None, name)

    def people_summary(self) -> dict:
        unmatched: Counter = Counter()
        matched: Counter = Counter()
        for entry in self._people.values():
            (matched if entry["user"] else unmatched)[entry["name"]] += entry["refs"]
        return {
            "referenced": len(self._people),
            "matched": sum(1 for e in self._people.values() if e["user"]),
            "unmatched": dict(unmatched),
            "matched_names": dict(matched),
        }

    def venue(self, orbit_venue_id, table: str):
        """The Norm venue for an Orbit venue id; ``_SKIP`` (drop recorded)
        when that venue is not imported."""
        norm = self.venue_map.get(orbit_venue_id)
        if norm:
            return norm
        skipped = orbit_venue_id in self.skipped_venues
        self.report.dropped(
            table, "venue skipped (--skip-venue)" if skipped else "venue not in Norm"
        )
        return _SKIP

    def tz(self, norm_venue_id: str | None) -> ZoneInfo:
        v = self.norm_venues.get(norm_venue_id) if norm_venue_id else None
        return ZoneInfo(v.timezone) if v is not None and v.timezone else self.org_tz

    def day_start(self, norm_venue_id: str | None) -> dt.time:
        v = self.norm_venues.get(norm_venue_id) if norm_venue_id else None
        return _time(getattr(v, "day_start_time", None)) or dt.time(7, 0)

    def week_id(self, venue_id: str, week_start: dt.date) -> str:
        """The pay week row for (venue, Monday) — made on first use, so a
        manual line or push for a week Orbit never opened still has one."""
        key = (venue_id, week_start)
        if key not in self.week_ids:
            row = self.plan.add(
                "pay_weeks",
                {
                    "id": self.id("pay_weeks", f"{venue_id}:{week_start.isoformat()}"),
                    "venue_id": venue_id,
                    "week_start": week_start,
                    "notes": None,
                    "closed_at": None,
                    "closed_by": None,
                    "version": 1,
                },
            )
            self.stamp(row, None)
            self.week_ids[key] = row["id"]
        return self.week_ids[key]

    def _map_venues(self, skip_venues) -> None:
        """Orbit venue → Norm venue on Loaded company id (03 §15.3). An Orbit
        venue with data and no Norm venue is listed as unmapped — the gate
        refuses unless it was skipped on purpose."""
        skip = {str(s).strip().lower() for s in skip_venues}
        mapped, skipped, unmapped = [], [], []
        for v in self.data.get("venues", []):
            self.orbit_venue[v["id"]] = v
            name = v.get("name") or v["id"]
            if name.lower() in skip or str(v.get("slug") or "").lower() in skip:
                self.skipped_venues.add(v["id"])
                skipped.append(name)
                continue
            norm = self.company_venues.get(str(v.get("loadedhub_company_id") or ""))
            if norm:
                self.venue_map[v["id"]] = norm
                mapped.append(f"{name} → {self.norm_venues[norm].name}")
            else:
                self.unmapped.append(name)
                unmapped.append(
                    f"{name} (Loaded company {str(v.get('loadedhub_company_id') or '—')[:8]}…)"
                )
        self.report.venues = {
            "mapped": mapped,
            "skipped": skipped,
            "unmapped": unmapped,
        }

    # ---- the run -------------------------------------------------------------

    def run(self) -> _Plan:
        if "payroll" in self.modules:
            self.pay_entities()
            self.pay_weeks_and_inputs()
            self.adjustments()
            self.accruals()
            self.journal_pushes()
            self.ico_pushes()
            self.mappings()
            self.leave_payouts()
            self.gratuities()
        if "entertainers" in self.modules:
            self.performers()
            self.band_members()
            self.series_and_performances()
            self.time_changes()
            self.performer_xero_contacts()
            self.bills()
            self.emails()
        if "finance" in self.modules:
            self.budgets()
            self.budget_lines()
            self.drivers()
        self.report.people = self.people_summary()
        self.report.decisions = {k: v for k, v in self.decisions.items() if v}
        return self.plan

    # ---- payroll -------------------------------------------------------------

    def pay_entities(self) -> None:
        for v in self.data.get("venues", []):
            norm = self.venue_map.get(v["id"])
            if not norm:
                continue
            codes = v.get("allowance_codes") or {}
            row = self.plan.add(
                "pay_entities",
                {
                    "id": self.id("pay_entities", norm),
                    "venue_id": norm,
                    "loaded_company_id": _clean(v.get("loadedhub_company_id")),
                    "ace_gratuity_allowance_code": _clean(
                        codes.get("gratuities") if isinstance(codes, dict) else None
                    ),
                    "payroll_on": True,
                },
            )
            self.stamp(row, v.get("created_at"), v.get("updated_at"))
            self.report.imported("venues")

    def pay_weeks_and_inputs(self) -> None:
        for w in self.data.get("wage_recon_weeks", []):
            venue = self.venue(w["venue_id"], "wage_recon_weeks")
            start = week_start_from_ending(w.get("week_ending"))
            if venue is _SKIP:
                continue
            if start is None:
                self.report.dropped(
                    "wage_recon_weeks", "no week ending", example=w["id"]
                )
                continue
            week_id = self.week_id(venue, start)
            row = self.plan.rows["pay_weeks"][week_id]
            row["notes"] = _clean(w.get("notes"))
            row["closed_at"] = _ts(w.get("reconciled_at"))
            row["closed_by"], _label = self.person(w.get("reconciled_by"))
            self.stamp(row, w.get("created_at"), w.get("updated_at"))
            self.report.imported("wage_recon_weeks")
        ace_by_week: dict[tuple, int] = {}
        for a in self.data.get("wage_recon_ace_inputs", []):
            venue = self.venue(a["venue_id"], "wage_recon_ace_inputs")
            start = week_start_from_ending(a.get("week_ending"))
            if venue is _SKIP:
                continue
            if start is None:
                self.report.dropped(
                    "wage_recon_ace_inputs", "no week ending", example=a["id"]
                )
                continue
            week_id = self.week_id(venue, start)
            gross = to_cents(a.get("gross_wages")) or 0
            row = self.plan.add(
                "pay_ace_inputs",
                {
                    "id": self.id("pay_ace_inputs", week_id),
                    "week_id": week_id,
                    "gross_cents": gross,
                    "net_cents": to_cents(a.get("net_wages")) or 0,
                    "esct_cents": to_cents(a.get("esct")) or 0,
                    "kiwisaver_cents": to_cents(a.get("kiwisaver")) or 0,
                    "entered_by": None,
                    "entered_at": _ts(a.get("updated_at")) or _ts(a.get("created_at")),
                    "source": SOURCE,
                    "rehearsal": False,
                    "version": 1,
                },
            )
            self.stamp(row, a.get("created_at"), a.get("updated_at"))
            ace_by_week[(a["venue_id"], str(a.get("week_ending")))] = gross
            if to_cents(a.get("gst")):
                self.report.tally("notes", "ACE input with a GST figure (not carried)")
            self.report.imported("wage_recon_ace_inputs")
        self.sums["ace_gross"] = ace_by_week

    def adjustments(self) -> None:
        total = 0
        for m in self.data.get("wage_recon_manual_lines", []):
            venue = self.venue(m["venue_id"], "wage_recon_manual_lines")
            start = week_start_from_ending(m.get("week_ending"))
            if venue is _SKIP:
                continue
            if start is None:
                self.report.dropped(
                    "wage_recon_manual_lines", "no week ending", example=m["id"]
                )
                continue
            amount = to_cents(m.get("amount")) or 0
            if _clean(m.get("category")) not in (None, "addition"):
                self.report.tally(
                    "notes", f"adjustment category {m.get('category')} (kept as signed)"
                )
            row = self.plan.add(
                "pay_adjustments",
                {
                    "id": self.id("pay_adjustments", m["id"]),
                    "week_id": self.week_id(venue, start),
                    "label": _clean(m.get("label")) or "Adjustment",
                    "amount_cents": amount,
                    "xero_account_code": _clean(m.get("xero_account_code")),
                    "xero_account_name": _clean(m.get("xero_account_name")),
                    "report_target": _clean(m.get("report_target")),
                    "notes": _clean(m.get("notes")),
                    "sort_order": int(m.get("sort_index") or 0),
                    "created_by": None,
                    "source": SOURCE,
                    "rehearsal": False,
                    "version": 1,
                },
            )
            self.stamp(row, m.get("created_at"), m.get("updated_at"))
            total += amount
            self.report.imported("wage_recon_manual_lines")
        self.sums["adjustments_cents"] = total

    def accruals(self) -> None:
        for a in self.data.get("wage_recon_accrued_pay", []):
            venue = self.venue(a["venue_id"], "wage_recon_accrued_pay")
            start = week_start_from_ending(a.get("week_ending"))
            if venue is _SKIP:
                continue
            if start is None:
                self.report.dropped(
                    "wage_recon_accrued_pay", "no week ending", example=a["id"]
                )
                continue
            week_id = self.week_id(venue, start)
            row = self.plan.add(
                "pay_accruals",
                {
                    "id": self.id("pay_accruals", week_id),
                    "week_id": week_id,
                    "opening_cents": to_cents(a.get("opening_balance")),
                    "accrued_cents": to_cents(a.get("additions")) or 0,
                    "paid_cents": to_cents(a.get("deductions")) or 0,
                    "source": SOURCE,
                },
            )
            self.stamp(row, a.get("created_at"), a.get("updated_at"))
            for col in ("holiday_pay", "sick_pay", "public_holiday_pay"):
                if to_cents(a.get(col)):
                    self.report.tally("notes", f"accrual with {col} (not carried)")
            self.report.imported("wage_recon_accrued_pay")

    def _push(
        self,
        *,
        key,
        kind,
        venue,
        week_start,
        week_id,
        counterparty,
        performance,
        tenant,
        xero_id,
        xero_number,
        status,
        request,
        response,
        pushed_by,
        pushed_at,
        voided_at,
        created,
        updated,
    ) -> dict | None:
        if not xero_id:
            self.report.tally(
                "pushes", f"{kind} without a Xero id (kept, listed for the gate)"
            )
        user_id, label = self.person(pushed_by)
        row = self.plan.add(
            "pay_xero_pushes",
            {
                "id": self.id("pay_xero_pushes", key),
                "week_id": week_id,
                "draft_id": None,
                "kind": kind,
                "venue_id": venue,
                "week_start": week_start,
                "counterparty_venue_id": counterparty,
                "entertainer_performance_id": performance,
                "xero_tenant_id": tenant,
                "xero_id": xero_id,
                "xero_number": xero_number,
                "xero_status": status,
                "request_payload": request,
                "response": response,
                "idempotency_key": key,
                "pushed_by": user_id,
                "pushed_at": _ts(pushed_at) or _ts(created),
                "voided_at": _ts(voided_at),
                "voided_by": None,
                "source": SOURCE,
            },
        )
        if label:
            row["response"] = {**(row["response"] or {}), "pushed_by_label": label}
        return self.stamp(row, created, updated)

    def journal_pushes(self) -> None:
        live, debit = 0, 0
        for p in self.data.get("wage_recon_xero_pushes", []):
            venue = self.venue(p["venue_id"], "wage_recon_xero_pushes")
            start = week_start_from_ending(p.get("week_ending"))
            if venue is _SKIP:
                continue
            if start is None:
                self.report.dropped(
                    "wage_recon_xero_pushes", "no week ending", example=p["id"]
                )
                continue
            payload = p.get("payload") if isinstance(p.get("payload"), dict) else {}
            totals = {
                "debit_cents": to_cents(p.get("total_debit")),
                "credit_cents": to_cents(p.get("total_credit")),
            }
            self._push(
                key=f"orbit:wage_recon_xero_pushes:{p['id']}",
                kind="journal",
                venue=venue,
                week_start=start,
                week_id=self.week_id(venue, start),
                counterparty=None,
                performance=None,
                tenant=_clean(p.get("xero_tenant_id")),
                xero_id=_clean(p.get("xero_manual_journal_id")),
                xero_number=None,
                status=_clean(p.get("status")),
                request=payload.get("top"),
                response={
                    "built": payload.get("built"),
                    "totals": totals,
                    "last_synced_at": p.get("last_synced_at"),
                },
                pushed_by=p.get("pushed_by"),
                pushed_at=p.get("pushed_at"),
                voided_at=p.get("voided_at"),
                created=p.get("created_at"),
                updated=p.get("updated_at"),
            )
            if p.get("entitlements_manual_journal_id"):
                self.report.tally(
                    "notes",
                    "journal push with an entitlements journal id (not carried)",
                )
            if not p.get("voided_at"):
                live += 1
                debit += totals["debit_cents"] or 0
            self.report.imported("wage_recon_xero_pushes")
        self.sums["journals"] = {"live": live, "debit_cents": debit}

    def ico_pushes(self) -> None:
        live, total = 0, 0
        for p in self.data.get("wage_recon_xero_intercompany_pushes", []):
            venue = self.venue(p["venue_id"], "wage_recon_xero_intercompany_pushes")
            if venue is _SKIP:
                continue
            counterparty = self.venue_map.get(p.get("counterparty_venue_id"))
            if not counterparty:
                self.report.dropped(
                    "wage_recon_xero_intercompany_pushes",
                    "counterparty venue not in Norm",
                    example=str(p.get("counterparty_venue_id"))[:8],
                )
                continue
            start = week_start_from_ending(p.get("week_ending"))
            if start is None:
                self.report.dropped(
                    "wage_recon_xero_intercompany_pushes",
                    "no week ending",
                    example=p["id"],
                )
                continue
            direction = _clean(p.get("direction"))
            if direction not in ("invoice", "bill"):
                self.report.dropped(
                    "wage_recon_xero_intercompany_pushes",
                    f"direction {direction!r}",
                    example=p["id"],
                )
                continue
            payload = p.get("payload") if isinstance(p.get("payload"), dict) else {}
            totals = {
                "subtotal_cents": to_cents(p.get("subtotal")),
                "gst_cents": to_cents(p.get("gst")),
                "total_cents": to_cents(p.get("total")),
            }
            self._push(
                key=f"orbit:wage_recon_xero_intercompany_pushes:{p['id']}",
                kind="ico_invoice" if direction == "invoice" else "ico_bill",
                venue=venue,
                week_start=start,
                week_id=self.week_id(venue, start),
                counterparty=counterparty,
                performance=None,
                tenant=_clean(p.get("xero_tenant_id")),
                xero_id=_clean(p.get("xero_invoice_id")),
                xero_number=_clean(
                    ((payload.get("request") or {}).get("InvoiceNumber"))
                ),
                status=_clean(p.get("status")),
                request=payload.get("request"),
                response={
                    "response": payload.get("response"),
                    "totals": totals,
                    "last_synced_at": p.get("last_synced_at"),
                },
                pushed_by=p.get("pushed_by"),
                pushed_at=p.get("pushed_at"),
                voided_at=p.get("voided_at"),
                created=p.get("created_at"),
                updated=p.get("updated_at"),
            )
            if not p.get("voided_at"):
                live += 1
                total += totals["total_cents"] or 0
            self.report.imported("wage_recon_xero_intercompany_pushes")
        self.sums["ico"] = {"live": live, "total_cents": total}

    def mappings(self) -> None:
        blocks, dropped = xero_mapping_blocks(
            self.data.get("xero_account_mappings", []),
            self.data.get("xero_intercompany_contacts", []),
            self.tenant_venue,
            self.venue_map,
        )
        for reason, n in dropped.items():
            table = (
                "xero_intercompany_contacts"
                if reason.startswith("contact")
                else "xero_account_mappings"
            )
            self.report.dropped(table, reason, n)
        carried_m = len(self.data.get("xero_account_mappings", [])) - sum(
            n for r, n in dropped.items() if not r.startswith("contact")
        )
        carried_c = len(self.data.get("xero_intercompany_contacts", [])) - sum(
            n for r, n in dropped.items() if r.startswith("contact")
        )
        self.report.imported("xero_account_mappings", carried_m)
        self.report.imported("xero_intercompany_contacts", carried_c)
        for venue, block in blocks.items():
            block["status"] = (
                "venue has a Xero connection in Norm"
                if venue in self.xero_venues
                else "NO Xero connection in Norm yet — written when the venue connects"
            )
        self.xero_blocks = blocks
        self.report.xero_mapping = {
            self.norm_venues[v].name: b
            for v, b in blocks.items()
            if v in self.norm_venues
        }

    def leave_payouts(self) -> None:
        """Orbit's ``::week::`` keys split into (request, week); the 100
        legacy rows keyed by request id only get their week from Loaded's day
        lines when the caller resolved them (``legacy_weeks``), else from
        when the row was keyed: payroll keyed every payout on the Tuesday
        AFTER the pay week (227 of 231 weekly keys sit exactly seven days
        after their week; 98 of 100 legacy rows were created on a Tuesday —
        Orbit data, 20 Oct 2026), so the pay week is the one before the week
        the row was created in. Weekly rows are placed first; a legacy row
        landing on a week a weekly row already holds is superseded (Orbit's
        own lookup prefers the weekly key), counted for the gate and listed
        for a person."""
        requested, paid = 0, 0
        seen: dict[tuple, str] = {}
        legacy: dict[str, str] = {}
        superseded: list[str] = []
        superseded_sum = [0, 0]
        rows = self.data.get("leave_request_payouts", [])
        ordered = [r for r in rows if PAYOUT_WEEK_SEPARATOR in str(r["request_id"])]
        ordered += [
            r for r in rows if PAYOUT_WEEK_SEPARATOR not in str(r["request_id"])
        ]
        for p in ordered:
            venue = self.venue(p["venue_id"], "leave_request_payouts")
            if venue is _SKIP:
                continue
            loaded_id, week = split_payout_key(p["request_id"])
            source = SOURCE
            if week is None:
                resolved = self.legacy_weeks.get(loaded_id)
                keyed = local_week_of(
                    _ts(p.get("created_at")), self.tz(venue), self.day_start(venue)
                )
                week = resolved or (keyed - dt.timedelta(days=7) if keyed else None)
                source = SOURCE_LEGACY
                legacy[loaded_id] = (
                    f"week {week} from Loaded day lines"
                    if resolved
                    else f"week {week}: the pay week before it was keyed ({keyed})"
                )
            if week is None:
                self.report.dropped(
                    "leave_request_payouts", "no pay week", example=loaded_id
                )
                continue
            nat = (loaded_id, week)
            req, pay = (
                to_cents(p.get("requested_amount")) or 0,
                to_cents(p.get("paid_amount")) or 0,
            )
            if nat in seen:
                self.report.dropped(
                    "leave_request_payouts",
                    "legacy row superseded by the weekly row for the same request and week",
                    example=loaded_id,
                )
                superseded.append(
                    f"{loaded_id} week {week}: legacy {_money(req)} / {_money(pay)} "
                    f"set aside for the weekly row — check it is not another week's payout"
                )
                superseded_sum[0] += req
                superseded_sum[1] += pay
                continue
            seen[nat] = p["request_id"]
            user_id, label = self.person(p.get("updated_by"))
            inputs = (
                p.get("workflow_inputs")
                if isinstance(p.get("workflow_inputs"), dict)
                else None
            )
            kind = workflow_kind(p.get("leave_type"))
            bond = None
            if (
                kind == "annual"
                and inputs
                and req > (to_cents(inputs.get("max_payable")) or req)
            ):
                bond = to_cents(inputs.get("uniform_bond"))
            row = self.plan.add(
                "pay_leave_payouts",
                {
                    "id": self.id(
                        "pay_leave_payouts", f"{loaded_id}:{week.isoformat()}"
                    ),
                    "venue_id": venue,
                    "week_start": week,
                    "loaded_request_id": loaded_id,
                    "staff_member_id": _clean(p.get("staff_member_id")),
                    "staff_name": _clean(p.get("staff_member_name")),
                    "leave_type": _clean(p.get("leave_type")),
                    "requested_cents": req,
                    "paid_cents": pay,
                    "bond_deducted_cents": bond,
                    "bond_consent_file_id": None,
                    "workflow_kind": kind,
                    "workflow_inputs": inputs,
                    "notes": _clean(p.get("notes")),
                    "decided_by": user_id,
                    "decided_at": _ts(p.get("updated_at")) or _ts(p.get("created_at")),
                    "source": source,
                    "rehearsal": False,
                    "version": 1,
                },
            )
            if label:
                row["notes"] = (row["notes"] + "\n" if row["notes"] else "") + (
                    f"Decided in Orbit by {label}."
                )
            self.stamp(row, p.get("created_at"), p.get("updated_at"))
            requested += req
            paid += pay
            self.report.imported("leave_request_payouts")
        self.sums["leave"] = {
            "rows": len(seen),
            "requested_cents": requested,
            "paid_cents": paid,
            "superseded_rows": len(superseded),
            "superseded_requested_cents": superseded_sum[0],
            "superseded_paid_cents": superseded_sum[1],
        }
        if legacy:
            self.decisions[
                "legacy leave payouts (pay week inferred, source=orbit_legacy)"
            ] = legacy
        if superseded:
            self.decisions["legacy leave payouts set aside for a weekly row"] = (
                superseded
            )

    def gratuities(self) -> None:
        batches: dict[str, str] = {}
        pools: dict[str, int] = {}
        for b in self.data.get("payrun_gratuity_batches", []):
            venue = self.venue(b["venue_id"], "payrun_gratuity_batches")
            if venue is _SKIP:
                continue
            start = _date(b.get("week_start"))
            period_start = _date(b.get("period_start"))
            if start is None or period_start is None:
                self.report.dropped(
                    "payrun_gratuity_batches", "no week or period", example=b["id"]
                )
                continue
            total = to_cents(b.get("total_amount")) or 0
            row = self.plan.add(
                "pay_gratuity_batches",
                {
                    "id": self.id("pay_gratuity_batches", b["id"]),
                    "venue_id": venue,
                    "week_start": start,
                    "period": period_start.strftime("%Y-%m"),
                    "total_cents": total,
                    "gl_code": _clean(b.get("gl_code")),
                    "notes": _clean(b.get("notes")),
                    "created_by": None,
                    "source": SOURCE,
                    "rehearsal": False,
                },
            )
            self.stamp(row, b.get("created_at"), b.get("updated_at"))
            batches[b["id"]] = row["id"]
            pools[b["id"]] = total
            self.report.imported("payrun_gratuity_batches")
        per_batch: dict[str, int] = defaultdict(int)
        for a in self.data.get("payrun_gratuity_allocations", []):
            batch = batches.get(a.get("batch_id"))
            if not batch:
                self.report.dropped(
                    "payrun_gratuity_allocations", "batch not imported", example=a["id"]
                )
                continue
            amount = to_cents(a.get("amount")) or 0
            payroll_venue = self.venue_map.get(a.get("payroll_venue_id"))
            if a.get("payroll_venue_id") and not payroll_venue:
                self.report.tally(
                    "gratuities",
                    "share paid by a venue not in Norm (payroll venue left blank)",
                )
            row = self.plan.add(
                "pay_gratuity_allocations",
                {
                    "id": self.id("pay_gratuity_allocations", a["id"]),
                    "batch_id": batch,
                    "staff_member_id": _clean(a.get("staff_member_id")),
                    "staff_name": _clean(a.get("staff_name")),
                    "pay_code": _clean(a.get("pay_code")),
                    "hours": _hours(a.get("hours")),
                    "amount_cents": amount,
                    "is_payable": bool(a.get("is_active", True)),
                    "payroll_venue_id": payroll_venue,
                    "excluded_at": None,
                    "excluded_by": None,
                },
            )
            self.stamp(row, a.get("created_at"))
            per_batch[a["batch_id"]] += amount
            self.report.imported("payrun_gratuity_allocations")
        self.sums["gratuities"] = {
            "allocated_cents": sum(per_batch.values()),
            "per_batch": dict(per_batch),
            "pools": pools,
        }
        # Three of Orbit's nine batches do not add up to their pool (20 Oct
        # 2026: $13.43, $53.55 and $51.19 short; the rest within 2c). That is
        # Orbit's own record, carried as it is and put to a person — the
        # gate compares shares with shares.
        off_pool = [
            f"batch {b[:8]}… ({self.norm_venues[self.plan.rows['pay_gratuity_batches'][batches[b]]['venue_id']].name}, "
            f"week {self.plan.rows['pay_gratuity_batches'][batches[b]]['week_start']}): "
            f"shares {_money(per_batch.get(b, 0))} vs pool {_money(pool)}"
            for b, pool in pools.items()
            if abs(per_batch.get(b, 0) - pool) > 10
        ]
        if off_pool:
            self.decisions[
                "gratuity batches whose shares do not add to the pool in Orbit"
            ] = off_pool

    # ---- entertainers --------------------------------------------------------

    def performers(self) -> None:
        unknown_lanes: list[str] = []  # no usable setup type in Orbit (02 §12's 46)
        look_lanes: list[str] = []  # a lane the rule set but flags for a look
        spellings: dict[str, str] = {}
        for p in self.data.get("entertainers", []):
            lane, reason = lane_for(p)
            code, problem = normalise_tax_code(p.get("tax_code"))
            raw_code = _clean(p.get("tax_code"))
            if raw_code is not None:
                spellings[raw_code] = (
                    f"→ {code}" + (f" ({problem})" if problem else "")
                    if code
                    else f"UNKNOWN ({problem})"
                )
            review = [
                r for r in (reason, problem, _clean(p.get("admin_review_reason"))) if r
            ]
            if p.get("needs_admin_review") and not review:
                review.append("flagged for review in Orbit")
            performer_type = _clean(p.get("performer_type"))
            if performer_type not in ("solo", "band", "agent"):
                performer_type = (
                    "band" if _clean(p.get("setup_type")) == "band_leader" else "solo"
                )
            status = (
                "accepted"
                if _clean(p.get("onboarding_status")) == "complete"
                else "invited"
            )
            declaration = (
                p.get("declaration_snapshot")
                if isinstance(p.get("declaration_snapshot"), dict)
                else None
            )
            if declaration is None and (
                p.get("declaration_signed_at") or p.get("declaration_signature")
            ):
                declaration = {
                    "wording_version": p.get("declaration_wording_version"),
                    "signature": p.get("declaration_signature"),
                    "signed_at": p.get("declaration_signed_at"),
                    "ip": p.get("declaration_ip"),
                    "user_agent": p.get("declaration_user_agent"),
                    "source": SOURCE,
                }
            wt_rate_type = _clean(p.get("wt_rate_type"))
            if wt_rate_type not in ("standard", "self_selected", "tailored"):
                wt_rate_type = None
            row = self.plan.add(
                "ent_performers",
                {
                    "id": self.id("ent_performers", p["id"]),
                    "name": _clean(p.get("name")) or "Unnamed performer",
                    "legal_name": _clean(p.get("legal_name")),
                    "trading_name": _clean(p.get("trading_name")),
                    "email": _email(p.get("email")),
                    "phone": _clean(p.get("phone")),
                    "lane": lane,
                    "performer_type": performer_type,
                    "band_mode": "leader" if performer_type == "band" else None,
                    "ird_number": _clean(p.get("ird_number")),
                    "bank_account": _clean(p.get("bank_account_number")),
                    "date_of_birth": _clean(p.get("date_of_birth")),
                    "tax_code": code,
                    "kiwisaver_rate": _ratio(p.get("kiwisaver_rate")),
                    "payroll_code": _clean(p.get("payroll_code")),
                    "gst_registered": bool(p.get("is_gst_registered")),
                    "gst_number": _clean(p.get("gst_number")),
                    "company_name": _clean(p.get("company_name")),
                    "company_number": _clean(p.get("company_number")),
                    "exemption_certificate_number": _clean(
                        p.get("exemption_certificate_number")
                    ),
                    "exemption_expiry_date": _date(p.get("exemption_expiry_date")),
                    "exemption_file_id": None,
                    "nz_tax_resident": p.get("nz_tax_resident")
                    if isinstance(p.get("nz_tax_resident"), bool)
                    else None,
                    "wt_rate_type": wt_rate_type,
                    "wt_rate_bp": _bp(p.get("wt_rate")),
                    "declaration_snapshot": declaration,
                    "declaration_file_id": None,
                    "band_leader_declaration_at": _ts(
                        p.get("band_leader_declaration_at")
                    ),
                    "contractor_confirmations": p.get("contractor_confirmations")
                    if isinstance(p.get("contractor_confirmations"), dict)
                    else None,
                    "onboarding_status": status,
                    "submitted_at": _ts(p.get("updated_at"))
                    if status == "accepted"
                    else None,
                    "accepted_at": _ts(p.get("updated_at"))
                    if status == "accepted"
                    else None,
                    "accepted_by": None,
                    "bank_changed_at": None,
                    "needs_review": bool(review),
                    "review_reason": "; ".join(review) if review else None,
                    "invite_sent_at": _ts(p.get("invite_sent_at")),
                    "invite_reminders_sent": int(p.get("reminder_count") or 0),
                    "last_reminder_at": _ts(p.get("last_reminder_sent_at")),
                    "is_active": bool(p.get("is_active", True)),
                    "notes": _clean(p.get("notes")),
                    "created_by": None,
                    "version": 1,
                },
            )
            self.stamp(row, p.get("created_at"), p.get("updated_at"))
            self.performer_ids[p["id"]] = row["id"]
            self.report.tally("performer lanes", lane)
            if reason:
                entry = f"{p['id'][:8]}… → {lane} ({reason})"
                if _clean(p.get("setup_type")) in (None, "unknown"):
                    unknown_lanes.append(entry)
                else:
                    look_lanes.append(entry)
            self.report.imported("entertainers")
        if unknown_lanes:
            self.decisions[
                "performers whose lane needs confirming: no setup type in Orbit (02 §12)"
            ] = unknown_lanes
        if look_lanes:
            self.decisions[
                "performers whose lane needs confirming: set up one way, paid another"
            ] = look_lanes
        if spellings:
            self.decisions["tax-code spellings in Orbit → Norm's code"] = dict(
                sorted(spellings.items())
            )

    def band_members(self) -> None:
        """Orbit's members-split flow collected members and stopped (0 rows
        today; its columns are not documented). Any row that exists becomes
        an invited member with an equal share — finance edits the shares."""
        by_band: dict[str, list[dict]] = defaultdict(list)
        for m in self.data.get("entertainer_group_members", []):
            band = self.performer_ids.get(
                m.get("group_entertainer_id")
                or m.get("entertainer_id")
                or m.get("group_id")
            )
            if not band:
                self.report.dropped(
                    "entertainer_group_members",
                    "band performer not imported",
                    example=str(m.get("id"))[:8],
                )
                continue
            by_band[band].append(m)
        for band, members in by_band.items():
            share, remainder = divmod(10000, len(members))
            for i, m in enumerate(members):
                row = self.plan.add(
                    "ent_band_members",
                    {
                        "id": self.id(
                            "ent_band_members",
                            m.get("id") or f"{band}:{m.get('email')}",
                        ),
                        "band_performer_id": band,
                        "member_performer_id": None,
                        "name": _clean(m.get("name") or m.get("member_name"))
                        or "Band member",
                        "email": _email(m.get("email") or m.get("member_email")),
                        "phone": _clean(m.get("phone") or m.get("member_phone")),
                        "share_bp": share + (remainder if i == 0 else 0),
                        "invite_status": "invited",
                        "sort_order": i,
                    },
                )
                self.stamp(row, m.get("created_at"), m.get("updated_at"))
                self.report.imported("entertainer_group_members")
            self.plan.rows["ent_performers"][band]["band_mode"] = "members_split"

    def series_and_performances(self) -> None:
        shifts = self.data.get("entertainer_shifts", [])
        by_series: dict[str, list[dict]] = defaultdict(list)
        for s in shifts:
            if s.get("recurrence_series_id") and self.performer_ids.get(
                s.get("entertainer_id")
            ):
                by_series[s["recurrence_series_id"]].append(s)
        series_ids: dict[str, str] = {}
        for orbit_series, rows in by_series.items():
            venue = self.venue_map.get(rows[0]["venue_id"])
            if not venue:
                continue
            rule = series_rule(rows)
            notes = [
                f"Reconstructed from {rule['occurrences']} Orbit bookings (series {orbit_series[:8]}…)."
            ]
            if len(rule["weekdays"]) > 1:
                self.report.tally(
                    "series",
                    "series spanning several weekdays (the most common kept as the rule's day)",
                )
                notes.append(
                    "Orbit booked it on several weekdays; the most common one is the rule's day."
                )
            if rule["interval_weeks"] > 1:
                # Norm's rule is weekly; a fortnightly Orbit series keeps its
                # bookings (each a performance) and says so here.
                self.report.tally(
                    "series",
                    f"every {rule['interval_weeks']} weeks in Orbit (Norm's rule is weekly; noted on the series)",
                )
                notes.append(f"Orbit repeated it every {rule['interval_weeks']} weeks.")
            row = self.plan.add(
                "ent_series",
                {
                    "id": self.id("ent_series", orbit_series),
                    "venue_id": venue,
                    "performer_id": self.performer_ids[rows[0]["entertainer_id"]],
                    "weekday": rule["weekday"],
                    "start_time": rule["start_time"],
                    "end_time": rule["end_time"],
                    "ends_next_day": bool(
                        rule["start_time"]
                        and rule["end_time"]
                        and rule["end_time"] <= rule["start_time"]
                    ),
                    "rate_cents": _rate(
                        rule["agreed_cents"],
                        hours_between(rule["start_time"], rule["end_time"]),
                    ),
                    "starts_on": rule["starts_on"],
                    "ends_on": rule["ends_on"],
                    "materialised_through": rule["ends_on"],
                    "notes": " ".join(notes),
                    "archived_at": None,
                    "created_by": None,
                    "version": 1,
                },
            )
            self.stamp(row, min(r.get("created_at") or "" for r in rows) or None)
            series_ids[orbit_series] = row["id"]
        bills = 0
        for s in shifts:
            venue = self.venue(s["venue_id"], "entertainer_shifts")
            if venue is _SKIP:
                continue
            performer = self.performer_ids.get(s.get("entertainer_id"))
            if not performer:
                self.report.dropped(
                    "entertainer_shifts", "performer not imported", example=s["id"][:8]
                )
                continue
            day = _date(s.get("shift_date"))
            if day is None:
                self.report.dropped(
                    "entertainer_shifts", "no date", example=s["id"][:8]
                )
                continue
            start, end = _time(s.get("start_time")), _time(s.get("end_time"))
            hours = hours_between(start, end)
            agreed = to_cents(s.get("agreed_amount")) or 0
            status = _clean(s.get("performance_status")) or "scheduled"
            if status not in ("scheduled", "approved", "did_not_perform"):
                self.report.tally(
                    "notes", f"performance status {status!r} read as scheduled"
                )
                status = "scheduled"
            approved_by, approver_label = self.person(s.get("approved_by"))
            cm_user = self.members.get(_email(s.get("sales_manager_email")) or "")
            has_cm = any(
                s.get(k)
                for k in (
                    "sales_manager_loadedhub_id",
                    "sales_manager_name",
                    "sales_manager_email",
                )
            )
            invoice_url = _clean(s.get("entertainer_invoice_url"))
            file_id = None
            if invoice_url:
                canonical = invoice_url.split("?", 1)[0]
                file_id = self.file_ids.get(canonical)
                self.wanted_files[invoice_url].append(
                    self.id("ent_performances", s["id"])
                )
                # The invoice's own number and amount live on the file row; a
                # file covering several performances takes the first stated.
                self.invoice_facts.setdefault(canonical, {})
                for src_key, dst_key in (
                    ("entertainer_invoice_number", "invoice_number"),
                    ("entertainer_invoice_amount", "invoice_cents"),
                ):
                    value = (
                        _clean(s.get(src_key))
                        if dst_key == "invoice_number"
                        else to_cents(s.get(src_key))
                    )
                    if (
                        value is not None
                        and dst_key not in self.invoice_facts[canonical]
                    ):
                        self.invoice_facts[canonical][dst_key] = value
            actual_start, actual_end = (
                _time(s.get("actual_start_time")),
                _time(s.get("actual_end_time")),
            )
            perf_start, perf_end = (
                _time(s.get("entertainer_actual_start_time")),
                _time(s.get("entertainer_actual_end_time")),
            )
            row = self.plan.add(
                "ent_performances",
                {
                    "id": self.id("ent_performances", s["id"]),
                    "venue_id": venue,
                    "performer_id": performer,
                    "series_id": series_ids.get(s.get("recurrence_series_id")),
                    "performance_date": day,
                    "start_time": start,
                    "end_time": end,
                    "ends_next_day": bool(start and end and end <= start),
                    "rate_cents": _rate(agreed, hours),
                    "agreed_cents": agreed,
                    "actual_start_time": actual_start,
                    "actual_end_time": actual_end,
                    "actual_ends_next_day": bool(
                        actual_start and actual_end and actual_end <= actual_start
                    ),
                    "actual_cents": to_cents(s.get("actual_amount")),
                    "performance_status": status,
                    "approved_at": _ts(s.get("approved_at")),
                    "approved_by": approved_by,
                    "dnp_reason": _clean(s.get("did_not_perform_reason")),
                    "performer_actual_start_time": perf_start,
                    "performer_actual_end_time": perf_end,
                    "performer_actual_ends_next_day": bool(
                        perf_start and perf_end and perf_end <= perf_start
                    ),
                    "performer_confirmed_at": _ts(s.get("entertainer_confirmed_at")),
                    "performer_dnp": bool(s.get("entertainer_did_not_perform")),
                    "performer_dnp_reason": _clean(
                        s.get("entertainer_did_not_perform_reason")
                    ),
                    "closing_manager_staff_id": _clean(
                        s.get("sales_manager_loadedhub_id")
                    ),
                    "closing_manager_name": _clean(s.get("sales_manager_name")),
                    "closing_manager_email": _email(s.get("sales_manager_email")),
                    "closing_manager_user_id": cm_user,
                    "closing_manager_source": SOURCE if has_cm else None,
                    "closing_manager_resolved_at": _ts(
                        s.get("sales_manager_email_first_sent_at")
                    )
                    or (_ts(s.get("updated_at")) if has_cm else None),
                    "payment_status": "paid"
                    if _clean(s.get("payment_status")) == "paid"
                    else "pending",
                    "paid_at": _ts(s.get("paid_at")),
                    "paid_by": None,
                    "paid_reference": _clean(s.get("paid_reference")),
                    "payroll_week_start": _date(s.get("paid_payrun_week_start")),
                    "invoice_file_id": file_id,
                    "notes": None,
                    "created_by": None,
                    "version": 1,
                },
            )
            if approver_label:
                self.report.tally(
                    "closing manager",
                    "approved in Orbit by someone without a Norm login",
                )
            if has_cm:
                self.report.tally(
                    "closing manager",
                    "stored by Orbit (source=orbit)"
                    + ("" if cm_user else " — no Norm user for the email"),
                )
            self.stamp(row, s.get("created_at"), s.get("updated_at"))
            self.performance_ids[s["id"]] = row["id"]
            self.report.tally("performances", f"{status} / {row['payment_status']}")
            if s.get("xero_bill_id"):
                bills += 1
            self.report.imported("entertainer_shifts")
        self.sums["entertainers"] = {
            "performers": len(self.performer_ids),
            "performances": len(self.performance_ids),
            "bills": bills,
            "invoice_urls": len(self.wanted_files),
        }
        self.decisions[
            "GST audit: bills pushed with GST for performers not GST-registered (02 §13 item 6)"
        ] = [
            f"performance {x['shift_id'][:8]}… on {x['date']} (performer {str(x['performer_id'])[:8]}…, bill {x['xero_bill_id'][:8]}…)"
            for x in gst_audit(
                shifts, {p["id"]: p for p in self.data.get("entertainers", [])}
            )
        ]

    def time_changes(self) -> None:
        for c in self.data.get("entertainer_shift_time_changes", []):
            performance = self.performance_ids.get(c.get("shift_id"))
            if not performance:
                self.report.dropped(
                    "entertainer_shift_time_changes",
                    "performance not imported",
                    example=str(c.get("id"))[:8],
                )
                continue
            source = _clean(c.get("changed_by_source")) or "system"
            changed_by_source = {
                "manager": "manager",
                "entertainer": "performer",
                "system": "system",
            }.get(source, "system")
            user_id, label = (None, None)
            if source == "manager":
                user_id, label = self.person(
                    c.get("changed_by_user_id"), c.get("changed_by_name")
                )
            else:
                label = _clean(c.get("changed_by_name"))
            row = self.plan.add(
                "ent_performance_changes",
                {
                    "id": self.id("ent_performance_changes", c["id"]),
                    "performance_id": performance,
                    "changed_at": _ts(c.get("changed_at"))
                    or dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc),
                    "changed_by_source": changed_by_source,
                    "changed_by_user_id": user_id,
                    "changed_by_name": label,
                    "field": _clean(c.get("field")) or "unknown",
                    "old_value": _clean(c.get("old_value")),
                    "new_value": _clean(c.get("new_value")),
                    "note": _clean(c.get("note")),
                    "ip_hash": None,
                    "user_agent": None,
                },
            )
            self.stamp(row, c.get("changed_at"))
            self.report.imported("entertainer_shift_time_changes")
        self.sums["entertainers"]["changes"] = self.plan.count(
            "ent_performance_changes"
        )

    def performer_xero_contacts(self) -> None:
        seen: set[tuple] = set()
        for c in self.data.get("entertainer_xero_contacts", []):
            performer = self.performer_ids.get(c.get("entertainer_id"))
            if not performer:
                self.report.dropped(
                    "entertainer_xero_contacts",
                    "performer not imported",
                    example=str(c.get("id"))[:8],
                )
                continue
            nat = (performer, c.get("xero_tenant_id"))
            if (
                nat in seen
                or not c.get("xero_tenant_id")
                or not c.get("xero_contact_id")
            ):
                self.report.dropped(
                    "entertainer_xero_contacts",
                    "duplicate or incomplete",
                    example=str(c.get("id"))[:8],
                )
                continue
            seen.add(nat)
            row = self.plan.add(
                "ent_performer_xero_contacts",
                {
                    "id": self.id("ent_performer_xero_contacts", c["id"]),
                    "performer_id": performer,
                    "xero_tenant_id": _clean(c.get("xero_tenant_id")),
                    "xero_contact_id": _clean(c.get("xero_contact_id")),
                    "xero_contact_name": _clean(c.get("xero_contact_name")),
                },
            )
            self.stamp(row, c.get("created_at"), c.get("updated_at"))
            self.report.imported("entertainer_xero_contacts")
        self.sums["entertainers"]["xero_contacts"] = len(seen)

    def bills(self) -> None:
        n = 0
        for s in self.data.get("entertainer_shifts", []):
            performance = self.performance_ids.get(s["id"])
            if not performance or not s.get("xero_bill_id"):
                continue
            venue = self.venue_map[s["venue_id"]]
            day = _date(s.get("shift_date"))
            self._push(
                key=f"orbit:entertainer_shifts:{s['id']}:bill",
                kind="entertainer_bill",
                venue=venue,
                week_start=_date(s.get("paid_payrun_week_start")) or week_start_of(day),
                week_id=None,
                counterparty=None,
                performance=performance,
                tenant=_clean(s.get("xero_bill_tenant_id")),
                xero_id=_clean(s.get("xero_bill_id")),
                xero_number=_clean(s.get("xero_bill_number")),
                status=_clean(s.get("xero_bill_status")),
                request=None,
                response=None,
                pushed_by=None,
                pushed_at=s.get("xero_bill_pushed_at"),
                voided_at=None,
                created=s.get("xero_bill_pushed_at") or s.get("created_at"),
                updated=s.get("updated_at"),
            )
            n += 1
        self.report.tally("pushes", f"entertainer bills carried: {n}")

    def emails(self) -> None:
        shifts_by_performer: dict[str, list[dict]] = defaultdict(list)
        for s in self.data.get("entertainer_shifts", []):
            if s["id"] in self.performance_ids:
                shifts_by_performer[s["entertainer_id"]].append(s)
        n = 0
        for p in self.data.get("entertainers", []):
            performer = self.performer_ids.get(p["id"])
            if not performer:
                continue
            for i, e in enumerate(
                email_history(p, shifts_by_performer.get(p["id"], []))
            ):
                s = e.pop("performance")
                performance_id = self.performance_ids.get(s["id"]) if s else None
                key = f"{p['id']}:{e['kind']}:{e['recipient_kind']}:{s['id'] if s else '-'}:{e['send_number']}"
                recipient_email = None
                if e["recipient_kind"] == "performer":
                    recipient_email = _email(p.get("email"))
                elif e["recipient_kind"] == "closing_manager" and s:
                    recipient_email = _email(s.get("sales_manager_email"))
                subject = EMAIL_KIND_LABELS.get(e["kind"], e["kind"].replace("_", " "))
                if e.get("send_count") and e["send_count"] > 1:
                    subject += f" — send {e['send_number']} of {e['send_count']}"
                row = self.plan.add(
                    "ent_email_links",
                    {
                        "id": self.id("ent_email_links", key),
                        "email_log_id": None,
                        "performer_id": performer,
                        "band_member_id": None,
                        "venue_id": self.venue_map.get(s["venue_id"]) if s else None,
                        "recipient_kind": e["recipient_kind"],
                        "kind": e["kind"],
                        "recipient_email": recipient_email,
                        "subject": subject + " (sent from Orbit; date only, no copy)",
                        "performance_ids": [performance_id] if performance_id else [],
                        "sent_at": e["sent_at"],
                        "source": SOURCE,
                    },
                )
                self.stamp(row, e["sent_at"])
                n += 1
                self.report.tally(
                    "email history", f"{e['kind']} → {e['recipient_kind']}"
                )
        self.sums["entertainers"]["emails"] = n

    # ---- files (after the transform: needs Orbit again) -----------------------

    def resolve_files(
        self, source: OrbitSource | None, existing_by_sha: dict[str, str], log=print
    ) -> None:
        """Invoice files: one ``ent_files`` row per distinct signed URL, bytes
        fetched once (content-addressed per org; a file Norm already holds —
        by URL or by hash — is reused, never re-fetched). A signed URL that
        no longer answers (they expire after a year) is counted; the
        performance keeps its number and amount and no file."""
        if not self.wanted_files:
            return
        if source is None:
            self.report.tally(
                "files",
                f"invoice files not copied (--no-files): {len(self.wanted_files)} URLs",
            )
            return
        fetched, reused, failed = 0, 0, 0
        planned_by_sha: dict[str, str] = {}  # the same bytes under two URLs
        for url, performance_ids in self.wanted_files.items():
            canonical = url.split("?", 1)[0]
            file_id = self.file_ids.get(canonical)
            if file_id is None:
                try:
                    body, content_type = source.fetch_signed(url)
                except OrbitReadError as exc:
                    failed += 1
                    self.report.tally(
                        "files",
                        "invoice file unavailable (signed URL expired or refused)",
                    )
                    self.report.example("invoice file unavailable", str(exc)[:80])
                    continue
                content_type = _safe_file_type(body)
                if content_type is None:
                    # Orbit's bucket allowed public uploads, so its bytes get
                    # the portal's check before Norm serves them inline
                    # (security review, 20 Oct 2026): a PDF with scripts or
                    # a form, or something that is neither a PDF nor an
                    # image, is left behind — the performance keeps its
                    # number and amount.
                    failed += 1
                    self.report.tally(
                        "files", "invoice file refused (not a plain PDF or image)"
                    )
                    self.report.example("invoice file refused", canonical[-80:])
                    continue
                sha = hashlib.sha256(body).hexdigest()
                file_id = existing_by_sha.get(sha) or planned_by_sha.get(sha)
                if file_id is None:
                    file_id = self.id("ent_files", sha)
                    planned_by_sha[sha] = file_id
                    filename = (
                        urllib.parse.unquote(canonical.rsplit("/", 1)[-1])[:200]
                        or "invoice"
                    )
                    facts = self.invoice_facts.get(canonical, {})
                    row = self.plan.add(
                        "ent_files",
                        {
                            "id": file_id,
                            "sha256": sha,
                            "kind": "invoice",
                            "filename": filename,
                            "content_type": content_type,
                            "size_bytes": len(body),
                            "data": body,
                            "source_ref": canonical,
                            "uploaded_by_user_id": None,
                            "invoice_number": facts.get("invoice_number"),
                            "invoice_cents": facts.get("invoice_cents"),
                            "check_status": "waived",
                            "extracted": None,
                            "check_findings": None,
                            "checked_at": None,
                            "waived_by": None,
                            "waived_at": None,
                        },
                    )
                    self.stamp(row, None)
                    fetched += 1
                else:
                    reused += 1
                self.file_ids[canonical] = file_id
            else:
                reused += 1
            for pid in performance_ids:
                self.plan.rows["ent_performances"][pid]["invoice_file_id"] = file_id
        if fetched:
            self.report.tally("files", f"invoice files copied from Orbit: {fetched}")
        if reused:
            self.report.tally(
                "files", f"invoice files already in Norm (reused): {reused}"
            )
        self.sums["entertainers"]["files"] = fetched + reused
        self.sums["entertainers"]["files_failed"] = failed
        log(f"  files: {fetched} copied, {reused} reused, {failed} unavailable")

    # ---- finance -------------------------------------------------------------

    def budgets(self) -> None:
        per_version: dict[tuple, list[dict]] = defaultdict(list)
        for b in self.data.get("budgets", []):
            venue = self.venue(b["venue_id"], "budgets")
            if venue is _SKIP:
                continue
            day = _date(b.get("budget_date"))
            amount = to_cents(b.get("financial_budget"))
            if day is None or amount is None:
                self.report.dropped(
                    "budgets", "no date or amount", example=str(b.get("id"))[:8]
                )
                continue
            per_version[(venue, fiscal_year_of(day, self.fy_start))].append(
                (day, amount, b)
            )
        months: dict[tuple, int] = defaultdict(int)
        for (venue, fy), days in sorted(per_version.items()):
            # Locked at the latest creation among its rows: Orbit's mirror
            # rewrites updated_at daily, so created_at is the only date the
            # import itself can be pinned to — and it is the same on every run.
            locked_at = max(
                _ts(b.get("created_at"))
                or dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
                for _d, _a, b in days
            )
            version = self.plan.add(
                "fin_budget_versions",
                {
                    "id": self.id("fin_budget_versions", f"{venue}:{fy}"),
                    "venue_id": venue,
                    "fiscal_year": fy,
                    "label": f"FY{fy} budget (imported from Orbit)",
                    "source": "import",
                    "locked_at": locked_at,
                    "locked_by": None,
                },
            )
            self.stamp(
                version,
                min(
                    _ts(b.get("created_at"))
                    for _d, _a, b in days
                    if _ts(b.get("created_at"))
                )
                or locked_at,
            )
            seen: set[dt.date] = set()
            for day, amount, b in days:
                if day in seen:
                    self.report.dropped(
                        "budgets",
                        "a second row for the same venue and day",
                        example=str(b.get("id"))[:8],
                    )
                    continue
                seen.add(day)
                row = self.plan.add(
                    "fin_budget_days",
                    {
                        "id": self.id(
                            "fin_budget_days", f"{version['id']}:{day.isoformat()}"
                        ),
                        "version_id": version["id"],
                        "date": day,
                        "amount_cents": amount,
                        "note": _clean(b.get("event_note")),
                    },
                )
                self.stamp(row, b.get("created_at"), b.get("updated_at"))
                months[(b["venue_id"], day.strftime("%Y-%m"))] += amount
                self.report.imported("budgets")
            self.report.tally(
                "budget versions",
                f"{self.norm_venues[venue].name} FY{fy}: {len(seen)} days",
            )
        self.sums["budget_months"] = dict(months)

    def budget_lines(self) -> None:
        seen: set[tuple] = set()
        for fb in self.data.get("finance_budgets", []):
            venue = self.venue(fb["venue_id"], "finance_budgets")
            if venue is _SKIP:
                continue
            mapped = BUDGET_LINE_ITEMS.get(str(fb.get("line_item")))
            if mapped is None:
                self.report.dropped(
                    "finance_budgets",
                    f"line item not carried (D33): {fb.get('line_item')}",
                )
                continue
            line_item, kind = mapped
            try:
                fy, month = int(fb.get("fiscal_year")), int(fb.get("month"))
            except (TypeError, ValueError):
                self.report.dropped(
                    "finance_budgets",
                    "no fiscal year or month",
                    example=str(fb.get("id"))[:8],
                )
                continue
            nat = (venue, fy, month, line_item)
            if nat in seen:
                self.report.dropped(
                    "finance_budgets", "duplicate line", example=str(fb.get("id"))[:8]
                )
                continue
            seen.add(nat)
            value = fb.get("value")
            row = self.plan.add(
                "fin_budget_lines",
                {
                    "id": self.id(
                        "fin_budget_lines", f"{venue}:{fy}:{month}:{line_item}"
                    ),
                    "venue_id": venue,
                    "fiscal_year": fy,
                    "month": month,
                    "line_item": line_item,
                    "value_cents": to_cents(value) if kind == "cents" else None,
                    "value_ratio": _ratio(value) if kind == "ratio" else None,
                    "version": 1,
                },
            )
            self.stamp(row, fb.get("created_at"), fb.get("updated_at"))
            self.report.imported("finance_budgets")

    def drivers(self) -> None:
        listed: list[str] = []
        for f in self.data.get("sales_forecast_factors", []):
            venue = self.venue(f["venue_id"], "sales_forecast_factors")
            if venue is _SKIP:
                continue
            day = _date(f.get("factor_date"))
            if day is None:
                self.report.dropped(
                    "sales_forecast_factors", "no date", example=str(f.get("id"))[:8]
                )
                continue
            row = self.plan.add(
                "fin_forecast_drivers",
                {
                    "id": self.id("fin_forecast_drivers", f["id"]),
                    "venue_id": venue,
                    "date": day,
                    "kind": "event",
                    "label": _clean(f.get("label")) or "Event",
                    # Orbit's factor had a direction, never a size. A zero
                    # uplift keeps the label on the day without moving the
                    # number until a person prunes the list and sets one.
                    "uplift_cents": None,
                    "uplift_bp": 0,
                    "created_by": None,
                    "archived_at": None,
                },
            )
            self.stamp(row, f.get("created_at"), f.get("updated_at"))
            listed.append(f"{self.norm_venues[venue].name} {day}: {row['label'][:60]}")
            self.report.imported("sales_forecast_factors")
        if listed:
            self.decisions[
                "event drivers imported with no uplift — prune the ones naming people, then set an uplift"
            ] = listed


#: What the Emails page shows for a history row carried from Orbit.
EMAIL_KIND_LABELS = {
    "invite": "Onboarding invitation",
    "invite_reminder": "Onboarding reminder",
    "confirm_request": "Confirmation request",
    "confirm_reminder": "Confirmation reminder",
    "manager_approval": "Approval request to the closing manager",
    "mismatch": "Times did not match",
}


def _bp(percent) -> int | None:
    """A percentage Orbit stored as a number (20.0) in basis points."""
    if percent is None or percent == "":
        return None
    try:
        return int(
            (Decimal(str(percent)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )
    except InvalidOperation:
        return None


def _looks_like_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _hours(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _ratio(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _rate(agreed_cents: int | None, hours: float | None) -> int | None:
    """Orbit stored ``agreed = rate × clock hours``; Norm stores the rate."""
    if agreed_cents is None or not hours:
        return agreed_cents
    return int(
        Decimal(agreed_cents) / Decimal(str(hours)).quantize(Decimal("0.0001"))
        + Decimal("0.5")
    )


def _safe_file_type(body: bytes) -> str | None:
    """``application/pdf`` for PDF bytes carrying no active content, the
    sniffed type for a JPEG/PNG/GIF/WebP, else None — the type comes from the
    bytes, never from Orbit's header or the file's name. The full ``pypdf``
    parse a portal upload gets is not applied: Orbit's files are what they
    are, and a page that will not parse is a person's problem to look at,
    not a danger to serve."""
    if body[:5] == b"%PDF-":
        from app.services.files.pdf_check import has_active_content

        return None if has_active_content(body) else "application/pdf"
    try:
        from app.services.functions.uploads import sniff_image

        return sniff_image(body)[0]
    except Exception:  # noqa: BLE001 — not an image Norm serves
        return None


# ===========================================================================
# The database side
# ===========================================================================


def resolve_org(db: Session, ref: str) -> Organization:
    org = (
        db.query(Organization)
        .filter((Organization.id == ref) | (Organization.slug == ref))
        .first()
    )
    if org is None:
        raise ImportRefused(f"no organisation with id or slug {ref!r}")
    return org


def org_modes(org) -> dict[str, str]:
    return {m: str(getattr(org, f"{m}_mode", None) or "off") for m in MODULES}


def _members(db: Session, org_id: str) -> dict[str, str]:
    rows = (
        db.query(User.id, User.email)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .filter(OrganizationMembership.organization_id == org_id)
        .all()
    )
    return {e.strip().lower(): uid for uid, e in rows if e}


def _connections(db: Session, venues: list) -> tuple[dict[str, str], set[str]]:
    """``(loaded company id → Norm venue, venues with a Xero connection)``.
    The company id is read from the connection's config; a venue whose
    Loaded connection has none cannot be joined and is reported."""
    ids = [v.id for v in venues]
    company_venues: dict[str, str] = {}
    xero: set[str] = set()
    if not ids:
        return company_venues, xero
    for row in db.query(Connection).filter(
        Connection.venue_id.in_(ids),
        Connection.connector_name.in_(("loadedhub", "xero")),
    ):
        if row.connector_name == "xero":
            xero.add(row.venue_id)
            continue
        company = str((row.config or {}).get("x_loaded_company_id") or "").strip()
        if company:
            company_venues[company] = row.venue_id
    return company_venues, xero


def _existing_files(
    db: Session, models: dict, org_id: str
) -> tuple[dict[str, str], dict[str, str]]:
    """``(source_ref → id, sha256 → id)`` of the org's ``ent_files``."""
    t = models["ent_files"].__table__
    by_ref: dict[str, str] = {}
    by_sha: dict[str, str] = {}
    cols = [t.c.id, t.c.sha256]
    has_ref = "source_ref" in t.c
    if has_ref:
        cols.append(t.c.source_ref)
    for row in db.execute(select(*cols).where(t.c.organization_id == org_id)):
        by_sha[row[1]] = row[0]
        if has_ref and row[2]:
            by_ref.setdefault(row[2], row[0])
    return by_ref, by_sha


def _import_id_clause(t):
    """SQL for "this id was minted by the importer" (UUID5)."""
    return (func.length(t.c.id) == 36) & (func.substr(t.c.id, 15, 1) == "5")


def _collisions(db: Session, models: dict, plan: _Plan, report: ImportReport) -> None:
    """Planned rows whose natural key a Norm-authored row already holds are
    left out and reported; children of a Norm-authored pay week are pointed
    at that week instead. Runs before the writes, inside the transaction."""
    org_id = plan.org_id
    remap_weeks: dict[str, str] = {}
    for table, keys in NATURAL_KEYS.items():
        rows = plan.rows.get(table)
        if not rows or table not in models:
            continue
        t = models[table].__table__
        if any(k not in t.c for k in keys):
            continue
        planned = {tuple(r.get(k) for k in keys): rid for rid, r in rows.items()}
        existing = db.execute(
            select(t.c.id, *[t.c[k] for k in keys]).where(
                t.c.organization_id == org_id, ~_import_id_clause(t)
            )
        ).all()
        for found in existing:
            nat = tuple(found[1:])
            planned_id = planned.get(nat)
            if planned_id is None or planned_id == found[0]:
                continue
            report.collisions.append(
                f"{table} {dict(zip(keys, nat))} — Norm-authored row {found[0][:8]}… kept, Orbit's left out"
            )
            del rows[planned_id]
            if table == "pay_weeks":
                remap_weeks[planned_id] = found[0]
    if remap_weeks:
        for table in (
            "pay_ace_inputs",
            "pay_adjustments",
            "pay_accruals",
            "pay_xero_pushes",
        ):
            for row in plan.rows.get(table, {}).values():
                if row.get("week_id") in remap_weeks:
                    row["week_id"] = remap_weeks[row["week_id"]]
        # The unique-per-week children now carry the Norm week's id: check
        # them again against what Norm holds there.
        for table in ("pay_ace_inputs", "pay_accruals"):
            rows = plan.rows.get(table)
            if not rows or table not in models:
                continue
            t = models[table].__table__
            week_ids = [
                r["week_id"]
                for r in rows.values()
                if r["week_id"] in remap_weeks.values()
            ]
            if not week_ids:
                continue
            taken = {
                r[0]
                for r in db.execute(
                    select(t.c.week_id).where(
                        t.c.organization_id == org_id, t.c.week_id.in_(week_ids)
                    )
                )
            }
            for rid, r in list(rows.items()):
                if r["week_id"] in taken:
                    report.collisions.append(
                        f"{table} for a week keyed in Norm — Norm's row kept, Orbit's left out"
                    )
                    del rows[rid]


def _write(
    db: Session, models: dict, plan: _Plan, modules, report: ImportReport
) -> None:
    """Upsert every planned row by id; delete the org's importer-authored rows
    the plan no longer has (Orbit deleted them), children first, keeping any
    a Norm-authored row still references."""
    from sqlalchemy.exc import IntegrityError

    from app.db.models import Base

    order = [t.name for t in Base.metadata.sorted_tables if t.name in models]
    touched = {t for m in modules for t in MODULE_TABLES[m]}
    org_id = plan.org_id
    for table in order:
        rows = list(plan.rows.get(table, {}).values())
        if not rows:
            continue
        t = models[table].__table__
        ids = list(plan.rows[table])
        present: set[str] = set()
        for i in range(0, len(ids), 5000):
            present |= {
                r[0]
                for r in db.execute(select(t.c.id).where(t.c.id.in_(ids[i : i + 5000])))
            }
        new = [r for r in rows if r["id"] not in present]
        old = [r for r in rows if r["id"] in present]
        for i in range(0, len(new), 1000):
            db.execute(insert(t), new[i : i + 1000])
        for r in old:
            db.execute(update(t).where(t.c.id == r["id"]).values(**r))
        report.written.setdefault(table, {})
        report.written[table]["insert"] = len(new)
        report.written[table]["update"] = len(old)
    for table in reversed(order):
        if table not in touched or table in _NEVER_DELETED:
            continue
        t = models[table].__table__
        keep_ids = set(plan.rows.get(table, {}))
        where = [t.c.organization_id == org_id, _import_id_clause(t)]
        if table == "pay_xero_pushes" and "kind" in t.c:
            kinds = []
            if "payroll" in modules:
                kinds += ["journal", "ico_invoice", "ico_bill"]
            if "entertainers" in modules:
                kinds += ["entertainer_bill"]
            where.append(t.c.kind.in_(kinds))
        stale = [
            r[0]
            for r in db.execute(select(t.c.id).where(*where))
            if r[0] not in keep_ids
        ]
        deleted, kept = 0, 0
        for sid in stale:
            sp = db.begin_nested()
            try:
                db.execute(delete(t).where(t.c.id == sid))
                sp.commit()
                deleted += 1
            except IntegrityError:
                sp.rollback()
                kept += 1
                report.tally(
                    "kept",
                    f"{table}: Orbit row gone but referenced by a Norm-authored row",
                )
        if deleted or kept:
            report.written.setdefault(table, {})
            report.written[table]["delete"] = deleted
            report.written[table]["kept"] = kept


def _db_counts(db: Session, models: dict, plan: _Plan) -> dict[str, tuple[int, int]]:
    out = {}
    for table, rows in plan.rows.items():
        if not rows:
            continue
        t = models[table].__table__
        ids = list(rows)
        found = 0
        for i in range(0, len(ids), 5000):
            found += db.execute(
                select(func.count())
                .select_from(t)
                .where(
                    t.c.organization_id == plan.org_id, t.c.id.in_(ids[i : i + 5000])
                )
            ).scalar()
        out[table] = (len(rows), found)
    return out


def _money(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.2f}"


def _gate(
    transform: _Transform,
    baseline: dict,
    modules,
    counts: dict,
    plan: _Plan,
    report: ImportReport,
) -> dict:
    """Every check compares what the transform planned with what Orbit says
    about itself in the same run, over the venues the run mapped. One
    failure fails the run."""
    checks: list[dict] = []
    sums = transform.sums
    orbit = baseline_totals(baseline, transform.venue_map)

    def check(name, ok, detail, baseline_note=None):
        checks.append(
            {"check": name, "ok": bool(ok), "detail": detail, "baseline": baseline_note}
        )

    for table, s in report.sources.items():
        if s["orbit_count"] is None:
            continue
        slack = max(2, int(s["orbit_count"] * 0.005))
        check(
            f"read {table}",
            abs(s["read"] - s["orbit_count"]) <= slack,
            f"{s['read']} read vs {s['orbit_count']} in Orbit",
        )
    for table, s in report.sources.items():
        if table not in _SOURCE_TABLES:
            continue
        accounted = s["imported"] + sum(s["dropped"].values())
        if accounted != s["read"]:
            check(
                f"accounted {table}",
                False,
                f"{s['read']} read but {accounted} imported/dropped — rows lost without a reason",
            )
    check(
        "every Orbit venue mapped",
        not transform.unmapped,
        "all mapped"
        if not transform.unmapped
        else "not in Norm: "
        + ", ".join(transform.unmapped)
        + " (CBI must exist as a non-trading venue, D1; or --skip-venue)",
    )
    if "payroll" in modules:
        planned = sums.get("ace_gross", {})
        expected = orbit.get("ace_gross", {})
        diff = {
            k for k in set(planned) | set(expected) if planned.get(k) != expected.get(k)
        }
        check(
            "ACE gross per entity-week",
            not diff,
            f"{len(planned)} weeks, {_money(sum(planned.values()))} planned vs "
            f"{_money(sum(expected.values()))} in Orbit"
            + (f"; differs on {len(diff)}" if diff else ""),
            PLAN_BASELINE["ace"],
        )
        pj, oj = sums.get("journals", {}), orbit.get("journals", {})
        pi, oi = sums.get("ico", {}), orbit.get("ico", {})
        check(
            "live journals: count and Σ debit",
            pj.get("live") == oj.get("live")
            and pj.get("debit_cents") == oj.get("debit_cents"),
            f"{pj.get('live')} live, {_money(pj.get('debit_cents'))} planned vs "
            f"{oj.get('live')} live, {_money(oj.get('debit_cents'))} in Orbit",
            PLAN_BASELINE["pushes"],
        )
        check(
            "live ICO documents: count and Σ total",
            pi.get("live") == oi.get("live")
            and pi.get("total_cents") == oi.get("total_cents"),
            f"{pi.get('live')} live, {_money(pi.get('total_cents'))} planned vs "
            f"{oi.get('live')} live, {_money(oi.get('total_cents'))} in Orbit",
            PLAN_BASELINE["pushes"],
        )
        check(
            "every push row has a Xero id",
            oj.get("without_xero_id", 0) == 0 and oi.get("without_xero_id", 0) == 0,
            f"{oj.get('without_xero_id', 0)} journals and {oi.get('without_xero_id', 0)} "
            "ICO rows without one in Orbit",
        )
        live_keys: Counter = Counter(
            (
                r["venue_id"],
                r["week_start"],
                r["kind"],
                r.get("counterparty_venue_id"),
                r.get("entertainer_performance_id"),
            )
            for r in plan.rows.get("pay_xero_pushes", {}).values()
            if not r.get("voided_at")
        )
        dupes = [k for k, n in live_keys.items() if n > 1]
        check(
            "partial unique on live pushes holds",
            not dupes,
            f"{len(dupes)} duplicate live (venue, week, kind, counterparty, performance) keys",
        )
        check(
            "Σ adjustments",
            sums.get("adjustments_cents") == orbit.get("adjustments_cents"),
            f"{_money(sums.get('adjustments_cents'))} planned vs "
            f"{_money(orbit.get('adjustments_cents'))} in Orbit",
            PLAN_BASELINE["adjustments"],
        )
        pl, ol = sums.get("leave", {}), orbit.get("leave", {})
        set_aside = pl.get("superseded_rows", 0)
        check(
            "Σ leave requested / paid, every payout one (venue, request, week)",
            pl.get("requested_cents", 0) + pl.get("superseded_requested_cents", 0)
            == ol.get("requested_cents")
            and pl.get("paid_cents", 0) + pl.get("superseded_paid_cents", 0)
            == ol.get("paid_cents")
            and pl.get("rows", 0) + set_aside == ol.get("rows"),
            f"{pl.get('rows')} payouts {_money(pl.get('requested_cents'))} / "
            f"{_money(pl.get('paid_cents'))} planned"
            + (
                f" + {set_aside} legacy set aside ({_money(pl.get('superseded_requested_cents'))} / "
                f"{_money(pl.get('superseded_paid_cents'))})"
                if set_aside
                else ""
            )
            + f" vs {ol.get('rows')} rows {_money(ol.get('requested_cents'))} / "
            f"{_money(ol.get('paid_cents'))} in Orbit",
            PLAN_BASELINE["leave"],
        )
        pg, og = sums.get("gratuities", {}), orbit.get("gratuities", {})
        batch_diff = [
            b
            for b in set(pg.get("per_batch", {})) | set(og.get("per_batch", {}))
            if pg.get("per_batch", {}).get(b) != og.get("per_batch", {}).get(b)
        ]
        off_pool = sum(
            1
            for b, pool in og.get("pools", {}).items()
            if abs(og.get("per_batch", {}).get(b, 0) - pool) > 10
        )
        check(
            "Σ gratuity allocations, batch by batch",
            pg.get("allocated_cents") == og.get("allocated_cents") and not batch_diff,
            f"{_money(pg.get('allocated_cents'))} planned vs "
            f"{_money(og.get('allocated_cents'))} in Orbit over {len(og.get('pools', {}))} batches"
            + (f"; {len(batch_diff)} differ" if batch_diff else "")
            + (
                f"; {off_pool} batch(es) do not add to their pool in Orbit itself (listed for a person)"
                if off_pool
                else ""
            ),
            PLAN_BASELINE["gratuities"],
        )
    if "entertainers" in modules:
        e = sums.get("entertainers", {})
        src = report.sources

        def carried(table, planned):
            s = src.get(table, {})
            return planned + sum(s.get("dropped", Counter()).values()) == s.get("read")

        check(
            "entertainer rows carried",
            carried("entertainers", e.get("performers", 0))
            and carried("entertainer_shifts", e.get("performances", 0))
            and carried("entertainer_shift_time_changes", e.get("changes", 0)),
            f"{e.get('performers')} performers / {e.get('performances')} performances / "
            f"{e.get('changes')} changes / {e.get('xero_contacts')} Xero contacts / "
            f"{e.get('bills')} bills / {e.get('files', 0)} invoice files "
            f"({e.get('files_failed', 0)} unavailable)",
            PLAN_BASELINE["entertainers"],
        )
    if "finance" in modules:
        planned = sums.get("budget_months", {})
        expected = orbit.get("budget_months", {})
        diff = [
            k for k in set(planned) | set(expected) if planned.get(k) != expected.get(k)
        ]
        check(
            "Σ locked budget per venue-month",
            not diff,
            f"{len(planned)} venue-months, {_money(sum(planned.values()))} planned vs "
            f"{_money(sum(expected.values()))} in Orbit"
            + (f"; differs on {len(diff)}" if diff else ""),
            PLAN_BASELINE["budget"],
        )
    short = {k: v for k, v in counts.items() if v[0] != v[1]}
    check(
        "rows written",
        not short,
        "all planned rows present" if not short else f"missing: {short}",
    )
    return {"passed": all(c["ok"] for c in checks), "checks": checks}


# ===========================================================================
# Xero mapping blocks → Connection.config (reported always, written on request)
# ===========================================================================


def apply_xero_mapping(
    db: Session, blocks: dict[str, dict], *, overwrite: bool = False
) -> dict[str, str]:
    """Merge the planned ``xero_mapping`` blocks into each venue's Xero
    connection row. A key the row already holds is kept unless
    ``overwrite`` — a person may have set it on the Bills panel. Returns
    what happened per venue. The coordinator runs this; the importer's
    nightly run only reports."""
    out: dict[str, str] = {}
    for venue_id, block in blocks.items():
        row = (
            db.query(Connection)
            .filter(
                Connection.connector_name == "xero", Connection.venue_id == venue_id
            )
            .first()
        )
        if row is None:
            out[venue_id] = "no Xero connection row — nothing written"
            continue
        config = dict(row.config or {})
        mapping = dict(config.get("xero_mapping") or {})
        payroll = dict(mapping.get("payroll") or {})
        inter = dict(mapping.get("intercompany") or {})
        contacts = dict(inter.get("contacts") or {})
        set_keys = 0
        for key, value in block.get("payroll", {}).items():
            if overwrite or key not in payroll:
                payroll[key] = value
                set_keys += 1
        for key in ("wages_account", "leave_account"):
            value = block.get("intercompany", {}).get(key)
            if value and (overwrite or key not in inter):
                inter[key] = value
                set_keys += 1
        for counterparty, contact in (
            block.get("intercompany", {}).get("contacts", {}).items()
        ):
            if overwrite or counterparty not in contacts:
                contacts[counterparty] = contact
                set_keys += 1
        inter["contacts"] = contacts
        mapping["payroll"] = payroll
        mapping["intercompany"] = inter
        config["xero_mapping"] = mapping
        row.config = config
        out[venue_id] = f"{set_keys} keys set"
    db.flush()
    return out


# ===========================================================================
# The post-freeze watch query (plan §15, 19 Jan row)
# ===========================================================================

#: Orbit's payroll-input tables: a row created or changed after the freeze
#: means someone keyed the week in Orbit after Norm became the record.
POST_FREEZE_TABLES = (
    ("leave_request_payouts", "request_id", "updated_at"),
    ("payrun_gratuity_batches", "id", "updated_at"),
    ("payrun_gratuity_allocations", "id", "created_at"),
    ("wage_recon_ace_inputs", "id", "updated_at"),
    ("wage_recon_manual_lines", "id", "updated_at"),
    ("wage_recon_accrued_pay", "id", "updated_at"),
    ("wage_recon_xero_pushes", "id", "created_at"),
    ("wage_recon_xero_intercompany_pushes", "id", "created_at"),
    ("entertainer_shifts", "id", "updated_at"),
)


def post_freeze_rows(
    source: OrbitSource, freeze_at: dt.datetime, orbit_venue_ids
) -> dict[str, list[dict]]:
    """Orbit payroll-input rows written after ``freeze_at`` at the given
    Orbit venues: table → rows (ids, venue, week and the timestamp only —
    never a name or an amount; the email says what to look at, not what it
    holds). Allocations have no venue: their batch's is looked up."""
    stamp = freeze_at.astimezone(dt.timezone.utc).isoformat()
    venues = set(orbit_venue_ids)
    out: dict[str, list[dict]] = {}
    batches: dict[str, dict] = {}
    for table, key, ts_col in POST_FREEZE_TABLES:
        rows = source.rows(
            table,
            select="*",
            filters=(f"{ts_col}=gte.{urllib.parse.quote(stamp)}",),
            key=(key,),
        )
        found = []
        for r in rows:
            venue = r.get("venue_id")
            if table == "payrun_gratuity_allocations":
                batch = batches.get(r.get("batch_id"))
                if batch is None:
                    hits = source.rows(
                        "payrun_gratuity_batches",
                        select="id,venue_id,week_start",
                        filters=(f"id=eq.{r.get('batch_id')}",),
                        key=("id",),
                    )
                    batch = batches[r.get("batch_id")] = hits[0] if hits else {}
                venue = batch.get("venue_id")
            if venues and venue not in venues:
                continue
            found.append(
                {
                    "id": str(r.get(key)),
                    "venue_id": venue,
                    "week": str(
                        r.get("week_ending")
                        or r.get("week_start")
                        or r.get("shift_date")
                        or ""
                    ),
                    "at": str(r.get(ts_col)),
                }
            )
        if found:
            out[table] = found
    return out


# ===========================================================================
# The run
# ===========================================================================


@dataclass
class ImportResult:
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
    modules=MODULES,
    dry_run: bool = False,
    skip_venues=(),
    copy_files: bool = True,
    write_xero_mapping: bool = False,
    commit: bool = True,
    now: dt.datetime | None = None,
    legacy_weeks: dict[str, dt.date] | None = None,
    log=print,
) -> ImportResult:
    """Import Orbit into one organisation: read → transform → (one
    transaction) lock, write, gate → commit or roll back.

    The database work runs in a SAVEPOINT, so a dry run or a failed gate
    rolls back to exactly where the caller's session was; ``commit`` then
    commits the session after a passing real run. ``ImportRefused`` (and an
    ``OrbitReadError`` while reading) means nothing was written."""
    modules = [m for m in MODULES if m in set(modules)]
    if not modules:
        raise ImportRefused("no module asked for (payroll, entertainers, finance)")
    now = now or dt.datetime.now(dt.timezone.utc)
    report = ImportReport(dry_run=dry_run, modules=modules)
    target = resolve_org(db, org)
    modes = org_modes(target)
    report.org = {
        "id": target.id,
        "name": target.name,
        "slug": target.slug,
        "modes": modes,
    }
    live = [m for m in modules if modes[m] == "live"]
    if live:
        raise ImportRefused(
            f"{target.name}: {', '.join(live)} is live — Norm is the record; the importer never loads Orbit over a live module."
        )
    models = _models()

    log("reading Orbit…")
    data = read_orbit(source, modules, report, log)
    baseline = read_baseline(source, modules)
    venues = db.query(Venue).filter(Venue.organization_id == target.id).all()
    company_venues, xero_venues = _connections(db, venues)
    files_by_ref, files_by_sha = (
        _existing_files(db, models, target.id)
        if "entertainers" in modules
        else ({}, {})
    )
    transform = _Transform(
        org=target,
        modes=modes,
        modules=modules,
        venues=venues,
        company_venues=company_venues,
        xero_venues=xero_venues,
        members=_members(db, target.id),
        data=data,
        report=report,
        skip_venues=skip_venues,
        now=now,
        legacy_weeks=legacy_weeks,
        file_ids=files_by_ref,
    )
    log("transforming…")
    plan = transform.run()
    if "entertainers" in modules:
        transform.resolve_files(source if copy_files else None, files_by_sha, log)
    plan.complete(models)

    log("writing (one transaction)…")
    nested = db.begin_nested()
    try:
        locked = db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
            {"k": _LOCK_KEY.format(org_id=target.id)},
        ).scalar()
        if not locked:
            raise ImportRefused("another import is running for this organisation")
        _collisions(db, models, plan, report)
        _write(db, models, plan, modules, report)
        db.flush()
        report.gate = _gate(
            transform, baseline, modules, _db_counts(db, models, plan), plan, report
        )
        if write_xero_mapping and transform.xero_blocks:
            applied = apply_xero_mapping(db, transform.xero_blocks)
            for venue_id, what in applied.items():
                name = transform.norm_venues[venue_id].name
                if name in report.xero_mapping:
                    report.xero_mapping[name]["status"] = what
        # Fence: the modes are read again, under a share lock held until
        # commit, so going live cannot slip in between the first check and now.
        fresh = db.execute(
            select(Organization)
            .where(Organization.id == target.id)
            .with_for_update(read=True)
        ).scalar_one()
        if any(org_modes(fresh)[m] == "live" for m in modules):
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
    "ACTOR",
    "BUDGET_LINE_ITEMS",
    "ImportRefused",
    "ImportReport",
    "ImportResult",
    "MODULES",
    "NAMESPACE",
    "OrbitPostgrest",
    "OrbitReadError",
    "PAYROLL_MAPPING_KEYS",
    "apply_xero_mapping",
    "baseline_totals",
    "creds_path",
    "email_history",
    "fiscal_year_of",
    "gst_audit",
    "hours_between",
    "import_id",
    "is_import_id",
    "lane_for",
    "local_week_of",
    "normalise_tax_code",
    "orbit_client",
    "org_modes",
    "post_freeze_rows",
    "run_import",
    "series_rule",
    "split_payout_key",
    "to_cents",
    "week_start_from_ending",
    "week_start_of",
    "workflow_kind",
    "xero_mapping_blocks",
]
