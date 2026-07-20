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
#
# WHAT BELONGS HERE: interactive surfaces — editing a draft, picking from a
# list, approving something. Things the user *does*.
#
# WHAT DOES NOT: tables and charts. Claude renders those natively from the
# tool's JSON, and it does it well. An embedded static table is strictly worse
# than letting Claude lay the data out in the conversation, because ours can't
# be summarised, sliced or reasoned over. A component earns a place here only
# by doing something Claude cannot.

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

UI_APPS: dict[str, UiApp] = {app.uri: app for app in (_WORKFLOW, _DISPLAY_BLOCK)}

DISPLAY_BLOCK_URI = _DISPLAY_BLOCK.uri

# Which Norm display component draws a tool's result, keyed by
# (connector, action). Tools listed here render through _DISPLAY_BLOCK.
#
# roster_editor is the weekly drag grid / day timeline — rich and interactive,
# which is the bar. (roster_table would have been the easy binding and the
# wrong one: it is a table, and Claude draws tables better than we embed them.)
#
# It renders read-only here: the working-document fetch is guarded on a
# working_document_id we never send, and every mutation is delegated to an
# `onAction` callback we deliberately do not pass — so no dead buttons and no
# network. Wiring onAction through the bridge to tools/call is what would make
# it editable from Claude, once there is a draft-safe action to route it to.
#
# Components parse the RAW connector payload (Norm's show_* tools hand it over
# untouched, internal_tools._show_component), so there is no adapter either side.
TOOL_COMPONENT: dict[tuple[str, str], str] = {
    ("loadedhub", "get_roster"): "roster_editor",
    # The date-safe front for the same data. Both are mapped because either may
    # be the enabled one — MCP curation decides which, and a binding that only
    # covered the raw action would silently drop the UI the day it was swapped.
    ("loadedhub", "get_roster_for_period"): "roster_editor",
}

# Bespoke apps for a connector tool, keyed by (connector, action). Empty: the
# only bespoke app left is the playbook workflow card.
TOOL_UI: dict[tuple[str, str], str] = {}

# Which playbook workflow renders into which app, keyed by playbook slug.
#
# create_stock_order renders into display-block — the REAL PurchaseOrderEditor,
# same one the web app mounts, with lines pre-resolved server-side
# (po_display.py) and its edits/submit routed back through the app-support
# tools (app_tools.py). The hand-written workflow.html card remains for any
# playbook that only has a text outcome; display-block also carries a
# `workflow_result` component for non-draft outcomes of bound playbooks.
PLAYBOOK_UI: dict[str, str] = {
    "create_stock_order": _DISPLAY_BLOCK.uri,
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


def ui_descriptor(uri: str | None, component: str | None = None) -> dict | None:
    """Describe a binding for the admin UI, or None if there is no UI.

    Lets Settings → MCP say *which* capabilities render an interactive
    component in Claude rather than plain data — otherwise the only way to know
    is to read this file.
    """
    if not uri:
        return None
    app = UI_APPS.get(uri)
    if app is None:
        return None
    return {
        "resource": uri,
        "component": component,
        "name": app.name,
    }


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
