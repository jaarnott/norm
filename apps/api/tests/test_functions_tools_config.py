"""The Functions tools' config: the norm_functions rows the sync script
installs, the App that claims them, their effect labels, and the ported
skills.

CI's config DB is empty, so the shipped rows are pinned here instead:
every parameter has a field_schema (without one the model sends strings and a
list sent as a string is silently dropped — memory: tool-row-field-schema);
rows, handlers and the App's claims name the same 14 tools; every write that
asks can preview itself; the validator's offline checks pass; the skills only
name Norm tools; and the script's own steps behave (skills stay off until
the App claims them; Orbit's endpoints are marked superseded, not deleted).
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

from app.agents import functions_tools as FT
from app.agents import internal_tools as IT
from app.agents.prompt_builder import build_input_schema
from app.db.config_models import ConnectionSpec, MarketplaceApp, Playbook
from app.services.config_validator import (
    check_approval_labels,
    check_connector_tools,
    check_endpoints_and_tools,
)
from app.services.functions import catalog

API = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))
from scripts.sync_approval_labels import labels  # noqa: E402


def _script(name):
    spec = importlib.util.spec_from_file_location(name, API / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SYNC = _script("sync_norm_functions")
ROWS = SYNC.tool_rows()


class TestRows:
    def test_rows_handlers_and_claims_name_the_same_tools(self):
        actions = [r["action"] for r in ROWS]
        assert len(actions) == len(set(actions)) == 14
        assert set(actions) == set(FT.ACTIONS)
        assert set(f"norm_functions.{a}" for a in actions) == set(catalog.TOOLS)
        assert catalog.APP["tools"] == list(catalog.TOOLS)
        registered = {a for c, a in IT.registered_actions() if c == "norm_functions"}
        assert registered == set(actions)

    @pytest.mark.parametrize("row", ROWS, ids=lambda r: r["action"])
    def test_every_parameter_has_a_schema_with_a_description(self, row):
        fields = list(row["required_fields"]) + list(row["optional_fields"])
        assert len(fields) == len(set(fields))
        assert set(row["field_schema"]) == set(fields)
        for name, schema in row["field_schema"].items():
            assert schema.get("type") in (
                "string",
                "integer",
                "number",
                "boolean",
                "array",
                "object",
            ), name
            assert schema.get("description"), name
            if schema["type"] == "array":
                assert "items" in schema, name
        built = build_input_schema(row)
        assert set(built["properties"]) == set(fields)
        assert built["required"] == row["required_fields"]

    def test_list_and_object_parameters_are_typed_for_the_model(self):
        by = {r["action"]: r["field_schema"] for r in ROWS}
        assert by["find_enquiries"]["stage"]["type"] == "array"
        assert by["build_proposal"]["options"]["items"]["type"] == "object"
        assert (
            by["build_proposal"]["options"]["items"]["properties"]["amount"]["type"]
            == "number"
        )
        assert by["draft_email"]["to"]["type"] == "array"
        assert by["find_threads"]["count"]["type"] == "boolean"

    def test_effects_follow_the_label_file(self):
        known = labels()
        by = {r["action"]: r for r in ROWS}
        reads = {
            "find_enquiries",
            "get_enquiry",
            "find_events",
            "get_event",
            "find_threads",
            "read_thread",
            "get_venue_profile",
        }
        for action, row in by.items():
            key = f"norm_functions.{action}"
            assert row["effect"] == known[key]["effect"]
            assert row["effect"] == ("read" if action in reads else row["effect"])
            if row["effect"] == "read":
                assert row["method"] == "GET"
        assert by["draft_email"]["effect"] == by["build_proposal"]["effect"] == "draft"
        for action in ("record_enquiry", "record_event", "file_thread"):
            assert (
                by[action]["effect"] == "write"
                and by[action]["approval"]["allow_auto"] is True
            )
        # Customer sends: always asked, and nobody can switch that off.
        for action in ("send_email", "send_proposal"):
            assert by[action]["approval"] == {
                "default": "ask",
                "allow_auto": False,
                "label": by[action]["approval"]["label"],
            }

    def test_the_validators_offline_checks_pass(self):
        claims = {key: "functions" for key in catalog.TOOLS}
        assert check_approval_labels([("norm_functions", ROWS)], claims) == []
        assert (
            check_endpoints_and_tools(
                [("norm_functions", "internal", ROWS, [])], claims, []
            )
            == []
        )
        assert (
            check_connector_tools(
                "norm_functions", "internal", ROWS, IT.registered_actions()
            )
            == []
        )

    def test_every_write_that_asks_previews_itself(self):
        for row in ROWS:
            if row["effect"] == "write":
                assert IT.get_previewer("norm_functions", row["action"]) is not None, (
                    row["action"]
                )

    def test_mcp_never_sees_a_draft_or_write_as_a_read(self):
        from app.mcp.projection import is_read_tool

        for row in ROWS:
            assert is_read_tool(row) is (row["effect"] == "read"), row["action"]


class TestTheApp:
    def test_the_app_claims_the_skills_and_the_seed_writes_them(self):
        seed = {a["slug"]: a for a in _script("sync_marketplace_catalog").APPS}
        comp = seed["functions"]["composition"]
        assert comp["skills"] == list(catalog.SKILLS) == list(SYNC.PLAYBOOKS)
        assert comp["tools"] == list(catalog.TOOLS)


#: Orbit's tool names: a ported skill may mention them only in its "What
#: changed" section.
ORBIT_NAMES = (
    "find_inbox_threads",
    "triage_thread",
    "venue_playbook",
    "compose_email",
    "functions_send_email",
    "functions_build_proposal",
    "manage_quote_brand",
    "mailbox_handle_thread",
    "min_spend_lines",
    "food_preorder",
    "snapshot",
)


class TestSkills:
    @pytest.mark.parametrize("slug", sorted(SYNC.PLAYBOOKS))
    def test_a_ported_skill_names_only_norm_tools(self, slug):
        text = SYNC.PLAYBOOKS[slug]["instructions"]
        assert "Ported from the owner's claude.ai skill" in text
        assert "never on a schedule" in text
        body, _, changed = text.partition("## What changed from the Orbit version")
        assert changed.strip(), "each port says what changed vs Orbit"
        for name in ORBIT_NAMES:
            assert name not in body, (slug, name)
        mentioned = set(re.findall(r"`([a-z]+_[a-z_]+)`", body))
        tools = {
            m
            for m in mentioned
            if re.match(r"(find|get|record|read|file|draft|send|build)_", m)
            and not m.endswith("_id")
        }
        assert tools <= set(FT.ACTIONS), (slug, tools - set(FT.ACTIONS))
        skills = {m for m in mentioned if m.startswith("functions_")}
        assert skills <= set(catalog.SKILLS), (slug, skills)

    def test_the_owners_formats_survive_the_port(self):
        sweep = SYNC.PLAYBOOKS["functions_inbox_sweep"]["instructions"]
        for line in (
            "Label Actioned - All Other, remove from inbox, no reply",
            "Label Actioned - Reservation, remove from inbox, action the booking",
            "Label Actioned - Enquiry, remove from inbox, record the enquiry and reply",
            "Label Actioned - Enquiry, remove from inbox, progress the thread",
        ):
            assert line in sweep
        runsheet = SYNC.PLAYBOOKS["functions_runsheet"]["instructions"]
        assert "permanent format, approved 21 Aug 2026" in runsheet
        for section in (
            "This week's first hour",
            "Decisions needed from James",
            "Leads by segment",
            "Patterns worth acting on",
            "Hygiene",
        ):
            assert section in runsheet
        tone = SYNC.PLAYBOOKS["functions_email_tone"]["instructions"]
        assert "Never an em dash" in tone
        assert (
            "{{quote_link}}"
            in SYNC.PLAYBOOKS["functions_proposal_build"]["instructions"]
        )


# --------------------------------------------------- the script's steps ----


class TestTheScript:
    def test_skills_stay_off_until_the_app_claims_them(self, db_session):
        db_session.query(Playbook).filter(Playbook.slug.in_(catalog.SKILLS)).delete()
        db_session.query(MarketplaceApp).filter_by(slug="functions").delete()
        db_session.flush()
        lines = SYNC._skills(db_session, dry_run=False)
        assert len(lines) == 5 and all("off until" in x for x in lines)
        rows = (
            db_session.query(Playbook).filter(Playbook.slug.in_(catalog.SKILLS)).all()
        )
        assert {p.enabled for p in rows} == {False}
        assert {p.agent_slug for p in rows} == {"functions"}

        for row in catalog.stand_in_rows({"functions"}):
            db_session.add(row)
        db_session.flush()
        assert all("; on)" in x for x in SYNC._skills(db_session, dry_run=False))
        assert {p.enabled for p in rows} == {True}
        assert SYNC._skills(db_session, dry_run=False) == []

    def test_the_spec_is_created_once(self, db_session):
        db_session.query(ConnectionSpec).filter_by(
            connector_name="norm_functions"
        ).delete()
        db_session.flush()
        assert SYNC._spec(db_session, dry_run=True) == [
            "create spec 'norm_functions' with 14 tool rows"
        ]
        assert (
            db_session.query(ConnectionSpec)
            .filter_by(connector_name="norm_functions")
            .first()
            is None
        )
        SYNC._spec(db_session, dry_run=False)
        db_session.flush()
        spec = (
            db_session.query(ConnectionSpec)
            .filter_by(connector_name="norm_functions")
            .one()
        )
        assert spec.execution_mode == "internal" and spec.endpoints == []
        assert SYNC._spec(db_session, dry_run=False) == []

    def test_orbit_endpoints_are_marked_superseded_not_deleted(self, db_session):
        db_session.query(ConnectionSpec).filter_by(
            connector_name="cook_brothers_app"
        ).delete()
        db_session.add(
            ConnectionSpec(
                connector_name="cook_brothers_app",
                display_name="CB App",
                execution_mode="mcp",
                auth_type="oauth2",
                tools=[],
                endpoints=[
                    {"action": "functions_get_enquiry", "method": "POST"},
                    {"action": "functions_manage_quote_brand", "method": "POST"},
                    {"action": "training_find_jobs", "method": "POST"},
                ],
            )
        )
        db_session.flush()
        (line,) = SYNC._superseded(db_session, dry_run=False)
        assert "functions_get_enquiry" in line and "training" not in line
        spec = (
            db_session.query(ConnectionSpec)
            .filter_by(connector_name="cook_brothers_app")
            .one()
        )
        by = {e["action"]: e for e in spec.endpoints}
        assert len(by) == 3
        assert (
            by["functions_get_enquiry"]["superseded_by"]
            == "norm_functions__get_enquiry"
        )
        assert "superseded_by" not in by["functions_manage_quote_brand"]
        assert by["functions_manage_quote_brand"]["superseded_reason"]
        assert "superseded_reason" not in by["training_find_jobs"]
        assert SYNC._superseded(db_session, dry_run=False) == []
        for target in filter(None, SYNC.SUPERSEDED.values()):
            assert target in FT.ACTIONS


class TestMcpCapabilities:
    def test_every_row_passes_the_validators_mcp_check(self):
        from app.mcp.projection import MCP_DENYLIST
        from app.mcp.scopes import MCP_SCOPES
        from app.services.config_validator import check_mcp_capability

        by_action = {r["action"]: r for r in ROWS}
        rows = SYNC.mcp_rows(set(catalog.SKILLS))
        assert {r["kind"] for r in rows} == {"connector", "playbook"}
        for row in rows:
            tool_def = (
                by_action.get(row["action"]) if row["kind"] == "connector" else None
            )
            issues = check_mcp_capability(
                row["kind"],
                row["target"],
                row["action"],
                row["scopes"],
                tool_def,
                True if row["kind"] == "playbook" else None,
                set(MCP_SCOPES),
                set(MCP_DENYLIST),
            )
            assert issues == [], (row, issues)
        # Only reads go out directly; a workflow row only for a claimed skill.
        assert all(
            by_action[r["action"]]["effect"] == "read"
            for r in rows
            if r["kind"] == "connector"
        )
        assert not [r for r in SYNC.mcp_rows(set()) if r["kind"] == "playbook"]
