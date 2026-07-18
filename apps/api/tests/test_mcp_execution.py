"""MCP venue authorization.

Enforcement lives only in the MCP layer, so these tests *are* the security
argument. Each one pins a specific way the platform's existing behaviour must
not leak in:

- POST /api/messages takes venue_id from the request body with no check.
- venue_service.get_user_venues fails open — no access rows means every venue.
- require_permission picks an arbitrary org membership and bypasses for admins.

None of that may be true here.
"""

import uuid

import pytest

from app.db.models import Organization, User, UserVenueAccess, Venue
from app.mcp.execution import VenueResolutionError, resolve_mcp_venue
from app.mcp.principal import McpPrincipal


def _org(db, name="Cook Brothers"):
    o = Organization(
        id=str(uuid.uuid4()), name=name, slug=f"org-{uuid.uuid4().hex[:8]}"
    )
    db.add(o)
    db.flush()
    return o


def _venue(db, name, org):
    v = Venue(
        id=str(uuid.uuid4()),
        name=name,
        timezone="Pacific/Auckland",
        organization_id=org.id if org else None,
    )
    db.add(v)
    db.flush()
    return v


def _user(db):
    u = User(
        id=str(uuid.uuid4()),
        email=f"u-{uuid.uuid4().hex[:8]}@x.com",
        hashed_password="x",
        full_name="U",
        role="user",
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _grant(db, user, venue):
    db.add(UserVenueAccess(user_id=user.id, venue_id=venue.id))
    db.flush()


def _principal(user, org, venues, **kw):
    return McpPrincipal(
        user_id=user.id,
        organization_id=org.id,
        venue_ids=tuple(v.id for v in venues),
        scopes=frozenset({"mcp:reports:read"}),
        **kw,
    )


class TestVenueResolution:
    def test_single_consented_venue_is_implied(self, db_session):
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, v)
        assert resolve_mcp_venue(_principal(u, org, [v]), None, db_session) == v.id

    def test_named_venue_resolves(self, db_session):
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        b = _venue(db_session, "Little High", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, b)
        assert (
            resolve_mcp_venue(_principal(u, org, [a, b]), "Little High", db_session)
            == b.id
        )

    def test_venue_name_is_case_insensitive(self, db_session):
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, v)
        assert (
            resolve_mcp_venue(_principal(u, org, [v]), "  la zeppa ", db_session)
            == v.id
        )

    def test_ambiguous_venue_asks_rather_than_guessing(self, db_session):
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        b = _venue(db_session, "Little High", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, b)
        with pytest.raises(VenueResolutionError) as exc:
            resolve_mcp_venue(_principal(u, org, [a, b]), None, db_session)
        assert "Which venue" in str(exc.value)
        assert "La Zeppa" in str(exc.value)


class TestVenueEnforcement:
    def test_no_consented_venues_fails_closed(self, db_session):
        """get_user_venues would fail OPEN here and hand over every venue."""
        org = _org(db_session)
        u = _user(db_session)
        with pytest.raises(VenueResolutionError) as exc:
            resolve_mcp_venue(_principal(u, org, []), None, db_session)
        assert "do not have access to any venues" in str(exc.value)

    def test_venue_outside_the_token_is_refused(self, db_session):
        org = _org(db_session)
        mine = _venue(db_session, "La Zeppa", org)
        theirs = _venue(db_session, "Someone Elses Bar", org)
        u = _user(db_session)
        _grant(db_session, u, mine)
        _grant(db_session, u, theirs)  # real access, but NOT consented
        with pytest.raises(VenueResolutionError):
            resolve_mcp_venue(
                _principal(u, org, [mine]), "Someone Elses Bar", db_session
            )

    def test_revoked_access_beats_the_token(self, db_session):
        """venue_ids froze at consent time; live access is the authority."""
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        # Token says yes, but no UserVenueAccess row exists.
        with pytest.raises(VenueResolutionError):
            resolve_mcp_venue(_principal(u, org, [v]), "La Zeppa", db_session)

    def test_cross_org_venue_is_refused(self, db_session):
        org_a = _org(db_session, "Org A")
        org_b = _org(db_session, "Org B")
        v = _venue(db_session, "Other Org Venue", org_b)
        u = _user(db_session)
        _grant(db_session, u, v)
        # Token is bound to org A; the venue belongs to org B.
        with pytest.raises(VenueResolutionError):
            resolve_mcp_venue(_principal(u, org_a, [v]), "Other Org Venue", db_session)

    def test_null_org_venue_is_refused_not_defaulted(self, db_session):
        """Venue.organization_id is nullable — a NULL-org venue must not become
        a bridge between orgs."""
        org = _org(db_session)
        v = _venue(db_session, "Orphan Venue", None)
        u = _user(db_session)
        _grant(db_session, u, v)
        with pytest.raises(VenueResolutionError):
            resolve_mcp_venue(_principal(u, org, [v]), "Orphan Venue", db_session)

    def test_error_does_not_reveal_other_venues(self, db_session):
        """The refusal must not confirm that a venue exists elsewhere."""
        org = _org(db_session)
        mine = _venue(db_session, "La Zeppa", org)
        _venue(db_session, "Secret Competitor Bar", org)
        u = _user(db_session)
        _grant(db_session, u, mine)
        with pytest.raises(VenueResolutionError) as exc:
            resolve_mcp_venue(
                _principal(u, org, [mine]), "Secret Competitor Bar", db_session
            )
        msg = str(exc.value)
        assert "La Zeppa" in msg  # what you *can* see
        assert "does not exist" not in msg  # never an existence oracle


class TestNoFailOpenImports:
    """Import-graph guards.

    Checked by AST, not by grepping text — the modules *document* why they
    avoid these, and a text search would match the warnings themselves.
    """

    @staticmethod
    def _imports(path):
        """Every (module, name) actually imported by a file."""
        import ast

        out = set()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    out.add((node.module or "", alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    out.add((alias.name, ""))
        return out

    @staticmethod
    def _mcp_files():
        import pathlib

        return list((pathlib.Path(__file__).parent.parent / "app" / "mcp").glob("*.py"))

    def test_mcp_never_imports_the_fail_open_venue_helper(self):
        """venue_service.get_user_venues fails open — a user with no access
        rows is handed every venue on the platform. Defensible for a logged-in
        human in a first-party UI; not for a third-party AI client. This is
        exactly what gets reintroduced by someone 'reusing existing code'."""
        offenders = [
            p.name
            for p in self._mcp_files()
            if any(name == "get_user_venues" for _mod, name in self._imports(p))
        ]
        assert not offenders, f"app/mcp/ must not import get_user_venues: {offenders}"

    def test_mcp_never_imports_the_non_org_aware_permission_check(self):
        """require_permission picks an arbitrary org membership and returns
        early for platform admins. The MCP layer must not depend on it."""
        offenders = [
            p.name
            for p in self._mcp_files()
            if any(
                name in {"require_permission", "require_role"}
                for _mod, name in self._imports(p)
            )
        ]
        assert not offenders, (
            f"app/mcp/ must not import require_permission/require_role: {offenders}"
        )
