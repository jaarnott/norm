"""Encrypt (or re-encrypt, or decrypt) the OAuth tokens already stored in
``connector_configs`` — the one-off backfill for app/db/encrypted.py — and the
secrets typed into its ``config`` (API keys, passwords: every value whose key
``encrypted.is_secret_key`` names a secret; 19 Oct 2026).

The column type encrypts every token written after ``TOKEN_ENCRYPTION_KEYS``
is set, and reads plaintext rows unchanged, so nothing breaks while rows sit
in plaintext. This script is the deliberate second step, run once per
environment after the key is provisioned:

  --dry-run   count what is plaintext / under the newest key / under an older
              key / unreadable, per connection (never prints a token);
  --apply     encrypt plaintext and re-encrypt older-key ciphertext under the
              newest key (also the key-rotation step: new key first in the
              setting, --apply, then drop the old key);
  --decrypt   write plaintext back — the rollback aid. Run it BEFORE deploying
              code older than the column type, which would otherwise send
              ``enc:v1:…`` to providers as the token.

Before writing anything it checks the KEY: the running service leaves a
marker encrypted under its newest key (``app/db/encrypted.py`` key canary,
written by ``/internal/refresh-tokens``, which every environment's scheduler
calls every 15 minutes). ``--apply`` refuses unless this script's newest key
wrote that marker — re-encrypting with a key the service doesn't hold would
make every token in the environment read as None. ``--decrypt`` needs one of
its keys to read it. So after adding or rotating a key: deploy, wait for the
next refresh-tokens tick (or call it), then run the script with the same
setting. Where nothing calls refresh-tokens (a laptop), write the marker
with the service's own settings:
    uv run python -c "from app.db.encrypted import write_key_canary as w; print(w())"

ROLLING BACK after ``--apply``: an image older than the column type reads
``enc:v1:…`` as the token itself. Run ``--decrypt`` (with the service's keys)
BEFORE deploying it, and ``--apply`` again after rolling forward.

Every write is a compare-and-set on the raw values it read: LoadedHub's
keep-alive rotates refresh tokens every 15 minutes, and writing back a token
that was rotated in between would revive a dead one and lose the venue's
connection within a day. A row that changed under us is re-read and retried;
a value no configured key can read is reported and never overwritten.

Runs against the environment's APP database (DATABASE_URL), never the shared
config DB. Usage (from apps/api, .env loaded):
    uv run python scripts/encrypt_connection_tokens.py --dry-run
    uv run python scripts/encrypt_connection_tokens.py --apply
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

COLUMNS = ("access_token", "refresh_token")
RETRIES = 3


def config_target(config: dict, mode: str) -> dict:
    """``config`` with its secret values as they should be stored: encrypted
    under the newest key (apply), or plain (decrypt). Non-secret values are
    left alone. Raises ValueError when a value cannot be read."""
    from app.db.encrypted import is_encrypted, is_secret_key

    return {
        k: target(v, mode)
        if isinstance(v, str) and (is_secret_key(k) or is_encrypted(v))
        else v
        for k, v in (config or {}).items()
    }


def config_states(config: dict) -> dict:
    """Each secret value's state (see ``classify``)."""
    from app.db.encrypted import is_encrypted, is_secret_key

    return {
        k: classify(v)
        for k, v in (config or {}).items()
        if isinstance(v, str) and v and (is_secret_key(k) or is_encrypted(v))
    }


def run_config(engine, mode: str, out=print, only_ids: list[str] | None = None) -> dict:
    """The typed secrets in ``config``, row by row, compare-and-set on the raw
    JSON text it read. Called by ``run`` after the key canary check."""
    import json

    from sqlalchemy import text

    counts: dict[str, int] = {}
    changed = skipped = 0
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, connector_name, venue_id, user_id, config::text AS raw "
                "FROM connector_configs "
                "WHERE (CAST(:ids AS VARCHAR[]) IS NULL OR id = ANY(:ids)) "
                "ORDER BY connector_name, id"
            ),
            {"ids": list(only_ids) if only_ids else None},
        ).fetchall()
    for row in rows:
        config = json.loads(row.raw) if row.raw else {}
        if not isinstance(config, dict):
            continue
        states = config_states(config)
        if not states:
            continue
        for state in states.values():
            counts[state] = counts.get(state, 0) + 1
        out(
            f"{_label(row)}: config " + ", ".join(f"{k}={v}" for k, v in states.items())
        )
        if mode == "dry-run":
            continue
        try:
            new = config_target(config, mode)
        except ValueError:
            out(f"  SKIPPED {_label(row)}: a config value cannot be decrypted")
            skipped += 1
            continue
        if new == config:
            continue
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE connector_configs SET config = CAST(:new AS json) "
                    "WHERE id = :id AND config::text = :old"
                ),
                {"id": row.id, "new": json.dumps(new), "old": row.raw},
            )
        if result.rowcount == 1:
            changed += 1
        else:
            out(f"  CHANGED UNDER US {_label(row)}: run again")
            skipped += 1
    return {
        **{f"config_values_{k}": v for k, v in sorted(counts.items())},
        "config_rows_changed": changed,
        "config_rows_skipped": skipped,
    }


def classify(value: str | None) -> str:
    """empty | plaintext | current | older_key | unreadable."""
    from app.db.encrypted import (
        decrypt_token,
        encrypted_under_newest_key,
        is_encrypted,
    )

    if value is None or value == "":
        return "empty"
    if not is_encrypted(value):
        return "plaintext"
    if encrypted_under_newest_key(value):
        return "current"
    return "older_key" if decrypt_token(value) is not None else "unreadable"


