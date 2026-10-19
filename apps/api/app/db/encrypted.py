"""Encryption at rest for connector OAuth tokens (Oct 2026) and for the
secrets typed into a connection's credentials (``EncryptedSecretsJSON``,
19 Oct 2026 — owner: "encrypt all typed secrets").

Every connector's tokens — LoadedHub's rotating refresh tokens, the venue
mailboxes' Gmail grants, Gmail/Outlook send-on-behalf, the MCP connectors —
live on one table, ``connector_configs``. So they are encrypted in one place:
``EncryptedText``, the column type of ``Connection.access_token`` and
``Connection.refresh_token``. Code keeps reading and writing plain strings;
the type encrypts on the way into the database and decrypts on the way out,
for the ORM and Core statements alike.

Stored values look like ``enc:v1:<Fernet token>``. Keys come from
``settings.TOKEN_ENCRYPTION_KEYS`` — comma-separated Fernet keys, newest
first (MultiFernet: the first encrypts, any of them decrypts). A key is
rotated by putting a new one in front, running
``scripts/encrypt_connection_tokens.py --apply`` (which re-encrypts every
value under the newest key), and only then dropping the old one.

Transition-safe by construction, because getting this wrong kills
production's LoadedHub connections (their refresh tokens rotate and live
~24h — a token we cannot read is a connection lost within a day):

* A value without the prefix is plaintext and is returned unchanged, so rows
  written before keys existed keep working. Encrypting them is a separate,
  deliberate step (``scripts/encrypt_connection_tokens.py``), never a side
  effect of a deploy.
* With no key configured, new values are written in plaintext and a warning
  is logged once — deploying this before the secret exists changes nothing.
* A value that is already ciphertext is never encrypted twice (so copying a
  raw value between rows, or re-running the backfill, is harmless).
* A prefixed value that no configured key opens reads as ``None``: the
  connection looks disconnected, an error is logged, and the ciphertext in
  the row is untouched — restoring the key restores the connection. Raising
  instead would take down every page that lists connections.

Not supported, by design: comparing these columns in SQL
(``Connection.access_token == x``). Fernet is randomised, so an encrypted
literal never equals the stored ciphertext. ``IS NULL`` checks are fine.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import JSON, Text
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"

_lock = threading.Lock()
#: (the raw setting it was built from, the MultiFernet or None) — rebuilt
#: whenever the setting changes, so tests and key rotation need no restart.
_cache: tuple[str, object | None] | None = None
_warned_no_key = False
_warned_bad_keys: set[str] = set()


def _raw_keys() -> str:
    from app.config import settings

    return str(getattr(settings, "TOKEN_ENCRYPTION_KEYS", "") or "")


def _fernet():
    """The configured MultiFernet, or None (no keys, or keys that don't
    parse — the latter logged as an error, never with the key material)."""
    global _cache, _warned_no_key
    raw = _raw_keys()
    cached = _cache
    if cached is not None and cached[0] == raw:
        return cached[1]
    with _lock:
        if _cache is not None and _cache[0] == raw:
            return _cache[1]
        from cryptography.fernet import Fernet, MultiFernet

        keys = [k.strip() for k in raw.split(",") if k.strip()]
        built = None
        if not keys:
            if not _warned_no_key:
                _warned_no_key = True
                logger.warning(
                    "TOKEN_ENCRYPTION_KEYS is not set: connector tokens are "
                    "stored in plaintext until a key is configured"
                )
        else:
            try:
                built = MultiFernet([Fernet(k.encode()) for k in keys])
            except (ValueError, TypeError):
                fingerprint = f"{len(keys)} key(s)"
                if fingerprint not in _warned_bad_keys:
                    _warned_bad_keys.add(fingerprint)
                    logger.error(
                        "TOKEN_ENCRYPTION_KEYS holds a value that is not a "
                        "Fernet key (%s configured) — tokens are written in "
                        "plaintext and encrypted ones cannot be read until "
                        "it is fixed",
                        fingerprint,
                    )
        _cache = (raw, built)
        return built


def encryption_configured() -> bool:
    """Whether new token values are being encrypted."""
    return _fernet() is not None


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_token(value: str | None) -> str | None:
    """``enc:v1:…`` under the newest key; unchanged when it is None, empty,
    already encrypted, or no key is configured."""
    if value is None or value == "" or is_encrypted(value):
        return value
    fernet = _fernet()
    if fernet is None:
        return value
    return PREFIX + fernet.encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_token(value: str | None) -> str | None:
    """The plaintext of a stored value. Plaintext passes through; ciphertext
    no configured key opens reads as None (logged)."""
    if not is_encrypted(value):
        return value
    from cryptography.fernet import InvalidToken

    fernet = _fernet()
    if fernet is not None:
        try:
            return fernet.decrypt(value[len(PREFIX) :].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            pass
    logger.error(
        "A connector token could not be decrypted with the configured "
        "TOKEN_ENCRYPTION_KEYS — the connection will read as disconnected "
        "until the key that wrote it is restored"
    )
    return None


def reencrypt_token(value: str | None) -> str | None:
    """A stored value re-encrypted under the newest key (plaintext gets
    encrypted). For the backfill/rotation script. Raises ``ValueError`` when
    there is no key, or the value cannot be decrypted — the script must
    never overwrite what it cannot read."""
    if value is None or value == "":
        return value
    fernet = _fernet()
    if fernet is None:
        raise ValueError("no TOKEN_ENCRYPTION_KEYS configured")
    if not is_encrypted(value):
        return encrypt_token(value)
    from cryptography.fernet import InvalidToken

    try:
        rotated = fernet.rotate(value[len(PREFIX) :].encode("ascii"))
    except InvalidToken as exc:
        raise ValueError("value cannot be decrypted with the configured keys") from exc
    return PREFIX + rotated.decode("ascii")


def encrypted_under_newest_key(value: str | None) -> bool:
    """Whether ciphertext was written by the newest key (False for
    plaintext, or for ciphertext an older key wrote)."""
    if not is_encrypted(value):
        return False
    from cryptography.fernet import Fernet, InvalidToken

    keys = [k.strip() for k in _raw_keys().split(",") if k.strip()]
    if not keys:
        return False
    try:
        Fernet(keys[0].encode()).decrypt(value[len(PREFIX) :].encode("ascii"))
        return True
    except (InvalidToken, ValueError, TypeError):
        return False


class EncryptedText(TypeDecorator):
    """``TEXT`` in the database, plaintext in Python, ``enc:v1:`` ciphertext
    in between (see the module docstring for the transition rules)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_token(value)

    def process_result_value(self, value, dialect):
        return decrypt_token(value)


