"""Authentication endpoints: register, login, me."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import User
from app.auth.security import hash_password, verify_password, create_access_token
from app.auth.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse
from app.auth.dependencies import get_current_user
from app.middleware.rate_limit import limiter

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

    # First user gets admin role, rest get manager
    is_first = db.query(User).count() == 0
    role = "admin" if is_first else "manager"

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
async def me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role
    ).model_dump()
