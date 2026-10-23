"""Who is asking, for which organisation, at which venues, holding which
scopes — and whether the module they touch is live.

Every Finance service takes a ``FinanceCtx`` instead of a user, so the
tenancy, venue and permission rules are decided once, here, from membership
(``functions/ctx.py``'s rules, with scopes added):

* **Organisation** — the user's membership (``active_org.membership_for``).
  A platform admin with no membership may name the organisation explicitly.
* **Venues** — the venues of that organisation the user has
  ``UserVenueAccess`` to, matching ``venue_service.get_user_venues`` and
  ``app_runtime._check_venue`` (fail closed: no rows, no venues). Owners and
  admins of the organisation, platform admins and system callers see every
  venue in it, **non-trading ones included** (``venues.is_trading = false`` is
  CBI, the head-office entity, which runs a pay run but never trades).
* **Scopes** — the person's org permissions (``app_runtime.org_permissions``:
  their role's list; a platform admin holds every scope). Routers gate on
  ``require_permission``; services gate again with ``ctx.require`` so a
  built-in or a job cannot skip the rule.
* **Modes** — the organisation's three module modes as they stood when the
  ctx was built (``modes.py``). Writes need ``live`` for their module.

The other callers are built here too, so nobody hand-rolls one: ``for_org``
(jobs and the importer — no user, every venue, every scope) and
``for_performer`` (a performer or band member on a public link — no venues,
no scopes; the services reach only that performer's own records).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.services.finance_core import modes as modes_svc
from app.services.finance_core.errors import Forbidden, NotFound, NotLive

#: Legacy membership roles that mean "runs the organisation".
_OWNER_ROLES = frozenset({"owner", "admin"})

#: The four org scopes the Finance Apps add (DESIGN-CONTRACT §1). Named here
#: so a system ctx holds them whether or not permissions.py lists them yet.
FINANCE_SCOPES = frozenset(
    {"payroll:read", "payroll:write", "finance:read", "budgets:write"}
)

_NOT_LIVE = {
    "shadow": (
        "{label} is in shadow mode for this organisation — Norm computes beside "
        "Orbit but writes nothing until it goes live."
    ),
    "off": "{label} is not switched on for this organisation.",
}
_LABELS = {
    "payroll": "Loaded Payroll",
    "entertainers": "Entertainers",
    "finance": "Norm Finance",
}


@dataclass(frozen=True)
class FinanceCtx:
    org_id: str
    #: The signed-in person, or None for a system or performer caller.
    user_id: str | None
    #: Venues in ``org_id`` this caller may read and write.
    venue_ids: frozenset[str]
    #: Owner/admin of the organisation (or platform admin, or system).
    is_owner: bool
    #: The org permission scopes the caller holds.
    scopes: frozenset[str]
    #: module -> off | shadow | live, as read when the ctx was built.
    modes: Mapping[str, str]
    #: Who acted when it wasn't a person — "Norm" for the agent, "importer",
    #: "tick", "performer". Recorded on facts beside ``user_id``.
    actor_label: str | None = None
    #: A performer on a public link (portal, onboarding, declaration).
    performer_id: str | None = None
    #: A band member on their own onboarding link.
    band_member_id: str | None = None
    user: Any = field(default=None, compare=False, repr=False)

    # --- scopes ------------------------------------------------------------

    def holds(self, scope: str) -> bool:
        return scope in self.scopes

    def require(self, scope: str) -> None:
        """Refuse unless the caller holds ``scope``."""
        if scope not in self.scopes:
            raise Forbidden(f"You need the {scope} permission to do that.")

    # --- modes ---------------------------------------------------------------

    def mode(self, module: str) -> str:
        modes_svc.column(module)  # validates the name
        return self.modes.get(module, "off")

    def is_live(self, module: str) -> bool:
        return self.mode(module) == "live"

    def require_live(self, module: str) -> None:
        """Refuse a write unless the module is live for the organisation."""
        current = self.mode(module)
        if current != "live":
            template = _NOT_LIVE.get(current, _NOT_LIVE["off"])
            raise NotLive(template.format(label=_LABELS[module]))

    # --- venues ----------------------------------------------------------------

    def can_see_venue(self, venue_id: str | None) -> bool:
        """``None`` (an organisation-wide row) is visible to everyone in the org."""
        return venue_id is None or venue_id in self.venue_ids

    def venue_clause(self, column, *, allow_null: bool = False):
        """SQL filter keeping rows at this caller's venues (and, with
        ``allow_null``, organisation-wide rows whose venue is NULL)."""
        clause = column.in_(sorted(self.venue_ids))
        return or_(column.is_(None), clause) if allow_null else clause

    # --- who ----------------------------------------------------------------------

    @property
    def is_performer(self) -> bool:
        return self.performer_id is not None

    @property
    def actor_kind(self) -> str:
        """``staff`` | ``system`` | ``performer`` — recorded on facts."""
        if self.is_performer:
            return "performer"
        if self.user_id:
            return "staff"
        return "system"


def _org(db: Session, org_id: str):
    from app.db.models import Organization

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise NotFound("Organisation not found.")
    return org


def _all_venue_ids(db: Session, org_id: str) -> frozenset[str]:
    from app.db.models import Venue

    return frozenset(
        r[0] for r in db.query(Venue.id).filter(Venue.organization_id == org_id).all()
    )


def _frozen(mapping: dict) -> Mapping[str, str]:
    return MappingProxyType(dict(mapping))


def build_ctx(
    db: Session,
    user,
    *,
    org_id: str | None = None,
    actor_label: str | None = None,
) -> FinanceCtx:
    """The ctx for a signed-in person (router or agent tool).

    ``org_id`` may only differ from the user's own organisation for a
    platform admin; anyone else naming another org gets ``Forbidden``.
    """
    from app.db.models import Role, UserVenueAccess, Venue
    from app.services import active_org
    from app.services.app_runtime import org_permissions

    if user is None:
        raise Forbidden("Sign in to use the Finance pages.")
    is_platform_admin = getattr(user, "role", None) == "admin"

    membership = active_org.membership_for(db, user.id, org_id)
    if org_id and membership is not None and membership.organization_id != org_id:
        membership = None  # a named organisation the user is not in

    if membership is None:
        if not (is_platform_admin and org_id):
            raise Forbidden("You are not a member of an organisation using Finance.")
        resolved_org = org_id
        is_owner = True
    else:
        resolved_org = membership.organization_id
        role_name = None
        if membership.role_id:
            role = db.query(Role).filter(Role.id == membership.role_id).first()
            role_name = role.name if role else None
        is_owner = (
            is_platform_admin or membership.role in _OWNER_ROLES or role_name == "owner"
        )

    org = _org(db, resolved_org)
    if is_owner:
        venue_ids = _all_venue_ids(db, org.id)
    else:
        venue_ids = frozenset(
            r[0]
            for r in db.query(UserVenueAccess.venue_id)
            .join(Venue, Venue.id == UserVenueAccess.venue_id)
            .filter(
                UserVenueAccess.user_id == user.id,
                Venue.organization_id == org.id,
            )
            .all()
        )
    # The person's scopes come from the membership we resolved, not from
    # whichever organisation the request happened to name.
    with active_org.use(org.id):
        scopes = frozenset(org_permissions(db, user))
    if is_platform_admin:
        scopes |= FINANCE_SCOPES
    return FinanceCtx(
        org_id=org.id,
        user_id=user.id,
        venue_ids=venue_ids,
        is_owner=is_owner,
        scopes=scopes,
        modes=_frozen(modes_svc.modes_of(org)),
        actor_label=actor_label,
        user=user,
    )


def for_org(db: Session, org_id: str, *, actor_label: str = "system") -> FinanceCtx:
    """A system ctx — every venue, every scope, no user. For the tick's jobs
    and the importer; never for a request a person or performer made."""
    from app.auth.permissions import ALL_ORG_PERMISSIONS

    org = _org(db, org_id)
    return FinanceCtx(
        org_id=org.id,
        user_id=None,
        venue_ids=_all_venue_ids(db, org.id),
        is_owner=True,
        scopes=frozenset(ALL_ORG_PERMISSIONS) | FINANCE_SCOPES,
        modes=_frozen(modes_svc.modes_of(org)),
        actor_label=actor_label,
    )


def for_performer(
    db: Session,
    org_id: str,
    performer_id: str,
    *,
    band_member_id: str | None = None,
) -> FinanceCtx:
    """A performer's ctx from a public link: no venues, no scopes — the
    services reach only the performer's own records (and, for a band
    member's link, only that member's row). Another organisation's ids never
    resolve."""
    from app.db.entertainers_models import EntBandMember, EntPerformer

    org = _org(db, org_id)
    performer = (
        db.query(EntPerformer.id)
        .filter(EntPerformer.organization_id == org.id, EntPerformer.id == performer_id)
        .first()
    )
    if performer is None:
        raise NotFound("Performer not found.")
    if band_member_id is not None:
        member = (
            db.query(EntBandMember.id)
            .filter(
                EntBandMember.organization_id == org.id,
                EntBandMember.id == band_member_id,
                EntBandMember.band_performer_id == performer_id,
            )
            .first()
        )
        if member is None:
            raise NotFound("Band member not found.")
    return FinanceCtx(
        org_id=org.id,
        user_id=None,
        venue_ids=frozenset(),
        is_owner=False,
        scopes=frozenset(),
        modes=_frozen(modes_svc.modes_of(org)),
        actor_label="performer",
        performer_id=performer_id,
        band_member_id=band_member_id,
    )


def require_venue(ctx: FinanceCtx, venue_id: str | None) -> None:
    """Refuse a venue the caller may not act at. A venue of another
    organisation is never in ``venue_ids``, so this is the cross-org check
    too."""
    if venue_id is not None and venue_id not in ctx.venue_ids:
        raise Forbidden("You do not have access to that venue.")


def require_owner(ctx: FinanceCtx) -> None:
    if not ctx.is_owner:
        raise Forbidden("Only an owner of the organisation can change this.")


def require_staff(ctx: FinanceCtx) -> None:
    """Staff-only actions — not for a performer on a public link."""
    if ctx.is_performer:
        raise Forbidden("Only venue staff can do that.")