# ---------------------------------------------------------------------------
# Typed secrets in a connection's credentials (Connection.config)
# ---------------------------------------------------------------------------
#
# A connector's credential form (Settings → Connections) saves what is typed
# into ``Connection.config`` — a JSON object of API keys, usernames, ids and
# passwords. The secrets in it are encrypted like the tokens above, by name:
# every path that rewrites a connection's config (the form, the OAuth
# callbacks merging a company id, scripts) keeps them encrypted without
# knowing which fields are secret. A credential field marked ``secret`` must
# therefore have a secret's name — config_validator checks every spec.

#: Credential keys whose values are secrets.
SECRET_CONFIG_KEYS = frozenset(
    {
        "password",
        "api_key",
        "api_secret",
        "access_token",
        "refresh_token",
        "user_token",
        "client_secret",
        "secret",
        "token",
        "private_key",
    }
)
SECRET_CONFIG_SUFFIXES = ("_password", "_secret", "_token", "_api_key", "_private_key")


def is_secret_key(key: object) -> bool:
    """Is this credential key's value a secret (and so stored encrypted)?"""
    k = str(key or "").strip().lower()
    return k in SECRET_CONFIG_KEYS or k.endswith(SECRET_CONFIG_SUFFIXES)


def encrypt_secrets(config):
    """A copy of ``config`` with every secret-named string value encrypted.
    Other values, non-dicts and already-encrypted values pass through."""
    if not isinstance(config, dict):
        return config
    return {
        k: encrypt_token(v) if isinstance(v, str) and is_secret_key(k) else v
        for k, v in config.items()
    }


