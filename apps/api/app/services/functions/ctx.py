"""Who is asking, for which organisation, at which venues — and may they write.

Every Functions service takes a ``FunctionsCtx`` instead of a user, so the
tenancy and venue rules are decided once, here, from membership:

* **Organisation** — the user's membership (``org_id_for_user``: orgs are
  single-membership today). A platform admin with no membership may name the
  organisation explicitly.
* **Venues** — the venues of that organisation the user has
  ``UserVenueAccess`` to, matching ``venue_service.get_user_venues`` and
  ``app_runtime._check_venue`` (fail closed: no rows, no venues). Owners and
  admins of the organisation (and platform admins) see every venue in it:
  running Functions for the group is their job, and Orbit's managers worked
  across venues.
* **Mode** — the organisation's ``functions_mode``. Writes need ``live``.

The other callers are built here too, so nobody hand-rolls one: ``for_org``
(importer, scheduler, mailbox sync — no user, every venue), ``for_client`` (a
customer in the portal — their one event, their verified email),
``for_quote`` (a customer on the quote page — their one enquiry) and
``build_ctx(..., actor_label="Norm")`` for the agent acting as the signed-in
user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.services.functions.errors import Forbidden, Invalid, NotFound, NotLive

#: Legacy membership roles that mean "runs the organisation".
_OWNER_ROLES = frozenset({"owner", "admin"})


@dataclass(frozen=True)
class FunctionsCtx:
    org_id: str
    #: The signed-in person, or None for a system or client caller.
    user_id: str | None
    #: Venues in ``org_id`` this caller may read and write.
    venue_ids: frozenset[str]
    #: Owner/admin of the organisation (or platform admin, or system).
    is_owner: bool
    #: The organisation's functions_mode when the ctx was built.
    mode: str
    #: Who acted when it wasn't a person — "Norm" for the agent, "importer",
    #: "mailbox sync", "customer". Recorded on facts beside ``user_id``.
    actor_label: str | None = None
    #: A customer's verified email (portal callers only).
    client_email: str | None = None
    #: A customer ctx reaches ONLY the record its token resolved to: the
    #: portal's event (and its menus/children), or the quote page's enquiry
    #: (and its options). Enforced in every load, so a wrong id passed by a
    #: public route is NotFound rather than another customer's booking.
    client_event_id: str | None = None
    client_enquiry_id: str | None = None
    user: Any = field(default=None, compare=False, repr=False)

    @property
    def is_client(self) -> bool:
        return bool(self.client_email or self.client_event_id or self.client_enquiry_id)

    @property
    def actor_kind(self) -> str:
        """``client`` | ``staff`` | ``system`` — the ACTOR_KINDS on facts."""
        if self.is_client:
            return "client"
        if self.user_id:
            return "staff"
        return "system"

    def owns(self, model, row) -> bool:
        """Whether a customer ctx may touch ``row`` (always True for staff
        and system callers). A row belongs to the customer's record when it
        IS that event/enquiry or carries its id."""
        if self.client_event_id is not None:
            owner = (
                row.id
                if model.__tablename__ == "fn_events"
                else getattr(row, "event_id", None)
            )
            if owner != self.client_event_id:
                return False
        if self.client_enquiry_id is not None:
            owner = (
                row.id
                if model.__tablename__ == "fn_enquiries"
                else getattr(row, "enquiry_id", None)
            )
            if owner != self.client_enquiry_id:
                return False
        return True

    def record_clause(self, model):
        """SQL twin of ``owns`` for list queries (``true()`` for staff)."""
        from sqlalchemy import and_, false, true

        clauses = []
        for wanted, table, col in (
            (self.client_event_id, "fn_events", "event_id"),
            (self.client_enquiry_id, "fn_enquiries", "enquiry_id"),
        ):
            if wanted is None:
                continue
            column = (
                model.id if model.__tablename__ == table else getattr(model, col, None)
            )
            clauses.append(false() if column is None else column == wanted)
        return and_(*clauses) if clauses else true()

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def can_see_venue(self, venue_id: str | None) -> bool:
        """``None`` (a group-wide enquiry) is visible to everyone in the org."""
        return venue_id is None or venue_id in self.venue_ids

    def venue_clause(self, column, *, allow_null: bool = False):
        """SQL filter keeping rows at this caller's venues (and, with
        ``allow_null``, group-wide rows whose venue is NULL)."""
        clause = column.in_(sorted(self.venue_ids))
        return or_(column.is_(None), clause) if allow_null else clause


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


def build_ctx(
    db: Session,
    user,
    *,
    org_id: str | None = None,
    actor_label: str | None = None,
) -> FunctionsCtx:
    """The ctx for a signed-in person (router or agent tool).

    ``org_id`` may only differ from the user's own organisation for a
    platform admin; anyone else naming another org gets ``Forbidden``.
    """
    from app.db.models import Role, UserVenueAccess, Venue

    if user is None:
        raise Forbidden("Sign in to use Functions.")
    is_platform_admin = getattr(user, "role", None) == "admin"
    from app.services import active_org

    membership = active_org.membership_for(db, user.id, org_id)
    if org_id and membership is not None and membership.organization_id != org_id:
        membership = None  # a named organisation the user is not in

    if membership is None:
        if not (is_platform_admin and org_id):
            raise Forbidden("You are not a member of an organisation using Functions.")
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
    return FunctionsCtx(
        org_id=org.id,
        user_id=user.id,
        venue_ids=venue_ids,
        is_owner=is_owner,
        mode=org.functions_mode or "off",
        actor_label=actor_label,
        user=user,
    )


def for_org(db: Session, org_id: str, *, actor_label: str = "system") -> FunctionsCtx:
    """A system ctx — every venue, no user. For the importer, the scheduler
    and the mailbox sync; never for a request a person or customer made."""
    org = _org(db, org_id)
    return FunctionsCtx(
        org_id=org.id,
        user_id=None,
        venue_ids=_all_venue_ids(db, org.id),
        is_owner=True,
        mode=org.functions_mode or "off",
        actor_label=actor_label,
    )


def for_client(db: Session, org_id: str, *, event_id: str, email: str) -> FunctionsCtx:
    """A portal customer's ctx: the event their token resolved to, at its
    venue, acting as their verified ``email``."""
    from app.db.functions_models import FnEvent

    org = _org(db, org_id)
    event = (
        db.query(FnEvent)
        .filter(FnEvent.organization_id == org.id, FnEvent.id == event_id)
        .first()
    )
    if event is None:
        raise NotFound("Event not found.")
    return FunctionsCtx(
        org_id=org.id,
        user_id=None,
        venue_ids=frozenset({event.venue_id}),
        is_owner=False,
        mode=org.functions_mode or "off",
        actor_label="customer",
        client_email=(email or "").strip().lower() or None,
        client_event_id=event.id,
    )


def for_quote(db: Session, org_id: str, *, enquiry_id: str) -> FunctionsCtx:
    """A quote-page customer's ctx: the enquiry their token resolved to and
    its options (which may be at several venues of a group-wide enquiry)."""
    from app.db.functions_models import FnEnquiry

    org = _org(db, org_id)
    found = (
        db.query(FnEnquiry.id)
        .filter(FnEnquiry.organization_id == org.id, FnEnquiry.id == enquiry_id)
        .first()
    )
    if found is None:
        raise NotFound("Enquiry not found.")
    return FunctionsCtx(
        org_id=org.id,
        user_id=None,
        venue_ids=_all_venue_ids(db, org.id),
        is_owner=False,
        mode=org.functions_mode or "off",
        actor_label="customer",
        client_enquiry_id=enquiry_id,
    )


def require_venue(ctx: FunctionsCtx, venue_id: str | None) -> None:
    """Refuse a venue the caller may not act at. A venue of another
    organisation is never in ``venue_ids``, so this is the cross-org check
    too."""
    if venue_id is not None and venue_id not in ctx.venue_ids:
        raise Forbidden("You do not have access to that venue.")


def require_live(ctx: FunctionsCtx) -> None:
    """Refuse every write unless the organisation's Functions are live."""
    if ctx.mode != "live":
        if ctx.mode == "shadow":
            raise NotLive(
                "Functions is in shadow mode for this organisation — it is "
                "read-only until it goes live."
            )
        raise NotLive("Functions is not switched on for this organisation.")


def require_owner(ctx: FunctionsCtx) -> None:
    if not ctx.is_owner:
        raise Forbidden("Only an owner of the organisation can change this.")


def require_org_member(db: Session, ctx: FunctionsCtx, user_id: str | None) -> None:
    """Refuse a user id from outside the organisation (users are global, so
    an owner or approver id is only meaningful for a member)."""
    from app.db.models import OrganizationMembership

    if user_id is None:
        return
    member = (
        db.query(OrganizationMembership.id)
        .filter(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == ctx.org_id,
        )
        .first()
    )
    if member is None:
        raise Invalid("That person is not in this organisation.")


def require_staff(ctx: FunctionsCtx) -> None:
    """Staff-only actions (skipping a section, recording a phone
    confirmation) — not for customers."""
    if ctx.is_client:
        raise Forbidden("Only venue staff can do that.")
