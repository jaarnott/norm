"""The one door a Norm app reaches data through.

A Norm app is user-authored software. Its UI is untrusted markup in a sandboxed
iframe and its logic is untrusted Python in the consolidator sandbox — so
neither of those is the security boundary. **This module is.** Every read and
every write an app performs comes through ``call_action`` and is checked here,
which is why there is exactly one function and no second path.

Four rules, in order, and each exists because of a specific way this could go
wrong:

1. **Audience.** The viewer owns the app, or a share names them (directly, by
   venue, or org-wide). Otherwise the app does not exist as far as they are
   concerned.
2. **Allowlist.** The action must be declared by the VERSION being run. Reach
   is per-version so that editing an app can never widen what an
   already-shared copy may touch — a wider reach means a new version and a
   fresh approval.
3. **Intersection.** Effective permission = the *viewer's* org permissions ∩
   the app's declared scopes. An app can never do more than the person running
   it: sharing to someone junior degrades safely, and an author cannot grant
   themselves reach they do not have.
4. **Writes are opted into twice.** A non-GET action must be declared in the
   version's ``writes`` list AND, for anyone other than the author, approved on
   the share. Default off.

Two deliberate departures from how the rest of Norm behaves, both fail-closed:

- **Venue access is explicit.** ``venue_service.get_user_venues`` fails OPEN (a
  user with no ``UserVenueAccess`` rows is handed every venue on the platform);
  the MCP surface refused to inherit that and so do we. No row, no venue.
- **``strict_venue=True``** on dispatch, so a missing connector config is an
  error rather than a silent fall back to some other venue's credentials.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Scope vocabulary for what an app may declare. Reused from the MCP surface
#: rather than invented: the labels and consent text are already written and
#: already reviewed, and one vocabulary means an app and a Claude connector can
#: never disagree about what "draft purchase orders" permits.
from app.mcp.scopes import MCP_SCOPES  # noqa: E402


@dataclass(frozen=True)
class AppAccess:
    """Why the viewer may (or may not) run this app, and how far."""

    can_run: bool
    role: str  # owner | edit | view | none
    write_approved: bool
    reason: str | None = None


def resolve_access(db: Session, app, user) -> AppAccess:
    """Audience check — owner, or a share that names this viewer.

    Org-wide visibility still requires the viewer to be in the app's own
    organization; an app is never visible across orgs, which is the property a
    marketplace would later have to break deliberately rather than inherit by
    accident.
    """
    from app.db.models import AppShare, OrganizationMembership, UserVenueAccess

    if app.archived_at is not None:
        return AppAccess(False, "none", False, "this app has been archived")

    if app.created_by and app.created_by == user.id:
        # The author always runs their own app at full declared reach; the
        # intersection rule below still holds them to their own permissions.
        return AppAccess(True, "owner", True)

    member = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == app.organization_id,
        )
        .first()
    )
    if not member:
        return AppAccess(False, "none", False, "app not found")

    venue_ids = {
        r.venue_id
        for r in db.query(UserVenueAccess)
        .filter(UserVenueAccess.user_id == user.id)
        .all()
    }

    def hits(share) -> bool:
        if share.principal_type == "user":
            return share.principal_id == user.id
        if share.principal_type == "organization":
            return share.principal_id == app.organization_id
        if share.principal_type == "venue":
            return share.principal_id in venue_ids
        return False

    matches = [
        s for s in db.query(AppShare).filter(AppShare.app_id == app.id).all() if hits(s)
    ]
    if not matches:
        return AppAccess(False, "none", False, "app not found")
    # Several grants can apply at once (named directly AND org-wide). The widest
    # wins: an explicit share should not be narrowed by a broader one that
    # happens to exist too.
    return AppAccess(
        True,
        "edit" if any(s.access == "edit" for s in matches) else "view",
        any(s.write_actions_approved for s in matches),
    )


def org_permissions(db: Session, user) -> set[str]:
    """The viewer's own org permissions — the ceiling on everything below.

    Platform admins bypass org roles everywhere else in Norm; they do here too,
    but nowhere else in this module is that shortcut taken.
    """
    from app.auth.permissions import ALL_ORG_PERMISSIONS
    from app.db.models import OrganizationMembership, Role

    if getattr(user, "role", None) == "admin":
        return set(ALL_ORG_PERMISSIONS)
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if not membership or not membership.role_id:
        return set()
    role = db.query(Role).filter(Role.id == membership.role_id).first()
    return set((role.permissions if role else None) or [])


def required_permissions(spec: dict) -> set[str]:
    """The org permissions an app's declared scopes add up to.

    An unknown scope contributes nothing and is therefore useless rather than
    dangerous — the same fail-closed reading ``mcp/scopes.py`` applies.
    """
    needed: set[str] = set()
    for name in (spec or {}).get("scopes") or []:
        scope = MCP_SCOPES.get(str(name))
        if scope:
            needed |= set(scope.requires)
    return needed


def describe_reach(spec: dict) -> list[str]:
    """The app's reach in the consent language the MCP surface already uses —
    what the builder shows a user before the app runs, and what a share
    approval names."""
    out: list[str] = []
    for name in (spec or {}).get("scopes") or []:
        scope = MCP_SCOPES.get(str(name))
        if scope:
            out.append(f"{scope.label} — {scope.description}")
    return out


def _declared(spec: dict, key: str, connector: str, action: str) -> bool:
    """Is (connector, action) in one of the version's declared lists?"""
    for entry in (spec or {}).get(key) or []:
        if isinstance(entry, dict):
            if entry.get("connector") == connector and entry.get("action") == action:
                return True
        elif isinstance(entry, str) and entry in (action, f"{connector}.{action}"):
            return True
    return False


