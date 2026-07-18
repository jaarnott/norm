"""Config rows -> MCP tool schemas.

This is the inverse of ``mcp_executor.convert_mcp_tools_to_spec``, which maps
inbound MCP tools onto ``ConnectorSpec.tools``. Here we go the other way.

The projection is deliberately thin. It never *authors* a schema — it reads
one, via ``prompt_builder.build_input_schema``, the same function that builds
tool definitions for Norm's own agents. Anthropic's ``input_schema`` and MCP's
``inputSchema`` are the same object, so there is exactly one implementation and
the two surfaces cannot drift.

Five filters narrow the surface, each strictly. A tool is exposed only if it
passes all of them:

1. **Bound and credentialed** — ``_collect_tools`` gates on an enabled
   ``AgentConnectorBinding`` capability plus a working ``ConnectorConfig``
   (per-venue, and per-user for gmail/outlook). Free reuse of the gating the
   in-app agents already trust.
2. **Curated** — an enabled ``McpCapability`` row. No row means not exposed.
3. **Not denylisted** — conversation-scoped tools can't work here (below).
4. **Actually a read** — see ``write_signals``. This one is not a formality:
   the config DB declares ``method: GET`` on tools that plainly write.
5. **In scope** — the principal's granted scopes cover the capability's.

A note on the inbound/outbound asymmetry, because it is tempting to overclaim:
``convert_mcp_tools_to_spec`` has to *guess* read-vs-write from a name prefix
(``method = "GET" if name.startswith("get_")``) because inbound MCP carries no
method. Outbound we have more to go on — but not simply "the real method". The
spec's ``method`` is wrong often enough that it has to be corroborated; see
``write_signals`` for what that means in practice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.prompt_builder import (
    _collect_tools,
    build_input_schema,
    build_venue_property,
)
from app.config import settings
from app.mcp.scopes import ACCESS_DRAFT, ACCESS_READ
from app.mcp.ui_apps import ui_resource_for, ui_resource_for_playbook

logger = logging.getLogger(__name__)

# Tools that resolve `source_tool_call_id` against ToolCall rows keyed by an
# Anthropic tool_use id, or that exist only to paint a Norm display block.
# Neither survives on a stateless surface with no thread and no UI, so they are
# refused at curation time rather than left for an admin to enable by mistake.
MCP_DENYLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("norm_reports", "render_chart"),  # emits a Norm display block
        ("norm", "search_tool_result"),  # needs a prior ToolCall row
        ("norm", "show_roster"),  # display-only
        ("norm", "show_orders"),  # display-only
        ("norm", "update_thread_summary"),  # conversation bookkeeping
        ("norm", "set_override"),  # conversation bookkeeping
    }
)

# Exposed to every principal regardless of scope. Norm owns business date
# logic, so the client must always be able to ask — mirrors
# prompt_builder._ALWAYS_INCLUDE, which does the same for the in-app agents.
ALWAYS_EXPOSE: frozenset[tuple[str, str]] = frozenset({("norm", "resolve_dates")})

READ_METHODS = frozenset({"GET", "HEAD"})

# Action names follow a `verb_object` convention (get_sales_data,
# create_purchase_order), so the verb is the FIRST token. Two sets, because
# matching every verb anywhere in the name produces false positives — "post" is
# a noun in `get_best_time_to_post`, and blocking a read tool over that is
# silly.

# Never nouns in this domain, so safe to match at ANY position. This is what
# catches `review_and_receive_invoices`, whose leading verb ("review") is
# innocuous but which receives invoices.
STRONG_MUTATING_TOKENS = frozenset(
    {
        "approve",
        "cancel",
        "create",
        "delete",
        "pay",
        "receive",
        "reject",
        "remove",
        "send",
        "submit",
        "update",
    }
)

# Meaningful only in the leading (verb) position — several are nouns elsewhere
# ("get_scheduled_posts", "get_sync_status").
MUTATING_TOKENS = STRONG_MUTATING_TOKENS | frozenset(
    {
        "add",
        "archive",
        "assign",
        "edit",
        "execute",
        "import",
        "post",
        "promote",
        "reset",
        "restore",
        "run",
        "save",
        "set",
        "sync",
        "trigger",
        "upload",
        "write",
    }
)


def _mutating_verbs(action: str) -> list[str]:
    """Mutating verbs in an action name. Whole-token matches only, so
    "get_received_invoices" is not caught by "receive"."""
    tokens = action.split("_")
    if not tokens:
        return []
    found = set(STRONG_MUTATING_TOKENS) & set(tokens)
    if tokens[0] in MUTATING_TOKENS:
        found.add(tokens[0])
    return sorted(found)


def write_signals(tool_def: dict) -> list[str]:
    """Reasons this tool is not a read. Empty list means it is one.

    **`method` is not trustworthy on its own, and this is not hypothetical.**
    Every `execution_mode="internal"` tool in the config DB declares `GET`,
    including `norm.create_purchase_order` and `norm_email.send_report_email`.
    Worse, `loadedhub.review_and_receive_invoices` declares `GET` while
    carrying `allowed_write_actions: ["receive_invoice"]` — it writes to the
    supplier ledger. Trusting `method` would publish invoice-receiving as a
    read-only MCP tool.

    So read-ness must be agreed by every signal we have:

    - ``method`` — the HTTP verb, when the spec is honest.
    - ``consolidator_config.allowed_write_actions`` — an explicit declaration
      that the consolidator performs non-GET calls. Unambiguous.
    - ``working_document`` — the tool creates an editable draft, so it writes.
    - the action name — a spec row can be wrong, but `create_`/`send_` in the
      name is a strong statement of intent.

    Any disagreement means the row is lying about itself, and we refuse. A
    disagreement is also a config bug worth fixing — config_validator reports
    it, and the admin endpoint shows it as the refusal reason.
    """
    signals: list[str] = []

    method = (tool_def.get("method") or "POST").upper()
    if method not in READ_METHODS:
        signals.append(f"method is {method}")

    consolidator = tool_def.get("consolidator_config") or {}
    write_actions = consolidator.get("allowed_write_actions") or []
    if write_actions:
        signals.append(
            f"consolidator declares allowed_write_actions: {', '.join(write_actions)}"
        )

    if tool_def.get("working_document"):
        signals.append("creates an editable draft (working_document)")

    verbs = _mutating_verbs(tool_def.get("action") or "")
    if verbs:
        signals.append(f"action name contains mutating verb(s): {', '.join(verbs)}")

    return signals


def is_read_tool(tool_def: dict) -> bool:
    """True only when every signal agrees this tool is a read."""
    return not write_signals(tool_def)


@dataclass(frozen=True)
class McpTool:
    """A projected, authorized MCP tool."""

    name: str
    kind: str  # "connector" | "playbook"
    connector: str | None
    action: str | None
    playbook_slug: str | None
    method: str
    access: str  # "read" | "draft" — derived, never configured
    scopes: frozenset[str]
    description: str
    input_schema: dict
    summary_fields: tuple[str, ...] | None = None
    # Whether venue authorization must run before executing. Derived from the
    # tool, not from the schema — see needs_venue().
    venue_scoped: bool = True
    # MCP Apps (SEP-1865): the ui:// resource this tool renders into, or None
    # for a plain text/data tool. Surfaced as `_meta.ui.resourceUri`.
    ui_resource: str | None = None

    @property
    def is_read_only(self) -> bool:
        return self.access == ACCESS_READ


def is_read_method(method: str) -> bool:
    return (method or "").upper() in READ_METHODS


def default_tool_name(kind: str, target: str, action: str) -> str:
    """The public tool name.

    Keeps the `connector__action` convention Norm uses internally, so a name
    means the same thing on both sides of the wire.
    """
    if kind == "playbook":
        return f"norm_playbook__{target}"
    return f"{target}__{action}"


def to_mcp_tool_dict(tool: McpTool) -> dict:
    """Render for `tools/list`.

    Note what is absent: the `[GET]` / `[POST]` description prefix. That prefix
    exists only so tool_loop._build_tool_meta can re-parse the method out of a
    description it wrote itself — Anthropic's tool schema has no method field.
    Claude's MCP client has no such parser, so the prefix would be noise. The
    method travels as a real field on McpTool, and read-only-ness is advertised
    through the annotation MCP actually defines for it.
    """
    out: dict = {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": {
            "readOnlyHint": tool.is_read_only,
            "destructiveHint": False,  # v1 is read + draft only
        },
    }
    # MCP Apps: bind the tool to its embedded UI. The host preloads this ui://
    # resource and renders the tool's result into it.
    if tool.ui_resource:
        out["_meta"] = {"ui": {"resourceUri": tool.ui_resource}}
    return out


def raw_tool_defs(config_db: Session) -> dict[tuple[str, str], dict]:
    """(connector, action) -> the spec's tool row, verbatim.

    ``_collect_tools`` projects a subset of keys and drops
    ``consolidator_config`` and ``working_document`` — the two strongest write
    signals (see ``write_signals``). So gating uses ``_collect_tools`` and
    signal-checking uses the raw row.
    """
    from app.db.config_models import ConnectorSpec

    out: dict[tuple[str, str], dict] = {}
    for spec in config_db.query(ConnectorSpec).all():
        for tool in spec.tools or []:
            action = tool.get("action")
            if action:
                out[(spec.connector_name, action)] = tool
    return out


def needs_venue(connector: str, action: str) -> bool:
    """Whether a tool operates on venue-scoped data.

    Everything does, except the always-on utilities. Deliberately NOT derived
    from whether the schema happens to carry a `venue` property: that property
    is only injected when the *user* has more than one venue, so a
    schema-driven rule would silently skip venue authorization for
    single-venue users — enforcement that evaporates for the smallest
    customers is not enforcement.
    """
    return (connector, action) not in ALWAYS_EXPOSE


def project_tools(
    db: Session,
    config_db: Session,
    *,
    user_id: str | None = None,
    granted_scopes: frozenset[str] | set[str] | None = None,
    venue_names: list[str] | None = None,
) -> list[McpTool]:
    """Project the curated, authorized MCP tool surface for one principal.

    ``venue_names`` are the principal's *consented* venues. When there is more
    than one, a `venue` enum is injected so the caller must name one — the
    values come from the token, never from a database-wide lookup.
    """
    from app.db.models import McpCapability

    granted = frozenset(granted_scopes or ())
    venue_names = venue_names or []

    # 1. Bound + credentialed — reuses the gating the in-app agents rely on.
    gated: set[tuple[str, str]] = {
        (t["connector"], t["action"])
        for t in _collect_tools(db, user_id=user_id, config_db=config_db)
    }
    # Full-fidelity rows, for signals and schema.
    available = {k: v for k, v in raw_tool_defs(config_db).items() if k in gated}

    # 2. Curation.
    caps = (
        config_db.query(McpCapability)
        .filter(McpCapability.enabled == True)  # noqa: E712
        .all()
    )

    tools: list[McpTool] = []
    seen: set[str] = set()

    # Playbook workflow tools — one per enabled, curated playbook.
    enabled_playbooks = {pb.slug: pb for pb in _enabled_playbooks(config_db)}
    for cap in caps:
        if cap.kind == "playbook":
            pb = enabled_playbooks.get(cap.target)
            if pb is None:
                continue  # playbook missing or disabled
            cap_scopes = frozenset(cap.scopes or ())
            if not cap_scopes <= granted:
                continue
            name = cap.tool_name_override or default_tool_name(
                "playbook", cap.target, ""
            )
            if name in seen:
                continue
            seen.add(name)
            tools.append(
                McpTool(
                    name=name,
                    kind="playbook",
                    connector=None,
                    action=None,
                    playbook_slug=cap.target,
                    method="WORKFLOW",
                    access=ACCESS_DRAFT,
                    scopes=cap_scopes,
                    description=cap.description_override or pb.description,
                    input_schema=_playbook_input_schema(venue_names),
                    venue_scoped=True,
                    ui_resource=(
                        ui_resource_for_playbook(cap.target)
                        if settings.MCP_UI_ENABLED
                        else None
                    ),
                )
            )
            continue

        if cap.kind != "connector":
            continue

        key = (cap.target, cap.action)
        tool_def = available.get(key)
        if tool_def is None:
            # Curated but not currently available — unbound, or no credentials
            # for this user. Silently omitted; the admin endpoint surfaces why.
            continue
        if key in MCP_DENYLIST:
            continue

        cap_scopes = frozenset(cap.scopes or ())
        always = key in ALWAYS_EXPOSE
        if not always and not cap_scopes <= granted:
            continue

        # Access is derived, never configured. Write-time validation already
        # refuses non-read tools; this re-checks on every request, because the
        # spec row can change after the row was enabled.
        if not is_read_tool(tool_def):
            logger.warning(
                "mcp_capability_no_longer_read_only",
                extra={
                    "connector": cap.target,
                    "action": cap.action,
                    "signals": write_signals(tool_def),
                },
            )
            continue

        name = cap.tool_name_override or default_tool_name(
            cap.kind, cap.target, cap.action
        )
        if name in seen:
            continue
        seen.add(name)

        description = (
            cap.description_override
            or tool_def.get("description")
            or (cap.action.replace("_", " "))
        )
        summary_fields = tool_def.get("summary_fields")

        # Offer the venue choice only when the principal actually has one to
        # make. With a single consented venue it's implied, and asking the
        # model to restate it is just a chance to get it wrong.
        extra: dict = {}
        tool_needs_venue = needs_venue(cap.target, cap.action)
        if tool_needs_venue and len(venue_names) > 1:
            extra["venue"] = build_venue_property(venue_names)

        tools.append(
            McpTool(
                name=name,
                kind="connector",
                connector=cap.target,
                action=cap.action,
                playbook_slug=None,
                method=(tool_def.get("method") or "GET").upper(),
                access=ACCESS_READ,
                scopes=cap_scopes,
                description=description,
                input_schema=build_input_schema(tool_def, extra),
                summary_fields=tuple(summary_fields) if summary_fields else None,
                venue_scoped=tool_needs_venue,
                ui_resource=(
                    ui_resource_for(cap.target, cap.action)
                    if settings.MCP_UI_ENABLED
                    else None
                ),
            )
        )

    return sorted(tools, key=lambda t: t.name)


# ── Natural scope suggestion ─────────────────────────────────────────────
# The default permission for a tool, so the admin panel can pre-select the one
# scope that fits instead of presenting the whole vocabulary (a POS tool
# offering "View HR records" is a footgun). This is a CONVENIENCE, not a
# boundary: real enforcement is the scope's `requires` plus the user's role, so
# a wrong guess is recoverable — the admin can always override it.
#
# Order matters: the first domain whose keywords appear wins. Procurement/stock
# is checked before sales precisely because a hospitality POS "order" is a sale
# (get_pos_orders, get_staff_orders → reports), while procurement names carry
# "purchase"/"stock" and must land on orders:read.
_SCOPE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mcp:roster:read", ("roster", "shift", "rota", "staffing")),
    (
        "mcp:hr:read",
        (
            "employee",
            "payroll",
            "timesheet",
            "leave",
            "contract",
            "absence",
            "time_off",
            "onboarding",
            "candidate",
            "applicant",
            "recruit",
        ),
    ),
    # Procurement / stock BEFORE sales — see the note above.
    (
        "mcp:orders:read",
        (
            "stock",
            "inventory",
            "supplier",
            "purchase",
            "delivery",
            "invoice",
            "sku",
            "wastage",
            "ingredient",
            "par_level",
        ),
    ),
    # POS / sales. "order" and "product" live here: a POS order is a sale, and
    # procurement orders were already caught above by "purchase"/"stock".
    (
        "mcp:reports:read",
        (
            "sales",
            "revenue",
            "trading",
            "pos",
            "discount",
            "takings",
            "transaction",
            "performance",
            "report",
            "order",
            "product",
            "cover",
            "spend",
            "gross",
            "margin",
            "cogs",
            "profit",
        ),
    ),
    ("mcp:tasks:read", ("task", "schedule", "automation")),
    ("mcp:venues:read", ("venue", "site", "location")),
)

# The draft counterpart of a read scope, used when a *playbook* (which creates
# drafts) matches a read domain — drafting a stock order needs orders:draft,
# not orders:read.
_DRAFT_OF: dict[str, str] = {
    "mcp:orders:read": "mcp:orders:draft",
    "mcp:tasks:read": "mcp:tasks:draft",
}

_DRAFTING_VERBS = frozenset(
    {"create", "draft", "build", "prepare", "raise", "generate", "new"}
)


def _looks_like_drafting(haystack: str) -> bool:
    return any(v in haystack for v in _DRAFTING_VERBS)


def suggest_scopes(
    target: str, action: str, description: str = "", *, drafts: bool = False
) -> list[str]:
    """The natural default scope(s) for a candidate, or [] if nothing fits.

    Keyword match over the connector/playbook name, action and description.
    Returns the single best-fitting scope. When ``drafts`` (a playbook, which
    can create drafts) and the matched domain has a draft variant and the name
    reads like a create/draft action, the draft scope is used instead of read.

    [] means "no obvious fit" — the panel then falls back to the full list,
    which is the right behaviour for a domain with no scope yet (marketing,
    social) rather than a confident wrong guess.
    """
    haystack = f"{target} {action} {description}".lower()
    for scope, keywords in _SCOPE_HINTS:
        if any(k in haystack for k in keywords):
            if drafts and scope in _DRAFT_OF and _looks_like_drafting(haystack):
                return [_DRAFT_OF[scope]]
            return [scope]
    return []


def exposable_reason(kind: str, tool_def: dict) -> str | None:
    """Why this candidate can't be exposed as a direct tool, or None if it can.

    Used by the admin endpoint to refuse a toggle at the point of clicking it,
    and by config_validator to catch a spec that changed under an already-
    enabled row.
    """
    if kind == "playbook":
        return None

    signals = write_signals(tool_def)
    if not signals:
        return None

    return (
        "This tool changes data, so it cannot be exposed as a direct MCP tool "
        f"(v1 direct tools are read-only). Evidence: {'; '.join(signals)}. "
        "Expose it via a playbook workflow instead, so Norm's draft and "
        "approval flow stays in the loop."
    )


__all__ = [
    "ACCESS_DRAFT",
    "ACCESS_READ",
    "ALWAYS_EXPOSE",
    "MCP_DENYLIST",
    "MUTATING_TOKENS",
    "McpTool",
    "default_tool_name",
    "needs_venue",
    "exposable_reason",
    "is_read_method",
    "is_read_tool",
    "project_tools",
    "raw_tool_defs",
    "suggest_scopes",
    "to_mcp_tool_dict",
    "write_signals",
]


def _enabled_playbooks(config_db):
    from app.db.config_models import Playbook

    return (
        config_db.query(Playbook)
        .filter(Playbook.enabled == True)  # noqa: E712
        .all()
    )


def _playbook_input_schema(venue_names: list[str]) -> dict:
    """Natural-language schema for a playbook tool.

    Deliberately just `request` (+ optional venue). Norm's playbook
    instructions and tool loop decide what to do with it — typed per-playbook
    params would be a schema authored in a second place.
    """
    props = {
        "request": {
            "type": "string",
            "description": "What you want done, in plain English. Include dates, "
            "quantities, and supplier or product names where relevant.",
        }
    }
    if len(venue_names) > 1:
        props["venue"] = build_venue_property(venue_names)
    return {
        "type": "object",
        "properties": props,
        "required": ["request"],
        "additionalProperties": False,
    }
