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
        "customer_purchase_order_number": "1520500",
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
        # Case/whitespace never differ; DOTS do ('1.9 KG' vs '19 KG').
        exp["lines"][1]["unit_of_measure"] = "Each"
        cur["lines"][1]["unit_of_measure"] = " each"
        assert spec_dojo.compare_extractions(exp, cur) == []
        exp["lines"][1]["unit_of_measure"] = "1.9 KG"
        cur["lines"][1]["unit_of_measure"] = "19 KG"
        assert spec_dojo.compare_extractions(exp, cur) != []

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
        assert run1.json()["extraction"]["lines"][0]["description"] == "HONEY LIQUID"
        assert run1.json()["expected"] is None  # no baseline yet

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
        # Raw values for the DojoSampleView: baseline + what was pulled.
        assert last.json()["expected"]["lines"][0]["unit_of_measure"] == "4kg"
        assert last.json()["extraction"]["lines"][0]["unit_of_measure"] == "4x4kg"

        # The admin corrects the baseline by hand (Expected side of the view):
        # accept the drifted unit as the truth → diffs recompute → pass.
        fixed = _extraction()
        fixed["lines"][0]["unit_of_measure"] = "4x4kg"
        put = client.put(
            f"/api/supplier-invoice-specs/samples/{sample_id}/expected-values",
            headers=admin_headers,
            json={"expected": fixed},
        )
        assert put.status_code == 200, put.text
        assert put.json()["status"] == "pass" and put.json()["diffs"] == []

    def test_expected_values_validation_and_permissions(
        self, client, admin_headers, manager_headers, db_session
    ):
        spec = _make_spec(db_session, "Expected Foods")
        s = SupplierSpecSample(spec_id=spec.id, label="s.pdf", pdf_bytes=b"%PDF-s")
        db_session.add(s)
        db_session.flush()
        bad = client.put(
            f"/api/supplier-invoice-specs/samples/{s.id}/expected-values",
            headers=admin_headers,
            json={"expected": {"no_lines": True}},
        )
        assert bad.status_code == 400
        denied = client.put(
            f"/api/supplier-invoice-specs/samples/{s.id}/expected-values",
            headers=manager_headers,
            json={"expected": _extraction()},
        )
        assert denied.status_code == 403
        ok = client.put(
            f"/api/supplier-invoice-specs/samples/{s.id}/expected-values",
            headers=admin_headers,
            json={"expected": _extraction()},
        )
        assert ok.status_code == 200
        assert ok.json()["sample"]["has_expected"] is True
        # no run yet — status untouched, expected returned for the editor
        assert ok.json()["expected"]["lines"][0]["description"] == "HONEY LIQUID"

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


class TestSupplierMatcher:
    def test_matches_and_creates(self, db_session):
        spec = SupplierInvoiceSpec(
            name="Bidfood", aliases=["Bidfood Limited"], instructions="x"
        )
        main = SupplierInvoiceSpec(name="Main prompt", aliases=[], instructions="m")
        short = SupplierInvoiceSpec(name="Kai", aliases=["Ka"], instructions="k")
        db_session.add_all([spec, main, short])
        db_session.flush()

        # exact, substring, alias
        assert spec_dojo.find_spec_for_supplier(db_session, "Bidfood").id == spec.id
        assert (
            spec_dojo.find_spec_for_supplier(db_session, "Bidfoods fresh produce").id
            == spec.id
        )
        assert (
            spec_dojo.find_spec_for_supplier(db_session, "BIDFOOD LIMITED").id
            == spec.id
        )
        # the reserved Main prompt row never matches a supplier
        assert spec_dojo.find_spec_for_supplier(db_session, "Main prompt") is None
        # aliases under 3 normalized chars are ignored ("Ka" won't match "Kapiti")
        got = spec_dojo.find_spec_for_supplier(db_session, "Kapiti Cheese")
        assert got is None
        # find-or-create
        made, created = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "Tamar Farming Company"
        )
        assert created is True and made.name == "Tamar Farming Company"
        again, created2 = spec_dojo.find_or_create_spec_for_supplier(
            db_session, "Tamar Farming Company"
        )
        assert created2 is False and again.id == made.id


class TestComposeOverride:
    def test_supplier_override_replaces_notes_only(self, db_session, monkeypatch):
        monkeypatch.setattr(spec_dojo, "main_prompt", lambda cdb: "MAIN")
        spec = SupplierInvoiceSpec(name="Acme", aliases=[], instructions="OLD NOTES")
        db_session.add(spec)
        db_session.flush()
        out = spec_dojo.compose_instructions(db_session, spec, "NEW NOTES")
        assert "NEW NOTES" in out and "OLD NOTES" not in out and "MAIN" in out

    def test_main_override_replaces_main(self, db_session):
        main = SupplierInvoiceSpec(
            name="Main prompt", aliases=[], instructions="STORED MAIN"
        )
        db_session.add(main)
        db_session.flush()
        out = spec_dojo.compose_instructions(db_session, main, "CANDIDATE MAIN")
        assert out == "CANDIDATE MAIN"


class TestCandidateRun:
    def test_supplier_scoped_and_config_untouched(self, db_session, monkeypatch):
        spec_a = _make_spec(db_session, "Cand A")
        spec_b = _make_spec(db_session, "Cand B")
        sa = SupplierSpecSample(
            spec_id=spec_a.id,
            label="a.pdf",
            pdf_bytes=b"%PDF-a",
            expected=_extraction(),
        )
        sb = SupplierSpecSample(spec_id=spec_b.id, label="b.pdf", pdf_bytes=b"%PDF-b")
        db_session.add_all([sa, sb])
        db_session.flush()

        seen = []

        def fake_run(
            db, cdb, spec, pdf, ctype="application/pdf", override_instructions=None
        ):
            seen.append((spec.id, override_instructions))
            return _extraction()

        monkeypatch.setattr(spec_dojo, "run_extraction", fake_run)
        out = spec_dojo.candidate_run(db_session, db_session, spec_a, "CANDIDATE")
        # only spec A's sample ran, with the candidate text as override
        assert [s[0] for s in seen] == [spec_a.id]
        assert seen[0][1] == "CANDIDATE"
        assert out["passed"] == 1 and out["failed"] == 0
        # stored config untouched
        db_session.refresh(spec_a)
        assert spec_a.instructions == "notes"

    def test_main_prompt_runs_all_baselined_samples(self, db_session, monkeypatch):
        main = SupplierInvoiceSpec(name="Main prompt", aliases=[], instructions="m")
        spec_a = _make_spec(db_session, "Cand C")
        spec_b = _make_spec(db_session, "Cand D")
        db_session.add(main)
        db_session.add_all(
            [
                SupplierSpecSample(
                    spec_id=spec_a.id,
                    label="a.pdf",
                    pdf_bytes=b"%PDF-a",
                    expected=_extraction(),
                ),
                SupplierSpecSample(
                    spec_id=spec_b.id,
                    label="b.pdf",
                    pdf_bytes=b"%PDF-b",
                    expected=_extraction(),
                ),
            ]
        )
        db_session.flush()
        ran = []
        monkeypatch.setattr(
            spec_dojo,
            "_extract_with_main_override",
            lambda db, cdb, own, s, text: ran.append((own.name, text)) or _extraction(),
        )
        out = spec_dojo.candidate_run(db_session, db_session, main, "NEW MAIN")
        assert sorted(r[0] for r in ran) == ["Cand C", "Cand D"]
        assert all(r[1] == "NEW MAIN" for r in ran)
        assert out["passed"] == 2

    def test_samples_without_a_baseline_never_run(self, db_session, monkeypatch):
        """The sensei only tests against baselines. A no-baseline sample
        (including the Dojo page's hidden drafts) can neither pass nor fail,
        so re-extracting it spends a full extraction to report 'new' — and in
        the proposal card those rows read as regression coverage that wasn't
        there (Bidfood, Aug 2026: 4 of 6 sibling rows were no-baseline)."""
        spec = _make_spec(db_session, "Cand E")
        with_base = SupplierSpecSample(
            spec_id=spec.id,
            label="base.pdf",
            pdf_bytes=b"%PDF-1",
            expected=_extraction(),
        )
        no_base = SupplierSpecSample(
            spec_id=spec.id, label="loose.pdf", pdf_bytes=b"%PDF-2"
        )
        draft = SupplierSpecSample(
            spec_id=spec.id, label="draft.pdf", pdf_bytes=b"%PDF-3", draft=True
        )
        db_session.add_all([with_base, no_base, draft])
        db_session.flush()

        ran = []

        def fake_run(
            db, cdb, spec, pdf, ctype="application/pdf", override_instructions=None
        ):
            ran.append(pdf)
            return _extraction()

        monkeypatch.setattr(spec_dojo, "run_extraction", fake_run)
        out = spec_dojo.candidate_run(db_session, db_session, spec, "CANDIDATE")
        assert ran == [b"%PDF-1"]  # only the baselined sample was extracted
        assert [s["label"] for s in out["samples"]] == ["base.pdf"]
        assert out["new"] == 0


