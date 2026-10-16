"""Venue mailboxes: connecting one, finding one, and how healthy each is.

**Connecting** goes through Norm's own OAuth connector ``venue_mailbox``
(``scripts/sync_venue_mailbox.py``) — the same per-venue Google connection
Norm Hiring's calendar invites use, so a venue has one Google grant, stored
(encrypted) on ``connector_configs``. Functions asks for its scopes with
``include_granted_scopes``, so a mailbox connected for calendar invites keeps
them. The Google OAuth client is read where every connector's is — the
spec's ``oauth_config`` — and its absence is a clear refusal, not a crash.

The callback attaches the grant to a ``fn_mailbox_accounts`` row keyed by
the mailbox's address: the importer's placeholder for that address when
there is one (it must be at the same venue), otherwise a new row. Attaching
reads the mailbox's signature (Gmail's send-as settings) and its labels (the
``Actioned - …`` folders), and marks the account for its initial sync.

**Health** is per mailbox: when it last synced cleanly, the last error, how
many ticks in a row failed, and whether it lags — the sync endpoint answers
non-200 when an active mailbox has not synced cleanly for ten minutes.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.db.functions_models import FnMailboxAccount
from app.db.models import Connection, Venue, _now
from app.services.functions._rows import load
from app.services.functions.ctx import FunctionsCtx, require_live, require_venue
from app.services.functions.errors import Conflict, FunctionsError
from app.services.functions.mailbox import gmail_client, mirror

logger = logging.getLogger(__name__)

CONNECTOR = gmail_client.CONNECTOR
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
CALLBACK_PATH = "/api/functions/inbox/mailboxes/oauth/callback"
#: An active mailbox that hasn't synced cleanly for this long pages someone.
LAG_ALERT = dt.timedelta(minutes=10)
SYNCING = ("active", "resyncing")


class MailboxSetupError(FunctionsError):
    """Connecting is impossible until something is configured."""

    status_code = 409
    code = "mailbox_setup"


class GoogleNotConfigured(MailboxSetupError):
    code = "google_not_configured"


class MailboxUnavailable(Conflict):
    """The mailbox can't send right now (not connected, or needs
    reconnecting) — sends fail closed."""

    code = "mailbox_unavailable"


def visible_accounts(db: Session, ctx: FunctionsCtx):
    return db.query(FnMailboxAccount).filter(
        FnMailboxAccount.organization_id == ctx.org_id,
        ctx.venue_clause(FnMailboxAccount.venue_id),
    )


def get_account(db: Session, ctx: FunctionsCtx, account_id: str) -> FnMailboxAccount:
    """A mailbox at one of the caller's venues — otherwise NotFound."""
    return load(db, ctx, FnMailboxAccount, account_id, label="Mailbox")


def is_connected(account) -> bool:
    return bool(account.connection_id) and account.status in SYNCING


def connected_account_for_venue(
    db: Session, ctx: FunctionsCtx, venue_id: str | None
) -> FnMailboxAccount | None:
    """The venue's connected mailbox, if any (the default sender)."""
    if not venue_id or venue_id not in ctx.venue_ids:
        return None
    return (
        visible_accounts(db, ctx)
        .filter(
            FnMailboxAccount.venue_id == venue_id,
            FnMailboxAccount.connection_id.isnot(None),
            FnMailboxAccount.status.in_(SYNCING),
        )
        .order_by(FnMailboxAccount.last_ok_at.desc().nulls_last())
        .first()
    )


def require_sendable(db: Session, ctx: FunctionsCtx, account: FnMailboxAccount) -> None:
    """Fail closed: live org, connected mailbox, grant not flagged dead."""
    require_live(ctx)
    if not is_connected(account):
        raise MailboxUnavailable(
            f"{account.google_email} needs reconnecting before Norm can send from it."
            if account.connection_id
            else f"{account.google_email} isn't connected to Norm yet."
        )
    dead = (
        db.query(Connection.needs_reconnect)
        .filter(Connection.id == account.connection_id)
        .scalar()
    )
    if dead is None or dead:
        raise MailboxUnavailable(
            f"{account.google_email} needs reconnecting before Norm can send from it."
        )


