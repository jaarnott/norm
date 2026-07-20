"""App-support tools — the callback surface for embedded MCP Apps.

These tools let the purchase-order card in Claude read/edit its draft and, on
an explicit user click, submit the order. The model can call them directly, so
every handler must re-verify venue and ownership server-side — these tests are
that argument, in the same spirit as test_mcp_execution.py:

- a venue_id argument is input to be checked, never an assertion;
- another user's draft must be indistinguishable from a missing one;
- the read bridge must not become "run any configured HTTP call";
- the submit path is scope-gated behind mcp:orders:submit, a scope that must
  stay individually allowlisted (scopes.py) rather than becoming a pattern.
"""

import uuid

import pytest

from app.db.models import (
    Organization,
    Thread,
    User,
    UserVenueAccess,
    Venue,
    WorkingDocument,
)
from app.mcp.app_tools import (
    AppToolError,
    COMPONENT_API_ALLOWLIST,
    PAGE_SIZE,
    app_tool_defs,
    execute_app_tool,
)
from app.mcp.principal import McpPrincipal


# ── Fixtures (test_mcp_execution.py pattern) ─────────────────────────────


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


def _principal(user, org, venues, scopes=("mcp:orders:read", "mcp:orders:draft")):
    return McpPrincipal(
        user_id=user.id,
        organization_id=org.id,
        venue_ids=tuple(v.id for v in venues),
        scopes=frozenset(scopes),
    )


def _draft(db, user, venue, data=None, sync_mode="submit"):
    t = Thread(
        id=str(uuid.uuid4()),
        user_id=user.id,
        venue_id=venue.id,
        domain="procurement",
        intent="procurement.tool_use",
        status="completed",
        raw_prompt="order",
        extracted_fields={},
        missing_fields=[],
    )
    db.add(t)
    db.flush()
    doc = WorkingDocument(
        id=str(uuid.uuid4()),
        thread_id=t.id,
        venue_id=venue.id,
        doc_type="order",
        connector_name="norm",
        sync_mode=sync_mode,
        data=data
        or {
            "venue": venue.name,
            "venue_id": venue.id,
            "order_lines": [{"itemId": "item-1", "quantity": 3}],
            "lines": [
                {"product": "Steinlager", "quantity": 3, "unit_price": 2.0},
            ],
        },
        version=1,
    )
    db.add(doc)
    db.flush()
    return t, doc


# ── Projection ───────────────────────────────────────────────────────────


class TestProjection:
    def test_defs_empty_when_ui_disabled(self):
        """No embedded apps -> no callers -> no callback surface."""
        assert app_tool_defs(False) == []

    def test_all_four_tools_defined(self):
        names = {d["name"] for d in app_tool_defs(True)}
        assert names == {
            "norm__get_working_document",
            "norm__update_working_document",
            "norm__component_api",
            "norm__place_stock_order",
        }

    def test_submit_requires_its_own_scope(self):
        """Place Order must not ride along on the draft scope — its consent
        text is the whole justification for the write."""
        defs = {d["name"]: d for d in app_tool_defs(True)}
        assert defs["norm__place_stock_order"]["scopes"] == {"mcp:orders:submit"}
        assert defs["norm__update_working_document"]["scopes"] == {"mcp:orders:draft"}

    def test_project_tools_gates_on_scopes(self, db_session):
        # Locally and in CI the config tables share the test DB (see
        # conftest.client), so one session serves both roles.
        from app.mcp.projection import project_tools

        names_without = {
            t.name
            for t in project_tools(db_session, db_session, granted_scopes=frozenset())
        }
        names_with = {
            t.name
            for t in project_tools(
                db_session,
                db_session,
                granted_scopes=frozenset(
                    {"mcp:orders:read", "mcp:orders:draft", "mcp:orders:submit"}
                ),
            )
        }
        assert "norm__place_stock_order" not in names_without
        assert "norm__component_api" not in names_without
        assert {"norm__place_stock_order", "norm__component_api"} <= names_with
        app_tools = [
            t
            for t in project_tools(
                db_session,
                db_session,
                granted_scopes=frozenset({"mcp:orders:submit"}),
            )
            if t.kind == "app"
        ]
        assert [t.name for t in app_tools] == ["norm__place_stock_order"]
        assert app_tools[0].access == "write"


# ── Working documents ────────────────────────────────────────────────────


