"""Shared fixtures for API tests.

Sets up a clean database state for each test function by rolling back
transactions, so tests are isolated without needing to drop/create tables.

Uses a minimal FastAPI app (no startup events) so tests run without
triggering the scheduler, config validation, or connector-spec migrations.
"""

import functools
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import (
    Base,
    User,
    Organization,
    OrganizationMembership,
    Venue,
    UserVenueAccess,
    Thread,
    Supplier,
    Product,
)
from app.db.config_models import ConfigBase
from app.db import mcp_models  # noqa: F401 — register MCP tables on Base.metadata
from app.db import functions_models  # Norm Functions tables (fn_*) on Base.metadata
from app.db.engine import get_db, get_config_db, get_config_db_rw
from app.auth.security import hash_password, create_access_token
from app.routers import (
    auth,
    admin,
    marketplace,
    threads,
    venues,
    messages,
    organizations,
    connectors,
    billing,
    automated_tasks,
    reports_crud,
    working_documents,
    agents,
    roles,
    component_apis,
    connector_specs,
    oauth,
    supplier_specs,
    supplier_tenders,
    supplier_spec_dojo,
    catalog_hygiene,
    invoice_fixes,
    invoice_activity,
    recipe_editor,
    uploads,
    apps as apps_router,
)

# The insight run's rewording is a real model call; a test that wants it
# switches it on and fakes the call (test_insight_wording.py).
settings.INSIGHT_WORDING_ENABLED = False
# The scheduler tick (/internal/run-due-tasks) also starts the insight runs
# on a thread of its own with real sessions, outside a test's rollback (the
# same leak as the unit check, which main switches off just below).
settings.INSIGHTS_ENABLED = False

# The daily unit check rides /internal/run-due-tasks in a background thread on
# the REAL clock and its own committing session. From its hour (19:00 UTC) a
# test calling that route (test_task_scheduler.py) left a real run for today
# in the test database, and test_unit_check.py — whose fixed clock is 19 Oct
# 2026 19:30 UTC — then found today's slot taken (19 Oct 2026, 19:47 UTC).
# Off for the suite; test_unit_check.py switches it on for its own runs.
settings.UNIT_CHECK_ENABLED = False

# The Claude connector is off unless switched on, and test_mcp_protocol.py
# checks exactly that default. A local .env that switches it on for testing
# against Claude made those checks fail on the dev box (passing in CI, which
# has no .env). The tests that need it on switch it on in their own fixtures.
settings.MCP_ENABLED = False


@pytest.fixture(autouse=True)
def _fresh_suggestion_config():
    """The suggestion config is cached for a minute (suggestions/config.py);
    a test's rows roll back, so its cache must not reach the next test."""
    from app.services.suggestions import config as suggestion_config

    suggestion_config.clear_cache()
    yield
    suggestion_config.clear_cache()


# ---------------------------------------------------------------------------
# Minimal app for tests (no startup/shutdown events)
# ---------------------------------------------------------------------------
_test_app = FastAPI()
from app.services.active_org import ActiveOrgMiddleware  # noqa: E402

_test_app.add_middleware(ActiveOrgMiddleware)
_test_app.include_router(auth.router, prefix="/api")
_test_app.include_router(admin.router, prefix="/api")
from app.routers import admin_organizations as _admin_orgs  # noqa: E402

_test_app.include_router(_admin_orgs.router, prefix="/api")
_test_app.include_router(threads.router, prefix="/api")
_test_app.include_router(venues.router, prefix="/api")
_test_app.include_router(messages.router, prefix="/api")
_test_app.include_router(organizations.router, prefix="/api")
_test_app.include_router(connectors.router, prefix="/api")
_test_app.include_router(billing.router, prefix="/api")
_test_app.include_router(automated_tasks.router, prefix="/api")
_test_app.include_router(reports_crud.router, prefix="/api")
_test_app.include_router(working_documents.router, prefix="/api")
_test_app.include_router(agents.router, prefix="/api")
_test_app.include_router(roles.router, prefix="/api")
_test_app.include_router(component_apis.router, prefix="/api")
_test_app.include_router(connector_specs.router, prefix="/api")
_test_app.include_router(oauth.router, prefix="/api")
_test_app.include_router(supplier_specs.router, prefix="/api")
_test_app.include_router(supplier_tenders.router, prefix="/api")
_test_app.include_router(supplier_spec_dojo.router, prefix="/api")
_test_app.include_router(catalog_hygiene.router, prefix="/api")
_test_app.include_router(invoice_fixes.router, prefix="/api")
_test_app.include_router(invoice_activity.router, prefix="/api")
_test_app.include_router(recipe_editor.router, prefix="/api")
_test_app.include_router(uploads.router, prefix="/api")
_test_app.include_router(apps_router.router, prefix="/api")
_test_app.include_router(marketplace.router, prefix="/api")

