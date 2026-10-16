"""Norm Functions' config rows: its team member, its tools, its skills.

Upserts, in the SHARED config DB:

- the ``functions`` agent_configs row — the team member the catalog's
  ``functions-agent`` row owns. sync_marketplace_catalog.py refuses to seed
  Functions' member and App until it exists ("unknown agent 'functions'").
  Display name, description and the persona the unified prompt adds to
  conversations under the member; no system prompt (per-agent prompts are
  only the pre-unified rollback, and Functions never had one).
- the ``norm_functions`` connector spec: one TOOL row per built-in in
  app/agents/functions_tools.py, each with a field_schema for every
  parameter (without one the model sends strings, and a list sent as a
  string is silently dropped) and its effect + approval policy taken from
  scripts/sync_approval_labels.py — one source for the labels, so the rows
  are labelled the moment they exist. The Functions App claims them
  (app/services/functions/catalog.py ``TOOLS``).
- the five functions-desk skills (playbooks) — the owner's claude.ai skills
  (inbox sweep, call vs email vs proposal, proposal build, email tone, the
  weekly runsheet), ported from Orbit's tools to Norm's. A skill is switched
  ON only once the Functions App claims it: an unclaimed playbook is offered
  to every organisation's agent (prompt_builder.blocked_playbook_slugs fails
  open), so between this run and the catalog seed they stay off.
- the ``mcp_capabilities`` rows that put Functions on Norm's MCP (the
  claude.ai "Norm" connector), by Norm's MCP policy: direct tools are READS
  only (the projection refuses anything that drafts or writes), and drafting,
  record changes and sends go through the five skills as WORKFLOW tools, which
  run Norm's own agent as the token's user — drafts land in Norm, and writes
  and customer sends wait on Norm's approval card (the workflow answers
  ``pending_approval`` with a link). Reads need ``mcp:functions:read``, the
  workflows ``mcp:functions:draft``; a workflow row is written only once its
  skill is claimed and on (the validator refuses a capability on a disabled
  playbook).
- Orbit's ``functions_*`` endpoints on ``cook_brothers_app`` marked
  superseded (``superseded_by`` / ``superseded_reason`` — the lifecycle in
  docs/tool-architecture-strategy.md §A; docs/lite-apps-architecture.md §3.3:
  "marked superseded, never deleted"). They are endpoints no App claims, so
  nothing reaches an LLM either way; the marker records where the work went.

Ship-day order — code first: the config DB is shared, and production must
know the handlers and the member before their rows appear.

  1. deploy (functions_tools.py registered from internal_tools, the member
     in app/agents/registry.MEMBERS, the web's member lists).
  2. this script:                  --dry-run, then for real — member, spec,
     tool rows (labelled), skills (off), MCP read capabilities, superseded
     markers.
  3. sync_marketplace_catalog.py:  --dry-run, then for real — creates
     ``functions-agent`` and ``functions`` (claiming the tools and skills).
  4. this script again — switches the claimed skills on and adds their MCP
     workflow capabilities.
  5. sync_approval_labels.py:      --dry-run — every norm_functions row
     reports "up to date" (labelled in step 2); apply only if it doesn't.

Idempotent. Committing reaches production immediately. Dry-run first.

Usage:
    uv run python scripts/sync_norm_functions.py --dry-run
    uv run python scripts/sync_norm_functions.py
"""

import argparse
import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.services.functions.catalog import (  # noqa: E402 — path set above
    APP,
    MEMBER,
    MEMBER_SLUG,
    SKILLS,
    TOOLS,
)
from app.services.functions.sanitize import HOSTED_FONTS  # noqa: E402

CONNECTOR = "norm_functions"

DESCRIPTION = (
    "Coordinates functions and events — enquiries, bookings, proposals and the "
    "venue inboxes — when asked in chat; it never runs on its own."
)

# Tone only, like every member's (scripts/sync_unified_prompt.py PERSONAS):
# rules and tool know-how live in the Norm prompt, the App note and the tools.
PERSONA = (
    "In here you're the functions coordinator: warm with clients, exact about "
    "dates, numbers and minimum spends, and quick to say what an enquiry needs next."
)

AGENT_CONFIG = {
    "display_name": MEMBER["name"],
    "description": DESCRIPTION,
    "persona": PERSONA,
    "enabled": True,
}

SPEC = {
    "display_name": "Norm Functions",
    "category": "internal",
    "execution_mode": "internal",
    "auth_type": "none",
    "auth_config": {},
    "base_url_template": None,
    "credential_fields": [],
    "api_documentation": (
        "Norm's own Functions module: enquiries, minimum-spend options, "
        "events, pre-order menus, proposals and the venue mailboxes. Every "
        "tool is a built-in (app/agents/functions_tools.py) acting as the "
        "person in the conversation; nothing here calls an outside system "
        "except the venue mailbox through Norm's outbox."
    ),
    "enabled": True,
}


# ---------------------------------------------------------------------------
# Tool rows (field_schema for every parameter)
# ---------------------------------------------------------------------------


def _s(description: str, **schema) -> dict:
    return {"type": schema.pop("type", "string"), "description": description, **schema}


def _enum(description: str, values) -> dict:
    return {"type": "string", "enum": list(values), "description": description}


def _strings(description: str, values=None) -> dict:
    items = {"type": "string", "enum": list(values)} if values else {"type": "string"}
    return {"type": "array", "items": items, "description": description}


BOOKING_TYPES = (
    "seated_dining",
    "group_booking",
    "large_event_partial",
    "large_event_exclusive",
)
STAGES = ("new", "replied", "quoted", "confirmed", "lost")
NEXT_ACTIONS = ("requested", "reply_overdue", "day_14", "day_7", "none")
EVENT_STATUSES = ("booked", "sent_to_client", "in_progress", "confirmed", "cancelled")
SOURCES = (
    "email",
    "web_form",
    "phone",
    "walk_in",
    "manual",
    "agent",
    "import",
    "other",
)

VENUE = _s("Venue name (as Norm lists it) or venue id.")
LIMIT = _s("Rows to return (default 25, at most 100).", type="integer")
OFFSET = _s("Rows to skip, for paging.", type="integer")
DATE = "A date as YYYY-MM-DD."
TIME = "A 24-hour time as HH:MM (venue-local)."