class TestAnalyseSample:
    def _seed(self, db_session, with_source=False):
        spec = _make_spec(db_session, "Analyse Foods")
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="s.pdf",
            pdf_bytes=b"%PDF-s",
            last_run={"extraction": _extraction(), "diffs": []},
        )
        db_session.add(s)
        db_session.flush()
        return spec, s

    def _canned_proposal(self, gt=None):
        return {
            "rationale": "the unit column is misread",
            "layout_facts": ["UOM column decides the unit"],
            "ground_truth": gt or _extraction(),
            "proposed_instructions": "USE THE UOM COLUMN",
        }

    def test_first_run_via_analyse_carries_replica_keys(self, db_session, monkeypatch):
        # The cannot-receive intake kicks analyse in the background, making its inline
        # extraction the sample's FIRST stored run — without the replica keys
        # the panel renders "no invoice view" for a perfectly good
        # cannot-receive sample (774238028/INV-958, 09 Aug 2026).
        spec = _make_spec(db_session, "First Run Foods")
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="fresh.pdf",
            pdf_bytes=b"%PDF-s",
            source_venue_id="v-1",
            source_invoice_id="inv-1",
        )
        db_session.add(s)
        db_session.flush()
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (self._canned_proposal(), None),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        monkeypatch.setattr(
            spec_dojo,
            "replica_stage",
            lambda *a, **k: (
                {"replica": True, "lines": []},
                [],
                {"header": [], "lines": []},
            ),
        )
        spec_dojo.analyse_sample(db_session, db_session, s.id)
        db_session.refresh(s)
        run = s.last_run or {}
        assert run.get("replica") == {"replica": True, "lines": []}
        assert "replica_diffs" in run and "replica_compare" in run

    def test_green_analysis_ready(self, db_session, monkeypatch):
        # The seeded CURRENT run already matches the agent's ground truth —
        # the no-needless-specs guard empties the proposed text (spec notes
        # exist to fix misreads); the values are still baselined.
        spec, s = self._seed(db_session)
        calls = {"ask": 0}

        def fake_llm(**kw):
            calls["ask"] += 1
            return self._canned_proposal(), None

        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: fake_llm(**k),
        )
        # candidate extraction matches the agent's ground truth exactly
        monkeypatch.setattr(
            spec_dojo,
            "run_extraction",
            lambda *a, **k: _extraction(),
        )
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "ready" and out["green"] is True
        assert out["proposed_instructions"] == ""  # guard: current read is fine
        assert out["spec_not_needed"] is True
        assert calls["ask"] == 1  # no refinement needed
        db_session.refresh(s)
        assert (s.analysis or {}).get("status") == "ready"
        assert spec.instructions == "notes"  # spec untouched
        # A green proposal populates the sample's expected values with the
        # agent's ground truth (the admin no longer hand-types them) and the
        # last run is re-diffed against that baseline.
        assert s.expected == _extraction()
        assert s.last_status == "pass"  # the seeded run matches the baseline

    def test_green_misread_keeps_text_no_auto_apply_on_existing_spec(
        self, db_session, monkeypatch
    ):
        # The current run MISREADS the document (qty 9), the agent's fix is
        # green — the text survives, but the spec already carries admin text,
        # so nothing auto-applies: the proposal awaits review.
        spec, s = self._seed(db_session)
        wrong = _extraction()
        wrong["lines"][0]["quantity"] = 9
        s.last_run = {"extraction": wrong, "diffs": []}
        db_session.flush()
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (self._canned_proposal(), None),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "ready" and out["green"] is True
        assert out["proposed_instructions"] == "USE THE UOM COLUMN"
        assert out["spec_not_needed"] is False
        assert out.get("auto_applied") is None
        assert spec.instructions == "notes"  # not written

    def test_auto_applies_first_spec_for_new_supplier(self, db_session, monkeypatch):
        # Self-training v1: green + a real misread + NO existing prompt text
        # → the analysis applies its own proposal (new supplier, nothing to
        # break). Anything touching an existing prompt still waits.
        spec, s = self._seed(db_session)
        spec.instructions = ""  # brand-new supplier: no prompt yet
        wrong = _extraction()
        wrong["lines"][0]["quantity"] = 9
        s.last_run = {"extraction": wrong, "diffs": []}
        db_session.flush()
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (self._canned_proposal(), None),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "applied"
        assert out["auto_applied"] is True
        db_session.refresh(s)
        assert spec.instructions == "USE THE UOM COLUMN"  # written automatically
        assert s.expected == _extraction()  # baselined
        assert (s.analysis or {}).get("status") == "applied"

    def test_new_supplier_that_reads_fine_gets_the_no_rules_spec(
        self, db_session, monkeypatch
    ):
        # A brand-new supplier whose invoices already read correctly still ends
        # with a spec — the standard NO_RULES_SPEC note, auto-applied like any
        # other — so every supplier goes through one path and the auto-spec
        # trigger's "has a spec?" check terminates. (An EXISTING spec that reads
        # fine is left untouched — see test_green_analysis_ready.)
        spec, s = self._seed(db_session)
        spec.instructions = ""  # brand-new supplier: empty holder
        db_session.flush()
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (self._canned_proposal(), None),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["spec_not_needed"] is True
        assert out["proposed_instructions"] == spec_dojo.NO_RULES_SPEC
        assert out["status"] == "applied" and out["auto_applied"] is True
        db_session.refresh(s)
        assert spec.instructions == spec_dojo.NO_RULES_SPEC  # the note IS the spec
        assert s.expected == _extraction()  # baselined like any green sample

    def _seed_auto_apply_with_source(self, db_session, monkeypatch):
        # A brand-new supplier whose sample came from a real invoice — the
        # self-training auto-apply case, wired for the heal follow-through.
        spec, s = self._seed(db_session)
        spec.instructions = ""
        s.source_venue_id = "v-src"
        s.source_invoice_id = "inv-src"
        wrong = _extraction()
        wrong["lines"][0]["quantity"] = 9
        s.last_run = {"extraction": wrong, "diffs": []}
        db_session.flush()
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (self._canned_proposal(), None),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        monkeypatch.setattr(
            spec_dojo, "resolve_sample_venue_id", lambda *a, **k: "v-here"
        )
        monkeypatch.setattr(
            spec_dojo,
            "replica_stage",
            lambda *a, **k: ({"replica": True, "lines": []}, [], {}),
        )
        return spec, s

    def test_auto_apply_heals_the_source_invoice(self, db_session, monkeypatch):
        # The prompt fix must reach the card that raised the sample: the
        # sensei taught itself Federal Merchants one minute after the review
        # blocked (396152, 19 Aug 2026), and the card stayed blocked until a
        # human pressed Re-analyse. Auto-apply now re-runs that review.
        spec, s = self._seed_auto_apply_with_source(db_session, monkeypatch)
        healed: list = []
        monkeypatch.setattr(
            "app.routers.invoice_fixes.heal_review",
            lambda db, cdb, venue_id, invoice_id: (
                healed.append((venue_id, invoice_id)) or True
            ),
        )
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["auto_applied"] is True
        assert healed == [("v-here", "inv-src")]

    def test_failed_heal_never_fails_the_analysis(self, db_session, monkeypatch):
        # Healing is follow-through, not part of the apply: Loaded being down
        # must not turn a successful auto-apply into a failed analysis.
        spec, s = self._seed_auto_apply_with_source(db_session, monkeypatch)

        def boom(*a, **k):
            raise RuntimeError("Loaded is down")

        monkeypatch.setattr("app.routers.invoice_fixes.heal_review", boom)
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["auto_applied"] is True
        db_session.refresh(s)
        assert (s.analysis or {}).get("status") == "applied"

    def test_green_analysis_keeps_admin_expected(self, db_session, monkeypatch):
        # An admin-entered baseline is never clobbered by the agent's ground
        # truth — the agent only fills the gap when nothing is stored yet.
        spec, s = self._seed(db_session)
        admin_expected = _extraction()
        admin_expected["lines"][0]["quantity"] = 7
        s.expected = admin_expected
        db_session.flush()
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (self._canned_proposal(), None),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "ready"
        db_session.refresh(s)
        assert s.expected["lines"][0]["quantity"] == 7  # admin's value survives

    def test_alias_proposal_verifies_against_target_and_stores_canonical_name(
        self, db_session, monkeypatch
    ):
        # Same layout under a different Loaded name: the agent answers
        # alias_of an existing spec. The candidate runs under the TARGET
        # spec's prompt, that spec's samples are the siblings, and the
        # stored proposal carries the target's canonical name.
        target = _make_spec(db_session, "Host Foods")
        target.instructions = "HOST RULES"
        spec, s = self._seed(db_session)
        hosts_used: list[str] = []

        def fake_extract(_db, _cdb, host_spec, *a, **k):
            hosts_used.append(host_spec.name)
            return _extraction()

        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (
                dict(self._canned_proposal(), alias_of="host foods"),
                None,
            ),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", fake_extract)
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "ready" and out["green"] is True
        assert out["alias_of"] == "Host Foods"  # canonical, not the lowercase
        assert "Host Foods" in hosts_used  # candidate ran under the target

    def test_alias_of_unknown_spec_is_not_green(self, db_session, monkeypatch):
        spec, s = self._seed(db_session)
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (
                dict(self._canned_proposal(), alias_of="No Such Spec"),
                None,
            ),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "not_green"
        assert any(
            d.get("field") == "alias_of"
            for d in out["candidate_results"]["own"]["diffs"]
        )

    def test_roster_rides_in_the_analysis_context(self, db_session):
        other = _make_spec(db_session, "Roster Foods")
        other.instructions = "ROSTER RULES"
        spec, s = self._seed(db_session)
        ctx = spec_dojo._analysis_context(db_session, db_session, spec, s)
        assert "EXISTING SUPPLIER SPECS" in ctx
        assert "Roster Foods" in ctx and "ROSTER RULES" in ctx

    def test_feedback_reply_rides_as_authoritative_and_threads(
        self, db_session, monkeypatch
    ):
        # The admin replies to a proposal ("the unit is wrong"): the agent
        # sees its previous proposal + the correction marked authoritative,
        # the thread is stored, and a green re-analysis UPDATES the
        # agent-populated expected values (they were the agent's own — the
        # correction must be able to fix them).
        spec, s = self._seed(db_session)
        old_gt = _extraction()
        old_gt["lines"][0]["unit_of_measure"] = "24 pack"  # the wrong unit
        s.expected = old_gt  # auto-populated by the earlier green proposal
        s.analysis = {
            "status": "ready",
            "green": True,
            "proposed_instructions": "OLD TEXT",
            "ground_truth": old_gt,
        }
        db_session.flush()
        prompts: list[str] = []

        def fake_llm(*a, **k):
            prompts.append(k.get("user_prompt") or "")
            return self._canned_proposal(), None  # corrected gt = _extraction()

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake_llm)
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = spec_dojo.analyse_sample(
            db_session, db_session, s.id, feedback="unit must stay as printed"
        )
        assert out["status"] == "ready"
        assert [m["text"] for m in out["thread"]] == ["unit must stay as printed"]
        assert "ADMIN CORRECTIONS" in prompts[0]
        assert "unit must stay as printed" in prompts[0]
        assert "YOUR PREVIOUS PROPOSAL" in prompts[0] and "OLD TEXT" in prompts[0]
        db_session.refresh(s)
        # expected was agent-owned (== previous gt) → refreshed to the fix
        assert s.expected == _extraction()

    def test_feedback_never_updates_admin_edited_expected(
        self, db_session, monkeypatch
    ):
        spec, s = self._seed(db_session)
        admin_expected = _extraction()
        admin_expected["lines"][0]["quantity"] = 7  # hand-edited: not agent gt
        s.expected = admin_expected
        s.analysis = {"status": "ready", "ground_truth": _extraction()}
        db_session.flush()
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm",
            lambda *a, **k: (self._canned_proposal(), None),
        )
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = spec_dojo.analyse_sample(
            db_session, db_session, s.id, feedback="check line one"
        )
        assert out["status"] == "ready"
        db_session.refresh(s)
        assert s.expected["lines"][0]["quantity"] == 7  # admin's value survives

    def test_failing_candidate_refines_once_then_not_green(
        self, db_session, monkeypatch
    ):
        spec, s = self._seed(db_session)
        calls = {"ask": 0}

        def fake_llm(*a, **k):
            calls["ask"] += 1
            return self._canned_proposal(), None

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake_llm)
        # candidate extraction NEVER matches the ground truth (qty differs)
        bad = _extraction()
        bad["lines"][0]["quantity"] = 99
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: bad)
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "not_green" and out["green"] is False
        assert calls["ask"] == 2  # exactly one refinement round


