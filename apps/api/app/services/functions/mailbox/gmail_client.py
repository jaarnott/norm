"""A thin Gmail REST client for venue mailboxes.

Tokens come from the venue's ``venue_mailbox`` connection through Norm's own
OAuth machinery (``oauth_service.refresh_access_token`` — the row-locked,
one-redemption-at-a-time refresh every connector uses), read in a short
session of their own: a sync or request never holds its database connection
while Google answers.

Every failure is one of four kinds, because each needs a different response:

* ``GmailNotFound`` (404/410) — the thing is gone: a deleted message is
  removed from the mirror, an expired history cursor triggers a resync.
* ``GmailTransient`` (429, 5xx, network) — try again later and move nothing.
  ``ambiguous`` is set when a WRITE may have happened anyway (a timeout or a
  5xx after the request was sent), which the outbox must not retry blindly.
* ``MailboxAuthError`` — a person must reconnect: the grant is dead
  (``invalid_grant``), or it never included Gmail access.
* ``GmailError`` — anything else (a bad request, the API switched off).

Writes refuse unless the client was made ``writable`` (the organisation is
live): shadow mode reads Gmail and never changes it.

``TRANSPORT`` is the seam tests use to play Gmail (an ``httpx.MockTransport``);
``tokens_for`` is the seam for the token source.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.services.functions.errors import NotLive

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CONNECTOR = "venue_mailbox"

#: Tests replace this with ``httpx.MockTransport(fake)``.
TRANSPORT: httpx.BaseTransport | None = None
TIMEOUT = 20.0
#: A token proven to sign in as the right mailbox is trusted this long
#: before Gmail is asked again (request paths; the sync asks every tick).
IDENTITY_TTL = 300.0
_verified: dict[str, tuple[str, float]] = {}


class GmailError(Exception):
    def __init__(self, message: str, *, status: int | None = None, reason: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason


class GmailNotFound(GmailError):
    pass


class GmailTransient(GmailError):
    def __init__(self, message: str, *, ambiguous: bool = False, **kw):
        super().__init__(message, **kw)
        self.ambiguous = ambiguous


class GmailRateLimited(GmailTransient):
    pass


class MailboxAuthError(GmailError):
    """The mailbox needs reconnecting (dead grant, or no Gmail permission)."""


class TokenProvider(Protocol):
    def access_token(self, *, force_refresh: bool = False) -> str: ...


def _independent(db: Session | None):
    """A session of its own for the token read/refresh (``refresh_access_token``
    commits and row-locks; neither may land in the caller's transaction)."""
    from app.services.functions.mailbox._sessions import independent_session

    return independent_session(db)


class ConnectionTokens:
    """Access tokens for one ``venue_mailbox`` connection row."""

    def __init__(
        self,
        connection_id: str,
        *,
        db: Session | None = None,
        config_db: Session | None = None,
    ):
        self.connection_id = connection_id
        self._db = db
        self._config_db = config_db

    def _spec(self):
        from app.db.config_models import ConnectionSpec

        if self._config_db is not None:
            return (
                self._config_db.query(ConnectionSpec)
                .filter(ConnectionSpec.connector_name == CONNECTOR)
                .first()
            )
        from app.db.engine import _ConfigSessionLocal

        cfg = _ConfigSessionLocal()
        try:
            return (
                cfg.query(ConnectionSpec)
                .filter(ConnectionSpec.connector_name == CONNECTOR)
                .first()
            )
        finally:
            cfg.close()

    def access_token(self, *, force_refresh: bool = False) -> str:
        from app.db.models import Connection
        from app.services.oauth_service import refresh_access_token

        session = _independent(self._db)
        try:
            conn = (
                session.query(Connection)
                .filter(Connection.id == self.connection_id)
                .first()
            )
            if conn is None or not (conn.access_token or conn.refresh_token):
                raise MailboxAuthError("The mailbox is not connected.")
            if conn.needs_reconnect:
                raise MailboxAuthError(
                    conn.last_auth_error or "Google refused the mailbox's sign-in."
                )
            expires = conn.token_expires_at
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=dt.timezone.utc)
            fresh = expires is None or expires > dt.datetime.now(
                dt.timezone.utc
            ) + dt.timedelta(seconds=60)
            if conn.access_token and fresh and not force_refresh:
                return conn.access_token
            spec = self._spec()
            if spec is None or not spec.oauth_config:
                raise GmailError("The venue mailbox connector is not set up.")
            venue_id = conn.venue_id
            try:
                return refresh_access_token(
                    spec, session, venue_id=venue_id, force=True
                )
            except ValueError as exc:
                # refresh_access_token has already flagged the connection
                # (needs_reconnect) when Google refused the grant.
                raise MailboxAuthError(str(exc)[:300]) from exc
            except httpx.HTTPError as exc:
                raise GmailTransient(f"Google's sign-in service: {exc}") from exc
        finally:
            session.close()


