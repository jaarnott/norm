"""Build agent system prompts dynamically from active connector specs."""

import datetime
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Read/write verbs used to classify an mcp-mode action whose transport method
# is a meaningless POST. Fail-closed: a name carrying ANY write verb is treated
# as a write, so `get_and_delete_x` never slips through as an unapproved read.
_READ_VERBS = frozenset({"get", "list", "search", "fetch", "find", "read"})
_WRITE_VERBS = frozenset(
    {
        "create",
        "update",
        "delete",
        "set",
        "add",
        "remove",
        "send",
        "place",
        "mark",
        "approve",
        "sign",
        "move",
        "publish",
        "cancel",
        "reject",
        "enrol",
        "enroll",
        "assign",
        "trigger",
        "upload",
        "toggle",
        "reset",
        "complete",
        "log",
        "draft",
        "submit",
        "save",
        "sync",
        "push",
        "write",
        "post",
        "put",
        "patch",
        "generate",
    }
)


def _name_is_read(action: str) -> bool:
    """A conservative read-verb check on the action name's segments —
    `functions_get_contacts`, `training_list_plans`, `stock_get_stock_on_hand`.
    Any write verb present vetoes it (fail-closed)."""
    segs = str(action or "").lower().split("_")
    if any(s in _WRITE_VERBS for s in segs):
        return False
    return any(s in _READ_VERBS for s in segs)


def _effective_method(spec, tool: dict) -> str:
    """The read/write label the agent loop classifies on.

    For an **mcp-mode** connector the real transport is always POST, so a genuine
    read — `functions_get_contacts`, `training_list_job_openings` — is mislabelled
    a write and demands an approval nobody should grant to READ. Treat such an
    action as GET when it is explicitly ``read_only``, or (the flag is unset on
    auto-discovered tools) when its name is unambiguously a read. Template-mode
    connectors keep their declared method: there a POST is a real write, and a
    mis-set flag must never turn one into an unapproved read. Same rule as
    ``app_runtime._tool_read_only`` / ``delegation.is_read_only_tool``.
    """
    declared = (tool.get("method") or "POST").upper()
    if (getattr(spec, "execution_mode", "") or "") != "mcp":
        return declared
    if "read_only" in tool:
        return "GET" if tool.get("read_only") else declared
    return "GET" if _name_is_read(tool.get("action", "")) else declared


# Internal tool actions that may still be listed in the config-DB spec but whose
# code handler has been removed — filtered out of every agent's menu so a
# lingering config row can't offer a tool that would fail on call.
_RETIRED_ACTIONS = {"delegate_to_agent"}

# Actions that work perfectly well but do not belong on an AGENT's menu.
# resolve_dates is the engine's and the MCP surface's, not the agent's: nine
# consolidators call it through call_api (which reads the spec row, never this
# list), and an MCP client has no Norm prompt telling it today's date — while
# an agent's own tools take `period` in plain English and apply the venue's
# trading day themselves.
#
# Unbinding it from every agent is NOT enough on its own: a binding with an
# empty capabilities list exposes every action on its connector, and
# executive_chef/norm is exactly that. So the removal has to live here.
# projection.ALWAYS_EXPOSE re-adds it for MCP after this filter, which is why
# that had to stop depending on a binding first.
_ENGINE_AND_MCP_ONLY = {"resolve_dates"}


