"""Create the Executive Chef agent config + connector bindings.

Executive Chef manages recipes and menus in LoadedHub. This upserts its
``agent_configs`` row (so it appears in Settings and has a system prompt) and its
``agent_connector_bindings`` for ``loadedhub`` (recipe reads + menu CRUD + a few
stock-reference reads).

The recipe **write** binding is deliberately left out: recipe writes go through
the ``cook_brothers_app`` MCP connector (Loaded's own recipe-write API can't be
authed from Norm), and that tool doesn't exist on the CB App yet. Add a
``cook_brothers_app`` binding here once ``kitchen_save_recipe`` is discovered via
``POST /api/connector-specs/cook_brothers_app/sync-mcp-tools``.

The menu actions (list_menus/get_menu/create_menu/update_menu/delete_menu) are
added to the loadedhub ConnectorSpec by ``sync_menu_actions.py`` — run that first
(or alongside); binding an action the spec doesn't define yet is harmless (it is
simply skipped until the definition exists).

The config DB is shared across every environment, so committing this reaches
production immediately. Dry-run first. The agent only becomes routable once the
ExecutiveChefAgent class ships in code (app/agents/registry.py).

Usage:
    .venv/bin/python scripts/sync_executive_chef_agent.py --dry-run
    .venv/bin/python scripts/sync_executive_chef_agent.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

AGENT_SLUG = "executive_chef"
DISPLAY_NAME = "Executive Chef"
DESCRIPTION = (
    "Manages recipes and menus in LoadedHub, and extracts recipes from documents"
)

SYSTEM_PROMPT = """You are the executive chef agent for Norm, a hospitality operations platform.
Today's date is {{today}}.

## Rules
- Only present data returned by tool calls. Never fabricate or estimate data.
- Always call resolve_dates before making API calls that need dates.
- For read-only tools (GET), proceed immediately. For write tools (POST/PUT/DELETE), describe what you plan to do — the user will approve before it executes.
- Match entity names fuzzily: "zeppa" = "La Zeppa", "dsc" = "Dunedin Social Club", "glass goose" = "The Glass Goose".
- Prefer action over clarification. Make reasonable assumptions for read operations.
- Use date formats exactly as shown in each tool's field description.

## Executive Chef Capabilities
You help hospitality venues manage their recipes and menus in LoadedHub.

**Recipes** — read every recipe with its ingredients, quantities, units and
versions. Edits open an interactive recipe editor card the user works in
directly; saving pushes the change back to Loaded. You can also extract a draft
recipe from an uploaded document (PDF, image, or Word) for the user to review.

**Menus** — read every menu with its sections and dishes. Create and update
menus (sections, dishes, sell prices) back to Loaded. Each menu line references
either a recipe or a stock item.

When a tool returns a draft or an interactive editor card, hand it to the user
and tell them what is waiting for them — never submit on their behalf; the
user's click in the card is the approval.
"""

# loadedhub actions to bind. Recipe reads exist today; menu actions are added by
# sync_menu_actions.py; stock-reference reads help resolve ingredients/units.
LOADEDHUB_ACTIONS = [
    "get_all_recipes",
    "get_recipe_details",
    "edit_recipe",  # internal tool — edits the open recipe draft (see sync_recipe_edit_tool.py)
    "list_menus",
    "get_menu",
    "create_menu",
    "update_menu",
    "delete_menu",
    "get_stock_items",
    "get_stock_item_groups",
    "get_stock_units",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import AgentConfig, AgentConnectorBinding, ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        # --- 1. agent_configs -------------------------------------------------
        cfg = db.query(AgentConfig).filter(AgentConfig.agent_slug == AGENT_SLUG).first()
        if cfg:
            cfg.display_name = DISPLAY_NAME
            cfg.description = DESCRIPTION
            cfg.system_prompt = SYSTEM_PROMPT
            cfg.enabled = True
            print(f"updating agent_configs '{AGENT_SLUG}'")
        else:
            db.add(
                AgentConfig(
                    agent_slug=AGENT_SLUG,
                    display_name=DISPLAY_NAME,
                    description=DESCRIPTION,
                    system_prompt=SYSTEM_PROMPT,
                    enabled=True,
                )
            )
            print(f"creating agent_configs '{AGENT_SLUG}'")

        # --- 2. agent_connector_bindings: loadedhub --------------------------
        # Full capability dicts (action + label + enabled) — the shape every
        # other agent binding uses. The Settings UI reads each cap's `enabled`
        # to show its toggle as on; a capability WITHOUT that field renders as
        # OFF (so the binding looks "not set up"), even though the agent's
        # prompt builder defaults a missing `enabled` to True.
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        tool_desc = (
            {t.get("action"): t.get("description", "") for t in (spec.tools or [])}
            if spec
            else {}
        )
        caps = [
            {
                "action": a,
                "label": tool_desc.get(a) or a.replace("_", " ").title(),
                "enabled": True,
            }
            for a in LOADEDHUB_ACTIONS
        ]
        binding = (
            db.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.agent_slug == AGENT_SLUG,
                AgentConnectorBinding.connector_name == "loadedhub",
            )
            .first()
        )
        if binding:
            binding.capabilities = caps
            binding.enabled = True
            print(f"updating binding {AGENT_SLUG} -> loadedhub ({len(caps)} actions)")
        else:
            db.add(
                AgentConnectorBinding(
                    agent_slug=AGENT_SLUG,
                    connector_name="loadedhub",
                    capabilities=caps,
                    enabled=True,
                )
            )
            print(f"creating binding {AGENT_SLUG} -> loadedhub ({len(caps)} actions)")

        if args.dry_run:
            print("\n--dry-run: rolling back, no changes committed.")
            db.rollback()
        else:
            db.commit()
            print("\ncommitted.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
