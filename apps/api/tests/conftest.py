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
from app.db.models import Base, User
from app.db.engine import get_db
from app.auth.security import hash_password, create_access_token
from app.routers import auth, admin


# ---------------------------------------------------------------------------
# Minimal app for tests (no startup/shutdown events)
# ---------------------------------------------------------------------------
_test_app = FastAPI()
_test_app.include_router(auth.router, prefix="/api")
_test_app.include_router(admin.router, prefix="/api")


# ---------------------------------------------------------------------------
# Database engine & session scoped to the test run
# ---------------------------------------------------------------------------
_engine = create_engine(settings.DATABASE_URL)


@pytest.fixture(autouse=True)
def _setup_tables():
    """Ensure all tables exist before the test suite runs."""
    Base.metadata.create_all(bind=_engine)
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

def _make_user(db_session, *, email=None, role="manager", full_name="Test User"):
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
