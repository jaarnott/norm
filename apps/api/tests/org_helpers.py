"""Organisation, venue, user and role helpers for the tenancy and permission
tests (test_scoped_routes, test_caller_scope, test_org_scoped_access,
test_permissions, test_email_permissions) — each file used to carry its own
copy of these.

Plain functions, like the ``_make_*`` helpers in conftest.py; the users made
here never log in with a password (tests sign a token with ``auth_headers``).
"""

import uuid

from app.auth.permissions import STANDARD_ROLES
from app.auth.security import create_access_token
from app.db.models import Organization, Role, User, UserVenueAccess, Venue


def make_org(db, name):
    o = Organization(id=str(uuid.uuid4()), name=name, slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(o)
    db.flush()
    return o


def make_venue(db, name, org):
    v = Venue(id=str(uuid.uuid4()), name=name, organization_id=org.id)
    db.add(v)
    db.flush()
    return v


def make_user(db, role="user"):
    u = User(
        id=str(uuid.uuid4()),
        email=f"u-{uuid.uuid4().hex[:8]}@x.com",
        hashed_password="x",
        full_name="U",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def grant_venue(db, user, venue):
    """Give a user access to a venue (a UserVenueAccess row)."""
    db.add(UserVenueAccess(id=str(uuid.uuid4()), user_id=user.id, venue_id=venue.id))
    db.flush()


def auth_headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def seed_system_roles(db):
    """Insert the standard system roles (owner, manager, team member, payroll
    admin). Returns dict[name, Role]."""
    roles = {}
    for name, defn in STANDARD_ROLES.items():
        role = Role(
            id=str(uuid.uuid4()),
            name=name,
            display_name=defn["display_name"],
            description=defn["description"],
            is_system=True,
            permissions=defn["permissions"],
            agents=defn["agents"],
            apps=defn["apps"],
            organization_id=None,
        )
        db.add(role)
        roles[name] = role
    db.flush()
    return roles
