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
