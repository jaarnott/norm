"""Shared fixtures for API tests.

Sets up a clean database state for each test function by rolling back
transactions, so tests are isolated without needing to drop/create tables.

Uses a minimal FastAPI app (no startup events) so tests run without
triggering the scheduler, config validation, or connector-spec migrations.
"""

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
from app.db.engine import get_db
from app.auth.security import hash_password, create_access_token
from app.routers import (
    auth,
    admin,
    threads,
    venues,
    organizations,
    connectors,
    billing,
    automated_tasks,
    reports_crud,
    working_documents,
    orders,
    agents,
    roles,
)


# ---------------------------------------------------------------------------
# Minimal app for tests (no startup/shutdown events)
# ---------------------------------------------------------------------------
_test_app = FastAPI()
_test_app.include_router(auth.router, prefix="/api")
_test_app.include_router(admin.router, prefix="/api")
_test_app.include_router(threads.router, prefix="/api")
_test_app.include_router(venues.router, prefix="/api")
_test_app.include_router(organizations.router, prefix="/api")
_test_app.include_router(connectors.router, prefix="/api")
_test_app.include_router(billing.router, prefix="/api")
_test_app.include_router(automated_tasks.router, prefix="/api")
_test_app.include_router(reports_crud.router, prefix="/api")
_test_app.include_router(working_documents.router, prefix="/api")
_test_app.include_router(orders.router, prefix="/api")
_test_app.include_router(agents.router, prefix="/api")
_test_app.include_router(roles.router, prefix="/api")


# ---------------------------------------------------------------------------
# Database engine & session scoped to the test run
# ---------------------------------------------------------------------------
_engine = create_engine(settings.DATABASE_URL)


@pytest.fixture(autouse=True)
def _setup_tables():
    """Ensure all tables exist before the test suite runs."""
    Base.metadata.create_all(bind=_engine)
    ConfigBase.metadata.create_all(bind=_engine)
    yield


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
    with TestClient(_test_app, raise_server_exceptions=False) as c:
        yield c
    _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------


def _make_user(db_session, *, email=None, role="user", full_name="Test User"):
    """Insert a user directly into the database and return it."""
    user = User(
        id=str(uuid.uuid4()),
        email=email or f"user-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=hash_password("testpass123"),
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