class TestApplyAnalysis:
    def test_apply_writes_spec_and_baseline(self, client, admin_headers, db_session):
        spec = _make_spec(db_session, "Apply Foods")
        gt = _extraction()
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="s.pdf",
            pdf_bytes=b"%PDF-s",
            analysis={
                "status": "ready",
                "green": True,
                "proposed_instructions": "NEW SPEC TEXT",
                "ground_truth": gt,
                "candidate_results": {
                    "own": {"status": "pass", "diffs": [], "extraction": gt}
                },
            },
        )
        db_session.add(s)
        db_session.flush()
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/apply-analysis",
            headers=admin_headers,
            json={"apply_spec": True, "save_expected": True},
        )
        assert res.status_code == 200, res.text
        assert res.json()["spec_instructions"] == "NEW SPEC TEXT"
        db_session.refresh(s)
        db_session.refresh(spec)
        assert spec.instructions == "NEW SPEC TEXT"
        assert s.expected == gt
        assert s.last_status == "pass"
        assert (s.analysis or {}).get("status") == "applied"

    def test_apply_never_clobbers_admin_expected(
        self, client, admin_headers, db_session
    ):
        # An admin corrected the expected values before applying: apply keeps
        # THEIR baseline and re-diffs the candidate extraction against it —
        # so a candidate that only matches the AGENT's values shows FAIL, not
        # a pass taken on the agent's say-so.
        spec = _make_spec(db_session, "Apply Keeps")
        gt = _extraction()
        admin_expected = _extraction()
        admin_expected["lines"][0]["quantity"] = 7  # admin disagrees with agent
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="s.pdf",
            pdf_bytes=b"%PDF-s",
            expected=admin_expected,
            analysis={
                "status": "ready",
                "green": True,
                "proposed_instructions": "NEW SPEC TEXT",
                "ground_truth": gt,
                "candidate_results": {
                    "own": {"status": "pass", "diffs": [], "extraction": gt}
                },
            },
        )
        db_session.add(s)
        db_session.flush()
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/apply-analysis",
            headers=admin_headers,
            json={"apply_spec": True, "save_expected": True},
        )
        assert res.status_code == 200, res.text
        db_session.refresh(s)
        assert s.expected["lines"][0]["quantity"] == 7  # admin's value survives
        assert s.last_status == "fail"  # candidate matches agent, NOT admin
        assert any(
            d.get("field") == "quantity" for d in (s.last_run or {}).get("diffs", [])
        )

    def test_apply_alias_merges_into_target_and_removes_duplicate_spec(
        self, client, admin_headers, db_session
    ):
        # Alias proposal applied: the supplier's name lands in the target's
        # aliases, the sample moves to the target, the redundant (empty)
        # auto-created spec is deleted, and the target's text is untouched
        # when no new text was proposed — one layout, one spec.
        target = _make_spec(db_session, "Bidfood Host")
        target.instructions = "OUTER/INNER RULES"
        spec = _make_spec(db_session, "Bidvest Duplicate")
        spec.instructions = ""  # auto-created rows are empty — deletable
        gt = _extraction()
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="s.pdf",
            pdf_bytes=b"%PDF-s",
            analysis={
                "status": "ready",
                "green": True,
                "proposed_instructions": "",
                "alias_of": "Bidfood Host",
                "ground_truth": gt,
                "candidate_results": {
                    "own": {"status": "pass", "diffs": [], "extraction": gt}
                },
            },
        )
        db_session.add(s)
        db_session.flush()
        spec_id = spec.id
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/apply-analysis",
            headers=admin_headers,
            json={"apply_spec": True, "save_expected": True},
        )
        assert res.status_code == 200, res.text
        assert res.json()["alias_added_to"] == "Bidfood Host"
        db_session.refresh(target)
        db_session.refresh(s)
        assert "Bidvest Duplicate" in (target.aliases or [])
        assert target.instructions == "OUTER/INNER RULES"  # untouched
        assert s.spec_id == target.id  # sample moved
        assert s.expected == gt
        assert (
            db_session.get(SupplierInvoiceSpec, spec_id) is None
        )  # duplicate row gone

    def test_apply_rebuilds_the_invoice_view_from_the_candidate(
        self, client, admin_headers, db_session, monkeypatch
    ):
        # Applying used to carry the PRE-fix replica forward, so the dojo's
        # invoice sheet showed a PO string no values tab contained (Federal
        # Merchants 396152, 19 Aug 2026). Apply now rebuilds the view from
        # the extraction it just recorded.
        spec = _make_spec(db_session, "Apply Rebuilds")
        gt = _extraction()
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="s.pdf",
            pdf_bytes=b"%PDF-s",
            source_venue_id="v-1",
            source_invoice_id="inv-1",
            last_run={
                "extraction": {"stale": True},
                "diffs": [],
                "replica": {
                    "purchase_order_number": "PO 1518452 Freeman & Grey 16P9388"
                },
                "replica_compare": {"header": [], "lines": []},
            },
            analysis={
                "status": "ready",
                "green": True,
                "proposed_instructions": "NEW SPEC TEXT",
                "ground_truth": gt,
                "candidate_results": {
                    "own": {"status": "pass", "diffs": [], "extraction": gt}
                },
            },
        )
        db_session.add(s)
        db_session.flush()
        seen: dict = {}

        def fake_stage(db, config_db, sample, extraction):
            seen["extraction"] = extraction
            return (
                {"purchase_order_number": "1518452"},
                [],
                {"header": [], "lines": []},
            )

        monkeypatch.setattr(spec_dojo, "replica_stage", fake_stage)
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/apply-analysis",
            headers=admin_headers,
            json={"apply_spec": True, "save_expected": True},
        )
        assert res.status_code == 200, res.text
        db_session.refresh(s)
        assert seen["extraction"] == gt  # rebuilt from what apply recorded
        assert (s.last_run or {})["replica"] == {"purchase_order_number": "1518452"}

    def test_apply_keeps_old_view_when_rebuild_impossible(
        self, client, admin_headers, db_session
    ):
        # A hand-uploaded sample (no source venue) can't rebuild — the old
        # view survives rather than being blanked or replaced by an error box.
        spec = _make_spec(db_session, "Apply Keeps View")
        gt = _extraction()
        old_view = {"purchase_order_number": "kept"}
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="s.pdf",
            pdf_bytes=b"%PDF-s",
            last_run={"extraction": {"stale": True}, "diffs": [], "replica": old_view},
            analysis={
                "status": "ready",
                "green": True,
                "proposed_instructions": "NEW SPEC TEXT",
                "ground_truth": gt,
                "candidate_results": {
                    "own": {"status": "pass", "diffs": [], "extraction": gt}
                },
            },
        )
        db_session.add(s)
        db_session.flush()
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/apply-analysis",
            headers=admin_headers,
            json={"apply_spec": True, "save_expected": True},
        )
        assert res.status_code == 200, res.text
        db_session.refresh(s)
        assert (s.last_run or {})["replica"] == old_view

    def test_apply_without_proposal_400(self, client, admin_headers, db_session):
        spec = _make_spec(db_session, "Apply None")
        s = SupplierSpecSample(spec_id=spec.id, label="s.pdf", pdf_bytes=b"%PDF-s")
        db_session.add(s)
        db_session.flush()
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/apply-analysis",
            headers=admin_headers,
            json={},
        )
        assert res.status_code == 400