def _tool_method(config_db: Session, connector: str, action: str) -> str:
    """The action's HTTP method from its ConnectorSpec — the source of truth for
    'is this a write', so an app cannot relabel one."""
    from app.db.config_models import ConnectorSpec

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == connector)
        .first()
    )
    for tool in (spec.tools if spec else None) or []:
        if isinstance(tool, dict) and tool.get("action") == action:
            return str(tool.get("method") or "GET").upper()
    raise HTTPException(404, f"unknown action {connector}.{action}")


def _reachable_venues(db: Session, user) -> set[str] | None:
    """The venues this viewer may see. ``None`` means every venue (platform
    admin), which is the only bypass anywhere in this module."""
    from app.db.models import UserVenueAccess

    if getattr(user, "role", None) == "admin":
        return None
    return {
        r.venue_id
        for r in db.query(UserVenueAccess).filter(UserVenueAccess.user_id == user.id)
    }


def _tool_read_only(config_db: Session, connector: str, action: str) -> bool:
    """Does the spec explicitly mark this action read-only?

    HTTP method is a good write signal for template-mode connectors and a
    useless one for MCP-mode connectors, where EVERY action is POST because
    that is the transport. MCP discovery infers GET only from a leading
    `get_` (`mcp_executor.convert_mcp_tools_to_spec`), and domain-prefixed
    names never match it — so `training_list_job_openings` reads as a write
    and an app merely LISTING data would be pushed through the write gate,
    demanding an approval nobody should have to grant to read.

    An explicit `read_only: true` on the action settles it — but ONLY for
    mcp-mode connectors, which is precisely where method carries no meaning.
    On a template-mode connector a POST really is a POST, and a mis-set flag
    there must not be able to turn a genuine write into an unapproved read.
    (`delegation.is_read_only_tool` is stricter still — it demands the flag AND
    a GET — so nothing here widens what a sub-agent may call.)

    Anything unreadable or absent means "not marked", i.e. still a write — the
    fail-closed direction.
    """
    from app.db.config_models import ConnectorSpec

    try:
        spec = (
            config_db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == connector)
            .first()
        )
        if not spec or (spec.execution_mode or "") != "mcp":
            return False
        for tool in spec.tools or []:
            if isinstance(tool, dict) and tool.get("action") == action:
                return bool(tool.get("read_only"))
    except Exception:  # noqa: BLE001 — an unreadable spec is not a read grant
        return False
    return False


def _check_venue(db: Session, user, venue_id: str | None) -> None:
    """Explicit venue access, or nothing.

    Deliberately NOT ``get_user_venues``: that helper fails open, handing a user
    with no access rows every venue on the platform. An app must never inherit
    that.
    """
    from app.db.models import UserVenueAccess

    if not venue_id:
        return
    if getattr(user, "role", None) == "admin":
        return
    has = (
        db.query(UserVenueAccess)
        .filter(
            UserVenueAccess.user_id == user.id,
            UserVenueAccess.venue_id == venue_id,
        )
        .first()
    )
    if not has:
        raise HTTPException(403, "you do not have access to that venue")


