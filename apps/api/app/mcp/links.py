"""Open-in-Norm deep links.

Where a task can't be completed inside Claude — a draft to review, an approval
to grant, a rich editor — the MCP tool returns a link back into Norm. Uses
``settings.app_url`` (the established per-environment pattern).

Requires a small frontend addition: the app page must read ?thread= / ?doc= /
?report= from the URL and select accordingly. Without it the link still opens
Norm; it just lands on the app home rather than the specific object.
"""

from __future__ import annotations

from app.config import settings


def _base() -> str:
    return settings.app_url.rstrip("/")


def thread_link(thread_id: str) -> str:
    return f"{_base()}/app?thread={thread_id}"


def working_document_link(doc_id: str, thread_id: str | None = None) -> str:
    url = f"{_base()}/app?doc={doc_id}"
    if thread_id:
        url += f"&thread={thread_id}"
    return url


def report_link(report_id: str) -> str:
    return f"{_base()}/app?report={report_id}"
