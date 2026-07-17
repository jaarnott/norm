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
FUNCTION_CODE = (_CONSOLIDATORS / "review_and_receive_invoices.py").read_text(
    encoding="utf-8"
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


def make_call_api(client, received_log):
    def call_api(connector, action, params=None):
        params = params or {}
        try:
            if action == "list_stock_invoices":
                r = client.get(
                    "/1.0/stock/internal/invoices",
                    params={
                        "from": params["from_date"],
                        "to": params["to_date"],
                        "page": params.get("page", 0),
                        "pageSize": params.get("pageSize", 100),
                    },
                )
            elif action == "get_invoice_detail":
                r = client.get(
                    f"/1.0/stock/invoices/{params['invoice_id']}",
                    params={"isAdjustingInvoice": "false", "includeDeleted": "false"},
                )
            elif action == "get_stock_purchase_order":
                r = client.get(
                    f"/1.0/stock/internal/purchase-orders/{params['purchase_order_id']}"
                )
            elif action == "receive_invoice":
                received_log.append(params["invoice_id"])
                r = client.put(
                    f"/1.0/stock/internal/invoices/{params['invoice_id']}",
                    json=params["invoice"],
                )
            else:
                return {"error": f"unexpected action {action}"}
            if r.status_code != 200:
                return {"error": f"API error {r.status_code}: {r.text[:200]}"}
            return r.json()
        except Exception as exc:  # noqa: BLE001 — mirror call_api contract
            return {"error": str(exc)}

    return call_api


def run_code(call_api, extract_document, **params):
    namespace = {
        "__builtins__": _SAFE_BUILTINS,
        **_SAFE_MODULES,
        "extract_document": extract_document,
    }
    exec(FUNCTION_CODE, namespace)
    import datetime as _dt

    defaults = {"today": _dt.date.today().isoformat(), **params}
    return namespace["run"](defaults, call_api, print)


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

    def test_dry_run_pipeline_mutates_nothing(self, lh):
        received_log = []
        call_api = make_call_api(lh, received_log)

        def stub_extract(
            connector, action, params=None, schema=None, instructions=None
        ):
            # Transport-contract stub: echo the draft's own values so PDF gates
            # pass through; the real extraction is unit-tested separately.
            inv = self._current_detail
            return {
                "invoice_number": inv.get("referenceNumber"),
                "lines": [
                    {
                        "code": ln.get("code"),
                        "description": ln.get("description"),
                        "quantity": ln.get("quantityReceived"),
                        "unit_price_ex_tax": ln.get("unitCost"),
                        "line_total_ex_tax": ln.get("totalCost"),
                    }
                    for ln in inv.get("lines", [])
                    if not ln.get("deletedAt")
                ],
                "charges": [],
                "total_incl_tax": inv.get("total"),
            }

        # wrap get_invoice_detail to remember the latest detail for the stub
        original = call_api

        def tracking_call_api(connector, action, params=None):
            result = original(connector, action, params)
            if action == "get_invoice_detail" and isinstance(result, dict):
                self._current_detail = result
            return result

        before = original(
            "loadedhub",
            "list_stock_invoices",
            {"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        before_flags = {i["id"]: i.get("isReceived") for i in before}

        result = run_code(tracking_call_api, stub_extract, dry_run=True)

        assert received_log == [], "dry run must never call receive_invoice"
        assert (
            result["summary"]["received"] + result["summary"]["skipped"]
            == result["reviewed"]
        )
        for verdict in result["skipped"]:
            assert verdict["reasons"], "every skipped invoice must carry reasons"

        after = original(
            "loadedhub",
            "list_stock_invoices",
            {"from_date": "2026-01-01", "to_date": "2026-12-31"},
        )
        after_flags = {i["id"]: i.get("isReceived") for i in after}
        assert before_flags == after_flags, "dry run changed isReceived state!"

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
        received_log = []
        call_api = make_call_api(lh, received_log)

        def stub_extract(
            connector, action, params=None, schema=None, instructions=None
        ):
            inv = self._current_detail
            return {
                "invoice_number": inv.get("referenceNumber"),
                "lines": [
                    {
                        "code": ln.get("code"),
                        "description": ln.get("description"),
                        "quantity": ln.get("quantityReceived"),
                        "unit_price_ex_tax": ln.get("unitCost"),
                        "line_total_ex_tax": ln.get("totalCost"),
                    }
                    for ln in inv.get("lines", [])
                    if not ln.get("deletedAt")
                ],
                "charges": [],
                "total_incl_tax": inv.get("total"),
            }

        def tracking_call_api(connector, action, params=None):
            result = call_api(connector, action, params)
            if action == "get_invoice_detail" and isinstance(result, dict):
                self._current_detail = result
            return result

        result = run_code(tracking_call_api, stub_extract, dry_run=False)

        for verdict in result["received"]:
            assert verdict["invoice_id"] in received_log
            check = call_api(
                "loadedhub", "get_invoice_detail", {"invoice_id": verdict["invoice_id"]}
            )
            assert check.get("isReceived") is True, (
                f"{verdict['reference_number']} not received"
            )
        for verdict in result["skipped"]:
            if "Receive failed" not in " ".join(verdict["reasons"]):
                check = call_api(
                    "loadedhub",
                    "get_invoice_detail",
                    {"invoice_id": verdict["invoice_id"]},
                )
                if isinstance(check, dict) and "error" not in check:
                    assert check.get("isReceived") is not True, (
                        f"skipped invoice {verdict['reference_number']} was modified!"
                    )