def _collect_tools(
    db: Session,
    user_id: str | None = None,
    config_db: Session | None = None,
) -> list[dict]:
    """Collect all enabled tools from all connector specs.

    Returns a deduplicated list of dicts with keys: action, connector,
    required_fields, field_mapping, method, description.

    config_db is used for AgentConnectionBinding and ConnectionSpec queries.
    db is used for Connection (credentials) queries.
    """
    from app.db.models import AgentConnectionBinding, Connection, ConnectionSpec

    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )

    bindings = (
        _cdb.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.enabled == True,  # noqa: E712
        )
        .all()
    )

    if not bindings:
        return []

    # Marketplace entitlement filter (docs/apps-marketplace-plan.md, the
    # connections/apps split). A connection stays available while ANY entitled
    # App declares it; tool actions can additionally be claimed per-app via
    # composition["tool_actions"] ("connector.action" or "connector.*").
    # project_tools() gates through this same function, so the MCP surface
    # honors marketplace toggles with no second code path. Inert until the
    # catalog is seeded (empty sets), and fail-open by design.
    from app.services.entitlements import (
        org_id_for_user,
        unentitled_connectors,
        unentitled_tool_actions,
    )

    _org = org_id_for_user(user_id, db)
    _blocked = unentitled_connectors(_org, db, _cdb)
    if _blocked:
        bindings = [b for b in bindings if b.connector_name not in _blocked]
    _blocked_actions = unentitled_tool_actions(_org, db, _cdb)

    tools: list[dict] = []
    for binding in bindings:
        spec = (
            _cdb.query(ConnectionSpec)
            .filter(
                ConnectionSpec.connector_name == binding.connector_name,
            )
            .first()
        )
        if not spec:
            continue

        # Gate on having an active connection — except internal specs which need no credentials
        if spec.execution_mode != "internal":
            has_config = (
                db.query(Connection)
                .filter(
                    Connection.connector_name == binding.connector_name,
                    Connection.enabled == "true",
                )
                .count()
                > 0
            )
            if not has_config:
                continue

        # User-scoped email connectors: require per-user Connection
        _USER_SCOPED_CONNECTORS = {"gmail", "microsoft_outlook"}
        if spec.connector_name in _USER_SCOPED_CONNECTORS:
            if not user_id:
                continue
            has_user_config = (
                db.query(Connection)
                .filter(
                    Connection.connector_name == spec.connector_name,
                    Connection.user_id == user_id,
                )
                .count()
                > 0
            )
            if not has_user_config:
                continue

        # Build an enabled-action set from binding capabilities
        enabled_actions: set[str] | None = None
        if binding.capabilities:
            enabled_actions = {
                cap["action"]
                for cap in binding.capabilities
                if cap.get("enabled", True)
            }

        for tool in spec.tools or []:
            action = tool.get("action", "")
            if enabled_actions is not None and action not in enabled_actions:
                continue
            if _blocked_actions and (
                f"{binding.connector_name}.{action}" in _blocked_actions
                or f"{binding.connector_name}.*" in _blocked_actions
            ):
                continue
            # Demoted tools ([consolidator-only]/[engine-only]) are for the
            # engine's own call_api, never the agent's menu — the structured
            # flag makes that machine-checked instead of a description
            # convention, and holds even for a binding with an empty
            # capabilities list (which otherwise exposes everything).
            if tool.get("engine_only"):
                continue
            # Retired internal tools whose handler no longer exists. The config
            # DB spec may still list them until the next sync — never offer a
            # tool that can't run. `delegate_to_agent` went when the multi-agent
            # router/handoff was folded into one capable agent.
            if action in _RETIRED_ACTIONS or action in _ENGINE_AND_MCP_ONLY:
                continue

            tools.append(
                {
                    "action": action,
                    "connector": spec.connector_name,
                    "required_fields": tool.get("required_fields", []),
                    "optional_fields": tool.get("optional_fields", []),
                    "field_mapping": tool.get("field_mapping", {}),
                    "field_descriptions": tool.get("field_descriptions", {}),
                    "field_schema": tool.get("field_schema", {}),
                    # Effective read/write label — an mcp read stays a read, so
                    # the agent loop auto-runs it instead of gating it (see
                    # _effective_method). Execution still POSTs regardless.
                    "method": _effective_method(spec, tool),
                    "description": tool.get("description", ""),
                    # Used by result-slimming (tool_loop._slim_tool_result and
                    # the MCP surface). Carried here so callers don't have to
                    # re-query the spec row.
                    "summary_fields": tool.get("summary_fields"),
                }
            )

    # Deduplicate — same connector+action may appear in multiple agent bindings
    seen: set[tuple[str, str]] = set()
    unique_tools: list[dict] = []
    for t in tools:
        key = (t["connector"], t["action"])
        if key not in seen:
            seen.add(key)
            unique_tools.append(t)
    return unique_tools


def build_dynamic_prompt(
    domain: str, db: Session, config_db: Session | None = None
) -> str | None:
    """Return the DB-stored system prompt if connector specs are bound.

    Returns None if no connector specs are bound, signalling the caller to
    use the DB-stored prompt directly via agent_config_service.
    """
    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    tools = _collect_tools(db, config_db=_cdb)
    if not tools:
        return None

    # Tools are bound — return the DB prompt (the admin manages it in Settings)
    from app.services.agent_config_service import get_system_prompt

    return get_system_prompt(domain, _cdb) or None


def build_venue_property(configured_venues: list[str], allow_all: bool = False) -> dict:
    """The `venue` enum property injected into external-connector tools.

    ``allow_all`` adds an "all" member for tools that can fan out across every
    consented venue. Opt-in: see mcp.projection.supports_all_venues.
    """
    description = f"Venue name. Available for: {', '.join(configured_venues)}."
    values = list(configured_venues)
    if allow_all:
        values.append("all")
        description += (
            " Use 'all' to cover every venue in one call — each is measured "
            "over its own trading day, and the result reports them separately."
        )
    return {
        "type": "string",
        "description": description,
        "enum": values,
    }


