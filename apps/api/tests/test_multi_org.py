"""A person in two organisations (Oct 2026).

Norm took "the user's first membership" in about fifty places. These tests give
one person two organisations, with a different role in each, and prove the one
they are acting in decides everything: venues, permissions, the organisation a
conversation (and its usage) belongs to. They also pin the leaks that started
this: removal and invites touched access across organisations.
"""

import pathlib
import re
import uuid

import pytest

from app.services import active_org, caller_scope
from app.services.venue_service import get_user_venues, user_can_access_venue
from tests.conftest import _make_membership
from tests.org_helpers import grant_venue, make_org, make_user, make_venue


@pytest.fixture(autouse=True)
def _system_roles(db_session):
    """The standard roles (owner, manager, team member, payroll admin) exist in
    every real database; a fresh test database has none until seeded."""
    from app.db.models import Role
    from tests.org_helpers import seed_system_roles

    if not (
        db_session.query(Role)
        .filter(Role.name == "owner", Role.organization_id.is_(None))
        .first()
    ):
        seed_system_roles(db_session)


@pytest.fixture()
def two_orgs(db_session):
    a, b = make_org(db_session, "Org A"), make_org(db_session, "Org B")
    va, vb = make_venue(db_session, "Venue A", a), make_venue(db_session, "Venue B", b)
    user = make_user(db_session)
    ma = _make_membership(db_session, user, a)
    mb = _make_membership(db_session, user, b)
    grant_venue(db_session, user, va)
    grant_venue(db_session, user, vb)
    db_session.flush()
    return type("W", (), dict(a=a, b=b, va=va, vb=vb, user=user, ma=ma, mb=mb))


class TestActiveOrganisation:
    def test_no_preference_is_the_oldest_membership(self, db_session, two_orgs):
        assert active_org.org_id_for(db_session, two_orgs.user.id) == two_orgs.a.id

    def test_a_request_picks_among_its_own_memberships(self, db_session, two_orgs):
        with active_org.use(two_orgs.b.id):
            assert active_org.org_id_for(db_session, two_orgs.user.id) == two_orgs.b.id

    def test_a_request_cannot_pick_an_organisation_it_is_not_in(
        self, db_session, two_orgs
    ):
        other = make_org(db_session, "Not mine")
        with active_org.use(other.id):
            assert active_org.org_id_for(db_session, two_orgs.user.id) == two_orgs.a.id

    def test_a_platform_admin_has_no_way_into_an_organisation_they_are_not_in(
        self, db_session
    ):
        admin = make_user(db_session, role="admin")
        org = make_org(db_session, "Private")
        with active_org.use(org.id):
            assert active_org.org_id_for(db_session, admin.id) is None


class TestVenues:
    def test_the_venue_list_is_the_active_organisations(self, db_session, two_orgs):
        names = lambda: [v.name for v in get_user_venues(db_session, two_orgs.user.id)]  # noqa: E731
        assert names() == ["Venue A"]
        with active_org.use(two_orgs.b.id):
            assert names() == ["Venue B"]

    def test_a_venue_of_the_other_organisation_is_refused_by_id(
        self, db_session, two_orgs
    ):
        assert user_can_access_venue(db_session, two_orgs.user.id, two_orgs.va.id)
        assert not user_can_access_venue(db_session, two_orgs.user.id, two_orgs.vb.id)
        with active_org.use(two_orgs.b.id):
            assert user_can_access_venue(db_session, two_orgs.user.id, two_orgs.vb.id)
            assert not user_can_access_venue(
                db_session, two_orgs.user.id, two_orgs.va.id
            )


class TestScopeAndUsage:
    def test_the_scope_follows_the_active_organisation(self, db_session, two_orgs):
        with active_org.use(two_orgs.b.id):
            assert caller_scope.for_user(db_session, two_orgs.user).org_id == (
                two_orgs.b.id
            )

    def test_a_conversation_belongs_to_the_organisation_it_started_in(
        self, db_session, two_orgs
    ):
        from app.db.models import Thread

        t = Thread(user_id=two_orgs.user.id, domain="norm", intent="x")
        with active_org.use(two_orgs.b.id):
            db_session.add(t)
            db_session.flush()
        assert t.organization_id == two_orgs.b.id
        # later, with no request around (an approval, a schedule), it still acts
        # as the organisation it began in, not the user's oldest membership
        assert caller_scope.for_thread(db_session, t.id).org_id == two_orgs.b.id

    def test_usage_is_billed_to_the_conversations_organisation(
        self, db_session, two_orgs
    ):
        from app.db.models import Thread
        from app.interpreter.llm_interpreter import _org_for_call
        from app.services.billing_service import _org_for_thread

        t = Thread(user_id=two_orgs.user.id, domain="norm", intent="x")
        with active_org.use(two_orgs.b.id):
            db_session.add(t)
            db_session.flush()
        assert _org_for_call(db_session, two_orgs.user.id, t.id) == two_orgs.b.id
        assert _org_for_thread(db_session, t.id) == two_orgs.b.id
        # a call with no thread, in a request for A, is A's
        with active_org.use(two_orgs.a.id):
            assert _org_for_call(db_session, two_orgs.user.id) == two_orgs.a.id


