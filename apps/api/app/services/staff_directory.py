"""The staff directory: who works at an organisation's venues
(docs/plans/staff-access.md, sections 6 and 11.6).

A ``StaffMember`` row per person, copied from Loaded every hour (``source``
"loadedhub", keyed by Loaded's staff id) or added by hand or from a
spreadsheet ("manual"). This module does the copying, the by-hand edits, the
tablet PINs, and the mirror into the HR apps' ``hr_suite/people`` records so
Training, Hiring and Performance keep reading one list.

What the copy does with Loaded's list, per venue:

* One Norm venue is one Loaded company — the venue's ``loadedhub`` Connection
  carries the company header, so the staff list that call returns is that
  venue's people (``get_labour``'s staff view reads it the same way). A person
  can appear under several venues; one row holds every venue.
* People Loaded has deleted (``datestampDeleted``) are not people here.
* After every venue has answered, Loaded-sourced rows nobody listed are
  switched off and their Norm access revoked; a row seen again is switched
  back on — unless a manager switched it off in Norm (``held_at``), which the
  copy respects until a manager switches it on again. A venue whose call
  failed is left as it was — a Loaded outage must never read as "everyone has
  left".

Account effects (membership, venue access, deactivating the login) live in
``services/staff_access``; this module calls it and never touches a
membership itself.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import logging
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    AppRecord,
    Connection,
    Organization,
    Role,
    StaffMember,
    User,
    Venue,
)

logger = logging.getLogger(__name__)

LOADED = "loadedhub"
MANUAL = "manual"

#: Where the HR apps keep their people (builtin_apps/training.json storage).
PEOPLE_NAMESPACE = "hr_suite"
PEOPLE_COLLECTION = "people"

#: The import's columns, in the order the template lists them.
CSV_COLUMNS = ("name", "email", "phone", "venues", "role")


class DirectoryError(ValueError):
    """A change the directory refuses, with a sentence for the person."""


@dataclass
class SyncSummary:
    added: int = 0
    updated: int = 0
    deactivated: int = 0
    reactivated: int = 0
    #: Venues whose Loaded call failed this run ("La Zeppa: ...").
    errors: list[str] = field(default_factory=list)

    def merge(self, other: SyncSummary) -> SyncSummary:
        self.added += other.added
        self.updated += other.updated
        self.deactivated += other.deactivated
        self.reactivated += other.reactivated
        self.errors.extend(other.errors)
        return self

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _staff_access():
    """``services/staff_access`` (the sign-in stream's module), imported when
    needed so the directory loads without it."""
    from app.services import staff_access

    return staff_access


def _clean_email(value) -> str | None:
    email = str(value or "").strip().lower()
    return email or None


def _clean_phone(value) -> str | None:
    phone = str(value or "").strip()
    return phone or None


# ── Loaded's payload ────────────────────────────────────────────────────────


def _rows_of(payload) -> list[dict]:
    """The staff rows in a ``get_staff_members`` payload: a list, or a dict
    carrying one under ``data``/``rows``/``items``."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "rows", "items", "staffMembers"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _role_names(row: dict) -> list[str]:
    """The member's Loaded role names, default role first, no repeats. Read
    from every place Loaded puts them: the top-level default and member
    roles, and ``activeRemuneration``'s member roles and venue assignments
    (config/consolidators/shapes.json)."""
    names: list[str] = []

    def add(name) -> None:
        text = " ".join(str(name or "").split())
        if text and text.casefold() not in {n.casefold() for n in names}:
            names.append(text)

    add(row.get("defaultMemberRoleRoleName"))
    for m in row.get("memberRoles") or []:
        if isinstance(m, dict):
            add(m.get("roleName"))
    rem = row.get("activeRemuneration") or {}
    if isinstance(rem, dict):
        for m in rem.get("memberRoles") or []:
            if isinstance(m, dict):
                add(m.get("roleName"))
        for a in rem.get("venueAssignments") or []:
            if isinstance(a, dict):
                add(a.get("defaultRoleName"))
                for m in a.get("memberRoles") or []:
                    if isinstance(m, dict):
                        add(m.get("roleName"))
    return names


def _loaded_person(row: dict) -> dict | None:
    """The directory fields of one Loaded staff row, or None when the row is
    not a current person (deleted, inactive, or without an id or a name)."""
    external_id = str(row.get("id") or "").strip()
    if not external_id or row.get("datestampDeleted"):
        return None
    rem = row.get("activeRemuneration")
    if isinstance(rem, dict) and rem.get("isActive") is False:
        return None
    name = " ".join(str(row.get("name") or "").split())
    if not name:
        name = " ".join(
            str(row.get(k) or "").strip() for k in ("firstName", "lastName")
        ).strip()
    if not name:
        return None
    return {
        "external_id": external_id,
        "name": name,
        "email": _clean_email(row.get("email")),
        "phone": _clean_phone(row.get("phoneMobile") or row.get("phone")),
        "loaded_roles": _role_names(row),
    }


def upsert_from_loaded(
    db: Session, org_id: str, venue, rows, *, now: datetime | None = None
) -> SyncSummary:
    """Fold one venue's Loaded staff list into the organisation's directory.

    ``rows`` is the payload of ``loadedhub.get_staff_members`` for ``venue``
    (a Venue or its id). New people are added, changed details updated, this
    venue is added to each person's venues, and a switched-off person who is
    back in the list is switched on again — unless a manager switched them
    off in Norm (``held_at``), which stands until a manager lifts it. Nobody
    is switched off here — that needs every venue's answer (``sync_org``).
    ``last_synced_at`` marks the rows this run saw.
    """
    now = now or _now()
    venue_id = getattr(venue, "id", venue)
    summary = SyncSummary()
    existing = {
        s.external_id: s
        for s in db.query(StaffMember)
        .filter(StaffMember.organization_id == org_id, StaffMember.source == LOADED)
        .all()
    }
    for row in _rows_of(rows):
        person = _loaded_person(row)
        if person is None:
            continue
        staff = existing.get(person["external_id"])
        if staff is None:
            staff = StaffMember(
                organization_id=org_id,
                source=LOADED,
                external_id=person["external_id"],
                name=person["name"],
                email=person["email"],
                phone=person["phone"],
                loaded_roles=person["loaded_roles"],
                venue_ids=[venue_id],
                is_active=True,
                last_synced_at=now,
            )
            db.add(staff)
            existing[person["external_id"]] = staff
            summary.added += 1
            continue
        changed = False
        for key in ("name", "email", "phone", "loaded_roles"):
            if getattr(staff, key) != person[key]:
                setattr(staff, key, person[key])
                changed = True
        if venue_id not in (staff.venue_ids or []):
            # A new list: a JSON column only notices reassignment.
            staff.venue_ids = list(staff.venue_ids or []) + [venue_id]
            changed = True
        if not staff.is_active and staff.held_at is None:
            staff.is_active = True
            staff.removed_at = None
            summary.reactivated += 1
            _restore_access(db, staff)
        elif changed and staff.is_active:
            summary.updated += 1
        staff.last_synced_at = now
    db.flush()
    return summary


def _restore_access(db: Session, staff: StaffMember) -> None:
    """Someone switched off and now back: give a claimed account its
    membership and venue access again, so an open session works at once
    rather than at the next sign-in."""
    if not staff.user_id:
        return
    user = db.query(User).filter(User.id == staff.user_id).first()
    if user is None:
        return
    try:
        _staff_access().ensure_membership(db, user, staff)
    except Exception:  # noqa: BLE001 — the directory row is still right
        logger.exception("could not restore access for staff %s", staff.id)


def _revoke(db: Session, staff: StaffMember) -> None:
    try:
        _staff_access().revoke(db, staff)
    except Exception:  # noqa: BLE001
        logger.exception("could not revoke access for staff %s", staff.id)


def connected_venues(db: Session, org_id: str) -> list[Venue]:
    """The organisation's venues with an enabled Loaded connection."""
    return (
        db.query(Venue)
        .join(Connection, Connection.venue_id == Venue.id)
        .filter(
            Venue.organization_id == org_id,
            Connection.connector_name == LOADED,
            Connection.enabled == "true",
        )
        .order_by(Venue.name)
        .all()
    )


def sync_org(
    db: Session, config_db: Session, org_id: str, *, now: datetime | None = None
) -> SyncSummary:
    """Copy every connected venue's staff list from Loaded into the directory
    and mirror the result into the HR apps' people records. Flushes; the
    caller commits."""
    from app.connectors.tool_executor import execute_connector_tool

    now = now or _now()
    summary = SyncSummary()
    venues = connected_venues(db, org_id)
    payloads: dict[str, object] = {}
    for venue in venues:
        try:
            result = execute_connector_tool(
                LOADED,
                "get_staff_members",
                {"include_deleted": "false", "include_last_clocks": "false"},
                db,
                config_db,
                venue_id=venue.id,
                strict_venue=True,
            )
        except Exception as exc:  # noqa: BLE001 — one venue never stops the rest
            logger.exception("staff sync: %s failed", venue.name)
            summary.errors.append(f"{venue.name}: {exc}")
            continue
        if not result.success:
            summary.errors.append(f"{venue.name}: {result.error or 'no answer'}")
            continue
        payloads[venue.id] = result.payload

    loaded_rows = (
        db.query(StaffMember)
        .filter(StaffMember.organization_id == org_id, StaffMember.source == LOADED)
        .all()
    )
    # The venues that answered own their assignments: take them off every
    # row first, and the upserts put back the ones Loaded still lists. A
    # venue that did not answer keeps what it had.
    if payloads:
        for staff in loaded_rows:
            kept = [v for v in (staff.venue_ids or []) if v not in payloads]
            if kept != list(staff.venue_ids or []):
                staff.venue_ids = kept
    for venue in venues:
        if venue.id in payloads:
            summary.merge(
                upsert_from_loaded(db, org_id, venue, payloads[venue.id], now=now)
            )

    if venues and not summary.errors:
        for staff in loaded_rows:
            if staff.is_active and staff.last_synced_at != now:
                staff.is_active = False
                staff.removed_at = now
                summary.deactivated += 1
                _revoke(db, staff)
    db.flush()

    mirror_people(db, config_db, org_id)
    db.flush()
    return summary


# ── The HR apps' people records ─────────────────────────────────────────────


def _training_on(db: Session, config_db: Session, org_id: str) -> bool:
    from app.services.builtin_apps import is_on

    try:
        return is_on("training", org_id, db, config_db)
    except Exception:  # noqa: BLE001 — a catalog hiccup means "don't create"
        logger.exception("could not tell whether Training is on for %s", org_id)
        return False


def mirror_people(db: Session, config_db: Session, org_id: str) -> int:
    """Keep ``hr_suite/people`` in step with the Loaded rows of the directory:
    a record matched by ``loadedhub_id`` (else email) gets the person's name,
    email, venues, Loaded role, active flag and Norm login; a new active
    person gets a record when the Training app is on. Returns how many records
    were written. The shape is the one scripts/migrate_orbit_training.py
    wrote and training.py's ``save_person`` writes."""
    staff_rows = (
        db.query(StaffMember)
        .filter(StaffMember.organization_id == org_id, StaffMember.source == LOADED)
        .all()
    )
    if not staff_rows:
        return 0
    records = (
        db.query(AppRecord)
        .filter(
            AppRecord.namespace == PEOPLE_NAMESPACE,
            AppRecord.collection == PEOPLE_COLLECTION,
            AppRecord.organization_id == org_id,
        )
        .all()
    )
    by_loaded: dict[str, AppRecord] = {}
    by_email: dict[str, AppRecord] = {}
    for rec in records:
        data = rec.data or {}
        if data.get("loadedhub_id"):
            by_loaded.setdefault(str(data["loadedhub_id"]), rec)
        if data.get("email"):
            by_email.setdefault(str(data["email"]).strip().lower(), rec)
    create = None  # looked up once, only if a record is missing
    written = 0
    for staff in staff_rows:
        rec = by_loaded.get(staff.external_id)
        if rec is None and staff.email:
            rec = by_email.get(staff.email)
        fields = {
            "name": staff.name,
            "email": staff.email,
            "is_active": bool(staff.is_active),
            "venues": list(staff.venue_ids or []),
            "loadedhub_id": staff.external_id,
        }
        if staff.loaded_roles:
            fields["loadedhub_role"] = staff.loaded_roles[0]
        if rec is None:
            if not staff.is_active:
                continue
            if create is None:
                create = _training_on(db, config_db, org_id)
            if not create:
                continue
            data = {"role": "team_member", **fields}
            if staff.user_id:
                data["norm_user_id"] = staff.user_id
            rec = AppRecord(
                namespace=PEOPLE_NAMESPACE,
                organization_id=org_id,
                venue_id=None,
                collection=PEOPLE_COLLECTION,
                data=data,
            )
            db.add(rec)
            by_loaded[staff.external_id] = rec
            written += 1
            continue
        data = dict(rec.data or {})
        for key, value in fields.items():
            if key == "email" and value is None:
                continue  # a person's email is never blanked by a sync
            data[key] = value
        if staff.user_id and not data.get("norm_user_id"):
            data["norm_user_id"] = staff.user_id
        if data != (rec.data or {}):
            rec.data = data  # a new dict: JSONB only notices reassignment
            written += 1
    db.flush()
    return written


# ── By hand and from a spreadsheet ──────────────────────────────────────────


def _org_venue_ids(db: Session, org_id: str) -> dict[str, Venue]:
    return {
        v.id: v for v in db.query(Venue).filter(Venue.organization_id == org_id).all()
    }


def _check_venues(db: Session, org_id: str, venue_ids) -> list[str]:
    """``venue_ids`` as a clean list, every one of them this organisation's."""
    wanted = [str(v) for v in (venue_ids or []) if v]
    known = _org_venue_ids(db, org_id)
    unknown = [v for v in wanted if v not in known]
    if unknown:
        raise DirectoryError("One of those venues isn't in this organisation.")
    out: list[str] = []
    for v in wanted:
        if v not in out:
            out.append(v)
    return out


def _check_role(db: Session, org_id: str, role_id) -> str | None:
    """``role_id`` when it is a system role or one of this organisation's."""
    if not role_id:
        return None
    role = (
        db.query(Role)
        .filter(
            Role.id == str(role_id),
            (Role.organization_id == org_id) | (Role.organization_id.is_(None)),
        )
        .first()
    )
    if role is None:
        raise DirectoryError("That role isn't one of this organisation's roles.")
    return role.id


def add_manual(
    db: Session,
    org_id: str,
    *,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    venue_ids=(),
    role_id: str | None = None,
) -> StaffMember:
    """Add someone by hand. Flushes; the caller commits."""
    name = " ".join(str(name or "").split())
    if not name:
        raise DirectoryError("A person needs a name.")
    email = _clean_email(email)
    if email and ("@" not in email or " " in email):
        raise DirectoryError("That email address doesn't look right.")
    staff = StaffMember(
        organization_id=org_id,
        source=MANUAL,
        name=name,
        email=email,
        phone=_clean_phone(phone),
        loaded_roles=[],
        venue_ids=_check_venues(db, org_id, venue_ids),
        role_id=_check_role(db, org_id, role_id),
        is_active=True,
    )
    db.add(staff)
    db.flush()
    return staff


_UNSET = object()


def update(
    db: Session,
    staff: StaffMember,
    *,
    name=_UNSET,
    email=_UNSET,
    phone=_UNSET,
    venue_ids=_UNSET,
    role_id=_UNSET,
    is_active=_UNSET,
) -> StaffMember:
    """Change a person. A Loaded-sourced row takes only ``role_id`` and
    ``is_active`` — its details are Loaded's to change. Switching someone off
    revokes their Norm access and holds the row (``held_at``) so the hourly
    copy doesn't switch them back on; switching them back on lifts the hold
    and restores their access."""
    org_id = staff.organization_id
    details = {"name": name, "email": email, "phone": phone, "venue_ids": venue_ids}
    given = {k: v for k, v in details.items() if v is not _UNSET}
    if given and staff.source == LOADED:
        raise DirectoryError(
            "This person comes from Loaded, so their name, contact details and "
            "venues are changed there. Their role and whether they're active "
            "can be changed here."
        )
    if "name" in given:
        clean = " ".join(str(given["name"] or "").split())
        if not clean:
            raise DirectoryError("A person needs a name.")
        staff.name = clean
    if "email" in given:
        clean_email = _clean_email(given["email"])
        if clean_email and ("@" not in clean_email or " " in clean_email):
            raise DirectoryError("That email address doesn't look right.")
        staff.email = clean_email
    if "phone" in given:
        staff.phone = _clean_phone(given["phone"])
    if "venue_ids" in given:
        staff.venue_ids = _check_venues(db, org_id, given["venue_ids"])
    if role_id is not _UNSET:
        staff.role_id = _check_role(db, org_id, role_id)
    if is_active is not _UNSET:
        wanted = bool(is_active)
        if staff.is_active and not wanted:
            staff.is_active = False
            staff.removed_at = _now()
            staff.held_at = staff.removed_at
            _revoke(db, staff)
        elif wanted and not staff.is_active:
            staff.is_active = True
            staff.removed_at = None
            staff.held_at = None
            _restore_access(db, staff)
    db.flush()
    return staff


def remove(db: Session, staff: StaffMember) -> None:
    """Take someone out of the directory: a row added by hand is deleted, a
    Loaded row is switched off (Loaded would only add it back). Either way
    their Norm access in this organisation goes."""
    _revoke(db, staff)
    if staff.source == LOADED:
        staff.is_active = False
        staff.removed_at = _now()
        staff.held_at = staff.removed_at
        staff.pin_hash = None
        staff.pin_set_at = None
    else:
        db.delete(staff)
    db.flush()


def _roles_by_label(db: Session, org_id: str) -> dict[str, Role]:
    out: dict[str, Role] = {}
    for role in (
        db.query(Role)
        .filter((Role.organization_id == org_id) | (Role.organization_id.is_(None)))
        .order_by(Role.is_system.asc())
        .all()
    ):
        for label in (role.name, role.display_name):
            key = " ".join(str(label or "").split()).casefold()
            if key:
                out.setdefault(key, role)
    return out


def import_csv(db: Session, org_id: str, text: str) -> ImportResult:
    """People from a spreadsheet: columns ``name,email,phone,venues,role``
    (``venues`` = venue names separated by ``;``, ``role`` = a role's name or
    display name). A row whose email is already in the directory updates
    that person; a bad row is reported with its line and skipped. Flushes;
    the caller commits."""
    result = ImportResult()
    reader = csv.DictReader(io.StringIO(str(text or "").lstrip("﻿")))
    headers = [h.strip().lower() for h in (reader.fieldnames or []) if h]
    if "name" not in headers:
        result.errors.append(
            {
                "line": 1,
                "message": "The first line needs the column names, including 'name'.",
            }
        )
        return result
    venues_by_name = {
        " ".join(v.name.split()).casefold(): v.id
        for v in _org_venue_ids(db, org_id).values()
    }
    roles = _roles_by_label(db, org_id)
    by_email = {
        s.email: s
        for s in db.query(StaffMember)
        .filter(StaffMember.organization_id == org_id, StaffMember.email.isnot(None))
        .all()
    }
    for raw in reader:
        line = reader.line_num
        row = {
            str(k or "").strip().lower(): str(v or "").strip()
            for k, v in raw.items()
            if k is not None
        }
        if not any(row.values()):
            continue
        name = " ".join(row.get("name", "").split())
        if not name:
            result.errors.append({"line": line, "message": "This row has no name."})
            continue
        email = _clean_email(row.get("email"))
        if email and ("@" not in email or " " in email):
            result.errors.append(
                {
                    "line": line,
                    "message": f"'{row.get('email')}' isn't an email address.",
                }
            )
            continue
        venue_ids: list[str] = []
        bad_venue = None
        for part in row.get("venues", "").split(";"):
            label = " ".join(part.split())
            if not label:
                continue
            vid = venues_by_name.get(label.casefold())
            if vid is None:
                bad_venue = label
                break
            if vid not in venue_ids:
                venue_ids.append(vid)
        if bad_venue:
            result.errors.append(
                {"line": line, "message": f"There's no venue called '{bad_venue}'."}
            )
            continue
        role = None
        role_label = " ".join(row.get("role", "").split())
        if role_label:
            role = roles.get(role_label.casefold())
            if role is None:
                result.errors.append(
                    {"line": line, "message": f"There's no role called '{role_label}'."}
                )
                continue
        existing = by_email.get(email) if email else None
        try:
            if existing is None:
                staff = add_manual(
                    db,
                    org_id,
                    name=name,
                    email=email,
                    phone=row.get("phone"),
                    venue_ids=venue_ids,
                    role_id=role.id if role else None,
                )
                if email:
                    by_email[email] = staff
                result.created += 1
            elif existing.source == LOADED:
                update(db, existing, role_id=role.id if role else existing.role_id)
                result.updated += 1
            else:
                update(
                    db,
                    existing,
                    name=name,
                    phone=row.get("phone") or existing.phone,
                    venue_ids=venue_ids or list(existing.venue_ids or []),
                    role_id=role.id if role else existing.role_id,
                )
                result.updated += 1
        except DirectoryError as exc:
            result.errors.append({"line": line, "message": str(exc)})
    db.flush()
    return result


# ── Tablet PINs ─────────────────────────────────────────────────────────────

_PIN_LABEL = b"norm-staff/pin/v1"


# ── Accounts a row is linked to ─────────────────────────────────────────────


PLACEHOLDER_DOMAIN = "staff.norm.local"


def placeholder_email(staff: StaffMember) -> str:
    """The address of the account a tablet PIN makes for someone before they
    have signed in with a code."""
    return f"staff-{staff.id}@{PLACEHOLDER_DOMAIN}"


def is_placeholder(user: User | None) -> bool:
    """An account Norm made for a staff member from a tablet PIN: no
    password, not a device, and the placeholder address."""
    return (
        user is not None
        and not user.password_set
        and not user.is_device
        and (user.email or "").endswith("@" + PLACEHOLDER_DOMAIN)
    )


def staff_record_for(
    db: Session, user: User, org_id: str | None, *, link: bool = False
) -> StaffMember | None:
    """The user's active staff record in ``org_id``: the row linked to them,
    else an unclaimed row with their email (a manager who is also on the
    roster). ``/auth/me`` and ``/api/me/pin`` read the same answer; with
    ``link`` an email match is linked to the user so later calls are direct.
    Flushed, not committed."""
    if not org_id:
        return None
    email = (user.email or "").strip().lower()
    staff = (
        db.query(StaffMember)
        .filter(
            StaffMember.organization_id == org_id,
            StaffMember.is_active.is_(True),
            (StaffMember.user_id == user.id)
            | (StaffMember.user_id.is_(None) & (StaffMember.email == email)),
        )
        .order_by(StaffMember.user_id.is_(None), StaffMember.created_at)
        .first()
    )
    if staff is not None and link and staff.user_id != user.id:
        staff.user_id = user.id
        db.flush()
    return staff


def _pin_key() -> bytes:
    from app.auth.security import JWT_SECRET

    return hmac.new(str(JWT_SECRET).encode(), _PIN_LABEL, hashlib.sha256).digest()


def pin_hash(org_id: str, pin: str) -> str:
    return hmac.new(_pin_key(), f"{org_id}:{pin}".encode(), hashlib.sha256).hexdigest()


def set_pin(db: Session, staff: StaffMember) -> str:
    """Give someone a new 5-digit tablet PIN, unique in their organisation,
    and return it — the one time it is ever readable. Flushes; the caller
    commits."""
    org_id = staff.organization_id
    for _ in range(100):
        pin = f"{secrets.randbelow(100_000):05d}"
        digest = pin_hash(org_id, pin)
        taken = (
            db.query(StaffMember.id)
            .filter(
                StaffMember.organization_id == org_id,
                StaffMember.pin_hash == digest,
                StaffMember.id != staff.id,
            )
            .first()
        )
        if taken is None:
            staff.pin_hash = digest
            staff.pin_set_at = _now()
            db.flush()
            return pin
    raise RuntimeError("could not find a free PIN")


def verify_pin(db: Session, org_id: str, venue_id: str, pin: str) -> StaffMember | None:
    """The active staff member at ``venue_id`` whose PIN this is, or None."""
    pin = str(pin or "").strip()
    if not pin.isdigit():
        return None
    staff = (
        db.query(StaffMember)
        .filter(
            StaffMember.organization_id == org_id,
            StaffMember.pin_hash == pin_hash(org_id, pin),
        )
        .first()
    )
    if staff is None or not staff.is_active:
        return None
    if venue_id not in (staff.venue_ids or []):
        return None
    return staff


def clear_pin(db: Session, staff: StaffMember) -> None:
    staff.pin_hash = None
    staff.pin_set_at = None
    db.flush()


# ── Settings → People ───────────────────────────────────────────────────────


def _role_name(role: Role | None) -> str | None:
    if role is None:
        return None
    return role.display_name or role.name


def list_people(db: Session, org_id: str) -> dict:
    """Everyone in the organisation: office users (memberships) and venue
    staff (directory rows), merged into one person when they share a login
    or an email. Venue devices are not people."""
    from app.db.models import OrganizationMembership, UserVenueAccess

    org = db.query(Organization).filter(Organization.id == org_id).first()
    venues = _org_venue_ids(db, org_id)
    roles = {
        r.id: r
        for r in db.query(Role)
        .filter((Role.organization_id == org_id) | (Role.organization_id.is_(None)))
        .all()
    }
    memberships = (
        db.query(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .filter(
            OrganizationMembership.organization_id == org_id,
            User.is_device.is_(False),
        )
        .all()
    )
    member_ids = [u.id for _, u in memberships]
    access: dict[str, list[str]] = {}
    if member_ids:
        for row in (
            db.query(UserVenueAccess)
            .filter(
                UserVenueAccess.user_id.in_(member_ids),
                UserVenueAccess.venue_id.in_(list(venues)),
            )
            .all()
        ):
            access.setdefault(row.user_id, []).append(row.venue_id)

    people: list[dict] = []
    by_user: dict[str, dict] = {}
    by_email: dict[str, dict] = {}
    for membership, user in memberships:
        role = roles.get(membership.role_id) if membership.role_id else None
        person = {
            "kind": "member",
            "user_id": user.id,
            "staff_id": None,
            "name": user.full_name,
            "email": user.email,
            "phone": None,
            "role_id": membership.role_id,
            "role_name": _role_name(role),
            "venue_ids": sorted(access.get(user.id, [])),
            "is_active": bool(user.is_active),
            "source": None,
            "loaded_roles": [],
            "signed_in": bool(user.is_active),
            "invited": not user.is_active and bool(user.password_set),
            "last_seen_at": None,
            "has_pin": False,
        }
        people.append(person)
        by_user[user.id] = person
        if user.email:
            by_email.setdefault(user.email.lower(), person)

    staff_rows = (
        db.query(StaffMember)
        .filter(StaffMember.organization_id == org_id)
        .order_by(StaffMember.name)
        .all()
    )
    loaded_role_names: set[str] = set()
    for staff in staff_rows:
        loaded_role_names.update(staff.loaded_roles or [])
        match = by_user.get(staff.user_id) if staff.user_id else None
        if match is None and staff.email:
            match = by_email.get(staff.email)
        if match is not None and match["kind"] == "member":
            match["kind"] = "both"
            match["staff_id"] = staff.id
            match["phone"] = staff.phone
            match["venue_ids"] = sorted(
                set(match["venue_ids"]) | set(staff.venue_ids or [])
            )
            match["source"] = staff.source
            match["loaded_roles"] = list(staff.loaded_roles or [])
            match["last_seen_at"] = staff.last_seen_at
            match["has_pin"] = bool(staff.pin_hash)
            if not match["role_id"] and staff.role_id:
                match["role_id"] = staff.role_id
                match["role_name"] = _role_name(roles.get(staff.role_id))
            if not staff.is_active:
                match["is_active"] = False
            continue
        role = roles.get(staff.role_id) if staff.role_id else None
        person = {
            "kind": "staff",
            "user_id": staff.user_id,
            "staff_id": staff.id,
            "name": staff.name,
            "email": staff.email,
            "phone": staff.phone,
            "role_id": staff.role_id,
            "role_name": _role_name(role),
            "venue_ids": list(staff.venue_ids or []),
            "is_active": bool(staff.is_active),
            "source": staff.source,
            "loaded_roles": list(staff.loaded_roles or []),
            "signed_in": bool(staff.user_id),
            "invited": False,
            "last_seen_at": staff.last_seen_at,
            "has_pin": bool(staff.pin_hash),
        }
        people.append(person)
        if staff.user_id:
            by_user[staff.user_id] = person
        if staff.email:
            by_email.setdefault(staff.email, person)

    people.sort(key=lambda p: (not p["is_active"], str(p["name"] or "").casefold()))
    return {
        "people": people,
        "roles": [
            {
                "id": r.id,
                "name": r.name,
                "display_name": r.display_name,
                "is_system": bool(r.is_system),
                # What the role allows, so People's "what this role allows"
                # dialog needs no second fetch of /roles (11.3 shapes).
                "permissions": list(r.permissions or []),
                "agents": None if r.agents is None else list(r.agents),
                "apps": None if r.apps is None else list(r.apps),
            }
            for r in sorted(
                roles.values(), key=lambda r: (not r.is_system, r.display_name or "")
            )
        ],
        "venues": [
            {"id": v.id, "name": v.name}
            for v in sorted(venues.values(), key=lambda v: v.name)
        ],
        "loaded_role_names": sorted(loaded_role_names, key=str.casefold),
        "sync": sync_status(db, org) if org else None,
    }


def sync_status(db: Session, org: Organization) -> dict:
    from app.db.models import StaffSyncRun

    last = (
        db.query(StaffSyncRun)
        .filter(StaffSyncRun.organization_id == org.id)
        .order_by(StaffSyncRun.started_at.desc())
        .first()
    )
    return {
        "enabled": bool(org.staff_access_enabled),
        "loaded_connected": bool(connected_venues(db, org.id)),
        "last_run_at": (last.finished_at or last.started_at) if last else None,
        "last_summary": last.summary if last else None,
    }