TOOL_ROWS: list[dict] = [
    {
        "action": "find_enquiries",
        "method": "GET",
        # A full page (100 rows) for the runsheet / inbox sweep fits.
        "max_result_chars": 50_000,
        "description": (
            "Find function enquiries and what each needs next, in one call. Every row "
            "carries the DERIVED stage (new, replied, quoted, confirmed, lost) and next "
            "action, worked out by Norm from the recorded facts — never compute your own: "
            "requested (the customer asked us to follow up on a date; parks everything "
            "else), reply_overdue (they wrote last, unanswered over 24h), day_14 (client "
            "silent 14+ days: call, then close — another email doesn't clear it), day_7 "
            "(our last reply 7+ days ago: one follow-up). Closed enquiries never need "
            "chasing. Use due_only for the work that's due today or overdue. Rows are "
            "slim — get_enquiry for one enquiry in full. Money is text plus cents."
        ),
        "required_fields": [],
        "optional_fields": [
            "venue",
            "stage",
            "next_action",
            "due_only",
            "booking_type",
            "source",
            "q",
            "received_from",
            "received_to",
            "date_from",
            "date_to",
            "include_archived",
            "sort",
            "limit",
            "offset",
        ],
        "field_schema": {
            "venue": _s(
                "Only this venue's enquiries (group-wide ones always match). Omit for all of yours."
            ),
            "stage": _strings("Only these stages.", STAGES),
            "next_action": _strings("Only these next actions.", NEXT_ACTIONS),
            "due_only": _s(
                "Only enquiries whose next action is due today or overdue.",
                type="boolean",
            ),
            "booking_type": _strings("Only these booking types.", BOOKING_TYPES),
            "source": _strings("Only enquiries from these sources.", SOURCES),
            "q": _s("Text in the subject, the contact's name, company or email."),
            "received_from": _s(f"Received on or after this date. {DATE}"),
            "received_to": _s(f"Received on or before this date. {DATE}"),
            "date_from": _s(f"Event (preferred) date window start. {DATE}"),
            "date_to": _s(f"Event (preferred) date window end. {DATE}"),
            "include_archived": _s("Include archived enquiries.", type="boolean"),
            "sort": _enum(
                "Row order (default created_desc). 'due' = most urgent next action first.",
                (
                    "created_desc",
                    "created_asc",
                    "due",
                    "preferred_date",
                    "last_activity",
                ),
            ),
            "limit": LIMIT,
            "offset": OFFSET,
        },
    },
    {
        "action": "get_enquiry",
        "method": "GET",
        "description": (
            "Everything about one enquiry in one read: stage and next action (with why), "
            "the contact and their other enquiries and bookings (duplicates at sister "
            "venues show here), the minimum-spend options (state, who must approve, "
            "whether Norm may edit them), food pre-order menus, the proposal (versions, "
            "last sent / viewed), linked threads, open drafts and recent activity. "
            "Identify it by enquiry_id, a linked gmail_thread_id, or the contact_email "
            "(their latest enquiry)."
        ),
        "required_fields": [],
        "optional_fields": ["enquiry_id", "gmail_thread_id", "contact_email"],
        "field_schema": {
            "enquiry_id": _s("The enquiry's id."),
            "gmail_thread_id": _s("A Gmail thread linked to the enquiry."),
            "contact_email": _s(
                "The customer's email — opens their most recent enquiry."
            ),
        },
    },
    {
        "action": "record_enquiry",
        "method": "POST",
        "description": (
            "Change an enquiry — asks the person first (an approval card shows the "
            "before → after). action: 'create' (the customer is matched by email or "
            "created; set booking_type deliberately, never guessed; optionally attach "
            "the thread it came from), 'update' (any listed field; an empty value "
            "clears it — e.g. follow_up_date ''), 'close' (lost, reason required), "
            "'reopen' (reason required, and ONLY when the client has said they want to "
            "proceed — a new date, a revised quote, 'we'd like to go ahead'; an "
            "explanation of why we lost it or 'maybe next year' is not a reopen), "
            "'confirm' (record the customer's yes by email/phone/in person: confirming "
            "an option — or a booking without a minimum spend — CREATES THE EVENT; only "
            "an approved option, or one in an area needing no approval, can be "
            "confirmed), 'decline' (record that they turned an option down). Stages "
            "are never set directly: they follow from these facts and from mail. "
            "Minimum-spend options are changed in build_proposal, never here."
        ),
        "required_fields": ["action"],
        "optional_fields": [
            "enquiry_id",
            "contact_name",
            "contact_email",
            "contact_phone",
            "contact_company",
            "venue",
            "booking_type",
            "subject",
            "guests",
            "date_from",
            "date_to",
            "start_time",
            "end_time",
            "ends_next_day",
            "follow_up_date",
            "follow_up_reason",
            "client_note",
            "notes",
            "source",
            "account_id",
            "gmail_thread_id",
            "reason",
            "option_id",
            "channel",
            "customer_email",
            "event_name",
            "area",
            "note",
        ],
        "field_schema": {
            "action": _enum(
                "What to do.",
                ("create", "update", "close", "reopen", "confirm", "decline"),
            ),
            "enquiry_id": _s("The enquiry (every action but create)."),
            "contact_name": _s("create: the customer's name."),
            "contact_email": _s(
                "create: the customer's email (matches an existing contact)."
            ),
            "contact_phone": _s("create: the customer's phone."),
            "contact_company": _s("create: the customer's company."),
            "venue": _s(
                "create/update: the venue name or id. Empty on update = group-wide (no venue yet)."
            ),
            "booking_type": _enum(
                "seated_dining = a table booking; group_booking = a reserved group area; "
                "large_event_partial = part of the venue; large_event_exclusive = whole "
                "venue. The two large_event types carry minimum spends.",
                BOOKING_TYPES,
            ),
            "subject": _s('Short description, e.g. "Casey\'s 40th".'),
            "guests": _s("Expected guests.", type="integer"),
            "date_from": _s(f"Preferred event date (or the first of a range). {DATE}"),
            "date_to": _s(f"Last date of a preferred range. {DATE}"),
            "start_time": _s(f"Preferred start. {TIME}"),
            "end_time": _s(f"Preferred end. {TIME}"),
            "ends_next_day": _s("The end time is after midnight.", type="boolean"),
            "follow_up_date": _s(
                f"Date we follow up (venue-local); '' clears it. Parks the other next actions. {DATE}"
            ),
            "follow_up_reason": _s(
                "Why we follow up then — what was promised or what remains."
            ),
            "client_note": _s(
                "A short, client-safe note (may appear on documents the customer sees)."
            ),
            "notes": _s(
                "Internal staff notes — never shown to the customer. Replaces the notes."
            ),
            "source": _enum("create: where it came from (default agent).", SOURCES),
            "account_id": _s(
                "create/update: the mailbox of a thread to attach (with gmail_thread_id)."
            ),
            "gmail_thread_id": _s("create/update: the thread to attach."),
            "reason": _s("close/reopen: why — for reopen, quote the client's words."),
            "option_id": _s(
                "confirm/decline: the minimum-spend option. Omit on confirm for a booking "
                "without a minimum spend (it takes the enquiry's preferred date, times and venue)."
            ),
            "channel": _enum(
                "confirm/decline: how the customer told us.",
                ("email", "phone", "in_person"),
            ),
            "customer_email": _s("confirm/decline: who said it, if not the contact."),
            "event_name": _s(
                "confirm: the event's name (default the enquiry subject)."
            ),
            "area": _s("confirm without an option: the area (name or id)."),
            "note": _s("confirm/decline: a note recorded with the customer's answer."),
        },
    },
    {
        "action": "find_events",
        "method": "GET",
        # A full page (100 rows) for the runsheet / inbox sweep fits.
        "max_result_chars": 40_000,
        "description": (
            "Booked events across a date window, with the derived status (booked → "
            "sent_to_client → in_progress → confirmed, or cancelled), money (minimum "
            "spend, food, expected spend) and totals. Use it to check the diary before "
            "any availability claim: other events on that date and the areas they hold "
            "(get_event each — a same-date event with no time overlap is a changeover "
            "note, not a clash). Rows are slim — get_event for one in full."
        ),
        "required_fields": [],
        "optional_fields": [
            "venue",
            "date_from",
            "date_to",
            "status",
            "include_cancelled",
            "booking_type",
            "q",
            "enquiry_id",
            "sort",
            "limit",
            "offset",
        ],
        "field_schema": {
            "venue": VENUE,
            "date_from": _s(f"Earliest event date. {DATE}"),
            "date_to": _s(f"Latest event date, inclusive. {DATE}"),
            "status": _strings("Only these statuses.", EVENT_STATUSES),
            "include_cancelled": _s("Include cancelled events.", type="boolean"),
            "booking_type": _strings("Only these booking types.", BOOKING_TYPES),
            "q": _s("Text in the event name or the contact's name."),
            "enquiry_id": _s("Only the events booked from this enquiry."),
            "sort": _enum(
                "Row order (default date_asc).",
                ("date_asc", "date_desc", "created_desc"),
            ),
            "limit": LIMIT,
            "offset": OFFSET,
        },
    },
    {
        "action": "get_event",
        "method": "GET",
        "description": (
            "One booked event in full (view 'details'): status and the client's three "
            "portal sections, the contact, area, times, payment and deposit, the run "
            "sheet, beverages (bar tab, who can authorise it), food (menus with dish "
            "counts and dietaries), security, entertainment, AV, money, recent changes. "
            "Or a printed sheet as Norm prints it (fields the venue switched off left "
            "out): 'full' (details + run sheet + beverages + food), 'run_sheet' (no "
            "food), 'food' (the kitchen's pre-order with dish counts). Allergy notes "
            "are health data: they're withheld unless include_allergies is set because "
            "the person needs them (e.g. the kitchen sheet)."
        ),
        "required_fields": ["event_id"],
        "optional_fields": ["view", "include_allergies"],
        "field_schema": {
            "event_id": _s("The event's id (find_events)."),
            "view": _enum(
                "What to return (default details).",
                ("details", "full", "run_sheet", "food"),
            ),
            "include_allergies": _s(
                "Include allergy notes — only when the person needs them.",
                type="boolean",
            ),
        },
    },
    {
        "action": "record_event",
        "method": "POST",
        "description": (
            "Staff-side changes to a booked event — asks the person first. action: "
            "'update' (name, area, date, times, description, payment method, deposit, "
            "bar tab amount, table plan), 'confirm_section' (the client confirmed a "
            "portal section by email/phone), 'skip_section' (the section doesn't need "
            "the client — note required), 'reopen_section', 'cancel' (reason "
            "required; also closes its enquiry as lost), 'reinstate'. The client's own "
            "answers (guests, notes, allergies, timings, bar-tab people, arrival "
            "drinks, food timing) are theirs to give on the portal: this tool refuses "
            "them — ask the client, or staff edit them on the event page. Amounts are "
            "in dollars."
        ),
        "required_fields": ["action", "event_id"],
        "optional_fields": [
            "name",
            "area",
            "event_date",
            "start_time",
            "end_time",
            "ends_next_day",
            "description",
            "payment_method",
            "deposit",
            "deposit_status",
            "bar_tab",
            "table_plan",
            "section",
            "reason",
            "note",
        ],
        "field_schema": {
            "action": _enum(
                "What to do.",
                (
                    "update",
                    "confirm_section",
                    "skip_section",
                    "reopen_section",
                    "cancel",
                    "reinstate",
                ),
            ),
            "event_id": _s("The event's id."),
            "name": _s("update: the event's name."),
            "area": _s("update: the area (name or id) at the event's venue."),
            "event_date": _s(f"update: the date. {DATE}"),
            "start_time": _s(f"update: start. {TIME}"),
            "end_time": _s(f"update: end. {TIME}"),
            "ends_next_day": _s("update: the end is after midnight.", type="boolean"),
            "description": _s("update: the staff description of the event."),
            "payment_method": _s("update: how they'll pay."),
            "deposit": _s(
                "update: deposit amount in dollars, e.g. 500.", type="number"
            ),
            "deposit_status": _s("update: deposit status, e.g. 'requested', 'paid'."),
            "bar_tab": _s("update: bar tab amount in dollars.", type="number"),
            "table_plan": _s("update: table plan notes."),
            "section": _enum("The portal section.", ("details", "beverages", "food")),
            "reason": _s("cancel (required) / reinstate: why."),
            "note": _s("A note for the event's change log (skip_section: required)."),
        },
    },
    {
        "action": "find_threads",
        "method": "GET",
        # A full page (100 rows) for the runsheet / inbox sweep fits.
        "max_result_chars": 60_000,
        "description": (
            "Threads in the venue mailboxes (Norm's mirror of Gmail): browse or search, "
            "newest first. Each row has its folder (enquiry / reservation / other, or "
            "unfiled — the sweep's queue), what it's linked to, whether the customer "
            "wrote last (awaiting_reply), and whether a Norm draft is waiting. Pass "
            "count:true for the total (a sweep's denominator). gmail_query (with "
            "account_id) runs a live Gmail search for older mail. Read a thread with "
            "read_thread before judging anything that matters."
        ),
        "required_fields": [],
        "optional_fields": [
            "venue",
            "account_id",
            "folder",
            "linked",
            "unread",
            "q",
            "since",
            "count",
            "gmail_query",
            "limit",
            "offset",
        ],
        "field_schema": {
            "venue": VENUE,
            "account_id": _s("One mailbox (account_id from a thread row)."),
            "folder": _enum(
                "Only this folder; 'unfiled' = not yet actioned.",
                ("enquiry", "reservation", "other", "unfiled"),
            ),
            "linked": _s(
                "true = only threads linked to an enquiry or event; false = only unlinked.",
                type="boolean",
            ),
            "unread": _s("Only threads with unread mail.", type="boolean"),
            "q": _s("Text in the subject, snippet or correspondents."),
            "since": _s("Only threads with mail after this ISO date or date-time."),
            "count": _s("Also count every matching thread.", type="boolean"),
            "gmail_query": _s(
                "A live Gmail search (Gmail's syntax) for mail older than the mirror; needs account_id."
            ),
            "limit": LIMIT,
            "offset": OFFSET,
        },
    },
    {
        "action": "read_thread",
        "method": "GET",
        "description": (
            "One thread as a conversation: who wrote what, when, in which direction "
            "(from customer / from us / internal / auto-reply), what it's linked to, "
            "the folder, Norm drafts on it. detail 'digest' (default) gives each "
            "message's snippet; 'full' gives the live bodies as plain text — always "
            "read 'full' before drafting against a thread or judging a money thread."
        ),
        "required_fields": ["account_id", "gmail_thread_id"],
        "optional_fields": ["detail", "max_chars"],
        "field_schema": {
            "account_id": _s("The mailbox (from find_threads)."),
            "gmail_thread_id": _s("The thread."),
            "detail": _enum("digest (default) or full.", ("digest", "full")),
            "max_chars": _s(
                "full: characters per message (default 4000).", type="integer"
            ),
        },
    },
    {
        "action": "file_thread",
        "method": "POST",
        "description": (
            "Everything you do TO a thread, in one call — asks the person first: file "
            "it (folder enquiry / reservation / other, or unfiled; mirrored to the "
            "mailbox's 'Actioned - …' Gmail label), link it to an enquiry or event, "
            "unlink it, or create a new enquiry from it (the customer is whoever wrote "
            "in — pass contact_email for a web-form or marketplace lead whose sender "
            "is a bot — and set booking_type deliberately). Filing doesn't archive the "
            "thread in Gmail."
        ),
        "required_fields": ["account_id", "gmail_thread_id"],
        "optional_fields": [
            "folder",
            "link_enquiry_id",
            "link_event_id",
            "unlink",
            "create_enquiry",
            "booking_type",
            "subject",
            "venue",
            "contact_name",
            "contact_email",
        ],
        "field_schema": {
            "account_id": _s("The mailbox."),
            "gmail_thread_id": _s("The thread."),
            "folder": _enum(
                "File it here.", ("enquiry", "reservation", "other", "unfiled")
            ),
            "link_enquiry_id": _s("Link the thread to this enquiry."),
            "link_event_id": _s("Link the thread to this event."),
            "unlink": _s("End the thread's current links.", type="boolean"),
            "create_enquiry": _s(
                "Create a new enquiry from this thread and link it.", type="boolean"
            ),
            "booking_type": _enum("create_enquiry: the booking type.", BOOKING_TYPES),
            "subject": _s("create_enquiry: the subject (default the thread's)."),
            "venue": _s("create_enquiry: the venue (default the mailbox's)."),
            "contact_name": _s("create_enquiry: the customer's name."),
            "contact_email": _s("create_enquiry: the customer's real address."),
        },
    },
    {
        "action": "draft_email",
        "method": "POST",
        "description": (
            "Write a draft email in Norm — never sends. It waits in the Functions Inbox "
            "(on the thread, or as a new email) for staff to review and send, or for "
            "the person to approve send_email / send_proposal on a card. kind 'email' "
            "for a plain reply (no prices — a price belongs in a proposal); kind "
            "'proposal' for a proposal's covering message (put {{quote_link}} where the "
            "link goes, or Norm adds it after the message). Write the message only — "
            "no sign-off: the mailbox signature and the quoted history are added when "
            "it's sent. Body as simple HTML (<p>, <ul><li>, <a href>) so links show as "
            "text. About an enquiry or event, it replies on the record's linked thread "
            "unless you name one. draft_id revises (or discards) an existing draft; "
            "hand_to_gmail puts it in the venue's Gmail as a draft (then it's sent "
            "from Gmail and locked in Norm)."
        ),
        "required_fields": [],
        "optional_fields": [
            "draft_id",
            "enquiry_id",
            "event_id",
            "account_id",
            "gmail_thread_id",
            "kind",
            "to",
            "cc",
            "bcc",
            "subject",
            "body",
            "discard",
            "hand_to_gmail",
        ],
        "field_schema": {
            "draft_id": _s("Revise this open Norm draft instead of writing a new one."),
            "enquiry_id": _s("The enquiry it's about."),
            "event_id": _s("The event it's about."),
            "account_id": _s("The mailbox (with gmail_thread_id for a reply)."),
            "gmail_thread_id": _s("Reply on this thread."),
            "kind": _enum(
                "email (default) or proposal (needs enquiry_id).", ("email", "proposal")
            ),
            "to": _strings(
                "Recipients. Omit on a reply to answer whoever wrote; set it for web-form "
                "and marketplace leads whose reply-to is a bot."
            ),
            "cc": _strings("Cc recipients."),
            "bcc": _strings("Bcc recipients."),
            "subject": _s("Subject (a reply keeps the thread's)."),
            "body": _s("The message, as simple HTML. No sign-off."),
            "discard": _s("With draft_id: discard that draft.", type="boolean"),
            "hand_to_gmail": _s(
                "Also put the draft in the venue mailbox's Gmail (only when asked).",
                type="boolean",
            ),
        },
    },
    {
        "action": "send_email",
        "method": "POST",
        "description": (
            "Send a Norm email draft to the customer — ALWAYS asks the person on an "
            "approval card first (it can't be switched to 'always allow'). Sends "
            "exactly the draft (draft_email writes it); if the draft quotes a price "
            "with no proposal sent it is refused — send a proposal instead, or, only "
            "when the person gives a reason to send it anyway, pass "
            "price_override_reason (recorded). Proposal drafts go through send_proposal."
        ),
        "required_fields": ["draft_id"],
        "optional_fields": ["price_override_reason"],
        "field_schema": {
            "draft_id": _s("The email draft to send."),
            "price_override_reason": _s(
                "Only for a priced email with no proposal: the reason the PERSON gave."
            ),
        },
    },
    {
        "action": "build_proposal",
        "method": "POST",
        "description": (
            "Build or revise an enquiry's proposal in one validated batch — never "
            "sends. options: add / change / withdraw minimum-spend options (area, "
            "amount in dollars, date, times, label) — every new option names its area; "
            "alternatives are separate options, never summed; anything APPROVED or "
            "booked is read-only to Norm (add a new option instead). New options are "
            "'proposed' until the venue's approver approves them in Norm. menus: add a "
            "pre-order menu from a venue template with a guest count (set menus are "
            "split by their default %), change guests, set dish quantities, or remove "
            "one. The proposal itself is free HTML + CSS: the first call starts it from "
            "the venue's brand template (after your options and menus, so it shows "
            "them); then edit with replacements (exact find/replace) or full html/css; "
            "restart rebuilds it from the brand, discarding hand edits. It's sanitised "
            "on save (no scripts, forms, remote images or remote fonts; images only "
            "from the venue image library). Fonts Norm hosts, usable by name with a "
            f"fallback: {', '.join(HOSTED_FONTS)}; any other font shows only on "
            "devices that have it. Norm adds the customer's Confirm/Decline buttons from the "
            "options below the document — never put buttons in the HTML. Returns the "
            "money check: any figure in the document that matches nothing on record."
        ),
        "required_fields": ["enquiry_id"],
        "optional_fields": [
            "options",
            "menus",
            "brand_venue",
            "restart",
            "html",
            "css",
            "replacements",
            "include_text",
            "include_html",
        ],
        "field_schema": {
            "enquiry_id": _s("The enquiry."),
            "options": {
                "type": "array",
                "description": "Minimum-spend options to add (no option_id), change, or withdraw.",
                "items": {
                    "type": "object",
                    "properties": {
                        "option_id": _s("An existing option to change or withdraw."),
                        "venue": _s("Venue (default the enquiry's)."),
                        "area": _s("Area name or id — required for a new option."),
                        "label": _s("Label, e.g. 'The Deck — exclusive'."),
                        "amount": _s(
                            "Minimum spend in dollars, e.g. 5500.", type="number"
                        ),
                        "date": _s(DATE),
                        "start_time": _s(TIME),
                        "end_time": _s(TIME),
                        "ends_next_day": _s("Ends after midnight.", type="boolean"),
                        "notes": _s("Internal notes on the option."),
                        "withdraw": _s("Withdraw this option.", type="boolean"),
                        "reason": _s("Why it's withdrawn."),
                    },
                },
            },
            "menus": {
                "type": "array",
                "description": "Pre-order menus to add (template_id) or change (menu_id).",
                "items": {
                    "type": "object",
                    "properties": {
                        "template_id": _s(
                            "Add this venue menu template (get_venue_profile)."
                        ),
                        "menu_id": _s("An existing menu on the enquiry."),
                        "guest_count": _s("Guests the menu is for.", type="integer"),
                        "distribute": _s(
                            "Set menus: split dishes by default % (default true).",
                            type="boolean",
                        ),
                        "quantities": {
                            "type": "object",
                            "description": "Dish name (or item id) -> quantity.",
                            "additionalProperties": {"type": "integer"},
                        },
                        "remove": _s("Remove this menu.", type="boolean"),
                    },
                },
            },
            "brand_venue": _s("Whose brand to start from (a group-wide enquiry)."),
            "restart": _s(
                "Rebuild the document from the brand template.", type="boolean"
            ),
            "html": _s("Replace the whole document body HTML."),
            "css": _s("Replace the document CSS."),
            "replacements": {
                "type": "array",
                "description": "Targeted edits to the current HTML, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "find": _s("Exact text/HTML to find."),
                        "replace": _s("Replacement."),
                        "all": _s(
                            "Replace every occurrence (default first only).",
                            type="boolean",
                        ),
                    },
                    "required": ["find", "replace"],
                },
            },
            "include_text": _s(
                "Return the document's visible text, to check it.", type="boolean"
            ),
            "include_html": _s("Return the document HTML and CSS.", type="boolean"),
        },
    },
    {
        "action": "send_proposal",
        "method": "POST",
        "description": (
            "Send a proposal to the customer — ALWAYS asks the person on an approval "
            "card first (it can't be switched to 'always allow'). Sends a proposal "
            "draft (draft_email kind 'proposal' — the covering message): freezes the "
            "proposal and the options on offer, puts the quote link in the email, and "
            "the enquiry becomes quoted when Gmail confirms. The card shows the "
            "options, what the customer can confirm, and the money check."
        ),
        "required_fields": ["draft_id"],
        "optional_fields": [],
        "field_schema": {"draft_id": _s("The proposal draft (the covering email).")},
    },
    {
        "action": "get_venue_profile",
        "method": "GET",
        "description": (
            "How a venue runs functions — read before quoting or replying: policies "
            "and house rules, areas (capacities, which booking types they suit, "
            "whether a minimum spend needs approval, minimum-spend notes), who "
            "approves, customer menu links (send these exact URLs — never invent "
            "one), pre-order menu templates with dishes and prices, the proposal "
            "brand, and the reply templates (the venue's reply playbooks) for a "
            "booking_type and stage — stage first_reply = a new enquiry, follow_up = "
            "a reply in a conversation already going. date adds the venue's Loaded "
            "budget and last year's same-weekday sales for that trading day (for "
            "deriving a minimum spend)."
        ),
        "required_fields": [],
        "optional_fields": ["venue", "booking_type", "stage", "date"],
        "field_schema": {
            "venue": _s("Venue name or id (needed unless you have one venue)."),
            "booking_type": _enum(
                "Narrow areas and reply templates to this booking type.", BOOKING_TYPES
            ),
            "stage": _enum(
                "Reply templates for this stage.", ("first_reply", "follow_up")
            ),
            "date": _s(f"Add the sales reference for this trading day. {DATE}"),
        },
    },
]


