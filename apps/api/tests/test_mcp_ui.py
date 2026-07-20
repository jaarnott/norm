"""MCP Apps (SEP-1865) embedded UI: resources + tool binding + capability.

These lock the wire contract the host depends on: the exact ``ui://`` resource
shape, the ``text/html;profile=mcp-app`` mime type, the extension capability in
``initialize``, and the ``_meta.ui.resourceUri`` binding on a tool — plus the
``MCP_UI_ENABLED`` kill switch actually removing all of it.
"""

from app.config import settings
from app.mcp.server import McpContext, handle_jsonrpc
from app.mcp.ui_apps import (
    UI_APPS,
    UI_EXTENSION_ID,
    UI_MIME_TYPE,
    list_ui_resources,
    read_ui_resource,
    ui_resource_for,
    ui_resource_for_playbook,
)
from app.mcp.projection import McpTool, to_mcp_tool_dict

DISPLAY_BLOCK_URI = "ui://norm/display-block"
WORKFLOW_URI = "ui://norm/workflow"


def _rpc(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return handle_jsonrpc(body, McpContext())


# ── Registry ─────────────────────────────────────────────────────────────


class TestRegistry:
    def test_static_presentation_is_left_to_claude(self):
        """Tables and charts are NOT embedded — Claude renders those natively.

        A component earns a ui:// binding only by being interactive. Binding a
        table here would be strictly worse than plain data: Claude can't
        summarise or slice what we've frozen into an iframe.
        """
        assert ui_resource_for("loadedhub", "get_sales_data") is None

    def test_date_safe_roster_keeps_the_ui(self):
        """Swapping MCP from the raw get_roster to get_roster_for_period must
        not silently drop the roster grid — a binding covering only the raw
        action would do exactly that."""
        from app.mcp.ui_apps import component_for

        assert (
            ui_resource_for("loadedhub", "get_roster_for_period") == DISPLAY_BLOCK_URI
        )
        assert component_for("loadedhub", "get_roster_for_period") == "roster_editor"

    def test_roster_uses_the_rich_component_not_the_table(self):
        """The roster IS interactive in Norm — a weekly drag grid — so it earns
        a binding. It must be roster_editor, never roster_table: the table is
        the thing Claude already does better."""
        from app.mcp.ui_apps import component_for

        assert ui_resource_for("loadedhub", "get_roster") == DISPLAY_BLOCK_URI
        assert component_for("loadedhub", "get_roster") == "roster_editor"

    def test_stock_order_playbook_renders_the_real_editor(self):
        """create_stock_order binds to display-block — the SAME
        PurchaseOrderEditor the web app mounts — not the hand-written summary
        card. The card was the reason the Claude demo looked nothing like
        Norm."""
        assert ui_resource_for_playbook("create_stock_order") == DISPLAY_BLOCK_URI
        assert ui_resource_for_playbook("cogs_analysis") is None
        assert ui_resource_for_playbook(None) is None

    def test_unmapped_tool_has_no_ui(self):
        assert ui_resource_for("loadedhub", "get_stock_items") is None
        assert ui_resource_for(None, None) is None

    def test_every_bound_uri_resolves_to_a_real_app(self):
        """A binding pointing at a missing app would render nothing, silently."""
        from app.mcp.ui_apps import PLAYBOOK_UI, TOOL_COMPONENT, TOOL_UI

        bound = list(TOOL_UI.values()) + list(PLAYBOOK_UI.values())
        bound += [DISPLAY_BLOCK_URI] * len(TOOL_COMPONENT)
        for uri in bound:
            assert uri in UI_APPS, f"{uri} is bound but not registered"

    def test_shared_bridge_and_css_are_injected(self):
        """Each app is served self-contained: no unresolved injection markers,
        and the shared protocol/style actually made it in."""
        for uri in UI_APPS:
            html = read_ui_resource(uri)["contents"][0]["text"]
            assert "__NORM_BRIDGE__" not in html and "__NORM_BASE_CSS__" not in html
            assert "window.NormApp" in html  # shared bridge present
            assert "--brand" in html  # base css injected

    def test_list_shape(self):
        rows = list_ui_resources()
        uris = {r["uri"] for r in rows}
        assert {WORKFLOW_URI, DISPLAY_BLOCK_URI} <= uris
        for r in rows:
            assert r["mimeType"] == UI_MIME_TYPE
            assert r["uri"].startswith("ui://")

    def test_read_returns_self_contained_html(self):
        res = read_ui_resource(WORKFLOW_URI)
        content = res["contents"][0]
        assert content["uri"] == WORKFLOW_URI
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
        assert WORKFLOW_URI in uris

    def test_resources_read(self):
        resp = _rpc("resources/read", {"uri": WORKFLOW_URI})
        content = resp["result"]["contents"][0]
        assert content["mimeType"] == UI_MIME_TYPE
        assert "<!DOCTYPE html>" in content["text"]

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
        d = to_mcp_tool_dict(_tool(ui_resource=WORKFLOW_URI))
        assert d["_meta"]["ui"]["resourceUri"] == WORKFLOW_URI

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
        assert "error" in _rpc("resources/read", {"uri": WORKFLOW_URI})


# ── UI payload vs model payload ──────────────────────────────────────────


class TestUiPayloadSeparation:
    """A UI tool's data must not be shrunk to fit the MODEL's budget.

    This is the bug that made a week-long roster arrive as a "_too_large" stub
    with no shifts left to draw: `content` and `structuredContent` were both
    the shaped payload.
    """

    def _roster(self, n):
        return {
            "id": 1,
            "rosteredShifts": [
                {
                    "staffMemberFirstName": f"Staff{i}",
                    "staffMemberLastName": "X",
                    "roleName": "Bar",
                    "totalHours": 7.5,
                    "rules": [
                        {
                            "startTime": "2026-07-20T16:00:00+12:00",
                            "endTime": "2026-07-20T23:30:00+12:00",
                        }
                    ],
                    "breaks": [{"breakStart": "x" * 200}],
                }
                for i in range(n)
            ],
        }

    def test_big_payload_is_shaped_for_the_model(self):
        from app.mcp.results import shape_result

        shaped, truncated = shape_result(self._roster(400))
        assert truncated
        assert "rosteredShifts" not in shaped  # the model gets a stub

    def test_but_the_app_still_gets_the_real_data(self):
        from app.mcp.results import ui_payload

        full = self._roster(400)
        assert ui_payload(full) is full  # unshaped, array intact

    def test_result_carries_both_halves(self):
        from app.connectors.mcp_protocol import tools_call_result
        from app.mcp.results import shape_result, ui_payload

        full = self._roster(400)
        shaped, _ = shape_result(full)
        res = tools_call_result(shaped, structured=ui_payload(full))
        # model-facing text is the stub; the app's copy has the shifts
        assert "rosteredShifts" not in res["content"][0]["text"]
        assert len(res["structuredContent"]["rosteredShifts"]) == 400

    def test_absurd_payload_falls_back_rather_than_shipping_megabytes(self):
        from app.mcp.results import ui_payload

        assert ui_payload({"x": "y" * 500_000}) is None

    def test_non_ui_tool_is_unchanged(self):
        from app.connectors.mcp_protocol import tools_call_result

        res = tools_call_result({"a": 1})
        assert res["structuredContent"] == {"a": 1}


class TestUiContentSummary:
    """The MODEL-facing content of a UI tool must not read as a failure.

    A fully-rendered 115-shift roster grid was handing the model the
    "too many to return, narrow the request" envelope — so the model re-fetched
    in chunks, hit the same envelope, and detoured through another tool while
    the user watched a perfectly good grid. For a UI tool the content is a
    compact "it's on screen" summary instead.
    """

    def _roster_wrapped(self, n):
        return {
            "window": {
                "label": "This week",
                "start": "2026-07-20",
                "end": "2026-07-26",
            },
            "data": [
                {
                    "id": "wk",
                    "totalHours": 283.25,
                    "rosteredShifts": [
                        {"staffMemberFirstName": f"S{i}", "totalHours": 4.0}
                        for i in range(n)
                    ],
                }
            ],
        }

    def test_summary_keeps_headline_fields_and_samples_shifts(self):
        from app.mcp.results import ui_content_summary

        out = ui_content_summary(self._roster_wrapped(115))
        assert out["_rendered_in_ui"] is True
        assert out["window"]["label"] == "This week"
        item = out["data"][0]
        assert item["totalHours"] == 283.25  # headline preserved
        # long list sampled, not dumped and not a "too many" envelope
        assert len(item["rosteredShifts"]) < 115
        assert "_too_large" not in out
        # The message instructs AGAINST narrowing — the opposite of the
        # shape_result envelope that says "narrow the request".
        assert "not narrow" in out["message"].lower()

    def test_message_tells_the_model_not_to_refetch(self):
        from app.mcp.results import ui_content_summary

        out = ui_content_summary(self._roster_wrapped(115))
        msg = out["message"].lower()
        assert "already" in msg or "on their screen" in msg
        assert "do not" in msg or "don't" in msg


# ── Bundle freshness ─────────────────────────────────────────────────────


class TestBundleFreshness:
    """The display-block bundle is a COMMITTED build artifact.

    Editing RosterTable without running `pnpm --filter @norm/mcp-ui build`
    would ship a stale component to Claude while the web app moved on — the
    exact drift this whole refactor exists to prevent. The build stamps a hash
    of its sources; we recompute it here.
    """

    # Keep in sync with apps/mcp-ui/scripts/emit.mjs SOURCES
    SOURCES = [
        "apps/web/app/components/display/GenericTable.tsx",
        "apps/web/app/components/display/RosterEditor.tsx",
        "apps/web/app/components/display/PurchaseOrderEditor.tsx",
        "apps/web/app/components/display/roster/shared.ts",
        "apps/web/app/components/display/roster/WeekGrid.tsx",
        "apps/web/app/components/display/roster/DayTimeline.tsx",
        "apps/web/app/components/display/roster/ShiftModal.tsx",
        "apps/web/app/lib/datetime.ts",
        "apps/web/app/lib/rosterTime.ts",
        "apps/web/app/components/display/roster/grid.ts",
        "apps/web/app/components/display/roster/warnings.ts",
        "apps/mcp-ui/src/registry.ts",
        "apps/mcp-ui/src/main.tsx",
        "apps/mcp-ui/src/sandbox-api.ts",
        "apps/mcp-ui/src/WorkflowResult.tsx",
        "apps/mcp-ui/vite.config.ts",
        "apps/api/app/mcp/ui/_bridge.js",
    ]

    def test_bundle_matches_its_sources(self):
        import hashlib
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        bundle = root / "apps/api/app/mcp/ui/display-block.html"
        html = bundle.read_text(encoding="utf-8")

        stamped = re.search(r"norm-mcp-ui-sources sha256:([0-9a-f]{64})", html)
        assert stamped, "bundle has no source stamp — rebuild apps/mcp-ui"

        h = hashlib.sha256()
        for rel in self.SOURCES:
            h.update((root / rel).read_bytes())

        assert stamped.group(1) == h.hexdigest(), (
            "display-block.html is STALE: a source component changed since it "
            "was built. Run: pnpm --filter @norm/mcp-ui build"
        )


class TestConsolidatorEnvelopeUnwrapping:
    """A *_for_period tool wraps its result as {window, data}. Components parse
    the raw connector shape and only look one level deep, so the envelope has to
    be unwrapped or a roster renders empty."""

    def _block(self, payload):
        from app.mcp.execution import NormMcpContext

        # Must be a tool that actually maps to a component, or _as_display_block
        # correctly passes the payload straight through.
        tool = McpTool(
            name="loadedhub__get_roster_for_period",
            kind="connector",
            connector="loadedhub",
            action="get_roster_for_period",
            playbook_slug=None,
            method="GET",
            access="read",
            scopes=frozenset({"mcp:roster:read"}),
            description="Roster for a period",
            input_schema={"type": "object", "properties": {}},
            ui_resource=DISPLAY_BLOCK_URI,
        )
        ctx = NormMcpContext(principal=None, db=None, config_db=None)
        return ctx._as_display_block(tool, payload)

    def test_envelope_is_unwrapped_for_the_component(self):
        roster = {"rosteredShifts": [{"staffMemberFirstName": "Ana"}]}
        window = {"description": "Yesterday — ...", "trading_aligned": True}
        block = self._block({"window": window, "data": roster})
        assert block["data"] == roster, "component must receive the raw payload"
        assert block["props"]["window"] == window, "window still travels, as a prop"

    def test_unwrapped_payload_passes_through_untouched(self):
        raw = {"rosteredShifts": []}
        assert self._block(raw)["data"] == raw

    def test_partial_envelope_is_not_mistaken_for_one(self):
        # `data` alone (no window) is a plausible connector payload, not an envelope.
        payload = {"data": [1, 2, 3]}
        assert self._block(payload)["data"] == payload
