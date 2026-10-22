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
import contextvars
from dataclasses import dataclass

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.services import active_org
from app.connectors import spec_rows

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


#: True while an app's own logic is running (run_logic). Collections a spec
#: lists in ``storage.logic_only`` are readable only then — never through the
#: direct records/files endpoints or the page's norm.storage — so an app's
#: field rules (e.g. Norm Performance's: pay and private review answers only
#: for the review's manager) can't be sidestepped by reading the raw rows.
_IN_LOGIC: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "app_in_logic", default=False
)


def _run_in_logic(fn, *args, **kwargs):
    token = _IN_LOGIC.set(True)
    try:
        return fn(*args, **kwargs)
    finally:
        _IN_LOGIC.reset(token)


@dataclass(frozen=True)
class SystemActor:
    """Who a SCHEDULED run of a built-in app acts as (services/app_schedules.py).

    Not a user and never constructible from a request. It is only honoured for
    apps built into Norm (resolve_access), and every query it makes is still
    pinned to the org the app was bound to — the admin-style shortcuts below
    (no venue-access rows, no permission ceiling) never reach another org.
    """

    id: None = None
    email: None = None
    full_name: str = "Norm (scheduled)"
    role: str = "admin"


SYSTEM = SystemActor()


def is_system(user) -> bool:
    return isinstance(user, SystemActor)


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
    if is_system(user) and not getattr(app, "builtin", False):
        # The system actor runs Norm's own apps only — never an org's app.
        return AppAccess(False, "none", False, "app not found")

    # Built into Norm (services/builtin_apps.py): no author, no shares —
    # anyone in the org it was bound to may use it and change its data. The
    # org was taken from the viewer's membership when it was bound; it is
    # re-checked here on every call rather than trusted.
    from app.services.builtin_apps import is_builtin
    from app.services.page_templates import is_template

    if is_template(app):
        # Norm's ready-made dashboard, bound to the viewer's own org
        # (services/page_templates.py): anyone in it may look, nobody writes.
        if is_system(user):
            return AppAccess(False, "none", False, "app not found")
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
        return AppAccess(True, "view", False)

    if is_builtin(app):
        from app.db.models import OrganizationMembership

        if is_system(user):
            return AppAccess(True, "view", True)
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
        return AppAccess(True, "view", True)

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
    # happens to exist too. A dashboard is changed by its owner only — anyone
    # it's shared with uses it as it is or makes a copy — whatever a share says.
    editable = getattr(app, "kind", "app") != "dashboard"
    return AppAccess(
        True,
        "edit" if editable and any(s.access == "edit" for s in matches) else "view",
        any(s.write_actions_approved for s in matches),
    )


def org_permissions(db: Session, user) -> set[str]:
    """The viewer's own org permissions — the ceiling on everything below.

    Platform admins bypass org roles everywhere else in Norm; they do here too,
    but nowhere else in this module is that shortcut taken.
    """
    from app.auth.permissions import ALL_ORG_PERMISSIONS
    from app.db.models import Role

    if getattr(user, "role", None) == "admin":
        return set(ALL_ORG_PERMISSIONS)
    membership = active_org.membership_for(db, user.id)
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
    """The action's HTTP method from its ConnectionSpec — the source of truth for
    'is this a write', so an app cannot relabel one."""
    from app.db.config_models import ConnectionSpec

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == connector)
        .first()
    )
    # An app calls TOOLS only (Sep 2026) — an endpoint is not an action.
    for tool in spec_rows.tools(spec):
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