def tool_rows() -> list[dict]:
    """The rows as installed: each labelled from sync_approval_labels."""
    from scripts.sync_approval_labels import labels

    known = labels()
    rows = []
    for row in TOOL_ROWS:
        key = f"{CONNECTOR}.{row['action']}"
        if key not in known:
            raise SystemExit(f"{key} has no effect label in sync_approval_labels.py")
        rows.append({**copy.deepcopy(row), **copy.deepcopy(known[key])})
    actions = [f"{CONNECTOR}.{r['action']}" for r in rows]
    if sorted(actions) != sorted(TOOLS):
        raise SystemExit("norm_functions rows and catalog.TOOLS disagree")
    return rows


# ---------------------------------------------------------------------------
# Superseded Orbit endpoints
# ---------------------------------------------------------------------------

#: Orbit's functions_* endpoint on cook_brothers_app -> the Norm tool doing
#: that work now (None: done on a Norm page, not by a tool).
SUPERSEDED = {
    "functions_find_enquiries": "find_enquiries",
    "functions_get_enquiry": "get_enquiry",
    "functions_record_enquiry": "record_enquiry",
    "functions_find_events": "find_events",
    "functions_get_event": "get_event",
    "functions_record_event": "record_event",
    "functions_find_inbox_threads": "find_threads",
    "functions_read_thread": "read_thread",
    "functions_triage_thread": "file_thread",
    "functions_compose_email": "draft_email",
    "functions_send_email": "draft_email",
    "functions_build_proposal": "build_proposal",
    "functions_send_proposal": "send_proposal",
    "functions_venue_playbook": "get_venue_profile",
    "functions_manage_quote_brand": None,
}


