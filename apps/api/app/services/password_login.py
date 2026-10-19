"""Password login — a connector authentication type (``auth_type`` "password_login").

Some APIs hand a token only to a username-and-password login (an OAuth
"password grant"). The connector's spec says where and how, in ``auth_config``::

    {
      "token_url": "https://example.com/api/token",
      "client_id": "web-app",              # optional
      "username_field": "username",        # credential keys (default as shown)
      "password_field": "password",
      "extra_form": {"remember": "false"}, # optional, sent as is
      "accounts": {                        # optional: one login, many accounts
        "url": "https://example.com/api/me",  # GET with the new token
        "list": "companies",               # where the list is in the reply
        "id": "id", "name": "name",        # each account's id and name
        "config_key": "account_id",        # where the chosen id is kept
        "label": "company"                 # what the screens call one
      }
    }

The username and password are the connection's credentials, typed into the
standard connect card per venue; the password is stored encrypted like every
typed secret (db/encrypted.py). The token goes in the connection's encrypted
token column with its expiry.

- Norm logs in only when it is about to make a call and holds no valid token,
  or the API answered 401 — never to keep a session warm (each login may cost
  the person something: Loaded's web app, for one, keeps one session per
  person and a new login replaces it).
- One login at a time per connection (the row is locked; a token another
  request just got is reused).
- A refused login is recorded on the connection (``needs_reconnect``,
  ``last_auth_error`` — the connect card and Settings → Connections show it),
  the admins get one email per run of failures, and Norm waits RETRY_AFTER
  before trying again by itself, so a changed password can't lock the account
  through repeated attempts. Saving credentials again (an admin) retries now.
- Saving credentials checks them first; a refused login is never saved.
- When one login can reach several accounts (Loaded: every company the person
  belongs to), the spec's ``accounts`` says how to list them. Connecting lists
  them by name and an admin picks the venue's one; it is kept in the
  connection's own config (``config_key``) and sent with every call, like any
  connector's account id. The pick must be one the login can see.

First used by Loaded Legacy (app/connectors/loaded_legacy.py).
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger(__name__)

AUTH_TYPE = "password_login"
#: Log in again this long before the token's expiry.
EXPIRY_MARGIN = timedelta(minutes=10)
#: After a refused login, the earliest Norm tries again on its own.
RETRY_AFTER = timedelta(hours=1)
#: When the API gives no expiry.
DEFAULT_LIFETIME = timedelta(hours=24)
TIMEOUT = 30.0


class PasswordLoginError(Exception):
    """A failure whose message is safe to show a person (never a secret)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _fields(spec) -> tuple[str, str]:
    cfg = spec.auth_config or {}
    return cfg.get("username_field") or "username", cfg.get(
        "password_field"
    ) or "password"