class TestWorkingDocuments:
    def test_read_own_draft(self, db_session):
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, v)
        _t, doc = _draft(db_session, u, v)
        out = execute_app_tool(
            "norm__get_working_document",
            {"working_document_id": doc.id},
            _principal(u, org, [v]),
            db_session,
            None,
        )
        assert out["id"] == doc.id
        assert out["version"] == 1
        assert out["data"]["lines"][0]["product"] == "Steinlager"

    def test_another_users_draft_reads_as_missing(self, db_session):
        """Indistinguishable from a nonexistent id — no enumeration oracle."""
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        owner, caller = _user(db_session), _user(db_session)
        _grant(db_session, owner, v)
        _grant(db_session, caller, v)
        _t, doc = _draft(db_session, owner, v)
        with pytest.raises(AppToolError) as exc:
            execute_app_tool(
                "norm__get_working_document",
                {"working_document_id": doc.id},
                _principal(caller, org, [v]),
                db_session,
                None,
            )
        missing = str(exc.value)
        with pytest.raises(AppToolError) as exc2:
            execute_app_tool(
                "norm__get_working_document",
                {"working_document_id": "no-such-doc"},
                _principal(caller, org, [v]),
                db_session,
                None,
            )
        assert str(exc2.value) == missing

    def test_update_applies_ops_and_bumps_version(self, db_session, monkeypatch):
        import app.routers.working_documents as wd_router

        monkeypatch.setattr(wd_router, "_trigger_sync", lambda doc_id: None)
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, v)
        _t, doc = _draft(db_session, u, v)
        out = execute_app_tool(
            "norm__update_working_document",
            {
                "working_document_id": doc.id,
                "ops": [{"op": "update_line", "index": 0, "fields": {"quantity": 5}}],
                "version": 1,
            },
            _principal(u, org, [v]),
            db_session,
            None,
        )
        assert out["version"] == 2
        assert out["data"]["lines"][0]["quantity"] == 5

    def test_update_with_stale_version_conflicts_without_applying(self, db_session):
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, v)
        _t, doc = _draft(db_session, u, v)
        doc.version = 4
        db_session.flush()
        out = execute_app_tool(
            "norm__update_working_document",
            {
                "working_document_id": doc.id,
                "ops": [{"op": "update_notes", "value": "x"}],
                "version": 1,
            },
            _principal(u, org, [v]),
            db_session,
            None,
        )
        assert out["conflict"] is True
        assert out["expected_version"] == 4
        assert doc.data.get("notes") != "x"

    def test_update_requires_ops(self, db_session):
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        _grant(db_session, u, v)
        _t, doc = _draft(db_session, u, v)
        with pytest.raises(AppToolError):
            execute_app_tool(
                "norm__update_working_document",
                {"working_document_id": doc.id, "ops": [], "version": 1},
                _principal(u, org, [v]),
                db_session,
                None,
            )


# ── Component API bridge ─────────────────────────────────────────────────


class TestComponentApiBridge:
    def _authorized(self, db):
        org = _org(db)
        v = _venue(db, "La Zeppa", org)
        u = _user(db)
        _grant(db, u, v)
        return org, v, u

    def test_only_allowlisted_actions(self, db_session):
        org, v, u = self._authorized(db_session)
        with pytest.raises(AppToolError) as exc:
            execute_app_tool(
                "norm__component_api",
                {
                    "venue_id": v.id,
                    "component_key": "purchase_order_editor",
                    "action_name": "create_orders_batch",
                },
                _principal(u, org, [v]),
                db_session,
                None,
            )
        assert "not available" in str(exc.value)
        # And the allowlist itself must never contain the submit action.
        assert ("purchase_order_editor", "create_orders_batch") not in (
            COMPONENT_API_ALLOWLIST
        )

    def test_unconsented_venue_fails_closed(self, db_session):
        org, v, u = self._authorized(db_session)
        other = _venue(db_session, "Elsewhere", org)
        with pytest.raises(AppToolError):
            execute_app_tool(
                "norm__component_api",
                {
                    "venue_id": other.id,
                    "component_key": "purchase_order_editor",
                    "action_name": "get_suppliers",
                },
                _principal(u, org, [v]),  # other not consented
                db_session,
                None,
            )

    def test_revoked_access_fails_closed(self, db_session):
        """Consent froze the venue in the token; live access was revoked
        since. The live check must win."""
        org, v, u = self._authorized(db_session)
        principal = _principal(u, org, [v])
        db_session.query(UserVenueAccess).filter(
            UserVenueAccess.user_id == u.id
        ).delete()
        db_session.flush()
        with pytest.raises(AppToolError):
            execute_app_tool(
                "norm__component_api",
                {
                    "venue_id": v.id,
                    "component_key": "purchase_order_editor",
                    "action_name": "get_suppliers",
                },
                principal,
                db_session,
                None,
            )

    def test_cross_org_venue_fails_closed(self, db_session):
        org, v, u = self._authorized(db_session)
        other_org = _org(db_session, "Rival Group")
        foreign = _venue(db_session, "Foreign", other_org)
        _grant(db_session, u, foreign)
        with pytest.raises(AppToolError):
            execute_app_tool(
                "norm__component_api",
                {
                    "venue_id": foreign.id,
                    "component_key": "purchase_order_editor",
                    "action_name": "get_suppliers",
                },
                _principal(u, org, [v, foreign]),
                db_session,
                None,
            )

    def test_large_lists_are_paged_and_stock_items_slimmed(
        self, db_session, monkeypatch
    ):
        org, v, u = self._authorized(db_session)
        items = [
            {
                "id": f"i-{n}",
                "name": f"Item {n}",
                "groupName": "Beer",
                "countingUnitId": "drop-me",
                "suppliers": [
                    {
                        "id": "v1",
                        "supplierId": "s1",
                        "unitCost": 1.0,
                        "description": "drop",
                    }
                ],
            }
            for n in range(PAGE_SIZE * 2 + 10)
        ]
        monkeypatch.setattr(
            "app.services.component_api.execute_component_action",
            lambda *a, **k: {"data": items, "status_code": 200},
        )
        out = execute_app_tool(
            "norm__component_api",
            {
                "venue_id": v.id,
                "component_key": "purchase_order_editor",
                "action_name": "get_stock_items_detail",
                "page": 2,
            },
            _principal(u, org, [v]),
            db_session,
            None,
        )
        assert out["total_pages"] == 3
        assert out["page"] == 2
        assert len(out["data"]) == 10
        assert out["total_items"] == PAGE_SIZE * 2 + 10
        first = out["data"][0]
        assert "countingUnitId" not in first  # slimmed
        assert "description" not in first["suppliers"][0]
        assert first["suppliers"][0]["supplierId"] == "s1"


