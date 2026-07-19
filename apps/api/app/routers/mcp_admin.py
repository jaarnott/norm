"""Curation of the MCP surface — Settings → MCP.

Candidates are **computed, not stored**: every ConnectorSpec action and every
Playbook, left-joined against ``mcp_capabilities``. A new connector or playbook
therefore appears here the moment it exists, disabled. Nothing has to be
registered in a second place; the toggle is the only decision.

The toggle is safe because the dangerous degrees of freedom aren't toggles:

- ``access`` (read vs draft) is derived from the spec's ``method``. A non-GET
  action is **refused** as a direct tool — it must go through a playbook so
  Norm's approval flow stays in the loop.
- ``scopes`` must come from ``app.mcp.scopes``; unknown values are refused.
- Conversation-scoped tools are denylisted in code.

All refusals happen here, at write time, and are re-checked daily by
``config_validator`` to catch drift (e.g. a spec whose method later changed).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_config_db, get_config_db_rw
from app.mcp.projection import (
    MCP_DENYLIST,
    default_tool_name,
    exposable_reason,
    is_read_tool,
    suggest_scopes,
)
from app.mcp.ui_apps import (
    component_for,
    ui_descriptor,
    ui_resource_for,
    ui_resource_for_playbook,
)
from app.mcp.scopes import MCP_SCOPES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-admin"])


class McpCapabilityOut(BaseModel):
    kind: str
    target: str
    action: str
    tool_name: str
    method: str
    description: str
    access: str | None  # None when not exposable
    enabled: bool
    scopes: list[str]
    grantable_scopes: list[str]
    suggested_scopes: list[str]  # the natural default(s); [] when nothing fits
    # Set when this capability renders an interactive component in Claude
    # rather than plain data: {resource, component, name}. None = data only.
    ui: dict | None
    exposable: bool
    reason: str | None


class McpScopeOut(BaseModel):
    name: str
    label: str
    description: str
    access_level: str
    requires: list[str]


class McpCapabilityIn(BaseModel):
    kind: str = Field(pattern="^(connector|playbook)$")
    target: str
    action: str = ""
    enabled: bool = False
    scopes: list[str] = []
    description_override: str | None = None
    tool_name_override: str | None = None


@router.get("/mcp/scopes", response_model=list[McpScopeOut])
def list_scopes(_user=Depends(require_permission("admin:system"))):
    """The scope vocabulary. Lives in code; served here for the UI."""
    return [
        McpScopeOut(
            name=s.name,
            label=s.label,
            description=s.description,
            access_level=s.access_level,
            requires=sorted(s.requires),
        )
        for s in sorted(MCP_SCOPES.values(), key=lambda s: s.name)
    ]


@router.get("/mcp/capabilities", response_model=list[McpCapabilityOut])
def list_capabilities(
    _user=Depends(require_permission("admin:system")),
    config_db: Session = Depends(get_config_db),
):
    """Every candidate, with its curation state.

    Candidates are derived from config, so this list can never fall behind the
    connectors and playbooks that actually exist.
    """
    from app.db.config_models import ConnectorSpec, McpCapability, Playbook

    existing = {
        (c.kind, c.target, c.action): c for c in config_db.query(McpCapability).all()
    }
    out: list[McpCapabilityOut] = []

    for spec in config_db.query(ConnectorSpec).order_by(ConnectorSpec.connector_name):
        for tool in spec.tools or []:
            action = tool.get("action") or ""
            if not action:
                continue
            method = (tool.get("method") or "POST").upper()
            key = ("connector", spec.connector_name, action)
            cap = existing.get(key)

            if (spec.connector_name, action) in MCP_DENYLIST:
                reason = (
                    "This tool only works inside a Norm conversation (it reads "
                    "prior results or paints a Norm UI block), so it cannot be "
                    "exposed over MCP."
                )
            else:
                reason = exposable_reason("connector", tool)
                if reason is None and not spec.enabled:
                    reason = "This connector is disabled."

            out.append(
                McpCapabilityOut(
                    kind="connector",
                    target=spec.connector_name,
                    action=action,
                    tool_name=(cap.tool_name_override if cap else None)
                    or default_tool_name("connector", spec.connector_name, action),
                    method=method,
                    description=tool.get("description") or action.replace("_", " "),
                    access="read" if is_read_tool(tool) else None,
                    enabled=bool(cap and cap.enabled),
                    scopes=list(cap.scopes or []) if cap else [],
                    grantable_scopes=[
                        s.name for s in MCP_SCOPES.values() if s.access_level == "read"
                    ],
                    suggested_scopes=suggest_scopes(
                        spec.connector_name, action, tool.get("description") or ""
                    ),
                    ui=ui_descriptor(
                        ui_resource_for(spec.connector_name, action),
                        component_for(spec.connector_name, action),
                    ),
                    exposable=reason is None,
                    reason=reason,
                )
            )

    for pb in config_db.query(Playbook).order_by(Playbook.slug):
        key = ("playbook", pb.slug, "")
        cap = existing.get(key)
        reason = None if pb.enabled else "This playbook is disabled."
        out.append(
            McpCapabilityOut(
                kind="playbook",
                target=pb.slug,
                action="",
                tool_name=(cap.tool_name_override if cap else None)
                or default_tool_name("playbook", pb.slug, ""),
                method="WORKFLOW",
                description=pb.description,
                access="draft",
                enabled=bool(cap and cap.enabled),
                scopes=list(cap.scopes or []) if cap else [],
                grantable_scopes=[s.name for s in MCP_SCOPES.values()],
                suggested_scopes=suggest_scopes(
                    pb.slug, "", pb.description, drafts=True
                ),
                ui=ui_descriptor(ui_resource_for_playbook(pb.slug)),
                exposable=reason is None,
                reason=reason,
            )
        )

    return out


@router.put("/mcp/capabilities", response_model=McpCapabilityOut)
def upsert_capability(
    body: McpCapabilityIn,
    _user=Depends(require_permission("admin:system")),
    config_db: Session = Depends(get_config_db_rw),
):
    """Enable/disable a capability and set its scopes.

    Validates before saving. A refusal here is the point: it's what stops a
    toggle from widening the v1 read/draft boundary.
    """
    from app.db.config_models import ConnectorSpec, McpCapability, Playbook

    action = body.action or ""

    # ── Validate the target exists, and derive its method ────────────
    if body.kind == "connector":
        spec = (
            config_db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == body.target)
            .first()
        )
        if not spec:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No such connector: {body.target}"
            )
        tool_def = next(
            (t for t in (spec.tools or []) if t.get("action") == action), None
        )
        if not tool_def:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No such action: {body.target}.{action}",
            )
        method = (tool_def.get("method") or "POST").upper()

        if (body.target, action) in MCP_DENYLIST:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{body.target}.{action} only works inside a Norm conversation "
                f"and cannot be exposed over MCP.",
            )

        # The v1 boundary, enforced structurally.
        reason = exposable_reason("connector", tool_def)
        if reason:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
    else:
        pb = config_db.query(Playbook).filter(Playbook.slug == body.target).first()
        if not pb:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No such playbook: {body.target}"
            )
        if body.enabled and not pb.enabled:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Playbook '{body.target}' is disabled — enable it before "
                f"exposing it over MCP.",
            )
        method = "WORKFLOW"

    # ── Validate scopes against the code-side vocabulary ─────────────
    unknown = set(body.scopes) - set(MCP_SCOPES)
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown MCP scope(s): {', '.join(sorted(unknown))}. "
            f"Valid scopes: {', '.join(sorted(MCP_SCOPES))}",
        )
    if body.enabled and not body.scopes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "An enabled capability must declare at least one scope — otherwise "
            "it is authorized by nothing but holding a token.",
        )

    # ── Upsert ───────────────────────────────────────────────────────
    cap = (
        config_db.query(McpCapability)
        .filter(
            McpCapability.kind == body.kind,
            McpCapability.target == body.target,
            McpCapability.action == action,
        )
        .first()
    )
    if cap is None:
        cap = McpCapability(kind=body.kind, target=body.target, action=action)
        config_db.add(cap)

    cap.enabled = body.enabled
    cap.scopes = body.scopes
    cap.description_override = body.description_override
    cap.tool_name_override = body.tool_name_override
    config_db.commit()
    config_db.refresh(cap)

    logger.info(
        "mcp_capability_updated",
        extra={
            "kind": cap.kind,
            "target": cap.target,
            "action": cap.action,
            "enabled": cap.enabled,
            "scopes": cap.scopes,
        },
    )

    return McpCapabilityOut(
        kind=cap.kind,
        target=cap.target,
        action=cap.action,
        tool_name=cap.tool_name_override
        or default_tool_name(cap.kind, cap.target, cap.action),
        method=method,
        description=cap.description_override or "",
        access="read" if body.kind == "connector" else "draft",
        enabled=cap.enabled,
        scopes=list(cap.scopes or []),
        grantable_scopes=[s.name for s in MCP_SCOPES.values()],
        suggested_scopes=suggest_scopes(
            cap.target,
            cap.action,
            cap.description_override or "",
            drafts=cap.kind == "playbook",
        ),
        ui=ui_descriptor(
            ui_resource_for_playbook(cap.target)
            if cap.kind == "playbook"
            else ui_resource_for(cap.target, cap.action),
            None if cap.kind == "playbook" else component_for(cap.target, cap.action),
        ),
        exposable=True,
        reason=None,
    )