def superseded_fields(action: str) -> dict:
    target = SUPERSEDED[action]
    if target is None:
        return {
            "superseded_reason": (
                "Norm Functions replaced Orbit Functions; the proposal brand is "
                "edited on Norm's Functions Settings page."
            )
        }
    return {
        "superseded_by": f"{CONNECTOR}__{target}",
        "superseded_reason": "Norm Functions replaced Orbit Functions (native module, Oct 2026).",
    }


# ---------------------------------------------------------------------------
# Skills — the owner's claude.ai functions-desk skills, ported to Norm
# ---------------------------------------------------------------------------

_PORT_NOTE = (
    "Ported from the owner's claude.ai skill (written for Orbit's tools) to Norm "
    "Functions, Oct 2026 — rules and formats kept; tools and system behaviour "
    "translated. It runs when someone asks in chat, never on a schedule."
)

INBOX_SWEEP = f"""# Functions Inbox Sweep

_{_PORT_NOTE}_

A sweep is read-and-report only until the user explicitly asks to process.
Nothing is filed, replied to, created or changed unless asked. Even during
processing, escalation items stay held for a human decision.

## Step 0 — Ask the user which mode

Before doing anything, ask:

> "Quick sweep (classify and present only), full sweep (read threads and draft
> replies for EMAIL-badge items), or full + proposals (full sweep, then build the
> proposal for every PROPOSAL-badge thread)?"

Wait for their answer. Quick mode runs Steps 1-3 only. Full mode adds Step 4. Full
+ proposals adds Step 4b. The proposal decision is the user's at mode selection —
never build proposals in a sweep the user did not opt into, and never skip them in
a sweep they did.

## Step 1 — Gather (read-only)

Pull the whole queue: `find_threads` with the venue, `folder: "unfiled"` and
`count: true` (page with `offset` until `has_more` is false). In Norm, "unfiled"
is the inbox — a thread leaves it when it is filed. `total` is the denominator —
classify exactly that many threads. Where a snippet is not enough to classify
confidently (any money thread, anything ambiguous), call `read_thread` (detail
`digest`) before deciding. Reading is always allowed; it changes nothing.

## Step 2 — Classify every thread into exactly one group

Classify by what the email IS, never by whether a reply is owed.

1. **Other** — newsletters, marketing, spam, automated notifications, internal
   staff mail, anything unrelated to functions.
2. **Reservation** — under ~25 guests with no exclusivity, private space or minimum
   spend need. An occasion on the form (birthday, shower, farewell) does NOT make it
   an enquiry, and neither does pre-order interest — the pre-order is the upsell
   offered inside the booking reply, never the classifier. Route: ResDiary booking +
   reserved area, always with a pre-order offer in the same reply. Becomes a New
   Enquiry when any of: ~25+ guests, exclusivity or dedicated-space language, or
   anything needing an event record (bar tab mechanics, staggered food, minimum
   spend, runsheet). Note the trade-off: reservation-routed web leads fall out of
   the enquiry conversion numbers.
3. **New Enquiry** — a request for a new event, function or group booking. May
   arrive as a new thread, a fresh request inside an existing thread, or a reply to
   the venue's initial template.
4. **Follow Up** — a reply on an in-flight enquiry or event already being handled,
   including losses, confirmations, and messages needing no reply.

Edge rules: a loss notice is a Follow Up (the enquiry gets closed, not just the
email filed). Internal mail on a client thread classifies by the thread's owner and
is flagged as a hygiene issue.

For each thread, also determine the **action badge** by running the decision test
from the `functions_call_email_proposal` skill against what is known from the
snippet and classification:
- **PROPOSAL** — pax + format known, price can travel
- **CALL** — looping constraint, post-quote silence, buying signal cold, event
  imminent, client walking away
- **EMAIL** — everything else: acks, holds, closes, simple follows

## Step 3 — Build the board (fixed format)

Present the board in chat in exactly this structure. Content goes in; the
structure, section labels and action lines never change.

```
# Inbox Sweep — <Venue name>
<date and time> | <N> threads | <sweep mode> | target: zero
**Tonight:** <tonight's events>            ← only when there are any (find_events today)
<processed> of <N> processed

## 1  Other — no functions reply · <count>
_Label Actioned - All Other, remove from inbox, no reply_
<cards>

## 2  Reservation · <count>
_Label Actioned - Reservation, remove from inbox, action the booking_
<cards>

## 3  New Enquiry · <count>
_Label Actioned - Enquiry, remove from inbox, record the enquiry and reply_
<cards>

## 4  Follow Up · <count>
_Label Actioned - Enquiry, remove from inbox, progress the thread_
<cards>

---
Sweep mode: <mode> | <N> threads classified, <processed> processed | Nothing actioned above unless explicitly processed. Escalations held for human decision.
```

Each card:

```
**<Name> · <occasion> · <pax> · <date>** `EMAIL|CALL|PROPOSAL` `<booking type>`
<details><summary>View email</summary>

<the verbatim last email — not a summary>
</details>

<What: a 2-3 sentence narrative of the situation>
**Next actions:**
- <what is owed>

<details><summary>Draft reply — review before sending</summary>

<full mode, EMAIL badge only: the draft text>
</details>
```

- The booking-type badge names the reply-template family the draft came from
  (e.g. `group booking`); omit it on Other, Reservation and no-reply done items.
- Done items are struck through (`~~…~~`); held items carry `HOLD`, moved items
  `MOVED`, tonight's events `TONIGHT`.

**Quick mode stops here.** Present the board and offer to process.

## Step 4 — Full mode: read threads and generate drafts

For every **EMAIL badge** thread in New Enquiry and Follow Up:

1. `read_thread` with detail `full` — never digest for a thread you are about to
   draft against. Confirmation threads (e.g. ResDiary) need the full history.
2. Take the `booking_type` from the linked enquiry (`get_enquiry`), or derive it
   from the thread content if no enquiry exists yet. The reply stage is
   `first_reply` for a New Enquiry and `follow_up` for a Follow Up.
3. `get_venue_profile` with the venue, `booking_type` and `stage` — it returns
   only the matching reply templates (the venue's reply playbooks).
4. Pick the closest template body as the starting draft.
5. Fill every `[BRACKET]` placeholder from the thread and system data (date from the
   enquiry, area from the event record or the venue profile, minimum spend from the
   enquiry's options or the venue profile). Never fill from memory.
   `draft_email` fills `{{client_name}}` and `{{sender_name}}` itself and
   reports any blank left; Norm refuses to send a draft that still has one.
6. Apply the `functions_email_tone` skill throughout — no holding phrases, real
   fees, close ask, hyphens not em dashes.
7. Show the draft in the card's draft block. Flag any placeholder that could not be
   filled (e.g. `[SPEND — check with James]`).

For every **PROPOSAL badge** thread:

- Read the thread (digest is enough).
- Note in the card what the proposal needs: guest count, format, date, space —
  "Proposal required: 75 guests, Baxter's exclusive, Sat 28 Nov — trigger the
  `functions_proposal_build` skill."
- Do not build the proposal during the sweep. Proposal Build is a full workflow
  (system checks, minimum spend derivation, document review) and is triggered
  deliberately per enquiry.

For every **CALL badge** thread:

- Note in the card what the call needs to cover: the specific question to resolve,
  the commitment to reach for, and the closure outcome if no answer.

For **Other** and **Reservation** threads and **done** items:

- No thread read. Narrative from the snippet is enough.

## Step 4b — Full + proposals mode: build the PROPOSAL-badge items

Only when the user chose "full + proposals" at Step 0. Present the board FIRST
(classification value lands fast), then run the `functions_proposal_build` skill
for each PROPOSAL-badge thread, in priority order (imminent dates and awaiting-us
items first).

Per enquiry:
- Enquiry record first: `record_enquiry` (action `create`, with the thread attached)
  or `file_thread` with `create_enquiry: true` — both ask the person on an approval
  card; batch them so one card covers the sweep. Updates to existing enquiries are
  `record_enquiry` action `update`.
- Derive minimum spends per the saved method; options land as "proposed" awaiting
  the venue approver's approval in Norm — say so.
- Covering email written per `functions_email_tone` as a proposal draft; document
  built from the venue's brand template (custom HTML only where the standard
  document genuinely does not fit, e.g. a formal quote).
- Read the document's text (`build_proposal` with `include_text`) and check it.
  Write anything the customer sees as if the client will read it.
- Nothing sends. Date collisions and floors below the settling threshold are built
  anyway but flagged as held before send.

Close with a per-enquiry report: what was built, the totals, the enquiry links, and
exactly what needs the user (approvals, reopens, collision calls) before Ash sends.

## Step 5 — Processing (only on explicit instruction)

When, and only when, the user says to process:

- One `file_thread` per thread with its `folder` (each asks the person — one
  approval card covers the batch). Folder mapping: Other → `other`, Reservation →
  `reservation`, New Enquiry and Follow Up → `enquiry`. Filing applies the
  mailbox's "Actioned - …" Gmail label; Norm does not archive the thread in Gmail.
- New Enquiries: `file_thread` with `create_enquiry: true` (or `record_enquiry`
  create) with `booking_type` set deliberately (never blank, never guessed from the
  subject alone). Existing ones: `link_enquiry_id`.
- Reply drafts from Step 4: verify the recipient (marketplace leads like
  Sharedspace have a bot as reply-to — pass the client's real address in `to`),
  then `draft_email` on the thread. One draft per client: revise an existing Norm
  draft (`draft_id`) rather than adding a second; discard superseded ones.
- Prices never travel in a plain email draft — any priced reply goes through the
  `functions_proposal_build` skill.
- Availability claims require a `find_events` check on that date and space before
  the draft is written. Never write "looking good" without checking the calendar.
- Drafts wait in the Functions Inbox: staff send them there, or the person approves
  `send_email` on a card. Never send on your own initiative.

Always held for the user even during processing: pricing or minimum spend decisions
not yet approved, holds on contested dates, guest-count growth that changes a space
or floor, same-day event changes, anything touching money already quoted.
Escalations are named with a recommendation, never silently kept.

## Quality bar

- Every thread in exactly one group; the count reconciles to `total`.
- Nothing actioned in Steps 1-3; nothing ever sent, only drafted.
- No money thread judged from a snippet.
- Full mode: no draft written without reading the full thread first.
- Full mode: no placeholder left blank without flagging it explicitly.
- Every availability claim checked against the diary, not assumed.
- Every escalation carries a recommendation.
- Misclassifications from a previous sweep corrected out loud.

## What changed from the Orbit version

- Tools: `find_inbox_threads` → `find_threads` (the queue is `folder: "unfiled"`;
  `count: true` gives `total`); `triage_thread` → `file_thread`;
  `functions_venue_playbook` reply playbooks → `get_venue_profile` reply templates
  (classification new_enquiry/follow_up → stage first_reply/follow_up; event_type →
  booking_type); `functions_compose_email` → `draft_email`.
- The board: Orbit's HTML template becomes the same structure in Norm's chat
  (markdown, with collapsible View email / Draft blocks); the template's CSS is not
  used — it would restyle Norm's page.
- Filing doesn't archive in Gmail (Norm files by label; the queue is "unfiled").
- Classification isn't stored on the enquiry: its stage (new until we reply) picks
  first reply vs follow-up; booking type is stored and set deliberately.
- Every filing, enquiry creation and update asks the person on an approval card;
  drafts live in Norm (they can be handed to Gmail on request) and are sent only by
  staff or from an approval card.
- Internal mail stays off client threads: file it as Other (Orbit's
  `mailbox_handle_thread` has no Norm equivalent).
- Template names (`{{client_name}}`, `{{sender_name}}`) are filled by
  `draft_email`; a send is refused while any other blank is left (`[DATE]`,
  `[SPEND — check with James]`).
"""

