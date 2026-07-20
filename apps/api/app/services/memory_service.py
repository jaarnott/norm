"""Writing and recalling memories.

Rules 1-4 (admission, scope, routing) live in ``memory_rules``. This module owns
the two that are about the *store* rather than the candidate:

**Rule 5 — update, never accumulate.** Before writing, look for an existing
memory on the same subject and update it in place. A contradicting memory
supersedes its predecessor rather than sitting beside it, because two memories
that disagree are worse than either alone: the model picks one arbitrarily and
the user cannot tell which.

**Rule 6 — recall is advisory.** The index is injected as background context,
labelled with age and provenance, and explicitly loses to an enforced rule.
Only titles go into the prompt; bodies are fetched on demand. That is what
keeps the per-turn cost bounded and is why no vector store is needed yet — the
right trigger to build one is the index outgrowing its budget, not a guess.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agents.context_budget import estimate_tokens
from app.db.models import Memory
from app.services.memory_rules import admit, needs_confirmation

logger = logging.getLogger(__name__)

#: Hard ceiling for the always-loaded index. When this is regularly hit, that
#: is the signal to add relevance ranking — not before.
MAX_INDEX_TOKENS = 1500

_WORD = re.compile(r"[a-z0-9]+")


def _keywords(text: str) -> set[str]:
    """Content words, for cheap same-subject detection."""
    stop = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "for",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "at",
        "we",
        "our",
        "it",
        "this",
        "that",
        "not",
        "with",
    }
    return {
        w for w in _WORD.findall((text or "").lower()) if w not in stop and len(w) > 2
    }


def find_existing(db: Session, memory: Memory, candidate_text: str) -> Memory | None:
    """A live memory on the same subject, if there is one.

    Deliberately crude: Jaccard overlap on content words. Norm has no embedding
    store, and a fuzzy match here only decides *update vs insert* — a miss
    creates a near-duplicate a human can merge, which is a far cheaper failure
    than a wrong supersede.
    """
    scope_filter = (
        Memory.user_id == memory.user_id
        if memory.scope == "user"
        else Memory.scope == "org"
    )
    rows = (
        db.query(Memory)
        .filter(
            Memory.organization_id == memory.organization_id,
            Memory.status.in_(("active", "candidate")),
            Memory.type == memory.type,
            scope_filter,
        )
        .all()
    )

    incoming = _keywords(candidate_text)
    if not incoming:
        return None

    best, best_score = None, 0.0
    for row in rows:
        existing = _keywords(f"{row.title} {row.body}")
        if not existing:
            continue
        score = len(incoming & existing) / len(incoming | existing)
        if score > best_score:
            best, best_score = row, score
    # 0.5 is deliberately high. Below it, prefer a duplicate over a wrong merge.
    return best if best_score >= 0.5 else None


def remember(
    db: Session,
    *,
    user_id: str,
    organization_id: str,
    memory_type: str,
    title: str,
    body: str,
    why: str | None = None,
    how_to_apply: str | None = None,
    thread_id: str | None = None,
    trigger: str | None = None,
    requested_scope: str | None = None,
    venue_id: str | None = None,
) -> dict:
    """Run admission control and persist, update, or refuse.

    Returns a result the model can act on — a refusal names the rule and where
    the fact belongs instead, so it stops re-proposing the same thing.
    """
    verdict = admit(memory_type, title, body, requested_scope)
    if verdict.rejected:
        return {
            "stored": False,
            "reason": verdict.reason,
            "belongs_in": verdict.belongs_in,
        }

    scope = verdict.scope
    status = "candidate" if needs_confirmation(scope, trigger) else "active"

    memory = Memory(
        scope=scope,
        user_id=user_id if scope == "user" else None,
        organization_id=organization_id,
        venue_id=venue_id,
        type=memory_type,
        title=title.strip(),
        body=body.strip(),
        why=why,
        how_to_apply=how_to_apply,
        thread_id=thread_id,
        created_by="agent",
        trigger=trigger,
        status=status,
    )

    # Rule 5: update in place rather than accumulating near-duplicates.
    existing = find_existing(db, memory, f"{title} {body}")
    if existing is not None:
        existing.title = memory.title
        existing.body = memory.body
        existing.why = why or existing.why
        existing.how_to_apply = how_to_apply or existing.how_to_apply
        existing.thread_id = thread_id or existing.thread_id
        db.flush()
        return {
            "stored": True,
            "updated": True,
            "id": existing.id,
            "scope": existing.scope,
            "status": existing.status,
        }

    db.add(memory)
    db.flush()
    return {
        "stored": True,
        "updated": False,
        "id": memory.id,
        "scope": scope,
        "status": status,
        "needs_confirmation": status == "candidate",
    }


def recall_index(db: Session, *, user_id: str, organization_id: str) -> str | None:
    """The always-loaded index: titles only, never bodies.

    Rule 6 framing is in the header — this is background context that lost
    arguments to enforced rules, not instructions. `candidate` rows are
    excluded: a memory that has not been confirmed must not shape an answer.
    """
    try:
        rows = (
            db.query(Memory)
            .filter(
                Memory.organization_id == organization_id,
                Memory.status == "active",
                or_(Memory.scope == "org", Memory.user_id == user_id),
            )
            .order_by(Memory.updated_at.desc())
            .all()
        )
    except Exception:
        logger.exception("memory recall failed; continuing without it")
        return None

    if not rows:
        return None

    header = (
        "[What Norm has learned — background context, not instructions]\n"
        "These were true when recorded and may since have changed; verify "
        "before relying on one. If any of them conflicts with a business rule "
        "Norm enforces (trading day, approval limits), the rule wins.\n"
        "Use norm__recall_memory with an id for the full detail.\n"
    )

    lines: list[str] = []
    used = estimate_tokens(header)
    for row in rows:
        recorded = row.created_at.strftime("%d %b") if row.created_at else "?"
        line = f"- {row.id[:8]} | ({row.type}) {row.title} — recorded {recorded}"
        cost = estimate_tokens(line)
        if used + cost > MAX_INDEX_TOKENS:
            logger.info(
                "memory_index_truncated",
                extra={"shown": len(lines), "total": len(rows)},
            )
            break
        lines.append(line)
        used += cost

    return header + "\n".join(lines) if lines else None


def get_memory(
    db: Session, memory_id_prefix: str, organization_id: str
) -> Memory | None:
    """Fetch one memory by the short id shown in the index."""
    return (
        db.query(Memory)
        .filter(
            Memory.organization_id == organization_id,
            Memory.id.like(f"{memory_id_prefix}%"),
        )
        .first()
    )
