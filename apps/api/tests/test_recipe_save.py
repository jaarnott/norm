"""Tests for the recipe writer's request construction (update vs create).

The writer routes through the Cook Brothers App MCP tool
``kitchen_loadedhub_update_recipe``. These tests intercept ``execute_spec`` at
the connector boundary and assert exactly what ``save_recipe`` hands it — the
live CB round-trip is exercised separately (it has a real side effect).

Create mode is the important case: the CB tool creates a recipe when
``recipe_id`` is omitted (or ``create: true``), so the writer must (a) not send
recipe_id/version_id, (b) flag create, and (c) not be blocked by the discovered
schema's required_fields.
"""

import pytest

from app.services import recipe_save
from app.services.recipe_save import RecipeSaveError, save_recipe


class _FakeResult:
    def __init__(self, success=True, payload=None, error=None):
        self.success = success
        self.response_payload = payload or {}
        self.error_message = error


@pytest.fixture()
def capture(monkeypatch):
    """Stub the CB context + resolver + executor; capture the execute_spec call."""
    fake_spec = type(
        "Spec",
        (),
        {
            "tools": [
                {
                    "action": recipe_save.SAVE_ACTION,
                    "required_fields": ["recipe_id", "version_id"],
                }
            ]
        },
    )()
    fake_cfg = type("Cfg", (), {"config": {}})()
    seen = {}

    monkeypatch.setattr(
        recipe_save, "_cb_context", lambda *a, **k: (fake_spec, fake_cfg)
    )
    monkeypatch.setattr(recipe_save, "resolve_cb_venue_id", lambda *a, **k: "cbven-1")

    def fake_execute_spec(spec, op, fields, creds, db, venue_id=None, **k):
        seen["op"] = op
        seen["fields"] = fields
        return _FakeResult(
            payload={"data": {"recipe_id": "NEW-R", "version_id": "NEW-V"}}
        ), None

    # save_recipe imports execute_spec from this module at call time.
    monkeypatch.setattr("app.connectors.spec_executor.execute_spec", fake_execute_spec)
    return seen


def test_update_sends_ids_and_no_create_flag(capture):
    recipe = {
        "recipe_id": "R1",
        "version_id": "V1",
        "name": "Aioli",
        "lines": [
            {
                "kind": "item",
                "ref_id": "i1",
                "name": "Egg",
                "unit_id": "u1",
                "quantity": 2,
            }
        ],
    }
    out = save_recipe("venue-1", recipe, db=None, config_db=None)
    f = capture["fields"]
    assert f["recipe_id"] == "R1"
    assert f["version_id"] == "V1"
    assert "create" not in f
    assert f["venue_id"] == "cbven-1"
    assert out["created"] is False


def test_create_omits_ids_and_flags_create(capture):
    recipe = {
        "name": "New Sauce",
        "notes": "method here",
        "yield_quantity": 2,
        "yield_unit_id": "u9",
        "lines": [
            {
                "kind": "item",
                "ref_id": "i1",
                "name": "Egg",
                "unit_id": "u1",
                "quantity": 2,
            }
        ],
    }
    out = save_recipe("venue-1", recipe, db=None, config_db=None)
    f = capture["fields"]
    assert f["create"] is True
    assert "recipe_id" not in f
    assert "version_id" not in f
    assert f["name"] == "New Sauce"
    assert f["yield_unit_id"] == "u9"
    # The required-field gate must be lifted, or a create (no ids) is blocked.
    assert capture["op"]["required_fields"] == []
    # The new ids come back to the caller.
    assert out["created"] is True
    assert out["recipe_id"] == "NEW-R"
    assert out["version_id"] == "NEW-V"


def test_explicit_create_flag_forces_create_even_with_recipe_id(capture):
    recipe = {"recipe_id": "R1", "create": True, "name": "Forced"}
    save_recipe("venue-1", recipe, db=None, config_db=None)
    f = capture["fields"]
    assert f["create"] is True
    assert "recipe_id" not in f


def test_create_requires_a_name(capture):
    with pytest.raises(RecipeSaveError, match="name"):
        save_recipe("venue-1", {"lines": []}, db=None, config_db=None)


def test_update_requires_version_id(capture):
    with pytest.raises(RecipeSaveError, match="version_id"):
        save_recipe("venue-1", {"recipe_id": "R1"}, db=None, config_db=None)


def test_rejects_too_many_lines(capture):
    recipe = {"name": "X", "lines": [{"kind": "item"}] * 501}
    with pytest.raises(RecipeSaveError, match="500"):
        save_recipe("venue-1", recipe, db=None, config_db=None)
