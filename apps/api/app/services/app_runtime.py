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

import logging
import time
from dataclasses import dataclass

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

    method = _tool_method(config_db, connector, action)
    if method != "GET":
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

    name = str(payload.get("name") or "").strip()
    slug = str(payload.get("slug") or re.sub(r"[^a-z0-9]+", "-", name.lower())).strip(
        "-"
    )
    if not name or not slug:
        raise HTTPException(400, "a name is required")

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

    # The sandbox's own call_api reaches the connector layer directly, which
    # would make an app's logic a way around its own app's declared reach.
    # call_api_override routes it back through this module's door instead, so
    # UI and logic are checked identically.
    return execute_function(
        version.logic_source,
        bound_params,
        db,
        thread_id=None,
        options={
            "max_api_calls": int(spec.get("max_api_calls") or 20),
            "allowed_write_actions": [
                f"{e.get('connector')}.{e.get('action')}"
                for e in spec.get("writes") or []
                if isinstance(e, dict)
            ],
        },
        call_api_override=_call_api,
    )
