"""The review_and_receive_invoices consolidator — orchestration only.

The engine's whole job is: pick the window/invoice, translate the caller's
run mode into the service's policy, call norm.review_invoices ONCE, and
report. All invoice intelligence lives in app/services/invoice_review.py
(pinned by tests/test_invoice_review.py); these tests exec the REAL
function_code inside the real sandbox namespace and pin the orchestration
contract: param plumbing, mode policy, the chat report shapes, and the
fix_invoices passthrough that drives the working-document fan-out.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "review_and_receive_invoices.py"
).read_text(encoding="utf-8")


class Api:
    """Scriptable norm.review_invoices endpoint."""

    def __init__(self, response=None, error=None):
        self.calls: list[tuple[str, str, dict]] = []
        self.response = response if response is not None else {"cards": []}
        self.error = error
        self.resolved = None

    def call_api(self, connector, action, params=None):
        self.calls.append((connector, action, dict(params or {})))
        if connector == "norm" and action == "resolve_dates":
            # The one other allowed call: the optional `period` resolution.
            return dict(self.resolved) if self.resolved else {"error": "offline"}
        assert connector == "norm" and action == "review_invoices", (
            f"unexpected call {connector}.{action} — the engine is "
            "orchestration-only and may call nothing else"
        )
        if self.error:
            return {"error": self.error}
        return dict(self.response)


def run_consolidator(api: Api, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, namespace)  # noqa: S102 — the real sandbox namespace
    defaults = {"today": "2026-08-10", "venue": "Bessie", "mode": "approve_fixes"}
    defaults.update(params)
    return namespace["run"](defaults, api.call_api, lambda m: None)


def _verdict(**over):
    v = {
        "invoice_id": "inv-1",
        "reference_number": "F100",
        "supplier_name": "Akaroa Salmon",
        "po_number": "1520001",
        "total": 252.75,
        "confidence": "ready",
        "suggestions": 0,
        "reasons": [],
    }
    v.update(over)
    return v


class TestRequestPlumbing:
    def test_one_call_with_window_and_policy(self):
        api = Api()
        run_consolidator(api, mode="autopilot")
        assert len(api.calls) == 1
        req = api.calls[0][2]
        assert req["venue"] == "Bessie"
        assert req["mode"] == "autopilot"
        assert req["from_date"] == "2026-06-11"  # today - 60d
        assert req["to_date"] == "2026-08-10"
        assert req["max_sensei"] == 2
        assert req["require_valid_po"] is True

    def test_explicit_window_wins(self):
        api = Api()
        run_consolidator(api, from_date="2026-08-01", to_date="2026-08-05")
        req = api.calls[0][2]
        assert req["from_date"] == "2026-08-01"
        assert req["to_date"] == "2026-08-05"

    def test_require_valid_po_false_passes_through(self):
        api = Api()
        run_consolidator(api, require_valid_po=False)
        assert api.calls[0][2]["require_valid_po"] is False

    def test_an_unset_personal_mode_lets_the_venue_decide(self):
        """Receiving is the venue's setting now. Substituting approve_all here
        looked like a safe default and was an override: the server treats what
        this sends as a CEILING, so a hard-coded approve_all pinned every venue
        to it and the ladder could never take effect."""
        api = Api()
        out = run_consolidator(api, mode=None)
        assert api.calls[0][2]["mode"] == "unset"
        assert out["mode"] == "unset"
        assert out["mode_unset"] is True
        # Reported from what came back, not predicted from the mode.
        assert out["dry_run"] is True  # this fixture receives nothing
        assert out["auto_submit"] is False

    def test_approve_fixes_passes_through(self):
        api = Api()
        out = run_consolidator(api, mode="approve_fixes")
        assert api.calls[0][2]["mode"] == "approve_fixes"
        assert out["auto_submit"] is False
        # dry_run reports whether anything was WRITTEN, not whether writing was
        # permitted — this engine no longer knows the latter, since the venue
        # owns the rung. Nothing came back received, so nothing was written.
        assert out["dry_run"] is True

    def test_dry_run_is_false_once_something_is_actually_received(self):
        api = Api(
            {"cards": [], "received": [_verdict(outcome="received")], "skipped": []}
        )
        out = run_consolidator(api, mode="autopilot")
        assert out["dry_run"] is False

    def test_autopilot_sets_auto_submit(self):
        api = Api()
        out = run_consolidator(api, mode="autopilot")
        assert out["auto_submit"] is True


class TestSingleInvoiceMode:
    def test_reviews_one_invoice_present_only(self):
        api = Api()
        out = run_consolidator(api, invoice_id="inv-77", mode="autopilot")
        req = api.calls[0][2]
        assert req["invoice_ids"] == ["inv-77"]
        # single-invoice review NEVER auto-writes, whatever the user's mode
        assert req["mode"] == "approve_all"
        assert "from_date" not in req
        assert out["mode"] == "autopilot"  # reported mode stays the caller's


class TestReporting:
    def test_cards_ride_to_fix_invoices_verbatim(self):
        cards = [{"invoice_id": "inv-1", "doc_schema": "replica_v1", "lines": []}]
        api = Api(
            {
                "cards": cards,
                "received": [],
                "skipped": [_verdict(outcome="needs review", confidence=None)],
                "sensei": [],
            }
        )
        out = run_consolidator(api)
        assert out["fix_invoices"] == cards  # the doc fan-out's items_path

    def test_received_and_skipped_verdict_shapes(self):
        api = Api(
            {
                "cards": [],
                "received": [_verdict(outcome="received")],
                "skipped": [
                    _verdict(
                        invoice_id="inv-2",
                        reference_number="F200",
                        confidence="needs_review",
                        reasons=["no invoice copy is attached in Loaded"],
                        outcome="needs review",
                    )
                ],
                "sensei": [],
            }
        )
        out = run_consolidator(api)
        assert out["summary"] == {"received": 1, "skipped": 1}
        got = out["received"][0]
        assert got["outcome"] == "received"
        assert got["total"] == "$252.75"  # money-formatted for the chat table
        skip = out["skipped"][0]
        assert skip["reference_number"] == "F200"
        assert skip["reasons"] == ["no invoice copy is attached in Loaded"]

    def test_ready_awaiting_approval_reports_as_pending_not_skip(self):
        api = Api(
            {
                "cards": [{"invoice_id": "inv-1"}],
                "received": [],
                "skipped": [_verdict(outcome="ready to receive — awaiting approval")],
                "sensei": [],
            }
        )
        out = run_consolidator(api, mode="approve_all")
        assert out["skipped"] == []
        assert out["received"][0]["outcome"] == "awaiting your approval"
        assert out["dry_run"] is True

    def test_rows_summarise_confidence_and_suggestions(self):
        api = Api(
            {
                "cards": [],
                "received": [],
                "skipped": [
                    _verdict(
                        confidence="needs_review",
                        suggestions=3,
                        reasons=["unit can't be determined"],
                        outcome="needs review",
                    )
                ],
                "sensei": [],
            }
        )
        out = run_consolidator(api)
        row = out["results"][0]
        assert row["reference"] == "F100"
        assert row["po"] == "1520001"
        assert row["checks"] == "1 blocking ✗ 3 suggested"
        assert row["reasons"] == "unit can't be determined"

    def test_missing_reference_number_labelled(self):
        api = Api(
            {
                "cards": [],
                "received": [_verdict(reference_number=None, outcome="received")],
                "skipped": [],
            }
        )
        out = run_consolidator(api)
        assert out["received"][0]["reference_number"] == "(no number)"

    def test_sensei_runs_logged(self):
        api = Api(
            {
                "cards": [],
                "received": [],
                "skipped": [],
                "sensei": [{"invoice_id": "inv-9", "supplier_name": "Harbour Fish"}],
            }
        )
        logs = []
        namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, namespace)  # noqa: S102
        namespace["run"](
            {"today": "2026-08-10", "venue": "Bessie", "mode": "approve_fixes"},
            api.call_api,
            logs.append,
        )
        assert any("sensei" in m and "Harbour Fish" in m for m in logs)

    def test_service_error_surfaces(self):
        api = Api(error="config DB down")
        out = run_consolidator(api)
        assert "Review service failed" in out["error"]
        assert "config DB down" in out["error"]


class TestEngineNormToolsPublished:
    """Every norm.* function the engine calls must be PUBLISHED, not just coded.

    The sandbox's call_api resolves a tool from the `norm` ConnectorSpec row
    before routing to the Python handler — a handler with no spec entry dies
    with "Tool not found" at runtime and the engine degrades silently.
    match_supplier shipped exactly that way (8 Aug 2026) because only the
    handler existed. The spec rows live in the shared config DB and are
    written by scripts/sync_*_tool.py, so this asserts each engine call site
    has BOTH a registered handler and a sync script publishing the action.
    """

    ENGINE_CALL_RE = r'call_api\(\s*"norm",\s*"(\w+)"'

    def _engine_actions(self):
        import re

        return set(re.findall(self.ENGINE_CALL_RE, FUNCTION_CODE))

    def test_call_sites_found(self):
        # The regex must keep matching the engine's call style — an empty set
        # would vacuously pass the real assertions below.
        assert self._engine_actions() == {"review_invoices"}

    def test_every_engine_norm_call_has_a_handler(self):
        from app.agents.internal_tools import get_handler

        missing = [a for a in self._engine_actions() if get_handler("norm", a) is None]
        assert not missing, f"engine calls norm.{missing} with no internal handler"

    def test_every_engine_norm_call_has_a_sync_script(self):
        scripts_dir = pathlib.Path(__file__).resolve().parent.parent / "scripts"
        published = ""
        for script in scripts_dir.glob("sync_*.py"):
            published += script.read_text(encoding="utf-8")
        missing = [
            a
            for a in self._engine_actions()
            if f'"action": "{a}"' not in published
            and f"'action': '{a}'" not in published
        ]
        assert not missing, (
            f"engine calls norm.{missing} but no scripts/sync_*.py publishes it "
            "into the norm ConnectorSpec — call_api will raise 'Tool not found' "
            "in production; add a sync script (see sync_review_invoices_tool.py)"
        )


class TestSyncConfigContract:
    """The sync script's tool definitions must match what the engine needs."""

    def test_write_action_declared(self):
        import importlib.util

        spec_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "scripts"
            / "sync_invoice_receiving_config.py"
        )
        spec = importlib.util.spec_from_file_location("sync_cfg", spec_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cfg = mod.CONSOLIDATOR_TOOL["consolidator_config"]
        assert cfg["allowed_write_actions"] == ["review_invoices"]
        # the single-invoice tool runs the SAME engine and needs the same grant
        pcfg = mod.PREPARE_RECEIVE_TOOL["consolidator_config"]
        assert pcfg["allowed_write_actions"] == ["review_invoices"]
        assert mod.PREPARE_RECEIVE_TOOL["working_document"]["items_path"] == (
            "fix_invoices"
        )
        assert "receive_invoice" in mod.RETIRED_ACTIONS


class TestPeriodResolution:
    def test_period_resolves_to_calendar_dates(self):
        api = Api()
        api.resolved = {
            "window": {
                "start": "2026-07-01T07:00:00+12:00",
                "end": "2026-08-01T06:59:59+12:00",
                "trading_aligned": True,
            }
        }
        run_consolidator(api, period="last month")
        req = next(p for (_c, a, p) in api.calls if a == "review_invoices")
        assert req["from_date"] == "2026-07-01"
        assert req["to_date"] == "2026-08-01"

    def test_no_period_keeps_the_sixty_day_default(self):
        api = Api()
        run_consolidator(api)
        req = next(p for (_c, a, p) in api.calls if a == "review_invoices")
        assert req["from_date"] == "2026-06-11"  # today (10 Aug) - 60 days
        assert not [c for c in api.calls if c[1] == "resolve_dates"]