def _tool_writes(config_db: Session, connector: str, action: str, method: str) -> bool:
    """Whether calling this tool from an app is a write (declared + approved).

    A tool's declared effect decides it: ``write`` is a write whatever its
    method — the GET-declared writers (report email, task changes, the invoice
    tools) used to pass as reads here. Drafts and reads are not. A row with no
    declared effect keeps the method rule below.
    """
    from app.db.config_models import ConnectionSpec

    try:
        spec = (
            config_db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == connector)
            .first()
        )
        row = spec_rows.find_tool(spec, action) if spec else None
    except Exception:  # noqa: BLE001 — unreadable: the method rule below decides
        row = None
    declared = spec_rows.effect(row)
    if declared is not None:
        return declared == "write"
    return method != "GET" and not _tool_read_only(config_db, connector, action)


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
    from app.db.config_models import ConnectionSpec

    try:
        spec = (
            config_db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == connector)
            .first()
        )
        if not spec or (spec.execution_mode or "") != "mcp":
            return False
        for tool in spec_rows.tools(spec):
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
    commit: bool = True,
) -> object:
    """Run ONE connector action on behalf of an app. The only data door.

    ``commit=False`` inside a logic run: the audit row then rides the run's
    own transaction, so a failed run still leaves nothing half-written."""
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
    if _tool_writes(config_db, connector, action, method):
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
                **_audit_app(app),
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
        if commit:
            db.commit()
        else:
            db.flush()
    except Exception as exc:  # noqa: BLE001 — audit must not fail the call
        if commit:
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
    # Norm's own apps change their data only through their own logic, where
    # their rules live (Training: who may set a manager; Performance: pay,
    # private review answers). Built-ins give every org member access, so a direct write
    # through the records/files endpoints would skip every one of those rules
    # — e.g. making yourself a colleague's manager to read their pay.
    if mutating and getattr(app, "builtin", False) and not _IN_LOGIC.get():
        raise HTTPException(403, f"changes to {app.name} go through the app itself")
    if (
        collection in set(_storage_spec(spec).get("logic_only") or [])
        and not _IN_LOGIC.get()
    ):
        raise HTTPException(
            403, f"'{collection}' is only available through the app itself"
        )
    # A built-in app's namespace is reserved platform-wide (see
    # services/builtin_apps.py) — refused here too, at run time, so no app
    # saved before the reservation existed can reach the built-ins' data.
    from app.services.builtin_apps import is_builtin, reserved_namespaces

    if namespace in reserved_namespaces() and not is_builtin(app):
        raise HTTPException(
            403, f"storage namespace '{namespace}' is reserved for Norm's own apps"
        )

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
        # A built-in serves every org from one codebase: a venue it tags a row
        # with must be one of THIS org's venues, whatever access rows say.
        if is_builtin(app):
            from app.db.models import Venue

            venue = db.query(Venue).filter(Venue.id == venue_id).first()
            if venue is None or venue.organization_id != app.organization_id:
                raise HTTPException(403, "that venue isn't in this organization")
    return namespace, app.organization_id


def _audit_app(app) -> dict:
    """Who an audit row is about. A built-in app has no ``apps`` row to point
    at, so it is named by slug instead (its org is on the viewer's own row)."""
    from app.services.builtin_apps import is_builtin
    from app.services.page_templates import is_template

    if is_builtin(app) or is_template(app):
        return {
            "app_id": None,
            "builtin_slug": app.slug,
            "organization_id": app.organization_id,
        }
    return {"app_id": app.id, "organization_id": app.organization_id}


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
                **_audit_app(app),
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
    group_wide: bool = False,
) -> dict:
    """Create a row, or replace the ``data`` of one this app can already see.

    An update is scoped by namespace + org + collection, so a caller cannot
    reach a row in another namespace by guessing its id. ``group_wide`` files
    the row under no venue — on create, or by clearing an update's venue —
    which ``venue_id=None`` can't express (None means "leave it as it is").
    """
    if group_wide:
        venue_id = None
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
        if group_wide:
            row.venue_id = None
        elif venue_id is not None:
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
    # First-come ownership (below) is per org, and a built-in app has no row
    # to claim with — so its namespace is reserved outright, platform-wide.
    from app.services.builtin_apps import reserved_namespaces

    if namespace in reserved_namespaces():
        raise HTTPException(
            403, f"storage namespace '{namespace}' is reserved for Norm's own apps"
        )
    # Each of Norm's templates keeps its records under its own name
    # (services/page_templates.bind), so no app can claim one.
    if namespace.startswith("template_"):
        raise HTTPException(
            403, f"storage namespace '{namespace}' is reserved for Norm's templates"
        )

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
    namespace, _ = _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=row.collection or "",
        mutating=False,
    )
    # By id, so the org and collection checks alone would let an app reach a
    # file in ANOTHER namespace that happens to use the same collection name.
    if row.namespace != namespace:
        raise HTTPException(404, "no such file")
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
    namespace, _ = _storage_guard(
        db,
        app=app,
        version=version,
        user=user,
        collection=row.collection or "",
        mutating=True,
    )
    # By id, so the org and collection checks alone would let an app reach a
    # file in ANOTHER namespace that happens to use the same collection name.
    if row.namespace != namespace:
        raise HTTPException(404, "no such file")
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


