"""Playbook results -> display blocks (po_display.py).

The create_stock_order playbook renders the REAL PurchaseOrderEditor in
Claude. That only works if the block carries lines the component can draw
without fetching — resolved server-side, mirroring the component's own
auto-resolve — and if those lines are persisted into the draft so the app's
`update_line` patches land on real rows instead of an empty array.
"""

import uuid

import pytest

from app.db.models import Organization, Thread, User, Venue, WorkingDocument
from app.mcp.po_display import _resolve_lines, playbook_display_block
from app.mcp.principal import McpPrincipal

VENUE_TZ = "Pacific/Auckland"


def _org(db):
    o = Organization(id=str(uuid.uuid4()), name="CB", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(o)
    db.flush()
    return o


def _venue(db, org, name="La Zeppa"):
    v = Venue(
        id=str(uuid.uuid4()), name=name, timezone=VENUE_TZ, organization_id=org.id
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


def _principal(user, org, venue):
    return McpPrincipal(
        user_id=user.id,
        organization_id=org.id,
        venue_ids=(venue.id,),
        scopes=frozenset({"mcp:orders:draft"}),
    )


def _draft(db, user, venue):
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
        sync_mode="submit",
        data={
            "venue": venue.name,
            "venue_id": venue.id,
            "order_lines": [{"itemId": "item-1", "quantity": 3}],
        },
        version=1,
    )
    db.add(doc)
    db.flush()
    return t, doc


REF_ITEMS = [
    {
        "id": "item-1",
        "name": "STEINLAGER PURE 330ML",
        "groupName": "Beer",
        "defaultSupplierId": "sup-lion",
        "globalSalesTaxSortOrder": 1,
        "globalPrice": 2.0,
        "orderingUnitId": "unit-24",
        "orderingUnitName": "24 Pack",
        "orderingUnitRatio": 24,
        "suppliers": [
            {
                "id": "var-a",
                "supplierId": "sup-other",
                "unitId": "unit-24",
                "unitCost": 1.8,
                "stockCode": "OTHER1",
                "brandId": None,
                "defaultForSupplier": True,
            },
            {
                "id": "var-b",
                "supplierId": "sup-lion",
                "unitId": "unit-24",
                "unitCost": 1.59,
                "stockCode": "1251707",
                "brandId": None,
                "defaultForSupplier": True,
            },
        ],
    }
]
REF_SUPPLIERS = [
    {"id": "sup-lion", "name": "Lion Nathan Liquor"},
    {"id": "sup-other", "name": "Other Beverages"},
]
REF_UNITS = [{"id": "unit-24", "name": "24 Pack", "ratio": 24}]


@pytest.fixture()
def fake_reference(monkeypatch):
    """Answer the component-api calls with canned reference data."""
    calls = []

    def fake_execute(component_key, action_name, params, venue_id, db, config_db):
        calls.append(action_name)
        data = {
            "get_stock_items_detail": REF_ITEMS,
            "get_suppliers": REF_SUPPLIERS,
            "get_units": REF_UNITS,
            "get_live_prices": {
                "itemCosts": {"item-1": [{"cost": 1.62, "unitName": "24 Pack"}]}
            },
        }[action_name]
        return {"data": data, "status_code": 200}

    monkeypatch.setattr(
        "app.services.component_api.execute_component_action", fake_execute
    )
    return calls


class TestResolveLines:
    def test_resolves_default_variant_names_and_live_price(
        self, db_session, fake_reference
    ):
        org = _org(db_session)
        v = _venue(db_session, org)
        lines = _resolve_lines(
            [{"itemId": "item-1", "quantity": 3}], v.id, db_session, None
        )
        assert len(lines) == 1
        line = lines[0]
        # Default-supplier default variant wins (sup-lion), like the component.
        assert line["stock_code"] == "1251707"
        assert line["supplier"] == "Lion Nathan Liquor"
        assert line["product"] == "STEINLAGER PURE 330ML"
        assert line["quantity"] == 3
        assert line["unit"] == "24 Pack"
        assert line["taxPercent"] == 0.15
        # Live price overlays the variant's stored cost.
        assert line["unit_price"] == 1.62

    def test_explicit_supplier_hint_wins(self, db_session, fake_reference):
        org = _org(db_session)
        v = _venue(db_session, org)
        lines = _resolve_lines(
            [{"itemId": "item-1", "quantity": 1, "supplierId": "sup-other"}],
            v.id,
            db_session,
            None,
        )
        assert lines[0]["stock_code"] == "OTHER1"
        assert lines[0]["supplier"] == "Other Beverages"

    def test_unknown_item_is_skipped(self, db_session, fake_reference):
        org = _org(db_session)
        v = _venue(db_session, org)
        lines = _resolve_lines(
            [{"itemId": "nope", "quantity": 1}, {"itemId": "item-1", "quantity": 2}],
            v.id,
            db_session,
            None,
        )
        assert [line["product"] for line in lines] == ["STEINLAGER PURE 330ML"]

    def test_reference_failure_returns_empty(self, db_session, monkeypatch):
        from app.services.component_api import ComponentApiError

        def boom(*a, **k):
            raise ComponentApiError("no credentials")

        monkeypatch.setattr("app.services.component_api.execute_component_action", boom)
        org = _org(db_session)
        v = _venue(db_session, org)
        assert _resolve_lines([{"itemId": "item-1"}], v.id, db_session, None) == []


class TestPlaybookDisplayBlock:
    def test_draft_order_renders_the_editor_and_persists_lines(
        self, db_session, fake_reference
    ):
        org = _org(db_session)
        v = _venue(db_session, org)
        u = _user(db_session)
        t, doc = _draft(db_session, u, v)
        payload = {
            "status": "draft_created",
            "working_document_id": doc.id,
            "doc_type": "order",
            "open_in_norm": "https://x/app?doc=1",
        }
        block = playbook_display_block(
            payload, v.id, _principal(u, org, v), db_session, db_session
        )
        assert block["component"] == "purchase_order_editor"
        assert block["data"]["working_document_id"] == doc.id
        assert block["data"]["thread_id"] == t.id
        assert block["props"]["activeVenueId"] == v.id
        assert block["props"]["thread_id"] == t.id
        assert "La Zeppa" in block["props"]["title"]
        assert block["data"]["lines"][0]["product"] == "STEINLAGER PURE 330ML"
        # Persisted: the app's update_line ops address doc.data["lines"], so
        # the resolved lines must exist in the draft, not just in the block.
        db_session.refresh(doc)
        assert doc.data["lines"][0]["stock_code"] == "1251707"
        # But never the block-only addressing fields.
        assert "working_document_id" not in doc.data
        assert "thread_id" not in doc.data

    def test_someone_elses_doc_id_gets_the_card_not_the_editor(
        self, db_session, fake_reference
    ):
        """The payload is worker output, not a trusted handle — a doc the
        principal doesn't own must not be rendered (or persisted to)."""
        org = _org(db_session)
        v = _venue(db_session, org)
        owner, caller = _user(db_session), _user(db_session)
        _t, doc = _draft(db_session, owner, v)
        payload = {
            "status": "draft_created",
            "working_document_id": doc.id,
            "doc_type": "order",
        }
        block = playbook_display_block(
            payload, v.id, _principal(caller, org, v), db_session, db_session
        )
        assert block["component"] == "workflow_result"

    def test_non_draft_outcomes_get_the_status_card(self, db_session):
        for status in ("completed", "running", "pending_approval"):
            block = playbook_display_block(
                {"status": status, "summary": "did things"},
                None,
                McpPrincipal(
                    user_id="u", organization_id="o", venue_ids=(), scopes=frozenset()
                ),
                db_session,
                db_session,
            )
            assert block["component"] == "workflow_result"
            assert block["data"]["status"] == status

    def test_resolution_crash_degrades_to_the_card(self, db_session, monkeypatch):
        """Display is enhancement: a bug here must not fail the workflow."""
        org = _org(db_session)
        v = _venue(db_session, org)
        u = _user(db_session)
        _t, doc = _draft(db_session, u, v)
        monkeypatch.setattr(
            "app.mcp.po_display._po_editor_block",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        block = playbook_display_block(
            {
                "status": "draft_created",
                "working_document_id": doc.id,
                "doc_type": "order",
            },
            v.id,
            _principal(u, org, v),
            db_session,
            db_session,
        )
        assert block["component"] == "workflow_result"
