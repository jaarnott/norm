from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# ── Primary (read-write) engine ──────────────────────────────────
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine)

# ── Read replica engine (optional) ───────────────────────────────
_read_engine = None
_ReadSessionLocal = None

if settings.DATABASE_READ_URL:
    _read_engine = create_engine(
        settings.DATABASE_READ_URL,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    _ReadSessionLocal = sessionmaker(bind=_read_engine)


def get_db():
    """FastAPI dependency that yields a read-write DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db():
    """FastAPI dependency that yields a read-only DB session.

    Falls back to the primary engine if no read replica is configured.
    Use this for GET endpoints that don't need to write.
    """
    factory = _ReadSessionLocal or SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()


# ── Config database engine (shared across environments) ────────
_config_engine = None
_ConfigSessionLocal = None

if settings.CONFIG_DATABASE_URL:
    _config_engine = create_engine(
        settings.CONFIG_DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    _ConfigSessionLocal = sessionmaker(bind=_config_engine)


def get_config_db():
    """FastAPI dependency that yields a config DB session (read-only by convention).

    Falls back to the primary engine if CONFIG_DATABASE_URL is not set.
    Use this for reading ConnectorSpec, AgentConfig, AgentConnectorBinding, SystemSecret.
    """
    factory = _ConfigSessionLocal or SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_config_db_rw():
    """FastAPI dependency that yields a config DB session for writes.

    Same as get_config_db() but signals intent to write. Falls back to primary.
    Use this for admin endpoints that modify config tables.
    """
    factory = _ConfigSessionLocal or SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()