#: The AI agents with a Dashboard page — where a Dashboard can live.
DASHBOARD_AGENTS = (
    "reports",
    "procurement",
    "hr",
    "time_attendance",
    "executive_chef",
    "finance",
    "marketing",
)
KINDS = ("app", "dashboard")

KIND_QUESTION = (
    "say which kind this is: kind 'dashboard' (it sits on that AI agent's "
    "Dashboard page) or kind 'app' (its own page in that AI agent's menu). Go by "
    "the person's word — 'dashboard' or 'app'. If they said neither, ask them "
    "where it should sit before saving; if they don't mind, figures to look at "
    "are a dashboard and something people work through is an app."
)


def apply_edits(source: str | None, edits, what: str = "page") -> str:
    """``source`` with each ``{"find", "replace"}`` applied in turn.

    A change sent as edits leaves everything it doesn't name exactly as it is,
    which is the point: a whole page re-sent for a one-line change is how a
    chart went missing (21 Oct 2026). So each ``find`` must appear EXACTLY once
    in the text as it stands, or the whole save is refused and nothing moves."""
    text = source or ""
    if not isinstance(edits, list) or not edits:
        raise HTTPException(
            400, f"{what} edits must be a non-empty list of {{find, replace}}"
        )
    for i, e in enumerate(edits, 1):
        find = e.get("find") if isinstance(e, dict) else None
        if not isinstance(find, str) or not find:
            raise HTTPException(
                400, f"{what} edit {i}: needs a non-empty `find` and a `replace`"
            )
        n = text.count(find)
        if n != 1:
            shown = find[:80] + ("…" if len(find) > 80 else "")
            raise HTTPException(
                400,
                f"{what} edit {i}: `find` appears {n} times in the current {what}, "
                f"and it must appear exactly once: {shown!r}. Read it again with "
                "get_app and copy the text exactly, with enough around it to be "
                "unique. Nothing was saved.",
            )
        text = text.replace(find, str(e.get("replace") or ""), 1)
    return text


def owner_name(db: Session, app) -> str:
    from app.db.models import User

    owner = db.get(User, app.created_by) if app.created_by else None
    return (owner.full_name or owner.email) if owner else "someone else"


def not_yours(db: Session, app) -> str:
    """Why a person can't change a dashboard they're using, worded for Norm to
    act on: they use it as it is, or make their own copy."""
    return (
        f"'{app.name}' is {owner_name(db, app)}'s dashboard, shared with the "
        "organisation: only they can change it. The person can keep using it "
        "as it is (they get its updates), or make their own copy and change "
        "that. Ask whether they'd like a copy (manage_dashboard op 'copy'); "
        "don't change anything until they say yes."
    )


def current_version(db: Session, app):
    from app.db.models import AppVersion

    if app.current_version_id:
        v = db.get(AppVersion, app.current_version_id)
        if v is not None:
            return v
    return (
        db.query(AppVersion)
        .filter(AppVersion.app_id == app.id)
        .order_by(AppVersion.version.desc())
        .first()
    )


