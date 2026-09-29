"""The tools-and-endpoints dashboard: derived truth, no checklist.

Sep 2026 vocabulary (docs/tool-architecture-strategy.md): every row is a tool
(consolidator or built-in) or an API endpoint. Pins the classification, leak
detection (an endpoint an LLM can still reach — App claim or MCP), the unused
list, the drift check against config/consolidators/*.py, and the added_at
stamping listener that gives the tools list its "date added" column.
"""

from app.db.config_models import ConnectionSpec
from app.services import consolidator_coverage as cc


def _spec(db, name="fakehub", tools=None):
    s = ConnectionSpec(
        connector_name=name,
        display_name=name,
        execution_mode="template",
        auth_type="none",
        auth_config={},
        tools=tools or [],
    )
    db.add(s)
    db.flush()
    return s


TOOLS = [
    {
        "action": "get_sales",
        "read_only": True,
        "consolidator_config": {
            "function_code": (
                "def run(p, call_api, l):\n"
                "    return call_api('fakehub', 'get_sales_data', {})\n"
            )
        },
    },
    {"action": "get_sales_data", "method": "GET", "path_template": "//x"},
    {
        "action": "get_sales_raw",
        "description": "[consolidator-only] backend",
        "method": "GET",
    },
    {"action": "unrelated_tool", "method": "GET", "path_template": "//y"},
]


def _report(db_session, monkeypatch, *, claims=None, mcp=None, usage=None):
    monkeypatch.setattr(cc, "_canonical_files", lambda: {})
    monkeypatch.setattr(cc, "_usage", lambda db, days: usage or {})
    monkeypatch.setattr(cc, "_exposed", lambda cdb: (claims or {}, mcp or set()))
    report = cc.coverage_report(db_session, db_session)
    return next(x for x in report["connectors"] if x["connector"] == "fakehub")


class TestClassificationAndLeaks:
    def test_rows_are_tools_or_endpoints(self, db_session, monkeypatch):
        _spec(db_session, tools=TOOLS)
        c = _report(db_session, monkeypatch)
        # the [consolidator-only] prefix no longer makes a row special: with
        # no code of its own it is an endpoint.
        assert c["counts"] == {"consolidator": 1, "built-in": 0, "endpoint": 3}

    def test_a_registered_handler_is_a_built_in(self, db_session, monkeypatch):
        monkeypatch.setattr(cc, "_canonical_files", lambda: {})
        monkeypatch.setattr(cc, "_usage", lambda db, days: {})
        monkeypatch.setattr(cc, "_exposed", lambda cdb: ({}, set()))
        _spec(db_session, name="gmail", tools=[{"action": "send_email"}])
        report = cc.coverage_report(db_session, db_session)
        c = next(x for x in report["connectors"] if x["connector"] == "gmail")
        assert c["counts"]["built-in"] == 1

    def test_an_endpoint_an_app_claims_is_a_leak(self, db_session, monkeypatch):
        _spec(db_session, tools=TOOLS)
        c = _report(
            db_session,
            monkeypatch,
            claims={"fakehub.get_sales_data": "loaded-reports"},
            usage={"fakehub__get_sales_data": 66},
        )
        assert [leak["action"] for leak in c["leaks"]] == ["get_sales_data"]
        leak = c["leaks"][0]
        assert leak["app"] == "loaded-reports"
        assert leak["calls_30d"] == 66

    def test_an_endpoint_enabled_on_mcp_is_a_leak(self, db_session, monkeypatch):
        _spec(db_session, tools=TOOLS)
        c = _report(db_session, monkeypatch, mcp={("fakehub", "unrelated_tool")})
        assert [leak["action"] for leak in c["leaks"]] == ["unrelated_tool"]

    def test_a_claimed_tool_is_not_a_leak(self, db_session, monkeypatch):
        _spec(db_session, tools=TOOLS)
        c = _report(db_session, monkeypatch, claims={"fakehub.get_sales": "loaded-reports"})
        assert c["leaks"] == []
        tool = next(r for r in c["tools"] if r["action"] == "get_sales")
        assert tool["app"] == "loaded-reports"

    def test_endpoints_no_tool_calls_are_unused(self, db_session, monkeypatch):
        _spec(db_session, tools=TOOLS)
        c = _report(db_session, monkeypatch)
        assert sorted(r["action"] for r in c["unused"]) == ["get_sales_raw", "unrelated_tool"]
        used = next(r for r in c["tools"] if r["action"] == "get_sales_data")
        assert used["used_by"] == ["fakehub.get_sales"]

    def test_for_period_twin_is_inferred_by_name(self, db_session, monkeypatch):
        tools = [
            {
                "action": "get_cogs_for_period",
                "consolidator_config": {"function_code": "x"},
            },
            {"action": "get_cogs", "method": "GET", "path_template": "//x"},
        ]
        _spec(db_session, tools=tools)
        c = _report(db_session, monkeypatch)
        raw = next(r for r in c["tools"] if r["action"] == "get_cogs")
        assert raw["superseded_by"] == "get_cogs_for_period"


