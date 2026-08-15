"""Install (or update) a fixture app for one organization.

The fixture pair lives in ``app/fixtures/apps/<slug>.{json,html}`` — the JSON
is the app row + spec, the HTML is the version's ``ui_source``. Installing goes
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
    ap.add_argument("--org-email", required=True, help="a member; their org gets the app")
    args = ap.parse_args()

    meta = json.loads((FIXTURES / f"{args.slug}.json").read_text())
    ui = (FIXTURES / f"{args.slug}.html").read_text()

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
                purpose=meta.get("purpose"),
                visibility="private",
            )
            db.add(app)
            db.flush()
        else:
            app.name = meta["name"]
            app.description = meta.get("description")
            app.icon = meta.get("icon")

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
            changelog="fixture install",
            created_by=user.id,
        )
        db.add(version)
        db.flush()
        app.current_version_id = version.id
        db.commit()
        print(f"installed '{app.name}' v{version.version} for org {app.organization_id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