def call_action(
    db: Session,
    config_db: Session,
    *,
    app,
    version,
    user,
    venue_id: str | None,
    connector: str,
    action: str,
    params: dict | None = None,
    access: AppAccess | None = None,
) -> object:
    """Run ONE connector action on behalf of an app. The only data door."""
    from app.connectors.tool_executor import execute_connector_tool
    from app.db.models import AppCall

    spec = version.spec or {}
    access = access or resolve_access(db, app, user)
    if not access.can_run:
        raise HTTPException(404, access.reason or "app not found")

    if not _declared(spec, "actions", connector, action):
        raise HTTPException(
            403,
            f"'{app.name}' is not allowed to call {connector}.{action} — it is "
            "not in this version's declared actions",
        )

    holds = org_permissions(db, user)
    missing = required_permissions(spec) - holds
    if missing:
        # The intersection rule. Phrased about the VIEWER, not the app: the app
        # is not broken, this person simply cannot do what it does.
        raise HTTPException(
            403,
            "you do not have the permissions this app needs: "
            + ", ".join(sorted(missing)),
        )

    # `method` is also what the audit row records, so resolve it once. An
    # action marked read_only is a read whatever its transport says.
    method = _tool_method(config_db, connector, action)
    if method != "GET" and not _tool_read_only(config_db, connector, action):
        if not _declared(spec, "writes", connector, action):
            raise HTTPException(
                403,
                f"{connector}.{action} changes data and is not declared as a "
                f"write action by '{app.name}'",
            )
        if not access.write_approved:
            raise HTTPException(
                403,
                f"writes are not approved for you on '{app.name}' — "
                f"{connector}.{action} was refused",
            )

    _check_venue(db, user, venue_id)

    t0 = time.time()
    result = execute_connector_tool(
        connector,
        action,
        dict(params or {}),
        db,
        config_db,
        venue_id=venue_id,
        # No silent fall back to another venue's credentials.
        strict_venue=True,
    )
    ms = int((time.time() - t0) * 1000)

    # Audited unconditionally — including the failures, which is where anything
    # interesting will be. Isolated: an audit problem must not fail a call that
    # already happened.
    try:
        db.add(
            AppCall(
                app_id=app.id,
                app_version_id=version.id,
                user_id=user.id,
                venue_id=venue_id,
                connector=connector,
                action=action,
                method=method,
                ok=bool(result.success),
                error=result.error,
                duration_ms=ms,
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 — audit must not fail the call
        db.rollback()
        logger.info("app audit write failed: %s", exc)

    if not result.success:
        raise HTTPException(502, result.error or f"{connector}.{action} failed")
    return result.payload


#: What an app must declare before it can store anything:
#:
#:     storage: {
#:       "namespace":   "hr_hiring_training",   # who the rows belong to
#:       "collections": ["people", "programs"], # the ONLY names it may touch
#:       "shared_with": ["hiring"]              # apps allowed to join (owner only)
#:     }
#: A collection is a NAME. It reaches the server as a URL path segment, so
#: anything path-shaped ("../../other-app/records/people") must be refused
#: here as well as encoded by the caller — defence on both sides of the wire.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: How many rows app logic may pull from one collection in a single pass.
#: High enough for a real dataset (Orbit's biggest collection is ~6,800
#: completions), low enough that a runaway query is still bounded.
_STORE_LIST_CEILING = 50_000

_STORAGE_OPS_READ = ("list", "get", "count", "file_get")


def _storage_spec(spec: dict) -> dict:
    st = (spec or {}).get("storage")
    return st if isinstance(st, dict) else {}


def _storage_reach(spec: dict, collection: str) -> str:
    """The namespace this app may use for ``collection``, or raise.

    Same shape of refusal as an undeclared action: named, and naming what WAS
    declared, because an app author reading the error should not have to guess.
    """
    if not _SAFE_NAME.match(str(collection or "")):
        raise HTTPException(400, "collection names are letters, digits, - and _")
    st = _storage_spec(spec)
    namespace = str(st.get("namespace") or "").strip()
    collections = [str(c) for c in (st.get("collections") or [])]
    if not namespace or not collections:
        raise HTTPException(403, "this app does not declare any storage")
    if collection not in collections:
        raise HTTPException(
            403,
            f"'{collection}' is not a collection this app declares — "
            f"declared: {', '.join(sorted(collections))}",
        )
    return namespace


def _storage_guard(
    db: Session,
    *,
    app,
    version,
    user,
    collection: str,
    mutating: bool,
    venue_id: str | None = None,
) -> tuple[str, str]:
    """The four checks, in the same order and for the same reasons as
    ``call_action``: audience, declared reach, permission intersection, and —
    for anything that changes a row — the write approval.

    Returns ``(namespace, organization_id)``. The org id is returned rather
    than taken from the caller because it is the hard tenancy boundary: every
    query below filters on the APP's organization, never on anything a request
    can influence.
    """
    access = resolve_access(db, app, user)
    if not access.can_run:
        raise HTTPException(404, access.reason or "app not found")

    spec = version.spec or {}
    namespace = _storage_reach(spec, collection)

    missing = required_permissions(spec) - org_permissions(db, user)
    if missing:
        raise HTTPException(
            403,
            "you do not have the permissions this app needs: "
            + ", ".join(sorted(missing)),
        )

    if mutating and not access.write_approved:
        raise HTTPException(
            403,
            f"writes are not approved for you on '{app.name}' — "
            f"changing '{collection}' was refused",
        )

    if venue_id:
        _check_venue(db, user, venue_id)
    return namespace, app.organization_id


def _audit_storage(
    db: Session, *, app, version, user, op: str, collection: str, venue_id, ok, error
) -> None:
    """Storage goes in the same audit trail as connector calls — one place to
    read 'what has this app done'.

    The audit row rides on the SAME transaction as the operation it records, so
    the two land together or not at all. It used to commit on its own, which
    made it the thing that persisted every write — and its `rollback()` on
    failure then discarded the very write it was recording."""
    from app.db.models import AppCall

    try:
        db.add(
            AppCall(
                app_id=app.id,
                app_version_id=version.id,
                user_id=user.id,
                venue_id=venue_id,
                connector="app_storage",
                action=f"{op}:{collection}",
                method="GET" if op in _STORAGE_OPS_READ else "POST",
                ok=ok,
                error=error,
            )
        )
        db.flush()
    except Exception as exc:  # noqa: BLE001 — audit must not fail the op
        logger.info("app storage audit write failed: %s", exc)


def _record_out(row) -> dict:
    return {
        "id": row.id,
        "venue_id": row.venue_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        **(row.data or {}),
    }


def _scope_venue(q, db, user, venue_id, include_global):
    """Venue scoping for a query, in one place so a read and a count can never
    disagree about what the viewer may see.

    Naming a venue means that venue (plus the group-wide rows unless the caller
    says otherwise). NOT naming one must not mean "every venue" — `_check_venue`
    only fires on a venue the caller supplies, so without this the gate would be
    opt-in by the caller. Unnamed therefore means everything they could reach
    anyway: their own venues plus the rows that belong to the whole org.
    """
    from app.db.models import AppRecord

    if venue_id:
        return (
            q.filter((AppRecord.venue_id == venue_id) | (AppRecord.venue_id.is_(None)))
            if include_global
            else q.filter(AppRecord.venue_id == venue_id)
        )
    reach = _reachable_venues(db, user)
    if reach is None:
        return q
    return (
        q.filter(AppRecord.venue_id.is_(None) | AppRecord.venue_id.in_(reach))
        if reach
        else q.filter(AppRecord.venue_id.is_(None))
    )


#: Comparisons a query may use. Deliberately small, and everything here maps
#: to something an index can serve.
_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_null")


def _json_text(value):
    """A Python value as the text JSONB would render it.

    `astext` always yields text, so the comparison value has to be text too —
    and the naive `str(value)` was wrong for everything that is not a string:
    `str(True)` is `"True"` where JSON says `true`, so `where={"is_active":
    True}` matched nothing at all.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return str(value)


def _field(key: str):
    """A `data` field, addressed by a dotted path.

    `result.signoff_at` reaches inside the document — which is the whole reason
    this column is JSONB, and is how the sign-off queue finds its work.
    """
    parts = [p for p in str(key).split(".") if p]
    if not parts:
        raise HTTPException(400, "a filter needs a field name")
    from app.db.models import AppRecord

    column = AppRecord.data
    for part in parts[:-1]:
        column = column[part]
    return column[parts[-1]]


def _condition(key: str, value):
    """One filter condition. A plain value means equality; a single-key dict
    names one of the comparisons in ``_OPS``."""
    field = _field(key)
    op, operand = "eq", value
    if isinstance(value, dict) and len(value) == 1:
        candidate = next(iter(value))
        if candidate in _OPS:
            op, operand = candidate, value[candidate]

    if op == "is_null":
        return field.astext.is_(None) if operand else field.astext.isnot(None)
    if op in ("in", "not_in"):
        texts = [_json_text(v) for v in (operand or [])]
        if not texts:
            # An empty IN matches nothing; an empty NOT IN excludes nothing.
            return sa.false() if op == "in" else sa.true()
        clause = field.astext.in_(texts)
        return clause if op == "in" else ~clause
    if op == "contains":
        return field.astext.ilike(f"%{operand}%")

    text = _json_text(operand)
    if text is None:
        return field.astext.is_(None) if op == "eq" else field.astext.isnot(None)
    return {
        "eq": lambda: field.astext == text,
        "ne": lambda: field.astext != text,
        "gt": lambda: field.astext > text,
        "gte": lambda: field.astext >= text,
        "lt": lambda: field.astext < text,
        "lte": lambda: field.astext <= text,
    }[op]()


def _apply_where(q, where: dict | None):
    for key, value in (where or {}).items():
        q = q.filter(_condition(key, value))
    return q


def store_list(
    db: Session,
    *,
    app,
    version,
    user,
    collection: str,
    where: dict | None = None,
    venue_id: str | None = None,
    include_global: bool = True,
    order_by: str | None = None,
    descending: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """Rows in a collection, filtered by equality on top-level ``data`` keys.

    ``include_global`` exists because of a real bug worth not repeating: Orbit's
    training programs are group-wide (no venue) and its own API filters them
    with ``venue_id IN (...)``, so a NULL venue never matches and every global
    program — plus every assignment hanging off one — is invisible through it.
    Asking for one venue therefore returns that venue's rows AND the rows that
    belong to everyone, unless the caller says otherwise.
    """
    from app.db.models import AppRecord

    namespace, org_id = _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        mutating=False,
        venue_id=venue_id,
    )
    q = db.query(AppRecord).filter(
        AppRecord.namespace == namespace,
        AppRecord.organization_id == org_id,
        AppRecord.collection == collection,
    )
    q = _scope_venue(q, db, user, venue_id, include_global)
    q = _apply_where(q, where)
    # Ordering by a document field, so a caller no longer has to pull a whole
    # collection into memory just to sort it. Insertion order stays the
    # default because it is the only one that is stable without a key.
    if order_by:
        column = _field(order_by).astext
        q = q.order_by(column.desc() if descending else column.asc())
    else:
        q = q.order_by(
            AppRecord.created_at.desc() if descending else AppRecord.created_at.asc()
        )
    rows = q.limit(min(int(limit), 1000)).offset(offset)
    out = [_record_out(r) for r in rows]
    _audit_storage(
        db,
        app=app,
        version=version,
        user=user,
        op="list",
        collection=collection,
        venue_id=venue_id,
        ok=True,
        error=None,
    )
    return out


def store_count(
    db: Session,
    *,
    app,
    version,
    user,
    collection: str,
    where: dict | None = None,
    venue_id: str | None = None,
    include_global: bool = True,
) -> int:
    """How many rows match, without materialising them.

    Every count in both apps was a Python ``len()`` over a fully fetched
    collection — the hiring board loaded every application in the org to print
    "3 applicants".
    """
    from app.db.models import AppRecord

    namespace, org_id = _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        mutating=False,
        venue_id=venue_id,
    )
    q = db.query(sa.func.count(AppRecord.id)).filter(
        AppRecord.namespace == namespace,
        AppRecord.organization_id == org_id,
        AppRecord.collection == collection,
    )
    q = _scope_venue(q, db, user, venue_id, include_global)
    return int(_apply_where(q, where).scalar() or 0)


def store_get(db: Session, *, app, version, user, collection: str, record_id: str):
    from app.db.models import AppRecord

    namespace, org_id = _storage_guard(
        db, app=app, version=version, user=user, collection=collection, mutating=False
    )
    row = (
        db.query(AppRecord)
        .filter(
            AppRecord.id == record_id,
            AppRecord.namespace == namespace,
            AppRecord.organization_id == org_id,
            AppRecord.collection == collection,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, f"no '{collection}' record {record_id}")
    # The row's OWN venue is the thing to check — a caller reaching a record by
    # id never supplies one, so nothing else would.
    _check_venue(db, user, row.venue_id)
    _audit_storage(
        db,
        app=app,
        version=version,
        user=user,
        op="get",
        collection=collection,
        venue_id=row.venue_id,
        ok=True,
        error=None,
    )
    return _record_out(row)


def store_put(
    db: Session,
    *,
    app,
    version,
    user,
    collection: str,
    data: dict,
    record_id: str | None = None,
    venue_id: str | None = None,
) -> dict:
    """Create a row, or replace the ``data`` of one this app can already see.

    An update is scoped by namespace + org + collection, so a caller cannot
    reach a row in another namespace by guessing its id.
    """
    from app.db.models import AppRecord

    namespace, org_id = _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        mutating=True,
        venue_id=venue_id,
    )
    if not isinstance(data, dict):
        raise HTTPException(400, "record data must be an object")

    row = None
    if record_id:
        row = (
            db.query(AppRecord)
            .filter(
                AppRecord.id == record_id,
                AppRecord.namespace == namespace,
                AppRecord.organization_id == org_id,
                AppRecord.collection == collection,
            )
            .first()
        )
        if not row:
            raise HTTPException(404, f"no '{collection}' record {record_id}")
        # Checked BEFORE any re-scoping below, so a caller cannot reach a row
        # in a venue it has no access to and then move it somewhere it does.
        _check_venue(db, user, row.venue_id)

    if row is None:
        row = AppRecord(
            namespace=namespace,
            organization_id=org_id,
            venue_id=venue_id,
            collection=collection,
            data=data,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(row)
    else:
        row.data = data
        row.updated_by = user.id
        if venue_id is not None:
            row.venue_id = venue_id
    db.flush()
    out = _record_out(row)
    _audit_storage(
        db,
        app=app,
        version=version,
        user=user,
        op="put",
        collection=collection,
        venue_id=venue_id,
        ok=True,
        error=None,
    )
    return out


def store_delete(
    db: Session, *, app, version, user, collection: str, record_id: str
) -> dict:
    from app.db.models import AppRecord

    namespace, org_id = _storage_guard(
        db, app=app, version=version, user=user, collection=collection, mutating=True
    )
    row = (
        db.query(AppRecord)
        .filter(
            AppRecord.id == record_id,
            AppRecord.namespace == namespace,
            AppRecord.organization_id == org_id,
            AppRecord.collection == collection,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, f"no '{collection}' record {record_id}")
    _check_venue(db, user, row.venue_id)
    venue_id = row.venue_id
    db.delete(row)
    db.flush()
    _audit_storage(
        db,
        app=app,
        version=version,
        user=user,
        op="delete",
        collection=collection,
        venue_id=venue_id,
        ok=True,
        error=None,
    )
    return {"deleted": record_id}


def _check_namespace_claim(
    db: Session, organization_id: str, slug: str, spec: dict
) -> None:
    """A namespace belongs to whoever claimed it first.

    Sharing storage between apps is a real need — Hiring and Training are one
    domain over one set of people — but "two apps can name the same namespace"
    with no further rule is just a hole: any app could name `hr_hiring` and
    read the candidate pipeline. So the owner has to say yes, in its own spec,
    by listing the joining app in ``storage.shared_with``.

    That keeps the doc's MapKit rule intact — nobody reads another app's data
    unless that app published it — while letting a suite of apps behave as one.
    """
    from app.db.models import App, AppVersion

    st = _storage_spec(spec)
    namespace = str(st.get("namespace") or "").strip()
    if not namespace:
        return

    collections = st.get("collections")
    if not isinstance(collections, list) or not collections:
        raise HTTPException(
            400, "spec.storage.collections must be a non-empty list of names"
        )

    # Every app in the org that already claims this namespace — INCLUDING the
    # one being saved, if it exists. Leaving itself out was a bug: re-saving
    # the owner made the newer joiner look like the owner, and the app that
    # created the namespace was refused entry to it.
    rows = (
        db.query(App, AppVersion)
        .join(AppVersion, AppVersion.id == App.current_version_id)
        .filter(
            App.organization_id == organization_id,
            App.archived_at.is_(None),
        )
        .all()
    )
    claimants = [
        (a, v)
        for a, v in rows
        if str(_storage_spec(v.spec or {}).get("namespace") or "") == namespace
    ]
    if not claimants:
        return

    # Earliest claimant owns it — and an owner re-saving its own app is just
    # an owner, not a joiner.
    owner_app, owner_version = min(claimants, key=lambda pair: pair[0].created_at)
    if owner_app.slug == slug:
        return
    allowed = [
        str(x)
        for x in (_storage_spec(owner_version.spec or {}).get("shared_with") or [])
    ]
    if slug not in allowed:
        raise HTTPException(
            403,
            f"storage namespace '{namespace}' belongs to '{owner_app.name}' — "
            f"it must list '{slug}' in spec.storage.shared_with before this app "
            "can share it",
        )


#: A single file's ceiling. Generous for training evidence and CVs (Orbit's
#: own public path capped at 10 MB) and small enough that the bytes belong in
#: a column rather than an object store.
_MAX_FILE_BYTES = 15 * 1024 * 1024


def _file_out(row) -> dict:
    """A file's metadata. Never the bytes — those come from the download
    endpoint, which re-checks who is asking."""
    return {
        "id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "collection": row.collection,
        "record_id": row.record_id,
        "venue_id": row.venue_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def file_put(
    db: Session,
    *,
    app,
    version,
    user,
    collection: str,
    record_id: str | None,
    filename: str,
    content_type: str | None,
    data: bytes,
    venue_id: str | None = None,
    source_ref: str | None = None,
) -> dict:
    """Store bytes against a record.

    Guarded exactly like a write to that record — same audience, same declared
    collection, same permission intersection, same write approval — so a file
    can never be reachable by someone who could not read the row it hangs off.
    """
    from app.db.models import AppFile

    namespace, org_id = _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        mutating=True,
        venue_id=venue_id,
    )
    if not data:
        raise HTTPException(400, "that file is empty")
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(
            413,
            f"that file is {len(data) // 1024 // 1024} MB — the limit is "
            f"{_MAX_FILE_BYTES // 1024 // 1024} MB",
        )
    row = AppFile(
        namespace=namespace,
        organization_id=org_id,
        venue_id=venue_id,
        collection=collection,
        record_id=record_id,
        filename=str(filename or "file")[:255],
        content_type=(content_type or "application/octet-stream")[:120],
        size_bytes=len(data),
        data=data,
        source_ref=source_ref,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    _audit_storage(
        db,
        app=app,
        version=version,
        user=user,
        op="file_put",
        collection=collection,
        venue_id=venue_id,
        ok=True,
        error=None,
    )
    return _file_out(row)


def file_list(
    db: Session, *, app, version, user, collection: str, record_id: str | None = None
) -> list[dict]:
    from app.db.models import AppFile

    namespace, org_id = _storage_guard(
        db, app=app, version=version, user=user, collection=collection, mutating=False
    )
    q = db.query(AppFile).filter(
        AppFile.namespace == namespace,
        AppFile.organization_id == org_id,
        AppFile.collection == collection,
    )
    if record_id:
        q = q.filter(AppFile.record_id == record_id)
    rows = [r for r in q.order_by(AppFile.created_at) if _may_see_venue(db, user, r)]
    return [_file_out(r) for r in rows]


def _may_see_venue(db: Session, user, row) -> bool:
    reach = _reachable_venues(db, user)
    return reach is None or row.venue_id is None or row.venue_id in reach


def file_fetch(db: Session, *, app, version, user, file_id: str):
    """The bytes, plus the metadata the caller needs to serve them.

    Deliberately re-runs the whole guard rather than trusting a URL: this is
    the one place bytes leave the system, and Orbit's equivalent was a public
    bucket URL that anyone with the link could read forever.
    """
    from app.db.models import AppFile

    row = db.query(AppFile).filter(AppFile.id == file_id).first()
    if not row or row.organization_id != app.organization_id:
        raise HTTPException(404, "no such file")
    _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=row.collection or "",
        mutating=False,
    )
    if not _may_see_venue(db, user, row):
        raise HTTPException(403, "you do not have access to that venue")
    _audit_storage(
        db,
        app=app,
        version=version,
        user=user,
        op="file_get",
        collection=row.collection or "",
        venue_id=row.venue_id,
        ok=True,
        error=None,
    )
    return row


def file_delete(db: Session, *, app, version, user, file_id: str) -> dict:
    from app.db.models import AppFile

    row = db.query(AppFile).filter(AppFile.id == file_id).first()
    if not row or row.organization_id != app.organization_id:
        raise HTTPException(404, "no such file")
    _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=row.collection or "",
        mutating=True,
    )
    if not _may_see_venue(db, user, row):
        raise HTTPException(403, "you do not have access to that venue")
    collection = row.collection or ""
    db.delete(row)
    db.flush()
    _audit_storage(
        db,
        app=app,
        version=version,
        user=user,
        op="file_delete",
        collection=collection,
        venue_id=row.venue_id,
        ok=True,
        error=None,
    )
    return {"deleted": file_id}


def save_app(db: Session, user, payload: dict) -> dict:
    """Create an app, or add an immutable version to one the user may edit.

    ONE implementation for both authors: the web endpoint (``routers/apps.py``)
    and the builder agent's ``save_app`` tool. The rule that matters is checked
    here so neither can drift: an author can only declare reach they themselves
    hold — the same intersection the door applies at call time, applied at save
    time so the refusal names the missing permission while it can still be
    fixed.
    """
    import re

    from app.db.models import App, AppVersion, OrganizationMembership

    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if not membership:
        raise HTTPException(403, "no organization membership")

    spec = payload.get("spec") or {}

    # Unknown scope names are REJECTED, not ignored. required_permissions
    # treats an unknown scope as granting nothing — safe at call time, but at
    # save time silence would mean an app whose whole permission story is a
    # typo passes the intersection vacuously and shows the viewer no reach at
    # all. The author gets the valid vocabulary while they can still fix it.
    unknown = [s for s in spec.get("scopes") or [] if str(s) not in MCP_SCOPES]
    if unknown:
        raise HTTPException(
            400,
            f"unknown scope(s) {', '.join(map(str, unknown))} — valid scopes: "
            + ", ".join(sorted(MCP_SCOPES)),
        )
    bad = [
        e
        for e in spec.get("actions") or []
        if not (isinstance(e, dict) and e.get("connector") and e.get("action"))
    ]
    if bad:
        raise HTTPException(
            400, 'each spec.actions entry must be {"connector": ..., "action": ...}'
        )

    missing = required_permissions(spec) - org_permissions(db, user)
    if missing:
        raise HTTPException(
            403,
            "an app cannot ask for more than you can do yourself — missing: "
            + ", ".join(sorted(missing)),
        )

    # An app's pages join an agent's menu. A slug with no agent behind it is
    # worse than useless: no sidebar button can ever select it, so the pages
    # are orphaned — and `page_context.agent` is taken on faith by the
    # supervisor, so chatting from such a page silently does nothing. Refuse it
    # here, the one choke point both the web endpoint and the builder tool go
    # through, and name the valid set.
    agent = str(payload.get("agent") or "").strip() or None
    if agent:
        from app.agents.registry import registered_domains

        known = set(registered_domains())
        if agent not in known:
            raise HTTPException(
                400,
                f"unknown agent '{agent}' — an app's pages must join one of: "
                + ", ".join(sorted(known)),
            )

    name = str(payload.get("name") or "").strip()
    slug = str(payload.get("slug") or re.sub(r"[^a-z0-9]+", "-", name.lower())).strip(
        "-"
    )
    if not name or not slug:
        raise HTTPException(400, "a name is required")

    _check_namespace_claim(db, membership.organization_id, slug, spec)

    app = (
        db.query(App)
        .filter(App.organization_id == membership.organization_id, App.slug == slug)
        .first()
    )
    if app is None:
        app = App(
            organization_id=membership.organization_id,
            created_by=user.id,
            slug=slug,
            name=name,
            description=payload.get("description"),
            icon=payload.get("icon"),
            agent=agent,
            purpose=payload.get("purpose"),
            visibility="private",
        )
        db.add(app)
        db.flush()
    else:
        access = resolve_access(db, app, user)
        if access.role not in ("owner", "edit"):
            raise HTTPException(403, "you cannot edit this app")
        app.name = name
        app.description = payload.get("description")
        app.icon = payload.get("icon")
        if agent:
            app.agent = agent
        if payload.get("purpose"):
            app.purpose = payload.get("purpose")

    last = (
        db.query(AppVersion)
        .filter(AppVersion.app_id == app.id)
        .order_by(AppVersion.version.desc())
        .first()
    )
    version = AppVersion(
        app_id=app.id,
        version=(last.version + 1) if last else 1,
        spec=spec,
        ui_source=payload.get("ui_source"),
        logic_source=payload.get("logic_source"),
        changelog=payload.get("changelog"),
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    app.current_version_id = version.id
    db.commit()
    return {
        "slug": app.slug,
        "version": version.version,
        "id": app.id,
        "name": app.name,
        "reach": describe_reach(spec),
    }


def run_logic(
    db: Session,
    config_db: Session,
    *,
    app,
    version,
    user,
    venue_id: str | None,
    params: dict | None = None,
) -> dict:
    """Run an app's server-side ``run(params, call_api, log)``.

    The consolidator sandbox is reused unchanged — no I/O, no imports, network
    only through the ``call_api`` we hand it, which is this module's door with
    the app and viewer already bound. So the sandbox constrains the code and the
    door constrains its reach; neither has to be re-argued here.
    """
    from app.connectors.function_executor import execute_function

    if not version.logic_source:
        raise HTTPException(400, f"'{app.name}' has no server-side logic")

    access = resolve_access(db, app, user)
    if not access.can_run:
        raise HTTPException(404, access.reason or "app not found")

    spec = version.spec or {}
    bound_params = {**(params or {})}
    if venue_id:
        bound_params.setdefault("venue_id", venue_id)

    def _call_api(connector: str, action: str, api_params: dict | None = None):
        return call_action(
            db,
            config_db,
            app=app,
            version=version,
            user=user,
            venue_id=(api_params or {}).get("venue_id") or venue_id,
            connector=connector,
            action=action,
            params=api_params,
            access=access,
        )

    class _Store:
        """`store` inside the sandbox — the same door the UI and the HTTP
        endpoints use, with this app, version and viewer already bound. The
        sandbox can therefore neither widen the collection list nor reach
        another namespace, because it never gets to say which app it is."""

        @staticmethod
        def list(
            collection,
            where=None,
            venue_id=None,
            include_global=True,
            order_by=None,
            descending=False,
            limit=None,
        ):
            """Every matching row, paged.

            **A venue is not assumed.** This used to substitute the app's
            currently-selected venue whenever the caller named none, so an app
            could not ask for "everything I can see" — which is why the Hiring
            board reported "no job openings" under a header reading "Openings
            across the group": the one job belonged to another venue. Omitting
            `venue_id` now means every venue the viewer can reach, and an app
            that wants the picker's venue passes `store.current_venue()`.

            The door bounds a single query at 1,000 rows so one call can never
            scan a whole table. Left there, that bound is a trap: logic asking
            for `limit=5000` got 1,000 rows and no indication, which is how a
            tracker built on 6,784 completions silently computed itself from
            1,000 of them and reported people as untrained. Paging here keeps
            the per-query bound and removes the silence — and if the overall
            ceiling is ever reached, it says so in the run log rather than
            returning a confidently wrong answer.
            """
            page = 1000
            ceiling = int(limit) if limit else _STORE_LIST_CEILING
            out = []
            offset = 0
            while len(out) < ceiling:
                batch = store_list(
                    db,
                    app=app,
                    version=version,
                    user=user,
                    collection=collection,
                    where=where,
                    venue_id=venue_id,
                    include_global=include_global,
                    order_by=order_by,
                    descending=descending,
                    limit=min(page, ceiling - len(out)),
                    offset=offset,
                )
                out.extend(batch)
                if len(batch) < page:
                    return out
                offset += len(batch)
            logger.warning(
                "app %s: store.list('%s') hit the %d-row ceiling — the result "
                "may be incomplete",
                app.slug,
                collection,
                ceiling,
            )
            return out

        @staticmethod
        def current_venue():
            """The venue the viewer has selected, for an app that deliberately
            wants to scope to it. Reads are group-wide by default."""
            return venue_id_bound

        @staticmethod
        def count(collection, where=None, venue_id=None, include_global=True):
            return store_count(
                db,
                app=app,
                version=version,
                user=user,
                collection=collection,
                where=where,
                venue_id=venue_id,
                include_global=include_global,
            )

        @staticmethod
        def get(collection, record_id):
            return store_get(
                db,
                app=app,
                version=version,
                user=user,
                collection=collection,
                record_id=record_id,
            )

        @staticmethod
        def put(collection, data, record_id=None, venue_id=None):
            # The app's current venue is a default for NEW rows only. Applying
            # it to an update would silently pull a group-wide row (venue NULL)
            # into whichever venue happened to be selected — the same promotion
            # hazard the venue-delete path guards against, in reverse.
            fallback = None if record_id else venue_id_bound
            return store_put(
                db,
                app=app,
                version=version,
                user=user,
                collection=collection,
                data=data,
                record_id=record_id,
                venue_id=venue_id if venue_id is not None else fallback,
            )

        @staticmethod
        def files(collection, record_id=None):
            """The files hanging off a record — metadata only. Bytes are
            served by the download endpoint, which re-checks the viewer."""
            return file_list(
                db,
                app=app,
                version=version,
                user=user,
                collection=collection,
                record_id=record_id,
            )

        @staticmethod
        def delete_file(file_id):
            return file_delete(db, app=app, version=version, user=user, file_id=file_id)

        @staticmethod
        def delete(collection, record_id):
            return store_delete(
                db,
                app=app,
                version=version,
                user=user,
                collection=collection,
                record_id=record_id,
            )

    venue_id_bound = venue_id

    # The sandbox's own call_api reaches the connector layer directly, which
    # would make an app's logic a way around its own app's declared reach.
    # call_api_override routes it back through this module's door instead, so
    # UI and logic are checked identically. Storage is bound the same way, and
    # is absent entirely for an app that declares none.
    return execute_function(
        version.logic_source,
        bound_params,
        db,
        thread_id=None,
        options={
            # `or 20` read a declared 0 as "unset", so an app saying it
            # makes no connector calls silently got a budget of 20.
            "max_api_calls": int(
                spec["max_api_calls"] if "max_api_calls" in spec else 20
            ),
            "allowed_write_actions": [
                f"{e.get('connector')}.{e.get('action')}"
                for e in spec.get("writes") or []
                if isinstance(e, dict)
            ],
        },
        call_api_override=_call_api,
        storage_override=_Store if _storage_spec(spec).get("namespace") else None,
    )
