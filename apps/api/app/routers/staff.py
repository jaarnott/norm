"""The staff directory, Settings → People, PINs and venue tablets
(docs/plans/staff-access.md, 11.6). Routes carry their full paths; the
router has no prefix.

Every route stays inside the organisation in the path: a venue must be one
of its venues, a role one of its own or a system role, and the person making
the change must hold the permission IN THAT ORGANISATION
(``auth/org_scope.require_org_permission`` — ``require_permission`` would
read their active organisation's role instead, so an owner elsewhere could
act here). The directory rules themselves live in
``services/staff_directory``; this file is the HTTP shape and the tablets,
whose accounts are made here.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.org_scope import permissions_in, require_org_permission
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.db.engine import get_config_db, get_db
from app.db.models import (
    Organization,
    OrganizationMembership,
    Role,
    StaffMember,
    User,
    UserVenueAccess,
    Venue,
    VenueDevice,
)
from app.services import active_org, staff_directory
from app.services.staff_directory import DirectoryError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["staff"])


# ── shared checks ───────────────────────────────────────────────────────────


def _org_or_404(org_id: str, db: Session) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(404, "Organization not found")
    return org


def _scoped(
    user: User, org_id: str, scope: str, db: Session
) -> OrganizationMembership | None:
    """The caller holds ``scope`` in the organisation in the path (a platform
    admin passes), and the organisation exists. Returns their membership
    there (None for an admin) for anything that needs their permissions."""
    membership = require_org_permission(db, user, org_id, scope)
    _org_or_404(org_id, db)
    return membership


def _staff_or_404(org_id: str, staff_id: str, db: Session) -> StaffMember:
    staff = (
        db.query(StaffMember)
        .filter(StaffMember.id == staff_id, StaffMember.organization_id == org_id)
        .first()
    )
    if staff is None:
        raise HTTPException(404, "Staff member not found")
    return staff


def _venue_in_org(org_id: str, venue_id: str, db: Session) -> Venue:
    venue = (
        db.query(Venue)
        .filter(Venue.id == venue_id, Venue.organization_id == org_id)
        .first()
    )
    if venue is None:
        raise HTTPException(400, "That venue isn't in this organisation.")
    return venue


def _role_in_org(org_id: str, role_id: str, db: Session) -> Role:
    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            (Role.organization_id == org_id) | (Role.organization_id.is_(None)),
        )
        .first()
    )
    if role is None:
        raise HTTPException(400, "That role isn't one of this organisation's roles.")
    return role


def _device_role(
    org_id: str, role_id: str, caller: OrganizationMembership | None, db: Session
) -> Role:
    """The role a tablet may act under: one of the organisation's, never
    Owner, and never one holding more than the person setting it up holds
    here — a Manager (org:manage) must not mint a year-long device token with
    billing:manage or org:roles in it. Platform admins are only held to the
    Owner rule."""
    role = _role_in_org(org_id, role_id, db)
    if role.name == "owner":
        raise HTTPException(400, "A tablet can't act as an Owner.")
    held = permissions_in(caller)
    if held is not None:
        extra = sorted(set(role.permissions or []) - held)
        if extra:
            raise HTTPException(
                400,
                "A tablet can't act under a role with more than you hold here: "
                + ", ".join(extra),
            )
    return role


def _staff_to_dict(staff: StaffMember) -> dict:
    return {
        "id": staff.id,
        "organization_id": staff.organization_id,
        "source": staff.source,
        "external_id": staff.external_id,
        "name": staff.name,
        "email": staff.email,
        "phone": staff.phone,
        "loaded_roles": list(staff.loaded_roles or []),
        "venue_ids": list(staff.venue_ids or []),
        "role_id": staff.role_id,
        "is_active": bool(staff.is_active),
        "user_id": staff.user_id,
        "has_pin": bool(staff.pin_hash),
        "last_seen_at": staff.last_seen_at,
        "last_synced_at": staff.last_synced_at,
        "removed_at": staff.removed_at,
    }


# ── People ──────────────────────────────────────────────────────────────────


@router.get("/api/organizations/{org_id}/people")
def list_people(
    org_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _scoped(user, org_id, "org:members", db)
    return staff_directory.list_people(db, org_id)


class StaffAccessBody(BaseModel):
    enabled: bool


@router.put("/api/organizations/{org_id}/staff-access")
def set_staff_access(
    org_id: str,
    body: StaffAccessBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Switch staff access on or off. Switching it on creates the venue roles
    (Kitchen, Front of house, Head chef, Venue manager) and starts the hourly
    copy from Loaded."""
    from app.services.role_access import ensure_venue_roles

    _scoped(user, org_id, "org:manage", db)
    org = _org_or_404(org_id, db)
    org.staff_access_enabled = bool(body.enabled)
    if body.enabled:
        ensure_venue_roles(db, org_id)
    db.commit()
    db.refresh(org)
    return {"sync": staff_directory.sync_status(db, org)}