class TestCannotReceiveIntake:
    """Cannot-receive is the ONE intake into the dojo (the admin-only
    add-to-dojo button and endpoint were removed Aug 2026). These pin the
    REAL staging path behind it — spec creation, source refs, dedupe,
    draft promotion — which test_autopilot_report (which mocks staging)
    deliberately does not cover.
    """

    def _fake_loaded(self, monkeypatch):
        # Patched at the SOURCE module: the staging helper (and the overview
        # endpoint) import LoadedInvoiceClient inside the call.
        import app.services.received_invoice as RI

        class FakeLoadedClient:
            def __init__(self, db, config_db, venue_id):
                pass

            def invoice(self, invoice_id):
                return {
                    "id": invoice_id,
                    "supplierName": "Tamar Farming Company",
                    "referenceNumber": "INV-9",
                    "fileId": "file-1",
                }

            def file_base64(self, file_id):
                import base64

                return base64.b64encode(b"%PDF-tamar").decode(), "application/pdf"

        monkeypatch.setattr(RI, "LoadedInvoiceClient", FakeLoadedClient)
        # The verdict recorder is test_autopilot_report's subject, not ours —
        # and it opens its own session, which can't see this test's rows.
        monkeypatch.setattr(
            "app.routers.invoice_fixes.record_receive_outcome", lambda *a, **k: None
        )
        return FakeLoadedClient

    def test_creates_spec_and_sample_with_source_refs(
        self, client, manager_headers, db_session, monkeypatch
    ):
        self._fake_loaded(monkeypatch)
        # the request handler opens its own RW config session — point the
        # factory at the shared test session (and don't let it be closed)
        from app.db import engine as engine_mod

        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)

        # The press must hand the sample to the sensei QUEUE (the worker
        # executes it; nothing runs in this request).
        started = []
        monkeypatch.setattr(
            "app.services.sensei_runner.start_analysis",
            lambda sid, fb=None: started.append(sid) or "queued",
        )

        # A MANAGER, not an admin — filing is deliberately open to whoever
        # hits the problem.
        res = client.post(
            "/api/invoice-fixes/cannot-receive",
            headers=manager_headers,
            json={"venue_id": "v1", "invoice_id": "inv-42"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["staged"] is True
        assert body["spec_name"] == "Tamar Farming Company"
        assert body["already_in_dojo"] is False
        assert started  # sensei kicked
        sample = (
            db_session.query(SupplierSpecSample)
            .filter(SupplierSpecSample.id == body["sample_id"])
            .first()
        )
        assert sample.source_invoice_id == "inv-42"
        assert sample.source_venue_id == "v1"
        assert sample.pdf_bytes == b"%PDF-tamar"
        # The spec was created for the new supplier as part of the filing.
        spec = (
            db_session.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.id == sample.spec_id)
            .first()
        )
        assert spec is not None and spec.name == "Tamar Farming Company"

        # same invoice again → reused (no duplicate), and reported as a
        # repeat so the card can say "sent back to the sensei".
        res2 = client.post(
            "/api/invoice-fixes/cannot-receive",
            headers=manager_headers,
            json={"venue_id": "v1", "invoice_id": "inv-42"},
        )
        assert res2.json()["already_in_dojo"] is True
        assert res2.json()["sample_id"] == body["sample_id"]


class TestDojoTriage:
    """The Dojo page: stage outstanding invoices as DRAFT samples (full
    toolkit, invisible to regression), promote into the dojo proper, and the
    one-fetch overview (outstanding + awaiting-review)."""

    def _fake(self, monkeypatch, db_session, rows=None):
        import app.services.received_invoice as RI
        from app.db import engine as engine_mod

        class FakeLoadedClient:
            def __init__(self, db, config_db, venue_id):
                pass

            def get(self, path):
                assert "status=NotReceived" in path
                return rows or []

            def invoice(self, invoice_id):
                return {
                    "id": invoice_id,
                    "supplierName": "Tamar Farming Company",
                    "referenceNumber": "INV-9",
                    "fileId": "file-1",
                }

            def file_base64(self, file_id):
                import base64

                return base64.b64encode(b"%PDF-t").decode(), "application/pdf"

        monkeypatch.setattr(RI, "LoadedInvoiceClient", FakeLoadedClient)
        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)

    def test_stage_draft_hidden_from_regression(
        self, client, admin_headers, db_session, monkeypatch
    ):
        self._fake(monkeypatch, db_session)
        res = client.post(
            "/api/supplier-invoice-specs/dojo/stage",
            headers=admin_headers,
            json={"venue_id": "v1", "invoice_id": "inv-d1"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["sample"]["draft"] is True
        assert body["was_draft"] is False and body["already_in_dojo"] is False
        spec_id = body["spec_id"]

        # Hidden from the per-spec list and the summary.
        listed = client.get(
            f"/api/supplier-invoice-specs/{spec_id}/samples", headers=admin_headers
        )
        assert listed.json()["samples"] == []
        summary = client.get(
            "/api/supplier-invoice-specs/dojo/summary", headers=admin_headers
        )
        assert spec_id not in [g["spec_id"] for g in summary.json()["specs"]]

        # Staging again reuses the draft.
        res2 = client.post(
            "/api/supplier-invoice-specs/dojo/stage",
            headers=admin_headers,
            json={"venue_id": "v1", "invoice_id": "inv-d1"},
        )
        assert res2.json()["sample"]["id"] == body["sample_id"]
        assert res2.json()["was_draft"] is True

        # Promote → appears in the per-spec list.
        prom = client.post(
            f"/api/supplier-invoice-specs/samples/{body['sample_id']}/promote",
            headers=admin_headers,
        )
        assert prom.json()["sample"]["draft"] is False
        listed2 = client.get(
            f"/api/supplier-invoice-specs/{spec_id}/samples", headers=admin_headers
        )
        assert [s["id"] for s in listed2.json()["samples"]] == [body["sample_id"]]

    def test_cannot_receive_promotes_existing_draft(
        self, client, admin_headers, db_session, monkeypatch
    ):
        import threading as _threading

        self._fake(monkeypatch, db_session)
        monkeypatch.setattr(
            "app.routers.invoice_fixes.record_receive_outcome", lambda *a, **k: None
        )

        class FakeThread:
            def __init__(self, *a, **k):
                pass

            def start(self):
                pass

        monkeypatch.setattr(_threading, "Thread", FakeThread)
        staged = client.post(
            "/api/supplier-invoice-specs/dojo/stage",
            headers=admin_headers,
            json={"venue_id": "v1", "invoice_id": "inv-d2"},
        ).json()
        # A "Norm can't do this one" press on an invoice somebody had merely
        # EXPANDED on the Dojo page must upgrade the invisible draft into a
        # real awaiting-review sample, not file a duplicate.
        res = client.post(
            "/api/invoice-fixes/cannot-receive",
            headers=admin_headers,
            json={"venue_id": "v1", "invoice_id": "inv-d2"},
        )
        assert res.json()["sample_id"] == staged["sample_id"]
        db_session.expire_all()
        s = db_session.get(SupplierSpecSample, staged["sample_id"])
        assert bool(s.draft) is False

    def test_overview_membership_and_pending_review(
        self, client, admin_headers, db_session, monkeypatch
    ):
        from app.db.models import ConnectorConfig
        from tests.conftest import _make_venue

        venue = _make_venue(db_session, name="Overview Venue")
        db_session.add(
            ConnectorConfig(
                connector_name="loadedhub", venue_id=venue.id, enabled="true"
            )
        )
        db_session.flush()

        rows = [
            {
                "id": "inv-a",
                "referenceNumber": "A-1",
                "supplierName": "Tamar Farming Company",
                "issuedAt": "2026-08-08",
                "total": 10.0,
                "fileId": "f",
            },
            {
                "id": "inv-b",
                "referenceNumber": "B-1",
                "supplierName": "Tamar Farming Company",
                "issuedAt": "2026-08-08",
                "total": 20.0,
                "fileId": "f",
            },
            {
                "id": "inv-c",
                "referenceNumber": "C-1",
                "supplierName": "Tamar Farming Company",
                "issuedAt": "2026-08-08",
                "total": 30.0,
                "fileId": "f",
            },
        ]
        self._fake(monkeypatch, db_session, rows=rows)

        spec = _make_spec(db_session, "Overview Foods")
        # inv-a: permanent sample, baselined + applied → in dojo, NOT pending.
        db_session.add(
            SupplierSpecSample(
                spec_id=spec.id,
                label="a.pdf",
                pdf_bytes=b"%P",
                source_venue_id=venue.id,
                source_invoice_id="inv-a",
                expected=_extraction(),
                analysis={"status": "applied"},
            )
        )
        # inv-b: draft → badge draft, NOT in pending_review.
        db_session.add(
            SupplierSpecSample(
                spec_id=spec.id,
                label="b.pdf",
                pdf_bytes=b"%P",
                source_venue_id=venue.id,
                source_invoice_id="inv-b",
                draft=True,
            )
        )
        # unrelated permanent sample with a READY proposal → pending_review.
        db_session.add(
            SupplierSpecSample(
                spec_id=spec.id,
                label="p.pdf",
                pdf_bytes=b"%P",
                expected=_extraction(),
                analysis={"status": "ready"},
            )
        )
        db_session.flush()

        res = client.get(
            "/api/supplier-invoice-specs/dojo/overview", headers=admin_headers
        )
        assert res.status_code == 200, res.text
        body = res.json()
        by_inv = {r["invoice_id"]: r for r in body["outstanding"]}
        assert by_inv["inv-a"]["in_dojo"] is True and by_inv["inv-a"]["draft"] is False
        assert by_inv["inv-b"]["draft"] is True and by_inv["inv-b"]["in_dojo"] is False
        assert by_inv["inv-c"]["sample_id"] is None
        pending = {p["label"] for p in body["pending_review"]}
        assert "p.pdf" in pending  # ready proposal awaits review
        assert "a.pdf" not in pending  # baselined + applied
        assert "b.pdf" not in pending  # drafts never appear here
        assert body["errors"] == []


class TestGroundTruthArithmetic:
    """The document's own arithmetic, enforced on the agent's ground truth —
    the pink-ling net (09 Aug 2026): a truth that mis-reads a line usually
    breaks qty × price = line_total, and must never go green."""

    def test_pink_ling_shape_flags(self):
        gt = _extraction()
        gt["lines"][0].update(
            quantity=0.5, unit_price_ex_tax=21.75, line_total_ex_tax=0.0
        )
        out = spec_dojo._ground_truth_violations(gt)
        assert any("0.5 x 21.75" in v for v in out)

    def test_not_available_zero_line_passes(self):
        gt = _extraction()
        gt["lines"][0].update(
            quantity=0, unit_price_ex_tax=21.75, line_total_ex_tax=0.0
        )
        gt["lines"][1].update(
            quantity=1, unit_price_ex_tax=100.0, line_total_ex_tax=100.0
        )
        gt.update(subtotal_ex_tax=100.0, tax_amount=15.0, total_incl_tax=115.0)
        assert spec_dojo._ground_truth_violations(gt) == []

    def test_header_chain_flags(self):
        gt = _extraction(subtotal_ex_tax=90.0)  # lines sum to 100
        out = spec_dojo._ground_truth_violations(gt)
        assert any("subtotal_ex_tax is 90.0" in v for v in out)
        gt2 = _extraction(total_incl_tax=200.0)  # 100 + 15 ≠ 200
        out2 = spec_dojo._ground_truth_violations(gt2)
        assert any("total_incl_tax is 200.0" in v for v in out2)

    def test_none_operands_skip(self):
        gt = _extraction()
        gt["lines"][0]["line_total_ex_tax"] = None
        gt["subtotal_ex_tax"] = None
        assert spec_dojo._ground_truth_violations(gt) == []

    def test_inconsistent_truth_never_green_never_auto_applies(
        self, db_session, monkeypatch
    ):
        # The exact sensei regression: empty spec (auto-apply territory), a
        # misreading current run, and an agent whose ground truth breaks the
        # printed arithmetic on BOTH asks → refinement fires with the
        # violation text, the result is not_green, nothing auto-applies.
        spec = SupplierInvoiceSpec(name="Arith Foods", aliases=[], instructions="")
        db_session.add(spec)
        db_session.flush()
        bad_gt = _extraction()
        bad_gt["lines"][0].update(
            quantity=0.5, unit_price_ex_tax=21.75, line_total_ex_tax=0.0
        )
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="a.pdf",
            pdf_bytes=b"%P",
            last_run={"extraction": _extraction(invoice_number="WRONG"), "diffs": []},
        )
        db_session.add(s)
        db_session.flush()
        asks = []

        def fake_llm(*a, **k):
            asks.append(k.get("user_prompt") or (a[1] if len(a) > 1 else ""))
            return (
                {
                    "rationale": "r",
                    "ground_truth": bad_gt,
                    "proposed_instructions": "BAD RULES",
                },
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake_llm)
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: bad_gt)
        out = spec_dojo.analyse_sample(db_session, db_session, s.id)
        assert out["status"] == "not_green"
        assert out.get("auto_applied") is None
        assert any("0.5 x 21.75" in v for v in out["ground_truth_violations"])
        assert len(asks) == 2  # refinement round ran
        assert "ARITHMETIC" in str(asks[1])
        db_session.refresh(spec)
        assert (spec.instructions or "") == ""  # nothing written


