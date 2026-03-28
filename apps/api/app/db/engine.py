import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

log = logging.getLogger(__name__)

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
    """
    factory = _ReadSessionLocal or SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()


# ── Config database engine (REQUIRED — shared across environments) ──
# The config DB holds connector specs, agent configs, bindings, and
# system secrets. All environments MUST connect to the same config DB.
# There is NO fallback — if the config DB is unreachable, the app
# should fail clearly rather than silently using stale local data.

if not settings.CONFIG_DATABASE_URL:
    raise RuntimeError(
        "CONFIG_DATABASE_URL is required. Set it in .env (local) or "
        "Secret Manager (cloud). All environments must connect to the "
        "shared config database."
    )

_config_engine = create_engine(
    settings.CONFIG_DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_timeout=10,
    pool_recycle=1800,
    pool_pre_ping=True,
)

# Test connectivity at import time — fail fast if unreachable
try:
    with _config_engine.connect() as _conn:
        _conn.execute(text("SELECT 1"))
    log.info("Config DB connected")
except Exception as exc:
    raise RuntimeError(
        f"Config DB unreachable at {settings.CONFIG_DATABASE_URL[:40]}... — "
        f"check CONFIG_DATABASE_URL in .env: {exc}"
    ) from exc

_ConfigSessionLocal = sessionmaker(bind=_config_engine)


def get_config_db():
    """FastAPI dependency that yields a config DB session (read-only by convention).

    Uses the shared config database. No fallback — fails if not connected.
    """
    db = _ConfigSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_config_db_rw():
    """FastAPI dependency that yields a config DB session for writes.

    Uses the shared config database. No fallback — fails if not connected.
    """
    db = _ConfigSessionLocal()
    try:
        yield db
    finally:
        db.close()
