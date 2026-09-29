"""The validator's guard for the Sep 2026 rule: only TOOLS reach an LLM, and
API endpoints are building blocks that return raw data
(docs/tool-architecture-strategy.md; check_endpoints_and_tools)."""

from app.services.config_validator import check_endpoints_and_tools

CONS = {
    "action": "get_things",
    "consolidator_config": {
        "function_code": "def run(p, call_api, l):\n    return call_api('acme', 'get_raw', {})\n"
    },
}
RAW = {"action": "get_raw", "method": "GET", "path_template": "/r"}


def _problems(specs, claims=None, mcp=None):
    return {
        (i.where, i.problem.split(" — ")[0])
        for i in check_endpoints_and_tools(specs, claims or {}, mcp or [])
    }


def test_a_clean_split_connector_passes():
    assert _problems([("acme", "template", [CONS], [RAW])]) == set()


def test_an_unsplit_connector_is_not_judged_on_lists():
    assert _problems([("acme", "template", [CONS, RAW], None)]) == set()


def test_misfiled_rows_are_errors():
    probs = _problems([("acme", "template", [RAW], [CONS])])
    assert ("acme.get_raw", "is an endpoint filed under tools") in probs
    assert (
        "acme.get_things",
        "is a tool (consolidator or built-in) filed under endpoints",
    ) in probs


def test_an_action_in_both_lists():
    probs = _problems([("acme", "template", [CONS, RAW], [RAW])])
    assert ("acme.get_raw", "is in both the tools and the endpoints list") in probs


def test_an_endpoint_may_not_carry_a_transform():
    shaped = {**RAW, "response_transform": {"enabled": True, "fields": {"a": "a"}}}
    probs = _problems([("acme", "template", [CONS], [shaped])])
    assert ("acme.get_raw", "is an endpoint carrying a response_transform") in probs


def test_an_app_may_not_claim_an_endpoint():
    probs = _problems(
        [("acme", "template", [CONS], [RAW])], claims={"acme.get_raw": "acme-app"}
    )
    assert any(w == "acme.get_raw" and "claims it" in p for w, p in probs)


def test_mcp_may_not_offer_an_endpoint():
    probs = _problems([("acme", "template", [CONS], [RAW])], mcp=[("acme", "get_raw")])
    assert any(w == "acme.get_raw" and "MCP" in p for w, p in probs)


def test_a_consolidator_calling_a_missing_endpoint():
    probs = _problems([("acme", "template", [CONS], [])])
    assert ("acme.get_things", "calls acme.get_raw, which does not exist") in probs


def test_the_stale_aggregate_guard_covers_consolidator_shapes():
    """The 332.25-vs-146.5 roster-hours incident guard (a filter that drops
    rows while a total passes through) must follow the transforms into the
    consolidators' shapes."""
    from app.services.config_validator import _check_stale_aggregates

    stale = {
        "enabled": True,
        "fields": {"totalHours": "totalHours", "rosteredShifts[].hours": "hours"},
        "filters": [
            {
                "field": "rosteredShifts[].isFromOtherVenue",
                "operator": "equals",
                "value": "false",
            }
        ],
    }
    tool = {
        "action": "get_labour",
        "consolidator_config": {
            "function_code": "x",
            "shapes": {"loadedhub.get_roster": stale},
        },
    }
    issues = _check_stale_aggregates("loadedhub.get_labour", tool)
    assert issues and "shape for loadedhub.get_roster" in issues[0].where
