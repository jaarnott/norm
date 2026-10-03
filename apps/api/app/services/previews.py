"""What a write will change, shown before anyone approves it.

Until Oct 2026 the approval card showed a tool name and its raw JSON
arguments. Now a write that asks first previews itself, from live data and
without writing anything, in one shape every card renders the same way:

    {title, target, venue, changes: [{field, before?, after}], warnings, note}

How a write previews itself:

- **A consolidator** runs in preview mode (connectors/function_executor.py):
  every write it attempts is recorded and not sent, and when it calls
  ``preview(card)`` just before writing, the run stops and that card is the
  preview. A consolidator without a ``preview(...)`` call still can't write
  in preview mode — its recorded writes become the card, labelled.
- **A built-in** registers a previewer beside its handler
  (``@previews`` in agents/internal_tools.py).
- **Anything else** shows its parameters as labelled fields.

A preview can also settle the call without asking anyone: a call that would
fail ("refused" — its error goes straight back to the model) or would change
nothing ("done" — its result is the real result).

The card carries a fingerprint of what the person saw. The approved run checks
it against live data just before writing and stops if it moved, so an
approval never lands on values nobody looked at.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.connectors import spec_rows
from app.connectors.function_executor import fingerprint

logger = logging.getLogger(__name__)

#: Rows a card shows per call; the rest are counted, not listed.
MAX_ROWS = 40

_VENUE_KEYS = ("venue", "venue_name", "venue_id", "_all_venues")


@dataclass
class Outcome:
    #: "card" — ask the person; "done" — nothing to change, ``result`` is the
    #: real result; "refused" — it would fail, ``result`` says why.
    kind: str
    card: dict | None = None
    result: dict | None = None
    #: The exact params to run once approved, when the preview pinned some.
    run_params: dict | None = None


def new_memo() -> dict:
    """Reads shared by one batch of previews (a venue's units, its suppliers)."""
    return {"lock": threading.Lock(), "reads": {}}


def _humanise(key: str) -> str:
    import re

    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).replace("_", " ")
    words = " ".join(words.split())
    return words[:1].upper() + words[1:].lower() if words else ""