def health(account, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    connected = bool(account.connection_id)
    if account.status == "disabled":
        state = "disabled"
    elif not connected:
        state = "not_connected"
    elif account.status == "needs_reconnect":
        state = "needs_reconnect"
    elif account.status == "resyncing":
        state = "syncing"
    else:
        state = "connected"
    lag = (now - account.last_ok_at).total_seconds() if account.last_ok_at else None
    lagging = (
        connected
        and account.status in SYNCING
        and (lag is None or lag > LAG_ALERT.total_seconds())
    )
    return {
        "state": state,
        "connected": connected and account.status in SYNCING,
        "last_ok_at": account.last_ok_at.isoformat() if account.last_ok_at else None,
        "last_error": account.last_error,
        "consecutive_failures": account.consecutive_failures or 0,
        "lag_seconds": int(lag) if lag is not None else None,
        "lagging": bool(lagging),
    }


def mailbox_dict(account, *, venue_name: str | None, folders: dict, now=None) -> dict:
    return {
        "id": account.id,
        "venue_id": account.venue_id,
        "venue_name": venue_name,
        "google_email": account.google_email,
        "display_name": account.display_name,
        "status": account.status,
        "version": account.version,
        "health": health(account, now),
        "folders": {f: f in folders for f in mirror.FOLDER_LABEL_NAMES},
        "has_signature": bool(account.signature_html),
    }


def list_mailboxes(db: Session, ctx: FunctionsCtx, now=None) -> dict:
    """Every visible venue with its mailboxes — the Inbox setup screen and
    the Settings health tile."""
    venues = (
        db.query(Venue)
        .filter(
            Venue.organization_id == ctx.org_id, Venue.id.in_(sorted(ctx.venue_ids))
        )
        .order_by(Venue.name)
        .all()
    )
    accounts = visible_accounts(db, ctx).order_by(FnMailboxAccount.google_email).all()
    names = {v.id: v.name for v in venues}
    out_accounts = [
        mailbox_dict(
            a,
            venue_name=names.get(a.venue_id),
            folders=mirror.folder_labels(db, a.id),
            now=now,
        )
        for a in accounts
    ]
    return {
        "venues": [
            {
                "venue_id": v.id,
                "venue_name": v.name,
                "mailbox_ids": [a["id"] for a in out_accounts if a["venue_id"] == v.id],
            }
            for v in venues
        ],
        "mailboxes": out_accounts,
    }


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


def connector_spec(config_db: Session):
    from app.db.config_models import ConnectionSpec

    return (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .first()
    )


def _require_google_client(spec) -> None:
    if spec is None or spec.auth_type != "oauth2":
        raise MailboxSetupError(
            "The venue mailbox connector isn't set up yet "
            "(scripts/sync_venue_mailbox.py)."
        )
    oauth = spec.oauth_config or {}
    if not (oauth.get("client_id") and oauth.get("client_secret")):
        raise GoogleNotConfigured(
            "Google client not configured: Norm's Google OAuth client (id and "
            "secret) has to be added to the venue mailbox connector before a "
            "mailbox can be connected."
        )
    if GMAIL_SCOPE not in str(oauth.get("scopes") or "").split():
        raise MailboxSetupError(
            "The venue mailbox connector doesn't ask Google for Gmail access "
            "yet (re-run scripts/sync_venue_mailbox.py)."
        )


def callback_uri(request_base_url: str) -> str:
    """Where Google sends the person back. Derived from OAUTH_REDIRECT_URI
    when it is pinned (deployed environments — the request's own base URL
    is the internal host there), else from the request."""
    from app.config import settings

    configured = (settings.OAUTH_REDIRECT_URI or "").strip()
    if configured and "/api/" in configured:
        return configured.split("/api/", 1)[0] + CALLBACK_PATH
    return request_base_url.rstrip("/") + CALLBACK_PATH


def start_connect(
    db: Session,
    config_db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str,
    redirect_uri: str,
) -> str:
    """The Google consent URL for connecting ``venue_id``'s mailbox. Allowed
    in shadow mode — mailboxes are connected before going live. The person
    asking is recorded on the OAuth state: the callback checks they may
    still connect this venue, and names them in the audit log and in the
    notice the mailbox receives."""
    from app.services.oauth_service import build_authorize_url

    require_venue(ctx, venue_id)
    if not ctx.user_id:
        raise MailboxSetupError("Only a signed-in person can connect a mailbox.")
    spec = connector_spec(config_db)
    _require_google_client(spec)
    url = build_authorize_url(
        spec, redirect_uri, db, venue_id=venue_id, user_id=ctx.user_id
    )
    extra = {"include_granted_scopes": "true"}
    placeholder = (
        visible_accounts(db, ctx)
        .filter(FnMailboxAccount.venue_id == venue_id)
        .order_by(FnMailboxAccount.connection_id.is_(None))
        .first()
    )
    if placeholder is not None:
        extra["login_hint"] = placeholder.google_email
    return url + "&" + urlencode(extra)


@dataclass
class ConnectOutcome:
    ok: bool
    message: str
    account_id: str | None = None
    google_email: str | None = None
    venue_name: str | None = None


class _StaticTokens:
    """The token Google just issued, for the callback's own Gmail calls."""

    def __init__(self, token: str):
        self.token = token

    def access_token(self, *, force_refresh: bool = False) -> str:
        if force_refresh:
            raise gmail_client.MailboxAuthError("Google refused the new sign-in.")
        return self.token


def _signature(send_as: list[dict], email: str) -> tuple[str | None, str | None]:
    """``(signature html, display name)`` of the mailbox's own send-as entry."""
    from app.services.functions.mailbox.display import clean_outbound_html

    mine = next(
        (s for s in send_as if (s.get("sendAsEmail") or "").lower() == email),
        None,
    ) or next((s for s in send_as if s.get("isPrimary")), None)
    if mine is None:
        return None, None
    sig = clean_outbound_html(mine.get("signature") or "") or None
    return sig, (mine.get("displayName") or None)


def _may_connect(db: Session, user_id: str | None, org_id: str, venue_id: str) -> bool:
    """Whether the person who started the connect may still connect this
    venue: a member of its organisation who can see it."""
    from app.db.models import OrganizationMembership, Role, User, UserVenueAccess

    if not user_id:
        return False
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        return False
    if user.role == "admin":  # a platform admin, as on the generic connect
        return True
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == org_id,
        )
        .first()
    )
    if membership is None:
        return False
    role = (
        db.query(Role).filter(Role.id == membership.role_id).first()
        if membership.role_id
        else None
    )
    if membership.role in ("owner", "admin") or (role and role.name == "owner"):
        return True
    return (
        db.query(UserVenueAccess.id)
        .filter(
            UserVenueAccess.user_id == user_id, UserVenueAccess.venue_id == venue_id
        )
        .first()
        is not None
    )


