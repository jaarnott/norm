"""Finance Phase 0 — the four scopes, who holds them, the module switches on
organisations and venues, and the migration that writes the scopes onto every
organisation's system roles (plan docs/plans/finance-migration.md §5.2 item 1,
§9 "Security"; owner decision D8).

Every signed-in check here is a NON-admin user: a platform admin bypasses org
permissions everywhere, so a test as admin@norm.local proves nothing.
"""

import importlib.util
import pathlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import require_permission
from app.auth.permissions import (
    ALL_ORG_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSION_SCOPES,
    PLATFORM_ADMIN_SCOPES,
    STANDARD_ROLES,
)
from app.db.engine import get_db
from app.db.models import Organization, Role, User, Venue
from app.mcp.scopes import MCP_SCOPES, scopes_grantable_by
from tests.conftest import _make_membership
from tests.org_helpers import auth_headers, make_org, make_user, seed_system_roles

FOUR = {"payroll:read", "payroll:write", "finance:read", "budgets:write"}
PAY = {"payroll:read", "payroll:write"}
FIN = {"finance:read", "budgets:write"}

MIGRATION = (
    pathlib.Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "kb6e8g0i2k4m_finance_scopes_and_modes.py"
)


def _perms(role: str) -> set[str]:
    return set(STANDARD_ROLES[role]["permissions"])


class TestScopes:
    def test_the_four_scopes_exist_in_two_groups(self):
        assert FOUR <= PERMISSION_SCOPES
        assert PERMISSION_GROUPS["Payroll"] == ["payroll:read", "payroll:write"]
        assert PERMISSION_GROUPS["Finance"] == ["finance:read", "budgets:write"]
        grouped = [s for scopes in PERMISSION_GROUPS.values() for s in scopes]
        assert len(grouped) == len(set(grouped))
        assert set(grouped) == PERMISSION_SCOPES - PLATFORM_ADMIN_SCOPES
        assert FOUR <= set(ALL_ORG_PERMISSIONS)

    def test_owner_and_payroll_admin_hold_all_four(self):
        assert FOUR <= _perms("owner")
        assert FOUR <= _perms("payroll_admin")
        assert "finances" in STANDARD_ROLES["payroll_admin"]["description"]

    def test_a_manager_sees_the_finances_but_never_pay(self):
        """The manager rule grants every new scope by default — payroll:* is
        named in its exclusion set (D8: a venue manager does not open the
        Payrun for their own venue)."""
        assert FIN <= _perms("manager")
        assert not PAY & _perms("manager")

    def test_a_team_member_holds_none(self):
        assert not FOUR & _perms("team_member")


class TestMcpScopes:
    def test_two_read_scopes_on_the_org_scopes(self):
        pay, fin = MCP_SCOPES["mcp:payroll:read"], MCP_SCOPES["mcp:finance:read"]
        assert (
            pay.requires == frozenset({"payroll:read"}) and pay.access_level == "read"
        )
        assert (
            fin.requires == frozenset({"finance:read"}) and fin.access_level == "read"
        )

    def test_grantable_follows_the_role(self):
        manager = scopes_grantable_by(_perms("manager"))
        assert "mcp:finance:read" in manager and "mcp:payroll:read" not in manager
        assert {"mcp:finance:read", "mcp:payroll:read"} <= scopes_grantable_by(
            _perms("owner")
        )


class TestModelColumns:
    def test_defaults_are_off_april_and_trading(self, db_session):
        org = make_org(db_session, "Defaults Co")
        venue = Venue(id=str(uuid.uuid4()), name="V", organization_id=org.id)
        db_session.add(venue)
        db_session.flush()
        db_session.refresh(org)
        db_session.refresh(venue)
        assert (org.payroll_mode, org.entertainers_mode, org.finance_mode) == (
            "off",
            "off",
            "off",
        )
        assert org.fiscal_year_start_month == 4
        assert org.payroll_contact_email is None
        assert venue.is_trading is True

    @pytest.mark.parametrize(
        "column, value",
        [
            ("payroll_mode", "maybe"),
            ("entertainers_mode", "on"),
            ("finance_mode", ""),
            ("fiscal_year_start_month", 13),
            ("fiscal_year_start_month", 0),
        ],
    )
    def test_the_checks_refuse_a_bad_value(self, db_session, column, value):
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.add(
                    Organization(
                        id=str(uuid.uuid4()),
                        name="Bad",
                        slug=f"bad-{uuid.uuid4().hex[:6]}",
                        **{column: value},
                    )
                )
                db_session.flush()


