"""Staff sign-in with an emailed code (docs/plans/staff-access.md, 11.5).

Two routes, both open: ``POST /api/auth/staff/code`` emails a code to an
address that is an active staff member somewhere with staff access on, and
answers ``202 {"sent": true}`` whatever happened — not on the list, throttled
or locked all look the same, and the email goes out after the response, so
neither content nor timing tells. ``POST /api/auth/staff/verify`` trades the
code for a month-long session token, making the account and memberships on
the first sign-in (``services/staff_access.claim_account``).
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.auth.schemas import UserResponse
from app.auth.security import create_access_token
from app.config import settings
from app.db.engine import SessionLocal, get_db
from app.db.models import Organization
from app.mcp.ratelimit import check_rate_limit
from app.middleware.rate_limit import limiter
from app.services import active_org, staff_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth/staff", tags=["staff-auth"])

#: Sends allowed per address in fifteen minutes (counted in Postgres, so it
#: holds across API instances; answered quietly).
SENDS_PER_ADDRESS = 3
SEND_WINDOW_SECONDS = 900


class CodeBody(BaseModel):
    email: str


class VerifyBody(BaseModel):
    email: str
    code: str


def _quietly_over(db: Session, bucket: str, limit: int, window: int) -> bool:
    """A limit the caller must not reveal: True when over. Commits."""
    try:
        check_rate_limit(db, bucket, limit, window)
    except HTTPException:
        return True
    return False


def _send_code(issued: staff_access.IssuedCode) -> None:
    """Email the code after the response, on a session of its own (the
    request's is closed by then). The body is kept out of ``email_logs``."""
    from app.services.email_service import send_system_email

    if settings.is_local:
        logger.warning(
            "DEV ONLY (local) — staff sign-in code for %s: %s",
            issued.email,
            issued.code,
        )
    db = SessionLocal()
    try:
        send_system_email(
            "staff_login_code",
            [issued.email],
            {
                "code": issued.code,
                "first_name": (issued.name or "").split(" ")[0] or "there",
                "minutes": int(staff_access.CODE_TTL.total_seconds() // 60),
            },
            db,
            log_body=False,
        )
        db.commit()
    except Exception:  # noqa: BLE001 — a failed email never surfaces to the caller
        logger.exception("staff sign-in code email to %s failed", issued.email)
        db.rollback()
    finally:
        db.close()


@router.post("/code", status_code=202)
@limiter.limit("10/minute")
def request_code(request: Request, body: CodeBody, db: Session = Depends(get_db)):
    """Email a sign-in code. The answer is the same whether or not the address
    is on a staff list, so nobody can test which emails are."""
    email = staff_access.normalise_email(body.email)
    over = email is None or _quietly_over(
        db, f"staff-code:{email}", SENDS_PER_ADDRESS, SEND_WINDOW_SECONDS
    )
    issued = None if over else staff_access.request_code(db, email)
    db.commit()
    if issued is not None:
        return JSONResponse(
            {"sent": True},
            status_code=202,
            background=BackgroundTask(_send_code, issued),
        )
    return {"sent": True}


_BAD_CODE = {
    "code": "bad_code",
    "message": "That code isn't right or has expired. Ask for a new one.",
}


@router.post("/verify")
@limiter.limit("20/minute")
def verify_code(request: Request, body: VerifyBody, db: Session = Depends(get_db)):
    """Check the code; on success sign the person in for a month, making
    their account and memberships if this is their first time."""
    email = staff_access.normalise_email(body.email)
    ok = email is not None and staff_access.verify_code(db, email, body.code)
    db.commit()  # a wrong guess is counted even though we refuse it
    if not ok:
        raise HTTPException(status_code=400, detail=_BAD_CODE)
    user = staff_access.claim_account(db, email)
    if user is None:
        # On a list when the code went out, on none now.
        db.rollback()
        raise HTTPException(status_code=400, detail=_BAD_CODE)
    db.commit()

    token = create_access_token(
        {"sub": user.id, "type": "staff"},
        expires_delta=timedelta(days=settings.STAFF_TOKEN_DAYS),
    )
    memberships = active_org.memberships_for(db, user.id)
    names = {
        o.id: o.name
        for o in db.query(Organization)
        .filter(Organization.id.in_([m.organization_id for m in memberships]))
        .all()
    }
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": UserResponse(
            id=user.id, email=user.email, full_name=user.full_name, role=user.role
        ).model_dump(),
        "organizations": [
            {"id": m.organization_id, "name": names.get(m.organization_id)}
            for m in memberships
        ],
    }