def save_app(db: Session, user, payload: dict) -> dict:
    """Create an app, or add an immutable version to one the user may edit.

    ONE implementation for both authors: the web endpoint (``routers/apps.py``)
    and the builder agent's ``save_app`` tool. The rule that matters is checked
    here so neither can drift: an author can only declare reach they themselves
    hold — the same intersection the door applies at call time, applied at save
    time so the refusal names the missing permission while it can still be
    fixed.

    A save of an existing app may leave out what it doesn't change: the name,
    the spec and the page carry over, and ``edits`` / ``logic_edits`` change
    just the parts they name (:func:`apply_edits`). A new one names its
    ``kind`` and its ``agent``; the kind changes only with ``change_kind``.
    """
    import copy
    import re

    from app.agents.registry import registered_domains
    from app.db.models import App, AppVersion

    membership = active_org.membership_for(db, user.id)
    if not membership:
        raise HTTPException(403, "no organization membership")

    name = str(payload.get("name") or "").strip()
    slug = str(payload.get("slug") or re.sub(r"[^a-z0-9]+", "-", name.lower())).strip(
        "-"
    )
    if not slug:
        raise HTTPException(400, "a name is required")
    # "template:<slug>" is how pages address Norm's templates
    # (services/page_templates.py); no app can take that form.
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        raise HTTPException(
            400, "an app's slug is lower-case letters, numbers and dashes"
        )
    # Built into Norm (services/builtin_apps.py): the slug is Norm's, so a
    # user-built app can't shadow Norm Hiring in anyone's menu.
    from app.services.builtin_apps import reserved_slugs

    if slug in reserved_slugs():
        raise HTTPException(
            400, f"'{slug}' is the name of an app built into Norm — pick another"
        )

    app = (
        db.query(App)
        .filter(App.organization_id == membership.organization_id, App.slug == slug)
        .first()
    )
    current = None
    if app is not None:
        access = resolve_access(db, app, user)
        if app.kind == "dashboard" and access.can_run and access.role != "owner":
            raise HTTPException(403, not_yours(db, app))
        if access.role not in ("owner", "edit"):
            raise HTTPException(403, "you cannot edit this app")
        current = current_version(db, app)

    name = name or (app.name if app is not None else "")
    if not name:
        raise HTTPException(400, "a name is required")
    if payload.get("spec") is not None:
        spec = payload["spec"]
    else:
        spec = copy.deepcopy(current.spec) if current is not None else {}
    spec = spec or {}

    def _source(key: str, edits_key: str, what: str):
        sent = payload.get(key)
        edits = payload.get(edits_key)
        if edits is not None:
            if sent is not None:
                raise HTTPException(400, f"send either {key} or {edits_key}, not both")
            if current is None:
                raise HTTPException(
                    400,
                    f"{edits_key} change an app that exists; a new app needs "
                    f"its whole {what} in {key}",
                )
            return apply_edits(getattr(current, key), edits, what)
        if sent is not None or key in payload or current is None:
            return sent or None
        return getattr(current, key)

    ui_source = _source("ui_source", "edits", "page")
    logic_source = _source("logic_source", "logic_edits", "logic")

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
    # The screens Norm may open directly, and where each can start (see
    # services/app_components.py). Optional — an app that declares none is one
    # component — but a declaration that's there must be well-formed, or Norm
    # would offer to open something the app can't honour.
    from app.services.app_components import validate_components

    problems = validate_components(spec)
    if problems:
        raise HTTPException(400, "; ".join(problems))

    missing = required_permissions(spec) - org_permissions(db, user)
    if missing:
        raise HTTPException(
            403,
            "an app cannot ask for more than you can do yourself — missing: "
            + ", ".join(sorted(missing)),
        )

    # Every app belongs to the AI agent whose job it does (owner, 21 Oct 2026,
    # docs/plans/apps-attached-to-agents.md): it sits in that agent's menu like
    # Norm's own apps, and the marketplace files it under that agent. The App
    # Builder is where apps are made, not where they live, and Home is not an
    # agent — an app that fits no agent goes under the nearest one. A slug with
    # no agent behind it is worse than useless: no sidebar button can select
    # it, and `page_context.agent` is taken on faith by the supervisor.
    agent = str(payload.get("agent") or "").strip() or None
    homes = sorted(d for d in registered_domains() if d != "app_builder")
    if agent == "app_builder":
        raise HTTPException(
            400,
            "an app belongs to the AI agent whose job it does, not the App "
            "Builder — pick the nearest of: " + ", ".join(homes),
        )
    if agent and agent not in homes:
        raise HTTPException(
            400,
            f"unknown agent '{agent}' — an app's pages must join one of: "
            + ", ".join(homes),
        )
    no_agent = HTTPException(
        400,
        "pick the AI agent whose job this does — it sits on that agent's "
        "Dashboard page or in its menu. The nearest of: " + ", ".join(homes),
    )

    # Dashboard or App: the person's word decides (docs/plans/
    # apps-and-dashboards-one-system.md §4). Changing an existing one's kind is
    # a deliberate step of its own, never a side effect of an edit.
    kind = str(payload.get("kind") or "").strip().lower() or None
    if kind is not None and kind not in KINDS:
        raise HTTPException(400, "kind is 'dashboard' or 'app'")
    change_kind = False
    if app is None:
        if kind is None:
            raise HTTPException(400, KIND_QUESTION)
        if agent is None:
            raise no_agent
    else:
        if agent is None and app.agent is None:
            raise no_agent
        if kind is not None and kind != app.kind:
            if not payload.get("change_kind"):
                raise HTTPException(
                    400,
                    f"'{app.name}' is a{'n' if app.kind == 'app' else ''} "
                    f"{app.kind}. Making it a{'n' if kind == 'app' else ''} "
                    f"{kind} is a step of its own: only when the person asks "
                    "for it, save again with change_kind: true.",
                )
            if access.role != "owner":
                raise HTTPException(
                    403, "only the person who made it can change what kind it is"
                )
            change_kind = True
    kind = kind or app.kind
    final_agent = agent or (app.agent if app is not None else None)
    if kind == "dashboard" and final_agent not in DASHBOARD_AGENTS:
        raise HTTPException(
            400,
            "a dashboard sits on an AI agent's Dashboard page — one of: "
            + ", ".join(DASHBOARD_AGENTS),
        )

    _check_namespace_claim(db, membership.organization_id, slug, spec)

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
            kind=kind,
        )
        db.add(app)
        db.flush()
    else:
        app.name = name
        if "description" in payload:
            app.description = payload.get("description")
        if "icon" in payload:
            app.icon = payload.get("icon")
        if agent:
            app.agent = agent
        if payload.get("purpose"):
            app.purpose = payload.get("purpose")
        if change_kind:
            _change_kind(db, app, kind)

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
        ui_source=ui_source,
        logic_source=logic_source,
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
        "kind": app.kind,
        "agent": app.agent,
        "reach": describe_reach(spec),
    }


