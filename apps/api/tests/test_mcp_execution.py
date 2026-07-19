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


class TestCalendarVenue:
    """Which venue's trading day applies to a tool that isn't venue-scoped.

    resolve_dates returns no venue data, so it needs no venue *authorization* —
    but it does need venue *settings* (day_start_time/timezone), or it silently
    applies the org default instead of the venue's own trading day.
    """

    def _ctx(self, db, principal):
        from app.mcp.execution import NormMcpContext

        return NormMcpContext(principal=principal, db=db, config_db=db)

    def test_single_venue_is_used(self, db_session):
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, v)
        ctx = self._ctx(db_session, _principal(u, org, [v]))
        assert ctx._calendar_venue() == v.id

    def test_several_venues_fall_back_to_the_default(self, db_session):
        """They may disagree on their day start. Silently picking one would be
        its own wrong answer, so use the configured default instead."""
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        b = _venue(db_session, "Mr Murdochs", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, b)
        ctx = self._ctx(db_session, _principal(u, org, [a, b]))
        assert ctx._calendar_venue() is None

    def test_no_venues_is_none(self, db_session):
        org = _org(db_session)
        u = _user(db_session)
        ctx = self._ctx(db_session, _principal(u, org, []))
        assert ctx._calendar_venue() is None


class TestAllVenuesFanOut:
    """`venue: "all"` on a *_for_period tool.

    Why this exists: asked for yesterday's sales across the group, Claude
    called the date-safe tool, was told "which venue?", and went and found
    norm_reports__get_periodic_sales instead — a tool with no trading-day
    awareness. It reported a Saturday total computed midnight-to-midnight, so
    post-midnight trade landed in Sunday. The safe tool has to be able to
    answer the question that actually gets asked, or it gets routed around.
    """

    def _tool(self, name="loadedhub__get_sales_for_period", multi_venue=True):
        from app.mcp.projection import McpTool

        return McpTool(
            name=name,
            kind="connector",
            connector="loadedhub",
            action="get_sales_for_period",
            playbook_slug=None,
            method="GET",
            access="read",
            scopes=frozenset({"mcp:reports:read"}),
            description="",
            input_schema={},
            multi_venue=multi_venue,
        )

    def _ctx(self, db, principal, tool, results):
        """Context with _execute stubbed: venue_id -> (success, payload/error)."""
        from types import SimpleNamespace

        from app.mcp.execution import NormMcpContext

        ctx = NormMcpContext(principal=principal, db=db, config_db=db)
        ctx._tools = {tool.name: tool}
        seen = []

        def fake_execute(_tool, params, venue_id):
            seen.append((venue_id, dict(params)))
            ok, body = results[venue_id]
            return SimpleNamespace(
                success=ok,
                payload=body if ok else None,
                error=None if ok else body,
            )

        ctx._execute = fake_execute
        ctx.seen = seen
        return ctx

    def _window(self, label):
        return {"start": f"{label}T07:00:00+12:00", "trading_aligned": True}

    def test_runs_once_per_consented_venue(self, db_session):
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        b = _venue(db_session, "Mr Murdochs", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, b)
        tool = self._tool()
        ctx = self._ctx(
            db_session,
            _principal(u, org, [a, b]),
            tool,
            {
                a.id: (True, {"window": self._window("2026-07-18"), "data": {"t": 1}}),
                b.id: (True, {"window": self._window("2026-07-18"), "data": {"t": 2}}),
            },
        )
        out = ctx.call_tool(tool.name, {"venue": "all", "period": "yesterday"})
        assert sorted(v for v, _ in ctx.seen) == sorted([a.id, b.id])
        # The period still reaches each call; only `venue` is consumed.
        assert all(p["period"] == "yesterday" for _, p in ctx.seen)
        assert "venue" not in ctx.seen[0][1]
        assert not out.get("isError")

    def test_each_venue_reports_its_own_window(self, db_session):
        """A group with mixed day starts must not have one venue's boundary
        imposed on the rest — that is the whole reason to fan out here rather
        than resolve one window and reuse it."""
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        b = _venue(db_session, "Mr Murdochs", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, b)
        tool = self._tool()
        ctx = self._ctx(
            db_session,
            _principal(u, org, [a, b]),
            tool,
            {
                a.id: (True, {"window": self._window("2026-07-18"), "data": {"t": 1}}),
                b.id: (True, {"window": self._window("2026-07-17"), "data": {"t": 2}}),
            },
        )
        out = ctx.call_tool(tool.name, {"venue": "all", "period": "yesterday"})
        import json

        rows = json.loads(out["content"][0]["text"])["venues"]
        by_name = {r["venue"]: r for r in rows}
        assert by_name["La Zeppa"]["window"]["start"].startswith("2026-07-18")
        assert by_name["Mr Murdochs"]["window"]["start"].startswith("2026-07-17")

    def test_one_venue_failing_does_not_fail_the_call(self, db_session):
        """A partial answer naming the missing venue beats a total failure —
        it is what makes a stale POS feed visible as a stale feed."""
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        b = _venue(db_session, "Mr Murdochs", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, b)
        tool = self._tool()
        ctx = self._ctx(
            db_session,
            _principal(u, org, [a, b]),
            tool,
            {
                a.id: (True, {"window": self._window("2026-07-18"), "data": {"t": 1}}),
                b.id: (False, "LoadedHub 500"),
            },
        )
        out = ctx.call_tool(tool.name, {"venue": "all", "period": "yesterday"})
        assert not out.get("isError")
        import json

        rows = {r["venue"]: r for r in json.loads(out["content"][0]["text"])["venues"]}
        assert rows["Mr Murdochs"]["error"] == "LoadedHub 500"
        assert rows["La Zeppa"]["data"] == {"t": 1}

    def test_every_venue_failing_is_an_error(self, db_session):
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        tool = self._tool()
        ctx = self._ctx(
            db_session, _principal(u, org, [a]), tool, {a.id: (False, "LoadedHub 500")}
        )
        out = ctx.call_tool(tool.name, {"venue": "all", "period": "yesterday"})
        assert out.get("isError")

    def test_fan_out_covers_only_consented_venues(self, db_session):
        """The security property. venue_ids is frozen at consent time, so a
        venue the user can otherwise reach must not be swept in by "all"."""
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        other = _venue(db_session, "Not Consented", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, other)
        tool = self._tool()
        # Principal consented to `a` only, though the user can access both.
        ctx = self._ctx(
            db_session,
            _principal(u, org, [a]),
            tool,
            {a.id: (True, {"window": self._window("2026-07-18"), "data": {"t": 1}})},
        )
        ctx.call_tool(tool.name, {"venue": "all", "period": "yesterday"})
        assert [v for v, _ in ctx.seen] == [a.id]

    def test_all_is_not_a_venue_for_tools_that_do_not_opt_in(self, db_session):
        """Raw actions take a caller-supplied window, so fanning one out would
        impose a single boundary on venues that may not share a day start."""
        org = _org(db_session)
        a = _venue(db_session, "La Zeppa", org)
        b = _venue(db_session, "Mr Murdochs", org)
        u = _user(db_session)
        _grant(db_session, u, a)
        _grant(db_session, u, b)
        tool = self._tool(name="loadedhub__get_stock_items", multi_venue=False)
        ctx = self._ctx(db_session, _principal(u, org, [a, b]), tool, {})
        out = ctx.call_tool(tool.name, {"venue": "all"})
        assert out.get("isError")
        assert ctx.seen == []
