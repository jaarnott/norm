"""MCP Apps (SEP-1865) — Norm's embedded UI resources.

An MCP App is a self-contained HTML page the server exposes at a ``ui://`` URI.
The host (claude.ai) renders it in a sandboxed iframe and pushes the bound
tool's result to it over a postMessage bridge. This is deliberately NOT a
Norm-hosted page framed by claude.ai: the HTML is served as an MCP *resource*
(over the already-authenticated ``POST /mcp``), and any live data the app needs
it fetches by calling a Norm tool back through the host — which re-enters our
own authenticated dispatch (scopes, venue, audit). So there is no new auth
surface, no cross-origin session, and no framing/CSP concern on Norm's side.

This module is the registry: which ``ui://`` apps exist, the HTML for each, and
which tool renders into which app. The HTML lives beside this file in ``ui/`` so
it can be edited as a real file (and linted/rendered) rather than a Python
string literal.
"""

from __future__ import annotations

import functools
from pathlib import Path

# The MCP Apps mime type. Must be exactly this — the host keys UI rendering off
# it (and advertises it back in the initialize extension capability).
UI_MIME_TYPE = "text/html;profile=mcp-app"

# The extension id a server declares in `initialize.capabilities.extensions`.
UI_EXTENSION_ID = "io.modelcontextprotocol/ui"

_UI_DIR = Path(__file__).parent / "ui"


class UiApp:
    """One ``ui://`` resource: an HTML app the host can render."""

    def __init__(self, uri: str, name: str, title: str, html_file: str) -> None:
        self.uri = uri
        self.name = name
        self.title = title
        self._html_file = html_file

    @functools.cached_property
    def html(self) -> str:
        return (_UI_DIR / self._html_file).read_text(encoding="utf-8")


# ── Registry ─────────────────────────────────────────────────────────────
# Keep this small and curated, like the tool surface. A ui:// app is only
# reachable if a tool is bound to it below.

_SALES_CHART = UiApp(
    uri="ui://norm/sales-chart",
    name="Sales chart",
    title="Norm — Sales chart",
    html_file="sales-chart.html",
)

UI_APPS: dict[str, UiApp] = {app.uri: app for app in (_SALES_CHART,)}

# Which curated tool renders into which app. Keyed by (connector, action).
# A tool absent here is a plain text/data tool with no embedded UI.
TOOL_UI: dict[tuple[str, str], str] = {
    ("loadedhub", "get_sales_data"): _SALES_CHART.uri,
}


# ── Accessors ────────────────────────────────────────────────────────────


def ui_resource_for(connector: str | None, action: str | None) -> str | None:
    """The ui:// resource a connector tool renders into, or None."""
    if not connector or not action:
        return None
    return TOOL_UI.get((connector, action))


def list_ui_resources() -> list[dict]:
    """`resources/list` entries for every registered UI app."""
    return [
        {
            "uri": app.uri,
            "name": app.name,
            "title": app.title,
            "mimeType": UI_MIME_TYPE,
        }
        for app in UI_APPS.values()
    ]


def read_ui_resource(uri: str) -> dict | None:
    """`resources/read` result for one ui:// app, or None if unknown.

    The HTML is self-contained (inline CSS/JS, no external origins), so no
    ``_meta.ui.csp`` relaxation is declared — the host's deny-by-default sandbox
    is exactly what we want.
    """
    app = UI_APPS.get(uri)
    if app is None:
        return None
    return {
        "contents": [
            {
                "uri": app.uri,
                "mimeType": UI_MIME_TYPE,
                "text": app.html,
            }
        ]
    }
