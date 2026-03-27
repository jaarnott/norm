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
)
from app.auth.security import hash_password, verify_password, create_access_token
from app.auth.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse
from app.auth.dependencies import get_current_user, require_permission
from app.auth.permissions import ALL_ORG_PERMISSIONS
from app.middleware.rate_limit import limiter

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

    if user.role == "admin":
        # Platform admins get all org permissions
        permissions = list(ALL_ORG_PERMISSIONS)
    else:
        # Load membership -> role -> permissions
        membership = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == user.id)
            .first()
        )
        if membership and membership.role_id:
            role_obj = db.query(Role).filter(Role.id == membership.role_id).first()
            if role_obj:
                permissions = role_obj.permissions or []
                org_role = {
                    "name": role_obj.name,
                    "display_name": role_obj.display_name,
                }

    resp = UserResponse(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role
    ).model_dump()
    resp["permissions"] = permissions
    resp["org_role"] = org_role
    return resp


# ── Invite ──────────────────────────────────────────────────────


class InviteRequest(BaseModel):
    email: str
    org_id: str
    role_id: str
    venue_ids: list[str] = []


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
        membership = OrganizationMembership(
            user_id=target_user.id,
            organization_id=body.org_id,
            role="member",
            role_id=body.role_id,
        )
        db.add(membership)
    else:
        membership.role_id = body.role_id

    # Set venue access
    db.query(UserVenueAccess).filter(UserVenueAccess.user_id == target_user.id).delete()
    for vid in body.venue_ids:
        db.add(UserVenueAccess(user_id=target_user.id, venue_id=vid))

    db.commit()

    # Generate invite token and send email
    token = create_invite_token(target_user.id)
    invite_url = f"{app_settings.APP_URL}/accept-invite?token={token}"

    org = db.query(Organization).filter(Organization.id == body.org_id).first()
    org_name = org.name if org else "Norm"
    inviter_name = user.full_name

    email_log_id = None
    try:
        email_log_id = send_system_email(
            template_name="invite_user",
            to=[email],
            context={
                "invite_url": invite_url,
                "inviter_name": inviter_name,
                "org_name": org_name,
                "email": email,
            },
            db=db,
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
        reset_url = f"{app_settings.APP_URL}/reset-password?token={token}"

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
