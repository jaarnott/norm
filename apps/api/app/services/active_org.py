"""Which organisation a request is acting in.

A person may belong to several organisations (Oct 2026). Norm used to take "the
user's first membership" in about fifty places, so a second membership would
have been ignored, or chosen by row order. Everything now asks here instead.

The browser sends ``X-Organization: <id>`` with every call. It is only a
*request*: the id is honoured when, and only when, the user holds a membership
in that organisation, so a client cannot choose an organisation it is not in.
With one membership nothing changes. With several and no (valid) header, the
oldest membership wins, which is deterministic rather than whatever row the
database returned first.

Work done FOR an organisation outranks the header: a tool call in a
conversation runs under that conversation's organisation (``caller_scope``),
and that is the organisation it acts in. Without this, a tool Norm ran beside
another one — on a worker thread, where the header never arrived — fell back
to the oldest membership: a Lott Group owner's app saves landed in his older
Cook Brothers membership, and his next read could not find them (21 Oct 2026).

Platform admins get no way into organisations they are not members of: there
is deliberately no support mode, for privacy.
"""

from __future__ import annotations

import contextvars

from sqlalchemy.orm import Session

#: The organisation the current request asked for (None: no preference).
requested_org: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "requested_org", default=None
)


def use(org_id: str | None):
    """Context manager for tests and background work: act as ``org_id``."""

    class _Use:
        def __enter__(self):
            self.token = requested_org.set(org_id)

        def __exit__(self, *exc):
            requested_org.reset(self.token)

    return _Use()


def memberships_for(db: Session, user_id: str | None) -> list:
    """Every membership of a user, oldest first."""
    if not user_id:
        return []
    from app.db.models import OrganizationMembership

    return (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user_id)
        .order_by(
            OrganizationMembership.created_at.asc().nulls_last(),
            OrganizationMembership.id,
        )
        .all()
    )


def membership_for(db: Session, user_id: str | None, org_id: str | None = None):
    """The membership the user is acting under: ``org_id`` if given and valid,
    else the organisation the work is for (a conversation's tool call), else
    the one the request asked for, else the oldest. None if no membership."""
    from app.services import caller_scope

    rows = memberships_for(db, user_id)
    if not rows:
        return None
    scope = caller_scope.current()
    for want in (org_id, scope.org_id if scope else None, requested_org.get()):
        if want:
            for m in rows:
                if m.organization_id == want:
                    return m
    return rows[0]


def org_id_for(db: Session, user_id: str | None, org_id: str | None = None):
    m = membership_for(db, user_id, org_id)
    return m.organization_id if m else None


class ActiveOrgMiddleware:
    """Pure ASGI (no task hop): copies ``X-Organization`` into the context
    variable so sync endpoints, which run in a copied context, see it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        want = None
        for k, v in scope.get("headers") or []:
            if k == b"x-organization":
                want = v.decode("latin-1").strip() or None
                break
        token = requested_org.set(want)
        try:
            await self.app(scope, receive, send)
        finally:
            requested_org.reset(token)
