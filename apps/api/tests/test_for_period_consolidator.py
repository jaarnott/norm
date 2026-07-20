"""Tests for the shared `for_period` consolidator function_code.

Same harness as the other consolidator tests: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, with a
scriptable fake for call_api.

What these tools exist to prevent: sales for "yesterday" were computed
midnight-to-midnight, so a late-night venue's post-midnight trade fell outside
the window and it read $0 for a Saturday — which looks like a POS outage rather
than a bad window. One implementation fronts every date-taking action, so the
behaviour is pinned once here rather than per tool.
"""

import pathlib

import pytest

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "for_period.py"
).read_text(encoding="utf-8")

TRADING = {
    "start": "2026-07-18T07:00:00+12:00",
    "end": "2026-07-19T06:59:59+12:00",
    "kind": "trading_day",
    "label": "Yesterday",
    "timezone": "Pacific/Auckland",
    "day_start": "07:00",
    "trading_aligned": True,
    "description": "Yesterday — Sat 18 Jul 07:00 → Sun 19 Jul 06:59 Pacific/Auckland",
}
CIVIL = dict(
    TRADING,
    start="2026-07-18T00:00:00+12:00",
    end="2026-07-19T00:00:00+12:00",
    kind="custom",
    label="Custom window",
    trading_aligned=False,
    description=(
        "Custom window — Sat 18 Jul 00:00 → Sun 19 Jul 00:00 Pacific/Auckland. "
        "Not a trading day: the venue's day starts at 07:00, so this splits a "
        "trading session."
    ),
)

SALES_OPTS = {
    "wraps": "get_sales_data",
    "start_param": "start_datetime",
    "end_param": "end_datetime",
}


class Api:
    def __init__(
        self, window=TRADING, payload=None, resolve_error=None, wrapped_error=None
    ):
        self.window = window
        self.payload = payload if payload is not None else {"total": 15945}
        self.resolve_error = resolve_error
        self.wrapped_error = wrapped_error
        self.calls = []

    def call_api(self, connector, action, params=None):
        self.calls.append((action, params or {}))
        if action == "resolve_dates":
            if self.resolve_error:
                return {"error": self.resolve_error}
            return {"success": True, "data": {"window": self.window}}
        if self.wrapped_error:
            return {"error": self.wrapped_error}
        return self.payload

    def actions(self):
        return [a for a, _ in self.calls]

    def params_for(self, action):
        return next(p for a, p in self.calls if a == action)


def run_consolidator(api, options=None, **params):
    namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, namespace)
    return namespace["run"](
        {"venue": "La Zeppa", **params},
        api.call_api,
        lambda m: None,
        None,
        {**SALES_OPTS, **(options or {})},
    )


class TestPeriodPath:
    def test_period_resolves_through_norms_calendar(self):
        api = Api()
        run_consolidator(api, period="yesterday", venue_id="v1")
        sent = api.params_for("resolve_dates")
        assert sent["query"] == "yesterday"
        assert sent["venue_id"] == "v1"  # so the venue's own day start applies

    def test_result_always_states_the_window_used(self):
        """The echo. A $0 venue is only diagnosable if you can see the window."""
        result = run_consolidator(Api(), period="yesterday")
        assert result["window"]["description"] == TRADING["description"]
        assert result["data"] == {"total": 15945}

    def test_missing_period_and_range_is_refused_without_fetching(self):
        api = Api()
        result = run_consolidator(api)
        assert "plain English" in result["error"]
        assert api.actions() == []


class TestParamMapping:
    """The wrapped actions name their window seven different ways. One config
    entry per tool maps it, so a caller learns one shape."""

    @pytest.mark.parametrize(
        "wraps,sp,ep",
        [
            ("get_sales_data", "start_datetime", "end_datetime"),
            ("get_pos_orders", "start", "end"),
            ("get_pos_item_sales", "start_time", "end_time"),
            ("get_received_invoices", "from", "to"),
            ("list_supplier_statements", "from_iso", "to_iso"),
            ("get_completed_stocktakes", "start_date", "end_date"),
        ],
    )
    def test_window_lands_on_the_wrapped_actions_param_names(self, wraps, sp, ep):
        api = Api()
        run_consolidator(
            api,
            options={"wraps": wraps, "start_param": sp, "end_param": ep},
            period="yesterday",
        )
        assert wraps in api.actions()
        sent = api.params_for(wraps)
        assert sent[sp] == TRADING["start"]
        assert sent[ep] == TRADING["end"]

    def test_other_arguments_are_forwarded(self):
        """Wrapped actions have their own required params (interval,
        posIdentifier, flags) — fronting must not swallow them."""
        api = Api()
        run_consolidator(
            api, period="yesterday", interval="1.00:00:00", posIdentifier="pos-7"
        )
        sent = api.params_for("get_sales_data")
        assert sent["interval"] == "1.00:00:00"
        assert sent["posIdentifier"] == "pos-7"
        assert sent["venue"] == "La Zeppa"

    def test_consumed_params_are_not_forwarded(self):
        api = Api()
        run_consolidator(api, period="yesterday", venue_id="v1")
        sent = api.params_for("get_sales_data")
        for key in ("period", "venue_id", "confirmed_by_user"):
            assert key not in sent

    def test_misconfiguration_is_reported_not_guessed(self):
        api = Api()
        result = run_consolidator(api, options={"wraps": None}, period="yesterday")
        assert "Misconfigured" in result["error"]
        assert api.actions() == []


