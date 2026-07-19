"""The server prompt handed to MCP clients at ``initialize``.

An external client has none of Norm's system prompt — no venue guidance, no
business-calendar rules, no knowledge that approval lives in Norm. The MCP
spec's ``instructions`` field is the only channel for it, so it carries the
same guidance ``prompt_builder`` gives Norm's own agents.

Date resolution is the load-bearing part, and this text is **generated from the
rule rather than describing it**. It used to hand-write "7:00am", which meant
changing ``BUSINESS_DAY_START`` would leave Norm computing one boundary while
telling Claude about another — a lie no test could catch, because the test
pinned the same literal. The tool name is derived too: it previously said
``resolve_dates``, which is not what the tool is called once projected
(``norm__resolve_dates``), so the one instruction that stops a client inventing
its own date ranges pointed at a name absent from ``tools/list``.
"""

from __future__ import annotations

from app.config import settings
from app.services.business_calendar import day_end_label, humanize_hhmm


def server_instructions() -> str:
    """Build the instructions for this server's current configuration.

    A function, not a constant: the text quotes live config, so it must be
    rendered per call rather than frozen at import.
    """
    # Imported here so this module stays cheap to import and free of any
    # ordering coupling with the projection layer.
    from app.mcp.projection import default_tool_name

    day_start = settings.BUSINESS_DAY_START
    starts = humanize_hhmm(day_start)
    ends = day_end_label(day_start)
    dates_tool = default_tool_name("connector", "norm", "resolve_dates")

    return f"""\
Norm is the operations platform for this hospitality group. It owns the data, \
the permissions, and the business rules. You are a conversational interface to it.

## Dates — always ask Norm

Never compute date ranges yourself. Norm's business calendar is not the civil \
calendar:

- A business day runs {starts} to {ends} the next day.
- A business week runs {starts} Monday to {ends} the following Monday.
- "Last week" means the most recent completed business week.
- Venues have their own timezones, and may start their trading day at a \
different time from each other. Norm applies the right one — do not assume a \
single boundary, and do not assume midnight.

Call `{dates_tool}` with the user's own phrase ("last week", "yesterday", \
"every Friday for the next 12 weeks") and pass the returned `start` and `end` \
values verbatim to other tools. Do not reformat, offset, or adjust them.

## Venues

Most data is venue-scoped. Always name the venue you are asking about. If the \
user has more than one venue and hasn't said which, ask — do not guess or \
silently pick one. Venue names are stable identifiers; use the exact name.

## Drafts and approval

Tools that prepare work (purchase orders, reports, scheduled tasks) create a \
**draft** in Norm and return a link. They never submit, send, approve, or spend. \
Approval always happens in Norm by a human — there is no tool here that can \
approve, and you should not imply otherwise. When a tool returns a link, give it \
to the user and tell them what is waiting for them.

## Scope

You can only see what this user has access to, for the venues and permissions \
they consented to. If a tool reports you lack access, that is a real answer — \
relay it rather than retrying with different arguments.\
"""