def build_input_schema(tool: dict, extra_properties: dict | None = None) -> dict:
    """Build a JSON Schema object from a ConnectionSpec tool row.

    Anthropic's ``input_schema`` and MCP's ``inputSchema`` are the same object,
    so this has two consumers: ``build_tool_definitions`` below (Norm's own
    agents) and ``app.mcp.projection`` (the external MCP surface). Keeping one
    implementation is what stops the two surfaces from drifting apart.

    ``extra_properties`` are merged after the field-derived ones — that's how
    the caller injects context it alone knows about (the venue enum here; MCP
    injects nothing today).

    Note the loose default: every field is ``type: string`` unless the spec row
    carries an explicit ``field_schema``, which is passed through verbatim and
    is the only way to express nested objects or arrays.
    """
    properties: dict = {}
    field_descs = tool.get("field_descriptions") or {}
    field_schemas = tool.get("field_schema") or {}
    field_mapping = tool.get("field_mapping") or {}
    required_fields = list(tool.get("required_fields") or [])
    all_fields = required_fields + list(tool.get("optional_fields") or [])

    for field in all_fields:
        # Explicit schema wins — supports nested objects/arrays
        if field in field_schemas:
            prop = {**field_schemas[field]}
            if "description" not in prop:
                prop["description"] = field_descs.get(field, field)
            properties[field] = prop
            continue

        api_name = field_mapping.get(field, field)
        desc_parts = []
        hint = field_descs.get(field, "")
        if hint:
            desc_parts.append(hint)
        if api_name != field:
            desc_parts.append(f"Maps to API field: {api_name}")
        properties[field] = {
            "type": "string",
            "description": ". ".join(desc_parts) if desc_parts else field,
        }

    if extra_properties:
        properties.update(extra_properties)

    return {
        "type": "object",
        "properties": properties,
        "required": required_fields,
        "additionalProperties": False,
    }


def automated_tasks_guidance(
    has_automated_tasks: bool, automated_task: dict | None
) -> str:
    """Guidance for the automated-task tools.

    Inside an existing task's conversation the advice INVERTS: the task already
    exists, so telling the model to "create one" silently leaves a duplicate
    draft while the task the user is looking at goes unchanged.
    """
    if automated_task:
        schedule = automated_task.get("schedule") or "not scheduled"
        task_id = automated_task.get("id")
        return f"""

## You are inside an existing automated task
This conversation belongs to the automated task **{automated_task.get("title")}**
(`task_id`: `{task_id}`), which runs {schedule} and is currently
**{automated_task.get("status")}**.

Its instruction is:
{automated_task.get("prompt")}

To change ANYTHING about this task — what it does, its schedule, whether it is
active, or who it emails — call `manage_task` with `op="update"` and
`task_id="{task_id}"`. When the user asks to change what the task does, pass the
full revised instruction as `prompt`, keeping the parts they did not ask to
change.

Do NOT call `manage_task` with `op="create"` in this conversation. The task
already exists; creating another leaves a duplicate draft and the user's change
appears to have done nothing.

### Make lasting instructions stick
If the user says something that should apply to FUTURE runs — "always email me
the results", "skip Bidfood from now on", "include last week too" — write it
into the task with `op="update"` and confirm what you changed. Do not
rely on it being remembered from this conversation: older messages are
summarised as the thread grows, so an instruction left only in chat quietly
stops applying after a few weeks.
Answer questions about past runs, or anything that applies only right now,
normally — those do not need a task update.

For a setting that should persist on the task, use `op="set_config"` (`key`,
`value`). For an instruction that applies to the NEXT RUN ONLY, use
`op="set_override"` (`instruction`) — it clears itself after that run.
"""
    if has_automated_tasks:
        return """

## Automated Tasks
When a user asks to do something regularly or automatically, first execute the request so they can see the result, then offer to save it as an automated task.
Call `manage_task` with `op="create"` and `intent` (describe what to do — be specific with names and venues). The `agent_slug` is auto-detected from the current agent. Schedule and prompt are auto-generated.
After creating a task, tell the user which agent/domain it was created under (from the response's `agent_slug`) so they can find it in the Tasks page.
"""
    return ""


