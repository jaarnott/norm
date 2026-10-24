"""What a role reaches: which AI agents and which apps a person sees, and so
which tools Norm may use in their chat (docs/plans/staff-access.md, section
11.3).

A role's ``agents`` and ``apps`` columns are NULL for "everything the
organisation has" (every hired agent, every app that is on) or a list naming
exactly which. The one special case is an explicit empty ``agents`` list: that
is venue staff — no AI agent at all — and it is what ``Access.staff`` means.
Platform admins and people with no membership get ``Access(None, None)``: the
App Map alone decides for them, as it did before roles reached this far.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth.permissions import VENUE_ROLES
from app.db.models import Role

#: The catalog App that fronts Norm's own core tools.
CORE_APP_SLUG = "norm-core"


@dataclass(frozen=True)
class Access:
    """What a person's role lets them reach. ``None`` means no limit."""

    agents: frozenset[str] | None
    apps: frozenset[str] | None

    @property
    def staff(self) -> bool:
        """Venue staff: a role that names no AI agent at all."""
        return self.agents == frozenset()


EVERYTHING = Access(None, None)


def _as_set(value) -> frozenset[str] | None:
    if value is None:
        return None
    return frozenset(str(v) for v in value)


def for_role(role: Role | None) -> Access:
    """The access a role gives; a missing role limits nothing."""
    if role is None:
        return EVERYTHING
    return Access(_as_set(role.agents), _as_set(role.apps))


def for_user(db: Session, user_id: str | None, org_id: str | None = None) -> Access:
    """The access of the role a user acts under in ``org_id`` (or the active
    organisation). Platform admins, people with no membership, and roles with
    NULLs all come back as ``Access(None, None)``."""
    from app.db.models import User
    from app.services import active_org

    if not user_id:
        return EVERYTHING
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or user.role == "admin":
        return EVERYTHING
    membership = active_org.membership_for(db, user_id, org_id)
    if membership is None or not membership.role_id:
        return EVERYTHING
    role = db.query(Role).filter(Role.id == membership.role_id).first()
    return for_role(role)


def allows_agent(access: Access, agent_key: str) -> bool:
    return access.agents is None or agent_key in access.agents


def allows_app(access: Access, app_key: str, *, member: str | None = None) -> bool:
    """Whether the role reaches an app. ``app_key`` is a built-in slug
    (``training``), ``custom:<slug>`` for an org-built app, a catalog app
    slug (``loaded-kitchen``) for an agent-level app, or a team app slug
    (``kitchen``, ``front_of_house``, ``managers``; services/screens.
    TEAM_SLUGS) — asked with no member; ``member`` is the agent the app
    belongs to, and an app of an allowed agent is allowed."""
    if access.apps is None or app_key in access.apps:
        return True
    return member is not None and allows_agent(access, member)


def core_allowed(access: Access) -> bool:
    """Norm Core's tools (the ``norm-core`` catalog app) are for everyone but
    venue staff."""
    return not access.staff


def _norm(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


def _mapped_names(role: Role) -> set[str]:
    return {_norm(n) for n in (role.loaded_roles or []) if _norm(n)}


def _org_roles(db: Session, org_id: str) -> list[Role]:
    """The organisation's custom roles first, then the system roles."""
    return (
        db.query(Role)
        .filter((Role.organization_id == org_id) | (Role.organization_id.is_(None)))
        .order_by(Role.is_system.asc(), Role.name)
        .all()
    )


def role_for_loaded_roles(db: Session, org_id: str, names: list[str]) -> Role | None:
    """The role whose ``loaded_roles`` holds one of ``names`` (case- and
    whitespace-insensitive), custom roles before system roles; None when
    nothing matches."""
    wanted = {_norm(n) for n in (names or []) if _norm(n)}
    if not wanted:
        return None
    for role in _org_roles(db, org_id):
        if wanted & _mapped_names(role):
            return role
    return None


def loaded_role_conflict(
    db: Session, org_id: str, names: list[str], *, exclude_role_id: str | None = None
) -> str | None:
    """The first of ``names`` already mapped to another role in this
    organisation, as ``"'Chef' already maps to Kitchen"``; None when free."""
    taken: dict[str, Role] = {}
    for role in _org_roles(db, org_id):
        if role.id == exclude_role_id:
            continue
        for key in _mapped_names(role):
            taken.setdefault(key, role)
    for name in names or []:
        other = taken.get(_norm(name))
        if other is not None:
            return f"'{str(name).strip()}' already maps to {other.display_name}"
    return None


def ensure_venue_roles(db: Session, org_id: str) -> list[Role]:
    """The four venue roles (VENUE_ROLES) as ordinary custom roles of the
    organisation, created when missing and left alone when present, so an
    owner's edits survive. Flushed, not committed."""
    existing = {
        r.name: r for r in db.query(Role).filter(Role.organization_id == org_id).all()
    }
    # A Loaded role the owner already mapped to a role of their own stays
    # theirs: a shipped guess that collided would make the new role unsaveable
    # in the editor ("'Chef' already maps to Cooks") until the chip was
    # removed by hand.
    taken = {name for r in existing.values() for name in _mapped_names(r)}
    out: list[Role] = []
    for name, defn in VENUE_ROLES.items():
        role = existing.get(name)
        if role is None:
            loaded_roles = [n for n in defn["loaded_roles"] if _norm(n) not in taken]
            taken.update(_norm(n) for n in loaded_roles)
            role = Role(
                organization_id=org_id,
                name=name,
                display_name=defn["display_name"],
                description=defn.get("description"),
                is_system=False,
                permissions=list(defn["permissions"]),
                agents=None if defn["agents"] is None else list(defn["agents"]),
                apps=None if defn["apps"] is None else list(defn["apps"]),
                loaded_roles=loaded_roles,
            )
            db.add(role)
        out.append(role)
    db.flush()
    return out