def _reason(resp: httpx.Response) -> str:
    """The API's reason for refusing a login, in its own words."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = None
    if isinstance(body, dict):
        text = body.get("error_description") or body.get("error") or body.get("message")
        if text:
            return f"{str(text)[:200]}"
    return f"The login was refused ({resp.status_code})"


def request_token(spec, username: str, password: str) -> dict:
    """Log in. ``{"access_token", "expires_at"}``; raises PasswordLoginError
    with the API's reason (never the request)."""
    cfg = spec.auth_config or {}
    url = cfg.get("token_url")
    if not url:
        raise PasswordLoginError(f"{spec.display_name} has no login address set up.")
    if not username or not password:
        raise PasswordLoginError("Enter the username and password.")
    form = {"grant_type": "password", "username": username, "password": password}
    if cfg.get("client_id"):
        form["client_id"] = cfg["client_id"]
    form.update({k: str(v) for k, v in (cfg.get("extra_form") or {}).items()})
    try:
        resp = httpx.post(
            url,
            data=form,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PasswordLoginError(
            f"Couldn't reach {spec.display_name} ({type(exc).__name__})"
        ) from None
    if resp.status_code != 200:
        raise PasswordLoginError(_reason(resp))
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        raise PasswordLoginError("The login answer wasn't readable") from None
    if not isinstance(data, dict) or not data.get("access_token"):
        raise PasswordLoginError("The login answer had no token")
    try:
        life = timedelta(seconds=int(data.get("expires_in")))
    except (TypeError, ValueError):
        life = DEFAULT_LIFETIME
    return {"access_token": data["access_token"], "expires_at": _now() + life}


def _state(row) -> dict:
    meta = row.oauth_metadata if isinstance(row.oauth_metadata, dict) else {}
    return dict(meta.get("password_login") or {})


def _set_state(row, **values) -> None:
    meta = (
        copy.deepcopy(row.oauth_metadata)
        if isinstance(row.oauth_metadata, dict)
        else {}
    )
    state = dict(meta.get("password_login") or {})
    state.update(
        {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in values.items()
        }
    )
    meta["password_login"] = state
    row.oauth_metadata = meta
    flag_modified(row, "oauth_metadata")


def _store(row, token: dict) -> str:
    row.access_token = token["access_token"]
    row.token_expires_at = token["expires_at"]
    row.needs_reconnect = False
    row.last_auth_error = None
    _set_state(row, last_login_at=_now(), failed_since=None)
    return row.access_token


def _login(spec, db: Session, row) -> str:
    """Log in with the connection's credentials (row locked by the caller)."""
    ukey, pkey = _fields(spec)
    config = row.config or {}
    try:
        token = request_token(
            spec, str(config.get(ukey) or ""), str(config.get(pkey) or "")
        )
    except PasswordLoginError as exc:
        first = not row.needs_reconnect
        row.needs_reconnect = True
        row.last_auth_error = str(exc)[:500]
        row.access_token = None
        row.token_expires_at = None
        _set_state(
            row,
            last_login_at=_now(),
            failed_since=_now() if first else _state(row).get("failed_since"),
        )
        db.commit()
        logger.warning(
            "password login refused",
            extra={
                "connector": spec.connector_name,
                "venue_id": row.venue_id,
                "reason": str(exc)[:200],
            },
        )
        if first:
            _email_failure(db, spec, row)
        raise
    out = _store(row, token)
    db.commit()
    logger.info(
        "password login ok",
        extra={"connector": spec.connector_name, "venue_id": row.venue_id},
    )
    return out


def _connection(spec, db: Session, venue_id: str | None, *, lock: bool = False):
    """The venue's own connection, else the organisation-wide one (the same
    fallback the executor and the connect card use)."""
    from app.db.models import Connection

    def q(vid):
        query = db.query(Connection).filter(
            Connection.connector_name == spec.connector_name,
            Connection.user_id.is_(None),
            Connection.enabled == "true",
            Connection.venue_id == vid if vid else Connection.venue_id.is_(None),
        )
        return (query.with_for_update().populate_existing() if lock else query).first()

    return (q(venue_id) if venue_id else None) or q(None)


def _valid(row) -> bool:
    exp = _aware(row.token_expires_at)
    return bool(row.access_token and exp and _now() < exp - EXPIRY_MARGIN)


def access_token(
    spec, db: Session, venue_id: str | None, *, force: bool = False
) -> str:
    """A token for this venue's connection: the stored one while it is valid,
    else a fresh login. ``force`` (the API answered 401) logs in even while the
    stored token looks valid. Never within RETRY_AFTER of a refused login."""
    row = _connection(spec, db, venue_id)
    if row is None:
        raise PasswordLoginError(
            f"{spec.display_name} isn't connected for this venue — connect it in "
            "Settings → Connections."
        )
    if not force and _valid(row):
        return row.access_token
    stale = row.access_token
    row = _connection(spec, db, venue_id, lock=True)
    if _valid(row) and (not force or row.access_token != stale):
        db.commit()  # another request logged in while we waited
        return row.access_token
    last = (
        _aware(datetime.fromisoformat(_state(row)["last_login_at"]))
        if _state(row).get("last_login_at")
        else None
    )
    if row.needs_reconnect and last and _now() - last < RETRY_AFTER:
        db.commit()
        raise PasswordLoginError(
            f"Norm couldn't log in to {spec.display_name} ({row.last_auth_error}). It "
            f"tries again after {(last + RETRY_AFTER).strftime('%H:%M UTC')}, or an "
            "admin can enter a current login in Settings → Connections."
        )
    return _login(spec, db, row)


def relogin(spec, db: Session, venue_id: str | None) -> str:
    """The API answered 401: log in again (once — the executor replays once)."""
    return access_token(spec, db, venue_id, force=True)


# ---------------------------------------------------------------------------
# The connect card (routers/connectors.upsert_connector)
# ---------------------------------------------------------------------------


class ChooseAccount(PasswordLoginError):
    """The login works but no account is chosen (or the chosen one isn't among
    the login's): the connect form shows ``accounts`` to pick from."""

    def __init__(self, message: str, choice: dict):
        super().__init__(message)
        self.choice = choice


def account_choice(spec) -> dict | None:
    """The spec's ``accounts`` rule — the key the chosen id is kept under, and
    what an account is called — or None when one login means one account."""
    rule = (spec.auth_config or {}).get("accounts") or {}
    if not rule.get("url") or not rule.get("config_key"):
        return None
    return {"key": rule["config_key"], "label": rule.get("label") or "account"}


def list_accounts(spec, token: str) -> list[dict]:
    """The accounts this login can reach, ``[{"id", "name"}]`` by name."""
    rule = (spec.auth_config or {}).get("accounts") or {}
    try:
        resp = httpx.get(
            rule["url"],
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PasswordLoginError(
            f"Couldn't reach {spec.display_name} ({type(exc).__name__})"
        ) from None
    if resp.status_code != 200:
        raise PasswordLoginError(
            f"{spec.display_name} wouldn't list this login's accounts "
            f"({resp.status_code})"
        )
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = None
    items = (
        data.get(rule["list"]) if rule.get("list") and isinstance(data, dict) else data
    )
    id_key, name_key = rule.get("id") or "id", rule.get("name") or "name"
    accounts = [
        {"id": str(a[id_key]), "name": str(a.get(name_key) or a[id_key])}
        for a in (items if isinstance(items, list) else [])
        if isinstance(a, dict) and a.get(id_key)
    ]
    return sorted(accounts, key=lambda a: a["name"].lower())


def _simple(name: str) -> str:
    name = name.lower().replace("&", " and ")
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return re.sub(r"^the ", "", re.sub(r"\s+", " ", name).strip())


def _suggest(accounts: list[dict], venue_name: str | None) -> str | None:
    """The account named like the venue, when exactly one is (a suggestion
    only — the admin picks)."""
    if not venue_name:
        return None
    same = [a["id"] for a in accounts if _simple(a["name"]) == _simple(venue_name)]
    return same[0] if len(same) == 1 else None


def check_and_prepare(
    spec, db: Session, venue_id: str | None, config: dict
) -> tuple[dict, dict, dict | None]:
    """Before credentials are saved: log in with them and, when the spec lists
    accounts, check the chosen one is among the login's. Returns ``(config,
    token, account)``. Raises PasswordLoginError (nothing is saved) when the
    login is refused, and ChooseAccount when an account must be picked."""
    from app.db.models import Venue

    config = dict(config or {})
    ukey, pkey = _fields(spec)
    token = request_token(
        spec, str(config.get(ukey) or "").strip(), str(config.get(pkey) or "")
    )
    choice = account_choice(spec)
    if choice is None:
        return config, token, None
    accounts = list_accounts(spec, token["access_token"])
    chosen = str(config.get(choice["key"]) or "").strip()
    account = next((a for a in accounts if a["id"] == chosen), None)
    if account is None:
        venue = db.get(Venue, venue_id) if venue_id else None
        raise ChooseAccount(
            (
                f"This login can't see the {choice['label']} chosen before. "
                if chosen
                else ""
            )
            + f"Choose the {choice['label']} for "
            + (venue.name if venue else "this connection")
            + ".",
            {
                **choice,
                "accounts": accounts,
                "suggested": _suggest(accounts, venue.name if venue else None),
            },
        )
    return config, token, account


def store_checked_token(row, token: dict, account: dict | None = None) -> None:
    """After the save: keep the token the check just got (no second login),
    and the chosen account's name for the screens."""
    _store(row, token)
    if account is not None:
        _set_state(row, account_name=account["name"])


def account_name(row) -> str | None:
    """The name of the account this connection works on (Settings shows it)."""
    return _state(row).get("account_name") if row is not None else None


def _email_failure(db: Session, spec, row) -> None:
    """Tell Norm's admins, once per run of failures. Never raises."""
    from app.config import settings
    from app.db.models import User, Venue
    from app.services.email_service import send_system_email

    try:
        to = sorted(
            {
                u.email
                for u in db.query(User).filter(User.role == "admin").all()
                if u.email and not u.email.endswith((".test", ".local"))
            }
        )
        if not to:
            return
        venue = db.get(Venue, row.venue_id) if row.venue_id else None
        ukey, _ = _fields(spec)
        send_system_email(
            "connector_login_failed",
            to,
            {
                "connector": spec.display_name,
                "venue": venue.name if venue else "every venue",
                "username": (row.config or {}).get(ukey),
                "error": row.last_auth_error,
                "link": f"{settings.app_url}/app?open=connections",
            },
            db,
        )
    except Exception:  # noqa: BLE001 — an email never breaks a login
        logger.exception("password login failure email failed")
