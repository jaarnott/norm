"""Authentication endpoints: register, login, me, invite, password reset."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import (
    User,
    OrganizationMembership,
    UserVenueAccess,
    Organization,
    Role,
    Venue,
)
from app.auth.security import hash_password, verify_password, create_access_token
from app.auth.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse
from app.auth.dependencies import get_current_user, require_permission
from app.auth.permissions import ALL_ORG_PERMISSIONS
from app.middleware.rate_limit import limiter
from app.services import active_org

logger = logging.getLogger(__name__)
router = APIRouter()


def _make_token_response(user: User) -> dict:
    token = create_access_token({"sub": user.id})
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user.id, email=user.email, full_name=user.full_name, role=user.role
        ),
    ).model_dump()


@router.post("/auth/register")
@limiter.limit("5/minute")
async def register(
    request: Request, req: RegisterRequest, db: Session = Depends(get_db)
):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    from app.services import org_admin

    if not req.organization_name.strip() or not req.venue_name.strip():
        raise HTTPException(
            status_code=422, detail="Name your business and its first venue"
        )

    # First user gets admin role, rest get user
    is_first = db.query(User).count() == 0
    role = "admin" if is_first else "user"

    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role=role,
    )
    db.add(user)
    db.flush()

    # Your own organisation, you as its owner, and its first venue — one
    # transaction, so a failure leaves no half-made account behind.
    org = org_admin.create_organization(
        db,
        req.organization_name,
        owner=user,
        billing_email=req.email,
        timezone=req.venue_timezone,
    )
    org_admin.create_venue(
        db,
        org.id,
        req.venue_name,
        location=req.venue_location,
        timezone=req.venue_timezone,
        grant_to=user,
    )
    db.commit()
    db.refresh(user)
    return _make_token_response(user)


@router.post("/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account disabled"
        )
    return _make_token_response(user)


@router.get("/auth/me")
async def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    permissions: list[str] = []
    org_role: dict | None = None

    memberships = active_org.memberships_for(db, user.id)
    active = active_org.membership_for(db, user.id)
    role_obj: Role | None = None
    if user.role == "admin":
        # Platform admins get all org permissions
        permissions = list(ALL_ORG_PERMISSIONS)
    if active and active.role_id:
        role_obj = db.query(Role).filter(Role.id == active.role_id).first()
        if role_obj:
            if user.role != "admin":
                permissions = role_obj.permissions or []
            org_role = {
                "name": role_obj.name,
                "display_name": role_obj.display_name,
            }

    # Every organisation the person can act in, for the switcher.
    from app.db.models import Organization

    orgs = {
        o.id: o.name
        for o in db.query(Organization)
        .filter(Organization.id.in_([m.organization_id for m in memberships]))
        .all()
    }
    resp = UserResponse(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role
    ).model_dump()
    resp["permissions"] = permissions
    resp["org_role"] = org_role
    resp["organizations"] = [
        {"id": m.organization_id, "name": orgs.get(m.organization_id)}
        for m in memberships
    ]
    resp["active_organization_id"] = active.organization_id if active else None

    # Staff access (docs/plans/staff-access.md, 11.5): what the role reaches
    # in the active organisation, whether this is a kitchen tablet's account,
    # and the person's staff record there, if any (claimed by them, or an
    # unclaimed one with their email — a manager who is also on the roster).
    from app.services import role_access
    from app.services.staff_directory import staff_record_for

    access = (
        role_access.EVERYTHING
        if user.role == "admin"
        else role_access.for_role(role_obj)
    )
    resp["access"] = {
        "agents": None if access.agents is None else sorted(access.agents),
        "apps": None if access.apps is None else sorted(access.apps),
        "staff": access.staff,
    }
    resp["is_device"] = bool(user.is_device)
    staff = staff_record_for(db, user, active.organization_id if active else None)
    resp["staff"] = (
        {
            "organization_id": staff.organization_id,
            "staff_member_id": staff.id,
            "venue_ids": list(staff.venue_ids or []),
            "has_pin": bool(staff.pin_hash),
        }
        if staff
        else None
    )
    return resp


# ── Invite ──────────────────────────────────────────────────────


class InviteRequest(BaseModel):
    email: str
    org_id: str
    #: Omitted on a resend: an existing member keeps their role and venues.
    role_id: str | None = None
    venue_ids: list[str] | None = None


@router.post("/auth/invite")
def invite_user(
    body: InviteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("org:members")),
):
    """Invite a new user by email. Creates inactive account, membership, venue access, sends invite email."""
    from app.auth.security import create_invite_token
    from app.services.email_service import send_system_email
    from app.config import settings as app_settings

    email = body.email.strip().lower()

    # The caller must hold org:members IN the organisation they are inviting to
    # (the dependency above checks the organisation they are acting in).
    from app.routers.organizations import _require_org_permission

    _require_org_permission(db, user, body.org_id, "org:members")

    # Check if user already exists
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        target_user = existing
    else:
        # Create inactive user (no password — they'll set it on accept)
        target_user = User(
            email=email,
            hashed_password=hash_password(str(uuid.uuid4())),  # random, unusable
            full_name=email.split("@")[0],  # placeholder
            role="user",
            is_active=False,
        )
        db.add(target_user)
        db.flush()

    # Ensure membership exists
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == target_user.id,
            OrganizationMembership.organization_id == body.org_id,
        )
        .first()
    )

    if not membership:
        if not body.role_id:
            raise HTTPException(
                status_code=422, detail="Choose a role for the new member"
            )
        membership = OrganizationMembership(
            user_id=target_user.id,
            organization_id=body.org_id,
            role="member",
            role_id=body.role_id,
        )
        db.add(membership)
    elif body.role_id:
        membership.role_id = body.role_id

    # Set venue access — for THIS organisation only. The invite used to delete
    # every venue row the person had, in every organisation, so inviting an
    # existing user to a second organisation silently locked them out of the
    # first. Venues named must belong to the organisation they are invited to.
    org_venue_ids = {
        v.id for v in db.query(Venue).filter(Venue.organization_id == body.org_id)
    }
    venue_ids = body.venue_ids
    stray = [vid for vid in (venue_ids or []) if vid not in org_venue_ids]
    if stray:
        db.rollback()
        raise HTTPException(
            status_code=422, detail="A venue named does not belong to that organisation"
        )
    if venue_ids is not None:  # None = leave their access as it is
        db.query(UserVenueAccess).filter(
            UserVenueAccess.user_id == target_user.id,
            UserVenueAccess.venue_id.in_(org_venue_ids),
        ).delete(synchronize_session=False)
        for vid in venue_ids:
            db.add(UserVenueAccess(user_id=target_user.id, venue_id=vid))

    db.commit()

    org = db.query(Organization).filter(Organization.id == body.org_id).first()
    org_name = org.name if org else "Norm"
    inviter_name = user.full_name

    # Someone who already has a working account has nothing to accept (the accept
    # step refuses an active account): tell them they were added and where the
    # switcher is, instead of sending a link that can only fail.
    if target_user.is_active:
        template, context = (
            "added_to_organization",
            {
                "inviter_name": inviter_name,
                "org_name": org_name,
                "app_url": app_settings.app_url,
                "email": email,
            },
        )
    else:
        token = create_invite_token(target_user.id)
        template, context = (
            "invite_user",
            {
                "invite_url": f"{app_settings.app_url}/accept-invite?token={token}",
                "inviter_name": inviter_name,
                "org_name": org_name,
                "email": email,
            },
        )

    email_log_id = None
    try:
        email_log_id = send_system_email(
            template_name=template,
            to=[email],
            context=context,
            db=db,
            organization_id=body.org_id,
        )
        db.commit()
    except Exception as exc:
        logger.exception("Failed to send invite email to %s: %s", email, exc)

    return {"ok": True, "user_id": target_user.id, "email_log_id": email_log_id}


# ── Accept Invite ───────────────────────────────────────────────


class AcceptInviteRequest(BaseModel):
    token: str
    full_name: str
    password: str


@router.post("/auth/accept-invite")
def accept_invite(body: AcceptInviteRequest, db: Session = Depends(get_db)):
    """Accept an invite — set name and password, activate account."""
    from app.auth.security import decode_typed_token

    try:
        payload = decode_typed_token(body.token, "invite")
    except ValueError as e:
        raise HTTPException(400, str(e))

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(400, "User not found")

    if user.is_active:
        raise HTTPException(
            400,
            "This invite has already been used. Please log in instead.",
        )

    user.full_name = body.full_name
    user.hashed_password = hash_password(body.password)
    user.is_active = True
    db.commit()

    return {"ok": True, "email": user.email}


# ── Forgot Password ────────────────────────────────────────────


class ForgotPasswordRequest(BaseModel):
    email: str


@router.post("/auth/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Send a password reset email. Always returns 200 (don't leak whether email exists)."""
    from app.auth.security import create_reset_token
    from app.services.email_service import send_system_email
    from app.config import settings as app_settings

    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()

    if user and user.is_active:
        token = create_reset_token(user.id)
        reset_url = f"{app_settings.app_url}/reset-password?token={token}"

        try:
            send_system_email(
                template_name="password_reset",
                to=[email],
                context={
                    "reset_url": reset_url,
                    "full_name": user.full_name,
                },
                db=db,
            )
            db.commit()
        except Exception as exc:
            logger.exception("Failed to send reset email to %s: %s", email, exc)

    # Always return 200 to not leak email existence
    return {"ok": True, "message": "If that email exists, a reset link has been sent."}


# ── Reset Password ──────────────────────────────────────────────


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


@router.post("/auth/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset password using a valid reset token."""
    from app.auth.security import decode_typed_token

    try:
        payload = decode_typed_token(body.token, "reset")
    except ValueError as e:
        raise HTTPException(400, str(e))

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(400, "User not found")

    user.hashed_password = hash_password(body.password)
    db.commit()

    return {"ok": True}
