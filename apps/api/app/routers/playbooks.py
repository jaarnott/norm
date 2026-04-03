"""Playbooks CRUD — focused instruction sets for agent workflows."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.config_models import Playbook
from app.db.engine import get_config_db, get_config_db_rw, get_db
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


class PlaybookCreate(BaseModel):
    slug: str
    agent_slug: str
    display_name: str
    description: str
    instructions: str
    tool_filter: list[str] | None = None
    enabled: bool = True


class PlaybookUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    instructions: str | None = None
    tool_filter: list[str] | None = None
    enabled: bool | None = None


def _to_dict(p: Playbook) -> dict:
    return {
        "id": p.id,
        "slug": p.slug,
        "agent_slug": p.agent_slug,
        "display_name": p.display_name,
        "description": p.description,
        "instructions": p.instructions,
        "tool_filter": p.tool_filter,
        "enabled": p.enabled,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("")
async def list_playbooks(
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    playbooks = (
        config_db.query(Playbook).order_by(Playbook.agent_slug, Playbook.slug).all()
    )
    return {"playbooks": [_to_dict(p) for p in playbooks]}


@router.get("/tools/{agent_slug}")
async def list_agent_tools(
    agent_slug: str,
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """List all available tool actions for an agent."""
    from app.db.config_models import AgentConnectorBinding, ConnectorSpec

    bindings = (
        config_db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == agent_slug,
            AgentConnectorBinding.enabled == True,  # noqa: E712
        )
        .all()
    )
    tools = []
    for binding in bindings:
        spec = (
            config_db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == binding.connector_name)
            .first()
        )
        if not spec:
            continue
        for tool in spec.tools or []:
            action = tool.get("action", "")
            if action:
                tools.append(
                    {
                        "action": action,
                        "connector": spec.connector_name,
                        "method": tool.get("method", "?"),
                        "description": tool.get("description", ""),
                    }
                )
    return {"tools": tools}


@router.get("/{slug}")
async def get_playbook(
    slug: str,
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    playbook = config_db.query(Playbook).filter(Playbook.slug == slug).first()
    if not playbook:
        raise HTTPException(404, f"Playbook not found: {slug}")
    return _to_dict(playbook)


@router.post("", status_code=201)
async def create_playbook(
    body: PlaybookCreate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    existing = config_db.query(Playbook).filter(Playbook.slug == body.slug).first()
    if existing:
        raise HTTPException(409, f"Playbook already exists: {body.slug}")

    playbook = Playbook(
        slug=body.slug,
        agent_slug=body.agent_slug,
        display_name=body.display_name,
        description=body.description,
        instructions=body.instructions,
        tool_filter=body.tool_filter,
        enabled=body.enabled,
    )
    config_db.add(playbook)
    config_db.commit()
    config_db.refresh(playbook)
    return _to_dict(playbook)


@router.put("/{slug}")
async def update_playbook(
    slug: str,
    body: PlaybookUpdate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    playbook = config_db.query(Playbook).filter(Playbook.slug == slug).first()
    if not playbook:
        raise HTTPException(404, f"Playbook not found: {slug}")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(playbook, key, value)

    config_db.commit()
    config_db.refresh(playbook)
    return _to_dict(playbook)


@router.delete("/{slug}")
async def delete_playbook(
    slug: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    playbook = config_db.query(Playbook).filter(Playbook.slug == slug).first()
    if not playbook:
        raise HTTPException(404, f"Playbook not found: {slug}")

    config_db.delete(playbook)
    config_db.commit()
    return {"ok": True}


class GeneratePlaybookBody(BaseModel):
    description: str
    agent_slug: str
    current_instructions: str | None = None


@router.post("/generate")
async def generate_playbook(
    body: GeneratePlaybookBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Use AI to generate or refine playbook instructions."""
    from app.services.secrets import get_api_key
    import anthropic

    api_key = get_api_key("anthropic", "api_key", db)
    if not api_key:
        raise HTTPException(400, "Anthropic API key required")

    # Build tool context for the agent
    from app.db.config_models import AgentConnectorBinding, ConnectorSpec

    bindings = (
        config_db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == body.agent_slug,
            AgentConnectorBinding.enabled == True,  # noqa: E712
        )
        .all()
    )
    tool_lines = []
    for binding in bindings:
        spec = (
            config_db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == binding.connector_name)
            .first()
        )
        if not spec:
            continue
        for tool in spec.tools or []:
            action = tool.get("action", "")
            method = tool.get("method", "?")
            desc = tool.get("description", "")
            tool_lines.append(f"- {spec.connector_name}__{action} [{method}]: {desc}")

    tools_text = "\n".join(tool_lines) if tool_lines else "(no tools bound)"

    if body.current_instructions:
        prompt = f"""You are a playbook editor for a hospitality AI platform. Refine the existing playbook instructions based on the user's request.

Available tools for the {body.agent_slug} agent:
{tools_text}

Current instructions:
{body.current_instructions}

User request: {body.description}

Return a JSON object with:
- "instructions": the updated playbook instructions (string, can use markdown)
- "display_name": a concise name for this playbook (if the current one should change, otherwise keep it)
- "description": a one-sentence description for router matching
- "tool_filter": array of tool action names to include (e.g. ["get_sales_data", "render_chart"]), or null for all tools
- "slug": a snake_case slug for this playbook

Return ONLY valid JSON, no markdown fences."""
    else:
        prompt = f"""You are a playbook generator for a hospitality AI platform. Create focused workflow instructions for an agent.

Available tools for the {body.agent_slug} agent:
{tools_text}

User description: {body.description}

Generate a JSON object with:
- "instructions": detailed step-by-step workflow instructions telling the agent exactly what tools to call and in what order, how to format results, and what to watch out for (string, can use markdown)
- "display_name": a concise name for this playbook
- "description": a one-sentence description for router matching
- "tool_filter": array of tool action names to include (e.g. ["get_sales_data", "render_chart"]), or null for all tools
- "slug": a snake_case slug for this playbook

Return ONLY valid JSON, no markdown fences."""

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        raw = "\n".join(lines).strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(500, f"AI returned invalid JSON: {raw[:500]}")

    return result