def _change_kind(db: Session, app, kind: str) -> None:
    """App <-> Dashboard. A Dashboard is shared only with its organisation, to
    use as it is, so becoming one drops any share with named people or venues
    and any edit right, and takes it off the marketplace."""
    from app.db.models import AppShare

    app.kind = kind
    if kind != "dashboard":
        return
    for share in db.query(AppShare).filter(AppShare.app_id == app.id).all():
        if share.principal_type == "organization" and share.principal_id == (
            app.organization_id
        ):
            share.access = "view"
        else:
            db.delete(share)
    db.flush()
    org_shared = (
        db.query(AppShare)
        .filter(AppShare.app_id == app.id, AppShare.principal_type == "organization")
        .first()
    )
    app.visibility = "organization" if org_shared else "private"
    _withdraw_listing(app)


def _withdraw_listing(app) -> None:
    """Take an app off the marketplace (its listing is named after the app and
    its organisation — routers/marketplace.submit_app). Publishing it again,
    once it's an app again, puts it back up for approval."""
    from app.db.config_models import MarketplaceApp
    from app.db.engine import _ConfigSessionLocal

    cdb = _ConfigSessionLocal()
    try:
        row = (
            cdb.query(MarketplaceApp)
            .filter(
                MarketplaceApp.slug == f"{app.slug}-{app.organization_id[:8]}",
                MarketplaceApp.tier == "user",
            )
            .first()
        )
        if row is not None and row.status != "withdrawn":
            row.status = "withdrawn"
            cdb.commit()
    finally:
        cdb.close()


# ── Each person's own settings on a page (norm.mine) ─────────────────────

#: A person's settings on one page are a handful of choices, not data.
MINE_MAX_BYTES = 16_000


def viewer_key(app) -> str:
    """Whose settings row a page uses: the app's own id; a template or a
    built-in has no apps row, so it goes by its name."""
    from app.services.builtin_apps import is_builtin

    if is_builtin(app):
        return f"builtin:{app.slug}"
    return str(app.id)


def mine_get(db: Session, app, user) -> dict:
    from app.db.models import AppViewerSetting

    row = (
        db.query(AppViewerSetting)
        .filter(
            AppViewerSetting.app_key == viewer_key(app),
            AppViewerSetting.organization_id == app.organization_id,
            AppViewerSetting.user_id == user.id,
        )
        .first()
    )
    return dict(row.data or {}) if row else {}


