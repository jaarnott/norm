"""One Norm prompt (Apps v3 prompt consolidation, 28 Sep 2026).

Every conversation gets ONE Norm prompt (the base row) + its member's light
personality. A member's old per-agent system prompt is never read."""

import uuid

from app.db.config_models import AgentConfig, MarketplaceApp
from app.db.models import ConnectionSpec
from app.services.agent_config_service import build_unified_prompt


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


class TestAssembly:
    def test_norm_prompt_plus_persona_and_no_app_notes(self, db_session):
        """Apps' notes rode in a capped "## Your Apps" section that silently
        dropped the Functions rule for Cook Brothers (23 Oct 2026). Each rule
        now sits on its tool, so an App's note never reaches the prompt."""
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
        assert "OLD PROCUREMENT PROMPT" not in out
        assert "Never guess item ids." not in out and "Your Apps" not in out


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
        _app(db, f"app-{name}", "procurement", tools=[f"{name}.noop"])

    def test_the_norm_prompt_never_the_old_member_prompt(self, db_session):
        from app.agents.prompt_builder import build_tool_definitions

        self._tools(db_session)
        _agent(
            db_session,
            "procurement",
            prompt="OLD PROCUREMENT PROMPT",
            persona="In here you're the buyer.",
        )
        # No Norm prompt yet: still the persona, never the old prompt.
        prompt, _ = build_tool_definitions(
            "procurement", db_session, config_db=db_session
        )
        assert "In here you're the buyer." in prompt
        assert "OLD PROCUREMENT PROMPT" not in prompt
        _agent(db_session, "base", prompt="You are Norm, today is {{today}}.")
        prompt, _ = build_tool_definitions(
            "procurement", db_session, config_db=db_session
        )
        assert "You are Norm" in prompt and "In here you're the buyer." in prompt
        assert "OLD PROCUREMENT PROMPT" not in prompt
        assert "{{today}}" not in prompt  # date still substituted


class TestTheRulesLiveOnTheirTools:
    def test_each_moved_rule_is_in_its_tools_installer(self):
        """The rules that left the prompt are on their tools' descriptions in
        the installers too, so re-running one keeps them."""
        import importlib.util
        import pathlib

        scripts = pathlib.Path(__file__).resolve().parents[1] / "scripts"

        def load(name):
            spec = importlib.util.spec_from_file_location(name, scripts / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for v in value.values():
                    yield from strings(v)
            elif isinstance(value, (list, tuple)):
                for v in value:
                    yield from strings(v)

        for (connector, action), (rule, installer) in load(
            "sync_app_rules_into_tools"
        ).RULES.items():
            mod = load(installer)
            assert any(
                rule in s for v in vars(mod).values() for s in strings(v)
            ), f"{connector}.{action}: rule missing from {installer}.py"