# ── Place order ──────────────────────────────────────────────────────────


class TestPlaceOrder:
    def _setup(self, db):
        org = _org(db)
        v = _venue(db, "La Zeppa", org)
        u = _user(db)
        _grant(db, u, v)
        return org, v, u

    def test_requires_batches_with_lines(self, db_session):
        org, v, u = self._setup(db_session)
        p = _principal(u, org, [v], scopes=("mcp:orders:submit",))
        for bad in ([], [{"supplierId": "s", "lines": []}], "nope"):
            with pytest.raises(AppToolError):
                execute_app_tool(
                    "norm__place_stock_order",
                    {"venue_id": v.id, "orders": bad},
                    p,
                    db_session,
                    None,
                )

    def test_submits_through_the_component_action(self, db_session, monkeypatch):
        org, v, u = self._setup(db_session)
        seen = {}

        def fake_execute(component_key, action_name, params, venue_id, db, config_db):
            seen.update(
                component=component_key,
                action=action_name,
                params=params,
                venue=venue_id,
            )
            return {"data": {"ok": True}, "status_code": 200}

        monkeypatch.setattr(
            "app.services.component_api.execute_component_action", fake_execute
        )
        orders = [
            {"supplierId": "s1", "lines": [{"itemId": "i", "quantityOrdered": 2}]}
        ]
        out = execute_app_tool(
            "norm__place_stock_order",
            {"venue_id": v.id, "orders": orders},
            _principal(u, org, [v], scopes=("mcp:orders:submit",)),
            db_session,
            None,
        )
        assert out["submitted"] is True
        assert seen["component"] == "purchase_order_editor"
        assert seen["action"] == "create_orders_batch"
        assert seen["params"] == orders
        assert seen["venue"] == v.id

    def test_upstream_refusal_is_data_not_success(self, db_session, monkeypatch):
        org, v, u = self._setup(db_session)
        monkeypatch.setattr(
            "app.services.component_api.execute_component_action",
            lambda *a, **k: {
                "data": {"detail": "supplier closed"},
                "status_code": 422,
                "error": True,
            },
        )
        out = execute_app_tool(
            "norm__place_stock_order",
            {
                "venue_id": v.id,
                "orders": [{"lines": [{"itemId": "i"}]}],
            },
            _principal(u, org, [v], scopes=("mcp:orders:submit",)),
            db_session,
            None,
        )
        assert out["submitted"] is False
        assert out["status_code"] == 422


# ── Scope vocabulary ─────────────────────────────────────────────────────


class TestSubmitScope:
    def test_vocabulary_is_valid(self):
        from app.mcp.scopes import validate_scope_vocabulary

        assert validate_scope_vocabulary() == []

    def test_submit_scope_requires_org_write(self):
        from app.mcp.scopes import MCP_SCOPES

        scope = MCP_SCOPES["mcp:orders:submit"]
        assert scope.access_level == "write"
        assert "orders:write" in scope.requires
        # The consent text must say the click submits — that sentence is the
        # authorization story for the only write on the surface.
        assert "Place Order" in scope.description

    def test_new_write_scopes_must_be_individually_allowlisted(self, monkeypatch):
        """Copying the pattern without the decision must fail validation."""
        from app.mcp import scopes as scopes_mod

        rogue = scopes_mod.McpScope(
            name="mcp:hr:write",
            label="x",
            description="x",
            access_level="write",
            requires=frozenset({"hr:read"}),
        )
        monkeypatch.setitem(scopes_mod.MCP_SCOPES, "mcp:hr:write", rogue)
        problems = scopes_mod.validate_scope_vocabulary()
        assert any("individually allowlisted" in p for p in problems)


# ── Dispatch ─────────────────────────────────────────────────────────────


class TestDispatch:
    def test_unknown_app_tool_refused(self, db_session):
        org = _org(db_session)
        v = _venue(db_session, "La Zeppa", org)
        u = _user(db_session)
        with pytest.raises(AppToolError):
            execute_app_tool(
                "norm__not_a_tool", {}, _principal(u, org, [v]), db_session, None
            )