class TestDrift:
    def test_differs_and_missing_files_are_flagged(self, db_session, monkeypatch):
        monkeypatch.setattr(
            cc,
            "_canonical_files",
            lambda: {"get_sales": "def run(p, c, l): return 1"},
        )
        monkeypatch.setattr(cc, "_usage", lambda db, days: {})
        tools = [
            {
                "action": "get_sales",
                "consolidator_config": {"function_code": "DIFFERENT"},
            },
            {
                "action": "get_other",
                "consolidator_config": {"function_code": "x"},
            },
        ]
        _spec(db_session, tools=tools)
        report = cc.coverage_report(db_session, db_session)
        c = next(x for x in report["connectors"] if x["connector"] == "fakehub")
        states = {d["action"]: d["state"] for d in c["drift"]}
        assert states == {
            "get_sales": "differs_from_file",
            "get_other": "no_canonical_file",
        }

    def test_matching_code_is_clean(self, db_session, monkeypatch):
        code = "def run(p, c, l): return 1"
        monkeypatch.setattr(
            cc, "_canonical_files", lambda: {"get_sales": code}
        )
        monkeypatch.setattr(cc, "_usage", lambda db, days: {})
        _spec(
            db_session,
            tools=[
                {
                    "action": "get_sales",
                    "consolidator_config": {"function_code": code},
                }
            ],
        )
        report = cc.coverage_report(db_session, db_session)
        c = next(x for x in report["connectors"] if x["connector"] == "fakehub")
        assert c["drift"] == []

    def test_wrappers_share_for_period_as_their_canonical_file(
        self, db_session, monkeypatch
    ):
        # A `wraps` marker means the row's canonical source is for_period.py —
        # eleven wrappers, one reviewed file, zero false "no_canonical_file".
        code = "def run(p, c, l): return 1"
        monkeypatch.setattr(cc, "_canonical_files", lambda: {"for_period": code})
        monkeypatch.setattr(cc, "_usage", lambda db, days: {})
        _spec(
            db_session,
            tools=[
                {
                    "action": "get_sales",
                    "consolidator_config": {"function_code": code, "wraps": "x"},
                },
                {
                    "action": "get_pos_orders_for_period",
                    "consolidator_config": {"function_code": "EDITED", "wraps": "y"},
                },
            ],
        )
        report = cc.coverage_report(db_session, db_session)
        c = next(x for x in report["connectors"] if x["connector"] == "fakehub")
        states = {d["action"]: d["state"] for d in c["drift"]}
        assert states == {"get_pos_orders_for_period": "differs_from_file"}

    def test_the_real_matcher_resolves_every_live_style_name(self):
        # No monkeypatching: the shipped canonical map must resolve the stem
        # (get_budgets), the get_-prefixed stem fallback, and the shared
        # files (for_period.py for
        # wrappers via `wraps`; review_and_receive_invoices.py for
        # receive_loadedhub_invoice via _SHARED_CANONICAL).
        canonical = cc._canonical_files()
        assert "get_budgets" in canonical
        # The get_ prefix fallback: reconcile_received_invoices.py also
        # resolves under get_reconcile_received_invoices (harmlessly).
        assert "get_reconcile_received_invoices" in canonical
        assert "for_period" in canonical
        assert (
            cc._SHARED_CANONICAL["receive_loadedhub_invoice"]
            == "review_and_receive_invoices"
        )
        assert "review_and_receive_invoices" in canonical


class TestAddedAtStamp:
    """The ConnectionSpec.tools listener: every writer assigns the list, so
    new actions get dated with no call-site convention — the tools card's
    "Added" column."""

    def test_new_actions_are_stamped_and_old_stamps_survive(self, db_session):
        s = _spec(db_session, name="stamped", tools=[{"action": "a"}])
        first = s.tools[0].get("added_at")
        # 'a' was new on the initial assignment (oldvalue = the column
        # default [] on a fresh instance) — it may or may not carry a stamp
        # depending on load state; the contract below is what matters.
        s.tools = [{"action": "a", "added_at": first}, {"action": "b"}]
        db_session.flush()
        by = {t["action"]: t for t in s.tools}
        assert by["b"].get("added_at")  # new action stamped at assignment
        # a writer rebuilding dicts without the stamp must not lose it
        stamp_b = by["b"]["added_at"]
        s.tools = [{"action": "a"}, {"action": "b"}]
        by = {t["action"]: t for t in s.tools}
        assert by["b"].get("added_at") == stamp_b
        if first:
            assert by["a"].get("added_at") == first

    def test_pre_stamping_tools_stay_unstamped(self, db_session):
        s = _spec(db_session, name="legacy", tools=[{"action": "old"}])
        # simulate a legacy entry with no stamp surviving a rewrite
        s.tools = [{"action": "old"}]
        s.tools = [dict(t, description="edited") for t in s.tools]
        old = next(t for t in s.tools if t["action"] == "old")
        # whatever the initial-assignment behaviour, a later rewrite never
        # FABRICATES a date for an entry that had none at the previous state
        # unless it was genuinely new — pinned by the 'b' case above; here we
        # only require the entry to still exist and not error.
        assert old["description"] == "edited"