def decrypt_secrets(config):
    """A copy of ``config`` with every encrypted value decrypted, whatever its
    key (a value no key opens reads as None, like a token)."""
    if not isinstance(config, dict):
        return config
    return {k: decrypt_token(v) if is_encrypted(v) else v for k, v in config.items()}


class EncryptedSecretsJSON(TypeDecorator):
    """``JSON`` in the database with its secret-named values encrypted;
    plaintext in Python. Rows written before keys existed read unchanged."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_secrets(value)

    def process_result_value(self, value, dialect):
        return decrypt_secrets(value)


# ---------------------------------------------------------------------------
# The key canary
# ---------------------------------------------------------------------------
#
# ``scripts/encrypt_connection_tokens.py --apply`` re-encrypts every token
# with the key IT is given. Run with a key the service doesn't have, every
# token in the environment would read as None — every connector down at once.
# So the running service leaves a marker encrypted under ITS newest key, and
# the script refuses unless its own newest key wrote the same marker.

#: A ``connector_configs`` row no connector reads: no venue, disabled, no
#: refresh token (so the keep-alive skips it).
CANARY_CONNECTOR = "_token_key_canary"
CANARY_TEXT = "norm-token-key-canary:v1"


def _canary_rows(db) -> list:
    from sqlalchemy import text

    return db.execute(
        text(
            "SELECT id, access_token FROM connector_configs "
            "WHERE connector_name = :c AND venue_id IS NULL"
        ),
        {"c": CANARY_CONNECTOR},
    ).all()


def ensure_key_canary(db) -> str:
    """Write the marker under this process's newest key (the caller's
    session; commits). ``no_key`` | ``current`` | ``written``."""
    from sqlalchemy import text

    from app.db.models import Connection

    if not encryption_configured():
        return "no_key"
    rows = _canary_rows(db)
    if rows and all(
        encrypted_under_newest_key(r.access_token)
        and decrypt_token(r.access_token) == CANARY_TEXT
        for r in rows
    ):
        return "current"
    if rows:
        db.execute(
            text(
                "UPDATE connector_configs SET access_token = :v WHERE connector_name = :c"
            ),
            {"v": encrypt_token(CANARY_TEXT), "c": CANARY_CONNECTOR},
        )
    else:
        db.add(
            Connection(
                connector_name=CANARY_CONNECTOR,
                config={"purpose": "token encryption key check (app/db/encrypted.py)"},
                enabled="false",
                access_token=CANARY_TEXT,
            )
        )
    db.commit()
    return "written"


def write_key_canary() -> str:
    """``ensure_key_canary`` in a session of its own, never raising — for a
    startup hook or a scheduled job."""
    from app.db.engine import SessionLocal

    db = SessionLocal()
    try:
        return ensure_key_canary(db)
    except Exception:  # noqa: BLE001 — the marker is a safety check, not the service
        logger.exception("token key canary not written")
        db.rollback()
        return "failed"
    finally:
        db.close()


def canary_status(db) -> str:
    """What this process's keys make of the service's marker: ``missing`` |
    ``newest`` (its newest key wrote it — safe to --apply) | ``older_key``
    (one of its keys reads it, not the newest — safe to --decrypt only) |
    ``unreadable`` (none of its keys: the wrong keys entirely)."""
    rows = _canary_rows(db)
    if not rows:
        return "missing"
    if all(decrypt_token(r.access_token) == CANARY_TEXT for r in rows):
        if all(encrypted_under_newest_key(r.access_token) for r in rows):
            return "newest"
        return "older_key"
    return "unreadable"
