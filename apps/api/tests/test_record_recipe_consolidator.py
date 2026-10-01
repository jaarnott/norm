"""record_recipe — the tool that wraps Orbit's recipe endpoint.

Sep 2026 rule (docs/tool-architecture-strategy.md): Orbit's MCP functions are
API endpoints, and an LLM only ever sees tools. ``kitchen_record_recipe`` was
the last endpoint an App claimed; this consolidator is what the Loaded Kitchen
App claims instead. Exec'd under the REAL sandbox namespace against the
canonical file.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "record_recipe.py"
).read_text()


def _run(params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    calls, logs = [], []

    def call_api(connector, action, body):
        calls.append((connector, action, body))
        return {"recipe_id": "r1", "saved": True}

    out = ns["run"](params, call_api, logs.append)
    return out, calls, logs


def test_passes_the_recipe_through_to_orbits_endpoint():
    params = {
        "name": "COMPONENT - Aioli",
        "yield_quantity": 2,
        "yield_unit": "kg",
        "notes": "<ol><li>Whisk</li></ol>",
        "ingredients": [
            {"kind": "item", "name": "Egg yolk", "quantity": 12, "unit": "each"}
        ],
        "venue": "La Zeppa",
    }
    out, calls, logs = _run(params)
    assert out == {"recipe_id": "r1", "saved": True}
    [(connector, action, body)] = calls
    assert (connector, action) == ("cook_brothers_app", "kitchen_record_recipe")
    # venue routing is call_api's job (from the tool call), not an API field
    assert "venue" not in body and "venue_id" not in body
    assert body["name"] == "COMPONENT - Aioli"
    assert body["ingredients"][0]["name"] == "Egg yolk"
    assert "create 'COMPONENT - Aioli'" in logs[0]


def test_an_edit_needs_only_the_recipe_id():
    out, calls, _ = _run({"recipe_id": "abc", "mode": "merge", "ingredients": []})
    assert calls and calls[0][2] == {
        "recipe_id": "abc",
        "mode": "merge",
        "ingredients": [],
    }


def test_a_create_without_a_name_is_refused_before_any_call():
    out, calls, _ = _run({"yield_unit": "kg"})
    assert "needs a name" in out["error"]
    assert calls == []


# ── consolidator review, 1 Oct 2026 ──────────────────────────────────────


def test_json_text_is_read_and_numbers_and_flags_coerced():
    """With no schema the model sent arrays as JSON text; Orbit's schema
    (additionalProperties false, typed lines) rejects that."""
    out, calls, _ = _run(
        {
            "name": "COMPONENT - Aioli",
            "yield_quantity": "2",
            "yield_unit": "kg",
            "is_counted_in_stocktake": "false",
            "ingredients": '[{"kind": "item", "name": "Egg yolk", "quantity": "12", "unit": "each"}]',
            "allergens": '[{"code": "egg", "status": "contains"}]',
        }
    )
    [(_, _, body)] = calls
    assert body["ingredients"] == [
        {"kind": "item", "name": "Egg yolk", "quantity": 12.0, "unit": "each"}
    ]
    assert body["allergens"] == [{"code": "egg", "status": "contains"}]
    assert body["yield_quantity"] == 2.0
    assert body["is_counted_in_stocktake"] is False


def test_an_ingredient_edit_must_say_merge_or_replace():
    """Orbit defaults to replace: sending one changed line deleted the rest."""
    line = {"kind": "item", "name": "Salt", "quantity": 5, "unit": "g"}
    out, calls, _ = _run({"recipe_id": "abc", "ingredients": [line]})
    assert "needs mode" in out["error"] and out["error"].startswith("nothing written")
    assert calls == []
    out, calls, _ = _run({"recipe_id": "abc", "ingredients": [], "mode": "Merge"})
    assert calls[0][2]["mode"] == "merge"
    # a create has no lines to lose
    out, calls, _ = _run({"name": "New", "yield_unit": "kg", "ingredients": [line]})
    assert calls and "mode" not in calls[0][2]


def test_bad_lines_are_refused_before_orbit_is_called():
    out, calls, _ = _run(
        {
            "name": "Broken",
            "ingredients": [{"name": "Salt", "quantity": "a pinch"}],
            "allergens": [{"code": "egg", "status": "maybe"}],
        }
    )
    assert calls == []
    for why in (
        "ingredients[0] needs kind",
        "ingredients[0] needs a unit",
        "ingredients[0] needs a numeric quantity",
        "allergens[0] status must be one of",
    ):
        assert why in out["error"]
