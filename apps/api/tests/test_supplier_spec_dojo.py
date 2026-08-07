"""Supplier Spec Dojo: comparator, sample lifecycle, run endpoints, permissions.

The dojo re-extracts stored sample invoices under the CURRENT prompts and
diffs against an admin-accepted baseline (services/spec_dojo.py). The LLM
runner is monkeypatched here — canned extractions exercise the status
lifecycle (new → save expected → pass → drifted run → fail) without network.
"""

from app.db.config_models import SupplierInvoiceSpec, SupplierSpecSample
from app.services import spec_dojo


def _extraction(**over):
    base = {
        "document_type": "invoice",
        "invoice_number": "INV-1",
        "invoice_date": "2026-08-05",
        "purchase_order_number": "1520500",
        "subtotal_ex_tax": 100.0,
        "tax_amount": 15.0,
        "total_incl_tax": 115.0,
        "lines": [
            {
                "code": "A1",
                "description": "HONEY LIQUID",
                "quantity": 1,
                "unit_of_measure": "4kg",
                "unit_price_ex_tax": 77.54,
                "line_total_ex_tax": 77.54,
            },
            {
                "code": "B2",
                "description": "CIDER 330ML 4X6",
                "quantity": 2,
                "unit_of_measure": "4x6 pack",
                "unit_price_ex_tax": 11.23,
                "line_total_ex_tax": 22.46,
            },
        ],
    }
    base.update(over)
    return base


class TestCompareExtractions:
    def test_identical_passes(self):
        assert spec_dojo.compare_extractions(_extraction(), _extraction()) == []

    def test_unit_normalization_equivalence(self):
        # '12PK' and '12 pack' are the same printed unit; case/space ignored.
        exp = _extraction()
        cur = _extraction()
        exp["lines"][1]["unit_of_measure"] = "4x6 Pack"
        cur["lines"][1]["unit_of_measure"] = "4X6 pack"
        assert spec_dojo.compare_extractions(exp, cur) == []
        exp["lines"][1]["unit_of_measure"] = "12PK"
        cur["lines"][1]["unit_of_measure"] = "12 pack"
        assert spec_dojo.compare_extractions(exp, cur) == []

    def test_line_value_diffs_reported(self):
        cur = _extraction()
        cur["lines"][0]["quantity"] = 2
        cur["lines"][1]["unit_of_measure"] = "24 pack"
        diffs = spec_dojo.compare_extractions(_extraction(), cur)
        fields = {(d["field"], d["line"]) for d in diffs}
        assert ("quantity", 1) in fields
        assert ("unit_of_measure", 2) in fields
        qty = next(d for d in diffs if d["field"] == "quantity")
        assert qty["expected"] == 1 and qty["actual"] == 2

    def test_number_tolerance_one_cent(self):
        cur = _extraction()
        cur["lines"][0]["unit_price_ex_tax"] = 77.55  # 1c — within tolerance
        assert spec_dojo.compare_extractions(_extraction(), cur) == []
        cur["lines"][0]["unit_price_ex_tax"] = 77.60
        assert spec_dojo.compare_extractions(_extraction(), cur) != []

    def test_missing_and_extra_lines(self):
        cur = _extraction()
        cur["lines"] = cur["lines"][:1]
        diffs = spec_dojo.compare_extractions(_extraction(), cur)
        assert any(d["field"] == "line_missing" and d["line"] == 2 for d in diffs)
        cur2 = _extraction()
        cur2["lines"].append({"code": "C3", "description": "EXTRA", "quantity": 1})
        diffs2 = spec_dojo.compare_extractions(_extraction(), cur2)
        assert any(d["field"] == "line_extra" for d in diffs2)

    def test_header_diff_reported(self):
        cur = _extraction(invoice_number="INV-2")
        diffs = spec_dojo.compare_extractions(_extraction(), cur)
        assert any(d["field"] == "invoice_number" and d["line"] is None for d in diffs)

    def test_unrecognisable_flag_compared(self):
        cur = _extraction()
        cur["lines"][0]["unit_of_measure"] = None
        cur["lines"][0]["unit_unrecognisable"] = True
        diffs = spec_dojo.compare_extractions(_extraction(), cur)
        assert any(d["field"] == "unit_unrecognisable" for d in diffs)