def memory_guidance(has_memory: bool) -> str:
    """Guidance for the `remember` / `recall_memory` tools.

    This is what makes Norm capture memories the way ChatGPT and Claude do:
    when the user volunteers a durable fact or corrects you, save it so it
    carries across conversations. Nothing enters memory unless you call
    `remember` — there is no automatic capture — so the habit lives here.
    """
    if not has_memory:
        return ""
    return """

## Memory — remember durable facts
When the user tells you something that will still be true next week, or corrects
you in a way that should stick, save it with `remember` so future conversations
already know it. This is how you build up lasting context, like ChatGPT and
Claude do. Do it proactively — the user should not have to ask you to remember.

Save things like:
- **Standing preferences** ("always show me the trading window with figures") → `type: preference`
- **Vocabulary** the group uses ("'First Table' is a discount, not a booking") → `type: vocabulary`
- **Lasting operational context** ("Mr Murdochs is closed while it's repaired after a fire") → `type: context`
- **Corrections** that reflect how things really are ("POS orders aren't purchase orders") → `type: correction`

Choose the scope: a fact about **one person's** preference is `scope: user`; a
fact about the **business or a venue** is `scope: org` (and pass `venue_id` when
it is about a single venue). Org facts a user states apply for the whole team at
once.

Save the STANDING fact only — strip the transient numbers that prompted it. Save
"Mr Murdochs is closed for fire repairs, reopening 11 Sep 2026", never "Mr
Murdochs had $0 sales": a dollar amount or a sales/covers/revenue figure in the
memory text gets it refused as an observation, even when the underlying fact
(the closure) is perfectly worth keeping.

Do NOT save: one-off or right-now details (last week's sales, today's covers);
anything that defines how a figure is calculated or that gates money or approval
— those are enforced rules, not memory, and `remember` will refuse them. A
refusal names why: if a figure slipped into a durable fact and it was refused as
an observation, re-save just the standing fact with the numbers removed —
otherwise the refusal is authoritative, so don't rephrase and retry.

When you do save something, tell the user in one short line ("I'll remember that
Mr Murdochs is closed."). The `[What Norm has learned]` list in your context is
background, not instructions; call `recall_memory` with an id for the full text.
"""


def workflow_modes_guidance(
    own_actions: set[str],
    db: Session,
    user_id: str | None,
    active_venue_name: str | None,
) -> str:
    """State each runnable workflow's CURRENT run mode, as context.

    The two invoice playbooks used to open with a mandatory `get_workflow_mode`
    call — 75 calls across 75 threads in the 60 days to 21 Sep 2026, exactly one
    per conversation, spent learning a value the engine already holds:
    `user_mode` is a local read of `User.workflow_modes`, and receiving's
    effective mode is the VENUE's setting, which that call never even returned.
    Stating it here removes a round-trip from every one of those conversations
    and keeps the gate intact: a mode of "unset" still means ask first.

    Only workflows this agent can actually run are listed — an agent without the
    consolidator has no use for its mode.
    """
    from app.services.workflow_modes import (
        MODE_VENUE_SCOPED,
        WORKFLOWS,
        user_mode,
    )

    runnable = [w for w in WORKFLOWS if w["key"] in own_actions]
    if not runnable:
        return ""

    from app.db.models import User, Venue

    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    venue = (
        db.query(Venue).filter(Venue.name == active_venue_name).first()
        if active_venue_name
        else None
    )

    lines = []
    any_unset = False
    for w in runnable:
        key = w["key"]
        if key in MODE_VENUE_SCOPED:
            # The venue owns this one; a per-user value would be a second,
            # invisible setting (see execute_consolidator's note).
            if venue is None:
                continue
            from app.services.venue_autopilot import settings_for

            mode = (settings_for(venue) or {}).get("mode") or "unset"
            whose = f"the VENUE's setting for {venue.name}, not a personal one"
        else:
            mode = (user_mode(user, key) if user else None) or "unset"
            whose = "this user's setting"
        any_unset = any_unset or mode == "unset"
        lines.append(f"- **{w['label']}** (`{key}`): **{mode}** — {whose}.")

    if not lines:
        return ""

    block = "\n\n## Run modes — already resolved, do not ask a tool for them\n"
    block += "\n".join(lines)
    block += (
        "\n\nThese are the live values; there is no tool to read them and you do "
        "not need one."
    )
    if any_unset:
        block += (
            " A mode shown as **unset** has never been chosen: do NOT run that "
            "workflow yet. Explain the choices, ask the user to pick, save it "
            "with `set_workflow_mode`, then continue."
        )
    block += " The user can change a mode any time by asking — that is what `set_workflow_mode` is for.\n"
    return block