def _text(value, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def plain(value, limit: int = 300) -> str:
    """A value as a person reads it — never JSON."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, dict):
        parts = [
            f"{_humanise(k)}: {plain(v, 60)}"
            for k, v in value.items()
            if k not in _VENUE_KEYS and v not in (None, "", [], {})
        ]
        return _text("; ".join(parts) or "—", limit)
    if isinstance(value, list):
        if value and all(isinstance(v, dict) for v in value):
            return _text(
                f"{len(value)} entries: " + " | ".join(plain(v, 80) for v in value[:3]),
                limit,
            )
        return _text(", ".join(plain(v, 60) for v in value), limit)
    return _text(value, limit)


def title_for(action: str, tool_def: dict | None) -> str:
    """The card's heading for a tool: its approval label, else its name."""
    label = spec_rows.approval(tool_def).get("label")
    text = str(label or action.replace("_", " ")).strip()
    return text[:1].upper() + text[1:]


def normalise(
    card: dict | None, *, title: str, venue: str | None, source: str, fp: str | None
) -> dict:
    """Any tool's card in the one shape the approval card renders."""
    card = card if isinstance(card, dict) else {}
    changes = []
    for c in card.get("changes") or []:
        if not isinstance(c, dict) or not c.get("field"):
            continue
        row = {"field": _text(c["field"], 80), "after": plain(c.get("after"))}
        if "before" in c:
            row["before"] = plain(c.get("before"))
        changes.append(row)
    out = {
        "title": _text(card.get("title") or title, 120),
        "target": _text(card.get("target"), 160) or None,
        "venue": _text(card.get("venue") or venue, 80) or None,
        "changes": changes[:MAX_ROWS],
        "warnings": [_text(w, 300) for w in (card.get("warnings") or []) if w][:10],
        "note": _text(card.get("note"), 600) or None,
        "source": source,
        "fingerprint": fp,
    }
    if len(changes) > MAX_ROWS:
        out["more_changes"] = len(changes) - MAX_ROWS
    return out


def from_params(params: dict) -> dict:
    """A call with no preview of its own: what it was asked to do."""
    return {
        "changes": [
            {"field": _humanise(k), "after": v}
            for k, v in (params or {}).items()
            if k not in _VENUE_KEYS and v not in (None, "", [], {})
        ],
        "warnings": [
            "Norm can't show what this replaces — these are the values it will use."
        ],
    }


def from_writes(writes: list[dict]) -> dict:
    """A consolidator with no preview of its own: the writes it would send."""
    changes = []
    for w in writes:
        step = _humanise(w.get("action") or "")
        for k, v in (w.get("params") or {}).items():
            if k in _VENUE_KEYS or v in (None, "", [], {}):
                continue
            changes.append({"field": f"{step} · {_humanise(k)}", "after": v})
    return {
        "changes": changes,
        "warnings": [
            "Norm can't show what this replaces — these are the values it will send."
        ],
    }


def _venue_of(params: dict) -> str | None:
    v = (params or {}).get("venue") or (params or {}).get("venue_name")
    return v if isinstance(v, str) and v.strip() else None


def _is_consolidator(connector: str, action: str, tool_def: dict | None) -> bool:
    from app.agents.internal_tools import get_handler

    cfg = (tool_def or {}).get("consolidator_config")
    return (
        get_handler(connector, action) is None
        and isinstance(cfg, dict)
        and bool(cfg.get("function_code"))
    )


def runs_in_sandbox(connector: str, action: str, tool_def: dict | None) -> bool:
    """A consolidator's preview reaches outside systems (slow, parallel-safe);
    a built-in's is a database read on the caller's session."""
    from app.agents.internal_tools import get_previewer

    return get_previewer(connector, action) is None and _is_consolidator(
        connector, action, tool_def
    )


def preview_call(
    connector: str,
    action: str,
    params: dict,
    tool_def: dict | None,
    db: Session,
    thread_id: str | None,
    memo: dict | None = None,
) -> Outcome:
    """Preview one write. Never writes, never raises."""
    from app.services import caller_scope

    title = title_for(action, tool_def)
    venue = _venue_of(params)
    try:
        with caller_scope.use(caller_scope.for_thread(db, thread_id)):
            return _preview(
                connector, action, params, tool_def, db, thread_id, memo, title, venue
            )
    except Exception as exc:  # noqa: BLE001 — a preview must never sink the ask
        logger.warning("preview of %s.%s failed: %s", connector, action, exc)
        card = from_params(params)
        card["warnings"] = [
            f"Norm couldn't check this against live data ({_text(exc, 160)}). "
            "These are the values it will use."
        ]
        return Outcome(
            "card",
            card=normalise(card, title=title, venue=venue, source="params", fp=None),
        )


def _preview(connector, action, params, tool_def, db, thread_id, memo, title, venue):
    from app.agents.internal_tools import execute_consolidator, get_previewer

    previewer = get_previewer(connector, action)
    if previewer is not None:
        out = previewer(dict(params or {}), db, thread_id) or {}
        if out.get("error"):
            return Outcome(
                "refused", result={"success": False, "data": {}, "error": out["error"]}
            )
        if "done" in out:
            return Outcome("done", result={"success": True, "data": out["done"]})
        card = out.get("preview")
        return Outcome(
            "card",
            card=normalise(
                card,
                title=title,
                venue=venue,
                source="tool",
                fp=fingerprint(out["basis"] if "basis" in out else card),
            ),
            run_params=out.get("run_params"),
        )

    if _is_consolidator(connector, action, tool_def):
        cfg = {
            **tool_def["consolidator_config"],
            "action": action,
            "preview": True,
            "_preview_memo": memo if memo is not None else new_memo(),
        }
        run = execute_consolidator(cfg, dict(params or {}), db, thread_id)
        if run.get("preview"):
            shown = run["preview"]
            return Outcome(
                "card",
                card=normalise(
                    shown.get("card"),
                    title=title,
                    venue=venue,
                    source="tool",
                    fp=shown.get("fingerprint"),
                ),
            )
        if run.get("preview_writes"):
            return Outcome(
                "card",
                card=normalise(
                    from_writes(run["preview_writes"]),
                    title=title,
                    venue=venue,
                    source="writes",
                    fp=None,
                ),
            )
        data = run.get("data")
        error = run.get("error") or (
            data.get("error") if isinstance(data, dict) else None
        )
        if not run.get("success", True) or error:
            return Outcome(
                "refused", result={"success": False, "data": data, "error": error}
            )
        # It read, decided there was nothing to write, and stopped: that run
        # IS the call ("no differences — nothing written").
        return Outcome("done", result={"success": True, "data": data})

    return Outcome(
        "card",
        card=normalise(
            from_params(params), title=title, venue=venue, source="params", fp=None
        ),
    )


def changed_since(tc, db: Session) -> dict | None:
    """For a built-in: the card as it is NOW, if it no longer matches the one
    approved; None when it still matches (or there is nothing to compare).

    A consolidator does this check itself, inside the sandbox, at the moment
    before it writes (``expect_fingerprint``).
    """
    from app.agents.internal_tools import get_previewer

    shown = tc.preview or {}
    if shown.get("source") != "tool" or not shown.get("fingerprint"):
        return None
    previewer = get_previewer(tc.connector_name, tc.action)
    if previewer is None:
        return None
    out = previewer(dict(tc.input_params or {}), db, tc.thread_id) or {}
    if out.get("error") or "done" in out:
        return None  # the handler reports either itself
    card = out.get("preview")
    now = fingerprint(out["basis"] if "basis" in out else card)
    if now == shown["fingerprint"]:
        return None
    return normalise(
        card,
        title=shown.get("title") or tc.action,
        venue=shown.get("venue"),
        source="tool",
        fp=now,
    )


def expected_fingerprint(tc) -> str | None:
    """What a consolidator's approved run must still see before it writes."""
    shown = tc.preview or {}
    if shown.get("source") == "tool":
        return shown.get("fingerprint")
    return None
