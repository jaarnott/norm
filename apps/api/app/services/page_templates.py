"""Norm's ready-made dashboards: pages every organisation can use as they are.

A template is a page of code, like an App (config_models.PageTemplate). It is
served the way a built-in app is (services/builtin_apps.py): one definition,
bound on the server to the VIEWER's own organisation, so the figures it reads
and any records it shows are that organisation's. Three differences:

* it is for looking at: nobody's role is above ``view`` and it never writes
  (no approved writes, no email, calendar or PDF doors), so a person's own
  choices on it go in their viewer settings (``norm.mine``), not its records;
* it lives in the shared config DB and is published from a platform admin's
  own dashboard, so it can be improved without a release — and everyone using
  it gets the improvement, because using a template never copies it;
* a person who wants to change it makes a copy, which is their own Dashboard.

Pages address a template as ``template:<slug>``; a user-built app may not take
that name (save_app refuses a slug with a colon).
"""

from __future__ import annotations

from types import SimpleNamespace

PREFIX = "template:"


def is_template_slug(slug: str | None) -> bool:
    return str(slug or "").startswith(PREFIX)


def plain(slug: str) -> str:
    return slug[len(PREFIX) :] if is_template_slug(slug) else slug


def is_template(app) -> bool:
    return bool(getattr(app, "template", False))


def get(config_db, slug: str, *, enabled_only: bool = True):
    from app.db.config_models import PageTemplate

    q = config_db.query(PageTemplate).filter(PageTemplate.slug == plain(slug))
    if enabled_only:
        q = q.filter(PageTemplate.enabled.is_(True))
    return q.first()


def for_agent(config_db, agent_slug: str) -> list:
    from app.db.config_models import PageTemplate

    return (
        config_db.query(PageTemplate)
        .filter(PageTemplate.agent_slug == agent_slug, PageTemplate.enabled.is_(True))
        .order_by(PageTemplate.name)
        .all()
    )


def bind(t, organization_id: str) -> tuple[SimpleNamespace, SimpleNamespace]:
    """The app and version objects the runtime works with, for ONE org.

    ``organization_id`` must come from the viewer's own membership — it is the
    tenancy boundary for every query the runtime makes for this page."""
    app = SimpleNamespace(
        id=f"{PREFIX}{t.slug}",
        template=True,
        builtin=False,
        kind="dashboard",
        organization_id=organization_id,
        slug=f"{PREFIX}{t.slug}",
        name=t.name,
        icon=t.icon,
        description=t.description,
        purpose=None,
        agent=t.agent_slug,
        created_by=None,
        created_at=None,
        archived_at=None,
        visibility="template",
        current_version_id=f"{PREFIX}{t.slug}@{t.version}",
    )
    # Its own storage name, whatever the dashboard it was published from used:
    # a template must never read the records of an app in the viewer's org
    # that happens to share that name.
    spec = dict(t.spec or {})
    if spec.get("storage"):
        spec["storage"] = {**spec["storage"], "namespace": f"template_{t.slug}"}
    version = SimpleNamespace(
        id=app.current_version_id,
        app_id=app.id,
        version=t.version,
        build=None,
        spec=spec,
        ui_source=t.ui_source,
        logic_source=t.logic_source,
        changelog=None,
        created_by=None,
        created_at=t.updated_at,
    )
    return app, version


def load_for_user(db, config_db, slug: str, user):
    """``(app, version)`` for a template bound to the viewer's org, or None when
    ``slug`` isn't a template name. 404 when there's no such template or the
    viewer has no organisation (no difference is disclosed)."""
    from fastapi import HTTPException

    from app.services import active_org

    if not is_template_slug(slug):
        return None
    t = get(config_db, slug)
    org = active_org.org_id_for(db, user.id)
    if t is None or not org:
        raise HTTPException(404, "app not found")
    return bind(t, org)


def publish(config_db, *, slug: str, app, version, user_id: str | None, note=None):
    """Make ``app`` (a platform admin's own Dashboard, at ``version``) the
    template ``slug``: a new template, or that template's next version.
    Returns the template row. The caller commits."""
    from app.db.config_models import PageTemplate, PageTemplateVersion

    slug = plain(slug).strip()
    t = config_db.query(PageTemplate).filter(PageTemplate.slug == slug).first()
    spec = dict(version.spec or {})
    if t is None:
        t = PageTemplate(
            slug=slug,
            agent_slug=app.agent,
            name=app.name,
            description=app.description,
            icon=app.icon,
            spec=spec,
            ui_source=version.ui_source or "",
            logic_source=version.logic_source,
            version=1,
            enabled=True,
        )
        config_db.add(t)
    else:
        t.version = (t.version or 0) + 1
        t.agent_slug = app.agent
        t.name = app.name
        t.description = app.description
        t.icon = app.icon
        t.spec = spec
        t.ui_source = version.ui_source or ""
        t.logic_source = version.logic_source
    t.source_org_id = app.organization_id
    t.source_slug = app.slug
    t.updated_by = user_id
    config_db.add(
        PageTemplateVersion(
            slug=slug,
            version=t.version,
            spec=spec,
            ui_source=t.ui_source,
            logic_source=t.logic_source,
            changelog=note or version.changelog,
            created_by=user_id,
        )
    )
    config_db.flush()
    return t