class TestHttp:
    def test_me_lists_organisations_and_takes_the_role_of_the_active_one(
        self, client, db_session, two_orgs
    ):
        from app.auth.security import create_access_token
        from app.db.models import Role

        owner = db_session.query(Role).filter(Role.name == "owner").first()
        member = db_session.query(Role).filter(Role.name == "team_member").first()
        if owner and member:
            two_orgs.ma.role_id = owner.id
            two_orgs.mb.role_id = member.id
            db_session.commit()
        h = {
            "Authorization": f"Bearer {create_access_token({'sub': two_orgs.user.id})}"
        }
        me = client.get("/api/auth/me", headers=h).json()
        assert {o["id"] for o in me["organizations"]} == {two_orgs.a.id, two_orgs.b.id}
        assert me["active_organization_id"] == two_orgs.a.id
        me_b = client.get(
            "/api/auth/me", headers={**h, "X-Organization": two_orgs.b.id}
        ).json()
        assert me_b["active_organization_id"] == two_orgs.b.id
        if owner and member:
            assert me_b["org_role"]["name"] == "team_member"
            assert me["org_role"]["name"] == "owner"
        venues = lambda hh: [  # noqa: E731
            v["name"] for v in client.get("/api/venues", headers=hh).json()["venues"]
        ]
        assert venues(h) == ["Venue A"]
        assert venues({**h, "X-Organization": two_orgs.b.id}) == ["Venue B"]

    def test_inviting_to_a_second_organisation_keeps_access_to_the_first(
        self, client, db_session, two_orgs, admin_headers
    ):
        from app.db.models import Role, UserVenueAccess

        c = make_org(db_session, "Org C")
        vc = make_venue(db_session, "Venue C", c)
        role = db_session.query(Role).filter(Role.name == "team_member").first()
        resp = client.post(
            "/api/auth/invite",
            json={
                "email": two_orgs.user.email,
                "org_id": c.id,
                "role_id": role.id,
                "venue_ids": [vc.id],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        rows = {
            a.venue_id
            for a in db_session.query(UserVenueAccess).filter_by(
                user_id=two_orgs.user.id
            )
        }
        assert rows == {two_orgs.va.id, two_orgs.vb.id, vc.id}

    def test_an_invite_cannot_name_another_organisations_venue(
        self, client, db_session, two_orgs, admin_headers
    ):
        from app.db.models import Role

        role = db_session.query(Role).filter(Role.name == "team_member").first()
        resp = client.post(
            "/api/auth/invite",
            json={
                "email": f"new-{uuid.uuid4().hex[:6]}@example.com",
                "org_id": two_orgs.a.id,
                "role_id": role.id,
                "venue_ids": [two_orgs.vb.id],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422


class TestThreadList:
    def test_the_list_shows_only_the_active_organisations_conversations(
        self, client, db_session, two_orgs
    ):
        from app.auth.security import create_access_token
        from app.db.models import Role, Thread

        owner = db_session.query(Role).filter(Role.name == "owner").first()
        for m in (two_orgs.ma, two_orgs.mb):
            m.role_id = owner.id
        for org, title in ((two_orgs.a, "in A"), (two_orgs.b, "in B")):
            with active_org.use(org.id):
                db_session.add(
                    Thread(
                        user_id=two_orgs.user.id, domain="norm", intent="x", title=title
                    )
                )
                db_session.flush()
        db_session.commit()
        h = {
            "Authorization": f"Bearer {create_access_token({'sub': two_orgs.user.id})}"
        }
        titles = lambda hh: sorted(  # noqa: E731
            t["title"] for t in client.get("/api/threads", headers=hh).json()["threads"]
        )
        assert titles(h) == ["in A"]
        assert titles({**h, "X-Organization": two_orgs.b.id}) == ["in B"]


class TestInviteEmail:
    def _invite(self, client, db_session, headers, email, org, monkeypatch):
        import app.services.email_service as es
        from app.db.models import Role

        sent = []
        monkeypatch.setattr(
            es, "send_system_email", lambda **k: sent.append(k) or "log-1"
        )
        role = db_session.query(Role).filter(Role.name == "team_member").first()
        resp = client.post(
            "/api/auth/invite",
            json={
                "email": email,
                "org_id": org.id,
                "role_id": role.id,
                "venue_ids": [],
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        return sent

    def test_an_existing_user_is_told_they_were_added_not_asked_to_accept(
        self, client, db_session, two_orgs, admin_headers, monkeypatch
    ):
        c = make_org(db_session, "Org C")
        sent = self._invite(
            client, db_session, admin_headers, two_orgs.user.email, c, monkeypatch
        )
        assert [k["template_name"] for k in sent] == ["added_to_organization"]
        assert "invite_url" not in sent[0]["context"]
        assert sent[0]["context"]["org_name"] == "Org C"

    def test_a_new_person_still_gets_the_accept_link(
        self, client, db_session, two_orgs, admin_headers, monkeypatch
    ):
        sent = self._invite(
            client,
            db_session,
            admin_headers,
            f"new-{uuid.uuid4().hex[:6]}@example.com",
            two_orgs.a,
            monkeypatch,
        )
        assert [k["template_name"] for k in sent] == ["invite_user"]
        assert "accept-invite?token=" in sent[0]["context"]["invite_url"]

    def test_the_new_email_renders(self, db_session):
        from app.services.email_templates import render_template

        subject, html = render_template(
            "added_to_organization",
            {"inviter_name": "Sam", "org_name": "Org C", "app_url": "https://x"},
            db_session,
        )
        assert subject == "Sam added you to Org C on Norm"
        assert "switcher" in html and "Org C" in html


class TestIntegrity:
    def test_access_without_membership_is_found(self, db_session, two_orgs):
        from app.services.access_integrity import orphaned_access

        assert orphaned_access(db_session) == []
        stray = make_user(db_session)
        grant_venue(db_session, stray, two_orgs.va)
        found = orphaned_access(db_session)
        assert [(r["email"], r["venue"]) for r in found] == [(stray.email, "Venue A")]


class TestNoFirstMembershipLookups:
    def test_nothing_takes_the_first_membership_of_a_user_by_hand(self):
        """The helper in services/active_org.py is the one place that decides."""
        root = pathlib.Path(__file__).resolve().parent.parent / "app"
        bad = []
        pat = re.compile(
            r"query\(\s*OrganizationMembership\s*\)\s*\.filter\(\s*"
            r"OrganizationMembership\.user_id\s*==\s*[\w\.]+,?\s*\)\s*\.first\(\)",
            re.S,
        )
        for f in root.rglob("*.py"):
            if f.name == "active_org.py":
                continue
            if pat.search(f.read_text()):
                bad.append(str(f.relative_to(root)))
        assert not bad, f"use active_org.membership_for instead: {bad}"


class TestAToolCallWithNoVenue:
    """The model is offered a "venue" field only when the person has more than
    one venue. With one it sends none, and the receive tool failed with
    "venue not found" at The Lott Cafe (thread 9df5c19a, 20 Oct 2026)."""

    ECHO = (
        "def run(params, call_api, log):\n"
        "    return {'venue': params.get('venue'), 'venue_id': params.get('venue_id'),\n"
        "            'venues': params.get('venues')}\n"
    )

    def _run(self, db_session, user, org, **params):
        from app.agents.internal_tools import execute_consolidator
        from app.db.models import Thread

        t = Thread(user_id=user.id, domain="norm", intent="x", organization_id=org.id)
        db_session.add(t)
        db_session.flush()
        with caller_scope.use(caller_scope.Scope(org.id, user_id=user.id)):
            out = execute_consolidator(
                {"function_code": self.ECHO}, params, db_session, t.id
            )
        return out["data"]

    def test_one_venue_in_the_organisation_is_used(self, db_session, two_orgs):
        # one venue in A (and another in B, which doesn't count)
        out = self._run(db_session, two_orgs.user, two_orgs.a)
        assert (out["venue"], out["venue_id"]) == ("Venue A", two_orgs.va.id)

    def test_a_named_venue_or_a_group_call_is_left_alone(self, db_session, two_orgs):
        out = self._run(db_session, two_orgs.user, two_orgs.a, venue="Somewhere")
        assert out["venue"] == "Somewhere" and out["venue_id"] is None
        out = self._run(db_session, two_orgs.user, two_orgs.a, venues="all")
        assert out["venue"] is None and out["venues"] == "all"

    def test_with_several_venues_the_model_chose_and_nothing_is_filled(
        self, db_session, two_orgs
    ):
        grant_venue(
            db_session, two_orgs.user, make_venue(db_session, "Venue A2", two_orgs.a)
        )
        out = self._run(db_session, two_orgs.user, two_orgs.a)
        assert out["venue"] is None and out["venue_id"] is None
