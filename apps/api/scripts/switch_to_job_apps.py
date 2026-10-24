"""Switch the catalog to the job apps (owner, 23 Oct 2026).

docs/plans/agents-and-apps-restructure.md. The code that knows the job apps
ships first (it treats each one as on while the App it comes from is —
entitlements.SUCCESSORS); this then, in order:

1. organisations' switches (app database): where an owner switched an old App
   off, the Apps it splits into are switched off too; then the old Apps'
   rows go;
2. the config database: Bidfood is removed (its connector, its binding to
   Procurement) and the Brevo email-campaign skill, whose App goes, is
   switched off;
3. open_app's description names Hiring, Training and Reviews
   (scripts/sync_app_components.py);
4. the catalog: the App rows from sync_marketplace_catalog.py, ONLY (its
   --only), so the agent rows' held-back prices are left alone; then the old
   App rows are deleted. The three Finance Apps (Payroll, Entertainers,
   Budget — finance_core/catalog.py CATALOG_SLUGS) are left out: they are
   opt-in, and only scripts/switch_finance_apps.py writes them, after
   copying each organisation's Loaded Payroll / Norm Finance switches.
   Writing them here would switch Finance off for every organisation using
   it, and would leave the two old Finance rows in place.

Everything it changes is backed up to ~/norm-split-backups first. Dry-run by
default; --apply writes. Run with DATABASE_URL at the environment's app
database (production: through the 5435 proxy) — the config database is shared.

Usage (from apps/api, .env loaded):
    uv run python scripts/switch_to_job_apps.py
    uv run python scripts/switch_to_job_apps.py --apply
"""

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

API = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))

#: The old Apps and the job apps that replace them.
from app.services.entitlements import SUCCESSORS  # noqa: E402
from app.services.finance_core.catalog import (  # noqa: E402
    CATALOG_SLUGS as FINANCE_SLUGS,
)

OLD_APPS = [
    "loaded-stock",
    "loaded-kitchen",
    "loaded-time",
    "loaded-reports",
    "xero",
    "bamboohr-app",
    "bidfood-app",
    "brevo-app",
    "metricool-app",
]
BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"


def _seed():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed", API / "scripts" / "sync_marketplace_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def switches(db, apply: bool, backup: dict) -> list[str]:
    from app.db.models import OrgAppEntitlement

    lines = []
    rows = (
        db.query(OrgAppEntitlement)
        .filter(OrgAppEntitlement.app_slug.in_(OLD_APPS))
        .all()
    )
    backup["org_app_entitlements"] = [
        {
            "organization_id": r.organization_id,
            "app_slug": r.app_slug,
            "enabled": r.enabled,
        }
        for r in rows
    ]
    have = {(r.organization_id, r.app_slug) for r in db.query(OrgAppEntitlement).all()}
    for r in rows:
        if r.enabled:
            continue
        for new in SUCCESSORS.get(r.app_slug, ()):
            if (r.organization_id, new) in have:
                continue
            lines.append(
                f"org {r.organization_id[:8]}: {new} off (was {r.app_slug} off)"
            )
            if apply:
                db.add(
                    OrgAppEntitlement(
                        organization_id=r.organization_id, app_slug=new, enabled=False
                    )
                )
    for r in rows:
        lines.append(f"org {r.organization_id[:8]}: remove the {r.app_slug} switch")
        if apply:
            db.delete(r)
    return lines


def config(cdb, apply: bool, backup: dict) -> list[str]:
    from app.db.config_models import AgentConnectionBinding, ConnectionSpec, Playbook

    lines = []
    spec = (
        cdb.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == "bidfood")
        .first()
    )
    bindings = (
        cdb.query(AgentConnectionBinding)
        .filter(AgentConnectionBinding.connector_name == "bidfood")
        .all()
    )
    if spec is not None:
        backup["bidfood_spec"] = {
            c.name: getattr(spec, c.name) for c in spec.__table__.columns
        }
        lines.append("remove the Bidfood connector")
        if apply:
            cdb.delete(spec)
    for b in bindings:
        backup.setdefault("bidfood_bindings", []).append(
            {c.name: getattr(b, c.name) for c in b.__table__.columns}
        )
        lines.append(f"remove the {b.agent_slug} → Bidfood binding")
        if apply:
            cdb.delete(b)
    pb = cdb.query(Playbook).filter(Playbook.slug == "email_campaign_builder").first()
    if pb is not None and pb.enabled:
        backup["email_campaign_builder_enabled"] = True
        lines.append(
            "switch off the email_campaign_builder skill (its App, Brevo, goes)"
        )
        if apply:
            pb.enabled = False
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import MarketplaceApp
    from app.db.engine import SessionLocal, _ConfigSessionLocal

    seed = _seed()
    # never the Finance Apps: switch_finance_apps.py writes those (docstring)
    app_slugs = [
        a["slug"]
        for a in seed.APPS
        if a["tier"] == "app" and a["slug"] not in FINANCE_SLUGS
    ]
    db, cdb = SessionLocal(), _ConfigSessionLocal()
    backup: dict = {
        "catalog": [
            {c.name: getattr(r, c.name) for c in r.__table__.columns}
            for r in cdb.query(MarketplaceApp).all()
        ]
    }
    try:
        lines = switches(db, args.apply, backup) + config(cdb, args.apply, backup)
        for line in lines:
            print(("" if args.apply else "[dry-run] ") + line)
        if args.apply:
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = BACKUP_DIR / f"job-apps-switch-{stamp}.json"
            path.write_text(json.dumps(backup, indent=1, default=str))
            print(f"backup {path}")
            db.commit()
            cdb.commit()
        else:
            db.rollback()
            cdb.rollback()
    finally:
        db.close()
        cdb.close()

    # 3. open_app's description
    from scripts.tool_row_updates import RowUpdate, apply_row_updates, load_script

    comp = load_script("sync_app_components")
    apply_row_updates(
        [
            RowUpdate(
                "norm",
                "open_app",
                fields={
                    "description": comp.OPEN_APP["description"],
                    "field_schema": comp.OPEN_APP["field_schema"],
                },
            )
        ],
        dry_run=not args.apply,
        label="open-app-job-apps",
    )

    # 4. the App rows only (the agent rows' prices stay as they are)
    cmd = [
        sys.executable,
        str(API / "scripts" / "sync_marketplace_catalog.py"),
        "--only",
        ",".join(app_slugs),
    ] + ([] if args.apply else ["--dry-run"])
    subprocess.run(cmd, check=True, cwd=API)

    cdb = _ConfigSessionLocal()
    try:
        for slug in OLD_APPS:
            row = cdb.query(MarketplaceApp).filter(MarketplaceApp.slug == slug).first()
            if row is not None:
                print(
                    ("" if args.apply else "[dry-run] ") + f"delete the {slug} App row"
                )
                if args.apply:
                    cdb.delete(row)
        if args.apply:
            cdb.commit()
            from app.services.agent_catalog import invalidate_cache

            invalidate_cache()
        else:
            cdb.rollback()
    finally:
        cdb.close()


if __name__ == "__main__":
    main()