CALL_EMAIL_PROPOSAL = f"""# Call vs Email vs Proposal — CBHG Functions

_{_PORT_NOTE}_

Purpose: decide the right next move on any enquiry. Built from CBHG's conversion
analysis (413 enquiries, 8 weeks): venues pricing in under a day convert ~69%;
venues pricing in 13+ days convert ~50%. The failure modes this skill kills:
wishy-washy emails that hold instead of advance, and quotes fired without the facts
to price correctly.

This skill decides WHICH move. HOW to execute a quote — options, documents,
drafting, governance — lives in the `functions_proposal_build` skill. Every outbound
of every kind is drafted durably in Norm (`draft_email`); a human sends it, from the
Inbox or by approving it on a card.

## The prime directive

**The first real reply advances the enquiry — and wherever a price is what advances
it, the first real reply carries the price.** Never send a holding reply ("I'll put
a proposal together for you now"). Real cases: seven consecutive holding replies
over four weeks and never a number; a client who asked for costs three times and
took nothing to Finance. Every outbound must carry a price, extract a decision, or
set a deadline — otherwise rewrite it or pick up the phone. Norm enforces the price
rule: an enquiry is Quoted only when a proposal is sent, and a plain email that
quotes a price is refused unless the person gives a reason (recorded) — and even
then the enquiry stays Replied.

**The social-booking exception (per Ash, Aug 2026):** for small social group
bookings — birthdays, drinks and nibbles, casual groups — a warm no-price first
reply is a legitimate opening move: we have space (diary-checked), we'd love to
host, pencil them in, point them at the functions pack with page numbers (the raised
area is page 9 of the Glass Goose Group Bookings pack; food options pages 15-20),
food order to follow. This is not a holding reply — it advances to a pencil-in and
hands the client the pack. It is never the move for corporate briefs, exclusive
hires, or anyone who asked for a price: they get the price first reply, always.

## Choose a QUOTE (a proposal) when

You have **a headcount (or range) and a format**, and the segment or the ask calls
for a price. Date is not required to price food and drink. You need it to confirm
availability and any minimum spend — check the diary, and if the date is missing,
ask for it in the same email that carries the price. Never hold a price waiting for
a date.

- Ranges are not blockers: cost both ends of a headcount range; show every date in
  a range.
- Budget unknown is not a blocker: price tiers, or ask for budget alongside the
  prices.
- ANY message that would contain an amount — even a casual "$21pp" answer — goes as
  a proposal, never a plain email. If a number leaves the building, it leaves as a
  quote. (A short proposal is fine: a few lines of HTML and the options.)
- "Enough information" means enough SYSTEM facts, not client facts: capacities, the
  diary and its events' areas, the day's budget where a floor applies, current menu
  prices. The costly errors were never missing client info — they were unchecked
  system facts.
- How to build it is decided in `functions_proposal_build`. Sending it marks the
  enquiry Quoted.

## Choose a plain EMAIL (draft_email kind "email") when — no prices, ever, on this path

- The social-booking opener above.
- One specific fact genuinely blocks pricing → ask exactly that, once. (If you can
  carry any price at all, it belongs in a proposal instead.)
- Logistics only: site-visit times, phone-number requests, confirmations, booking
  and deposit links for a client who already said yes.
- The client asked for space → deliver at the agreed moment, no reply required, no
  scarcity pressure.
- Parked wake-ups with no price change.

Plain drafts can be handed to Gmail (`hand_to_gmail`) when the person wants to send
from there. Client email always belongs to an enquiry — link or create one first
(`file_thread`), and if a client has been told a booking exists, make sure the event
exists too: record their yes with `record_enquiry` action `confirm` (it creates the
event; it asks the person first). Pass `to` explicitly on web-form and marketplace
threads (their reply-to is our own mailbox or a bot — redirect to the client's real
address). Internal and operational mail (suppliers, finance, staff, approval
chatter) never touches client threads. (This rule exists because an internal
approval discussion once ran on a live client thread, one reply-all from disaster.)

## Choose the PHONE when

- A constraint loops on email (one enquiry spent four rounds on seated-vs-standing;
  one call fixed it). Three emails = one call.
- Post-quote silence 8+ days or 2+ unanswered. Day 14 = call and close, never a third
  email (Norm's next action `day_14` says exactly this).
- A buying signal has gone cold ("can you hold the 8th?" then silence).
- The event is inside a fortnight.
- Budget stated and fit obvious — that is a closing conversation.
- The client asked for a call and gave windows — honour the windows, not your
  convenience.
- Promoter checklists; competing parties on one date (first-deposit rule); returning
  customers before any closure; site visits (never end one without asking for the
  hold).

Write the goal of the call in one line before dialling; end with a commitment or a
closure; record the outcome against the enquiry the moment the call ends
(`record_enquiry`: update notes and the follow-up, close with the reason, or confirm
the booking). An unrecorded call did not happen.

## Decision test (every enquiry)

1. Small social booking, first contact, no price asked? → **warm no-price opener
   today** (plain email: space checked, pencil-in, pack with page numbers).
2. Can I price from pax + format, and the segment or ask calls for it? → **Quote
   today** (`functions_proposal_build`). Date missing? Ask for it alongside the price.
3. One fact blocking, and no price can travel yet? → **one plain email** asking
   exactly that.
4. A stuck human decision (silence, loop, clash, imminent date)? → **Phone.**
5. Client mid-process? → light email at the agreed moment, nothing before.

Norm's derived next action on the enquiry (`find_enquiries` / `get_enquiry`) is the
input, not the answer: reply_overdue means we owe a move today; day_7 is the one
follow-up email; day_14 is the phone.

## Before drafting — fetch the reply template

Once the action is EMAIL or a proposal's covering message, run this before writing a
word:

1. Take the `booking_type` from the enquiry (set by a person — never guessed from the
   email; set it deliberately with `record_enquiry` if it's missing) and the stage:
   `first_reply` while the enquiry is new (we haven't replied), `follow_up` once the
   conversation is going.
2. `get_venue_profile` with the venue, `booking_type` and `stage` — returns only the
   matching reply templates.
3. Pick the most relevant template body as the starting draft.
4. Fill every `[BRACKET]` placeholder from system data: date and guest count from the
   enquiry, area from the event or the venue profile, minimum spend from the
   enquiry's options — never from memory. `draft_email` fills
   `{{client_name}}` / `{{sender_name}}` and reports any blank left; a draft
   with a blank is never sent.
5. Apply `functions_email_tone` throughout — including link text, never raw URLs
   (write the body as HTML so links render as text), and no sign-off (Norm adds the
   mailbox signature).
6. Add a close ask if none exists in the template.

The template is a starting point, not a finished email. System data fills the
blanks; email tone governs the voice; judgment writes what the template cannot
anticipate (bad news, pivots, competing dates).

## Anti-patterns (all observed, all costly)

The holding reply. The third chase into silence. Prices in a plain email. Quoting
from memory instead of the system. Telling a client they're booked with no event
behind it. A quote with no follow-up date. Pressuring a client who asked for space.
Internal chatter on client threads. Raw URLs in a client email. Deriving a minimum
spend for a birthday.

## What changed from the Orbit version

- Orbit's three draft kinds become two in Norm: a plain `email` and a `proposal`.
  Orbit's `price_email` (price in the body, marks Quoted) has no equivalent: Quoted
  happens only when a proposal is sent (its quote link lets the customer confirm),
  so a quick price goes as a short proposal.
- `functions_send_email` / `compose_email` → `draft_email` (Norm drafts aren't
  mirrored to Gmail automatically — `hand_to_gmail` on request); sending is
  `send_email` / `send_proposal`, always on an approval card.
- Reply playbooks → the venue's reply templates in `get_venue_profile`
  (classification → stage first_reply/follow_up, event_type → booking_type).
- "Make sure the event record exists" → record the customer's confirmation
  (`record_enquiry` confirm), which creates the event — events are never created
  directly for a minimum-spend booking.
"""

