"""FastAPI dependencies for authentication and authorization."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError

from app.db.engine import get_db
from app.db.models import User
from app.auth.security import decode_access_token

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


def require_role(*roles: str):
    def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return user

    return role_checker


def require_permission(*permissions: str):
    """Check user has ALL specified permissions.

    - admin:* scopes -> checks User.role == "admin"
    - org scopes -> loads membership -> role -> permissions
    - Platform admins (User.role == "admin") bypass org checks
    """

    def checker(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        from app.auth.permissions import PLATFORM_ADMIN_SCOPES

        needed = set(permissions)

        # Platform admin scopes
        admin_needed = needed & PLATFORM_ADMIN_SCOPES
        if admin_needed:
            if user.role != "admin":
                raise HTTPException(403, "Platform admin required")
            needed -= admin_needed

        if not needed:
            return user

        # Platform admins bypass org permission checks
        if user.role == "admin":
            return user

        # Load membership -> role -> permissions
        from app.db.models import OrganizationMembership, Role

        membership = (
            db.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == user.id)
            .first()
        )

        if not membership or not membership.role_id:
            raise HTTPException(403, "No organization role assigned")

        role = db.query(Role).filter(Role.id == membership.role_id).first()
        if not role:
            raise HTTPException(403, "Role not found")

        user_perms = set(role.permissions or [])
        missing = needed - user_perms
        if missing:
            raise HTTPException(
                403, f"Missing permissions: {', '.join(sorted(missing))}"
            )

        return user

    return checker
