"""Tests for venue deletion.

A venue with tool_call history could not be deleted at all: 13 tables carry a
venues.id foreign key and none of them cascade, but the endpoint only cleared
two (user_venue_access, connector_configs). The final DELETE then raised

    update or delete on table "venues" violates foreign key constraint
    "tool_calls_venue_id_fkey" on table "tool_calls"

so the venue was stuck. The tests below cover both halves of the intended
behaviour — venue-scoped config is removed, history survives with a null venue —
plus a guard that fails if a new venue-referencing table is added and not
handled, which is how this bug would otherwise come back.
"""

import uuid

import pytest

from app.db.models import (
    ConnectorConfig,
    Thread,
    ToolCall,
    UserVenueAccess,
    Venue,
)
from tests.conftest import _make_organization, _make_user, _make_venue


@pytest.fixture
def org(db_session):
    return _make_organization(db_session)


@pytest.fixture
def venue(db_session, org):
    return _make_venue(db_session, name="Doomed Venue", organization_id=org.id)


class TestDeleteVenue:
    def test_venue_with_tool_call_history_can_be_deleted(
        self, client, db_session, admin_user, admin_headers, venue
    ):
        """The exact regression: tool_calls blocked the delete entirely."""
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="complete",
            intent="x",
            raw_prompt="p",
            venue_id=venue.id,
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(thread)
        db_session.flush()
        db_session.add(
            ToolCall(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                venue_id=venue.id,
                iteration=1,
                tool_name="loadedhub__get_sales_data",
                connector_name="loadedhub",
                action="get_sales_data",
                method="GET",
                status="executed",
            )
        )
        db_session.flush()

        resp = client.delete(f"/api/venues/{venue.id}", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        assert db_session.query(Venue).filter(Venue.id == venue.id).first() is None

    def test_history_survives_with_the_venue_reference_dropped(
        self, client, db_session, admin_user, admin_headers, venue
    ):
        """Deleting a venue must not erase the record of what happened there."""
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="complete",
            intent="x",
            raw_prompt="p",
            venue_id=venue.id,
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(thread)
        db_session.flush()
        tc = ToolCall(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            venue_id=venue.id,
            iteration=1,
            tool_name="loadedhub__get_sales_data",
            connector_name="loadedhub",
            action="get_sales_data",
            method="GET",
            status="executed",
        )
        db_session.add(tc)
        db_session.flush()

        resp = client.delete(f"/api/venues/{venue.id}", headers=admin_headers)
        assert resp.status_code == 200

        db_session.expire_all()
        assert (
            db_session.query(Thread).filter(Thread.id == thread.id).first() is not None
        )
        surviving = db_session.query(ToolCall).filter(ToolCall.id == tc.id).first()
        assert surviving is not None, "tool call history must not be deleted"
        assert surviving.venue_id is None, "venue reference should be cleared"

    def test_venue_scoped_config_is_removed(
        self, client, db_session, admin_user, admin_headers, venue
    ):
        """Access grants and connector configs (incl. OAuth tokens) must not linger."""
        db_session.add(
            UserVenueAccess(
                id=str(uuid.uuid4()), user_id=admin_user.id, venue_id=venue.id
            )
        )
        db_session.add(
            ConnectorConfig(
                connector_name="loadedhub",
                venue_id=venue.id,
                config={},
                enabled="true",
                access_token="secret-token",
                refresh_token="secret-refresh",
            )
        )
        db_session.flush()

        resp = client.delete(f"/api/venues/{venue.id}", headers=admin_headers)
        assert resp.status_code == 200

        db_session.expire_all()
        assert (
            db_session.query(UserVenueAccess)
            .filter(UserVenueAccess.venue_id == venue.id)
            .count()
            == 0
        )
        assert (
            db_session.query(ConnectorConfig)
            .filter(ConnectorConfig.venue_id == venue.id)
            .count()
            == 0
        ), "OAuth tokens must not outlive the venue"

    def test_delete_nonexistent_venue_returns_404(self, client, admin_headers):
        resp = client.delete(f"/api/venues/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404


class TestEveryVenueReferenceIsHandled:
    """Guard: a new venue-referencing table must be handled by delete_venue.

    None of the venue foreign keys cascade, so any table added later that
    references venues.id will silently make venues undeletable again — with no
    failing test, because the break only shows up for venues that happen to have
    data in that table.
    """

    def test_delete_venue_covers_every_table_with_a_venue_fk(self):
        import inspect

        from app.db import models as m
        from app.routers.organizations import delete_venue

        source = inspect.getsource(delete_venue)

        referencing = set()
        for name in dir(m):
            obj = getattr(m, name)
            table = getattr(obj, "__tablename__", None)
            if table and hasattr(obj, "venue_id") and name != "Venue":
                referencing.add(name)

        unhandled = {name for name in referencing if name not in source}
        assert not unhandled, (
            f"These models reference venues.id but delete_venue doesn't handle "
            f"them: {sorted(unhandled)}. Venue FKs don't cascade, so deleting a "
            f"venue with rows in these tables will fail. Either delete the rows "
            f"(venue-scoped config) or null venue_id (history)."
        )