PROPOSAL_BUILD = f"""# Proposal Build — CBHG Functions

_{_PORT_NOTE}_

Purpose: take an enquiry from thread to a sent-ready quote in one sitting — the first
real reply carries the price. Target same day, never past 48 hours. An enquiry is
Quoted only when its proposal is sent, so this path is the main event of the
functions desk. (WHETHER a quote is the right move at all is decided in
`functions_call_email_proposal`.)

## The model: a proposal and its covering email

Every quote is an EMAIL carrying a PROPOSAL link.

- **The proposal** (`build_proposal`): free HTML + CSS for the enquiry, started from
  the venue's brand template, plus the structured numbers it offers — the
  minimum-spend **options** and the food pre-order **menus**. The customer opens it
  on a link and confirms or declines each option on Norm's own panel below the
  document — confirming an option creates the event.
- **The covering email** (`draft_email` kind `proposal`): the message that carries
  the link — put `{{{{quote_link}}}}` where it goes, or Norm adds it after the message.
  It waits in Norm as a draft.
- **Sending** (`send_proposal`, or staff pressing Send in Norm): freezes the
  proposal and the options on offer as a version, puts the quote link in the email,
  sends it on the enquiry's thread, and marks the enquiry Quoted. `send_proposal`
  always asks the person on an approval card.
- A plain email (`draft_email` kind `email`) carries no prices.
- Norm appends the mailbox signature — write bodies WITHOUT a sign-off. Write the body
  as HTML so links go out as link text (`functions_email_tone`), and pass `to`
  explicitly on web-form and marketplace threads, whose senders resolve to our own
  mailbox or a bot.

**Which shape:** minimum spends, exclusivity structures, multi-option quotes —
the full branded document. Food recommendations, casual price answers, re-quotes,
package restatements — still a proposal, but a short one (a few lines and the
menu). If no price travels at all → a plain email (and question whether this task
belongs in `functions_call_email_proposal` instead).

## Step 1 — one read, everything

`get_enquiry` (by enquiry_id, gmail_thread_id or contact_email) returns the contact
history, the options, menus, proposal state, linked threads, open drafts and recent
activity in one call; `read_thread` (detail `full`) on the linked thread for the
wording. Establish: what the client actually asked for (format, dates, flexibility,
budget, constraints — speeches, AV, weather, licence hours); what has already been
promised in writing (an offered no-minimum reservation binds you; a claimed booking
must exist as an event — record the customer's confirmation if Norm disagrees with
what a client was told); what is missing (phone, headcount); duplicates at sister
venues via the contact history. Trust the derived facts: stage and quote tracking
come from recorded actions only.

## Step 2 — system facts, never memory

Verify before pricing: area capacities and fit (`get_venue_profile`); the date in
the diary AND the areas the diary's other events hold (`find_events` for the date,
`get_event` on each — a same-date event with no time overlap is a changeover note,
not a clash); the day's budget where a floor will be derived (`get_venue_profile`
with `date`: Loaded budget and last year's same-weekday sales); current menu template
prices (`get_venue_profile` menus — printed packs lag the system). If a price is not
in the system or published, do not invent it — flag it or leave the item out.

## Step 3 — derive the numbers (by segment)

- **Social group bookings (birthdays, drinks and nibbles, post-event groups):** do NOT
  derive or attach a minimum spend — speed to price beats floor precision, and waiting
  on the approval is the wrong trade. Quote the food and tab, and cover exclusivity
  with one line: a minimum spend may apply if they want the area exclusively, figure
  confirmed before anything is locked. Keep any derivation out of client-facing text.
- **Exclusive hires and corporate briefs:** the floor genuinely drives the quote.
  Derive per the saved method: day budget × the window's organic share (order
  timestamps, same-season sample from last year — with the Loaded tools, last year's
  same-weekday sales inside the hire window ÷ that day's sales; when history is
  missing use the most recent same-weekday sample and say so; function spikes
  stripped), time-boxed to the hire window. Full venue = full day budget only when the
  hire is the full trading day.
- Options land as **proposed** and await the venue approver's approval in Norm —
  drafting never blocks on it; a human approves before send (until then the
  customer can't confirm that option, and the money check says so).
- **Approval routing:** realistic client spend >= ~1.2× floor → quote firm. Clearly
  below what the space earns → quote no minimum; offer another date, smaller area or
  plain reservation. Escalate to James (the venue's minimum-spend approver —
  `get_venue_profile` names who) only for sub-90% settling on exclusive hires,
  structural oddities (ticketed, past-licence, residencies) or unreliable data.
- **Food** from real template items with explicit quantities (`build_proposal` menus:
  template, guest count, quantities); recompute every total independently. **Bar
  tab** as an indicative per-head, explicitly theirs to set, honest about what it buys.
- Land just inside any stated budget; split food (firm) from beverage (their call);
  cost both ends of a headcount range; two-option structure wherever exclusivity is
  genuinely in play and available; firm numbers always — never "I'd need to check."

## Step 4 — build the document

`build_proposal` is proposal data and the document only — no email. One validated
batch: `options` (each new one names its area), `menus`, document changes. The first
call starts the document from the venue's brand template — always edit a real
baseline, never write from scratch. Use `replacements` for targeted edits; full
html/css only when restructuring; `restart` to return to the brand template (it
discards every hand fix — re-apply them).

**Sequencing rule:** set the options (with their areas) and menus BEFORE the document
is first started, in the same call or an earlier one: the template's spaces, options
and menu blocks are filled from them when it starts, and after that the HTML is free
text that doesn't follow option changes (only `restart` re-renders it).

Approved options are read-only to you: never change or withdraw one — add a new
option and say why.

**Check the text every build** (`include_text`): replace raw dish names ("DISH - …")
with clean names; replace vague gestures ("cakeage applies per person") with the
real figure; make sure nothing internal is there — derivations, approvals, margins,
staff notes. Only the enquiry's `client_note` is meant for the customer; `notes` are
internal and never belong in the document. Never put buttons or forms in the HTML —
Norm renders Confirm / Decline from the options.

**Template vs document:** hand-written fixes live on the single enquiry only.
Changing the venue's brand template — the starting point for every future proposal —
is done by staff on the Functions Settings page (brand). High blast radius; never
casual.

## Step 5 — check your work

Read the returned `check`: every warning is a figure in the document that matches
nothing on record, or an option the customer can't confirm yet — fix the figure or
the option, or say why it stands. Read the document text (`include_text`) end to
end: collisions of dates, orphaned template text, every point above. Tell the person
to glance at the preview on the enquiry page (the link) — it is exactly what the
customer will see. Re-verify totals against the returned options and menus.
Arithmetic is recomputed, never trusted — including your own.

## Step 6 — the email

Before writing, fetch the matching reply template: `get_venue_profile` with the
venue, the enquiry's `booking_type` and the stage (`first_reply` while new,
`follow_up` after). Pick the closest body as a starting point and fill the
`[BRACKET]` placeholders from the data just built (`draft_email` fills
`{{client_name}}` / `{{sender_name}}` and reports any blank left — a draft with a
blank is never sent). Don't write from scratch.

Then `draft_email` with `kind: "proposal"` and the `enquiry_id`: short, firm numbers,
one honest caveat where one exists, real urgency only if true, ask for the phone
number if missing, close ask, no sign-off. Voice per `functions_email_tone`. The
draft is durable in Norm the moment it's written — that IS the deliverable.

## Step 7 — hand over the send

Default: leave the draft in Norm for Ash to review and send (from the enquiry or the
Inbox). Call `send_proposal` only when explicitly asked — the person approves it on a
card. Sending marks the enquiry **Quoted** — the only way that status ever happens —
so the review before send is the review that matters. Verify the recipient on the
draft; internal approval chatter never goes on the client thread.

## Step 8 — close the record

`record_enquiry` action `update`: `follow_up_date` always set (tie one to the
proposal's validity), `follow_up_reason` stating what was drafted and what remains
(approval, review, send), and the internal `notes` updated with what was quoted and
why. A quote with no follow-up date is next week's backlog. Phone outcomes recorded
the moment the call ends.

## Quality bar before anything is handed over

Every figure traceable to a system or published source; totals recomputed; every
option genuinely acceptable to the venue and actually available in the diary; the
document text read through and the money check clean (or every warning explained);
recipient verified on the draft; minimum spend either segment-appropriately skipped,
formula-clean, or explicitly escalated; the shape justified; follow-up date attached.
If any of these cannot be met — say so rather than ship it.

## What changed from the Orbit version

- One send path: the proposal's covering email (`draft_email` kind `proposal`) sent
  by `send_proposal` or staff; Orbit's `price_email` (price in the body, marks Quoted)
  has no equivalent — a quick price is a short proposal.
- `min_spend_lines` → `options` (one option per alternative; no lines inside an
  option); `food_preorder` → `menus` (template + guests + quantities;
  recommendation packs are gone — set menus split by their default %).
  `document.reset` → `restart`. Uploading images happens on the Functions Settings
  page (the venue image library), not in the tool.
- No PDF snapshot: read the document text (`include_text`) and use the enquiry
  page's preview. Orbit's template gotchas (UTC times, "Prepared by" a random staff
  member, notes rendered to the client) don't exist in Norm's template — times are
  venue-local and no notes render — but read the text anyway.
- The customer confirms options on the quote page, which creates the event; a
  manager's approval only makes an option confirmable.
- `manage_quote_brand` → the Functions Settings page (no agent tool).
- `send_proposal` always asks on an approval card; nothing is ever sent unasked.
"""

