"""Apps built into Norm: Norm Hiring and Norm Training (28 Sep 2026).

They run on the app platform — one sandboxed page, server-side logic, app
storage — but unlike an app someone builds, a built-in app is **part of Norm**:

* its code lives here in the repo (``app/builtin_apps/<slug>.{json,html,py}``)
  and ships with every deploy, so every organization runs the current code
  the moment it deploys — nothing is installed per org, and nothing needs
  syncing;
* it has no author, no versions and no shares, and nobody in an org can edit
  or re-save it; its slug is reserved so a user-built app can't shadow it;
* who can use it is the catalog's call, like Loaded Time: the org's catalog
  App is on (its team member hired, the App not switched off) — then anyone
  in the org can open it and change its data. (No permission scopes for now;
  declaring ``spec.scopes`` again brings the usual permission ceiling back.)

**Tenancy — the one rule that matters.** The same code serves every
organization, so which org's data it reaches must never come from the
request. :func:`bind` takes the org from the viewer's OWN membership, on the
server, and the runtime then filters every record, file and audit row on
``app.organization_id`` exactly as it does for any app. ``resolve_access``
re-checks that membership on every call rather than trusting the object.

**Namespaces are reserved platform-wide.** App storage belongs to whoever
claims a namespace first *within an org* — fine between an org's own apps,
but a built-in has no row to claim with, so a user-built app naming
``hr_suite`` would become its owner and read the candidate pipeline. Every
built-in's namespace is therefore reserved: saving a user-built app that
declares one is refused, and the storage door refuses one at run time too.

User-built apps are untouched by all of this — same rows, same authors, same
shares.
"""

from __future__ import annotations

import functools
import hashlib
import json
import pathlib
from dataclasses import dataclass
from types import SimpleNamespace

SOURCE_DIR = pathlib.Path(__file__).resolve().parent.parent / "builtin_apps"


@dataclass(frozen=True)
class BuiltinApp:
    slug: str
    name: str
    icon: str | None
    description: str | None
    purpose: str | None
    agent: str | None
    spec: dict
    ui_source: str
    logic_source: str | None
    build: str  # short hash of the code: what "version" means for a built-in


@functools.lru_cache(maxsize=1)
def builtin_apps() -> dict[str, BuiltinApp]:
    """Every built-in app, read once per process from the repo — so after
    editing one locally, restart the dev API (uvicorn's reloader only watches
    .py files); a deploy restarts everything anyway."""
    out: dict[str, BuiltinApp] = {}
    for meta_path in sorted(SOURCE_DIR.glob("*.json")):
        meta = json.loads(meta_path.read_text())
        slug = meta["slug"]
        ui = (SOURCE_DIR / f"{slug}.html").read_text()
        logic_path = SOURCE_DIR / f"{slug}.py"
        logic = logic_path.read_text() if logic_path.exists() else None
        digest = hashlib.sha256(
            (meta_path.read_text() + ui + (logic or "")).encode()
        ).hexdigest()[:8]
        out[slug] = BuiltinApp(
            slug=slug,
            name=meta.get("name") or slug.title(),
            icon=meta.get("icon"),
            description=meta.get("description"),
            purpose=meta.get("purpose"),
            agent=meta.get("agent"),
            spec=meta.get("spec") or {},
            ui_source=ui,
            logic_source=logic,
            build=digest,
        )
    return out


def get_builtin(slug: str | None) -> BuiltinApp | None:
    return builtin_apps().get(str(slug or ""))


def reserved_slugs() -> set[str]:
    return set(builtin_apps())


def reserved_namespaces() -> set[str]:
    return {
        ns
        for b in builtin_apps().values()
        if (ns := str((b.spec.get("storage") or {}).get("namespace") or "").strip())
    }


def is_builtin(app) -> bool:
    return bool(getattr(app, "builtin", False))


def bind(
    b: BuiltinApp, organization_id: str
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """The app and version objects the runtime works with, for ONE org.

    ``organization_id`` must come from the viewer's own membership (see
    :func:`load_for_user`) — it is the tenancy boundary for every query the
    runtime makes on this app's behalf."""
    app = SimpleNamespace(
        id=f"builtin:{b.slug}",
        builtin=True,
        organization_id=organization_id,
        slug=b.slug,
        name=b.name,
        icon=b.icon,
        description=b.description,
        purpose=b.purpose,
        agent=b.agent,
        created_by=None,
        created_at=None,
        archived_at=None,
        visibility="builtin",
        current_version_id=f"builtin:{b.slug}@{b.build}",
    )
    version = SimpleNamespace(
        id=app.current_version_id,
        app_id=app.id,
        version=0,
        build=b.build,
        spec=b.spec,
        ui_source=b.ui_source,
        logic_source=b.logic_source,
        changelog=None,
        created_by=None,
        created_at=None,
    )
    return app, version


def catalog_slug_for(builtin_slug: str, config_db) -> str | None:
    """The catalog App standing in front of a built-in (Norm Hiring -> hiring)."""
    from app.services.entitlements import _catalog

    for row in _catalog(config_db):
        if (
            row.tier == "app"
            and (row.composition or {}).get("app_slug") == builtin_slug
        ):
            return row.slug
    return None


def is_on(builtin_slug: str, organization_id: str, db, config_db) -> bool:
    """Is the org's catalog App for this built-in on? Gating inactive (no
    catalog tiers, fail-open) counts as on, like everywhere else."""
    from app.services.entitlements import apps_on

    on = apps_on(organization_id, db, config_db)
    if on is None:
        return True
    catalog_slug = catalog_slug_for(builtin_slug, config_db)
    # A built-in no catalog row fronts has nothing to switch it off.
    return catalog_slug is None or catalog_slug in on


def load_for_user(db, config_db, slug: str, user):
    """``(app, version)`` for a built-in, bound to the VIEWER's org — or None
    when ``slug`` isn't a built-in. Raises 404 when the viewer has no org or
    the org has the App switched off (no difference is disclosed)."""
    from fastapi import HTTPException

    from app.services.entitlements import org_id_for_user

    b = get_builtin(slug)
    if b is None:
        return None
    org = org_id_for_user(user.id, db)
    if not org or not is_on(b.slug, org, db, config_db):
        raise HTTPException(404, "app not found")
    return bind(b, org)