def tokens_for(
    connection_id: str, *, db: Session | None = None, config_db: Session | None = None
) -> TokenProvider:
    """The token source for a connection (tests replace this)."""
    return ConnectionTokens(connection_id, db=db, config_db=config_db)


def _error_from(resp: httpx.Response, *, write: bool) -> GmailError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    err = (body or {}).get("error") or {}
    if isinstance(err, str):  # OAuth-style {"error": "invalid_grant"}
        reasons = {err}
        message = (body or {}).get("error_description") or err
    else:
        reasons = {str(e.get("reason") or "") for e in (err.get("errors") or [])} | {
            str(err.get("status") or "")
        }
        message = err.get("message") or resp.text[:300]
    status = resp.status_code
    reason = ",".join(sorted(r for r in reasons if r))
    if status in (404, 410):
        return GmailNotFound(message, status=status, reason=reason)
    if status == 429 or (
        status == 403 and reasons & {"rateLimitExceeded", "userRateLimitExceeded"}
    ):
        return GmailRateLimited(message, status=status, reason=reason)
    if status == 401 or reasons & {"invalid_grant"}:
        return MailboxAuthError(
            "Google refused the mailbox's sign-in.", status=status, reason=reason
        )
    if status == 403 and reasons & {
        "insufficientPermissions",
        "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    }:
        return MailboxAuthError(
            "The mailbox was connected without Gmail access — reconnect it and "
            "allow Gmail.",
            status=status,
            reason=reason,
        )
    if status >= 500:
        return GmailTransient(message, status=status, reason=reason, ambiguous=write)
    return GmailError(message, status=status, reason=reason)


class GmailClient:
    """One mailbox. ``writable`` = the organisation is live."""

    def __init__(
        self,
        tokens: TokenProvider,
        *,
        writable: bool,
        transport: httpx.BaseTransport | None = None,
        expected_email: str | None = None,
    ):
        self.tokens = tokens
        self.writable = writable
        #: The mailbox this client must be signed in as (None = the connect
        #: flow, which is finding that out).
        self.expected_email = (expected_email or "").strip().lower() or None
        self._identity_ok = False
        self._http = httpx.Client(
            transport=transport if transport is not None else TRANSPORT,
            timeout=TIMEOUT,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params=None,
        json: dict | None = None,
        write: bool = False,
    ) -> dict:
        if write and not self.writable:
            raise NotLive(
                "Functions is in shadow mode — Norm reads the mailbox but does "
                "not change it until the organisation goes live."
            )
        if self.expected_email and not self._identity_ok and path != "/profile":
            self.verify_identity()
        token = self.tokens.access_token()
        for attempt in (0, 1):
            try:
                resp = self._http.request(
                    method,
                    GMAIL_API + path,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.ConnectError as exc:
                # Never reached Google: nothing can have happened.
                raise GmailTransient(f"Gmail couldn't be reached: {exc}") from exc
            except httpx.HTTPError as exc:
                raise GmailTransient(
                    f"Gmail didn't answer: {exc}", ambiguous=write
                ) from exc
            if resp.status_code == 401 and attempt == 0:
                # An access token Google no longer accepts: one forced
                # refresh, then the request again.
                token = self.tokens.access_token(force_refresh=True)
                continue
            break
        if resp.status_code >= 300:
            raise _error_from(resp, write=write)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def verify_identity(self, *, fresh: bool = False) -> None:
        """Refuse to work for the wrong mailbox: the connection's grant must
        sign in as ``expected_email``. A grant swapped underneath an account
        (another Google account connected onto the venue) raises
        ``MailboxAuthError`` — the sync then marks the mailbox
        ``needs_reconnect``, and sends and reads refuse."""
        import hashlib
        import time

        if not self.expected_email:
            return
        key = hashlib.sha256(self.tokens.access_token().encode()).hexdigest()
        hit = _verified.get(key)
        if (
            not fresh
            and hit is not None
            and hit[0] == self.expected_email
            and time.monotonic() - hit[1] < IDENTITY_TTL
        ):
            self._identity_ok = True
            return
        actual = str(self.profile().get("emailAddress") or "").strip().lower()
        if actual != self.expected_email:
            raise MailboxAuthError(
                f"This venue's Google connection now signs in as "
                f"{actual or 'another account'}, not {self.expected_email} — "
                "reconnect the mailbox."
            )
        if len(_verified) > 1000:
            _verified.clear()
        _verified[key] = (actual, time.monotonic())
        self._identity_ok = True

    # -- reads ------------------------------------------------------------
    def profile(self) -> dict:
        return self._request("GET", "/profile")

    def history(self, start_history_id: int, page_token: str | None = None) -> dict:
        params: list[tuple[str, str]] = [
            ("startHistoryId", str(start_history_id)),
            ("maxResults", "500"),
        ]
        for kind in ("messageAdded", "messageDeleted", "labelAdded", "labelRemoved"):
            params.append(("historyTypes", kind))
        if page_token:
            params.append(("pageToken", page_token))
        return self._request("GET", "/history", params=params)

    def list_messages(
        self,
        q: str | None = None,
        *,
        page_token: str | None = None,
        max_results: int = 500,
    ) -> dict:
        params: dict = {"maxResults": str(max_results)}
        if q:
            params["q"] = q
        if page_token:
            params["pageToken"] = page_token
        return self._request("GET", "/messages", params=params)

    def message(self, message_id: str, fmt: str = "metadata") -> dict:
        from app.services.functions.mailbox.mime import METADATA_HEADERS

        params: list[tuple[str, str]] = [("format", fmt)]
        if fmt == "metadata":
            params += [("metadataHeaders", h) for h in METADATA_HEADERS]
        return self._request("GET", f"/messages/{message_id}", params=params)

    def thread(self, thread_id: str, fmt: str = "full") -> dict:
        from app.services.functions.mailbox.mime import METADATA_HEADERS

        params: list[tuple[str, str]] = [("format", fmt)]
        if fmt == "metadata":
            params += [("metadataHeaders", h) for h in METADATA_HEADERS]
        return self._request("GET", f"/threads/{thread_id}", params=params)

    def attachment(self, message_id: str, attachment_id: str) -> dict:
        """``{"size", "data"}`` — the attachment's bytes, base64url."""
        return self._request(
            "GET", f"/messages/{message_id}/attachments/{attachment_id}"
        )

    def labels(self) -> list[dict]:
        return self._request("GET", "/labels").get("labels") or []

    def send_as(self) -> list[dict]:
        return self._request("GET", "/settings/sendAs").get("sendAs") or []

    def draft(self, draft_id: str, fmt: str = "full") -> dict:
        return self._request("GET", f"/drafts/{draft_id}", params={"format": fmt})

    # -- writes -----------------------------------------------------------
    def send(self, raw: str, thread_id: str | None = None) -> dict:
        body: dict = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return self._request("POST", "/messages/send", json=body, write=True)

    def create_draft(self, raw: str, thread_id: str | None = None) -> dict:
        message: dict = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        return self._request("POST", "/drafts", json={"message": message}, write=True)

    def delete_draft(self, draft_id: str) -> None:
        self._request("DELETE", f"/drafts/{draft_id}", write=True)

    def modify_thread(
        self, thread_id: str, *, add: list[str] = (), remove: list[str] = ()
    ) -> dict:
        return self._request(
            "POST",
            f"/threads/{thread_id}/modify",
            json={"addLabelIds": list(add), "removeLabelIds": list(remove)},
            write=True,
        )


def client_for(
    account,
    *,
    writable: bool,
    db: Session | None = None,
    config_db: Session | None = None,
) -> GmailClient:
    """A client for a mailbox account (anything with ``connection_id``)."""
    if not getattr(account, "connection_id", None):
        raise MailboxAuthError("The mailbox is not connected.")
    return GmailClient(
        tokens_for(account.connection_id, db=db, config_db=config_db),
        writable=writable,
        expected_email=getattr(account, "google_email", None),
    )
