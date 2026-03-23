"""AI-powered E2E test generator using Claude."""

import json
import logging
from anthropic import Anthropic
from app.config import settings

logger = logging.getLogger(__name__)

# Available data-testid selectors in the frontend
AVAILABLE_SELECTORS = {
    "sidebar": [
        "sidebar-home",
        "sidebar-procurement",
        "sidebar-hr",
        "sidebar-reports",
        "sidebar-settings",
        "sidebar-logout",
    ],
    "auth": [
        "login-email",
        "login-password",
        "login-name",
        "login-submit",
        "login-toggle-mode",
    ],
    "home": ["home-message-input", "home-send-btn"],
    "tasks": [
        "new-chat-btn",
        "search-btn",
        "filter-all",
        "filter-awaiting",
        "filter-needs-input",
        "filter-completed",
    ],
    "task_detail": [
        "tab-conversation",
        "tab-details",
        "tab-activity",
        "message-input",
        "send-btn",
        "approve-btn",
        "reject-btn",
    ],
    "settings": [
        "settings-tab-connectors",
        "settings-tab-agents",
        "settings-tab-specs",
        "settings-tab-deployments",
        "settings-tab-tests",
    ],
}

SYSTEM_PROMPT = """You are an expert Playwright test generator. Given a natural language description of a user flow, generate:
1. A list of human-readable steps
2. A complete Playwright test script

Rules:
- Use data-testid selectors whenever possible (preferred over CSS selectors)
- Available data-testid selectors: {selectors}
- Always start by navigating to the login page and logging in with test credentials
- Use explicit waits (waitForSelector, waitForURL) instead of fixed timeouts
- Include meaningful assertions at each step
- The test should be self-contained and idempotent
- Base URL will be injected via Playwright config, use relative paths
- Test credentials: email="admin@norm.local", password="changeme123"

Output format (JSON):
{{
  "steps": [
    {{"step": 1, "description": "Navigate to login page", "selector": null}},
    {{"step": 2, "description": "Enter email", "selector": "[data-testid=login-email]"}}
  ],
  "playwright_script": "import {{ test, expect }} from '@playwright/test';\\n\\ntest('...', async ({{ page }}) => {{ ... }});"
}}

Return ONLY the JSON object, no markdown or explanation."""


async def generate_test(description: str) -> dict:
    """Generate a Playwright test from a natural language description."""
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    selectors_str = json.dumps(AVAILABLE_SELECTORS, indent=2)

    response = client.messages.create(
        model=settings.LLM_INTERPRETER_MODEL or "claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT.format(selectors=selectors_str),
        messages=[
            {
                "role": "user",
                "content": f"Generate a Playwright E2E test for: {description}",
            }
        ],
    )

    text = response.content[0].text.strip()

    # Try to parse JSON (handle markdown code blocks)
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    result = json.loads(text)
    return {
        "steps": result.get("steps", []),
        "playwright_script": result.get("playwright_script", ""),
    }
