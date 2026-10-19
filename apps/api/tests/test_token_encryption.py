"""Connector OAuth tokens encrypted at rest (app/db/encrypted.py).

These tokens are production's LoadedHub connections: their refresh tokens
rotate on every redemption and die within ~24h, so a token Norm cannot read,
or a rotation written back stale, is a venue disconnected by tomorrow.
Pinned here:

* round trip: the ORM writes ``enc:v1:`` ciphertext and reads plaintext;
  Core statements on the table do the same;
* transition: plaintext rows read unchanged (with and without a key); with no
  key, new values stay plaintext and one warning is logged;
* rotation: a new key in front still reads the old ciphertext, and the
  backfill re-encrypts under it; dropping a key that still wrote a value
  makes that value read as None (logged) without overwriting it;
* nothing is ever encrypted twice;
* the real refresh path (oauth_service) sends the PLAINTEXT refresh token to
  the provider and stores the rotated one encrypted — exactly once, under the
  row lock;
* the backfill script counts, encrypts, decrypts, never prints a token, and
  re-reads a row that changed between its read and its write.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from app.config import settings
from app.db import encrypted as enc
from app.db.models import Connection
from tests.conftest import _make_venue

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


@pytest.fixture()
def keys(monkeypatch):
    """Set TOKEN_ENCRYPTION_KEYS for one test (the cache follows the
    setting, so no restart or reload is needed)."""

    def use(value: str):
        monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEYS", value, raising=False)

    use(KEY_A)
    return use


@pytest.fixture()
def no_keys(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEYS", "", raising=False)


def _raw(db, conn_id: str) -> tuple[str | None, str | None]:
    row = db.execute(
        text("SELECT access_token, refresh_token FROM connector_configs WHERE id = :i"),
        {"i": conn_id},
    ).first()
    return row[0], row[1]


def _connection(db, venue, **kw) -> Connection:
    row = Connection(
        connector_name=kw.pop("connector_name", "loadedhub"),
        venue_id=venue.id,
        config={},
        enabled="true",
        **kw,
    )
    db.add(row)
    db.flush()
    return row


def _reload(db, conn_id: str) -> Connection:
    db.expire_all()
    return db.query(Connection).filter(Connection.id == conn_id).one()


class TestRoundTrip:
    def test_orm_writes_ciphertext_and_reads_plaintext(self, db_session, keys):
        venue = _make_venue(db_session)
        row = _connection(
            db_session, venue, access_token="acc-1", refresh_token="ref-1"
        )

        raw_access, raw_refresh = _raw(db_session, row.id)
        assert raw_access.startswith("enc:v1:") and "acc-1" not in raw_access
        assert raw_refresh.startswith("enc:v1:") and "ref-1" not in raw_refresh

        again = _reload(db_session, row.id)
        assert again.access_token == "acc-1"
        assert again.refresh_token == "ref-1"

    def test_core_statements_on_the_table_encrypt_too(self, db_session, keys):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue)
        table = Connection.__table__
        db_session.execute(
            table.update().where(table.c.id == row.id).values(refresh_token="ref-core")
        )
        assert _raw(db_session, row.id)[1].startswith("enc:v1:")
        got = db_session.execute(
            select(table.c.refresh_token).where(table.c.id == row.id)
        ).scalar_one()
        assert got == "ref-core"

    def test_null_and_empty_stay_as_they_are(self, db_session, keys):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, access_token="", refresh_token=None)
        assert _raw(db_session, row.id) == ("", None)
        again = _reload(db_session, row.id)
        assert again.access_token == "" and again.refresh_token is None

    def test_is_null_filters_still_work(self, db_session, keys):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, refresh_token="ref")
        found = (
            db_session.query(Connection.id)
            .filter(Connection.id == row.id, Connection.refresh_token.isnot(None))
            .all()
        )
        assert found == [(row.id,)]

    def test_never_encrypts_twice(self, keys):
        once = enc.encrypt_token("tok")
        assert enc.encrypt_token(once) == once
        assert enc.decrypt_token(once) == "tok"

    def test_ciphertext_is_randomised(self, keys):
        assert enc.encrypt_token("tok") != enc.encrypt_token("tok")


class TestTransition:
    def test_plaintext_rows_read_unchanged_with_a_key(self, db_session, keys):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue)
        db_session.execute(
            text(
                "UPDATE connector_configs SET access_token = 'legacy-acc', "
                "refresh_token = 'legacy-ref' WHERE id = :i"
            ),
            {"i": row.id},
        )
        again = _reload(db_session, row.id)
        assert again.access_token == "legacy-acc"
        assert again.refresh_token == "legacy-ref"

    def test_plaintext_rows_read_unchanged_without_a_key(self, db_session, no_keys):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, access_token="plain-acc")
        assert _raw(db_session, row.id)[0] == "plain-acc"
        assert _reload(db_session, row.id).access_token == "plain-acc"

    def test_no_key_writes_plaintext_and_warns_once(self, no_keys, caplog, monkeypatch):
        monkeypatch.setattr(enc, "_warned_no_key", False)
        monkeypatch.setattr(enc, "_cache", None)
        with caplog.at_level(logging.WARNING, logger="app.db.encrypted"):
            assert enc.encrypt_token("a") == "a"
            assert enc.encrypt_token("b") == "b"
        warnings = [r for r in caplog.records if "TOKEN_ENCRYPTION_KEYS" in r.message]
        assert len(warnings) == 1
        assert enc.encryption_configured() is False

    def test_a_malformed_key_is_reported_without_its_value(
        self, keys, caplog, monkeypatch
    ):
        monkeypatch.setattr(enc, "_warned_bad_keys", set())
        keys("not-a-fernet-key")
        with caplog.at_level(logging.ERROR, logger="app.db.encrypted"):
            assert enc.encrypt_token("a") == "a"
        assert any("not a Fernet key" in r.message for r in caplog.records)
        assert all("not-a-fernet-key" not in r.getMessage() for r in caplog.records)


class TestRotation:
    def test_a_new_key_in_front_still_reads_old_ciphertext(self, db_session, keys):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, refresh_token="ref-a")
        old_raw = _raw(db_session, row.id)[1]

        keys(f"{KEY_B},{KEY_A}")
        assert _reload(db_session, row.id).refresh_token == "ref-a"
        assert enc.encrypted_under_newest_key(old_raw) is False

        rotated = enc.reencrypt_token(old_raw)
        assert rotated != old_raw and enc.encrypted_under_newest_key(rotated)

        keys(KEY_B)  # the old key is dropped once everything is rotated
        assert enc.decrypt_token(rotated) == "ref-a"

    def test_a_dropped_key_reads_as_none_and_is_never_overwritten(
        self, db_session, keys, caplog
    ):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, access_token="acc", refresh_token="ref")
        before = _raw(db_session, row.id)

        keys(KEY_B)
        with caplog.at_level(logging.ERROR, logger="app.db.encrypted"):
            again = _reload(db_session, row.id)
        assert again.refresh_token is None and again.access_token is None
        assert any("could not be decrypted" in r.message for r in caplog.records)

        # Editing another column must not write the unreadable values back.
        again.last_auth_error = "touched"
        db_session.flush()
        assert _raw(db_session, row.id) == before

        keys(KEY_A)  # restoring the key restores the connection
        assert _reload(db_session, row.id).refresh_token == "ref"

    def test_reencrypt_refuses_what_it_cannot_read(self, keys):
        foreign = "enc:v1:" + Fernet(KEY_B.encode()).encrypt(b"x").decode()
        with pytest.raises(ValueError):
            enc.reencrypt_token(foreign)

    def test_reencrypt_needs_a_key(self, no_keys):
        with pytest.raises(ValueError):
            enc.reencrypt_token("plain")


def _oauth_spec(connector_name="loadedhub"):
    spec = MagicMock()
    spec.connector_name = connector_name
    spec.auth_type = "oauth2"
    spec.oauth_config = {
        "token_url": "https://auth.example.com/token",
        "client_id": "cid",
        "client_secret": "secret",
        "scopes": "read",
    }
    return spec


def _token_response(payload: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = ""
    return resp


class TestRefreshPathWithEncryption:
    """oauth_service through the column type — the production path."""

    def test_refresh_sends_plaintext_and_stores_the_rotation_encrypted(
        self, db_session, keys
    ):
        from app.services.oauth_service import refresh_access_token

        venue = _make_venue(db_session)
        row = _connection(
            db_session,
            venue,
            access_token="old-acc",
            refresh_token="old-ref",
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        with patch(
            "app.services.oauth_service.httpx.post",
            return_value=_token_response(
                {
                    "access_token": "new-acc",
                    "refresh_token": "rotated-ref",
                    "expires_in": 3600,
                }
            ),
        ) as post:
            token = refresh_access_token(
                _oauth_spec(), db_session, venue_id=venue.id, force=True
            )

        assert token == "new-acc"
        # The provider got the decrypted refresh token, never ciphertext.
        sent = post.call_args.kwargs["data"]
        assert sent["refresh_token"] == "old-ref"
        assert post.call_count == 1

        raw_access, raw_refresh = _raw(db_session, row.id)
        assert raw_access.startswith("enc:v1:") and raw_refresh.startswith("enc:v1:")
        again = _reload(db_session, row.id)
        assert again.access_token == "new-acc"
        assert again.refresh_token == "rotated-ref"
        assert again.needs_reconnect is False

    def test_a_legacy_plaintext_row_refreshes_and_comes_back_encrypted(
        self, db_session, keys
    ):
        from app.services.oauth_service import refresh_access_token

        venue = _make_venue(db_session)
        row = _connection(db_session, venue)
        db_session.execute(
            text(
                "UPDATE connector_configs SET access_token = 'plain-acc', "
                "refresh_token = 'plain-ref' WHERE id = :i"
            ),
            {"i": row.id},
        )
        db_session.expire_all()
        with patch(
            "app.services.oauth_service.httpx.post",
            return_value=_token_response(
                {"access_token": "a2", "refresh_token": "r2", "expires_in": 60}
            ),
        ) as post:
            refresh_access_token(
                _oauth_spec(), db_session, venue_id=venue.id, force=True
            )
        assert post.call_args.kwargs["data"]["refresh_token"] == "plain-ref"
        assert all(v.startswith("enc:v1:") for v in _raw(db_session, row.id))

    def test_an_omitted_refresh_token_keeps_the_stored_one(self, db_session, keys):
        from app.services.oauth_service import refresh_access_token

        venue = _make_venue(db_session)
        row = _connection(
            db_session, venue, access_token="old", refresh_token="keep-me"
        )
        with patch(
            "app.services.oauth_service.httpx.post",
            return_value=_token_response({"access_token": "new", "expires_in": 60}),
        ):
            refresh_access_token(
                _oauth_spec(), db_session, venue_id=venue.id, force=True
            )
        assert _reload(db_session, row.id).refresh_token == "keep-me"

    def test_get_valid_access_token_returns_plaintext(self, db_session, keys):
        from app.services.oauth_service import get_valid_access_token

        venue = _make_venue(db_session)
        _connection(
            db_session,
            venue,
            access_token="live-acc",
            refresh_token="ref",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.expire_all()
        assert (
            get_valid_access_token(_oauth_spec(), db_session, venue_id=venue.id)
            == "live-acc"
        )

    def test_refresh_all_tokens_sees_encrypted_rows(self, db_session, keys):
        from app.services.oauth_service import refresh_all_tokens

        venue = _make_venue(db_session)
        _connection(
            db_session,
            venue,
            connector_name="enc_test_connector",
            access_token="a",
            refresh_token="r",
        )
        config_db = MagicMock()
        spec = _oauth_spec("enc_test_connector")
        config_db.query.return_value.filter.return_value.first.return_value = spec
        with patch(
            "app.services.oauth_service.httpx.post",
            return_value=_token_response(
                {"access_token": "a2", "refresh_token": "r2", "expires_in": 60}
            ),
        ) as post:
            out = refresh_all_tokens(db=db_session, config_db=config_db)
        assert any("enc_test_connector" in label for label in out["refreshed"])
        bodies = [c.kwargs["data"]["refresh_token"] for c in post.call_args_list]
        assert "r" in bodies

    def test_exchange_code_stores_new_tokens_encrypted(self, db_session, keys):
        from app.db.models import OAuthState
        from app.services.oauth_service import exchange_code

        venue = _make_venue(db_session)
        db_session.add(
            OAuthState(connector_name="loadedhub", state="st-enc", venue_id=venue.id)
        )
        db_session.flush()
        with patch(
            "app.services.oauth_service.httpx.post",
            return_value=_token_response(
                {"access_token": "x-acc", "refresh_token": "x-ref", "expires_in": 60}
            ),
        ):
            exchange_code(
                _oauth_spec(), "code", "st-enc", "https://cb.example", db_session
            )
        row = (
            db_session.query(Connection)
            .filter(
                Connection.connector_name == "loadedhub",
                Connection.venue_id == venue.id,
            )
            .one()
        )
        assert all(v.startswith("enc:v1:") for v in _raw(db_session, row.id))
        assert _reload(db_session, row.id).refresh_token == "x-ref"


class _ConnAdapter:
    """The script's engine, played by the test's own connection so its
    reads and writes stay inside the test transaction."""

    def __init__(self, conn):
        self.conn = conn

    @contextlib.contextmanager
    def connect(self):
        yield self.conn

    @contextlib.contextmanager
    def begin(self):
        with self.conn.begin_nested():
            yield self.conn


def _canary(db, *, write: bool = True) -> None:
    """Start from no marker (the local DB is shared with dev data — this is
    rolled back with the test), then let "the service" write one under the
    keys currently set."""
    db.execute(
        text("DELETE FROM connector_configs WHERE connector_name = :c"),
        {"c": enc.CANARY_CONNECTOR},
    )
    if write:
        assert enc.ensure_key_canary(db) == "written"


def _canary_raw(db) -> list[str]:
    return [r.access_token for r in enc._canary_rows(db)]


class TestBackfillScript:
    def _rows(self, db, keys_value=None):
        _canary(db)
        venue = _make_venue(db)
        plain = _connection(db, venue, connector_name="enc_script_plain")
        db.execute(
            text(
                "UPDATE connector_configs SET access_token = 'p-acc', "
                "refresh_token = 'p-ref' WHERE id = :i"
            ),
            {"i": plain.id},
        )
        current = _connection(
            db,
            venue,
            connector_name="enc_script_current",
            access_token="c-acc",
            refresh_token="c-ref",
        )
        return plain, current

    def _run(self, db, mode, ids):
        import scripts.encrypt_connection_tokens as script

        lines: list[str] = []
        # only_ids: the local DB is shared with real dev data — touch only
        # this test's rows.
        summary = script.run(
            _ConnAdapter(db.connection()), mode, out=lines.append, only_ids=ids
        )
        return summary, lines

    def test_dry_run_counts_and_never_prints_a_token(
        self, db_session, keys, monkeypatch
    ):
        plain, current = self._rows(db_session)
        before = (_raw(db_session, plain.id), _raw(db_session, current.id))
        summary, lines = self._run(db_session, "dry-run", [plain.id, current.id])
        assert summary["values_plaintext"] == 2
        assert summary["values_current"] == 2
        assert summary["rows_changed"] == 0
        assert (_raw(db_session, plain.id), _raw(db_session, current.id)) == before
        joined = "\n".join(lines)
        for secret in ("p-acc", "p-ref", "c-acc", "c-ref", "enc:v1:"):
            assert secret not in joined

    def test_apply_encrypts_plaintext_and_rotates_old_keys(
        self, db_session, keys, monkeypatch
    ):
        plain, current = self._rows(db_session)
        keys(f"{KEY_B},{KEY_A}")  # KEY_A wrote `current`; KEY_B is now newest
        _canary(db_session)  # the service, deployed with KEY_B, marks it
        summary, _ = self._run(db_session, "apply", [plain.id, current.id])
        assert summary["rows_changed"] == 2
        for row in (plain, current):
            for value in _raw(db_session, row.id):
                assert enc.encrypted_under_newest_key(value)
        keys(KEY_B)
        assert _reload(db_session, plain.id).refresh_token == "p-ref"
        assert _reload(db_session, current.id).refresh_token == "c-ref"

    def test_decrypt_writes_plaintext_back(self, db_session, keys, monkeypatch):
        plain, current = self._rows(db_session)
        self._run(db_session, "decrypt", [plain.id, current.id])
        assert _raw(db_session, current.id) == ("c-acc", "c-ref")
        assert _raw(db_session, plain.id) == ("p-acc", "p-ref")

    def test_unreadable_values_are_skipped_not_overwritten(
        self, db_session, keys, monkeypatch
    ):
        _, current = self._rows(db_session)
        before = _raw(db_session, current.id)
        keys(KEY_B)  # KEY_A is gone: `current` cannot be read
        _canary(db_session)
        summary, lines = self._run(db_session, "apply", [current.id])
        assert summary["rows_skipped"] == 1
        assert _raw(db_session, current.id) == before
        assert any("SKIPPED" in line for line in lines)

    def test_a_row_that_changes_mid_write_is_reread(
        self, db_session, keys, monkeypatch
    ):
        """A keep-alive rotating the refresh token between the script's read
        and its write must win: the script re-reads and encrypts the NEW
        token instead of writing back the one it saw."""
        import scripts.encrypt_connection_tokens as script

        plain, _ = self._rows(db_session)
        real_target = script.target
        calls = {"n": 0}

        def racing_target(value, mode):
            calls["n"] += 1
            if calls["n"] == 1:
                db_session.connection().execute(
                    text(
                        "UPDATE connector_configs SET refresh_token = 'rotated' "
                        "WHERE id = :i"
                    ),
                    {"i": plain.id},
                )
            return real_target(value, mode)

        monkeypatch.setattr(script, "target", racing_target)
        summary, _ = self._run(db_session, "apply", [plain.id])
        assert summary["retries"] >= 1 and summary["rows_changed"] == 1
        assert _reload(db_session, plain.id).refresh_token == "rotated"
        assert all(v.startswith("enc:v1:") for v in _raw(db_session, plain.id))


class TestKeyCanary:
    """--apply must never re-encrypt under a key the service doesn't hold:
    the service leaves a marker under its newest key, and the script checks
    it holds that key before writing anything."""

    def _rows(self, db):
        venue = _make_venue(db)
        conn = _connection(db, venue, connector_name="enc_canary_plain")
        db.execute(
            text(
                "UPDATE connector_configs SET access_token = 'p-acc', "
                "refresh_token = 'p-ref' WHERE id = :i"
            ),
            {"i": conn.id},
        )
        return conn

    def _run(self, db, mode, ids):
        return TestBackfillScript._run(self, db, mode, ids)

    def test_the_service_writes_and_keeps_its_marker(self, db_session, keys, no_keys):
        _canary(db_session, write=False)
        assert enc.ensure_key_canary(db_session) == "no_key"
        assert enc.canary_status(db_session) == "missing"
        keys(KEY_A)
        assert enc.ensure_key_canary(db_session) == "written"
        assert enc.ensure_key_canary(db_session) == "current"
        assert enc.canary_status(db_session) == "newest"
        (raw,) = _canary_raw(db_session)
        assert raw.startswith("enc:v1:") and enc.CANARY_TEXT not in raw
        keys(f"{KEY_B},{KEY_A}")  # a rotation: the next tick re-marks it
        assert enc.canary_status(db_session) == "older_key"
        assert enc.ensure_key_canary(db_session) == "written"
        assert enc.canary_status(db_session) == "newest"
        keys(KEY_A)  # a script holding only the old key can't read it
        assert enc.canary_status(db_session) == "unreadable"

    def test_apply_refuses_without_a_marker(self, db_session, keys):
        _canary(db_session, write=False)
        conn = self._rows(db_session)
        summary, lines = self._run(db_session, "apply", [conn.id])
        assert summary["key_canary"] == "missing" and summary["refused"]
        assert _raw(db_session, conn.id) == ("p-acc", "p-ref")
        assert any(line.startswith("REFUSED") for line in lines)

    def test_apply_refuses_a_key_the_service_does_not_hold(self, db_session, keys):
        """The review's case: the operator's shell has a different key from
        the service. Writing would make every token read as None."""
        keys(KEY_B)
        _canary(db_session)  # the service runs KEY_B
        conn = self._rows(db_session)
        keys(KEY_A)  # the operator's setting
        summary, _ = self._run(db_session, "apply", [conn.id])
        assert summary["key_canary"] == "unreadable" and summary["refused"]
        assert _raw(db_session, conn.id) == ("p-acc", "p-ref")

    def test_apply_refuses_when_its_newest_key_is_not_the_services(
        self, db_session, keys
    ):
        _canary(db_session)  # service: KEY_A
        conn = self._rows(db_session)
        keys(f"{KEY_B},{KEY_A}")  # operator added KEY_B before the service did
        summary, _ = self._run(db_session, "apply", [conn.id])
        assert summary["key_canary"] == "older_key" and summary["refused"]
        assert _raw(db_session, conn.id) == ("p-acc", "p-ref")

    def test_decrypt_needs_only_a_key_that_reads_the_marker(self, db_session, keys):
        """The rollback aid still works from a setting that holds the
        service's key among others."""
        _canary(db_session)  # service: KEY_A
        venue = _make_venue(db_session)
        conn = _connection(
            db_session,
            venue,
            connector_name="enc_canary_current",
            access_token="c-acc",
            refresh_token="c-ref",
        )
        keys(f"{KEY_B},{KEY_A}")
        summary, _ = self._run(db_session, "decrypt", [conn.id])
        assert summary["key_canary"] == "older_key" and "refused" not in summary
        assert _raw(db_session, conn.id) == ("c-acc", "c-ref")
        keys(KEY_B)
        summary, _ = self._run(db_session, "decrypt", [conn.id])
        assert summary["refused"]

    def test_the_marker_itself_is_never_rewritten_by_the_script(self, db_session, keys):
        _canary(db_session)
        before = _canary_raw(db_session)
        canary_id = enc._canary_rows(db_session)[0].id
        conn = self._rows(db_session)
        summary, _ = self._run(db_session, "apply", [conn.id, canary_id])
        assert summary["rows_changed"] == 1
        assert _canary_raw(db_session) == before
        self._run(db_session, "decrypt", [conn.id, canary_id])
        assert _canary_raw(db_session) == before


