"""scripts/switch_finance_apps.py — the switch from Loaded Payroll and Norm
Finance to Payroll, Entertainers and Budget (24 Oct 2026).

The Apps are opt-in, so an organisation keeps Finance through the catalog
change only if its old switch rows were copied first — on rows as well as off
rows (switch_to_job_apps.py copied only off rows; reusing it here would have
taken Finance away from Cook Brothers in production)."""

from __future__ import annotations

from app.db.config_models import MarketplaceApp
from app.db.models import OrgAppEntitlement, Role
from scripts import switch_finance_apps as sw
from tests.org_helpers import make_org


def _switch(db, org, slug, enabled):
    db.add(OrgAppEntitlement(organization_id=org.id, app_slug=slug, enabled=enabled))


def _switches(db, org) -> dict[str, bool]:
    return {
        e.app_slug: e.enabled
        for e in db.query(OrgAppEntitlement)
        .filter(OrgAppEntitlement.organization_id == org.id)
        .all()
    }


class TestSwitches:
    def test_on_and_off_rows_are_copied_and_the_old_ones_kept(self, db_session):
        cook = make_org(db_session, "Cook Bros")  # uses Finance today
        major = make_org(db_session, "Major Venues")  # switched Norm Finance off
        done = make_org(db_session, "Already Done")  # an earlier run, or an owner
        _switch(db_session, cook, "loaded-payroll", True)
        _switch(db_session, cook, "norm-finance", True)
        _switch(db_session, major, "norm-finance", False)
        _switch(db_session, done, "loaded-payroll", True)
        _switch(db_session, done, "entertainers", False)  # left as it is
        _switch(db_session, cook, "bills", False)  # not ours: untouched
        role = Role(
            organization_id=cook.id,
            name="payroll-office",
            display_name="Payroll office",
            permissions=[],
            apps=["bills", "loaded-payroll", "payroll", "norm-finance"],
        )
        plain = Role(
            organization_id=cook.id,
            name="kitchen",
            display_name="Kitchen",
            permissions=[],
            apps=["recipes-and-costing"],
        )
        db_session.add_all([role, plain])
        db_session.flush()

        # A dry run changes nothing and says what it would do.
        backup: dict = {}
        lines = sw.switches(db_session, apply=False, backup=backup)
        assert _switches(db_session, cook) == {
            "loaded-payroll": True,
            "norm-finance": True,
            "bills": False,
        }
        assert any(
            "switch payroll on (copied from loaded-payroll on)" in x for x in lines
        )
        assert any("entertainers already switched off, left alone" in x for x in lines)
        assert {r["app_slug"] for r in backup["org_app_entitlements"]} >= {
            "loaded-payroll",
            "norm-finance",
            "entertainers",
        }

        sw.switches(db_session, apply=True, backup={})
        db_session.flush()
        assert _switches(db_session, cook) == {
            "loaded-payroll": True,  # kept for a rollback (stage 5 removes it)
            "norm-finance": True,
            "payroll": True,
            "entertainers": True,
            "budget": True,
            "bills": False,
        }
        assert _switches(db_session, major) == {"norm-finance": False, "budget": False}
        assert _switches(db_session, done) == {
            "loaded-payroll": True,
            "payroll": True,
            "entertainers": False,  # an existing target is never overwritten
        }
        db_session.refresh(role)
        assert role.apps == ["bills", "payroll", "entertainers", "budget"]
        db_session.refresh(plain)
        assert plain.apps == ["recipes-and-costing"]

        # Idempotent: a second run copies nothing.
        again = sw.switches(db_session, apply=False, backup={})
        assert not any("switch " in x and "copied" in x for x in again)
        assert sw.missing_successors(db_session) == []

    def test_the_catalog_phase_waits_for_the_copies(self, db_session):
        org = make_org(db_session, "Waiting Co")
        _switch(db_session, org, "loaded-payroll", True)
        _switch(db_session, org, "payroll", True)
        db_session.flush()
        assert sw.missing_successors(db_session) == [
            f"org {org.id[:8]}: loaded-payroll has no entertainers switch"
        ]


class TestCatalogChecks:
    def test_the_new_rows_claim_every_finance_skill(self, db_session):
        """With the old rows gone and the seed's rows in place, the four
        skills are still claimed — checked before anything is written."""
        db_session.add(
            MarketplaceApp(
                slug="norm-finance",
                name="Norm Finance",
                description="",
                tier="app",
                bundled=False,
                composition={"member": "finance", "skills": ["budget_review"]},
            )
        )
        db_session.flush()
        assert sw.unclaimed_after(db_session) == []

    def test_a_skill_switching_off_is_caught(self):
        out = (
            "  spec 'norm_payroll': update display_name\n"
            "  update playbook 'budget_review' (instructions; on)\n"
            "  update playbook 'forecast_review' (enabled; off until its App claims it)\n"
            "(dry run — nothing written)\n"
        )
        assert sw.skills_switching_off(out) == [
            "update playbook 'forecast_review' (enabled; off until its App claims it)"
        ]
        assert sw.skills_switching_off("Finance config up to date") == []

    def test_the_seed_call_names_the_new_rows_and_bills(self):
        assert sw.SEED_SLUGS == ("payroll", "entertainers", "budget", "bills")
        assert set(sw.SEED_SLUGS) >= set(sw.CATALOG_SLUGS)