class TestSenseiHandler:
    """norm.sensei_train_supplier: the once-per-supplier guard and the
    analysis-outcome mapping (trained / no_spec_needed / pending_review)."""

    def _call(self, db_session, monkeypatch, supplier="Sensei Foods"):
        from app.agents.internal_tools import get_handler
        from app.db import engine as engine_mod

        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)
        handler = get_handler("norm", "sensei_train_supplier")
        assert handler is not None
        return handler(
            {"venue_id": "v1", "invoice_id": "inv-s1", "supplier_name": supplier},
            db_session,
            None,
        )

    def test_skips_when_spec_has_instructions(self, db_session, monkeypatch):
        _make_spec(db_session, "Sensei Foods")  # instructions "notes"
        out = self._call(db_session, monkeypatch)
        assert out["data"]["status"] == "skipped"
        assert "instructions" in out["data"]["reason"]

    def test_skips_when_spec_already_has_a_sample(self, db_session, monkeypatch):
        spec = _make_spec(db_session, "Sensei Foods")
        spec.instructions = ""
        db_session.add(
            SupplierSpecSample(spec_id=spec.id, label="s.pdf", pdf_bytes=b"%P")
        )
        db_session.flush()
        out = self._call(db_session, monkeypatch)
        assert out["data"]["status"] == "skipped"
        assert "sample" in out["data"]["reason"]

    def test_fresh_supplier_maps_analysis_outcomes(self, db_session, monkeypatch):
        staged = {
            "sample_id": "s-1",
            "spec_id": "sp-1",
            "spec_name": "Fresh Foods",
            "created_spec": True,
            "already_in_dojo": False,
            "was_draft": False,
        }
        monkeypatch.setattr(spec_dojo, "stage_invoice_sample", lambda *a, **k: staged)
        cases = [
            (
                {
                    "status": "applied",
                    "auto_applied": True,
                    "proposed_instructions": "NEW RULES",
                },
                ("trained", "NEW RULES"),
            ),
            (
                {
                    "status": "ready",
                    "spec_not_needed": True,
                    "proposed_instructions": "",
                },
                ("no_spec_needed", ""),
            ),
            (
                {"status": "ready", "proposed_instructions": "TXT"},
                ("pending_review", ""),
            ),
            ({"status": "failed", "error": "boom"}, ("failed", "")),
        ]
        for analysis, (want_status, want_text) in cases:
            monkeypatch.setattr(
                spec_dojo,
                "analyse_sample",
                lambda *a, **k: analysis,  # noqa: B023
            )
            out = self._call(db_session, monkeypatch, supplier="Fresh Foods")
            assert out["data"]["status"] == want_status, analysis
            assert out["data"].get("instructions", "") == want_text

    def test_already_in_dojo_skips_analysis(self, db_session, monkeypatch):
        monkeypatch.setattr(
            spec_dojo,
            "stage_invoice_sample",
            lambda *a, **k: {
                "sample_id": "s-1",
                "spec_id": "sp-1",
                "spec_name": "Fresh Foods",
                "created_spec": False,
                "already_in_dojo": True,
                "was_draft": False,
            },
        )
        called = []
        monkeypatch.setattr(
            spec_dojo, "analyse_sample", lambda *a, **k: called.append(1)
        )
        out = self._call(db_session, monkeypatch, supplier="Fresh Foods")
        assert out["data"]["status"] == "skipped"
        assert called == []


