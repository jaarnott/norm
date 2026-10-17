"""One Norm prompt (Apps v3 prompt consolidation, 28 Sep 2026).

Every conversation gets ONE Norm prompt (the base row) + its member's light
personality + short notes for the Apps that are on. A member's old per-agent
system prompt is never read."""

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