class TestEditorPayload:
    def test_mismatches_ride_as_copy_fields(self):
        cur = _extraction()
        cur["lines"][0]["quantity"] = 3
        cur["lines"][0]["unit_of_measure"] = "16kg"
        cur["lines"][1]["unit_price_ex_tax"] = 12.00
        diffs = spec_dojo.compare_extractions(_extraction(), cur)
        data = spec_dojo.editor_payload(_extraction(), cur, diffs, "fail")
        l0, l1 = data["lines"]
        assert l0["quantity_received"] == 1  # baseline renders as the draft
        assert l0["copy_quantity"] == 3 and l0["copy_quantity_mismatch"] is True
        assert l0["recommended_unit"] == "16kg" and l0["copy_unit_mismatch"] is True
        assert l1["copy_unit_price"] == 12.00 and l1["copy_unit_cost_mismatch"] is True
        assert data["dojo_status"] == "fail"
        assert data["dojo_diffs"] == diffs

    def test_no_baseline_renders_current_plain(self):
        cur = _extraction()
        data = spec_dojo.editor_payload(None, cur, [], "new")
        assert data["lines"][0]["quantity_received"] == 1
        assert "copy_quantity_mismatch" not in data["lines"][0]
        assert data["dojo_status"] == "new"


def _make_spec(db, name="Dojo Foods"):
    spec = SupplierInvoiceSpec(name=name, aliases=[], instructions="notes")
    db.add(spec)
    db.flush()
    return spec


class TestSampleLifecycle:
    def _upload(self, client, admin_headers, spec_id, label="inv-1.pdf"):
        return client.post(
            f"/api/supplier-invoice-specs/{spec_id}/samples",
            headers=admin_headers,
            files={"file": (label, b"%PDF-1.4 fake", "application/pdf")},
        )

    def test_upload_list_pdf_delete(self, client, admin_headers, db_session):
        spec = _make_spec(db_session)
        up = self._upload(client, admin_headers, spec.id)
        assert up.status_code == 201, up.text
        meta = up.json()
        assert meta["label"] == "inv-1.pdf"
        assert meta["last_status"] == "new" and meta["has_expected"] is False

        listed = client.get(
            f"/api/supplier-invoice-specs/{spec.id}/samples", headers=admin_headers
        )
        assert [s["id"] for s in listed.json()["samples"]] == [meta["id"]]

        pdf = client.get(
            f"/api/supplier-invoice-specs/samples/{meta['id']}/pdf",
            headers=admin_headers,
        )
        assert pdf.status_code == 200
        assert pdf.content == b"%PDF-1.4 fake"
        assert pdf.headers["content-type"].startswith("application/pdf")

        deleted = client.delete(
            f"/api/supplier-invoice-specs/samples/{meta['id']}", headers=admin_headers
        )
        assert deleted.json()["deleted"] is True
        assert (
            client.get(
                f"/api/supplier-invoice-specs/{spec.id}/samples", headers=admin_headers
            ).json()["samples"]
            == []
        )

    def test_rejects_non_pdf_and_oversize(self, client, admin_headers, db_session):
        spec = _make_spec(db_session, "Reject Foods")
        bad = client.post(
            f"/api/supplier-invoice-specs/{spec.id}/samples",
            headers=admin_headers,
            files={"file": ("x.csv", b"a,b", "text/csv")},
        )
        assert bad.status_code == 400

    def test_run_baseline_pass_fail_lifecycle(
        self, client, admin_headers, db_session, monkeypatch
    ):
        spec = _make_spec(db_session, "Lifecycle Foods")
        sample_id = self._upload(client, admin_headers, spec.id).json()["id"]

        canned = {"value": _extraction()}
        monkeypatch.setattr(
            spec_dojo,
            "run_extraction",
            lambda db, cdb, sp, pdf, ctype="application/pdf": canned["value"],
        )

        # First run: no baseline yet → status new, editor shows the values.
        run1 = client.post(
            f"/api/supplier-invoice-specs/samples/{sample_id}/run",
            headers=admin_headers,
        )
        assert run1.status_code == 200, run1.text
        assert run1.json()["status"] == "new"
        assert run1.json()["editor_data"]["lines"][0]["description"] == "HONEY LIQUID"

        # Admin accepts → baseline stored, status pass.
        acc = client.post(
            f"/api/supplier-invoice-specs/samples/{sample_id}/expected",
            headers=admin_headers,
        )
        assert acc.json()["sample"]["has_expected"] is True
        assert acc.json()["sample"]["last_status"] == "pass"

        # Identical re-run → pass, no diffs.
        run2 = client.post(
            f"/api/supplier-invoice-specs/samples/{sample_id}/run",
            headers=admin_headers,
        )
        assert run2.json()["status"] == "pass" and run2.json()["diffs"] == []

        # Prompt regression (drifted extraction) → fail with the diff.
        drifted = _extraction()
        drifted["lines"][0]["unit_of_measure"] = "4x4kg"
        canned["value"] = drifted
        run3 = client.post(
            f"/api/supplier-invoice-specs/samples/{sample_id}/run",
            headers=admin_headers,
        )
        assert run3.json()["status"] == "fail"
        assert any(d["field"] == "unit_of_measure" for d in run3.json()["diffs"])
        # The stored last-run is viewable without re-running.
        last = client.get(
            f"/api/supplier-invoice-specs/samples/{sample_id}/last-run",
            headers=admin_headers,
        )
        assert last.json()["status"] == "fail"
        assert last.json()["editor_data"]["lines"][0]["copy_unit_mismatch"] is True

    def test_run_error_recorded_not_500(
        self, client, admin_headers, db_session, monkeypatch
    ):
        spec = _make_spec(db_session, "Error Foods")
        sample_id = self._upload(client, admin_headers, spec.id).json()["id"]

        def boom(*a, **k):
            raise RuntimeError("llm down")

        monkeypatch.setattr(spec_dojo, "run_extraction", boom)
        run = client.post(
            f"/api/supplier-invoice-specs/samples/{sample_id}/run",
            headers=admin_headers,
        )
        assert run.status_code == 200
        assert run.json()["status"] == "error"
        assert "llm down" in run.json()["error"]