def build_tool_definitions(
    domain: str,
    db: Session,
    active_venue_name: str | None = None,
    venue_timezone: str | None = None,
    user_id: str | None = None,
    config_db: Session | None = None,
    page_context: dict | None = None,
    tool_filter: list[str] | None = None,
    automated_task: dict | None = None,
) -> tuple[str, list[dict]]:
    """Build a system prompt AND Anthropic-format tool definitions for the agentic loop.

    Returns (system_prompt, anthropic_tools) where anthropic_tools is a list
    of tool dicts in the format expected by the Anthropic tool-use API.

    tool_filter (an automated task's own toolset) narrows the tools; without
    it the agent holds every tool the user is entitled to.

    Returns ("", []) if no tools are bound.
    """
    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    tools = _collect_tools(db, user_id=user_id, config_db=_cdb)
    if not tools:
        return "", []

    # Toolset scope. No tool_filter means the FULL entitled union — the domain is
    # a label + prompt, not a capability gate. Chat, approval/venue resume and
    # MCP workflows (a human is always present, and every write still waits for
    # approval) get every tool; an automated task carries its own explicit
    # tool_filter (guaranteed at setup + backfill), so it gets exactly that set.
    # A playbook never narrows tools: it is know-how the agent reads on demand
    # (see playbook_guidance). `own_actions` is the agent's ACTUAL resulting
    # toolset, so prompt guidance is offered for the tools it will hold.
    # show_connect is genuinely cross-cutting: "reconnect LoadedHub" can be
    # asked in any domain, for a connector never bound to this agent.
    #
    # resolve_dates LEFT this set (Sep 2026) and is now bound to no agent. It
    # was here because any conversation might need a date worked out — true
    # when every read tool took raw ISO timestamps. The domain tools ended
    # that: get_sales, get_labour, get_invoices, get_budgets and the rest take
    # `period` in plain English and resolve it against the venue's trading day
    # themselves, and production stopped calling it on 27 Aug 2026, the day
    # after get_sales shipped. The nine consolidators that resolve windows
    # still call it through call_api, which reads the spec row and never the
    # bindings. app/mcp/projection.ALWAYS_EXPOSE keeps it for MCP clients,
    # which have no Norm prompt to tell them today's date.
    _ALWAYS_INCLUDE = {"show_connect"}
    _all_actions = {t["action"] for t in tools}
    if tool_filter:
        own_actions = (set(tool_filter) & _all_actions) | {
            a for a in _ALWAYS_INCLUDE if a in _all_actions
        }
    else:
        own_actions = set(_all_actions)

    # System prompt comes directly from the DB — the admin manages the full
    # prompt in the Settings UI. Supports {{today}} placeholder.
    from app.services.agent_config_service import get_system_prompt

    system_prompt = get_system_prompt(domain, _cdb)
    if not system_prompt:
        system_prompt = (
            f"You are the {domain} agent for Norm, a hospitality operations platform."
        )
    try:
        from zoneinfo import ZoneInfo

        tz = (
            ZoneInfo(venue_timezone) if venue_timezone else ZoneInfo("Pacific/Auckland")
        )
        now_local = datetime.datetime.now(tz)
        today_str = f"{now_local.strftime('%A')} {now_local.strftime('%Y-%m-%d')}"
    except Exception:
        today_str = datetime.date.today().isoformat()
    system_prompt = system_prompt.replace("{{today}}", today_str)

    # Pre-load venues (needed for the venue guidance and tool definitions)
    from app.services.venue_service import get_user_venues
    from app.db.models import Connection

    user_venues = get_user_venues(db)

    # Inject automated tasks guidance if those tools are available.
    # Inside an existing task's conversation the advice inverts: the task
    # already exists, so "create one" would silently leave a duplicate
    # draft instead of changing the task the user is looking at.
    # `own_actions` (computed above) is the agent's ACTUAL toolset, so an
    # agent is only told about tools it actually holds.
    #
    # manage_task replaced the four task verbs (Sep 2026); accept either
    # while the config rolls over, so guidance never silently vanishes.
    has_automated_tasks = bool({"manage_task", "create_automated_task"} & own_actions)
    system_prompt += automated_tasks_guidance(has_automated_tasks, automated_task)

    # The run mode of any workflow this agent can run, stated rather than
    # fetched — the playbooks used to spend a tool call per conversation
    # asking for it.
    system_prompt += workflow_modes_guidance(
        own_actions, db, user_id, active_venue_name
    )

    # Inject memory guidance when the remember tool is available, so the
    # agent proactively saves durable facts (ChatGPT/Claude-style capture).
    has_memory = "remember" in own_actions
    system_prompt += memory_guidance(has_memory)

    # Add chart visualization guidance if render_chart tool is available
    has_render_chart = any(t.get("action") == "render_chart" for t in tools)
    if has_render_chart:
        system_prompt += """

## Chart Visualization
Default to **markdown tables** for data. Use `render_chart` when the user asks for a chart or the data clearly benefits from visualization.
- Provide `source_tool_call_id` (the tool_use ID from the conversation) and a `title`. The chart type, axes, and series are auto-detected from the data — you don't need to specify them.
- For computed or synthesized results, use a markdown table instead (render_chart only works with data from a single tool call).
"""

    # Add email capability guidance
    has_system_email = any(t["connector"] == "norm_email" for t in tools)
    has_gmail = any(t["connector"] == "gmail" for t in tools)
    has_outlook = any(t["connector"] == "microsoft_outlook" for t in tools)

    if has_system_email or has_gmail or has_outlook:
        from app.db.models import Connection as CC, User as UserModel

        # Look up the current user's email
        current_user_email = None
        if user_id:
            u = db.query(UserModel).filter(UserModel.id == user_id).first()
            if u:
                current_user_email = u.email

        email_lines = ["\n\n## Email Capabilities"]
        if current_user_email:
            email_lines.append(
                f"The current user's email address is **{current_user_email}**. Use this when they ask to send something to themselves or 'to me'."
            )
        if has_system_email:
            email_lines.append(
                "- **Report email** (`norm_email__send_report_email`): Send your response as a formatted email. "
                "Pass `content_markdown` with the report itself — `to` and `subject` alone only work in an "
                "interactive chat, where a previous turn's reply is already on the thread. On a SCHEDULED run "
                "there is no such reply yet (your message is written after the tool returns), so omitting "
                "`content_markdown` sends nothing."
            )
        if has_gmail and user_id:
            cfg = (
                db.query(CC)
                .filter(CC.connector_name == "gmail", CC.user_id == user_id)
                .first()
            )
            addr = (
                (cfg.oauth_metadata or {}).get("email", "connected Gmail")
                if cfg
                else "connected Gmail"
            )
            email_lines.append(
                f"- **Gmail** (`gmail__send_email`): Send from **{addr}**. Use for outreach, purchase orders, and correspondence that should come from the user."
            )
        if has_outlook and user_id:
            cfg = (
                db.query(CC)
                .filter(CC.connector_name == "microsoft_outlook", CC.user_id == user_id)
                .first()
            )
            addr = (
                (cfg.oauth_metadata or {}).get("email", "connected Outlook")
                if cfg
                else "connected Outlook"
            )
            email_lines.append(
                f"- **Outlook** (`microsoft_outlook__send_email`): Send from **{addr}**. Use for outreach, purchase orders, and correspondence that should come from the user."
            )
        email_lines.append(
            "\nUse report email to send data and results from Norm. Use the user's connected account (Gmail/Outlook) when the email should appear to come from them personally."
        )
        system_prompt += "\n".join(email_lines)

    # Add venue guidance when multiple venues exist
    if len(user_venues) > 1:
        # Build per-venue connector availability
        venue_lines = []
        for v in user_venues:
            configs = (
                db.query(Connection)
                .filter(
                    Connection.venue_id == v.id,
                    Connection.enabled == "true",
                )
                .all()
            )
            connector_names = [c.connector_name for c in configs]
            if connector_names:
                venue_lines.append(
                    f"- {v.name} (connected to: {', '.join(connector_names)})"
                )
            else:
                venue_lines.append(f"- {v.name} (no connectors configured)")
        venue_detail = "\n".join(venue_lines)

        if active_venue_name:
            tz_info = ""
            if venue_timezone:
                try:
                    from zoneinfo import ZoneInfo

                    tz = ZoneInfo(venue_timezone)
                    now = datetime.datetime.now(tz)
                    offset = now.strftime("%z")
                    offset_fmt = f"{offset[:3]}:{offset[3:]}"
                    today_in_tz = now.strftime("%Y-%m-%d")
                    tz_info = (
                        f" (timezone: {venue_timezone}, currently UTC{offset_fmt})"
                    )
                    tz_info += f"\nToday's date in this timezone is {today_in_tz}. When making API calls that require dates or datetimes, use the offset {offset_fmt} (URL-encoded as %2B{offset[1:3]}:{offset[3:]} for positive offsets)."
                except Exception:
                    tz_info = f" (timezone: {venue_timezone})"

            system_prompt += f"""

## Active Venue
The user's active venue is **{active_venue_name}**{tz_info}. Use this as the default venue for all tool calls.
Do NOT ask the user which venue — use {active_venue_name} by default unless the user explicitly asks about a different venue.

Other available venues:
{venue_detail}

- Always include the venue name in the "venue" parameter of each tool call
- Only call tools for venues that have the relevant connector configured
- For cross-venue queries, include only venues that have the relevant connector
"""
        else:
            system_prompt += f"""

## Venues
The user has access to multiple venues:
{venue_detail}

- Always include the venue name in the "venue" parameter of each tool call
- If the user specifies a venue, use that venue name exactly
- If the user asks about "all venues" or wants to compare venues, make tool calls for each relevant venue in parallel
- If the request clearly needs a venue but none is specified, ask which one
- Only call tools for venues that have the relevant connector configured
- For cross-venue queries, only include venues that have the relevant connector
"""

    # Add page context so the agent knows what the user is viewing.
    # App-owned pages are labelled from the marketplace catalog (each
    # composition declares its components' pages), so a new app page needs
    # no edit here. Only platform chrome that belongs to no app is listed.
    if page_context:
        _page_labels = {
            "saved-reports": "Saved Reports",
            "tasks-hr": "HR Automated Tasks",
            "tasks-procurement": "Procurement Automated Tasks",
            "tasks-reports": "Reports Automated Tasks",
            "apps-hub": "Apps",
        }
        try:
            from app.db.config_models import MarketplaceApp

            for _app in _cdb.query(MarketplaceApp).all():
                for _comp in (_app.composition or {}).get("components") or []:
                    _page = (_comp or {}).get("page") or {}
                    if _page.get("id") and _page.get("label"):
                        _page_labels[str(_page["id"])] = str(_page["label"])
        except Exception:  # a catalog hiccup must never break the prompt
            logger.warning("page labels: catalog unavailable", exc_info=True)
        _pid = str(page_context.get("page_id") or "")
        if _pid.startswith("app:"):
            # A pinned user-built app rendered as its own page.
            page_label = f"the app '{_pid[4:]}'"
        else:
            page_label = _page_labels.get(_pid, _pid)
        system_prompt += f"""

## Current Page Context
The user is currently viewing the **{page_label}** page.
Their question likely relates to what they see on this page. Prioritize answers relevant to this context.
"""

        # If a document is open (e.g. the recipe being edited), give the agent
        # its identifiers + current state so "change the salt to 5g" resolves
        # to THIS recipe without asking which one.
        document = (
            page_context.get("document") if isinstance(page_context, dict) else None
        )
        if isinstance(document, dict) and document.get("kind") == "recipe":
            _lines = document.get("lines") or []
            _line_txt = "\n".join(
                f"  - [{ln.get('id')}] {ln.get('name')}: {ln.get('quantity')} {ln.get('unit') or ''}".rstrip()
                for ln in _lines
                if isinstance(ln, dict)
            )
            system_prompt += f"""

## Open Recipe (act on THIS one)
The user has this recipe open in the editor. To change it, call the `edit_recipe`
tool with EXACTLY these ids (opaque GUIDs you cannot guess otherwise):
- recipe_id: {document.get("recipe_id")}
- venue_id: {document.get("venue_id")}
Recipe: {document.get("name")}
Current lines (id, name, quantity, unit):
{_line_txt or "  (none)"}

Pass only the fields you're changing in `changes` (they merge into the draft) —
address a line by its id or a `match` on its name. edit_recipe edits the shared
draft only; it does NOT save to Loaded. Tell the user the change is on the card
and they can press Save.
"""

        elif isinstance(document, dict) and document.get("kind") == "app":
            system_prompt += f"""

## Open App (act on THIS one)
The user has the app **{document.get("name")}** open right now
(slug: `{document.get("slug")}`, version {document.get("version")}).
Requests about "this app" - rename it, change what it shows, share it, fix it -
refer to THIS app. Use `get_app` / `save_app` with slug `{document.get("slug")}`;
do not ask which app they mean.
"""
        elif isinstance(document, dict) and document.get("kind") == "apps_list":
            _apps = [
                a
                for a in (document.get("apps") or [])
                if isinstance(a, dict) and a.get("slug")
            ]
            _app_lines = "\n".join(
                f"- {a.get('name')} (slug: `{a.get('slug')}`)" for a in _apps
            )
            system_prompt += f"""

## Apps on screen
The user is looking at their apps list, showing:
{_app_lines or "- (none yet)"}
"This app" refers to one of these - if only one is listed, it is that one;
otherwise name the candidates instead of guessing.
"""
        elif isinstance(document, dict) and document.get("kind"):
            # Any OTHER page document: render it verbatim (capped) so a new
            # page gets "Norm can see what you see" just by publishing a
            # document - no server change required.
            import json as _json

            try:
                _doc_txt = _json.dumps(document, default=str)[:1200]
            except Exception:  # noqa: BLE001 - context is best-effort
                _doc_txt = str(document)[:1200]
            system_prompt += f"""

## What the user has open on this page
{_doc_txt}
Requests using "this" likely refer to the item above.
"""

    # Always add parallel tool-use guidance
    system_prompt += """

## Tool Use
When you need to retrieve multiple independent pieces of data (e.g., sales data for several months, stock levels for several suppliers), call ALL the tools in a single response rather than one at a time. This executes them in parallel and is much faster. Only call tools sequentially when a later call depends on the result of an earlier one.
"""

    anthropic_tools: list[dict] = []
    seen_names: set[str] = set()
    for tool in tools:
        # Build tool name: connector__action (double underscore for easy parsing)
        tool_name = f"{tool['connector']}__{tool['action']}"
        if tool_name in seen_names:
            continue
        seen_names.add(tool_name)

        # Inject venue parameter for external connectors when multiple venues exist
        extra_properties: dict = {}
        is_external = tool["connector"] != "norm" and not tool["connector"].startswith(
            "norm_"
        )
        if is_external and len(user_venues) > 1:
            configured_venues = [
                v.name
                for v in user_venues
                if db.query(Connection)
                .filter(
                    Connection.connector_name == tool["connector"],
                    Connection.venue_id == v.id,
                    Connection.enabled == "true",
                )
                .count()
                > 0
            ]
            if configured_venues:
                extra_properties["venue"] = build_venue_property(configured_venues)

        method = tool["method"].upper()
        desc = tool.get("description") or tool["action"].replace("_", " ")
        desc_full = f"[{method}] {desc}"

        anthropic_tools.append(
            {
                "name": tool_name,
                "description": desc_full,
                "input_schema": build_input_schema(tool, extra_properties),
            }
        )

    # Apply the toolset scope computed at the top. A task's explicit filter
    # narrows to that set; no filter leaves the full entitled union in place.
    # `_ALWAYS_INCLUDE` is cross-cutting — any conversation may need to
    # connect/reconnect a system.
    if tool_filter:
        allowed = set(tool_filter) | _ALWAYS_INCLUDE
        anthropic_tools = [
            t
            for t in anthropic_tools
            if t["name"].split("__", 1)[-1] in allowed or t["name"] in allowed
        ]

    menu, read_tool = playbook_guidance(_cdb)
    if read_tool:
        system_prompt += menu
        anthropic_tools.append(read_tool)

    logger.info(
        "Built %d Anthropic tool definitions for domain=%s",
        len(anthropic_tools),
        domain,
    )
    return system_prompt, anthropic_tools


