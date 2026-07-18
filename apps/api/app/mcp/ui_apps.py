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
it can be edited as a real file rather than a Python string literal.

The postMessage protocol and the shared look are NOT copied into each app —
``_bridge.js`` and ``_base.css`` are injected at read time. Each *delivered*
resource is still one self-contained file, but the protocol has exactly one
implementation to fix if a host disagrees with it.
"""

from __future__ import annotations

import functools
from pathlib import Path

# The MCP Apps mime type. Must be exactly this — the host keys UI rendering off
# it (and we advertise it back in the initialize extension capability).
UI_MIME_TYPE = "text/html;profile=mcp-app"

# The extension id a server declares in `initialize.capabilities.extensions`.
UI_EXTENSION_ID = "io.modelcontextprotocol/ui"

_UI_DIR = Path(__file__).parent / "ui"

_BASE_CSS_MARKER = "/*__NORM_BASE_CSS__*/"
_BRIDGE_MARKER = "/*__NORM_BRIDGE__*/"


@functools.lru_cache(maxsize=None)
def _shared(name: str) -> str:
    return (_UI_DIR / name).read_text(encoding="utf-8")


class UiApp:
    """One ``ui://`` resource: an HTML app the host can render."""

    def __init__(self, uri: str, name: str, title: str, html_file: str) -> None:
        self.uri = uri
        self.name = name
        self.title = title
        self._html_file = html_file

    @functools.cached_property
    def html(self) -> str:
        raw = (_UI_DIR / self._html_file).read_text(encoding="utf-8")
        return raw.replace(_BASE_CSS_MARKER, _shared("_base.css")).replace(
            _BRIDGE_MARKER, _shared("_bridge.js")
        )


# ── Registry ─────────────────────────────────────────────────────────────
# Keep this small and curated, like the tool surface. A ui:// app is only
# reachable if a tool or playbook is bound to it below.

_SALES_CHART = UiApp(
    uri="ui://norm/sales-chart",
    name="Sales chart",
    title="Norm — Sales chart",
    html_file="sales-chart.html",
)
# Renders a playbook outcome: status, the agent's summary (often a markdown
# table — e.g. the drafted purchase order), and an "open in Norm" action.
_WORKFLOW = UiApp(
    uri="ui://norm/workflow",
    name="Workflow result",
    title="Norm — Workflow",
    html_file="workflow.html",
)

# Renders Norm's OWN display components (imported from apps/web, bundled by
# apps/mcp-ui) rather than a hand-written re-implementation. Preferred for any
# tool whose result a Norm component already knows how to draw — one renderer,
# fixed once, for both the web app and Claude.
_DISPLAY_BLOCK = UiApp(
    uri="ui://norm/display-block",
    name="Norm display block",
    title="Norm",
    html_file="display-block.html",
)

UI_APPS: dict[str, UiApp] = {
    app.uri: app for app in (_SALES_CHART, _WORKFLOW, _DISPLAY_BLOCK)
}

DISPLAY_BLOCK_URI = _DISPLAY_BLOCK.uri

# Which Norm display component draws a tool's result, keyed by
# (connector, action). Tools listed here render through _DISPLAY_BLOCK.
#
# The component must be in the sandbox-safe registry (apps/mcp-ui/src/registry.ts)
# — i.e. a pure function of its data, with no fetch on mount. That rules out
# roster_editor/purchase_order_editor/orders_dashboard, which self-fetch and
# would fail with no session inside the host's iframe.
#
# Note the components parse the RAW connector payload: Norm's own show_* tools
# hand it over untouched (internal_tools._show_component), so there is no
# adapter to write on either side.
TOOL_COMPONENT: dict[tuple[str, str], str] = {
    ("loadedhub", "get_roster"): "roster_table",
}

# Which curated connector tool renders into which app, keyed by
# (connector, action). A tool absent here is a plain text/data tool.
TOOL_UI: dict[tuple[str, str], str] = {
    ("loadedhub", "get_sales_data"): _SALES_CHART.uri,
}

# Which playbook workflow renders into which app, keyed by playbook slug.
PLAYBOOK_UI: dict[str, str] = {
    "create_stock_order": _WORKFLOW.uri,
}


# ── Accessors ────────────────────────────────────────────────────────────


def ui_resource_for(connector: str | None, action: str | None) -> str | None:
    """The ui:// resource a connector tool renders into, or None.

    A tool mapped to a Norm display component wins: reusing the real component
    beats a bespoke app, so TOOL_COMPONENT is checked first.
    """
    if not connector or not action:
        return None
    if (connector, action) in TOOL_COMPONENT:
        return DISPLAY_BLOCK_URI
    return TOOL_UI.get((connector, action))


def component_for(connector: str | None, action: str | None) -> str | None:
    """The Norm display component that draws this tool's result, or None."""
    if not connector or not action:
        return None
    return TOOL_COMPONENT.get((connector, action))


def ui_resource_for_playbook(slug: str | None) -> str | None:
    """The ui:// resource a playbook workflow renders into, or None."""
    if not slug:
        return None
    return PLAYBOOK_UI.get(slug)


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
