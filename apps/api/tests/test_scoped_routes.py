"""The doors that let a signed-in user reach past their own venues (Oct 2026).

- /working-documents/from-connector ran ANY endpoint — writes included — and
  any built-in, for any signed-in user, and with no venue it used whichever
  venue's login came first.
- every working-document route found a document by id alone, so anyone could
  read or patch one, and a patch syncs: a write to the venue's Loaded.
- MCP passed `venues` straight through — 'all' listed every venue in the
  database, and a name reached any organisation's venue.
- a chart's "all venues" read every connected venue in the database, and a
  dashboard filter's venue was never checked.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.auth.security import create_access_token
from app.db.models import (
    Connection,
    Organization,
    User,
    UserVenueAccess,
    Venue,
    WorkingDocument,
)
from tests.conftest import _make_membership, _make_thread


def _org(db, name):
    o = Organization(id=str(uuid.uuid4()), name=name, slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(o)
    db.flush()
    return o


def _venue(db, name, org):
    v = Venue(id=str(uuid.uuid4()), name=name, organization_id=org.id)
    db.add(v)
    db.flush()
    return v


def _user(db, role="user"):
    u = User(
        id=str(uuid.uuid4()),
        email=f"u-{uuid.uuid4().hex[:8]}@x.com",
        hashed_password="x",
        full_name="U",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def world(db_session):
    ours, rival = _org(db_session, "Ours"), _org(db_session, "Rival")
    w = SimpleNamespace(
        ours=ours,
        rival=rival,
        mine=_venue(db_session, "Mine", ours),
        theirs=_venue(db_session, "Theirs", rival),
        user=_user(db_session),
    )
    _make_membership(db_session, w.user, ours)
    db_session.add(
        UserVenueAccess(id=str(uuid.uuid4()), user_id=w.user.id, venue_id=w.mine.id)
    )
    db_session.flush()
    w.headers = {"Authorization": f"Bearer {create_access_token({'sub': w.user.id})}"}
    return w


def _doc(db, *, venue=None, thread=None):
    d = WorkingDocument(
        id=str(uuid.uuid4()),
        thread_id=thread.id if thread else None,
        doc_type="roster",
        connector_name="loadedhub",
        venue_id=venue.id if venue else None,
        sync_mode="submit",
        data=[{"id": "r", "rosteredShifts": []}],
        external_ref={},
        sync_status="synced",
        version=1,
    )
    db.add(d)
    db.flush()
    return d


class TestFromConnector:
    def _post(self, client, w, **body):
        return client.post(
            "/api/working-documents/from-connector",
            json={"connector_name": "loadedhub", "action": "get_roster", **body},
            headers=w.headers,
        )

    def test_a_venue_the_user_cannot_access_is_refused(self, client, world):
        r = self._post(client, world, venue_id=world.theirs.id)
        assert r.status_code == 403

    def test_a_built_in_a_page_does_not_load_is_refused(self, client, world):
        r = client.post(
            "/api/working-documents/from-connector",
            json={"connector_name": "norm", "action": "list_venues"},
            headers=world.headers,
        )
        assert r.status_code == 403

    def test_a_write_endpoint_is_refused(self, client, world, db_session):
        from app.db.config_models import ConnectionSpec

        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="writehub",
                display_name="W",
                auth_type="none",
                auth_config={},
                tools=[],
                endpoints=[
                    {"action": "publish", "method": "PUT", "path_template": "/x"}
                ],
                enabled=True,
            )
        )
        db_session.flush()
        r = client.post(
            "/api/working-documents/from-connector",
            json={
                "connector_name": "writehub",
                "action": "publish",
                "venue_id": world.mine.id,
            },
            headers=world.headers,
        )
        assert r.status_code == 403 and "not a read" in r.text

    def test_no_venue_never_borrows_a_venues_login(self, client, world, db_session):
        """Without a venue it took whichever venue's login came first."""
        from app.db.config_models import ConnectionSpec

        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="readhub",
                display_name="R",
                auth_type="none",
                auth_config={},
                tools=[],
                endpoints=[{"action": "list", "method": "GET", "path_template": "/x"}],
                enabled=True,
            )
        )
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="readhub",
                venue_id=world.theirs.id,
                config={"token": "theirs"},
                enabled="true",
            )
        )
        db_session.flush()
        r = client.post(
            "/api/working-documents/from-connector",
            json={"connector_name": "readhub", "action": "list"},
            headers=world.headers,
        )
        assert r.status_code == 400 and "No credentials" in r.text

    def test_a_chat_card_document_belongs_to_its_thread(
        self, client, world, db_session, monkeypatch
    ):
        import app.connectors.spec_executor as se
        from app.db.config_models import ConnectionSpec

        def fake_execute_spec(spec, tool_def, params, creds, db, **kw):
            return SimpleNamespace(
                success=True, response_payload=[{"id": "r"}], error_message=None
            ), None

        monkeypatch.setattr(se, "execute_spec", fake_execute_spec)
        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="rosterhub",
                display_name="R",
                auth_type="none",
                auth_config={},
                tools=[],
                endpoints=[
                    {"action": "get_roster", "method": "GET", "path_template": "/r"}
                ],
                enabled=True,
            )
        )
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="rosterhub",
                venue_id=world.mine.id,
                config={},
                enabled="true",
            )
        )
        db_session.flush()

        def post(thread):
            return client.post(
                "/api/working-documents/from-connector",
                json={
                    "connector_name": "rosterhub",
                    "action": "get_roster",
                    "doc_type": "roster",
                    "venue_id": world.mine.id,
                    "thread_id": thread.id,
                },
                headers=world.headers,
            )

        r = post(_make_thread(db_session, world.user))
        assert r.status_code == 200, r.text
        assert r.json()["thread_id"]
        assert post(_make_thread(db_session, _user(db_session))).status_code == 404


