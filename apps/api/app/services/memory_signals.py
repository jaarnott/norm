"""Capture the corrections Norm was throwing away.

Two signals, both of which already existed in the product and neither of which
was ever read:

1. **Draft edits.** When a human changes an agent-generated purchase order or
   roster, the delta *is* the correction. It passed through ``_apply_op`` into
   ``pending_ops`` — a sync outbox that is drained and cleared once the
   connector accepts the change. The evidence was destroyed on success.
2. **Rejections with a note.** ``Approval`` has carried ``action="rejected"``
   and a free-text ``notes`` field all along, written in four places and read
   in none outside its own thread. A rejection note is a labelled correction in
   the user's own words.

They are treated differently on purpose. A rejection note is already a sentence
about what Norm should have done, so it can be proposed as a candidate memory
directly. A draft edit is a JSON diff — "quantity 5 → 8" says nothing durable on
its own — so it is banked as evidence and only proposed once the same field has
been corrected repeatedly. Proposing on a single edit would fill the review
queue with noise and teach people to click approve without reading.

Everything proposed here lands as a *candidate*: Rule 4 gives auto-write only to
the explicit and correction triggers, and an inference from behaviour is neither.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import MemorySignal

logger = logging.getLogger(__name__)

#: How many times the same field must be corrected before it is worth
#: proposing. Two could be coincidence; three is a habit.
REPEAT_THRESHOLD = 3


def _describe_ops(ops: list) -> str:
    """One line describing what the human changed."""
    parts = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        name = op.get("op", "edit")
        field = op.get("field") or op.get("path") or ""
        value = op.get("value")
        text = name if not field else f"{name} {field}"
        if value is not None and not isinstance(value, (dict, list)):
            text += f" → {value}"
        parts.append(text)
    return "; ".join(parts[:5]) or "edited the draft"


def record_draft_edit(
    db: Session,
    *,
    organization_id: str | None,
    user_id: str | None,
    thread_id: str | None,
    document_kind: str | None,
    ops: list,
) -> MemorySignal | None:
    """Bank a human's correction to an agent-drafted document.

    Never raises: this is observation, and losing a signal must never fail the
    edit the user actually asked for.
    """
    if not organization_id or not ops:
        return None
    try:
        signal = MemorySignal(
            organization_id=organization_id,
            user_id=user_id,
            thread_id=thread_id,
            kind="draft_edit",
            summary=f"Corrected {document_kind or 'a draft'}: {_describe_ops(ops)}",
            detail={"document_kind": document_kind, "ops": ops[:20]},
        )
        db.add(signal)
        db.flush()
        return signal
    except Exception:
        logger.exception("failed to record draft-edit signal; continuing")
        return None


def record_rejection(
    db: Session,
    *,
    organization_id: str | None,
    user_id: str | None,
    thread_id: str | None,
    notes: str | None,
) -> MemorySignal | None:
    """Bank a rejection that came with a reason.

    A rejection with no note carries no information beyond "no", so it is not
    recorded — there is nothing to learn from it.
    """
    if not organization_id or not (notes or "").strip():
        return None
    try:
        signal = MemorySignal(
            organization_id=organization_id,
            user_id=user_id,
            thread_id=thread_id,
            kind="rejection",
            summary=f"Rejected with reason: {notes.strip()[:300]}",
            detail={"notes": notes},
        )
        db.add(signal)
        db.flush()
        return signal
    except Exception:
        logger.exception("failed to record rejection signal; continuing")
        return None


def propose_from_rejection(db: Session, signal: MemorySignal) -> dict | None:
    """Turn a rejection note into a candidate memory.

    The note is already a sentence about what Norm should have done, so it goes
    through the normal admission rules unchanged. Most will be refused — a
    rejection reason is often about *this* order rather than a standing
    preference — and that is the rules working, not a failure.
    """
    from app.services.memory_service import remember

    notes = (signal.detail or {}).get("notes", "")
    if not notes.strip():
        return None

    result = remember(
        db,
        user_id=signal.user_id,
        organization_id=signal.organization_id,
        memory_type="correction",
        title=notes.strip()[:80],
        body=notes.strip(),
        why="Recorded from a rejected action, in the user's own words.",
        thread_id=signal.thread_id,
        trigger="rejection",
    )
    if result.get("stored") and result.get("id"):
        signal.promoted_to_memory_id = result["id"]
        db.flush()
    return result


def promote_repeated_edits(
    db: Session, *, organization_id: str, user_id: str | None = None
) -> list[dict]:
    """Propose candidates for corrections that keep recurring.

    Groups unpromoted draft-edit signals by what was changed and proposes one
    candidate per group that has hit REPEAT_THRESHOLD. A single edit stays
    banked: it is evidence, not a conclusion.
    """
    from app.services.memory_service import remember

    query = db.query(MemorySignal).filter(
        MemorySignal.organization_id == organization_id,
        MemorySignal.kind == "draft_edit",
        MemorySignal.promoted_to_memory_id.is_(None),
    )
    if user_id:
        query = query.filter(MemorySignal.user_id == user_id)

    groups: dict[str, list[MemorySignal]] = {}
    for signal in query.all():
        groups.setdefault(signal.summary, []).append(signal)

    proposed = []
    for summary, signals in groups.items():
        if len(signals) < REPEAT_THRESHOLD:
            continue
        result = remember(
            db,
            user_id=signals[0].user_id,
            organization_id=organization_id,
            memory_type="preference",
            title=summary[:80],
            body=(
                f"{summary}. Corrected {len(signals)} times, so Norm's default "
                "here is probably wrong."
            ),
            why="Inferred from repeated manual corrections to generated drafts.",
            thread_id=signals[0].thread_id,
            trigger="draft_edit",
        )
        if result.get("stored") and result.get("id"):
            for signal in signals:
                signal.promoted_to_memory_id = result["id"]
            db.flush()
        proposed.append(result)
    return proposed