from app.routers import internal as _internal  # noqa: E402
from app.routers import mcp as _mcp  # noqa: E402
from app.routers import mcp_admin as _mcp_admin  # noqa: E402
from app.routers import mcp_oauth as _mcp_oauth  # noqa: E402
from app.routers import well_known as _well_known  # noqa: E402

_test_app.include_router(_internal.router)
# No prefix — mirrors the real mount in main.py. The MCP endpoint lives at the
# host root so its OAuth discovery documents resolve.
_test_app.include_router(_mcp.router)
_test_app.include_router(_well_known.router)
_test_app.include_router(_mcp_admin.router, prefix="/api")
_test_app.include_router(_mcp_oauth.router, prefix="/api")
from app.routers import approval_preferences as _approval_preferences  # noqa: E402

_test_app.include_router(_approval_preferences.router, prefix="/api")
from app.routers import functions as _functions  # noqa: E402
from app.routers import functions_forms as _functions_forms  # noqa: E402
from app.routers import functions_inbox as _functions_inbox  # noqa: E402
from app.routers import public as _public  # noqa: E402

_test_app.include_router(_functions.router, prefix="/api")
_test_app.include_router(_functions_inbox.router, prefix="/api")
_test_app.include_router(_functions_forms.router, prefix="/api")
_test_app.include_router(_public.router, prefix="/api")

from app.routers import suggestions as _suggestions  # noqa: E402

_test_app.include_router(_suggestions.router, prefix="/api")
from app.routers import suggestion_admin as _suggestion_admin  # noqa: E402

_test_app.include_router(_suggestion_admin.router, prefix="/api")


# ---------------------------------------------------------------------------
# Database engine & session scoped to the test run
# ---------------------------------------------------------------------------
_engine = create_engine(settings.DATABASE_URL)


@pytest.fixture(scope="session", autouse=True)
def _setup_tables():
    """Ensure all tables exist before the test suite runs.

    SESSION scope, deliberately. Left at the default function scope this ran
    `create_all` plus eleven `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` before
    EVERY test — ~33,000 DDL statements a run. `ALTER TABLE` takes an ACCESS
    EXCLUSIVE lock, so two agents running the suite at once on the one local
    Postgres contended on those locks and the loser failed in SETUP, reported
    as an ERROR against whichever test was unlucky. Seen 2 Oct 2026 from two
    sessions at once: a different victim each run, each passing alone and with
    its own file, one of them a test taking no fixtures at all — which is only
    reachable through an autouse fixture. Not connection exhaustion: the local
    app DB sat at 11/100 and the config DB at 25-33/100 throughout.

    The work is idempotent and only needs doing once per run, so nothing is
    lost by hoisting it.
    """
    # Two phases: the Functions tables reference venues (organization_id, id)
    # and need the company-settings columns, which a persistent local DB only
    # gains from the guarded DDL below — so everything else first, then the
    # DDL, then the fn_* tables.
    Base.metadata.create_all(
        bind=_engine,
        tables=[
            t
            for t in Base.metadata.sorted_tables
            if t.name not in functions_models.FUNCTIONS_TABLES
        ],
    )
    ConfigBase.metadata.create_all(bind=_engine)
    _ensure_functions_prerequisites()
    Base.metadata.create_all(bind=_engine)
    # create_all only ADDS TABLES — mirror main._ensure_config_tables' guarded
    # ALTERs so a persistent local test DB gains columns added to existing
    # config tables (fresh CI databases get them from create_all).
    from sqlalchemy import text as _sqltext

    with _engine.begin() as conn:
        for ddl in (
            "ALTER TABLE supplier_spec_samples "
            "ADD COLUMN IF NOT EXISTS source_venue_id VARCHAR",
            "ALTER TABLE supplier_spec_samples "
            "ADD COLUMN IF NOT EXISTS source_invoice_id VARCHAR",
            "ALTER TABLE supplier_spec_samples "
            "ADD COLUMN IF NOT EXISTS source_company_id VARCHAR",
            "ALTER TABLE supplier_spec_samples ADD COLUMN IF NOT EXISTS analysis JSON",
            "ALTER TABLE supplier_spec_samples ADD COLUMN IF NOT EXISTS expected_replica JSON",
            "ALTER TABLE supplier_spec_samples ADD COLUMN IF NOT EXISTS draft BOOLEAN",
            "ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS persona TEXT",
            "ALTER TABLE connector_specs ADD COLUMN IF NOT EXISTS endpoints JSON",
            # component_api_configs gained the field-mapping columns after the
            # table first shipped; a persistent local test DB needs them added.
            "ALTER TABLE component_api_configs "
            "ADD COLUMN IF NOT EXISTS field_mapping JSON",
            "ALTER TABLE component_api_configs "
            "ADD COLUMN IF NOT EXISTS ref_fields JSON",
            "ALTER TABLE component_api_configs "
            "ADD COLUMN IF NOT EXISTS id_field VARCHAR",
            "ALTER TABLE component_api_configs "
            "ADD COLUMN IF NOT EXISTS response_field_mapping JSON",
            # A conversation started from a Home insight (Oct 2026).
            "ALTER TABLE threads ADD COLUMN IF NOT EXISTS insight_id VARCHAR",
            # Top-ups record the usage they add in dollars (Oct 2026 plans).
            "ALTER TABLE token_top_ups ADD COLUMN IF NOT EXISTS usage_usd NUMERIC(10,2)",
            # A dashboard chart remembers what its cached rows are for
            # (migration fb1c2d3e4f5a, Oct 2026); without it every report
            # route 500s on a persistent local DB.
            "ALTER TABLE report_charts ADD COLUMN IF NOT EXISTS data_meta JSON",
            # A conversation remembers its organisation (migration v5x7z9b2d4f6).
            "ALTER TABLE threads ADD COLUMN IF NOT EXISTS organization_id VARCHAR",
            # A task remembers its organisation (migration wd9f1h3j5l7n).
            "ALTER TABLE automated_tasks ADD COLUMN IF NOT EXISTS organization_id VARCHAR",
        ):
            conn.execute(_sqltext(ddl))
    yield


