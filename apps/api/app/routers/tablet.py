"""What a kitchen tablet (a venue device) calls: its context and the PIN step
(docs/plans/staff-access.md, sections 5 and 11.6). Routes carry their full
paths; the router has no prefix.

Both routes need a device token — one whose ``type`` is ``device`` and whose
``VenueDevice`` still stands (``auth/dependencies.get_current_user`` checks
the device; this file checks the type, so a person's own token gets 403).
Wrong PINs are counted on the server per device: after five in five minutes
the tablet is locked for the rest of the window, however often the page is
reloaded.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.dependencies import bearer_scheme, get_current_user
from app.auth.security import create_access_token, decode_access_token, hash_password
from app.config import settings
from app.db.engine import get_db
from app.db.models import Organization, StaffMember, User, Venue, VenueDevice
from app.mcp.ratelimit import _window_start, check_rate_limit
from app.services import staff_directory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tablet"])

#: Wrong PINs allowed per device per window before the tablet locks.
PIN_ATTEMPTS = 5
PIN_WINDOW_SECONDS = 300


def _device(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VenueDevice:
    """The venue device this request comes from. ``get_current_user`` has
    already refused a revoked device; this refuses anything that isn't a
    device token at all."""
    try:
        payload = decode_access_token(credentials.credentials)
    except JWTError:
        raise HTTPException(401, "Invalid token")
    if payload.get("type") != "device":
        raise HTTPException(403, "This is for venue tablets only.")
    device = (
        db.query(VenueDevice)
        .filter(
            VenueDevice.id == payload.get("device"),
            VenueDevice.user_id == user.id,
            VenueDevice.revoked_at.is_(None),
        )
        .first()
    )
    if device is None:
        raise HTTPException(401, "Device revoked")
    device.last_seen_at = datetime.now(timezone.utc)
    return device


@router.get("/api/tablet/context")
def tablet_context(
    device: VenueDevice = Depends(_device), db: Session = Depends(get_db)
):
    venue = db.query(Venue).filter(Venue.id == device.venue_id).first()
    org = (
        db.query(Organization).filter(Organization.id == device.organization_id).first()
    )
    db.commit()  # last_seen_at
    return {
        "device": {
            "id": device.id,
            "name": device.name,
            "pin_required": bool(device.pin_required),
            "venue": {"id": device.venue_id, "name": venue.name if venue else None},
            "organization": {
                "id": device.organization_id,
                "name": org.name if org else None,
            },
        }
    }


class PinBody(BaseModel):
    pin: str


def _bucket(device: VenueDevice) -> str:
    return f"tablet-pin:{device.id}"


def _locked(db: Session, device: VenueDevice, now: datetime) -> bool:
    """Has this device already used its wrong PINs for the window? Read
    without counting, so a right PIN after a lock still waits it out."""
    window = _window_start(now, PIN_WINDOW_SECONDS)
    row = db.execute(
        text(
            "SELECT count FROM mcp_rate_limits "
            "WHERE bucket_key = :key AND window_start = :window"
        ),
        {"key": _bucket(device), "window": window},
    ).first()
    return bool(row) and int(row[0]) >= PIN_ATTEMPTS


def _locked_response(now: datetime) -> HTTPException:
    window = _window_start(now, PIN_WINDOW_SECONDS)
    retry_after = int(window.timestamp() + PIN_WINDOW_SECONDS - now.timestamp())
    return HTTPException(
        429,
        "Too many wrong PINs. This tablet is locked for a few minutes.",
        headers={"Retry-After": str(max(retry_after, 1))},
    )


def _user_for_staff(db: Session, staff: StaffMember) -> User:
    """The staff member's Norm account for this tap: the one their row is
    linked to, else a placeholder made now (``staff-<id>@staff.norm.local``,
    no password). Never an account looked up by email: a PIN proves who
    tapped it, not who owns the address a manager typed on their row — so
    resolving by email would hand anyone with org:members a session as any
    existing account (an owner's, or another organisation's) by adding a row
    with that email and setting its PIN. Signing in with a code later adopts
    the placeholder (``staff_access.claim_account``)."""
    from app.services import staff_access

    user = None
    if staff.user_id:
        user = db.query(User).filter(User.id == staff.user_id).first()
        if user is not None and not user.is_active:
            if user.password_set or user.is_device:
                raise HTTPException(400, "That PIN isn't right.")
            user.is_active = True
    if user is None:
        user = User(
            email=staff_directory.placeholder_email(staff),
            hashed_password=hash_password(secrets.token_urlsafe(24)),
            full_name=staff.name,
            role="user",
            is_active=True,
            is_device=False,
            password_set=False,
        )
        db.add(user)
        db.flush()
        staff.user_id = user.id
    staff_access.ensure_membership(db, user, staff)
    db.flush()
    return user


@router.post("/api/tablet/pin")
def tablet_pin(
    body: PinBody,
    device: VenueDevice = Depends(_device),
    db: Session = Depends(get_db),
):
    """Tap a PIN: a short token for that person at this device's venue."""
    now = datetime.now(timezone.utc)
    if _locked(db, device, now):
        db.commit()
        raise _locked_response(now)
    staff = staff_directory.verify_pin(
        db, device.organization_id, device.venue_id, body.pin
    )
    if staff is None:
        # Counted per device; the sixth wrong PIN in the window is the 429.
        check_rate_limit(db, _bucket(device), PIN_ATTEMPTS, PIN_WINDOW_SECONDS, now=now)
        raise HTTPException(400, "That PIN isn't right.")
    user = _user_for_staff(db, staff)
    staff.last_seen_at = now
    db.commit()
    ttl = timedelta(minutes=settings.TABLET_PIN_TTL_MINUTES)
    token = create_access_token(
        {"sub": user.id, "type": "pin", "device": device.id, "staff": staff.id},
        expires_delta=ttl,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int(ttl.total_seconds()),
        "user": {"id": user.id, "full_name": user.full_name},
    }
