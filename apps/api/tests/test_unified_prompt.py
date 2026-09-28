"""One Norm prompt (Apps v3 prompt consolidation, 28 Sep 2026).

Every conversation gets ONE Norm prompt (the base row) + its member's light
personality + short notes for the Apps that are on. Writing the base prompt is
the switch; clearing it rolls back to per-agent prompts."""

import importlib.util
import pathlib
import uuid

from app.db.config_models import AgentConfig, MarketplaceApp, Playbook
from app.db.models import AgentConnectionBinding, ConnectionSpec
from app.services.agent_config_service import (
    build_unified_prompt,
    unified_prompt_active,
)

REPO = pathlib.Path(__file__).resolve().parents[3]


def _agent(db, slug, prompt=None, persona=None, description=None):
    row = db.query(AgentConfig).filter_by(agent_slug=slug).first()
    if row is None:
        row = AgentConfig(agent_slug=slug, display_name=slug)
        db.add(row)
    row.system_prompt, row.persona, row.description = prompt, persona, description
    db.flush()
    return row


def _app(db, slug, member, note=None, tools=None):
    comp = {"member": member, "tools": tools or []}
    if note:
        comp["note"] = note
    db.add(
        MarketplaceApp(
            slug=slug,
            name=slug.title(),
            description="",
            tier="app",
            bundled=True,
            composition=comp,
        )
    )
    db.flush()


class TestTheSwitch:
    def test_off_until_the_norm_prompt_exists(self, db_session):
        assert unified_prompt_active(db_session) is False
        _agent(db_session, "base", prompt="You are Norm.")
        assert unified_prompt_active(db_session) is True

    def test_clearing_it_rolls_back(self, db_session):
        _agent(db_session, "base", prompt="  ")
        assert unified_prompt_active(db_session) is False


class TestAssembly:
    def test_norm_prompt_plus_persona_plus_notes(self, db_session):
        _agent(db_session, "base", prompt="You are Norm.")
        _agent(
            db_session,
            "procurement",
            prompt="OLD PROCUREMENT PROMPT",
            persona="In here you're the buyer.",
        )
        _app(db_session, "stock", "procurement", note="Never guess item ids.")
        out = build_unified_prompt("procurement", db_session)
        assert out.startswith("You are Norm.")
        assert "In here you're the buyer." in out
        assert "**Stock**: Never guess item ids." in out
        assert "OLD PROCUREMENT PROMPT" not in out

    def test_notes_only_for_apps_that_are_on(self, db_session):
        _agent(db_session, "base", prompt="You are Norm.")
        _app(db_session, "stock", "procurement", note="Stock note.")
        _app(db_session, "kitchen", "executive_chef", note="Kitchen note.")
        out = build_unified_prompt("procurement", db_session, apps_on={"stock"})
        assert "Stock note." in out and "Kitchen note." not in out

    def test_other_members_notes_still_apply_when_on(self, db_session):
        # One agent holds every tool its org's Apps allow, so a Loaded Kitchen
        # note applies in a Procurement conversation too — that's the fix for
        # "recipe tools, no recipe guidance".
        _agent(db_session, "base", prompt="You are Norm.")
        _app(db_session, "kitchen", "executive_chef", note="Kitchen note.")
        assert "Kitchen note." in build_unified_prompt("procurement", db_session)

    def test_long_notes_are_capped(self, db_session):
        _agent(db_session, "base", prompt="You are Norm.")
        _app(db_session, "wordy", "procurement", note="x" * 900)
        out = build_unified_prompt("procurement", db_session)
        assert "x" * 301 not in out


class TestBuildToolDefinitionsUsesIt:
    def _tools(self, db):
        name = f"t-{uuid.uuid4().hex[:6]}"
        db.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name=name,
                display_name=name,
                category="internal",
                execution_mode="internal",
                auth_type="none",
                tools=[
                    {
                        "action": "noop",
                        "method": "GET",
                        "description": "x",
                        "required_fields": [],
                        "field_descriptions": {},
                    }
                ],
                enabled=True,
            )
        )
        db.add(
            AgentConnectionBinding(
                id=str(uuid.uuid4()),
                agent_slug="procurement",
                connector_name=name,
                capabilities=[{"action": "noop", "enabled": True}],
                enabled=True,
            )
        )
        db.flush()

    def test_unified_when_the_norm_prompt_exists(self, db_session):
        from app.agents.prompt_builder import build_tool_definitions

        self._tools(db_session)
        _agent(
            db_session,
            "procurement",
            prompt="OLD PROCUREMENT PROMPT",
            persona="In here you're the buyer.",
        )
        prompt, _ = build_tool_definitions(
            "procurement", db_session, config_db=db_session
        )
        assert "OLD PROCUREMENT PROMPT" in prompt  # no Norm prompt yet: legacy
        _agent(db_session, "base", prompt="You are Norm, today is {{today}}.")
        prompt, _ = build_tool_definitions(
            "procurement", db_session, config_db=db_session
        )
        assert "You are Norm" in prompt and "In here you're the buyer." in prompt
        assert "OLD PROCUREMENT PROMPT" not in prompt
        assert "{{today}}" not in prompt  # date still substituted

    def test_mode_can_be_forced_for_the_replay_eval(self, db_session):
        from app.agents.prompt_builder import build_tool_definitions

        self._tools(db_session)
        _agent(db_session, "procurement", prompt="OLD PROCUREMENT PROMPT")
        _agent(db_session, "base", prompt="You are Norm.")
        legacy, _ = build_tool_definitions(
            "procurement", db_session, config_db=db_session, prompt_mode="per_agent"
        )
        assert "OLD PROCUREMENT PROMPT" in legacy


class TestNoteCaps:
    def test_validator_flags_long_and_total(self):
        from types import SimpleNamespace

        from app.services.config_validator import check_app_notes

        rows = [
            SimpleNamespace(slug=f"a{i}", composition={"note": "y" * 290})
            for i in range(6)
        ]
        rows.append(SimpleNamespace(slug="big", composition={"note": "z" * 400}))
        issues = check_app_notes(rows)
        assert any("big" in i.where for i in issues)
        assert any(i.where == "catalog.notes" for i in issues)


class TestRolloutScript:
    def _mod(self):
        spec = importlib.util.spec_from_file_location(
            "sup", REPO / "apps/api/scripts/sync_unified_prompt.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_plan_is_idempotent_and_builds_the_skill(self, db_session):
        mod = self._mod()
        _agent(db_session, "procurement", prompt="p")
        _agent(
            db_session,
            "app_builder",
            prompt="You are Norm's App Builder.\nToday's date is {{today}}.\n\n## How to work\n1. Build.",
        )
        _agent(
            db_session,
            "router",
            prompt="Available domains:\n{domains}\n\nDomain capabilities:\n- procurement: x\n\nTitle guidelines:\n- short",
        )
        first = mod.plan(db_session, dry_run=False)
        db_session.flush()
        assert any("Norm prompt" in c for c in first)
        assert unified_prompt_active(db_session)
        skill = db_session.query(Playbook).filter_by(slug="build_an_app").one()
        assert skill.instructions.startswith("## How to work")
        router = db_session.query(AgentConfig).filter_by(agent_slug="router").one()
        assert (
            "Domain capabilities" not in router.system_prompt
            and "{domains}" in router.system_prompt
        )
        assert mod.plan(db_session, dry_run=False) == []  # second run: nothing to do

    def test_obsolete_rules_are_gone_from_the_norm_prompt(self):
        mod = self._mod()
        assert "[Tool]" not in mod.NORM_PROMPT
        assert "Use date formats exactly" not in mod.NORM_PROMPT
