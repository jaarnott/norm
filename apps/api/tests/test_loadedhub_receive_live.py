"""Opt-in LIVE integration test for the invoice-receiving consolidator.

Runs the canonical function_code against the REAL LoadedHub TEST environment
(test.loadedhub.com, venue "JA Test - Bessie") using the email/password session
token — no Norm connector plumbing involved, so this validates the LoadedHub
API contract itself: invoice list/detail shapes, PO detail shape, binary PDF
download, and (optionally) the receive PUT.

Skipped unless .local/loadedhub-credentials.json exists (git-ignored) AND
RUN_LOADEDHUB_INTEGRATION=1. The live receive step additionally requires
LOADEDHUB_LIVE_RECEIVE=1 — without it the run is forced to dry_run.

PDF extraction is stubbed from the fetched invoice data: this test verifies the
transport contract (endpoints, auth, shapes, binary download), not the LLM
extraction quality — that gate is covered by unit tests and exercised for real
via the in-app flow.
"""

import json
import os
import pathlib

import httpx
import pytest

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

CREDS_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / ".local"
    / "loadedhub-credentials.json"
)

pytestmark = pytest.mark.skipif(
    not (CREDS_PATH.exists() and os.environ.get("RUN_LOADEDHUB_INTEGRATION") == "1"),
    reason="live LoadedHub test env — set RUN_LOADEDHUB_INTEGRATION=1 with .local creds",
)

_CONSOLIDATORS = (
    pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
)
RECONCILE_CODE = (_CONSOLIDATORS / "reconcile_received_invoices.py").read_text(
    encoding="utf-8"
)


@pytest.fixture(scope="module")
def lh():
    creds = json.loads(CREDS_PATH.read_text())["test"]
    # OAuth password grant — exactly what the Loaded web app sends on login.
    resp = httpx.post(
        creds["token_endpoint"],
        data={
            "grant_type": "password",
            "client_id": "mercury",
            "username": creds["email"],
            "password": creds["password"],
        },
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"login failed: {resp.status_code} {resp.text[:200]}"
    )
    body = resp.json()
    token = body.get("access_token") or body.get("token") or body.get("accessToken")
    assert token, f"no token in login response: {list(body)}"
    client = httpx.Client(
        base_url=creds["api_host"],
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            # Stock endpoints scope data by company — same value Norm stores
            # per venue as ConnectorConfig.config.x_loaded_company_id.
            "x-loaded-company-id": creds["company_id"],
        },
        timeout=60,
    )
    yield client
    client.close()


