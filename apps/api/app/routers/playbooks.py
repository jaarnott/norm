"""Playbooks CRUD — step-by-step guides the agent reads when a request matches."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.config_models import Playbook
from app.db.engine import get_config_db, get_config_db_rw, get_db
from app.db.models import User
from app.services.models import agent_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


class PlaybookCreate(BaseModel):
    slug: str
    agent_slug: str
    display_name: str
    description: str
    instructions: str
    enabled: bool = True


class PlaybookUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    instructions: str | None = None
    enabled: bool | None = None


def _to_dict(p: Playbook) -> dict:
    return {
        "id": p.id,
        "slug": p.slug,
        "agent_slug": p.agent_slug,
        "display_name": p.display_name,
        "description": p.description,
        "instructions": p.instructions,
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


@router.get("/tools/all")
async def list_entitled_tools(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Tool actions available for a task's tool_filter.

    The full entitled union — the same set an interactive agent sees — so a
    task's filter can span domains (e.g. read a report AND order stock). It
    reuses the runtime assembly, so it is entitlement-filtered, deduped, and
    excludes retired tools.
    """
    return {"tools": _tool_rows(db, user.id, config_db)}


def _tool_rows(db: Session, user_id: str, config_db: Session) -> list[dict]:
    from app.agents.prompt_builder import _collect_tools

    return [
        {
            "action": t["action"],
            "connector": t["connector"],
            "method": t.get("method", "?"),
            "description": t.get("description", ""),
        }
        for t in _collect_tools(db, user_id=user_id, config_db=config_db)
    ]


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

    # Tool context: every tool the agent can use (it holds the full union)
    tool_lines = [
        f"- {t['connector']}__{t['action']} [{t['method']}]: {t['description']}"
        for t in _tool_rows(db, user.id, config_db)
    ]
    tools_text = "\n".join(tool_lines) if tool_lines else "(no tools bound)"

    if body.current_instructions:
        prompt = f"""You are a playbook editor for a hospitality AI platform. Refine the existing playbook instructions based on the user's request.

Available tools:
{tools_text}

Current instructions:
{body.current_instructions}

User request: {body.description}

Return a JSON object with:
- "instructions": the updated playbook instructions (string, can use markdown)
- "display_name": a concise name for this playbook (if the current one should change, otherwise keep it)
- "description": one sentence saying when to use this playbook (the agent reads it to decide whether to open the playbook)
- "slug": a snake_case slug for this playbook

Return ONLY valid JSON, no markdown fences."""
    else:
        prompt = f"""You are a playbook generator for a hospitality AI platform. Create focused workflow instructions for an agent.

Available tools:
{tools_text}

User description: {body.description}

Generate a JSON object with:
- "instructions": detailed step-by-step workflow instructions telling the agent exactly what tools to call and in what order, how to format results, and what to watch out for (string, can use markdown)
- "display_name": a concise name for this playbook
- "description": one sentence saying when to use this playbook (the agent reads it to decide whether to open the playbook)
- "slug": a snake_case slug for this playbook

Return ONLY valid JSON, no markdown fences."""

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=agent_model(db),
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
