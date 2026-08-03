"""Venue access is scoped to the user's list — fail CLOSED, no admin bypass.

The bug: get_user_venues returned EVERY venue on the platform (no org filter)
for a user with no UserVenueAccess rows — a stale "migration period" fallback.
In a multi-org setup that meant seeing another org's venue in the conversation
picker ("a venue shows up but isn't on my company list"), and POST /messages
trusted whatever venue_id it was handed. These pin the closed behaviour.
"""

import uuid

from app.db.models import Organization, UserVenueAccess, Venue
from app.services.venue_service import get_user_venues, user_can_access_venue


def _venue(db, name="Other Org Venue", org_id=None):
    if org_id is None:
        org = Organization(
            id=str(uuid.uuid4()), name="Rival", slug=f"rival-{uuid.uuid4().hex[:8]}"
        )
        db.add(org)
        db.flush()
        org_id = org.id
    v = Venue(
        id=str(uuid.uuid4()),
        name=name,
        timezone="Pacific/Auckland",
        organization_id=org_id,
    )
    db.add(v)
    db.flush()
    return v


class TestGetUserVenues:
    def test_no_access_rows_returns_nothing(self, db_session, admin_user):
        """The whole point: 0 rows means 0 venues, not every venue everywhere."""
        # A venue exists in another org that the user has NO access to.
        _venue(db_session)
        assert get_user_venues(db_session, admin_user.id) == []

    def test_scoped_to_the_access_list(self, db_session, admin_user, venue):
        """With access to one venue, only that venue — never a sibling org's."""
        _venue(db_session, name="Someone Else's")  # different org, no access
        db_session.add(UserVenueAccess(user_id=admin_user.id, venue_id=venue.id))
        db_session.flush()
        got = get_user_venues(db_session, admin_user.id)
        assert [v.id for v in got] == [venue.id]

    def test_system_context_still_sees_all(self, db_session, venue):
        """user_id=None is an internal/system call, not a user request."""
        _venue(db_session)
        got = get_user_venues(db_session, None)
        assert len(got) >= 2  # every venue, unscoped


class TestUserCanAccessVenue:
    def test_true_only_with_a_matching_access_row(self, db_session, admin_user, venue):
        assert user_can_access_venue(db_session, admin_user.id, venue.id) is False
        db_session.add(UserVenueAccess(user_id=admin_user.id, venue_id=venue.id))
        db_session.flush()
        assert user_can_access_venue(db_session, admin_user.id, venue.id) is True

    def test_no_venue_requested_is_allowed(self, db_session, admin_user):
        assert user_can_access_venue(db_session, admin_user.id, None) is True

    def test_system_context_allowed(self, db_session, venue):
        assert user_can_access_venue(db_session, None, venue.id) is True


class TestListVenuesEndpoint:
    def test_empty_without_access(self, client, admin_user, admin_headers, venue):
        """No admin_venue_access fixture here — the picker must come back empty."""
        resp = client.get("/api/venues", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["venues"] == []


class TestMessageVenueAuthorization:
    def test_post_message_rejects_inaccessible_venue(
        self, client, admin_user, admin_headers, venue
    ):
        """Even an admin can't message against a venue not on their list."""
        resp = client.post(
            "/api/messages",
            json={"message": "hi", "venue_id": venue.id},
            headers=admin_headers,
        )
        assert resp.status_code == 403
        assert "access" in resp.json()["detail"].lower()

    def test_stream_rejects_inaccessible_venue_before_streaming(
        self, client, admin_user, admin_headers, venue
    ):
        resp = client.post(
            "/api/messages/stream",
            json={"message": "hi", "venue_id": venue.id},
            headers=admin_headers,
        )
        assert resp.status_code == 403

    def test_no_venue_is_not_blocked(
        self, client, admin_user, admin_headers, monkeypatch
    ):
        """A message with no venue passes the check (handle_message is stubbed
        so we don't invoke the LLM)."""
        import app.routers.messages as m

        monkeypatch.setattr(m, "handle_message", lambda *a, **k: {"ok": True})
        resp = client.post(
            "/api/messages", json={"message": "hi"}, headers=admin_headers
        )
        assert resp.status_code == 200