class TestLiveContract:
    def test_pdf_download_returns_real_pdf(self, lh):
        """The binary download endpoint must return actual PDF bytes."""
        invoices = lh.get(
            "/1.0/stock/internal/invoices",
            params={
                "from": "2026-01-01",
                "to": "2026-12-31",
                "page": 0,
                "pageSize": 50,
            },
        ).json()
        with_file = [i for i in invoices if i.get("fileId") and not i.get("isReceived")]
        if not with_file:
            detailed = [
                lh.get(
                    f"/1.0/stock/invoices/{i['id']}",
                    params={"isAdjustingInvoice": "false", "includeDeleted": "false"},
                ).json()
                for i in invoices[:5]
            ]
            with_file = [d for d in detailed if d.get("fileId")]
        assert with_file, "no invoice with an attached file in the test venue"
        file_id = with_file[0]["fileId"]
        r = lh.get(f"/1.0/stock/internal/invoices/files/{file_id}")
        if r.status_code == 500:
            # Known test-env limitation: invoice files aren't served there.
            # The download contract was verified in production on 16 Jul 2026
            # (F55755100 → application/pdf, bytes identical to the source PDF).
            pytest.skip("test env cannot serve invoice files (known limitation)")
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:5] == b"%PDF-"

    @staticmethod
    def _local_sessions():
        """Real local sessions — the live environment contract: the local DB
        holds real Loaded credentials and the shared config DB the specs."""
        from app.db.engine import SessionLocal, _ConfigSessionLocal

        return SessionLocal(), _ConfigSessionLocal()

    @staticmethod
    def _venue_id(db):
        from app.db.models import ConnectorConfig

        cred = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == "loadedhub",
                ConnectorConfig.enabled == "true",
            )
            .first()
        )
        assert cred, "local DB has no loadedhub credentials"
        return cred.venue_id

    def _flags(self, lh):
        invs = lh.get(
            "/1.0/stock/internal/invoices",
            params={
                "from": "2026-01-01",
                "to": "2026-12-31",
                "page": 0,
                "pageSize": 200,
            },
        ).json()
        return {i["id"]: i.get("isReceived") for i in invs}

    def test_dry_run_pipeline_mutates_nothing(self, lh, monkeypatch):
        """The full service loop (listing, details, replica reference data)
        against real Loaded, extraction stubbed unreadable — approve_all must
        write nothing and every invoice must surface as a card with reasons."""
        from app.services import invoice_review as IR

        db, cdb = self._local_sessions()
        try:
            venue_id = self._venue_id(db)
            before = self._flags(lh)

            monkeypatch.setattr(
                IR,
                "extract_invoice_copies_parallel",
                lambda db_, lh_, reqs, **kw: (
                    [{"error": "stubbed in live transport test"}] * len(reqs)
                ),
            )
            out = IR.review_invoices(db, cdb, venue_id, mode="approve_all")

            assert out["received"] == [], "approve_all must never receive"
            assert len(out["skipped"]) == len(out["verdicts"])
            for card in out["cards"]:
                assert card["doc_schema"] == "replica_v1"
                assert any(
                    i["code"] in ("copy_unreadable", "no_copy_attached")
                    for i in card["issues"]
                )
            after = self._flags(lh)
            assert before == after, "dry run changed isReceived state!"
        finally:
            db.close()
            cdb.close()

    def test_reconcile_dry_run_mutates_nothing(self, lh):
        """Phase 2: the reconciliation pipeline against real statements."""
        writes = []

        def call_api(connector, action, params=None):
            params = params or {}
            try:
                if action == "list_supplier_statements":
                    r = lh.get(
                        "/1.0/stock/internal/supplier-statements",
                        params={
                            "from": params["from_iso"],
                            "to": params["to_iso"],
                            "includeDeleted": "false",
                        },
                    )
                elif action == "list_received_invoices":
                    r = lh.get(
                        "/1.0/stock/internal/stock-received",
                        params={
                            "from": params["from_date"],
                            "to": params["to_date"],
                            "property": "Invoiced",
                            "includeAdjustingInvoices": "true",
                            "ifNoneGetLastReceived": "false",
                        },
                    )
                else:
                    writes.append(action)
                    return {"error": f"unexpected write {action} in dry run"}
                if r.status_code != 200:
                    return {"error": f"API error {r.status_code}"}
                return r.json()
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

        def stub_extract(
            connector, action, params=None, schema=None, instructions=None
        ):
            # Test env can't serve PDFs; transport contract is what's under test.
            return {"error": "pdf extraction stubbed out in test env"}

        namespace = {
            "__builtins__": _SAFE_BUILTINS,
            **_SAFE_MODULES,
            "extract_document": stub_extract,
        }
        exec(RECONCILE_CODE, namespace)
        import datetime as _dt

        result = namespace["run"](
            {"today": _dt.date.today().isoformat(), "dry_run": True},
            call_api,
            print,
        )
        assert writes == [], "dry run attempted a write"
        assert "error" not in result, result.get("error")
        assert result["summary"]["reconciled"] + result["summary"][
            "not_reconciled"
        ] + result["summary"]["needs_statement"] == len(result["results"])
        # With PDF extraction stubbed to fail, nothing may pass the gates —
        # every verdict must carry explicit reasons.
        for verdict in result["not_reconciled"]:
            assert verdict["reasons"]

    @pytest.mark.skipif(
        os.environ.get("LOADEDHUB_LIVE_RECEIVE") != "1",
        reason="live receive writes to the test venue — set LOADEDHUB_LIVE_RECEIVE=1",
    )
    def test_live_receive_flips_isreceived(self, lh):
        """REAL autopilot run — real extraction, real replica, real receives
        against the live test venue. Only confident invoices flip; everything
        else must be untouched."""
        from app.services import invoice_review as IR

        db, cdb = self._local_sessions()
        try:
            venue_id = self._venue_id(db)
            result = IR.review_invoices(db, cdb, venue_id, mode="autopilot")

            for verdict in result["received"]:
                check = lh.get(
                    f"/1.0/stock/invoices/{verdict['invoice_id']}",
                    params={"isAdjustingInvoice": "false", "includeDeleted": "false"},
                ).json()
                assert check.get("isReceived") is True, (
                    f"{verdict['reference_number']} not received"
                )
            for verdict in result["skipped"]:
                if "receive failed" in str(verdict.get("outcome", "")).lower():
                    continue
                check = lh.get(
                    f"/1.0/stock/invoices/{verdict['invoice_id']}",
                    params={"isAdjustingInvoice": "false", "includeDeleted": "false"},
                ).json()
                if isinstance(check, dict):
                    assert check.get("isReceived") is not True, (
                        f"skipped invoice {verdict['reference_number']} was modified!"
                    )
        finally:
            db.close()
            cdb.close()
