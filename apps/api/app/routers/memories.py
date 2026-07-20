"""Governance for what Norm has learned — list, approve, edit, delete.

Auto-writing memories is only defensible if a human can see and undo every one
of them, so this is not an optional extra: it is the other half of the design.

Two things it must do that a plain CRUD router would not:

- **Approve candidates.** Org-scoped memories are written as ``candidate`` and
  never reach a prompt until confirmed here, because a shared write changes
  other people's answers. Without this endpoint they accumulate forever.
- **Re-check on edit.** A human editing a memory can turn a harmless preference
  into a business rule ("...and the trading day starts at 7am"). Edits run back
  through admission control, so the Rule 2 boundary cannot be walked around via
  the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import Memory, OrganizationMembership, User
from app.services.memory_rules import admit

router = APIRouter(tags=["memories"])


class MemoryOut(BaseModel):
    id: str
    scope: str
    type: str
    title: str
    body: str
    why: str | None = None
    how_to_apply: str | None = None
    status: str
    trigger: str | None = None
    created_by: str
    thread_id: str | None = None
    created_at: str | None = None
    last_used_at: str | None = None


class MemoryUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    why: str | None = None
    how_to_apply: str | None = None


def _org_id(user: User, db: Session) -> str:
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=400, detail="No organization membership")
    return membership.organization_id


def _visible(db: Session, user: User):
    """Memories this user may see: their own, plus every org memory.

    Org memories are deliberately visible to all members — they shape everyone's
    answers, so everyone should be able to see and challenge them. Another
    user's *personal* preferences are not shown.
    """
    org_id = _org_id(user, db)
    return db.query(Memory).filter(
        Memory.organization_id == org_id,
        Memory.status != "superseded",
        or_(Memory.scope == "org", Memory.user_id == user.id),
    )


def _serialize(m: Memory) -> MemoryOut:
    return MemoryOut(
        id=m.id,
        scope=m.scope,
        type=m.type,
        title=m.title,
        body=m.body,
        why=m.why,
        how_to_apply=m.how_to_apply,
        status=m.status,
        trigger=m.trigger,
        created_by=m.created_by,
        thread_id=m.thread_id,
        created_at=m.created_at.isoformat() if m.created_at else None,
        last_used_at=m.last_used_at.isoformat() if m.last_used_at else None,
    )


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Everything learned, newest first. `status=candidate` is the review queue."""
    query = _visible(db, user)
    if status:
        query = query.filter(Memory.status == status)
    return [_serialize(m) for m in query.order_by(Memory.created_at.desc()).all()]


@router.post("/memories/{memory_id}/approve", response_model=MemoryOut)
def approve_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Promote a candidate to active so it starts shaping answers."""
    memory = _visible(db, user).filter(Memory.id == memory_id).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    if memory.status != "candidate":
        raise HTTPException(
            status_code=400, detail=f"Memory is already {memory.status}"
        )
    memory.status = "active"
    # A human confirmed it, so the provenance is no longer purely the agent's.
    memory.created_by = "user"
    db.commit()
    return _serialize(memory)


@router.patch("/memories/{memory_id}", response_model=MemoryOut)
def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Edit a memory, re-checking it against admission control.

    The re-check is the point: without it, editing is a way to introduce
    exactly the business rules Rule 2 exists to keep out.
    """
    memory = _visible(db, user).filter(Memory.id == memory_id).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    new_title = body.title if body.title is not None else memory.title
    new_body = body.body if body.body is not None else memory.body

    verdict = admit(memory.type, new_title, new_body, requested_scope=memory.scope)
    if verdict.rejected:
        raise HTTPException(
            status_code=400,
            detail={"reason": verdict.reason, "belongs_in": verdict.belongs_in},
        )

    memory.title = new_title
    memory.body = new_body
    if body.why is not None:
        memory.why = body.why
    if body.how_to_apply is not None:
        memory.how_to_apply = body.how_to_apply
    memory.created_by = "user"
    db.commit()
    return _serialize(memory)


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Archive rather than hard-delete, so a mistaken removal is recoverable
    and the provenance trail survives."""
    memory = _visible(db, user).filter(Memory.id == memory_id).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    memory.status = "archived"
    db.commit()
    return {"ok": True, "id": memory_id, "status": "archived"}
