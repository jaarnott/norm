"""Switch the catalog from the two old Finance Apps to the three new ones
(24 Oct 2026, docs/plans/finance-menu-consolidation.md).

Loaded Payroll (``loaded-payroll``) became Payroll and Entertainers; Norm
Finance (``norm-finance``) became Budget (app/services/finance_core/catalog.py
``LEGACY_SLUGS``). All five are opt-in (``bundled=False``): an organisation
has one only while its switch row says so. So the switch rows must be copied
before the new catalog rows exist — otherwise, the moment they do, an
organisation using Finance today (Cook Brothers in production) loses every
Finance tool, skill and page. The code that knows the new Apps ships first
and bridges the gap (until a new App has a row of its own it counts as on
exactly when its old App is, and only while its own mode is on).

Two phases, in this order:

1. ``switches`` — this environment's app database (``DATABASE_URL``). Run it
   in EVERY environment (testing, staging, production), before the catalog
   phase runs anywhere:
   - every ``org_app_entitlements`` row for an old slug is copied to each
     new slug with the SAME ``enabled`` value — on rows and off rows alike
     (switch_to_job_apps.py copied only off rows, because its job apps were
     bundled; reusing it here would skip Cook Brothers). A new slug that
     already has a row is left alone. The old rows are KEPT, so a rollback
     to the old code still works; the restructure's stage-5 tidy-up deletes
     them;
   - a role whose ``apps`` list names an old slug gets the new slugs in its
     place.
   Early is harmless: entitlements ignore a switch row whose slug has no
   catalog row yet.

2. ``catalog`` — the SHARED config database, once, and only after
   production serves the new code:
   - refuses while this environment still has an old switch row without its
     new ones (and reminds you every environment's switches phase must have
     run), or if the new rows would leave one of the four Finance skills
     unclaimed;
   - runs ``sync_marketplace_catalog.py --only payroll,entertainers,budget,bills``
     (creates the three App rows; Bills hands the three payroll writes to
     Xero over to Payroll), then deletes the two old App rows — ``--only``
     never retires anything;
   - finally runs ``sync_norm_finance.py --dry-run`` and fails loudly if any
     Finance skill would switch off.

Dry-run by default, and a dry run opens its database sessions read-only, so
it can be pointed at production's read-only login. ``--apply`` writes, after
backing up everything it changes to
``~/norm-split-backups/finance-apps-switch-<UTC>.json``.

Usage (from apps/api, .env loaded; DATABASE_URL at the environment's app
database — production through the 5435 proxy):
    uv run python scripts/switch_finance_apps.py                    # both phases, dry run
    uv run python scripts/switch_finance_apps.py --phase switches --apply
    uv run python scripts/switch_finance_apps.py --phase catalog --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

API = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))

from app.services.finance_core.catalog import (  # noqa: E402 — path set above
    CATALOG_SLUGS,
    LEGACY_SLUGS,
)

#: The catalog rows the seed writes in the catalog phase.
SEED_SLUGS = ("payroll", "entertainers", "budget", "bills")
#: Rows backed up besides those: the agent the Apps belong to.
BACKUP_SLUGS = sorted(set(SEED_SLUGS) | set(LEGACY_SLUGS) | {"finance-agent"})
#: Every slug whose switch rows the script reads.
SWITCH_SLUGS = sorted(set(LEGACY_SLUGS) | CATALOG_SLUGS)
SKILL_OFF = "off until its App claims it"
BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"


def _new_apps(apps: list) -> list[str]:
    """A role's apps list with each old slug replaced by its new ones, in
    place and without repeats."""
    out: list[str] = []
    for a in apps:
        for n in LEGACY_SLUGS.get(a, (a,)):
            if n not in out:
                out.append(n)
    return out


def _org_names(db) -> dict[str, str]:
    from app.db.models import Organization

    # two columns, not the model: an app database a migration behind the
    # code must still be readable (the dry run is read-only)
    return dict(db.query(Organization.id, Organization.name).all())


def _roles_have_apps(db) -> bool:
    """Roles carry an ``apps`` list from the staff-access migration
    (bi4k6m8o0q2t) on; an older database has none to rewrite."""
    from sqlalchemy import inspect

    return any(c["name"] == "apps" for c in inspect(db.get_bind()).get_columns("roles"))


def switches(db, apply: bool, backup: dict) -> list[str]:
    """Copy the old Apps' switch rows to the new Apps and rewrite role app
    lists. Returns one line per change."""
    from app.db.models import OrgAppEntitlement, Role

    rows = (
        db.query(OrgAppEntitlement)
        .filter(OrgAppEntitlement.app_slug.in_(SWITCH_SLUGS))
        .order_by(OrgAppEntitlement.organization_id, OrgAppEntitlement.app_slug)
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
    names = _org_names(db)
    have = {(r.organization_id, r.app_slug): r.enabled for r in rows}
    lines = []
    for r in rows:
        if r.app_slug not in LEGACY_SLUGS:
            continue
        org = f"{names.get(r.organization_id, '?')} ({r.organization_id[:8]})"
        state = "on" if r.enabled else "off"
        for new in LEGACY_SLUGS[r.app_slug]:
            if (r.organization_id, new) in have:
                kept = "on" if have[(r.organization_id, new)] else "off"
                lines.append(f"{org}: {new} already switched {kept}, left alone")
                continue
            lines.append(
                f"{org}: switch {new} {state} (copied from {r.app_slug} {state})"
            )
            have[(r.organization_id, new)] = r.enabled
            if apply:
                db.add(
                    OrgAppEntitlement(
                        organization_id=r.organization_id,
                        app_slug=new,
                        enabled=r.enabled,
                    )
                )
    touched = []
    roles = []
    if _roles_have_apps(db):
        roles = (
            db.query(Role.id, Role.organization_id, Role.display_name, Role.apps)
            .order_by(Role.organization_id, Role.name)
            .all()
        )
    else:
        lines.append("roles carry no apps list in this database yet: none to rewrite")
    for role_id, org_id, display_name, role_apps in roles:
        apps = list(role_apps or [])
        if not set(apps) & set(LEGACY_SLUGS):
            continue
        new_apps = _new_apps(apps)
        touched.append({"id": role_id, "organization_id": org_id, "apps": apps})
        lines.append(
            f"role {display_name} ({names.get(org_id, 'system')}): "
            f"apps {apps} -> {new_apps}"
        )
        if apply:
            db.query(Role).filter(Role.id == role_id).update(
                {Role.apps: new_apps}, synchronize_session=False
            )
    backup["roles"] = touched
    if apply:
        db.flush()
    return lines


def missing_successors(db) -> list[str]:
    """Old switch rows in this environment whose new rows are not all there
    yet — the catalog phase refuses while there are any."""
    from app.db.models import OrgAppEntitlement

    rows = (
        db.query(OrgAppEntitlement)
        .filter(OrgAppEntitlement.app_slug.in_(SWITCH_SLUGS))
        .all()
    )
    have = {(r.organization_id, r.app_slug) for r in rows}
    out = []
    for r in rows:
        for new in LEGACY_SLUGS.get(r.app_slug, ()):
            if (r.organization_id, new) not in have:
                out.append(
                    f"org {r.organization_id[:8]}: {r.app_slug} has no {new} switch"
                )
    return out


def _seed():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed", API / "scripts" / "sync_marketplace_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def unclaimed_after(cdb) -> list[str]:
    """The Finance skills no active row would claim once the catalog phase
    has run: the old rows gone, the seed's rows for SEED_SLUGS in place."""
    from app.db.config_models import MarketplaceApp
    from scripts.sync_norm_finance import PLAYBOOKS

    claimed: set[str] = set()
    for row in cdb.query(MarketplaceApp).filter(MarketplaceApp.status == "active"):
        if row.slug not in LEGACY_SLUGS and row.slug not in SEED_SLUGS:
            claimed |= set((row.composition or {}).get("skills") or [])
    for app in _seed().APPS:
        if app["slug"] in SEED_SLUGS:
            claimed |= set(app["composition"].get("skills") or [])
    return sorted(set(PLAYBOOKS) - claimed)


