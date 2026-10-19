"""The recipe editor's writer: what ``save_recipe`` hands ``record_recipe``.

Since 19 Oct 2026 the editor (and the claude.ai recipe card) save through
Loaded Legacy's ``record_recipe`` tool — one write path with the agent's
(docs/plans/loaded-legacy-takeover.md). These tests intercept the tool call
and assert exactly what goes in; the write itself is tested in
test_record_recipe_consolidator.py, and end to end in test_loaded_legacy.py.
"""

from types import SimpleNamespace

import pytest

from app.services import recipe_save
from app.services.recipe_save import RecipeSaveError, save_recipe

UNITS = {
    "u-g": {"id": "u-g", "name": "Gram", "ratio": 0.001},
    "u-kg": {"id": "u-kg", "name": "Kilo", "ratio": 1.0},
}


@pytest.fixture()
def capture(monkeypatch):
    """The venue's units, and the tool call (answered with ``capture.answer``)."""
    seen = SimpleNamespace(
        calls=[], answer={"saved": True, "recipe_id": "r-new", "version_id": "v-new"}
    )
    monkeypatch.setattr(recipe_save, "_units", lambda venue_id, db, config_db: UNITS)

    def tool(connector, action, params, db, config_db, **kw):
        seen.calls.append((connector, action, params, kw))
        return SimpleNamespace(success=True, payload=seen.answer, error=None)

    monkeypatch.setattr("app.connectors.tool_executor.execute_connector_tool", tool)
    return seen


def _lines():
    return [
        {
            "kind": "recipe",
            "ref_id": "r-polenta-2",
            "name": "POLENTA",
            "unit_id": "u-kg",
            "quantity": 0.5,
        },
        {
            "kind": "item",
            "ref_id": "it-salt",
            "name": "SALT",
            "unit_id": "u-g",
            "quantity": 4,
        },
    ]


def test_an_update_replaces_the_lines_with_the_ids_the_chef_chose(capture):
    """Prod thread aad94383: a line pinned to one of three POLENTA sub-recipes
    must save as that one — the id goes, never just the name."""
    out = save_recipe(
        "venue-1",
        {
            "recipe_id": "r-1",
            "version_id": "v-1",
            "name": "DISH - X",
            "lines": _lines(),
        },
        None,
        None,
    )
    [(connector, action, params, kw)] = capture.calls
    assert (connector, action) == ("loaded_legacy", "record_recipe")
    assert kw == {"venue_id": "venue-1", "strict_venue": True}
    assert params["recipe_id"] == "r-1" and params["version_id"] == "v-1"
    assert params["mode"] == "replace"
    polenta, salt = params["ingredients"]
    assert polenta["ref_id"] == "r-polenta-2" and polenta["kind"] == "recipe"
    # the unit's ratio goes with it, so the tool matches nothing by name
    assert (salt["unit"], salt["unit_ratio"], salt["quantity"]) == ("u-g", 0.001, 4)
    assert out["saved"] and out["detail"] == {"via": "loaded_legacy"}


def test_a_create_omits_the_ids_and_carries_the_yield_unit(capture):
    out = save_recipe(
        "venue-1",
        {
            "name": "COMPONENT - New",
            "yield_quantity": 2,
            "yield_unit_id": "u-kg",
            "lines": _lines(),
        },
        None,
        None,
    )
    params = capture.calls[0][2]
    assert "recipe_id" not in params and "version_id" not in params
    assert (params["yield_unit"], params["yield_unit_ratio"]) == ("u-kg", 1.0)
    assert out["created"] is False or out["recipe_id"] == "r-new"
    assert (out["recipe_id"], out["version_id"]) == ("r-new", "v-new")


def test_an_explicit_create_flag_forces_a_create_even_with_a_recipe_id(capture):
    save_recipe(
        "v",
        {"create": True, "recipe_id": "r-1", "name": "Copy", "lines": _lines()},
        None,
        None,
    )
    assert "recipe_id" not in capture.calls[0][2]


@pytest.mark.parametrize(
    "recipe, why",
    [
        ({"lines": []}, "needs a name"),
        ({"recipe_id": "r-1", "lines": []}, "version_id"),
        ({"recipe_id": "r-1", "version_id": "v", "lines": [{}] * 501}, "at most 500"),
        (
            {
                "recipe_id": "r-1",
                "version_id": "v",
                "lines": [{"ref_id": "x", "unit_id": "u-cup", "quantity": 1}],
            },
            "isn't one of this venue's",
        ),
    ],
)
def test_bad_input_is_refused_before_the_tool(capture, recipe, why):
    with pytest.raises(RecipeSaveError, match=why):
        save_recipe("v", recipe, None, None)
    assert capture.calls == []


def test_the_tools_refusal_reaches_the_editor(capture):
    capture.answer = {"error": "nothing written — 'SALT': no match"}
    with pytest.raises(RecipeSaveError, match="no match"):
        save_recipe(
            "v",
            {"recipe_id": "r-1", "version_id": "v-1", "lines": _lines()},
            None,
            None,
        )