class TestChargesIgnored:
    """The charges concept was removed (08 Aug 2026 — every billed amount is a
    LINE). Old baselines may still carry an inert charges key; the comparator
    must ignore it entirely."""

    def test_stale_charges_key_is_inert(self):
        exp = _extraction(
            charges=[{"description": "Courier Freight", "amount_ex_tax": 30.0}]
        )
        cur = _extraction()
        assert spec_dojo.compare_extractions(exp, cur) == []
        assert spec_dojo.compare_extractions(cur, exp) == []


class TestReplicaStage:
    """The replica: our extraction resolved into a full working document and
    scored against Loaded's own resolution. Venue-less (hand-uploaded)
    samples get no replica; invoice-intake samples get one on every run; the
    replica keys survive every last_run rebuild site."""

    def _sample_with_venue(self, db, spec):
        from app.db.config_models import SupplierSpecSample

        s = SupplierSpecSample(
            spec_id=spec.id,
            label="v.pdf",
            content_type="application/pdf",
            pdf_bytes=b"%PDF-1.4 fake",
            source_venue_id="v-1",
            source_invoice_id="inv-1",
        )
        db.add(s)
        db.flush()
        return s

    def test_hand_upload_gets_no_replica(
        self, client, admin_headers, db_session, monkeypatch
    ):
        spec = _make_spec(db_session, name="NoVenue Foods")
        up = client.post(
            f"/api/supplier-invoice-specs/{spec.id}/samples",
            headers=admin_headers,
            files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")},
        ).json()
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        out = client.post(
            f"/api/supplier-invoice-specs/samples/{up['id']}/run",
            headers=admin_headers,
        ).json()
        assert out["replica"] is None
        assert out["replica_diffs"] == []
        assert out["sample"]["has_replica"] is False

    def test_venue_sample_builds_and_scores_replica(
        self, client, admin_headers, db_session, monkeypatch
    ):
        spec = _make_spec(db_session, name="Venue Foods")
        s = self._sample_with_venue(db_session, spec)
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        fake_replica = {"replica": True, "linked_supplier_id": "sup-1", "lines": []}
        fake_diffs = [
            {
                "field": "linked_item_id",
                "line": 1,
                "description": "HONEY LIQUID",
                "expected": "item-1",
                "actual": None,
            }
        ]
        monkeypatch.setattr(
            spec_dojo,
            "replica_stage",
            lambda *a, **k: (fake_replica, fake_diffs, {"header": [], "lines": []}),
        )
        out = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/run", headers=admin_headers
        ).json()
        assert out["replica"] == fake_replica
        assert out["replica_diffs"] == fake_diffs
        assert out["sample"]["has_replica"] is True
        assert out["sample"]["replica_diff_count"] == 1

        # last-run serves the stored replica
        lr = client.get(
            f"/api/supplier-invoice-specs/samples/{s.id}/last-run",
            headers=admin_headers,
        ).json()
        assert lr["replica"] == fake_replica
        assert lr["replica_diffs"] == fake_diffs

    def test_replica_survives_expected_values_and_save_expected(
        self, client, admin_headers, db_session, monkeypatch
    ):
        spec = _make_spec(db_session, name="Survive Foods")
        s = self._sample_with_venue(db_session, spec)
        monkeypatch.setattr(spec_dojo, "run_extraction", lambda *a, **k: _extraction())
        monkeypatch.setattr(
            spec_dojo,
            "replica_stage",
            lambda *a, **k: ({"replica": True, "lines": []}, [], None),
        )
        client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/run", headers=admin_headers
        )
        # PUT expected-values used to rebuild last_run from scratch.
        r = client.put(
            f"/api/supplier-invoice-specs/samples/{s.id}/expected-values",
            headers=admin_headers,
            json={"expected": _extraction()},
        )
        assert r.status_code == 200
        db_session.expire_all()
        run = db_session.get(type(s), s.id).last_run
        assert run.get("replica") == {"replica": True, "lines": []}
        # POST /expected (promote) keeps it too.
        client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/expected",
            headers=admin_headers,
        )
        db_session.expire_all()
        run2 = db_session.get(type(s), s.id).last_run
        assert run2.get("replica") == {"replica": True, "lines": []}

    def test_errored_replica_not_counted_as_scored(self, db_session):
        from app.routers.supplier_spec_dojo import _sample_meta

        spec = _make_spec(db_session, name="Errored Foods")
        s = self._sample_with_venue(db_session, spec)
        s.last_run = {
            "replica": {"replica": True, "error": "boom", "lines": []},
            "replica_diffs": [],
        }
        db_session.flush()
        meta = _sample_meta(s)
        assert meta["has_replica"] is False

    def test_replica_warning_count_in_meta(self, db_session):
        from app.routers.supplier_spec_dojo import _sample_meta

        spec = _make_spec(db_session, name="Warned Foods")
        s = self._sample_with_venue(db_session, spec)
        s.last_run = {
            "replica": {"replica": True, "warnings": ["dup", "credit"], "lines": []},
            "replica_diffs": [],
        }
        db_session.flush()
        meta = _sample_meta(s)
        assert meta["has_replica"] is True
        assert meta["replica_warning_count"] == 2


