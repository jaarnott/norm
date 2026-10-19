"""Organisations page for platform admins (Oct 2026).

Every organisation with its venues and people; create and delete organisations;
change a member's role and venues within one organisation. Adding a person goes
through the normal invite (POST /api/auth/invite), removing one through
DELETE /api/organizations/{id}/members/{user}, and venues through the usual
venue routes, so each rule lives in one place.

Platform admins only. Creating an organisation here never makes the admin a
member of it: they manage its shape, they don't get into its data.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_db
from app.db.models import User
from app.services import org_admin

router = APIRouter(tags=["admin"])


def _refused(e: org_admin.OrgAdminError):
    return HTTPException(e.status, str(e))


@router.get("/admin/organizations")
def list_all(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    return org_admin.overview(db)


class CreateOrg(BaseModel):
    name: str
    timezone: str | None = None


@router.post("/admin/organizations")
def create(
    body: CreateOrg,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    try:
        org = org_admin.create_organization(db, body.name, timezone=body.timezone)
    except org_admin.OrgAdminError as e:
        raise _refused(e)
    db.commit()
    return {"id": org.id, "name": org.name, "slug": org.slug}


@router.delete("/admin/organizations/{org_id}")
def delete(
    org_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    try:
        out = org_admin.delete_organization(db, org_id)
    except org_admin.OrgAdminError as e:
        db.rollback()
        raise _refused(e)
    db.commit()
    return out


class MemberAccess(BaseModel):
    role_id: str | None = None
    venue_ids: list[str] | None = None


@router.put("/admin/organizations/{org_id}/members/{member_id}")
def set_member(
    org_id: str,
    member_id: str,
    body: MemberAccess,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    try:
        org_admin.set_member_access(
            db, org_id, member_id, role_id=body.role_id, venue_ids=body.venue_ids
        )
    except org_admin.OrgAdminError as e:
        db.rollback()
        raise _refused(e)
    db.commit()
    return {"ok": True}