def _raw_config(db, conn_id: str) -> dict:
    import json

    raw = db.execute(
        text("SELECT config::text FROM connector_configs WHERE id = :i"), {"i": conn_id}
    ).scalar()
    return json.loads(raw)


class TestTypedSecrets:
    """The secrets typed into a connection's credentials (19 Oct 2026, owner:
    "encrypt all typed secrets"): encrypted by name, so every path that
    rewrites a connection keeps them encrypted."""

    def test_secret_named_values_are_encrypted_and_others_are_not(
        self, db_session, keys
    ):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, connector_name="typed_secrets")
        row.config = {"api_key": "k-1", "password": "pw-1", "customer_code": "C9"}
        db_session.flush()
        raw = _raw_config(db_session, row.id)
        assert enc.is_encrypted(raw["api_key"]) and enc.is_encrypted(raw["password"])
        assert raw["customer_code"] == "C9"
        assert _reload(db_session, row.id).config == {
            "api_key": "k-1",
            "password": "pw-1",
            "customer_code": "C9",
        }

    def test_another_path_rewriting_the_config_keeps_them_encrypted(
        self, db_session, keys
    ):
        # The OAuth callback merges a company id into config the same way.
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, connector_name="typed_secrets")
        row.config = {"api_key": "k-1"}
        db_session.flush()
        row = _reload(db_session, row.id)
        row.config = {**row.config, "x_loaded_company_id": "c-1"}
        db_session.flush()
        raw = _raw_config(db_session, row.id)
        assert enc.is_encrypted(raw["api_key"]) and raw["x_loaded_company_id"] == "c-1"

    def test_plaintext_secrets_already_stored_read_unchanged(self, db_session, keys):
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, connector_name="typed_secrets")
        db_session.execute(
            text(
                """UPDATE connector_configs SET config = '{"api_key": "old"}' """
                "WHERE id = :i"
            ),
            {"i": row.id},
        )
        assert _reload(db_session, row.id).config == {"api_key": "old"}

    def test_every_secret_field_in_use_has_a_secret_name(self):
        from app.connectors.loaded_legacy import SPEC
        from app.routers.connectors import _get_platform_connectors
        from app.services.config_validator import check_secret_credential_fields

        # The secret fields of the specs live in production on 19 Oct 2026.
        for key in ("api_key", "access_token", "user_token", "password"):
            assert enc.is_secret_key(key), key
        for meta in _get_platform_connectors():
            for field in meta["fields"]:
                assert not field.get("secret") or enc.is_secret_key(field["key"])
        assert not check_secret_credential_fields(
            "loaded_legacy", SPEC["credential_fields"]
        )
        # A secret field without a secret's name would be stored in plain text.
        [issue] = check_secret_credential_fields("x", [{"key": "pin", "secret": True}])
        assert "unencrypted" in issue.problem
        for plain in ("x_loaded_company_id", "username", "customer_code", "subdomain"):
            assert not enc.is_secret_key(plain), plain

    def test_the_backfill_encrypts_and_decrypts_typed_secrets(self, db_session, keys):
        import scripts.encrypt_connection_tokens as script

        _canary(db_session)
        venue = _make_venue(db_session)
        row = _connection(db_session, venue, connector_name="typed_secrets")
        db_session.execute(
            text(
                """UPDATE connector_configs SET config = """
                """'{"api_key": "plain-k", "subdomain": "acme"}' WHERE id = :i"""
            ),
            {"i": row.id},
        )
        lines: list[str] = []
        summary = script.run(
            _ConnAdapter(db_session.connection()),
            "dry-run",
            out=lines.append,
            only_ids=[row.id],
        )
        assert summary["config_values_plaintext"] == 1
        assert "plain-k" not in "\n".join(lines)
        summary = script.run(
            _ConnAdapter(db_session.connection()),
            "apply",
            out=lines.append,
            only_ids=[row.id],
        )
        assert summary["config_rows_changed"] == 1
        raw = _raw_config(db_session, row.id)
        assert (
            enc.encrypted_under_newest_key(raw["api_key"])
            and raw["subdomain"] == "acme"
        )
        script.run(
            _ConnAdapter(db_session.connection()),
            "decrypt",
            out=lines.append,
            only_ids=[row.id],
        )
        assert _raw_config(db_session, row.id) == {
            "api_key": "plain-k",
            "subdomain": "acme",
        }
