"""Calendar invites from a venue's mailbox — the sandbox's ``calendar`` object
for Norm's built-in apps (Oct 2026).

Orbit sends every interview invite from the job's VENUE mailbox
(``venue_gmail_accounts``): the venue owns the calendar, interviewers are the
guests, and the event carries a candidate pack. Norm had no venue mailbox —
its email connections are per user (``oauth.py`` stamps ``user_id`` on any
connector in category ``email``). So:

* **A venue-scoped Google connection.** Connector ``venue_mailbox`` (spec from
  ``scripts/sync_venue_mailbox.py``, category ``calendar``) goes through the
  ordinary OAuth flow with ``?venue_id=``; because its category isn't
  ``email`` the token is stored on the VENUE (``connector_configs.venue_id``,
  ``user_id`` NULL). Scopes: Calendar events, Drive (files Norm creates —
  ``drive.file``, never the whole Drive), and the account's address.
* **Tenancy.** Every venue, connection and file this door touches belongs to
  the app's own org — checked here, never taken from the app. A connection is
  found by venue, and a stored account id is honoured only when it is one of
  this org's venue mailboxes.
* **Guests are the org's people.** An attendee is ``person:<id>`` (resolved
  through the app's own ``people``) or an address belonging to the org — the
  same rule as ``app_email``. Candidates are never guests (they get an email).
* **It never raises into the app.** No mailbox, a missing scope, the Calendar
  API switched off, an expired grant: each comes back as ``{ok: False,
  message}`` in words a hiring manager can act on, and the interview the app
  just saved stands.
* **The pack is best-effort.** Files named in ``attach_file_ids`` are uploaded
  to the mailbox's Drive and shared with the guests only (Orbit shares packs
  with anyone holding the link — CVs included). If Drive fails, the invite
  still goes and the result says why there's no attachment.

Google is reached with ``httpx`` through :func:`_client`, which the tests
replace with a mock transport.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CONNECTOR = "venue_mailbox"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
DRIVE_UPLOAD = (
    "https://www.googleapis.com/upload/drive/v3/files"
    "?uploadType=multipart&fields=id,webViewLink"
)
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
DEFAULT_TZ = "Pacific/Auckland"
MAX_GUESTS = 50

CONNECT_HINT = (
    "Connect the venue mailbox in Settings → Venues to send calendar invites."
)


def _client() -> httpx.Client:
    return httpx.Client(timeout=20.0)


def _refresh_token(config_db: Session | None, venue_id: str) -> str | None:
    """A fresh access token for a venue's mailbox, or None.

    Done in its OWN app-database session: ``refresh_access_token`` commits, and
    committing the app run's session would land the run's half-done writes."""
    from app.db.config_models import ConnectionSpec
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.services.oauth_service import refresh_access_token

    owns_config = config_db is None
    cfg = config_db or _ConfigSessionLocal()
    session = SessionLocal()
    try:
        spec = (
            cfg.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec or not spec.oauth_config:
            return None
        return refresh_access_token(spec, session, venue_id=venue_id)
    except Exception as exc:  # noqa: BLE001 — reported as "reconnect", not raised
        logger.info("venue mailbox refresh failed (venue %s): %s", venue_id, exc)
        return None
    finally:
        session.close()
        if owns_config:
            cfg.close()


def _parse_time(value, tz_name: str) -> datetime | None:
    """An ISO timestamp as an aware datetime. A naive one is read as the
    venue's wall-clock time."""
    from zoneinfo import ZoneInfo

    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        except Exception:  # noqa: BLE001
            dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _google_error(status: int, text: str) -> str:
    """Google's refusal, in words."""
    if status == 403 and (
        "SCOPE_INSUFFICIENT" in text or "insufficient authentication scopes" in text
    ):
        return "scope"
    if status == 403 and ("SERVICE_DISABLED" in text or "accessNotConfigured" in text):
        return "disabled"
    if status == 401:
        return "auth"
    return "other"