class TestEnvVenueResolution:
    """Samples travel between environments via the shared config DB; venue
    ids don't. The Loaded company id stamped at filing time is the
    env-independent key — each environment resolves its OWN venue for the
    same company (16 Aug 2026: every prod-filed sample failed its replica
    build locally with "not connected for venue <prod-id>")."""

    def _sample(self, db, venue_id="prod-venue-x", company="co-123"):
        spec = _make_spec(db, name=f"EnvRes {venue_id}")
        s = SupplierSpecSample(
            spec_id=spec.id,
            label="e.pdf",
            pdf_bytes=b"%PDF-e",
            source_venue_id=venue_id,
            source_invoice_id="inv-e",
            source_company_id=company,
        )
        db.add(s)
        db.flush()
        return s

    def test_same_env_venue_wins(self, db_session):
        from tests.conftest import _make_venue

        v = _make_venue(db_session, name="Here Venue")
        s = self._sample(db_session, venue_id=v.id, company="ignored")
        assert spec_dojo.resolve_sample_venue_id(db_session, s) == v.id

    def test_cross_env_sample_resolves_by_company(self, db_session):
        from app.db.models import ConnectorConfig
        from tests.conftest import _make_venue

        local = _make_venue(db_session, name="Local Twin")
        db_session.add(
            ConnectorConfig(
                connector_name="loadedhub",
                venue_id=local.id,
                enabled="true",
                config={"x_loaded_company_id": "co-123"},
            )
        )
        db_session.flush()
        s = self._sample(db_session, venue_id="prod-venue-x", company="co-123")
        assert spec_dojo.resolve_sample_venue_id(db_session, s) == local.id

    def test_no_company_match_is_an_honest_error(self, db_session):
        s = self._sample(db_session, venue_id="prod-venue-x", company="co-nope")
        assert spec_dojo.resolve_sample_venue_id(db_session, s) is None
        replica, diffs, rows = spec_dojo.replica_stage(
            db_session, db_session, s, {"document_type": "invoice", "lines": []}
        )
        assert replica["error"].startswith("this sample was filed in another")
        assert diffs == [] and rows is None

    def test_staging_stamps_the_company_id(self, db_session, monkeypatch):
        """stage_invoice_sample must record the venue's Loaded company at
        filing time — without it the sample is forever env-locked."""
        import app.db.engine as engine_mod
        import app.services.received_invoice as RI
        from app.db.models import ConnectorConfig
        from tests.conftest import _make_venue

        v = _make_venue(db_session, name="Stamp Venue")
        db_session.add(
            ConnectorConfig(
                connector_name="loadedhub",
                venue_id=v.id,
                enabled="true",
                config={"x_loaded_company_id": "co-stamp"},
            )
        )
        db_session.flush()

        class FakeLoadedClient:
            def __init__(self, db, config_db, venue_id):
                pass

            def invoice(self, invoice_id):
                return {
                    "id": invoice_id,
                    "supplierName": "Stamp Foods",
                    "referenceNumber": "INV-77",
                    "fileId": "file-1",
                }

            def file_base64(self, file_id):
                import base64

                return base64.b64encode(b"%PDF-stamp").decode(), "application/pdf"

        monkeypatch.setattr(RI, "LoadedInvoiceClient", FakeLoadedClient)
        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)
        staged = spec_dojo.stage_invoice_sample(db_session, v.id, "inv-77", draft=False)
        s = db_session.get(SupplierSpecSample, staged["sample_id"])
        assert s.source_company_id == "co-stamp"


