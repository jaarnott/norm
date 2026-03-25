"""Role management and permission listing endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import Organization, OrganizationMembership, Role, User
from app.auth.dependencies import get_current_user, require_permission
from app.auth.permissions import (
    PERMISSION_SCOPES,
    PERMISSION_GROUPS,
)

router = APIRouter()


# --- Helpers ---


def _require_org_membership(
    user: User, org_id: str, db: Session
) -> OrganizationMembership | None:
    """Return membership or None. Platform admins get a pass."""
    if user.role == "admin":
        return None  # platform admin bypass
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == org_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(403, "Not a member of this organization")
    return membership


def _get_org_or_404(org_id: str, db: Session) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    return org


def _role_to_dict(role: Role) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "display_name": role.display_name,
        "description": role.description,
        "is_system": role.is_system,
        "permissions": role.permissions or [],
        "organization_id": role.organization_id,
        "created_at": role.created_at.isoformat() if role.created_at else None,
    }


# --- Permission catalog ---


@router.get("/permissions")
async def list_permissions(user: User = Depends(get_current_user)):
    """Return all permission scopes and their groupings."""
    return {
        "permissions": sorted(PERMISSION_SCOPES),
        "groups": PERMISSION_GROUPS,
    }


# --- Role CRUD (org-scoped) ---


@router.get("/organizations/{org_id}/roles")
async def list_roles(
    org_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("org:read")),
):
    """List system roles + org custom roles."""
    _get_org_or_404(org_id, db)
    _require_org_membership(user, org_id, db)

    roles = (
        db.query(Role)
        .filter((Role.organization_id == org_id) | (Role.organization_id.is_(None)))
        .order_by(Role.is_system.desc(), Role.name)
        .all()
    )
    return {"roles": [_role_to_dict(r) for r in roles]}


class CreateRoleBody(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    permissions: list[str] = []


@router.post("/organizations/{org_id}/roles")
async def create_role(
    org_id: str,
    body: CreateRoleBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("org:roles")),
):
    """Create a custom role for the organization."""
    _get_org_or_404(org_id, db)
    _require_org_membership(user, org_id, db)

    # Validate permissions
    invalid = set(body.permissions) - PERMISSION_SCOPES
    if invalid:
        raise HTTPException(400, f"Invalid permissions: {', '.join(sorted(invalid))}")

    # Check name uniqueness within org
    existing = (
        db.query(Role)
        .filter(Role.organization_id == org_id, Role.name == body.name)
        .first()
    )
    if existing:
        raise HTTPException(400, f"Role '{body.name}' already exists in this org")

    role = Role(
        organization_id=org_id,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        is_system=False,
        permissions=body.permissions,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return _role_to_dict(role)


class UpdateRoleBody(BaseModel):
    display_name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None


@router.put("/organizations/{org_id}/roles/{role_id}")
async def update_role(
    org_id: str,
    role_id: str,
    body: UpdateRoleBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("org:roles")),
):
    """Update a custom role (system roles cannot be modified)."""
    _get_org_or_404(org_id, db)
    _require_org_membership(user, org_id, db)

    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(404, "Role not found")
    if role.is_system:
        raise HTTPException(400, "System roles cannot be modified")
    if role.organization_id != org_id:
        raise HTTPException(403, "Role does not belong to this organization")

    if body.permissions is not None:
        invalid = set(body.permissions) - PERMISSION_SCOPES
        if invalid:
            raise HTTPException(
                400, f"Invalid permissions: {', '.join(sorted(invalid))}"
            )
        role.permissions = body.permissions
    if body.display_name is not None:
        role.display_name = body.display_name
    if body.description is not None:
        role.description = body.description

    db.commit()
    db.refresh(role)
    return _role_to_dict(role)


@router.delete("/organizations/{org_id}/roles/{role_id}")
async def delete_role(
    org_id: str,
    role_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("org:roles")),
):
    """Delete a custom role (must not be system, must not be in use)."""
    _get_org_or_404(org_id, db)
    _require_org_membership(user, org_id, db)

    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(404, "Role not found")
    if role.is_system:
        raise HTTPException(400, "System roles cannot be deleted")
    if role.organization_id != org_id:
        raise HTTPException(403, "Role does not belong to this organization")

    # Check if any memberships use this role
    in_use = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.role_id == role_id)
        .first()
    )
    if in_use:
        raise HTTPException(400, "Role is currently assigned to members")

    db.delete(role)
    db.commit()
    return {"ok": True}


# --- Member role assignment ---


class AssignRoleBody(BaseModel):
    role_id: str


@router.put("/organizations/{org_id}/members/{user_id}/role")
async def assign_member_role(
    org_id: str,
    user_id: str,
    body: AssignRoleBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("org:members")),
):
    """Assign a role to a member of the organization."""
    _get_org_or_404(org_id, db)
    _require_org_membership(user, org_id, db)

    # Verify the role exists and is available to this org
    role = db.query(Role).filter(Role.id == body.role_id).first()
    if not role:
        raise HTTPException(404, "Role not found")
    if role.organization_id is not None and role.organization_id != org_id:
        raise HTTPException(403, "Role does not belong to this organization")

    # Find the membership
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(404, "User is not a member of this organization")

    membership.role_id = body.role_id
    db.commit()
    return {"ok": True, "role_id": body.role_id}
