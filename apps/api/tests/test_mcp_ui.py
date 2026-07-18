"""MCP Apps (SEP-1865) embedded UI: resources + tool binding + capability.

These lock the wire contract the host depends on: the exact ``ui://`` resource
shape, the ``text/html;profile=mcp-app`` mime type, the extension capability in
``initialize``, and the ``_meta.ui.resourceUri`` binding on a tool — plus the
``MCP_UI_ENABLED`` kill switch actually removing all of it.
"""


from app.config import settings
from app.mcp.server import McpContext, handle_jsonrpc
from app.mcp.ui_apps import (
    UI_EXTENSION_ID,
    UI_MIME_TYPE,
    list_ui_resources,
    read_ui_resource,
    ui_resource_for,
)
from app.mcp.projection import McpTool, to_mcp_tool_dict

SALES_CHART_URI = "ui://norm/sales-chart"


def _rpc(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return handle_jsonrpc(body, McpContext())


# ── Registry ─────────────────────────────────────────────────────────────


class TestRegistry:
    def test_sales_chart_bound_to_get_sales_data(self):
        assert ui_resource_for("loadedhub", "get_sales_data") == SALES_CHART_URI

    def test_unmapped_tool_has_no_ui(self):
        assert ui_resource_for("loadedhub", "get_roster") is None
        assert ui_resource_for(None, None) is None

    def test_list_shape(self):
        rows = list_ui_resources()
        assert any(r["uri"] == SALES_CHART_URI for r in rows)
        for r in rows:
            assert r["mimeType"] == UI_MIME_TYPE
            assert r["uri"].startswith("ui://")

    def test_read_returns_self_contained_html(self):
        res = read_ui_resource(SALES_CHART_URI)
        content = res["contents"][0]
        assert content["uri"] == SALES_CHART_URI
        assert content["mimeType"] == UI_MIME_TYPE
        html = content["text"]
        assert "<!DOCTYPE html>" in html
        # Self-contained: the postMessage bridge is inline, no external origins.
        assert "ui/initialize" in html
        assert "ui/notifications/initialized" in html
        assert "http://" not in html and "https://" not in html

    def test_read_unknown_returns_none(self):
        assert read_ui_resource("ui://norm/does-not-exist") is None


# ── Wire: resources over dispatch ────────────────────────────────────────


class TestResourcesDispatch:
    def test_resources_list(self):
        resp = _rpc("resources/list")
        uris = [r["uri"] for r in resp["result"]["resources"]]
        assert SALES_CHART_URI in uris

    def test_resources_read(self):
        resp = _rpc("resources/read", {"uri": SALES_CHART_URI})
        content = resp["result"]["contents"][0]
        assert content["mimeType"] == UI_MIME_TYPE
        assert "<svg" in content["text"] or "svg" in content["text"]

    def test_resources_read_missing_uri(self):
        resp = _rpc("resources/read", {})
        assert resp["error"]["code"] == -32602  # INVALID_PARAMS

    def test_resources_read_unknown_uri(self):
        resp = _rpc("resources/read", {"uri": "ui://norm/nope"})
        assert "error" in resp


# ── initialize capability ────────────────────────────────────────────────


class TestInitializeCapability:
    def test_advertises_ui_extension(self):
        resp = _rpc("initialize", {"protocolVersion": "2025-06-18"})
        caps = resp["result"]["capabilities"]
        assert "resources" in caps
        assert caps["extensions"][UI_EXTENSION_ID]["mimeTypes"] == [UI_MIME_TYPE]


# ── tool binding ─────────────────────────────────────────────────────────


def _tool(ui_resource=None):
    return McpTool(
        name="loadedhub__get_sales_data",
        kind="connector",
        connector="loadedhub",
        action="get_sales_data",
        playbook_slug=None,
        method="GET",
        access="read",
        scopes=frozenset({"mcp:reports:read"}),
        description="Get sales data",
        input_schema={"type": "object", "properties": {}},
        ui_resource=ui_resource,
    )


class TestToolBinding:
    def test_meta_ui_emitted_when_bound(self):
        d = to_mcp_tool_dict(_tool(ui_resource=SALES_CHART_URI))
        assert d["_meta"]["ui"]["resourceUri"] == SALES_CHART_URI

    def test_no_meta_when_unbound(self):
        assert "_meta" not in to_mcp_tool_dict(_tool(ui_resource=None))


# ── kill switch ──────────────────────────────────────────────────────────


class TestKillSwitch:
    def test_disabled_removes_capability_and_resources(self, monkeypatch):
        monkeypatch.setattr(settings, "MCP_UI_ENABLED", False)
        init = _rpc("initialize", {"protocolVersion": "2025-06-18"})
        caps = init["result"]["capabilities"]
        assert "extensions" not in caps
        assert "resources" not in caps

        assert _rpc("resources/list")["result"]["resources"] == []
        assert "error" in _rpc("resources/read", {"uri": SALES_CHART_URI})
