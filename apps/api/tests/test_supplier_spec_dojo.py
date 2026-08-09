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
        monkeypatch.setattr(spec_dojo, "_builtin_main_prompt", lambda src: "MAIN")
        monkeypatch.setattr(spec_dojo, "_engine_source", lambda cdb: "")
        spec = SupplierInvoiceSpec(name="Acme", aliases=[], instructions="OLD NOTES")
        db_session.add(spec)
        db_session.flush()
        out = spec_dojo.compose_instructions(db_session, spec, "NEW NOTES")
        assert "NEW NOTES" in out and "OLD NOTES" not in out and "MAIN" in out

    def test_main_override_replaces_main(self, db_session, monkeypatch):
        monkeypatch.setattr(spec_dojo, "_engine_source", lambda cdb: "")
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

    def test_main_prompt_runs_all_samples(self, db_session, monkeypatch):
        main = SupplierInvoiceSpec(name="Main prompt", aliases=[], instructions="m")
        spec_a = _make_spec(db_session, "Cand C")
        spec_b = _make_spec(db_session, "Cand D")
        db_session.add(main)
        db_session.add_all(
            [
                SupplierSpecSample(
                    spec_id=spec_a.id, label="a.pdf", pdf_bytes=b"%PDF-a"
                ),
                SupplierSpecSample(
                    spec_id=spec_b.id, label="b.pdf", pdf_bytes=b"%PDF-b"
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
        assert out["new"] == 2  # no baselines


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

    def test_green_analysis_ready(self, db_session, monkeypatch):
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
        assert out["proposed_instructions"] == "USE THE UOM COLUMN"
        assert calls["ask"] == 1  # no refinement needed
        db_session.refresh(s)
        assert (s.analysis or {}).get("status") == "ready"
        # A green proposal populates the sample's expected values with the
        # agent's ground truth (the admin no longer hand-types them) and the
        # last run is re-diffed against that baseline.
        assert s.expected == _extraction()
        assert s.last_status == "pass"  # the seeded run matches the baseline

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


class TestAddToDojo:
    def _fake_loaded(self, monkeypatch):
        from app.routers import invoice_fixes as IFX

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

        monkeypatch.setattr(IFX, "_Loaded", FakeLoadedClient)
        return FakeLoadedClient

    def test_creates_spec_and_sample_with_source_refs(
        self, client, admin_headers, db_session, monkeypatch
    ):
        import threading as _threading

        self._fake_loaded(monkeypatch)
        # the request handler opens its own RW config session — point the
        # factory at the shared test session (and don't let it be closed)
        from app.db import engine as engine_mod

        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)

        started = []

        class FakeThread:
            def __init__(self, *a, **k):
                started.append(k.get("name"))

            def start(self):
                pass

        monkeypatch.setattr(_threading, "Thread", FakeThread)

        res = client.post(
            "/api/invoice-fixes/add-to-dojo",
            headers=admin_headers,
            json={"venue_id": "v1", "invoice_id": "inv-42"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["created_spec"] is True
        assert body["spec_name"] == "Tamar Farming Company"
        assert body["analysis"] == "running"
        assert started  # background analysis kicked
        sample = (
            db_session.query(SupplierSpecSample)
            .filter(SupplierSpecSample.id == body["sample_id"])
            .first()
        )
        assert sample.source_invoice_id == "inv-42"
        assert sample.source_venue_id == "v1"
        assert sample.pdf_bytes == b"%PDF-tamar"

        # same invoice again → reused, no duplicate
        res2 = client.post(
            "/api/invoice-fixes/add-to-dojo",
            headers=admin_headers,
            json={"venue_id": "v1", "invoice_id": "inv-42"},
        )
        assert res2.json()["already_in_dojo"] is True
        assert res2.json()["sample_id"] == body["sample_id"]

    def test_requires_admin(self, client, manager_headers, monkeypatch):
        self._fake_loaded(monkeypatch)
        res = client.post(
            "/api/invoice-fixes/add-to-dojo",
            headers=manager_headers,
            json={"venue_id": "v1", "invoice_id": "inv-42"},
        )
        assert res.status_code == 403


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