def _ensure_functions_prerequisites() -> None:
    """Company-settings columns on organizations and UNIQUE (organization_id,
    id) on venues, for a persistent local DB whose tables predate them (fresh
    CI databases get both from the migration / create_all).

    Only what is MISSING runs: ALTER TABLE takes ACCESS EXCLUSIVE before it
    checks IF NOT EXISTS, and this DB is shared with other sessions' dev
    servers and test runs (see _setup_tables)."""
    from sqlalchemy import text as _sqltext

    wanted = {
        "currency": "VARCHAR(3) NOT NULL DEFAULT 'NZD'",
        "default_timezone": "VARCHAR NOT NULL DEFAULT 'Pacific/Auckland'",
        "tax_label": "VARCHAR NOT NULL DEFAULT 'incl. GST'",
        "functions_mode": (
            "VARCHAR NOT NULL DEFAULT 'off' CONSTRAINT "
            "ck_organizations_functions_mode CHECK "
            "(functions_mode IN ('off', 'shadow', 'live'))"
        ),
    }
    with _engine.begin() as conn:
        have = {
            r[0]
            for r in conn.execute(
                _sqltext(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'organizations'"
                )
            )
        }
        has_venue_key = conn.execute(
            _sqltext("SELECT 1 FROM pg_constraint WHERE conname = 'uq_venues_org_id'")
        ).first()
        missing = [c for c in wanted if c not in have]
        if missing or not has_venue_key:
            conn.execute(_sqltext("SET LOCAL lock_timeout = '10s'"))
        for column in missing:
            conn.execute(
                _sqltext(
                    f"ALTER TABLE organizations ADD COLUMN IF NOT EXISTS "
                    f"{column} {wanted[column]}"
                )
            )
        if not has_venue_key:
            # Two runs starting together can both see it missing; the loser
            # must not fail its whole session over the winner's constraint.
            conn.execute(
                _sqltext(
                    "DO $$ BEGIN "
                    "ALTER TABLE venues ADD CONSTRAINT uq_venues_org_id "
                    "UNIQUE (organization_id, id); "
                    "EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; "
                    "END $$"
                )
            )