def _probe_client(db_session, scope: str) -> TestClient:
    """A one-route app behind ``require_permission(scope)`` — what every
    /api/payroll/* and /api/finance/* router will gate on."""
    api = FastAPI()

    @api.get("/probe")
    def probe(user: User = Depends(require_permission(scope))):
        return {"ok": user.id}

    def _db():
        yield db_session

    api.dependency_overrides[get_db] = _db
    return TestClient(api)


def _member(db, org, role):
    user = make_user(db)
    membership = _make_membership(db, user, org)
    membership.role_id = role.id
    db.flush()
    return user


class TestRequirePermissionRoutes:
    """The plan's Phase 0 gate: a non-admin owner 200, a manager 403 on a
    payroll:read route — the system roles as seeded from STANDARD_ROLES."""

    def test_owner_and_payroll_admin_pass_manager_and_team_member_do_not(
        self, db_session
    ):
        roles = seed_system_roles(db_session)
        org = make_org(db_session, "Gate Co")
        client = _probe_client(db_session, "payroll:read")
        status = {
            name: client.get(
                "/probe", headers=auth_headers(_member(db_session, org, roles[name]))
            ).status_code
            for name in ("owner", "manager", "payroll_admin", "team_member")
        }
        assert status == {
            "owner": 200,
            "manager": 403,
            "payroll_admin": 200,
            "team_member": 403,
        }

    def test_a_manager_passes_finance_read(self, db_session):
        roles = seed_system_roles(db_session)
        org = make_org(db_session, "Gate Co")
        client = _probe_client(db_session, "finance:read")
        assert (
            client.get(
                "/probe",
                headers=auth_headers(_member(db_session, org, roles["manager"])),
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/probe",
                headers=auth_headers(_member(db_session, org, roles["team_member"])),
            ).status_code
            == 403
        )


def _load_migration():
    spec = importlib.util.spec_from_file_location("fin_scopes_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_old_roles(db) -> dict[str, Role]:
    """The system roles as a database seeded BEFORE Phase 0 holds them: no
    payroll or finance scope on any of them, plus one custom role that
    already carries payroll:read (an owner's edit the downgrade must undo)."""
    roles = {}
    for name, defn in STANDARD_ROLES.items():
        role = Role(
            id=str(uuid.uuid4()),
            name=name,
            display_name=defn["display_name"],
            is_system=True,
            permissions=[p for p in defn["permissions"] if p not in FOUR],
            organization_id=None,
        )
        db.add(role)
        roles[name] = role
    org = make_org(db, "Custom Co")
    custom = Role(
        id=str(uuid.uuid4()),
        name="pay-clerk",
        display_name="Pay clerk",
        is_system=False,
        permissions=["reports:read", "payroll:read"],
        organization_id=org.id,
    )
    db.add(custom)
    roles["custom"] = custom
    db.flush()
    return roles


def _held(db, role_id) -> set[str]:
    db.expire_all()
    return set(db.query(Role).filter(Role.id == role_id).one().permissions or [])


class TestRoleBackfillMigration:
    """kb6e8g0i2k4m writes the scopes onto the system roles — roles are rows
    seeded once, so STANDARD_ROLES alone never reaches production; without
    this every real owner 403s on the first Finance page."""

    def _run(self, db, fn):
        ctx = MigrationContext.configure(db.connection())
        with Operations.context(ctx):
            fn()

    def test_upgrade_grants_per_role_and_is_idempotent(self, db_session):
        mig = _load_migration()
        roles = _seed_old_roles(db_session)
        assert not FOUR & _held(db_session, roles["owner"].id)

        self._run(db_session, mig.upgrade)
        assert FOUR <= _held(db_session, roles["owner"].id)
        assert FOUR <= _held(db_session, roles["payroll_admin"].id)
        manager = _held(db_session, roles["manager"].id)
        assert FIN <= manager and not PAY & manager
        assert not FOUR & _held(db_session, roles["team_member"].id)
        # A custom role is the owner's business: untouched.
        assert _held(db_session, roles["custom"].id) == {"reports:read", "payroll:read"}

        before = {n: _held(db_session, r.id) for n, r in roles.items()}
        self._run(db_session, mig.upgrade)
        assert {n: _held(db_session, r.id) for n, r in roles.items()} == before

    def test_downgrade_strips_the_scopes_from_every_role(self, db_session):
        mig = _load_migration()
        roles = _seed_old_roles(db_session)
        self._run(db_session, mig.upgrade)
        self._run(db_session, mig.downgrade)
        for role in roles.values():
            assert not FOUR & _held(db_session, role.id)
        assert "reports:read" in _held(db_session, roles["custom"].id)