def _redeem(spec, code: str, pending, redirect_uri: str, db: Session) -> dict:
    """Spend the state and exchange the code — storing NOTHING. Unlike
    ``oauth_service.exchange_code`` (which files the tokens at once), the
    grant is only stored after we know whose mailbox it is: a mailbox that
    belongs to another organisation must never have its tokens filed here,
    not even for a moment. Same state rules (single use, 15-minute life,
    PKCE verifier replayed)."""
    import httpx

    from app.services.oauth_service import OAUTH_STATE_TTL_MIN

    created = pending.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    expired = created is not None and _now() - created > dt.timedelta(
        minutes=OAUTH_STATE_TTL_MIN
    )
    verifier = pending.code_verifier
    db.delete(pending)
    db.commit()
    if expired:
        raise ValueError("This sign-in link has expired — start again from Norm.")
    oauth = spec.oauth_config or {}
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": oauth.get("client_id", ""),
    }
    if oauth.get("client_secret"):
        body["client_secret"] = oauth["client_secret"]
    if verifier:
        body["code_verifier"] = verifier
    resp = httpx.post(
        oauth.get("token_url", ""),
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        logger.warning("mailbox connect: token exchange refused (%s)", resp.status_code)
        raise ValueError(f"Google refused the sign-in ({resp.status_code}).")
    return resp.json()


@dataclass
class _Redeemed:
    venue: Venue
    org_id: str
    initiator_id: str
    token_data: dict


def _begin_connect(
    db: Session, config_db: Session, *, code: str, state: str, redirect_uri: str
) -> "_Redeemed | ConnectOutcome":
    """The start both connect flows share: the state must be a venue mailbox
    connect started by someone who may still connect that venue, and the
    code is redeemed WITHOUT storing anything (see ``_redeem``)."""
    from app.db.models import OAuthState

    pending = db.query(OAuthState).filter(OAuthState.state == state).first()
    if pending is None or pending.connector_name != CONNECTOR or not pending.venue_id:
        return ConnectOutcome(
            False, "This sign-in link has expired — start again from Norm."
        )
    venue = db.query(Venue).filter(Venue.id == pending.venue_id).first()
    if venue is None or not venue.organization_id:
        return ConnectOutcome(False, "That venue no longer exists.")
    org_id, initiator_id = venue.organization_id, pending.user_id
    if not _may_connect(db, initiator_id, org_id, venue.id):
        db.delete(pending)
        db.commit()
        logger.warning(
            "functions.mailbox.connect_refused reason=initiator org=%s venue=%s user=%s",
            org_id,
            venue.id,
            initiator_id,
        )
        return ConnectOutcome(
            False,
            "This sign-in link wasn't started from Norm by someone who can "
            "connect this venue — start again from Norm.",
            venue_name=venue.name,
        )
    spec = connector_spec(config_db)
    try:
        _require_google_client(spec)
    except MailboxSetupError as exc:
        return ConnectOutcome(False, exc.message, venue_name=venue.name)
    try:
        token_data = _redeem(spec, code, pending, redirect_uri, db)
    except ValueError as exc:
        return ConnectOutcome(False, str(exc), venue_name=venue.name)
    return _Redeemed(venue, org_id, initiator_id, token_data)


def _ownership_refusal(
    db: Session, r: _Redeemed, email: str, *, may_replace: bool
) -> str | None:
    """Why ``email`` may not become this venue's mailbox, or None.

    Never a mailbox another organisation has in Functions (placeholder or
    connected — the attack: org B's link completed by org A's mailbox
    owner), nor another venue's in this organisation. Unless ``may_replace``
    (the Functions connect, where changing the venue's mailbox is the point),
    not a different address from the Functions mailbox already running on
    this venue's connection either: Settings → Venues must not swap the grant
    under a live Functions mailbox."""
    venue, org_id = r.venue, r.org_id
    elsewhere = (
        db.query(FnMailboxAccount.id)
        .filter(
            FnMailboxAccount.organization_id != org_id,
            func.lower(FnMailboxAccount.google_email) == email,
        )
        .first()
    )
    reason = message = None
    if elsewhere is not None:
        reason = "other_org"
        message = (
            f"{email} is already connected to Norm Functions by another "
            "organisation, so it can't be connected here. Nothing was saved."
        )
    else:
        same_org = (
            db.query(FnMailboxAccount)
            .filter(
                FnMailboxAccount.organization_id == org_id,
                func.lower(FnMailboxAccount.google_email) == email,
            )
            .first()
        )
        if same_org is not None and same_org.venue_id != venue.id:
            other = db.query(Venue.name).filter(Venue.id == same_org.venue_id).scalar()
            reason = "other_venue"
            message = (
                f"{email} is {other or 'another venue'}'s mailbox in Norm Functions. "
                f"Connect it from {other or 'that venue'}, or sign in with "
                f"{venue.name}'s own mailbox. Nothing was saved."
            )
        elif not may_replace:
            running = (
                db.query(FnMailboxAccount.google_email)
                .join(Connection, Connection.id == FnMailboxAccount.connection_id)
                .filter(
                    FnMailboxAccount.organization_id == org_id,
                    Connection.connector_name == CONNECTOR,
                    Connection.venue_id == venue.id,
                    func.lower(FnMailboxAccount.google_email) != email,
                )
                .first()
            )
            if running is not None:
                reason = "replaces_functions_mailbox"
                message = (
                    f"{venue.name}'s mailbox in Norm Functions is {running[0]}. "
                    f"Sign in with {running[0]} here, or change the venue's "
                    "mailbox from the Functions Inbox. Nothing was saved."
                )
    if reason:
        logger.warning(
            "functions.mailbox.connect_refused reason=%s org=%s venue=%s user=%s "
            "google_email=%s",
            reason,
            org_id,
            venue.id,
            r.initiator_id,
            email,
        )
    return message


def _store(db: Session, r: _Redeemed, email: str, now: dt.datetime) -> Connection:
    """File the verified grant (encrypted at rest) on the venue's connection,
    with who connected it and when merged into its metadata."""
    from app.services.oauth_service import _store_tokens

    _store_tokens(
        db,
        CONNECTOR,
        {
            **r.token_data,
            "email": email,
            "connected_by_user_id": r.initiator_id,
            "connected_at": now.isoformat(),
        },
        venue_id=r.venue.id,
    )
    return (
        db.query(Connection)
        .filter(
            Connection.connector_name == CONNECTOR, Connection.venue_id == r.venue.id
        )
        .first()
    )


def _tell_the_mailbox(db: Session, r: _Redeemed, account, now: dt.datetime) -> None:
    from app.db.models import Organization, User
    from app.services.functions.mailbox.notify import send_connected_notice

    initiator = db.query(User).filter(User.id == r.initiator_id).first()
    org_name = db.query(Organization.name).filter(Organization.id == r.org_id).scalar()
    try:
        send_connected_notice(
            db,
            account=account,
            venue=r.venue,
            org_name=org_name or "",
            by=initiator,
            when=now,
        )
        db.commit()
    except Exception:  # noqa: BLE001 — the connect stands; the notice is a courtesy
        logger.exception("mailbox connected notice failed for %s", account.id)


def finish_connect(
    db: Session,
    config_db: Session,
    *,
    code: str,
    state: str,
    redirect_uri: str,
    transport=None,
) -> ConnectOutcome:
    """The Functions connect's redirect: redeem the code, find out which
    mailbox it is, and only then — if that mailbox may join THIS venue —
    store the grant and attach the account.

    Refused, with nothing stored: a state Norm's connect didn't make (or
    whose starter can no longer connect the venue), a grant without Gmail,
    or a mailbox ``_ownership_refusal`` rules out. On success the connect is
    written to the audit log, and the mailbox itself is told who connected
    it."""
    begun = _begin_connect(
        db, config_db, code=code, state=state, redirect_uri=redirect_uri
    )
    if isinstance(begun, ConnectOutcome):
        return begun
    r = begun
    venue_id, org_id, venue_name = r.venue.id, r.org_id, r.venue.name
    granted = str(r.token_data.get("scope") or "").split()
    if granted and GMAIL_SCOPE not in granted:
        return ConnectOutcome(
            False,
            "Google didn't grant Gmail access. Connect again and tick the box "
            "that lets Norm read, send and label the venue's email.",
            venue_name=venue_name,
        )
    client = gmail_client.GmailClient(
        _StaticTokens(r.token_data.get("access_token") or ""),
        writable=False,
        transport=transport,
    )
    try:
        profile = client.profile()
        email = str(profile.get("emailAddress") or "").strip().lower()
        signature, display_name = _signature(client.send_as(), email)
        labels = client.labels()
    except gmail_client.GmailError as exc:
        logger.info("mailbox connect: Gmail refused (%s): %s", venue_id, exc)
        return ConnectOutcome(
            False,
            "Google signed in, but Gmail refused Norm: " + exc.message,
            venue_name=venue_name,
        )
    finally:
        client.close()
    if not email:
        return ConnectOutcome(False, "Gmail didn't say which mailbox this is.")
    refusal = _ownership_refusal(db, r, email, may_replace=True)
    if refusal:
        return ConnectOutcome(False, refusal, venue_name=venue_name)
    existing = (
        db.query(FnMailboxAccount)
        .filter(
            FnMailboxAccount.organization_id == org_id,
            func.lower(FnMailboxAccount.google_email) == email,
        )
        .first()
    )

    now = _now()
    conn = _store(db, r, email, now)
    account = attach(
        db,
        org_id=org_id,
        venue_id=venue_id,
        connection_id=conn.id,
        google_email=email,
        display_name=display_name,
        signature_html=signature,
        labels=labels,
        existing=existing,
        now=now,
    )
    db.commit()
    logger.info(
        "functions.mailbox.connected org=%s venue=%s account=%s google_email=%s user=%s",
        org_id,
        venue_id,
        account.id,
        email,
        r.initiator_id,
    )
    _tell_the_mailbox(db, r, account, now)
    return ConnectOutcome(
        True,
        f"{email} is connected to {venue_name}. Norm is reading its mail now — "
        "the Inbox fills over the next few minutes.",
        account_id=account.id,
        google_email=email,
        venue_name=venue_name,
    )


USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"


def _google_email(access_token: str, transport=None) -> str | None:
    """The address a fresh grant belongs to (``userinfo.email`` scope — every
    venue mailbox grant has it; Gmail access may not be granted)."""
    import httpx

    try:
        with httpx.Client(
            transport=transport if transport is not None else gmail_client.TRANSPORT,
            timeout=gmail_client.TIMEOUT,
        ) as client:
            resp = client.get(
                USERINFO, headers={"Authorization": f"Bearer {access_token}"}
            )
    except httpx.HTTPError as exc:
        logger.info("mailbox connect: userinfo failed: %s", exc)
        return None
    if resp.status_code != 200:
        return None
    try:
        return str(resp.json().get("email") or "").strip().lower() or None
    except ValueError:
        return None


def finish_settings_connect(
    db: Session,
    config_db: Session,
    *,
    code: str,
    state: str,
    redirect_uri: str,
    transport=None,
) -> ConnectOutcome:
    """The ``venue_mailbox`` connect from Settings → Venues (``routers/oauth``
    sends its callback here): the same connection the Functions mailbox runs
    on, so the same rules — state and starter checked, code redeemed without
    storing, the address checked against every organisation's Functions
    mailboxes and against the one already running on this venue — before
    anything is filed. Gmail access isn't required (Norm Hiring's calendar
    invites only need calendar), so the address comes from Google's userinfo.
    A Functions mailbox already on this connection under the same address is
    simply healed (the sync picks the fresh grant up)."""
    begun = _begin_connect(
        db, config_db, code=code, state=state, redirect_uri=redirect_uri
    )
    if isinstance(begun, ConnectOutcome):
        return begun
    r = begun
    email = _google_email(r.token_data.get("access_token") or "", transport)
    if not email:
        return ConnectOutcome(
            False,
            "Google didn't say which account signed in, so nothing was saved — "
            "connect again.",
            venue_name=r.venue.name,
        )
    refusal = _ownership_refusal(db, r, email, may_replace=False)
    if refusal:
        return ConnectOutcome(False, refusal, venue_name=r.venue.name)
    now = _now()
    conn = _store(db, r, email, now)
    db.commit()
    logger.info(
        "venue_mailbox.connected org=%s venue=%s connection=%s google_email=%s user=%s",
        r.org_id,
        r.venue.id,
        conn.id,
        email,
        r.initiator_id,
    )
    running = (
        db.query(FnMailboxAccount)
        .filter(
            FnMailboxAccount.organization_id == r.org_id,
            FnMailboxAccount.connection_id == conn.id,
        )
        .first()
    )
    if running is not None:
        _tell_the_mailbox(db, r, running, now)
    return ConnectOutcome(
        True,
        f"{email} is connected to {r.venue.name}.",
        google_email=email,
        venue_name=r.venue.name,
    )


def attach(
    db: Session,
    *,
    org_id: str,
    venue_id: str,
    connection_id: str,
    google_email: str,
    display_name: str | None,
    signature_html: str | None,
    labels: list[dict],
    existing: FnMailboxAccount | None = None,
    now: dt.datetime | None = None,
) -> FnMailboxAccount:
    """Point a mailbox account at the venue's connection. A placeholder (or
    a reconnect) keeps its id, links and any cursor; a fresh account starts
    its initial sync. Another account still on this connection is detached
    — a venue has one Google grant, so it has one connected mailbox."""
    now = now or _now()
    db.execute(
        update(FnMailboxAccount.__table__)
        .where(
            FnMailboxAccount.organization_id == org_id,
            FnMailboxAccount.connection_id == connection_id,
            func.lower(FnMailboxAccount.google_email) != google_email,
        )
        .values(
            connection_id=None,
            status="needs_reconnect",
            last_error=f"Replaced by {google_email} on this venue's connection.",
            lease_until=None,
            lease_owner=None,
        )
    )
    account = existing
    if account is None:
        account = FnMailboxAccount(
            organization_id=org_id, venue_id=venue_id, google_email=google_email
        )
        db.add(account)
    account.connection_id = connection_id
    account.status = "active" if account.history_id else "resyncing"
    account.display_name = display_name or account.display_name
    account.signature_html = signature_html
    account.last_ok_at = now
    account.last_error = None
    account.consecutive_failures = 0
    account.reconnect_notified_at = None
    account.lease_until = None
    account.lease_owner = None
    db.flush()
    mirror.replace_labels(db, mirror.AccountRef.of(account), labels)
    db.flush()
    return account