@router.post("/api/organizations/{org_id}/staff/sync")
def sync_now(
    org_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    _scoped(user, org_id, "org:manage", db)
    org = _org_or_404(org_id, db)
    try:
        summary = staff_directory.sync_org(db, config_db, org_id)
        db.commit()
    except Exception:
        logger.exception("staff sync for %s failed", org_id)
        raise HTTPException(502, "Norm couldn't read the staff list from Loaded.")
    return {"summary": summary.as_dict(), "sync": staff_directory.sync_status(db, org)}


class StaffBody(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    venue_ids: list[str] = []
    role_id: str | None = None


@router.post("/api/organizations/{org_id}/staff")
def add_staff(
    org_id: str,
    body: StaffBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _scoped(user, org_id, "org:members", db)
    try:
        staff = staff_directory.add_manual(
            db,
            org_id,
            name=body.name,
            email=body.email,
            phone=body.phone,
            venue_ids=body.venue_ids,
            role_id=body.role_id,
        )
        db.commit()
    except DirectoryError as exc:
        # Nothing is committed on this path; the request's session is
        # discarded with whatever was flushed.
        raise HTTPException(400, str(exc))
    db.refresh(staff)
    return _staff_to_dict(staff)


class StaffUpdateBody(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    venue_ids: list[str] | None = None
    role_id: str | None = None
    is_active: bool | None = None


@router.put("/api/organizations/{org_id}/staff/{staff_id}")
def update_staff(
    org_id: str,
    staff_id: str,
    body: StaffUpdateBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change a person. A Loaded-sourced row takes only ``role_id`` and
    ``is_active``; the rest is Loaded's."""
    _scoped(user, org_id, "org:members", db)
    staff = _staff_or_404(org_id, staff_id, db)
    given = body.model_dump(exclude_unset=True)
    if staff.source == staff_directory.LOADED:
        details = set(given) - {"role_id", "is_active"}
        if details:
            raise HTTPException(
                400,
                "This person comes from Loaded, so their name, contact details "
                "and venues are changed there.",
            )
    try:
        staff_directory.update(db, staff, **given)
        db.commit()
    except DirectoryError as exc:
        # Nothing is committed on this path; the request's session is
        # discarded with whatever was flushed.
        raise HTTPException(400, str(exc))
    db.refresh(staff)
    return _staff_to_dict(staff)


@router.delete("/api/organizations/{org_id}/staff/{staff_id}")
def delete_staff(
    org_id: str,
    staff_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _scoped(user, org_id, "org:members", db)
    staff = _staff_or_404(org_id, staff_id, db)
    source = staff.source
    staff_directory.remove(db, staff)
    db.commit()
    return {"ok": True, "deleted": source != staff_directory.LOADED}


class ImportBody(BaseModel):
    csv: str


@router.post("/api/organizations/{org_id}/staff/import")
def import_staff(
    org_id: str,
    body: ImportBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _scoped(user, org_id, "org:members", db)
    result = staff_directory.import_csv(db, org_id, body.csv)
    db.commit()
    return result.as_dict()


@router.post("/api/organizations/{org_id}/staff/{staff_id}/pin")
def set_staff_pin(
    org_id: str,
    staff_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A new PIN for this person, returned once."""
    _scoped(user, org_id, "org:members", db)
    staff = _staff_or_404(org_id, staff_id, db)
    if not staff.is_active:
        raise HTTPException(400, "This person has left, so they can't have a PIN.")
    pin = staff_directory.set_pin(db, staff)
    db.commit()
    return {"pin": pin}


@router.delete("/api/organizations/{org_id}/staff/{staff_id}/pin")
def clear_staff_pin(
    org_id: str,
    staff_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _scoped(user, org_id, "org:members", db)
    staff = _staff_or_404(org_id, staff_id, db)
    staff_directory.clear_pin(db, staff)
    db.commit()
    return {"ok": True}


# ── Me ──────────────────────────────────────────────────────────────────────


def _my_staff_record(user: User, db: Session) -> StaffMember:
    """The caller's staff record in the active organisation — the same lookup
    ``/auth/me`` reports (their own row, else an unclaimed row with their
    email: a manager who is also on the roster), linked to them here so the
    next call is direct."""
    org_id = active_org.org_id_for(db, user.id)
    staff = staff_directory.staff_record_for(db, user, org_id, link=True)
    if staff is None:
        raise HTTPException(404, "You don't have a staff record in this organisation.")
    return staff


@router.post("/api/me/pin")
def set_my_pin(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    staff = _my_staff_record(user, db)
    pin = staff_directory.set_pin(db, staff)
    db.commit()
    return {"pin": pin}


@router.delete("/api/me/pin")
def clear_my_pin(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    staff = _my_staff_record(user, db)
    staff_directory.clear_pin(db, staff)
    db.commit()
    return {"ok": True}


def _today_items(db: Session, config_db: Session, user: User) -> list[dict]:
    """What's due for this person today: their outstanding training, read
    through the Training app's own logic (its ``member`` view), when the app
    is on and they have a people record. Anything that goes wrong reads as
    nothing due — Home must never fail over its Today block."""
    from app.db.models import AppRecord
    from app.services.app_runtime import run_logic
    from app.services.builtin_apps import bind, get_builtin, is_on

    org_id = active_org.org_id_for(db, user.id)
    if not org_id or not is_on("training", org_id, db, config_db):
        return []
    person = (
        db.query(AppRecord)
        .filter(
            AppRecord.namespace == staff_directory.PEOPLE_NAMESPACE,
            AppRecord.collection == staff_directory.PEOPLE_COLLECTION,
            AppRecord.organization_id == org_id,
            AppRecord.data["norm_user_id"].astext == user.id,
        )
        .first()
    )
    if person is None:
        return []
    b = get_builtin("training")
    if b is None:
        return []
    app, version = bind(b, org_id)
    venues = [
        {"id": v.id, "name": v.name}
        for v in db.query(Venue).filter(Venue.organization_id == org_id).all()
    ]
    out = run_logic(
        db,
        config_db,
        app=app,
        version=version,
        user=user,
        venue_id=None,
        params={"op": "member", "person_id": person.id, "venues": venues},
    )
    if not out.get("success"):
        logger.warning("today: training member view failed: %s", out.get("error"))
        return []
    data = out.get("data") or {}
    today = datetime.now(timezone.utc).date().isoformat()
    items: list[dict] = []
    for a in data.get("assignments") or []:
        if a.get("status") == "completed":
            continue
        percent = a.get("percent") or 0
        detail = (f"{percent}% done" if percent else "Not started") + (
            f" · {a['instance']}" if a.get("instance") else ""
        )
        items.append(
            {
                "kind": "training",
                "title": a.get("program") or "Training",
                "detail": detail,
                "due_at": None,
                "tone": "info",
                "app_slug": "training",
                "page_id": "training",
            }
        )
    for plan in data.get("plans") or []:
        for section in plan.get("sections") or []:
            if section.get("status") == "completed":
                continue
            due = section.get("due_date")
            items.append(
                {
                    "kind": "training",
                    "title": section.get("name") or "Training section",
                    "detail": plan.get("program") or "Training plan",
                    "due_at": due,
                    "tone": "overdue" if due and due < today else "due",
                    "app_slug": "training",
                    "page_id": "training",
                }
            )
    items.sort(key=lambda i: (i["due_at"] is None, i["due_at"] or ""))
    return items


@router.get("/api/me/today")
def my_today(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    try:
        return {"items": _today_items(db, config_db, user)}
    except Exception:  # noqa: BLE001 — see _today_items
        logger.exception("today items failed for user %s", user.id)
        return {"items": []}


# ── Tablets ─────────────────────────────────────────────────────────────────


def _device_to_dict(device: VenueDevice, venue: Venue | None = None) -> dict:
    membership_role = None
    if device.user is not None:
        for m in device.user.memberships:
            if m.organization_id == device.organization_id:
                membership_role = m.role_id
    return {
        "id": device.id,
        "name": device.name,
        "venue_id": device.venue_id,
        "venue_name": (venue or device.venue).name if (venue or device.venue) else None,
        "pin_required": bool(device.pin_required),
        "role_id": membership_role,
        "user_id": device.user_id,
        "created_at": device.created_at,
        "last_seen_at": device.last_seen_at,
        "revoked_at": device.revoked_at,
    }


@router.get("/api/organizations/{org_id}/devices")
def list_devices(
    org_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _scoped(user, org_id, "org:manage", db)
    devices = (
        db.query(VenueDevice)
        .filter(VenueDevice.organization_id == org_id, VenueDevice.revoked_at.is_(None))
        .order_by(VenueDevice.created_at)
        .all()
    )
    return {"devices": [_device_to_dict(d) for d in devices]}


class DeviceBody(BaseModel):
    venue_id: str
    name: str
    role_id: str
    pin_required: bool = True


@router.post("/api/organizations/{org_id}/devices")
def create_device(
    org_id: str,
    body: DeviceBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sign a tablet in as a venue device. Makes the device's own user (a
    membership with ``role_id`` and access to that one venue) and returns its
    token — the only time the token is shown."""
    caller = _scoped(user, org_id, "org:manage", db)
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, "A tablet needs a name, like 'La Zeppa kitchen'.")
    venue = _venue_in_org(org_id, body.venue_id, db)
    role = _device_role(org_id, body.role_id, caller, db)

    device_id = str(uuid.uuid4())
    device_user = User(
        email=f"device-{device_id}@devices.norm.local",
        hashed_password=hash_password(secrets.token_urlsafe(24)),
        full_name=name,
        role="user",
        is_active=True,
        is_device=True,
        password_set=False,
    )
    db.add(device_user)
    db.flush()
    db.add(
        OrganizationMembership(
            user_id=device_user.id,
            organization_id=org_id,
            role="member",
            role_id=role.id,
        )
    )
    db.add(UserVenueAccess(user_id=device_user.id, venue_id=venue.id))
    device = VenueDevice(
        id=device_id,
        organization_id=org_id,
        venue_id=venue.id,
        name=name,
        user_id=device_user.id,
        pin_required=bool(body.pin_required),
        created_by=user.id,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    token = create_access_token(
        {"sub": device_user.id, "type": "device", "device": device.id},
        expires_delta=timedelta(days=settings.DEVICE_TOKEN_DAYS),
    )
    return {"device": _device_to_dict(device, venue), "token": token}


class DeviceUpdateBody(BaseModel):
    name: str | None = None
    pin_required: bool | None = None
    role_id: str | None = None


def _device_or_404(org_id: str, device_id: str, db: Session) -> VenueDevice:
    device = (
        db.query(VenueDevice)
        .filter(VenueDevice.id == device_id, VenueDevice.organization_id == org_id)
        .first()
    )
    if device is None:
        raise HTTPException(404, "Tablet not found")
    return device


@router.put("/api/organizations/{org_id}/devices/{device_id}")
def update_device(
    org_id: str,
    device_id: str,
    body: DeviceUpdateBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    caller = _scoped(user, org_id, "org:manage", db)
    device = _device_or_404(org_id, device_id, db)
    if device.revoked_at is not None:
        raise HTTPException(400, "This tablet has been signed out.")
    if body.name is not None:
        name = " ".join(body.name.split())
        if not name:
            raise HTTPException(400, "A tablet needs a name.")
        device.name = name
        if device.user is not None:
            device.user.full_name = name
    if body.pin_required is not None:
        device.pin_required = bool(body.pin_required)
    if body.role_id is not None:
        role = _device_role(org_id, body.role_id, caller, db)
        membership = (
            db.query(OrganizationMembership)
            .filter(
                OrganizationMembership.user_id == device.user_id,
                OrganizationMembership.organization_id == org_id,
            )
            .first()
        )
        if membership is None:
            membership = OrganizationMembership(
                user_id=device.user_id, organization_id=org_id, role="member"
            )
            db.add(membership)
        membership.role_id = role.id
    db.commit()
    db.refresh(device)
    return _device_to_dict(device)


@router.delete("/api/organizations/{org_id}/devices/{device_id}")
def revoke_device(
    org_id: str,
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sign the tablet out for good: its token stops working at its next
    request and its user is switched off."""
    _scoped(user, org_id, "org:manage", db)
    device = _device_or_404(org_id, device_id, db)
    if device.revoked_at is None:
        device.revoked_at = datetime.now(timezone.utc)
    if device.user is not None:
        device.user.is_active = False
    db.commit()
    return {"ok": True}