class TestDocumentAccess:
    def test_another_venues_document_is_not_found(self, client, world, db_session):
        doc = _doc(db_session, venue=world.theirs)
        assert (
            client.get(f"/api/working-documents/{doc.id}", headers=world.headers)
        ).status_code == 404
        r = client.patch(
            f"/api/working-documents/{doc.id}",
            json={"ops": [], "version": 1},
            headers=world.headers,
        )
        assert r.status_code == 404
        r = client.get(
            f"/api/threads/x/working-documents/{doc.id}", headers=world.headers
        )
        assert r.status_code == 404

    def test_my_venues_document_opens(self, client, world, db_session):
        doc = _doc(db_session, venue=world.mine)
        r = client.get(f"/api/working-documents/{doc.id}", headers=world.headers)
        assert r.status_code == 200

    def test_a_document_without_a_venue_is_its_threads(self, client, world, db_session):
        mine = _doc(db_session, thread=_make_thread(db_session, world.user))
        theirs = _doc(db_session, thread=_make_thread(db_session, _user(db_session)))
        assert (
            client.get(f"/api/working-documents/{mine.id}", headers=world.headers)
        ).status_code == 200
        assert (
            client.get(f"/api/working-documents/{theirs.id}", headers=world.headers)
        ).status_code == 404

    def test_another_users_thread_lists_nothing(self, client, world, db_session):
        thread = _make_thread(db_session, _user(db_session))
        r = client.get(
            f"/api/threads/{thread.id}/working-documents", headers=world.headers
        )
        assert r.status_code == 404


class TestMcpVenues:
    def _ctx(self, db, w, venues):
        from app.mcp.execution import NormMcpContext
        from app.mcp.principal import McpPrincipal
        from app.mcp.projection import McpTool

        tool = McpTool(
            name="loadedhub__get_sales",
            kind="connector",
            connector="loadedhub",
            action="get_sales",
            playbook_slug=None,
            method="GET",
            access="read",
            scopes=frozenset({"mcp:reports:read"}),
            description="",
            input_schema={},
            multi_venue=True,
        )
        principal = McpPrincipal(
            user_id=w.user.id,
            organization_id=w.ours.id,
            venue_ids=tuple(v.id for v in venues),
            scopes=frozenset({"mcp:reports:read"}),
        )
        ctx = NormMcpContext(principal=principal, db=db, config_db=db)
        ctx._tools = {tool.name: tool}
        seen = []

        def fake_execute(_tool, params, venue_id):
            seen.append(dict(params))
            return SimpleNamespace(success=True, payload={"ok": True}, error=None)

        ctx._execute = fake_execute
        ctx.seen = seen
        return ctx

    def test_all_means_this_principals_venues(self, db_session, world):
        ctx = self._ctx(db_session, world, [world.mine])
        ctx.call_tool("loadedhub__get_sales", {"venues": "all", "period": "today"})
        assert ctx.seen[-1]["venues"] == ["Mine"]

    def test_another_organisations_venue_is_refused(self, db_session, world):
        ctx = self._ctx(db_session, world, [world.mine])
        out = ctx.call_tool(
            "loadedhub__get_sales", {"venues": ["Theirs"], "period": "today"}
        )
        assert out.get("isError") and not ctx.seen

    def test_an_html_escaped_name_is_read_as_meant(self, db_session, world):
        amp = _venue(db_session, "Freeman & Grey", world.ours)
        db_session.add(
            UserVenueAccess(
                id=str(uuid.uuid4()), user_id=world.user.id, venue_id=amp.id
            )
        )
        db_session.flush()
        ctx = self._ctx(db_session, world, [world.mine, amp])
        ctx.call_tool(
            "loadedhub__get_sales", {"venue": "Freeman &amp; Grey", "period": "today"}
        )
        ctx.call_tool(
            "loadedhub__get_sales",
            {
                "venue": "Mine",
                "venues": '["Freeman &amp; Grey", "Mine"]',
                "period": "today",
            },
        )
        assert ctx.seen and ctx.seen[-1]["venues"] == ["Freeman & Grey", "Mine"]


class TestChartVenues:
    def test_all_venues_is_the_users_organisation(self, db_session, world):
        from app.routers.reports_crud import _chart_venue_ids

        for v in (world.mine, world.theirs):
            db_session.add(
                Connection(
                    id=str(uuid.uuid4()),
                    connector_name="charthub",
                    venue_id=v.id,
                    config={},
                    enabled="true",
                )
            )
        db_session.flush()
        assert _chart_venue_ids(db_session, world.user, "charthub", None) == [
            world.mine.id
        ]

    def test_a_filter_venue_must_be_the_users(self, db_session, world):
        from fastapi import HTTPException

        from app.routers.reports_crud import _chart_venue_ids

        with pytest.raises(HTTPException) as e:
            _chart_venue_ids(db_session, world.user, "charthub", world.theirs.id)
        assert e.value.status_code == 403