class TestDeviation:
    def test_deviating_range_asks_before_fetching(self):
        api = Api(window=CIVIL)
        result = run_consolidator(
            api, start="2026-07-18T00:00:00+12:00", end="2026-07-19T00:00:00+12:00"
        )
        assert result["needs_confirmation"] is True
        assert "get_sales_data" not in api.actions()

    def test_the_question_is_answerable_from_fact(self):
        """Asking 'is this right?' invites agreement and would launder a mistake
        as confirmed. Ask what the user actually said instead."""
        result = run_consolidator(
            Api(window=CIVIL),
            start="2026-07-18T00:00:00+12:00",
            end="2026-07-19T00:00:00+12:00",
        )
        assert "explicitly ask" in result["question"]
        assert "confirmed_by_user=true" in result["question"]

    def test_confirmed_deviating_range_is_honoured_verbatim(self):
        """Reconciling against a bank statement legitimately wants civil days."""
        api = Api(window=CIVIL)
        run_consolidator(
            api,
            start="2026-07-18T00:00:00+12:00",
            end="2026-07-19T00:00:00+12:00",
            confirmed_by_user=True,
        )
        sent = api.params_for("get_sales_data")
        assert sent["start_datetime"] == CIVIL["start"]

    def test_aligned_explicit_range_needs_no_confirmation(self):
        api = Api(window=TRADING)
        result = run_consolidator(api, start=TRADING["start"], end=TRADING["end"])
        assert "needs_confirmation" not in result
        assert "get_sales_data" in api.actions()


class TestFailures:
    def test_resolver_error_fetches_nothing(self):
        api = Api(resolve_error="timezone lookup failed")
        result = run_consolidator(api, period="yesterday")
        assert "Could not resolve the period" in result["error"]
        assert "get_sales_data" not in api.actions()

    def test_unresolvable_phrase_suggests_a_simpler_one(self):
        api = Api(window=None)
        result = run_consolidator(api, period="the week before the long weekend")
        assert "yesterday" in result["error"]
        assert "get_sales_data" not in api.actions()

    def test_wrapped_error_still_reports_the_window(self):
        api = Api(wrapped_error="LoadedHub 500")
        result = run_consolidator(api, period="yesterday")
        assert result["error"] == "LoadedHub 500"
        assert result["window"]["description"] == TRADING["description"]