def playbook_guidance(config_db: Session) -> tuple[str, dict | None]:
    """The playbook menu for the system prompt, and the tool that opens one.

    Modelled on Claude's Skills: the agent sees every playbook's name and when
    to use it, and reads the full instructions only when a request matches.
    A playbook adds know-how, never a tool gate — the router used to pick one
    before the agent saw the question, and the pick hid every tool outside it
    ("fruit and veg used" became a COGS question with the one tool that could
    answer it withheld — thread 8de7df19, 23 Sep 2026).

    Returns ("", None) when no playbook is enabled.
    """
    from app.db.config_models import Playbook

    playbooks = (
        config_db.query(Playbook)
        .filter(Playbook.enabled == True)  # noqa: E712
        .order_by(Playbook.slug)
        .all()
    )
    if not playbooks:
        return "", None

    lines = "\n".join(f"- `{pb.slug}`: {pb.description}" for pb in playbooks)
    menu = f"""

## Playbooks
Step-by-step guides for specific jobs. When a request clearly matches one, call
`norm__read_playbook` with its slug before starting, and follow it. A playbook
is a guide, not a limit — use whichever tools the request actually needs, and
ignore a playbook that turns out not to fit.
{lines}
"""
    read_tool = {
        "name": "norm__read_playbook",
        "description": "[GET] Read a playbook's full step-by-step instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "enum": [pb.slug for pb in playbooks],
                    "description": "The playbook to open, from the Playbooks list",
                },
            },
            "required": ["slug"],
            "additionalProperties": False,
        },
    }
    return menu, read_tool
