"""Xero's OAuth hooks: bind the organisation a person just authorised to a venue.

Why Xero needs a hook where most connectors don't (LoadedHub's and Gmail's
live in ``routers/oauth.py``): Xero's token response names no organisation.
Which tenant the person picked on the consent screen takes two more calls —
the access token's ``authentication_event_id`` claim, then
``GET https://api.xero.com/connections?authEventId=…`` — and the
organisation's country, currency and short code a third, ``GET /Organisation``.
Every Xero API call then carries ``xero-tenant-id`` rendered from
``creds.xero_tenant_id``; writing that is this module's job.

One organisation per venue per consent. Each venue's Connect is its own
consent and its own refresh-token chain, and nothing here touches another
venue's row. (Orbit's integration copied one refresh token onto every tenant
it saw — Xero refresh tokens are single-use, so sibling rows died on the first
refresh.) A venue already bound to one organisation refuses a consent for a
different one, exactly as LoadedHub's wrong-company check does: the tokens are
wiped, the row is flagged, and the page says which two organisations clashed.
"""

from __future__ import annotations

import base64
import html
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Connection

logger = logging.getLogger(__name__)

CONNECTOR = "xero"
CONNECTIONS_URL = "https://api.xero.com/connections"
ORGANISATION_URL = "https://api.xero.com/api.xro/2.0/Organisation"
TIMEOUT = 30.0

# Keys bind_tenant writes into Connection.config; disconnect() drops them all.
CONFIG_KEYS = (
    "xero_tenant_id",
    "xero_connection_id",
    "xero_country",
    "xero_currency",
    "xero_short_code",
    "xero_timezone",
)


class XeroConnectError(Exception):
    """The authorisation cannot be bound to this venue. The message is for the person."""


def authentication_event_id(access_token: str | None) -> str | None:
    """The consent this access token came from: Xero's ``authentication_event_id``.

    Xero access tokens are JWTs. The payload is read without verifying the
    signature — the token came straight from Xero's token endpoint over TLS
    and is used only to pick which of the person's organisations was just
    authorised. The id_token does NOT carry this claim (checked 17 Oct 2026);
    the access token does, and it equals ``authEventId`` on ``/connections``.
    """
    try:
        payload = str(access_token or "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    value = claims.get("authentication_event_id") if isinstance(claims, dict) else None
    return str(value) if value else None


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def list_connections(access_token: str, auth_event_id: str | None = None) -> list[dict]:
    """The organisations this token can reach, optionally only one consent's."""
    params = {"authEventId": auth_event_id} if auth_event_id else None
    resp = httpx.get(
        CONNECTIONS_URL, headers=_headers(access_token), params=params, timeout=TIMEOUT
    )
    if resp.status_code != 200:
        raise XeroConnectError(
            f"Xero would not list the authorised organisations ({resp.status_code})"
        )
    data = resp.json()
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict)]


def get_organisation(access_token: str, tenant_id: str) -> dict:
    """Country, base currency, short code, timezone. Best effort: {} on failure."""
    try:
        resp = httpx.get(
            ORGANISATION_URL,
            headers={**_headers(access_token), "xero-tenant-id": tenant_id},
            timeout=TIMEOUT,
        )
    except Exception as exc:
        logger.warning("Xero organisation lookup failed for %s: %s", tenant_id, exc)
        return {}
    if resp.status_code != 200:
        logger.warning(
            "Xero organisation lookup returned %s for %s", resp.status_code, tenant_id
        )
        return {}
    orgs = (resp.json() or {}).get("Organisations") or []
    return orgs[0] if orgs and isinstance(orgs[0], dict) else {}


def revoke_connection(access_token: str, connection_id: str) -> bool:
    """``DELETE /connections/{id}`` — frees the tenant slot (an uncertified app
    gets 25). A 404 means it is already gone, which is the outcome we wanted."""
    try:
        resp = httpx.delete(
            f"{CONNECTIONS_URL}/{connection_id}",
            headers=_headers(access_token),
            timeout=TIMEOUT,
        )
    except Exception as exc:
        logger.warning("Xero revoke failed for connection %s: %s", connection_id, exc)
        return False
    if resp.status_code in (204, 404):
        return True
    logger.warning(
        "Xero revoke returned %s for connection %s: %s",
        resp.status_code,
        connection_id,
        resp.text[:200],
    )
    return False


def _wipe(row: Connection, error: str) -> None:
    """Drop the just-stored tokens and flag the row; the binding keys stay."""
    row.access_token = None
    row.refresh_token = None
    row.token_expires_at = None
    row.oauth_metadata = None
    row.needs_reconnect = True
    row.last_auth_error = error[:1000]
    row.last_auth_checked_at = datetime.now(timezone.utc)


def _newest_first(conns: list[dict]) -> list[dict]:
    return sorted(
        conns,
        key=lambda c: str(c.get("updatedDateUtc") or c.get("createdDateUtc") or ""),
        reverse=True,
    )