def mine_set(db: Session, app, user, changes: dict) -> dict:
    """Merge ``changes`` into the person's own settings (a None value removes
    that one). Nobody else's screen changes. The caller commits."""
    import json

    from app.db.models import AppViewerSetting

    if not isinstance(changes, dict):
        raise HTTPException(400, "settings are an object of names and values")
    row = (
        db.query(AppViewerSetting)
        .filter(
            AppViewerSetting.app_key == viewer_key(app),
            AppViewerSetting.organization_id == app.organization_id,
            AppViewerSetting.user_id == user.id,
        )
        .first()
    )
    data = dict(row.data or {}) if row else {}
    for k, v in changes.items():
        if v is None:
            data.pop(str(k), None)
        else:
            data[str(k)] = v
    if len(json.dumps(data)) > MINE_MAX_BYTES:
        raise HTTPException(
            413, "too much to keep as one person's settings — keep choices small"
        )
    if row is None:
        row = AppViewerSetting(
            app_key=viewer_key(app),
            organization_id=app.organization_id,
            user_id=user.id,
            data=data,
        )
        db.add(row)
    else:
        row.data = data
    db.flush()
    return data


# ── History: every save is kept, and any version can be brought back ──────


def list_versions(db: Session, app) -> list[dict]:
    from app.db.models import AppVersion, User

    rows = (
        db.query(AppVersion)
        .filter(AppVersion.app_id == app.id)
        .order_by(AppVersion.version.desc())
        .all()
    )
    names = {
        u.id: (u.full_name or u.email)
        for u in db.query(User)
        .filter(User.id.in_({r.created_by for r in rows if r.created_by}))
        .all()
    }
    return [
        {
            "version": r.version,
            "current": r.id == app.current_version_id,
            "note": r.changelog,
            "by": names.get(r.created_by),
            "at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def restore_version(db: Session, app, user, number: int, access) -> dict:
    """Bring back version ``number`` as the newest version — nothing is ever
    overwritten, so bringing one back can itself be undone."""
    from app.db.models import AppVersion

    if access.role != "owner" and not (
        access.role == "edit" and getattr(app, "kind", "app") == "app"
    ):
        raise HTTPException(403, "only the person who made it can bring back a version")
    old = (
        db.query(AppVersion)
        .filter(AppVersion.app_id == app.id, AppVersion.version == int(number))
        .first()
    )
    if old is None:
        raise HTTPException(404, f"there is no version {number}")
    missing = required_permissions(old.spec or {}) - org_permissions(db, user)
    if missing:
        raise HTTPException(
            403,
            "that version can do more than you can yourself — missing: "
            + ", ".join(sorted(missing)),
        )
    last = (
        db.query(AppVersion)
        .filter(AppVersion.app_id == app.id)
        .order_by(AppVersion.version.desc())
        .first()
    )
    version = AppVersion(
        app_id=app.id,
        version=last.version + 1,
        spec=old.spec,
        ui_source=old.ui_source,
        logic_source=old.logic_source,
        changelog=f"Brought back version {old.version}",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    app.current_version_id = version.id
    db.commit()
    return {"slug": app.slug, "version": version.version, "from": old.version}


def viewer_of(db: Session, user, app) -> dict:
    """Who is running the app — set by the platform, read by the app's logic
    as ``params["_viewer"]``. The app maps ``user_id`` to its own people."""
    if is_system(user):
        return {
            "system": True,
            "user_id": None,
            "name": user.full_name,
            "email": None,
            "is_admin": False,
            "role": "system",
            "permissions": [],
        }
    from app.db.models import OrganizationMembership, Role

    role_name = None
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == app.organization_id,
        )
        .first()
    )
    perms: set[str] = set()
    if membership and membership.role_id:
        role = db.query(Role).filter(Role.id == membership.role_id).first()
        role_name = (role.display_name or role.name) if role else None
        perms = set((role.permissions if role else None) or [])
    if getattr(user, "role", None) == "admin":
        from app.auth.permissions import ALL_ORG_PERMISSIONS

        perms = set(ALL_ORG_PERMISSIONS)
    return {
        "system": False,
        "user_id": user.id,
        "name": getattr(user, "full_name", None) or getattr(user, "email", None),
        "email": getattr(user, "email", None),
        "is_admin": getattr(user, "role", None) == "admin",
        "role": role_name,
        # From the membership in THIS app's org — not the user's first org.
        "permissions": sorted(perms),
    }


def _email_door(db: Session, app, version, user, mode: str):
    """The sandbox's ``email`` object (services/app_email.py), bound to this
    app, org and viewer. ``person:<id>`` recipients resolve through the app's
    own ``people`` collection."""
    from app.services.app_email import AppEmail

    def person_email(person_id: str) -> str | None:
        try:
            row = store_get(
                db,
                app=app,
                version=version,
                user=user,
                collection="people",
                record_id=str(person_id),
            )
        except HTTPException:
            return None
        return (row or {}).get("email")

    door = AppEmail(
        db, app=app, version=version, user=user, mode=mode, person_email=person_email
    )
    # The sandbox gets plain functions only. Handing it the AppEmail instance
    # would hand it `.db` — the live session — since the sandbox only blocks
    # UNDERSCORED attribute access. Closures keep the instance out of reach.
    from types import SimpleNamespace

    def send(
        to,
        subject,
        html,
        key=None,
        kind=None,
        cc=None,
        reply_to=None,
        heading=None,
        button=None,
        sanitize=False,
    ):
        return door.send(
            to,
            subject,
            html,
            key=key,
            kind=kind,
            cc=cc,
            reply_to=reply_to,
            heading=heading,
            button=button,
            sanitize=sanitize,
        )

    def enabled():
        return door.enabled()

    def link(inputs=None):
        return door.link(inputs)

    def set_enabled(on):
        return door.set_enabled(on)

    def log(limit=200):
        return door.log(limit)

    def preview(log_id):
        return door.preview(log_id)

    return SimpleNamespace(
        send=send,
        enabled=enabled,
        link=link,
        set_enabled=set_enabled,
        log=log,
        preview=preview,
        mode=door.mode,
    )


def _calendar_door(db: Session, config_db, app, version, user, mode="live"):
    from app.services.app_calendar import calendar_door

    return calendar_door(db, config_db, app, version, user, mode)


def _pdf_door(db: Session, app, version, user):
    from app.services.app_pdf import pdf_door

    return pdf_door(db, app, version, user)


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
    builtin = bool(getattr(app, "builtin", False))
    raw = params or {}
    email_mode = "live"
    if builtin:
        # Norm's own apps: underscored keys are the platform's to set, never
        # the caller's — a client that sends `_viewer` must not get to say who
        # it is. The one exception is `_email_mode`, and only its two safe
        # values (dry_run / test). User-built apps keep their params exactly
        # as sent, and are never handed the viewer's identity.
        bound_params = {k: v for k, v in raw.items() if not str(k).startswith("_")}
        if raw.get("_email_mode") in ("dry_run", "test"):
            email_mode = raw["_email_mode"]
        if "_schedule" in raw and is_system(user):
            bound_params["_schedule"] = raw["_schedule"]
        bound_params["_viewer"] = viewer_of(db, user, app)
        bound_params["_email_mode"] = email_mode
    else:
        bound_params = {**raw}
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
            # Built-ins: the audit row rides the run's transaction. A
            # user-built app keeps its committed-per-call audit, so failing
            # the run afterwards can't erase the record of a write it made.
            commit=not builtin,
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
            # venue_id="" says "group-wide" explicitly: a new row with no venue
            # even while one is selected, or an update that clears its venue.
            if venue_id == "":
                return store_put(
                    db,
                    app=app,
                    version=version,
                    user=user,
                    collection=collection,
                    data=data,
                    record_id=record_id,
                    group_wide=True,
                )
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
    return _run_in_logic(
        execute_function,
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
        # Email is for Norm's own apps only (services/app_email.py).
        email_override=_email_door(db, app, version, user, email_mode)
        if isinstance(spec.get("email"), dict) and getattr(app, "builtin", False)
        else None,
        # Venue-mailbox calendar and candidate-pack PDFs: Norm's own apps only
        # (services/app_calendar.py, services/app_pdf.py).
        calendar_override=_calendar_door(db, config_db, app, version, user, email_mode)
        if isinstance(spec.get("calendar"), dict) and getattr(app, "builtin", False)
        else None,
        pdf_override=_pdf_door(db, app, version, user)
        if isinstance(spec.get("pdf"), dict) and getattr(app, "builtin", False)
        else None,
    )
