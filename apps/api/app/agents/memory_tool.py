"""Anthropic's memory tool, backed by Norm: a conversation's notebook.

The tool is client-side. Claude requests one of six file operations under
``/memories`` and this module executes it against ``ThreadMemoryFile`` rows
scoped to the conversation, returning the text the tool-use contract expects
(docs.claude.com, "Memory tool"). Norm owns the storage, so Norm sets the
scope (the thread), the caps, and what the card shows.

Decided 3 Oct 2026 (plan: ~/.claude/plans/context-editing.md, Change 3):

- **The built-in tool, not a Norm-designed job document.** With it present,
  Anthropic injects "view your memory directory before anything else; record
  progress; assume interruption" at the top of every call. Norm's own note
  tools exist and were not used — `remember` 17 calls in 90 days,
  `update_thread_summary` once — because nothing made the model reach for
  them.
- **No prescribed format.** Tested twice with nothing said about format: the
  model created one markdown file before answering, headings per item plus a
  status line, and resumed from it in a fresh conversation. It never used a
  checklist, so a handler enforcing one would have rejected every write. The
  card renders whatever is here as markdown.
- **Scope is the conversation.** The docs' examples use memory as long-term
  knowledge; that is `remember`'s job, with admission rules and a
  candidate-until-confirmed lifecycle. This directory lives and dies with the
  thread.
- **The don't-write-twice guard lives in the write path, not here.** On
  resume the model re-judged one item; the notes are advisory to it.
"""

from __future__ import annotations

import datetime as _dt
import logging
import posixpath

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: The tool entry, verbatim from the docs. Anthropic-defined: no input_schema.
MEMORY_TOOL: dict = {"type": "memory_20250818", "name": "memory"}
ROOT = "/memories"
#: A notebook file. 64k is far past anything observed (a 14-line job wrote
#: 3.4k) and well under any result cap; the point is a bound, not a budget.
MAX_FILE_CHARS = 64_000
#: A `view` of a file is truncated here; the tool description already tells
#: Claude that long files are truncated and to page with view_range.
VIEW_MAX_CHARS = 16_000
MAX_FILES = 50


class MemoryPathError(ValueError):
    pass


def _safe_path(raw: object) -> str:
    """Canonicalise a path and refuse anything that could leave /memories.

    The docs' warning: `/memories/../../secrets.env` reaches outside the
    directory. Rejects traversal, backslashes and percent-encoding before
    normalising, then checks the normalised form is still inside the root.
    """
    path = str(raw or "").strip()
    if not path:
        raise MemoryPathError("a path is required")
    if "\\" in path or "%" in path or "\x00" in path:
        raise MemoryPathError(f"The path {path} is not allowed.")
    if ".." in path.split("/"):
        raise MemoryPathError(f"The path {path} is not allowed.")
    norm = posixpath.normpath(path)
    if norm != ROOT and not norm.startswith(ROOT + "/"):
        raise MemoryPathError(
            f"The path {path} is outside {ROOT}. All memory files live under {ROOT}."
        )
    return norm


def _files(db: Session, thread_id: str) -> dict:
    from app.db.models import ThreadMemoryFile

    rows = (
        db.query(ThreadMemoryFile)
        .filter(ThreadMemoryFile.thread_id == thread_id)
        .order_by(ThreadMemoryFile.path)
        .all()
    )
    return {r.path: r for r in rows}


def _numbered(text: str, start: int = 1) -> str:
    lines = text.split("\n")
    if lines and lines[-1] == "" and len(lines) > 1:
        lines = lines[:-1]
    return "\n".join(f"{i + start:6d}\t{line}" for i, line in enumerate(lines))


def _size(n: int) -> str:
    return f"{n / 1024:.1f}K" if n >= 100 else f"{n}B"


def _view(files, path: str, view_range) -> str:
    if path == ROOT or path not in files:
        inside = [p for p in files if p.startswith(path.rstrip("/") + "/")]
        if path != ROOT and not inside:
            return f"The path {path} does not exist. Please provide a valid path."
        listing = [f"{_size(sum(len(f.content) for f in files.values()))}\t{path}"]
        listing += [f"{_size(len(files[p].content))}\t{p}" for p in sorted(inside)]
        return (
            f"Here're the files and directories up to 2 levels deep in {path}, "
            "excluding hidden items and node_modules:\n" + "\n".join(listing)
        )
    content = files[path].content
    if view_range:
        try:
            a, b = int(view_range[0]), int(view_range[1])
        except (TypeError, ValueError, IndexError):
            return "Error: view_range must be [start_line, end_line]"
        lines = content.split("\n")
        a = max(1, a)
        b = len(lines) if b == -1 else min(b, len(lines))
        body = _numbered("\n".join(lines[a - 1 : b]), start=a)
        return f"Here's the content of {path} with line numbers:\n{body}"
    if len(content) > VIEW_MAX_CHARS:
        shown = content[:VIEW_MAX_CHARS]
        kept = shown.count("\n") + 1
        return (
            f"Here's the content of {path} with line numbers:\n{_numbered(shown)}\n"
            f"[truncated after {kept} lines of {content.count(chr(10)) + 1} — "
            "use view_range to read the rest]"
        )
    return f"Here's the content of {path} with line numbers:\n{_numbered(content)}"