EMAIL_TONE = f"""# Email Tone — CBHG Functions

_{_PORT_NOTE}_

Purpose: the voice rules for every customer-facing message across the CBHG functions
desk — covering emails on proposals (`functions_proposal_build` Step 6), plain
replies (`functions_call_email_proposal`), and sweep-generated drafts
(`functions_inbox_sweep`). One skill; update here, not inline in the others.

## The voice

Warm, direct, and confident. We know our venues well and we back them — but we are
not a hotel concierge and we are not performing enthusiasm. Write the way a
switched-on person at the venue would talk to a friend-of-a-friend: genuine, no
fluff, gets to the point.

## Structure

- **Short paragraphs.** One idea per paragraph. If a reply runs past five paragraphs,
  cut it.
- **Bullets for lists.** Any list of three or more items becomes bullets — `<ul><li>`
  in the HTML body. Indent sub-items for nested lists (e.g. food selections inside a
  menu section).
- **No bullet for a single item.** One thing is a sentence, not a list.
- **Subject lines.** Specific and human: "Your 40th at Baxter's - Saturday 28
  November" not "Re: Functions Enquiry." Use a hyphen to separate the occasion from
  the date where both are known. (A reply keeps its thread's subject.)

## Language rules

**Say the true thing plainly.** No framing phrases that signal hesitation or perform
honesty:
- Never: "I have to be upfront," "I'll be honest," "to be completely honest," "I need
  to be transparent," "I want to be clear"
- Just say it: "Saturday 28 November is already booked" not "I have to be upfront —
  Saturday 28 November is already booked"

**Hyphens sparingly.** Use a hyphen only where it does genuine grammatical work
(compound modifiers: "walk-and-fork spread"). Never as connective glue between
clauses — use a comma, colon, or a new sentence instead. Never an em dash.

**Real fees, not vague gestures.** State the actual figure: "$4 per person, capped at
$150" not "a small cakeage fee may apply." If the figure is genuinely unknown, say
you will confirm it — do not hide behind vagueness. (Any price goes in a proposal,
not a plain email — `functions_call_email_proposal`.)

**Hyperlinks with link text, never raw URLs.** "View the dinner menu" linked
(`<a href="…">View the dinner menu</a>`), never https://www.glassgoose.co.nz/s/GG-Dinner-Menu...
Use the exact menu links from `get_venue_profile`; never invent one.

**No venue jargon in client emails.** Never: pax, ref, min spend (say "minimum
spend" in full the first time), F&G, GG, DSC. Venue names in full.

**Urgency only when real.** "Peak season Saturdays go early" is true and earns its
place. "This date won't last long" when the diary is half-empty is not.

## Warm markers (use these, they work)

"Just let me know" / "happy to help" / "no problem at all" / "we'd love to have you" /
"sounds like a great night." Natural, not gushing. One per email is enough.

**Never:** "I'd be absolutely delighted," "it would be our pleasure," "we are thrilled
at the prospect." These read as form letters.

## Closings

Ask for the one thing that moves the enquiry forward — a date confirmation, a
headcount, a decision. End on that ask, not on a pleasantry.

- Good: "Send through a couple of dates and we'll check them straight away."
- Good: "Just say the word and we'll hold it for you."
- Weak: "Please do not hesitate to get in touch if you have any further questions."

Sign off with the sender's name only. No titles, no "The Glass Goose Team" unless
there is no named sender. In Norm the mailbox signature is added when the email is
sent and the email comes from "<first name> from <venue>", so a draft ends on the
close ask with no sign-off of its own; only when `draft_email` reports
`mailbox_signature: false` add the sender's first name on its own line.

## Bad news and unavailability

Lead with the fact, pivot immediately to what we can offer. No over-apologising.

- Good: "Unfortunately Saturday 28 November is already booked at Baxter's. We do have
  two sister venues free that evening that would suit your group well."
- Weak: "I'm so sorry to have to let you know that unfortunately on this occasion we
  are unable to accommodate..."

## What changed from the Orbit version

- Bodies are written as simple HTML in `draft_email` (Orbit took body_text plus
  body_html); bullets are `<ul><li>`.
- The signature and sender name are added by Norm's outbox, so drafts carry no
  sign-off unless the mailbox has no signature.
"""

RUNSHEET = f"""# Functions Runsheet — CBHG (permanent format, approved 21 Aug 2026)

_{_PORT_NOTE}_

Purpose: a working document Ash and James run top to bottom. Every open lead gets a
full narrative review with a specific recommended next action and a day attached. If
it's not in here it won't resurface. The format is permanent: it shrinks as the
backlog clears but the structure never changes.

## Step 1 — Gather

`find_enquiries` with the `venue`, `stage: ["new", "replied", "quoted"]`, `limit:
100`, `sort: "due"`. The queue caps at 100 rows a call — page with `offset` while
`has_more`, and say so in the document if leads are known to sit beyond what you
pulled (cross-check against a recent inbox sweep and prior runsheets).

Read full threads (`get_enquiry`, then `read_thread` detail `full`) only where the
next action genuinely depends on wording: high-value leads, ambiguous states,
anything about to be closed or called. Digest is enough for a clear status.

Check for contact duplicates across venues via the enquiry's contact history
(`get_enquiry` → contact_history) — a lead being handled at two venues without
either side knowing is a hygiene issue, not two separate wins.

## Step 2 — Judge each lead before writing

For every enquiry, establish:

- What they actually asked for (not what we offered)
- How the conversation has really gone: speed, gaps, who is waiting on whom
- Trust of the data: enquiries with a customer reply recorded before any outbound
  carry a mirror gap — their recorded silence may be false (mail older than the
  mirror, or sent outside the linked thread); never call-and-close on metadata alone
- The one next action with a day and the reasoning (Norm's derived next action is the
  input: reply_overdue, day_7, day_14, requested)

A lead where the customer spoke last is never "parked" — it is dropped. Parking is
only valid when the customer has told us why (waiting on manager approval, on leave,
deliberately pushed to a date — recorded as the enquiry's follow-up date and reason)
and we sent the last message.

## Step 3 — Write in the fixed format

Per lead, in this order:

**Bold header:** Name - Company · pax · date · stage

**Narrative paragraph:** What they want, how the conversation has actually gone,
where it stands. No one-liners. A lead that earns a place on the runsheet earns a
paragraph.

**Last email:** who sent it, when, and what it said (one sentence — not "I just
wanted to check in.")

**Next action:** the specific action, the day it should happen, and one line of
reasoning. Every lead must have a Next with a day. A lead with no Next is a lead that
disappears.

## Step 4 — Assemble in this fixed order

**1. This week's first hour, in order** — owed replies oldest first, unread messages,
imminent events (inside a fortnight), ghost records (quoted with zero email
activity), status corrections. Bold the time-critical and money items.

**2. Decisions needed from James** (the venue's minimum-spend approver) — only genuine
escalations per the approval-routing rules: date clashes (two live parties on the
same night), policy questions (ticketed events, residencies, DJ commercial terms),
structural discounts (below-floor minimum spend), and competing cross-venue requests.
Options waiting on his approval belong here by name. Not "I thought you should know"
items — those go in the narrative.

**3. Leads by segment:**
- Large corporate (100+ pax, named company, budget stated or implied)
- Mid-size Christmas (30–100 pax, year-end or Christmas, company event)
- Events and entertainment (ticketed, DJ/band, promoter, recurring)
- Social (birthdays, farewell, celebrations, non-corporate)
- Cross-venue handoffs (referred to or from a sister venue — confirm the handoff
  landed before closing)
- Parked (customer explicitly told us why; we sent last)
- Beyond the queue cap (named leads known to exist but not in the pull)

**4. Patterns worth acting on** — three leads failing the same way is a playbook
change, not three problems. Name the pattern, propose the fix.

**5. Hygiene** — follow-up dates missing on quoted leads; link problems (threads not
linked to their enquiry, or linked to the wrong one); mirror gaps (name the leads, do
not call-and-close on them); ghost records; stale follow-up dates that have passed;
facts that are missing (a client told they're booked with no confirmation recorded,
a quote sent outside Norm without our link).

## Step 5 — The judgement layer

Sequence the week's chases so phone-first leads land early: post-quote silence 8+
days, stated budgets, events inside a fortnight.

Flag where parking is hiding an owed reply — this is the single most common failure
mode in the pipeline.

Put covers-at-stake numbers on the money section so priority is self-evident.
"$14,952 sent, four emails unanswered" is more actionable than "awaiting response."

Covers-at-risk counter at the top: how many covers across quoted-but-not-confirmed
leads, and what they represent in revenue at current minimums (each row's value).

## Quality bar before shipping

- Every lead has a Next with a day
- Nothing awaiting-us appears anywhere but the first hour
- Every James item is a decision only he can make
- Mirror-gap leads are named and flagged, not silently absorbed
- Queue cap, if hit, is stated plainly with any known beyond-cap leads named
- Patterns section has at least one observation if the pipeline has 20+ leads
- Hygiene section exists even if short

The runsheet is read-only: it recommends; it changes nothing. Recording outcomes
(follow-up dates, closures) happens afterwards with `record_enquiry`, when asked.

## What changed from the Orbit version

- `functions_find_enquiries` (status, limit 50) → `find_enquiries` (stage, up to 100
  a call, `offset` to page).
- Stages are derived by Norm from the facts (mail, proposals sent, events, closures),
  so "status mismatches" can't happen; the hygiene item becomes missing facts.
- James's approvals happen in Norm (the enquiry page), and Norm says which options
  are still waiting on them.
"""