class VenueCalendar:
    """The door's implementation. Bound to one app, org and viewer by
    :func:`calendar_door`; the sandbox only ever sees closures over it."""

    def __init__(
        self, db: Session, config_db: Session | None, *, app, version, user, mode="live"
    ):
        self.db = db
        self.config_db = config_db
        self.app = app
        self.version = version
        self.user = user
        # dry_run / test runs (the app's preview and "send me a test") must
        # not touch real calendars either.
        self.preview = mode in ("dry_run", "test")

    def _tag(self) -> dict:
        """Marks an event as made by this app for this org — checked before
        Norm ever edits or deletes one, so a stored id can't point Norm at an
        event it didn't create (Orbit's, or anyone's)."""
        return {"norm_app": self.app.slug, "norm_org": str(self.app.organization_id)}

    def _ours(self, client, base, event_id, auth) -> str:
        """'ours' | 'gone' | 'foreign' for an existing event id."""
        res = client.get(f"{base}/{quote(str(event_id), safe='')}", headers=auth)
        if res.status_code in (404, 410):
            return "gone"
        if res.status_code != 200:
            return "foreign"
        private = ((res.json() or {}).get("extendedProperties") or {}).get(
            "private"
        ) or {}
        tag = self._tag()
        return "ours" if all(private.get(k) == v for k, v in tag.items()) else "foreign"

    # -- venues and connections --------------------------------------------------
    def _venue(self, venue_id):
        from app.db.models import Venue

        if not venue_id:
            return None
        venue = self.db.query(Venue).filter(Venue.id == str(venue_id)).first()
        if venue is None or venue.organization_id != self.app.organization_id:
            return None
        return venue

    def _connection(self, venue, account_id=None):
        """The mailbox for an event: the account that owns an existing event
        (if it is still one of this org's venue mailboxes), else the venue's."""
        from app.db.models import Connection, Venue

        base = (
            self.db.query(Connection)
            .join(Venue, Venue.id == Connection.venue_id)
            .filter(
                Connection.connector_name == CONNECTOR,
                Connection.user_id.is_(None),
                Connection.enabled == "true",
                Venue.organization_id == self.app.organization_id,
            )
        )
        if venue is None:
            return None
        if account_id:
            # Only a mailbox of THIS venue — a stored account id can't move
            # the event onto another venue's mailbox.
            owned = base.filter(
                Connection.id == str(account_id), Connection.venue_id == venue.id
            ).first()
            if owned is not None and owned.access_token:
                return owned
        row = base.filter(Connection.venue_id == venue.id).first()
        return row if row is not None and row.access_token else None

    def _token(self, conn) -> str | None:
        exp = conn.token_expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if conn.access_token and (
            exp is None or datetime.now(timezone.utc) < exp - timedelta(seconds=60)
        ):
            return conn.access_token
        token = _refresh_token(self.config_db, conn.venue_id)
        if token:
            try:
                self.db.refresh(conn)
            except Exception:  # noqa: BLE001 — the token we got back is enough
                pass
        return token

    def _fingerprint(self, conn) -> str:
        return hashlib.sha256((conn.refresh_token or "").encode()).hexdigest()[:12]

    def _remember(self, conn, **values) -> None:
        """Cache what we learned about a mailbox (its address) on the
        connection, keyed to the grant so a reconnect starts fresh."""
        from sqlalchemy.orm.attributes import set_committed_value

        from app.db.models import Connection
        from app.services.app_email import _independent_session

        meta = dict(conn.oauth_metadata or {})
        meta.update({k: v for k, v in values.items() if v})
        meta["mailbox_grant"] = self._fingerprint(conn)
        # Written in its own short session, never in the run's transaction:
        # holding this row's lock for the rest of the run would block (and
        # with a token refresh's SELECT … FOR UPDATE, deadlock) the refresh.
        # The in-memory copy is updated without marking it dirty.
        session = _independent_session(self.db)
        try:
            row = session.query(Connection).filter(Connection.id == conn.id).first()
            if row is not None:
                row.oauth_metadata = meta
                session.commit()
            set_committed_value(conn, "oauth_metadata", meta)
        except Exception as exc:  # noqa: BLE001 — a cache, not a write that matters
            session.rollback()
            logger.info("venue mailbox metadata not saved: %s", exc)
        finally:
            session.close()

    def _mailbox_email(self, conn, token: str | None) -> str | None:
        meta = conn.oauth_metadata or {}
        if meta.get("email") and meta.get("mailbox_grant") == self._fingerprint(conn):
            return meta["email"]
        if not token:
            return meta.get("email")
        try:
            with _client() as client:
                r = client.get(TOKENINFO, params={"access_token": token})
            if r.status_code == 200:
                info = r.json()
                self._remember(conn, email=info.get("email"), scopes=info.get("scope"))
                return info.get("email") or meta.get("email")
        except Exception as exc:  # noqa: BLE001
            logger.info("venue mailbox tokeninfo failed: %s", exc)
        return meta.get("email")

    def _allowed(self, venue) -> str | None:
        """None when this viewer may send from the venue, else why not."""
        from fastapi import HTTPException

        from app.services.app_runtime import _check_venue

        try:
            _check_venue(self.db, self.user, venue.id)
        except HTTPException:
            return (
                f"You don't have access to {venue.name}, so you can't send its invites."
            )
        return None

    # -- what the app can ask ------------------------------------------------------
    def venues(self) -> list[dict]:
        """The org's venues and whether each has a mailbox — no Google calls."""
        from app.db.models import Connection, Venue

        rows = (
            self.db.query(Venue)
            .filter(Venue.organization_id == self.app.organization_id)
            .order_by(Venue.name)
            .all()
        )
        conns = {
            c.venue_id: c
            for c in self.db.query(Connection).filter(
                Connection.connector_name == CONNECTOR,
                Connection.user_id.is_(None),
                Connection.venue_id.in_([v.id for v in rows] or [""]),
            )
        }
        out = []
        for v in rows:
            c = conns.get(v.id)
            connected = bool(c and c.access_token and c.enabled == "true")
            out.append(
                {
                    "id": v.id,
                    "name": v.name,
                    "timezone": v.timezone or DEFAULT_TZ,
                    "mailbox": {
                        "connected": connected,
                        "email": (c.oauth_metadata or {}).get("email") if c else None,
                        "needs_reconnect": bool(c and c.needs_reconnect),
                    },
                }
            )
        return out

    def status(self, venue_id) -> dict:
        venue = self._venue(venue_id)
        if venue is None:
            return {
                "connected": False,
                "message": "This job has no venue, so there's no venue mailbox to send invites from.",
            }
        conn = self._connection(venue)
        if conn is None:
            return {
                "connected": False,
                "venue": venue.name,
                "message": f"{venue.name} has no connected mailbox. {CONNECT_HINT}",
            }
        email = self._mailbox_email(conn, conn.access_token)
        return {
            "connected": True,
            "venue": venue.name,
            "email": email,
            "account_id": conn.id,
            "needs_reconnect": bool(conn.needs_reconnect),
        }

    def local_time(self, value, venue_id=None) -> str:
        """``value`` in the venue's own time, the way an invite says it:
        "Tuesday 14 October 2026, 2:30pm"."""
        from zoneinfo import ZoneInfo

        venue = self._venue(venue_id) if venue_id else None
        tz_name = (venue.timezone if venue else None) or DEFAULT_TZ
        dt = _parse_time(value, tz_name)
        if dt is None:
            return str(value or "")
        try:
            dt = dt.astimezone(ZoneInfo(tz_name))
        except Exception:  # noqa: BLE001
            pass
        hour = dt.hour % 12 or 12
        return (
            f"{dt:%A} {dt.day} {dt:%B} {dt.year}, "
            f"{hour}:{dt:%M}{'am' if dt.hour < 12 else 'pm'}"
        )

    def _guests(self, items, mailbox: str | None) -> tuple[list[dict], list, list]:
        """``(attendees, skipped_names, refused)``."""
        from fastapi import HTTPException

        from app.services.app_email import _EMAIL
        from app.services.app_runtime import store_get

        seen = {str(mailbox or "").lower()} - {""}
        attendees: list[dict] = []
        skipped: list[str] = []
        refused: list[str] = []
        for item in list(items or [])[: MAX_GUESTS * 2]:
            name = None
            if isinstance(item, dict):
                raw, name = str(item.get("email") or "").strip(), item.get("name")
            else:
                raw = str(item or "").strip()
            if raw.startswith("person:"):
                try:
                    person = store_get(
                        self.db,
                        app=self.app,
                        version=self.version,
                        user=self.user,
                        collection="people",
                        record_id=raw.split(":", 1)[1],
                    )
                except HTTPException:
                    refused.append(raw)
                    continue
                name = person.get("name") or name
                address = str(person.get("email") or "").strip()
                if not address:
                    skipped.append(name or "Unnamed")
                    continue
            else:
                address = raw
                if not (_EMAIL.match(address) and self._belongs_to_org(address)):
                    if address:
                        refused.append(address)
                    continue
            if not _EMAIL.match(address):
                skipped.append(name or address)
                continue
            key = address.lower()
            if key in seen:
                continue
            seen.add(key)
            guest = {"email": key}
            if name:
                guest["displayName"] = str(name)
            attendees.append(guest)
        return attendees[:MAX_GUESTS], skipped, refused

    def _belongs_to_org(self, address: str) -> bool:
        from sqlalchemy import func

        from app.db.models import AppRecord, OrganizationMembership, User

        org = self.app.organization_id
        if (
            self.db.query(User.id)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .filter(
                OrganizationMembership.organization_id == org,
                func.lower(User.email) == address.lower(),
            )
            .first()
        ):
            return True
        namespace = str(
            ((self.version.spec or {}).get("storage") or {}).get("namespace") or ""
        )
        return bool(namespace) and (
            self.db.query(AppRecord.id)
            .filter(
                AppRecord.namespace == namespace,
                AppRecord.organization_id == org,
                AppRecord.collection == "people",
                func.lower(AppRecord.data["email"].astext) == address.lower(),
            )
            .first()
            is not None
        )

    def _drive_attach(
        self, client, token, file_ids, guests, title
    ) -> tuple[list, list, str | None]:
        """Upload each file to the mailbox's Drive, share it with the guests,
        and return ``(attachments, drive_file_ids, note)``."""
        from fastapi import HTTPException

        from app.services.app_runtime import file_fetch

        attachments: list[dict] = []
        drive_ids: list[str] = []
        note = None
        for fid in list(file_ids or [])[:5]:
            try:
                row = file_fetch(
                    self.db,
                    app=self.app,
                    version=self.version,
                    user=self.user,
                    file_id=str(fid),
                )
            except HTTPException:
                note = "the candidate pack couldn't be read, so it isn't attached"
                continue
            boundary = "norm_pack_boundary"
            meta = json.dumps({"name": row.filename, "mimeType": row.content_type})
            body = (
                (
                    f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                    f"--{boundary}\r\nContent-Type: {row.content_type}\r\n\r\n"
                ).encode()
                + bytes(row.data or b"")
                + f"\r\n--{boundary}--".encode()
            )
            up = client.post(
                DRIVE_UPLOAD,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                content=body,
            )
            if up.status_code not in (200, 201):
                kind = _google_error(up.status_code, up.text)
                note = (
                    "the mailbox hasn't allowed Google Drive, so the candidate pack isn't attached"
                    if kind == "scope"
                    else "the Google Drive API is switched off, so the candidate pack isn't attached"
                    if kind == "disabled"
                    else f"Google Drive refused the candidate pack ({up.status_code}), so it isn't attached"
                )
                logger.info("drive upload failed %s: %s", up.status_code, up.text[:300])
                continue
            info = up.json()
            file_id = info.get("id")
            drive_ids.append(file_id)
            # Shared with the guests only — never "anyone with the link".
            for guest in guests:
                perm_url = f"{DRIVE_FILES}/{quote(file_id)}/permissions"
                payload = {
                    "role": "reader",
                    "type": "user",
                    "emailAddress": guest["email"],
                }
                r = client.post(
                    perm_url,
                    params={"sendNotificationEmail": "false"},
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
                if r.status_code >= 400:
                    # Not a Google account: Drive insists on telling them.
                    client.post(
                        perm_url,
                        params={"sendNotificationEmail": "true"},
                        headers={"Authorization": f"Bearer {token}"},
                        json=payload,
                    )
            attachments.append(
                {
                    "fileUrl": info.get("webViewLink")
                    or f"https://drive.google.com/file/d/{file_id}/view",
                    "title": title or row.filename,
                    "mimeType": row.content_type,
                }
            )
        return attachments, drive_ids, note

    def _drive_delete(self, client, token, ids) -> None:
        for fid in list(ids or [])[:10]:
            try:
                client.delete(
                    f"{DRIVE_FILES}/{quote(str(fid))}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            except Exception:  # noqa: BLE001 — leftover files are clutter, not harm
                pass

    def _refusal(self, kind, conn_email, venue_name, status=None) -> dict:
        who = conn_email or f"{venue_name}'s mailbox"
        if kind == "scope":
            msg = f"{who} hasn't allowed calendar access. Reconnect the venue mailbox in Settings → Venues to send calendar invites."
        elif kind == "disabled":
            msg = "The Google Calendar API is switched off for Norm's Google project, so no invite was sent."
        elif kind == "auth":
            msg = f"{who} needs reconnecting (Settings → Venues) before invites can be sent."
        else:
            msg = f"Google Calendar refused the invite ({status}). Try saving again in a minute."
        return {"ok": False, "calendar_synced": False, "message": msg}

    def upsert(
        self,
        venue_id,
        event,
        event_id=None,
        account_id=None,
        calendar_id=None,
        attach_file_ids=None,
        attach_title=None,
        replace_drive_file_ids=None,
    ) -> dict:
        """Create or update one event on the venue mailbox's calendar.

        ``event``: ``{summary, description (HTML), location, start (ISO),
        end (ISO) | duration_minutes, attendees: ["person:<id>" | address],
        hide_guests}``. Updates go to the account that owns the event; an
        event that has gone (or lives on a calendar this mailbox can't see)
        is created afresh."""
        if not isinstance(event, dict):
            return {"ok": False, "message": "no event details were given"}
        if self.preview:
            return {
                "ok": True,
                "calendar_synced": False,
                "message": "Preview only — no calendar invite was sent.",
            }
        venue = self._venue(venue_id)
        if venue is None:
            return {
                "ok": False,
                "calendar_synced": False,
                "message": "This job has no venue, so there's no venue mailbox to send the invite from.",
            }
        why = self._allowed(venue)
        if why:
            return {"ok": False, "calendar_synced": False, "message": why}
        conn = self._connection(venue, account_id if event_id else None)
        venue_name = venue.name if venue else "this venue"
        if conn is None:
            return {
                "ok": False,
                "calendar_synced": False,
                "message": f"{venue_name} has no connected mailbox. {CONNECT_HINT}",
            }
        token = self._token(conn)
        if not token:
            return {
                "ok": False,
                "calendar_synced": False,
                "message": f"{venue_name}'s mailbox needs reconnecting (Settings → Venues) before invites can be sent.",
            }
        mailbox = self._mailbox_email(conn, token)
        tz_name = (venue.timezone if venue else None) or DEFAULT_TZ
        start = _parse_time(event.get("start"), tz_name)
        if start is None:
            return {
                "ok": False,
                "message": "the interview has no time yet, so no invite was sent",
            }
        end = _parse_time(event.get("end"), tz_name)
        if end is None or end <= start:
            try:
                minutes = int(float(event.get("duration_minutes") or 30))
            except (TypeError, ValueError):
                minutes = 30
            end = start + timedelta(minutes=max(5, min(minutes, 24 * 60)))
        guests, skipped, refused = self._guests(event.get("attendees"), mailbox)

        body = {
            "summary": str(event.get("summary") or "Interview")[:250],
            "description": str(event.get("description") or "")[:8000],
            "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
            "attendees": guests,
            "guestsCanModify": False,
            "guestsCanSeeOtherGuests": not bool(event.get("hide_guests")),
            "reminders": {"useDefault": True},
            "extendedProperties": {"private": self._tag()},
        }
        if event.get("location"):
            body["location"] = str(event["location"])[:500]

        cal = str(calendar_id or "primary")
        base = f"{CALENDAR_API}/calendars/{quote(cal, safe='')}/events"
        auth = {"Authorization": f"Bearer {token}"}
        try:
            with _client() as client:
                attachments, drive_ids, note = (
                    self._drive_attach(
                        client, token, attach_file_ids, guests, attach_title
                    )
                    if attach_file_ids
                    else ([], [], None)
                )
                if attachments:
                    body["attachments"] = attachments
                params = {"sendUpdates": "all", "supportsAttachments": "true"}
                res = None
                left_alone = False
                if event_id and self._ours(client, base, event_id, auth) == "foreign":
                    # Not an event Norm made (e.g. Orbit's): never edit it.
                    # A fresh invite goes out from the venue mailbox instead.
                    event_id, left_alone = None, True
                if event_id:
                    res = client.patch(
                        f"{base}/{quote(str(event_id), safe='')}",
                        params=params,
                        headers=auth,
                        json=body,
                    )
                    if res.status_code in (404, 410):
                        res = None  # gone, or not on this calendar: make a new one
                if res is None:
                    res = client.post(base, params=params, headers=auth, json=body)
                    if res.status_code == 404 and cal != "primary":
                        # A calendar this mailbox can't see (an imported id):
                        # the mailbox's own calendar is where invites belong.
                        cal, base = (
                            "primary",
                            f"{CALENDAR_API}/calendars/primary/events",
                        )
                        res = client.post(base, params=params, headers=auth, json=body)
                if res.status_code not in (200, 201):
                    kind = _google_error(res.status_code, res.text)
                    logger.info(
                        "calendar upsert failed %s: %s", res.status_code, res.text[:300]
                    )
                    # The new pack is useless without the event; don't leave it in Drive.
                    self._drive_delete(client, token, drive_ids)
                    return self._refusal(kind, mailbox, venue_name, res.status_code)
                created = res.json()
                if drive_ids and replace_drive_file_ids:
                    self._drive_delete(client, token, replace_drive_file_ids)
        except httpx.HTTPError as exc:
            logger.info("calendar upsert transport error: %s", exc)
            return {
                "ok": False,
                "calendar_synced": False,
                "message": "Google Calendar couldn't be reached. Try saving again in a minute.",
            }

        organizer = ((created.get("organizer") or {}).get("email")) or mailbox
        if organizer and organizer != (conn.oauth_metadata or {}).get("email"):
            self._remember(conn, email=organizer)
        message = (
            f"Invite sent from {organizer or venue_name} to {len(guests)} "
            f"guest{'' if len(guests) == 1 else 's'}"
        )
        if skipped:
            message += f". No email on file for: {', '.join(skipped)}"
        if note:
            message += f". Note: {note}"
        if left_alone:
            message += (
                ". The earlier invite wasn't made by Norm, so it was left as it was"
            )
        return {
            "ok": True,
            "calendar_synced": True,
            "event_id": created.get("id"),
            "calendar_id": cal,
            "account_id": conn.id,
            "mailbox": organizer,
            "html_link": created.get("htmlLink"),
            "hangout_link": created.get("hangoutLink"),
            "guests": len(guests),
            "skipped": skipped,
            "refused": refused,
            "attached": bool(attachments),
            "attachment_note": note,
            "drive_file_ids": drive_ids
            if attachments
            else list(replace_drive_file_ids or []),
            "message": message,
        }

    def delete(
        self, venue_id, event_id, account_id=None, calendar_id=None, drive_file_ids=None
    ) -> dict:
        """Remove an event (guests are told). Already gone counts as done."""
        if not event_id:
            return {"ok": True, "message": "There was no calendar event to remove."}
        if self.preview:
            return {
                "ok": True,
                "message": "Preview only — the calendar event was left.",
            }
        venue = self._venue(venue_id)
        if venue is None:
            return {
                "ok": False,
                "message": "This job has no venue, so there's no venue mailbox to remove the event from.",
            }
        why = self._allowed(venue)
        if why:
            return {"ok": False, "message": why}
        conn = self._connection(venue, account_id)
        if conn is None:
            name = venue.name if venue else "This venue"
            return {
                "ok": False,
                "message": f"{name} has no connected mailbox, so the calendar event couldn't be removed.",
            }
        token = self._token(conn)
        if not token:
            return {
                "ok": False,
                "message": "The venue mailbox needs reconnecting (Settings → Venues), so the calendar event couldn't be removed.",
            }
        cal = str(calendar_id or "primary")
        url = f"{CALENDAR_API}/calendars/{quote(cal, safe='')}/events/{quote(str(event_id), safe='')}"
        try:
            with _client() as client:
                base = f"{CALENDAR_API}/calendars/{quote(cal, safe='')}/events"
                ours = self._ours(
                    client, base, event_id, {"Authorization": f"Bearer {token}"}
                )
                if ours == "gone":
                    self._drive_delete(client, token, drive_file_ids)
                    return {
                        "ok": True,
                        "message": "The calendar event was already gone.",
                    }
                if ours == "foreign":
                    return {
                        "ok": False,
                        "message": "That calendar event wasn't made by Norm, so Norm left it alone.",
                    }
                res = client.delete(
                    url,
                    params={"sendUpdates": "all"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if res.status_code not in (200, 204, 404, 410):
                    kind = _google_error(res.status_code, res.text)
                    out = self._refusal(
                        kind,
                        self._mailbox_email(conn, token),
                        venue.name if venue else "the venue",
                        res.status_code,
                    )
                    out["message"] = out["message"].replace(
                        "the invite", "the cancellation"
                    )
                    return out
                self._drive_delete(client, token, drive_file_ids)
        except httpx.HTTPError:
            return {
                "ok": False,
                "message": "Google Calendar couldn't be reached to remove the event.",
            }
        return {
            "ok": True,
            "message": "Calendar event removed — guests have been told.",
        }


def calendar_door(
    db: Session, config_db: Session | None, app, version, user, mode: str = "live"
):
    """The sandbox's ``calendar`` object. Plain functions over a
    :class:`VenueCalendar` the sandbox can't reach (it holds ``db``)."""
    impl = VenueCalendar(db, config_db, app=app, version=version, user=user, mode=mode)

    def venues():
        return impl.venues()

    def status(venue_id):
        return impl.status(venue_id)

    def local_time(value, venue_id=None):
        return impl.local_time(value, venue_id)

    def upsert(
        venue_id,
        event,
        event_id=None,
        account_id=None,
        calendar_id=None,
        attach_file_ids=None,
        attach_title=None,
        replace_drive_file_ids=None,
    ):
        return impl.upsert(
            venue_id,
            event,
            event_id=event_id,
            account_id=account_id,
            calendar_id=calendar_id,
            attach_file_ids=attach_file_ids,
            attach_title=attach_title,
            replace_drive_file_ids=replace_drive_file_ids,
        )

    def delete(
        venue_id, event_id, account_id=None, calendar_id=None, drive_file_ids=None
    ):
        return impl.delete(
            venue_id,
            event_id,
            account_id=account_id,
            calendar_id=calendar_id,
            drive_file_ids=drive_file_ids,
        )

    return SimpleNamespace(
        venues=venues,
        status=status,
        local_time=local_time,
        upsert=upsert,
        delete=delete,
    )