def handle(params: dict, db: Session, thread_id: str | None) -> dict:
    """Execute one memory command. Returns the loop's internal-tool shape:
    ``{"success": bool, "data": {"text": <what Claude reads>}}``.

    The text follows the docs' reference strings so Claude's expectations
    hold (it is trained on them). Every error is a normal result with
    ``success: False`` — a bad path must never raise into the turn.
    """
    from app.db.models import ThreadMemoryFile

    def ok(text: str, changed: bool = False) -> dict:
        if changed:
            _announce(db, thread_id)
        return {"success": True, "data": {"text": text}}

    def err(text: str) -> dict:
        return {"success": False, "data": {"text": text}, "error": text}

    if not thread_id:
        return err("Error: memory is available only inside a conversation.")
    command = str(params.get("command") or "").strip()
    try:
        if command == "rename":
            old = _safe_path(params.get("old_path"))
            new = _safe_path(params.get("new_path"))
        else:
            path = _safe_path(params.get("path"))
    except MemoryPathError as exc:
        return err(f"Error: {exc}")

    files = _files(db, thread_id)
    now = _dt.datetime.now(_dt.timezone.utc)

    if command == "view":
        return ok(_view(files, path, params.get("view_range")))

    if command == "create":
        text = str(params.get("file_text") or "")
        if path == ROOT:
            return err(f"Error: {ROOT} is a directory.")
        if len(text) > MAX_FILE_CHARS:
            return err(
                f"Error: file is {len(text):,} characters; the limit is "
                f"{MAX_FILE_CHARS:,}. Keep the notebook to what the task needs."
            )
        if path not in files and len(files) >= MAX_FILES:
            return err(f"Error: at most {MAX_FILES} files; delete or merge one first.")
        # Claude's own tool description says create "creates or overwrites";
        # the docs allow either. Overwrite: a notebook is re-created, not
        # versioned, and an error here only costs the model a round trip.
        row = files.get(path)
        if row is None:
            row = ThreadMemoryFile(thread_id=thread_id, path=path, content=text)
            db.add(row)
        else:
            row.content = text
        row.updated_at = now
        db.flush()
        return ok(f"File created successfully at: {path}", changed=True)

    if command == "str_replace":
        row = files.get(path)
        if row is None:
            return err(
                f"Error: The path {path} does not exist. Please provide a valid path."
            )
        old_str = str(params.get("old_str") or "")
        new_str = str(params.get("new_str") or "")
        count = row.content.count(old_str) if old_str else 0
        if count == 0:
            return err(
                f"No replacement was performed, old_str `{old_str}` did not appear "
                f"verbatim in {path}."
            )
        if count > 1:
            lines = [
                str(i + 1)
                for i, line in enumerate(row.content.split("\n"))
                if old_str in line
            ]
            return err(
                "No replacement was performed. Multiple occurrences of old_str "
                f"`{old_str}` in lines: {', '.join(lines)}. Please ensure it is unique"
            )
        updated = row.content.replace(old_str, new_str, 1)
        if len(updated) > MAX_FILE_CHARS:
            return err(
                f"Error: the edit would take the file past {MAX_FILE_CHARS:,} characters."
            )
        at = row.content[: row.content.index(old_str)].count("\n")
        row.content = updated
        row.updated_at = now
        db.flush()
        lines = updated.split("\n")
        lo, hi = max(0, at - 2), min(len(lines), at + new_str.count("\n") + 3)
        snippet = _numbered("\n".join(lines[lo:hi]), start=lo + 1)
        return ok(f"The memory file has been edited.\n{snippet}", changed=True)

    if command == "insert":
        row = files.get(path)
        if row is None:
            return err(f"Error: The path {path} does not exist")
        lines = row.content.split("\n")
        try:
            at = int(params.get("insert_line"))
        except (TypeError, ValueError):
            return err("Error: insert_line must be an integer")
        if at < 0 or at > len(lines):
            return err(
                f"Error: Invalid `insert_line` parameter: {at}. It should be within "
                f"the range of lines of the file: [0, {len(lines)}]"
            )
        text = str(params.get("insert_text") or "").rstrip("\n")
        lines[at:at] = text.split("\n")
        updated = "\n".join(lines)
        if len(updated) > MAX_FILE_CHARS:
            return err(
                f"Error: the edit would take the file past {MAX_FILE_CHARS:,} characters."
            )
        row.content = updated
        row.updated_at = now
        db.flush()
        return ok(f"The file {path} has been edited.", changed=True)

    if command == "delete":
        if path == ROOT:
            return err(f"Error: {ROOT} itself cannot be deleted.")
        victims = [p for p in files if p == path or p.startswith(path + "/")]
        if not victims:
            return err(f"Error: The path {path} does not exist")
        for p in victims:
            db.delete(files[p])
        db.flush()
        return ok(f"Successfully deleted {path}", changed=True)

    if command == "rename":
        if old == ROOT or new == ROOT:
            return err(f"Error: {ROOT} itself cannot be renamed.")
        moving = [p for p in files if p == old or p.startswith(old + "/")]
        if not moving:
            return err(f"Error: The path {old} does not exist")
        for p in moving:
            target = new + p[len(old) :]
            if target in files:
                return err(f"Error: The destination {target} already exists")
        for p in moving:
            files[p].path = new + p[len(old) :]
            files[p].updated_at = now
        db.flush()
        return ok(f"Successfully renamed {old} to {new}", changed=True)

    return err(f"Error: unknown command {command!r}")


def notebook(db: Session, thread_id: str) -> list[dict]:
    """The conversation's files, for the thread API and the card."""
    return [
        {
            "path": r.path,
            "content": r.content,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in _files(db, thread_id).values()
    ]


def _announce(db: Session, thread_id: str) -> None:
    """Push the notebook to the browser so the card updates mid-turn."""
    try:
        from app.agents.tool_loop import _emit_event

        _emit_event({"type": "notebook", "files": notebook(db, thread_id)})
    except Exception:  # noqa: BLE001 — a UI event must never break a write
        logger.debug("notebook event not delivered", exc_info=True)
