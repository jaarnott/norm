"""Install (or update) a fixture app for one organization.

The fixture lives in ``app/fixtures/apps/<slug>.{json,html,py}`` — the JSON is
the app row + spec, the HTML is the version's ``ui_source``, and the optional
``.py`` is its ``logic_source`` (the server-side ``run()``). Installing goes
through the same shape the save endpoint produces: a new immutable AppVersion
each run, ``current_version_id`` moved forward, visibility untouched.

Usage:
    uv run python scripts/install_fixture_app.py weekly-venue-performance \
        --org-email admin@norm.local
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "app" / "fixtures" / "apps"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument(
        "--org-email", required=True, help="a member; their org gets the app"
    )
    args = ap.parse_args()

    meta = json.loads((FIXTURES / f"{args.slug}.json").read_text())
    ui = (FIXTURES / f"{args.slug}.html").read_text()
    logic_path = FIXTURES / f"{args.slug}.py"
    logic = logic_path.read_text() if logic_path.exists() else None

    from app.db.engine import SessionLocal
    from app.db.models import App, AppVersion, OrganizationMembership, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.org_email).first()
        if not user:
            print(f"no user {args.org_email}")
            return 1
        membership = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == user.id)
            .first()
        )
        if not membership:
            print(f"{args.org_email} has no organization membership")
            return 1

        # Fixtures build the rows directly rather than going through
        # save_app, so the namespace rule has to be applied explicitly — a
        # first-party app must not be able to claim a namespace another app
        # owns just because it skipped the front door.
        from app.services.app_runtime import _check_namespace_claim

        _check_namespace_claim(
            db, membership.organization_id, meta["slug"], meta["spec"]
        )

        app = (
            db.query(App)
            .filter(
                App.organization_id == membership.organization_id,
                App.slug == meta["slug"],
            )
            .first()
        )
        if app is None:
            app = App(
                organization_id=membership.organization_id,
                created_by=user.id,
                slug=meta["slug"],
                name=meta["name"],
                description=meta.get("description"),
                icon=meta.get("icon"),
                agent=meta.get("agent"),
                purpose=meta.get("purpose"),
                visibility="private",
            )
            db.add(app)
            db.flush()
        else:
            app.name = meta["name"]
            app.description = meta.get("description")
            app.icon = meta.get("icon")
            if meta.get("agent"):
                app.agent = meta["agent"]

        last = (
            db.query(AppVersion)
            .filter(AppVersion.app_id == app.id)
            .order_by(AppVersion.version.desc())
            .first()
        )
        version = AppVersion(
            app_id=app.id,
            version=(last.version + 1) if last else 1,
            spec=meta["spec"],
            ui_source=ui,
            logic_source=logic,
            changelog="fixture install",
            created_by=user.id,
        )
        db.add(version)
        db.flush()
        app.current_version_id = version.id
        db.commit()
        print(
            f"installed '{app.name}' v{version.version} for org {app.organization_id}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
