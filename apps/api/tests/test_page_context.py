"""Page context in the system prompt — "Norm can see what you see".

The client publishes what the user has open (`lib/pageDocument.ts`) and it
rides on `page_context.document`. These tests pin the server half: the app
kinds resolve "this app" to a slug without asking, and ANY other kind renders
through the generic fallback — so a future page gets page-awareness by
publishing a document, with no server change.

Born from a live failure: on the Apps page with one app on screen, "change the
name of this app" was answered with "which app do you mean?".
"""

import uuid

from app.agents.prompt_builder import build_tool_definitions
from app.db.models import AgentConnectorBinding, ConnectorSpec


def _bind_one_tool(db, domain):
    """The minimum for build_tool_definitions to proceed: one internal spec
    with one action, bound to the domain."""
    name = f"t-{uuid.uuid4().hex[:8]}"
    db.add(
        ConnectorSpec(
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
                    "description": "does nothing",
                    "required_fields": [],
                    "field_descriptions": {},
                }
            ],
            enabled=True,
        )
    )
    db.add(
        AgentConnectorBinding(
            id=str(uuid.uuid4()),
            agent_slug=domain,
            connector_name=name,
            capabilities=[{"action": "noop", "label": "Noop", "enabled": True}],
            enabled=True,
        )
    )
    db.flush()


def _prompt(db, page_context):
    domain = f"d-{uuid.uuid4().hex[:6]}"
    _bind_one_tool(db, domain)
    system_prompt, tools = build_tool_definitions(
        domain, db, config_db=db, page_context=page_context
    )
    assert tools, "the harness must bind at least one tool"
    return system_prompt


class TestAppKinds:
    def test_an_open_app_is_named_by_slug(self, db_session):
        prompt = _prompt(
            db_session,
            {
                "page_id": "apps-hub",
                "agent": "app_builder",
                "document": {
                    "kind": "app",
                    "slug": "top-sellers",
                    "name": "Top Sellers",
                    "version": 3,
                },
            },
        )
        assert "Open App (act on THIS one)" in prompt
        assert "top-sellers" in prompt
        assert "Top Sellers" in prompt
        assert "do not ask which app" in prompt

    def test_the_apps_list_names_the_candidates(self, db_session):
        prompt = _prompt(
            db_session,
            {
                "page_id": "apps-hub",
                "agent": "app_builder",
                "document": {
                    "kind": "apps_list",
                    "apps": [
                        {"slug": "top-sellers", "name": "Top Sellers"},
                        {"slug": "weekly-perf", "name": "Weekly performance"},
                    ],
                },
            },
        )
        assert "Apps on screen" in prompt
        assert "`top-sellers`" in prompt and "`weekly-perf`" in prompt
        assert "name the candidates instead of guessing" in prompt

    def test_a_pinned_app_page_id_labels_itself(self, db_session):
        prompt = _prompt(
            db_session,
            {"page_id": "app:top-sellers", "agent": "app_builder"},
        )
        assert "the app 'top-sellers'" in prompt


class TestGenericFallback:
    def test_an_unknown_kind_renders_verbatim(self, db_session):
        # A page nobody has written a bespoke section for still tells the
        # agent what is open.
        prompt = _prompt(
            db_session,
            {
                "page_id": "stocktake",
                "agent": "procurement",
                "document": {
                    "kind": "stocktake",
                    "stocktake_id": "st-42",
                    "venue": "La Zeppa",
                },
            },
        )
        assert "What the user has open on this page" in prompt
        assert "st-42" in prompt

    def test_the_generic_block_is_capped(self, db_session):
        prompt = _prompt(
            db_session,
            {
                "page_id": "x",
                "agent": "procurement",
                "document": {"kind": "huge", "blob": "z" * 50_000},
            },
        )
        section = prompt.split("What the user has open on this page", 1)[1]
        assert len(section) < 2_000

    def test_no_document_no_section(self, db_session):
        prompt = _prompt(db_session, {"page_id": "orders", "agent": "procurement"})
        assert "Current Page Context" in prompt  # the page itself is still named
        assert "What the user has open" not in prompt
        assert "Open App" not in prompt