def skills_switching_off(output: str) -> list[str]:
    """The lines of a sync_norm_finance.py dry run that would switch a Finance
    skill off (or create one off)."""
    return [line.strip() for line in output.splitlines() if SKILL_OFF in line]


def _read_only(session) -> None:
    from sqlalchemy import text

    session.execute(text("SET TRANSACTION READ ONLY"))


def _rows(cdb, slugs) -> list[dict]:
    from app.db.config_models import MarketplaceApp

    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in cdb.query(MarketplaceApp).filter(MarketplaceApp.slug.in_(slugs))
    ]


def catalog(db, apply: bool, backup: dict, *, assume_copied: bool = False) -> None:
    """The catalog phase (see the module docstring). Raises SystemExit when
    it must not go ahead. ``assume_copied``: a dry run of both phases, where
    the switches phase wrote nothing — judge as if its copies were made."""
    from app.db.config_models import MarketplaceApp, Playbook
    from app.db.engine import _ConfigSessionLocal
    from scripts.sync_norm_finance import PLAYBOOKS

    tag = "" if apply else "[dry-run] "
    gaps = [] if assume_copied else missing_successors(db)
    if gaps:
        raise SystemExit(
            "refusing the catalog phase: run --phase switches here first\n  "
            + "\n  ".join(gaps)
        )
    print(
        f"{tag}reminder: the switches phase must have run in EVERY environment's "
        "app database (testing, staging, production) — this run can check only "
        "this one."
    )
    cdb = _ConfigSessionLocal()
    try:
        if not apply:
            _read_only(cdb)
        lost = unclaimed_after(cdb)
        if lost:
            raise SystemExit(
                "refusing the catalog phase: these skills would be claimed by no "
                f"App afterwards: {', '.join(lost)}"
            )
        backup["catalog"] = _rows(cdb, BACKUP_SLUGS)
        backup["playbooks"] = {
            pb.slug: pb.enabled
            for pb in cdb.query(Playbook).filter(Playbook.slug.in_(list(PLAYBOOKS)))
        }
    finally:
        cdb.rollback()
        cdb.close()
    if apply:
        _write_backup(backup, "catalog")

    cmd = [
        sys.executable,
        str(API / "scripts" / "sync_marketplace_catalog.py"),
        "--only",
        ",".join(SEED_SLUGS),
    ] + ([] if apply else ["--dry-run"])
    print(f"{tag}{' '.join(cmd[1:])}")
    subprocess.run(cmd, check=True, cwd=API)

    cdb = _ConfigSessionLocal()
    try:
        if not apply:
            _read_only(cdb)
        for slug in LEGACY_SLUGS:
            row = cdb.query(MarketplaceApp).filter(MarketplaceApp.slug == slug).first()
            if row is not None:
                print(f"{tag}delete the {slug} App row")
                if apply:
                    cdb.delete(row)
        if apply:
            cdb.commit()
            from app.services.agent_catalog import invalidate_cache

            invalidate_cache()
        else:
            cdb.rollback()
    finally:
        cdb.close()

    out = subprocess.run(
        [sys.executable, str(API / "scripts" / "sync_norm_finance.py"), "--dry-run"],
        check=True,
        cwd=API,
        capture_output=True,
        text=True,
    ).stdout
    print("sync_norm_finance.py --dry-run:")
    print(out.rstrip())
    off = skills_switching_off(out)
    if off:
        raise SystemExit(
            "STOP: a Finance skill would switch off — restore the catalog from the "
            "backup or claim it before running sync_norm_finance.py:\n  "
            + "\n  ".join(off)
        )
    print("the four Finance skills stay on")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--phase", choices=("switches", "catalog", "all"), default="all"
    )
    args = parser.parse_args()
    tag = "" if args.apply else "[dry-run] "
    # our lines and the sub-scripts' output, in the order they happen
    sys.stdout.reconfigure(line_buffering=True)

    from app.db.engine import SessionLocal

    backup: dict = {}
    db = SessionLocal()
    try:
        if not args.apply:
            _read_only(db)
        assume_copied = False
        if args.phase in ("switches", "all"):
            lines = switches(db, args.apply, backup)
            for line in lines or ["no switch rows or roles name the old Finance Apps"]:
                print(tag + line)
            if args.apply:
                _write_backup(backup, "switches")
                db.commit()
            else:
                db.rollback()
                if args.phase == "all":
                    assume_copied = True
                    if lines:
                        print(
                            "[dry-run] the catalog phase below is judged as if "
                            "these copies had been made"
                        )
        if args.phase in ("catalog", "all"):
            if not args.apply:
                _read_only(db)
            catalog(db, args.apply, backup, assume_copied=assume_copied)
    finally:
        db.rollback()
        db.close()


def _write_backup(backup: dict, phase: str) -> pathlib.Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"finance-apps-switch-{phase}-{stamp}.json"
    path.write_text(json.dumps(backup, indent=1, default=str))
    print(f"backup {path}")
    return path


if __name__ == "__main__":
    main()
