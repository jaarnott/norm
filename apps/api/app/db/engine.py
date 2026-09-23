import logging
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

log = logging.getLogger(__name__)

# ── Connector-call DB budget (per process) ───────────────────────
# A fan-out query opens one DB session per parallel call. Left unbounded, a
# single large multi-venue query checked out the whole pool and held it across
# the slow Loaded HTTP calls, pinning the database (a 6-venue COGS query filled
# norm-prod-db's ~50 slots and starved every other request until a restart).
#
# This bounds how many connector calls may hold a DB connection AT ONCE in this
# PROCESS, below its pool ceiling (22) so request-scoped work — chat, /units —
# always has headroom. It guards only the brief render phase (token resolution)
# inside spec_executor.execute_spec, which does no nested/fan-out work, so it
# cannot deadlock; the slow HTTP round-trip runs outside it, and the fan-out
# worker's fresh session is returned to the pool before that call.
DB_CALL_LIMIT = 12
db_call_semaphore = threading.BoundedSemaphore(DB_CALL_LIMIT)

# ── Primary (read-write) engine ──────────────────────────────────
# A POOL IS PER PROCESS — AND EACH INSTANCE RUNS FOUR.
#
# gunicorn starts 4 workers per Cloud Run instance (Dockerfile `-w 4`), and each
# worker is a process with its own pool. So the sum that has to fit the database
# is  pool x 4 workers x maxScale instances,  not pool x instances. This file
# used to count instances only, and was out by four.
#
# THE FLOOR IS ONE PROCESS'S PEAK. A consolidator's parallel fan-out opens a
# session PER CALL, up to 20 at once (function_executor `_worker`,
# ThreadPoolExecutor(min(len(calls), 20))), and the tool loop runs up to 8
# read-only calls concurrently, each with its own session — all inside the one
# worker handling that turn. A pool below that fails the work outright rather
# than queueing it: 5+7 died on 27 Aug 2026 with "QueuePool limit of size 5
# overflow 7 reached, connection timed out". So the ceiling stays 22.
#
# THE IDLE FOOTPRINT IS pool_size, AND IT NEVER SHRINKS. QueuePool keeps up to
# pool_size returned connections open forever and closes only the overflow. At
# pool_size=10, every burst left its worker holding ten idle connections: on
# 23 Sep 2026 two big fan-out turns ratcheted norm-prod-db from 10 to 48 IDLE
# connections (8 processes x up to 10 each) with request volume flat, until
# nothing could connect. At 2 the idle footprint is 2 x 8 = 16, and bursts use
# overflow connections that close as soon as they are returned.
#
# Keep maxScale at 2 on a db-g1-small (~50 slots), or raise the tier first.
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=2,
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
    # Same budget as the primary — a replica is a separate instance, but an
    # environment that points this at the PRIMARY doubles that instance's
    # footprint, which is exactly the sum that overran above.
    _read_engine = create_engine(
        settings.DATABASE_READ_URL,
        pool_size=2,
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

# The config DB is a small instance shared across ALL environments (local,
# testing, staging, production), so keep the idle footprint low: pool_size is the
# number of connections each app instance holds open indefinitely, and four
# environments each holding five was most of the DB's 25-connection budget before
# any real work ran. max_overflow still allows short bursts. Spec lookups are now
# cached per run (see function_executor), so steady-state config-DB use is light.
_config_engine = create_engine(
    settings.CONFIG_DATABASE_URL,
    pool_size=2,
    max_overflow=10,
    pool_timeout=10,
    pool_recycle=900,
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
