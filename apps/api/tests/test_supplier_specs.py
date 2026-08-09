"""Supplier invoice specs: CRUD router + the engine-only read handler.

The specs are admin-maintained per-supplier extraction notes (name + aliases +
instructions) the review engine appends to the PDF-extraction prompt. The
engine-side matching/injection is covered in
test_invoice_review_consolidator.py::TestSupplierSpecs.
"""

from app.agents.internal_tools import get_handler
from app.db.config_models import SupplierInvoiceSpec


class TestSupplierSpecCrud:
    def test_create_list_update_delete(self, client, admin_headers):
        created = client.post(
            "/api/supplier-invoice-specs",
            headers=admin_headers,
            json={
                "name": "Service Foods",
                "aliases": ["Service Foods Auckland"],
                "instructions": "CTN and UNIT columns are split.",
            },
        )
        assert created.status_code == 201, created.text
        spec = created.json()
        assert spec["name"] == "Service Foods"
        assert spec["aliases"] == ["Service Foods Auckland"]

        listed = client.get("/api/supplier-invoice-specs", headers=admin_headers)
        assert any(s["id"] == spec["id"] for s in listed.json()["specs"])

        updated = client.put(
            f"/api/supplier-invoice-specs/{spec['id']}",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False

        deleted = client.delete(
            f"/api/supplier-invoice-specs/{spec['id']}", headers=admin_headers
        )
        assert deleted.status_code == 200
        listed2 = client.get("/api/supplier-invoice-specs", headers=admin_headers)
        assert not any(s["id"] == spec["id"] for s in listed2.json()["specs"])

    def test_duplicate_name_conflicts(self, client, admin_headers):
        body = {"name": "Bidfood", "aliases": [], "instructions": ""}
        assert (
            client.post(
                "/api/supplier-invoice-specs", headers=admin_headers, json=body
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/supplier-invoice-specs", headers=admin_headers, json=body
            ).status_code
            == 409
        )

    def test_short_alias_rejected(self, client, admin_headers):
        res = client.post(
            "/api/supplier-invoice-specs",
            headers=admin_headers,
            json={"name": "Sawmill", "aliases": ["SF"], "instructions": ""},
        )
        assert res.status_code == 422
        assert "too short" in res.json()["detail"]

    def test_write_requires_platform_admin(self, client, manager_headers):
        res = client.post(
            "/api/supplier-invoice-specs",
            headers=manager_headers,
            json={"name": "X Foods", "aliases": [], "instructions": ""},
        )
        assert res.status_code == 403

    def test_list_requires_platform_admin(self, client, manager_headers):
        # Specs are site-wide config: admins only, even for viewing.
        res = client.get("/api/supplier-invoice-specs", headers=manager_headers)
        assert res.status_code == 403

    def test_main_prompt_row_protected(self, client, admin_headers):
        # The reserved "Main prompt" row (the engine's base extraction prompt)
        # can be edited but never deleted or renamed.
        created = client.post(
            "/api/supplier-invoice-specs",
            headers=admin_headers,
            json={"name": "Main prompt", "aliases": [], "instructions": "base"},
        )
        assert created.status_code == 201
        spec_id = created.json()["id"]
        renamed = client.put(
            f"/api/supplier-invoice-specs/{spec_id}",
            headers=admin_headers,
            json={"name": "Something else"},
        )
        assert renamed.status_code == 400
        deleted = client.delete(
            f"/api/supplier-invoice-specs/{spec_id}", headers=admin_headers
        )
        assert deleted.status_code == 400
        edited = client.put(
            f"/api/supplier-invoice-specs/{spec_id}",
            headers=admin_headers,
            json={"instructions": "edited base"},
        )
        assert edited.status_code == 200
        assert edited.json()["instructions"] == "edited base"


class TestGetSupplierInvoiceSpecsHandler:
    def test_returns_enabled_specs_only(
        self, client, db_session, admin_headers, monkeypatch
    ):
        # Seed via the same test DB the handler's config session points at
        # (locally main and config DB share the engine — see conftest).
        from app.db import engine as engine_mod

        db_session.add(
            SupplierInvoiceSpec(
                name="On", aliases=["On Foods"], instructions="i", enabled=True
            )
        )
        db_session.add(
            SupplierInvoiceSpec(name="Off", aliases=[], instructions="x", enabled=False)
        )
        db_session.flush()
        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
        # keep the handler from closing the shared test session
        monkeypatch.setattr(db_session, "close", lambda: None)

        handler = get_handler("norm", "get_supplier_invoice_specs")
        out = handler({}, db_session, None)
        assert out["success"] is True
        names = [s["name"] for s in out["data"]["specs"]]
        assert "On" in names and "Off" not in names

    def test_failure_degrades_to_empty(self, db_session, monkeypatch):
        from app.db import engine as engine_mod

        def boom():
            raise RuntimeError("config db down")

        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", boom)
        handler = get_handler("norm", "get_supplier_invoice_specs")
        out = handler({}, db_session, None)
        assert out["success"] is True
        assert out["data"]["specs"] == []