#: slug -> playbook row. Description = what the agent's playbook menu shows.
PLAYBOOKS = {
    "functions_inbox_sweep": {
        "display_name": "Functions inbox sweep",
        "description": (
            "Run a venue functions inbox sweep: classify every unfiled thread into the "
            "fixed board (Other / Reservation / New Enquiry / Follow Up, with "
            "CALL / EMAIL / PROPOSAL badges), and optionally read threads, draft "
            "replies and build proposals. Use when asked for an inbox sweep, inbox "
            "review, or to process the functions inbox."
        ),
        "instructions": INBOX_SWEEP,
    },
    "functions_call_email_proposal": {
        "display_name": "Call vs email vs proposal",
        "description": (
            "Decide the right next move on any functions enquiry — a quote (proposal), "
            "a plain email, or a phone call — and fetch the venue's reply template "
            "before drafting. Use whenever an enquiry needs a next action decided or a "
            "reply drafted."
        ),
        "instructions": CALL_EMAIL_PROPOSAL,
    },
    "functions_proposal_build": {
        "display_name": "Proposal build",
        "description": (
            "Build a functions proposal from thread to sent-ready in one sitting: system "
            "facts, minimum spend where the segment calls for it, the document, the "
            "covering email as a draft — then hand to a person to send. Use whenever a "
            "functions enquiry needs a priced quote."
        ),
        "instructions": PROPOSAL_BUILD,
    },
    "functions_email_tone": {
        "display_name": "Email tone",
        "description": (
            "Voice and style rules for every customer-facing functions email — "
            "language, structure, warm markers, closings, bad news. Read before "
            "drafting any customer email."
        ),
        "instructions": EMAIL_TONE,
    },
    "functions_runsheet": {
        "display_name": "Functions runsheet (weekly lead review)",
        "description": (
            "Produce the open-leads runsheet (weekly lead review) for a venue's "
            "functions pipeline in its permanent format. Use when asked for a "
            "runsheet, lead review, pipeline review or Monday brief."
        ),
        "instructions": RUNSHEET,
    },
}


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _locked(query, dry_run: bool):
    """Lock the rows a real run rewrites; a dry run (against the database
    production reads) takes no locks."""
    return query if dry_run else query.with_for_update()


def _agent_config(db, dry_run: bool) -> list[str]:
    from app.db.config_models import AgentConfig

    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == MEMBER_SLUG).first()
    if row is None:
        if not dry_run:
            db.add(AgentConfig(agent_slug=MEMBER_SLUG, **AGENT_CONFIG))
        return [
            f"create agent_configs '{MEMBER_SLUG}' ({AGENT_CONFIG['display_name']})"
        ]
    diff = [k for k, v in AGENT_CONFIG.items() if getattr(row, k) != v]
    if diff and not dry_run:
        for k, v in AGENT_CONFIG.items():
            setattr(row, k, v)
    return [f"update agent_configs '{MEMBER_SLUG}' ({', '.join(diff)})"] if diff else []


def _spec(db, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    rows = tool_rows()
    spec = _locked(
        db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == CONNECTOR),
        dry_run,
    ).first()
    if spec is None:
        if not dry_run:
            db.add(
                ConnectionSpec(
                    connector_name=CONNECTOR, tools=rows, endpoints=[], **SPEC
                )
            )
        return [f"create spec '{CONNECTOR}' with {len(rows)} tool rows"]
    lines = []
    for k, v in SPEC.items():
        if getattr(spec, k) != v:
            lines.append(f"spec '{CONNECTOR}': update {k}")
            if not dry_run:
                setattr(spec, k, v)
    changed = [r["action"] for r in rows if spec_rows.find_tool(spec, r["action"]) != r]
    if changed:
        lines.append(f"spec '{CONNECTOR}': add/update tool rows {', '.join(changed)}")
        if not dry_run:
            spec.tools = spec_rows.upsert(spec.tools, rows)
            flag_modified(spec, "tools")
    if spec.endpoints is None:
        lines.append(f"spec '{CONNECTOR}': split (endpoints = [])")
        if not dry_run:
            spec.endpoints = []
    if lines and not dry_run:
        spec.version = (spec.version or 0) + 1
    return lines


def _skills(db, dry_run: bool) -> list[str]:
    from app.db.config_models import MarketplaceApp, Playbook

    if sorted(PLAYBOOKS) != sorted(SKILLS):
        raise SystemExit("PLAYBOOKS and catalog.SKILLS disagree")
    app = db.query(MarketplaceApp).filter(MarketplaceApp.slug == APP["slug"]).first()
    claimed = set((app.composition or {}).get("skills") or []) if app else set()
    lines = []
    for slug, fields in PLAYBOOKS.items():
        want = {"agent_slug": MEMBER_SLUG, **fields, "enabled": slug in claimed}
        row = db.query(Playbook).filter(Playbook.slug == slug).first()
        state = "on" if want["enabled"] else "off until the Functions App claims it"
        if row is None:
            lines.append(f"create playbook '{slug}' ({state})")
            if not dry_run:
                db.add(Playbook(slug=slug, **want))
            continue
        diff = [k for k, v in want.items() if getattr(row, k) != v]
        if diff:
            lines.append(f"update playbook '{slug}' ({', '.join(diff)}; {state})")
            if not dry_run:
                for k, v in want.items():
                    setattr(row, k, v)
    return lines


def _superseded(db, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    spec = _locked(
        db.query(ConnectionSpec).filter(
            ConnectionSpec.connector_name == "cook_brothers_app"
        ),
        dry_run,
    ).first()
    if spec is None:
        return ["cook_brothers_app spec not found — nothing to mark superseded"]
    marked = []
    for row in spec_rows.rows(spec):
        action = row.get("action")
        if action not in SUPERSEDED:
            continue
        fields = superseded_fields(action)
        if any(row.get(k) != v for k, v in fields.items()):
            marked.append(action)
            if not dry_run:
                row.update(fields)
    if marked and not dry_run:
        flag_modified(spec, "tools")
        if spec_rows.is_split(spec):
            flag_modified(spec, "endpoints")
        spec.version = (spec.version or 0) + 1
    return [f"cook_brothers_app: mark superseded {', '.join(marked)}"] if marked else []


#: Norm's MCP scopes for Functions (app/mcp/scopes.py).
MCP_READ_SCOPE = "mcp:functions:read"
MCP_DRAFT_SCOPE = "mcp:functions:draft"
#: The skills offered as MCP workflows. Email tone is a reference the others
#: read inside Norm, not a job to run.
MCP_WORKFLOWS = tuple(s for s in SKILLS if s != "functions_email_tone")


def mcp_rows(claimed_skills) -> list[dict]:
    """The capabilities: every read tool directly, and each claimed skill as
    a workflow (drafts and writes never go out as direct tools)."""
    reads = [r["action"] for r in tool_rows() if r["effect"] == "read"]
    rows = [
        {
            "kind": "connector",
            "target": CONNECTOR,
            "action": a,
            "scopes": [MCP_READ_SCOPE],
        }
        for a in reads
    ]
    rows += [
        {"kind": "playbook", "target": slug, "action": "", "scopes": [MCP_DRAFT_SCOPE]}
        for slug in MCP_WORKFLOWS
        if slug in claimed_skills
    ]
    return rows


def _mcp(db, dry_run: bool) -> list[str]:
    from app.db.config_models import MarketplaceApp, McpCapability

    app = db.query(MarketplaceApp).filter(MarketplaceApp.slug == APP["slug"]).first()
    claimed = set((app.composition or {}).get("skills") or []) if app else set()
    lines = []
    for want in mcp_rows(claimed):
        row = (
            db.query(McpCapability)
            .filter(
                McpCapability.kind == want["kind"],
                McpCapability.target == want["target"],
                McpCapability.action == want["action"],
            )
            .first()
        )
        name = (
            f"{want['target']}.{want['action']}" if want["action"] else want["target"]
        )
        if row is None:
            lines.append(
                f"create MCP capability {want['kind']} {name} ({want['scopes'][0]})"
            )
            if not dry_run:
                db.add(McpCapability(enabled=True, **want))
        elif row.scopes != want["scopes"] or not row.enabled:
            lines.append(f"update MCP capability {want['kind']} {name}")
            if not dry_run:
                row.scopes, row.enabled = want["scopes"], True
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import app.agents.internal_tools  # noqa: F401 — registers the handlers
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changes: list[str] = []
        changes += _agent_config(db, args.dry_run)
        changes += _spec(db, args.dry_run)
        changes += _skills(db, args.dry_run)
        changes += _mcp(db, args.dry_run)
        changes += _superseded(db, args.dry_run)
        if not changes:
            print("Norm Functions config up to date")
            return 0
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            db.rollback()
            print("(dry run — nothing written)")
            return 0
        db.commit()
        print(f"committed {len(changes)} change(s)")
        from app.services.config_validator import validate_config

        summary = validate_config(config_db=db)
        print(
            f"validate_config: {'ok' if summary.get('ok') else summary.get('issue_count')}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