def bind_tenant(db: Session, venue_id: str | None, token_data: dict) -> dict:
    """Bind the organisation from THIS consent to THIS venue's row.

    ``exchange_code`` has already stored the tokens on the row. Returns the
    binding (``tenant_id``, ``tenant_name``, ``country``, ``currency``). Raises
    ``XeroConnectError`` — after wiping those tokens and flagging the row — when
    the consent cannot be bound: no venue, no organisation, more than one, or
    an organisation other than the one this venue is already bound to.
    """
    access_token = str(token_data.get("access_token") or "")
    query = db.query(Connection).filter(Connection.connector_name == CONNECTOR)
    if venue_id:
        query = query.filter(Connection.venue_id == venue_id)
    else:
        query = query.filter(Connection.venue_id.is_(None))
    row = query.first()

    if not venue_id:
        message = "Xero is connected per venue — start from a venue's Connect button."
        if row:
            _wipe(row, message)
            db.commit()
        raise XeroConnectError(message)
    if not row:
        raise XeroConnectError("No Xero connection row for this venue")

    def fail(message: str) -> None:
        _wipe(row, message)
        db.commit()
        raise XeroConnectError(message)

    event = authentication_event_id(access_token)
    try:
        conns = list_connections(access_token, event)
        if event and not conns:
            # Older tenant records can lack the event id; fall back to the
            # whole list and take the most recently (re)authorised one.
            conns = _newest_first(list_connections(access_token))[:1]
    except XeroConnectError as exc:
        fail(str(exc))
    if not conns:
        fail(
            "Xero returned no organisation for this authorisation — choose an "
            "organisation on Xero's consent screen and try again."
        )
    if len(conns) > 1 and not event:
        conns = _newest_first(conns)[:1]
    if len(conns) > 1:
        names = ", ".join(
            html.escape(str(c.get("tenantName") or c.get("tenantId"))) for c in conns
        )
        fail(
            f"Authorise one organisation per venue — this authorisation covered "
            f"{len(conns)}: {names}"
        )

    tenant = conns[0]
    tenant_id = str(tenant.get("tenantId") or "").strip()
    tenant_name = str(tenant.get("tenantName") or tenant_id)
    if not tenant_id:
        fail("Xero returned an organisation without an id")

    config = dict(row.config or {})
    stored = str(config.get("xero_tenant_id") or "").strip()
    if stored and stored != tenant_id:
        stored_name = html.escape(
            str((row.oauth_metadata or {}).get("tenant_name") or stored)
        )
        fail(
            f"This venue is connected to {stored_name} in Xero, but the "
            f"authorisation you just gave is for {html.escape(tenant_name)}. "
            f"Choose {stored_name} on Xero's screen, or disconnect this venue first."
        )

    org = get_organisation(access_token, tenant_id)
    config.update(
        {
            "xero_tenant_id": tenant_id,
            "xero_connection_id": str(tenant.get("id") or ""),
            "xero_country": str(
                org.get("CountryCode") or config.get("xero_country") or ""
            ),
            "xero_currency": str(
                org.get("BaseCurrency") or config.get("xero_currency") or ""
            ),
            "xero_short_code": str(
                org.get("ShortCode") or config.get("xero_short_code") or ""
            ),
            "xero_timezone": str(
                org.get("Timezone") or config.get("xero_timezone") or ""
            ),
        }
    )
    row.config = config
    flag_modified(row, "config")

    meta = dict(row.oauth_metadata or {})
    meta.pop("id_token", None)  # a JWT nobody reads; never store it
    meta.update(
        {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "tenant_type": str(tenant.get("tenantType") or ""),
            "auth_event_id": event or str(tenant.get("authEventId") or ""),
        }
    )
    row.oauth_metadata = meta
    flag_modified(row, "oauth_metadata")
    row.needs_reconnect = False
    row.last_auth_error = None
    db.commit()
    return {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "country": config["xero_country"],
        "currency": config["xero_currency"],
    }


def connected_message(bound: dict) -> str:
    """'Connected to Cook Brothers Bars Federal Street Limited (NZ, NZD)'."""
    where = ", ".join(
        str(x) for x in (bound.get("country"), bound.get("currency")) if x
    )
    name = html.escape(str(bound.get("tenant_name") or "Xero"))
    return f"Connected to {name}" + (f" ({where})" if where else "")


def disconnect(row: Connection, access_token: str | None = None) -> bool:
    """Revoke at Xero and drop the binding, so a reconnect may pick another
    organisation. Returns whether Xero confirmed the revoke; the caller clears
    the tokens either way."""
    revoked = False
    connection_id = str((row.config or {}).get("xero_connection_id") or "")
    token = access_token or row.access_token
    if token and connection_id:
        revoked = revoke_connection(token, connection_id)
    row.config = {k: v for k, v in (row.config or {}).items() if k not in CONFIG_KEYS}
    flag_modified(row, "config")
    return revoked
