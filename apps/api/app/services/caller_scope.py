"""Whose organisation is calling — for code that has no request to ask.

Built-in handlers get ``(params, db, thread_id)`` and the consolidator engine
gets the tool call's params; neither is told which organisation the work is
for. So every name lookup underneath ran across ALL organisations:
``norm.list_venues`` listed every venue in the database (the venues='all'
fan-out of get_sales / get_labour / received items), eight handlers took the
first venue whose name merely contained the text they were given, and a
consolidator's venue name found its login the same way. Harmless with one
organisation, a leak with two (consolidator review, Oct 2026).

Each entry point sets the scope once — an agent tool call from its thread's
user (``tool_loop._execute_tool_call``), the shared executor behind MCP,
charts and apps from the venue it was authorised for
(``tool_executor.execute_connector_tool``), a page request from the signed-in
user — and the code underneath reads it. A context variable, not a param, so
nothing a model sends can choose it. Worker threads get a copy
(function_executor's ``call_api_parallel``).

No scope at all means a system context (a background worker, a script):
lookups behave as before, except that a name never silently picks between
two organisations' venues.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Scope:
    #: The caller's organisation.
    org_id: str | None
    #: A platform admin with no organisation of their own sees every venue,
    #: as they always have.
    unrestricted: bool = False


_SCOPE: contextvars.ContextVar[Scope | None] = contextvars.ContextVar(
    "caller_scope", default=None
)


def current() -> Scope | None:
    return _SCOPE.get()


@contextmanager
def use(scope: Scope | None):
    """Run the block under ``scope``. ``None`` keeps whatever is already set —
    a call that can't tell who is asking never widens an outer scope."""
    if scope is None:
        yield current()
        return
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


# ------------------------------------------------------------ deriving it --


def for_venue(db: Session, venue_id: str | None) -> Scope | None:
    if not venue_id:
        return None
    from app.db.models import Venue

    v = db.query(Venue).filter(Venue.id == venue_id).first()
    return Scope(v.organization_id) if v and v.organization_id else None


def for_user(db: Session, user) -> Scope | None:
    """The user's organisation: their membership, else the one organisation
    their venues belong to; a platform admin with neither is unrestricted."""
    if user is None:
        return None
    from app.db.models import UserVenueAccess, Venue
    from app.services.entitlements import org_id_for_user

    org = org_id_for_user(user.id, db)
    if org:
        return Scope(org)
    orgs = {
        row[0]
        for row in db.query(Venue.organization_id)
        .join(UserVenueAccess, UserVenueAccess.venue_id == Venue.id)
        .filter(UserVenueAccess.user_id == user.id)
        .all()
        if row[0]
    }
    if len(orgs) == 1:
        return Scope(orgs.pop())
    if getattr(user, "role", None) == "admin":
        return Scope(None, unrestricted=True)
    return None


def for_thread(db: Session, thread_id: str | None) -> Scope | None:
    if not thread_id:
        return None
    from app.db.models import Thread, User

    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if thread is None:
        return None
    user = (
        db.query(User).filter(User.id == thread.user_id).first()
        if thread.user_id
        else None
    )
    return for_user(db, user) or for_venue(db, thread.venue_id)


# ------------------------------------------------------------ using it ----


def venue_query(db: Session):
    """The venues the caller may name, or None when nobody is known."""
    from app.db.models import Venue

    scope = current()
    if scope is None:
        return None
    q = db.query(Venue)
    if scope.unrestricted:
        return q
    return q.filter(Venue.organization_id == scope.org_id)


def allows(venue) -> bool:
    """Whether the caller may act on this venue (True with no scope)."""
    scope = current()
    if scope is None or scope.unrestricted:
        return True
    return venue is not None and venue.organization_id == scope.org_id


def venue_key(name: str | None) -> str:
    """'The Glass Goose' and 'Glass Goose', "Mr Murdoch's" and 'Mr Murdochs',
    'Freeman & Grey' and 'Freeman and Grey' compare equal. A whole name still
    has to match — never a part of one."""
    text = str(name or "").lower().replace("&", " and ")
    kept = "".join(
        ch if ch.isalnum() else (" " if ch in " -/,.\t" else "") for ch in text
    )
    words = kept.split()
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def find_venue(db: Session, name: str | None = None, venue_id: str | None = None):
    """``(venue, None)`` or ``(None, message)``.

    An id wins, and must be the caller's. A name must be a whole venue name
    (case, punctuation and a leading "The" aside) among the caller's venues —
    never "the first venue containing these letters", which is what eight
    handlers did with ``ilike('%name%').first()`` across every organisation.
    """
    from app.db.models import Venue

    if venue_id:
        v = db.query(Venue).filter(Venue.id == str(venue_id)).first()
        if v is None or not allows(v):
            return None, f"No venue with id {venue_id}."
        return v, None
    if not name or not str(name).strip():
        return None, "No venue given."
    q = venue_query(db)
    candidates = (q if q is not None else db.query(Venue)).all()
    text = str(name).strip()
    hits = [v for v in candidates if v.name and v.name.lower() == text.lower()]
    if not hits:
        key = venue_key(text)
        hits = [v for v in candidates if key and venue_key(v.name) == key]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, (
            f"'{text}' names more than one venue — pass the venue id instead."
        )
    names = sorted(v.name for v in candidates if v.name)
    listed = (": " + ", ".join(names)) if q is not None and names else ""
    return None, f"No venue called '{text}'{listed}."
