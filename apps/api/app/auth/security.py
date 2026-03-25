"""Password hashing and JWT token management."""

from datetime import datetime, timezone, timedelta

from passlib.context import CryptContext
from jose import jwt, JWTError

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

JWT_SECRET = settings.JWT_SECRET
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def create_invite_token(user_id: str) -> str:
    """Create a JWT invite token (7-day expiry)."""
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    return jwt.encode(
        {"sub": user_id, "type": "invite", "exp": expire},
        settings.JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def create_reset_token(user_id: str) -> str:
    """Create a JWT password reset token (1-hour expiry)."""
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode(
        {"sub": user_id, "type": "reset", "exp": expire},
        settings.JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def decode_typed_token(token: str, expected_type: str) -> dict:
    """Decode and validate a typed JWT token. Returns payload or raises ValueError."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise ValueError(f"Invalid or expired token: {e}")
    if payload.get("type") != expected_type:
        raise ValueError(f"Invalid token type: expected {expected_type}")
    return payload