class TestReplicaViewRows:
    """The dojo invoice view is built from the replica ALONE — the
    replica-vs-Loaded comparison (and its bless adjudication) was removed
    16 Aug 2026: Loaded's read of the same paper is competing OCR, not a
    reference."""

    def _replica(self):
        return {
            "supplier_name": "Honey Co",
            "reference_number": "INV-9",
            "linked_supplier_id": "sup-1",
            "linked_purchase_order_id": "po-1",
            "purchase_order_number": "1521145",
            "issued_at": "2026-08-10",
            "subtotal": 100.0,
            "tax_amount": 15.0,
            "total": 115.0,
            "lines": [
                {
                    "code": "A1",
                    "description": "HONEY LIQUID",
                    "linked_item_id": "item-h",
                    "linked_unit_id": "u-4kg",
                    "quantity_received": 1,
                    "unit_cost": 77.54,
                    "sale_tax_rate": 0.15,
                    "not_a_display_key": "dropped",
                },
            ],
        }

    def test_rows_carry_replica_values_and_no_loaded_side(self):
        rows = spec_dojo.replica_view_rows(self._replica())
        by_field = {h["field"]: h for h in rows["header"]}
        # The invoice-sheet fields all present, nothing ever "differs".
        for f in (
            "supplier_name",
            "reference_number",
            "linked_supplier_id",
            "linked_purchase_order_id",
            "purchase_order_number",
            "issued_at",
            "subtotal",
            "tax_amount",
            "total",
        ):
            assert f in by_field, f
            assert by_field[f]["loaded"] is None
            assert by_field[f]["differs"] is False
        assert by_field["reference_number"]["replica"] == "INV-9"
        assert len(rows["lines"]) == 1
        line = rows["lines"][0]
        assert line["loaded"] is None and line["diff_fields"] == []
        assert line["replica"]["unit_cost"] == 77.54
        # Slimmed to display keys only.
        assert "not_a_display_key" not in line["replica"]

    def test_bless_route_is_gone(self, client, admin_headers, db_session):
        spec = _make_spec(db_session, name="No Bless Foods")
        s = SupplierSpecSample(spec_id=spec.id, label="x.pdf", pdf_bytes=b"%PDF-x")
        db_session.add(s)
        db_session.flush()
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/replica-expected",
            headers=admin_headers,
        )
        assert res.status_code in (404, 405)


class TestInterruptedAnalysis:
    """An analysis runs in a thread that dies with the process. The row is
    left at "running" with no error and the panel spins forever (Lion Nathan
    94793550, stuck across a restart, 10 Aug 2026). A run older than the
    stale threshold is reported failed so it can be started again."""

    @staticmethod
    def _running(minutes_ago: float) -> dict:
        import datetime as dt

        at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
        return {"status": "running", "thread": [], "at": at.isoformat()}

    def test_fresh_run_still_reports_running(self):
        view = spec_dojo.analysis_view(self._running(2))
        assert view["status"] == "running"
        assert "error" not in view

    def test_orphaned_run_reports_failed_with_a_reason(self):
        view = spec_dojo.analysis_view(
            self._running(spec_dojo.STALE_ANALYSIS_MINUTES + 5)
        )
        assert view["status"] == "failed"
        assert "interrupted" in view["error"]

    def test_the_stored_row_is_not_mutated(self):
        # Pure: another replica may still own the run, and the next real run
        # overwrites the row anyway.
        stored = self._running(spec_dojo.STALE_ANALYSIS_MINUTES + 5)
        spec_dojo.analysis_view(stored)
        assert stored["status"] == "running"

    def test_finished_and_missing_analyses_pass_through(self):
        assert spec_dojo.analysis_view(None) is None
        done = {"status": "ready", "green": True}
        assert spec_dojo.analysis_view(done) is done

    def test_undatable_run_is_left_alone(self):
        odd = {"status": "running", "at": "not-a-date"}
        assert spec_dojo.analysis_view(odd)["status"] == "running"

    def test_sample_meta_surfaces_the_failure(self, db_session):
        from app.routers.supplier_spec_dojo import _sample_meta

        spec = _make_spec(db_session, "Interrupted Co")
        sample = SupplierSpecSample(
            spec_id=spec.id,
            label="stuck.pdf",
            pdf_bytes=b"%PDF-x",
            analysis=self._running(spec_dojo.STALE_ANALYSIS_MINUTES + 1),
        )
        db_session.add(sample)
        db_session.flush()
        assert _sample_meta(sample)["analysis_status"] == "failed"


class TestAutostudyTrigger:
    """The Receive Invoice screen's auto-spec trigger
    (``spec_dojo.autostudy_if_spec_less``): study a supplier that has no
    content-bearing spec, and never block the screen. The gate is one rule — a
    spec WITH content is done; anything else is studied. stage + enqueue are
    stubbed (the queue's own dedup is tested in test_sensei_runner)."""

    def _spec(self, db, name, *, instructions="", enabled=True):
        spec = SupplierInvoiceSpec(
            name=name, aliases=[], instructions=instructions, enabled=enabled
        )
        db.add(spec)
        db.flush()
        return spec

    def _record(self, monkeypatch):
        calls = {"staged": [], "enqueued": []}
        monkeypatch.setattr(
            spec_dojo,
            "stage_invoice_sample",
            lambda db, vid, iid, **k: (
                calls["staged"].append((vid, iid)) or {"sample_id": f"sample-{iid}"}
            ),
        )
        from app.services import sensei_runner

        monkeypatch.setattr(
            sensei_runner,
            "enqueue",
            lambda sid, *a, **k: (calls["enqueued"].append(sid) or "queued"),
        )
        return calls

    def test_a_spec_with_content_is_not_studied(self, db_session, monkeypatch):
        self._spec(db_session, "Acme", instructions="read column 2 as the size")
        calls = self._record(monkeypatch)
        review = {"supplier_name": "Acme"}
        spec_dojo.autostudy_if_spec_less(None, db_session, "v1", "inv-1", review)
        assert calls["staged"] == [] and calls["enqueued"] == []
        assert "sensei_studying" not in review

    def test_a_spec_less_supplier_starts_a_study(self, db_session, monkeypatch):
        calls = self._record(monkeypatch)
        review = {"supplier_name": "Newco"}
        spec_dojo.autostudy_if_spec_less(None, db_session, "v1", "inv-9", review)
        assert calls["staged"] == [("v1", "inv-9")]
        assert calls["enqueued"] == ["sample-inv-9"]
        assert review["sensei_studying"] is True

    def test_an_empty_holder_spec_is_still_studied(self, db_session, monkeypatch):
        # A spec ROW with no instructions (a study still in flight, or a failed
        # one) is NOT "done": the gate checks content, not row existence, so a
        # missing/empty spec always studies again.
        self._spec(db_session, "Holder", instructions="")
        calls = self._record(monkeypatch)
        review = {"supplier_name": "Holder"}
        spec_dojo.autostudy_if_spec_less(None, db_session, "v1", "inv-2", review)
        assert calls["staged"] == [("v1", "inv-2")]
        assert review["sensei_studying"] is True

    def test_no_supplier_name_is_a_noop(self, db_session, monkeypatch):
        calls = self._record(monkeypatch)
        review = {}
        spec_dojo.autostudy_if_spec_less(None, db_session, "v1", "inv-1", review)
        assert calls["staged"] == [] and "sensei_studying" not in review

    def test_a_staging_error_never_escapes(self, db_session, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no invoice copy attached — nothing to add")

        monkeypatch.setattr(spec_dojo, "stage_invoice_sample", boom)
        review = {"supplier_name": "Errco"}
        spec_dojo.autostudy_if_spec_less(None, db_session, "v1", "inv-1", review)
        assert "sensei_studying" not in review