@pytest.fixture()
def db_session():
    """Yield a DB session that rolls back after each test."""
    connection = _engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    """FastAPI TestClient wired to the per-test DB session."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass  # session cleanup handled by db_session fixture

    _test_app.dependency_overrides[get_db] = _override_get_db
    # The MCP endpoint and admin router also depend on the config DB; locally
    # (and in CI) it's the same physical DB, so point it at the same session.
    _test_app.dependency_overrides[get_config_db] = _override_get_db
    _test_app.dependency_overrides[get_config_db_rw] = _override_get_db
    with TestClient(_test_app, raise_server_exceptions=False) as c:
        yield c
    _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------


@functools.cache
def _test_password_hash() -> str:
    """bcrypt is slow on purpose (~0.3 s a hash). Hashing once per run, not
    once per user, halved the suite's time (the Functions world makes five
    users a test); logins with "testpass123" still verify."""
    return hash_password("testpass123")


def _make_user(db_session, *, email=None, role="user", full_name="Test User"):
    """Insert a user directly into the database and return it."""
    user = User(
        id=str(uuid.uuid4()),
        email=email or f"user-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=_test_password_hash(),
        full_name=full_name,
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture()
def admin_user(db_session):
    """Create an admin user."""
    return _make_user(db_session, role="admin", full_name="Admin User")


@pytest.fixture()
def manager_user(db_session):
    """Create a manager (non-admin) user."""
    return _make_user(db_session, role="manager", full_name="Manager User")


@pytest.fixture()
def admin_token(admin_user):
    """Return a valid JWT token for the admin user."""
    return create_access_token({"sub": admin_user.id})


@pytest.fixture()
def manager_token(manager_user):
    """Return a valid JWT token for the manager user."""
    return create_access_token({"sub": manager_user.id})


@pytest.fixture()
def admin_headers(admin_token):
    """Authorization headers for admin."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def manager_headers(manager_token):
    """Authorization headers for manager."""
    return {"Authorization": f"Bearer {manager_token}"}


# ---------------------------------------------------------------------------
# Organization, venue, and data helpers
# ---------------------------------------------------------------------------


def _make_organization(db_session, *, name="Test Org", slug=None, plan="starter"):
    """Insert an organization directly into the database."""
    org = Organization(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug or f"test-org-{uuid.uuid4().hex[:8]}",
        plan=plan,
    )
    db_session.add(org)
    db_session.flush()
    return org


def _make_membership(db_session, user, org, role="member"):
    """Insert an org membership."""
    mem = OrganizationMembership(
        id=str(uuid.uuid4()),
        user_id=user.id,
        organization_id=org.id,
        role=role,
    )
    db_session.add(mem)
    db_session.flush()
    return mem


def _make_venue(
    db_session, *, name="Test Venue", organization_id=None, location="Auckland"
):
    """Insert a venue directly into the database."""
    venue = Venue(
        id=str(uuid.uuid4()),
        name=name,
        location=location,
        organization_id=organization_id,
    )
    db_session.add(venue)
    db_session.flush()
    return venue


def _make_venue_access(db_session, user, venue):
    """Grant a user access to a venue."""
    access = UserVenueAccess(
        id=str(uuid.uuid4()),
        user_id=user.id,
        venue_id=venue.id,
    )
    db_session.add(access)
    db_session.flush()
    return access


def _make_thread(
    db_session,
    user,
    *,
    domain="procurement",
    status="awaiting_user_input",
    intent="place_order",
    raw_prompt="Order something",
    venue_id=None,
):
    """Insert a thread directly into the database."""
    thread = Thread(
        id=str(uuid.uuid4()),
        user_id=user.id,
        domain=domain,
        status=status,
        intent=intent,
        raw_prompt=raw_prompt,
        venue_id=venue_id,
        extracted_fields={},
        missing_fields=[],
    )
    db_session.add(thread)
    db_session.flush()
    return thread


def _make_supplier(db_session, name="Test Supplier"):
    """Insert a supplier."""
    s = Supplier(id=str(uuid.uuid4()), name=name)
    db_session.add(s)
    db_session.flush()
    return s


def _make_product(db_session, supplier, name="Test Product"):
    """Insert a product."""
    p = Product(
        id=str(uuid.uuid4()),
        supplier_id=supplier.id,
        name=name,
        unit="case",
    )
    db_session.add(p)
    db_session.flush()
    return p


@pytest.fixture()
def organization(db_session):
    """Create a test organization."""
    return _make_organization(db_session)


@pytest.fixture()
def venue(db_session, organization):
    """Create a test venue linked to the org."""
    return _make_venue(db_session, organization_id=organization.id)


@pytest.fixture()
def admin_org_membership(db_session, admin_user, organization):
    """Make admin an owner of the test organization."""
    return _make_membership(db_session, admin_user, organization, role="owner")


@pytest.fixture()
def manager_org_membership(db_session, manager_user, organization):
    """Make manager a member of the test organization."""
    return _make_membership(db_session, manager_user, organization, role="member")


@pytest.fixture()
def admin_venue_access(db_session, admin_user, venue):
    """Grant admin access to the test venue."""
    return _make_venue_access(db_session, admin_user, venue)