class TestDojoRunAll:
    def test_run_all_groups_by_supplier(
        self, client, admin_headers, db_session, monkeypatch
    ):
        from app.db import engine as engine_mod
        from app.routers import supplier_spec_dojo as dojo_router

        spec_a = _make_spec(db_session, "Alpha Foods")
        spec_b = _make_spec(db_session, "Beta Foods")
        for spec in (spec_a, spec_b):
            db_session.add(
                SupplierSpecSample(spec_id=spec.id, label="s.pdf", pdf_bytes=b"%PDF- x")
            )
        db_session.flush()

        monkeypatch.setattr(
            spec_dojo,
            "run_extraction",
            lambda db, cdb, sp, pdf, ctype="application/pdf": _extraction(),
        )

        # Serial executor + the shared test session for the workers (the real
        # thread pool would open real DB sessions outside the test harness).
        class SerialPool:
            def __init__(self, max_workers=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def map(self, fn, items):
                return [fn(i) for i in items]

        monkeypatch.setattr(dojo_router, "ThreadPoolExecutor", SerialPool)
        monkeypatch.setattr(engine_mod, "SessionLocal", lambda: db_session)
        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)

        res = client.post(
            "/api/supplier-invoice-specs/dojo/run", headers=admin_headers, json={}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        names = {s["name"] for s in body["suppliers"]}
        assert {"Alpha Foods", "Beta Foods"} <= names
        assert body["new"] >= 2  # no baselines stored → both report new

        summary = client.get(
            "/api/supplier-invoice-specs/dojo/summary", headers=admin_headers
        )
        rows = {r["spec_id"]: r for r in summary.json()["specs"]}
        assert rows[spec_a.id]["total"] == 1


class TestDojoPermissions:
    def test_all_routes_admin_only(self, client, manager_headers, db_session):
        spec = _make_spec(db_session, "Perm Foods")
        assert (
            client.get(
                f"/api/supplier-invoice-specs/{spec.id}/samples",
                headers=manager_headers,
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/supplier-invoice-specs/{spec.id}/samples",
                headers=manager_headers,
                files={"file": ("x.pdf", b"%PDF-", "application/pdf")},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/supplier-invoice-specs/dojo/run",
                headers=manager_headers,
                json={},
            ).status_code
            == 403
        )