def target(value: str | None, mode: str) -> str | None:
    """What a raw stored value should become. Raises ValueError for a value
    that cannot be read (it is left alone)."""
    from app.db.encrypted import decrypt_token, is_encrypted, reencrypt_token

    if value is None or value == "":
        return value
    if mode == "decrypt":
        if not is_encrypted(value):
            return value
        plain = decrypt_token(value)
        if plain is None:
            raise ValueError("unreadable")
        return plain
    return reencrypt_token(value)


def _label(row) -> str:
    scope = (
        f"venue {row.venue_id}"
        if row.venue_id
        else f"user {row.user_id}"
        if row.user_id
        else "global"
    )
    return f"{row.connector_name} ({scope}) [{row.id}]"


def run(engine, mode: str, out=print, only_ids: list[str] | None = None) -> dict:
    """``mode``: dry-run | apply | decrypt. ``only_ids`` limits it to those
    connections (``--connection-id``). Returns the counts."""
    from sqlalchemy import text

    from app.db.encrypted import CANARY_CONNECTOR, canary_status

    counts: dict[str, int] = {}
    changed = skipped = conflicts = 0
    with engine.connect() as conn:
        canary = canary_status(conn)
        ids = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT id FROM connector_configs "
                    "WHERE (access_token IS NOT NULL OR refresh_token IS NOT NULL) "
                    "AND connector_name <> :canary "
                    "AND (CAST(:ids AS VARCHAR[]) IS NULL OR id = ANY(:ids)) "
                    "ORDER BY connector_name, id"
                ),
                {
                    "ids": list(only_ids) if only_ids else None,
                    "canary": CANARY_CONNECTOR,
                },
            )
        ]
    out(f"key canary: {canary}")
    refusal = None
    if mode == "apply" and canary != "newest":
        refusal = (
            "the service's key canary was not written by this script's newest "
            "key (status: %s) — run with the SAME TOKEN_ENCRYPTION_KEYS as the "
            "service (newest first), after the service has written its canary" % canary
        )
    elif mode == "decrypt" and canary not in ("newest", "older_key"):
        refusal = (
            "none of this script's keys can read the service's key canary "
            "(status: %s) — run with the service's TOKEN_ENCRYPTION_KEYS" % canary
        )
    if refusal:
        out("REFUSED: " + refusal)
        return {"refused": refusal, "key_canary": canary}
    for row_id in ids:
        for attempt in range(RETRIES):
            with engine.begin() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, connector_name, venue_id, user_id, "
                        "access_token, refresh_token FROM connector_configs "
                        "WHERE id = :id"
                    ),
                    {"id": row_id},
                ).first()
                if row is None:
                    break
                states = {c: classify(getattr(row, c)) for c in COLUMNS}
                if attempt == 0:
                    for state in states.values():
                        counts[state] = counts.get(state, 0) + 1
                    out(
                        f"{_label(row)}: "
                        + ", ".join(f"{c}={s}" for c, s in states.items())
                    )
                if mode == "dry-run":
                    break
                try:
                    new = {c: target(getattr(row, c), mode) for c in COLUMNS}
                except ValueError:
                    out(f"  SKIPPED {_label(row)}: a value cannot be decrypted")
                    skipped += 1
                    break
                if all(new[c] == getattr(row, c) for c in COLUMNS):
                    break
                result = conn.execute(
                    text(
                        "UPDATE connector_configs "
                        "SET access_token = :new_a, refresh_token = :new_r "
                        "WHERE id = :id "
                        "AND access_token IS NOT DISTINCT FROM :old_a "
                        "AND refresh_token IS NOT DISTINCT FROM :old_r"
                    ),
                    {
                        "id": row.id,
                        "new_a": new["access_token"],
                        "new_r": new["refresh_token"],
                        "old_a": row.access_token,
                        "old_r": row.refresh_token,
                    },
                )
                if result.rowcount == 1:
                    changed += 1
                    break
            # The row changed between read and write (a refresh landed):
            # read it again rather than write back what we saw.
            conflicts += 1
        else:
            out(f"  GAVE UP on {row_id}: it kept changing — run again")
            skipped += 1
    summary = {
        "key_canary": canary,
        **{f"values_{k}": v for k, v in sorted(counts.items())},
        "rows_changed": changed,
        "rows_skipped": skipped,
        "retries": conflicts,
        **run_config(engine, mode, out=out, only_ids=only_ids),
    }
    out("summary: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--decrypt", action="store_true")
    parser.add_argument(
        "--connection-id",
        action="append",
        help="only this connector_configs row (repeatable)",
    )
    args = parser.parse_args()

    from sqlalchemy import create_engine

    from app.config import settings
    from app.db.encrypted import encryption_configured

    mode = "apply" if args.apply else "decrypt" if args.decrypt else "dry-run"
    if mode != "dry-run" and not encryption_configured():
        print("TOKEN_ENCRYPTION_KEYS is not set (or not a valid Fernet key) — refusing")
        return 2
    if not encryption_configured():
        print("note: no key configured — every value will read as plaintext/unreadable")
    engine = create_engine(settings.DATABASE_URL)
    try:
        summary = run(engine, mode, only_ids=args.connection_id)
    finally:
        engine.dispose()
    if mode == "dry-run":
        print("(dry run — nothing written)")
    if summary.get("refused"):
        return 3
    return 1 if summary.get("rows_skipped") or summary.get("config_rows_skipped") else 0


if __name__ == "__main__":
    sys.exit(main())
