"""Add the ``venue_mailbox`` connector: a Google account connected to a VENUE,
for interview calendar invites and candidate packs (Oct 2026).

Norm Hiring sends interview invites from the job venue's own mailbox, as
Orbit does (``services/app_calendar.py``). Norm's email connections are per
user — ``routers/oauth.py`` stamps ``user_id`` on any connector whose category
is ``email`` — so this connector is deliberately category ``calendar``: the
ordinary OAuth flow, started with ``?venue_id=``, then stores the token on the
venue (``connector_configs.venue_id``, ``user_id`` NULL), and Settings → Venues
lists it with a Connect button for every venue without further UI work.

Scopes, least first:
  * ``calendar.events`` — create / update / delete the invite events;
  * ``drive.file`` — upload the candidate pack and share it with the guests.
    Only files Norm itself creates — never the rest of the mailbox's Drive;
  * ``userinfo.email`` — the mailbox's own address (via tokeninfo), so it is
    never invited to its own event and the result can say who sent the
    invite. Not ``openid``: Google would then return an ``id_token``, which
    ``oauth.py`` keeps in the connection's metadata and prints on its
    "Connected" page.

The Google OAuth client (id + secret) is found, in order, on: this spec
already (a re-run never clobbers it), the ``gmail`` spec, the config DB's
``system_secrets`` (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET), or this shell's
environment — never printed. As of Oct 2026 NONE of those holds one: a Google
Cloud OAuth client (type "Web application") has to be created first, with
``<env>/api/oauth/callback`` as its redirect URIs, the Calendar and Drive APIs
enabled, and these scopes on its consent screen (Internal, for the group's
Workspace, avoids Google's app verification). The door reports "Calendar API
is switched off" when the API isn't enabled.

No tools and no endpoints: nothing LLM-facing. The connection is used only by
the built-in apps' ``calendar`` door.

The config DB is shared across every environment, so committing this reaches
production immediately. Dry-run first; apply only with the owner's go-ahead.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_venue_mailbox.py --dry-run
    uv run python scripts/sync_venue_mailbox.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "venue_mailbox"
CLIENT_FROM = "gmail"

SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive.file",
    ]
)

SPEC = {
    "display_name": "Venue mailbox (Google)",
    # NOT "email": that category makes oauth.py store the token per USER.
    "category": "calendar",
    "execution_mode": "internal",
    "auth_type": "oauth2",
    "auth_config": {},
    "base_url_template": "https://www.googleapis.com",
    "tools": [],
    "endpoints": [],
    "credential_fields": [],
    "api_documentation": (
        "A Google account connected to a venue. Norm Hiring sends interview "
        "calendar invites from it (interviewers as guests) and attaches the "
        "candidate pack from its Drive. Connect one per venue in Settings → Venues."
    ),
    "enabled": True,
}

OAUTH = {
    "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "scopes": SCOPES,
}


def _google_client(db, existing) -> tuple[dict, str | None]:
    """``({client_id, client_secret}, where it came from)`` — or ``({}, None)``."""
    import os

    from app.db.config_models import ConnectionSpec
    from app.db.models import SystemSecret

    def pair(source: dict) -> dict:
        got = {k: source.get(k) for k in ("client_id", "client_secret")}
        return got if all(got.values()) else {}

    if existing is not None and pair(existing.oauth_config or {}):
        return pair(existing.oauth_config or {}), f"the existing '{CONNECTOR}' spec"
    gmail = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CLIENT_FROM)
        .first()
    )
    if gmail is not None and pair(gmail.oauth_config or {}):
        return pair(gmail.oauth_config or {}), f"the '{CLIENT_FROM}' spec"
    secrets = {
        s.key: s.value
        for s in db.query(SystemSecret).filter(
            SystemSecret.key.in_(["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"])
        )
    }
    found = pair(
        {
            "client_id": secrets.get("GOOGLE_CLIENT_ID"),
            "client_secret": secrets.get("GOOGLE_CLIENT_SECRET"),
        }
    )
    if found:
        return found, "system_secrets"
    found = pair(
        {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
        }
    )
    return (found, "this shell's environment") if found else ({}, None)


def plan(db) -> tuple[object | None, dict, list[str]]:
    """What would be written: ``(existing spec, oauth_config, notes)``."""
    from app.db.config_models import ConnectionSpec

    notes: list[str] = []
    existing = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .first()
    )
    client, where = _google_client(db, existing)
    # Merge over what's stored, never replace it: a re-run must not drop keys
    # an admin set by hand (or the client pair once it's stored here).
    oauth = {
        **((existing.oauth_config or {}) if existing is not None else {}),
        **OAUTH,
        **client,
    }
    if not client:
        notes.append(
            "WARNING: no Google OAuth client found (this spec, the gmail spec, "
            "system_secrets, environment) — the spec can be written, but "
            "Connect will fail until client_id/client_secret are set"
        )
    else:
        notes.append(f"Google client: from {where} (values not shown)")
    if existing is not None:
        current = existing.oauth_config or {}
        changed = sorted(
            k for k in set(current) | set(oauth) if current.get(k) != oauth.get(k)
        )
        notes.append(
            f"updating '{CONNECTOR}': oauth keys changing = "
            f"{[k if 'secret' not in k else k + ' (hidden)' for k in changed] or 'none'}"
        )
    else:
        notes.append(f"creating '{CONNECTOR}' (category=calendar, auth_type=oauth2)")
    notes.append(f"scopes: {SCOPES}")
    return existing, oauth, notes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        existing, oauth, notes = plan(db)
        for line in notes:
            print(line)
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        if existing is None:
            db.add(ConnectionSpec(connector_name=CONNECTOR, oauth_config=oauth, **SPEC))
        else:
            for key, value in SPEC.items():
                if key == "enabled":
                    continue  # a re-run never switches a disabled spec back on
                setattr(existing, key, value)
            existing.oauth_config = oauth
            flag_modified(existing, "oauth_config")
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