class TestSandboxSafety:
    def test_runs_without_imports(self):
        assert "\nimport " not in FUNCTION_CODE
        assert "\nfrom " not in FUNCTION_CODE

    def test_every_registered_tool_declares_no_writes(self):
        """These are declared GET and so bypass the approval gate. Wrapping a
        write with this pattern would route around it."""
        import importlib.util

        spec_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "scripts"
            / "sync_for_period_config.py"
        )
        spec = importlib.util.spec_from_file_location("sync_for_period", spec_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for action, wraps, sp, ep, returns in module.WRAPPED:
            tool = module.tool_for(action, wraps, sp, ep, returns, "")
            assert tool["method"] == "GET"
            assert tool["consolidator_config"]["allowed_write_actions"] == []


class TestWrappedArgumentsAreReachable:
    """Fronting an action must not hide what that action requires.

    The wrapper declared `required_fields: []`, so get_sales_data's `interval`
    (and posIdentifier, and the three timeclock flags) had no place in the
    schema. Every call resolved the trading-day window correctly and then died
    with "Missing required fields: interval" — a failure that reads like a date
    bug and isn't. Five of the thirteen tools were affected.
    """

    def _module(self):
        import importlib.util
        import pathlib

        path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "scripts"
            / "sync_for_period_config.py"
        )
        spec = importlib.util.spec_from_file_location("sync_for_period", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _wrapped(self):
        return {
            "action": "get_sales_data",
            "required_fields": ["interval", "start_datetime", "end_datetime"],
            "optional_fields": ["posIdentifier"],
            "field_descriptions": {
                "interval": "Bucket size, e.g. 1.00:00:00",
                "start_datetime": "ISO start",
                "posIdentifier": "POS id",
            },
            "field_schema": {"interval": {"type": "string"}},
        }

    def test_required_fields_of_the_wrapped_action_are_required_here(self):
        m = self._module()
        tool = m.tool_for(
            "get_sales_for_period",
            "get_sales_data",
            "start_datetime",
            "end_datetime",
            "Sales",
            "",
            wrapped=self._wrapped(),
        )
        assert "interval" in tool["required_fields"]

    def test_the_date_params_it_replaces_are_not_inherited(self):
        """They are the whole point of the wrapper — re-exposing them would put
        the caller back in the business of computing timestamps."""
        m = self._module()
        tool = m.tool_for(
            "get_sales_for_period",
            "get_sales_data",
            "start_datetime",
            "end_datetime",
            "Sales",
            "",
            wrapped=self._wrapped(),
        )
        fields = set(tool["required_fields"]) | set(tool["optional_fields"])
        assert "start_datetime" not in fields
        assert "end_datetime" not in fields
        assert {"period", "start", "end", "confirmed_by_user"} <= fields

    def test_optional_fields_and_their_descriptions_carry_over(self):
        m = self._module()
        tool = m.tool_for(
            "get_sales_for_period",
            "get_sales_data",
            "start_datetime",
            "end_datetime",
            "Sales",
            "",
            wrapped=self._wrapped(),
        )
        assert "posIdentifier" in tool["optional_fields"]
        assert tool["field_descriptions"]["posIdentifier"] == "POS id"
        assert tool["field_schema"]["interval"] == {"type": "string"}
        # Our own descriptions still win for the fields we define.
        assert tool["field_descriptions"]["period"] == m.PERIOD_DESC

    def test_inherited_fields_reach_the_wrapped_call(self):
        """Schema exposure is only half of it — the consolidator must forward
        the value through, which is what _CONSUMED governs."""
        api = Api()
        run_consolidator(api, period="yesterday", interval="1.00:00:00")
        assert api.params_for("get_sales_data")["interval"] == "1.00:00:00"

    def test_a_field_the_template_defaults_becomes_optional_and_is_filled(self):
        """get_sales_data renders `interval | default('1.00:00:00')`, so the
        request always had a value — only the required-field check refused it.
        Filling it here means a caller working from a stale copy of the schema
        still succeeds, which is otherwise unfixable from the server side."""
        m = self._module()
        wrapped = dict(
            self._wrapped(),
            path_template="/pos/sales?interval={{ interval | default('1.00:00:00') }}",
        )
        tool = m.tool_for(
            "get_sales_for_period",
            "get_sales_data",
            "start_datetime",
            "end_datetime",
            "Sales",
            "",
            wrapped=wrapped,
        )
        assert "interval" not in tool["required_fields"]
        assert "interval" in tool["optional_fields"]
        assert tool["consolidator_config"]["defaults"] == {"interval": "1.00:00:00"}

        api = Api()
        run_consolidator(
            api,
            options={"defaults": {"interval": "1.00:00:00"}},
            period="yesterday",
        )
        assert api.params_for("get_sales_data")["interval"] == "1.00:00:00"

    def test_a_caller_supplied_value_beats_the_default(self):
        api = Api()
        run_consolidator(
            api,
            options={"defaults": {"interval": "1.00:00:00"}},
            period="yesterday",
            interval="0.01:00:00",
        )
        assert api.params_for("get_sales_data")["interval"] == "0.01:00:00"

    def test_fields_without_a_declared_default_stay_required(self):
        """We must not invent a posIdentifier — an invented id would query the
        wrong POS rather than fail."""
        m = self._module()
        wrapped = {
            "action": "get_staff_item_orders",
            "required_fields": ["posIdentifier", "start", "end"],
            "path_template": "/staff?pos={{ posIdentifier }}",
        }
        tool = m.tool_for(
            "get_staff_item_orders_for_period", "get_staff_item_orders",
            "start", "end", "Orders", "", wrapped=wrapped,
        )
        assert "posIdentifier" in tool["required_fields"]
        assert tool["consolidator_config"]["defaults"] == {}

    def test_a_schema_valid_call_satisfies_the_executors_required_check(self):
        """The end-to-end assertion, run locally.

        Builds the wrapper the sync script would write, calls it with only what
        that schema allows, then applies spec_executor's own missing-required
        computation to the params the consolidator forwards. This is the check
        that was never run before shipping: the window resolved fine and the
        call died one layer further in.
        """
        m = self._module()
        wrapped_row = self._wrapped()
        tool = m.tool_for(
            "get_sales_for_period",
            "get_sales_data",
            "start_datetime",
            "end_datetime",
            "Sales",
            "",
            wrapped=wrapped_row,
        )
        # A caller may only supply what the wrapper exposes.
        allowed = set(tool["required_fields"]) | set(tool["optional_fields"])
        call = {f: "1.00:00:00" for f in tool["required_fields"]}
        call["period"] = "yesterday"
        assert set(call) <= allowed

        api = Api()
        run_consolidator(api, **call)
        forwarded = api.params_for("get_sales_data")

        # spec_executor.execute_spec, verbatim.
        missing = [
            f
            for f in wrapped_row["required_fields"]
            if f not in forwarded or not forwarded[f]
        ]
        assert missing == [], f"would fail with: Missing required fields: {missing}"


class TestSummaryFieldsAreInherited:
    """A wrapper must not be more context-hostile than the action it fronts.

    The raw tool projects to its summary_fields when a result is oversized; the
    wrapper had none, so it fell through to the "_too_large" stub and the model
    got a sample row instead of the data. get_received_invoices carried
    summary_fields while get_received_invoices_for_period did not.
    """

    def _module(self):
        import importlib.util
        import pathlib

        path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "scripts"
            / "sync_for_period_config.py"
        )
        spec = importlib.util.spec_from_file_location("sync_fp", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_summary_fields_carry_over_from_the_wrapped_action(self):
        m = self._module()
        tool = m.tool_for(
            "get_received_invoices_for_period", "get_received_invoices",
            "from", "to", "Invoices", "",
            wrapped={
                "required_fields": ["from", "to"],
                "summary_fields": ["invoiceNumber", "supplierName", "total"],
            },
        )
        assert tool["summary_fields"] == ["invoiceNumber", "supplierName", "total"]

    def test_max_result_chars_carries_over_too(self):
        m = self._module()
        tool = m.tool_for(
            "x_for_period", "x", "a", "b", "R", "",
            wrapped={"required_fields": [], "max_result_chars": 100_000},
        )
        assert tool["max_result_chars"] == 100_000

    def test_absent_slimming_config_is_not_invented(self):
        m = self._module()
        tool = m.tool_for("x_for_period", "x", "a", "b", "R", "", wrapped={})
        assert "summary_fields" not in tool
        assert "max_result_chars" not in tool


class TestAggregation:
    """The answer before the rows.

    The model was handed 120 product rows to answer "what were sales
    yesterday". Reading them costs tokens, and arithmetic across them is an
    error source — a total computed in the consolidator is exact, one the model
    adds up by eye is a guess that looks like a fact.
    """

    def _rows(self):
        return [
            {"product": "Peroni", "quantity": 32, "revenue": 392.56, "cost": 103.68},
            {"product": "Stella", "quantity": 24, "revenue": 247.06, "cost": 53.21},
        ]

    def test_summary_carries_the_row_count_and_column_sums(self):
        result = run_consolidator(Api(payload=self._rows()), period="yesterday")
        summary = result["summary"]
        assert summary["row_count"] == 2
        assert summary["column_sums"]["revenue"] == 639.62
        assert summary["column_sums"]["quantity"] == 56

    def test_the_rows_are_still_returned(self):
        """The summary is additive. The UI component and any follow-up question
        still need the detail."""
        result = run_consolidator(Api(payload=self._rows()), period="yesterday")
        assert len(result["data"]) == 2
        assert result["window"]["trading_aligned"] is True

    def test_sums_are_labelled_as_column_sums_not_totals(self):
        """Summing a rate or a unit price is meaningless. Naming them honestly
        is what stops the model reporting sum(hourly_rate) as a cost."""
        result = run_consolidator(Api(payload=self._rows()), period="yesterday")
        assert "column_sums" in result["summary"]
        assert "not meaningful" in result["summary"]["_note"]

    def test_booleans_are_not_summed(self):
        """bool is an int in Python; adding up flags would be nonsense."""
        rows = [{"reconciled": True, "total": 10}, {"reconciled": False, "total": 5}]
        result = run_consolidator(Api(payload=rows), period="yesterday")
        assert "reconciled" not in result["summary"]["column_sums"]
        assert result["summary"]["column_sums"]["total"] == 15

    def test_nested_row_arrays_are_found(self):
        """Several actions wrap their rows in an envelope."""
        payload = {"meta": "x", "results": [{"amount": 3}, {"amount": 4}]}
        result = run_consolidator(Api(payload=payload), period="yesterday")
        assert result["summary"]["row_count"] == 2
        assert result["summary"]["column_sums"]["amount"] == 7

    def test_a_non_tabular_payload_gets_no_summary(self):
        """Inventing a summary for a scalar result would be noise."""
        result = run_consolidator(Api(payload={"total": 15945}), period="yesterday")
        assert "summary" not in result

    def test_an_empty_result_gets_no_summary(self):
        result = run_consolidator(Api(payload=[]), period="yesterday")
        assert "summary" not in result
